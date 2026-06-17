#!/usr/bin/env python3
"""배치/뉴스/텔레그램 헬스 진단 스크립트 (one-shot).

다음 4가지를 한 번에 점검한다:
  (1) cron_registration_requests 등록 상태 + 핵심 키 4개 presence
  (2) news_items / news_events 오늘(KST) 적재 통계
  (3) 워치리스트 active 현황 + news 매핑
  (4) 텔레그램 봇 토큰/chat_id 존재 + 최근 send_message 로그
  (5) daytrading_cron_daemon 상태 파일/마지막 tick

DB: invest, host=host.docker.internal:5432, user=jenty521 (.env 자동 주입 가정)

사용:
  python3 scripts/cron/diag_health.py
"""
from __future__ import annotations

import json
import os
import sys
import socket
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parents[2]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

os.environ.setdefault("APP_DB_HOST", "host.docker.internal")
os.environ.setdefault("APP_DB_NAME", "invest")
os.environ.setdefault("APP_DB_USER", "jenty521")
os.environ.setdefault("APP_DB_PORT", "5432")

import psycopg  # noqa: E402
from app.env_loader import ensure_env_loaded  # noqa: E402

ensure_env_loaded()

KST = timezone(timedelta(hours=9))
CORE_KEYS = [
    "strategy-pipeline-tick",
    "entry-signal-execution",
    "exit-signal-execution",
    "live-order-execution",
]
NEWS_KEYS = ["market-news-refresh", "toss-reference-refresh", "intraday-watchlist-refresh"]


def db():
    primary = os.getenv("APP_DB_HOST", "host.docker.internal")
    cfg = dict(
        host=primary,
        port=int(os.getenv("APP_DB_PORT", "5432")),
        dbname=os.getenv("APP_DB_NAME", "invest"),
        user=os.getenv("APP_DB_USER", "jenty521"),
        password=os.getenv("APP_DB_PASSWORD") or None,
        connect_timeout=5,
    )
    try:
        return psycopg.connect(**cfg)
    except Exception:
        if primary != "host.docker.internal":
            raise
        try:
            socket.gethostbyname(primary)
            raise
        except Exception:
            cfg["host"] = "127.0.0.1"
            return psycopg.connect(**cfg)


def fetch_all_dict(conn, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_one_dict(conn, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        return dict(zip(cols, row)) if row else None


def section(title: str) -> None:
    print("\n" + "=" * 78)
    print(f" {title}")
    print("=" * 78)


def main() -> int:
    conn = db()
    out: dict[str, Any] = {}

    # (1) cron_registration_requests
    section("(1) cron_registration_requests")
    rows = fetch_all_dict(
        conn,
        "select cron_key, status, schedule_expr, no_agent, script_path, "
        "priority, payload_json, updated_at "
        "from cron_registration_requests "
        "order by priority nulls last, cron_key",
    )
    print(f"total registered rows: {len(rows)}")
    statuses: dict[str, int] = {}
    for r in rows:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    print(f"by status: {statuses}")
    print("\ncore keys:")
    core = {r["cron_key"]: r for r in rows}
    for k in CORE_KEYS:
        r = core.get(k)
        if r is None:
            print(f"  [MISSING] {k}")
        else:
            print(
                f"  [ok] {k:32s} status={r['status']:10s} "
                f"no_agent={r['no_agent']!s:5s} script={r['script_path'] or ''} "
                f"sched={r['schedule_expr']} priority={r['priority']}"
            )
    print("\nnews/watch keys:")
    for k in NEWS_KEYS:
        r = core.get(k)
        if r is None:
            print(f"  [MISSING] {k}")
        else:
            print(
                f"  [ok] {k:32s} status={r['status']:10s} "
                f"sched={r['schedule_expr']}"
            )

    out["cron"] = {
        "total": len(rows),
        "by_status": statuses,
        "core_present": {k: (k in core) for k in CORE_KEYS},
    }

    # (2) news_items / news_events (오늘 KST)
    section("(2) news_items / news_events (today KST)")
    today_kst = datetime.now(KST).date()
    try:
        r = fetch_one_dict(
            conn,
            "select count(*) as total, "
            "count(*) filter (where instrument_id is not null) as mapped, "
            "count(*) filter (where observed_at >= (now() at time zone 'Asia/Seoul')::date) as today, "
            "count(*) filter (where observed_at >= (now() at time zone 'Asia/Seoul')::date "
            "                    and instrument_id is not null) as today_mapped "
            "from news_items",
        )
        print(f"news_items total={r['total']} mapped={r['mapped']} "
              f"today={r['today']} today_mapped={r['today_mapped']}")
        out["news_items"] = r
    except Exception as exc:  # noqa: BLE001
        print(f"news_items query failed: {exc}")
        out["news_items"] = {"error": str(exc)}

    try:
        r = fetch_one_dict(
            conn,
            "select count(*) as total, "
            "count(*) filter (where observed_at >= (now() at time zone 'Asia/Seoul')::date) as today "
            "from news_events",
        )
        print(f"news_events total={r['total']} today={r['today']}")
        out["news_events"] = r
    except Exception as exc:  # noqa: BLE001
        print(f"news_events query failed: {exc}")
        out["news_events"] = {"error": str(exc)}

    # (3) watchlist_active
    section("(3) watchlist_active + news mapping")
    try:
        r = fetch_one_dict(
            conn,
            "select count(*) filter (where is_active=true) as active, "
            "count(*) filter (where is_active=true "
            "  and (meta_json->>'news_count')::int > 0) as active_with_news "
            "from watchlist_active",
        )
        print(f"watchlist_active active={r['active']} active_with_news={r['active_with_news']}")
        out["watchlist_active"] = r
    except Exception as exc:  # noqa: BLE001
        print(f"watchlist_active query failed: {exc}")
        out["watchlist_active"] = {"error": str(exc)}

    # (4) telegram bot
    section("(4) telegram bot env + recent send")
    bot = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    print(f"TELEGRAM_BOT_TOKEN present: {bool(bot)}")
    print(f"TELEGRAM_CHAT_ID present: {bool(chat)}")
    if bot:
        url = f"https://api.telegram.org/bot{bot}/getMe"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                body = resp.read().decode("utf-8")
            j = json.loads(body)
            if j.get("ok"):
                print(f"  bot username: @{j['result'].get('username')}")
            else:
                print(f"  getMe not ok: {j}")
        except Exception as exc:  # noqa: BLE001
            print(f"  getMe failed: {exc}")

    out["telegram"] = {"bot_present": bool(bot), "chat_present": bool(chat)}

    # (4-b) try to find recent telegram delivery log if there's a known table
    try:
        r = fetch_one_dict(
            conn,
            "select table_name from information_schema.tables "
            "where table_schema='public' and table_name in "
            "('telegram_messages','telegram_deliveries','notification_log','cron_run_log')",
        )
        if r:
            tname = r["table_name"]
            print(f"  delivery log table found: {tname}")
            sample = fetch_all_dict(
                conn,
                f"select * from {tname} order by 1 desc limit 5",
            )
            for s in sample:
                print(f"    {s}")
            out["telegram_log_table"] = tname
        else:
            print("  no delivery log table (telegram_messages/telegram_deliveries/notification_log/cron_run_log)")
    except Exception as exc:  # noqa: BLE001
        print(f"  log table probe failed: {exc}")

    # (5) daytrading_cron_daemon state
    section("(5) daytrading_cron_daemon state")
    state_paths = [
        WORKSPACE / "runtime" / "daytrading_cron_daemon_state.json",
        Path("/Users/jenty521/HermesCompanies/investment_firm/workspace/runtime/daytrading_cron_daemon_state.json"),
    ]
    for p in state_paths:
        if p.exists():
            try:
                st = json.loads(p.read_text(encoding="utf-8"))
                print(f"  state: {p}")
                print(f"    keys: {list(st.keys())[:20]}")
                for k in ("last_tick_at", "due_count", "last_run", "status"):
                    if k in st:
                        print(f"    {k}: {st[k]}")
            except Exception as exc:  # noqa: BLE001
                print(f"  state read failed: {exc}")
        else:
            print(f"  not found: {p}")
    log_paths = [
        WORKSPACE / "runtime" / "daytrading_cron_daemon.log",
        Path("/Users/jenty521/HermesCompanies/investment_firm/workspace/runtime/daytrading_cron_daemon.log"),
    ]
    for p in log_paths:
        if p.exists():
            try:
                lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()[-30:]
                print(f"  log tail ({p}):")
                for line in lines:
                    print(f"    {line}")
            except Exception as exc:  # noqa: BLE001
                print(f"  log read failed: {exc}")
        else:
            print(f"  not found: {p}")

    print("\n" + "=" * 78)
    print(" summary json")
    print("=" * 78)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
