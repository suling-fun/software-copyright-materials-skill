#!/usr/bin/env python3
"""Convert the two formal .docx deliverables into PDF for platform upload.

中国版权保护中心的在线提交只接受 PDF，而 `build_docx_from_md.py` 产出的是 Word。
本脚本把 `正式资料/` 下的代码鉴别材料与操作手册各转成同名 PDF，输出到同一目录。

转换路径按优先级降级：

1. macOS 且 Microsoft Word 可用时，经 osascript 驱动 Word。动作顺序固定为
   打开（不使用只读模式）→ 重新分页 → 更新全部域 → 再次重新分页 → 另存为 PDF →
   关闭且不保存。**更新域与随后的重新分页不可省略**：操作手册的目录是 Word 的 TOC 域，
   若在未更新域的情况下直接导出，目录页码会全部印成 1（已实测）。只读方式打开则不会
   重新分页，故 `open` 一律不加 read only。
2. Word 不可用时降级至 LibreOffice（`soffice --headless --convert-to pdf`）。
   LibreOffice 未必更新目录域，此时终端会明确提示须人工核对操作手册首页目录的页码。
3. 两者皆不可用时报错退出。

转换后逐份校验：文件存在且非空、未加密（不含 `/Encrypt`）、能读出页数。代码 PDF 的
页数须与 `正式资料/申请表信息.txt` 的「页数」字段一致（该文件缺少该字段时回退至
`草稿/申请表信息.md`，并在输出中标明依据来源），不一致即判失败；操作手册 PDF 只
输出页数，不作硬性比对（其页数由平台按上传文件自动填写）。

任一校验不通过时，本次生成的那份 PDF 会被改名为 `<原名>.pdf.invalid`（改名不成则删除），
以免半成品留在 `正式资料/` 被 `verify_materials.py` 当作页数判据；转换前就已存在、且本次
未被改写的 PDF 一律不动，并在终端提示须自行确认其是否过时。

只使用标准库，不引入任何第三方依赖。

注意：Microsoft Word 运行于沙盒之中，无法把 PDF 写入 `/private/tmp` 一类目录，此时会以
`document ... doesn't understand the "save as" message` 报错。`正式资料/` 位于用户项目内，
正常路径不受此限；若要另择目录做试验，须选在用户可访问的位置。

Usage:
    python3 convert_to_pdf.py --workdir 软件著作权申请资料
"""

from __future__ import annotations

import argparse
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from common import read_text

try:
    # 复用 verify_materials.py 里已有的 PDF 页数判定，避免两处实现对同一份 PDF
    # 给出不同页数，导致本脚本判「一致」而最终校验判「不一致」。
    from verify_materials import pdf_page_count
except Exception:  # pragma: no cover - 仅在脚本被单独拷出 scripts/ 时触发
    def pdf_page_count(pdf_path: Path) -> int | None:
        """Fallback copy of verify_materials.pdf_page_count()'s algorithm."""
        import zlib

        try:
            data = pdf_path.read_bytes()
        except Exception:
            return None

        def count_in(blob: bytes) -> tuple[int, int | None]:
            pages = len(re.findall(rb"/Type\s*/Page(?![s/A-Za-z])", blob))
            counts = [int(value) for value in re.findall(rb"/Count\s+(\d+)", blob)]
            return pages, (max(counts) if counts else None)

        page_objects, root_count = count_in(data)
        if page_objects:
            return page_objects
        if root_count:
            return root_count

        decoded = bytearray()
        for match in re.finditer(rb"stream\r?\n", data):
            chunk = data[match.end() : match.end() + 2_000_000]
            try:
                decoded += zlib.decompressobj().decompress(chunk)
            except Exception:
                continue
        if decoded:
            page_objects, root_count = count_in(bytes(decoded))
            if page_objects:
                return page_objects
            if root_count:
                return root_count
        return None


CODE_DOCX_PATTERN = "*-代码(*).docx"
MANUAL_DOCX_PATTERN = "*_操作手册.docx"

# 「页数」字段行：与 verify_materials.py 的 FIELD_LINE_RE 同源，另兼容草稿 Markdown
# 里可能出现的列表符号前缀。
PAGE_FIELD_RE = re.compile(r"^(?:➤|[-*]\s*)?页数[：:]\s*(\d+)\s*$")

WORD_BUNDLE_ID = "com.microsoft.Word"
WORD_TIMEOUT_SECONDS = 900
# Word 冷启动或正忙于排版时，`open` 之后文档不会立即出现在 documents 集合中，
# 须按名轮询等待；两者相乘即最长等待时长（此处为 300 秒）。
OPEN_POLL_ATTEMPTS = 600
OPEN_POLL_INTERVAL = 0.5
SOFFICE_TIMEOUT_SECONDS = 900

SOFFICE_CANDIDATES = [
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/opt/homebrew/bin/soffice",
    "/usr/local/bin/soffice",
    "/usr/bin/soffice",
]


# ---------------------------------------------------------------------------
# 后端探测


def find_word() -> bool:
    """Return True when Microsoft Word can be driven through osascript on this machine."""
    if platform.system() != "Darwin":
        return False
    if shutil.which("osascript") is None:
        return False
    # 只解析应用路径，不启动 Word，避免探测阶段就弹出界面。
    probe = subprocess.run(
        ["osascript", "-e", f'POSIX path of (path to application id "{WORD_BUNDLE_ID}")'],
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0 and probe.stdout.strip() != ""


def find_soffice() -> str | None:
    """Return the path to a usable LibreOffice `soffice` binary, or None."""
    found = shutil.which("soffice")
    if found:
        return found
    for candidate in SOFFICE_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# 转换


def applescript_quote(value: str) -> str:
    """Escape a Python string for embedding into an AppleScript string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_word_script(docx_path: Path, pdf_path: Path) -> str:
    doc_posix = applescript_quote(str(docx_path))
    pdf_posix = applescript_quote(str(pdf_path))
    doc_name = applescript_quote(docx_path.name)
    # AppleEvent 的默认应答上限为 120 秒，而操作手册含大量截图，重新分页与导出远超该时限，
    # 不显式放宽就会以 “AppleEvent timed out (-1712)” 失败。
    return f'''
set docPosix to "{doc_posix}"
set pdfPosix to "{pdf_posix}"
set docName to "{doc_name}"
with timeout of {WORD_TIMEOUT_SECONDS} seconds
    tell application "Microsoft Word"
        -- 先记下当前的警告设置：本脚本会临时关闭警告，异常路径上若不恢复，
        -- 该 Word 实例的后续人工使用会一直处于静默状态（保存、覆盖等提示均不再弹出）。
        set savedAlerts to alerts all
        try
            set savedAlerts to display alerts
        end try
        try
            set display alerts to alerts none
        end try
        set theDoc to missing value
        try
            -- 目标文档若因上一次失败仍处于打开状态，先关闭且不保存，否则 Word 会沿用
            -- 已在内存中的旧副本。此处按文档名寻址：Word 的 documents 集合不支持
            -- `count`，遍历写法会直接报错。
            try
                if (exists document docName) then close document docName saving no
            end try
            -- 不使用 read only：只读模式下 Word 不会重新分页，更新域也不会改变目录页码。
            -- Word 的 `open` 不返回可绑定的结果，`active document` 在文档尚未载入完成时又会
            -- 返回 missing value，故打开后按文档名轮询，直至该文档出现再取得其引用。
            open (POSIX file docPosix)
            repeat {OPEN_POLL_ATTEMPTS} times
                try
                    if (exists document docName) then
                        set theDoc to document docName
                        exit repeat
                    end if
                end try
                delay {OPEN_POLL_INTERVAL}
            end repeat
            if theDoc is missing value then
                error "Microsoft Word did not open " & docName & " within the expected time"
            end if
            repaginate theDoc
            -- 更新全部域，使目录（TOC 域）取得真实页码，而不是全部印成 1。
            -- 命令一次性作用于复数目标：Word 的 fields 集合不支持 `count`，
            -- `repeat with ... in (every field of ...)` 会直接报错。
            try
                update field (every field of text object of theDoc)
            on error updateErr
                -- 退路：只更新目录域的页码。仍失败则须报错，不可静默放过——
                -- 目录未更新时页码会全部印成 1，材料不可用。
                try
                    repeat with aToc in (every table of contents of theDoc)
                        update page numbers aToc
                    end repeat
                on error
                    error "failed to update fields: " & updateErr
                end try
            end try
            -- 更新域会改变正文长度，须再次重新分页后再导出。
            repaginate theDoc
            save as theDoc file name pdfPosix file format format PDF
            close theDoc saving no
            set theDoc to missing value
        on error errMsg number errNum
            -- 异常路径：文档不得留在 Word 中，警告设置须还原，随后原样抛回错误，
            -- 使 osascript 仍以非零退出码结束，Python 侧得以照常报错。
            try
                if theDoc is not missing value then close theDoc saving no
            end try
            try
                if (exists document docName) then close document docName saving no
            end try
            try
                set display alerts to savedAlerts
            end try
            error errMsg number errNum
        end try
        try
            set display alerts to savedAlerts
        end try
    end tell
end timeout
'''


def convert_with_word(docx_path: Path, pdf_path: Path) -> None:
    """Drive Microsoft Word through osascript; raise RuntimeError on failure."""
    script = build_word_script(docx_path, pdf_path)
    try:
        result = subprocess.run(
            ["osascript", "-"],
            input=script,
            capture_output=True,
            text=True,
            # 比 AppleScript 内部的 `with timeout` 多留一段余量，使超时先由 Word 侧报出
            # 具体错误，而不是被 Python 直接掐断进程、丢掉错误信息。
            timeout=WORD_TIMEOUT_SECONDS + 60,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Microsoft Word 转换 {docx_path.name} 超时（超过 {WORD_TIMEOUT_SECONDS + 60} 秒）。"
            "请检查 Word 是否弹出了对话框正在等待人工点击。"
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"Microsoft Word 转换 {docx_path.name} 失败（osascript 退出码 {result.returncode}）：\n"
            f"{result.stderr.strip()}"
        )


def convert_with_soffice(soffice: str, docx_path: Path, pdf_path: Path) -> None:
    """Convert through LibreOffice headless; raise RuntimeError on failure."""
    try:
        result = subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(pdf_path.parent),
                str(docx_path),
            ],
            capture_output=True,
            text=True,
            timeout=SOFFICE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"LibreOffice 转换 {docx_path.name} 超时（超过 {SOFFICE_TIMEOUT_SECONDS} 秒）。"
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"LibreOffice 转换 {docx_path.name} 失败（退出码 {result.returncode}）：\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    # LibreOffice 按源文件主名输出，正常情况下即为目标路径；若不然则显式报错，
    # 不做静默改名，以免掩盖版本差异导致的输出位置变化。
    if not pdf_path.exists():
        raise RuntimeError(
            f"LibreOffice 已执行完毕，但未在预期位置生成 {pdf_path}。"
            f"soffice 输出：{result.stdout.strip()}"
        )


# ---------------------------------------------------------------------------
# 校验


def pdf_is_encrypted(pdf_path: Path) -> bool:
    try:
        data = pdf_path.read_bytes()
    except Exception:
        return False
    return re.search(rb"/Encrypt[\s/<\[]", data) is not None


def inspect_pdf(pdf_path: Path, label: str) -> int:
    """Check existence / size / encryption / page count; raise RuntimeError on any failure."""
    if not pdf_path.exists():
        raise RuntimeError(f"{label} 转换后未生成 PDF：{pdf_path}")
    size = pdf_path.stat().st_size
    if size <= 0:
        raise RuntimeError(f"{label} 生成的 PDF 文件大小为 0 字节：{pdf_path}")
    if pdf_is_encrypted(pdf_path):
        raise RuntimeError(
            f"{label} 生成的 PDF 含有 /Encrypt 加密字典：{pdf_path}。"
            "版权中心平台不接受加密 PDF，须去除加密后重新上传。"
        )
    pages = pdf_page_count(pdf_path)
    if pages is None:
        raise RuntimeError(f"{label} 生成的 PDF 无法读出页数：{pdf_path}")
    return pages


def pdf_fingerprint(pdf_path: Path) -> tuple[int, int] | None:
    """Return (mtime_ns, size) for `pdf_path`, or None when it does not exist.

    用于区分「本次转换刚写出的 PDF」与「转换之前就已存在的 PDF」：只有前者才可
    由本脚本处置，后者可能是用户自行放入的文件，一律不动。
    """
    try:
        stat = pdf_path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def dispose_failed_pdf(pdf_path: Path, before: tuple[int, int] | None) -> str | None:
    """Quarantine a PDF that this run produced but that failed verification.

    半成品若留在 正式资料/，verify_materials.py 会把它（或上一次运行遗留的旧 PDF）
    当作页数判据与申请表比对，从而给出错误结论。故改名为 `.pdf.invalid`，改名不成
    再行删除；改名与删除只施加于本次生成的文件，转换前就存在且未被改写的 PDF 不动。
    返回一句已作何处置的说明，无须处置时返回 None。
    """
    after = pdf_fingerprint(pdf_path)
    if after is None:
        return None
    if before is not None and after == before:
        return (
            f"注意：{pdf_path.name} 的大小与修改时间与本次转换前完全一致，判定为本次之前就已存在的文件，"
            "未作处置。若其内容已过时，须自行删除，否则 verify_materials.py 会以它作为页数判据。"
        )
    target = pdf_path.parent / (pdf_path.name + ".invalid")
    try:
        if target.exists():
            target.unlink()
        pdf_path.rename(target)
        return (
            f"已将本次生成的 {pdf_path.name} 改名为 {target.name}，以免这份未通过校验的半成品被 "
            "verify_materials.py 当作页数判据；确认无用后可自行删除。"
        )
    except OSError as rename_error:
        try:
            pdf_path.unlink()
            return (
                f"改名失败（{rename_error}），已直接删除本次生成的半成品 {pdf_path.name}，"
                "以免它被 verify_materials.py 当作页数判据。"
            )
        except OSError as unlink_error:
            return (
                f"警告：本次生成的 {pdf_path.name} 未通过校验，且既无法改名也无法删除（{unlink_error}）。"
                "须手动删除，否则 verify_materials.py 会以它作为页数判据。"
            )


def read_declared_pages(workdir: Path) -> tuple[int, Path]:
    """Return the 页数 declared in 正式资料/申请表信息.txt, falling back to 草稿/申请表信息.md.

    以正式资料为先：verify_materials.py 判定页数时读的正是 正式资料/申请表信息.txt，
    两处若因手工改动分叉，本脚本按草稿判定会出现「转换通过、校验失败」的矛盾。
    回退到草稿时，返回的路径即依据来源，由调用方在输出中标明。
    """
    candidates = [
        workdir / "正式资料" / "申请表信息.txt",
        workdir / "草稿" / "申请表信息.md",
    ]
    checked: list[str] = []
    for path in candidates:
        if not path.exists():
            checked.append(f"{path}（不存在）")
            continue
        for line in read_text(path).splitlines():
            match = PAGE_FIELD_RE.match(line.strip())
            if match:
                return int(match.group(1)), path
        checked.append(f"{path}（存在，但未找到「页数」字段行）")
    raise RuntimeError("未能读取申请表的「页数」字段，已检查：" + "；".join(checked))


# ---------------------------------------------------------------------------


def find_one(formal_dir: Path, pattern: str, label: str) -> Path:
    # Word 打开文档期间会在同目录写入 `~$` 开头的属主锁文件，异常退出时可能残留。
    # 它同样匹配上面的通配符，须先排除，否则会被误判为「找到多份材料」。
    matches = sorted(p for p in formal_dir.glob(pattern) if not p.name.startswith("~$"))
    if not matches:
        raise RuntimeError(f"未在 {formal_dir} 找到匹配 `{pattern}` 的{label}")
    if len(matches) > 1:
        names = "、".join(p.name for p in matches)
        raise RuntimeError(
            f"在 {formal_dir} 找到多个匹配 `{pattern}` 的{label}：{names}。"
            "请先清理多余文件，确保正式资料目录中每类材料只有一份。"
        )
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="将正式资料中的 Word 文档转换为平台上传所需的 PDF")
    parser.add_argument("--workdir", default="软件著作权申请资料", help="skill 输出目录（默认为 软件著作权申请资料）")
    args = parser.parse_args()

    workdir = Path(args.workdir).resolve()
    formal_dir = workdir / "正式资料"
    if not formal_dir.is_dir():
        print(f"错误：正式资料目录不存在：{formal_dir}", file=sys.stderr)
        return 1

    try:
        code_docx = find_one(formal_dir, CODE_DOCX_PATTERN, "代码鉴别材料 docx")
        manual_docx = find_one(formal_dir, MANUAL_DOCX_PATTERN, "操作手册 docx")
    except RuntimeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    use_word = find_word()
    soffice = None if use_word else find_soffice()
    if use_word:
        print("转换后端：Microsoft Word（经 osascript 驱动，导出前更新域并重新分页）")
    elif soffice:
        print(f"转换后端：LibreOffice（{soffice}）")
        print(
            "提示：LibreOffice 未必会更新 Word 的目录域。转换完成后，须人工打开操作手册 PDF 的"
            "首页目录，逐条核对页码是否为真实页码，而非全部印成 1。"
        )
    else:
        print(
            "错误：本机既没有可用的 Microsoft Word（仅 macOS 支持经 osascript 驱动），"
            "也没有可用的 LibreOffice（soffice）。请安装其中之一后重试。",
            file=sys.stderr,
        )
        return 1

    results: list[tuple[str, Path, int, tuple[int, int] | None]] = []
    for label, docx_path in (("代码鉴别材料", code_docx), ("操作手册", manual_docx)):
        pdf_path = docx_path.with_suffix(".pdf")
        # 转换前先记录目标 PDF 的指纹，失败时据此判断该文件是否确由本次生成，
        # 从而只处置本次的产物，不误删用户自行放入的同名 PDF。
        before = pdf_fingerprint(pdf_path)
        print(f"正在转换 {label}：{docx_path.name} → {pdf_path.name}")
        try:
            if use_word:
                convert_with_word(docx_path, pdf_path)
            else:
                assert soffice is not None
                convert_with_soffice(soffice, docx_path, pdf_path)
            pages = inspect_pdf(pdf_path, label)
        except RuntimeError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            disposal = dispose_failed_pdf(pdf_path, before)
            if disposal:
                print(disposal, file=sys.stderr)
            return 1
        results.append((label, pdf_path, pages, before))

    code_pages = results[0][2]
    try:
        declared_pages, declared_from = read_declared_pages(workdir)
    except RuntimeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    if declared_from.suffix.lower() == ".md":
        print(
            f"提示：未能从 正式资料/申请表信息.txt 读取「页数」字段，本次依据草稿 {declared_from}。"
            "verify_materials.py 一律以 正式资料/申请表信息.txt 为准，两者若已分叉，"
            "会出现「本脚本通过、最终校验失败」的矛盾，须先使正式资料补齐该字段。"
        )

    if declared_pages != code_pages:
        print(
            f"错误：代码鉴别材料 PDF 的实际页数为 {code_pages}，"
            f"而 {declared_from} 的「页数」字段为 {declared_pages}，"
            f"相差 {abs(code_pages - declared_pages)} 页。"
            "两者必须一致：请先核对代码材料的取用范围与申请表页数字段，修正后重新生成。",
            file=sys.stderr,
        )
        code_pdf_path, code_before = results[0][1], results[0][3]
        disposal = dispose_failed_pdf(code_pdf_path, code_before)
        if disposal:
            print(disposal, file=sys.stderr)
        return 1

    for label, pdf_path, pages, _ in results:
        print(f"OK {label} PDF：{pdf_path}（{pages} 页，未加密）")
    print(
        f"OK pdf conversion: 代码鉴别材料 PDF 页数 {code_pages} 与 {declared_from} 的「页数」字段一致"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
