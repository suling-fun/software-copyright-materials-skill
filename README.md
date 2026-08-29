# software-copyright-materials

用于从真实软件项目生成、校验并辅助提交中国软件著作权申请材料的 Skill。

本项目是 [Fokkyp/SoftwareCopyright-Skill](https://github.com/Fokkyp/SoftwareCopyright-Skill) 1.3 的派生项目，当前仓库为 [suling-fun/software-copyright-materials-skill](https://github.com/suling-fun/software-copyright-materials-skill)，由 [suling-fun](https://github.com/suling-fun) 维护。这不是上游仓库的简单镜像，而是在继承原有思路的基础上进行了大范围修正、增补与重构。

## 来源与主要变化

- 增补中国版权保护中心网站填报、打印和上传签章页的操作流程；
- 建立正式资料、中间产物、截图、视频帧和提交记录的固定归档目录；
- 将操作视频能力精简为 FFmpeg 与 FFprobe 抽帧，移除语音转录依赖；
- 增加分阶段用户确认、材料一致性检查和交付前校验机制；
- 重构项目分析、代码材料、申请信息、操作手册和 DOCX 生成脚本及相应规则。

本项目继续采用上游的 MIT License，并完整保留原版权与许可声明，详见 [LICENSE](LICENSE)。使用本项目不代表获得中国版权保护中心或其他登记机构的认可、认证或背书。

## 主要能力

- 分析真实项目并生成业务理解、申请信息、代码材料和操作手册草稿；
- 通过分阶段确认和一致性校验控制材料生成过程；
- 从操作视频中抽取界面帧，或复用当前环境已有的浏览器操作与截图能力；
- 按固定目录保存正式资料、中间产物、截图和版权中心提交记录；
- 指导中国版权保护中心网站填报、签章页打印及上传。

详细工作流、人工门禁和材料规则见 [SKILL.md](SKILL.md)。

## 安装

将仓库克隆到 Claude Skills 目录：

```bash
git clone https://github.com/suling-fun/software-copyright-materials-skill.git \
  ~/.claude/skills/software-copyright-materials
```

也可以复制本目录到其他支持 `SKILL.md` 的运行环境，并按该环境的 Skill 发现规则启用。

## 运行依赖

- Python 3.10 或更高版本；
- 推荐安装 `python-docx`；缺失时部分 DOCX 生成路径可使用 Pandoc；
- 视频抽帧需要 FFmpeg 和 FFprobe；
- 网页截图可使用 Playwright、ChatGPT、Claude、Ego Lite 等任一具备浏览器操作和截图能力的环境，本地 Playwright 路径还需要可用的浏览器运行时；
- PDF 导出需要 Word、Pages、LibreOffice 或其他能够正确渲染 DOCX 的工具；`python-docx` 负责读写 DOCX，不包含 PDF 排版渲染引擎。

## 免责声明

本项目仅提供材料整理与流程辅助，不构成法律意见或登记结果保证。提交前应由申请人核对材料、权属和平台最新要求。
