"""Explicit, small SIWC checks with a separate Windows credential profile/DB.

No collector, test app, production config, NAS, or paid-provider fallback starts.
Run preflight first. Login and inference stages require the user's prior approval.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[1]
SOURCE_ROOT = WORKTREE / "src"
sys.path.insert(0, str(SOURCE_ROOT))
os.environ["PYTHONPATH"] = str(SOURCE_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="Separate validation profile name, e.g. news-ed9d-01")
    parser.add_argument("--stage", choices=("preflight", "settings", "login", "models", "probe", "news", "batch", "disconnect"), default="preflight")
    parser.add_argument("--model", default="", help="Exact slug from the models stage; no guessed default")
    parser.add_argument("--input", type=Path, help="UTF-8 JSON news input, 1 article or 2-3 article batch")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", args.profile):
        parser.error("profile must be 1-40 lowercase letters, digits or hyphens")
    from kiwoom_monitor.infrastructure import chatgpt_auth
    from kiwoom_monitor.infrastructure.chatgpt_plan import ChatGPTPlanClient
    from kiwoom_monitor.infrastructure.naver_news import LocalNaverNewsConfig
    from kiwoom_monitor.infrastructure.news_ai import analyze_articles
    from google.auth import jwt  # noqa: F401
    from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: F401
    if sys.platform != "win32" or not os.environ.get("LOCALAPPDATA"):
        raise ValueError("Windows 사용자 세션이 필요합니다.")
    if not Path(chatgpt_auth.__file__).resolve().is_relative_to(SOURCE_ROOT):
        raise ValueError("실행 소스가 현재 워크트리가 아닙니다.")
    protected_path = Path(os.environ["LOCALAPPDATA"]) / "KiwoomMonitor" / "ChatGPTPlanValidation" / args.profile / "credentials.dpapi"
    data_root = WORKTREE / "data" / "diagnostics" / "chatgpt-plan-validation" / args.profile
    config = LocalNaverNewsConfig(data_root / "naver_news.dat", chatgpt_storage_path=protected_path)
    auth = chatgpt_auth.ChatGPTAuth(chatgpt_auth.ChatGPTCredentialStore(protected_path))
    client = ChatGPTPlanClient(auth)
    report: dict = {"stage": args.stage, "profile": args.profile, "source": str(SOURCE_ROOT),
                    "database": str(data_root / "news.sqlite3"), "inference_requests": 0}
    if args.stage == "preflight":
        report.update(windows=True, verification_dependencies=True, isolated_paths=True,
                      login_started=False, credentials_read=False)
        print(json.dumps(report, ensure_ascii=False))
        return 0
    if args.stage == "settings":
        from PySide6.QtWidgets import QApplication
        from kiwoom_monitor.presentation.news_settings_dialog import NaverNewsSettingsDialog
        app = QApplication.instance() or QApplication([])
        dialog = NaverNewsSettingsDialog(config, section="connections", chatgpt_auth=auth)
        dialog.show()
        return app.exec()
    started = time.monotonic()
    try:
        if args.stage == "login":
            status = auth.connect(threading.Event(), account_id=str(auth.status()["active"]))
            active = next(item for item in status["accounts"] if item["id"] == status["active"])
            report.update(connected=active["connected"], plan_permission=active["permitted"])
        elif args.stage == "models":
            report["models"] = client.models()
        elif args.stage == "disconnect":
            report["revocation_confirmed"] = auth.disconnect()["revocation_confirmed"]
        elif args.stage == "probe":
            if not args.model:
                parser.error("model must be chosen from the account catalog")
            report["inference_requests"] = 1
            text, usage = client.respond(args.model, "Reply with exactly OK.")
            report.update(model=args.model, usage=usage, inference_requests=1, probe_ok=text.strip() == "OK")
            if not report["probe_ok"]:
                raise ValueError("짧은 확인 요청의 결과가 예상과 다릅니다.")
        else:
            from kiwoom_monitor.application.news_analysis import assess_stock_news
            from kiwoom_monitor.infrastructure.naver_news import NewsAISettings, StockNewsItem
            from kiwoom_monitor.infrastructure.news_ai import analysis_body_hash
            from kiwoom_monitor.infrastructure.persistence.news_ai_repository import NewsAIRepository
            if not args.model or args.input is None:
                parser.error("news/batch requires an account model and explicit input JSON")
            raw = json.loads(args.input.read_text(encoding="utf-8"))
            articles = raw.get("articles")
            stock_code, stock_name = raw.get("stock_code"), raw.get("stock_name")
            expected = 1 if args.stage == "news" else 3
            if (not isinstance(stock_name, str) or not stock_name.strip() or not isinstance(stock_code, str)
                    or not re.fullmatch(r"[0-9]{6}", stock_code) or not isinstance(articles, list)
                    or not 1 <= len(articles) <= expected or (args.stage == "batch" and len(articles) < 2)):
                raise ValueError("뉴스1건 또는 동일 종목의 작은 묶음2~3건을 지정하세요.")
            inputs, items = [], []
            for article in articles:
                if any(not isinstance(article.get(key), str) or not article[key].strip()
                       for key in ("title", "body", "url")):
                    raise ValueError("각 뉴스에 title/body/url이 필요합니다.")
                inputs.append((article["title"], article["body"]))
                published = datetime.fromisoformat(article["published_at"]) if article.get("published_at") else None
                items.append(StockNewsItem(article["title"], "", article["url"], article["url"], published,
                    assess_stock_news(stock_name, article["title"], article["body"])))
            settings = NewsAISettings(provider="chatgpt_plan", model=args.model, chatgpt_storage_path=protected_path)
            report["inference_requests"] = 1
            results, usage = analyze_articles(settings, stock_name, tuple(inputs))
            repository = NewsAIRepository(data_root / "news.sqlite3")
            for item, (_title, body), result in zip(items, inputs, results, strict=True):
                repository.save(stock_code, item, "chatgpt_plan", args.model, analysis_body_hash(stock_name, body), result)
                loaded = repository.load(stock_code, item)
                if loaded is None or loaded.analysis.summary != result.summary or loaded.provider != "chatgpt_plan":
                    raise ValueError("격리 뉴스 DB의 결과 대조에 실패했습니다.")
            repository.log_request("chatgpt_plan", args.model, "single" if len(items) == 1 else "batch", len(items), len(items), usage)
            report.update(model=args.model, inference_requests=1, articles=len(items), saved=len(results),
                          usage=usage.__dict__, news_readback=True)
        report["ok"] = True
    except chatgpt_auth.ChatGPTPlanError as error:
        report.update(ok=False, error_code=error.code, http_status=error.status_code, body_shape=error.body_shape, request_id=error.request_id)
        if error.identity_failure:
            report["identity_failure"] = error.identity_failure
        if error.response_failure:
            report["response_failure"] = error.response_failure
        if error.response_diagnostics:
            report["response_diagnostics"] = error.response_diagnostics
        report["inference_requests"] = int(error.inference_attempted)
    except ValueError:
        report.update(ok=False, error_code="validation_failed")
    report.update(elapsed_seconds=round(time.monotonic() - started, 3), completed_at=datetime.now(UTC).isoformat())
    data_root.mkdir(parents=True, exist_ok=True)
    output = data_root / (args.stage + "-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + ".json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
