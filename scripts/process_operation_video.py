#!/usr/bin/env python3
"""Turn a user-supplied operation-recording video into visual evidence.

When the current environment cannot inspect video directly, this script extracts
frames via ffmpeg so the resulting images can be opened like ordinary screenshots.
Judging what each frame shows and writing the manual from it is the model's job,
not this script's.

Frame extraction is time-based (evenly spaced across the whole duration), not purely
scene-change-based. A first version relied only on ffmpeg's per-frame `scene` score,
which measures whole-frame pixel difference between consecutive frames: for a typical
UI recording (most of the frame is a fixed nav bar / background, only a small region
changes) that score can stay orders of magnitude below any reasonable threshold, so a
real state change can be silently skipped with no error at all. Guaranteed periodic
sampling cannot silently miss a whole time range; scene-change timestamps are layered
on top only to add extra resolution around sharp cuts between periodic samples.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from common import ensure_dir, write_json

DEFAULT_SCENE_THRESHOLD = 0.35
MIN_PERIODIC_INTERVAL_SECONDS = 1.0
DEDUPE_MIN_GAP_SECONDS = 0.5
MAX_FRAMES = 120


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def probe_duration(video: Path) -> float:
    try:
        completed = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", "--", str(video)],
            text=True,
            capture_output=True,
            timeout=30,
        )
        if completed.returncode != 0:
            return 0.0
        data = json.loads(completed.stdout or "{}")
        return float(data.get("format", {}).get("duration") or 0.0)
    except Exception:
        return 0.0


def parse_showinfo_timestamps(stderr: str) -> list[float]:
    return [float(m) for m in re.findall(r"pts_time:([0-9.]+)", stderr)]


def periodic_timestamps(duration: float, max_frames: int) -> list[float]:
    if duration <= 0:
        return [0.0]
    interval = max(MIN_PERIODIC_INTERVAL_SECONDS, duration / max_frames)
    count = int(duration / interval) + 1
    return [round(min(i * interval, duration), 2) for i in range(count)]


def scene_change_timestamps(video: Path, scene_threshold: float) -> list[float]:
    try:
        completed = subprocess.run(
            [
                "ffmpeg",
                "-i",
                str(video),
                "-vf",
                f"select='gt(scene\\,{scene_threshold})',showinfo",
                "-vsync",
                "vfr",
                "-f",
                "null",
                "-",
            ],
            text=True,
            capture_output=True,
            timeout=600,
        )
        return parse_showinfo_timestamps(completed.stderr)
    except Exception:
        return []


def dedupe(timestamps: list[float], min_gap: float) -> list[float]:
    merged: list[float] = []
    for ts in sorted({round(ts, 2) for ts in timestamps}):
        if not merged or ts - merged[-1] >= min_gap:
            merged.append(ts)
    return merged


def downsample_by_time(timestamps: list[float], max_frames: int) -> list[float]:
    """Evenly thin a sorted timestamp list by time span, not by list index.

    Thinning by index (`timestamps[i * len / max_frames]`) silently breaks the
    periodic-coverage guarantee once scene-change candidates cluster in one
    stretch of the video (e.g. a loading animation): that stretch dominates a
    disproportionate share of list *entries*, so index-based sampling pulls most
    of the budget from there and starves every other stretch of the timeline.
    Bucketing by time value keeps the budget spread across the actual duration.
    """
    if len(timestamps) <= max_frames or max_frames <= 1:
        return timestamps[:max_frames] if max_frames >= 1 else timestamps
    start, end = timestamps[0], timestamps[-1]
    span = end - start
    if span <= 0:
        return timestamps[:max_frames]
    kept: list[float] = []
    for slot in range(max_frames):
        target = start + span * slot / (max_frames - 1)
        nearest = min(timestamps, key=lambda ts: abs(ts - target))
        if not kept or nearest != kept[-1]:
            kept.append(nearest)
    return kept


def select_timestamps(periodic: list[float], scene: list[float], max_frames: int, min_gap: float) -> list[float]:
    """Periodic samples are a guaranteed baseline; scene changes only fill leftover budget.

    Merging both lists and thinning the union (even by time, not just by index)
    still lets a burst of scene-change points outcompete periodic points for
    slots within their shared neighborhood. Locking in the periodic baseline
    first, and only spending remaining budget on scene extras that don't
    already have a nearby periodic point, keeps the "never miss a whole time
    range" guarantee regardless of how the scene detector behaves.
    """
    baseline = dedupe(periodic, min_gap)
    if len(baseline) > max_frames:
        baseline = downsample_by_time(baseline, max_frames)

    remaining = max_frames - len(baseline)
    if remaining <= 0 or not scene:
        return baseline

    extras: list[float] = []
    for ts in dedupe(scene, min_gap):
        if len(extras) >= remaining:
            break
        if all(abs(ts - existing) >= min_gap for existing in baseline) and all(
            abs(ts - existing) >= min_gap for existing in extras
        ):
            extras.append(ts)

    return sorted(baseline + extras)


def extract_frame_at(video: Path, timestamp: float, out_path: Path) -> tuple[bool, str]:
    coarse = max(0.0, timestamp - 2.0)
    fine = timestamp - coarse
    try:
        completed = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{coarse:.3f}",
                "-i",
                str(video),
                "-ss",
                f"{fine:.3f}",
                "-frames:v",
                "1",
                "-qscale:v",
                "3",
                str(out_path),
            ],
            text=True,
            capture_output=True,
            timeout=60,
        )
        ok = completed.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0
        return ok, ("" if ok else completed.stderr[-400:])
    except Exception as exc:
        return False, str(exc)


def extract_frames(video: Path, out_dir: Path, scene_threshold: float, max_frames: int) -> list[dict[str, Any]]:
    ensure_dir(out_dir)
    for existing in out_dir.glob("frame_*.png"):
        existing.unlink()

    duration = probe_duration(video)
    if duration <= 0:
        print(
            "WARNING: 未能探测到视频时长（ffprobe 返回空结果），无法保证按时间均匀取样覆盖整段视频，"
            "仅能退化为对开头抽取单帧。若视频文件名以 `-` 开头，须重命名后重试。"
        )
    periodic = periodic_timestamps(duration, max_frames)
    scene = scene_change_timestamps(video, scene_threshold)
    timestamps = select_timestamps(periodic, scene, max_frames, DEDUPE_MIN_GAP_SECONDS)
    if not timestamps:
        timestamps = [0.0]
    if duration > 0:
        # A timestamp landing on (or past) the last decodable instant reliably fails to seek;
        # nudge it back just enough to still land inside the last frame.
        timestamps = [min(ts, max(0.0, duration - 0.05)) for ts in timestamps]
        effective_interval = duration / max(len(periodic) - 1, 1)
        if effective_interval > 10:
            print(
                f"NOTE: 视频时长 {duration:.0f} 秒，按当前 --max-frames={max_frames} 计算的取样间隔约为 "
                f"{effective_interval:.1f} 秒，覆盖粒度较粗，密集的操作步骤可能无法采集。建议增大 --max-frames。"
            )

    frames: list[dict[str, Any]] = []
    failures: list[str] = []
    for i, ts in enumerate(timestamps, start=1):
        name = f"frame_{i:04d}.png"
        path = out_dir / name
        ok, err = extract_frame_at(video, ts, path)
        if not ok:
            # The container's reported duration can sit slightly past the last
            # decodable frame (common with variable-frame-rate mobile recordings),
            # so a seek very close to duration can still fail even after the
            # epsilon clamp above; step further back before giving up on this slot.
            for retry_ts in (max(0.0, ts - 0.5), max(0.0, ts - 1.5)):
                ok, err = extract_frame_at(video, retry_ts, path)
                if ok:
                    ts = retry_ts
                    break
        if ok:
            frames.append({"frame": name, "timestamp": ts})
        else:
            failures.append(f"t={ts}: {err}")

    if not frames:
        detail = "\n".join(failures[:5])
        raise SystemExit(
            "STOP_FOR_USER\n"
            "NEXT_ACTION: 视频抽帧全部失败。须确认视频文件是否损坏、格式是否受 ffmpeg 支持，"
            "或改用 Chrome DevTools MCP、Codex Computer Use、用户自行截图三种方式之一。\n"
            f"失败详情（节选）：\n{detail}"
        )
    if failures:
        print(f"WARNING: {len(failures)}/{len(timestamps)} 个目标时间点抽帧失败，已跳过，其余帧仍可使用。")
        print(f"失败详情（节选）：{failures[0]}")

    return frames


def format_ts(seconds: float) -> str:
    minutes = int(seconds // 60)
    secs = seconds - minutes * 60
    return f"{minutes:02d}:{secs:05.2f}"


def write_evidence(out_dir: Path, video: Path, frames_dir_name: str, frames: list[dict[str, Any]]) -> dict[str, Any]:
    entries = []
    for item in frames:
        entries.append(
            {
                "frame": f"{frames_dir_name}/{item['frame']}",
                "timestamp": item["timestamp"],
                "timestamp_display": format_ts(item["timestamp"]),
            }
        )

    manifest = {
        "video_source": str(video.resolve()),
        "frame_count": len(entries),
        "frames": entries,
    }
    write_json(out_dir / "视频证据.json", manifest)

    lines = [
        "# 操作视频证据",
        "",
        f"- 视频来源：`{video.name}`",
        f"- 抽取帧数：{len(entries)}",
        "",
        "以下逐帧列出时间戳与图片路径。须以当前环境可用的图片查看能力逐张检查画面，判断该时刻所执行的操作与界面上呈现的内容，据此撰写业务理解的典型操作流程与操作手册的操作步骤。不得臆测视频中未出现的内容。",
        "",
    ]
    for entry in entries:
        lines.append(f"## {entry['timestamp_display']}（第 {entries.index(entry) + 1} 帧）")
        lines.append("")
        lines.append(f"- 图片：`{entry['frame']}`")
        lines.append("")
    (out_dir / "视频证据.md").write_text("\n".join(lines), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--out-dir", default="软件著作权申请资料/操作视频")
    parser.add_argument("--scene-threshold", type=float, default=DEFAULT_SCENE_THRESHOLD)
    parser.add_argument("--max-frames", type=int, default=MAX_FRAMES)
    args = parser.parse_args()

    video = Path(args.video)
    if not video.exists():
        raise SystemExit(f"Video not found: {video}")
    # Resolve to an absolute path so it always starts with "/": a relative path
    # starting with "-" (e.g. "-recording.mp4") gets misread as an unrecognized
    # flag by ffprobe's own argument parser, silently returning an empty result
    # instead of an error, which would defeat every guarantee below.
    video = video.resolve()

    if args.max_frames < 1:
        raise SystemExit(f"--max-frames must be a positive integer, got {args.max_frames}")

    if not have_ffmpeg():
        raise SystemExit(
            "STOP_FOR_USER\n"
            "NEXT_ACTION: 未检测到 ffmpeg/ffprobe，无法处理操作视频。须安装后重试"
            "（macOS: brew install ffmpeg；Ubuntu/Debian: apt install ffmpeg），"
            "或改用 Chrome DevTools MCP、Codex Computer Use、用户自行截图三种方式之一。"
        )

    out_dir = ensure_dir(Path(args.out_dir))
    frames_dir = ensure_dir(out_dir / "帧")
    frames = extract_frames(video, frames_dir, args.scene_threshold, args.max_frames)

    manifest = write_evidence(out_dir, video, "帧", frames)

    print(f"OK video evidence: {out_dir}")
    print(f"Frames extracted: {manifest['frame_count']}")


if __name__ == "__main__":
    main()
