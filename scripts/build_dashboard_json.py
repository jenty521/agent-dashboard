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
    "title": "COOLPEACE AGENT",
    "subtitle": "에이전트 기반 단타 전략 대시보드",
    "market_state": "대기",
    "summary": "전략 상태를 한눈에 확인하는 GitHub Pages + JSON 대시보드",
    "note": "source.json이 있으면 그 내용을 반영하고, 없으면 heartbeat 스냅샷을 생성합니다.",
    "tags": ["GitHub Pages", "JSON", "10분 갱신", "전략 요약", "정적 UI"],
    "stats": [
        {"label": "MODE", "value": "WAIT", "desc": "진입 전 대기 상태"},
        {"label": "REFRESH", "value": "10m", "desc": "GitHub Actions 스케줄"},
        {"label": "SOURCE", "value": "JSON", "desc": "data/source.json"},
        {"label": "DEPLOY", "value": "gh-pages", "desc": "정적 브랜치 배포"},
    ],
    "strategy_cards": [
        {
            "title": "시장 레짐",
            "badge": "FILTER",
            "tone": "accent",
            "summary": "상승 추세와 거래대금이 맞을 때만 전략을 활성화합니다.",
            "points": ["추세 확인 후 진입", "거래대금/변동성 필터", "레짐 불일치 시 대기"],
            "foot": "레짐이 먼저, 종목은 그 다음",
        },
        {
            "title": "진입 시나리오",
            "badge": "ENTRY",
            "tone": "blue",
            "summary": "급등·돌파·눌림 중 하나의 시나리오만 선택합니다.",
            "points": ["중복 신호 금지", "관찰 → 확인 → 진입", "호가/체결 강도 확인"],
            "foot": "선택과 집중",
        },
        {
            "title": "리스크 가드",
            "badge": "RISK",
            "tone": "amber",
            "summary": "손절, 분할, 시간 제한으로 큰 손실을 먼저 막습니다.",
            "points": ["손절 기준 선확정", "주문 가능 수량 기준", "보유·한도 불명확 시 차단"],
            "foot": "보호가 수익보다 앞선다",
        },
        {
            "title": "청산 규칙",
            "badge": "EXIT",
            "tone": "emerald",
            "summary": "목표 수익, 트레일링, 당일 종료 규칙으로 마무리합니다.",
            "points": ["목표 도달 시 분할 정리", "오후 약세 전 선제 정리", "익절보다 보호 우선"],
            "foot": "끝까지 노출하지 않기",
        },
    ],
    "flow_steps": [
        {"title": "수집", "badge": "01", "body": "시세·체결·뉴스·수급 데이터를 모읍니다."},
        {"title": "정리", "badge": "02", "body": "원본 JSON을 요약 데이터로 정규화합니다."},
        {"title": "평가", "badge": "03", "body": "레짐, 점수, 리스크 게이트를 적용합니다."},
        {"title": "표시", "badge": "04", "body": "정적 HTML이 최신 스냅샷을 렌더링합니다."},
    ],
    "notes": [
        {"title": "운영 방식", "status": "ok", "body": "JSON만 갱신해서 GitHub에 push", "foot": "자동 배포 가능"},
        {"title": "검증", "status": "warn", "body": "로컬 미리보기와 GitHub Pages 경로를 모두 확인", "foot": "실제 노출 기준"},
    ],
    "checklists": [
        {
            "task": "JSON 생성",
            "status": "done",
            "detail": "latest.json 생성",
            "foot": "docs/data/latest.json",
        },
        {
            "task": "Pages 배포",
            "status": "ready",
            "detail": "gh-pages 브랜치 루트 배포",
            "foot": "repo settings에서 source 확인",
        },
        {
            "task": "UI 점검",
            "status": "todo",
            "detail": "브라우저에서 카드·섹션 렌더 확인",
            "foot": "반응형 레이아웃",
        },
    ],
    "updates": [
        {"name": "seed", "status": "done", "detail": "초기 대시보드 스켈레톤", "time": "manual"},
        {"name": "schedule", "status": "ready", "detail": "10분마다 워크플로우 갱신", "time": "github-actions"},
        {"name": "ui-refresh", "status": "ready", "detail": "프리미엄 스타일 대시보드로 개편", "time": "this request"},
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
    payload["stats"] = as_list(source.get("stats"), payload["stats"])
    payload["strategy_cards"] = as_list(source.get("strategy_cards"), payload["strategy_cards"])
    payload["flow_steps"] = as_list(source.get("flow_steps"), payload["flow_steps"])
    payload["notes"] = as_list(source.get("notes"), payload["notes"])
    payload["checklists"] = as_list(source.get("checklists"), payload["checklists"])
    payload["updates"] = as_list(source.get("updates"), payload["updates"])

    for key in ("gate", "positions", "watchlist", "alerts", "links", "signals", "controls"):
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
