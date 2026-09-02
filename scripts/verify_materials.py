#!/usr/bin/env python3
"""Pre-delivery verification for generated software copyright materials.

This is the "final gate" script for the software-copyright-materials skill.
It runs *after* `build_docx_from_md.py` has produced `正式资料/` and checks
everything that can be verified mechanically: file completeness, naming and
page-count consistency, leftover placeholder text, known boilerplate/fabrication
patterns, screenshot dimensions, application-form field length limits, and
whether every required human-confirmation gate was actually recorded.

What this script CANNOT check — because it requires reading the target
project's real source code, watching real screenshots, and judging whether
prose is genuinely grounded in the project instead of plausible-sounding
filler — is documented in `references/verification_checklist.md`. Those items
must be delegated to an independent agent/human, never rubber-stamped by the
same process that generated the materials.

Only the standard library is used so this script runs even when python-docx
is not installed; python-docx (already an optional dependency of this skill)
is used opportunistically to get more precise results (e.g. `doc.paragraphs`
text extraction and image dimensions via `doc.inline_shapes`), and every
check that needs it degrades to a WARN (not a crash) when it is unavailable.

Usage:
    python3 verify_materials.py --workdir 软件著作权申请资料
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

from common import read_json, read_text

try:
    # Reuse the exact 有效字符数 (whitespace/newline-stripped length) algorithm
    # used elsewhere in this skill, instead of re-implementing it and risking
    # the two counts drifting apart.
    from generate_application_info import effective_len
except Exception:  # pragma: no cover - defensive fallback if import layout changes
    def effective_len(value: str) -> int:
        """Fallback copy of generate_application_info.effective_len()'s algorithm.

        Kept in sync manually with that function; only used if the import above
        fails for some reason (e.g. this script is copied out of scripts/).
        """
        return len(str(value or "").replace(" ", "").replace("\n", ""))

try:
    from docx import Document

    DOCX_AVAILABLE = True
except Exception:  # pragma: no cover - exercised whenever python-docx is absent
    DOCX_AVAILABLE = False

try:
    # 与 build_docx_from_md.py 里图片尺寸约束保持一致：那两个常量就叫
    # IMAGE_MAX_WIDTH_IN / IMAGE_MAX_HEIGHT_IN，直接复用，避免两处数值不同步。
    from build_docx_from_md import IMAGE_MAX_WIDTH_IN, IMAGE_MAX_HEIGHT_IN
except Exception:  # pragma: no cover - defensive fallback if import layout changes
    IMAGE_MAX_WIDTH_IN = 5.8  # 与 build_docx_from_md.py 里图片尺寸约束保持一致
    IMAGE_MAX_HEIGHT_IN = 8.8  # 与 build_docx_from_md.py 里图片尺寸约束保持一致

# 图片尺寸比对的容忍误差（英寸），用于吸收 EMU 换算和排版取整带来的微小浮动。
IMAGE_SIZE_TOLERANCE_IN = 0.05


# ---------------------------------------------------------------------------
# 编造 / 套话黑名单
#
# 下列短语曾由生成环节硬编码产出，与具体项目无关，属模板套话；或系模型在操作
# 手册正文中编造插入的内容。一旦在最终正式文档中再次出现，即表明可能发生同类
# 回归。后续遇到同类问题，将新短语加入对应列表即可，无须改动检查逻辑。
#
# 无条件黑名单：任何时候出现都判定为问题。
UNCONDITIONAL_BLACKLIST_PHRASES = [
    "账号进入、业务创建",
    "再选择或创建业务对象",
]

# 条件黑名单：这几个词本身是常见的软件工程文档术语（总体设计说明书、详细设计
# 说明书、测试案例……）。只有在业务理解里没有把它们写进 manual_extra_sections
# 所声明的章节时，才当作套话处理；模型在 manual_extra_sections 中声明并给出
# evidence 的章节，其正文出现这几个词属正常引用。
CONDITIONAL_BLACKLIST_PHRASES = [
    "总体设计",
    "详细设计",
    "测试案例",
]

# 截图跳过时允许存在的占位字样；非跳过状态下出现即视为异常残留。
SCREENSHOT_PLACEHOLDER_TERMS = ["截图预留", "截图缺失", "截图无法插入"]

# 截图方式的历史取值，与当前取值语义一一对应，读取旧的 截图方式确认.json 时按此对齐。
LEGACY_SCREENSHOT_METHODS = {
    "chrome-devtools": "browser-automation",
    "computer-use": "desktop-automation",
}


def normalize_screenshot_method(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip().lower()
    return LEGACY_SCREENSHOT_METHODS.get(text, text or value)

# 申请表信息.txt 全文级别的未确认残留字样。
UNCONFIRMED_TERMS = ["待用户确认", "待补充"]

# 申请表字段长度限制：(字段名, 最少有效字符数(0 表示无下限), 最多有效字符数)
FIELD_LENGTH_LIMITS: list[tuple[str, int, int]] = [
    ("开发目的", 0, 50),
    ("面向领域 / 行业", 0, 50),
    ("软件的主要功能", 500, 1300),
    ("软件的技术特点", 0, 100),
]

FIELD_LINE_RE = re.compile(r"^➤([^：]+)：(.*)$")


class CheckResult:
    __slots__ = ("name", "status", "message")

    def __init__(self, name: str, status: str, message: str) -> None:
        self.name = name
        self.status = status  # "PASS" | "FAIL" | "WARN"
        self.message = message


RESULTS: list[CheckResult] = []


def record(name: str, status: str, message: str) -> None:
    if status not in ("PASS", "FAIL", "WARN"):
        raise ValueError(f"invalid status: {status}")
    RESULTS.append(CheckResult(name, status, message))


# ---------------------------------------------------------------------------
# DOCX helpers (zipfile-based, python-docx optional)


def docx_header_text(docx_path: Path) -> str | None:
    """Concatenate all <w:t> text found in the first word/headerN.xml part."""
    try:
        with zipfile.ZipFile(docx_path) as zf:
            names = zf.namelist()
            header_name = next(
                (n for n in sorted(names) if re.fullmatch(r"word/header\d*\.xml", n)), None
            )
            if not header_name:
                return None
            xml = zf.read(header_name).decode("utf-8", errors="replace")
        return "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))
    except Exception:
        return None


def docx_page_break_count(docx_path: Path) -> int | None:
    """Count explicit <w:br w:type="page"/> breaks in word/document.xml."""
    try:
        with zipfile.ZipFile(docx_path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
        return len(re.findall(r'<w:br\b[^>]*\bw:type="page"[^>]*/?>', xml))
    except Exception:
        return None


def docx_body_text(docx_path: Path) -> str:
    """Return the concatenated body text of a docx file.

    Uses python-docx's `doc.paragraphs` when available; otherwise falls back to
    zipfile + a plain <w:t> regex over word/document.xml so this still works
    without python-docx installed.
    """
    if DOCX_AVAILABLE:
        try:
            doc = Document(str(docx_path))
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception:
            pass
    try:
        with zipfile.ZipFile(docx_path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
        return "\n".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 申请表信息.txt parsing


def parse_application_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        match = FIELD_LINE_RE.match(line)
        if match:
            fields[match.group(1).strip()] = match.group(2).strip()
    return fields


# ---------------------------------------------------------------------------
# Checks


def find_one(formal_dir: Path, pattern: str, label: str, check_prefix: str) -> Path | None:
    check_name = f"{check_prefix} / {label}"
    matches = sorted(formal_dir.glob(pattern)) if formal_dir.exists() else []
    if not matches:
        record(check_name, "FAIL", f"未在 {formal_dir} 找到匹配 `{pattern}` 的文件")
        return None
    extra_note = ""
    if len(matches) > 1:
        extra_note = f"（找到 {len(matches)} 个匹配文件，本次校验使用第一个：{', '.join(p.name for p in matches)}）"
    path = matches[0]
    size = path.stat().st_size
    if size > 0:
        record(check_name, "WARN" if extra_note else "PASS", f"{path} 存在且非空（{size} 字节）{extra_note}")
    else:
        record(check_name, "FAIL", f"{path} 存在但文件大小为 0 字节{extra_note}")
        return None
    return path


def check_file_completeness(formal_dir: Path) -> tuple[Path | None, Path | None, Path | None]:
    check_prefix = "1. 文件完整性"

    app_txt = formal_dir / "申请表信息.txt"
    if not formal_dir.exists():
        record(check_prefix, "FAIL", f"正式资料目录不存在：{formal_dir}")
        return None, None, None
    if app_txt.exists() and app_txt.stat().st_size > 0:
        record(f"{check_prefix} / 申请表信息.txt", "PASS", f"{app_txt} 存在且非空（{app_txt.stat().st_size} 字节）")
    elif app_txt.exists():
        record(f"{check_prefix} / 申请表信息.txt", "FAIL", f"{app_txt} 存在但文件大小为 0 字节")
        app_txt = None
    else:
        record(f"{check_prefix} / 申请表信息.txt", "FAIL", f"缺少文件：{app_txt}")
        app_txt = None

    code_docx = find_one(formal_dir, "*-代码(*).docx", "代码材料 docx（*-代码(*).docx）", check_prefix)
    manual_docx = find_one(formal_dir, "*_操作手册.docx", "操作手册 docx（*_操作手册.docx）", check_prefix)
    return app_txt, code_docx, manual_docx


def check_naming_consistency(
    app_fields: dict[str, str], code_docx: Path | None, manual_docx: Path | None
) -> None:
    check_name = "2. 命名一致性"
    software_name = app_fields.get("软件全称", "").strip()
    version = app_fields.get("版本号", "").strip()

    if not software_name or "待用户确认" in software_name:
        record(check_name, "FAIL", f"申请表信息.txt 的「软件全称」字段异常，无法作为命名基准：{software_name!r}")
        return
    if not version or "待用户确认" in version:
        record(check_name, "FAIL", f"申请表信息.txt 的「版本号」字段异常，无法作为命名基准：{version!r}")
        return

    expected_header = f"{software_name} {version}"

    for label, path in (("代码材料 docx", code_docx), ("操作手册 docx", manual_docx)):
        if path is None:
            record(f"{check_name} / {label}", "WARN", "文件缺失（已在文件完整性检查中报告），跳过命名比对")
            continue

        if not path.name.startswith(software_name):
            record(
                f"{check_name} / {label} 文件名",
                "FAIL",
                f"期望文件名以软件全称「{software_name}」开头，实际文件名为 {path.name!r}",
            )
        else:
            record(f"{check_name} / {label} 文件名", "PASS", f"{path.name} 以软件全称「{software_name}」开头")

        header_text = docx_header_text(path)
        if header_text is None:
            record(
                f"{check_name} / {label} 页眉",
                "WARN",
                f"未能从 {path.name} 提取页眉文字（可能缺少 word/header*.xml 或读取失败），跳过页眉一致性比对",
            )
        elif expected_header not in header_text:
            record(
                f"{check_name} / {label} 页眉",
                "FAIL",
                f"期望页眉文字包含「{expected_header}」（软件全称+版本号），实际页眉文字为 {header_text!r}",
            )
        else:
            record(f"{check_name} / {label} 页眉", "PASS", f"页眉文字包含「{expected_header}」")


def check_page_count(app_fields: dict[str, str], code_docx: Path | None) -> None:
    check_name = "3. 页数一致性"
    if code_docx is None:
        record(check_name, "WARN", "代码材料 docx 缺失（已在文件完整性检查中报告），跳过页数比对")
        return

    declared = app_fields.get("页数", "").strip()
    if not declared.isdigit():
        record(check_name, "FAIL", f"申请表信息.txt 的「页数」字段不是纯数字：{declared!r}")
        return
    declared_pages = int(declared)

    break_count = docx_page_break_count(code_docx)
    if break_count is None:
        record(check_name, "WARN", f"未能读取 {code_docx.name} 的 word/document.xml 分页符，跳过页数比对")
        return
    actual_pages = break_count + 1

    if declared_pages != actual_pages:
        record(
            check_name,
            "FAIL",
            f"申请表「页数」字段为 {declared_pages}，但 {code_docx.name} 实际分页符数量为 {break_count}"
            f"（真实页数 = 分页符数量 + 1 = {actual_pages}），两者不一致",
        )
    else:
        record(check_name, "PASS", f"申请表「页数」= {declared_pages}，与 {code_docx.name} 实际页数一致")


def check_placeholder_residue(workdir: Path, app_txt: Path | None, manual_docx: Path | None) -> None:
    check_name = "4. 占位符/未确认内容残留"

    if app_txt is not None:
        text = read_text(app_txt)
        residue_terms = [t for t in UNCONFIRMED_TERMS if t in text]
        if residue_terms:
            record(
                f"{check_name} / 申请表信息.txt",
                "FAIL",
                f"申请表信息.txt 全文中仍含有未确认字样：{residue_terms}，说明存在字段未被真正补全就进入了正式材料",
            )
        else:
            record(f"{check_name} / 申请表信息.txt", "PASS", "申请表信息.txt 未发现「待用户确认」「待补充」字样")
    else:
        record(f"{check_name} / 申请表信息.txt", "WARN", "申请表信息.txt 缺失（已在文件完整性检查中报告），跳过残留检测")

    if manual_docx is None:
        record(f"{check_name} / 操作手册截图占位", "WARN", "操作手册 docx 缺失（已在文件完整性检查中报告），跳过截图占位检测")
        return

    manual_text = docx_body_text(manual_docx)
    placeholder_terms = [t for t in SCREENSHOT_PLACEHOLDER_TERMS if t in manual_text]
    if not placeholder_terms:
        record(f"{check_name} / 操作手册截图占位", "PASS", "操作手册正文未发现截图占位字样")
        return

    screenshot_json = workdir / "截图方式确认.json"
    screenshot_method = None
    if screenshot_json.exists():
        try:
            screenshot_method = normalize_screenshot_method(read_json(screenshot_json).get("screenshot_method"))
        except Exception:
            screenshot_method = None

    if screenshot_method == "skip":
        record(
            f"{check_name} / 操作手册截图占位",
            "WARN",
            f"操作手册正文含有截图占位字样 {placeholder_terms}，但 截图方式确认.json 记录 screenshot_method=\"skip\"，"
            "属于用户明确选择暂不截图时的预期状态，不算错误",
        )
    else:
        record(
            f"{check_name} / 操作手册截图占位",
            "FAIL",
            f"操作手册正文含有截图占位字样 {placeholder_terms}，但 截图方式确认.json 的 screenshot_method="
            f"{screenshot_method!r}（不是 \"skip\"），说明截图理应已经生成插入，却仍残留占位文字",
        )


MANUAL_HEADING_RE = re.compile(r"^\s*(?:\(\d+\)|[零一二三四五六七八九十百]+)、\s*(.+?)\s*$")


def manual_sections_containing(manual_text: str, phrase: str) -> list[str]:
    """Return the titles of the chapters whose body contains `phrase`.

    章节边界按正文里的中文大写序号标题行切分；出现在任何标题之前的文字归入空标题。
    """
    owners: list[str] = []
    current = ""
    for line in manual_text.splitlines():
        heading = MANUAL_HEADING_RE.match(line)
        if heading:
            current = heading.group(1)
            if phrase in current:
                owners.append(current)
            continue
        if phrase in line:
            owners.append(current)
    return owners


def check_fabricated_phrases(workdir: Path, manual_docx: Path | None) -> None:
    check_name = "5. 编造/套话内容检测"
    if manual_docx is None:
        record(check_name, "WARN", "操作手册 docx 缺失（已在文件完整性检查中报告），跳过套话检测")
        return

    manual_text = docx_body_text(manual_docx)

    hit = [p for p in UNCONDITIONAL_BLACKLIST_PHRASES if p in manual_text]
    if hit:
        record(f"{check_name} / 固定黑名单", "FAIL", f"操作手册正文含有已知问题套话：{hit}")
    else:
        record(f"{check_name} / 固定黑名单", "PASS", "操作手册正文未发现固定黑名单套话")

    business_path = workdir / "草稿" / "业务理解.json"
    extra_section_titles: list[str] = []
    if business_path.exists():
        try:
            business = read_json(business_path)
        except Exception:
            business = {}
        raw_extra = business.get("manual_extra_sections")
        if isinstance(raw_extra, list):
            extra_section_titles = [
                str(item.get("title") or "").strip()
                for item in raw_extra
                if isinstance(item, dict) and str(item.get("title") or "").strip()
            ]

    cond_check = f"{check_name} / 条件黑名单（总体设计/详细设计/测试案例）"
    undeclared: list[str] = []
    declared: list[str] = []
    for phrase in CONDITIONAL_BLACKLIST_PHRASES:
        if phrase not in manual_text:
            continue
        owners = sorted(set(manual_sections_containing(manual_text, phrase)))
        if owners and all(owner in extra_section_titles for owner in owners):
            declared.append(f"{phrase}（位于自定章节：{'、'.join(owners)}）")
        else:
            undeclared.append(phrase)

    if undeclared:
        record(
            cond_check,
            "FAIL",
            "下列字样未处于 manual_extra_sections 所声明的章节范围内，"
            f"操作手册正文含有疑似套话：{undeclared}",
        )
    elif declared:
        record(
            cond_check,
            "PASS",
            f"相关字样均位于 manual_extra_sections 声明的章节内，视为模型已声明并附证据的正常引用：{declared}",
        )
    else:
        record(cond_check, "PASS", "操作手册正文未发现总体设计/详细设计/测试案例字样")


def check_screenshot_dimensions(manual_docx: Path | None) -> None:
    check_name = "6. 截图健全性"
    if manual_docx is None:
        record(check_name, "WARN", "操作手册 docx 缺失（已在文件完整性检查中报告），跳过截图尺寸检测")
        return
    if not DOCX_AVAILABLE:
        record(check_name, "WARN", "python-docx 不可用，跳过截图宽高检测。该检查依赖 python-docx 的 inline_shapes API，无法改由 zipfile 缺省实现")
        return

    try:
        doc = Document(str(manual_docx))
    except Exception as exc:
        record(check_name, "WARN", f"无法用 python-docx 打开 {manual_docx.name}：{exc}")
        return

    shapes = list(doc.inline_shapes)
    if not shapes:
        record(check_name, "WARN", f"{manual_docx.name} 中未发现任何内嵌图片（inline_shapes 为空）。若操作手册应含截图，须人工核实是否存在漏插")
        return

    problems: list[str] = []
    for idx, shape in enumerate(shapes, start=1):
        try:
            width_in = shape.width / 914400
            height_in = shape.height / 914400
        except Exception as exc:
            problems.append(f"第 {idx} 张图片无法读取宽高：{exc}")
            continue
        if width_in > IMAGE_MAX_WIDTH_IN + IMAGE_SIZE_TOLERANCE_IN:
            problems.append(
                f"第 {idx} 张图片宽度 {width_in:.3f} 英寸，超过上限 {IMAGE_MAX_WIDTH_IN} 英寸（含 {IMAGE_SIZE_TOLERANCE_IN} 英寸容差）"
            )
        if height_in > IMAGE_MAX_HEIGHT_IN + IMAGE_SIZE_TOLERANCE_IN:
            problems.append(
                f"第 {idx} 张图片高度 {height_in:.3f} 英寸，超过上限 {IMAGE_MAX_HEIGHT_IN} 英寸（含 {IMAGE_SIZE_TOLERANCE_IN} 英寸容差）"
            )

    if problems:
        record(check_name, "FAIL", f"{manual_docx.name} 共 {len(shapes)} 张内嵌图片，其中 {len(problems)} 处尺寸超出约束：" + "；".join(problems))
    else:
        record(
            check_name,
            "PASS",
            f"{manual_docx.name} 共 {len(shapes)} 张内嵌图片，宽高均未超过约束（≤{IMAGE_MAX_WIDTH_IN}in × {IMAGE_MAX_HEIGHT_IN}in，含 {IMAGE_SIZE_TOLERANCE_IN} 英寸容差）",
        )


def check_field_lengths(app_fields: dict[str, str]) -> None:
    check_name = "7. 申请表字段格式"
    if not app_fields:
        record(check_name, "WARN", "申请表信息.txt 缺失或为空（已在文件完整性检查中报告），跳过字段长度检测")
        return

    for field_name, min_chars, max_chars in FIELD_LENGTH_LIMITS:
        sub_check = f"{check_name} / {field_name}"
        value = app_fields.get(field_name, "")
        if not value:
            record(sub_check, "FAIL", "字段缺失或为空")
            continue
        if "待用户确认" in value:
            record(sub_check, "WARN", "字段仍含「待用户确认」，跳过长度校验。该情形应已在第 4 项占位符残留检测中报出")
            continue

        length = effective_len(value)
        bound_text = f"{min_chars} 至 {max_chars} 字" if min_chars else f"≤{max_chars} 字符"
        if min_chars and length < min_chars:
            record(sub_check, "FAIL", f"有效字符数（去空格/换行）为 {length}，低于下限 {min_chars}；限制为 {bound_text}")
        elif length > max_chars:
            record(sub_check, "FAIL", f"有效字符数（去空格/换行）为 {length}，超过上限 {max_chars}；限制为 {bound_text}")
        else:
            record(sub_check, "PASS", f"有效字符数（去空格/换行）为 {length}，符合限制（{bound_text}）")


def check_gates(workdir: Path) -> None:
    check_name = "8. 门禁记录完整性"

    env_check_path = workdir / "环境检查.json"
    requires_user_input = False
    if env_check_path.exists():
        try:
            requires_user_input = bool(read_json(env_check_path).get("requires_user_input"))
        except Exception:
            record(f"{check_name} / 环境检查.json", "WARN", f"{env_check_path} 存在但无法解析为 JSON，按不需要 environment 门禁处理")
    else:
        record(f"{check_name} / 环境检查.json", "WARN", f"{env_check_path} 缺失，无法判断是否需要 environment 门禁，按不需要处理")

    env_gate_check = f"{check_name} / environment"
    if requires_user_input:
        env_confirm_path = workdir / "环境确认.json"
        confirmed = env_confirm_path.exists()
        if confirmed:
            try:
                confirmed = bool(read_json(env_confirm_path).get("environment_confirmed"))
            except Exception:
                confirmed = False
        if confirmed:
            record(env_gate_check, "PASS", "环境检查显示需要用户选择，且 环境确认.json 已记录 environment_confirmed=true")
        else:
            record(
                env_gate_check,
                "FAIL",
                "环境检查.json 显示 requires_user_input=true（python-docx 与 pandoc 均不可用），"
                f"但 {env_confirm_path} 缺失或未记录 environment_confirmed=true",
            )
    else:
        record(env_gate_check, "PASS", "环境检查.json 显示 requires_user_input=false（或缺失按不需要处理），无需 environment 门禁")

    gate_specs: list[tuple[str, Path, str]] = [
        ("business", workdir / "草稿" / "业务理解.json", "user_confirmed"),
        ("code-selection", workdir / "草稿" / "代码文件选择.json", "user_confirmed"),
        ("screenshot-method", workdir / "截图方式确认.json", "screenshot_method_confirmed"),
        ("application-fields", workdir / "草稿" / "申请表字段确认.json", "application_fields_confirmed"),
        ("markdown", workdir / "草稿" / "最终生成确认.json", "markdown_confirmed"),
    ]
    for stage, path, key in gate_specs:
        sub_check = f"{check_name} / {stage}"
        if not path.exists():
            record(sub_check, "FAIL", f"缺少门禁文件：{path}")
            continue
        try:
            data = read_json(path)
        except Exception as exc:
            record(sub_check, "FAIL", f"{path} 存在但无法解析为 JSON：{exc}")
            continue
        value = data.get(key)
        if value:
            record(sub_check, "PASS", f"{path} 已记录 {key}={value!r}")
        else:
            record(sub_check, "FAIL", f"{path} 存在，但 {key} 字段为 {value!r}（缺失或未确认），说明该门禁未被真正记录")


# ---------------------------------------------------------------------------
# Report rendering


def render_report() -> tuple[str, dict[str, int]]:
    counts = {"PASS": 0, "FAIL": 0, "WARN": 0}
    lines = ["# 校验报告", "", "程序化校验结果（`scripts/verify_materials.py`），逐项列出：", ""]
    for result in RESULTS:
        counts[result.status] += 1
        lines.append(f"- [{result.status}] {result.name}：{result.message}")
    lines.append("")
    lines.append(f"**汇总：通过 {counts['PASS']} 项、失败 {counts['FAIL']} 项、警告 {counts['WARN']} 项。**")
    lines.append("")
    lines.append(
        "本报告仅覆盖可程序化判断的项目。代码真实性、业务真实性、截图匹配度等须由"
        "独立 agent 或人工核实的项目，见 `references/verification_checklist.md` 第二部分，"
        "本项校验无法亦不应替代该部分工作。"
    )
    return "\n".join(lines), counts


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="软著正式材料交付前程序化校验")
    parser.add_argument("--workdir", default="软件著作权申请资料", help="skill 输出目录（默认为 软件著作权申请资料）")
    args = parser.parse_args()

    workdir = Path(args.workdir)
    formal_dir = workdir / "正式资料"

    app_txt, code_docx, manual_docx = check_file_completeness(formal_dir)
    app_fields = parse_application_fields(read_text(app_txt)) if app_txt is not None else {}

    check_naming_consistency(app_fields, code_docx, manual_docx)
    check_page_count(app_fields, code_docx)
    check_placeholder_residue(workdir, app_txt, manual_docx)
    check_fabricated_phrases(workdir, manual_docx)
    check_screenshot_dimensions(manual_docx)
    check_field_lengths(app_fields)
    check_gates(workdir)

    report_text, counts = render_report()
    print(report_text)

    report_path = workdir / "校验报告.md"
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_text + "\n", encoding="utf-8")
        print(f"\n校验报告已写入：{report_path}")
    except Exception as exc:
        print(f"\n警告：校验报告写入失败：{exc}", file=sys.stderr)

    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
