# agent-dashboard

GitHub Pages 기반의 정적 전략 대시보드입니다.

## 페이지
- `docs/index.html` — 전략 요약 메인 페이지
- `docs/watch.html` — 오늘 관심 테마, 후보종목, 관심목록, 시그널, 매수목록 페이지

## 데이터
- `docs/data/latest.json` — 메인 페이지가 읽는 최신 스냅샷
- `docs/data/watch.json` — 관심목록 페이지가 읽는 최신 스냅샷
- `data/source.json` — 메인 페이지용 원본 JSON
- `data/watch_source.json` — 관심목록 페이지용 원본 JSON

## 빌드 스크립트
- `scripts/build_dashboard_json.py` — `source.json` → `latest.json`
- `scripts/build_watch_dashboard_json.py` — `watch_source.json` → `watch.json`

## 갱신 방식
1. Python 스크립트가 JSON 스냅샷 생성
2. `main` 브랜치에 push
3. GitHub Actions가 10분 단위로 JSON을 재생성
4. `gh-pages` 브랜치로 정적 배포

## 비고
- 두 페이지 모두 AI 생성이 아니라 Python 코드로 JSON을 만든 뒤 렌더링합니다.
- `source.json`이 없더라도 workflow는 heartbeat 스냅샷을 생성합니다.
- GitHub Pages가 이미 켜져 있으면 `gh-pages` 브랜치 루트를 바라보게 하면 됩니다.
- JSON 구조를 바꾸면 각 HTML 렌더러도 함께 맞춰 주세요.
