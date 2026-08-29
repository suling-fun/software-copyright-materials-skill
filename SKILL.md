---
name: software-copyright-materials
description: >
  Generate guided Chinese software copyright application materials from a real project
  and assist with submission on the China Copyright Protection Center platform.
  Use this skill when the user asks for 软件著作权, 软著申请资料, 软著代码材料,
  操作手册, 申请表信息, 官网填报, 签章页, or wants Word/TXT/PDF materials and
  guided submission for software copyright registration.
  The workflow analyzes the imported project, extracts real source code, creates Markdown
  drafts for user confirmation, then uses bundled DOCX tooling to produce final
  Word documents and TXT.
license: MIT
allowed-tools: Bash Read Write Edit Glob Grep WebSearch WebFetch
metadata:
  short-description: 生成、校验并辅助提交中国软件著作权申请材料
  author: suling-fun
  maintainer: suling-fun
  version: "3.0.0"
  repository: https://github.com/suling-fun/software-copyright-materials-skill
  upstream: https://github.com/Fokkyp/SoftwareCopyright-Skill
  upstream-version: "1.3"
  provenance: 基于上游 Skill 大范围修正、增补与重构
---

# 软著申请资料生成

本项目基于 [Fokkyp/SoftwareCopyright-Skill](https://github.com/Fokkyp/SoftwareCopyright-Skill) 1.3 进行了大范围修正、增补与重构；来源、许可继承和主要变化见 [README.md](README.md#来源与主要变化)。

这个 skill 生成可审阅、可追溯的软著申请资料。详细规则拆分在 `references/` 下，按阶段需要再读取；本文件只保留跨阶段的核心原则和严格顺序化的执行步骤。

创建任何材料或中间产物前，必须先读取 [references/output_directory_rules.md](references/output_directory_rules.md)，确定目标项目根目录和本阶段唯一允许的写入位置。

## 运行条件

- 需要 Python 3.10 或更高版本。
- 推荐安装 `python-docx`；不可用时可选用 `pandoc` 作为 DOCX 兜底路径。
- 处理操作视频时需要 `ffmpeg` 和 `ffprobe`，仅用于提取具有时间覆盖保证的画面帧。

## 核心原则

- 固定输出根目录：`<目标项目根目录>/软件著作权申请资料/`。运行使用相对默认路径的脚本前先进入目标项目根目录；只有测试 Skill 自身时才允许显式指定隔离的临时目录。完整目录树、归类、命名、禁止事项和旧项目兼容原则见 [references/output_directory_rules.md](references/output_directory_rules.md)。
- 先生成 Markdown 草稿，用户确认后再生成正式 Word/TXT；正式可交付或可上传材料只能写入 `正式资料/`，版权中心操作记录、签章页、回执和状态证据只能写入 `提交记录/`，不要散落在输出目录根部或混入正式资料。
- 正式 Word/TXT 的文字一律使用默认黑色字体，不生成蓝色超链接、主题色标题或其他彩色文字；Markdown 链接写入 Word 时必须转成普通文本。
- 正式资料中的软件名称、代码 Word 页眉版本号必须与 `草稿/申请表信息.md` 已确认的"软件全称""版本号"字段一致；正式生成时以已确认字段为准。
- 代码材料必须来自真实项目源码，禁止 AI 编造代码。
- 写申请表和操作手册前，必须先形成模型研判后的 `草稿/业务理解.md/json`，理解软件业务、行业、目标用户、核心价值和操作流程；详见 [references/business_understanding_rules.md](references/business_understanding_rules.md)。
- 如果用户能提供该软件的真实操作演示视频，操作视频优先于纯代码推断：典型操作流程、操作手册的操作步骤和界面截图都应该优先以视频里实际观察到的内容为准。视频处理只负责抽帧，判断每一帧在做什么必须由模型逐张查看后决定，不得只按文件名或时间顺序猜测。
- 脚本只能收集项目证据、校验字段和生成文件；行业判断、功能抽取、代码抽取选择、操作手册结构必须由模型阅读项目后决定，不得依赖脚本关键字表或固定范本。
- 生成代码材料前，必须先生成代码文件候选清单，由模型理解项目后填写抽取文件和选择理由，再让用户确认或修改；详见 [references/code_selection_rules.md](references/code_selection_rules.md)。
- 代码鉴别材料的分页、页数规则详见 [references/copyright_material_rules.md](references/copyright_material_rules.md)。
- 操作手册成稿应像真实软件随附的操作说明，而不是研发说明、功能清单或 AI 生成的汇总文；结构、写作口径和"去 AI 味"要求详见 [references/manual_structure.md](references/manual_structure.md)。
- 申请表信息各字段的口径、默认值和一致性要求详见 [references/application_fields.md](references/application_fields.md)。
- 在中国版权保护中心完成 R11 官网填报、保存草稿、确认填报、打印及上传签章页时，读取 [references/ccopyright_submission_workflow.md](references/ccopyright_submission_workflow.md)。
- 申请表信息中的硬件/系统环境必须让用户确认或填写，不能硬编码。
- Word 生成优先用 `python-docx`；不可用时按 `pandoc`、再按基础 OOXML 兜底，不得引用本 skill 之外的 DOCX 生成目录或工具。

## 强制人工门禁

凡是涉及用户选择、确认或补充信息的阶段，必须先停止当前执行，不得继续调用下一步脚本。即使处于自动审核、自动继续或无人值守模式，也必须把 `STOP_FOR_USER` 和 `NEXT_ACTION` 原样告知用户，并等待用户输入后再继续。

禁止使用"用户未选择则默认继续"的逻辑。用户回复确认后，先用确认脚本记录对应门禁，再进入下一阶段：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/confirm_stage.py --workdir 软件著作权申请资料 --stage <阶段名> --note "<用户确认内容>"
```

必须停住的门禁：

- `environment`：`python-docx` 和 `pandoc` 均不可用时，用户必须选择"安装 python-docx"或"使用基础 DOCX 兜底继续"。
- `project`：存在多个项目候选目录时，用户必须指定项目目录。
- `business`：`草稿/业务理解.md` 生成后，用户必须确认行业、目标用户、核心功能和申请口径。
- `application-fields`：`草稿/申请表信息.md` 生成后，用户必须补全并确认硬件、系统环境、著作权人、日期等字段。
- `code-selection`：`草稿/代码文件选择.json` 生成后，用户必须确认或修改抽取文件。
- `screenshot-method`：操作手册截图前，用户必须在操作视频、Chrome DevTools MCP、Codex Computer Use、用户自行截图四种方式中选择一种（是否提供操作视频在第 3 步已先问过，此处正式记录最终采用的方式，视频缺口部分可以补充说明用了哪种方式处理）；如果用户明确说"现在不截图/先跳过截图"，记录为 `skip`。
- `markdown`：全部 Markdown 草稿完成后，用户必须确认可以进入 Word/TXT 生成。

## 工作流

### 1. 启动环境检查

先进入已确定的目标项目根目录，再创建固定输出目录并检查运行能力：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/check_environment.py \
  --out-dir 软件著作权申请资料
```

输出：

- `软件著作权申请资料/环境检查.md`
- `软件著作权申请资料/环境检查.json`

环境检查必须告诉用户：

- 当前会在"目标项目根目录/软件著作权申请资料"下生成材料。
- Markdown 草稿、TXT、基础 DOCX 是否可用。
- `python-docx` 是否可用（决定能否走规范 Word 排版路径）。
- `python-docx` 不可用时，`pandoc` 是否可用作为兜底。

用户选择：

- 如果 `python-docx` 已可用，直接继续，无需用户选择。
- 如果 `python-docx` 不可用但 `pandoc` 可用，继续用 `pandoc` 兜底生成，同时提示用户可执行 `pip install python-docx` 获得更规范的排版控制。
- 如果 `python-docx` 和 `pandoc` 都不可用，必须停止并等待用户选择"安装 python-docx"还是"使用基础 OOXML 兜底继续"；不得自动继续。

需要用户选择时，用户回复后记录门禁：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/confirm_stage.py \
  --workdir 软件著作权申请资料 \
  --stage environment \
  --note "<用户选择>"
```

不要等到最后验证阶段才发现 DOCX 生成环境不可用；这个信息必须在流程开始时给出。

### 2. 定位项目

用户通常会把项目放在当前文件夹下。先扫描当前目录，避开本 skill、自身输出目录、`node_modules`、构建产物和隐藏目录，找到最可能的项目根目录。

如果有多个候选项目，必须停止并询问用户选择；如果只有一个明显候选项目，可以直接使用。

### 3. 操作视频（可选）

在分析项目和撰写业务理解、操作手册之前，先停止询问用户：是否有该软件的真实操作演示视频（录屏）？如果有，这段视频会优先于纯代码推断，同时用于后续的业务理解、操作手册撰写和截图三个环节。

- 如果用户没有视频，直接跳过本步骤，继续第 4 步；截图方式留到第 9 步再从 Chrome DevTools MCP、Codex Computer Use、用户自行截图三选一。
- 如果用户能提供视频，获取文件路径后运行：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/process_operation_video.py \
  --video <视频文件路径> \
  --out-dir 软件著作权申请资料/操作视频
```

该脚本按时间均匀抽帧（默认最密每秒 1 帧，帧数上限 120 张，抽帧间隔按视频总时长自动拉伸；`--max-frames` 可以调大上限），再叠加一遍画面突变检测补充额外的帧；不是只依赖画面突变检测本身来决定抽哪些帧——真实界面录屏大多是"大部分画面不变、只有一小块区域变化"，纯突变检测的分数常常低到测不出来，会静默漏掉真实发生的状态变化，所以必须以按时间均匀取样兜底。视频较长（超过几分钟）时应该调大 `--max-frames`，否则平均每帧间隔会被拉得很大，可能错过密集的操作步骤。判断每一帧画面里在操作什么、界面上有什么，必须由模型逐张使用当前环境可用的图片查看能力检查 `软件著作权申请资料/操作视频/帧/*.png` 后自行判断，不得只凭文件名或时间戳臆测画面内容。

输出：

- `操作视频/帧/*.png`：按时间均匀取样并叠加画面突变检测抽取的帧，附带时间戳。
- `操作视频/视频证据.md`：逐帧时间戳和图片相对路径。
- `操作视频/视频证据.json`：同样内容的结构化版本。

如果第 1 步环境检查显示 `ffmpeg` 不可用，或视频处理脚本报错，必须停止并让用户选择：安装 `ffmpeg` 后重试，还是放弃视频、改用第 9 步的其他截图方式。不得跳过用户选择直接假装视频可用或者假装没有视频。

### 4. 分析项目

运行：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/analyze_project.py \
  --project <项目目录> \
  --out 软件著作权申请资料/analysis/project.json
```

分析内容包括：

- `package.json`、README、脚本命令、依赖
- 前端框架和主要编程语言
- 入口文件、路由、页面、组件、接口、状态管理
- 源码文件数量和源程序行数
- 软件名称候选、主要功能候选、运行命令候选

### 5. 形成业务理解

在写申请表和操作手册前，先让脚本收集项目证据：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/generate_business_context.py \
  --project <项目目录> \
  --analysis 软件著作权申请资料/analysis/project.json \
  --software-name "<软件全称>" \
  --out-dir 软件著作权申请资料/草稿
```

输出：

- `草稿/业务理解证据.md`
- `草稿/业务理解证据.json`
- `草稿/业务理解模型稿模板.json`

这一步只收集证据，不决定最终业务口径。接下来必须由模型阅读 `业务理解证据.md/json`、README、PRD/BRD、页面文案、路由、接口、必要源码、用户补充资料，以及第 3 步生成的 `操作视频/视频证据.md`（如果用户提供了操作视频），自行判断行业、目标用户、核心价值、应写入的功能、典型操作流程和操作手册章节结构，生成业务理解模型稿 JSON。有操作视频时，`operation_flow`（典型操作流程）应该优先反映视频里观察到的真实操作顺序，而不是只按代码路由结构推断。字段清单和 `manual_modules` 子字段要求见 [references/business_understanding_rules.md](references/business_understanding_rules.md)；模型不得用脚本关键字表决定行业、功能和结构。

如果项目材料不足、业务类型较新，或用户明确希望参考竞品，可联网搜索相近产品和行业资料；外部调研只用于理解行业表达，不能编造项目不存在的功能，调研摘要应写入业务理解草稿并区分"项目证据"和"行业参考"。

模型完成研判后运行：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/generate_business_context.py \
  --project <项目目录> \
  --analysis 软件著作权申请资料/analysis/project.json \
  --software-name "<软件全称>" \
  --out-dir 软件著作权申请资料/草稿 \
  --model-context <模型生成的业务理解JSON>
```

输出：

- `草稿/业务理解.md`
- `草稿/业务理解.json`

生成后必须停止，等待用户确认或修改；业务理解确认前，不得生成申请表和操作手册。如果业务理解仍不充分，先请用户补充产品说明。用户确认后运行：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/confirm_stage.py \
  --workdir 软件著作权申请资料 \
  --stage business \
  --note "<用户确认内容>"
```

### 6. 引导用户确认字段

根据分析结果，按官网实际表单顺序向用户确认申请表全部字段（软件全称、版本号、著作权人、硬件/系统环境、主要功能等）。项目可推断字段可以先给建议值，但硬件/系统环境必须允许用户选择建议值或手动填写。完整字段清单、默认值和一致性要求见 [references/application_fields.md](references/application_fields.md)。

此阶段需要先停止等待用户输入；收到用户回复后，可整理为 `answers` JSON 传入申请表草稿生成。申请表字段的最终门禁在 `草稿/申请表信息.md` 生成后记录（见第 8 步）。

### 7. 确认代码文件选择

生成代码材料前，先运行候选文件分析：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/propose_code_selection.py \
  --project <项目目录> \
  --analysis 软件著作权申请资料/analysis/project.json \
  --out-dir 软件著作权申请资料/草稿
```

输出：

- `草稿/代码文件候选清单.md`：给用户看的候选说明。
- `草稿/代码文件选择.json`：可编辑的选择文件。

脚本生成的候选清单只列证据，不默认选择文件。模型必须先阅读业务理解、候选文件、入口文件、页面文件和必要源码，判断哪些源码最能体现软件真实功能和运行逻辑，然后修改 `代码文件选择.json` 中每个文件的 `selected` 和 `model_reason`。选择优先级和边界见 [references/code_selection_rules.md](references/code_selection_rules.md)。用户确认并记录 `code-selection` 门禁后，代码抽取只读取 `代码文件选择.json` 中选中的完整文件。用户确认后运行：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/confirm_stage.py \
  --workdir 软件著作权申请资料 \
  --stage code-selection \
  --note "<用户确认内容>"
```

### 8. 生成 Markdown 草稿

运行代码材料抽取：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/extract_code_material.py \
  --project <项目目录> \
  --analysis 软件著作权申请资料/analysis/project.json \
  --selection 软件著作权申请资料/草稿/代码文件选择.json \
  --software-name "<软件全称>" \
  --version "<版本号>" \
  --out-dir 软件著作权申请资料/草稿
```

代码分页和页数规则（每页行数、`代码-前后各30页.md`/`代码-全部.md` 的生成条件、候选源码不足时的停止条件）见 [references/copyright_material_rules.md](references/copyright_material_rules.md)。同时生成 `代码提取清单.md` 和 `代码提取清单.json`，用于追溯代码来源。

生成申请表信息草稿：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/generate_application_info.py \
  --analysis 软件著作权申请资料/analysis/project.json \
  --code-manifest 软件著作权申请资料/草稿/代码提取清单.json \
  --business-context 软件著作权申请资料/草稿/业务理解.json \
  --software-name "<软件全称>" \
  --version "<版本号>" \
  --out-dir 软件著作权申请资料/草稿
```

生成后必须停止，让用户检查并补全 `草稿/申请表信息.md`。字段补全并确认后运行：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/confirm_stage.py \
  --workdir 软件著作权申请资料 \
  --stage application-fields \
  --note "<用户确认内容>"
```

生成操作手册草稿：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/generate_manual_draft.py \
  --analysis 软件著作权申请资料/analysis/project.json \
  --business-context 软件著作权申请资料/草稿/业务理解.json \
  --software-name "<软件全称>" \
  --version "<版本号>" \
  --out-dir 软件著作权申请资料/草稿
```

操作手册必须基于模型写入 `草稿/业务理解.json` 的 `manual_modules` 组织章节，语言面向普通用户，避免代码、框架、接口等技术化表达。如果第 3 步提供了操作视频，每个能在视频里找到对应画面的模块，其操作步骤、界面元素、操作结果描述都应该以模型逐帧查看 `操作视频/视频证据.md` 后观察到的真实内容为准，而不是仅凭代码结构或业务理解推断；视频没有覆盖的模块仍按现有方式基于业务理解和源码证据撰写。完整的骨架、写作口径、"去 AI 味"要求和自检记录规范见 [references/manual_structure.md](references/manual_structure.md)。撰写时由 agent 自行循环检查、压缩重复内容和修正缺失信息。每个核心模块正文通常为 2 个短段落，普通模块目标约 120-220 个中文字符，复杂模块最多约 300 字；保留真实入口、主要动作、关键规则、结果反馈和独立截图预留，不为凑字数复述背景。完整草稿完成后只向用户发起一次整体确认，确认前不得进入正式 Word/TXT 生成。

### 9. 选择并获取截图

先检查第 3 步是否已经提供并处理过操作视频（`软件著作权申请资料/操作视频/视频证据.md` 是否存在）：

- 如果存在：对操作手册里能在视频中找到对应画面的模块，模型先用 Read 工具逐帧查看 `操作视频/帧/*.png`，为每个模块挑选最能体现该模块界面和操作结果的一帧或几帧，复制到 `软件著作权申请资料/截图/` 并按模块命名（例如 `项目管理-列表页.png`），不能只按时间顺序机械分配、也不能一个模块配多张无关的帧。只有操作手册里存在视频没有覆盖到的模块，才需要从下面三种方式里再选一种处理这部分缺口。
- 如果不存在：从下面三种方式中选择一种，覆盖整本操作手册。

必须给出的截图方式（视频已覆盖时以下三种只用于处理缺口模块）：

1. Chrome DevTools MCP：适合已在浏览器中打开的 Web 项目，优先用于网页全页截图。
2. Codex Computer Use：适合需要通过桌面应用或浏览器界面点击、切换、查看状态后截图的场景。
3. 用户自行截图：用户自己把 PNG/JPG/JPEG/WebP 图片放入 `软件著作权申请资料/用户截图/`，agent 只负责整理和引用。

如果用户明确说"现在不截图""先跳过截图""这次不截图"，也必须记录截图方式门禁，方法填 `skip`。跳过截图不阻塞正式资料生成，但操作手册中每个核心功能模块必须保留可见的截图预留文字，例如：`【截图预留：请在此处插入"项目管理"页面或操作结果截图。】`。不要使用 HTML 注释作为截图占位，因为正式 Word 中看不到。

用户选择后，先记录门禁（视频已完全覆盖时方法填 `video`；视频加其他方式补缺口时，方法填实际用来处理缺口的那种，并在 `--note` 里说明视频覆盖了哪些模块）：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/confirm_stage.py \
  --workdir 软件著作权申请资料 \
  --stage screenshot-method \
  --method <video|chrome-devtools|computer-use|user-supplied|skip> \
  --note "<用户选择>"
```

然后按用户选择检查当前能力并执行：

- 选择 video：不需要额外工具，按上面"如果存在"里描述的方式从 `操作视频/帧/` 中挑选、整理到 `截图/`；如果操作手册里仍有模块没被视频覆盖，回到上面三选一处理这部分缺口。
- 选择 Chrome DevTools MCP：先用工具发现能力检查当前环境是否有 `mcp__chrome_devtools__` 的 `list_pages`、`take_snapshot`、`take_screenshot`。可用时，先 `list_pages` 确认当前浏览器页面，再按页面/路由截图保存到 `软件著作权申请资料/截图/`；不可用时停止，告知用户需要重新选择截图方式或手动提供截图。
- 选择 Codex Computer Use：先用工具发现能力检查当前环境是否有 `mcp__computer_use__` 的 `get_app_state`、`click`、`press_key`。可用时，先 `get_app_state` 查看目标应用或浏览器当前状态，再按操作手册需要导航和截图；如果当前 Computer Use 只能返回会话内截图而不能直接保存图片文件，则说明限制，并让用户改选 Chrome DevTools MCP 或把截图放入 `用户截图/`。
- 选择用户自行截图：创建 `软件著作权申请资料/用户截图/`，提示用户把截图文件放入该目录；用户放入后运行下面的整理命令，把图片复制到 `软件著作权申请资料/截图/` 并生成 `截图清单.json`。
- 选择跳过截图：不运行截图工具，继续保留操作手册中的可见截图预留文字；在生成报告中说明用户选择暂不截图，正式操作手册已预留截图位置。

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/capture_screenshots.py \
  --manual-dir 软件著作权申请资料/用户截图 \
  --out-dir 软件著作权申请资料/截图
```

截图成功后，把截图引用补入 `草稿/操作手册.md`；截图失败或用户选择暂不提供截图时，继续生成带截图预留位的文字版，并在报告中说明"操作手册截图未生成或未插入，已保留截图预留位置"。

### 10. 用户确认 Markdown

生成 Word 前，必须让用户确认 `软件著作权申请资料/草稿/` 下的 Markdown。

重点检查：

- 软件名称和版本号是否一致
- 代码材料（前30页+后30页按完整文件边界对齐、合并为一份文档，页码连续，实际页数可能在 60 页上下小幅浮动）页眉的软件名称和版本号是否与 `申请表信息.md` 的"软件全称""版本号"一致
- 操作手册 Word 页眉是否与代码材料页眉一致，均使用 `申请表信息.md` 的"软件全称"和"版本号"
- `业务理解.md` 是否准确反映软件真实业务、行业和目标用户
- `申请表信息.md` 中"待用户确认"的字段是否已确认
- 代码材料是否只来自用户确认的完整文件
- 操作手册是否符合审核员阅读场景，普通读者是否能看懂模块用途和操作方式
- 操作手册每个章节是否有段落内容，核心模块是否写清模块用途、操作过程和结果反馈，是否避免过度技术化语言
- 截图是否正确；若用户跳过截图，正式操作手册是否保留可见截图预留位置

用户确认后，必须记录 `markdown` 门禁；未记录时不得生成正式 Word/TXT。

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/confirm_stage.py \
  --workdir 软件著作权申请资料 \
  --stage markdown \
  --note "<用户确认内容>"
```

### 11. 生成正式 Word/TXT

用户确认后运行：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/build_docx_from_md.py \
  --workdir 软件著作权申请资料 \
  --software-name "<软件全称>" \
  --version "<版本号>"
```

正式生成脚本必须重新读取 `草稿/申请表信息.md` 中已确认的"软件全称"和"版本号"，并用它们生成正式资料文件名、代码 Word 页眉和操作手册 Word 页眉。操作手册页眉必须与代码材料页眉格式一致：左侧为"软件全称 版本号"，右侧为"第 <页码> 页"。若命令参数 `--software-name` / `--version` 与申请表字段不同，以申请表字段为准，并在 `正式资料/生成报告.md` 中记录提示。

输出：

- `正式资料/申请表信息.txt`
- 代码达到或超过 60 页：
  - `正式资料/<软件全称>-代码(前30页+后30页).docx`（前后段按完整文件边界对齐、合并为一份文档，页码连续编号，实际总页数可能在 60 页上下小幅浮动，以保证不截断任何文件/函数为准）
- 代码不足 60 页：
  - `正式资料/<软件全称>-代码(全部).docx`
- `正式资料/<软件全称>_操作手册.docx`
- `正式资料/生成报告.md`

### 12. 三轮验证

至少执行三轮验证并修复发现的问题：

1. 文件完整性：目标 Word/TXT 是否存在且非空。
2. 代码真实性：抽样检查代码片段能回溯到项目源码。
3. 业务真实性：申请表和操作手册中的行业、目标用户、主要功能、操作流程能回溯到 `业务理解.md` 和项目文档。
4. 一致性和格式：软件名称、版本号、页数规则、申请表字段、操作手册标题和截图引用是否一致。

第 12 步实际执行分两步，两步都完成才算完成：

**第一步，跑程序化校验脚本**（覆盖上面 1、4 两条能自动判断的部分，以及命名一致性、页数、套话黑名单、截图尺寸、门禁记录等更细的检查项）：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/verify_materials.py --workdir 软件著作权申请资料
```

任何 `[FAIL]` 都必须修复后重新跑，直到全部 `[PASS]`/`[WARN]`；报告同时写入 `软件著作权申请资料/校验报告.md`。

**第二步，把上面 2、3 两条（代码真实性、业务真实性）以及截图匹配度、行业定位合理性、操作手册文风，委派给一个独立的验证 agent 逐项核实**，清单和具体做法见 [references/verification_checklist.md](references/verification_checklist.md) 第二部分。委派时给独立 agent 原始问题（例如"审计这份操作手册是否存在编造内容和截图错位"），不要给"验证我生成的材料对不对"，避免确认偏误；生成材料的 agent 不能是唯一的验证方。

其他可用命令：

```bash
python3 -m py_compile ${CLAUDE_SKILL_DIR}/scripts/*.py
```

打开生成的 `.docx` 文件人工检查排版、页眉和分页是否正常。如果 `环境检查.md` 显示 `python-docx` 不可用，说明正在走 `pandoc`/基础 OOXML 兜底路径。用户明确选择不安装 `python-docx` 并记录 `environment` 门禁后，继续生成 Markdown、TXT 和兜底 DOCX，并在报告中说明当前使用兜底路径。

### 13. 中国版权保护中心官网填报与签章页

用户要求继续提交时，读取 [references/ccopyright_submission_workflow.md](references/ccopyright_submission_workflow.md)，按已验证的 R11 页面顺序操作。这个阶段的目标不是重复制作材料，而是把已经确认的 `正式资料/申请表信息.txt`、代码 PDF 和操作手册 PDF 准确录入平台，并完成到用户授权的停止点。

关键边界：

- 登录、短信验证码、图形验证码等身份验证由用户完成；不得索取或保存密码。
- “保存至草稿箱”属于可恢复操作，可以在字段和材料回读无误后执行，并在用户中心确认条目状态为“暂存”。
- “确认填报”会把草稿推进到正式申请及签章流程。点击前必须让用户确认申请信息；后续出现任何声明、承诺或签署内容时，必须让申请人本人阅读并决定，agent 不得接受可能不实的声明。
- 确认填报后，主要工作是等待平台生成签章页；生成后由用户打印并签字或盖章，再上传扫描件。上传完成后回到用户中心核对平台显示的实际状态，不推测是否已经受理。
- 每个项目单独记录流水号、当前状态和材料文件路径，禁止复用其他项目的流水号或签章页。
- 所有版权中心过程记录、签章页、回执和状态证据统一写入 `软件著作权申请资料/提交记录/`，不得放入 `正式资料/`；命名和归档规则见输出目录规范。

## 何时询问用户

以下场景必须询问并停止，等待用户输入后再继续：

- 多个项目候选目录需要选择。
- 启动环境检查发现完整 DOCX 环境缺失时，询问用户是否安装完整环境。
- 定位项目后，需要先问用户是否有可提供的操作视频；有则优先用于业务理解、操作手册和截图三个环节。
- 业务理解草稿生成后，请用户确认软件用途、行业、目标用户、核心功能和申请口径。
- 软件全称、著作权人、日期、硬件/系统环境等登记字段需要确认。
- 代码文件候选清单生成后，需要用户确认或修改 `代码文件选择.json`。
- 操作手册截图前，需要确认操作视频是否已覆盖全部模块；未覆盖或没有视频时，需要用户在 Chrome DevTools MCP、Codex Computer Use、用户自行截图三种方式中选择一种；选择后再检查对应工具是否可用。
- 用户是否确认 Markdown 草稿并进入 Word 生成。
- 官网需要登录、验证码或其他身份验证时，由用户完成后再继续。
- 点击“确认填报”前，必须让用户确认最终申请信息；遇到声明、承诺、签名或盖章内容时，由申请人本人阅读和决定。
