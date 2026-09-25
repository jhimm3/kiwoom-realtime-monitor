from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from kiwoom_monitor.application.news_analysis import assess_stock_news
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context


class DartCredentialValidationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DartDisclosureClient:
    def __init__(self, api_key: str, cache_path: Path, *, timeout_seconds: float = 10.0) -> None:
        self._api_key = api_key
        self._cache_path = cache_path
        self._timeout = timeout_seconds

    def validate_credentials(self) -> None:
        """Probe today's first disclosure without loading or changing the company-code cache."""
        if not self._api_key:
            raise DartCredentialValidationError("INVALID_CREDENTIAL")
        today = datetime.now().strftime("%Y%m%d")
        query = urlencode({"crtfc_key": self._api_key, "bgn_de": today, "end_de": today,
                           "page_no": 1, "page_count": 1})
        try:
            payload = self._json(f"https://opendart.fss.or.kr/api/list.json?{query}")
        except HTTPError as error:
            code = "CREDENTIAL_VALIDATION_RETRYABLE" if error.code == 429 or error.code >= 500 else "CREDENTIAL_VALIDATION_FAILED"
            raise DartCredentialValidationError(code) from None
        except (OSError, TimeoutError):
            raise DartCredentialValidationError("CREDENTIAL_VALIDATION_RETRYABLE") from None
        except ValueError:
            raise DartCredentialValidationError("CREDENTIAL_VALIDATION_FAILED") from None
        if not isinstance(payload, dict):
            raise DartCredentialValidationError("CREDENTIAL_VALIDATION_FAILED")
        status = str(payload.get("status", ""))
        if status == "013" or (status == "000" and isinstance(payload.get("list"), list)):
            return
        code = ("INVALID_CREDENTIAL" if status in {"010", "011", "901"} else
                "CREDENTIAL_VALIDATION_RETRYABLE" if status in {"020", "800", "900"} else
                "CREDENTIAL_VALIDATION_FAILED")
        raise DartCredentialValidationError(code)

    def search(self, stock_code: str, stock_name: str, *, days: int = 30) -> tuple[StockNewsItem, ...]:
        if not self._api_key:
            return ()
        corp_code = self._corp_codes().get(stock_code[:6])
        if not corp_code:
            return ()
        today = datetime.now().date()
        query = urlencode({
            "crtfc_key": self._api_key, "corp_code": corp_code,
            "bgn_de": (today - timedelta(days=days)).strftime("%Y%m%d"),
            "end_de": today.strftime("%Y%m%d"), "page_count": 30,
        })
        payload = self._json(f"https://opendart.fss.or.kr/api/list.json?{query}")
        if str(payload.get("status", "000")) not in {"000", "013"}:
            raise ValueError(f"DART 조회 실패: {payload.get('message', payload.get('status'))}")
        results: list[StockNewsItem] = []
        for raw in payload.get("list", ()) or ():
            receipt = str(raw.get("rcept_no", ""))
            title = str(raw.get("report_nm", "공시"))
            description = f"{stock_name} 공식 공시 · 제출인 {raw.get('flr_nm', '')}"
            date_text = str(raw.get("rcept_dt", ""))
            try:
                published = datetime.strptime(date_text, "%Y%m%d").astimezone()
            except ValueError:
                published = None
            link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}"
            results.append(StockNewsItem(title, description, link, link, published, assess_stock_news(stock_name, title, description)))
        return tuple(results)

    def list_disclosures(
        self, stock_code: str, begin: date, end: date,
        *, disclosure_type: str = "",
    ) -> tuple[dict[str, object], ...]:
        """Return complete raw DART list rows for one listed company and date range."""
        if not self._api_key or end < begin:
            return ()
        corp_code = self._corp_codes().get(stock_code[:6])
        if not corp_code:
            return ()
        page = 1
        results: list[dict[str, object]] = []
        while True:
            query = urlencode({
                "crtfc_key": self._api_key, "corp_code": corp_code,
                "bgn_de": begin.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
                "page_no": page, "page_count": 100,
                **({"pblntf_ty": disclosure_type} if disclosure_type else {}),
            })
            payload = self._json(f"https://opendart.fss.or.kr/api/list.json?{query}")
            status = str(payload.get("status", "000"))
            if status == "013":
                return tuple(results)
            if status != "000":
                raise ValueError(f"DART 조회 실패: {payload.get('message', status)}")
            rows = payload.get("list", ()) or ()
            if not isinstance(rows, list):
                raise ValueError("DART 조회 응답의 list 형식이 올바르지 않습니다.")
            results.extend(dict(row) for row in rows if isinstance(row, dict))
            total_pages = int(payload.get("total_page") or 1)
            if page >= total_pages:
                return tuple(results)
            page += 1

    def _corp_codes(self) -> dict[str, str]:
        if self._cache_path.exists() and datetime.now().timestamp() - self._cache_path.stat().st_mtime < 86400 * 30:
            return json.loads(self._cache_path.read_text(encoding="utf-8"))
        request = Request(
            "https://opendart.fss.or.kr/api/corpCode.xml?" + urlencode({"crtfc_key": self._api_key}),
            headers={"User-Agent": "KiwoomRealtimeMonitor/NewsPrototype"},
        )
        with urlopen(request, timeout=self._timeout, context=system_ssl_context()) as response:
            archive = zipfile.ZipFile(io.BytesIO(response.read()))
            root = ElementTree.fromstring(archive.read("CORPCODE.xml"))
        mapping = {
            str(node.findtext("stock_code", "")).strip(): str(node.findtext("corp_code", "")).strip()
            for node in root.findall("list") if str(node.findtext("stock_code", "")).strip()
        }
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
        return mapping

    def _json(self, url: str) -> dict[str, object]:
        with urlopen(Request(url, headers={"User-Agent": "KiwoomRealtimeMonitor/NewsPrototype"}), timeout=self._timeout, context=system_ssl_context()) as response:
            return json.loads(response.read().decode("utf-8"))
