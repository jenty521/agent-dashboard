# agent-dashboard

GitHub Pages 기반의 정적 전략 대시보드입니다. `docs/index.html`이 `docs/data/latest.json`을 읽어 화면을 렌더링하고, `source.json`을 갱신하면 GitHub Actions가 10분 단위로 최신 스냅샷을 다시 만듭니다.

## 구조
- `docs/index.html` — 프리미엄 스타일 정적 대시보드
- `docs/data/latest.json` — 대시보드가 읽는 최신 스냅샷
- `data/source.json` — workflow가 읽는 원본 JSON
- `scripts/build_dashboard_json.py` — 원본 JSON을 latest.json으로 변환
- `.github/workflows/deploy.yml` — 10분 단위로 갱신 후 `gh-pages` 브랜치로 배포

## 갱신 방식
1. `data/source.json`을 갱신
2. `main` 브랜치에 push
3. GitHub Actions가 `docs/data/latest.json`을 재생성
4. `gh-pages` 브랜치로 정적 배포

## 비고
- `source.json`이 없더라도 workflow는 heartbeat 스냅샷을 생성합니다.
- GitHub Pages가 이미 켜져 있으면 `gh-pages` 브랜치 루트를 바라보게 하면 됩니다.
- Pages 활성화가 안 되어 있으면 repo settings에서 소스를 한 번 지정해야 할 수 있습니다.
- JSON 구조를 바꾸면 `docs/index.html` 렌더러도 함께 맞춰 주세요.
