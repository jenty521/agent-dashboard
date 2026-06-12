#!/usr/bin/env python3
"""Build docs/data/watch.json for the watchlist dashboard.

Usage:
  python3 scripts/build_watch_dashboard_json.py \
    --source data/watch_source.json \
    --output docs/data/watch.json

The script is deterministic and uses Python only, so the dashboard data can be
produced on schedule without any AI step.
"""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

DEFAULT_PAYLOAD = {
    "title": "PKJ-Stock Watchlist",
    "subtitle": "오늘 관심 테마와 후보를 10분 단위로 갱신하는 페이지",
    "market_state": "대기",
    "summary": "테마, 후보종목, 관심목록, 시그널, 매수목록을 한 화면에 정리합니다.",
    "note": "source 파일이 없더라도 heartbeat 스냅샷을 생성합니다.",
    "tags": ["오늘 관심 테마", "후보종목", "관심목록", "시그널", "매수목록"],
    "theme_summary": [],
    "candidate_list": [],
    "watchlist": [],
    "signal_on": [],
    "buy_list": [],
}


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def as_list(value, fallback):
    if isinstance(value, list) and value:
        return value
    return deepcopy(fallback)


def money(value) -> str:
    try:
        return f"{int(value):,}원"
    except Exception:
        return "—"


def build_payload(source: dict | None, source_name: str) -> dict:
    now_kst = datetime.now(KST)
    now_utc = datetime.now(timezone.utc)
    source = source or {}

    payload = deepcopy(DEFAULT_PAYLOAD)
    payload.update(
        {
            "title": source.get("title", payload["title"]),
            "subtitle": source.get("subtitle", payload["subtitle"]),
            "market_state": source.get("market_state", payload["market_state"]),
            "summary": source.get("summary", payload["summary"]),
            "note": source.get("note", payload["note"]),
            "source": source_name,
            "updated_at": now_kst.strftime("%Y-%m-%d %H:%M:%S KST"),
            "generated_at_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )

    payload["tags"] = as_list(source.get("tags"), payload["tags"])
    payload["theme_summary"] = as_list(source.get("theme_summary"), payload["theme_summary"])
    payload["candidate_list"] = as_list(source.get("candidate_list"), payload["candidate_list"])
    payload["watchlist"] = as_list(source.get("watchlist"), payload["watchlist"])
    payload["signal_on"] = as_list(source.get("signal_on"), payload["signal_on"])
    payload["buy_list"] = as_list(source.get("buy_list"), payload["buy_list"])

    payload["counts"] = {
        "themes": len(payload["theme_summary"]),
        "candidates": len(payload["candidate_list"]),
        "watchlist": len(payload["watchlist"]),
        "signals": len(payload["signal_on"]),
        "buys": len(payload["buy_list"]),
    }

    # Enrich buy list with derived fields for display.
    for row in payload["buy_list"]:
        buy = row.get("buy_amount", 0)
        current = row.get("current_amount", 0)
        try:
            diff = int(row.get("diff_amount", current - buy))
        except Exception:
            diff = current - buy
        try:
            rate = row.get("diff_rate", (diff / buy * 100) if buy else 0)
        except Exception:
            rate = 0
        row["buy_amount_text"] = money(buy)
        row["current_amount_text"] = money(current)
        row["diff_amount_text"] = f"{diff:+,}원"
        row["diff_rate_text"] = f"{rate:+.2f}%"

    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/watch_source.json"))
    parser.add_argument("--output", type=Path, default=Path("docs/data/watch.json"))
    args = parser.parse_args()

    source = load_json(args.source) if args.source.exists() else {}
    payload = build_payload(source, source_name=str(args.source) if args.source.exists() else "heartbeat")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output} from {args.source if args.source.exists() else 'heartbeat'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
