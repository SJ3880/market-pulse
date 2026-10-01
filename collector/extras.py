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
        for r in rows:
            nm = r.get("STATBL_NM", "")
            cyc = r.get("DTACYCLE_CD", "") or r.get("DTACYCLE_NM", "")
            if "아파트" not in nm or "주간" not in nm and "WK" not in cyc:
                continue
            kind = "sale" if "매매" in nm else "jeonse" if "전세" in nm else None
            if not kind:
                continue
            score = (2 if "변동률" in nm else 1 if "지수" in nm else 0) - (1 if "규모" in nm or "연령" in nm else 0)
            if kind not in found or score > found[kind][0]:
                found[kind] = (score, r.get("STATBL_ID"), nm, "변동률" in nm)
        if len(rows) < 1000:
            break
    if not found:
        raise RuntimeError("주간 아파트 통계표를 찾지 못함")
    return {k: dict(id=v[1], name=v[2], is_rate=v[3]) for k, v in found.items()}


def fetch_reb(http_get, key):
    if not key:
        return {"ok": False, "error": "REB_KEY 미설정", "missing_key": True}

    def run():
        tables = REB_META.get("tables") or _reb_find_tables(http_get, key)
        REB_META["tables"] = tables
        result = {"tables": tables, "series": {}}
        for kind, t in tables.items():
            pts = {}
            base = (f"{REB_BASE}SttsApiTblData.do?KEY={key}&Type=json&pSize=1000"
                    f"&STATBL_ID={t['id']}&DTACYCLE_CD=WK")
            first = json.loads(_decode(http_get(base + "&pIndex=1", timeout=15, fixture_key=f"reb_{kind}_1")))
            total = _find_key(first, "list_total_count")
            if total:
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
                ser[region] = [[w, lab, v] for w, lab, v in vals[-30:]]
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


# ─────────────────────────── 네이버 금융: 수급·업종 ───────────────────────────
FLOW_RE = re.compile(r"(개인|외국인|기관)\s*(?:</?[^>]+>\s*)*?([+\-−]?\s*[\d,]+)\s*(?:</?[^>]+>\s*)*억")


def fetch_flow(http_get):
    def run():
        out = {}
        for mkt in ("KOSPI", "KOSDAQ"):
            url = f"https://finance.naver.com/sise/sise_index.naver?code={mkt}"
            txt = _decode(http_get(url, timeout=8, fixture_key="nv_index_" + mkt))
            i0 = txt.find("투자자별")
            section = txt[i0: i0 + 4000] if i0 >= 0 else txt  # 투자자별 매매동향 영역만
            vals = {}
            for who, num in FLOW_RE.findall(section):
                if who in vals:
                    continue
                n = num.replace(",", "").replace("−", "-").replace(" ", "")
                try:
                    vals[who] = int(n)
                except ValueError:
                    pass
            if len(vals) < 3:
                i = txt.find("외국인")
                _dbg("flow_" + mkt, txt[max(0, i - 1500): i + 1500] if i >= 0 else txt[:3000])
                continue
            out[mkt] = {"individual": vals["개인"], "foreign": vals["외국인"], "institution": vals["기관"]}
        if not out:
            raise RuntimeError("투자자별 매매동향을 읽지 못함")
        return {"markets": out, "unit": "억원"}

    return _cached("flow", 60, run)


SECTOR_RE = re.compile(
    r"type=upjong&(?:amp;)?no=(\d+)\"[^>]*>([^<]+)</a>\s*</td>\s*<td[^>]*>\s*(?:<[^>]+>\s*)*([+\-]?[\d\.]+)\s*%", re.S)


def fetch_sectors(http_get):
    def run():
        txt = _decode(http_get("https://finance.naver.com/sise/sise_group.naver?type=upjong", timeout=8,
                               fixture_key="nv_upjong"))
        rows = []
        for no, name, pct in SECTOR_RE.findall(txt):
            try:
                rows.append({"no": no, "name": html.unescape(name).strip(), "pct": float(pct)})
            except ValueError:
                pass
        if len(rows) < 10:
            i = txt.find("upjong&")
            _dbg("sectors", txt[max(0, i - 500): i + 3000] if i >= 0 else txt[:3000])
            raise RuntimeError(f"업종 표를 읽지 못함({len(rows)}개)")
        rows.sort(key=lambda r: -r["pct"])
        up = sum(1 for r in rows if r["pct"] > 0)
        return {"top": rows[:6], "bottom": rows[-6:][::-1], "count": len(rows), "up": up,
                "down": sum(1 for r in rows if r["pct"] < 0)}

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
        txt = _decode(http_get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10", timeout=10,
                               fixture_key="fred_dgs10"))
        rows = [r for r in csv.reader(io.StringIO(txt)) if len(r) == 2]
        for d, v in reversed(rows[1:]):
            try:
                return {"value": float(v), "date": d}
            except ValueError:
                continue
        raise RuntimeError("값 없음")
    return _cached("fred10", 3600, run)


def collect_extras(http_get, fetch_quote):
    keys = {"ecos": os.environ.get("ECOS_KEY", "").strip(), "reb": os.environ.get("REB_KEY", "").strip()}
    jobs = {
        "rates": lambda: fetch_ecos(http_get, keys["ecos"]),
        "realestate": lambda: fetch_reb(http_get, keys["reb"]),
        "flow": lambda: fetch_flow(http_get),
        "sectors": lambda: fetch_sectors(http_get),
        "bigcaps": lambda: fetch_bigcaps(fetch_quote),
        "fred10": lambda: fred_10y(http_get),
    }
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {k: ex.submit(f) for k, f in jobs.items()}
        return {k: f.result() for k, f in futs.items()}
