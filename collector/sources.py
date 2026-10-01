# -*- coding: utf-8 -*-
"""
수집 대상(뉴스 소스·시장지표)과 이슈 선정 기준을 한곳에 모아 둔 설정 파일.
코딩을 몰라도 이 파일만 고치면 소스를 추가·삭제하거나 가중치를 바꿀 수 있습니다.
"""
from urllib.parse import quote

# ─────────────────────────────────────────────────────────────
# 1) 신뢰 매체 화이트리스트와 권위 가중치
#    - 여기에 없는 매체의 기사는 Google 뉴스에 떠도 버립니다.
#    - 1.00 : 통신사·공식기관 / 0.95 : 경제 전문지·글로벌 메이저 / 0.85 : 종합지·방송
# ─────────────────────────────────────────────────────────────
OUTLETS = {
    # 공식기관
    "기획재정부": 1.0, "재정경제부": 1.0, "금융위원회": 1.0, "국토교통부": 1.0, "한국은행": 1.0,
    "금융감독원": 1.0, "한국거래소": 1.0, "Federal Reserve": 1.0, "정책브리핑": 1.0,
    # 통신사·경제 전문
    "연합뉴스": 1.0, "연합인포맥스": 1.0, "뉴스1": 0.9, "뉴시스": 0.9,
    "한국경제": 0.95, "한경": 0.95, "매일경제": 0.95, "서울경제": 0.95, "머니투데이": 0.92,
    "이데일리": 0.92, "파이낸셜뉴스": 0.9, "아시아경제": 0.9, "헤럴드경제": 0.88,
    "조선비즈": 0.92, "비즈워치": 0.88, "더벨": 0.95, "딜사이트": 0.9, "인베스트조선": 0.92,
    "한국경제TV": 0.85, "SBS Biz": 0.88, "연합뉴스TV": 0.9, "이투데이": 0.85, "뉴스핌": 0.85,
    "아주경제": 0.85, "디지털타임스": 0.85, "전자신문": 0.85, "머니S": 0.8, "한국금융신문": 0.82,
    # 종합지·방송
    "조선일보": 0.88, "중앙일보": 0.88, "동아일보": 0.88, "한겨레": 0.85, "경향신문": 0.85,
    "한국일보": 0.85, "국민일보": 0.82, "세계일보": 0.8, "서울신문": 0.82, "문화일보": 0.82,
    "KBS": 0.9, "KBS 뉴스": 0.9, "MBC": 0.88, "MBC 뉴스": 0.88, "SBS": 0.88, "SBS 뉴스": 0.88,
    "JTBC": 0.85, "YTN": 0.88, "MBN": 0.82, "채널A": 0.8, "TV조선": 0.8,
    # 해외
    "Reuters": 1.0, "Bloomberg": 1.0, "Bloomberg.com": 1.0, "The Wall Street Journal": 1.0,
    "WSJ": 1.0, "Financial Times": 1.0, "CNBC": 0.95, "Associated Press": 0.95, "AP News": 0.95,
    "MarketWatch": 0.9, "Barron's": 0.92, "The New York Times": 0.9, "The Economist": 0.95,
    "Yahoo Finance": 0.85, "Fortune": 0.85, "Business Insider": 0.8, "Axios": 0.85,
    "Nikkei Asia": 0.95, "BBC": 0.9, "Investopedia": 0.75, "Morningstar": 0.85,
}

# 매체명 표기 흔들림 정리 (Google 뉴스 표기 → 표준명)
OUTLET_ALIASES = {
    "한국경제신문": "한국경제", "hankyung.com": "한국경제", "매경": "매일경제", "mk.co.kr": "매일경제",
    "연합뉴스 (Yonhap)": "연합뉴스", "Yonhap News Agency": "연합뉴스", "서울경제신문": "서울경제",
    "Reuters.com": "Reuters", "reuters.com": "Reuters", "wsj.com": "WSJ", "cnbc.com": "CNBC",
    "ft.com": "Financial Times", "The Associated Press": "Associated Press", "AP": "Associated Press",
    "bloomberg.com": "Bloomberg", "Yahoo Finance UK": "Yahoo Finance", "MarketWatch.com": "MarketWatch",
    "조선일보 경제": "조선일보", "biz.chosun.com": "조선비즈", "edaily": "이데일리",
}


def gn(q, hours=None):
    """Google 뉴스 한국판 검색 RSS (최근 1일)."""
    w = f" when:{hours}h" if hours else " when:1d"
    return f"https://news.google.com/rss/search?q={quote(q + w)}&hl=ko&gl=KR&ceid=KR:ko"


def gn_en(q):
    return f"https://news.google.com/rss/search?q={quote(q + ' when:1d')}&hl=en-US&gl=US&ceid=US:en"


GN_KR_BUSINESS = "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ko&gl=KR&ceid=KR:ko"
GN_US_BUSINESS = "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en"

# ─────────────────────────────────────────────────────────────
# 2) 뉴스 피드
#    kind: gnews(구글 뉴스, 매체명은 기사마다 다름) / rss(언론사·기관 직접 RSS)
#    tab : economy / stocks / realestate / policy  (None 이면 키워드로 자동 분류)
#    official: True 면 '정책·공식발표' 탭으로
# ─────────────────────────────────────────────────────────────
FEEDS = [
    # ── 경제 ──
    dict(id="gn_kr_biz", label="Google 뉴스 · 경제 헤드라인", kind="gnews", url=GN_KR_BUSINESS, tab=None, top=True),
    dict(id="gn_rate", label="Google 뉴스 · 금리/한은", kind="gnews", url=gn("기준금리 OR 한국은행 OR 금통위 OR 국고채"), tab="economy"),
    dict(id="gn_fx", label="Google 뉴스 · 환율", kind="gnews", url=gn("환율 OR 원달러 OR 외환시장"), tab="economy"),
    dict(id="gn_price", label="Google 뉴스 · 물가/경기", kind="gnews", url=gn("소비자물가 OR 경기 OR 성장률 OR 고용"), tab="economy"),
    dict(id="gn_trade", label="Google 뉴스 · 수출/무역", kind="gnews", url=gn("수출 OR 무역수지 OR 경상수지 OR 관세"), tab="economy"),
    dict(id="gn_fed", label="Google 뉴스 · 연준/미국경제", kind="gnews", url=gn("연준 OR FOMC OR 파월 OR 미국 물가"), tab="economy"),
    dict(id="gn_us_biz", label="Google News · US Business", kind="gnews", url=GN_US_BUSINESS, tab=None, lang="en", top=True),
    dict(id="gn_us_econ", label="Google News · Fed/Economy", kind="gnews", url=gn_en("Federal Reserve OR inflation OR Treasury yields"), tab="economy", lang="en"),
    # ── 주식 ──
    dict(id="gn_kospi", label="Google 뉴스 · 코스피/코스닥", kind="gnews", url=gn("코스피 OR 코스닥 OR 증시"), tab="stocks"),
    dict(id="gn_flow", label="Google 뉴스 · 수급", kind="gnews", url=gn("외국인 순매수 OR 외국인 순매도 OR 공매도 OR 개인 투자자"), tab="stocks"),
    dict(id="gn_semis", label="Google 뉴스 · 반도체/대형주", kind="gnews", url=gn("삼성전자 주가 OR SK하이닉스 주가 OR 반도체주"), tab="stocks"),
    dict(id="gn_nyse", label="Google 뉴스 · 뉴욕증시", kind="gnews", url=gn("뉴욕증시 OR 나스닥 OR S&P500 OR 엔비디아"), tab="stocks"),
    dict(id="gn_ipo", label="Google 뉴스 · IPO/공모주", kind="gnews", url=gn("공모주 OR 수요예측 OR 상장 첫날 OR IPO 코스닥"), tab="stocks", sub="ipo"),
    dict(id="gn_us_mkt", label="Google News · Stock market", kind="gnews", url=gn_en("stock market OR Nasdaq OR S&P 500 OR Dow"), tab="stocks", lang="en"),
    # ── 부동산 ──
    dict(id="gn_apt", label="Google 뉴스 · 아파트값", kind="gnews", url=gn("아파트값 OR 집값 OR 아파트 매매 OR 부동산원"), tab="realestate"),
    dict(id="gn_jeonse", label="Google 뉴스 · 전월세", kind="gnews", url=gn("전세 OR 월세 OR 전셋값 OR 임대차"), tab="realestate"),
    dict(id="gn_loan", label="Google 뉴스 · 대출규제", kind="gnews", url=gn("주택담보대출 OR DSR OR 가계대출 OR 부동산 대책"), tab="realestate"),
    dict(id="gn_supply", label="Google 뉴스 · 공급/청약", kind="gnews", url=gn("분양 OR 아파트 청약 OR 재건축 OR 주택공급 OR 부동산 PF"), tab="realestate"),
    # ── 언론사 직접 RSS (속보성 보강) ──
    dict(id="hk_eco", label="한국경제 · 경제", kind="rss", outlet="한국경제", url="https://www.hankyung.com/feed/economy", tab="economy"),
    dict(id="hk_fin", label="한국경제 · 증권", kind="rss", outlet="한국경제", url="https://www.hankyung.com/feed/finance", tab="stocks"),
    dict(id="hk_re", label="한국경제 · 부동산", kind="rss", outlet="한국경제", url="https://www.hankyung.com/feed/realestate", tab="realestate"),
    dict(id="hk_intl", label="한국경제 · 국제", kind="rss", outlet="한국경제", url="https://www.hankyung.com/feed/international", tab=None),
    dict(id="mk_eco", label="매일경제 · 경제", kind="rss", outlet="매일경제", url="https://www.mk.co.kr/rss/30100041/", tab="economy"),
    dict(id="mk_stock", label="매일경제 · 증권", kind="rss", outlet="매일경제", url="https://www.mk.co.kr/rss/50200011/", tab="stocks"),
    dict(id="mk_re", label="매일경제 · 부동산", kind="rss", outlet="매일경제", url="https://www.mk.co.kr/rss/50300009/", tab="realestate"),
    dict(id="yna_eco", label="연합뉴스 · 경제", kind="rss", outlet="연합뉴스", url="https://www.yna.co.kr/rss/economy.xml", tab="economy"),
    dict(id="yna_mkt", label="연합뉴스 · 마켓+", kind="rss", outlet="연합뉴스", url="https://www.yna.co.kr/rss/market.xml", tab="stocks"),
    dict(id="infomax", label="연합인포맥스 · 전체", kind="rss", outlet="연합인포맥스", url="https://news.einfomax.co.kr/rss/allArticle.xml", tab=None),
    dict(id="cnbc_top", label="CNBC · Top News", kind="rss", outlet="CNBC", url="https://www.cnbc.com/id/100003114/device/rss/rss.html", tab=None, lang="en"),
    dict(id="cnbc_mkt", label="CNBC · Markets", kind="rss", outlet="CNBC", url="https://www.cnbc.com/id/20910258/device/rss/rss.html", tab="economy", lang="en"),
    dict(id="mw_top", label="MarketWatch · Top", kind="rss", outlet="MarketWatch", url="https://feeds.content.dowjones.io/public/rss/mw_topstories", tab=None, lang="en"),
    # ── 공식기관 (정책·공식발표 탭) ──
    dict(id="kr_moef", label="정책브리핑 · 기획재정부", kind="rss", outlet="기획재정부", url="https://www.korea.kr/rss/dept_moef.xml", official=True),
    dict(id="kr_fsc", label="정책브리핑 · 금융위원회", kind="rss", outlet="금융위원회", url="https://www.korea.kr/rss/dept_fsc.xml", official=True),
    dict(id="kr_molit", label="정책브리핑 · 국토교통부", kind="rss", outlet="국토교통부", url="https://www.korea.kr/rss/dept_molit.xml", official=True),
    dict(id="fed_press", label="Federal Reserve · Press", kind="rss", outlet="Federal Reserve", url="https://www.federalreserve.gov/feeds/press_all.xml", official=True, lang="en"),
]

# ─────────────────────────────────────────────────────────────
# 3) 시장지표 (Yahoo Finance 차트 API)
# ─────────────────────────────────────────────────────────────
MARKETS = [
    # group, symbol, 이름, 소수점, 단위
    ("kr", "^KS11", "코스피", 2, ""),
    ("kr", "^KQ11", "코스닥", 2, ""),
    ("kr", "KRW=X", "원/달러", 2, "원"),
    ("kr", "JPYKRW=X", "원/100엔", 2, "원"),
    ("us", "^GSPC", "S&P 500", 2, ""),
    ("us", "^IXIC", "나스닥", 2, ""),
    ("us", "^DJI", "다우", 2, ""),
    ("us", "^SOX", "필라델피아 반도체", 2, ""),
    ("us", "^VIX", "VIX", 2, ""),
    ("asia", "^N225", "닛케이 225", 2, ""),
    ("asia", "000001.SS", "상하이종합", 2, ""),
    ("asia", "^HSI", "항셍", 2, ""),
    ("macro", "^TNX", "미 국채 10년", 3, "%"),
    ("macro", "DX-Y.NYB", "달러인덱스", 2, ""),
    ("macro", "CL=F", "WTI 유가", 2, "$"),
    ("macro", "GC=F", "금", 1, "$"),
    ("macro", "BTC-USD", "비트코인", 0, "$"),
]

# ─────────────────────────────────────────────────────────────
# 4) 이슈 선정 기준
# ─────────────────────────────────────────────────────────────
# 시장 영향이 큰 키워드 — 제목에 있으면 가점(최대 3개)
IMPACT_KEYWORDS = [
    "기준금리", "금리 인하", "금리 인상", "금통위", "FOMC", "연준", "파월", "국채", "환율", "원달러",
    "물가", "CPI", "PCE", "고용", "실업률", "GDP", "성장률", "경상수지", "무역수지", "수출", "관세",
    "코스피", "코스닥", "외국인", "공매도", "밸류업", "반도체", "삼성전자", "하이닉스", "엔비디아",
    "급등", "급락", "폭락", "폭등", "사상 최고", "최고치", "최저치", "서킷브레이커", "사이드카",
    "IPO", "공모주", "상장", "유상증자", "블록딜", "M&A", "인수",
    "집값", "아파트값", "전세", "DSR", "주담대", "가계대출", "대책", "규제", "공급", "PF", "청약",
    "Fed", "rate cut", "rate hike", "inflation", "recession", "tariff", "Treasury", "record high", "selloff",
]

# 이슈에서 제외할 기사 제목 패턴
EXCLUDE_PATTERNS = [
    r"^\[(포토|사진|인사|부고|게시판|알림|광고|AD|이벤트|모집|화보|오늘의 운세|날씨)\]",
    r"\[(포토|사진|인사|부고|게시판|화보)\]",
    r"(인사|부고)\s*$", r"^\s*\(?(사진|포토)\)?", r"오늘의\s?(운세|날씨)", r"^\[?광고",
]

TAB_RULES = {
    "realestate": ["부동산", "아파트", "집값", "전세", "월세", "주택", "분양", "청약", "재건축", "재개발",
                    "DSR", "주담대", "주택담보", "임대", "전셋", "매매가", "PF", "건설사", "오피스텔", "토지", "LH",
                    "real estate", "housing", "mortgage", "home sales"],
    "stocks": ["코스피", "코스닥", "증시", "주가", "상장", "공모", "IPO", "외국인 순매", "기관 순매", "공매도",
               "시가총액", "ETF", "증권", "주식", "뉴욕증시", "나스닥", "S&P", "다우", "종목", "배당", "자사주",
               "stock", "shares", "Nasdaq", "S&P 500", "Dow", "earnings", "IPO", "equities", "rally", "selloff"],
    "economy": ["금리", "환율", "물가", "경기", "성장률", "수출", "무역", "고용", "재정", "예산", "세수", "한은",
                "한국은행", "연준", "FOMC", "관세", "GDP", "소비", "inflation", "Fed", "economy", "GDP",
                "jobs", "tariff", "Treasury", "yields", "central bank"],
}

IPO_WORDS = ["공모", "IPO", "수요예측", "상장 첫날", "상장예비심사", "증권신고서", "스팩", "SPAC", "따상", "코넥스", "기술특례"]
GLOBAL_WORDS = ["뉴욕증시", "나스닥", "S&P", "다우", "엔비디아", "테슬라", "애플", "마이크로소프트", "미국 증시", "미 증시",
                "닛케이", "일본 증시", "중국 증시", "항셍", "유럽 증시", "월가", "빅테크"]

OFFICIAL_KEYWORDS_BOK = ["한국은행", "한은"]
