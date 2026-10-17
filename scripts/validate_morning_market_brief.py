"""One explicit grounded subscription probe; isolated results, no NAS/settings writes."""
from pathlib import Path
import argparse
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTAuth, ChatGPTCredentialStore, ChatGPTPlanError
from kiwoom_monitor.infrastructure.chatgpt_plan import ChatGPTPlanClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    client = ChatGPTPlanClient(ChatGPTAuth(ChatGPTCredentialStore(args.profile)))
    now = datetime.now(ZoneInfo("Asia/Seoul")).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        from kiwoom_monitor.application.morning_market_brief import DEFAULT_SETTINGS, render_prompt, validate_output, content_hash
        prompt = (render_prompt(DEFAULT_SETTINGS, {"cutoff_at": now, "timezone": "Asia/Seoul", "watchlist": "", "portfolio": "계좌 자료 없음"})
                  if args.full else f"현재 한국 시각은 {now}이다. 웹 검색으로 한국은행의 가장 최근 기준금리 결정 발표를 공식 사이트에서 확인한다. 날짜와 금리, 출처 URL만 한국어 JSON으로 반환한다. 주식매매 판단은 하지 않는다.")
        result = client.research("gpt-6.1-sol", prompt, reasoning_effort="high")
        report = {"status": "completed", "model": "gpt-6.1-sol", "reasoning_effort": "high",
                  "prompt_hash": content_hash(prompt), "prompt": prompt,
                  "searched_at": now, "search_calls": result.search_calls,
                  "sources": result.sources, "usage": result.usage, "text": result.text}
        if args.full:
            try:
                report["validated"] = validate_output(result.text,
                    {"sources": result.sources, "data_gaps": [], "cutoff_at": now})
                report["contract_validated"] = True
            except (ValueError, TypeError, KeyError):
                report["status"] = "invalid_contract"
                report["contract_validated"] = False
    except ChatGPTPlanError as error:
        report = {"status": "failed", "code": error.code, "http_status": error.status_code,
                  "inference_attempted": error.inference_attempted}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k not in {"text", "sources", "validated"}}, ensure_ascii=False))
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
