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
                portfolio = build_portfolio_summary(cur) or {}
                risk_diagnostics = build_risk_diagnostics(cur) or {}

                source_label = "db:public.news_events+watchlist_active+watchlist_candidates+surge_pool+positions+account_events+account_capital_baselines+collector_status_snapshots"
                payload = deepcopy(DEFAULT_PAYLOAD)
                payload.update(
                    {
                        "title": "COOLPEACE AGENT WATCH",
                        "subtitle": "오늘 관심 테마와 후보를 DB에서 읽어 10분 단위로 갱신하는 페이지",
                        "market_state": "대기",
                        "summary": "실제 PostgreSQL 데이터로 테마, 후보종목, 관심목록, 시그널, 보유종목을 보여줍니다.",
                        "note": "watchlist_active / watchlist_candidates / surge_pool / news_events / positions / account_events를 읽어 생성합니다.",
                        "source": source_label,
                        "theme_summary": theme_summary,
                        "candidate_list": candidate_list,
                        "watchlist": watchlist,
                        "signal_on": signal_on,
                        "buy_list": buy_list,
                        "portfolio": portfolio,
                        "risk_diagnostics": risk_diagnostics,
                    }
                )
                payload["tags"] = ["실데이터", "DB-first", "관심목록", "시그널", "보유종목", "계좌자산"]
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
