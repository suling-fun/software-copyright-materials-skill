#!/usr/bin/env python3
"""Best-effort screenshot helpers for operation manuals."""

from __future__ import annotations

import argparse
import json
import shutil
import re
from pathlib import Path
from urllib.parse import urljoin

from common import ensure_dir, read_json, write_json


# 路径分隔符不得出现在文件名中，统一折叠为下划线（路由 /a/b 整理为 a_b）。
PATH_SEPARATOR_PATTERN = re.compile(r"[\\/]+")

# 各操作系统保留或不接受的字符：Windows 保留的 <>:"|?* 与全部控制字符。
# 除此之外的字符（含中文、假名、全角标点）一律保留，以免中文文件名退化为下划线。
RESERVED_CHAR_PATTERN = re.compile(r'[<>:"|?*\x00-\x1f\x7f]')

# Windows 保留设备名，作为文件名主干时无法创建，需加前缀规避。
WINDOWS_RESERVED_STEMS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

# 文件名主干的长度上限：字符数上限保持既有的 80，另按 UTF-8 字节数封顶，
# 避免中文名在 255 字节上限的文件系统上连同前缀与扩展名一起越界。
MAX_NAME_CHARS = 80
MAX_NAME_BYTES = 150


def safe_name(path: str) -> str:
    """Sanitise a route or file stem into a portable file name, keeping it readable.

    仅剔除真正不安全的字符：路径分隔符、控制字符，以及各操作系统保留的 <>:"/\\|?* 。
    中文与其他可读字符原样保留；纯点号（. 与 ..）一类可能造成路径穿越的名称一律回退为
    固定占位名。
    """
    value = str(path or "").strip()
    value = value.strip("/\\")
    if not value:
        return "home"
    value = PATH_SEPARATOR_PATTERN.sub("_", value)
    value = RESERVED_CHAR_PATTERN.sub("", value)
    # Windows 会自动截去结尾的点与空格，先行去除以免落盘名与预期名不一致。
    value = value.strip().rstrip(". ").strip("_").strip()
    if not value or set(value) <= {"."}:
        return "page"
    if value.split(".")[0].upper() in WINDOWS_RESERVED_STEMS:
        value = f"_{value}"
    value = value[:MAX_NAME_CHARS]
    while value and len(value.encode("utf-8")) > MAX_NAME_BYTES:
        value = value[:-1]
    return value or "page"


def reserved_names(out_dir: Path, exclude: set[Path]) -> set[str]:
    """Return the lower-cased names already present in `out_dir`.

    目标目录通常已经存放了由操作视频抽帧、按模块命名复制进来的截图，本脚本随后
    只为缺口模块补充。若不把这些既有文件预先占位，同名的用户截图会把它们直接覆盖。
    `exclude` 用于排除本次的来源文件自身：当来源目录即目标目录时，文件不应与自己重名。
    """
    if not out_dir.is_dir():
        return set()
    names: set[str] = set()
    for path in out_dir.iterdir():
        if not path.is_file():
            continue
        try:
            if path.resolve() in exclude:
                continue
        except OSError:
            pass
        names.add(path.name.lower())
    return names


def unique_target(out_dir: Path, stem: str, suffix: str, taken: set[str]) -> Path:
    """Reserve `<stem><suffix>` in `out_dir`, appending -2、-3…… only on collision.

    整理后的文件名保持与来源一致，不再无条件添加序号前缀；仅当与同一批次或目标
    目录中的已有文件重名时，才为后续同名文件追加序号以示区分。
    """
    candidate = f"{stem}{suffix}"
    if candidate.lower() not in taken:
        taken.add(candidate.lower())
        return out_dir / candidate
    for index in range(2, 1000):
        candidate = f"{stem}-{index}{suffix}"
        if candidate.lower() not in taken:
            taken.add(candidate.lower())
            return out_dir / candidate
    raise RuntimeError(f"unable to find a free file name for {stem}{suffix}")


def collect_manual_screenshots(input_dir: Path, out_dir: Path) -> dict[str, object]:
    out_dir = ensure_dir(out_dir)
    screenshots = []
    errors = []
    renamed = []
    allowed = {".png", ".jpg", ".jpeg", ".webp"}
    sources = [
        path
        for path in sorted(input_dir.iterdir())
        if path.is_file() and path.suffix.lower() in allowed
    ]
    source_paths = {path.resolve() for path in sources}
    # 预载目标目录中已有的文件名（大小写不敏感），使同名文件自动追加序号而非覆盖。
    taken: set[str] = reserved_names(out_dir, source_paths)
    for path in sources:
        desired = f"{safe_name(path.stem)}{path.suffix.lower()}"
        target = unique_target(out_dir, safe_name(path.stem), path.suffix.lower(), taken)
        if path.resolve() != target.resolve():
            shutil.copy2(path, target)
        entry = {"route": "", "url": "", "path": str(target), "source": str(path)}
        if target.name != desired:
            entry["renamed_from"] = desired
            entry["rename_reason"] = (
                f"目标目录中已存在同名文件 {desired}，为避免覆盖，本次整理改名为 {target.name}"
            )
            renamed.append(
                {"source": str(path), "desired_name": desired, "final_name": target.name}
            )
        screenshots.append(entry)
    if not screenshots:
        errors.append({"error": f"no screenshot images found in {input_dir}"})
    manifest = {
        "status": "ok" if screenshots else "empty",
        "method": "user-supplied",
        "screenshots": screenshots,
        "renamed": renamed,
        "errors": errors,
    }
    write_json(out_dir / "截图清单.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url")
    parser.add_argument("--analysis")
    parser.add_argument("--out-dir", default="软件著作权申请资料/截图")
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument("--manual-dir", help="Collect user-supplied screenshots from this directory")
    args = parser.parse_args()

    if args.manual_dir:
        manifest = collect_manual_screenshots(Path(args.manual_dir), Path(args.out_dir))
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        if not manifest["screenshots"]:
            raise SystemExit(3)
        return

    if not args.base_url or not args.analysis:
        raise SystemExit("Missing --base-url and --analysis unless --manual-dir is provided")

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        print(json.dumps({"status": "error", "reason": f"playwright unavailable: {exc}"}, ensure_ascii=False))
        raise SystemExit(2)

    analysis = read_json(Path(args.analysis))
    paths = analysis.get("routes") or ["/"]
    clean_paths = []
    for path in paths:
        if isinstance(path, str) and path.startswith("/") and path not in clean_paths:
            clean_paths.append(path)
    clean_paths = clean_paths[: args.max_pages] or ["/"]

    out_dir = ensure_dir(Path(args.out_dir))
    screenshots = []
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        for route in clean_paths:
            url = urljoin(args.base_url.rstrip("/") + "/", route.lstrip("/"))
            file_path = out_dir / f"{safe_name(route)}.png"
            try:
                page.goto(url, wait_until="networkidle", timeout=15_000)
                page.screenshot(path=str(file_path), full_page=True)
                screenshots.append({"route": route, "url": url, "path": str(file_path)})
            except Exception as exc:
                errors.append({"route": route, "url": url, "error": str(exc)})
        browser.close()

    manifest = {"status": "ok" if screenshots else "partial", "screenshots": screenshots, "errors": errors}
    write_json(out_dir / "截图清单.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if not screenshots:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
