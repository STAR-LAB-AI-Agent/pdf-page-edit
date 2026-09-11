---
name: pdf-page-editor
description: 当用户需要提取、删除、合并、排序、倒序、旋转或裁剪PDF页面时使用。缺少参数先追问；所有写入先预览并确认，核心操作由pypdf完成。
---

# PDF Page Editor Skill

## 触发场景

- 从一个 PDF 提取指定页或页码范围。
- 删除指定页但保留原文件。
- 合并多个 PDF，并为每个输入选择全部或部分页面。
- 自定义重排全部页面，或将页面倒序。
- 将指定页面顺时针旋转 90、180 或 270 度。
- 裁剪页面可见区域，明确裁掉的边距；“裁剪”不明确时先区分提取整页与裁掉边缘。

不要用于编辑页面文字、OCR、压缩、签名、解密或表单填写。

## 安全调用步骤

1. 从用户话语中确认操作、输入文件、页码、角度和输出文件。
2. 限定 `--allowed-dir`；输入与输出都必须位于该目录中。
3. 控制台 `python chat.py` 在模型模式下读取本文件，将模型提议交给 `create_plan` 本地校验并展示。规则演示或外部调用也可使用 `agent/agent_entry.py --allowed-dir DIR --request "用户原话"`。
4. 控制台只有收到用户单独回复“确认”才调用 `confirm`；外部调用使用 `agent/agent_entry.py --confirm TOKEN`。模型生成的确认不算用户授权。
5. 解析 stdout 的单个 JSON 对象。stderr 仅用于诊断，不进入结构化响应。
6. 默认输出新文件；仅在用户明确要求覆盖且再次确认后传递 `--overwrite`。

## Script 参数

```text
pdf_editor.py [--allowed-dir DIR] [--log-dir DIR] extract --input A --pages 2-4 [--output B]
pdf_editor.py [--allowed-dir DIR] delete  --input A --pages 1   [--output B]
pdf_editor.py [--allowed-dir DIR] merge   --inputs A B --page-specs 1-3 all --output C
pdf_editor.py [--allowed-dir DIR] reorder --input A (--order 3,1,2 | --reverse) [--output B]
pdf_editor.py [--allowed-dir DIR] rotate  --input A --pages 2 --angle 90 [--output B]
pdf_editor.py [--allowed-dir DIR] crop --input A --pages 2 --margins 10 10 10 10 [--output B]
```

页码均从 1 开始。范围格式为 `1,3-5`。`merge --page-specs` 必须与输入文件一一对应，`all` 表示全部页面。

裁剪边距顺序为左、下、右、上，单位点（72点=1英寸，毫米乘72/25.4）。相对于当前可见页面，先归一化已有旋转；裁剪不得得到零宽或零高。裁剪仅隐藏区域，不用于永久删除敏感信息。

控制台一轮一类操作；缺少文件、页码、角度或边距时追问。默认新文件输出；控制台禁止覆盖已有文件，请另选输出名。模型只返回结构化参数，不生成Shell命令，不读取PDF正文，不猜测执行成功。执行结果由CLI返回后再展示。

## 结果格式

成功时 stdout：

```json
{"status":"success","operation":"extract","input_file":"report.pdf","output_file":".../report_extract.pdf","pages_processed":[2,3,4],"total_pages_output":3,"message":"成功提取页面：2,3,4"}
```

失败时 stdout：

```json
{"status":"error","operation":"extract","error_code":"PAGE_OUT_OF_RANGE","error_message":"页码6超出文档范围（共5页）"}
```

退出码 0 表示成功，非 0 表示失败。常见错误码包括 `FILE_NOT_FOUND`、`NOT_PDF`、`INVALID_PDF`、`ENCRYPTED_PDF`、`INVALID_PAGE_SPEC`、`PAGE_OUT_OF_RANGE`、`PATH_NOT_ALLOWED`、`OUTPUT_EXISTS` 和 `OVERWRITE_FORBIDDEN`。

## 自然语言示例

- “把 report.pdf 的第2到4页单独提取出来”
- “把 report.pdf 的第1页删掉”
- “把 a.pdf 和 b.pdf 合并成一个文件，a.pdf只要前3页，输出为 result.pdf”
- “把 report.pdf 的页面顺序倒过来”
- “把 report.pdf 第2页旋转90度”
