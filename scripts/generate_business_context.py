#!/usr/bin/env python3
"""Collect project evidence and write a model-authored business context."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from common import ensure_dir, iter_project_files, read_json, read_text, rel, write_json

try:  # 业务理解.md 呈现的章节序列必须与 操作手册.md 的实际产出一致，直接复用渲染侧的规范化逻辑
    from generate_manual_draft import (
        extra_sections_at,
        normalize_extra_sections,
    )

    MANUAL_RENDERER_AVAILABLE = True
except Exception:  # 手册渲染脚本不可用时退化为按原文展示，不影响业务理解生成
    MANUAL_RENDERER_AVAILABLE = False

# 位置取值对应的成稿位置说明，供用户判断章节顺序是否合理。
EXTRA_SECTION_POSITION_LABELS = {
    "before_modules": "功能模块章节之前",
    "after_modules": "功能模块之后、常见问题解答之前",
    "before_glossary": "常见问题解答之后、术语表之前",
}


DOC_EXTS = {".md", ".txt", ".rst", ".adoc"}
MAX_DOC_CHARS = 80_000
MAX_DOCS = 40


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_md(text: str) -> str:
    text = re.sub(r"`{3}.*?`{3}", " ", text, flags=re.S)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    text = re.sub(r"[>#*_`|]", " ", text)
    return normalize_space(text)


def skip_doc(path: Path, project: Path) -> bool:
    r = rel(path, project).lower()
    skip_parts = (
        "node_modules",
        ".git/",
        "dist/",
        "build/",
        ".next/",
        "coverage/",
        "软件著作权申请资料",
    )
    return any(part in r for part in skip_parts)


def extract_headings(text: str, limit: int = 24) -> list[str]:
    headings: list[str] = []
    for line in text.splitlines():
        clean = line.strip()
        if clean.startswith("#"):
            title = clean.lstrip("#").strip()
            if title and title not in headings:
                headings.append(title[:120])
        if len(headings) >= limit:
            break
    return headings


def extract_opening(text: str, limit: int = 900) -> str:
    clean = strip_md(text)
    return clean[:limit].strip()


def collect_documents(project: Path) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for path in iter_project_files(project, DOC_EXTS):
        if skip_doc(path, project):
            continue
        try:
            text = read_text(path, limit=MAX_DOC_CHARS)
        except Exception:
            continue
        if not text.strip():
            continue
        docs.append(
            {
                "path": rel(path, project),
                "size": path.stat().st_size,
                "headings": extract_headings(text),
                "opening": extract_opening(text),
            }
        )
    docs.sort(key=lambda item: (item["path"].count("/"), item["path"]))
    return docs[:MAX_DOCS]


def collect_code_evidence(analysis: dict[str, Any]) -> dict[str, Any]:
    source = analysis.get("source") or {}
    categorized = source.get("categorized_files") or {}
    return {
        "project_name": analysis.get("project_name"),
        "software_name_candidate": analysis.get("software_name_candidate"),
        "frameworks": analysis.get("frameworks") or [],
        "language": analysis.get("language"),
        "routes": analysis.get("routes") or [],
        "feature_name_candidates": analysis.get("feature_candidates") or [],
        "entry_files": categorized.get("entry") or [],
        "page_files": categorized.get("page") or [],
        "component_files": categorized.get("component") or [],
        "api_files": categorized.get("api") or [],
        "run_command_candidates": analysis.get("run_command_candidates") or [],
        "package": analysis.get("package") or {},
    }


def build_evidence(project: Path, analysis: dict[str, Any], software_name: str, web_notes: str) -> dict[str, Any]:
    return {
        "software_name": software_name,
        "project_root": str(project.resolve()),
        "instruction": (
            "本文件仅收集证据，不决定行业、功能或手册结构。"
            "须由模型阅读上述证据及必要的项目源码后，另行编写业务理解模型稿。"
        ),
        "documents": collect_documents(project),
        "code_evidence": collect_code_evidence(analysis),
        "external_research_notes": web_notes,
    }


def write_evidence_md(path: Path, evidence: dict[str, Any]) -> None:
    lines = [
        "# 业务理解证据",
        "",
        f"- 软件名称：{evidence['software_name']}",
        f"- 项目目录：`{evidence['project_root']}`",
        "",
        "本文件仅列出可供研判的项目证据，不代表最终申报口径。",
        "应阅读哪些文档、抽取哪些功能、采用何种操作手册结构，须由模型自行判断。",
        "",
        "## 代码与页面证据",
        "",
    ]
    code = evidence["code_evidence"]
    for key in ("frameworks", "language", "routes", "feature_name_candidates", "entry_files", "page_files", "component_files", "api_files"):
        value = code.get(key)
        if value:
            lines.append(f"- {key}：{value}")
    lines.extend(["", "## 文档证据", ""])
    for doc in evidence["documents"]:
        lines.extend(
            [
                f"### {doc['path']}",
                "",
                f"- 大小：{doc['size']} bytes",
                f"- 标题线索：{'；'.join(doc['headings']) if doc['headings'] else '无'}",
                "",
                doc["opening"],
                "",
            ]
        )
    if evidence.get("external_research_notes"):
        lines.extend(["## 外部调研摘要", "", evidence["external_research_notes"], ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def write_model_template(path: Path, evidence: dict[str, Any]) -> None:
    template = {
        "software_name": evidence["software_name"],
        "product_positioning": "",
        "industry": "",
        "target_users": [],
        "core_value": "",
        "business_features": [],
        "business_feature_details": {},
        "operation_flow": [],
        "application_purpose": "",
        "main_functions": "",
        "technical_characteristics": "",
        "software_technical_option": "应用软件",
        "software_category": "应用软件",
        "manual_overview": [
            "操作手册「说明」章的第一段正文，由模型直接撰写成文并原样输出，不作拼接、改写与术语替换。内容为软件定位、目标用户与业务场景。",
            "第二段正文，说明用户自进入软件至完成核心任务的整体流程。不得写入「本操作手册用于……」「面向软著审核……」一类解释文档写作方式的元话语。",
        ],
        "manual_feature_highlights": [
            {
                "title": "该功能特点的名称，按当前项目真实功能命名",
                "paragraph": "该项正文，为成文段落，说明本项功能解决何种业务问题、用户在页面上看到何种内容、完成操作后得到何种结果。本数组共填 4 至 8 项，各项句式须有差异，不得套用同一模板轮换。",
            }
        ],
        "manual_modules": [
            {
                "title": "真实页面或核心流程名称",
                "body": [
                    "该模块章节的正文第一段，由模型直接撰写成文并原样输出。以 2 段为常态，必要时可写 3 段，去除空白后合计 120 至 300 字。",
                    "第二段正文。全部正文须覆盖本模块 entry、operation_steps、validation_rules、feedback、visible_elements、rationale、boundary 所记录的要素。上述字段为核对清单，不作为造句素材，其作用在于校验正文有无遗漏，模型须将其内容自行写成通顺的段落。",
                ],
                "evidence": ["页面、路由、组件、README 或需求文档路径"],
                "purpose": "该页面或流程在当前软件中的用途。",
                "usage": "用户在什么业务场景下会使用该页面，正在处理什么具体事务。",
                "entry": "用户从哪里进入该页面或流程。",
                "visible_elements": ["用户实际能看到的输入框、按钮、列表、状态或结果区域"],
                "operation_steps": ["按真实页面顺序描述用户动作，不得写入代码实现。"],
                "validation_rules": ["输入限制、必填项、额度、权限、异常提示等规则。无相应内容时留空数组。"],
                "feedback": ["操作完成后用户能看到的结果、提示或状态变化。"],
                "screenshot": "截图预留说明",
                "rationale": ["该模块中「本可采用另一种做法，而本软件选择了这一种」的真实原因，成稿中渲染为「原因在于……」式说明。无法写出真实原因时留空数组，不得以空泛表述填充。"],
                "boundary": ["可预见的边界情形及软件的实际表现，例如依赖数据缺失时界面的显示内容、前置条件未满足时仍可使用的功能、超出额度时的处置方式。无相应内容时留空数组。"],
            }
        ],
        "system_requirements": [
            {"item": "操作系统", "minimum": "按项目实际填写", "recommended": "按项目实际填写"},
            {"item": "浏览器或客户端", "minimum": "按项目实际填写", "recommended": "按项目实际填写"},
        ],
        "faq": [
            {"question": "按当前软件真实使用场景填写常见问题", "answer": "给出面向普通用户的处理方法。"}
        ],
        "glossary": [
            {"term": "当前软件中的业务术语", "definition": "用普通中文解释含义。"}
        ],
        "manual_extra_sections": [
            {
                "title": "按本项目实际需要命名，若无需要则整个数组留空",
                "reason": "对照 references/manual_structure.md「项目自定章节」的三条判定标准，逐条说明本章须独立成章的理由。三条中任缺其一，即不应设立。",
                "evidence": ["源码路径、配置文件、运行产物、项目文档或操作视频帧"],
                "position": "填写 before_modules、after_modules 或 before_glossary 三者之一",
                "order": 1,
                "paragraphs": [],
                "table": {"headers": ["需要表格时填写表头，无须表格时删除整个 table 字段"], "rows": [["对应的单元格内容"]]},
            }
        ],
        "model_review_notes": [
            "操作手册的必备章节固定为六项，顺序不变：说明、功能特点、系统要求、按真实页面或流程逐章操作、常见问题解答、术语表。其中常见问题解答与术语表恒为最后两章。除该六项之外的章节，一律由模型按当前项目实际情况判断是否设立，并写入 manual_extra_sections。本流程不提供可供照搬的可选章节清单，设立标准见 references/manual_structure.md 的「项目自定章节」一节，三条判定标准须同时成立，任缺其一即不应设立。",
            "操作手册的叙述性正文由模型撰写，生成环节不造句、不改写：说明章正文写入 manual_overview，功能特点各项写入 manual_feature_highlights，各功能模块章节正文写入 manual_modules 每项的 body，项目自定章节正文写入 manual_extra_sections 每项的 paragraphs。生成环节仅负责章节顺序与编号、图注编号、表格渲染、页眉及各项校验。",
            "manual_modules 中 entry、operation_steps、validation_rules、feedback、visible_elements、rationale、boundary 等字段为核对清单，不作为造句素材。模型须将上述字段的内容自行写入 body，其作用在于校验正文有无遗漏。未写入正文的内容不会出现在成稿中。",
            "manual_modules 须按当前项目真实页面、导航入口、按钮、输入限制、系统反馈与截图位置编写，不得仅写抽象功能名。",
            "不得照抄范本文案。范本的作用在于说明手册须具体、可操作、便于审核人员理解。",
            "不得以关键词表决定行业与功能。行业与功能的判定须能从项目证据或用户补充材料中说明来源。",
        ],
    }
    write_json(path, template)


def load_model_context(path: Path) -> dict[str, Any]:
    data = read_json(path)
    if not isinstance(data, dict):
        raise SystemExit(f"Invalid model context JSON: {path}")
    return data


def required_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise SystemExit(f"Model context field must be a list: {field}")
    items = [str(item).strip() for item in value if str(item).strip()]
    if not items:
        raise SystemExit(f"Model context field cannot be empty: {field}")
    return items


def required_text(data: dict[str, Any], field: str) -> str:
    value = str(data.get(field) or "").strip()
    if not value:
        raise SystemExit(f"Model context field cannot be empty: {field}")
    return value


def normalize_model_context(model: dict[str, Any], evidence: dict[str, Any], web_notes: str) -> dict[str, Any]:
    features = required_list(model.get("business_features"), "business_features")
    details = model.get("business_feature_details") or {}
    if not isinstance(details, dict):
        raise SystemExit("Model context field must be an object: business_feature_details")
    missing = [feature for feature in features if not str(details.get(feature) or "").strip()]
    if missing:
        raise SystemExit("Model context missing feature details: " + "、".join(missing[:12]))
    manual_overview = model.get("manual_overview") or []
    if manual_overview and not isinstance(manual_overview, list):
        raise SystemExit("Model context field must be a list: manual_overview")
    if not manual_overview:
        raise SystemExit("Model context field cannot be empty: manual_overview")
    feature_highlights = model.get("manual_feature_highlights") or []
    if feature_highlights and not isinstance(feature_highlights, list):
        raise SystemExit("Model context field must be a list: manual_feature_highlights")
    if not feature_highlights:
        raise SystemExit("Model context field cannot be empty: manual_feature_highlights")
    for index, highlight in enumerate(feature_highlights, start=1):
        if not isinstance(highlight, dict):
            raise SystemExit(f"manual_feature_highlights item {index} must be an object")
        if not str(highlight.get("paragraph") or "").strip():
            raise SystemExit(f"manual_feature_highlights item {index} missing field: paragraph")
    extra_sections = model.get("manual_extra_sections") or []
    if extra_sections and not isinstance(extra_sections, list):
        raise SystemExit("Model context field must be a list: manual_extra_sections")
    manual_modules = model.get("manual_modules") or []
    if manual_modules and not isinstance(manual_modules, list):
        raise SystemExit("Model context field must be a list: manual_modules")
    if not manual_modules:
        raise SystemExit("Model context field cannot be empty: manual_modules")
    for index, module in enumerate(manual_modules, start=1):
        if not isinstance(module, dict):
            raise SystemExit(f"manual_modules item {index} must be an object")
        title = str(module.get("title") or module.get("feature") or "").strip()
        for field in ("purpose", "usage", "entry", "operation_steps", "feedback", "body"):
            value = module.get(field)
            if field == "usage" and not str(value or "").strip():
                value = module.get("usage_scenario")
            if isinstance(value, list):
                missing_value = not any(str(item).strip() for item in value)
            else:
                missing_value = not str(value or "").strip()
            if missing_value:
                raise SystemExit(f"manual_modules item {index} ({title or 'untitled'}) missing field: {field}")
    system_requirements = model.get("system_requirements") or []
    if system_requirements and not isinstance(system_requirements, list):
        raise SystemExit("Model context field must be a list: system_requirements")
    if not system_requirements:
        raise SystemExit("Model context field cannot be empty: system_requirements")
    faq = model.get("faq") or []
    if faq and not isinstance(faq, list):
        raise SystemExit("Model context field must be a list: faq")
    if not faq:
        raise SystemExit("Model context field cannot be empty: faq")
    glossary = model.get("glossary") or []
    if glossary and not isinstance(glossary, list):
        raise SystemExit("Model context field must be a list: glossary")
    if not glossary:
        raise SystemExit("Model context field cannot be empty: glossary")
    context = {
        "software_name": evidence["software_name"],
        "business_understanding_required": True,
        "source_documents": [{"path": doc["path"], "size": doc["size"]} for doc in evidence["documents"]],
        "project_evidence_file": "业务理解证据.md",
        "product_positioning": required_text(model, "product_positioning"),
        "industry": required_text(model, "industry"),
        "target_users": required_list(model.get("target_users"), "target_users"),
        "core_value": required_text(model, "core_value"),
        "business_features": features,
        "business_feature_details": {feature: str(details.get(feature)).strip() for feature in features},
        "operation_flow": required_list(model.get("operation_flow"), "operation_flow"),
        "application_purpose": required_text(model, "application_purpose"),
        "main_functions": required_text(model, "main_functions"),
        "technical_characteristics": required_text(model, "technical_characteristics"),
        "software_technical_option": str(model.get("software_technical_option") or "应用软件"),
        "software_category": str(model.get("software_category") or "应用软件"),
        "manual_overview": [str(paragraph).strip() for paragraph in manual_overview if str(paragraph).strip()],
        "manual_feature_highlights": feature_highlights,
        "manual_extra_sections": extra_sections,
        "manual_modules": manual_modules,
        "system_requirements": system_requirements,
        "faq": faq,
        "glossary": glossary,
        "model_authored": True,
        "external_research_notes": web_notes,
        "confirmation_required": True,
        "user_confirmed": False,
        "confirmation_stage": "business",
        "next_action": "须确认 草稿/业务理解.md 中的软件用途、行业、目标用户、核心功能、手册结构与申请口径。确认后运行 confirm_stage.py --stage business。",
        "review_notes": [
            "确认模型判定的行业领域、目标用户与主要功能是否符合实际申报口径。",
            "确认操作手册结构是否按真实页面与流程展开，而非套用抽象功能列表。",
        ],
    }
    return context


def fallback_normalize_extra_sections(raw_items: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Minimal stand-in used only when the manual renderer cannot be imported."""
    if raw_items is None:
        return [], []
    if not isinstance(raw_items, list):
        return [], ["manual_extra_sections 不是数组，将不生成任何项目自定章节"]
    sections: list[dict[str, Any]] = []
    issues: list[str] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            issues.append(f"manual_extra_sections 第 {index} 项结构不符或缺少 title，将不予成章")
            continue
        title = str(item.get("title")).strip()
        position = str(item.get("position") or "").strip()
        if position not in EXTRA_SECTION_POSITION_LABELS:
            issues.append(f"manual_extra_sections「{title}」的 position 取值 {position!r} 不合法，将不予成章")
            continue
        raw_paragraphs = item.get("paragraphs")
        paragraphs = (
            [str(paragraph).strip() for paragraph in raw_paragraphs if str(paragraph).strip()]
            if isinstance(raw_paragraphs, list)
            else ([str(raw_paragraphs).strip()] if str(raw_paragraphs or "").strip() else [])
        )
        table = item.get("table") if isinstance(item.get("table"), dict) else None
        if not paragraphs and table is None:
            issues.append(f"manual_extra_sections「{title}」既无 paragraphs 亦无可用 table，将不予成章")
            continue
        try:
            order = int(item.get("order"))
        except (TypeError, ValueError):
            order = None
        sections.append(
            {"title": title, "position": position, "order": order, "paragraphs": paragraphs, "table": table}
        )
    return sections, issues


def fallback_extra_sections_at(sections: list[dict[str, Any]], position: str) -> list[dict[str, Any]]:
    selected = [section for section in sections if section["position"] == position]
    return sorted(selected, key=lambda section: section["order"] if section["order"] is not None else 10**6)


def resolve_extra_sections(context: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    raw_items = context.get("manual_extra_sections")
    if MANUAL_RENDERER_AVAILABLE:
        return normalize_extra_sections({"manual_extra_sections": raw_items})
    return fallback_normalize_extra_sections(raw_items)


def sections_at(sections: list[dict[str, Any]], position: str) -> list[dict[str, Any]]:
    if MANUAL_RENDERER_AVAILABLE:
        return extra_sections_at(sections, position)
    return fallback_extra_sections_at(sections, position)


def manual_module_titles(context: dict[str, Any]) -> list[str]:
    """Chapter titles the manual will use for `manual_modules`, after the renderer's own cleanup."""
    titles: list[str] = []
    for index, module in enumerate(context.get("manual_modules") or [], start=1):
        if isinstance(module, dict):
            raw = module.get("title") or module.get("feature") or f"功能模块 {index}"
        else:
            raw = str(module)
        titles.append(str(raw).strip() or f"功能模块 {index}")
    return titles


def manual_chapter_sequence(context: dict[str, Any], sections: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """The full chapter list in the order the manual will render it, as (title, kind)."""
    sequence: list[tuple[str, str]] = [
        ("说明", "必备章节"),
        ("功能特点", "必备章节"),
        ("系统要求", "必备章节"),
    ]
    for section in sections_at(sections, "before_modules"):
        sequence.append((section["title"], "项目自定章节"))
    sequence.extend((title, "功能模块章节") for title in manual_module_titles(context))
    for section in sections_at(sections, "after_modules"):
        sequence.append((section["title"], "项目自定章节"))
    sequence.append(("常见问题解答", "必备章节"))
    for section in sections_at(sections, "before_glossary"):
        sequence.append((section["title"], "项目自定章节"))
    sequence.append(("术语表", "必备章节"))
    return sequence


def raw_extra_section(context: dict[str, Any], title: str) -> dict[str, Any]:
    for item in context.get("manual_extra_sections") or []:
        if isinstance(item, dict) and str(item.get("title") or "").strip() == title:
            return item
    return {}


def append_manual_structure_md(lines: list[str], context: dict[str, Any]) -> None:
    sections, issues = resolve_extra_sections(context)
    lines.extend(
        [
            "",
            "## 操作手册章节构成",
            "",
            "以下为操作手册成稿将实际包含的全部章节及其顺序。必备章节固定为说明、功能特点、系统要求、"
            "各功能模块、常见问题解答、术语表，顺序不变。其余章节由模型按本项目实际情况判断设立。"
            "请据此确认各自定章节是否应当存在、所处位置是否合适。",
            "",
            "### 最终章节顺序",
            "",
        ]
    )
    for index, (title, kind) in enumerate(manual_chapter_sequence(context, sections), start=1):
        lines.append(f"{index}. {title}（{kind}）")
    lines.extend(["", "### 项目自定章节", ""])
    if not sections:
        lines.append(
            "- 本项目未设立必备章节之外的章节。该结果系依据 references/manual_structure.md「项目自定章节」"
            "三条判定标准逐项判断得出，并非遗漏。"
        )
    for index, section in enumerate(sections, start=1):
        raw = raw_extra_section(context, section["title"])
        position = section["position"]
        position_label = EXTRA_SECTION_POSITION_LABELS.get(position, position)
        order = section["order"]
        lines.append(f"{index}. {section['title']}")
        lines.append(f"   - 位置：{position}（{position_label}）")
        lines.append(f"   - 同位置内次序：{order if order is not None else '未指定，按数组原有先后排列'}")
        reason = str(raw.get("reason") or "").strip()
        lines.append(f"   - 设立理由：{reason or '未填写，须补充对照三条判定标准的说明'}")
        evidence = raw.get("evidence")
        if isinstance(evidence, list):
            evidence_text = "；".join(str(item).strip() for item in evidence if str(item).strip())
        else:
            evidence_text = str(evidence or "").strip()
        lines.append(f"   - 证据来源：{evidence_text or '未填写，须补充源码路径、配置文件或文档位置'}")
        if section["paragraphs"]:
            lines.append("   - 正文段落：")
            lines.extend(f"     - {paragraph}" for paragraph in section["paragraphs"])
        else:
            lines.append("   - 正文段落：无")
        table = section["table"]
        if table:
            headers = "、".join(str(header) for header in table.get("headers") or [])
            row_count = len(table.get("rows") or [])
            lines.append(f"   - 附表：本章附有表格，表头为 {headers}，共 {row_count} 行数据")
    if issues:
        lines.extend(["", "### 未纳入成稿的自定章节条目", ""])
        lines.extend(f"- {issue}" for issue in issues)


def write_context_md(path: Path, context: dict[str, Any]) -> None:
    lines = [
        "# 业务理解",
        "",
        f"- 软件名称：{context['software_name']}",
        f"- 产品定位：{context['product_positioning']}",
        f"- 面向领域 / 行业：{context['industry']}",
        f"- 核心价值：{context['core_value']}",
        f"- 证据文件：`{context['project_evidence_file']}`",
        "",
        "## 目标用户",
        "",
    ]
    lines.extend(f"- {item}" for item in context["target_users"])
    lines.extend(["", "## 主要业务功能", ""])
    lines.extend(f"- {item}" for item in context["business_features"])
    lines.extend(["", "## 功能说明", ""])
    for item in context["business_features"]:
        lines.append(f"- {item}：{context['business_feature_details'].get(item, '')}")
    lines.extend(["", "## 典型操作流程", ""])
    lines.extend(f"{i}. {item}" for i, item in enumerate(context["operation_flow"], start=1))
    append_manual_structure_md(lines, context)
    if context.get("manual_modules"):
        lines.extend(["", "## 操作手册页面/流程模块", ""])
        for i, module in enumerate(context["manual_modules"], start=1):
            if not isinstance(module, dict):
                lines.append(f"{i}. {module}")
                continue
            title = module.get("title") or module.get("feature") or f"模块 {i}"
            usage = module.get("usage") or module.get("usage_scenario") or ""
            entry = module.get("entry") or ""
            steps = module.get("operation_steps") or module.get("steps") or []
            lines.append(f"{i}. {title}" + (f"：{entry}" if entry else ""))
            if usage:
                lines.append(f"   - 使用场景：{usage}")
            if steps:
                lines.append(f"   - 操作要点：{'；'.join(str(item) for item in steps)}")
    lines.extend(
        [
            "",
            "## 申请表建议口径",
            "",
            f"- 开发目的：{context['application_purpose']}",
            f"- 软件的主要功能：{context['main_functions']}",
            f"- 技术特点：{context['technical_characteristics']}",
            f"- 软件的技术特点选项：{context['software_technical_option']}",
            f"- 软件分类：{context['software_category']}",
            "",
            "## 证据来源",
            "",
        ]
    )
    lines.extend(f"- `{item['path']}`" for item in context["source_documents"])
    lines.extend(["", "## 待确认", ""])
    lines.extend(f"- {item}" for item in context["review_notes"])
    lines.extend(
        [
            "",
            "```text",
            "STOP_FOR_USER",
            f"NEXT_ACTION: {context['next_action']}",
            "```",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--software-name", required=True)
    parser.add_argument("--out-dir", default="软件著作权申请资料/草稿")
    parser.add_argument("--web-notes", help="Optional plain-text notes from external/competitor research")
    parser.add_argument("--model-context", help="Model-authored business context JSON")
    args = parser.parse_args()

    project = Path(args.project)
    analysis = read_json(Path(args.analysis))
    web_notes = read_text(Path(args.web_notes)) if args.web_notes else ""
    out_dir = ensure_dir(Path(args.out_dir))

    evidence = build_evidence(project, analysis, args.software_name, web_notes)
    write_json(out_dir / "业务理解证据.json", evidence)
    write_evidence_md(out_dir / "业务理解证据.md", evidence)

    if not args.model_context:
        write_model_template(out_dir / "业务理解模型稿模板.json", evidence)
        print(f"OK business evidence: {out_dir / '业务理解证据.md'}")
        print(f"OK model template: {out_dir / '业务理解模型稿模板.json'}")
        print("NEXT_ACTION: 须先阅读业务理解证据与项目源码，自行编写业务理解模型稿 JSON，再以 --model-context 生成 业务理解.md 与 业务理解.json。")
        return

    model = load_model_context(Path(args.model_context))
    context = normalize_model_context(model, evidence, web_notes)
    write_json(out_dir / "业务理解.json", context)
    write_context_md(out_dir / "业务理解.md", context)
    print(f"OK business context: {out_dir / '业务理解.md'}")
    print(f"Features: {len(context['business_features'])}")
    print("STOP_FOR_USER")
    print(f"NEXT_ACTION: {context['next_action']}")


if __name__ == "__main__":
    main()
