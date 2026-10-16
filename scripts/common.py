#!/usr/bin/env python3
"""Shared helpers for the software copyright materials skill."""

from __future__ import annotations

import ast
import bisect
import io
import json
import os
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class CodeLayout:
    """代码鉴别材料的排版数值，单位为厘米与磅。"""

    page_width_cm: float
    page_height_cm: float
    margin_top_cm: float
    margin_bottom_cm: float
    margin_left_cm: float
    margin_right_cm: float
    header_distance_cm: float
    body_font_size_pt: float
    body_line_pt: float
    header_font_size_pt: float
    header_line_pt: float


# 代码鉴别材料两个渲染后端（build_docx_from_md.py 出 DOCX、render_code_pdf.py 直出 PDF）
# 的唯一数值来源。两份材料的分页必须逐页一致，代码材料的页数又是申请表「页数」字段的
# 硬校验对象，故改动此处会同时改变 DOCX 与 PDF 的分页，不得只调整其中一侧。
# 此处只存纯数值，厘米转 EMU、磅转点等单位换算由各后端自行完成，
# 以免本模块被迫依赖 python-docx 或 fpdf2。
# 每页行数（extract_code_material.LINES_PER_PAGE）不在此处：两个后端都不读取该值，
# 分页一律由抽取环节产出的中间 Markdown 决定；且 extract_code_material 依赖本模块，
# 反向引用会形成循环导入。
CODE_LAYOUT = CodeLayout(
    page_width_cm=21,
    page_height_cm=29.7,
    margin_top_cm=2.0,
    margin_bottom_cm=2.0,
    margin_left_cm=2.5,
    margin_right_cm=2.0,
    # python-docx 默认模板的 sectPr 记 w:header="720" twips，即 1.27cm，
    # DOCX 侧沿用该默认值，PDF 侧据此换算页眉距页面上边缘的距离。
    header_distance_cm=1.27,
    body_font_size_pt=7.2,
    body_line_pt=14,
    header_font_size_pt=8,
    header_line_pt=12,
)

EXCLUDE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".output",
    "coverage",
    "target",
    "vendor",
    "软件著作权申请资料",
    "software-copyright-materials",
}

CODE_EXTS = {
    ".vue",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".html",
    ".svelte",
    ".astro",
    ".json",
    ".md",
}

KNOWN_CONFIG_FILES = {
    ".babelrc",
    ".eslintrc",
    ".eslintrc.json",
    ".eslintrc.yaml",
    ".eslintrc.yml",
    ".prettierrc",
    ".prettierrc.json",
    ".prettierrc.yaml",
    ".prettierrc.yml",
    ".swcrc",
    "angular.json",
    "app.json",
    "astro.config.mjs",
    "astro.config.ts",
    "babel.config.js",
    "babel.config.json",
    "Cargo.lock",
    "Cargo.toml",
    "composer.json",
    "docker-compose.yaml",
    "docker-compose.yml",
    "eslint.config.cjs",
    "eslint.config.js",
    "eslint.config.mjs",
    "go.mod",
    "go.sum",
    "jsconfig.json",
    "lerna.json",
    "manifest.json",
    "next.config.js",
    "next.config.mjs",
    "next.config.ts",
    "nuxt.config.js",
    "nuxt.config.ts",
    "nx.json",
    "package-lock.json",
    "package.json",
    "playwright.config.js",
    "playwright.config.ts",
    "postcss.config.cjs",
    "postcss.config.js",
    "prettier.config.cjs",
    "prettier.config.js",
    "prettier.config.mjs",
    "project.json",
    "pyproject.toml",
    "rollup.config.js",
    "rollup.config.mjs",
    "rollup.config.ts",
    "svelte.config.js",
    "stylelintrc.json",
    "tailwind.config.js",
    "tailwind.config.ts",
    "tsconfig.app.json",
    "tsconfig.json",
    "tsconfig.node.json",
    "tslint.json",
    "turbo.json",
    "vite.config.js",
    "vite.config.mjs",
    "vite.config.ts",
    "vitest.config.js",
    "vitest.config.ts",
    "webpack.config.js",
    "webpack.config.ts",
    "workspace.json",
}

FRONTEND_EXTS = {
    ".vue",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".html",
    ".svelte",
    ".astro",
}

SUPPLEMENT_CODE_EXTS = {
    ".py",
    ".java",
    ".go",
    ".rs",
    ".cs",
    ".php",
    ".rb",
    ".kt",
    ".swift",
    ".sql",
    ".sh",
    ".json",
}

DART_CODE_EXTS = {
    ".dart",
}

COPYRIGHT_CODE_EXTS = FRONTEND_EXTS | SUPPLEMENT_CODE_EXTS | DART_CODE_EXTS

LOCK_FILES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "bun.lock",
}


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[3]


def is_excluded(path: Path) -> bool:
    parts = set(path.parts)
    if parts & EXCLUDE_DIRS:
        return True
    name = path.name
    if name.startswith(".") and name not in {".env.example"}:
        return True
    if name in LOCK_FILES:
        return True
    if name.endswith(".map") or name.endswith(".min.js") or name.endswith(".min.css"):
        return True
    return False


def iter_project_files(project: Path, exts: set[str] | None = None) -> Iterable[Path]:
    project = project.resolve()
    for root, dirs, files in os.walk(project):
        root_path = Path(root)
        dirs[:] = [d for d in dirs if not is_excluded(root_path / d)]
        for filename in files:
            path = root_path / filename
            if is_excluded(path):
                continue
            if exts is not None and path.suffix.lower() not in exts:
                continue
            yield path


def rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def read_text(path: Path, limit: int | None = None) -> str:
    data = path.read_bytes()
    if limit is not None:
        data = data[:limit]
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(read_text(path))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def count_text_lines(path: Path, skip_blank: bool = True) -> int:
    try:
        text = read_text(path)
    except Exception:
        return 0
    if not text:
        return 0
    if skip_blank:
        return sum(1 for line in text.splitlines() if line.strip())
    return len(text.splitlines())


def is_known_config_file(path: Path) -> bool:
    """Return True for well-known config files that shouldn't count as source code."""
    return path.name in KNOWN_CONFIG_FILES


def looks_binary(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:4096]
    except Exception:
        return True
    return b"\x00" in chunk


def normalize_title(value: str) -> str:
    value = re.sub(r"[-_]+", " ", value).strip()
    value = re.sub(r"\s+", " ", value)
    return value or "待命名软件"


def safe_filename(value: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]+', "_", value).strip()
    return value or "软件"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# 注释剔除
#
# 中国版权保护中心要求代码鉴别材料不含注释。extract_code_material.py 抽取时调用
# strip_comments() 剔除注释，verify_materials.py 交付前再以同一函数复检残留，
# 两处共用本节实现，不得另行复制。
#
# 处理方式按后缀分三档：
# - 完整剔除（full）：Python 以标准库 tokenize 识别注释、以 ast 定位文档字符串；
#   C 系语言、CSS 系与 HTML/Vue 等标记语言以逐字符状态机识别，字符串、模板字符串
#   与正则字面量内的注释符号不作注释处理。
# - 仅剔除整行注释（whole_line）：状态机无法可靠处理的语言（PHP、Shell、Ruby、SQL、
#   YAML、TOML），以及 Python 解析失败的文件，退回整行注释判定。
# - 无注释语法（none）：JSON 等本身不含注释的格式。
# ---------------------------------------------------------------------------

C_STYLE_EXTS = {
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".c", ".h", ".cc",
    ".cpp", ".hpp", ".cs", ".go", ".rs", ".swift", ".kt", ".kts", ".php",
    ".dart", ".scss", ".sass", ".less",
}
CSS_STYLE_EXTS = {".css"}
HASH_STYLE_EXTS = {".py", ".sh", ".bash", ".zsh", ".rb", ".yml", ".yaml", ".toml"}
MARKUP_SCRIPT_EXTS = {".html", ".htm", ".vue", ".svelte", ".astro"}
MARKUP_ONLY_EXTS = {".xml", ".svg"}
SQL_STYLE_EXTS = {".sql"}
NO_COMMENT_EXTS = {".json"}

STRIP_MODE_FULL = "full"
STRIP_MODE_WHOLE_LINE = "whole_line"
STRIP_MODE_NONE = "none"
STRIP_MODE_LABELS = {
    STRIP_MODE_FULL: "完整剔除",
    STRIP_MODE_WHOLE_LINE: "仅剔除整行注释",
    STRIP_MODE_NONE: "无注释语法",
}

PY_ENCODING_RE = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*[-\w.]+")


def comment_syntax(suffix: str) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Return (line comment prefixes, block comment (open, close) pairs) for a suffix.

    仅供整行注释判定（classify_source_lines）使用：整行 strip 后以注释符开头，
    或该行处于已开启的块注释之内，才计为注释行。
    """
    suffix = suffix.lower()
    if suffix in C_STYLE_EXTS or suffix in MARKUP_SCRIPT_EXTS:
        prefixes: tuple[str, ...] = ("//",)
        blocks: tuple[tuple[str, str], ...] = (("/*", "*/"),)
        if suffix in MARKUP_SCRIPT_EXTS:
            blocks = (("<!--", "-->"), ("/*", "*/"))
        if suffix == ".php":
            prefixes = ("//", "#")
        return prefixes, blocks
    if suffix in CSS_STYLE_EXTS:
        return (), (("/*", "*/"),)
    if suffix in HASH_STYLE_EXTS:
        return ("#",), ()
    if suffix in MARKUP_ONLY_EXTS:
        return (), (("<!--", "-->"),)
    if suffix in SQL_STYLE_EXTS:
        return ("--",), (("/*", "*/"),)
    return (), ()


def split_source_lines(text: str) -> list[str]:
    """Split on \\n / \\r\\n / \\r only, matching how tokenize and ast count lines."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _is_directive_line(index: int, stripped: str, suffix: str) -> bool:
    """Shebang 与 Python 编码声明属解释器指令，保留，不计为注释。"""
    if index == 0 and stripped.startswith("#!"):
        return True
    if suffix == ".py" and index <= 1 and PY_ENCODING_RE.match(stripped):
        return True
    return False


def classify_source_lines(text: str, suffix: str) -> list[str]:
    """Classify every source line as blank / comment / code (whole-line rule only)."""
    suffix = suffix.lower()
    prefixes, blocks = comment_syntax(suffix)
    kinds: list[str] = []
    pending_close: str | None = None
    for index, raw in enumerate(split_source_lines(text)):
        stripped = raw.strip()
        if not stripped:
            kinds.append("blank")
            continue
        if pending_close is not None:
            pos = stripped.find(pending_close)
            if pos == -1:
                kinds.append("comment")
                continue
            rest = stripped[pos + len(pending_close):].strip()
            pending_close = None
            kinds.append("code" if rest else "comment")
            continue
        if _is_directive_line(index, stripped, suffix):
            kinds.append("code")
            continue
        if any(stripped.startswith(prefix) for prefix in prefixes):
            kinds.append("comment")
            continue
        matched = False
        for opener, closer in blocks:
            if not stripped.startswith(opener):
                continue
            matched = True
            pos = stripped.find(closer, len(opener))
            if pos == -1:
                pending_close = closer
                kinds.append("comment")
            else:
                rest = stripped[pos + len(closer):].strip()
                kinds.append("code" if rest else "comment")
            break
        if matched:
            continue
        kinds.append("code")
    return kinds


@dataclass
class StripResult:
    """strip_comments() 的结果。行号均以 split_source_lines() 的 1 起行号计。"""

    lines: list[str]
    mode: str
    blank_line_count: int
    stripped_comment_line_count: int
    trimmed_line_count: int
    changed_line_numbers: list[int]
    note: str = ""


@dataclass(frozen=True)
class _CLikeConfig:
    line_comment: bool = True
    nested_block: bool = False
    single_quote: str = "string"  # string | char（Rust：区分字符字面量与生命周期标注）| none
    triple_quotes: tuple[str, ...] = ()
    triple_escape: bool = True
    backtick: str = "none"  # template（JS 模板字符串）| raw（Go 原始字符串）| ident（反引号标识符）| none
    regex: bool = False
    jsx: bool = False
    raw_prefix: str = ""  # rust | cpp | cs | dart
    css_url: bool = False


_JS = _CLikeConfig(backtick="template", regex=True)
_JSX = _CLikeConfig(backtick="template", regex=True, jsx=True)
_CSS = _CLikeConfig(line_comment=False, css_url=True)
_SCSS = _CLikeConfig(css_url=True)
_C_LIKE_CONFIGS: dict[str, _CLikeConfig] = {
    ".js": _JSX, ".jsx": _JSX, ".tsx": _JSX, ".mjs": _JSX, ".cjs": _JS, ".ts": _JS,
    ".java": _CLikeConfig(triple_quotes=('"""',)),
    ".c": _CLikeConfig(), ".h": _CLikeConfig(raw_prefix="cpp"),
    ".cc": _CLikeConfig(raw_prefix="cpp"), ".cpp": _CLikeConfig(raw_prefix="cpp"),
    ".hpp": _CLikeConfig(raw_prefix="cpp"),
    ".cs": _CLikeConfig(triple_quotes=('"""',), raw_prefix="cs"),
    ".go": _CLikeConfig(backtick="raw"),
    ".rs": _CLikeConfig(nested_block=True, single_quote="char", raw_prefix="rust"),
    ".swift": _CLikeConfig(nested_block=True, single_quote="none", triple_quotes=('"""',), backtick="ident"),
    ".kt": _CLikeConfig(nested_block=True, triple_quotes=('"""',), triple_escape=False, backtick="ident"),
    ".kts": _CLikeConfig(nested_block=True, triple_quotes=('"""',), triple_escape=False, backtick="ident"),
    ".dart": _CLikeConfig(nested_block=True, triple_quotes=('"""', "'''"), raw_prefix="dart"),
    ".css": _CSS, ".scss": _SCSS, ".sass": _SCSS, ".less": _SCSS,
}

# 正则字面量判定：`/` 前一个有效记号为下列标点或关键字时视为正则开始，否则视为除号。
_REGEX_PREV_PUNCT = set("(,=:[!&|?{;+-*%~^<>")
_REGEX_PREV_WORDS = {
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
    "throw", "case", "do", "else", "yield", "await",
}
_RUST_CHAR_RE = re.compile(r"'(?:\\(?:x[0-9a-fA-F]{2}|u\{[0-9a-fA-F]{1,6}\}|.)|[^\\'\n])'")
_RUST_RAW_RE = re.compile(r'b?r(#*)"')
_CPP_RAW_RE = re.compile(r'(?:u8|u|U|L)?R"([^()\\\s]{0,16})\(')
_CS_VERBATIM_RE = re.compile(r'(?:\$@|@\$|@)"')


def _is_ident(ch: str) -> bool:
    return ch.isalnum() or ch in "_$"


def _scan_c_like(text: str, cfg: _CLikeConfig, start: int = 0, end: int | None = None) -> list[tuple[int, int, str]]:
    """Return comment ranges (start, end, replacement) found in text[start:end]."""
    n = len(text) if end is None else end
    ranges: list[tuple[int, int, str]] = []
    # last：最近一个有效记号，("w", 词) 或 ("p", 标点)；用于正则字面量与 JSX 注释判定。
    state = {"last": ("", "")}

    def skip_quoted(i: int, quote: str, escape: bool = True, multiline: bool = False) -> int:
        k = i + 1
        while k < n:
            ch = text[k]
            if escape and ch == "\\":
                k += 2
                continue
            if ch == quote:
                return k + 1
            if ch == "\n" and not multiline:
                return k
            k += 1
        return n

    def skip_until(i: int, closer: str, escape: bool) -> int:
        k = i
        while k < n:
            if escape and text[k] == "\\":
                k += 2
                continue
            if text.startswith(closer, k):
                return k + len(closer)
            k += 1
        return n

    def block_end(i: int) -> int:
        depth = 0
        k = i
        while k < n:
            if text.startswith("/*", k) and (cfg.nested_block or depth == 0):
                depth += 1
                k += 2
                continue
            if text.startswith("*/", k):
                depth -= 1
                k += 2
                if depth == 0:
                    return k
                continue
            k += 1
        return n

    def prev_nonspace(i: int) -> int:
        k = i - 1
        while k >= start and text[k] in " \t\r\n":
            k -= 1
        return k

    def regex_allowed() -> bool:
        kind, value = state["last"]
        if kind == "":
            return True
        if kind == "w":
            return value in _REGEX_PREV_WORDS
        if value == "<" and cfg.jsx:
            return False
        return value in _REGEX_PREV_PUNCT

    def skip_regex(i: int) -> int | None:
        k = i + 1
        in_class = False
        while k < n:
            ch = text[k]
            if ch == "\\":
                k += 2
                continue
            if ch == "\n":
                return None
            if ch == "[":
                in_class = True
            elif ch == "]":
                in_class = False
            elif ch == "/" and not in_class:
                k += 1
                while k < n and text[k].isalpha():
                    k += 1
                return k
            k += 1
        return None

    def skip_template(i: int) -> int:
        k = i + 1
        while k < n:
            ch = text[k]
            if ch == "\\":
                k += 2
                continue
            if ch == "`":
                return k + 1
            if ch == "$" and k + 1 < n and text[k + 1] == "{":
                k = scan(k + 2, stop_on_brace=True)
                continue
            k += 1
        return n

    def scan(i: int, stop_on_brace: bool) -> int:
        depth = 0
        while i < n:
            c = text[i]
            nxt = text[i + 1] if i + 1 < n else ""
            if c in " \t\r\n":
                i += 1
                continue
            if c == "/" and nxt == "/" and cfg.line_comment and not (i > start and text[i - 1] == ":"):
                j = text.find("\n", i, n)
                j = n if j == -1 else j
                ranges.append((i, j, ""))
                i = j
                continue
            if c == "/" and nxt == "*":
                j = block_end(i)
                s, e = i, j
                if cfg.jsx:
                    b = prev_nonspace(i)
                    k = j
                    while k < n and text[k] in " \t\r\n":
                        k += 1
                    if b >= start and text[b] == "{" and k < n and text[k] == "}":
                        # JSX 注释 `{/* */}` 连同花括号一并删除。判定为 JSX 的条件：`{` 前一个有效字符为
                        # `>`（不含箭头 `=>`）或 `}`，且 `{` 紧贴该字符或位于行首。`Promise<void> {/* */}`
                        # 一类函数体因 `>` 与 `{` 之间隔有空格而不受影响。
                        before = prev_nonspace(b)
                        is_arrow = before >= start + 1 and text[before] == ">" and text[before - 1] == "="
                        gap = text[before + 1:b] if before >= start else ""
                        adjacent = gap == "" or "\n" in gap
                        if before >= start and text[before] in ">}" and not is_arrow and adjacent:
                            s, e = b, k + 1
                            depth -= 1
                ranges.append((s, e, ""))
                i = e
                continue
            if cfg.css_url and text[i:i + 4].lower() == "url(":
                j = text.find(")", i, n)
                i = n if j == -1 else j + 1
                state["last"] = ("w", "url")
                continue
            if cfg.raw_prefix == "rust" and c in "rb" and (i == start or not _is_ident(text[i - 1])):
                m = _RUST_RAW_RE.match(text, i, n)
                if m:
                    i = skip_until(m.end(), '"' + m.group(1), escape=False)
                    state["last"] = ("w", '"')
                    continue
            if cfg.raw_prefix == "cpp" and c in "uULR" and (i == start or not _is_ident(text[i - 1])):
                m = _CPP_RAW_RE.match(text, i, n)
                if m:
                    i = skip_until(m.end(), ")" + m.group(1) + '"', escape=False)
                    state["last"] = ("w", '"')
                    continue
            if cfg.raw_prefix == "cs" and c in "@$":
                m = _CS_VERBATIM_RE.match(text, i, n)
                if m and not text.startswith('"""', m.end() - 1):
                    k = m.end()
                    while k < n:
                        if text[k] == '"':
                            if k + 1 < n and text[k + 1] == '"':
                                k += 2
                                continue
                            k += 1
                            break
                        k += 1
                    i = k
                    state["last"] = ("w", '"')
                    continue
            if cfg.raw_prefix == "dart" and c == "r" and nxt in "'\"" and (i == start or not _is_ident(text[i - 1])):
                for quote in ("'''", '"""', nxt):
                    if text.startswith(quote, i + 1):
                        i = skip_until(i + 1 + len(quote), quote, escape=False)
                        break
                state["last"] = ("w", '"')
                continue
            if c in "\"'":
                triple = next((t for t in cfg.triple_quotes if text.startswith(t, i)), None)
                if triple:
                    i = skip_until(i + 3, triple, escape=cfg.triple_escape)
                    state["last"] = ("w", '"')
                    continue
                if c == "'" and cfg.single_quote == "char":
                    m = _RUST_CHAR_RE.match(text, i, n)
                    if m:
                        i = m.end()
                        state["last"] = ("w", '"')
                        continue
                    state["last"] = ("p", "'")
                    i += 1
                    continue
                if c == "'" and cfg.single_quote == "none":
                    state["last"] = ("p", "'")
                    i += 1
                    continue
                i = skip_quoted(i, c)
                state["last"] = ("w", '"')
                continue
            if c == "`" and cfg.backtick != "none":
                if cfg.backtick == "template":
                    i = skip_template(i)
                elif cfg.backtick == "raw":
                    i = skip_quoted(i, "`", escape=False, multiline=True)
                else:
                    i = skip_quoted(i, "`", escape=False)
                state["last"] = ("w", '"')
                continue
            if c == "/" and cfg.regex and nxt not in "/*>" and regex_allowed():
                j = skip_regex(i)
                if j is not None:
                    i = j
                    state["last"] = ("w", "/regex/")
                    continue
            if _is_ident(c):
                k = i + 1
                while k < n and _is_ident(text[k]):
                    k += 1
                state["last"] = ("w", text[i:k])
                i = k
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                if stop_on_brace and depth == 0:
                    state["last"] = ("w", "}")
                    return i + 1
                depth -= 1
            state["last"] = ("p", c)
            i += 1
        return n

    scan(start, stop_on_brace=False)
    return ranges


_MARKUP_TOKEN_RE = re.compile(r"<!--|<script\b[^>]*>|<style\b[^>]*>", re.IGNORECASE)
_LANG_ATTR_RE = re.compile(r"""\blang\s*=\s*["']?([\w-]+)""", re.IGNORECASE)


def _scan_markup(text: str, suffix: str) -> list[tuple[int, int, str]]:
    ranges: list[tuple[int, int, str]] = []
    pos = 0
    n = len(text)
    if suffix == ".astro" and text.startswith("---"):
        close = text.find("\n---", 3)
        if close != -1:
            ranges.extend(_scan_c_like(text, _C_LIKE_CONFIGS[".ts"], 3, close))
            pos = close + 4
    if suffix in MARKUP_ONLY_EXTS:
        while True:
            open_at = text.find("<!--", pos)
            if open_at == -1:
                return ranges
            close = text.find("-->", open_at + 4)
            end = n if close == -1 else close + 3
            ranges.append((open_at, end, ""))
            pos = end
    while True:
        m = _MARKUP_TOKEN_RE.search(text, pos)
        if not m:
            return ranges
        token = m.group(0)
        if token == "<!--":
            close = text.find("-->", m.end())
            end = n if close == -1 else close + 3
            ranges.append((m.start(), end, ""))
            pos = end
            continue
        is_script = token.lower().startswith("<script")
        closer = "</script" if is_script else "</style"
        close = text.lower().find(closer, m.end())
        body_end = n if close == -1 else close
        lang_match = _LANG_ATTR_RE.search(token)
        lang = lang_match.group(1).lower() if lang_match else ""
        if is_script:
            cfg = _C_LIKE_CONFIGS[".tsx"] if lang in {"tsx", "jsx"} else _C_LIKE_CONFIGS[".ts"]
        else:
            cfg = _SCSS if lang in {"scss", "sass", "less"} else _CSS
        ranges.extend(_scan_c_like(text, cfg, m.end(), body_end))
        pos = body_end


def _python_ranges(text: str) -> list[tuple[int, int, str]]:
    """Comments via tokenize, docstrings via ast. Raises on unparsable source."""
    lines = text.split("\n")
    line_starts: list[int] = []
    offset = 0
    for line in lines:
        line_starts.append(offset)
        offset += len(line) + 1

    def char_offset(row: int, col: int) -> int:
        return line_starts[row - 1] + col

    def byte_col_to_char(row: int, byte_col: int) -> int:
        return len(lines[row - 1].encode("utf-8")[:byte_col].decode("utf-8", errors="ignore"))

    ranges: list[tuple[int, int, str]] = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type != tokenize.COMMENT:
            continue
        row, col = tok.start
        before = lines[row - 1][:col]
        if row <= 2 and not before.strip() and _is_directive_line(row - 1, tok.string, ".py"):
            continue
        ranges.append((char_offset(row, col), char_offset(*tok.end), ""))

    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if not (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str)):
            continue
        s = char_offset(first.lineno, byte_col_to_char(first.lineno, first.col_offset))
        e = char_offset(first.end_lineno, byte_col_to_char(first.end_lineno, first.end_col_offset))
        tail = re.match(r"[ \t]*;[ \t]*", text[e:])
        if tail:
            e += tail.end()
        # 函数体或类体仅有文档字符串时，剔除后须以 pass 占位，否则剔除结果不再是合法代码。
        replacement = "pass" if len(body) == 1 and not isinstance(node, ast.Module) else ""
        ranges.append((s, e, replacement))
    return ranges


def _apply_ranges(text: str, ranges: list[tuple[int, int, str]]) -> tuple[str, set[int]]:
    """Remove ranges while keeping their newlines, so output line i maps to input line i."""
    newline_positions = [i for i, ch in enumerate(text) if ch == "\n"]

    def line_index(pos: int) -> int:
        return bisect.bisect_left(newline_positions, pos)

    out: list[str] = []
    touched: set[int] = set()
    cursor = 0
    for s, e, repl in sorted(ranges):
        if s < cursor:
            continue
        segment = text[s:e]
        if not repl and "\n" not in segment:
            before = text[s - 1] if s > 0 else ""
            after = text[e] if e < len(text) else ""
            if before and after and _is_ident(before) and _is_ident(after):
                repl = " "
            elif before in (" ", "\t") and after in (" ", "\t"):
                while e < len(text) and text[e] in " \t":
                    e += 1
                segment = text[s:e]
            elif before in (" ", "\t") and after and after in ",;)]}":
                # `a /* x */, b` 去掉注释后不留下 `a , b`；仅回退到本行已有代码之后，不动缩进。
                k = s
                while k > cursor and text[k - 1] in " \t":
                    k -= 1
                if k > cursor and text[k - 1] != "\n":
                    s = k
                    segment = text[s:e]
        out.append(text[cursor:s])
        out.append(repl + "\n" * segment.count("\n"))
        for idx in range(line_index(s), line_index(max(s, e - 1)) + 1):
            touched.add(idx)
        cursor = e
    out.append(text[cursor:])
    return "".join(out), touched


def _strip_once(text: str, suffix: str) -> StripResult:
    source_lines = split_source_lines(text)
    normalized = "\n".join(source_lines)
    mode = STRIP_MODE_FULL
    note = ""
    ranges: list[tuple[int, int, str]] | None = None
    if suffix == ".py":
        try:
            ranges = _python_ranges(normalized)
        except (SyntaxError, tokenize.TokenError, ValueError) as exc:
            mode, note = STRIP_MODE_WHOLE_LINE, f"Python 解析失败（{type(exc).__name__}），退回整行注释判定"
    elif suffix in MARKUP_SCRIPT_EXTS or suffix in MARKUP_ONLY_EXTS:
        ranges = _scan_markup(normalized, suffix)
    elif suffix in _C_LIKE_CONFIGS:
        ranges = _scan_c_like(normalized, _C_LIKE_CONFIGS[suffix])
    elif suffix in NO_COMMENT_EXTS:
        ranges, mode = [], STRIP_MODE_NONE
    else:
        prefixes, blocks = comment_syntax(suffix)
        if prefixes or blocks:
            mode, note = STRIP_MODE_WHOLE_LINE, "该语言未实现逐字符解析，仅剔除整行注释"
        else:
            ranges, mode, note = [], STRIP_MODE_NONE, "未识别该后缀的注释语法，未作剔除"

    kept: list[str] = []
    changed: list[int] = []
    blank = stripped = trimmed = 0
    if ranges is None:
        for number, (line, kind) in enumerate(zip(source_lines, classify_source_lines(normalized, suffix)), start=1):
            if kind == "blank":
                blank += 1
            elif kind == "comment":
                stripped += 1
                changed.append(number)
            else:
                kept.append(line)
        return StripResult(kept, mode, blank, stripped, trimmed, changed, note)

    output, touched = _apply_ranges(normalized, ranges)
    for index, (original, line) in enumerate(zip(source_lines, output.split("\n"))):
        if not original.strip():
            blank += 1
            continue
        if index in touched:
            changed.append(index + 1)
            line = line.rstrip()
            if not line.strip():
                stripped += 1
                continue
            trimmed += 1
        kept.append(line)
    return StripResult(kept, mode, blank, stripped, trimmed, changed, note)


def strip_comments(text: str, suffix: str) -> StripResult:
    """Remove every comment from `text`; blank lines are dropped as well.

    剔除结果须满足幂等：对结果再执行一次不应再有任何改动，verify_materials.py 即以此
    判定残留。Python 文档字符串剔除后，其后的字符串表达式可能成为新的文档字符串，
    故循环执行至结果稳定（至多 5 轮），并累计各轮剔除的行数。
    """
    suffix = suffix.lower()
    result = _strip_once(text, suffix)
    for _ in range(4):
        if result.mode == STRIP_MODE_NONE:
            break
        again = _strip_once("\n".join(result.lines), suffix)
        if not again.changed_line_numbers:
            break
        result = StripResult(
            again.lines,
            result.mode,
            result.blank_line_count,
            result.stripped_comment_line_count + again.stripped_comment_line_count,
            result.trimmed_line_count,
            result.changed_line_numbers,
            result.note,
        )
    return result
