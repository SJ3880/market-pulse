# 마켓 펄스 (Market Pulse)

경제 · 주식시장(국내/해외/IPO) · 부동산의 **지금 가장 이슈인 뉴스**와 **주요 시장지표**를 1분마다 갱신해서 보여 주는 개인 대시보드입니다.

```
[GitHub Actions 수집기]  ──매분 0초──▶  data 브랜치(latest.json, snap/*.json)
        ▲ 뉴스 RSS·시세                              │
        │                                            ▼
  신뢰 매체·공식기관                 [GitHub Pages 화면]  ──매분 40초에 최신 파일 확인
```

---

## 1. 처음 설정하기 (약 10분, 코딩 필요 없음)

### ① 저장소 만들기
1. github.com 로그인 → 오른쪽 위 **+** → **New repository**
2. Repository name: `market-pulse` (원하는 이름 가능)
3. **Public** 선택 ← 꼭 공개로. 공개 저장소여야 1분 수집(Actions)과 Pages가 무료입니다.
4. **Create repository**

### ② 파일 올리기
1. 받은 압축파일을 풉니다.
2. 새 저장소 화면에서 **uploading an existing file** (또는 Add file → Upload files) 클릭
3. 압축 푼 폴더 안의 `index.html`, `README.md`, `assets` 폴더, `collector` 폴더를 끌어다 놓기 → **Commit changes**

### ③ 자동 수집 파일 추가 (숨김 폴더라 따로 만듭니다)
1. 저장소에서 **Add file → Create new file**
2. 파일 이름 칸에 그대로 입력: `.github/workflows/collect.yml`
3. 압축 푼 폴더의 `.github/workflows/collect.yml` 내용을 메모장으로 열어 복사 → 붙여넣기
   (맥 Finder에서 숨김 폴더 보기: `Cmd + Shift + .`)
4. **Commit changes**

### ④ 수집 시작
1. 저장소 상단 **Actions** 탭 → (안내가 나오면) 초록 버튼 눌러 활성화
2. 왼쪽 목록 **실시간 수집** → 오른쪽 **Run workflow** → **Run workflow**
3. 1~2분 뒤 저장소 브랜치 목록에 `data`가 생기면 성공입니다. (이후로는 20분마다 자동으로 이어서 돌아갑니다)

### ⑤ 웹사이트 켜기
1. **Settings → Pages**
2. Source: **Deploy from a branch** / Branch: **main**, 폴더 **/(root)** → **Save**
3. 1~2분 뒤 `https://내아이디.github.io/market-pulse/` 접속

화면 오른쪽 위에 **초록 점 + "OO초 뒤 갱신"**이 보이면 정상 작동 중입니다.

---

## 2. 화면 구성

| 탭 | 내용 |
|---|---|
| 브리핑 | 핵심 지표 요약, 분야별 1위 이슈, 전체 이슈 Top 10, 새로 떠오르는 이슈, 다가오는 일정 |
| 경제 | 금리·환율·물가·수출·연준 이슈 + 환율/금리/유가/금 지표 |
| 주식시장 | 국내·해외·IPO·공모 구분 필터 + 국내외 지수·반도체·VIX·비트코인 |
| 부동산 | 집값·전월세·대출규제·공급 이슈 + 금리 지표, 부동산원 주간 동향 일정 |
| 정책·공식발표 | 기재부·금융위·국토부(정책브리핑), 미 연준 보도자료 원문 |
| 일정 | FOMC·금통위(공식 일정), 옵션만기·고용보고서·수출입동향 등(정기 규칙) 60일치 |

상단 시세판: 코스피·코스닥·원/달러·원/100엔·S&P500·나스닥·다우·필라델피아 반도체·VIX·닛케이·상하이·항셍·미 국채 10년·달러인덱스·WTI·금·비트코인 (상승=빨강, 하락=파랑)

## 3. 이슈 선정 기준

1. **신뢰 매체만 집계** — 통신사(연합뉴스·연합인포맥스·뉴스1·뉴시스), 경제지(한경·매경·서경·머투·이데일리·더벨 등), 종합지·방송, Reuters·Bloomberg·WSJ·FT·CNBC, 정부·중앙은행. 목록 밖 매체는 버립니다.
2. **같은 사건은 하나로 묶기** — 제목이 비슷한 기사를 한 이슈로 묶고 **몇 개 매체가 다뤘는지** 셉니다(같은 매체 중복은 1회).
3. **점수** = 보도 매체 수(로그) × 최신성(6시간마다 영향 감소) × 출처 권위 × 시장영향 키워드 가점(금리·환율·외국인·DSR 등, 최대 3개) × 포털 주요뉴스 가점
4. **급부상** = 최근 2시간 안에 3곳 이상이 새로 다룬, 처음 보도된 지 3시간 이내 이슈
5. **NEW / ▲▼** = 직전 갱신 대비 순위 변화

## 4. 고치고 싶을 때

모든 설정은 `collector/sources.py` 한 파일에 있습니다. GitHub에서 파일을 열고 연필 아이콘으로 수정 → Commit 하면 다음 수집부터 반영됩니다.

- 매체 추가/가중치: `OUTLETS`
- 뉴스 소스 추가: `FEEDS` (Google 뉴스 검색어 `gn("검색어")`를 쓰면 가장 쉽습니다)
- 시세 종목 추가: `MARKETS` (Yahoo Finance 종목코드, 예: 삼성전자 `005930.KS`)
- 가점 키워드: `IMPACT_KEYWORDS`

## 5. 문제 해결

| 증상 | 해결 |
|---|---|
| "데이터를 아직 찾지 못했어요" | Actions 탭에서 '실시간 수집'이 초록 체크인지 확인. 빨간 X면 눌러서 로그 확인 |
| 로그에 `Permission denied` / `403` | Settings → Actions → General → Workflow permissions → **Read and write permissions** → Save |
| 일부 소스 '실패' | 화면 맨 아래 **수집 상태**에서 어떤 소스인지 확인. 언론사가 RSS 주소를 바꾼 경우라 `sources.py`에서 주소를 고치거나 지우면 됩니다. 나머지 소스는 계속 정상 작동합니다 |
| 노란/빨간 점 (데이터 지연) | GitHub 예약 실행이 늦어진 경우. 보통 20분 안에 자동 복구. 급하면 Actions → Run workflow |
| 60일 뒤 수집이 멈춤 | GitHub는 60일간 main 브랜치에 변경이 없으면 예약 실행을 끕니다. 안내 메일이 오면 Actions 탭에서 다시 켜거나 README를 한 글자 고쳐 Commit |

### 알아 둘 점
- 수집은 매분 0초에 시작하고 화면은 매분 40초에 새 파일을 확인합니다. 심야(0~6시)에는 5분 간격입니다.
- 시세는 Yahoo Finance 기준이라 국내 지수는 수 분 지연될 수 있습니다.
- GitHub Actions를 상시로 돌리는 구조라, GitHub 정책상 과도한 사용으로 보면 제한될 가능성이 있습니다. 그럴 땐 `.github/workflows/collect.yml`의 `*/20`을 그대로 두고 `collect.py`의 `interval_now()` 값을 늘려 간격을 넓히면 됩니다.
- 투자 판단의 참고 자료로만 사용하세요.

## 6. 내 컴퓨터에서 미리 보기 (선택)

```bash
python collector/collect.py --once --no-push   # out/ 폴더에 데이터 1회 생성
python -m http.server 8000                     # http://localhost:8000 접속
```
