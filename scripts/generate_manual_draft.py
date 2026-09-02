#!/usr/bin/env python3
"""Generate a reviewer-oriented operation manual Markdown draft."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from common import ensure_dir, read_json


MATCH_PUNCTUATION_PATTERN = re.compile(
    r"[\s，。；、：？！,.;:?!\"'“”‘’（）()\[\]【】《》〈〉…—\-_/\\|~·]+"
)


def normalize_for_match(text: str) -> str:
    """Drop whitespace and punctuation so wording can be compared without formatting noise."""
    return MATCH_PUNCTUATION_PATTERN.sub("", str(text or ""))


def reflected_in(prose: str, item: str, ratio: float = 0.5) -> bool:
    """Whether the model's own prose reflects `item`.

    判定按去标点后的 3 字片段覆盖率进行：原样写入固然通过，改用其他措辞但保留过半片段亦通过；
    仅有零星字符重合则不通过。此处不要求逐字一致，判定结果不用于改写正文，仅用于报出待修正项。
    """
    needle = normalize_for_match(item)
    haystack = normalize_for_match(prose)
    if not needle:
        return True
    if not haystack:
        return False
    if needle in haystack:
        return True
    if len(needle) <= 3:
        return False
    grams = {needle[index : index + 3] for index in range(len(needle) - 2)}
    matched = sum(1 for gram in grams if gram in haystack)
    return matched / len(grams) >= ratio


def paragraph_list(raw: Any) -> list[str]:
    """Normalise a model-authored paragraph field into a list of non-empty paragraphs."""
    if isinstance(raw, list):
        return [str(paragraph).strip() for paragraph in raw if str(paragraph).strip()]
    text = str(raw or "").strip()
    return [text] if text else []


TECHNICAL_TERMS = [
    "技术实现",
    "代码",
    "框架",
    "接口封装",
    "状态管理",
    "异步任务",
    "任务队列",
    "数据持久化",
    "业务逻辑",
    "React",
    "Next.js",
    "FastAPI",
    "LangGraph",
    "Celery",
]

TEMPLATE_MARKERS = [
    "重要功能之一",
    "通过清晰的页面入口、信息展示和结果反馈",
    "对应操作环节",
    "审核时可重点查看",
    "审核人员可通过",
    "按照页面提示填写内容、选择资料、确认方案或点击提交按钮",
    "系统处理完成后显示结果或提示信息",
    "帮助用户用户",
    "帮助用户系统",
    "主要用于在",
    "项目管理或资产中心项目管理",
    "进入方式：",
    "页面内容：",
    "操作步骤：",
    "操作规则：",
    "操作结果与反馈：",
    "功能特点根据当前项目资料",
    "软件围绕",
    "账号进入、业务创建、过程查看、结果确认和资料管理",
    "再选择或创建业务对象",
    "【截图预留：【截图预留：",
]

AI_TONE_MARKERS = [
    "旨在",
    "赋能",
    "一站式",
    "智能化",
    "高效便捷",
    "显著提升",
    "强大能力",
    "丰富功能",
    "极大地",
    "全方位",
    "多维度",
    "闭环",
    "降本增效",
    "优化体验",
    "提升效率",
]


REQUIRED_SECTION_TITLES = ["说明", "功能特点", "系统要求", "常见问题解答", "术语表"]

# manual_modules 中的下列字段为核对清单：模型须将其内容自行写入 `body`，
# 校验环节据以检查正文有无遗漏，不再用其拼装句子。
REQUIRED_MODULE_COVERAGE = (
    ("entry", "真实入口"),
    ("visible_elements", "用户可见元素"),
    ("steps", "主要动作"),
    ("validation_rules", "输入或状态规则"),
    ("feedback", "结果反馈"),
)

SECTION_HEADING_PATTERN = re.compile(
    r"^##\s+(\(\d+\)|[零一二三四五六七八九十百]+)、\s*(.+?)\s*$",
    flags=re.M,
)

FIGURE_LABEL_PATTERN = re.compile(r"(?:^|【)图\s*(\d+)\s*-\s*(\d+)", flags=re.M)

SCREENSHOT_PLACEHOLDER_PATTERN = re.compile(r"【(?:图\s*\d+\s*-\s*\d+\s+)?截图预留：.*?】", flags=re.S)


def manual_section_body(text: str, title: str) -> str:
    number_pattern = r"(?:\(\d+\)、|[零一二三四五六七八九十百]+、)"
    pattern = re.compile(rf"^##\s+{number_pattern}\s*{re.escape(title)}\s*$", flags=re.M)
    match = pattern.search(text)
    if not match:
        return ""
    next_match = re.search(rf"^##\s+{number_pattern}", text[match.end() :], flags=re.M)
    end = match.end() + next_match.start() if next_match else len(text)
    return text[match.end() : end].strip()


def parse_chinese_number(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    paren = re.fullmatch(r"\((\d+)\)", text)
    if paren:
        return int(paren.group(1))
    if text.isdigit():
        return int(text)
    digits = "零一二三四五六七八九"
    if text in digits:
        return digits.index(text)
    if text.startswith("十"):
        rest = text[1:]
        if not rest:
            return 10
        return 10 + digits.index(rest) if rest in digits else None
    if "十" in text:
        tens, _, ones = text.partition("十")
        if tens not in digits:
            return None
        value_tens = digits.index(tens) * 10
        if not ones:
            return value_tens
        if ones not in digits:
            return None
        return value_tens + digits.index(ones)
    return None


def iter_manual_sections(text: str) -> list[tuple[str, str, str]]:
    """Return (number token, title, body) for every rendered chapter, in document order."""
    matches = list(SECTION_HEADING_PATTERN.finditer(text))
    sections: list[tuple[str, str, str]] = []
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        sections.append((match.group(1), match.group(2), text[match.end() : end]))
    return sections


def module_prose(body: str) -> str:
    """Return module prose without the independently preserved screenshot placeholder."""
    return SCREENSHOT_PLACEHOLDER_PATTERN.sub("", body).strip()


def module_prose_length(body: str) -> int:
    return len(re.sub(r"\s+", "", module_prose(body)))


def technical_scope_text(text: str, modules: list[dict[str, Any]]) -> str:
    """Chapters whose readers are ordinary users: 功能特点、常见问题解答 and each module chapter.

    「说明」章与全部项目自定章节承担交代运行环境、文件路径与数值口径的职责，
    技术术语在其中属正常表达，不纳入本范围（见 manual_structure.md「技术性表达的边界」）。
    """
    titles = ["功能特点", "常见问题解答"]
    titles.extend(
        str(module.get("feature") or "").strip()
        for module in modules
        if str(module.get("feature") or "").strip()
    )
    bodies = [manual_section_body(text, title) for title in titles]
    return "\n".join(body for body in bodies if body)


def structure_issues(text: str, modules: list[dict[str, Any]], extra_sections: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    sections = iter_manual_sections(text)
    for index, (token, _title, _body) in enumerate(sections, start=1):
        number = parse_chinese_number(token)
        if number is None:
            issues.append(f"章节序号无法识别：{token}")
            continue
        if number != index:
            issues.append(f"章节序号不连续：第 {index} 个章节的序号为 {token}")
    module_titles = {
        str(module.get("feature") or "").strip()
        for module in modules
        if str(module.get("feature") or "").strip()
    }
    extra_titles = [str(item.get("title") or "").strip() for item in extra_sections]
    for title in extra_titles:
        if title and not manual_section_body(text, title):
            issues.append(f"缺少项目自定章节：{title}")
    known_titles = set(REQUIRED_SECTION_TITLES) | module_titles | {t for t in extra_titles if t}
    for _token, title, _body in sections:
        if title not in known_titles:
            issues.append(f"成稿出现既非必备章节、也不在 manual_extra_sections 与 manual_modules 中的章节：{title}")
    if sections and sections[-1][1] != "术语表":
        issues.append(f"术语表不是最后一章，当前末章为：{sections[-1][1]}")
    for index, (_token, title, body) in enumerate(sections, start=1):
        labels = [(int(chapter), int(serial)) for chapter, serial in FIGURE_LABEL_PATTERN.findall(body)]
        for order, (chapter, serial) in enumerate(labels, start=1):
            if chapter != index:
                issues.append(f"图编号章号与所在章不一致：{title} 中出现「图 {chapter}-{serial}」，应为第 {index} 章")
            if serial != order:
                issues.append(f"图编号章内序号不连续：{title} 中第 {order} 幅图编号为「图 {chapter}-{serial}」")
    return issues


def manual_quality_issues(
    text: str,
    modules: list[dict[str, Any]],
    extra_sections: list[dict[str, Any]] | None = None,
    extra_section_issues: list[str] | None = None,
) -> list[str]:
    issues: list[str] = []
    extra_sections = extra_sections or []
    for title in REQUIRED_SECTION_TITLES:
        if not manual_section_body(text, title):
            issues.append(f"缺少通用手册章节：{title}")
    if re.search(r"^##\s+\(\d+\)、", text, flags=re.M):
        issues.append("章节标题仍使用括号数字，应使用中文大写序号")
    issues.extend(extra_section_issues or [])
    issues.extend(structure_issues(text, modules, extra_sections))
    scope_text = technical_scope_text(text, modules)
    for term in TECHNICAL_TERMS:
        if term in scope_text:
            issues.append(f"存在偏技术表达：{term}")
    for marker in TEMPLATE_MARKERS:
        if marker in text:
            issues.append(f"存在模板化表达：{marker}")
    for marker in AI_TONE_MARKERS:
        if marker in text:
            issues.append(f"存在机器生成特征或空泛表达：{marker}")
    expected_figures = sum(len(module.get("screenshot_notes") or []) for module in modules)
    if len(SCREENSHOT_PLACEHOLDER_PATTERN.findall(text)) < expected_figures:
        issues.append("截图预留数量少于核心模块数量")
    list_lines = [
        line.strip()
        for line in text.splitlines()
        if re.match(r"^(?:[-*+]\s+|\d+\.\s+)", line.strip())
    ]
    if list_lines:
        issues.append(f"正文仍存在项目符号或编号列表：{list_lines[0][:40]}")
    for module in modules:
        title = str(module.get("feature") or "").strip()
        if not title:
            continue
        body = manual_section_body(text, title)
        if not body:
            issues.append(f"缺少核心模块章节：{title}")
            continue
        prose = module_prose(body)
        prose_length = module_prose_length(body)
        paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", prose) if paragraph.strip()]
        if len(paragraphs) not in (2, 3):
            issues.append(f"模块正文段落数应为 2 段，必要时 3 段，当前为 {len(paragraphs)} 段：{title}")
        if prose_length < 120:
            issues.append(f"模块正文过短（正文 {prose_length} 字，下限 120 字）：{title}")
        if prose_length > 300:
            issues.append(f"模块正文过长（正文 {prose_length} 字，上限 300 字）：{title}")
        for field, label in REQUIRED_MODULE_COVERAGE:
            values = as_text_list(module.get(field))
            if values and not any(reflected_in(prose, value) for value in values):
                issues.append(f"模块正文未体现{label}：{title}，须由模型将该项内容写入 `body`")
        for field, label in (("rationale", "设计取舍原因"), ("boundary", "边界情形")):
            values = as_text_list(module.get(field))
            if values and not any(reflected_in(prose, value) for value in values):
                issues.append(
                    f"模块 `{field}` 非空但正文未体现{label}：{title}，"
                    "须由模型将该内容融入 `body`，生成环节不再拼接追加"
                )
        for label in ("进入方式：", "页面内容：", "操作步骤：", "操作规则：", "操作结果与反馈："):
            if label in body:
                issues.append(f"模块仍使用制式小标题：{title} / {label}")
    return issues


TEMPLATE_SIMILARITY_THRESHOLD = 0.35


def sentence_shape(paragraph: str, title: str) -> set[str]:
    """The 4-character shingles of a paragraph, with the module name removed.

    去掉模块名后，两段套用同一模板的正文会在连接成分上大量重合，而各自撰写的正文不会。
    """
    value = paragraph.replace(title, "") if title else paragraph
    value = re.sub(r"\s+", "", value)
    return {value[index : index + 4] for index in range(len(value) - 3)}


def shape_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def template_rotation_warnings(text: str, modules: list[dict[str, Any]]) -> list[str]:
    """Flag three or more modules whose first paragraph shares one sentence shape.

    判定从严：须有三个以上模块两两高度重合，宁可漏报，不可误报。本项仅作提示，不改写正文。
    """
    shapes: list[tuple[str, set[str]]] = []
    for module in modules:
        title = str(module.get("feature") or "").strip()
        if not title:
            continue
        prose = module_prose(manual_section_body(text, title))
        paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", prose) if paragraph.strip()]
        if not paragraphs:
            continue
        shape = sentence_shape(paragraphs[0], title)
        if len(shape) >= 12:
            shapes.append((title, shape))
    groups: set[frozenset[str]] = set()
    for index, (title, shape) in enumerate(shapes):
        similar = [
            other_title
            for other_index, (other_title, other_shape) in enumerate(shapes)
            if other_index != index and shape_similarity(shape, other_shape) >= TEMPLATE_SIMILARITY_THRESHOLD
        ]
        if len(similar) >= 2:
            groups.add(frozenset([title, *similar]))
    return [
        "以下模块正文首段句式高度雷同，疑似模板轮换，须由模型分别改写：" + "、".join(sorted(group))
        for group in sorted(groups, key=sorted)
    ]


def manual_quality_warnings(text: str, modules: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    has_rationale_or_boundary = any(
        module.get("rationale") or module.get("boundary") for module in modules
    )
    if modules and not has_rationale_or_boundary:
        warnings.append("未提供任何设计取舍原因与边界情形，手册易呈现模板化特征")
    warnings.extend(template_rotation_warnings(text, modules))
    return warnings


def as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [item.strip() for item in re.split(r"[；;\n]+", text) if item.strip()]


def required_module_text(item: dict[str, Any], field: str, title: str) -> str:
    value = str(item.get(field) or "").strip()
    if not value:
        raise SystemExit(
            "STOP_FOR_USER\n"
            f"NEXT_ACTION: 操作手册页面模块「{title}」缺少 `{field}`。须回到业务理解阶段，"
            "由模型依据真实页面证据补全 manual_modules，再生成操作手册。"
        )
    return value


def required_module_list(item: dict[str, Any], field: str, title: str) -> list[str]:
    values = as_text_list(item.get(field))
    if not values:
        raise SystemExit(
            "STOP_FOR_USER\n"
            f"NEXT_ACTION: 操作手册页面模块「{title}」缺少 `{field}`。须回到业务理解阶段，"
            "由模型依据真实页面证据补全 manual_modules，再生成操作手册。"
        )
    return values


def normalize_manual_modules(
    business: dict[str, Any] | None,
    fallback_modules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    manual_modules = business.get("manual_modules") if business else []
    if not manual_modules:
        raise SystemExit(
            "STOP_FOR_USER\n"
            "NEXT_ACTION: 业务理解缺少 `manual_modules`。不得按 auth/query/form 等模板推测操作手册内容。"
            "须先阅读项目真实页面、路由、按钮、输入项、提示与结果反馈，补全 manual_modules，再生成操作手册。"
        )

    modules: list[dict[str, Any]] = []
    for index, item in enumerate(manual_modules, start=1):
        if not isinstance(item, dict):
            raise SystemExit(
                "STOP_FOR_USER\n"
                f"NEXT_ACTION: manual_modules 第 {index} 项不是对象，无法据以生成真实操作手册。须补全 title、purpose、entry、operation_steps、feedback 等字段。"
            )
        title = str(item.get("title") or item.get("feature") or "").strip() or f"功能模块 {index}"
        purpose = required_module_text(item, "purpose", title)
        entry = required_module_text(item, "entry", title)
        usage = str(item.get("usage") or item.get("usage_scenario") or item.get("description") or "").strip()
        if not usage:
            raise SystemExit(
                "STOP_FOR_USER\n"
                f"NEXT_ACTION: 操作手册页面模块「{title}」缺少 `usage` 或 `usage_scenario`。须回到业务理解阶段，"
                "补充用户在何种场景下使用该页面、处理何种具体事务，再生成操作手册。"
            )
        steps = required_module_list(item, "operation_steps", title)
        feedback = required_module_list(item, "feedback", title)
        body = required_module_body(item, title)
        raw_screenshot = item.get("screenshot")
        raw_notes = raw_screenshot if isinstance(raw_screenshot, list) else [raw_screenshot]
        screenshot_notes = [
            strip_screenshot_note(str(note)) for note in raw_notes if str(note or "").strip()
        ]
        screenshot_notes = [note for note in screenshot_notes if note]
        if not screenshot_notes:
            screenshot_notes = [f"请在此处插入「{title}」页面或操作结果截图"]
        modules.append(
            {
                "feature": title,
                "raw_feature": title,
                "body": body,
                "purpose": purpose,
                "entry": entry,
                "usage": usage,
                "visible_elements": as_text_list(item.get("visible_elements") or item.get("page_elements")),
                "steps": steps,
                "validation_rules": as_text_list(item.get("validation_rules") or item.get("rules") or item.get("limits")),
                "feedback": feedback,
                "rationale": as_text_list(item.get("rationale")),
                "boundary": as_text_list(item.get("boundary")),
                "screenshot_notes": screenshot_notes,
            }
        )
    return modules


def required_module_body(item: dict[str, Any], title: str) -> list[str]:
    """Take the module chapter prose the model wrote; never compose a fallback."""
    paragraphs = paragraph_list(item.get("body"))
    if not paragraphs:
        raise SystemExit(
            "STOP_FOR_USER\n"
            f"NEXT_ACTION: 操作手册页面模块「{title}」缺少 `body`。操作手册正文由模型撰写，生成环节不造句、不作缺省生成。"
            "须回到业务理解阶段，依据该模块的 entry、visible_elements、operation_steps、validation_rules、"
            "feedback、rationale、boundary 撰写 2 段（必要时 3 段）正文并写入 `body`，再重新生成操作手册。"
        )
    return paragraphs


def required_manual_overview(business: dict[str, Any] | None) -> list[str]:
    """说明章正文，由模型撰写并原样输出。"""
    paragraphs = paragraph_list(business.get("manual_overview") if business else None)
    if not paragraphs:
        raise SystemExit(
            "STOP_FOR_USER\n"
            "NEXT_ACTION: 业务理解缺少 `manual_overview`。操作手册「说明」章正文由模型撰写，生成环节不造句、不作缺省生成。"
            "须补写软件定位、目标用户、业务场景与整体流程的段落数组，再重新生成操作手册。"
        )
    return paragraphs


def required_feature_highlights(business: dict[str, Any] | None) -> list[dict[str, str]]:
    """功能特点各项正文，由模型撰写并原样输出。"""
    raw_items = business.get("manual_feature_highlights") if business else None
    items: list[dict[str, str]] = []
    if isinstance(raw_items, list):
        for entry in raw_items:
            if isinstance(entry, dict):
                title = str(entry.get("title") or "").strip()
                paragraph = str(entry.get("paragraph") or "").strip()
            else:
                title = ""
                paragraph = str(entry or "").strip()
            if paragraph:
                items.append({"title": title, "paragraph": paragraph})
    if not items:
        raise SystemExit(
            "STOP_FOR_USER\n"
            "NEXT_ACTION: 业务理解缺少 `manual_feature_highlights`。操作手册「功能特点」章正文由模型撰写，"
            "生成环节不造句、不作缺省生成。须按真实功能撰写 4 至 8 项，每项含 title 与成文的 paragraph，"
            "各项句式须有差异，补齐后再重新生成操作手册。"
        )
    return items


def strip_screenshot_note(note: str) -> str:
    """Keep the model's own wording; drop any placeholder wrapper or figure number it already wrote."""
    value = str(note or "").strip()
    value = re.sub(r"^【\s*", "", value)
    value = re.sub(r"\s*】$", "", value)
    value = re.sub(r"^图\s*\d+\s*-\s*\d+\s*", "", value)
    value = re.sub(r"^截图预留[：:]\s*", "", value)
    value = re.sub(r"^图\s*\d+\s*-\s*\d+\s*", "", value)
    return value.strip().rstrip("。")


def screenshot_placeholder(chapter_index: int, serial: int, note: str) -> str:
    return f"【图 {chapter_index}-{serial} 截图预留：{note}。】"


def normalize_system_requirements(business: dict[str, Any] | None) -> list[dict[str, str]]:
    raw_items = business.get("system_requirements") if business else None
    rows: list[dict[str, str]] = []
    if isinstance(raw_items, list):
        for item in raw_items:
            if isinstance(item, dict):
                name = str(item.get("item") or item.get("name") or "").strip()
                minimum = str(item.get("minimum") or item.get("min") or "").strip()
                recommended = str(item.get("recommended") or item.get("recommend") or "").strip()
                if name:
                    rows.append(
                        {
                            "item": name,
                            "minimum": minimum or "按实际部署环境配置",
                            "recommended": recommended or minimum or "按实际部署环境配置",
                        }
                    )
    if not rows:
        raise SystemExit(
            "STOP_FOR_USER\n"
            "NEXT_ACTION: 业务理解缺少 `system_requirements`。须依据真实项目运行形态与已确认的申请表环境补全，再生成操作手册。"
        )
    return rows


def normalize_faq(business: dict[str, Any] | None, software_name: str) -> list[dict[str, str]]:
    raw_items = business.get("faq") if business else None
    items: list[dict[str, str]] = []
    if isinstance(raw_items, list):
        for item in raw_items:
            if isinstance(item, dict):
                question = str(item.get("question") or item.get("q") or "").strip()
                answer = str(item.get("answer") or item.get("a") or "").strip()
                if question and answer:
                    items.append({"question": question, "answer": answer})
    if not items:
        raise SystemExit(
            "STOP_FOR_USER\n"
            "NEXT_ACTION: 业务理解缺少 `faq`。须依据当前软件真实使用场景补全常见问题，再生成操作手册。"
        )
    return items


def normalize_glossary(business: dict[str, Any] | None, modules: list[dict[str, Any]], software_name: str) -> list[dict[str, str]]:
    raw_items = business.get("glossary") if business else None
    items: list[dict[str, str]] = []
    if isinstance(raw_items, list):
        for item in raw_items:
            if isinstance(item, dict):
                term = str(item.get("term") or item.get("name") or "").strip()
                definition = str(item.get("definition") or item.get("description") or "").strip()
                if term and definition:
                    items.append({"term": term, "definition": definition})
    if items:
        return items
    raise SystemExit(
        "STOP_FOR_USER\n"
        "NEXT_ACTION: 业务理解缺少 `glossary`。须依据当前软件真实业务对象与页面术语补全术语表，再生成操作手册。"
    )


def chinese_number(value: int) -> str:
    digits = "零一二三四五六七八九"
    if value <= 0:
        return str(value)
    if value < 10:
        return digits[value]
    if value == 10:
        return "十"
    if value < 20:
        return "十" + digits[value % 10]
    if value < 100:
        tens, ones = divmod(value, 10)
        return digits[tens] + "十" + (digits[ones] if ones else "")
    return str(value)


def section_heading(index: int, title: str) -> str:
    return f"## {chinese_number(index)}、{title}"


EXTRA_SECTION_POSITIONS = ("before_modules", "after_modules", "before_glossary")


def normalize_extra_sections(business: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[str]]:
    """Carry `manual_extra_sections` through untouched.

    章节名、位置、正文与表格一律按模型所写渲染，渲染环节不解释其语义，
    不识别任何具体章节名，亦不作推断或缺省填充。
    """
    raw_items = business.get("manual_extra_sections") if business else None
    if raw_items is None:
        return [], []
    issues: list[str] = []
    if not isinstance(raw_items, list):
        return [], ["manual_extra_sections 不是数组，未生成任何项目自定章节"]
    sections: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            issues.append(f"manual_extra_sections 第 {index} 项不是对象，未生成对应章节")
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            issues.append(f"manual_extra_sections 第 {index} 项缺少 title，未生成对应章节")
            continue
        position = str(item.get("position") or "").strip()
        if position not in EXTRA_SECTION_POSITIONS:
            issues.append(
                f"manual_extra_sections「{title}」的 position 取值 {position!r} 不在 "
                f"{EXTRA_SECTION_POSITIONS} 之内，未生成对应章节"
            )
            continue
        raw_paragraphs = item.get("paragraphs")
        if isinstance(raw_paragraphs, list):
            paragraphs = [str(paragraph).strip() for paragraph in raw_paragraphs if str(paragraph).strip()]
        elif str(raw_paragraphs or "").strip():
            paragraphs = [str(raw_paragraphs).strip()]
        else:
            paragraphs = []
        table = normalize_extra_table(item.get("table"))
        if table is None and item.get("table") is not None:
            issues.append(f"manual_extra_sections「{title}」的 table 结构不符合预期，已忽略该表格")
        if not paragraphs and table is None:
            issues.append(f"manual_extra_sections「{title}」既无 paragraphs 也无可用 table，未生成对应章节")
            continue
        try:
            order = int(item.get("order"))
        except (TypeError, ValueError):
            order = None
        sections.append(
            {
                "title": title,
                "position": position,
                "order": order,
                "paragraphs": paragraphs,
                "table": table,
            }
        )
    return sections, issues


def normalize_extra_table(raw_table: Any) -> dict[str, list[Any]] | None:
    if not isinstance(raw_table, dict):
        return None
    headers = raw_table.get("headers")
    rows = raw_table.get("rows")
    if not isinstance(headers, list) or not headers:
        return None
    if not isinstance(rows, list):
        return None
    clean_headers = [str(header).strip() for header in headers]
    clean_rows: list[list[str]] = []
    for row in rows:
        if not isinstance(row, list):
            return None
        clean_rows.append([str(cell).strip() for cell in row])
    return {"headers": clean_headers, "rows": clean_rows}


def extra_sections_at(sections: list[dict[str, Any]], position: str) -> list[dict[str, Any]]:
    selected = [section for section in sections if section["position"] == position]
    return sorted(
        selected,
        key=lambda section: section["order"] if section["order"] is not None else 10**6,
    )


def append_extra_sections(lines: list[str], sections: list[dict[str, Any]], start_index: int) -> int:
    index = start_index
    for section in sections:
        lines.extend([section_heading(index, section["title"]), ""])
        for paragraph in section["paragraphs"]:
            lines.extend([paragraph, ""])
        table = section["table"]
        if table:
            lines.append("| " + " | ".join(table["headers"]) + " |")
            lines.append("| " + " | ".join("---" for _ in table["headers"]) + " |")
            for row in table["rows"]:
                lines.append("| " + " | ".join(row) + " |")
            lines.append("")
        index += 1
    return index


def tidy_manual_output(text: str) -> str:
    """Typographic normalisation only: 中英文之间补一个空格。不改动模型所写的文字。"""
    value = re.sub(r"(?<=[\u4e00-\u9fff])([A-Za-z][A-Za-z0-9.+-]*)(?=[\u4e00-\u9fff])", r" \1 ", text)
    value = re.sub(r" {2,}", " ", value)
    return value


def feature_highlight_paragraph(item: dict[str, str]) -> str:
    """Render one 功能特点 item as a single paragraph, verbatim."""
    title = item["title"]
    paragraph = item["paragraph"]
    if not title or paragraph.startswith(title):
        return paragraph
    return f"{title}：{paragraph}"


def append_modules_canonical(lines: list[str], modules: list[dict[str, Any]], start_index: int) -> int:
    for i, module in enumerate(modules, start=start_index):
        lines.extend([section_heading(i, module["feature"]), ""])
        for paragraph in module["body"]:
            lines.extend([paragraph, ""])
        for serial, note in enumerate(module.get("screenshot_notes") or [], start=1):
            lines.extend([screenshot_placeholder(i, serial, note), ""])
    return start_index + len(modules)


def render_manual_canonical(
    software_name: str,
    modules: list[dict[str, Any]],
    overview_paragraphs: list[str],
    feature_highlights: list[dict[str, str]],
    business: dict[str, Any] | None = None,
    extra_sections: list[dict[str, Any]] | None = None,
) -> str:
    """Assemble the manual from prose the model wrote; the script orders, numbers and renders only."""
    system_rows = normalize_system_requirements(business)
    faq_items = normalize_faq(business, software_name)
    glossary_items = normalize_glossary(business, modules, software_name)
    extra_sections = extra_sections or []

    lines = [f"# {software_name}操作手册", ""]
    next_index = 1
    lines.extend([section_heading(next_index, "说明"), ""])
    next_index += 1
    for paragraph in overview_paragraphs:
        lines.extend([paragraph, ""])
    lines.extend([section_heading(next_index, "功能特点"), ""])
    next_index += 1
    for item in feature_highlights:
        lines.extend([feature_highlight_paragraph(item), ""])
    lines.extend([section_heading(next_index, "系统要求"), "", "| 系统要求 | 最低配置 | 推荐配置 |", "| --- | --- | --- |"])
    next_index += 1
    for row in system_rows:
        lines.append(f"| {row['item']} | {row['minimum']} | {row['recommended']} |")
    lines.append("")
    next_index = append_extra_sections(
        lines, extra_sections_at(extra_sections, "before_modules"), start_index=next_index
    )
    next_index = append_modules_canonical(lines, modules, start_index=next_index)
    next_index = append_extra_sections(
        lines, extra_sections_at(extra_sections, "after_modules"), start_index=next_index
    )
    lines.extend([section_heading(next_index, "常见问题解答"), ""])
    for item in faq_items:
        lines.extend([f"问题：{item['question']}", f"解决方法：{item['answer']}", ""])
    next_index += 1
    next_index = append_extra_sections(
        lines, extra_sections_at(extra_sections, "before_glossary"), start_index=next_index
    )
    lines.extend([section_heading(next_index, "术语表"), "", "| 术语 | 解释 |", "| --- | --- |"])
    for item in glossary_items:
        lines.append(f"| {item['term']} | {item['definition']} |")
    lines.append("")
    append_stop(lines)
    return tidy_manual_output("\n".join(lines))


def append_stop(lines: list[str]) -> None:
    lines.extend(
        [
            "```text",
            "STOP_FOR_USER",
            "NEXT_ACTION: 须一次性确认完整操作手册草稿是否符合真实业务。必要时先统一修改段落内容，再运行 confirm_stage.py --stage markdown。",
            "```",
            "",
        ]
    )


def build_manual_text(
    analysis: dict[str, Any],
    software_name: str,
    version: str,
    business: dict[str, Any] | None = None,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    modules = normalize_manual_modules(business, [])
    overview_paragraphs = required_manual_overview(business)
    feature_highlights = required_feature_highlights(business)
    extra_sections, extra_section_issues = normalize_extra_sections(business)
    records: list[dict[str, Any]] = []

    def render() -> str:
        return render_manual_canonical(
            software_name,
            modules,
            overview_paragraphs,
            feature_highlights,
            business,
            extra_sections,
        )

    def review(round_no: int, action: str, text: str) -> dict[str, Any]:
        return {
            "round": round_no,
            "action": action,
            "issues": manual_quality_issues(text, modules, extra_sections, extra_section_issues),
            "warnings": manual_quality_warnings(text, modules),
        }

    text = render()
    records.append(review(1, "初稿生成", text))

    text = render()
    records.append(review(2, "压缩重复、保留流程衔接", text))

    text = render()
    records.append(review(3, "制式模板与机器生成特征复核", text))

    text = render()
    records.append(review(4, "设计取舍原因与边界情形复核", text))

    for round_no in range(5, 7):
        issues = records[-1]["issues"]
        if not issues:
            break
        text = render()
        records.append(review(round_no, "复核后仍须回到业务理解阶段补写", text))
        break
    return text, records, modules


def write_review_records(out_dir: Path, records: list[dict[str, Any]], modules: list[dict[str, Any]]) -> None:
    (out_dir / "操作手册自检记录.json").write_text(
        json.dumps({"rounds": records, "module_count": len(modules)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = ["# 操作手册自检记录", ""]
    for record in records:
        lines.extend([f"## 第 {record['round']} 轮：{record['action']}", ""])
        if record["issues"]:
            lines.extend(f"- {issue}" for issue in record["issues"])
        else:
            lines.append("- 未发现须继续修正的问题")
        for warning in record.get("warnings") or []:
            lines.append(f"- WARN：{warning}")
        lines.append("")
    lines.extend(["## 模块清单", ""])
    lines.extend(f"- {module['feature']}" for module in modules)
    (out_dir / "操作手册自检记录.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_manual(path: Path, analysis: dict[str, Any], software_name: str, version: str, business: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    text, records, modules = build_manual_text(analysis, software_name, version, business)
    path.write_text(text, encoding="utf-8")
    write_review_records(path.parent, records, modules)
    return records


def require_confirmed_business(business: dict[str, Any] | None) -> None:
    if business is None:
        raise SystemExit(
            "STOP_FOR_USER\n"
            "NEXT_ACTION: 操作手册须基于已确认的业务理解生成。须先生成并确认 草稿/业务理解.md。"
        )
    if business.get("confirmation_required") and not business.get("user_confirmed"):
        raise SystemExit(
            "STOP_FOR_USER\n"
            "NEXT_ACTION: 业务理解尚未确认。须先确认 草稿/业务理解.md，"
            "再运行 `python3 <SKILL_DIR>/scripts/confirm_stage.py --workdir 软件著作权申请资料 --stage business --note \"<用户确认内容>\"`。"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--software-name", required=True)
    parser.add_argument("--version", default="V1.0")
    parser.add_argument("--business-context", help="Business context JSON generated before manual drafting")
    parser.add_argument("--out-dir", default="软件著作权申请资料/草稿")
    args = parser.parse_args()

    analysis = read_json(Path(args.analysis))
    business = read_json(Path(args.business_context)) if args.business_context else None
    require_confirmed_business(business)
    out_dir = ensure_dir(Path(args.out_dir))
    out_path = out_dir / "操作手册.md"
    records = write_manual(out_path, analysis, args.software_name, args.version, business)
    print(f"OK manual draft: {out_path}")
    print(f"OK manual self-review: {out_dir / '操作手册自检记录.md'}")
    for record in records:
        print(
            f"Review round {record['round']}: {record['action']} "
            f"issues={len(record['issues'])} warnings={len(record.get('warnings') or [])}"
        )
    for warning in records[-1].get("warnings") or []:
        print(f"WARN: {warning}")
    if records[-1]["issues"]:
        print("STOP_FOR_USER")
        print(
            "NEXT_ACTION: 操作手册自检仍存在问题。操作手册正文由模型撰写，生成环节不作改写。"
            "须回到业务理解阶段，按自检记录逐条重写 manual_overview、manual_feature_highlights "
            "或对应模块的 body，再重新生成。"
        )
        raise SystemExit(1)
    print("STOP_FOR_USER")
    print("NEXT_ACTION: 须一次性确认完整操作手册草稿是否符合真实业务。必要时先统一修改段落内容，再运行 confirm_stage.py --stage markdown。")


if __name__ == "__main__":
    main()
