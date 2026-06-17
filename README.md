# agent-dashboard

GitHub Pages 기반의 정적 전략 대시보드. 운영자/개발자 둘 다 짧게 쓸 수 있도록 운영 사실 + 코드 매핑을 한 페이지에 합쳤다.

대시보드에 대한 어떤 대화가 새 세션에서 시작되더라도, 이 README와 연결된 파일들만 보면 전체 그림이复原되도록 작성했다.

---

## 0. 30초 요약 (운영자/개발자 공통)

- **용도**: `invest` 프로필의 단타 전략 상태를 **정적 웹페이지**로 노출.
- **방식**: `data/*.json` → 빌드 스크립트 → `docs/data/*.json` → HTML이 fetch 해서 렌더.
- **갱신 주기**: GitHub Actions로 **10분마다** 재생성.
- **배포**: `gh-pages` 브랜치 → GitHub Pages 루트.
- **단일 진실 문서**: `~/.hermes/profiles/invest/AGENTS.md` (배치/시그널/매매 정책).
- **DB**: `invest` (host PostgreSQL, `host.docker.internal:5432`, `jenty521`).

## 1. 페이지

- `docs/index.html` — 전략 요약 메인 페이지 (`latest.json` 소비).
- `docs/watch.html` — 오늘의 테마/후보/관심/시그널/보유/계좌 페이지 (`watch.json` 소비).

## 2. 데이터 파일

| 파일 | 역할 | 출처 | 빌더 |
|---|---|---|---|
| `data/source.json` | 메인 페이지 원본 | 사람이 편집 | `scripts/build_dashboard_json.py` |
| `data/watch_source.json` | 워치 페이지 원본 | 사람이 편집 또는 빌더가 덮어씀 | `scripts/build_watch_dashboard_json.py` |
| `docs/data/latest.json` | 메인 페이지 스냅샷 | 빌더 산출 | (위) |
| `docs/data/watch.json` | 워치 페이지 스냅샷 | 빌더 산출 | (위) |

- `source.json`/`watch_source.json`이 없으면 빌더는 **heartbeat 스냅샷**을 생성해 대시보드는 멈추지 않는다 (status=`WAIT`).
- `latest.json`/`watch.json`은 **10분 단위 재생성**되며, 항상 `updated_at`(KST) / `generated_at_utc`(Z) 두 타임스탬프를 가진다.

## 3. 빌드 스크립트

- `scripts/build_dashboard_json.py`
  - 입력: `data/source.json` (선택)
  - 출력: `docs/data/latest.json` (필수)
  - 옵션 키: `gate`, `positions`, `watchlist`, `alerts`, `links`, `signals`, `controls`, `portfolio`
  - `source`에 위 키가 있으면 그대로 머지, 없으면 `DEFAULT_PAYLOAD` 그대로 사용.
- `scripts/build_watch_dashboard_json.py`
  - 입력: `data/watch_source.json`
  - 출력: `docs/data/watch.json`
  - `theme_summary` / `candidate_list` / `watchlist` / `signals` / `positions` / `account` / `portfolio` 섹션을 표준 키로 노출.

## 4. 갱신 → 배포 흐름

```
1) 사람이 data/source.json 또는 워치 빌더가 watch_source.json 생성
2) main 브랜치에 push
3) GitHub Actions 10분 주기:
   python3 scripts/build_dashboard_json.py --source data/source.json --output docs/data/latest.json
   python3 scripts/build_watch_dashboard_json.py --source data/watch_source.json --output docs/data/watch.json
4) gh-pages 브랜치로 정적 배포 (Pages source = gh-pages / root)
```

## 5. 정책 매핑 (이 카드가 어느 AGENTS.md 조항과 연결되는지)

| 카드 / 섹션 | 정책 | 출처 |
|---|---|---|
| `stats.MODE` (WAIT/PRE_OPEN/INTRADAY/EOD/BLOCKED) | 5-상태 운영 모드 | AGENTS.md §2, §3 |
| `theme_summary` | 테마 클러스터링 (반도체/AI/2차전지/바이오/원전/게임/로봇/방산/자동차/플랫폼) | AGENTS.md §8 |
| `candidate_list` | 거래량 활발 종목, 동적 후보, ETF/ETN 제외, size_target=5 / size_max=10 | AGENTS.md §9 |
| `watchlist` | 우선순위: `watchlist_hot_overrides` → `watchlist_candidates` → `instruments` → `watchlist_active` | AGENTS.md §9 |
| `signals` | BUY: rvol/trade_value/vwap/orb/hod/breakout/continuation/lock 가중치 | AGENTS.md §5 |
| `signals` (SELL) | 8-mode exit: HARD_STOP_LOSS / TIME_STOP / TAKE_PROFIT / TRAILING_STOP / BREAKEVEN_DEFEND / LOSS_CUT / HOLD_PARTIAL / SLIPPAGE_DEFEND | AGENTS.md §6 |
| `gate` | 현금/보유/리스크/사이드 가드, slippage guard, side-aware | AGENTS.md §5, §6, §10 |
| `positions` | 보유 종목 (Kiwoom 1차, TOSS 보조), open SELL order 시 execution_lock | AGENTS.md §6, §10 |
| `account` | 최신 `account_events` 기반 available_cash + holdings_value, account_capital_baselines 대비 증감률 | AGENTS.md §10 |
| `risk` (notes/alerts) | 주말/공휴일 매매 차단, 보유·한도 불명확 시 보수적 차단, fills 스키마 경고 | AGENTS.md §10, §12 |
| `updates` | 빌더/스케줄/UI 변경 이력 | 운영 노트 |

## 6. 데이터 항목(현재 노출 vs 정책상 있어야 할 것)

### 노출 중
- 테마 요약, 후보 목록, 관심목록, 시그널, 보유, 계좌/포트폴리오, 업데이트 이력, 체크리스트, 운영 노트.

### 보강 후보 (다음 변경 사이클에서 검토)
1. **stale 필터**: `candidate_list`에 `collected_at`이 당일이 아닌 항목 제거. (정책: AGENTS.md §9 “당일 동적 후보”)
2. **테마 화이트리스트**: `코스피`/`코스닥` 같은 시장지수성 태그는 `theme_summary`에서 제외하거나 `theme_role='market_index'`로 라벨 분리. (정책: AGENTS.md §8 “테마 클러스터링은 종목 가중치”)
3. **시그널 ↔ 결정 분리**: 현재 `signal` 단일 필드. `gate` 결과(현금/보유/리스크/사이드)를 `decision_card`로 분리. (정책: AGENTS.md §5, §6)
4. **stale 표기**: `updated_at`이 15분 초과 시 카드 상단 노란 띠. (운영 신호)
5. **전략 카드 톤 통일**: 현재 4가지 tone(accent/blue/amber/emerald). 라이트 테마 + 한 톤 강조 형태로 단순화 가능. (정책: memory “흰색 라이트 테마 선호”)
6. **차트 썸네일**: `signal-chart-capture` cron의 Naver 공개차트 URL을 `signal.chart_url`로 1줄 추가 → 카드 썸네일 가능. (정책: AGENTS.md §4 #27)

## 7. UI/UX 운영 가이드 (짧게)

- **테마**: 흰색 라이트. 강조색은 1개. (memory: 라이트 테마 선호)
- **글꼴**: 시스템 산세리프. 한국어 본문 14~16px, 카드 타이틀 18~20px.
- **레이아웃**: 1열(모바일) → 2열(태블릿) → 3~4열(데스크톱). 카드는 `grid-gap: 16px`.
- **상태 색**: WAIT 회색, INTRADAY 파랑, EOD 청록, BLOCKED 적색, PRE_OPEN 호박.
- **반응형**: `max-width: 1280px` 가운데 정렬, 카드 최소 너비 280px.
- **접근성**: 카드/버튼 `focus-visible` 외곽선 명확. 색만으로 상태를 전달하지 말고 텍스트도 동반.
- **반응형 점검 순서**: 1) 데스크톱 1280 / 2) 태블릿 820 / 3) 모바일 390.
- **렌더링 검증**: 로컬 `python -m http.server`로 `docs/`를 띄워 카드/섹션/모바일 뷰 모두 확인.

## 8. 변경 작업 가이드 (개발자 hand-off)

### JSON 스키마를 바꿀 때
1. `data/source.json`(또는 워치 빌더 입력) 변경.
2. `scripts/build_*.py`에서 `DEFAULT_PAYLOAD` 또는 `as_list` 분기 보강.
3. `docs/index.html` / `docs/watch.html`의 렌더러(`renderCard` 등)도 함께 수정.
4. PR 본문에 “스키마 변경” 명시 + before/after JSON 예시 첨부.

### 카드/섹션을 추가할 때
1. 빌더가 새 키를 payload에 머지하는지 확인 (`source` 입력에 키가 있으면 그대로 사용).
2. HTML 렌더러에 `if (payload.foo) renderFoo(...)` 가드 추가.
3. `data/source.json`에 새 키 예시 1개 추가 → `latest.json` 회귀 확인.
4. README §5 정책 매핑표에 1행 추가.

### 색/테마를 바꿀 때
1. HTML/CSS의 `tone-*` 클래스만 수정. 빌더 payload는 그대로.
2. 라이트 테마 정책 유지(배경 흰색, 본문 검정, 강조 1색).
3. `body.dark` 토글이 들어갈 경우, `prefers-color-scheme` 대신 명시 토글로.

### 빌더/스케줄을 바꿀 때
1. `.github/workflows/*.yml`의 `cron`은 `*/10 * * * *` (KST 변환은 빌더가 `Asia/Seoul`로 처리).
2. 빌더 실행 커맨드를 README §4에 그대로 반영.
3. `ACTIONS_STEP_DEBUG`로 한 번 실제 로그 확인.

## 9. 자주 보는 파일/위치

- 이 README: `agent-dashboard/README.md`
- 원본: `data/source.json`, `data/watch_source.json`
- 빌더: `scripts/build_dashboard_json.py`, `scripts/build_watch_dashboard_json.py`
- 산출: `docs/data/latest.json`, `docs/data/watch.json`
- 페이지: `docs/index.html`, `docs/watch.html`
- 정책 단일 진실: `~/.hermes/profiles/invest/AGENTS.md`
- 시크릿/크론: 워크스페이스의 `scripts/cron/register_pipeline_crons.py` 및 `crontab_daytrading_prepared.cron`
- 토스 OpenAPI 가이드: `docs/vault/toss_openapi/llms.txt`

## 10. 비고

- 두 페이지 모두 AI 생성 본문이 아니라 **Python 빌더가 만든 JSON을 정적 HTML이 fetch 해서 렌더**한다.
- `source.json`/`watch_source.json`이 비어 있어도 워크플로우는 heartbeat 스냅샷을 생성한다.
- Pages가 `gh-pages` 루트를 가리키고 있는지, `docs/.nojekyll`이 커밋돼 있는지 확인.
- JSON 구조를 바꾸면 각 HTML 렌더러도 함께 맞춰야 한다.
- **시크릿은 절대 JSON/HTML에 노출하지 말 것**. 키/토큰/계좌번호가 필요하면 `.env`에서 빌더가 동적으로 주입.
