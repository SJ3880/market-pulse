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


def gn(q, hours=None, days=None):
    """Google 뉴스 한국판 검색 RSS (기본 최근 1일)."""
    w = f" when:{hours}h" if hours else f" when:{days}d" if days else " when:1d"
    return f"https://news.google.com/rss/search?q={quote(q + w)}&hl=ko&gl=KR&ceid=KR:ko"


def gn_en(q, days=1):
    return f"https://news.google.com/rss/search?q={quote(q + f' when:{days}d')}&hl=en-US&gl=US&ceid=US:en"


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
    # ── IPO 관련 이슈 (더벨류: 상장 전 펀딩·주관사·상장 추진/연기·밸류에이션·제도, 최근 3일) ──
    dict(id="gn_ipo_fund", label="Google 뉴스 · 상장 전 투자유치", kind="gnews", url=gn('프리IPO OR "pre-IPO" OR 시리즈C OR 시리즈D OR 투자 유치 기업가치 OR 브릿지 투자', days=3), tab="ipo"),
    dict(id="gn_ipo_lead", label="Google 뉴스 · 주관사 선정", kind="gnews", url=gn("상장 주관사 선정 OR 대표주관사 선정 OR IPO 주관 경쟁 OR 주관사 교체", days=3), tab="ipo"),
    dict(id="gn_ipo_plan", label="Google 뉴스 · 상장 추진/연기/철회", kind="gnews", url=gn("상장 추진 OR 상장 연기 OR 상장 철회 OR IPO 재도전 OR 상장 재추진 OR IPO 대어", days=3), tab="ipo"),
    dict(id="gn_ipo_val", label="Google 뉴스 · 몸값/엑시트", kind="gnews", url=gn("IPO 몸값 OR 상장 기업가치 OR 구주매출 OR FI 엑시트 OR 투자금 회수 IPO OR 밸류에이션 논란", days=3), tab="ipo"),
    dict(id="gn_ipo_rule", label="Google 뉴스 · 상장 제도", kind="gnews", url=gn("상장 제도 개편 OR 기술특례 제도 OR 상장심사 개선 OR 공모주 제도 OR 의무보유확약 OR 코스닥 상장 요건", days=3), tab="ipo"),
    dict(id="gn_ipo_league", label="Google 뉴스 · ECM 리그테이블", kind="gnews", url=gn("ECM 리그테이블 OR IPO 주관 실적 OR IPO 시장 전망 OR 증권사 IB 실적", days=3), tab="ipo"),
    dict(id="gn_ipo_abroad", label="Google 뉴스 · 해외 상장", kind="gnews", url=gn("나스닥 상장 추진 OR 미국 상장 추진 OR 해외 상장 OR 이중상장", days=3), tab="ipo"),
    dict(id="gn_ipo_thebell", label="Google 뉴스 · 더벨/딜사이트/인베스트조선 IPO", kind="gnews",
         url=gn("IPO OR 상장 OR 프리IPO (site:thebell.co.kr OR site:dealsite.co.kr OR site:investchosun.com)", days=3), tab="ipo"),
    dict(id="gn_ipo_en", label="Google News · IPO pipeline", kind="gnews", url=gn_en("IPO pipeline OR plans IPO OR IPO valuation OR pre-IPO funding OR delays IPO", days=3), tab="ipo", lang="en"),
    # ── IB (유상증자·블록딜·메자닌·M&A, 최근 3일) ──
    dict(id="gn_ecm", label="Google 뉴스 · 유상증자/메자닌", kind="gnews", url=gn("유상증자 OR 전환사채 OR 교환사채 OR 신주인수권부사채", days=3), tab="ib", sub="ecm"),
    dict(id="gn_ecm2", label="Google 뉴스 · 블록딜/지분매각", kind="gnews", url=gn("블록딜 OR 시간외 대량매매 OR 지분 매각 OR 오버행", days=3), tab="ib", sub="ecm"),
    dict(id="gn_mna", label="Google 뉴스 · M&A", kind="gnews", url=gn("인수합병 OR 경영권 매각 OR 우선협상대상자 OR 지분 인수 OR 사모펀드 인수", days=3), tab="ib", sub="mna"),
    dict(id="gn_mna2", label="Google 뉴스 · 매각/인수전", kind="gnews", url=gn("매각 추진 OR 인수전 OR 본입찰 OR 예비입찰 OR M&A 시장", days=3), tab="ib", sub="mna"),
    dict(id="gn_ib_thebell", label="Google 뉴스 · 더벨/딜사이트/인베스트조선 IB", kind="gnews",
         url=gn("유상증자 OR 블록딜 OR 전환사채 OR M&A OR 경영권 OR 인수 (site:thebell.co.kr OR site:dealsite.co.kr OR site:investchosun.com)", days=3), tab="ib"),
    dict(id="gn_ib_en", label="Google News · M&A/Offerings", kind="gnews", url=gn_en("acquisition deal billion OR merger agreement OR share offering OR block trade", days=3), tab="ib", lang="en"),
    # ── 부동산 ──
    dict(id="gn_apt", label="Google 뉴스 · 아파트값", kind="gnews", url=gn("아파트값 OR 집값 OR 아파트 매매 OR 부동산원"), tab="realestate"),
    dict(id="gn_jeonse", label="Google 뉴스 · 전월세", kind="gnews", url=gn("전세 OR 월세 OR 전셋값 OR 임대차"), tab="realestate"),
    dict(id="gn_loan", label="Google 뉴스 · 대출규제", kind="gnews", url=gn("주택담보대출 OR DSR OR 가계대출 OR 부동산 대책"), tab="realestate"),
    dict(id="gn_supply", label="Google 뉴스 · 공급/청약", kind="gnews", url=gn("분양 OR 아파트 청약 OR 재건축 OR 주택공급 OR 부동산 PF"), tab="realestate"),
    # ── 언론사 직접 RSS (속보성 보강) ──
    dict(id="mk_eco", label="매일경제 · 경제", kind="rss", outlet="매일경제", url="https://www.mk.co.kr/rss/30100041/", tab="economy"),
    dict(id="mk_stock", label="매일경제 · 증권", kind="rss", outlet="매일경제", url="https://www.mk.co.kr/rss/50200011/", tab="stocks"),
    dict(id="mk_re", label="매일경제 · 부동산", kind="rss", outlet="매일경제", url="https://www.mk.co.kr/rss/50300009/", tab="realestate"),
    dict(id="yna_eco", label="연합뉴스 · 경제", kind="rss", outlet="연합뉴스", url="https://www.yna.co.kr/rss/economy.xml", tab="economy"),
    dict(id="yna_mkt", label="연합뉴스 · 마켓+", kind="rss", outlet="연합뉴스", url="https://www.yna.co.kr/rss/market.xml", tab="stocks"),
    dict(id="yna_all", label="연합뉴스 · 최신", kind="rss", outlet="연합뉴스", url="https://www.yna.co.kr/rss/news.xml", tab=None),
    dict(id="yna_ind", label="연합뉴스 · 산업", kind="rss", outlet="연합뉴스", url="https://www.yna.co.kr/rss/industry.xml", tab=None),
    dict(id="donga_eco", label="동아일보 · 경제", kind="rss", outlet="동아일보", url="https://rss.donga.com/economy.xml", tab="economy"),
    dict(id="hani_eco", label="한겨레 · 경제", kind="rss", outlet="한겨레", url="https://www.hani.co.kr/rss/economy/", tab="economy"),
    dict(id="khan_eco", label="경향신문 · 경제", kind="rss", outlet="경향신문", url="https://www.khan.co.kr/rss/rssdata/economy_news.xml", tab="economy"),
    dict(id="sbs_eco", label="SBS · 경제", kind="rss", outlet="SBS", url="https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=02&plink=RSSREADER", tab="economy"),
    dict(id="chosun_eco", label="조선일보 · 경제", kind="rss", outlet="조선일보", url="https://www.chosun.com/arc/outboundfeeds/rss/category/economy/?outputType=xml", tab="economy"),
    dict(id="newsis_eco", label="뉴시스 · 경제", kind="rss", outlet="뉴시스", url="https://www.newsis.com/RSS/economy.xml", tab="economy"),
    dict(id="infomax", label="연합인포맥스 · 전체", kind="rss", outlet="연합인포맥스", url="https://news.einfomax.co.kr/rss/allArticle.xml", tab=None),
    dict(id="cnbc_top", label="CNBC · Top News", kind="rss", outlet="CNBC", url="https://www.cnbc.com/id/100003114/device/rss/rss.html", tab=None, lang="en"),
    dict(id="cnbc_mkt", label="CNBC · Markets", kind="rss", outlet="CNBC", url="https://www.cnbc.com/id/20910258/device/rss/rss.html", tab="economy", lang="en"),
    dict(id="mw_top", label="MarketWatch · Top", kind="rss", outlet="MarketWatch", url="https://feeds.content.dowjones.io/public/rss/mw_topstories", tab=None, lang="en"),
    # ── 공식기관 (정책·공식발표 탭) ──
    # 정책브리핑(korea.kr)은 해외 서버(GitHub) 접속을 막아 두어, 부처 발표를 보도한 신뢰 매체 기사로 대신 수집
    dict(id="gov_fin", label="부처 발표 · 재정경제부/기재부", kind="gnews", official=True, official_name="재정경제부·기재부",
         url=gn('"재정경제부" OR "기획재정부" OR "기획예산처" 발표'), must=["재정경제부", "기획재정부", "기재부", "재경부", "기획예산처"]),
    dict(id="gov_fsc", label="부처 발표 · 금융위/금감원", kind="gnews", official=True, official_name="금융위·금감원",
         url=gn('"금융위원회" OR "금융위" OR "금융감독원" 발표'), must=["금융위", "금감원", "금융감독원"]),
    dict(id="gov_molit", label="부처 발표 · 국토교통부", kind="gnews", official=True, official_name="국토교통부",
         url=gn('"국토교통부" OR "국토부" 발표'), must=["국토교통부", "국토부"]),
    dict(id="gov_bok", label="부처 발표 · 한국은행", kind="gnews", official=True, official_name="한국은행",
         url=gn('"한국은행" 발표 OR 통계'), must=["한국은행", "한은"]),
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
    "물가", "CPI", "PCE", "고용지표", "고용보고서", "실업률", "GDP", "성장률", "경상수지", "무역수지", "수출", "관세",
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
    "ipo": ["IPO", "프리IPO", "상장", "주관사", "몸값", "기업가치", "구주매출", "투자 유치", "엑시트", "기술특례", "스팩",
            "예비심사", "상장심사", "listing", "pre-IPO"],
    "ib": ["유상증자", "블록딜", "전환사채", "교환사채", "메자닌", "M&A", "인수합병", "경영권", "매각", "사모펀드", "PEF",
           "인수", "합병", "지분", "IB", "deal", "offering", "acquisition", "merger", "CB", "EB", "BW", "증자", "사채",
           "투자 유치", "최대주주"],
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

IPO_WORDS = ["IPO", "프리IPO", "pre-IPO", "상장 추진", "상장 주관", "대표주관", "상장 철회", "상장 연기", "상장 재추진",
             "재상장", "몸값", "구주매출", "기술특례", "상장심사", "예비심사", "코스닥 상장", "유가증권시장 상장", "코스피 상장",
             "스팩", "SPAC", "증시 입성", "상장 앞둔", "상장 예정", "공모주", "공모가", "수요예측", "공모 청약", "일반청약", "증권신고서",
             "상장 첫날", "따상", "코넥스", "이중상장", "나스닥 상장"]
# IPO 탭에서 빼는 '일정성' 기사 (수요예측 결과·청약 경쟁률·신고서 제출·상장 첫날 주가 등)
IPO_ROUTINE_WORDS = ["수요예측", "청약", "경쟁률", "증권신고서", "공모가 확정", "공모가 상단", "공모가 하단", "상장 첫날",
                     "시초가", "따상", "공모주 일정", "이번주 공모", "청약 일정", "환불", "배정", "상장일", "애프터마켓",
                     "데뷔 첫날", "상장 이튿날", "debut", "first day of trading", "[IPO챗]"]
# IB 탭에서 빼는 비(非)딜성 기사
IB_NOISE_WORDS = ["노조", "세미나", "포럼", "간담회", "접근성", "행사", "주요공시", "공시 모음", "협약식", "업무협약", "MOU"]
# 일정성 단어가 있어도 이 단어가 있으면 '이슈'로 보고 남김
IPO_ISSUE_WORDS = ["철회", "연기", "논란", "흥행 실패", "미달", "부진", "제도", "개편", "역대", "최대", "대어", "조원",
                   "정정 요구", "제동", "금감원"]
# IPO 이슈 우선순위 가점
IPO_PRIORITY = {
    "상장 전 투자유치": ["프리IPO", "pre-IPO", "시리즈C", "시리즈D", "시리즈E", "투자 유치", "투자유치", "브릿지", "기업가치", "valuation", "funding"],
    "주관사": ["주관사 선정", "대표주관", "주관 경쟁", "주관사 교체", "주관 실적", "리그테이블"],
    "상장 추진·연기": ["상장 추진", "상장 연기", "상장 철회", "재도전", "재추진", "이중상장", "나스닥 상장", "해외 상장", "plans IPO", "delays IPO"],
    "몸값·엑시트": ["몸값", "구주매출", "엑시트", "투자금 회수", "밸류에이션", "기업가치"],
    "제도 변화": ["제도", "개편", "개선", "요건", "의무보유", "상장심사", "금융당국", "거래소"],
}
IB_SPECIALIST_OUTLETS = ["더벨", "딜사이트", "인베스트조선"]

# IB 탭: 세부 구분용 단어 (IPO 는 별도 탭)
IB_SUB_WORDS = {
    "ecm": ["유상증자", "블록딜", "전환사채", "교환사채", "신주인수권부사채", "제3자배정", "주주배정", "시간외 대량매매",
            "CB 발행", "EB 발행", "BW 발행", "rights offering", "secondary offering", "block trade"],
    "mna": ["M&A", "인수합병", "경영권", "매각 주관", "우선협상대상자", "지분 인수", "인수 추진", "인수전", "PEF", "사모펀드",
            "예비입찰", "본입찰", "주식매매계약", "acquisition", "merger", "takeover", "buyout"],
}
# IB 중 '정말 큰 건' 가점 단어
IB_BIG_WORDS = ["조원", "조 원", "최대", "역대", "대어", "billion", "record"]
GLOBAL_WORDS = ["뉴욕증시", "나스닥", "S&P", "다우", "엔비디아", "테슬라", "애플", "마이크로소프트", "미국 증시", "미 증시",
                "닛케이", "일본 증시", "중국 증시", "항셍", "유럽 증시", "월가", "빅테크"]

OFFICIAL_KEYWORDS_BOK = ["한국은행", "한은"]

# ─────────────────────────────────────────────────────────────
# 5) 시장과 무관한 기사 거르기
# ─────────────────────────────────────────────────────────────
# 제목에 이 단어(정규식)가 있으면 이슈에서 제외 (연예·가십·인사·운세 등)
NOISE_WORDS = [
    "배우", "가수(?!요)", "아이돌", "방송인", "연예", "개그맨", "개그우먼", "걸그룹", "보이그룹", "유튜버", "인플루언서",
    "남편", "아내", "열애", "결혼식", "이혼", "불륜", "\\[프로필\\]", "프로필\\]", "이 시각 헤드라인", "이 시각", "부고", "\\[인사\\]",
    "운세", "드라마", "예능", "축구", "야구", "농구", "배구", "올림픽", "월드컵", "맛집", "레시피",
    "celebrity", "horoscope",
]
# 경제·시장 관련성 판단 단어 (TAB_RULES·IMPACT_KEYWORDS 와 함께 사용)
# 단독 보도(1개 매체) 이슈는 이 단어가 2개 이상, 여러 매체 이슈는 1개 이상 있어야 남김
RELEVANT_WORDS = [
    "기업", "실적", "매출", "영업이익", "순이익", "투자", "대미투자", "은행", "증권", "금융", "대출", "부채", "채권",
    "펀드", "자산", "주주", "상장사", "시총", "반도체", "AI", "배터리", "자동차", "조선", "원전", "방산", "바이오",
    "유가", "원자재", "금값", "달러", "엔화", "위안", "통화", "재정", "세수", "세금", "세제", "법인세", "예산",
    "규제", "정책", "기재부", "재경부", "금융위", "금감원", "한은", "국토부", "공정위", "산업부", "무역", "통상",
    "소비", "물가", "임금", "일자리", "고용", "경기", "성장", "가계", "부동산", "주택", "아파트", "분양", "청약",
    "전세", "월세", "집값", "매매", "임대", "건설", "PF", "리츠", "ETF", "코스피", "코스닥", "증시", "주가", "IPO",
    "박스권", "랠리", "목표주가", "증권사", "외국인", "기관", "개인 투자자", "수급", "상승", "하락", "급등", "급락",
    "market", "stocks", "economy", "inflation", "Fed", "rates", "bond", "yield", "earnings", "tariff", "oil",
    "dollar", "housing", "mortgage", "bank", "investor", "GDP", "jobs",
]
# 제목 앞머리에서 지울 꼬리표
TITLE_TAGS_STRIP = r"^\s*\[(그래픽|표|영상|사진|이런국장 저런주식|집코노미[^\]]*|부동산360|이슈 ?분석|오늘의 ?\w+)\]\s*"
