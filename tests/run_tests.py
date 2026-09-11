#!/usr/bin/env python3
"""Reproducible integration tests for Script, Skill protocol, and Agent confirmation."""

from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader, PdfWriter


ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "skills" / "pdf-page-editor" / "scripts" / "pdf_editor.py"
AGENT = ROOT / "agent" / "agent_entry.py"
spec = importlib.util.spec_from_file_location("pdf_agent", AGENT)
agent_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_module)


def make_pdf(path: Path, pages: int) -> None:
    writer = PdfWriter()
    for index in range(pages):
        writer.add_blank_page(width=300 + index, height=400 + index)
    with path.open("wb") as stream:
        writer.write(stream)


def make_encrypted_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    writer.encrypt("secret")
    with path.open("wb") as stream:
        writer.write(stream)


class PdfEditorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name)
        make_pdf(self.work / "a.pdf", 5)
        make_pdf(self.work / "b.pdf", 2)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_editor(self, *args: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        command = [sys.executable, str(EDITOR), "--allowed-dir", str(self.work), "--log-dir", str(self.work / "logs"), *args]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertTrue(result.stdout.strip(), msg=f"stdout为空；stderr={result.stderr}")
        return result, json.loads(result.stdout)

    def test_01_extract_range(self) -> None:
        result, data = self.run_editor("extract", "--input", "a.pdf", "--pages", "2-4")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["pages_processed"], [2, 3, 4])
        self.assertEqual(len(PdfReader(data["output_file"]).pages), 3)

    def test_02_delete_first_page(self) -> None:
        result, data = self.run_editor("delete", "--input", "a.pdf", "--pages", "1")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["pages_deleted"], [1])
        self.assertEqual(len(PdfReader(data["output_file"]).pages), 4)

    def test_03_merge_with_subsets(self) -> None:
        result, data = self.run_editor("merge", "--inputs", "a.pdf", "b.pdf", "--page-specs", "1-2", "2", "--output", "joined.pdf")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["total_pages_output"], 3)

    def test_04_reorder_custom(self) -> None:
        result, data = self.run_editor("reorder", "--input", "b.pdf", "--order", "2,1")
        self.assertEqual(result.returncode, 0)
        reader = PdfReader(data["output_file"])
        self.assertEqual(float(reader.pages[0].mediabox.width), 301)

    def test_05_reverse(self) -> None:
        result, data = self.run_editor("reorder", "--input", "a.pdf", "--reverse", "--output", "reverse.pdf")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(data["pages_processed"], [5, 4, 3, 2, 1])

    def test_06_rotate_last_page(self) -> None:
        result, data = self.run_editor("rotate", "--input", "a.pdf", "--pages", "5", "--angle", "90")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(PdfReader(data["output_file"]).pages[4].rotation, 90)

    def test_07_single_page_delete_rejected(self) -> None:
        make_pdf(self.work / "one.pdf", 1)
        result, data = self.run_editor("delete", "--input", "one.pdf", "--pages", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(data["error_code"], "NO_PAGES_REMAIN")

    def test_08_page_out_of_range(self) -> None:
        result, data = self.run_editor("extract", "--input", "a.pdf", "--pages", "6")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(data["error_code"], "PAGE_OUT_OF_RANGE")

    def test_09_file_not_found(self) -> None:
        result, data = self.run_editor("extract", "--input", "missing.pdf", "--pages", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(data["error_code"], "FILE_NOT_FOUND")

    def test_10_non_pdf(self) -> None:
        (self.work / "note.txt").write_text("not a pdf", encoding="utf-8")
        result, data = self.run_editor("extract", "--input", "note.txt", "--pages", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(data["error_code"], "NOT_PDF")

    def test_11_encrypted_pdf(self) -> None:
        make_encrypted_pdf(self.work / "locked.pdf")
        result, data = self.run_editor("extract", "--input", "locked.pdf", "--pages", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(data["error_code"], "ENCRYPTED_PDF")

    def test_12_invalid_page_spec(self) -> None:
        result, data = self.run_editor("extract", "--input", "a.pdf", "--pages", "abc")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(data["error_code"], "INVALID_PAGE_SPEC")

    def test_13_path_escape_rejected(self) -> None:
        result, data = self.run_editor("extract", "--input", "../outside.pdf", "--pages", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(data["error_code"], "PATH_NOT_ALLOWED")

    def test_14_existing_output_not_overwritten(self) -> None:
        (self.work / "exists.pdf").write_bytes((self.work / "b.pdf").read_bytes())
        result, data = self.run_editor("extract", "--input", "a.pdf", "--pages", "1", "--output", "exists.pdf")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(data["error_code"], "OUTPUT_EXISTS")

    def test_15_merge_empty_list_is_json_error(self) -> None:
        result, data = self.run_editor("merge", "--output", "none.pdf")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(data["error_code"], "INVALID_ARGUMENT")

    def test_16_agent_preview_then_confirm(self) -> None:
        preview = subprocess.run([sys.executable, str(AGENT), "--allowed-dir", str(self.work), "--request", "把 a.pdf 的第2到4页单独提取出来，输出为 agent.pdf"], capture_output=True, text=True, encoding="utf-8")
        plan = json.loads(preview.stdout)
        self.assertEqual(plan["status"], "confirmation_required")
        self.assertFalse((self.work / "agent.pdf").exists())
        confirmed = subprocess.run([sys.executable, str(AGENT), "--confirm", plan["confirmation_token"]], capture_output=True, text=True, encoding="utf-8")
        data = json.loads(confirmed.stdout)
        self.assertEqual(data["status"], "success")
        self.assertTrue((self.work / "agent.pdf").is_file())
        repeated = subprocess.run([sys.executable, str(AGENT), "--confirm", plan["confirmation_token"]], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(json.loads(repeated.stdout)["error_code"], "PLAN_NOT_FOUND")

    def test_17_negative_overwrite(self):
        intent = agent_module.parse_intent("把 a.pdf 第1页提取出来，不要覆盖原文件")
        self.assertFalse(intent["overwrite"])

    def test_18_quoted_filename(self):
        intent = agent_module.parse_intent('把 "my report.pdf" 第1页提取出来，输出为 "my result.pdf"')
        self.assertEqual(intent["input"], "my report.pdf")
        self.assertEqual(intent["output"], "my result.pdf")

    def test_19_rotation_rejects_invalid_angle(self):
        for angle in (190, 900, 45):
            with self.subTest(angle=angle), self.assertRaises(agent_module.IntentError):
                agent_module.parse_intent(f"把 a.pdf 第1页旋转{angle}度")

    def test_20_output_is_not_merge_input(self):
        with self.assertRaises(agent_module.IntentError):
            agent_module.parse_intent("合并 a.pdf，输出为 result.pdf")

    def test_21_invalid_input_creates_no_output_directory(self):
        result, data = self.run_editor("extract", "--input", "a.pdf", "--pages", "999999999999", "--output", "new/result.pdf")
        self.assertEqual(data["error_code"], "PAGE_OUT_OF_RANGE")
        self.assertFalse((self.work / "new").exists())

    def test_22_old_temp_file_preserved(self):
        old_temp = self.work / ".a_extract.pdf.tmp"
        old_temp.write_bytes(b"user data")
        result, data = self.run_editor("extract", "--input", "a.pdf", "--pages", "1")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(old_temp.read_bytes(), b"user data")
        self.assertFalse(list(self.work.glob(".pdf-editor-*.tmp")))

    def test_23_logging_error_is_json(self):
        log_file = self.work / "not-directory"
        log_file.write_text("preserve", encoding="utf-8")
        result = subprocess.run([sys.executable, str(EDITOR), "--log-dir", str(log_file), "extract", "--input", "a.pdf", "--pages", "1"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(json.loads(result.stdout)["status"], "error")
        self.assertNotIn("Traceback", result.stderr)

    def test_24_agent_invalid_arguments_json(self):
        result = subprocess.run([sys.executable, str(AGENT)], capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["status"], "error")

    def test_25_reject_combined_operations(self):
        with self.assertRaises(agent_module.IntentError):
            agent_module.parse_intent("把 a.pdf 第1页删除后旋转90度")

    def test_26_input_changed_after_preview(self):
        preview = agent_module.create_plan("把 a.pdf 第1页提取出来", self.work)
        make_pdf(self.work / "a.pdf", 2)
        code, data = agent_module.confirm(preview["confirmation_token"])
        self.assertEqual(data["error_code"], "INPUT_CHANGED")
        self.assertNotEqual(code, 0)
        self.assertFalse((self.work / "a_extract.pdf").exists())


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PdfEditorTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = f"\n汇总：共{result.testsRun}项，通过{result.testsRun - len(result.failures) - len(result.errors)}项，失败{len(result.failures)}项，错误{len(result.errors)}项"
    print(summary)
    raise SystemExit(0 if result.wasSuccessful() else 1)
