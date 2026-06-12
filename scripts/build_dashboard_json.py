#!/usr/bin/env python3
"""Build docs/data/latest.json for the static GitHub dashboard.

Usage:
  python3 scripts/build_dashboard_json.py \
    --source data/source.json \
    --output docs/data/latest.json

If the source file does not exist, a safe heartbeat snapshot is emitted so the
GitHub Actions schedule can still refresh the dashboard every 10 minutes.
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
    "title": "Agent Dashboard",
    "subtitle": "GitHub Pages + JSON snapshot",
    "market_state": "대기",
    "summary": "10분 단위로 갱신되는 정적 대시보드",
    "note": "source.json이 있으면 그 내용을 반영하고, 없으면 heartbeat 스냅샷을 생성합니다.",
    "tags": ["GitHub Pages", "JSON", "10분 갱신", "정적 대시보드"],
    "stats": [
        {"label": "Mode", "value": "WAIT", "desc": "매매 대기 상태"},
        {"label": "Refresh", "value": "10m", "desc": "권장 갱신 주기"},
        {"label": "Source", "value": "JSON", "desc": "data/source.json"},
        {"label": "Deploy", "value": "Pages", "desc": "gh-pages 브랜치"},
    ],
    "notes": [
        {
            "title": "운영 방식",
            "status": "ok",
            "body": "JSON만 갱신해서 GitHub에 push하면 대시보드가 업데이트됩니다.",
            "foot": "자동 배포 가능",
        },
        {
            "title": "안정성",
            "status": "warn",
            "body": "원본 JSON이 없을 때도 heartbeat 기본값으로 화면이 유지됩니다.",
            "foot": "fallback 안전장치",
        },
    ],
    "checklists": [
        {
            "task": "JSON 생성",
            "status": "done",
            "detail": "latest.json 생성 완료",
            "foot": "docs/data/latest.json",
        },
        {
            "task": "Pages 배포",
            "status": "todo",
            "detail": "GitHub Actions로 gh-pages 브랜치 배포",
            "foot": "repo settings에서 Pages source 지정 필요할 수 있음",
        },
    ],
    "updates": [
        {
            "name": "seed",
            "status": "done",
            "detail": "초기 대시보드 스켈레톤",
            "time": "manual_seed",
        },
        {
            "name": "heartbeat",
            "status": "ready",
            "detail": "워크플로우가 10분마다 갱신",
            "time": "scheduled",
        },
    ],
}


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def as_list(value, fallback):
    if isinstance(value, list) and value:
        return value
    return deepcopy(fallback)


def build_payload(source: dict | None, source_name: str) -> dict:
    now_kst = datetime.now(KST)
    now_utc = datetime.now(timezone.utc)
    source = source or {}

    payload = deepcopy(DEFAULT_PAYLOAD)
    payload.update({
        "title": source.get("title", payload["title"]),
        "subtitle": source.get("subtitle", payload["subtitle"]),
        "market_state": source.get("market_state", payload["market_state"]),
        "summary": source.get("summary", payload["summary"]),
        "note": source.get("note", payload["note"]),
        "source": source_name,
        "updated_at": now_kst.strftime("%Y-%m-%d %H:%M:%S KST"),
        "generated_at_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
    })

    payload["tags"] = as_list(source.get("tags"), payload["tags"])
    payload["stats"] = as_list(source.get("stats"), payload["stats"])
    payload["notes"] = as_list(source.get("notes"), payload["notes"])
    payload["checklists"] = as_list(source.get("checklists"), payload["checklists"])
    payload["updates"] = as_list(source.get("updates"), payload["updates"])

    # Optional overlay fields for future integrations.
    for key in ("gate", "positions", "watchlist", "alerts", "links"):
        if key in source:
            payload[key] = source[key]

    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/source.json"))
    parser.add_argument("--output", type=Path, default=Path("docs/data/latest.json"))
    args = parser.parse_args()

    source = load_json(args.source) if args.source.exists() else {}
    payload = build_payload(source, source_name=str(args.source) if args.source.exists() else "heartbeat")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output} from {args.source if args.source.exists() else 'heartbeat'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
