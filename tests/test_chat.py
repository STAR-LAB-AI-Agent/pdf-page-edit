"""Dialog state and actual PDF output regressions; model mocks are explicitly labelled."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.chat import ChatSession, OllamaPlanner
from agent import agent_entry as core
from pypdf import PdfReader, PdfWriter


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name)
        for name in ("1.pdf", "2.pdf"):
            writer = PdfWriter()
            for i in range(3):
                writer.add_blank_page(width=300+i, height=400+i)
            writer.write(self.work / name)
        self.session = ChatSession(self.work)

    def tearDown(self):
        self.session.cancel()
        self.temp.cleanup()

    def test_extract_chinese_no_spaces(self):
        self.assertIn("确认", self.session.respond("请将这个1.pdf第2到3页提取出来"))
        self.assertFalse((self.work / "1_extract.pdf").exists())
        self.session.respond("确认")
        self.assertEqual(len(PdfReader(self.work / "1_extract.pdf").pages), 2)

    def test_clarify_pages(self):
        self.assertIn("页码", self.session.respond("请将1.pdf提取出来"))
        self.assertIn("确认", self.session.respond("第2页"))
        self.session.respond("确认")
        self.assertEqual(len(PdfReader(self.work / "1_extract.pdf").pages), 1)

        self.assertIn("角度", self.session.respond("将1.pdf第2页旋转"))
        self.assertIn("确认", self.session.respond("90度"))
        self.session.respond("确认")
        self.assertEqual(PdfReader(self.work / "1_rotate.pdf").pages[1].rotation, 90)

    def test_cancel_invalidates_token(self):
        self.session.respond("将1.pdf第1页旋转90度")
        token = self.session.pending["confirmation_token"]
        self.session.respond("取消")
        self.assertNotEqual(core.confirm(token)[0], 0)
        self.assertFalse((self.work / "1_rotate.pdf").exists())

    def test_followup_uses_result(self):
        self.session.respond("提取 1.pdf 第2页")
        self.session.respond("确认")
        self.session.respond("把它第1页旋转90度")
        self.session.respond("确认")
        self.assertEqual(PdfReader(self.work / "1_extract_rotate.pdf").pages[0].rotation, 90)

    def test_crop_ambiguity_extract(self):
        self.assertIn("整页", self.session.respond("将1.pdf第2页裁剪"))
        self.session.respond("提取整页")
        self.session.respond("确认")
        self.assertTrue((self.work / "1_extract.pdf").exists())

    def test_crop_margins(self):
        self.session.respond("将1.pdf第2页四边各裁掉10点")
        self.session.respond("确认")
        reader = PdfReader(self.work / "1_crop.pdf")
        self.assertEqual(float(reader.pages[1].cropbox.width), 281)
        self.assertEqual(float(reader.pages[0].cropbox.width), 300)

    def test_invalid_crop_rejected_before_preview(self):
        self.session.respond("将1.pdf第2页四边各裁掉999毫米")
        self.assertIsNone(self.session.pending)
        self.assertFalse((self.work / "1_crop.pdf").exists())

    def test_merge_no_spaces(self):
        self.session.respond("合并1.pdf和2.pdf，输出为合并.pdf")
        self.session.respond("确认")
        self.assertEqual(len(PdfReader(self.work / "合并.pdf").pages), 6)

    def test_new_request_invalidates_old(self):
        self.session.respond("提取 1.pdf 第1页")
        token = self.session.pending["confirmation_token"]
        self.session.respond("将1.pdf倒序")
        self.assertNotEqual(core.confirm(token)[0], 0)
        self.session.respond("确认")
        self.assertEqual(float(PdfReader(self.work / "1_reorder.pdf").pages[0].mediabox.width), 302)

    def test_out_of_range_no_preview(self):
        self.session.respond("提取 1.pdf 第99页")
        self.assertIsNone(self.session.pending)

    def test_model_proposal_real_tool(self):
        planner = OllamaPlanner("mock-only")
        self.session.planner = planner
        with patch.object(planner, "plan", return_value={"intent": {"operation":"delete", "input":"1.pdf", "pages":"1", "output":"model.pdf"}}):
            self.session.respond("帮我去掉封面")
            self.assertFalse((self.work / "model.pdf").exists())
            self.session.respond("确认")
        self.assertEqual(len(PdfReader(self.work / "model.pdf").pages), 2)

    def test_model_cannot_escape_or_overwrite(self):
        planner = OllamaPlanner("mock-only")
        self.session.planner = planner
        for output in ("../escape.pdf", "1.pdf"):
            self.session.cancel()
            with patch.object(planner, "plan", return_value={"intent": {"operation":"extract", "input":"1.pdf", "pages":"1", "output":output, "overwrite":True}}):
                self.session.respond("提取第一页")
            self.assertIsNone(self.session.pending)
        self.assertEqual(len(PdfReader(self.work / "1.pdf").pages), 3)

    def test_model_transport_bounded(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({"message":{"content":'{"question":"请提供页码"}'}}).encode()
        opener = MagicMock()
        opener.open.return_value = response
        planner = OllamaPlanner("mock-only")
        with patch("urllib.request.build_opener", return_value=opener):
            self.assertIn("question", planner.plan("提取1.pdf", None))
        request = opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(len(payload["messages"]), 2)
        self.assertEqual(planner.calls, 1)
        self.assertFalse(payload["stream"])

    def test_confirm_does_not_call_model(self):
        planner = OllamaPlanner("mock-only")
        self.session.planner = planner
        with patch.object(planner, "plan") as call:
            self.session.respond("确认")
            self.session.respond("帮助")
            self.session.respond("状态")
            call.assert_not_called()

    def test_console_end_to_end(self):
        result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "chat.py"), "--demo", "--allowed-dir", str(self.work)], input="将1.pdf第2页旋转90度\n确认\n退出\n", encoding="utf-8", capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(PdfReader(self.work / "1_rotate.pdf").pages[1].rotation, 90)


if __name__ == "__main__":
    unittest.main(verbosity=2)
