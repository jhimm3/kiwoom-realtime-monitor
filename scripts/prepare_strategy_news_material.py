"""Freeze stored article and RULE history as optional PANIC and DD research input."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from kiwoom_monitor.application.news_material import MaterialNews, derive_material_news
from kiwoom_monitor.infrastructure.exchange_effective_dates import document_rows


def _history(paths, kind):
    for path in paths:
        payload = json.loads(path.read_bytes())
        if not isinstance(payload, dict) or payload.get("kind") != kind or not isinstance(payload.get("revisions"), list):
            raise ValueError(f"expected stored {kind} history: {path}")
        for row in payload["revisions"]:
            if kind == "body" and isinstance(row.get("filing_html"), str):
                row = {**row, "filing_rows": document_rows(row["filing_html"])}
            yield row


def prepare(article_paths, event_paths, *, body_paths=(), symbol=None, dd_event_ref=None):
    articles = _history(article_paths, "article")
    events = _history(event_paths, "event")
    result = derive_material_news(articles, events, _history(body_paths, "body"))
    if dd_event_ref is not None:
        timeline = MaterialNews.from_documents(result["revisions"])
        chain = next((chain for chain in timeline.chains if (chain[0].symbol, chain[0].ref) == (symbol, dd_event_ref)), None)
        if chain is None:
            raise ValueError("DD event_ref must select an original event for the given stock")
        refs = {row.ref for row in chain}
        result["dd"] = {"event_ref": dd_event_ref, "revisions": [row for row in result["revisions"] if row["ref"] in refs]}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--article-history", type=Path, action="append", required=True)
    parser.add_argument("--event-history", type=Path, action="append", default=[])
    parser.add_argument("--body-history", type=Path, action="append", default=[])
    parser.add_argument("--symbol")
    parser.add_argument("--dd-event-ref")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.article_history, args.event_history, body_paths=args.body_history,
                     symbol=args.symbol, dd_event_ref=args.dd_event_ref)
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True).encode()
    with args.output.open("xb") as stream:
        stream.write(encoded)
    print(json.dumps({"output": str(args.output), "sha256": hashlib.sha256(encoded).hexdigest(),
                      "revisions": len(result["revisions"]), "unresolved": len(result["unresolved"])}))


if __name__ == "__main__":
    main()
