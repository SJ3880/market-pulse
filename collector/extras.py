# -*- coding: utf-8 -*-
"""
뉴스 외 추가 지표 수집
 - 국내 금리(한국은행 ECOS): 국고채 3·10년, 기준금리, CD 91일, 주담대 금리   ← ECOS_KEY 필요
 - 부동산(한국부동산원 R-ONE): 주간 아파트 매매·전세 가격 변동률             ← REB_KEY 필요
 - 수급·시황(네이버 금융): 투자자별 순매수, 업종 등락
 - 대형주 등락(Yahoo), 미 국채 10년 검증(FRED)
각 항목은 실패해도 다른 수집에 영향을 주지 않고, 직전 성공 값을 유지합니다.
실패 시 응답 일부를 debug 에 남겨 원인 확인에 씁니다.
"""
import csv
import html
import datetime as dt
import io
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

KST = dt.timezone(dt.timedelta(hours=9))

import threading

_cache = {}   # name -> {"next": 다음 수집 시각, "val": 마지막 결과(성공 또는 지연 표시)}
DEBUG = {}    # name -> 응답 일부 (실패 원인 확인용)
_lock = threading.Lock()


def _cached(name, every_sec, fn):
    """every_sec 동안은 이전 결과 재사용. 실패하면 직전 성공 값을 '지연'으로 표시하고 2분 뒤 재시도."""
    now = time.time()
    with _lock:
        c = _cache.get(name)
    if c and now < c["next"]:
        return c["val"]
    try:
        v = fn()
        v["ok"] = True
        v["fetched_at"] = int(now)
        with _lock:
            _cache[name] = {"next": now + every_sec, "val": v, "good": v}
            for k in [k for k in DEBUG if k == name or k.startswith(name + "_")]:
                DEBUG.pop(k, None)
        return v
    except Exception as ex:  # noqa: BLE001
        err = f"{type(ex).__name__}: {str(ex)[:160]}"
        good = (c or {}).get("good")
        val = dict(good, ok=False, error=err, stale=True) if good else {"ok": False, "error": err}
        with _lock:
            _cache[name] = {"next": now + min(120, every_sec), "val": val, "good": good}
        return val


def _dbg(name, text):
    with _lock:
        DEBUG[name] = text[:4000] if isinstance(text, str) else str(text)[:4000]


def _decode(raw):
    for enc in ("utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ─────────────────────────── 한국은행 ECOS ───────────────────────────
ECOS_SERIES = [
    # key, 이름, 통계표, 주기, 항목코드, 소수점
    ("ktb3", "국고채 3년", "817Y002", "D", "010200000", 3),
    ("ktb10", "국고채 10년", "817Y002", "D", "010210000", 3),
    ("cd91", "CD 91일", "817Y002", "D", "010502000", 2),
    ("base", "기준금리", "722Y001", "D", "0101000", 2),
    ("mortgage", "주담대 금리(신규)", "121Y006", "M", "BECBLA0302", 2),
]


def fetch_ecos(http_get, key):
    if not key:
        return {"ok": False, "error": "ECOS_KEY 미설정", "missing_key": True}

    def run():
        today = dt.datetime.now(KST)
        out = {}
        errors = []
        for k, name, tbl, cyc, item, digits in ECOS_SERIES:
            if cyc == "D":
                back = 400 if k == "base" else 75  # 기준금리는 변경일에만 값이 있을 수 있어 넉넉히
                s, e = (today - dt.timedelta(days=back)).strftime("%Y%m%d"), today.strftime("%Y%m%d")
            else:
                s, e = (today - dt.timedelta(days=800)).strftime("%Y%m"), today.strftime("%Y%m")
            url = f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/1000/{tbl}/{cyc}/{s}/{e}/{item}"
            try:
                txt = _decode(http_get(url, timeout=10, fixture_key="ecos_" + k))
                j = json.loads(txt)
                rows = (j.get("StatisticSearch") or {}).get("row") or []
                pts = []
                for r in rows:
                    v = r.get("DATA_VALUE")
                    if v in (None, "", "-"):
                        continue
                    pts.append([r["TIME"], float(v)])
                if not pts:
                    _dbg("ecos_" + k, txt)
                    errors.append(f"{name}: {(j.get('RESULT') or {}).get('MESSAGE', '데이터 없음')}")
                    continue
                pts.sort()
                last, prev = pts[-1], (pts[-2] if len(pts) > 1 else None)
                out[k] = dict(name=name, digits=digits, cycle=cyc, value=last[1], date=last[0],
                              prev=prev[1] if prev else None,
                              change=round(last[1] - prev[1], 4) if prev else None,
                              series=pts[-60:])
            except Exception as ex:  # noqa: BLE001
                errors.append(f"{name}: {type(ex).__name__}")
        if not out:
            raise RuntimeError("; ".join(errors) or "응답 없음")
        return {"series": out, "errors": errors}

    return _cached("ecos", 30 * 60, run)


# ─────────────────────────── 한국부동산원 R-ONE ───────────────────────────
REB_BASE = "https://www.reb.or.kr/r-one/openapi/"
REGIONS = ["전국", "수도권", "서울"]


def _reb_rows(obj):
    """응답 JSON 안에서 행(dict 목록)을 찾아 평탄화."""
    rows = []

    def walk(o):
        if isinstance(o, dict):
            if "row" in o and isinstance(o["row"], list):
                rows.extend(r for r in o["row"] if isinstance(r, dict))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    return rows


def _reb_find_tables(http_get, key):
    found = {}
    for page in range(1, 6):
        url = f"{REB_BASE}SttsApiTbl.do?KEY={key}&Type=json&pIndex={page}&pSize=1000"
        txt = _decode(http_get(url, timeout=15, fixture_key=f"reb_tbl_{page}"))
        rows = _reb_rows(json.loads(txt))
        if not rows:
            if page == 1:
                _dbg("reb_tables", txt)
            break
        names = [f"{r.get('STATBL_ID')}|{r.get('STATBL_NM')}|{r.get('DTACYCLE_CD') or r.get('DTACYCLE_NM')}"
                 for r in rows if "아파트" in str(r.get("STATBL_NM", ""))]
        for r in rows:
            nm = str(r.get("STATBL_NM", ""))
            cyc = str(r.get("DTACYCLE_CD", "") or r.get("DTACYCLE_NM", ""))
            weekly = "WK" in cyc.upper() or "주" in cyc or "주간" in nm or "(주)" in nm
            if "아파트" not in nm or not weekly:
                continue
            kind = "sale" if "매매" in nm else "jeonse" if "전세" in nm else None
            if not kind:
                continue
            score = (2 if "변동률" in nm else 1 if "지수" in nm else 0) - (1 if "규모" in nm or "연령" in nm else 0)
            if kind not in found or score > found[kind][0]:
                found[kind] = (score, r.get("STATBL_ID"), nm, "변동률" in nm)
        if len(rows) < 1000:
            break
    if found:
        return {k: dict(id=v[1], name=v[2], is_rate=v[3], cycle="WK") for k, v in found.items()}
    # Open API 에 주간 통계가 없으면 월간 '매매가격지수_아파트'·'전세가격지수_아파트' 사용
    monthly = {}
    for page in range(1, 6):
        rows = _reb_rows(json.loads(_decode(http_get(f"{REB_BASE}SttsApiTbl.do?KEY={key}&Type=json&pIndex={page}&pSize=1000",
                                                     timeout=15, fixture_key=f"reb_tbl_{page}"))))
        for r in rows:
            nm = str(r.get("STATBL_NM", ""))
            if str(r.get("DTACYCLE_CD", "")).upper() != "MM" or any(w in nm for w in ("규모", "연령", "계절", "지역별", "통합")):
                continue
            if re.search(r"매매가격지수_아파트$", nm):
                monthly.setdefault("sale", dict(id=r["STATBL_ID"], name=nm, is_rate=False, cycle="MM"))
            elif re.search(r"(?<!월세)전세가격지수_아파트$", nm):
                monthly.setdefault("jeonse", dict(id=r["STATBL_ID"], name=nm, is_rate=False, cycle="MM"))
        if len(rows) < 1000:
            break
    if not monthly:
        raise RuntimeError("아파트 가격지수 통계표를 찾지 못함")
    return monthly


def fetch_reb(http_get, key):
    if not key:
        return {"ok": False, "error": "REB_KEY 미설정", "missing_key": True}

    def run():
        tables = REB_META.get("tables") or _reb_find_tables(http_get, key)
        REB_META["tables"] = tables
        result = {"tables": tables, "series": {}, "cycle": next(iter(tables.values())).get("cycle", "WK")}
        for kind, t in tables.items():
            pts = {}
            cyc = t.get("cycle", "WK")
            base = (f"{REB_BASE}SttsApiTblData.do?KEY={key}&Type=json&pSize=1000"
                    f"&STATBL_ID={t['id']}&DTACYCLE_CD={cyc}")
            if cyc == "MM":  # 최근 약 2년치만 요청
                now = dt.datetime.now(KST)
                start = (now - dt.timedelta(days=800)).strftime("%Y%m")
                base += f"&START_WRTTIME={start}&END_WRTTIME={now.strftime('%Y%m')}"
            first = json.loads(_decode(http_get(base + "&pIndex=1", timeout=15, fixture_key=f"reb_{kind}_1")))
            total = _find_key(first, "list_total_count")
            if cyc == "MM":
                last_page = max(1, -(-int(total or 1000) // 1000))
                page_nos = list(range(2, min(last_page, 15) + 1))
            elif total:
                last_page = max(1, -(-int(total) // 1000))
                page_nos = list(range(max(2, last_page - 7), last_page + 1))
            else:
                page_nos = list(range(2, 9))
            pages = [first]
            for pg in page_nos:  # 최신 자료가 있는 뒤쪽 페이지 위주로 (첫 페이지도 포함)
                pj = json.loads(_decode(http_get(f"{base}&pIndex={pg}", timeout=15, fixture_key=f"reb_{kind}_{pg}")))
                if not _reb_rows(pj):
                    break
                pages.append(pj)
            items = {}
            for pj in pages:
                for r in _reb_rows(pj):
                    items[r.get("ITM_NM", "")] = items.get(r.get("ITM_NM", ""), 0) + 1
            main_item = max(items, key=items.get) if items else ""
            for pj in pages:
                for r in _reb_rows(pj):
                    if r.get("ITM_NM", "") != main_item:
                        continue
                    region = (r.get("CLS_NM") or "").strip()
                    full = (r.get("CLS_FULLNM") or region).strip()
                    if region not in REGIONS or full not in REGIONS and not full.endswith(">" + region) and full != region:
                        continue
                    when = str(r.get("WRTTIME_IDTFR_ID") or "")
                    try:
                        val = float(r.get("DTA_VAL"))
                    except (TypeError, ValueError):
                        continue
                    label = r.get("WRTTIME_DESC") or when
                    pts.setdefault(region, {})[when] = (label, val)
            txt = json.dumps(first, ensure_ascii=False)
            if not pts:
                _dbg("reb_" + kind, txt)
            ser = {}
            for region, d in pts.items():
                keys = sorted(d)
                vals = [(k, d[k][0], d[k][1]) for k in keys]
                if not t["is_rate"]:  # 지수 → 주간 변동률(%)
                    vals = [(vals[i][0], vals[i][1], round((vals[i][2] / vals[i - 1][2] - 1) * 100, 3))
                            for i in range(1, len(vals)) if vals[i - 1][2]]
                ser[region] = [[w, lab, v] for w, lab, v in vals[-(24 if cyc == "MM" else 30):]]
            if not ser:
                _dbg("reb_" + kind + "_rows", txt)
            result["series"][kind] = ser
        if not any(result["series"].values()):
            raise RuntimeError("지역 데이터(전국/수도권/서울)를 찾지 못함")
        return result

    return _cached("reb", 6 * 3600, run)


REB_META = {}


def _find_key(o, k):
    if isinstance(o, dict):
        if k in o:
            return o[k]
        for v in o.values():
            r = _find_key(v, k)
            if r is not None:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _find_key(v, k)
            if r is not None:
                return r
    return None


# ─────────────────────────── 투자자별 순매수 (네이버 금융) ───────────────────────────
# 네이버 금융이 새 화면(자바스크립트로 그리는 방식)으로 바뀌어, 여러 주소를 차례로 시도
FLOW_RE = re.compile(r"(개인|외국인|기관)\s*(?:</?[^>]+>\s*)*?([+\-−]?\s*[\d,]+)\s*(?:</?[^>]+>\s*)*억")
DAY_ROW_RE = re.compile(r"(\d{2}\.\d{2}\.\d{2})\s*</td>\s*<td[^>]*>\s*([+\-−]?[\d,]+)\s*</td>\s*<td[^>]*>\s*([+\-−]?[\d,]+)\s*</td>"
                        r"\s*<td[^>]*>\s*([+\-−]?[\d,]+)\s*</td>", re.S)
JSON_KEYS = {
    "individual": r"(?:personal|individual|indi|private)",
    "foreign": r"(?:foreign|frgn|forgn)",
    "institution": r"(?:institution|institutional|organ|inst)",
}


def _num(x):
    return int(float(x.replace(",", "").replace("−", "-").replace(" ", "").replace("+", "")))


def _flow_from_text(txt):
    """HTML 표 / JSON / 페이지 속 데이터에서 개인·외국인·기관 값 찾기."""
    # 1) 일별 투자자 매매동향 표 (가장 최근 날짜 행): 날짜, 개인, 외국인, 기관계
    m = DAY_ROW_RE.search(txt)
    if m:
        return {"individual": _num(m.group(2)), "foreign": _num(m.group(3)), "institution": _num(m.group(4)),
                "date": m.group(1)}, "day-table"
    # 2) 한글 표기 '개인 +1,234억'
    i0 = txt.find("투자자별")
    vals = {}
    for who, n in FLOW_RE.findall(txt[i0: i0 + 4000] if i0 >= 0 else txt):
        vals.setdefault(who, n)
    if len(vals) == 3:
        return {"individual": _num(vals["개인"]), "foreign": _num(vals["외국인"]), "institution": _num(vals["기관"])}, "kr-text"
    # 3) JSON 키 (네이버 dealTrendInfo 의 personalValue 등)
    j = txt.find('"dealTrendInfo"')
    if j >= 0:
        txt = txt[j: j + 600]
    out = {}
    for k, pat in JSON_KEYS.items():
        m = re.search(r'"[A-Za-z]*' + pat + r'[A-Za-z]*"\s*:\s*"?([+\-−]?[\d,]+(?:\.\d+)?)', txt, re.I)
        if m:
            out[k] = _num(m.group(1))
    if len(out) == 3:
        return out, "json"
    return None, None


def fetch_flow(http_get):
    def run():
        out, used = {}, {}
        today = dt.datetime.now(KST).strftime("%Y%m%d")
        for mkt, sosok in (("KOSPI", "01"), ("KOSDAQ", "02")):
            cands = [
                f"https://finance.naver.com/sise/investorDealTrendDay.naver?bizdate={today}&sosok={sosok}",
                f"https://m.stock.naver.com/api/index/{mkt}/integration",
                f"https://m.stock.naver.com/api/index/{mkt}/trend",
                f"https://finance.naver.com/sise/sise_index.naver?code={mkt}",
            ]
            for n, url in enumerate(cands):
                try:
                    txt = _decode(http_get(url, timeout=8, fixture_key=f"nv_flow_{mkt}_{n}"))
                except Exception as ex:  # noqa: BLE001
                    _dbg(f"flow_{mkt}_{n}", f"{url}\n{type(ex).__name__}: {ex}")
                    continue
                vals, how = _flow_from_text(txt)
                if vals:
                    out[mkt] = vals
                    used[mkt] = f"{n}:{how}"
                    break
                k = max(txt.find("foreign"), txt.find("외국인"), txt.find("frgn"))
                _dbg(f"flow_{mkt}_{n}", url + "\n" + (txt[max(0, k - 1500): k + 1500] if k >= 0 else txt[-3000:]))
        if not out:
            raise RuntimeError("투자자별 매매동향을 읽지 못함 (debug/flow_* 참고)")
        return {"markets": out, "unit": "억원", "via": used}

    return _cached("flow", 60, run)


# ─────────────────────────── 업종 등락 (섹터 ETF, Yahoo) ───────────────────────────
# 네이버 업종 화면 대신, 업종을 대표하는 국내 섹터 ETF 등락률로 업종 흐름을 봄 (Yahoo 시세)
SECTOR_ETFS = [
    ("091160.KS", "반도체"), ("091170.KS", "은행"), ("091180.KS", "자동차"), ("102970.KS", "증권"),
    ("117700.KS", "건설"), ("117680.KS", "철강"), ("117460.KS", "에너지화학"), ("244580.KS", "바이오"),
    ("140700.KS", "보험"), ("140710.KS", "운송"), ("266410.KS", "필수소비재"), ("266390.KS", "경기소비재"),
    ("305540.KS", "2차전지"), ("266370.KS", "IT"), ("139230.KS", "중공업"), ("139220.KS", "건설기계·조선"),
    ("228790.KS", "화장품"), ("091220.KS", "금융"),
]


def fetch_sectors(fetch_quote):
    def run():
        with ThreadPoolExecutor(max_workers=8) as ex:
            qs = list(ex.map(lambda e: fetch_quote(("etf", e[0], e[1], 0, "원")), SECTOR_ETFS))
        rows = [{"no": q["sym"], "name": q["name"], "pct": round(q["pct"], 2)} for q in qs
                if q.get("price") is not None and q.get("pct") is not None]
        if len(rows) < 6:
            raise RuntimeError(f"섹터 ETF 시세 부족({len(rows)}개)")
        rows.sort(key=lambda r: -r["pct"])
        return {"top": rows[:6], "bottom": rows[-6:][::-1], "count": len(rows),
                "up": sum(1 for r in rows if r["pct"] > 0), "down": sum(1 for r in rows if r["pct"] < 0),
                "basis": "섹터 ETF"}

    return _cached("sectors", 120, run)


# ─────────────────────────── 대형주 (Yahoo) ───────────────────────────
BIGCAPS = [
    ("005930.KS", "삼성전자"), ("000660.KS", "SK하이닉스"), ("373220.KS", "LG에너지솔루션"),
    ("207940.KS", "삼성바이오로직스"), ("005380.KS", "현대차"), ("000270.KS", "기아"),
    ("012450.KS", "한화에어로스페이스"), ("329180.KS", "HD현대중공업"), ("034020.KS", "두산에너빌리티"),
    ("068270.KS", "셀트리온"), ("105560.KS", "KB금융"), ("055550.KS", "신한지주"),
    ("086790.KS", "하나금융지주"), ("035420.KS", "NAVER"), ("035720.KS", "카카오"),
]


def fetch_bigcaps(fetch_quote):
    def run():
        with ThreadPoolExecutor(max_workers=8) as ex:
            qs = list(ex.map(lambda e: fetch_quote(("big", e[0], e[1], 0, "원")), BIGCAPS))
        ok = [dict(sym=q["sym"], name=q["name"], price=q["price"], pct=q["pct"], spark=q.get("spark", [])[-40:])
              for q in qs if q.get("price") is not None]
        if not ok:
            raise RuntimeError("대형주 시세 없음")
        return {"stocks": ok}

    return _cached("bigcaps", 60, run)


# ─────────────────────────── 미 국채 10년 (FRED) ───────────────────────────
def fred_10y(http_get):
    """FRED DGS10 최근값(전일 기준). Yahoo 값 단위 검증용."""
    def run():
        txt = _decode(http_get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10", timeout=25,
                               fixture_key="fred_dgs10"))
        rows = [r for r in csv.reader(io.StringIO(txt)) if len(r) == 2]
        for d, v in reversed(rows[1:]):
            try:
                return {"value": float(v), "date": d}
            except ValueError:
                continue
        raise RuntimeError("값 없음")
    return _cached("fred10", 3600, run)


# ─────────────────────────── 코스피·코스닥 실시간 (네이버) ───────────────────────────
def _f(x):
    try:
        return float(str(x).replace(",", "").replace("+", "").strip())
    except (TypeError, ValueError):
        return None


def fetch_realtime_index(http_get):
    """Yahoo 지수는 수 분 늦을 수 있어, 장중에는 네이버 실시간 지수 값을 우선 사용."""
    out = {}
    for mkt, sym in (("KOSPI", "^KS11"), ("KOSDAQ", "^KQ11")):
        try:
            txt = _decode(http_get(f"https://m.stock.naver.com/api/index/{mkt}/basic", timeout=5,
                                   fixture_key="nv_basic_" + mkt))
            j = json.loads(txt)
            price = _f(j.get("closePrice"))
            chg = _f(j.get("compareToPreviousClosePrice"))
            pct = _f(j.get("fluctuationsRatio"))
            if price is None:
                raise ValueError("closePrice 없음")
            falling = str((j.get("compareToPreviousPrice") or {}).get("name", "")).upper() in ("FALLING", "LOWER_LIMIT")
            if chg is not None and falling and chg > 0:
                chg = -chg
            if pct is not None and falling and pct > 0:
                pct = -pct
            out[sym] = {"price": price, "change": chg, "pct": pct,
                        "time": j.get("localTradedAt") or j.get("tradedAt"), "status": j.get("marketStatus")}
        except Exception as ex:  # noqa: BLE001
            _dbg("rt_" + mkt, f"{type(ex).__name__}: {ex}\n" + (locals().get("txt") or "")[:2000])
    return out


# ─────────────────────────── 크립토 (업비트 원화 시세 + 해외 달러 시세 + 공포탐욕) ───────────────────────────
CRYPTO_COINS = [("BTC", "비트코인", "BTC-USD", 0), ("ETH", "이더리움", "ETH-USD", 0),
                ("XRP", "리플", "XRP-USD", 4), ("SOL", "솔라나", "SOL-USD", 2)]
FNG_KO = {"Extreme Fear": "극단적 공포", "Fear": "공포", "Neutral": "중립", "Greed": "탐욕", "Extreme Greed": "극단적 탐욕"}


def _crypto_slow(http_get):
    """공포·탐욕 지수, 전체 시총·비트코인 점유율 (자주 안 바뀜 → 10분마다)."""
    def run():
        out = {}
        try:
            fg = json.loads(http_get("https://api.alternative.me/fng/?limit=30", timeout=8, fixture_key="fng"))["data"]
            out["fng"] = dict(value=int(fg[0]["value"]), label=FNG_KO.get(fg[0]["value_classification"], fg[0]["value_classification"]),
                              history=[int(x["value"]) for x in fg][::-1])
        except Exception as ex:  # noqa: BLE001
            out["fng_error"] = f"{type(ex).__name__}"
        try:
            g = json.loads(http_get("https://api.coingecko.com/api/v3/global", timeout=8, fixture_key="cg_global"))["data"]
            out["global"] = dict(mcap_usd=g["total_market_cap"]["usd"], mcap_pct=round(g["market_cap_change_percentage_24h_usd"], 2),
                                 btc_dom=round(g["market_cap_percentage"]["btc"], 1), eth_dom=round(g["market_cap_percentage"]["eth"], 1))
        except Exception as ex:  # noqa: BLE001
            out["global_error"] = f"{type(ex).__name__}"
        if not out.get("fng") and not out.get("global"):
            raise RuntimeError("공포탐욕·시총 모두 실패")
        return out
    return _cached("crypto_slow", 600, run)


def fetch_crypto(http_get, fetch_quote):
    def run():
        with ThreadPoolExecutor(max_workers=6) as ex:
            qs = list(ex.map(lambda c: fetch_quote(("crypto", c[2], c[1], c[3], "$")), CRYPTO_COINS))
            fx_f = ex.submit(fetch_quote, ("fx", "KRW=X", "원/달러", 2, "원"))
        fx = fx_f.result().get("price")
        up, up_err = {}, None
        try:
            raw = http_get("https://api.upbit.com/v1/ticker?markets=" + ",".join("KRW-" + c[0] for c in CRYPTO_COINS),
                           timeout=6, fixture_key="upbit")
            for t in json.loads(raw):
                up[t["market"].split("-")[1]] = t
        except Exception as ex:  # noqa: BLE001
            up_err = f"업비트: {type(ex).__name__}"
            try:  # 빗썸으로 대신
                d = json.loads(http_get("https://api.bithumb.com/public/ticker/ALL_KRW", timeout=6, fixture_key="bithumb"))["data"]
                for c in CRYPTO_COINS:
                    t = d.get(c[0])
                    if t:
                        up[c[0]] = dict(trade_price=float(t["closing_price"]),
                                        signed_change_rate=float(t["fluctate_rate_24H"]) / 100,
                                        acc_trade_price_24h=float(t["acc_trade_value_24H"]))
                up_err += " → 빗썸 시세 사용"
            except Exception:  # noqa: BLE001
                pass
        coins = []
        for (code, name, _, digits), q in zip(CRYPTO_COINS, qs):
            u = up.get(code, {})
            usd, krw = q.get("price"), u.get("trade_price")
            prem = round((krw / (usd * fx) - 1) * 100, 2) if usd and krw and fx else None
            coins.append(dict(code=code, name=name, digits=digits, usd=usd, usd_pct=q.get("pct"),
                              spark=(q.get("spark") or [])[-60:], krw=krw,
                              krw_pct=round(u["signed_change_rate"] * 100, 2) if u.get("signed_change_rate") is not None else None,
                              krw_vol=u.get("acc_trade_price_24h"), premium=prem))
        if not any(c["usd"] or c["krw"] for c in coins):
            raise RuntimeError("코인 시세 없음")
        out = dict(coins=coins, usdkrw=fx, src=("빗썸" if up_err and up else "업비트") + "(원화)·Yahoo(달러)")
        if up_err:
            out["warn"] = up_err
        slow = _crypto_slow(http_get)
        for k in ("fng", "global"):
            if slow.get(k):
                out[k] = slow[k]
        return out

    return _cached("crypto", 30, run)


def collect_extras(http_get, fetch_quote):
    keys = {"ecos": os.environ.get("ECOS_KEY", "").strip(), "reb": os.environ.get("REB_KEY", "").strip()}
    jobs = {
        "rates": lambda: fetch_ecos(http_get, keys["ecos"]),
        "realestate": lambda: fetch_reb(http_get, keys["reb"]),
        "flow": lambda: fetch_flow(http_get),
        "sectors": lambda: fetch_sectors(fetch_quote),
        "bigcaps": lambda: fetch_bigcaps(fetch_quote),
        "realtime": lambda: {"ok": True, "quotes": fetch_realtime_index(http_get)},
        "crypto": lambda: fetch_crypto(http_get, fetch_quote),
    }
    with ThreadPoolExecutor(max_workers=7) as ex:
        futs = {k: ex.submit(f) for k, f in jobs.items()}
        return {k: f.result() for k, f in futs.items()}
