# agent-dashboard

GitHub Pages 기반의 정적 대시보드 샘플입니다.

## 구조
- `docs/index.html` — 정적 대시보드
- `docs/data/latest.json` — 대시보드가 읽는 단일 JSON 스냅샷
- `.github/workflows/deploy.yml` — `main` 푸시 시 `gh-pages` 브랜치로 배포

## 갱신 방식
1. 수집 스크립트 또는 수동 작업으로 `docs/data/latest.json` 갱신
2. `git add` / `git commit` / `git push`
3. GitHub Actions가 `docs/`를 `gh-pages` 브랜치로 배포

## 비고
- 처음 한 번은 GitHub repo settings에서 Pages source를 `gh-pages` 브랜치로 지정해야 할 수 있습니다.
- JSON 구조를 바꾸면 `docs/index.html`의 렌더러도 함께 맞춰 주세요.
