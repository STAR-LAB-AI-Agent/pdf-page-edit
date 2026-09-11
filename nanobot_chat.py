"""Safe PDF console powered by the installed Nanobot AgentLoop."""
import os
from pathlib import Path
from loguru import logger
from agent.chat import ChatSession, HELP
from agent.nanobot_planner import NanobotPlanner

ROOT = Path(__file__).resolve().parent


def main():
    logger.remove()  # Do not log user content or Nanobot's full prompts.
    os.environ["PDF_TOOL_PYTHON"] = str(ROOT / ".venv-pdf/Scripts/python.exe")
    planner = NanobotPlanner(ROOT / "nanobot-local.json")
    work = ROOT / "pdf-work"
    work.mkdir(exist_ok=True)
    session = ChatSession(work, planner)
    print(f"Nanobot 0.3.0 + {planner.model} 本地PDF助手\nPDF文件夹：{work}\n{HELP}")
    try:
        while True:
            text = input("\n你 > ")
            if text.strip() in {"退出","exit","quit"}:
                break
            print("助手 > " + session.respond(text))
    except (KeyboardInterrupt, EOFError):
        print("\n对话结束。")
    finally:
        session.cancel()
        planner.close()


if __name__ == "__main__":
    main()
