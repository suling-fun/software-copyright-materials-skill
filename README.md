# software-copyright-materials

本 skill 用于从真实软件项目生成、校验并辅助提交中国软件著作权申请材料。

本项目是 [Fokkyp/SoftwareCopyright-Skill](https://github.com/Fokkyp/SoftwareCopyright-Skill) 1.3 的派生项目，当前仓库为 [suling-fun/software-copyright-materials-skill](https://github.com/suling-fun/software-copyright-materials-skill)，由 [suling-fun](https://github.com/suling-fun) 维护。本项目并非上游仓库的简单镜像，而是在继承原有思路的基础上完成了大范围修正、增补与重构。

## 来源与主要变化

- 增补中国版权保护中心网站填报、打印与签章页上传的操作流程。
- 建立正式资料、中间产物、截图、视频帧与提交记录的固定归档目录。
- 将操作视频处理能力精简为 FFmpeg 与 FFprobe 抽帧，移除语音转录依赖。
- 增加分阶段用户确认、材料一致性检查与交付前校验机制。
- 重构项目分析、代码材料、申请信息、操作手册与 DOCX 生成脚本及相应规则。

## 版本

当前版本为 4.0.0。自 4.0.0 起，业务理解 JSON 的字段契约不向后兼容：新增 `manual_overview`、`manual_feature_highlights` 与 `manual_modules[].body` 三个必填的正文字段，删除 `manual_sections` 与 `related_documents`，原先由脚本按固定清单生成的条件章节改由通用的 `manual_extra_sections` 承载。按 3.x 生成的业务理解文件须重新编写后方可使用。

本项目继续采用上游的 MIT License，并完整保留原版权与许可声明，详见 [LICENSE](LICENSE)。使用本项目不代表获得中国版权保护中心或其他登记机构的认可、认证或背书。

## 设计取向

本 skill 与常见的材料生成工具有两处根本区别，二者共同决定了成稿的质量上限。

**操作手册的章节结构由模型判断，不由脚本预设。** 必备章节固定为说明、功能特点、系统要求、各功能页面与模块、常见问题解答、术语表六项；其余章节一律由模型阅读项目后按三条判定标准自行决定是否设立。本 skill 不提供可选章节清单，脚本亦不解释任何章节的语义，只按模型指定的位置插入并原样渲染。

**操作手册的叙述性正文由模型撰写，脚本不造句。** 脚本无法判断语句是否通顺、是否贴合当前项目，按结构化字段拼装句子只会产出模板化正文与偶发病句。因此脚本的职责限于章节顺序与编号、图注编号、表格渲染、页眉，以及段落数、字数、必备要素覆盖、套话与模板句式的校验；不得改写模型写下的文字，发现问题一律报为待修正项交由模型重写。

## 主要能力

- 分析真实项目，生成业务理解、申请信息、代码材料与操作手册草稿。
- 通过分阶段人工门禁与一致性校验控制材料生成过程，缺少必要输入时停止而非缺省生成。
- 从操作视频中抽取界面帧，或调用当前环境已有的浏览器自动化与桌面操作截图能力。
- 按固定目录保存正式资料、中间产物、截图与版权中心提交记录。
- 指导中国版权保护中心网站的填报、签章页打印与上传。

详细工作流、人工门禁与材料规则参见 [SKILL.md](SKILL.md)。

## 安装

将仓库克隆到运行环境的 skill 目录，具体位置按该环境的 Skill 发现规则确定：

```bash
git clone https://github.com/suling-fun/software-copyright-materials-skill.git \
  <运行环境的 skill 目录>/software-copyright-materials
```

任何支持 `SKILL.md` 的运行环境均可使用。启用后，执行首个脚本前须先按 [SKILL.md](SKILL.md) 「脚本目录」一节的说明导出 `SKILL_DIR`。

## 运行依赖

- Python 3.10 或更高版本；
- 推荐安装 `python-docx`；该库缺失时，部分 DOCX 生成路径可改用 Pandoc；
- 视频抽帧需要 FFmpeg 与 FFprobe；
- 网页截图需要任一具备浏览器操作与截图能力的环境，常见实现有 Playwright、Playwright MCP、Chrome DevTools MCP、Browser Use、Stagehand 与 Browserbase；桌面应用截图需要 computer use 类桌面控制工具；本地 Playwright 路径还需要可用的浏览器运行时；
- PDF 导出需要 Word、Pages、LibreOffice 或其他能够正确渲染 DOCX 的工具；`python-docx` 负责读写 DOCX，不包含 PDF 排版渲染引擎。

## 免责声明

本项目仅提供材料整理与流程辅助，不构成法律意见或登记结果保证。提交前应由申请人核对材料、权属和平台最新要求。
