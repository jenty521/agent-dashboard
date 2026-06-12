#!/usr/bin/env python3
"""Build docs/data/watch.json for the watchlist dashboard.

Usage:
  python3 scripts/build_watch_dashboard_json.py \
    --source data/watch_source.json \
    --output docs/data/watch.json

The script prefers live PostgreSQL data so the watch page shows real rows.
If the database is unavailable, it falls back to the optional source JSON,
and if that is empty it emits an honest empty-state snapshot.
"""
from __future__ import annotations

import argparse
import json
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

KST = ZoneInfo("Asia/Seoul")
DEFAULT_DB_URL = "postgresql://jenty521@127.0.0.1:5432/invest?sslmode=disable"

DEFAULT_PAYLOAD: dict[str, Any] = {
    "title": "PKJ-Stock Watchlist",
    "subtitle": "오늘 관심 테마와 후보를 10분 단위로 갱신하는 페이지",
    "market_state": "대기",
    "summary": "테마, 후보종목, 관심목록, 시그널, 매수목록을 한 화면에 정리합니다.",
    "note": "source 파일이 없더라도 empty-state 스냅샷을 생성합니다.",
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
        return f"{int(float(value)):,}원"
    except Exception:
        return "—"


def fmt_kst(value) -> str:
    if value in (None, ""):
        return "—"
    if isinstance(value, str):
        return value
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        dt = value.astimezone(KST) if value.tzinfo else value.replace(tzinfo=timezone.utc).astimezone(KST)
        return dt.strftime("%Y-%m-%d %H:%M:%S KST")
    return str(value)


def score_letter(count: int, max_catalyst: float) -> str:
    if count >= 30 or max_catalyst >= 8.5:
        return "A"
    if count >= 15 or max_catalyst >= 7.0:
        return "A-"
    if count >= 8 or max_catalyst >= 5.0:
        return "B+"
    if count >= 4:
        return "B"
    return "B-"


def safe_tag_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if str(v).strip()]


def build_theme_summary(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        with tagged as (
          select
            published_at,
            catalyst_score,
            jsonb_array_elements_text(theme_tags_json) as tag
          from public.news_events
          where theme_tags_json is not null
            and jsonb_typeof(theme_tags_json) = 'array'
            and published_at >= now() - interval '48 hours'
        )
        select
          tag,
          count(*)::int as cnt,
          coalesce(max(catalyst_score), 0)::float as max_catalyst,
          max(published_at) as last_at
        from tagged
        group by tag
        order by cnt desc, max_catalyst desc, last_at desc
        limit 5;
        """
    )
    rows = cur.fetchall()
    summary = []
    for row in rows:
        tag = str(row["tag"])
        cnt = int(row["cnt"] or 0)
        max_catalyst = float(row["max_catalyst"] or 0)
        last_at = row["last_at"]
        summary.append(
            {
                "theme": tag,
                "score": score_letter(cnt, max_catalyst),
                "reason": f"최근 {cnt}건 뉴스 태그 · 최고 촉매 {max_catalyst:.1f}",
                "updated_at": fmt_kst(last_at),
            }
        )
    return summary


def build_candidates(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        select
          c.symbol,
          coalesce(i.name, c.meta_json->>'name', c.symbol) as name,
          c.list_date,
          c.score,
          c.source,
          c.meta_json,
          a.score as active_score,
          a.meta_json as active_meta,
          a.updated_at as active_updated_at
        from public.watchlist_candidates c
        left join lateral (
          select score, meta_json, updated_at
          from public.watchlist_active a
          where a.symbol = c.symbol
          order by a.updated_at desc, a.id desc
          limit 1
        ) a on true
        left join public.instruments i on i.symbol = c.symbol
        order by c.updated_at desc nulls last, c.score desc nulls last, c.symbol asc
        limit 12;
        """
    )
    rows = cur.fetchall()
    items = []
    for row in rows:
        meta = row.get("meta_json") or {}
        active_meta = row.get("active_meta") or {}
        theme_tags = safe_tag_list(active_meta.get("theme_tags"))
        hot_override = active_meta.get("hot_override") or {}
        surge = active_meta.get("surge") or {}
        theme = theme_tags[0] if theme_tags else hot_override.get("reason") or surge.get("stage") or row.get("source") or "watchlist"
        signal = hot_override.get("reason") or surge.get("stage") or row.get("source") or f"score {float(row.get('score') or 0):.1f}"
        collected_at = row.get("active_updated_at") or row.get("list_date")
        items.append(
            {
                "ticker": row["symbol"],
                "name": row["name"],
                "theme": theme,
                "signal": signal,
                "collected_at": fmt_kst(collected_at),
                "source": row.get("source") or "watchlist_candidates",
                "candidate_score": float(row.get("score") or 0),
                "active_score": float(row.get("active_score") or 0) if row.get("active_score") is not None else None,
            }
        )
    return items


def build_watchlist(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        select
          a.symbol,
          coalesce(i.name, c.meta_json->>'name', a.symbol) as name,
          a.score,
          a.meta_json,
          a.updated_at,
          c.source as candidate_source,
          c.list_date,
          c.score as candidate_score
        from public.watchlist_active a
        left join lateral (
          select source, list_date, score, meta_json
          from public.watchlist_candidates c
          where c.symbol = a.symbol
          order by c.updated_at desc, c.id desc
          limit 1
        ) c on true
        left join public.instruments i on i.symbol = a.symbol
        where a.is_active is true
        order by a.score desc nulls last, a.updated_at desc nulls last, a.symbol asc
        limit 12;
        """
    )
    rows = cur.fetchall()
    items = []
    for row in rows:
        meta = row.get("meta_json") or {}
        theme_tags = safe_tag_list(meta.get("theme_tags"))
        hot_override = meta.get("hot_override") or {}
        candidate = meta.get("candidate") or {}
        surge = meta.get("surge") or {}
        sources = meta.get("sources") or []
        reason_parts = []
        if hot_override.get("reason"):
            reason_parts.append(str(hot_override["reason"]))
        if theme_tags:
            reason_parts.append("태그: " + ", ".join(theme_tags[:3]))
        if surge.get("stage"):
            reason_parts.append(f"시그널: {surge.get('stage')}")
        if candidate.get("score") is not None:
            reason_parts.append(f"후보점수 {float(candidate['score']):.1f}")
        if not reason_parts and sources:
            reason_parts.append("sources: " + ", ".join(map(str, sources[:3])))
        if not reason_parts:
            reason_parts.append("관심 유지")
        signal = hot_override.get("reason") or surge.get("stage") or (theme_tags[0] if theme_tags else row.get("candidate_source") or "watch")
        items.append(
            {
                "ticker": row["symbol"],
                "name": row["name"],
                "reason": " / ".join(reason_parts),
                "signal": signal,
                "collected_at": fmt_kst(meta.get("selected_at") or row.get("updated_at")),
                "score": float(row.get("score") or 0),
                "source": row.get("candidate_source") or "watchlist_active",
            }
        )
    return items


def build_signal_on(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        with ranked as (
          select
            s.symbol,
            s.source,
            s.kind,
            s.rank,
            s.price,
            s.change_pct,
            s.volume,
            s.trade_value,
            s.trigger_strength,
            s.payload_json,
            s.captured_at,
            s.consumed_at,
            row_number() over (partition by s.symbol order by s.captured_at desc nulls last, s.id desc) as rn
          from public.surge_pool s
          where s.captured_at >= now() - interval '24 hours'
        )
        select * from ranked where rn = 1 order by captured_at desc nulls last, trigger_strength desc nulls last, symbol asc limit 8;
        """
    )
    rows = cur.fetchall()
    items = []
    for row in rows:
        payload = row.get("payload_json") or {}
        stage = payload.get("stage") or row.get("kind") or row.get("source") or "signal"
        items.append(
            {
                "ticker": row["symbol"],
                "name": row["symbol"],
                "signal": stage,
                "collected_at": fmt_kst(row.get("captured_at")),
                "signal_at": fmt_kst(row.get("captured_at")),
                "source": row.get("source"),
                "trade_value_text": money(row.get("trade_value")),
                "change_pct_text": f"{float(row.get('change_pct') or 0):+.2f}%",
                "trigger_strength": float(row.get("trigger_strength") or 0),
            }
        )
    return items


def build_buy_list(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        select
          p.symbol,
          coalesce(i.name, p.symbol) as name,
          p.side,
          p.quantity,
          p.avg_price,
          p.market_price,
          p.market_value,
          p.realized_pnl,
          p.unrealized_pnl,
          p.last_updated_at,
          p.position_json
        from public.positions p
        left join public.instruments i on i.symbol = p.symbol
        where coalesce(p.quantity, 0) > 0
        order by p.last_updated_at desc nulls last, p.updated_at desc nulls last, p.symbol asc
        limit 20;
        """
    )
    rows = cur.fetchall()
    items = []
    for row in rows:
        qty = float(row.get("quantity") or 0)
        avg_price = float(row.get("avg_price") or 0)
        market_price = float(row.get("market_price") or 0)
        market_value = float(row.get("market_value") or (market_price * qty))
        buy_amount = avg_price * qty
        diff = market_value - buy_amount
        rate = (diff / buy_amount * 100) if buy_amount else 0.0
        signal = row.get("side") or "보유"
        items.append(
            {
                "ticker": row["symbol"],
                "name": row["name"],
                "buy_amount": round(buy_amount),
                "current_amount": round(market_value),
                "diff_amount": round(diff),
                "diff_rate": rate,
                "signal": signal,
                "collected_at": fmt_kst(row.get("last_updated_at")),
                "buy_amount_text": money(buy_amount),
                "current_amount_text": money(market_value),
                "diff_amount_text": f"{diff:+,.0f}원" if diff % 1 == 0 else f"{diff:+,.2f}원",
                "diff_rate_text": f"{rate:+.2f}%",
            }
        )
    return items


@dataclass
class WatchPayload:
    payload: dict[str, Any]
    source_label: str


def build_from_database(db_url: str) -> WatchPayload | None:
    try:
        with psycopg.connect(db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                theme_summary = build_theme_summary(cur)
                candidate_list = build_candidates(cur)
                watchlist = build_watchlist(cur)
                signal_on = build_signal_on(cur)
                buy_list = build_buy_list(cur)

        source_label = "db:public.news_events+watchlist_active+watchlist_candidates+surge_pool+positions"
        payload = deepcopy(DEFAULT_PAYLOAD)
        payload.update(
            {
                "title": "PKJ-Stock Watchlist",
                "subtitle": "오늘 관심 테마와 후보를 DB에서 읽어 10분 단위로 갱신하는 페이지",
                "market_state": "대기",
                "summary": "실제 PostgreSQL 데이터로 테마, 후보종목, 관심목록, 시그널, 보유종목을 보여줍니다.",
                "note": "watchlist_active / watchlist_candidates / surge_pool / news_events / positions를 읽어 생성합니다.",
                "source": source_label,
                "theme_summary": theme_summary,
                "candidate_list": candidate_list,
                "watchlist": watchlist,
                "signal_on": signal_on,
                "buy_list": buy_list,
            }
        )
        payload["tags"] = ["실데이터", "DB-first", "관심목록", "시그널", "보유종목"]
        return WatchPayload(payload=payload, source_label=source_label)
    except Exception as exc:
        print(f"db fetch failed: {exc}")
        return None


def build_from_source(source: dict | None, source_label: str) -> WatchPayload:
    source = source or {}
    payload = deepcopy(DEFAULT_PAYLOAD)
    payload.update(
        {
            "title": source.get("title", payload["title"]),
            "subtitle": source.get("subtitle", payload["subtitle"]),
            "market_state": source.get("market_state", payload["market_state"]),
            "summary": source.get("summary", payload["summary"]),
            "note": source.get("note", payload["note"]),
            "source": source_label,
        }
    )
    payload["tags"] = as_list(source.get("tags"), payload["tags"])
    payload["theme_summary"] = as_list(source.get("theme_summary"), payload["theme_summary"])
    payload["candidate_list"] = as_list(source.get("candidate_list"), payload["candidate_list"])
    payload["watchlist"] = as_list(source.get("watchlist"), payload["watchlist"])
    payload["signal_on"] = as_list(source.get("signal_on"), payload["signal_on"])
    payload["buy_list"] = as_list(source.get("buy_list"), payload["buy_list"])
    return WatchPayload(payload=payload, source_label=source_label)


def finalize_payload(watch: WatchPayload, source_name: str) -> dict[str, Any]:
    now_kst = datetime.now(KST)
    now_utc = datetime.now(timezone.utc)
    payload = deepcopy(watch.payload)
    payload["updated_at"] = now_kst.strftime("%Y-%m-%d %H:%M:%S KST")
    payload["generated_at_utc"] = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["source"] = source_name
    payload["counts"] = {
        "themes": len(payload["theme_summary"]),
        "candidates": len(payload["candidate_list"]),
        "watchlist": len(payload["watchlist"]),
        "signals": len(payload["signal_on"]),
        "buys": len(payload["buy_list"]),
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/watch_source.json"))
    parser.add_argument("--output", type=Path, default=Path("docs/data/watch.json"))
    parser.add_argument("--db-url", default=os.getenv("DATABASE_URL", DEFAULT_DB_URL))
    args = parser.parse_args()

    db_watch = build_from_database(args.db_url)
    if db_watch is not None:
      source_label = db_watch.source_label
      payload = finalize_payload(db_watch, source_label)
      mode = "database"
    else:
      source = load_json(args.source) if args.source.exists() else {}
      fallback_label = str(args.source) if args.source.exists() else "empty-state"
      source_watch = build_from_source(source, source_label=fallback_label)
      payload = finalize_payload(source_watch, source_watch.source_label)
      mode = "source"

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output} from {mode} ({payload['source']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
