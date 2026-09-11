"""Actual Nanobot AgentLoop with one non-writing PDF proposal tool."""
import asyncio
import json
import time
from pathlib import Path

from nanobot.agent.loop import AgentLoop
from nanobot.agent.context import ContextBuilder
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config
from nanobot.providers.factory import make_provider
from nanobot.providers.base import LLMResponse

from agent import agent_entry as core


class PdfContext(ContextBuilder):
    """Keep the framework context limited to the actual PDF Skill."""
    def __init__(self, workspace, skill):
        super().__init__(workspace)
        self.pdf_skill = skill

    def build_system_prompt(self, *args, **kwargs):
        return ("你是PDF页面助手，仅通过pdf_proposal提出计划。不能执行或确认文件操作。"
                "每次只处理一类操作；缺少文件、页码、角度、裁剪边距时先追问，不能猜测。"
                "完整需求调用pdf_proposal，尚未执行时不要声称成功。"
                "input/output用PDF文件名，pages/order用字符串；合并用inputs和page_specs数组。"
                "默认输出原名_操作.pdf。current_file是上一项成功结果，‘它’指该文件。"
                "裁剪有歧义时先区分提取整页和裁掉边缘。以中文简短回复。\n"
                + self.pdf_skill)


class ProposalTool(Tool):
    name = "pdf_proposal"
    description = "提出一项PDF操作计划，不能执行或确认。参数不完整时直接向用户追问。"
    parameters = {
        "type": "object", "properties": {
            "operation": {"type":"string", "enum":["extract","delete","merge","reorder","rotate","crop"]},
            "input": {"type":"string"}, "output": {"type":"string"},
            "pages": {"type":"string", "description":"1-based页码，例如1或2-4"},
            "inputs": {"type":"array", "items":{"type":"string"}},
            "page_specs": {"type":"array", "items":{"type":"string"}},
            "order": {"type":"string"}, "reverse": {"type":"boolean"},
            "angle": {"type":"integer", "enum":[90,180,270]},
            "margins": {"type":"array", "items":{"type":"number"}}
        }, "required":["operation","output"], "additionalProperties":False
    }

    def __init__(self):
        self.proposal = None

    async def execute(self, **kwargs):
        if self.proposal is not None:
            return "已经提出计划，请停止调用，等待用户确认。"
        self.proposal = kwargs
        return '{"status":"proposal_only","message":"尚未执行，程序将校验并向用户显示确认预览"}'


class NanobotPlanner:
    def __init__(self, config_path: Path):
        config = Config.model_validate_json(config_path.read_text(encoding="utf-8"))
        if (config.providers.ollama.api_base != "http://127.0.0.1:11434/v1"
                or config.agents.defaults.provider != "ollama"
                or config.agents.defaults.model_preset
                or config.agents.defaults.fallback_models
                or config.providers.ollama.proxy):
            raise core.IntentError("本地入口仅允许本机Ollama服务")
        self.model = config.agents.defaults.model
        self.calls = 0
        self.requests = 0
        self.usage = {}
        self.last_metrics = {}
        self.event_loop = asyncio.new_event_loop()
        self.workspace = (config_path.resolve().parent / config.agents.defaults.workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        provider = make_provider(config)
        self.provider = provider
        original_chat = provider.chat

        async def measured_chat(*args, **kwargs):
            # The tool already handed a proposal to the confirmation layer.
            # End this framework turn locally; a second model summary adds no information.
            if getattr(self, "tool", None) is not None and self.tool.proposal is not None:
                return LLMResponse(content="操作计划已交由程序校验，等待用户确认；尚未执行。")
            self.calls += 1
            response = await original_chat(*args, **kwargs)
            for key, value in response.usage.items():
                if isinstance(value, int):
                    self.usage[key] = self.usage.get(key, 0) + value
            return response

        provider.chat = measured_chat
        self.agent = AgentLoop(bus=MessageBus(), provider=provider,
                               workspace=self.workspace, model=self.model, max_iterations=2,
                               context_window_tokens=8192, restrict_to_workspace=True,
                               tools_config=config.tools, mcp_servers={})
        # Remove default shell/file/network/etc. tools, not just prompt restrictions.
        self.registry = ToolRegistry()
        self.tool = ProposalTool()
        self.registry.register(self.tool)
        self.agent.tools = self.registry
        self.skill = (core.PROJECT_ROOT / "skills/pdf-page-editor/SKILL.md").read_text(encoding="utf-8")
        self.agent.context = PdfContext(self.workspace, self.skill)

    def plan(self, text, last_file):
        self.tool.proposal = None
        self.requests += 1
        self.usage = {}
        calls_before = self.calls
        start = time.monotonic()
        prompt = json.dumps({"current_file":last_file,"request":text},ensure_ascii=False) + "\n/no_think"
        try:
            result = self.event_loop.run_until_complete(asyncio.wait_for(
                self.agent.process_direct(prompt, session_key=f"pdf:{self.requests}",
                                          ephemeral=True, persist_user_message=False, tools=self.registry), 120))
        except Exception as exc:
            raise core.IntentError("Nanobot本地模型请求失败或超时，未执行PDF操作") from exc
        self.last_metrics = {"seconds":round(time.monotonic()-start,2), "model_calls":self.calls-calls_before, "tokens":self.usage}
        if self.tool.proposal is not None:
            return {"intent":self.tool.proposal}
        return {"question": (result.content if result else "请补充文件名、页码和操作。")[:800]}

    def close(self):
        client = getattr(self.provider, "_client", None)
        if client is not None:
            self.event_loop.run_until_complete(client.close())
        self.event_loop.run_until_complete(self.event_loop.shutdown_asyncgens())
        self.event_loop.close()
