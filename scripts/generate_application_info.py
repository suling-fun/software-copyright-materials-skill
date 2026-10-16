#!/usr/bin/env python3
"""Generate the Markdown draft for application form information."""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
from pathlib import Path
from typing import Any

from common import ensure_dir, read_json, read_text


MIN_MAIN_FUNCTION_CHARS = 500
MAX_MAIN_FUNCTION_CHARS = 1300

# 「软件的技术特点」缺失时的待确认占位。口径与「开发目的」一致：含「待用户确认」字样，
# 可被 verify_materials.py 的 STRICT-全文占位检测拦截，不可能静默进入正式材料。
TECHNICAL_CHARACTERISTICS_PENDING = (
    "待用户确认（≤100字符，须说明本软件自身的技术实现要点，"
    "不得套用通用框架与分层描述）"
)


# 开发工具与运行平台在无任何项目证据时的待确认占位：无证据的确定性断言（例如一律
# 回答 Visual Studio Code）同样属于编造，交由用户按实际情况填写。
IDE_PENDING = "待用户确认（填写实际使用的开发工具名称）"
RUNTIME_OS_PENDING = "待用户确认（≤50字符，填写软件实际运行的操作系统或浏览器环境）"


FIELD_ORDER = [
    "软件全称",
    "软件简称",
    "版本号",
    "软件分类",
    "开发完成日期",
    "开发方式",
    "软件说明",
    "发表状态",
    "首次发表日期",
    "著作权人",
    "权利范围",
    "权利取得方式",
    "开发的硬件环境",
    "运行的硬件环境",
    "开发该软件的操作系统",
    "软件开发环境 / 开发工具",
    "该软件的运行平台 / 操作系统",
    "软件运行支撑环境 / 支持软件",
    "编程语言",
    "源程序量",
    "开发目的",
    "面向领域 / 行业",
    "软件的主要功能",
    "软件的技术特点",
    "页数",
]


def effective_len(value: str) -> int:
    return len(str(value or "").replace(" ", "").replace("\n", ""))


# 「软件的主要功能」的待补写口径。此处刻意不提供任何通用功能描述：凡「面向用户业务
# 场景的综合软件系统」「帮助用户高效完成日常工作」一类句子，换成任意其他软件后同样
# 成立，属模板套话。以套话替换模型依据真实项目写出的内容，比内容缺失更为有害，故
# 缺失与不足下限两种情形一律落到待确认占位，由用户回到业务理解补写真实内容。
MAIN_FUNCTION_MISSING_PENDING = (
    f"待用户确认（{MIN_MAIN_FUNCTION_CHARS} 至 {MAX_MAIN_FUNCTION_CHARS} 字，"
    "须逐项写明本软件实际具备的功能、操作方式与处理结果，每一项均须能在项目代码或界面中找到对应证据；"
    "不得以通用描述、评价性表述或重复语句凑足字数）"
)


def main_function_pending_notice(text: str) -> str:
    """Flag an under-length 主要功能 as pending while keeping the real text intact.

    原文逐字保留在占位说明之后，绝不以生成文本替换；返回值含「待用户确认」，
    可被 verify_materials.py 的 STRICT-全文占位检测拦下，未补写即无法推进到正式材料。
    """
    length = effective_len(text)
    gap = MIN_MAIN_FUNCTION_CHARS - length
    return (
        f"待用户确认（现有正文有效字数为 {length} 字，低于下限 {MIN_MAIN_FUNCTION_CHARS} 字，尚缺 {gap} 字。"
        "须回到 草稿/业务理解.json 的 main_functions，依据本软件的真实功能补写至下限后重新生成；"
        "补写内容须能在项目代码或界面中找到对应证据，不得以通用描述、评价性表述或重复语句凑足字数。"
        "以下为现有原文，须在其基础上补写，不得删改）"
    ) + str(text)


def normalize_main_function(value: Any) -> str:
    """Return the 主要功能 field value without ever fabricating content.

    - 缺失：落到待确认占位，与「开发目的」「软件的技术特点」两个字段口径一致。
    - 不足下限：保留原文，前置待补写说明，标为待确认。
    - 满足下限：原样返回，不作任何改写。
    """
    text = str(value or "")
    if not text.strip():
        return MAIN_FUNCTION_MISSING_PENDING
    if "待用户确认" in text:
        return text
    if effective_len(text) < MIN_MAIN_FUNCTION_CHARS:
        return main_function_pending_notice(text)
    return text


def build_fields(
    analysis: dict[str, Any],
    manifest: dict[str, Any],
    software_name: str,
    version: str,
    answers: dict[str, str],
    business: dict[str, Any] | None = None,
) -> dict[str, str]:
    language = analysis.get("language") or "待用户确认"
    project = Path(analysis.get("project_root") or ".")
    hardware_hint = current_hardware_environment()
    dev_os_hint = current_operating_system()
    version_hint = version_confirmation_hint(analysis, version)
    software_name_hint = f"待用户确认（建议：{software_name}；请确认最终软件全称）"

    defaults = {
        "软件全称": software_name_hint,
        "软件简称": "",
        "版本号": version_hint,
        "软件分类": (business.get("software_category") or "应用软件") if business else "应用软件",
        "开发完成日期": "待用户确认（YYYY-MM-DD）",
        "开发方式": (business.get("development_situation") or "单独开发") if business else "单独开发",
        "软件说明": "原创",
        "发表状态": "待用户确认（已发表/未发表）",
        "首次发表日期": "待用户确认（YYYY-MM-DD，未发表则留空）",
        "著作权人": "待用户确认",
        "权利范围": (business.get("rights_scope") or "全部权利") if business else "全部权利",
        "权利取得方式": (business.get("rights_acquisition") or "原始取得") if business else "原始取得",
        "开发的硬件环境": hardware_hint,
        "运行的硬件环境": hardware_hint,
        "开发该软件的操作系统": dev_os_hint,
        "软件开发环境 / 开发工具": f"开发环境: {dev_os_hint}/开发工具: {infer_ide_name(project)}",
        "该软件的运行平台 / 操作系统": infer_runtime_os(analysis),
        "软件运行支撑环境 / 支持软件": infer_runtime_support(analysis, project),
        "编程语言": language,
        "源程序量": str(
            (analysis.get("source") or {}).get("total_line_count")
            or manifest.get("source_line_count")
            or manifest.get("selected_source_line_count")
            or "待用户确认"
        ),
        "开发目的": (business.get("application_purpose") or f"待用户确认（≤50字符，须说明开发目的，不得仅写软件名称）") if business else "待用户确认（≤50字符，须说明开发目的，不得仅写软件名称）",
        "面向领域 / 行业": (business.get("industry") or "待用户确认") if business else "待用户确认",
        # 主要功能不设通用兜底文案：缺失或不足下限时一律落到待确认占位，且保留模型
        # 依据真实项目写出的原文，绝不以生成文本替换（见 normalize_main_function）。
        "软件的主要功能": (business.get("main_functions") if business else ""),
        # 技术特点不设通用兜底文案：任何「采用某某框架、模块化组件、状态管理」一类描述
        # 换成任意其他软件后同样成立，属于模板套话，一旦静默进入正式材料即构成空洞申报。
        # 业务理解未提供该字段时一律落到待确认占位，由用户结合本软件实际实现补写。
        "软件的技术特点": (business.get("technical_characteristics") or TECHNICAL_CHARACTERISTICS_PENDING) if business else TECHNICAL_CHARACTERISTICS_PENDING,
        # 页数口径为「代码鉴别材料实际页数」：优先取按完整文件边界对齐裁剪后的实际渲染页数
        # （rendered_pages）；仅当该字段缺失（旧版本产物或未生成该字段）时回退到理论总页数
        # （total_pages，即不作前 30 页与后 30 页裁剪时的总页数）。两者在「全部代码」模式下相等。
        "页数": str(manifest.get("rendered_pages") or manifest.get("total_pages") or "待用户确认"),
    }
    defaults.update({k: v for k, v in answers.items() if v})

    # 未发表时首次发表日期应为空，不触发待确认项
    publish_status = defaults.get("发表状态", "")
    if "未发表" in publish_status and "已发表" not in publish_status:
        if "待用户确认" in defaults.get("首次发表日期", "") or not defaults.get("首次发表日期", "").strip():
            defaults["首次发表日期"] = ""

    # 软件的主要功能：缺失或不足下限时标为待补写，原文一律保留。
    # 此处同时覆盖业务理解与 --answers 两条来源，两者的处理口径保持一致。
    defaults["软件的主要功能"] = normalize_main_function(defaults.get("软件的主要功能"))

    return defaults


def version_numbers(value: str) -> tuple[int, ...]:
    raw = str(value or "").strip()
    raw = raw.lstrip("vV")
    parts = re.findall(r"\d+", raw)
    return tuple(int(part) for part in parts[:3])


def version_less_than_1(value: str) -> bool:
    numbers = version_numbers(value)
    return bool(numbers) and numbers[0] < 1


def normalize_version_label(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return raw if raw.upper().startswith("V") else f"V{raw}"


def project_version_candidate(analysis: dict[str, Any]) -> str:
    value = str((analysis.get("package") or {}).get("version") or "").strip()
    if value and value.upper() != "V1.0":
        return normalize_version_label(value)
    return ""


def version_confirmation_hint(analysis: dict[str, Any], requested_version: str) -> str:
    project_version = project_version_candidate(analysis)
    requested = normalize_version_label(requested_version or "V1.0")
    if project_version and version_less_than_1(project_version):
        return (
            f"待用户确认（项目版本号为 {project_version}，软著首次提交通常建议从 V1.0 开始；"
            f"请确认填写 V1.0 还是 {project_version}）"
        )
    if not project_version and version_less_than_1(requested):
        return (
            f"待用户确认（当前建议版本号为 {requested}，软著首次提交通常建议从 V1.0 开始；"
            f"请确认填写 V1.0 还是 {requested}）"
        )
    if project_version and project_version != requested:
        return f"待用户确认（项目版本号为 {project_version}，当前建议为 {requested}；请确认最终申报版本号）"
    return f"待用户确认（建议：{requested}；请确认最终版本号）"


def format_gb(size: int | None) -> str:
    if not size:
        return ""
    return f"{size / (1024 ** 3):.0f}GB"


def total_memory_bytes() -> int | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None


def current_hardware_environment() -> str:
    parts: list[str] = []
    cpu_count = os.cpu_count()
    machine = platform.machine()
    processor = platform.processor()
    if processor and processor != machine and processor.lower() != "arm":
        parts.append(f"CPU {processor}")
    if cpu_count:
        parts.append(f"CPU {cpu_count}核")
    if machine:
        parts.append(f"架构 {machine}")
    memory = format_gb(total_memory_bytes())
    if memory:
        parts.append(f"内存 {memory}")
    try:
        disk = shutil.disk_usage(Path.home())
        disk_total = format_gb(disk.total)
        if disk_total:
            parts.append(f"硬盘 {disk_total}")
    except OSError:
        pass
    if parts:
        return "、".join(parts)
    return "待用户确认"


def current_operating_system() -> str:
    system = platform.system()
    if system == "Darwin":
        version = platform.mac_ver()[0]
        label = f"macOS {version}" if version else f"macOS（Darwin {platform.release()}）"
    elif system == "Windows":
        label = f"Windows {platform.release()}"
    elif system == "Linux":
        label = f"Linux {platform.release()}"
    else:
        label = f"{system} {platform.release()}".strip() or "待用户确认"
    return label


def infer_ide_name(project: Path) -> str:
    if (project / ".idea").exists():
        return "WebStorm 或 IntelliJ IDEA"
    if (project / ".vscode").exists():
        return "Visual Studio Code"
    if list(project.glob("*.code-workspace")):
        return "Visual Studio Code"
    # 项目中没有任何 IDE 痕迹时不臆断开发工具：无证据的确定性断言与套话同性质。
    return IDE_PENDING


def infer_runtime_os(analysis: dict[str, Any]) -> str:
    frameworks = set(analysis.get("frameworks") or [])
    deps = set((analysis.get("package") or {}).get("dependency_names") or [])
    if "Electron" in frameworks or "electron" in deps or "Tauri" in frameworks or "@tauri-apps/api" in deps:
        return "Windows 10/11 或 macOS 13及以上版本"
    if frameworks & {"Vue", "React", "Vite", "Next.js", "Nuxt", "Svelte", "Astro", "Angular"}:
        return "Windows 10/11 或 macOS 13及以上版本"
    # 既未识别到桌面端框架，也未识别到前端框架时，运行平台无从推断，交由用户填写。
    return RUNTIME_OS_PENDING


def project_file(project: Path, relative: str) -> Path | None:
    if not relative:
        return None
    path = project / relative
    return path if path.exists() else None


def load_project_package(project: Path, analysis: dict[str, Any]) -> dict[str, Any]:
    package_path = project_file(project, (analysis.get("package") or {}).get("path") or "")
    if package_path:
        try:
            return read_json(package_path)
        except Exception:
            return {}
    return {}


def read_readme(project: Path) -> str:
    for name in ("README.md", "README.zh.md", "readme.md", "Readme.md"):
        path = project / name
        if path.exists():
            try:
                return read_text(path, limit=12000)
            except Exception:
                return ""
    return ""


def extract_requirement_bullets(text: str) -> list[str]:
    wanted = ("python", "node", "docker", "compose", "postgres", "redis", "chrome", "edge", "safari")
    # Patterns that indicate a feature description rather than a runtime requirement.
    _feature_start = re.compile(r"^(?:[一-鿿]|L\d|P\d|[A-Z]\d\s)")
    bullets: list[str] = []
    for line in text.splitlines():
        match = re.match(r"\s*[-*]\s+(.+)", line)
        if not match:
            continue
        item = match.group(1).strip()
        if any(key in item.lower() for key in wanted) and item not in bullets:
            if len(item) > 80:
                continue
            if _feature_start.match(item) and "（" in item:
                continue
            bullets.append(item)
    return bullets[:8]


def detect_package_manager(project: Path, package_path: str) -> str:
    base = (project / package_path).parent if package_path else project
    checks = [
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "Yarn"),
        ("bun.lock", "Bun"),
        ("bun.lockb", "Bun"),
        ("package-lock.json", "npm"),
    ]
    for filename, manager in checks:
        if (base / filename).exists() or (project / filename).exists():
            return manager
    return "npm"


def has_support_term(items: list[str], term: str) -> bool:
    return any(term.lower() in item.lower() for item in items)


def infer_runtime_support(analysis: dict[str, Any], project: Path) -> str:
    """Infer runtime support environment (≤50 chars plain text)."""
    package_info = load_project_package(project, analysis)
    package_path = (analysis.get("package") or {}).get("path") or ""
    deps = set((analysis.get("package") or {}).get("dependency_names") or [])
    frameworks = set(analysis.get("frameworks") or [])

    support: list[str] = []
    readme_requirements = extract_requirement_bullets(read_readme(project))

    if package_info or deps or frameworks & {"Vue", "React", "Vite", "Next.js", "Nuxt", "Svelte", "Astro", "Angular"}:
        if not has_support_term(support, "node"):
            node_engine = str((package_info.get("engines") or {}).get("node") or "").strip()
            support.append(f"Node.js {node_engine}".strip() if node_engine else "Node.js")
        support.append(detect_package_manager(project, package_path))
        support.append("现代浏览器")

    if ((project / "pyproject.toml").exists() or any(project.glob("*/pyproject.toml"))) and not has_support_term(support, "python"):
        support.append("Python")
    if ((project / "requirements.txt").exists() or list(project.glob("*/requirements*.txt"))) and not has_support_term(support, "python"):
        support.append("Python")

    if ((project / "docker-compose.yml").exists() or (project / "docker-compose.yaml").exists() or list(project.glob("docker-compose*.yml"))) and not has_support_term(support, "docker"):
        support.append("Docker")

    compose_text = ""
    for compose in list(project.glob("docker-compose*.yml")) + list(project.glob("docker-compose*.yaml")):
        try:
            compose_text += "\n" + read_text(compose, limit=20000).lower()
        except Exception:
            continue
    if "postgres" in compose_text:
        support.append("PostgreSQL")
    if "redis" in compose_text:
        support.append("Redis")

    # Also incorporate readme requirements into support software
    for req in readme_requirements:
        if not has_support_term(support, req.split()[0].lower()):
            support.append(req)

    # Deduplicate
    unique: list[str] = []
    for item in support:
        clean = str(item).strip().rstrip("；;")
        if clean and clean not in unique:
            unique.append(clean)

    if not unique:
        return "待用户确认"

    # Enforce ≤50 char limit: trim items if needed
    result = "、".join(unique)
    if len(result) > 50:
        trimmed: list[str] = []
        for item in unique:
            candidate = "、".join(trimmed + [item])
            if len(candidate) <= 50:
                trimmed.append(item)
            else:
                break
        result = "、".join(trimmed) if trimmed else unique[0][:50]
    return result


def write_application_md(path: Path, fields: dict[str, str], analysis: dict[str, Any], manifest: dict[str, Any], business: dict[str, Any] | None = None) -> None:
    # 未发表时，首次发表日期不应输出「待用户确认」，此处直接留空
    publish_status = fields.get("发表状态", "")
    if "未发表" in publish_status and "已发表" not in publish_status:
        if "待用户确认" in (fields.get("首次发表日期") or "") or not (fields.get("首次发表日期") or "").strip():
            fields["首次发表日期"] = ""

    lines = ["# 申请表信息", ""]
    for field in FIELD_ORDER:
        lines.append(f"➤{field}：{fields.get(field, '待用户确认')}")
    pending = [field for field in FIELD_ORDER if "待用户确认" in (fields.get(field) or "")]

    # Build warnings for common issues
    warnings: list[str] = []
    soft_name = fields.get("软件全称", "")
    clean_name = str(soft_name).strip()
    raw_name = clean_name
    if "待用户确认" in clean_name and "建议：" in clean_name:
        try:
            raw_name = clean_name.split("建议：")[1].rstrip("）").split("；")[0].strip()
        except (IndexError, ValueError):
            raw_name = clean_name
    recommended_suffixes = ("软件", "平台", "系统")
    if raw_name and not raw_name.endswith(recommended_suffixes):
        warnings.append("软件全称未以「软件」「平台」或「系统」结尾，建议结合实际命名补充软件属性后缀。")

    main_func = fields.get("软件的主要功能", "")
    if main_func and "待用户确认" not in main_func:
        func_len = len(str(main_func).replace(" ", "").replace("\n", ""))
        if func_len < MIN_MAIN_FUNCTION_CHARS:
            warnings.append(f"软件的主要功能仅有 {func_len} 字，应不少于 {MIN_MAIN_FUNCTION_CHARS} 字，须扩写功能说明。")
        elif func_len > MAX_MAIN_FUNCTION_CHARS:
            warnings.append(f"软件的主要功能共 {func_len} 字，超过建议上限 {MAX_MAIN_FUNCTION_CHARS} 字，须予精简。")

    # Check character limits for fields with ≤50 or ≤100 constraints
    char_limit_fields = {
        "开发的硬件环境": 50,
        "运行的硬件环境": 50,
        "开发该软件的操作系统": 50,
        "软件开发环境 / 开发工具": 50,
        "该软件的运行平台 / 操作系统": 50,
        "软件运行支撑环境 / 支持软件": 50,
        "开发目的": 50,
        "面向领域 / 行业": 50,
        "软件的技术特点": 100,
    }
    for field_name, limit in char_limit_fields.items():
        value = fields.get(field_name, "")
        if value and "待用户确认" not in value and len(value) > limit:
            warnings.append(f"{field_name}共 {len(value)} 字符，超过限制 {limit} 字符，须予精简。")

    lines.extend(
        [
            "",
            "## 字段填写口径",
            "",
            "- 软件全称：须由用户确认。最终正式资料文件名、代码页眉与操作手册中的软件名称均以本字段为准。",
            "- 软件全称如含商标文字（品牌名、自造词、第三方平台名称或带 ®、™ 标识的文字），版权中心可能要求提供商标注册证明或权利声明。须由用户选择去除商标文字，或在确有注册商标时保留原名称并准备商标证明，选择结果写入 application-fields 确认记录。",
            "- 软件简称：可选，如有常用简称则填写。",
            "- 版本号：须由用户确认。项目版本小于 V1.0 时，软著首次提交通常建议使用 V1.0，亦可按实际项目版本填写，最终以本字段为准。",
            "- 软件分类：应用软件/嵌入式软件/中间件/系统软件/其他。",
            "- 开发完成日期、首次发表日期：须使用 YYYY-MM-DD 格式。",
            "- 开发方式：单独开发/合作开发/委托开发/下达任务开发。",
            "- 软件说明：原创 / 修改（含翻译软件、合成软件）。",
            "- 发表状态：已发表或未发表。已发表时须附首次发表日期。",
            "- 软件开发环境 / 开发工具：≤50字符，格式 `开发环境: <OS>/开发工具: <IDE名称>`。",
            "- 开发该软件的操作系统：≤50字符，填写实际开发电脑的操作系统版本。",
            "- 该软件的运行平台 / 操作系统：≤50字符，填写软件运行所在的操作系统或浏览器环境。",
            "- 软件运行支撑环境 / 支持软件：≤50字符，直接列出运行依赖（如 Node.js、npm、浏览器），不加格式前缀。",
            "- 开发的硬件环境与运行的硬件环境：≤50字符，可采用检测到的本机配置作为建议值。",
            "- 源程序量：纯数字（不含「行」字），指全部源程序的总行数。",
            "- 开发目的：≤50字符，以一句话说明开发目的，不得仅写软件名称。",
            "- 面向领域 / 行业：≤50字符。",
            "- 软件的主要功能：500 至 1300 字。",
            "- 软件的技术特点：多选标签（APP/游戏软件/教育软件/金融软件/医疗软件/地理信息软件/云计算软件/信息安全软件/大数据软件/人工智能软件/VR软件/5G软件/小程序/物联网软件/智慧城市软件）+ 文本描述≤100字符。",
            "",
            "## 项目分析摘要",
            "",
            f"- 项目目录：{analysis.get('project_root', '')}",
            f"- 框架：{'、'.join(analysis.get('frameworks') or []) or '未识别'}",
            f"- 源码文件数：{analysis.get('source', {}).get('total_file_count', analysis.get('source', {}).get('file_count', 0))}",
            f"- 源程序量（含空行）：{analysis.get('source', {}).get('total_line_count', analysis.get('source', {}).get('line_count', 0))}",
            f"- 代码材料页数：{manifest.get('rendered_pages') or manifest.get('total_pages', 0)}",
            f"- 代码输出模式：{manifest.get('mode', '')}",
            f"- 业务理解：{'已读取 草稿/业务理解.json' if business else '未提供，以项目分析结果缺省生成'}",
            "",
            "## 待确认字段",
            "",
        ]
    )
    if warnings:
        lines.append("## 字段提醒")
        lines.append("")
        lines.extend(f"- {w}" for w in warnings)
        lines.append("")
    if pending:
        lines.extend(f"- {field}" for field in pending)
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "```text",
            "STOP_FOR_USER",
            "NEXT_ACTION: 须补全并确认申请表字段。确认后运行 confirm_stage.py --stage application-fields。",
            "```",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def require_confirmed_business(business: dict[str, Any] | None) -> None:
    if business is None:
        raise SystemExit(
            "STOP_FOR_USER\n"
            "NEXT_ACTION: 申请表信息须基于已确认的业务理解生成。须先生成并确认 草稿/业务理解.md。"
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
    parser.add_argument("--code-manifest", required=True)
    parser.add_argument("--software-name", required=True)
    parser.add_argument("--version", default="V1.0")
    parser.add_argument("--answers", help="Optional JSON object with confirmed field values")
    parser.add_argument("--business-context", help="Business context JSON generated before material drafting")
    parser.add_argument("--out-dir", default="软件著作权申请资料/草稿")
    args = parser.parse_args()

    analysis = read_json(Path(args.analysis))
    manifest = read_json(Path(args.code_manifest))
    answers = read_json(Path(args.answers)) if args.answers else {}
    business = read_json(Path(args.business_context)) if args.business_context else None
    require_confirmed_business(business)
    out_dir = ensure_dir(Path(args.out_dir))

    fields = build_fields(analysis, manifest, args.software_name, args.version, answers, business)
    out_path = out_dir / "申请表信息.md"
    write_application_md(out_path, fields, analysis, manifest, business)
    print(f"OK application draft: {out_path}")
    print("STOP_FOR_USER")
    print("NEXT_ACTION: 须补全并确认申请表字段。确认后运行 confirm_stage.py --stage application-fields。")


if __name__ == "__main__":
    main()
