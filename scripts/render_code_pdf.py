#!/usr/bin/env python3
"""Render the code identification material straight from Markdown to PDF.

代码鉴别材料的分页完全由脚本决定，与 Word 排版无关：`build_docx_from_md.py` 的
`build_code_docx_python()` 每页固定 50 行，页与页之间插入显式分页符，页码起始值取中间
Markdown 首页的页号。既然页数在生成时即已确定，本模块便直接由同一份中间 Markdown 渲染
PDF，绕开 Microsoft Word 与 LibreOffice，避免在缺少这两者的机器上无法产出上传所需的 PDF。

排版参数逐项对齐 `build_docx_from_md.py`，不得单独调整：

- 页面：A4，宽 21cm、高 29.7cm；上下边距 2.0cm，左边距 2.5cm，右边距 2.0cm（configure_code_a4）
- 正文：等宽字体，字号 7.2pt，行距为「精确 14pt」，段前段后为 0
- 页眉：左侧「<软件全称> <版本号>」，右侧「第 <页码> 页」，宋体 8pt，行距 12pt（set_code_header）
- 页码：自中间 Markdown 首页的页号起逐页递增，与 DOCX 的 pgNumType/PAGE 域一致

字体处理：不使用 PDF 内置的非嵌入 CID 字体，一律把系统字体的子集嵌入 PDF。macOS 的中文
字体为 .ttc 字体集合，且首个 face 未必是所需字重（Songti.ttc 的 face 0 为 Black），故按
PostScript 名取出指定 face，另存为临时字体文件后再注册。正文按字符切换字体：字符在等宽
字体中有字形即用等宽字体，否则用中文字体；两者皆无字形时明确报错，不产出含缺字的 PDF。

本模块只依赖 fpdf2 与其既有依赖 fontTools，且 fpdf2 为可选依赖：导入失败时
`FPDF_AVAILABLE` 为 False，调用方据此降级至 Word / LibreOffice 链路。

Usage:
    python3 render_code_pdf.py --workdir 软件著作权申请资料
    python3 render_code_pdf.py --markdown 草稿/代码-前后各30页.md --out 代码.pdf --header "某软件 V1.0"
"""

from __future__ import annotations

import argparse
import html
import logging
import re
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable, NamedTuple

from build_docx_from_md import (
    application_software_name,
    application_version,
    parse_code_pages,
    resolve_code_specs,
)
from common import CODE_LAYOUT, safe_filename

try:
    from fpdf import FPDF

    FPDF_AVAILABLE = True
except Exception:  # pragma: no cover - 取决于运行环境是否安装了 fpdf2
    FPDF_AVAILABLE = False


class CodePdfError(RuntimeError):
    """Raised when the direct PDF backend cannot produce a trustworthy file."""


# ---------------------------------------------------------------------------
# 排版常量：一律由 common.CODE_LAYOUT 换算而来，该处是 DOCX 与 PDF 两个后端的唯一数值
# 来源，本模块只负责厘米转磅，不得在此另行写死取值。

CM = 72.0 / 2.54

PAGE_WIDTH_PT = CODE_LAYOUT.page_width_cm * CM
PAGE_HEIGHT_PT = CODE_LAYOUT.page_height_cm * CM
MARGIN_TOP_PT = CODE_LAYOUT.margin_top_cm * CM
MARGIN_BOTTOM_PT = CODE_LAYOUT.margin_bottom_cm * CM
MARGIN_LEFT_PT = CODE_LAYOUT.margin_left_cm * CM
MARGIN_RIGHT_PT = CODE_LAYOUT.margin_right_cm * CM

BODY_FONT_SIZE_PT = CODE_LAYOUT.body_font_size_pt
BODY_LINE_PT = CODE_LAYOUT.body_line_pt

HEADER_FONT_SIZE_PT = CODE_LAYOUT.header_font_size_pt
HEADER_LINE_PT = CODE_LAYOUT.header_line_pt
# python-docx 默认模板的 sectPr 记 w:header="720" twips，即 0.5 英寸；
# build_docx_from_md.py 未改动该值，故页眉距页面上边缘 1.27cm。
HEADER_DISTANCE_PT = CODE_LAYOUT.header_distance_cm * CM

# 精确行距下，基线距行框顶端为「行高 − 下伸部」。下伸部按字号的 0.25 倍估算，
# 该值只影响每行文字在 14pt 行框内的纵向位置，不影响每页行数与总页数。
DESCENT_RATIO = 0.25

# 代码鉴别材料 docx 的命名式样。与 convert_to_pdf.CODE_DOCX_PATTERN 一致，
# 此处另行定义以避免两个模块互相导入。
CODE_DOCX_PATTERN = "*-代码(*).docx"


# ---------------------------------------------------------------------------
# 字体查找


class FontCandidate(NamedTuple):
    """One searchable font file, plus the PostScript names acceptable inside a collection."""

    path: str
    faces: tuple[str, ...] = ()


# 等宽正文字体：Consolas 在 macOS 上通常不存在，故给出跨平台的回退链。
MONO_CANDIDATES: tuple[FontCandidate, ...] = (
    FontCandidate("C:/Windows/Fonts/consola.ttf"),
    FontCandidate("/Library/Fonts/Consolas.ttf"),
    FontCandidate("/System/Library/Fonts/Menlo.ttc", ("Menlo-Regular",)),
    FontCandidate("/System/Library/Fonts/Monaco.ttf"),
    FontCandidate("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    FontCandidate("/usr/share/fonts/dejavu/DejaVuSansMono.ttf"),
    FontCandidate("/usr/share/fonts/TTF/DejaVuSansMono.ttf"),
    FontCandidate("/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"),
    FontCandidate("/usr/share/fonts/liberation/LiberationMono-Regular.ttf"),
    FontCandidate("/usr/share/fonts/truetype/freefont/FreeMono.ttf"),
    FontCandidate("/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf"),
)

# 中文字体：优先宋体一系，与 DOCX 页眉所用的 SimSun 取向一致。
CJK_CANDIDATES: tuple[FontCandidate, ...] = (
    FontCandidate("C:/Windows/Fonts/simsun.ttc", ("SimSun",)),
    FontCandidate("/Library/Fonts/SimSun.ttf"),
    FontCandidate(
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        ("STSongti-SC-Regular", "STSong", "STSongti-SC-Light"),
    ),
    FontCandidate("/System/Library/Fonts/STHeiti Light.ttc", ("STHeitiSC-Light", "STXihei")),
    FontCandidate("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    FontCandidate(
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        ("NotoSansCJKsc-Regular", "NotoSansCJKSC-Regular"),
    ),
    FontCandidate(
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        ("NotoSerifCJKsc-Regular", "NotoSerifCJKSC-Regular"),
    ),
    FontCandidate("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", ("NotoSansCJKsc-Regular",)),
    FontCandidate("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", ("NotoSansCJKsc-Regular",)),
    FontCandidate("/usr/share/fonts/truetype/arphic/uming.ttc", ("AR PL UMing CN",)),
    FontCandidate("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", ("WenQuanYiZenHei",)),
    FontCandidate("/usr/share/fonts/wenquanyi/wqy-zenhei/wqy-zenhei.ttc", ("WenQuanYiZenHei",)),
    FontCandidate("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", ("WenQuanYiMicroHei",)),
)

COLLECTION_SUFFIXES = {".ttc", ".otc"}


def _face_names(font: Any) -> set[str]:
    names: set[str] = set()
    try:
        table = font["name"]
    except Exception:
        return names
    for name_id in (6, 4, 1):
        try:
            value = table.getDebugName(name_id)
        except Exception:
            value = None
        if value:
            names.add(value)
    return names


def _extract_face(path: Path, faces: tuple[str, ...], workdir: Path) -> Path:
    """Save one face of a .ttc/.otc collection as a standalone font file.

    fpdf2 接受 .ttc 路径，但一律取 face 0，而 face 0 未必是所需字重
    （Songti.ttc 的 face 0 为 STSongti-SC-Black）。故按 PostScript 名定位后另存。
    """
    from fontTools.ttLib import TTCollection

    collection = TTCollection(str(path), lazy=True)
    chosen = None
    chosen_index = 0
    for wanted in faces:
        for index, font in enumerate(collection.fonts):
            if wanted in _face_names(font):
                chosen, chosen_index = font, index
                break
        if chosen is not None:
            break
    if chosen is None:
        if not collection.fonts:
            raise CodePdfError(f"字体集合 {path} 不含任何 face")
        chosen, chosen_index = collection.fonts[0], 0
    suffix = ".ttf" if "glyf" in chosen else ".otf"
    out_path = workdir / f"{safe_filename(path.stem)}-{chosen_index}{suffix}"
    chosen.save(str(out_path))
    return out_path


class ResolvedFont(NamedTuple):
    file: Path
    source: Path
    label: str


def resolve_font(candidates: Iterable[FontCandidate], workdir: Path) -> ResolvedFont | None:
    """Return the first usable candidate, extracting a collection face when needed."""
    for candidate in candidates:
        path = Path(candidate.path)
        if not path.is_file():
            continue
        try:
            if path.suffix.lower() in COLLECTION_SUFFIXES:
                file = _extract_face(path, candidate.faces, workdir)
            else:
                file = path
        except Exception:
            # 单个候选不可用不应中断查找，继续尝试下一项。
            continue
        return ResolvedFont(file=file, source=path, label=path.name)
    return None


def font_charset(path: Path) -> set[int]:
    """Return the set of Unicode code points the font file can render."""
    from fontTools.ttLib import TTFont

    try:
        font = TTFont(str(path), lazy=True, fontNumber=0)
        return set(font.getBestCmap().keys())
    except Exception as exc:
        raise CodePdfError(f"无法读取字体 {path} 的字符表：{exc}")


class FontPair(NamedTuple):
    mono: ResolvedFont
    cjk: ResolvedFont
    mono_chars: set[int]
    cjk_chars: set[int]


def prepare_fonts(workdir: Path) -> FontPair:
    """Locate one monospace font and one CJK font, or raise CodePdfError."""
    mono = resolve_font(MONO_CANDIDATES, workdir)
    if mono is None:
        raise CodePdfError(
            "未找到可用的等宽字体，已按以下路径查找："
            + "、".join(c.path for c in MONO_CANDIDATES)
        )
    cjk = resolve_font(CJK_CANDIDATES, workdir)
    if cjk is None:
        raise CodePdfError(
            "未找到可用的中文字体，已按以下路径查找："
            + "、".join(c.path for c in CJK_CANDIDATES)
        )
    return FontPair(mono, cjk, font_charset(mono.file), font_charset(cjk.file))


# ---------------------------------------------------------------------------
# 文本切分


def split_runs(text: str, primary: set[int], secondary: set[int]) -> list[tuple[bool, str]]:
    """Split one line into (use_primary_font, text) runs, switching per character.

    返回值中 True 表示该段用首选字体，False 表示用次选字体。两套字符表都不含的字符
    交由调用方统一报错，不在此处静默丢弃。
    """
    runs: list[tuple[bool, str]] = []
    for char in text:
        code = ord(char)
        use_primary = code in primary or code not in secondary
        if runs and runs[-1][0] == use_primary:
            runs[-1] = (use_primary, runs[-1][1] + char)
        else:
            runs.append((use_primary, char))
    return runs


# 制表符与其余控制字符在字体的字符表中没有字形，绘制前须先行展开。Word 按 1.27cm 的
# 默认制表位排布，本模块按 4 个空格展开，缩进的视觉宽度因而略有出入；该差异只作用于
# 行内横向位置，不影响每页行数与总页数。
TAB_WIDTH = 4


def sanitize_line(text: str) -> str:
    """Expand tabs and neutralise control characters before measuring or drawing."""
    return "".join(char if char >= " " else " " for char in text.expandtabs(TAB_WIDTH))


def font_runs(text: str, fonts: FontPair, prefer_cjk: bool = False) -> list[tuple[bool, str]]:
    """Split text into (use_mono_font, chunk) runs.

    正文以等宽字体为首选，中文字符落到中文字体；页眉则相反，因 DOCX 的页眉一律用
    SimSun，此处以中文字体为首选，仅在其缺字时才回退至等宽字体。
    """
    if prefer_cjk:
        return [
            (not use_cjk, chunk)
            for use_cjk, chunk in split_runs(text, fonts.cjk_chars, fonts.mono_chars)
        ]
    return split_runs(text, fonts.mono_chars, fonts.cjk_chars)


def unsupported_characters(text: str, fonts: FontPair) -> set[str]:
    return {
        char
        for char in text
        if ord(char) not in fonts.mono_chars and ord(char) not in fonts.cjk_chars
    }


# ---------------------------------------------------------------------------
# 渲染


def _draw_runs(
    pdf: Any,
    runs: list[tuple[bool, str]],
    x: float,
    baseline: float,
    size_pt: float,
    available: float,
) -> None:
    """Draw one line, condensing it horizontally only when it would pass the right margin."""
    widths: list[float] = []
    pdf.set_stretching(100)
    total = 0.0
    for use_primary, chunk in runs:
        pdf.set_font("mono" if use_primary else "cjk", "", size_pt)
        width = pdf.get_string_width(chunk)
        widths.append(width)
        total += width
    factor = 1.0 if total <= available or total <= 0 else available / total
    pdf.set_stretching(factor * 100)
    cursor = x
    for (use_primary, chunk), width in zip(runs, widths):
        pdf.set_font("mono" if use_primary else "cjk", "", size_pt)
        pdf.text(cursor, baseline, chunk)
        cursor += width * factor
    pdf.set_stretching(100)


def _line_width(pdf: Any, runs: list[tuple[bool, str]], size_pt: float) -> float:
    pdf.set_stretching(100)
    total = 0.0
    for use_primary, chunk in runs:
        pdf.set_font("mono" if use_primary else "cjk", "", size_pt)
        total += pdf.get_string_width(chunk)
    return total


def render_markdown_to_pdf(md_path: Path, pdf_path: Path, header_left: str) -> dict[str, Any]:
    """Render one code-material Markdown file to PDF; return a summary dict.

    返回 {"pages": 页数, "start_page": 首页页号, "lines": 各页行数, "condensed": 横向压缩的行数,
    "mono_font": 等宽字体文件名, "cjk_font": 中文字体文件名}。
    """
    if not FPDF_AVAILABLE:
        raise CodePdfError("fpdf2 不可用，无法直出 PDF（pip install fpdf2）")
    pages = parse_code_pages(md_path)
    if not pages:
        raise CodePdfError(f"未能从 {md_path} 解析出任何代码页")

    # fontTools 的子集化会把无法处理的表逐一打印到 stderr，与本脚本的进度输出混在一起，
    # 只保留其错误级别的日志。
    logging.getLogger("fontTools.subset").setLevel(logging.ERROR)
    logging.getLogger("fontTools.ttLib").setLevel(logging.ERROR)

    available = PAGE_WIDTH_PT - MARGIN_LEFT_PT - MARGIN_RIGHT_PT
    body_capacity = PAGE_HEIGHT_PT - MARGIN_TOP_PT - MARGIN_BOTTOM_PT
    max_lines = max(len(lines) for _, lines in pages)
    if max_lines * BODY_LINE_PT > body_capacity:
        raise CodePdfError(
            f"{md_path} 中单页最多 {max_lines} 行，按 {BODY_LINE_PT:g}pt 行距超出正文区高度 "
            f"{body_capacity:.1f}pt，直出会改变分页，须先核对代码抽取环节的每页行数"
        )

    with tempfile.TemporaryDirectory(prefix="code-pdf-fonts-") as tmp:
        tmp_dir = Path(tmp)
        fonts = prepare_fonts(tmp_dir)

        missing: set[str] = set()
        for _, lines in pages:
            for line in lines:
                missing |= unsupported_characters(sanitize_line(line), fonts)
        missing |= unsupported_characters(sanitize_line(header_left), fonts)
        if missing:
            sample = "".join(sorted(missing)[:20])
            raise CodePdfError(
                f"以下字符在 {fonts.mono.label} 与 {fonts.cjk.label} 中均无字形，"
                f"直出会产生缺字或方框：{sample}（共 {len(missing)} 个）"
            )

        pdf = FPDF(orientation="P", unit="pt", format=(PAGE_WIDTH_PT, PAGE_HEIGHT_PT))
        pdf.set_auto_page_break(False)
        pdf.set_margins(MARGIN_LEFT_PT, MARGIN_TOP_PT, MARGIN_RIGHT_PT)
        pdf.add_font("mono", "", str(fonts.mono.file))
        pdf.add_font("cjk", "", str(fonts.cjk.file))
        pdf.set_text_color(0, 0, 0)

        start_page = pages[0][0]
        header_baseline = HEADER_DISTANCE_PT + HEADER_LINE_PT - HEADER_FONT_SIZE_PT * DESCENT_RATIO
        condensed = 0
        line_counts: list[int] = []

        for index, (_, lines) in enumerate(pages):
            pdf.add_page()
            # 页眉：左「软件全称 版本号」，右「第 N 页」
            left_runs = font_runs(sanitize_line(header_left), fonts, prefer_cjk=True)
            _draw_runs(
                pdf,
                left_runs,
                MARGIN_LEFT_PT,
                header_baseline,
                HEADER_FONT_SIZE_PT,
                available,
            )
            page_label = f"第 {start_page + index} 页"
            right_runs = font_runs(page_label, fonts, prefer_cjk=True)
            right_width = _line_width(pdf, right_runs, HEADER_FONT_SIZE_PT)
            _draw_runs(
                pdf,
                right_runs,
                PAGE_WIDTH_PT - MARGIN_RIGHT_PT - right_width,
                header_baseline,
                HEADER_FONT_SIZE_PT,
                available,
            )

            for row, line in enumerate(lines):
                # 空行同样落一个空格，与 build_code_docx_python() 的处理一致，
                # 使 PDF 中每页的文本行数与中间 Markdown 的行数逐行对应。
                text = sanitize_line(line) if line else " "
                runs = font_runs(text, fonts)
                baseline = (
                    MARGIN_TOP_PT
                    + row * BODY_LINE_PT
                    + BODY_LINE_PT
                    - BODY_FONT_SIZE_PT * DESCENT_RATIO
                )
                if _line_width(pdf, runs, BODY_FONT_SIZE_PT) > available:
                    condensed += 1
                _draw_runs(pdf, runs, MARGIN_LEFT_PT, baseline, BODY_FONT_SIZE_PT, available)
            line_counts.append(len(lines))

        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf.output(str(pdf_path))

        return {
            "pages": len(pages),
            "start_page": start_page,
            "lines": line_counts,
            "condensed": condensed,
            "mono_font": fonts.mono.label,
            "cjk_font": fonts.cjk.label,
        }


# ---------------------------------------------------------------------------
# 数据源定位


def find_code_docx(workdir: Path) -> Path | None:
    formal_dir = workdir / "正式资料"
    if not formal_dir.is_dir():
        return None
    matches = sorted(p for p in formal_dir.glob(CODE_DOCX_PATTERN) if not p.name.startswith("~$"))
    return matches[0] if len(matches) == 1 else None


def safe_name_from_docx(docx_path: Path) -> str | None:
    """Recover the `<软件名>` part of `<软件名>-代码(...).docx`."""
    marker = "-代码("
    stem = docx_path.name
    if marker not in stem:
        return None
    return stem.split(marker, 1)[0] or None


def resolve_code_markdown(workdir: Path, docx_path: Path | None = None) -> tuple[Path, list[str]]:
    """Locate the intermediate Markdown backing the code material; return (path, notes).

    一律沿用 build_docx_from_md.resolve_code_specs()：以 草稿/代码提取清单.json 的
    outputs 字段为准，抽取环节的降级命名（如 代码-前21页+后21页.md）同样可用。
    先按 docx 文件名精确匹配，匹配不上时才退回「草稿中唯一存在的那份」。
    """
    draft_dir = workdir / "草稿"
    if not draft_dir.is_dir():
        raise CodePdfError(f"草稿目录不存在：{draft_dir}")

    safe_name = None
    if docx_path is not None:
        safe_name = safe_name_from_docx(docx_path)
    if safe_name is None:
        app_name = application_software_name(draft_dir)
        safe_name = safe_filename(app_name) if app_name else "软件"

    specs, notes = resolve_code_specs(draft_dir, safe_name)
    existing = [(md_name, docx_name) for md_name, docx_name in specs if (draft_dir / md_name).is_file()]
    if not existing:
        raise CodePdfError(
            "草稿目录下没有可用的代码材料 Markdown，已按清单查找："
            + "、".join(md_name for md_name, _ in specs)
        )
    if docx_path is not None:
        for md_name, docx_name in existing:
            if docx_name == docx_path.name:
                return draft_dir / md_name, notes
    if len(existing) == 1:
        md_name = existing[0][0]
        if docx_path is not None and existing[0][1] != docx_path.name:
            notes.append(
                f"代码材料 Word 名为 {docx_path.name}，与清单推导的 {existing[0][1]} 不一致，"
                f"已按草稿中唯一存在的 {md_name} 直出 PDF"
            )
        return draft_dir / md_name, notes
    raise CodePdfError(
        f"草稿中存在多份代码材料 Markdown（{'、'.join(md for md, _ in existing)}），"
        f"无法确定与 {docx_path.name if docx_path else '正式资料'} 对应的一份"
    )


def docx_header_left(docx_path: Path) -> str | None:
    """Read the left-hand header text out of an existing DOCX, or return None.

    页眉左侧文字是 DOCX 的既成事实，直接取用可保证 PDF 与 DOCX 逐字一致，
    无须再猜测生成时用的是申请表信息还是命令行参数。
    """
    try:
        with zipfile.ZipFile(docx_path) as archive:
            names = sorted(n for n in archive.namelist() if re.fullmatch(r"word/header\d*\.xml", n))
            for name in names:
                xml = archive.read(name).decode("utf-8", "ignore")
                cell = re.search(r"<w:tc[ >].*?</w:tc>", xml, re.S)
                if not cell:
                    continue
                # `<w:t[^>]*>` 会连 `<w:tcPr>`、`<w:tbl>` 一并匹配，故限定为 `<w:t>` 或 `<w:t ...>`
                texts = re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", cell.group(0), re.S)
                value = html.unescape("".join(texts)).strip()
                if value:
                    return value
    except Exception:
        return None
    return None


def resolve_header_text(workdir: Path, docx_path: Path | None = None) -> str:
    """Return the header's left-hand text: `<软件全称> <版本号>`."""
    if docx_path is not None and docx_path.is_file():
        from_docx = docx_header_left(docx_path)
        if from_docx:
            return from_docx
    draft_dir = workdir / "草稿"
    name = application_software_name(draft_dir)
    version = application_version(draft_dir)
    if not name:
        raise CodePdfError(
            "无法确定页眉左侧文字：既未能从代码材料 Word 的页眉读出，"
            f"{draft_dir / '申请表信息.md'} 的「软件全称」字段也不可用"
        )
    return f"{name} {version}".strip()


def build_code_pdf(workdir: Path, docx_path: Path | None, pdf_path: Path) -> dict[str, Any]:
    """One-call entry point: locate the Markdown and header, then render the PDF."""
    md_path, notes = resolve_code_markdown(workdir, docx_path)
    header_left = resolve_header_text(workdir, docx_path)
    result = render_markdown_to_pdf(md_path, pdf_path, header_left)
    result["markdown"] = str(md_path)
    result["header"] = header_left
    result["notes"] = notes
    return result


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="由代码材料的中间 Markdown 直接渲染 PDF，不经 Word")
    parser.add_argument("--workdir", default="软件著作权申请资料", help="skill 输出目录")
    parser.add_argument("--markdown", help="直接指定中间 Markdown，跳过清单定位")
    parser.add_argument("--out", help="输出 PDF 路径，默认与代码材料 Word 同名")
    parser.add_argument("--header", help="页眉左侧文字，默认取自 Word 页眉或申请表信息")
    args = parser.parse_args()

    if not FPDF_AVAILABLE:
        print("错误：fpdf2 不可用，无法直出 PDF。请执行 pip install fpdf2 后重试。", file=sys.stderr)
        return 1

    workdir = Path(args.workdir).resolve()
    try:
        if args.markdown:
            md_path = Path(args.markdown).resolve()
            header = args.header or resolve_header_text(workdir, find_code_docx(workdir))
            out_path = Path(args.out).resolve() if args.out else md_path.with_suffix(".pdf")
            result = render_markdown_to_pdf(md_path, out_path, header)
            result["markdown"] = str(md_path)
            result["header"] = header
            result["notes"] = []
        else:
            docx_path = find_code_docx(workdir)
            if args.out:
                out_path = Path(args.out).resolve()
            elif docx_path is not None:
                out_path = docx_path.with_suffix(".pdf")
            else:
                raise CodePdfError("未在 正式资料/ 找到唯一的代码鉴别材料 Word，须以 --out 指定输出路径")
            result = build_code_pdf(workdir, docx_path, out_path)
    except CodePdfError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    for note in result.get("notes", []):
        print(f"提示：{note}")
    print(f"OK code pdf: {out_path}")
    print(
        f"共 {result['pages']} 页，首页页码 {result['start_page']}，"
        f"每页行数取值 {sorted(set(result['lines']))}，"
        f"字体 {result['mono_font']} + {result['cjk_font']}"
    )
    if result["condensed"]:
        print(f"提示：{result['condensed']} 行超出正文宽度，已按行横向压缩以免越出右边距")
    return 0


if __name__ == "__main__":
    sys.exit(main())
