#!/usr/bin/env python3
"""Extract real source code and create Markdown draft pages."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

from common import (
    COPYRIGHT_CODE_EXTS,
    FRONTEND_EXTS,
    STRIP_MODE_LABELS,
    STRIP_MODE_NONE,
    STRIP_MODE_WHOLE_LINE,
    classify_source_lines,
    comment_syntax,
    ensure_dir,
    is_known_config_file,
    iter_project_files,
    looks_binary,
    read_json,
    read_text,
    rel,
    safe_filename,
    strip_comments,
    write_json,
)


LINES_PER_PAGE = 50
SPLIT_THRESHOLD_PAGES = 60
SEGMENT_PAGES = SPLIT_THRESHOLD_PAGES // 2
REORDER_WINDOW = 30
# 降级切分（无法重排出 30+30 页时）允许的每段页数区间。
# 超出该区间即视为材料不足，须回到代码文件选择环节调整抽取范围，不得径直输出。
FALLBACK_MIN_SEGMENT_PAGES = 28
FALLBACK_MAX_SEGMENT_PAGES = 32


def category_weight(path: Path, project: Path) -> tuple[int, str]:
    r = rel(path, project).lower()
    name = path.name.lower()
    priority = 80
    if name in {"main.ts", "main.js", "main.tsx", "main.jsx", "app.vue", "app.tsx"} or r in {
        "src/app/page.tsx",
        "src/app/layout.tsx",
        "app/page.tsx",
        "app/layout.tsx",
    } or r.endswith("/src/app/page.tsx") or r.endswith("/src/app/layout.tsx"):
        priority = 0
    elif path.suffix.lower() in {".css", ".scss", ".sass", ".less"}:
        priority = 90
    elif "/router/" in r or "/routes/" in r or "router." in r or "routes." in r:
        priority = 10
    elif "/pages/" in r or "/views/" in r or "/app/" in r or "/screens/" in r:
        priority = 20
    elif "/api/" in r or "/apis/" in r or "/services/" in r or "request." in r:
        priority = 30
    elif "/store/" in r or "/stores/" in r or "/pinia/" in r or "/redux/" in r:
        priority = 40
    elif "/components/" in r:
        priority = 50
    elif "/utils/" in r or "/lib/" in r or "/hooks/" in r or "/composables/" in r:
        priority = 60
    elif path.suffix.lower() not in FRONTEND_EXTS:
        if any(part in r for part in ("/backend/app/", "/server/", "/api/", "/services/", "/models/", "/schemas/", "/workers/")):
            priority = 70
        elif name in {"docker-compose.yml", "docker-compose.yaml", "pyproject.toml"} or path.suffix.lower() in {".toml", ".yml", ".yaml"}:
            priority = 95
        else:
            priority = 100
    return priority, r


def should_skip_file(path: Path) -> bool:
    if path.suffix.lower() not in COPYRIGHT_CODE_EXTS:
        return True
    if is_known_config_file(path):
        return True
    if looks_binary(path):
        return True
    try:
        size = path.stat().st_size
    except OSError:
        return True
    if size <= 0 or size > 800_000:
        return True
    try:
        sample = read_text(path, limit=20_000)
    except Exception:
        return True
    lines = sample.splitlines()
    if any(len(line) > 3000 for line in lines[:80]):
        return True
    return False


def selected_line_estimate(item: dict[str, Any]) -> int:
    try:
        total = int(item.get("material_line_count") or item.get("line_count") or 0)
    except (TypeError, ValueError):
        total = 0
    return total + 1 if total > 0 else 0


def selection_overview(selection_path: Path | None, lines_per_page: int) -> dict[str, Any]:
    """Summarise the editable selection file.

    `valid` 表示选择文件确实解析出了候选清单。只有在 `valid` 为真时，
    `unselected_count == 0` 才足以证明「已选文件 = 全部候选文件」；
    选择文件缺失或结构异常时不得据此断言代码材料涵盖全部源码。
    """
    overview: dict[str, Any] = {
        "valid": False,
        "available_lines": 0,
        "available_pages": 0,
        "candidate_count": 0,
        "selected_count": 0,
        "unselected_count": 0,
        "unselected_paths": [],
    }
    if selection_path is None or not selection_path.exists():
        return overview
    try:
        data = read_json(selection_path)
    except Exception:
        return overview
    items = data.get("files") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return overview
    entries = [item for item in items if isinstance(item, dict)]
    if not entries:
        return overview
    unselected = [item for item in entries if not item.get("selected") and selected_line_estimate(item) > 0]
    available_lines = sum(selected_line_estimate(item) for item in entries)
    overview.update(
        {
            "valid": True,
            "available_lines": available_lines,
            "available_pages": (available_lines + lines_per_page - 1) // lines_per_page if available_lines else 0,
            "candidate_count": len(entries),
            "selected_count": sum(1 for item in entries if item.get("selected")),
            "unselected_count": len(unselected),
            "unselected_paths": [str(item.get("path")) for item in unselected if item.get("path")],
        }
    )
    return overview


def available_pages_from_selection(selection_path: Path | None, lines_per_page: int) -> tuple[int, int, int]:
    overview = selection_overview(selection_path, lines_per_page)
    return overview["available_lines"], overview["available_pages"], overview["unselected_count"]


def marker_for(path: Path, project: Path) -> str:
    return f"// File: {rel(path, project)}"


# ---------------------------------------------------------------------------
# 注释剔除与行数统计
#
# 剔除逻辑位于 common.strip_comments()，verify_materials.py 复用同一实现检测残留。
# comment_syntax / classify_source_lines 自 common 导入后在本模块重新导出，
# 保持既有的 `from extract_code_material import classify_source_lines` 可用。
# ---------------------------------------------------------------------------


def empty_stats() -> dict[str, int]:
    return {"code": 0, "marker": 0, "blank": 0, "stripped_comment": 0, "trimmed": 0}


def stats_summary(stats: dict[str, int]) -> dict[str, Any]:
    return {
        "code_line_count": stats["code"],
        "marker_line_count": stats["marker"],
        "material_line_count": stats["code"] + stats["marker"],
        "blank_line_count": stats["blank"],
        "stripped_comment_line_count": stats["stripped_comment"],
        "inline_comment_trimmed_line_count": stats["trimmed"],
    }


def load_selected_files(project: Path, selection_path: Path | None) -> list[dict[str, Any]]:
    if selection_path is None:
        raise SystemExit(
            "STOP_FOR_USER\n"
            "NEXT_ACTION: 代码抽取须先经 propose_code_selection.py 生成并确认 草稿/代码文件选择.json。"
        )

    data = read_json(selection_path)
    if isinstance(data, dict) and data.get("selection_required") and not data.get("user_confirmed"):
        raise SystemExit(
            "STOP_FOR_USER\n"
            "NEXT_ACTION: 代码文件选择尚未确认。须先确认或修改 草稿/代码文件选择.json，"
            "再运行 `python3 <SKILL_DIR>/scripts/confirm_stage.py --workdir 软件著作权申请资料 --stage code-selection --note \"<用户确认内容>\"`。"
        )
    items = data.get("files") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise SystemExit(f"Invalid selection file: {selection_path}")

    selected = []
    for item in items:
        if not isinstance(item, dict) or not item.get("selected"):
            continue
        path_value = item.get("path")
        if not path_value:
            continue
        selected.append(
            {
                "path": str(path_value),
                "selected": True,
            }
        )
    return selected


def prepare_records(project: Path, selection_path: Path | None) -> list[dict[str, Any]]:
    """Read every selected file once, strip all comments and keep the remaining code lines.

    注释一律剔除（中国版权保护中心要求代码材料不含注释），空行一并剔除。
    """
    selected_items = load_selected_files(project, selection_path)
    records: list[dict[str, Any]] = []

    for item in selected_items:
        path = (project / item["path"]).resolve()
        try:
            path.relative_to(project.resolve())
        except ValueError:
            raise SystemExit(f"Selected file is outside project: {path}")
        if should_skip_file(path):
            continue
        text = read_text(path)
        source_lines = text.splitlines()
        result = strip_comments(text, path.suffix.lower())
        kept_lines = result.lines
        if not kept_lines:
            continue

        records.append(
            {
                "path": rel(path, project),
                "source_line_count": len(source_lines),
                "blank_line_count": result.blank_line_count,
                "code_line_count": len(kept_lines),
                "stripped_comment_line_count": result.stripped_comment_line_count,
                "inline_comment_trimmed_line_count": result.trimmed_line_count,
                "comment_strip_mode": result.mode,
                "comment_strip_note": result.note,
                "selected_line_start": 1,
                "selected_line_end": len(source_lines),
                "selected_line_count": len(kept_lines),
                "lines": kept_lines,
                "kinds": ["code"] * len(kept_lines),
            }
        )
    return records


def record_material_lines(record: dict[str, Any]) -> int:
    """Marker line plus the retained source lines."""
    return record["selected_line_count"] + 1


def assemble(records: list[dict[str, Any]], project: Path) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    all_lines: list[str] = []
    all_kinds: list[str] = []
    manifest_files: list[dict[str, Any]] = []
    for record in records:
        start = len(all_lines) + 1
        all_lines.append(f"// File: {record['path']}")
        all_kinds.append("marker")
        all_lines.extend(record["lines"])
        all_kinds.extend(record["kinds"])
        end = len(all_lines)
        entry = {key: value for key, value in record.items() if key not in {"lines", "kinds"}}
        entry["material_line_start"] = start
        entry["material_line_end"] = end
        manifest_files.append(entry)
    return all_lines, all_kinds, manifest_files


def paginate(lines: list[str], lines_per_page: int) -> list[list[str]]:
    return [lines[i : i + lines_per_page] for i in range(0, len(lines), lines_per_page)]


def nearest_file_boundary(boundaries: list[int], target: int) -> int:
    return min(boundaries, key=lambda b: abs(b - target))


def split_front_back_by_file_boundary(
    code_lines: list[str], files: list[dict[str, Any]], lines_per_page: int
) -> tuple[int, int]:
    """Fallback split: snap the cut to the nearest complete-file boundary.

    仅在无法通过重排使前后段恰好各 30 页时使用。返回 (front_end, back_start)。
    """
    target_front_lines = SEGMENT_PAGES * lines_per_page
    target_back_start = len(code_lines) - SEGMENT_PAGES * lines_per_page + 1
    file_ends = [item["material_line_end"] for item in files]
    file_starts = [item["material_line_start"] for item in files]

    front_end = nearest_file_boundary(file_ends, target_front_lines)
    back_start = nearest_file_boundary(file_starts, target_back_start)
    if back_start <= front_end:
        later_starts = [s for s in file_starts if s > front_end]
        back_start = min(later_starts) if later_starts else len(code_lines) + 1
    return front_end, back_start


# ---------------------------------------------------------------------------
# 页数对齐：在不截断任何文件的前提下重排文件顺序，使前后段各恰好 30 页
# ---------------------------------------------------------------------------


def segment_bounds(pages: int, lines_per_page: int) -> tuple[int, int]:
    """Line-count range whose ceil(lines / lines_per_page) equals `pages`."""
    return (pages - 1) * lines_per_page + 1, pages * lines_per_page


def greedy_run(sizes: list[int], order: list[int], limit: int) -> list[int]:
    """Take items along `order` while the running sum stays within `limit`."""
    chosen: list[int] = []
    total = 0
    for index in order:
        size = sizes[index]
        if total + size > limit:
            break
        total += size
        chosen.append(index)
    return chosen


def min_change_subset(
    positions: list[int], sizes: list[int], baseline: set[int], lo: int, hi: int
) -> list[int] | None:
    """Pick a subset of `positions` whose size sum falls in [lo, hi].

    在满足行数区间的前提下，最小化与 `baseline` 的差异数量（即被挪动的文件数），
    差异数相同时按被改动位置的先后作确定性排序，保证同样输入得到同样输出。
    """
    if lo > hi:
        return None
    # sum -> (changes, changed_positions, chosen_positions)
    best: dict[int, tuple[int, tuple[int, ...], tuple[int, ...]]] = {0: (0, (), ())}
    for position in positions:
        size = sizes[position]
        in_baseline = position in baseline
        nxt: dict[int, tuple[int, tuple[int, ...], tuple[int, ...]]] = {}
        for total, (changes, changed, chosen) in best.items():
            excluded = (
                changes + (1 if in_baseline else 0),
                changed + ((position,) if in_baseline else ()),
                chosen,
            )
            current = nxt.get(total)
            if current is None or excluded < current:
                nxt[total] = excluded
            new_total = total + size
            if new_total <= hi:
                included = (
                    changes + (0 if in_baseline else 1),
                    changed + (() if in_baseline else (position,)),
                    chosen + (position,),
                )
                current = nxt.get(new_total)
                if current is None or included < current:
                    nxt[new_total] = included
        best = nxt
    winner: tuple[int, int, tuple[int, ...], tuple[int, ...]] | None = None
    for total, (changes, changed, chosen) in best.items():
        if not lo <= total <= hi:
            continue
        # 先取改动最少的方案；改动数相同时取行数更多的方案，使末页更接近满页。
        candidate = (changes, -total, changed, chosen)
        if winner is None or candidate < winner:
            winner = candidate
    if winner is None:
        return None
    return list(winner[3])


def solve_segment(
    order: list[int], sizes: list[int], lo: int, hi: int, from_start: bool, window: int = REORDER_WINDOW
) -> list[int] | None:
    """Choose files forming one segment of exactly the requested page count.

    `order` 为当前文件顺序（元素是文件索引）。从头取段时结果即为新的前段，
    从尾取段时结果即为新的后段。搜索限定在边界前后各 `window` 个文件的窗口内，
    窗口外的文件按贪心结果强制保留或强制排除，避免文件较多时的组合爆炸。
    """
    if not order:
        return None
    scan = order if from_start else list(reversed(order))
    baseline_scan = greedy_run(sizes, scan, hi)
    baseline = set(baseline_scan)
    boundary = len(baseline_scan)
    low_bound = max(0, boundary - window)
    high_bound = min(len(scan), boundary + window)
    forced = [index for index in scan[:low_bound] if index in baseline]
    forced_sum = sum(sizes[index] for index in forced)
    flex = scan[low_bound:high_bound]
    chosen = min_change_subset(flex, sizes, baseline, lo - forced_sum, hi - forced_sum)
    if chosen is None:
        return None
    selected = set(forced) | set(chosen)
    return [index for index in order if index in selected]


def arrange_for_exact_pages(
    records: list[dict[str, Any]], lines_per_page: int, pages: int
) -> tuple[list[dict[str, Any]], int, int] | None:
    """Reorder whole files so the front and back segments each hit `pages` exactly.

    Returns (reordered records, front line count, back line count) or None.
    """
    sizes = [record_material_lines(record) for record in records]
    order = list(range(len(records)))
    lo, hi = segment_bounds(pages, lines_per_page)

    for front_first in (True, False):
        if front_first:
            first = solve_segment(order, sizes, lo, hi, from_start=True)
            if first is None:
                continue
            remaining = [index for index in order if index not in set(first)]
            second = solve_segment(remaining, sizes, lo, hi, from_start=False)
            if second is None:
                continue
            front, back = first, second
        else:
            first = solve_segment(order, sizes, lo, hi, from_start=False)
            if first is None:
                continue
            remaining = [index for index in order if index not in set(first)]
            second = solve_segment(remaining, sizes, lo, hi, from_start=True)
            if second is None:
                continue
            front, back = second, first
        front_set, back_set = set(front), set(back)
        middle = [index for index in order if index not in front_set and index not in back_set]
        new_order = front + middle + back
        front_lines = sum(sizes[index] for index in front)
        back_lines = sum(sizes[index] for index in back)
        return [records[index] for index in new_order], front_lines, back_lines
    return None


def page_of(line_no: int, lines_per_page: int) -> int:
    """1-based page index of a 1-based line number."""
    return (line_no + lines_per_page - 1) // lines_per_page


def assign_delivery_coordinates(
    files: list[dict[str, Any]],
    mode: str,
    front_end_line: int,
    back_start_line: int,
    front_pages: int,
    lines_per_page: int,
) -> None:
    """Record where each file actually lands in the delivered document.

    `material_line_start/end` 以整装序列为坐标系，该序列含未收录的中段文件；
    交付文档已剔除中段，后段文件因而整体前移一个中段长度。此处为每个进入交付
    文档的文件另记一组交付坐标 `rendered_line_start/end` 与 `page_start/page_end`，
    使抽查人员可按清单直接定位到文档中的实际位置。未收录文件的交付坐标为空。
    """
    split_mode = mode == "front30_back30"
    back_offset = (back_start_line - 1) - front_end_line if split_mode else 0
    for item in files:
        start = int(item["material_line_start"])
        end = int(item["material_line_end"])
        if not split_mode:
            item["segment"] = "全部"
            rendered_start, rendered_end = start, end
            page_start = page_of(rendered_start, lines_per_page)
            page_end = page_of(rendered_end, lines_per_page)
        elif end <= front_end_line:
            item["segment"] = "前段"
            rendered_start, rendered_end = start, end
            page_start = page_of(rendered_start, lines_per_page)
            page_end = page_of(rendered_end, lines_per_page)
        elif start >= back_start_line:
            item["segment"] = "后段"
            rendered_start, rendered_end = start - back_offset, end - back_offset
            # 后段的首页自文档第 front_pages + 1 页起算；前段末页可能不满页，
            # 故后段页码须以「相对后段起点的行号」换算，不能直接按整体行号取整。
            page_start = front_pages + page_of(rendered_start - front_end_line, lines_per_page)
            page_end = front_pages + page_of(rendered_end - front_end_line, lines_per_page)
        else:
            item["segment"] = "未收录"
            rendered_start = rendered_end = page_start = page_end = None
        item["rendered"] = rendered_start is not None
        item["rendered_line_start"] = rendered_start
        item["rendered_line_end"] = rendered_end
        item["page_start"] = page_start
        item["page_end"] = page_end


def write_pages_md(path: Path, title: str, software_name: str, version: str, pages: list[tuple[int, list[str]]]) -> None:
    chunks = [f"# {title}", "", f"软件名称：{software_name}", f"版本号：{version}", ""]
    for page_no, page_lines in pages:
        chunks.extend([f"## 第 {page_no} 页", "", "```text"])
        chunks.extend(page_lines)
        chunks.extend(["```", ""])
    path.write_text("\n".join(chunks), encoding="utf-8")


def write_manifest_md(path: Path, manifest: dict[str, Any]) -> None:
    overall = manifest["line_stats"]["selected_files"]
    rendered = manifest["line_stats"]["rendered_material"]
    lines = [
        "# 代码提取清单",
        "",
        f"- 软件名称：{manifest['software_name']}",
        f"- 版本号：{manifest['version']}",
        f"- 项目目录：{manifest['project_root']}",
        f"- 源码文件数：{manifest['file_count']}",
        f"- 整装材料行数（选中文件依次拼接后的总行数，含未收录中段）：{manifest['assembled_material_line_count']}",
        f"- 交付材料行数（写入代码材料文档的实际行数）：{manifest['rendered_material_line_count']}",
        f"- 未收录中段行数：{manifest['unrendered_material_line_count']}"
        f"（{manifest['unrendered_file_count']} 个文件未进入交付文档）",
        f"- 每页行数：{manifest['lines_per_page']}",
        f"- 总页数（全部候选材料按行数计算）：{manifest['total_pages']}",
        f"- 实际输出页数：{manifest['rendered_pages']}",
        f"- 前段页数：{manifest['front_pages']}",
        f"- 后段页数：{manifest['back_pages']}",
        f"- 目标页数：{manifest['target_pages']}",
        f"- 候选源码可生成页数：{manifest['available_candidate_pages']}",
        f"- 补充状态：{manifest['supplement_status']}",
        f"- 输出模式：{manifest['mode']}",
        f"- 页数对齐方式：{manifest['pagination_status']}",
        f"- 文件顺序：{manifest['order_status']}",
        "",
        "## 注释剔除说明",
        "",
        "材料已剔除全部注释，行号与源码不逐行对应。逐行回溯须以「源码行范围」列所示的整文件范围为准。",
        "剔除范围包括整行注释、行尾注释、跨行与行内块注释、JSX 注释、HTML/XML/Vue 模板注释与 Python 文档字符串；"
        "剔除后变为空白的行一并删除。Shebang（#!）与 Python 编码声明行属解释器指令，予以保留。",
        "",
    ]
    # 仅剔除整行注释的文件，以及后缀未识别、未作剔除的文件，均须提示人工复核。
    whole_line_files = [
        item
        for item in manifest["files"]
        if item.get("comment_strip_mode") == STRIP_MODE_WHOLE_LINE
        or (item.get("comment_strip_mode") == STRIP_MODE_NONE and item.get("comment_strip_note"))
    ]
    if whole_line_files:
        lines.extend(
            [
                f"> 注意：下列 {len(whole_line_files)} 个文件未能完整剔除注释（多为语言无法可靠逐字符解析，"
                "标注「仅剔除整行注释」），行尾注释与行内块注释可能残留，须人工复核：",
                ">",
            ]
        )
        for item in whole_line_files:
            note = item.get("comment_strip_note") or ""
            lines.append(f"> - `{item['path']}`（{note}）" if note else f"> - `{item['path']}`")
        lines.append("")
    lines.extend(
        [
            "## 行数构成统计",
            "",
            "「全部选中文件」一行反映所选源码剔除注释与空行后的行数构成，「实际输出材料」一行反映本次写入代码材料的行数构成。",
            "材料行数为有效代码行与文件标记行之和；已剔除注释行指整行均为注释、剔除后整行删除的行，"
            "行尾注释已清理行指去掉行尾或行内注释后仍保留代码的行。",
            "",
            "| 统计范围 | 有效代码行 | 文件标记行 | 材料行数 | 已剔除注释行 | 行尾注释已清理行 | 空行（已剔除） |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            f"| 全部选中文件 | {overall['code_line_count']} | {overall['marker_line_count']} | "
            f"{overall['material_line_count']} | {overall['stripped_comment_line_count']} | "
            f"{overall['inline_comment_trimmed_line_count']} | {overall['blank_line_count']} |",
            f"| 实际输出材料 | {rendered['code_line_count']} | {rendered['marker_line_count']} | "
            f"{rendered['material_line_count']} | {rendered['stripped_comment_line_count']} | "
            f"{rendered['inline_comment_trimmed_line_count']} | {rendered['blank_line_count']} |",
            "",
        ]
    )
    if manifest.get("pagination_warning"):
        lines.extend([f"> 页数告警：{manifest['pagination_warning']}", ""])
    lines.extend(
        [
            "## 文件来源",
            "",
            "表中行序即材料中的实际排列顺序。",
            "",
            "行号有两套坐标：「整装行范围」以全部选中文件依次拼接后的序列计数，含未收录的中段；",
            "「交付行范围」与「交付页码」以代码材料文档自身计数，即抽查时可直接定位的位置。",
            "两者对前段一致，对后段相差一个中段长度。未收录文件不进入交付文档，其交付坐标标注为「未收录」。",
            "",
            "| 序号 | 文件 | 所属段落 | 源码行数 | 源码行范围 | 抽取行数 | 已剔除注释行 | 空行 | 注释剔除方式 | 整装行范围 | 交付行范围 | 交付页码 |",
            "| ---: | --- | --- | ---: | --- | ---: | ---: | ---: | --- | --- | --- | --- |",
        ]
    )
    for order, item in enumerate(manifest["files"], start=1):
        if item.get("rendered"):
            rendered_range = f"{item['rendered_line_start']}-{item['rendered_line_end']}"
            if item["page_start"] == item["page_end"]:
                page_range = f"第 {item['page_start']} 页"
            else:
                page_range = f"第 {item['page_start']}-{item['page_end']} 页"
        else:
            rendered_range = "未收录"
            page_range = "未收录"
        lines.append(
            f"| {order} | `{item['path']}` | {item['segment']} | {item['source_line_count']} | "
            f"{item['selected_line_start']}-{item['selected_line_end']} | "
            f"{item['selected_line_count']} | {item['stripped_comment_line_count']} | "
            f"{item['blank_line_count']} | {STRIP_MODE_LABELS.get(item.get('comment_strip_mode'), '未知')} | "
            f"{item['material_line_start']}-{item['material_line_end']} | "
            f"{rendered_range} | {page_range} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def aggregate_kinds(kinds: list[str]) -> dict[str, int]:
    stats = empty_stats()
    for kind in kinds:
        if kind in stats:
            stats[kind] += 1
    return stats


def extract(
    project: Path,
    out_dir: Path,
    software_name: str,
    version: str,
    lines_per_page: int,
    selection_path: Path | None,
) -> dict[str, Any]:
    ensure_dir(out_dir)
    records = prepare_records(project, selection_path)
    if not records:
        raise SystemExit("No selected frontend source code files found for extraction.")

    total_material_lines = sum(record_material_lines(record) for record in records)
    total_pages = math.ceil(total_material_lines / lines_per_page)
    overview = selection_overview(selection_path, lines_per_page)
    available_lines = overview["available_lines"]
    available_pages = overview["available_pages"]
    unselected_count = overview["unselected_count"]
    # 不足 60 页时，只要仍有未选中的候选文件即停止：候选池本身不足 60 页并不构成
    # 直接输出「全部」的理由，「全部」须以已选文件覆盖全部候选文件为前提。
    if total_pages < SPLIT_THRESHOLD_PAGES and unselected_count > 0:
        preview_paths = overview["unselected_paths"][:5]
        preview = "、".join(preview_paths)
        if len(overview["unselected_paths"]) > len(preview_paths):
            preview += " 等"
        if available_pages >= SPLIT_THRESHOLD_PAGES:
            reason = f"而候选源码足以补齐至 {SPLIT_THRESHOLD_PAGES} 页"
        else:
            reason = (
                f"候选源码合计仅 {available_pages} 页，即便全选也不足 {SPLIT_THRESHOLD_PAGES} 页，"
                "但代码材料须涵盖全部可用源码方可按「全部」提交"
            )
        raise SystemExit(
            "STOP_FOR_USER\n"
            f"NEXT_ACTION: 当前已选代码仅 {total_pages} 页，{reason}。"
            f"尚有 {unselected_count} 个候选文件未选中（{preview}）。"
            "须在 草稿/代码文件选择.json 中继续选择补充文件，重新记录 code-selection 确认点后再行抽取。"
        )

    outputs: list[str] = []
    warnings: list[str] = []
    pagination_warning: str | None = None
    coverage_warning: str | None = None
    fallback_split = False
    original_order = [record["path"] for record in records]

    if total_pages >= SPLIT_THRESHOLD_PAGES:
        arrangement = arrange_for_exact_pages(records, lines_per_page, SEGMENT_PAGES)
        if arrangement is not None:
            records, front_line_count, back_line_count = arrangement
            code_lines, code_kinds, files = assemble(records, project)
            front_end = front_line_count
            back_start = len(code_lines) - back_line_count + 1
            pagination_status = "已按完整文件边界重排文件顺序，前后段各恰好 30 页"
        else:
            code_lines, code_kinds, files = assemble(records, project)
            front_end, back_start = split_front_back_by_file_boundary(code_lines, files, lines_per_page)
            fallback_split = True
            pagination_status = "重排后仍无法凑成 30+30 页，回退至最接近的完整文件边界切分"

        front_lines = code_lines[:front_end]
        front_kinds = code_kinds[:front_end]
        back_lines = code_lines[back_start - 1 :] if back_start <= len(code_lines) else []
        back_kinds = code_kinds[back_start - 1 :] if back_start <= len(code_lines) else []
        front = list(enumerate(paginate(front_lines, lines_per_page), start=1))
        back = [(len(front) + 1 + i, page) for i, page in enumerate(paginate(back_lines, lines_per_page))]
        front_pages, back_pages = len(front), len(back)
        rendered_pages = front_pages + back_pages
        rendered_stats = aggregate_kinds(front_kinds + back_kinds)

        if fallback_split and not (
            FALLBACK_MIN_SEGMENT_PAGES <= front_pages <= FALLBACK_MAX_SEGMENT_PAGES
            and FALLBACK_MIN_SEGMENT_PAGES <= back_pages <= FALLBACK_MAX_SEGMENT_PAGES
        ):
            raise SystemExit(
                "STOP_FOR_USER\n"
                f"NEXT_ACTION: 降级切分后前段 {front_pages} 页、后段 {back_pages} 页，共 {rendered_pages} 页，"
                f"已超出每段 {FALLBACK_MIN_SEGMENT_PAGES} 至 {FALLBACK_MAX_SEGMENT_PAGES} 页的允许偏差；"
                f"已选文件本可生成 {total_pages} 页，按此切分提交将构成材料不足。"
                "须在 草稿/代码文件选择.json 中调整抽取范围（例如增补体量较小的源文件，"
                f"使前后段的行数总和能落入每段 {segment_bounds(SEGMENT_PAGES, lines_per_page)[0]} 至 "
                f"{segment_bounds(SEGMENT_PAGES, lines_per_page)[1]} 行的区间），"
                "重新记录 code-selection 确认点后再行抽取。"
            )

        if front_pages == SEGMENT_PAGES and back_pages == SEGMENT_PAGES:
            combined_path = out_dir / "代码-前后各30页.md"
            title = (
                f"代码材料（前 {front_pages} 页与后 {back_pages} 页，共 {rendered_pages} 页，"
                "按完整文件边界对齐，不截断函数与方法）"
            )
        else:
            combined_path = out_dir / f"代码-前{front_pages}页+后{back_pages}页.md"
            title = (
                f"代码材料（前 {front_pages} 页与后 {back_pages} 页，共 {rendered_pages} 页，"
                "按完整文件边界对齐，不截断函数与方法）"
            )
            pagination_warning = (
                f"已选文件的行数组合无法在保持文件完整的前提下凑成 {SEGMENT_PAGES}+{SEGMENT_PAGES} 页，"
                f"实际输出为前 {front_pages} 页 + 后 {back_pages} 页，共 {rendered_pages} 页。"
                "如须精确 60 页，可在 代码文件选择.json 中增删文件（尤其是行数较小的文件），"
                "使前后段的行数总和能落入每段 "
                f"{segment_bounds(SEGMENT_PAGES, lines_per_page)[0]} 至 {segment_bounds(SEGMENT_PAGES, lines_per_page)[1]} 行的区间。"
            )
            warnings.append(pagination_warning)

        write_pages_md(combined_path, title, software_name, version, front + back)
        outputs.append(combined_path.name)
        mode = "front30_back30"
        front_end_line, back_start_line = front_end, back_start
    else:
        code_lines, code_kinds, files = assemble(records, project)
        all_pages = list(enumerate(paginate(code_lines, lines_per_page), start=1))
        # 「全部」以已选文件覆盖全部候选文件为前提。此处已通过上文的停止条件，
        # 唯一可能不成立的情形是选择文件缺失或结构异常，无从证明覆盖关系。
        complete_coverage = bool(overview["valid"] and overview["unselected_count"] == 0)
        if complete_coverage:
            all_path = out_dir / "代码-全部.md"
            all_title = "代码材料（全部）"
            mode = "all_under_60_pages"
            pagination_status = "总页数不足 60 页，且已选文件覆盖全部候选源码，输出全部代码材料"
        else:
            all_path = out_dir / "代码-已选部分.md"
            all_title = "代码材料（已选部分）"
            mode = "selected_under_60_pages"
            pagination_status = "总页数不足 60 页，且无法确认已选文件覆盖全部候选源码，输出已选部分代码材料"
            coverage_warning = (
                "无法依据 草稿/代码文件选择.json 确认已选文件已覆盖全部候选源码，"
                "本次材料不得称「全部」，已按「已选部分」命名与标注。"
                "须核对选择文件后重新抽取，方可按全部代码材料提交。"
            )
            warnings.append(coverage_warning)
        write_pages_md(all_path, all_title, software_name, version, all_pages)
        outputs.append(all_path.name)
        front_pages = len(all_pages)
        back_pages = 0
        rendered_pages = len(all_pages)
        rendered_stats = aggregate_kinds(code_kinds)
        front_end_line, back_start_line = len(code_lines), 0

    assign_delivery_coordinates(files, mode, front_end_line, back_start_line, front_pages, lines_per_page)

    overall_stats = empty_stats()
    for item in files:
        overall_stats["code"] += item["code_line_count"]
        overall_stats["blank"] += item["blank_line_count"]
        overall_stats["stripped_comment"] += item["stripped_comment_line_count"]
        overall_stats["trimmed"] += item["inline_comment_trimmed_line_count"]
        overall_stats["marker"] += 1
    overall_summary = stats_summary(overall_stats)
    # 有效代码行与文件标记行按交付材料的实际行序列计数；剔除类计数为文件级数据，
    # 前后段均以完整文件边界切分，故按进入交付文档的文件求和即可。
    for item in files:
        if item.get("rendered"):
            rendered_stats["blank"] += item["blank_line_count"]
            rendered_stats["stripped_comment"] += item["stripped_comment_line_count"]
            rendered_stats["trimmed"] += item["inline_comment_trimmed_line_count"]
    rendered_summary = stats_summary(rendered_stats)

    whole_line_only = [item["path"] for item in files if item["comment_strip_mode"] == STRIP_MODE_WHOLE_LINE]
    if whole_line_only:
        preview = "、".join(whole_line_only[:5]) + (" 等" if len(whole_line_only) > 5 else "")
        warnings.append(
            f"{len(whole_line_only)} 个文件仅剔除整行注释（{preview}），行尾注释与行内块注释可能残留，须人工复核。"
        )

    new_order = [record["path"] for record in records]
    order_status = "与 代码文件选择.json 一致" if new_order == original_order else "已为页数对齐重排，下表顺序为准"

    if available_pages >= SPLIT_THRESHOLD_PAGES:
        supplement_status = "候选源码可满足前 30 页与后 30 页的要求"
    elif mode == "all_under_60_pages":
        supplement_status = "候选源码不足 60 页，已选文件覆盖全部候选源码，按全部代码材料生成"
    elif mode == "selected_under_60_pages":
        supplement_status = "候选源码不足 60 页，且无法确认已选文件覆盖全部候选源码，按已选部分生成"
    else:
        supplement_status = "候选源码不足 60 页"

    manifest = {
        "software_name": software_name,
        "version": version,
        "project_root": str(project.resolve()),
        "file_count": len(files),
        # material_line_count 沿用旧键名，其口径为整装序列（含未收录中段）；
        # 下面两个键分别给出整装行数与交付行数，避免继续以单一数值指代两种口径。
        "material_line_count": len(code_lines),
        "assembled_material_line_count": len(code_lines),
        "rendered_material_line_count": rendered_summary["material_line_count"],
        "unrendered_material_line_count": len(code_lines) - rendered_summary["material_line_count"],
        "unrendered_file_count": sum(1 for item in files if not item.get("rendered")),
        "rendered_file_count": sum(1 for item in files if item.get("rendered")),
        "source_line_count": sum(item["source_line_count"] for item in files),
        "selected_source_line_count": sum(item["selected_line_count"] for item in files),
        "lines_per_page": lines_per_page,
        "total_pages": total_pages,
        "rendered_pages": rendered_pages,
        "front_pages": front_pages,
        "back_pages": back_pages,
        "target_pages": SPLIT_THRESHOLD_PAGES,
        "target_segment_pages": SEGMENT_PAGES,
        "available_candidate_line_count": available_lines,
        "available_candidate_pages": available_pages,
        "supplement_status": supplement_status,
        "candidate_file_count": overview["candidate_count"],
        "candidate_selected_count": overview["selected_count"],
        "candidate_unselected_count": unselected_count,
        "selection_file_valid": overview["valid"],
        "covers_all_candidates": bool(overview["valid"] and unselected_count == 0),
        "coverage_warning": coverage_warning,
        "fallback_split": fallback_split,
        "mode": mode,
        "pagination_status": pagination_status,
        "pagination_warning": pagination_warning,
        "pagination_exact": bool(mode != "front30_back30" or (front_pages == SEGMENT_PAGES and back_pages == SEGMENT_PAGES)),
        "order_status": order_status,
        "file_order_changed": new_order != original_order,
        "original_file_order": original_order,
        # 注释一律剔除，材料行号与源码不逐行对应；保留 strip_comments 键供旧读取方兼容。
        "comments_stripped": True,
        "strip_comments": True,
        "whole_line_only_strip_files": whole_line_only,
        "line_stats": {
            "selected_files": overall_summary,
            "rendered_material": rendered_summary,
        },
        "selection_file": str(selection_path) if selection_path else None,
        "outputs": outputs,
        "warnings": warnings,
        "files": files,
        "safe_software_filename": safe_filename(software_name),
    }
    write_json(out_dir / "代码提取清单.json", manifest)
    write_manifest_md(out_dir / "代码提取清单.md", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--analysis", help="Optional project analysis JSON; retained for workflow traceability")
    parser.add_argument("--software-name", required=True)
    parser.add_argument("--version", default="V1.0")
    parser.add_argument("--out-dir", default="软件著作权申请资料/草稿")
    parser.add_argument("--lines-per-page", type=int, default=LINES_PER_PAGE)
    parser.add_argument("--selection", help="Editable JSON file created by propose_code_selection.py")
    parser.add_argument(
        "--strip-comments",
        action="store_true",
        help="已弃用，无任何作用：注释现已始终剔除。保留该参数仅为兼容旧命令。",
    )
    args = parser.parse_args()
    if args.strip_comments:
        print("NOTE --strip-comments 已弃用：注释现已始终剔除，该参数不再产生任何作用")

    project = Path(args.project)
    if not project.exists():
        raise SystemExit(f"Project not found: {project}")
    if args.analysis and not Path(args.analysis).exists():
        raise SystemExit(f"Analysis JSON not found: {args.analysis}")

    selection = Path(args.selection) if args.selection else None
    if selection and not selection.exists():
        raise SystemExit(f"Selection JSON not found: {selection}")

    manifest = extract(
        project,
        Path(args.out_dir),
        args.software_name,
        args.version,
        args.lines_per_page,
        selection,
    )
    rendered = manifest["line_stats"]["rendered_material"]
    print(f"OK code drafts: {args.out_dir}")
    print(f"Selected files: {manifest['file_count']}")
    print(f"Mode: {manifest['mode']}")
    print(f"Total pages: {manifest['total_pages']}")
    print(
        f"Material lines: assembled {manifest['assembled_material_line_count']}, "
        f"rendered {manifest['rendered_material_line_count']} "
        f"(unrendered {manifest['unrendered_material_line_count']} in {manifest['unrendered_file_count']} files)"
    )
    print(f"Rendered pages: {manifest['rendered_pages']} (front {manifest['front_pages']} + back {manifest['back_pages']})")
    print(f"Pagination: {manifest['pagination_status']}")
    print(f"File order: {manifest['order_status']}")
    print(
        "Line stats (rendered): "
        f"code {rendered['code_line_count']}, marker {rendered['marker_line_count']}, "
        f"total {rendered['material_line_count']}; "
        f"stripped comment lines {rendered['stripped_comment_line_count']}, "
        f"inline comments trimmed {rendered['inline_comment_trimmed_line_count']}"
    )
    print("NOTE 材料已剔除全部注释，行号与源码不逐行对应")
    for warning in manifest["warnings"]:
        print(f"WARN {warning}")
    print(f"Outputs: {', '.join(manifest['outputs'])}")


if __name__ == "__main__":
    main()
