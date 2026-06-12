# agent-dashboard

GitHub 기반 정적 대시보드 샘플입니다.

## 구조
- `index.html` — `gh-pages` 브랜치 루트에서 서빙할 대시보드
- `data/latest.json` — 대시보드가 읽는 단일 JSON 스냅샷
- `docs/` — 개발 중 미리보기용 동일 자산

## 갱신 방식
1. 수집 스크립트 또는 수동 작업으로 `data/latest.json` 또는 `docs/data/latest.json` 갱신
2. GitHub에 push
3. `gh-pages` 브랜치 루트가 갱신되면 정적 대시보드도 함께 업데이트

## 비고
- 이 저장소는 `gh-pages` 브랜치 루트 기준으로 바로 서빙할 수 있게 준비되어 있습니다.
- 현재 계정/플랜에서는 GitHub Pages 사이트 생성 API가 허용되지 않아, GitHub repo settings에서 Pages 소스를 수동으로 `gh-pages` / `/`로 지정해야 할 수 있습니다.
