#!/usr/bin/env python3
"""Check runtime capabilities at the beginning of the workflow."""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
from pathlib import Path
from typing import Any

from common import ensure_dir, write_json


def command_version(command: list[str]) -> tuple[bool, str]:
    if not shutil.which(command[0]):
        return False, "not found"
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=20)
        output = (completed.stdout or completed.stderr).strip().splitlines()
        return completed.returncode == 0, output[0] if output else "available"
    except Exception as exc:
        return False, str(exc)


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def module_version(distribution: str) -> str:
    """Return an installed distribution's version string, or "not found"."""
    try:
        from importlib.metadata import version

        return version(distribution)
    except Exception:
        return "not found"


def check_environment(skill_dir: Path) -> dict[str, Any]:
    python_docx = module_available("docx")
    # fpdf2 只服务于代码鉴别材料的 PDF 直出路径：有它便无须 Word 与 LibreOffice
    # 即可产出代码 PDF；没有它不影响其余环节，转换时自动降级。
    fpdf_ok = module_available("fpdf")
    pandoc_ok, pandoc_version = command_version(["pandoc", "--version"])
    ffmpeg_ok, ffmpeg_version = command_version(["ffmpeg", "-version"])
    ffprobe_ok, ffprobe_version = command_version(["ffprobe", "-version"])

    final_docx_mode = "python-docx" if python_docx else ("pandoc" if pandoc_ok else "basic-ooxml")
    requires_user_input = not python_docx and not pandoc_ok
    next_action = (
        "python-docx 与 pandoc 均不可用。请在以下两项中作出选择：1) 安装 python-docx（pip install python-docx）；2) 采用基础 DOCX 缺省生成方式继续。选择确定后方可进入项目分析。"
        if requires_user_input
        else "DOCX 生成环境可用，可进入项目分析。"
    )
    return {
        "output_directory": "当前目录/软件著作权申请资料",
        "capabilities": {
            "markdown_drafts": True,
            "application_txt": True,
            "basic_docx": True,
            "python_docx": python_docx,
            "pandoc_preview": pandoc_ok,
            "operation_video_frames": ffmpeg_ok and ffprobe_ok,
            "code_pdf_direct": fpdf_ok,
        },
        "versions": {
            "fpdf2": module_version("fpdf2") if fpdf_ok else "not found",
            "pandoc": pandoc_version,
            "ffmpeg": ffmpeg_version,
            "ffprobe": ffprobe_version,
        },
        "final_docx_mode": final_docx_mode,
        "recommendation": (
            (
                "python-docx 可用，将采用 python-docx 生成符合规范的 Word 文档。"
                if python_docx
                else "python-docx 不可用，将采用 pandoc 或基础 OOXML 方式缺省生成 Word 文档。建议安装 python-docx，以获得更规范的排版控制（pip install python-docx）。"
            )
            + (
                "fpdf2 可用，代码鉴别材料的 PDF 可由中间 Markdown 直接渲染，无须 Microsoft Word 或 LibreOffice；"
                "操作手册的 PDF 仍须经 Word 导出，因其目录页码由 Word 的域回填。"
                if fpdf_ok
                else "fpdf2 不可用，代码鉴别材料的 PDF 须经 Microsoft Word 或 LibreOffice 转换。"
                "建议安装 fpdf2（pip install fpdf2），此后在没有 Word 与 LibreOffice 的机器上亦可产出代码材料 PDF。"
            )
        ),
        "install_prompt": (
            "是否安装 python-docx。安装后文档生成更为规范；不安装亦可继续，将采用 pandoc 或基础 DOCX 方式缺省生成。"
            if not python_docx
            else "无须安装，python-docx 已可用。"
        ),
        "requires_user_input": requires_user_input,
        "confirmation_stage": "environment" if requires_user_input else None,
        "next_action": next_action,
    }


def write_markdown(path: Path, data: dict[str, Any]) -> None:
    caps = data["capabilities"]
    lines = [
        "# 软著申请资料生成环境检查",
        "",
        f"- 输出目录：`{data['output_directory']}`",
        f"- 最终 Word 模式：`{data['final_docx_mode']}`",
        "",
        "## 能力状态",
        "",
        f"- Markdown 草稿：{'可用' if caps['markdown_drafts'] else '不可用'}",
        f"- 申请表 TXT：{'可用' if caps['application_txt'] else '不可用'}",
        f"- 基础 DOCX 生成：{'可用' if caps['basic_docx'] else '不可用'}",
        f"- python-docx：{'可用' if caps['python_docx'] else '不可用'}",
        f"- pandoc 预览：{'可用' if caps['pandoc_preview'] else '不可用'}（{data['versions']['pandoc']}）",
        f"- 操作视频抽帧（ffmpeg + ffprobe）：{'可用' if caps['operation_video_frames'] else '不可用'}（ffmpeg: {data['versions']['ffmpeg']}；ffprobe: {data['versions']['ffprobe']}）",
        f"- 代码材料 PDF 直出（fpdf2）：{'可用' if caps['code_pdf_direct'] else '不可用'}（fpdf2: {data['versions']['fpdf2']}）",
        "",
        "代码鉴别材料的分页由脚本固定为每页 50 行，页数在生成时即已确定，故其 PDF 可由中间 Markdown 直接渲染。fpdf2 可用时走该路径，不可用时降级至 Microsoft Word 或 LibreOffice。操作手册不适用直出：其目录是 Word 的 TOC 域，页码须经 Word 渲染回填。",
        "",
        "操作视频抽帧能力仅在用户选择提供操作视频时需要。不提供视频不影响后续流程，仍可使用当前环境中任一具备浏览器操作与截图能力的工具，或由用户自行提供截图。",
        "",
        "## 建议",
        "",
        data["recommendation"],
        "",
        "## 用户选择",
        "",
        data["install_prompt"],
        "",
        "python-docx 与 pandoc 均不可用时，须先等待用户作出选择，并记录 `environment` 确认点，方可继续。",
        "",
        "```text" if data.get("requires_user_input") else "",
        "STOP_FOR_USER" if data.get("requires_user_input") else "",
        f"NEXT_ACTION: {data['next_action']}" if data.get("requires_user_input") else "",
        "```" if data.get("requires_user_input") else "",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="软件著作权申请资料")
    args = parser.parse_args()

    skill_dir = Path(__file__).resolve().parents[1]
    out_dir = ensure_dir(Path(args.out_dir))
    data = check_environment(skill_dir)
    write_json(out_dir / "环境检查.json", data)
    write_markdown(out_dir / "环境检查.md", data)
    print(f"OK environment check: {out_dir}")
    print(f"Final DOCX mode: {data['final_docx_mode']}")
    print(data["recommendation"])
    if data.get("requires_user_input"):
        print("STOP_FOR_USER")
        print(f"NEXT_ACTION: {data['next_action']}")


if __name__ == "__main__":
    main()
