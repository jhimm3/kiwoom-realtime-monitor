"""Read the Kiwoom stock lists used for ranking follow-ups and NXT eligibility."""

from datetime import datetime, time
from typing import TypedDict

from kiwoom_monitor.domain.ranking import normalize_stock_code
from kiwoom_monitor.domain.time_basis import as_kst


NXT_CATALOG_KIND = "nxt_eligibility_catalog"
PREMARKET_CATALOG_SETTING = "premarket_stock_catalog"
PREMARKET_MARKETS = {"0": "KOSPI", "10": "KOSDAQ"}


class PremarketCatalog(TypedDict):
    flags: dict[str, bool]
    stocks: dict[str, tuple[str, str]]


def nxt_catalog_refresh_due(now: datetime) -> bool:
    now = as_kst(now)
    return now.weekday() < 5 and time(7) <= now.time() < time(20)


def load_excluded_product_codes(request_page) -> frozenset[str]:
    """Use Kiwoom's ETF, ELW and three ETN market lists. Never infer from a name."""
    codes: set[str] = set()
    product_markets = {"3", "8", "60", "70", "90"}
    for market in ("8", "60", "70", "90", "3"):
        has_products = False
        for row in _stock_list_rows(request_page, market):
            code = normalize_stock_code(row.get("code"))
            market_code = str(row.get("marketCode", "")).strip()
            if len(code) == 6 and code.isalnum() and market_code in product_markets:
                codes.add(code)
                has_products = True
        if market == "8" and not has_products:
            raise ValueError("Kiwoom ETF list is empty")
    return frozenset(codes)


def load_premarket_catalog(request_page) -> PremarketCatalog:
    """Build both views from the same complete pair of Kiwoom market lists."""
    flags: dict[str, bool | None] = {}
    stocks: dict[str, tuple[str, str]] = {}
    for market in PREMARKET_MARKETS:
        has_codes = False
        for row in _stock_list_rows(request_page, market):
            market_code = str(row.get("marketCode", "")).strip()
            if market_code not in PREMARKET_MARKETS:
                continue
            code = normalize_stock_code(row.get("code"))
            name = str(row.get("name") or "").strip()
            if len(code) != 6 or not code.isalnum() or not name:
                raise ValueError("invalid Kiwoom stock identity")
            identity = (name, PREMARKET_MARKETS[market_code])
            if stocks.setdefault(code, identity) != identity:
                raise ValueError(f"conflicting Kiwoom stock identity: {code}")
            has_codes |= market_code == market
            raw = str(row.get("nxtEnable", "")).strip().upper()
            if raw not in {"Y", "N"}:
                continue
            flag = raw == "Y"
            flags[code] = flag if flags.get(code, flag) is flag else None
        if not has_codes:
            raise ValueError(f"empty Kiwoom stock list: {market}")
    return {"flags": {code: flag for code, flag in flags.items() if flag is not None}, "stocks": stocks}


def _stock_list_rows(request_page, market: str):
    next_key = ""
    seen_keys: set[str] = set()
    while True:
        payload, has_next, next_key = request_page(
            "ka10099", "/api/dostk/stkinfo", {"mrkt_tp": market},
            cont_yn="Y" if next_key else "N", next_key=next_key,
        )
        rows = payload.get("list")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("invalid Kiwoom stock list page")
        yield from rows
        if not has_next:
            return
        if not next_key or next_key in seen_keys or len(seen_keys) >= 99:
            raise ValueError("invalid Kiwoom stock list continuation")
        seen_keys.add(next_key)
