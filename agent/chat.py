"""Interactive PDF assistant: bounded model planning, local validation and confirmation."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

from agent import agent_entry as core

SKILL = core.PROJECT_ROOT / "skills/pdf-page-editor/SKILL.md"
HELP = '''直接输入中文，例如：
  请将1.pdf第2到4页提取出来
  将1.pdf第2页旋转90度
  删除1.pdf第1页
  合并1.pdf和2.pdf，输出为合并.pdf（文件名间建议留空格）
  将1.pdf倒序
  将1.pdf第2页四边各裁掉10毫米
还可以输入：文件、帮助、状态、取消、退出。
出现预览后，单独回复“确认”才会写文件。'''


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise core.IntentError("模型服务不允许重定向")


class OllamaPlanner:
    """One bounded request to a fixed loopback endpoint; no shell or document content."""
    def __init__(self, model: str):
        self.model = model
        self.calls = 0
        self.last_metrics = {}
        self.skill = SKILL.read_text(encoding="utf-8")

    def plan(self, text: str, last_file: str | None) -> dict:
        system = self.skill + '''
你是PDF对话智能体，依据上述Skill判断是否调用工具。只输出一个JSON对象。
信息不足或与PDF无关：{"question":"简短追问或能力说明"}。
完整操作：{"intent":{"operation":"extract|delete|merge|reorder|rotate|crop",...}}。
所有intent必须含output（新PDF名）和overwrite:false。
extract/delete: input,pages；rotate另含angle（顺时针90/180/270）。
crop: input,pages,margins（左下右上裁掉的点数；毫米乘72/25.4）。
reorder: input,order（全部页序字符串）或reverse:true。
merge: inputs（数组）,page_specs（逐文件页码字符串或all）。
不要产生脚本、命令或其他字段。页码使用1-based字符串，如2-4或1,3。
每次只能一类操作；复合任务先请用户选择第一步。不要猜页码、角度或裁剪边距。
“裁剪”有歧义时问是提取整页还是裁掉边缘。不得把输出当输入。
用户可用“它”指当前文件。不调用工具也能回答简单帮助。
你不能确认或执行操作，也不能声称已完成。实际执行由程序收到用户单独确认后完成。
'''
        payload = {"model": self.model, "stream": False, "format": "json",
                   "options": {"temperature": 0, "num_predict": 512, "num_ctx": 8192},
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": json.dumps({"current_file": last_file, "request": text}, ensure_ascii=False)}]}
        req = urllib.request.Request("http://127.0.0.1:11434/api/chat",
                                     data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        start = time.monotonic()
        self.calls += 1
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(req, timeout=45) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("oversized response")
            data = json.loads(raw)
            result = json.loads(data["message"]["content"])
            if not isinstance(result, dict) or set(result) not in ({"question"}, {"intent"}):
                raise ValueError("invalid model response")
            if "question" in result and (not isinstance(result["question"], str) or len(result["question"]) > 800):
                raise ValueError("invalid question")
            self.last_metrics = {"seconds": round(time.monotonic() - start, 2),
                                 "prompt_tokens": data.get("prompt_eval_count"), "output_tokens": data.get("eval_count")}
            return result
        except core.IntentError:
            raise
        except Exception as exc:
            raise core.IntentError("本地模型调用失败，请检查Ollama服务和模型名称；未执行PDF操作。") from exc


class ChatSession:
    def __init__(self, directory: Path, planner=None):
        self.directory = directory.resolve()
        if not self.directory.is_dir():
            raise core.IntentError("工作目录不存在")
        self.planner = planner
        self.pending = None
        self.last_file = None
        self.partial = ""
        self.crop_question = False

    def cancel(self):
        if self.pending:
            (core.PENDING_DIR / f"{self.pending['confirmation_token']}.json").unlink(missing_ok=True)
        self.pending = None
        self.partial = ""
        self.crop_question = False

    def respond(self, text: str) -> str:
        text = text.strip()
        if text in {"帮助", "help", "?", "？"}:
            return HELP
        if text == "状态":
            return f"模式：{'本地模型 ' + self.planner.model if self.planner else '规则演示（未接入大模型）'}；当前文件：{self.last_file or '未选择'}；待确认：{bool(self.pending)}；模型调用：{self.planner.calls if self.planner else 0}；最近指标：{self.planner.last_metrics if self.planner else {}}"
        if text in {"文件", "查看文件", "列出文件"}:
            from itertools import islice
            files = list(islice((p for p in self.directory.iterdir() if p.suffix.lower() == ".pdf" and p.is_file() and p.resolve().is_relative_to(self.directory)), 21))
            return "当前目录PDF（最多20项）：\n" + ("\n".join(p.name for p in files[:20]) or "暂无PDF，请先放入文件。")
        if text in {"取消", "算了", "不要执行"}:
            self.cancel()
            return "已取消，未执行待确认操作。"
        if text in {"确认", "确认执行"}:
            if not self.pending:
                return "没有待确认的操作，请先描述需求。"
            plan, self.pending = self.pending, None
            code, result = core.confirm(plan["confirmation_token"])
            if code:
                return "操作未完成：" + result.get("error_message", "未知错误")
            self.last_file = str(Path(result["output_file"]).relative_to(self.directory))
            return result["message"] + f"\n文件：{result['output_file']}\n输出共{result['total_pages_output']}页。后续说“它”指这个结果文件。"
        if not text:
            return "请输入需求，或输入“帮助”。"
        if len(text) > 2000:
            return "单条请求请控制在2000字符以内。"
        if self.pending:
            self.cancel()
        if self.crop_question and any(word in text for word in ("提取", "整页")):
            request = re.sub("裁剪|裁掉", "提取", self.partial)
            self.crop_question = False
        else:
            # Bounded clarification state, never an unbounded conversation transcript.
            request = (self.partial + "；" + text).strip("；")[-4000:]
        try:
            if self.planner:
                result = self.planner.plan(request, self.last_file)
                if "question" in result:
                    self.partial = request
                    return result["question"]
                intent = result["intent"]
                if not isinstance(intent, dict):
                    raise core.IntentError("模型返回了无效参数，请重新描述需求")
                # A model cannot authorize overwriting. Console always creates new files.
                intent["overwrite"] = False
            else:
                if re.search("裁剪|裁掉", request) and not re.search(r"毫米|mm|点|pt", request, re.I):
                    self.partial, self.crop_question = request, True
                    return "你是要提取整页，还是裁掉页面边缘？可回复“提取整页”或“四边各裁掉10毫米”。"
                if not re.search(r"\.pdf", request, re.I) and self.last_file:
                    request = f'"{self.last_file}"；' + request
                # Separate conjunctions around filenames for deterministic demo parsing.
                request = re.sub(r"(?<=\.pdf)(?:和|与)", " ", request, flags=re.I)
                request = re.sub(r"(?<!\w)(?:删除)(?=[^\s]*?\.pdf)", "删除 ", request)
                intent = core.parse_intent(request)
            plan = core.create_plan(request, self.directory, intent)
            self.pending = plan
            self.partial = ""
            self.crop_question = False
            return plan["plan"] + "\n请回复“确认”或“取消”；修改需求会使旧预览失效。"
        except (core.IntentError, TypeError, ValueError, KeyError) as exc:
            self.partial = request
            return "请补充或修正（也可输入“取消”重新开始）：" + (str(exc) if isinstance(exc, core.IntentError) else "参数格式有误")


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="PDF连续对话入口")
    parser.add_argument("--allowed-dir")
    parser.add_argument("--model", default=os.environ.get("PDF_AGENT_MODEL"))
    parser.add_argument("--demo", action="store_true", help="离线规则演示模式")
    args = parser.parse_args()
    session = None
    try:
        directory = args.allowed_dir or input("PDF工作文件夹（回车使用项目文件夹）：").strip().strip('"') or str(core.PROJECT_ROOT)
        model = None if args.demo else args.model
        if not model and not args.demo:
            model = input("本地Ollama模型名称（留空进入规则演示模式）：").strip()
        session = ChatSession(Path(directory), OllamaPlanner(model) if model else None)
        print("\nPDF助手已启动。" + (f"本地模型：{model}" if model else "当前为规则演示模式，未调用大模型。"))
        print(HELP)
        while True:
            text = input("\n你 > ")
            if text.strip() in {"退出", "exit", "quit"}:
                break
            try:
                print("助手 > " + session.respond(text))
            except Exception:
                print("助手 > 处理失败，未获得成功结果。请检查文件权限后重新操作。")
        return 0
    except (EOFError, KeyboardInterrupt):
        print("\n对话结束。")
        return 0
    except core.IntentError as exc:
        print(str(exc))
        return 2
    finally:
        if session:
            session.cancel()


if __name__ == "__main__":
    raise SystemExit(main())
