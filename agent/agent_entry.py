#!/usr/bin/env python3
"""Lightweight Agent: Chinese natural language -> preview/confirmation -> PDF Skill CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import subprocess
import sys
import os
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EDITOR = PROJECT_ROOT / "skills" / "pdf-page-editor" / "scripts" / "pdf_editor.py"
PENDING_DIR = PROJECT_ROOT / ".agent_pending"
_spec = importlib.util.spec_from_file_location("pdf_editor_validation", EDITOR)
editor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(editor)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class IntentError(Exception):
    pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise IntentError(f"命令行参数错误：{message}")


def emit(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def extract_pdf_names(text: str) -> list[str]:
    quoted = re.findall(r"[\"'“‘]([^\"'”’]+?\.pdf)[\"'”’]", text, flags=re.I)
    unquoted = re.sub(r"[\"'“‘][^\"'”’]+?\.pdf[\"'”’]", " ", text, flags=re.I)
    plain = re.findall(r"(?<![\w.-])([^\s，,；;]+?\.pdf)", unquoted, flags=re.I)
    names: list[str] = []
    for item in quoted + plain:
        cleaned = item.strip("，,。.;；:：\"'“”‘’")
        if cleaned not in names:
            names.append(cleaned)
    return names


def chinese_number(value: str) -> int:
    value = value.strip()
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        return (digits.get(left, 1) * 10) + digits.get(right, 0)
    if value in digits:
        return digits[value]
    raise IntentError(f"无法识别页码：{value}")


def page_spec(text: str) -> str:
    num = r"[0-9零一二两三四五六七八九十]+"
    range_match = re.search(rf"第?({num})\s*(?:到|至|-|~)\s*第?({num})页?", text)
    if range_match:
        return f"{chinese_number(range_match.group(1))}-{chinese_number(range_match.group(2))}"
    list_match = re.search(rf"第?((?:{num})(?:\s*[,，、]\s*(?:{num}))+?)页", text)
    if list_match:
        return ",".join(str(chinese_number(part)) for part in re.split(r"[,，、]", list_match.group(1)))
    one_match = re.search(rf"第({num})页", text)
    if one_match:
        return str(chinese_number(one_match.group(1)))
    first_match = re.search(r"前\s*([0-9零一二两三四五六七八九十]+)\s*页", text)
    if first_match:
        return f"1-{chinese_number(first_match.group(1))}"
    raise IntentError("没有识别到页码，请使用“第2页”或“第2到4页”等表达")


def parse_intent(text: str) -> dict:
    text = re.sub(r"(?:请将|请把|将|把|这个|合并(?=[^\s]*?\.pdf))", lambda m: "合并 " if m.group() == "合并" else " ", text, count=1)
    text = text.replace("这个", " ")
    text = text.replace("删掉", "删除")
    operation_groups = (
        ("提取", "导出", "单独"), ("删除", "去掉", "移除"),
        ("合并", "拼接", "组合"), ("倒过来", "倒序", "逆序", "排序", "顺序", "重排"),
        ("旋转", "转动"), ("裁剪", "裁掉"),
    )
    if sum(any(word in text for word in group) for group in operation_groups) > 1:
        raise IntentError("一次只能执行一类操作，请将组合任务拆成单步")
    files = extract_pdf_names(text)
    output_match = re.search(r"(?:输出(?:为|到)?|保存(?:为|到)?)\s*(?:[\"“]([^\"”]+\.pdf)[\"”]|([^\s，,]+\.pdf))", text, re.I)
    output = (output_match.group(1) or output_match.group(2)) if output_match else None
    if output_match:
        files = extract_pdf_names(text[:output_match.start()] + " " + text[output_match.end():])
    overwrite = bool(re.search(r"覆盖(?:原文件|输入文件|现有文件)", text)) and not bool(
        re.search(r"(?:不|不要|禁止|不得|无需)\s*覆盖", text)
    )

    if any(word in text for word in ("合并", "拼接", "组合")):
        if len(files) < 2:
            raise IntentError("合并操作至少需要两个PDF文件")
        inputs = [name for name in files if name != output]
        if len(inputs) < 2:
            raise IntentError("合并操作至少需要两个输入PDF，输出文件不计入输入")
        specs = ["all"] * len(inputs)
        first_only = re.search(rf"{re.escape(inputs[0])}[^，。；;]*?前\s*([0-9零一二两三四五六七八九十]+)\s*页", text, re.I)
        if first_only:
            specs[0] = f"1-{chinese_number(first_only.group(1))}"
        return {"operation": "merge", "inputs": inputs, "page_specs": specs, "output": output or "merged.pdf", "overwrite": overwrite}

    if not files:
        raise IntentError("没有识别到PDF文件名，请在指令中包含xxx.pdf")
    source = files[0]
    if any(word in text for word in ("裁剪", "裁掉")):
        match = re.search(r"(?:四边|四周).*?(\d+(?:\.\d+)?)\s*(毫米|mm|点|pt)", text, re.I)
        if not match:
            raise IntentError("裁剪需要边距，例如“四边各裁掉10毫米”；若要另存整页，请说“提取”")
        margin = float(match.group(1)) * (72 / 25.4 if match.group(2).lower() in {"毫米", "mm"} else 1)
        operation = "crop"
        intent = {"operation": operation, "input": source, "pages": page_spec(text), "margins": [margin] * 4}
    elif any(word in text for word in ("提取", "导出", "单独")):
        operation = "extract"
        intent = {"operation": operation, "input": source, "pages": page_spec(text)}
    elif any(word in text for word in ("删除", "去掉", "移除")):
        operation = "delete"
        intent = {"operation": operation, "input": source, "pages": page_spec(text)}
    elif any(word in text for word in ("倒过来", "倒序", "逆序")):
        operation = "reorder"
        intent = {"operation": operation, "input": source, "reverse": True}
    elif any(word in text for word in ("排序", "顺序", "重排")):
        operation = "reorder"
        intent = {"operation": operation, "input": source, "order": page_spec(text)}
    elif any(word in text for word in ("旋转", "转动")):
        angle_match = re.search(r"(?:旋转|转动)[；;，,\s]*(?:顺时针)?\s*(90|180|270)(?!\d)\s*度?", text)
        if not angle_match:
            raise IntentError("旋转角度必须是90、180或270度")
        operation = "rotate"
        intent = {"operation": operation, "input": source, "pages": page_spec(text), "angle": int(angle_match.group(1))}
    else:
        raise IntentError("无法识别操作，请明确使用提取、删除、合并、排序、倒序或旋转")
    intent["output"] = output or str(Path(source).with_name(f"{Path(source).stem}_{operation}.pdf"))
    intent["overwrite"] = overwrite
    return intent


def describe(intent: dict) -> str:
    op = intent["operation"]
    if op == "merge":
        pieces = [f"{name}（页码：{spec}）" for name, spec in zip(intent["inputs"], intent["page_specs"])]
        action = f"合并{'、'.join(pieces)}"
    elif op == "extract":
        action = f"从{intent['input']}提取第{intent['pages']}页"
    elif op == "delete":
        action = f"从{intent['input']}删除第{intent['pages']}页"
    elif op == "reorder":
        action = f"将{intent['input']}页面倒序" if intent.get("reverse") else f"按{intent['order']}重排{intent['input']}"
    elif op == "crop":
        action = f"裁剪{intent['input']}第{intent['pages']}页，左/下/右/上边距为{intent['margins']}点（仅改变可见区域）"
    else:
        action = f"将{intent['input']}第{intent['pages']}页旋转{intent['angle']}度"
    overwrite = "；将覆盖现有文件" if intent.get("overwrite") else "；不会覆盖输入文件"
    return f"即将{action}，输出为{intent['output']}{overwrite}。"


def validate_intent(intent: dict, allowed_dir: Path) -> None:
    """Validate model proposals locally before a confirmation can be issued."""
    if not isinstance(intent, dict) or intent.get("operation") not in {"extract", "delete", "merge", "reorder", "rotate", "crop"}:
        raise IntentError("未知的PDF工具操作")
    allowed = {"operation", "input", "inputs", "output", "pages", "page_specs", "order", "reverse", "angle", "margins", "overwrite"}
    if set(intent) - allowed or type(intent.get("overwrite", False)) is not bool:
        raise IntentError("工具参数结构不合法")
    if not isinstance(intent.get("output"), str):
        raise IntentError("请指定输出PDF文件名")
    if "reverse" in intent and type(intent["reverse"]) is not bool:
        raise IntentError("倒序参数必须为布尔值")
    if intent.get("reverse") and "order" in intent:
        raise IntentError("不能同时指定倒序与自定义顺序")
    if any(not isinstance(intent[k], str) or intent[k].startswith("-") for k in ("pages", "order", "output") if k in intent):
        raise IntentError("页码、顺序和输出参数格式非法")
    names = intent.get("inputs") if intent["operation"] == "merge" else [intent.get("input")]
    if not isinstance(names, list) or not names or any(not isinstance(n, str) or not n or n.startswith("-") for n in names):
        raise IntentError("请指定有效的输入PDF文件名")
    if intent["operation"] == "merge" and len(names) < 2:
        raise IntentError("合并至少需要两个输入PDF")
    try:
        paths = [editor.resolve_allowed(n, allowed_dir, must_exist=True) for n in names]
        output = editor.safe_output(intent["output"], paths[0], intent["operation"], allowed_dir)
        editor.check_output(output, paths, intent.get("overwrite", False))
        for index, path in enumerate(paths):
            reader = editor.open_pdf(path)
            try:
                total = len(reader.pages)
                op = intent["operation"]
                if op == "merge":
                    specs = intent.get("page_specs")
                    if not isinstance(specs, list) or len(specs) != len(paths):
                        raise IntentError("合并页码必须与每个文件一一对应")
                    if specs[index] != "all":
                        editor.parse_pages(specs[index], total)
                elif op == "reorder":
                    if not intent.get("reverse"):
                        pages = editor.parse_pages(intent.get("order", ""), total)
                        if sorted(pages) != list(range(1, total + 1)):
                            raise IntentError("排序必须包含全部原页面且每页一次")
                else:
                    pages = editor.parse_pages(intent.get("pages", ""), total)
                    if op == "delete" and len(set(pages)) == total:
                        raise IntentError("不能删除全部页面")
                    if op == "rotate" and (type(intent.get("angle")) is not int or intent["angle"] not in (90, 180, 270)):
                        raise IntentError("旋转角度必须是90、180或270度")
                    if op == "crop":
                        margins = intent.get("margins")
                        if not isinstance(margins, list) or len(margins) != 4 or any(type(v) not in (int, float) or not editor.math.isfinite(v) or v < 0 for v in margins):
                            raise IntentError("需要四个非负边距，单位为点")
                        for n in set(pages):
                            page = reader.pages[n - 1]
                            width, height = float(page.cropbox.width), float(page.cropbox.height)
                            if page.rotation % 180:
                                width, height = height, width
                            if margins[0] + margins[2] >= width or margins[1] + margins[3] >= height:
                                raise IntentError("裁剪边距超过页面大小")
            finally:
                reader.close()
    except editor.EditorError as exc:
        raise IntentError(exc.message) from exc
    except (TypeError, ValueError, KeyError) as exc:
        raise IntentError("工具参数类型不正确，请重新描述需求") from exc


def create_plan(request: str, allowed_dir: Path, intent: dict | None = None) -> dict:
    allowed_dir = allowed_dir.resolve()
    intent = parse_intent(request) if intent is None else intent
    validate_intent(intent, allowed_dir)
    snapshots = {}
    for name in intent.get("inputs", [intent.get("input")]):
        path = (allowed_dir / name).resolve()
        if not path.is_relative_to(allowed_dir) or not path.is_file():
            raise IntentError("输入文件不存在或不在允许目录内")
        snapshots[str(path)] = file_hash(path)
    token = secrets.token_urlsafe(18)
    payload = {
        "intent": intent,
        "allowed_dir": str(allowed_dir),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "request_hash": hashlib.sha256(request.encode("utf-8")).hexdigest(),
        "input_hashes": snapshots,
    }
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    (PENDING_DIR / f"{token}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return {"status": "confirmation_required", "plan": describe(intent), "confirmation_token": token, "message": "请确认后再执行；令牌10分钟内有效。"}


def command_for(intent: dict, allowed_dir: Path) -> list[str]:
    command = [os.environ.get("PDF_TOOL_PYTHON", sys.executable), str(EDITOR), "--allowed-dir", str(allowed_dir), intent["operation"]]
    if intent["operation"] == "merge":
        command += ["--inputs", *intent["inputs"], "--page-specs", *intent["page_specs"], "--output", intent["output"]]
    else:
        command += ["--input", intent["input"], "--output", intent["output"]]
        if "pages" in intent:
            command += ["--pages", intent["pages"]]
        if intent.get("reverse"):
            command.append("--reverse")
        elif "order" in intent:
            command += ["--order", intent["order"]]
        if "angle" in intent:
            command += ["--angle", str(intent["angle"])]
        if "margins" in intent:
            command += ["--margins", *map(str, intent["margins"])]
    if intent.get("overwrite"):
        command.append("--overwrite")
    return command


def confirm(token: str) -> tuple[int, dict]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,30}", token):
        return 2, {"status": "error", "error_code": "INVALID_TOKEN", "error_message": "确认令牌格式非法"}
    path = PENDING_DIR / f"{token}.json"
    if not path.is_file():
        return 2, {"status": "error", "error_code": "PLAN_NOT_FOUND", "error_message": "确认计划不存在或已执行"}
    payload = json.loads(path.read_text(encoding="utf-8"))
    created = datetime.fromisoformat(payload["created_at"])
    if datetime.now(timezone.utc) - created > timedelta(minutes=10):
        path.unlink()
        return 2, {"status": "error", "error_code": "PLAN_EXPIRED", "error_message": "确认计划已过期，请重新提交指令"}
    # One-shot token: remove before execution to prevent accidental duplicate writes.
    claimed = path.with_suffix(".running")
    try:
        path.rename(claimed)
    except FileNotFoundError:
        return 2, {"status": "error", "error_code": "PLAN_NOT_FOUND", "error_message": "确认计划已被其他进程使用"}
    claimed.unlink()
    for filename, digest in payload.get("input_hashes", {}).items():
        source = Path(filename)
        if not source.is_file() or file_hash(source) != digest:
            return 2, {"status": "error", "error_code": "INPUT_CHANGED", "error_message": "预览后输入文件发生变化，请重新生成计划"}
    result = subprocess.run(command_for(payload["intent"], Path(payload["allowed_dir"])), capture_output=True, text=True, encoding="utf-8")
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    try:
        response = json.loads(result.stdout)
        if not isinstance(response, dict) or response.get("status") not in {"success", "error"}:
            raise ValueError("Invalid response schema")
    except (json.JSONDecodeError, ValueError):
        response = {"status": "error", "error_code": "SKILL_PROTOCOL_ERROR", "error_message": "PDF Skill未返回合法JSON"}
        return 3, response
    response["agent_message"] = response.get("message", response.get("error_message", "操作完成"))
    return result.returncode, response


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = JsonArgumentParser(description="AI PDF页面编排智能体")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--request", help="自然语言PDF操作指令")
    mode.add_argument("--confirm", help="确认令牌")
    parser.add_argument("--allowed-dir", default=".", help="允许操作PDF的目录")
    try:
        args = parser.parse_args()
        if args.request is not None:
            allowed_dir = Path(args.allowed_dir).expanduser().resolve()
            if not allowed_dir.is_dir():
                raise IntentError("允许目录不存在或不是目录")
            emit(create_plan(args.request, allowed_dir))
            return 0
        code, response = confirm(args.confirm)
        emit(response)
        return code
    except IntentError as exc:
        emit({"status": "error", "error_code": "INTENT_NOT_UNDERSTOOD", "error_message": str(exc)})
        return 2
    except Exception as exc:
        emit({"status": "error", "error_code": "AGENT_INTERNAL_ERROR", "error_message": f"智能体处理失败：{type(exc).__name__}"})
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
