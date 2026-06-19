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
EXCLUDED_PRODUCT_NAME_RE = (
    r"^(TIGER|KODEX|KOSEF|ARIRANG|ACE|SOL|KBSTAR|HANARO|KINDEX|PLUS|RISE|TREX|TIMEFOLIO|"
    r"WON|SMART|FOCUS|TRUE|MIRAE|KIWOOM|BNK|IBK|SHINYOUNG|NH|AUM)"
    r"|ETF|ETN|레버리지|인버스|선물|커버드콜|채권|달러"
)

DISPLAY_LABELS: dict[str, str] = {
    "daily_universe_scan": "일일 유니버스 스캔",
    "intraday_watchlist_refresh": "장중 워치리스트 재평가",
    "market_news_refresh": "뉴스/블로그 최신 수집",
    "pre_market_data_quality_check": "장전 데이터 품질/갭 점검",
    "session_state_heartbeat": "세션 하트비트",
    "intraday_risk_reconciliation": "장중 리스크/체결 대사",
    "broker_readiness_snapshot": "브로커 상태 스냅샷",
    "market_holiday_sync": "휴장일 동기화",
    "account_holdings_snapshot": "계좌 보유 스냅샷",
    "live_market_snapshot": "장중 시세 스냅샷",
    "intraday_bars_rollup": "장중 분봉 롤업",
    "strategy_pipeline_tick": "전략 후보 파이프라인",
    "entry_signal_execution": "진입 시그널 실행",
    "exit_signal_execution": "청산 시그널 실행",
    "post_market_analysis_summary": "장마감 분석 요약",
    "daily_pnl_and_risk_summary": "일일 손익/리스크 요약",
    "overnight_drift_monitor": "야간 드리프트 모니터",
    "kis_ranking_scan": "KIS 랭킹 스캔",
    "intraday_surge_detector": "장중 급등 감지",
    "signal_chart_capture": "시그널 차트 캡처",
    "market_ticks+orderbook": "시장 틱·호가",
    "breakout": "돌파",
    "lock": "잠금",
    "continuation": "추세 지속",
    "watch": "관심 유지",
}


def display_label(value: Any) -> str:
    raw = str(value or "")
    return DISPLAY_LABELS.get(raw.lower(), raw.replace("_", " ").replace("-", " ").replace("+", "·"))


DEFAULT_PAYLOAD: dict[str, Any] = {
    "title": "COOLPEACE AGENT WATCH",
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
    "portfolio": {},
    "risk_diagnostics": {},
    "cron_runs_today": {"as_of": "", "summary": [], "recent_failures": [], "stuck_running": []},
    # 2026-06-17: 신규 섹션 (모니터링 가시성 강화)
    "system_status": {
        "market_state": "unknown",  # pre_open / open / closed / after_hours / holiday
        "session_state": "NO_TRADE",  # TRADE_OK / NO_TRADE / BLOCKED
        "session_entered_at": "",
        "session_age_minutes": 0,
        "is_trade_window": False,
        "next_cron_due_in_minutes": 0,
        "kst_now": "",
    },
    "data_freshness": [],  # [{"key": "bars_1m", "label": "1분봉", "last": "...", "age_minutes": 5, "status": "fresh|stale|critical"}]
    "health": {  # cron_runs_today 요약
        "total_runs": 0,
        "ok": 0,
        "failed": 0,
        "timeout": 0,
        "running": 0,
        "stuck_running_count": 0,
        "ok_rate_pct": 0.0,
        "alert_level": "ok",  # ok / warn / critical
        "alerts": [],  # 사람이 읽을 수 있는 경고 메시지
    },
    "cron_recent_failures": [],  # 최근 24h 실패/timeout 10건 (cron_runs_today.recent_failures와 동일)
    "cron_stuck_running": [],  # 5분+ running 상태
    "strategy_candidates": {  # strategy_candidates 실시간 집계
        "as_of": "",
        "totals": {"buy": 0, "sell": 0, "ready_to_execute": 0, "blocked": 0, "all": 0},
        "today": {"buy": 0, "sell": 0, "ready_to_execute": 0, "blocked": 0, "all": 0},
        "ready_to_execute_list": [],  # READY_TO_EXECUTE 종목 (매수 후보)
        "blocked_list": [],  # BLOCKED 사유와 함께
    },
    "orders_today": {  # 오늘 매수/매도 side별 status
        "as_of": "",
        "totals": {"buy": 0, "sell": 0, "submitted": 0, "filled": 0, "rejected": 0, "cancelled": 0},
        "recent": [],  # 최근 5건
    },
    "open_positions": [],  # positions 테이블의 보유 종목 + 실시간 PnL
    "watchlist_active_freshness": {  # watchlist_active의 신선도
        "last_refreshed_at": "",
        "age_minutes": 0,
        "size": 0,
    },
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


def is_tradeable_equity_name(name: str | None) -> bool:
    if not name:
        return False
    import re

    return re.search(EXCLUDED_PRODUCT_NAME_RE, name, flags=re.IGNORECASE) is None


def tradeable_name_sql_filter(expr: str) -> str:
    return f"coalesce({expr}, '') !~* $${EXCLUDED_PRODUCT_NAME_RE}$$"


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


RISK_TAG_LABELS_KO: dict[str, str] = {
    "etf_etn": "ETF/ETN 제외",
    "leveraged_product": "레버리지·인버스 상품 위험",
    "lp_or_nav_gap_watch": "괴리율·LP호가 점검 필요",
    "tracking_error_risk": "추적오차/괴리율 위험",
    "lp_quote_risk": "LP 호가 공백 위험",
}


PRODUCT_RISK_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("etf_etn", ("ETF", "ETN", "TIGER", "KODEX", "KOSEF", "ARIRANG", "ACE", "SOL", "KBSTAR", "HANARO", "KINDEX", "RISE", "TIMEFOLIO")),
    ("leveraged_product", ("레버리지", "인버스", "2X", "-2X", "선물", "곱버스")),
    ("tracking_error_risk", ("합성", "TR", "커버드콜", "채권", "달러", "원유", "금", "구리")),
    ("lp_quote_risk", ("LP", "유동성공급자")),
)


def product_risk_tags(name: Any, meta: dict[str, Any] | None = None) -> list[str]:
    meta = meta if isinstance(meta, dict) else {}
    tags = set(str(tag).strip().lower() for tag in safe_tag_list(meta.get("risk_tags")))
    text = str(name or "").upper()
    for tag, keywords in PRODUCT_RISK_KEYWORDS:
        if any(keyword.upper() in text for keyword in keywords):
            tags.add(tag)
    if meta.get("lp_quote_missing") or meta.get("indicative_value_gap_pct"):
        tags.add("lp_or_nav_gap_watch")
    return sorted(tag for tag in tags if tag)


def risk_label_list(tags: list[str]) -> list[str]:
    return [RISK_TAG_LABELS_KO.get(str(tag).lower(), str(tag)) for tag in tags]


def source_provenance(*parts: Any) -> list[str]:
    values: list[str] = []
    for part in parts:
        if isinstance(part, list):
            values.extend(str(v) for v in part if str(v).strip())
        elif part not in (None, ""):
            values.append(str(part))
    return list(dict.fromkeys(values))


def _theme_score(item: dict[str, Any]) -> float:
    value = item.get("active_score") if item.get("active_score") is not None else item.get("score")
    if value is None:
        value = item.get("candidate_score")
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def assign_theme_roles(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(str(item.get("theme") or "미분류"), []).append(item)
    for group in grouped.values():
        ranked = sorted(group, key=_theme_score, reverse=True)
        leader_score = _theme_score(ranked[0]) if ranked else 0.0
        for idx, item in enumerate(ranked):
            item_score = _theme_score(item)
            ratio = (item_score / leader_score) if leader_score > 0 else 0.0
            role = "leader" if idx == 0 and item_score > 0 else ("follower" if ratio >= 0.65 else "watch")
            item["theme_role"] = role
            item["theme_role_label"] = {"leader": "테마 리더", "follower": "후행 동조", "watch": "관심 관찰"}.get(role, role)
            item["theme_role_score"] = round(ratio * 100, 1) if leader_score > 0 else 0.0
    return items


def build_theme_summary(cur) -> list[dict[str, Any]]:
    cur.execute(
        f"""
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
                "theme": display_label(tag),
                "score": score_letter(cnt, max_catalyst),
                "reason": f"최근 {cnt}건 뉴스 태그 · 최고 촉매 {max_catalyst:.1f}",
                "updated_at": fmt_kst(last_at),
            }
        )
    return summary


def build_candidates(cur) -> list[dict[str, Any]]:
    cur.execute(
        f"""
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
        where {tradeable_name_sql_filter("coalesce(i.name, c.meta_json->>'name', c.symbol)")}
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
        theme = display_label(theme_tags[0]) if theme_tags else display_label(hot_override.get("reason") or surge.get("stage") or row.get("source") or "watchlist")
        signal = display_label(hot_override.get("reason") or surge.get("stage") or row.get("source") or f"score {float(row.get('score') or 0):.1f}")
        collected_at = row.get("active_updated_at") or row.get("list_date")
        risk_tags = product_risk_tags(row.get("name"), active_meta or meta)
        provenance = source_provenance(row.get("source") or "watchlist_candidates", active_meta.get("sources"), hot_override.get("source"), surge.get("source"))
        items.append(
            {
                "ticker": row["symbol"],
                "name": row["name"],
                "theme": theme,
                "signal": signal,
                "collected_at": fmt_kst(collected_at),
                "source": row.get("source") or "watchlist_candidates",
                "source_provenance": provenance,
                "risk_tags": risk_tags,
                "risk_labels": risk_label_list(risk_tags),
                "block_reason_label": " / ".join(risk_label_list(risk_tags)),
                "candidate_score": float(row.get("score") or 0),
                "active_score": float(row.get("active_score") or 0) if row.get("active_score") is not None else None,
            }
        )
    return assign_theme_roles(items)


def build_watchlist(cur) -> list[dict[str, Any]]:
    cur.execute(
        f"""
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
          and {tradeable_name_sql_filter("coalesce(i.name, c.meta_json->>'name', a.symbol)")}
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
            reason_parts.append("태그: " + ", ".join(display_label(tag) for tag in theme_tags[:3]))
        if surge.get("stage"):
            reason_parts.append(f"시그널: {display_label(surge.get('stage'))}")
        if candidate.get("score") is not None:
            reason_parts.append(f"후보점수 {float(candidate['score']):.1f}")
        if not reason_parts and sources:
            reason_parts.append("sources: " + ", ".join(display_label(v) for v in sources[:3]))
        if not reason_parts:
            reason_parts.append("관심 유지")
        signal = display_label(hot_override.get("reason") or surge.get("stage") or (theme_tags[0] if theme_tags else row.get("candidate_source") or "watch"))
        risk_tags = product_risk_tags(row.get("name"), meta)
        provenance = source_provenance(row.get("candidate_source") or "watchlist_active", sources, hot_override.get("source"), surge.get("source"))
        items.append(
            {
                "ticker": row["symbol"],
                "name": row["name"],
                "reason": " / ".join(reason_parts),
                "signal": signal,
                "collected_at": fmt_kst(meta.get("selected_at") or row.get("updated_at")),
                "score": float(row.get("score") or 0),
                "source": row.get("candidate_source") or "watchlist_active",
                "source_provenance": provenance,
                "risk_tags": risk_tags,
                "risk_labels": risk_label_list(risk_tags),
                "block_reason_label": " / ".join(risk_label_list(risk_tags)),
                "score_breakdown": meta.get("score_breakdown") or {},
                "strategy_variant": meta.get("strategy_variant") or "",
                "strategy_variant_reason": meta.get("strategy_variant_reason") or "",
                "signal_hits": safe_tag_list(meta.get("signal_hits")),
                "derived_hot_override": meta.get("derived_hot_override") or {},
                "hot_override": hot_override or {},
            }
        )
    return assign_theme_roles(items)


def build_signal_on(cur) -> list[dict[str, Any]]:
    cur.execute(
        f"""
        with ranked as (
          select
            s.symbol,
            coalesce(i.name, s.symbol) as name,
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
          left join public.instruments i on i.symbol = s.symbol
          where s.captured_at >= now() - interval '24 hours'
            and {tradeable_name_sql_filter("coalesce(i.name, s.symbol)")}
        )
        select * from ranked where rn = 1 order by captured_at desc nulls last, trigger_strength desc nulls last, symbol asc limit 8;
        """
    )
    rows = cur.fetchall()
    items = []
    for row in rows:
        payload = row.get("payload_json") or {}
        stage = display_label(payload.get("stage") or row.get("kind") or row.get("source") or "signal")
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


def _normalize_symbol(code: Any) -> str:
    text = str(code or "").strip().upper()
    if text.startswith("A") and text[1:].isdigit():
        return text[1:]
    if text.startswith("*") and len(text) > 2:
        return _normalize_symbol(text.lstrip("*"))
    return text


def _signed_money_text(value: float) -> str:
    return f"{value:+,.0f}원"


def _signed_rate_text(value: float) -> str:
    return f"{value:+.2f}%"


def _latest_kiwoom_account_snapshot(cur) -> dict[str, Any] | None:
    cur.execute(
        """
        select id, account_no, observed_at, normalized_json, raw_json
        from public.account_events
        where broker_name = 'kiwoom'
          and event_type = 'account_status_snapshot'
        order by observed_at desc, id desc
        limit 1
        """
    )
    return cur.fetchone()


def _baseline_for_account(cur, account_no: str) -> dict[str, Any] | None:
    cur.execute(
        """
        select initial_total_assets, observed_at, source_account_event_id, note
        from public.account_capital_baselines
        where broker_name = 'kiwoom' and account_no = %s
        order by id desc
        limit 1
        """,
        [account_no],
    )
    return cur.fetchone()


def _extract_holdings_from_snapshot(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not snapshot:
        return []
    normalized = snapshot.get("normalized_json") or {}
    raw_payload = normalized.get("raw_payload") or snapshot.get("raw_json") or {}
    balance = raw_payload.get("balance_and_holdings") if isinstance(raw_payload, dict) else {}
    holdings = balance.get("acnt_evlt_remn_indv_tot") if isinstance(balance, dict) else []
    if not isinstance(holdings, list):
        return []
    items: list[dict[str, Any]] = []
    for item in holdings:
        if not isinstance(item, dict):
            continue
        qty = float(item.get("rmnd_qty") or item.get("trde_able_qty") or item.get("qty") or 0)
        buy_amount = float(item.get("pur_amt") or (float(item.get("pur_pric") or 0) * qty) or 0)
        current_amount = float(item.get("evlt_amt") or (float(item.get("cur_prc") or 0) * qty) or 0)
        diff = current_amount - buy_amount
        rate = (diff / buy_amount * 100) if buy_amount else 0.0
        symbol = _normalize_symbol(item.get("stk_cd") or item.get("symbol") or item.get("code"))
        name = str(item.get("stk_nm") or item.get("name") or symbol or "")
        items.append(
            {
                "ticker": symbol,
                "name": name,
                "buy_amount": round(buy_amount),
                "current_amount": round(current_amount),
                "diff_amount": round(diff),
                "diff_rate": rate,
                "signal": "보유",
                "collected_at": fmt_kst(snapshot.get("observed_at")),
                "buy_amount_text": money(buy_amount),
                "current_amount_text": money(current_amount),
                "diff_amount_text": _signed_money_text(diff),
                "diff_rate_text": _signed_rate_text(rate),
                "source": "account_status_snapshot",
            }
        )
    return items


def build_buy_list(cur) -> list[dict[str, Any]]:
    cur.execute(
        f"""
        select
          i.symbol,
          coalesce(i.name, i.symbol) as name,
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
        left join public.instruments i on i.id = p.instrument_id
        where coalesce(p.quantity, 0) > 0
          and {tradeable_name_sql_filter("coalesce(i.name, i.symbol)")}
        order by p.last_updated_at desc nulls last, p.updated_at desc nulls last, i.symbol asc
        limit 20;
        """
    )
    rows = cur.fetchall()
    if not rows:
        rows = _extract_holdings_from_snapshot(_latest_kiwoom_account_snapshot(cur))
        return rows

    items = []
    for row in rows:
        qty = float(row.get("quantity") or 0)
        avg_price = float(row.get("avg_price") or 0)
        market_price = float(row.get("market_price") or 0)
        market_value = float(row.get("market_value") or (market_price * qty))
        buy_amount = avg_price * qty
        diff = market_value - buy_amount
        rate = (diff / buy_amount * 100) if buy_amount else 0.0
        signal = display_label(row.get("side") or "보유")
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
                "diff_amount_text": _signed_money_text(diff),
                "diff_rate_text": _signed_rate_text(rate),
                "source": "positions",
            }
        )
    return items


def build_portfolio_summary(cur) -> dict[str, Any] | None:
    snapshot = _latest_kiwoom_account_snapshot(cur)
    if not snapshot:
        return None
    normalized = snapshot.get("normalized_json") or {}
    current_total_assets = float(normalized.get("total_assets") or 0)
    if not current_total_assets:
        current_total_assets = float((normalized.get("available_cash") or 0)) + float((normalized.get("holdings_value") or 0))
    baseline = _baseline_for_account(cur, str(snapshot.get("account_no") or ""))
    initial_total_assets = float(baseline.get("initial_total_assets") or current_total_assets) if baseline else current_total_assets
    growth_amount = current_total_assets - initial_total_assets
    growth_rate = (growth_amount / initial_total_assets * 100) if initial_total_assets else 0.0
    return {
        "account_no": snapshot.get("account_no") or "",
        "updated_at": fmt_kst(snapshot.get("observed_at")),
        "current_total_assets": round(current_total_assets),
        "current_total_assets_text": money(current_total_assets),
        "initial_total_assets": round(initial_total_assets),
        "initial_total_assets_text": money(initial_total_assets),
        "growth_amount": round(growth_amount),
        "growth_amount_text": _signed_money_text(growth_amount),
        "growth_rate": growth_rate,
        "growth_rate_text": _signed_rate_text(growth_rate),
        "cash_text": money(normalized.get("available_cash")),
        "holdings_value_text": money(normalized.get("holdings_value")),
        "baseline_note": baseline.get("note") if baseline else "baseline not yet seeded",
    }


def build_risk_diagnostics(cur) -> dict[str, Any] | None:
    cur.execute(
        """
        select id, observed_at, status, blocked_reason, detail_json
        from public.collector_status_snapshots
        where collector_name = 'intraday_risk_reconciliation'
        order by observed_at desc, id desc
        limit 1;
        """
    )
    row = cur.fetchone()
    if not row:
        return None
    detail = row.get("detail_json") or {}
    if not isinstance(detail, dict):
        detail = {}
    local = detail.get("local") or {}
    broker = detail.get("broker") or {}
    comparison = detail.get("comparison") or {}
    mismatches = comparison.get("mismatches") or []
    if not isinstance(mismatches, list):
        mismatches = [str(mismatches)]
    return {
        "status": str(row.get("status") or comparison.get("status") or "unknown"),
        "observed_at": fmt_kst(row.get("observed_at")),
        "blocked_reason": str(row.get("blocked_reason") or ""),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:5],
        "local_open_orders": int(local.get("open_orders") or 0),
        "local_position_count": int(local.get("position_count") or 0),
        "local_gross_exposure": float(local.get("gross_exposure") or 0),
        "broker_open_orders": int(broker.get("broker_open_orders") or 0),
        "broker_position_count": int(broker.get("broker_position_count") or 0),
        "broker_available_cash": broker.get("broker_available_cash"),
        "broker_can_trade": bool(broker.get("broker_can_trade")),
        "product_risk_policy": {
            "risk_tags": list(RISK_TAG_LABELS_KO.keys()),
            "risk_labels": list(RISK_TAG_LABELS_KO.values()),
            "block_reason_label": "ETF/ETN·레버리지·인버스·괴리율·LP호가 위험은 개별주 후보에서 제외/차단",
        },
        "detail_source": "collector_status_snapshots",
    }


def build_cron_runs_today(cur, *, top_failures: int = 5) -> dict[str, Any]:
    """cron_job_runs 오늘 집계 + 최근 실패 N건 (2026-06-17).

    대시보드에서 '배치 정상 작동?' 빠르게 확인용. cron_job_runs 테이블이 비어있으면
    추적 미적용 잡만 있다는 뜻 (현재는 6개 핵심 잡 + purge 잡 적용).
    """
    summary_rows = fetch_all_safe(
        cur,
        """
        SELECT job_name,
               count(*)::int AS total,
               sum(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END)::int AS ok,
               sum(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)::int AS fail,
               sum(CASE WHEN status = 'timeout' THEN 1 ELSE 0 END)::int AS timeout,
               sum(CASE WHEN status = 'running' THEN 1 ELSE 0 END)::int AS running,
               max(started_at) AS last_started_at
        FROM cron_job_runs
        WHERE started_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Seoul')
        GROUP BY job_name
        ORDER BY job_name ASC
        """,
    )
    recent_failures = fetch_all_safe(
        cur,
        """
        SELECT job_name, started_at, finished_at, duration_ms, exit_code,
               substring(error_text, 1, 240) AS error_excerpt
        FROM cron_job_runs
        WHERE status IN ('failed', 'timeout')
          AND started_at >= now() - interval '24 hours'
        ORDER BY started_at DESC
        LIMIT %s
        """,
        [int(top_failures)],
    )
    return {
        "as_of": fmt_kst(datetime.now(ZoneInfo("Asia/Seoul"))),
        "summary": summary_rows or [],
        "recent_failures": recent_failures or [],
        "stuck_running": fetch_all_safe(
            cur,
            """
            SELECT job_name, started_at
            FROM cron_job_runs
            WHERE status = 'running' AND started_at < now() - interval '5 minutes'
            ORDER BY started_at ASC
            """
        )
        or [],
    }


# 2026-06-17: 신규 헬퍼/빌더 (대시보드 모니터링 가시성 강화)

KST_NOW_FN = lambda: datetime.now(KST)  # noqa: E731


def _kst_now_text() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def _age_minutes(dt: Any) -> int:
    """DB timestamp → KST 기준 분 단위 age. None/빈값은 -1 (unknown)."""
    if dt is None or dt == "":
        return -1
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            return -1
    if not isinstance(dt, datetime):
        return -1
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(KST) - dt.astimezone(KST)
    return int(delta.total_seconds() // 60)


def _freshness_status(age_minutes: int, *, threshold_stale: int = 30, threshold_critical: int = 120) -> str:
    """age_minutes → fresh (≤threshold_stale) / stale (≤threshold_critical) / critical."""
    if age_minutes < 0:
        return "unknown"
    if age_minutes <= threshold_stale:
        return "fresh"
    if age_minutes <= threshold_critical:
        return "stale"
    return "critical"


def _market_state_label(kst_now: datetime) -> dict[str, Any]:
    """현재 KST 시각 기준 market_state (pre_open / open / closed / after_hours / holiday).

    한국 시장: 09:00~15:30 = open, 15:30~16:00 = after_hours, 16:00~次日08:30 = closed, 08:30~09:00 = pre_open.
    """
    weekday = kst_now.weekday()  # 0=월 ~ 6=일
    hm = kst_now.hour * 60 + kst_now.minute
    if weekday >= 5:
        state = "holiday"
    elif 540 <= hm < 930:  # 09:00~15:30
        state = "open"
    elif 930 <= hm < 960:  # 15:30~16:00
        state = "after_hours"
    elif 510 <= hm < 540:  # 08:30~09:00
        state = "pre_open"
    else:
        state = "closed"
    is_trade = state == "open"
    return {"market_state": state, "is_trade_window": is_trade}


def build_system_status(cur) -> dict[str, Any]:
    """현재 KST 시각 기준 market_state + session_state + last_heartbeat."""
    kst_now = datetime.now(KST)
    market = _market_state_label(kst_now)
    # session_state (가장 최근 row)
    sess = fetch_one_safe(
        cur,
        """
        SELECT current_state, previous_state, entered_at, reason_code
        FROM session_state
        ORDER BY session_id DESC
        LIMIT 1
        """,
    )
    cur_state = (sess or {}).get("current_state") or "NO_TRADE"
    entered_at = (sess or {}).get("entered_at")
    age_min = _age_minutes(entered_at)
    return {
        "market_state": market["market_state"],
        "is_trade_window": market["is_trade_window"],
        "session_state": cur_state,
        "session_entered_at": fmt_kst(entered_at) if entered_at else "",
        "session_age_minutes": age_min,
        "kst_now": _kst_now_text(),
    }


def build_data_freshness(cur) -> list[dict[str, Any]]:
    """각 데이터 소스의 신선도 (bars_1m / market_ticks / strategy_candidates / orders / positions / account_events / cron_job_runs / watchlist_active / watchlist_candidates).

    임계치: 1분 단위 = 5분, 5분 단위 = 15분, 그 외 = 30분 stale / 120분 critical.
    """
    out: list[dict[str, Any]] = []
    # 1분 단위 (5분 stale / 30분 critical)
    one_min = fetch_all_safe(
        cur,
        """
        SELECT 'bars_1m'::text AS key, '1분봉' AS label, MAX(bar_time) AS last FROM bars_1m WHERE symbol='005930'
        UNION ALL SELECT 'market_ticks', '장중 틱(삼성)', MAX(observed_at) FROM market_ticks WHERE symbol='005930'
        """
    )
    for row in one_min:
        age = _age_minutes(row.get("last"))
        out.append(
            {
                "key": row["key"],
                "label": row["label"],
                "last": fmt_kst(row.get("last")) if row.get("last") else "—",
                "age_minutes": age,
                "status": _freshness_status(age, threshold_stale=5, threshold_critical=30),
                "tier": "1min",
            }
        )
    # 5분 단위 (15분 stale / 60분 critical)
    five_min = fetch_all_safe(
        cur,
        """
        SELECT 'watchlist_active'::text AS key, '워치리스트' AS label, MAX(updated_at) AS last FROM watchlist_active WHERE is_active = true
        UNION ALL SELECT 'watchlist_candidates', '후보 종목 풀', MAX(updated_at) FROM watchlist_candidates WHERE list_date = CURRENT_DATE
        UNION ALL SELECT 'strategy_candidates', '매수 후보', MAX(created_at) FROM strategy_candidates WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Seoul')
        UNION ALL SELECT 'cron_job_runs', '배치 실행 기록', MAX(started_at) FROM cron_job_runs
        """
    )
    for row in five_min:
        age = _age_minutes(row.get("last"))
        out.append(
            {
                "key": row["key"],
                "label": row["label"],
                "last": fmt_kst(row.get("last")) if row.get("last") else "—",
                "age_minutes": age,
                "status": _freshness_status(age, threshold_stale=15, threshold_critical=60),
                "tier": "5min",
            }
        )
    # 그 외 (30분 stale / 120분 critical)
    others = fetch_all_safe(
        cur,
        """
        SELECT 'orders'::text AS key, '주문' AS label, MAX(created_at) AS last FROM orders
        UNION ALL SELECT 'positions', '보유 포지션', MAX(updated_at) FROM positions WHERE quantity > 0
        UNION ALL SELECT 'account_events', '계좌 스냅샷', MAX(observed_at) FROM account_events WHERE event_type = 'account_status_snapshot'
        """
    )
    for row in others:
        age = _age_minutes(row.get("last"))
        out.append(
            {
                "key": row["key"],
                "label": row["label"],
                "last": fmt_kst(row.get("last")) if row.get("last") else "—",
                "age_minutes": age,
                "status": _freshness_status(age, threshold_stale=30, threshold_critical=120),
                "tier": "snapshot",
            }
        )
    return out


def build_health(cur, cron_summary: list[dict[str, Any]], stuck_running: list[dict[str, Any]]) -> dict[str, Any]:
    """cron_runs_today → 사람이 읽을 수 있는 health summary + alerts."""
    total = sum(int(r.get("total") or 0) for r in cron_summary)
    ok = sum(int(r.get("ok") or 0) for r in cron_summary)
    failed = sum(int(r.get("fail") or 0) for r in cron_summary)
    timeout = sum(int(r.get("timeout") or 0) for r in cron_summary)
    running = sum(int(r.get("running") or 0) for r in cron_summary)
    ok_rate = (ok / total * 100) if total else 0.0
    stuck_n = len(stuck_running or [])

    alerts: list[str] = []
    if failed >= 1:
        alerts.append(f"실패 {failed}건: 최근 실패는 cron_recent_failures 확인")
    if timeout >= 1:
        alerts.append(f"timeout {timeout}건 (5분+ 실행 잡 점검)")
    if stuck_n >= 1:
        alerts.append(f"stuck_running {stuck_n}건: 5분+ running 상태")
    if total >= 1 and ok_rate < 80.0:
        alerts.append(f"성공률 {ok_rate:.1f}% (80% 미만)")

    if failed >= 5 or timeout >= 3 or stuck_n >= 3 or (total >= 5 and ok_rate < 60.0):
        alert_level = "critical"
    elif failed >= 1 or timeout >= 1 or stuck_n >= 1 or (total >= 5 and ok_rate < 90.0):
        alert_level = "warn"
    else:
        alert_level = "ok"

    return {
        "total_runs": total,
        "ok": ok,
        "failed": failed,
        "timeout": timeout,
        "running": running,
        "stuck_running_count": stuck_n,
        "ok_rate_pct": round(ok_rate, 1),
        "alert_level": alert_level,
        "alerts": alerts,
    }


def build_strategy_candidates(cur) -> dict[str, Any]:
    """strategy_candidates 실시간 집계 + READY_TO_EXECUTE/BLOCKED 목록."""
    as_of = fmt_kst(datetime.now(KST))
    # 전체 카운트 (instrument_id → instruments.name 조인)
    rows = fetch_all_safe(
        cur,
        f"""
        SELECT sc.side, sc.candidate_status, COUNT(*)::int AS cnt
        FROM strategy_candidates sc
        LEFT JOIN public.instruments i ON i.id = sc.instrument_id
        WHERE {tradeable_name_sql_filter("coalesce(i.name, '')")}
        GROUP BY 1, 2
        """
    ) or []
    totals: dict[str, int] = {"buy": 0, "sell": 0, "ready_to_execute": 0, "blocked": 0, "all": 0}
    for r in rows:
        side = (r.get("side") or "").upper()
        status = r.get("candidate_status") or ""
        cnt = int(r.get("cnt") or 0)
        totals["all"] += cnt
        if side in ("BUY", "SELL"):
            totals[side.lower()] += cnt
        if status in ("READY_TO_EXECUTE", "APPROVED", "ORDER_SUBMITTED"):
            totals["ready_to_execute"] += cnt
        if status == "BLOCKED":
            totals["blocked"] += cnt

    # 오늘 카운트
    today_rows = fetch_all_safe(
        cur,
        """
        SELECT side, candidate_status, COUNT(*)::int AS cnt
        FROM strategy_candidates
        WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Seoul')
        GROUP BY 1, 2
        """
    ) or []
    today: dict[str, int] = {"buy": 0, "sell": 0, "ready_to_execute": 0, "blocked": 0, "all": 0}
    for r in today_rows:
        side = (r.get("side") or "").upper()
        status = r.get("candidate_status") or ""
        cnt = int(r.get("cnt") or 0)
        today["all"] += cnt
        if side in ("BUY", "SELL"):
            today[side.lower()] += cnt
        if status in ("READY_TO_EXECUTE", "APPROVED", "ORDER_SUBMITTED"):
            today["ready_to_execute"] += cnt
        if status == "BLOCKED":
            today["blocked"] += cnt

    # READY_TO_EXECUTE 종목
    ready = fetch_all_safe(
        cur,
        f"""
        SELECT sc.symbol_via_inst AS symbol, i.name, sc.side, sc.candidate_status,
               sc.priority_score, sc.confidence_score, sc.entry_price,
               sc.created_at
        FROM (
          SELECT sc.id, sc.instrument_id, sc.side, sc.candidate_status,
                 sc.priority_score, sc.confidence_score, sc.entry_price, sc.created_at,
                 i.symbol AS symbol_via_inst
          FROM strategy_candidates sc
          LEFT JOIN public.instruments i ON i.id = sc.instrument_id
          WHERE sc.candidate_status IN ('READY_TO_EXECUTE', 'APPROVED', 'ORDER_SUBMITTED')
            AND {tradeable_name_sql_filter("coalesce(i.name, '')")}
          ORDER BY sc.priority_score DESC NULLS LAST, sc.created_at DESC
          LIMIT 10
        ) sc
        LEFT JOIN public.instruments i ON i.id = sc.instrument_id
        """
    ) or []
    ready_list = []
    for r in ready:
        ready_list.append(
            {
                "ticker": r.get("symbol"),
                "name": r.get("name"),
                "side": r.get("side"),
                "status": r.get("candidate_status"),
                "priority_score": float(r.get("priority_score") or 0),
                "confidence_score": float(r.get("confidence_score") or 0),
                "entry_price": float(r.get("entry_price") or 0),
                "entry_price_text": money(r.get("entry_price")),
                "created_at": fmt_kst(r.get("created_at")),
            }
        )

    # BLOCKED 종목
    blocked = fetch_all_safe(
        cur,
        f"""
        SELECT i.symbol, i.name, sc.side, sc.candidate_status,
               sc.invalidation_reason, sc.payload_json->>'block_reason' AS block_reason,
               LEFT(sc.payload_json::text, 200) AS payload_excerpt, sc.created_at
        FROM strategy_candidates sc
        LEFT JOIN public.instruments i ON i.id = sc.instrument_id
        WHERE sc.candidate_status = 'BLOCKED'
          AND sc.created_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Seoul')
          AND {tradeable_name_sql_filter("coalesce(i.name, '')")}
        ORDER BY sc.created_at DESC
        LIMIT 10
        """
    ) or []
    blocked_list = []
    for r in blocked:
        reason = r.get("invalidation_reason") or r.get("block_reason") or ""
        if not reason and r.get("payload_excerpt"):
            reason = str(r.get("payload_excerpt"))[:150]
        blocked_list.append(
            {
                "ticker": r.get("symbol"),
                "name": r.get("name"),
                "side": r.get("side"),
                "status": r.get("candidate_status"),
                "reason": str(reason)[:200],
                "reason_short": str(reason)[:80],
                "created_at": fmt_kst(r.get("created_at")),
            }
        )

    return {
        "as_of": as_of,
        "totals": totals,
        "today": today,
        "ready_to_execute_list": ready_list,
        "blocked_list": blocked_list,
    }


def build_orders_today(cur) -> dict[str, Any]:
    """오늘 매수/매도 side별 status + 최근 5건."""
    as_of = fmt_kst(datetime.now(KST))
    rows = fetch_all_safe(
        cur,
        """
        SELECT side, status, COUNT(*)::int AS cnt
        FROM orders
        WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Seoul')
        GROUP BY 1, 2
        """
    ) or []
    totals = {"buy": 0, "sell": 0, "submitted": 0, "filled": 0, "rejected": 0, "cancelled": 0}
    for r in rows:
        side = (r.get("side") or "").upper()
        status = (r.get("status") or "").upper()
        cnt = int(r.get("cnt") or 0)
        if side in ("BUY", "SELL"):
            totals[side.lower()] += cnt
        if status == "SUBMITTED":
            totals["submitted"] += cnt
        elif status in ("FILLED", "PARTIALLY_FILLED"):
            totals["filled"] += cnt
        elif status == "REJECTED":
            totals["rejected"] += cnt
        elif status in ("CANCELLED", "EXPIRED"):
            totals["cancelled"] += cnt

    recent = fetch_all_safe(
        cur,
        f"""
        SELECT o.order_key, o.side, o.status, o.quantity, o.limit_price, o.created_at, o.submitted_at,
               coalesce(i.symbol, '—') AS symbol, coalesce(i.name, '—') AS name
        FROM orders o
        LEFT JOIN public.instruments i ON i.id = o.instrument_id
        WHERE {tradeable_name_sql_filter("coalesce(i.name, i.symbol)")}
        ORDER BY o.created_at DESC
        LIMIT 5
        """
    ) or []
    recent_list = []
    for r in recent:
        recent_list.append(
            {
                "order_key": r.get("order_key"),
                "side": r.get("side"),
                "status": r.get("status"),
                "symbol": r.get("symbol"),
                "name": r.get("name"),
                "quantity": float(r.get("quantity") or 0),
                "limit_price": float(r.get("limit_price") or 0),
                "limit_price_text": money(r.get("limit_price")),
                "created_at": fmt_kst(r.get("created_at")),
                "submitted_at": fmt_kst(r.get("submitted_at")),
            }
        )
    return {"as_of": as_of, "totals": totals, "recent": recent_list}


def build_open_positions(cur) -> list[dict[str, Any]]:
    """positions 테이블의 보유 종목 + 실시간 손익.

    (quantity > 0, ETF/ETN/레버리지/인버스 제외)
    """
    rows = fetch_all_safe(
        cur,
        f"""
        SELECT i.symbol, coalesce(i.name, i.symbol) AS name, p.side, p.quantity, p.avg_price,
               p.market_price, p.market_value, p.realized_pnl, p.unrealized_pnl,
               p.last_updated_at
        FROM public.positions p
        LEFT JOIN public.instruments i ON i.id = p.instrument_id
        WHERE coalesce(p.quantity, 0) > 0
          AND {tradeable_name_sql_filter("coalesce(i.name, i.symbol)")}
        ORDER BY p.market_value DESC NULLS LAST
        """
    ) or []
    items = []
    for r in rows:
        qty = float(r.get("quantity") or 0)
        avg = float(r.get("avg_price") or 0)
        mkt = float(r.get("market_price") or 0)
        mv = float(r.get("market_value") or 0)
        if mv == 0 and mkt and qty:
            mv = mkt * qty
        cost = avg * qty
        upnl = float(r.get("unrealized_pnl") or 0) or (mv - cost)
        rate = (upnl / cost * 100) if cost else 0.0
        items.append(
            {
                "ticker": r.get("symbol"),
                "name": r.get("name"),
                "side": r.get("side") or "BUY",
                "quantity": qty,
                "avg_price": avg,
                "avg_price_text": money(avg),
                "market_price": mkt,
                "market_price_text": money(mkt),
                "market_value": mv,
                "market_value_text": money(mv),
                "unrealized_pnl": upnl,
                "unrealized_pnl_text": _signed_money_text(upnl),
                "unrealized_pnl_rate": round(rate, 2),
                "unrealized_pnl_rate_text": _signed_rate_text(rate),
                "last_updated_at": fmt_kst(r.get("last_updated_at")),
            }
        )
    return items


def build_watchlist_active_freshness(cur) -> dict[str, Any]:
    """watchlist_active의 신선도 (active 종목 수 + 마지막 갱신)."""
    row = fetch_one_safe(
        cur,
        """
        SELECT MAX(updated_at) AS last, COUNT(*)::int AS size
        FROM watchlist_active
        WHERE is_active = true
        """
    ) or {}
    last = row.get("last")
    return {
        "last_refreshed_at": fmt_kst(last) if last else "",
        "age_minutes": _age_minutes(last),
        "size": int(row.get("size") or 0),
    }


def fetch_one_safe(cur, sql: str, params: list | None = None) -> dict[str, Any] | None:
    """dict_row 사용 시 cur.fetchone()이 이미 dict를 반환하므로 그대로 사용."""
    try:
        cur.execute(sql, params or [])
        row = cur.fetchone()
        if row is None:
            return None
        if cur.description is None:
            return None
        # row가 이미 dict (dict_row)면 그대로 사용
        if isinstance(row, dict):
            return row
        # 일반 row면 zip으로 dict 변환
        cols = [d.name for d in cur.description]
        return dict(zip(cols, row))
    except Exception:
        return None


def fetch_all_safe(cur, sql: str, params: list | None = None) -> list[dict[str, Any]]:
    """cron_job_runs 같은 신규 테이블이 아직 없을 때 빈 리스트로 fallback.

    dict_row 사용 시 cur.fetchall()이 이미 dict list를 반환하므로 그대로 사용.
    일반 cursor일 때는 컬럼명을 zip해서 dict list로 변환.
    """
    try:
        cur.execute(sql, params or [])
        rows = cur.fetchall()
        if not rows:
            return []
        if cur.description is None:
            return []
        # row가 이미 dict (dict_row)면 그대로 사용
        if isinstance(rows[0], dict):
            return list(rows)
        # 일반 row면 zip으로 dict 변환
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]
    except Exception:
        return []


@dataclass
class WatchPayload:
    payload: dict[str, Any]
    source_label: str


def build_from_database(db_url: str) -> WatchPayload | None:
    try:
        with psycopg.connect(db_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                # 2026-06-17: 시스템 상태 (시각 + market + session) 먼저
                system_status = build_system_status(cur)
                theme_summary = build_theme_summary(cur)
                candidate_list = build_candidates(cur)
                watchlist = build_watchlist(cur)
                signal_on = build_signal_on(cur)
                buy_list = build_buy_list(cur)
                portfolio = build_portfolio_summary(cur) or {}
                risk_diagnostics = build_risk_diagnostics(cur) or {}
                cron_runs_today = build_cron_runs_today(cur)
                # 신규 7개 (모니터링 가시성)
                data_freshness = build_data_freshness(cur)
                health = build_health(cur, cron_runs_today.get("summary", []), cron_runs_today.get("stuck_running", []))
                strategy_candidates = build_strategy_candidates(cur)
                orders_today = build_orders_today(cur)
                open_positions = build_open_positions(cur)
                watchlist_active_freshness = build_watchlist_active_freshness(cur)
                # 2026-06-17: market_state를 시각 기준으로 갱신 (이전 빌더의 '대기' hardcode 대체)
                market_state_text = {
                    "pre_open": "장 시작 전",
                    "open": "장중",
                    "after_hours": "시간외",
                    "closed": "장 마감",
                    "holiday": "휴장",
                    "unknown": "상태 불명",
                }.get(system_status.get("market_state", "unknown"), "상태 불명")
                # system_status에 다음 cron due까지의 시간도 계산 (간단 추정)
                next_due_min = 0
                if system_status.get("is_trade_window"):
                    # 장중이면 다음 5분 단위 cron까지의 시간
                    now_min = datetime.now(KST).minute
                    next_due_min = max(0, 5 - (now_min % 5))
                else:
                    next_due_min = 0
                system_status["next_cron_due_in_minutes"] = next_due_min
                system_status["market_state_text"] = market_state_text

                source_label = "db:public.news_events+watchlist_active+watchlist_candidates+surge_pool+positions+account_events+account_capital_baselines+collector_status_snapshots+cron_job_runs+strategy_candidates+orders+session_state"
                payload = deepcopy(DEFAULT_PAYLOAD)
                payload.update(
                    {
                        "title": "COOLPEACE AGENT WATCH",
                        "subtitle": "오늘 관심 테마와 후보를 DB에서 읽어 10분 단위로 갱신하는 페이지",
                        "market_state": market_state_text,
                        "summary": "실제 PostgreSQL 데이터로 테마, 후보종목, 관심목록, 시그널, 보유종목을 보여줍니다.",
                        "note": "watchlist_active / watchlist_candidates / surge_pool / news_events / positions / account_events / cron_job_runs / strategy_candidates / orders / session_state를 읽어 생성합니다.",
                        "source": source_label,
                        "theme_summary": theme_summary,
                        "candidate_list": candidate_list,
                        "watchlist": watchlist,
                        "signal_on": signal_on,
                        "buy_list": buy_list,
                        "portfolio": portfolio,
                        "risk_diagnostics": risk_diagnostics,
                        "cron_runs_today": cron_runs_today,
                        "system_status": system_status,
                        "data_freshness": data_freshness,
                        "health": health,
                        "cron_recent_failures": cron_runs_today.get("recent_failures", []),
                        "cron_stuck_running": cron_runs_today.get("stuck_running", []),
                        "strategy_candidates": strategy_candidates,
                        "orders_today": orders_today,
                        "open_positions": open_positions,
                        "watchlist_active_freshness": watchlist_active_freshness,
                    }
                )
                # 신규 health alert level을 market_state pill에 반영
                health_text = "OK" if health.get("alert_level") == "ok" else ("주의" if health.get("alert_level") == "warn" else "위험")
                ok_pct = health.get("ok_rate_pct", 0)
                payload["market_state"] = f"{market_state_text} · {health_text} · {ok_pct:.0f}%"
                payload["tags"] = [
                    "실데이터",
                    "DB-first",
                    "관심목록",
                    "시그널",
                    "보유종목",
                    "계좌자산",
                    "배치 헬스",
                    "신선도",
                ]
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
    if isinstance(source.get("risk_diagnostics"), dict):
        payload["risk_diagnostics"] = source.get("risk_diagnostics")
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
        "portfolio": 1 if payload.get("portfolio") else 0,
        "risk": 1 if payload.get("risk_diagnostics") else 0,
    }
    # 2026-06-17: datetime/date 객체를 JSON 직렬화 가능한 문자열로 변환
    payload = _jsonify(payload)
    return payload


def _jsonify(obj: Any) -> Any:
    """datetime/date 객체를 ISO 문자열로 변환 (재귀적으로)."""
    if obj is None:
        return None
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonify(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_jsonify(v) for v in obj)
    return obj


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
