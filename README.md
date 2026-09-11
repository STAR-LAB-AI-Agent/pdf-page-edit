# AI PDF 页面编排

2026年9月6日对话版：安装依赖后双击 **启动对话.cmd**，或运行一次 `python -X utf8 chat.py`，随后直接输入中文。新增连续对话、追问、Ollama本地模型接口和区域裁剪。详细操作、配置和任务书对照见 [对话版使用与验收说明](docs/对话版验收说明.md)。已有Word文件是历史快照，尚需同步。

预览记录输入SHA-256，确认前输入发生变化时须重新预览。带空格文件名使用双引号；每条自然语言指令只执行一类操作。输出先写随机临时文件并验证页数和旋转属性，再发布；不覆盖模式需要文件系统支持硬链接，不支持时返回错误，允许覆盖时使用原子替换。本工具适用于可信的本机工作目录。

《智能体开发实战》任务书 11 的完整实验项目。用户用中文描述页面操作，轻量 Agent 将意图整理成可确认计划；用户确认后，Agent 调用 Skill 的确定性 CLI，由 `pypdf` 完成页面读写并返回精简 JSON。

```text
自然语言 -> Agent（意图解析/确认） -> SKILL.md -> pdf_editor.py -> pypdf -> JSON -> Agent回答
```

项目使用自定义轻量编排器，无需nanobot或MCP。对话入口提供Ollama模型模式和明确标注的规则演示模式。模型读取实际SKILL.md后提出参数，Python校验并预览，用户单独确认才执行。旧版纯规则入口不等同于大模型智能体。本机尚未配置真实模型，模型接口模拟测试不能替代真实模型验收。

## 功能与场景

- `extract`：提取 `1,3-5` 等页码为新 PDF。
- `delete`：删除指定页，拒绝生成零页 PDF。
- `merge`：合并多个 PDF，可分别指定 `all` 或页码范围。
- `reorder`：自定义完整页序，或用 `--reverse` 倒序。
- `rotate`：将指定页顺时针旋转 90/180/270 度。
- `crop`：按左/下/右/上边距裁剪可见区域，不永久删除隐藏内容。
- 安全：目录白名单、默认禁止覆盖、写前确认、一次性确认令牌、原子写入、脱敏日志和结构化错误。

## 环境与安装

要求 Python 3.10+。核心依赖固定为 `pypdf==6.10.0`，其许可证为 BSD-3-Clause。

开源来源：[pypdf源码](https://github.com/py-pdf/pypdf/tree/6.10.0)、[许可证](https://github.com/py-pdf/pypdf/blob/6.10.0/LICENSE)。实际使用PdfReader读取、PdfWriter.add_page/write组装输出、PageObject.rotate旋转和cropbox裁剪；安装包保留其许可证。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 单次调用接口（连续对话请使用chat.py）

把待处理 PDF 放到一个工作目录。第一步只生成计划，不写 PDF：

```powershell
python agent/agent_entry.py --allowed-dir "D:\pdf-work" --request "把 report.pdf 的第2到4页单独提取出来"
```

返回示例：

```json
{"status":"confirmation_required","plan":"即将从report.pdf提取第2-4页，输出为report_extract.pdf；不会覆盖输入文件。","confirmation_token":"...","message":"请确认后再执行；令牌10分钟内有效。"}
```

核对计划后，用原样返回的令牌确认：

```powershell
python agent/agent_entry.py --confirm "上一步的confirmation_token"
```

令牌只能使用一次，10 分钟过期。提取、旋转等所有写操作都执行同一确认流程；若自然语言明确要求覆盖，计划会突出提示覆盖风险，但底层仍须显式传入覆盖参数。

支持的典型指令：

```text
把 report.pdf 的第2到4页单独提取出来
把 report.pdf 的第1页删掉
把 a.pdf 和 b.pdf 合并成一个文件，a.pdf只要前3页，输出为 result.pdf
把 report.pdf 的页面顺序倒过来
把 report.pdf 第2页旋转90度
```

## 直接使用 Skill CLI

先设置允许目录，再使用下面的命令形式。页码从 1 开始。

```powershell
python skills/pdf-page-editor/scripts/pdf_editor.py --allowed-dir "D:\pdf-work" extract --input report.pdf --pages 2-4
python skills/pdf-page-editor/scripts/pdf_editor.py --allowed-dir "D:\pdf-work" delete --input report.pdf --pages 1
python skills/pdf-page-editor/scripts/pdf_editor.py --allowed-dir "D:\pdf-work" merge --inputs a.pdf b.pdf --page-specs 1-3 all --output merged.pdf
python skills/pdf-page-editor/scripts/pdf_editor.py --allowed-dir "D:\pdf-work" reorder --input report.pdf --reverse
python skills/pdf-page-editor/scripts/pdf_editor.py --allowed-dir "D:\pdf-work" rotate --input report.pdf --pages 2 --angle 90
```

CLI 的 stdout 永远只包含一个 JSON 对象；诊断信息写入 stderr。成功退出码为 0，失败为非 0。默认输出名为 `原名_操作.pdf`，已存在的输出会被拒绝。直接调用 CLI 时，`--overwrite` 只表示底层技术许可；面向用户的应用必须仍先完成确认。

Skill 的完整触发条件、调用契约和错误码见 `skills/pdf-page-editor/SKILL.md`。

## 测试

```powershell
python tests/run_tests.py
python tests/test_chat.py
```

原26项测试加新增15项对话测试，自动生成临时PDF并清理。新增部分覆盖追问、取消、连续操作、裁剪、控制台子进程和模型接口模拟；模型模拟不代表真实模型理解能力已验收。

## 日志与隐私

运行日志默认写到 `logs/operations.log`，每行是一个 JSON 记录。日志只保存 UTC 时间、操作名、输入/输出的文件名、状态和错误码；不保存目录、自然语言原文、PDF 内容、密码、令牌或环境变量。`logs/example.log` 是脱敏示例，真实运行日志已被 `.gitignore` 忽略。

## 项目结构

```text
README.md
requirements.txt
agent/agent_entry.py
skills/pdf-page-editor/SKILL.md
skills/pdf-page-editor/scripts/pdf_editor.py
tests/run_tests.py
tests/test_cases.md
tests/fixtures/.gitkeep
logs/.gitkeep
logs/example.log
docs/实验报告.md
docs/每日实习记录.md
docs/demo_script.md
```

## 已知限制

- 不编辑页面中的文字、图片、批注、书签或表单，也不做 OCR、压缩与解密。
- 加密 PDF 无论是否为空密码都会拒绝，避免含糊的授权边界。
- 规则式中文解析面向文档中的常见句式，不等同于开放域语言模型；复杂指令应拆成单步或直接使用 CLI。
- 自定义重排要求每个原页面恰好出现一次，防止无意遗漏或复制页面。
- 合并自然语言目前仅识别第一个输入的“前 N 页”；复杂的逐文件页码应直接用 CLI 的 `--page-specs`。
- `pypdf` 处理的是 PDF 对象结构；对异常生成器产生的极端 PDF，视觉结果仍建议人工抽查。

## 许可证

本课程项目代码可按 MIT License 使用；第三方 `pypdf` 遵循其 BSD-3-Clause License。本仓库不捆绑第三方二进制文件或有版权的样例文档。
