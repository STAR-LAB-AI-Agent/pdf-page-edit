#!/usr/bin/env python3
"""Deterministic PDF page operations with a JSON-only stdout contract."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError


class EditorError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise EditorError("INVALID_ARGUMENT", f"命令行参数错误：{message}")


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def fail(operation: str, code: str, message: str, exit_code: int = 2) -> int:
    print(f"[{code}] {message}", file=sys.stderr)
    emit({"status": "error", "operation": operation, "error_code": code, "error_message": message})
    return exit_code


def resolve_allowed(path_text: str, allowed_dir: Path, *, must_exist: bool = False) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = allowed_dir / path
    path = path.resolve(strict=False)
    try:
        path.relative_to(allowed_dir)
    except ValueError as exc:
        raise EditorError("PATH_NOT_ALLOWED", f"路径不在允许目录内：{path.name}") from exc
    if must_exist and not path.is_file():
        raise EditorError("FILE_NOT_FOUND", f"PDF文件不存在：{path.name}")
    return path


def open_pdf(path: Path) -> PdfReader:
    if path.suffix.lower() != ".pdf":
        raise EditorError("NOT_PDF", f"输入文件不是PDF：{path.name}")
    if path.stat().st_size == 0:
        raise EditorError("EMPTY_FILE", f"输入文件为空：{path.name}")
    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            raise EditorError("ENCRYPTED_PDF", f"不支持加密PDF：{path.name}")
        if len(reader.pages) == 0:
            raise EditorError("EMPTY_PDF", f"PDF不包含页面：{path.name}")
        return reader
    except EditorError:
        raise
    except (PdfReadError, OSError, ValueError, TypeError) as exc:
        raise EditorError("INVALID_PDF", f"PDF损坏或无法读取：{path.name}") from exc


def parse_pages(spec: str, total: int) -> list[int]:
    if not spec or not spec.strip():
        raise EditorError("INVALID_PAGE_SPEC", "页码不能为空")
    result: list[int] = []
    for raw_part in spec.split(","):
        part = raw_part.strip()
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise EditorError("INVALID_PAGE_SPEC", f"页码格式非法：{part or spec}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < 1 or start > end:
            raise EditorError("INVALID_PAGE_SPEC", f"页码范围非法：{part}")
        if end > total:
            raise EditorError("PAGE_OUT_OF_RANGE", f"页码{end}超出文档范围（共{total}页）")
        result.extend(range(start, end + 1))
    return result


def safe_output(output_text: str | None, source: Path, operation: str, allowed_dir: Path) -> Path:
    output = resolve_allowed(output_text, allowed_dir) if output_text else source.with_name(f"{source.stem}_{operation}.pdf")
    if output.suffix.lower() != ".pdf":
        raise EditorError("INVALID_OUTPUT", "输出文件扩展名必须是.pdf")
    return output


def check_output(output: Path, inputs: Iterable[Path], overwrite: bool) -> None:
    resolved_inputs = {item.resolve() for item in inputs}
    if output.resolve() in resolved_inputs and not overwrite:
        raise EditorError("OVERWRITE_FORBIDDEN", "默认禁止覆盖输入文件；如确需覆盖，请显式使用--overwrite")
    if output.exists() and not overwrite:
        raise EditorError("OUTPUT_EXISTS", f"输出文件已存在：{output.name}")


def write_pdf(writer: PdfWriter, output: Path, overwrite: bool = False) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".pdf-editor-", suffix=".tmp", dir=output.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            writer.write(stream)
        with temp.open("rb") as stream:
            verified = PdfReader(stream)
            if len(verified.pages) != len(writer.pages) or any(
                actual.rotation != expected.rotation or actual.cropbox != expected.cropbox
                for actual, expected in zip(verified.pages, writer.pages)
            ):
                raise EditorError("OUTPUT_VERIFICATION_FAILED", "输出页数或旋转属性校验失败")
        if overwrite:
            temp.replace(output)
        else:
            # Exclusive publication: a concurrently created target must survive.
            try:
                os.link(temp, output)
            except FileExistsError as exc:
                raise EditorError("OUTPUT_EXISTS", f"输出文件已存在：{output.name}") from exc
    finally:
        if temp.exists():
            temp.unlink()


def configure_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("pdf_editor")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(log_dir / "operations.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def log_result(logger: logging.Logger, operation: str, inputs: list[Path], output: Path | None, status: str, error_code: str | None = None) -> None:
    # Only basenames and fixed fields are logged; free-form arguments and secrets are excluded.
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation": operation,
        "input_files": [p.name for p in inputs],
        "output_file": output.name if output else None,
        "status": status,
    }
    if error_code:
        record["error_code"] = error_code
    logger.info(json.dumps(record, ensure_ascii=False, separators=(",", ":")))


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description="使用pypdf执行安全、确定性的PDF页面操作")
    parser.add_argument("--allowed-dir", default=".", help="允许读写PDF的目录边界，默认当前目录")
    parser.add_argument("--log-dir", help="日志目录，默认项目logs目录")
    subparsers = parser.add_subparsers(dest="operation", required=True)

    def common(name: str, needs_pages: bool = False) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name)
        sub.add_argument("--input", required=True)
        sub.add_argument("--output")
        if needs_pages:
            sub.add_argument("--pages", required=True, help="1-based页码，如1,3-5")
        sub.add_argument("--overwrite", action="store_true")
        return sub

    common("extract", True)
    common("delete", True)
    reorder = common("reorder")
    reorder_group = reorder.add_mutually_exclusive_group(required=True)
    reorder_group.add_argument("--order", help="1-based顺序，如3,1,2")
    reorder_group.add_argument("--reverse", action="store_true")
    rotate = common("rotate", True)
    rotate.add_argument("--angle", required=True, type=int, choices=(90, 180, 270))
    crop = common("crop", True)
    crop.add_argument("--margins", nargs=4, type=float, required=True, metavar=("LEFT", "BOTTOM", "RIGHT", "TOP"), help="从当前可见边缘裁掉的点数，顺序左下右上，72点=1英寸")
    merge = subparsers.add_parser("merge")
    merge.add_argument("--inputs", nargs="+", required=True)
    merge.add_argument("--page-specs", nargs="*", help="与inputs一一对应；all表示全部页面")
    merge.add_argument("--output", required=True)
    merge.add_argument("--overwrite", action="store_true")
    return parser


def execute(args: argparse.Namespace, allowed_dir: Path) -> tuple[dict, list[Path], Path]:
    operation = args.operation
    writer = PdfWriter()

    if operation == "merge":
        if not args.inputs:
            raise EditorError("EMPTY_INPUT_LIST", "合并文件列表不能为空")
        inputs = [resolve_allowed(item, allowed_dir, must_exist=True) for item in args.inputs]
        output = resolve_allowed(args.output, allowed_dir)
        if output.suffix.lower() != ".pdf":
            raise EditorError("INVALID_OUTPUT", "输出文件扩展名必须是.pdf")
        check_output(output, inputs, args.overwrite)
        specs = args.page_specs or ["all"] * len(inputs)
        if len(specs) != len(inputs):
            raise EditorError("PAGE_SPEC_COUNT_MISMATCH", "page-specs数量必须与inputs数量一致")
        processed: dict[str, list[int]] = {}
        for path, spec in zip(inputs, specs):
            reader = open_pdf(path)
            pages = list(range(1, len(reader.pages) + 1)) if spec.lower() in {"all", "*"} else parse_pages(spec, len(reader.pages))
            for page in pages:
                writer.add_page(reader.pages[page - 1])
            processed[path.name] = pages
        write_pdf(writer, output, args.overwrite)
        return ({"status": "success", "operation": operation, "input_files": [p.name for p in inputs], "output_file": str(output), "pages_processed": processed, "total_pages_output": len(writer.pages), "message": f"成功合并{len(inputs)}个PDF文件"}, inputs, output)

    source = resolve_allowed(args.input, allowed_dir, must_exist=True)
    reader = open_pdf(source)
    total = len(reader.pages)
    output = safe_output(args.output, source, operation, allowed_dir)
    check_output(output, [source], args.overwrite)

    if operation == "extract":
        selected = parse_pages(args.pages, total)
        for page in selected:
            writer.add_page(reader.pages[page - 1])
        message = f"成功提取页面：{','.join(map(str, selected))}"
    elif operation == "delete":
        removed = parse_pages(args.pages, total)
        removed_set = set(removed)
        selected = [page for page in range(1, total + 1) if page not in removed_set]
        if not selected:
            raise EditorError("NO_PAGES_REMAIN", "删除后PDF将不包含任何页面，操作已拒绝")
        for page in selected:
            writer.add_page(reader.pages[page - 1])
        message = f"成功删除页面：{','.join(map(str, removed))}"
    elif operation == "reorder":
        selected = list(range(total, 0, -1)) if args.reverse else parse_pages(args.order, total)
        if len(selected) != total or sorted(selected) != list(range(1, total + 1)):
            raise EditorError("INVALID_PAGE_ORDER", "排序必须且只能包含原PDF的每一页一次")
        for page in selected:
            writer.add_page(reader.pages[page - 1])
        message = "成功重新排列页面"
    elif operation == "crop":
        cropped = parse_pages(args.pages, total)
        cropped_set = set(cropped)
        if any(not math.isfinite(x) or x < 0 for x in args.margins):
            raise EditorError("INVALID_CROP", "裁剪边距必须是非负有限数")
        left, bottom, right, top = args.margins
        selected = list(range(1, total + 1))
        for number in selected:
            page_obj = reader.pages[number - 1]
            if number in cropped_set:
                if page_obj.rotation:
                    page_obj.transfer_rotation_to_content()
                box = page_obj.cropbox
                if left + right >= float(box.width) or bottom + top >= float(box.height):
                    raise EditorError("INVALID_CROP", "裁剪后必须保留正宽度和高度")
                box.lower_left = (float(box.left) + left, float(box.bottom) + bottom)
                box.upper_right = (float(box.right) - right, float(box.top) - top)
            writer.add_page(page_obj)
        message = "成功裁剪指定页的可见区域（不永久删除隐藏内容）"
    elif operation == "rotate":
        rotated = parse_pages(args.pages, total)
        rotated_set = set(rotated)
        selected = list(range(1, total + 1))
        for page in selected:
            page_obj = reader.pages[page - 1]
            if page in rotated_set:
                page_obj.rotate(args.angle)
            writer.add_page(page_obj)
        message = f"成功将页面{','.join(map(str, rotated))}旋转{args.angle}度"
    else:
        raise EditorError("UNKNOWN_OPERATION", f"不支持的操作：{operation}")

    write_pdf(writer, output, args.overwrite)
    details = {"pages_processed": selected}
    if operation == "delete":
        details["pages_deleted"] = removed
    if operation == "rotate":
        details["pages_rotated"] = rotated
        details["angle"] = args.angle
    if operation == "crop":
        details["pages_cropped"] = cropped
        details["margins"] = args.margins
    return ({"status": "success", "operation": operation, "input_file": source.name, "output_file": str(output), **details, "total_pages_output": len(writer.pages), "message": message}, [source], output)


def main() -> int:
    # Keep the machine-readable contract stable on Windows consoles using GBK.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = build_parser()
    try:
        args = parser.parse_args()
    except EditorError as exc:
        operation = next((item for item in ("extract", "delete", "merge", "reorder", "rotate") if item in sys.argv[1:]), "unknown")
        return fail(operation, exc.code, exc.message)
    operation = args.operation or "unknown"
    project_root = Path(__file__).resolve().parents[3]
    logger = None
    inputs: list[Path] = []
    output: Path | None = None
    try:
        allowed_dir = Path(args.allowed_dir).expanduser().resolve()
        log_dir = Path(args.log_dir).expanduser().resolve() if args.log_dir else project_root / "logs"
        logger = configure_logging(log_dir)
        if not allowed_dir.is_dir():
            raise EditorError("INVALID_ALLOWED_DIR", "允许目录不存在或不是目录")
        result, inputs, output = execute(args, allowed_dir)
        log_result(logger, operation, inputs, output, "success")
        emit(result)
        return 0
    except EditorError as exc:
        if logger:
            log_result(logger, operation, inputs, output, "error", exc.code)
        return fail(operation, exc.code, exc.message)
    except Exception as exc:  # Last-resort boundary: CLI must never expose a traceback.
        if logger:
            log_result(logger, operation, inputs, output, "error", "INTERNAL_ERROR")
        return fail(operation, "INTERNAL_ERROR", f"操作失败：{type(exc).__name__}", 3)


if __name__ == "__main__":
    raise SystemExit(main())
