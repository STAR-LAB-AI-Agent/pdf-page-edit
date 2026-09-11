"""Real local-model acceptance. No mocks; test data is generated, never user PDFs."""
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from loguru import logger
from pypdf import PdfReader, PdfWriter
from agent.chat import ChatSession
from agent.nanobot_planner import NanobotPlanner


def main():
    logger.remove()
    os.environ["PDF_TOOL_PYTHON"] = str(ROOT / ".venv-pdf/Scripts/python.exe")
    planner = NanobotPlanner(ROOT / "nanobot-local.json")
    records = []
    cases = [
        ("提取", "请将1.pdf第2到3页提取出来，输出为extract.pdf", "extract.pdf", 2),
        ("旋转", "将1.pdf第2页顺时针旋转90度，输出为rotate.pdf", "rotate.pdf", 3),
        ("合并", "合并1.pdf和2.pdf的全部页面，输出为merge.pdf", "merge.pdf", 5),
        ("删除", "删除1.pdf第1页，输出为delete.pdf", "delete.pdf", 2),
        ("倒序", "将1.pdf全部页面倒序，输出为reverse.pdf", "reverse.pdf", 3),
        ("裁剪", "将1.pdf第2页四边各裁掉10点，输出为crop.pdf", "crop.pdf", 3),
    ]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            for name, pages in [("1.pdf",3),("2.pdf",2)]:
                writer = PdfWriter()
                for i in range(pages):
                    writer.add_blank_page(width=300+i, height=400+i)
                writer.write(work/name)
            original = hashlib.sha256((work/"1.pdf").read_bytes()).hexdigest()
            session = ChatSession(work, planner)
            try:
                for label, request, filename, total in cases:
                    session.cancel()
                    session.last_file = None
                    started = time.monotonic()
                    preview = session.respond(request)
                    prewrite = (work/filename).exists()
                    confirmed = session.respond("确认") if session.pending else "NO_PLAN"
                    passed = False
                    if (work/filename).is_file():
                        reader = PdfReader(work/filename)
                        passed = len(reader.pages)==total and not prewrite
                        if label == "旋转": passed &= reader.pages[1].rotation == 90 and reader.pages[0].rotation == 0
                        if label == "提取": passed &= [float(p.mediabox.width) for p in reader.pages] == [301,302]
                        if label == "删除": passed &= float(reader.pages[0].mediabox.width)==301
                        if label == "合并": passed &= [float(p.mediabox.width) for p in reader.pages]==[300,301,302,300,301]
                        if label == "倒序": passed &= float(reader.pages[0].mediabox.width)==302
                        if label == "裁剪": passed &= float(reader.pages[1].cropbox.width)==281
                    passed &= hashlib.sha256((work/"1.pdf").read_bytes()).hexdigest()==original
                    record = {"case":label,"passed":bool(passed),"seconds":round(time.monotonic()-started,2),"metrics":planner.last_metrics,"request":request,"preview":preview,"result":confirmed}
                    records.append(record)
                    print(json.dumps(record,ensure_ascii=False),flush=True)
            finally:
                session.cancel()
    finally:
        planner.close()
    report = {"date":datetime.now().isoformat(),"backend":"Nanobot 0.3.0 AgentLoop + Ollama qwen3:4b", "mocked":False,"cases":records}
    (ROOT/"tests/live_nanobot_results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return 0 if len(records)==6 and all(r["passed"] for r in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
