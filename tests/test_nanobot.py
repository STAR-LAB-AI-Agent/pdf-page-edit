"""Nanobot AgentLoop integration with an explicitly simulated provider."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from loguru import logger
from pypdf import PdfReader, PdfWriter
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from agent.chat import ChatSession
from agent.nanobot_planner import NanobotPlanner


class FakeProvider(LLMProvider):
    def __init__(self, name="pdf_proposal", args=None):
        super().__init__()
        self.name, self.args, self.count = name, args, 0

    async def chat(self, messages, tools=None, model=None, **kwargs):
        self.count += 1
        if self.count == 1:
            return LLMResponse(content=None, tool_calls=[ToolCallRequest("test", self.name, self.args)])
        return LLMResponse(content="请核对操作预览。")

    def get_default_model(self):
        return "mock-only"


class NanobotTests(unittest.TestCase):
    def setUp(self):
        logger.remove()
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        writer = PdfWriter()
        for i in range(3):
            writer.add_blank_page(width=300+i, height=400)
        writer.write(self.directory / "1.pdf")
        os.environ["PDF_TOOL_PYTHON"] = str(ROOT / ".venv-pdf/Scripts/python.exe")

    def tearDown(self):
        os.environ.pop("PDF_TOOL_PYTHON", None)
        self.temp.cleanup()

    def planner(self, name, args):
        with patch("agent.nanobot_planner.make_provider", return_value=FakeProvider(name, args)):
            return NanobotPlanner(ROOT / "nanobot-local.json")

    def test_real_loop_proposes_then_confirm_executes(self):
        planner = self.planner("pdf_proposal", {"operation":"extract", "input":"1.pdf", "pages":"2-3", "output":"result.pdf"})
        session = ChatSession(self.directory, planner)
        try:
            self.assertEqual(planner.agent.tool_names, ["pdf_proposal"])
            session.respond("提取1.pdf第2到3页")
            self.assertIsNotNone(session.pending)
            self.assertFalse((self.directory / "result.pdf").exists())
            session.respond("确认")
            self.assertEqual(len(PdfReader(self.directory / "result.pdf").pages), 2)
        finally:
            session.cancel()
            planner.close()

    def test_shell_tool_is_unavailable(self):
        planner = self.planner("exec", {"command":"echo test"})
        try:
            self.assertIn("question", planner.plan("请求越权工具", None))
            self.assertIsNone(planner.tool.proposal)
        finally:
            planner.close()

    def test_cancel_no_write(self):
        planner = self.planner("pdf_proposal", {"operation":"rotate", "input":"1.pdf", "pages":"1", "angle":90, "output":"rotated.pdf"})
        session = ChatSession(self.directory, planner)
        try:
            session.respond("旋转1.pdf第1页90度")
            self.assertIsNotNone(session.pending)
            session.respond("取消")
            session.respond("确认")
            self.assertFalse((self.directory / "rotated.pdf").exists())
        finally:
            session.cancel()
            planner.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
