#!/usr/bin/env python3
"""Record explicit user confirmations for gated workflow stages."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import read_json, write_json


MAIN_FUNCTION_MIN_CHARS = 500
MAIN_FUNCTION_MAX_CHARS = 1300

SCREENSHOT_METHODS = ["video", "browser-automation", "desktop-automation", "user-supplied", "skip"]

LEGACY_SCREENSHOT_METHODS = {
    "chrome-devtools": "browser-automation",
    "computer-use": "desktop-automation",
}


def screenshot_method_arg(value: str) -> str:
    """Accept the retired spellings on the command line and map them to current values."""
    text = str(value or "").strip().lower()
    return LEGACY_SCREENSHOT_METHODS.get(text, text)


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json_or_empty(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return read_json(path)


def write_confirmation(path: Path, data: dict[str, Any], key: str, note: str) -> None:
    data[key] = True
    data["confirmation_note"] = note
    data["confirmed_at"] = timestamp()
    write_json(path, data)


def pending_application_fields(md_path: Path) -> list[str]:
    if not md_path.exists():
        return [f"缺少 {md_path}"]
    return [line.strip() for line in md_path.read_text(encoding="utf-8").splitlines() if "待用户确认" in line]


def effective_len(value: str) -> int:
    return len(str(value or "").replace(" ", "").replace("\n", ""))


def application_field_value(md_path: Path, field_name: str) -> str:
    prefix = f"➤{field_name}："
    for line in md_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


def application_field_issues(md_path: Path) -> list[str]:
    issues = pending_application_fields(md_path)
    if not md_path.exists():
        return issues

    main_function = application_field_value(md_path, "软件的主要功能")
    if not main_function:
        issues.append("➤软件的主要功能：缺少填写内容，须填写 500 至 1300 字。")
    elif "待用户确认" not in main_function:
        count = effective_len(main_function)
        if count < MAIN_FUNCTION_MIN_CHARS:
            issues.append(
                f"➤软件的主要功能：当前 {count} 字，少于 {MAIN_FUNCTION_MIN_CHARS} 字，须扩写至 500 至 1300 字。"
            )
        elif count > MAIN_FUNCTION_MAX_CHARS:
            issues.append(
                f"➤软件的主要功能：当前 {count} 字，超过 {MAIN_FUNCTION_MAX_CHARS} 字，须精简至 500 至 1300 字。"
            )
    return issues


def confirm_environment(workdir: Path, note: str) -> Path:
    out_path = workdir / "环境确认.json"
    data = load_json_or_empty(out_path)
    write_confirmation(out_path, data, "environment_confirmed", note)
    return out_path


def confirm_project(workdir: Path, note: str) -> Path:
    out_path = workdir / "项目确认.json"
    data = load_json_or_empty(out_path)
    write_confirmation(out_path, data, "project_confirmed", note)
    return out_path


def confirm_business(workdir: Path, note: str) -> Path:
    path = workdir / "草稿/业务理解.json"
    if not path.exists():
        raise SystemExit("Missing 草稿/业务理解.json")
    data = read_json(path)
    write_confirmation(path, data, "user_confirmed", note)
    return path


def confirm_code_selection(workdir: Path, note: str) -> Path:
    path = workdir / "草稿/代码文件选择.json"
    if not path.exists():
        raise SystemExit("Missing 草稿/代码文件选择.json")
    data = read_json(path)
    files = data.get("files") if isinstance(data, dict) else []
    selected = [item for item in files if isinstance(item, dict) and item.get("selected")]
    if not selected:
        raise SystemExit(
            "STOP_FOR_USER\n"
            "NEXT_ACTION: 代码文件选择尚未由模型填写。须先选定至少一个源码文件并填写选择理由，再交由用户确认。"
        )
    missing_reason = [item.get("path") for item in selected if not str(item.get("model_reason") or "").strip()]
    if data.get("model_selection_required") and missing_reason:
        raise SystemExit(
            "STOP_FOR_USER\n"
            "NEXT_ACTION: 已选源码缺少模型选择理由。须补全 `model_reason` 后方可确认。\n"
            + "\n".join(f"- {item}" for item in missing_reason[:20])
        )
    write_confirmation(path, data, "user_confirmed", note)
    return path


def parse_screenshot_method(method: str, note: str) -> str:
    """Map any accepted spelling to the current values.

    旧取值 chrome-devtools 与 computer-use 仍予识别，分别对应
    browser-automation 与 desktop-automation，以兼容已生成的 截图方式确认.json。
    """
    value = (method or note or "").lower()
    if any(key in value for key in ("skip", "no-screenshot", "none", "不截图", "跳过", "暂不", "先不", "不要截图", "无需截图")):
        return "skip"
    if any(key in value for key in ("video", "视频", "录屏", "录像")):
        return "video"
    if any(key in value for key in ("browser", "chrome", "devtools", "mcp", "浏览器")):
        return "browser-automation"
    if any(key in value for key in ("desktop", "computer", "电脑", "桌面")):
        return "desktop-automation"
    if any(key in value for key in ("user", "manual", "self", "手动", "自己", "用户")):
        return "user-supplied"
    raise SystemExit(
        "STOP_FOR_USER\n"
        "NEXT_ACTION: 须明确截图方式，取值为 video、browser-automation、desktop-automation、user-supplied 或 skip。"
    )


def confirm_screenshot_method(workdir: Path, note: str, method: str) -> Path:
    selected = parse_screenshot_method(method, note)
    out_path = workdir / "截图方式确认.json"
    data = load_json_or_empty(out_path)
    data["screenshot_method"] = selected
    write_confirmation(out_path, data, "screenshot_method_confirmed", note)
    return out_path


def confirm_application_fields(workdir: Path, note: str) -> Path:
    issues = application_field_issues(workdir / "草稿/申请表信息.md")
    if issues:
        raise SystemExit(
            "STOP_FOR_USER\n"
            "NEXT_ACTION: 申请表信息仍有字段未满足要求。须先补全或修正相应字段，再行确认。\n"
            + "\n".join(f"- {item}" for item in issues[:20])
        )
    out_path = workdir / "草稿/申请表字段确认.json"
    data = load_json_or_empty(out_path)
    write_confirmation(out_path, data, "application_fields_confirmed", note)
    return out_path


def confirm_markdown(workdir: Path, note: str) -> Path:
    issues = []
    business = workdir / "草稿/业务理解.json"
    selection = workdir / "草稿/代码文件选择.json"
    screenshot = workdir / "截图方式确认.json"
    fields = workdir / "草稿/申请表字段确认.json"

    if not business.exists() or not read_json(business).get("user_confirmed"):
        issues.append("业务理解尚未确认")
    if not selection.exists() or not read_json(selection).get("user_confirmed"):
        issues.append("代码文件选择尚未确认")
    if not screenshot.exists() or not read_json(screenshot).get("screenshot_method_confirmed"):
        issues.append("截图方式尚未确认")
    if not fields.exists() or not read_json(fields).get("application_fields_confirmed"):
        issues.append("申请表字段尚未确认")
    field_issues = application_field_issues(workdir / "草稿/申请表信息.md")
    if field_issues:
        issues.append("申请表信息仍有字段未满足要求")

    if issues:
        raise SystemExit(
            "STOP_FOR_USER\n"
            "NEXT_ACTION: Markdown 草稿确认前须先处理以下事项：\n"
            + "\n".join(f"- {item}" for item in issues)
        )

    out_path = workdir / "草稿/最终生成确认.json"
    data = load_json_or_empty(out_path)
    write_confirmation(out_path, data, "markdown_confirmed", note)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", default="软件著作权申请资料")
    parser.add_argument(
        "--stage",
        required=True,
        choices=[
            "environment",
            "project",
            "business",
            "code-selection",
            "screenshot-method",
            "application-fields",
            "markdown",
        ],
    )
    parser.add_argument("--note", default="用户已确认")
    parser.add_argument(
        "--method",
        type=screenshot_method_arg,
        choices=SCREENSHOT_METHODS,
        help="Screenshot capture method when --stage screenshot-method",
    )
    args = parser.parse_args()

    workdir = Path(args.workdir)
    if args.stage == "environment":
        path = confirm_environment(workdir, args.note)
    elif args.stage == "project":
        path = confirm_project(workdir, args.note)
    elif args.stage == "business":
        path = confirm_business(workdir, args.note)
    elif args.stage == "code-selection":
        path = confirm_code_selection(workdir, args.note)
    elif args.stage == "screenshot-method":
        path = confirm_screenshot_method(workdir, args.note, args.method or "")
    elif args.stage == "application-fields":
        path = confirm_application_fields(workdir, args.note)
    else:
        path = confirm_markdown(workdir, args.note)

    print(f"OK confirmation recorded: {args.stage}")
    print(path)


if __name__ == "__main__":
    main()
