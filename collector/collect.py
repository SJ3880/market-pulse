# -*- coding: utf-8 -*-
"""
Market Pulse 수집기
 - 신뢰 매체 뉴스(RSS)와 시장지표를 모아 '지금 가장 이슈인 것'을 점수화해 JSON 스냅샷으로 저장합니다.
 - GitHub Actions 에서 1분 간격으로 반복 실행되고, 결과는 저장소의 `data` 브랜치에 올라갑니다.

실행 예)
  python collector/collect.py --once --no-push          # 한 번만 수집해서 ./out 에 저장 (로컬 확인용)
  python collector/collect.py --loop-minutes 55          # 55분 동안 1분마다 수집 + data 브랜치 업로드
외부 라이브러리 없이 파이썬 기본 기능만 사용합니다.
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import email.utils
import gzip
import html.entities
import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sources as S  # noqa: E402
import extras as X  # noqa: E402
import alerts as A  # noqa: E402
import feedback as FB  # noqa: E402
import summaries as SM  # noqa: E402

SUM_CACHE = {}  # 원문 요약 저장분 {링크: [시각, 요약]}

KST = dt.timezone(dt.timedelta(hours=9))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MAX_AGE_H = 72           # 수집 단계에서 이보다 오래된 기사는 버림
TAB_MAX_AGE_H = {"ipo": 72, "ib": 72, "funding": 72, "crypto": 30}   # 그 외 탭은 36시간 (리그테이블 등 피드별 예외 허용)
TAB_LIMIT = {"ipo": 20, "ib": 20, "funding": 20, "crypto": 20}       # 탭별 최대 이슈 수 (그 외 TOP_N)
TAB_HALF_LIFE = {"ipo": 14, "ib": 14, "funding": 16}   # 최신성 감소 속도(시간) — 딜 탭은 천천히
TOP_N = 30               # 탭별 이슈 개수
FIXTURE_DIR = os.environ.get("FIXTURE_DIR")  # 테스트용: 실제 인터넷 대신 파일에서 읽기


def log(*a):
    print(dt.datetime.now(KST).strftime("%H:%M:%S"), *a, flush=True)


# ─────────────────────────── 네트워크 ───────────────────────────
def http_get(url, timeout=8, fixture_key=None):
    if FIXTURE_DIR:
        p = os.path.join(FIXTURE_DIR, (fixture_key or hashlib.md5(url.encode()).hexdigest()))
        for ext in ("", ".xml", ".json"):
            if os.path.exists(p + ext):
                with open(p + ext, "rb") as f:
                    return f.read()
        raise FileNotFoundError(f"fixture 없음: {fixture_key}")
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "gzip",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data


# ─────────────────────────── 피드 파싱 ───────────────────────────
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def strip_html(s):
    if not s:
        return ""
    s = html.unescape(TAG_RE.sub(" ", html.unescape(s)))
    return WS_RE.sub(" ", s).strip()


def local(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_time(s):
    if not s:
        return None
    s = re.sub(r"\b(KST|GMT\+0?9(:00)?)\b", "+0900", s.strip())
    try:
        t = email.utils.parsedate_to_datetime(s)
        if t.tzinfo is None:  # RFC822 의 '-0000' 등 시간대 미상 → UTC 로 간주
            t = t.replace(tzinfo=dt.timezone.utc)
        return t.timestamp()
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            t = dt.datetime.strptime(s.replace("Z", "+00:00"), fmt)
            if t.tzinfo is None:
                t = t.replace(tzinfo=KST)
            return t.timestamp()
        except Exception:
            continue
    return None


XML_ENT = {"amp", "lt", "gt", "quot", "apos"}


ENT_RE = re.compile(r"&(#\d+;|#x[0-9a-fA-F]+;|(\w+);)?")


def fix_entities(t):
    def sub(m):
        if m.group(1) is None:
            return "&amp;"            # 맨 & 기호
        name = m.group(2)
        if name is None or name in XML_ENT:
            return m.group(0)         # 숫자 엔티티 / XML 기본 엔티티는 그대로
        cp = html.entities.name2codepoint.get(name)
        return f"&#{cp};" if cp else "&amp;" + name + ";"
    return ENT_RE.sub(sub, t)


def decode_xml(raw):
    if isinstance(raw, str):
        return raw
    m = re.match(rb"\s*<\?xml[^>]*encoding=[\"']([\w\-]+)[\"']", raw)
    enc = (m.group(1).decode().lower() if m else "utf-8")
    if enc in ("euc-kr", "ks_c_5601-1987", "ksc5601"):
        enc = "cp949"
    try:
        return raw.decode(enc)
    except (LookupError, UnicodeDecodeError):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("cp949", errors="replace")


def parse_feed(raw):
    """RSS 2.0 / Atom 을 공통 형식(list of dict)으로."""
    txt = decode_xml(raw)
    txt = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", txt)
    txt = re.sub(r"^\s*<\?xml[^>]*\?>", "", txt)
    # CDATA 밖에서만 엔티티 정리 (&nbsp; 같은 HTML 엔티티는 XML 파서가 거부)
    parts = re.split(r"(<!\[CDATA\[.*?\]\]>)", txt, flags=re.S)
    txt = "".join(p if p.startswith("<![CDATA[") else fix_entities(p) for p in parts)
    root = ET.fromstring(txt)
    out = []
    for el in root.iter():
        name = local(el.tag)
        if name not in ("item", "entry"):
            continue
        d = {"title": "", "link": "", "pub": None, "desc": "", "desc_html": "", "source": "", "source_url": ""}
        for c in el:
            n = local(c.tag)
            if n == "title":
                d["title"] = strip_html(c.text or "")
            elif n == "link":
                rel = c.get("rel", "alternate")
                v = (c.text or c.get("href") or "").strip()
                if v and (rel == "alternate" or not d["link"]):
                    d["link"] = html.unescape(v)
            elif n in ("pubDate", "published", "updated", "date") and not d["pub"]:
                d["pub"] = parse_time(c.text)
            elif n in ("description", "summary", "content") and not d["desc_html"]:
                d["desc_html"] = c.text or ""
                d["desc"] = strip_html(c.text or "")
            elif n == "source":
                d["source"] = (c.text or "").strip()
                d["source_url"] = c.get("url", "")
        if d["title"] and d["link"]:
            out.append(d)
    return out


GN_RELATED_RE = re.compile(r'<a href="([^"]+)"[^>]*>(.*?)</a>(?:&nbsp;|\s)*<font[^>]*>(.*?)</font>', re.S)


def normalize_outlet(name):
    name = (name or "").strip()
    name = S.OUTLET_ALIASES.get(name, name)
    if name in S.OUTLETS:
        return name
    # 'xxx 뉴스', 'www.xxx.com' 같은 변형 처리
    for k in S.OUTLETS:
        if name.lower() == k.lower():
            return k
    base = re.sub(r"\s*(뉴스|신문|닷컴)$", "", name)
    if base in S.OUTLETS:
        return base
    return name


EXCLUDE_RE = [re.compile(p) for p in S.EXCLUDE_PATTERNS]


def excluded(title):
    return any(r.search(title) for r in EXCLUDE_RE)


OUTLET_NAMES = set(S.OUTLETS) | set(S.OUTLET_ALIASES)


def clean_title(title):
    """' - 머니투데이' 같은 매체명 꼬리, '[그래픽]' 같은 앞 꼬리표 제거."""
    t = title.strip()
    for _ in range(2):
        if " - " in t:
            head, tail = t.rsplit(" - ", 1)
            tail_n = tail.strip()
            if tail_n in OUTLET_NAMES or normalize_outlet(tail_n) in S.OUTLETS:  # 알려진 매체명일 때만
                t = head.strip()
    t = re.sub(S.TITLE_TAGS_STRIP, "", t)
    return t.strip()


NOISE_RE = re.compile("|".join(S.NOISE_WORDS))


def is_noise(title):
    return bool(NOISE_RE.search(title))


RELEVANT_ALL = list(dict.fromkeys(S.RELEVANT_WORDS + S.IMPACT_KEYWORDS + [w for ws in S.TAB_RULES.values() for w in ws]))


IMPACT_SET = {w.lower() for w in S.IMPACT_KEYWORDS}


_WORD_RE = {}


def _has(word, tl):
    w = word.lower()
    if w.isascii():  # 영문은 단어 경계로 ('ai' 가 'said' 에 걸리지 않게)
        r = _WORD_RE.get(w) or _WORD_RE.setdefault(w, re.compile(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])"))
        return bool(r.search(tl))
    return w in tl


def relevance(text, tab=None):
    """경제·시장 관련 단어 점수 (시장영향 키워드·해당 탭 핵심어는 2점)."""
    tl = text.lower()
    strong = IMPACT_SET | {w.lower() for w in S.TAB_RULES.get(tab, [])}
    return sum(2 if w.lower() in strong else 1 for w in RELEVANT_ALL if _has(w, tl))


def items_from_feed(feed, raw, now):
    entries = parse_feed(raw)
    items = []
    for e in entries:
        title, outlet = e["title"], feed.get("outlet", "")
        if feed["kind"] == "gnews":
            outlet = e["source"]
            if not outlet and " - " in title:
                outlet = title.rsplit(" - ", 1)[1]
            suffix = " - " + outlet
            if outlet and title.endswith(suffix):
                title = title[: -len(suffix)].strip()
        outlet = normalize_outlet(outlet)
        if feed.get("brand") and (not feed.get("brand_of") or outlet in feed["brand_of"]):
            outlet = feed["brand"]  # site: 로 좁힌 검색이라 전문 섹션(시그널·마켓인사이트 등) 이름으로 표시
        if outlet not in S.OUTLETS or outlet in S.PAYWALL_OUTLETS:
            continue  # 화이트리스트 밖 매체·유료 전용 매체(본문 확인 불가) 제외
        title = clean_title(title)
        ts = e["pub"] or now
        if now - ts > feed.get("max_age_h", MAX_AGE_H) * 3600 or ts - now > 3600:
            continue
        if excluded(title) or is_noise(title) or not re.match(r"https?://", e["link"]):
            continue
        if feed.get("must") and not any(w in title for w in feed["must"]):
            continue  # 부처 발표 피드: 제목에 기관명이 있는 기사만
        group = None
        related = []
        if feed["kind"] == "gnews" and e["desc_html"]:
            for href, rt, ro in GN_RELATED_RE.findall(html.unescape(e["desc_html"])):
                ro_n = normalize_outlet(strip_html(ro))
                rt = clean_title(strip_html(rt))
                if ro_n in S.OUTLETS and ro_n not in S.PAYWALL_OUTLETS and rt and not excluded(rt) and not is_noise(rt) and href.startswith("http"):
                    related.append((href, rt, ro_n))
            if len(related) > 1:
                group = hashlib.md5(e["link"].encode()).hexdigest()[:10]
        summary = "" if feed["kind"] == "gnews" else e["desc"][:700]
        base = dict(feed=feed["id"], tab=feed.get("tab"), sub=feed.get("sub"), lang=feed.get("lang", "ko"),
                    max_age_h=feed.get("max_age_h"),
                    official=feed.get("official", False), top=feed.get("top", False), group=group)
        if feed.get("official_name"):  # 발표 기관명으로 표시하고, 보도 매체는 요약란에
            summary = f"{outlet} 보도"
            outlet = feed["official_name"]
            related = []
        if related:
            for href, rt, ro in related:
                items.append(dict(base, title=rt, link=href, outlet=ro, ts=ts, summary=""))
        else:
            items.append(dict(base, title=title, link=e["link"], outlet=outlet, ts=ts, summary=summary))
    return items


def fetch_all_feeds(now):
    results, status = [], []

    def work(feed):
        t0 = time.time()
        try:
            raw = http_get(feed["url"], fixture_key=feed["id"])
            its = items_from_feed(feed, raw, now)
            return feed, its, None, time.time() - t0
        except Exception as ex:  # noqa: BLE001
            return feed, [], f"{type(ex).__name__}: {str(ex)[:120]}", time.time() - t0

    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        for feed, its, err, sec in ex.map(work, S.FEEDS):
            results.extend(its)
            status.append(dict(id=feed["id"], label=feed["label"], ok=err is None, count=len(its),
                               ms=int(sec * 1000), error=err))
    return results, status


# ─────────────────────────── 분류 ───────────────────────────
def contains_any(text, words):
    tl = text.lower()
    return sum(1 for w in words if w.lower() in tl)


def classify(item):
    t = item["title"] + " " + item.get("summary", "")[:80]
    scores = {tab: contains_any(t, words) for tab, words in S.TAB_RULES.items()}
    if item["tab"]:
        tab = item["tab"]
        # 피드 탭이 정해져 있어도 부동산 신호가 훨씬 강하면 이동 (예: 한경 경제 피드의 집값 기사)
        best = max(scores, key=scores.get)
        if best != tab and scores[best] >= 2 and scores.get(tab, 0) == 0:
            tab = best
    else:
        best = max(scores, key=scores.get)
        tab = best if scores[best] > 0 else None
    sub = None
    # IPO 단어 → IPO 탭, 유증·블록딜·메자닌·M&A 단어 → IB 탭 (어느 피드에서 왔든)
    is_ipo = contains_any(t, S.IPO_WORDS) > 0
    ib_sub = next((k for k in ("ecm", "mna") if contains_any(t, S.IB_SUB_WORDS[k])), None)
    if item.get("tab") == "ipo" or (is_ipo and tab in ("stocks", "economy", "ib", "funding", None)):
        return "ipo", None
    is_crypto = contains_any(item["title"], S.CRYPTO_WORDS) > 0
    if item.get("tab") == "crypto" or (is_crypto and tab in ("stocks", "economy", "crypto", "ib", None)):
        return "crypto", None
    if tab == "crypto":
        tab = "economy"
    is_funding = contains_any(t, S.FUNDING_WORDS) > 0
    if item.get("tab") == "funding" or (is_funding and tab in ("stocks", "economy", "ib", None)):
        return ("funding", None) if is_funding or item.get("tab") == "funding" else (tab, None)
    if tab == "funding":
        tab = "economy"
    if item.get("tab") == "ib":
        return "ib", ib_sub or item.get("sub")
    if ib_sub and tab in ("stocks", "economy", None):
        return "ib", ib_sub
    if tab == "ipo":  # '상장' 같은 넓은 단어로만 걸린 기사는 주식 탭으로
        tab = "stocks"
    if tab == "ib" and not ib_sub:
        tab = "economy"
    if tab == "stocks":
        if item["lang"] == "en" or contains_any(t, S.GLOBAL_WORDS):
            sub = "global"
        else:
            sub = "kr"
    return tab, sub


# ─────────────────────────── 묶기(클러스터링) ───────────────────────────
PARTICLE_RE = re.compile(
    r"(으로|에서|까지|부터|에게|보다|처럼|이나|이며|이고|이다|했다|한다|된다|됐다|하는|하고|해서|"
    r"은|는|이|가|을|를|에|의|도|로|와|과|만|나)$")
STOP = set("""종합 속보 단독 오늘 내일 올해 지난해 이번 관련 대한 위해 이후 전망 기자 뉴스 등 것 수 위 중 및 더 첫 또 왜 다시
결국 사실상 현장 영상 포토 the a an of to in on for and as at by with from is are be its it this that after amid
says said will new us 美 韓 中 日 vs 그 이 저 한 두 세 일 월 년 개 명 억 조 원 만 달러 1일 2일 국내 시장 투자 경제 정부
상승 하락 앞두고 몰려 연속 촉각 주목 분석 전문가 우려 기대 기대감 가능성 발표 확대 축소 증가 감소 계속 여전 지속 돌파
shares stock stocks rise rises rising fall falls falling jump jumps jumped earnings report reports ahead futures
prices price after before beat beats miss misses market markets rally gains gain losses loss week today year how what why
강세 약세 마감 출발 개장 기록 최대 최고 최저 만에 넘어 넘었 주간 이번주 지난주 비상 우려에 for over up down""".split())


def tokens(title, keep_num=False):
    raw = re.findall(r"[가-힣]+|[A-Za-z][A-Za-z\-&\.]+|\d[\d\.,]*%?", title)
    out = []
    for w in raw:
        if re.match(r"[가-힣]", w) and len(w) > 2:
            w = PARTICLE_RE.sub("", w)
        w = w.lower()
        if re.match(r"\d", w):
            if keep_num and len(w) >= 2:  # '8%', '1,420원' 같은 수치는 같은 사건 판별에 유용
                out.append(w)
            continue
        if len(w) < 2 or (w.isascii() and len(w) < 3) or w in STOP:
            continue
        out.append(w)
    return out


def bigrams(title):
    s = re.sub(r"[^가-힣A-Za-z0-9]", "", title.lower())
    return {s[i:i + 2] for i in range(len(s) - 1)}


OPPOSITES = [("상승", "하락"), ("확대", "축소"), ("매수", "매도"), ("급등", "급락"), ("인상", "인하"), ("증가", "감소"),
             ("반등", "하락"), ("강세", "약세"), ("rise", "fall"), ("gain", "drop"), ("beat", "miss"),
             ("jump", "fall"), ("hike", "cut")]


def opposite(ta, tb):
    la, lb = ta.lower(), tb.lower()
    for x, y in OPPOSITES:
        if (x in la and y in lb and y not in la) or (y in la and x in lb and x not in la):
            return True
    return False


def similar(a, b, loose=False):
    if loose and not opposite(a["title"], b["title"]):  # 같은 기관 발표끼리는 느슨하게
        sh_ = len(a["tk"] & b["tk"])
        if sh_ >= 3 or (sh_ >= 2 and len(a["bg"] & b["bg"]) / max(1, len(a["bg"] | b["bg"])) >= 0.2):
            return True
    if opposite(a["title"], b["title"]):
        return False
    shared = len(a["tk"] & b["tk"])
    if a["title"].isascii() and b["title"].isascii():  # 영문은 글자쌍이 우연히 겹치기 쉬워 단어 기준만
        return shared >= 3 and shared / max(1, min(len(a["tk"]), len(b["tk"]))) >= 0.6
    ja = len(a["bg"] & b["bg"]) / max(1, len(a["bg"] | b["bg"]))
    if ja >= 0.38:
        return True
    if shared >= 3 and shared / max(1, min(len(a["tk"]), len(b["tk"]))) >= 0.5:
        return True
    if shared >= 2 and ja >= 0.3:
        return True
    small = a["bg"] if len(a["bg"]) <= len(b["bg"]) else b["bg"]
    if shared >= 2 and len(a["bg"] & b["bg"]) / max(1, len(small)) >= 0.6:
        return True
    return False


def cluster(items, loose=False):
    for it in items:
        it["bg"] = bigrams(it["title"])
        it["tk"] = set(tokens(it["title"], keep_num=True))
    items.sort(key=lambda x: -x["ts"])
    clusters = []
    by_group = {}
    for it in items:
        target = None
        if it.get("group") and it["group"] in by_group:
            target = by_group[it["group"]]
        else:
            for c in clusters:
                # 대표 기사 + 최근 합류 기사 몇 개와 비교
                if any(similar(it, m, loose) for m in c["members"][:3]):
                    target = c
                    break
        if target is None:
            target = {"members": []}
            clusters.append(target)
        target["members"].append(it)
        if it.get("group"):
            by_group[it["group"]] = target
    return clusters


# ─────────────────────────── 점수화 ───────────────────────────
def impact_hits(text):
    tl = text.lower()
    return [k for k in S.IMPACT_KEYWORDS if k.lower() in tl]


def score_cluster(c, now, half_life=6):
    mem = c["members"]
    # 같은 매체 중복 제거 (가장 최근 것)
    by_outlet = {}

    def better(a, b):  # 같은 매체 기사 중: 요약 있음 > 직접 링크 > 최신
        ka = (bool(a.get("summary")), "news.google.com" not in a["link"], a["ts"])
        kb = (bool(b.get("summary")), "news.google.com" not in b["link"], b["ts"])
        return ka > kb
    for m in mem:
        if m["outlet"] not in by_outlet or better(m, by_outlet[m["outlet"]]):
            by_outlet[m["outlet"]] = m
    uniq = sorted(by_outlet.values(), key=lambda m: -m["ts"])
    outlets = len(uniq)
    newest = max(m["ts"] for m in mem)
    first = min(m["ts"] for m in mem)
    age_h = max(0.0, (now - newest) / 3600)
    recent2h = len({m["outlet"] for m in mem if now - m["ts"] <= 7200})
    auth = max(S.OUTLETS.get(m["outlet"], 0.7) for m in mem)
    # 대표 기사: 한국어 > 권위 > 최신
    rep = sorted(uniq, key=lambda m: (m["lang"] != "ko", -S.OUTLETS.get(m["outlet"], .7), -m["ts"]))[0]
    hits = []
    for m in uniq[:5]:
        for h in impact_hits(m["title"]):
            if h not in hits:
                hits.append(h)
    is_top = any(m.get("top") for m in mem)

    coverage = 1 + 1.5 * math.log(1 + outlets)
    recency = 0.3 + 0.7 * math.exp(-age_h / half_life)
    impact = 1 + 0.12 * min(3, len(hits))
    score = coverage * recency * auth * impact * (1.15 if is_top else 1.0)

    reasons = [f"{outlets}개 매체 보도"]
    if recent2h >= 3:
        reasons.append(f"최근 2시간 {recent2h}곳")
    if is_top:
        reasons.append("포털 주요뉴스")
    if hits:
        reasons.append("키워드 " + ", ".join(hits[:2]))

    summary, summary_src = SM.best_rss_summary(uniq, lambda o: S.OUTLETS.get(o, 0.7))
    subs = Counter(m.get("_sub") for m in mem if m.get("_sub"))
    return dict(
        title=rep["title"], link=rep["link"], outlet=rep["outlet"], ts=int(newest), first_ts=int(first),
        summary=summary, summary_src=summary_src, outlets=[m["outlet"] for m in uniq], count=outlets,
        articles=[dict(title=m["title"], link=m["link"], outlet=m["outlet"], ts=int(m["ts"]), lang=m.get("lang", "ko"))
                  for m in uniq[:10]],
        score=round(score, 3), reasons=reasons, keywords=hits[:4],
        rising=recent2h >= 3 and (now - first) < 3 * 3600,
        sub=subs.most_common(1)[0][0] if subs else None,
        _bg=list(rep["bg"]),
    )


BIG_AMOUNT_RE = re.compile(r"\d+(?:\.\d+)?\s?조|\d,?\d{3}\s?억|[0-9]?천억|\$\s?\d+(?:\.\d+)?\s?(?:billion|bn)", re.I)


def _is_big(text):
    """조 단위·천억 단위 금액이나 '최대·역대·대어' 표현이 있으면 대형 딜."""
    return bool(BIG_AMOUNT_RE.search(text)) or _has_any(text, S.IB_BIG_WORDS)


def _has_any(text, words):
    tl = text.lower()
    return any(_has(w, tl) for w in words)  # 영문은 단어 경계로 ('SPAC' 이 'space' 에 걸리지 않게)


def specialist_boost(i):
    """딜사이트·인베스트조선·서울경제 시그널·이데일리 마켓in 보도면 최우선(×1.6), 그 밖의 IB 전문매체는 ×1.25. 대표 기사도 전문매체 기사로.
    (더벨은 유료회원 전용이라 본문을 볼 수 없어 수집하지 않음)"""
    top = [a for a in i["articles"] if a["outlet"] in S.TOP_SPECIALISTS]
    other = [a for a in i["articles"] if a["outlet"] in S.IB_SPECIALIST_OUTLETS]
    pick = (top or other or [None])[0]
    if not pick:
        return False
    i["score"] = round(i["score"] * (1.6 if top else 1.25), 3)
    i["reasons"].append(pick["outlet"])
    if i["outlet"] not in S.IB_SPECIALIST_OUTLETS:  # 제목·링크를 전문매체 기사로
        i["title"], i["link"], i["outlet"] = pick["title"], pick["link"], pick["outlet"]
    return True


FUNDING_GENERIC = {"투자", "유치", "시리즈", "펀딩", "스타트업", "벤처", "누적", "투자금", "기업가치", "인정", "투자사",
                   "벤처캐피탈", "vc", "바이오", "프리a", "시드", "브릿지", "신규", "후속", "참여", "주도", "라운드", "ai"}


def funding_adjust(issues):
    """비상장 투자 탭: 투자유치 기사만, 금액 큰 순·전문매체 우선, 같은 회사 중복 접기, 해외는 최대 3개."""
    out = []
    for i in issues:
        text = " ".join(a["title"] for a in i["articles"][:3])
        if not _has_any(text, S.FUNDING_WORDS + ["투자", "유치", "raise"]):
            continue
        if _is_big(text) or re.search(r"\d{3,}\s?억", text):
            i["score"] = round(i["score"] * 1.25, 3)
            i["reasons"].append("대규모 투자")
        m = re.search(r"시리즈\s?([A-E])", text)
        if m:
            i["reasons"].append(f"시리즈{m.group(1)}")
            if m.group(1) in "CDE":
                i["score"] = round(i["score"] * 1.1, 3)
        if not specialist_boost(i) and i["count"] < 2:
            i["score"] = round(i["score"] * 0.8, 3)
        out.append(i)
    out.sort(key=lambda x: -x["score"])
    out = fold_duplicates(out, S.IPO_GENERIC_TOKENS | FUNDING_GENERIC)
    dom, frn = [], []
    for i in out:
        (frn if is_foreign_ipo(i) else dom).append(i)
    return sorted(dom + frn[:3], key=lambda x: -x["score"])


def ipo_adjust(issues):
    """IPO 탭: 수요예측·청약·신고서 같은 일정성 기사는 빼고(논란·철회·제도 등 이슈성 예외),
    상장 전 대규모 펀딩·주관사·상장 추진/연기·몸값·제도 변화를 위로. IB 전문매체(딜사이트 등) 보도는 가점."""
    out = []
    for i in issues:
        text = " ".join(a["title"] for a in i["articles"][:3])
        if _has_any(text, S.IPO_ROUTINE_WORDS) and not _has_any(text, S.IPO_ISSUE_WORDS):
            continue
        ipo_terms = S.IPO_WORDS + [w for ws in S.IPO_PRIORITY.values() for w in ws] + ["상장", "listing", "go public"] + S.LEAGUE_WORDS
        if not _has_any(text, ipo_terms):
            continue  # IPO 와 무관한 기사(물가·회사채 등) 제외
        if _has_any(text, S.IB_SUB_WORDS["ecm"]) and not _has_any(text, ["IPO", "상장", "프리IPO"]):
            continue  # 일반 유상증자·CB 는 IB 탭 몫
        tags = [k for k, words in S.IPO_PRIORITY.items() if _has_any(text, words)]
        if tags:
            i["score"] = round(i["score"] * (1.15 + 0.05 * min(2, len(tags) - 1)), 3)
            i["reasons"].append(tags[0])
        if _is_big(text):
            i["score"] = round(i["score"] * 1.15, 3)
            i["reasons"].append("대규모")
        if not specialist_boost(i) and i["count"] < 2 and not tags:
            i["score"] = round(i["score"] * 0.7, 3)  # 일반매체 단독·유형 없는 기사는 아래로
        out.append(i)
    out.sort(key=lambda x: -x["score"])
    out = fold_duplicates(out, S.IPO_GENERIC_TOKENS)
    # 국내 90% 이상: 해외 이슈는 최대 IPO_FOREIGN_MAX 개
    dom, frn = [], []
    for i in out:
        (frn if is_foreign_ipo(i) else dom).append(i)
    for i in frn[: S.IPO_FOREIGN_MAX]:
        i["reasons"].append("해외")
    return sorted(dom + frn[: S.IPO_FOREIGN_MAX], key=lambda x: -x["score"])


def is_foreign_ipo(i):
    text = " ".join(a["title"] for a in i["articles"][:3])
    if all(a.get("lang") == "en" or re.fullmatch(r"[\x00-\x7F\s]+", a["title"] or "") for a in i["articles"][:3]):
        return True
    return _has_any(text, S.IPO_FOREIGN_MARKERS) and not _has_any(text, S.IPO_DOMESTIC_MARKERS)


ENTITY_ALIASES = {"오픈ai": "openai", "앤트로픽": "anthropic", "스페이스x": "spacex", "엔비디아": "nvidia",
                  "한투": "한국투자증권", "한국투자": "한국투자증권", "한화證": "한화투자증권"}


def _entity_tokens(title, generic):
    t = re.sub(r"[\[\]【】<>《》\"'“”‘’()]", " ", title)
    for k, v in (("오픈AI", " OpenAI "), ("오픈에이아이", " OpenAI "), ("앤트로픽", " Anthropic "), ("스페이스X", " SpaceX ")):
        t = t.replace(k, v)
    return {ENTITY_ALIASES.get(w, w) for w in tokens(t) if len(w) >= 2 and w not in generic}


TAB_ORDER = ("economy", "stocks", "realestate", "ipo", "ib", "funding", "crypto")
# 경제·주식·부동산에서 '같은 이슈' 판단 때 무시하는 흔한 단어
GENERAL_GENERIC = set("""환율 금리 기준금리 물가 코스피 코스닥 증시 외국인 기관 개인 순매수 순매도 아파트 서울 집값 전세 월세 매매
부동산 미국 연준 한국 정부 한은 대출 가계 수출 반도체 주가 지수 상승 하락 급등 급락 마감 출발 시장 투자자 정책 규제
대책 공급 분양 청약 거래 가격 경제 성장 경기 소비 고용 관세 무역 중국 일본 유럽 달러 원화 원달러 채권 국채 금값
유가 실적 영업이익 매출 전년 대비 전망 우려 기대 영향 발표 확대 축소 최고 최저 사상 역대 주간 이번주 지난주 오늘""".split())


def same_story(a, b):
    """두 이슈가 같은 사건인지 (제목 유사도 또는 고유 단어 2개 이상 공유)."""
    ta = {"title": a["title"], "bg": bigrams(a["title"]), "tk": set(tokens(a["title"], keep_num=True))}
    tb = {"title": b["title"], "bg": bigrams(b["title"]), "tk": set(tokens(b["title"], keep_num=True))}
    ja = len(ta["bg"] & tb["bg"]) / max(1, len(ta["bg"] | tb["bg"]))
    if ja >= 0.5 and not opposite(a["title"], b["title"]):
        return True
    ea = _entity_tokens(a["title"], S.IPO_GENERIC_TOKENS | GENERAL_GENERIC)
    eb = _entity_tokens(b["title"], S.IPO_GENERIC_TOKENS | GENERAL_GENERIC)
    return len(ea & eb) >= 2


def fold_duplicates(issues, generic, strict=False):
    """같은 회사·같은 딜을 다룬 이슈(예: 시리즈 기사 ①②③, 같은 회사 다른 각도)를 하나로 접고,
    접힌 기사들은 위 이슈의 '다른 보도' 목록으로 옮김."""
    toks = [_entity_tokens(i["title"], generic) for i in issues]
    df = Counter(w for ts in toks for w in ts)
    kept, kept_toks = [], []
    for i, ts in zip(issues, toks):
        rare = {w for w in ts if df[w] <= 8}
        host = None
        for k, kt in zip(kept, kept_toks):
            shared = rare & kt
            if not shared:
                continue
            if strict:
                # 경제·주식·부동산: 고유 단어 2개 이상 겹치거나, 1개 겹치고 제목도 꽤 비슷할 때만
                ja = len(bigrams(i["title"]) & bigrams(k["title"])) / max(1, len(bigrams(i["title"]) | bigrams(k["title"])))
                if len(shared) < 2 and ja < 0.2:
                    continue
            host = k
            break
        if host is None:
            kept.append(i)
            kept_toks.append(ts)
            continue
        seen = {a["link"] for a in host["articles"]}
        for a in i["articles"]:
            if a["link"] not in seen and len(host["articles"]) < 12:
                host["articles"].append(a)
        host["outlets"] = list(dict.fromkeys(host["outlets"] + i["outlets"]))
        host["count"] = len(host["outlets"])
        host["reasons"][0] = f"{host['count']}개 매체 보도"
    return kept


def ib_adjust(issues):
    """IB 탭 우선순위: 여러 매체가 다룬 대형 딜 > 여러 매체 딜 > 단독 보도(아래쪽, 개수 채우기용).
    딜(유증·블록딜·메자닌·M&A) 단어나 금액이 없는 기사, 노조·행사·공시 모음 같은 기사는 제외."""
    kept = []
    for i in issues:
        text = " ".join(a["title"] for a in i["articles"][:3])
        if _has_any(text, S.IB_NOISE_WORDS):
            continue
        deal_words = (S.IB_SUB_WORDS["ecm"] + S.IB_SUB_WORDS["mna"] + S.LEAGUE_WORDS
                      + ["인수", "매각", "자본확충", "자본조달", "지분", "증자", "인수금융"])
        if not (_has_any(text, deal_words) or BIG_AMOUNT_RE.search(text)):
            continue
        kept.append(i)
    issues = kept
    for i in issues:
        text = " ".join(a["title"] for a in i["articles"][:3])
        if _is_big(text):
            i["score"] = round(i["score"] * 1.25, 3)
            i["reasons"].append("대형 딜")
        if _has_any(text, S.LEAGUE_WORDS):
            i["score"] = round(i["score"] * 1.3, 3)
            i["reasons"].append("리그테이블")
        if not specialist_boost(i) and i["count"] < 2:
            i["score"] = round(i["score"] * 0.55, 3)  # 일반매체 단독 보도는 아래로
    issues.sort(key=lambda x: -x["score"])
    return fold_duplicates(issues, S.IPO_GENERIC_TOKENS | {"인수", "매각", "유상증자", "블록딜", "지분", "경영권", "m&a",
                                                           "우선협상대상자", "자본확충", "자본조달", "사모펀드", "pef"})


def top_keywords(issues, n=14):
    cnt = Counter()
    for iss in issues:
        for a in iss["articles"][:4]:
            for t in set(tokens(a["title"])):
                cnt[t] += iss["score"]
    generic = {"코스피", "부동산", "아파트", "증시", "주가", "금리", "stock", "market", "stocks", "markets"}
    out = []
    for w, v in cnt.most_common(60):
        if len(out) >= n:
            break
        if any(w != o and w in o for o, _ in out):
            continue
        out.append((w, round(v, 2)))
    # 너무 일반적인 단어는 뒤로
    out.sort(key=lambda x: (x[0] in generic, -x[1]))
    return out


def rank_changes(issues, prev):
    """직전 스냅샷 대비 순위 변화 (새 진입/상승/하락)."""
    prev_list = [(set(p.get("_bg", [])), p.get("rank", 99)) for p in prev or []]
    for i, iss in enumerate(issues):
        iss["rank"] = i + 1
        bg = set(iss["_bg"])
        best, best_rank = 0, None
        for pbg, prank in prev_list:
            j = len(bg & pbg) / max(1, len(bg | pbg))
            if j > best:
                best, best_rank = j, prank
        if best >= 0.4 and best_rank is not None:
            iss["delta"] = best_rank - iss["rank"]
            iss["is_new"] = False
        else:
            iss["delta"] = 0
            iss["is_new"] = bool(prev_list)


# ─────────────────────────── 시장지표 ───────────────────────────
def fetch_quote(entry):
    group, sym, name, digits, unit = entry
    last_err = None
    for host in ("query1", "query2"):
        url = (f"https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(sym)}"
               f"?range=1d&interval=5m&includePrePost=false")
        try:
            j = json.loads(http_get(url, timeout=8, fixture_key="q_" + re.sub(r"\W", "_", sym)))
            r = j["chart"]["result"][0]
            meta = r["meta"]
            closes = [c for c in (r.get("indicators", {}).get("quote", [{}])[0].get("close") or []) if c is not None]
            price = meta.get("regularMarketPrice") or (closes[-1] if closes else None)
            prev = meta.get("previousClose") or meta.get("chartPreviousClose")
            if price is None:
                raise ValueError("no price")
            scale = 0.1 if sym == "^TNX" and price > 20 else 1  # 일부 응답은 10배 표기
            if sym == "JPYKRW=X":
                scale = 100  # 원/100엔
            price *= scale
            prev = prev * scale if prev else None
            spark = [round(c * scale, 4) for c in closes]
            if len(spark) > 60:
                step = len(spark) / 60
                spark = [spark[int(i * step)] for i in range(60)] + [spark[-1]]
            chg = price - prev if prev else None
            return dict(sym=sym, name=name, group=group, digits=digits, unit=unit, price=round(price, 4),
                        prev=round(prev, 4) if prev else None, change=round(chg, 4) if chg is not None else None,
                        pct=round(chg / prev * 100, 3) if prev else None, spark=spark,
                        ts=meta.get("regularMarketTime"), tz=meta.get("exchangeTimezoneName"))
        except Exception as ex:  # noqa: BLE001
            last_err = ex
    return dict(sym=sym, name=name, group=group, digits=digits, unit=unit, error=str(last_err)[:80])


def fetch_markets():
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(fetch_quote, S.MARKETS))


# ─────────────────────────── 스냅샷 생성 ───────────────────────────
def build_snapshot(prev_snapshot=None):
    now = time.time()
    t0 = time.time()
    items, status = fetch_all_feeds(now)
    markets = fetch_markets()
    extras = X.collect_extras(http_get, fetch_quote)
    # 코스피·코스닥: 네이버 실시간 값으로 덮어쓰기 (Yahoo 지연 보완)
    rt = (extras.get("realtime") or {}).get("quotes") or {}
    for m in markets:
        q = rt.get(m["sym"])
        if q and q.get("price"):
            m["price"] = q["price"]
            if q.get("change") is not None:
                m["change"] = q["change"]
                m["prev"] = round(q["price"] - q["change"], 4)
            if q.get("pct") is not None:
                m["pct"] = q["pct"]
            m["spark"] = (m.get("spark") or [])[-60:] + [q["price"]]
            m["src"] = "naver"
    # 미 국채 10년: Yahoo 단위(×10 여부)를 FRED 공식값과 비교해 보정
    f10 = extras.get("fred10") or {}
    for m in markets:
        if m["sym"] == "^TNX" and m.get("price") and f10.get("value"):
            ref = f10["value"]
            for k in (1, 0.1, 10):
                if abs(m["price"] * k - ref) < 0.6:
                    if k != 1:
                        for fld in ("price", "prev", "change"):
                            if m.get(fld) is not None:
                                m[fld] = round(m[fld] * k, 4)
                        m["spark"] = [round(v * k, 4) for v in m.get("spark", [])]
                    m["check"] = f"FRED {f10.get('date')} {ref:.2f}% 대비 확인"
                    break
            else:
                m["check"] = f"FRED {ref:.2f}%와 차이 큼"

    # 링크 중복 제거 (같은 기사면 요약이 있는 언론사 직접 링크를 남김)
    items.sort(key=lambda it: ("news.google.com" in it["link"], not it.get("summary")))
    seen, uniq = set(), []
    for it in items:
        key = re.sub(r"([?&])(utm_[^=&]*|call_from|from|ref)=[^&#]*", r"\1", it["link"]).split("#")[0].rstrip("?&")
        k2 = (it["outlet"], re.sub(r"\W", "", it["title"])[:40])
        if key in seen or k2 in seen:
            continue
        seen.add(key)
        seen.add(k2)
        uniq.append(it)

    official, by_tab = [], defaultdict(list)
    for it in uniq:
        if it["official"]:
            official.append(it)
            continue
        tab, sub = classify(it)
        if tab and now - it["ts"] > max(TAB_MAX_AGE_H.get(tab, 36), it.get("max_age_h") or 0) * 3600:
            continue  # 경제·주식·부동산은 36시간, IPO·IB 는 72시간까지
        if tab:
            it["_sub"] = sub
            by_tab[tab].append(it)

    tabs = {}
    prev_tabs = (prev_snapshot or {}).get("tabs", {})
    ranked = {}
    for tab in TAB_ORDER:
        cl = cluster(by_tab.get(tab, []))
        issues = [score_cluster(c, now, TAB_HALF_LIFE.get(tab, 6)) for c in cl]
        # 시장 관련성 확인: 단독 보도는 관련 단어 2개 이상, 다수 보도는 1개 이상
        issues = [i for i in issues if not is_noise(i["title"]) and
                  relevance(" ".join(a["title"] for a in i["articles"][:3]), tab) >= (1 if i["count"] >= 2 else 2)]
        if tab == "ib":
            issues = ib_adjust(issues)
        elif tab == "ipo":
            issues = ipo_adjust(issues)
        elif tab == "funding":
            issues = funding_adjust(issues)
        issues.sort(key=lambda x: -x["score"])
        # 같은 회사·같은 사건 반복 기사 접기 (탭 안)
        issues = fold_duplicates(issues, S.IPO_GENERIC_TOKENS | GENERAL_GENERIC | FUNDING_GENERIC,
                                 strict=tab not in ("ipo", "ib", "funding"))
        ranked[tab] = issues

    # 탭 사이 중복: 더 구체적인 탭(IPO > IB > 부동산 > 주식 > 경제)에만 남김
    taken = []
    for tab in ("ipo", "ib", "funding", "crypto", "realestate", "stocks", "economy"):
        keep = []
        for iss in ranked[tab]:
            if any(same_story(iss, o) for o in taken):
                continue
            keep.append(iss)
        ranked[tab] = keep
        taken += keep[: TAB_LIMIT.get(tab, TOP_N)]

    for tab in TAB_ORDER:
        issues = ranked[tab][:TAB_LIMIT.get(tab, TOP_N)]
        rank_changes(issues, prev_tabs.get(tab, {}).get("issues"))
        mx = issues[0]["score"] if issues else 1
        for iss in issues:
            iss["heat"] = round(100 * iss["score"] / mx)
        tabs[tab] = dict(issues=issues, keywords=top_keywords(issues), total_articles=len(by_tab.get(tab, [])))

    # 같은 발표를 여러 매체가 보도한 경우 하나로 묶기
    policy = []
    for c in cluster(official, loose=True):
        mem = sorted(c["members"], key=lambda m: (-S.OUTLETS.get(m["summary"].replace(" 보도", ""), 0.7), -m["ts"]))
        rep = mem[0]
        media = sorted({m["summary"].replace(" 보도", "") for m in mem if m["summary"].endswith(" 보도")})
        if len(media) > 1:
            summary = f"{', '.join(media[:3])}{' 외' if len(media) > 3 else ''} 보도 ({len(media)}곳)"
        elif media:
            summary = f"{media[0]} 보도"
        else:
            summary = rep.get("summary", "")
            if summary[:30].replace(" ", "") == rep["title"][:30].replace(" ", ""):
                summary = ""  # 제목과 같은 요약은 생략
        policy.append(dict(title=rep["title"], link=rep["link"], outlet=rep["outlet"],
                           ts=int(max(m["ts"] for m in mem)), summary=summary))
    policy.sort(key=lambda x: -x["ts"])
    policy = policy[:60]

    # 요약이 없거나 짧은 기사는 원문 앞부분으로 보강 (모든 탭 + 정책·발표)
    try:
        SM.enrich(tabs, http_get, SUM_CACHE, policy=policy, outlet_weight=lambda o: S.OUTLETS.get(o, 0.7))
    except Exception as ex:  # noqa: BLE001
        log("요약 보강 실패:", repr(ex))

    snap = dict(
        version=1,
        generated_at=int(now),
        generated_kst=dt.datetime.fromtimestamp(now, KST).strftime("%Y-%m-%d %H:%M:%S"),
        took_ms=int((time.time() - t0) * 1000),
        markets=markets, tabs=tabs, policy=policy, sources=status, extras=extras,
        method=("이슈 점수 = 보도 매체 수(로그) × 최신성(6시간 반감) × 출처 권위 × 시장영향 키워드 가점"
                " × 포털 주요뉴스 가점. 화이트리스트 매체만 집계, 같은 매체 중복 보도는 1회로 계산."),
    )
    return snap


def stamp_of(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y%m%d-%H%M")


# ─────────────────────────── 업로드(data 브랜치) ───────────────────────────
def sh(cmd, cwd, check=True):
    r = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{cmd}\n{r.stdout}\n{r.stderr}")
    return r.stdout.strip()


class DataBranch:
    """`data` 브랜치를 항상 커밋 1개로 유지하며 강제 푸시 (저장소가 커지지 않음).
    timeline/ (하루 흐름), state/ (알림 기록) 은 실행이 바뀌어도 이어서 보관."""

    def __init__(self, repo_dir, workdir):
        self.dir = workdir
        if os.path.exists(workdir):
            shutil.rmtree(workdir)
        os.makedirs(os.path.join(workdir, "snap"))
        token, slug = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
        if token and slug:  # GitHub Actions 안에서는 토큰으로 직접 푸시
            remote = f"https://x-access-token:{token}@github.com/{slug}.git"
        else:
            remote = sh("git config --get remote.origin.url", repo_dir)
        sh("git init -q -b data", workdir)
        sh(f"git remote add origin {remote}", workdir)
        # actions/checkout 이 저장한 인증 헤더 복사
        for line in sh("git config --get-regexp '^http\\..*extraheader' || true", repo_dir, check=False).splitlines():
            k, v = line.split(" ", 1)
            sh(f"git config {k} '{v}'", workdir)
        sh("git config user.name 'market-pulse-bot'", workdir)
        sh("git config user.email 'market-pulse-bot@users.noreply.github.com'", workdir)
        self.first = True

    def restore(self):
        """이전 data 브랜치에서 직전 스냅샷을 읽고 timeline/state 폴더를 가져옴."""
        try:
            sh("git fetch -q --depth 1 origin data", self.dir)
        except Exception:  # noqa: BLE001
            return None
        for d in ("timeline", "state"):
            sh(f"git checkout FETCH_HEAD -- {d} 2>/dev/null || true", self.dir, check=False)
        try:
            return json.loads(sh("git show FETCH_HEAD:latest.json", self.dir))
        except Exception:  # noqa: BLE001
            return None

    def publish(self, snap):
        st = write_snapshot(self.dir, snap, keep=20, pretty=False)
        sh("git add -A", self.dir)
        amend = "" if self.first else "--amend"
        sh(f"git commit -q {amend} -m 'snapshot {st}'", self.dir)
        self.first = False
        sh("git push -q -f origin HEAD:data", self.dir)
        return st


def write_snapshot(store, snap, keep=20, pretty=False):
    st = stamp_of(snap["generated_at"])
    snap["stamp"] = st
    body = json.dumps(snap, ensure_ascii=False, **({"indent": 1} if pretty else {"separators": (",", ":")}))
    os.makedirs(os.path.join(store, "snap"), exist_ok=True)
    with open(os.path.join(store, "snap", st + ".json"), "w", encoding="utf-8") as f:
        f.write(body)
    with open(os.path.join(store, "latest.json"), "w", encoding="utf-8") as f:
        f.write(body)
    snaps = sorted(os.listdir(os.path.join(store, "snap")))
    for old in snaps[:-keep]:
        os.remove(os.path.join(store, "snap", old))
    # 실패한 추가 지표의 응답 일부 (원인 확인용)
    ddir = os.path.join(store, "debug")
    os.makedirs(ddir, exist_ok=True)
    for fn in os.listdir(ddir):
        if fn[:-4] not in X.DEBUG:
            os.remove(os.path.join(ddir, fn))
    for name, text in X.DEBUG.items():
        with open(os.path.join(ddir, name + ".txt"), "w", encoding="utf-8") as f:
            f.write(text)
    return st


# ─────────────────────────── 하루 타임라인 ───────────────────────────
TIMELINE_DAYS = 30


def update_timeline(store, snap):
    """10분마다 탭별 상위 3개 이슈를 timeline/YYYY-MM-DD.json 에 기록."""
    now = dt.datetime.fromtimestamp(snap["generated_at"], KST)
    slot = now.replace(minute=now.minute - now.minute % 10, second=0).strftime("%H:%M")
    date = now.strftime("%Y-%m-%d")
    tdir = os.path.join(store, "timeline")
    os.makedirs(tdir, exist_ok=True)
    path = os.path.join(tdir, date + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            day = json.load(f)
    except Exception:  # noqa: BLE001
        day = {"date": date, "slots": []}
    if day["slots"] and day["slots"][-1]["t"] == slot:
        return False
    entry = {"t": slot, "tabs": {}}
    for tab in ("economy", "stocks", "realestate", "ipo", "ib", "funding", "crypto"):
        entry["tabs"][tab] = [dict(title=i["title"], link=i["link"], outlet=i["outlet"], count=i["count"])
                              for i in snap["tabs"].get(tab, {}).get("issues", [])[:3]]
    q = {m["sym"]: m for m in snap["markets"]}
    entry["mkt"] = {k: q[s].get("price") for k, s in (("kospi", "^KS11"), ("kosdaq", "^KQ11"), ("usdkrw", "KRW=X"))
                    if s in q}
    day["slots"].append(entry)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(day, f, ensure_ascii=False, separators=(",", ":"))
    files = sorted(fn[:-5] for fn in os.listdir(tdir) if re.match(r"\d{4}-\d{2}-\d{2}\.json$", fn))
    for old in files[:-TIMELINE_DAYS]:
        os.remove(os.path.join(tdir, old + ".json"))
    with open(os.path.join(tdir, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"dates": files[-TIMELINE_DAYS:][::-1]}, f)
    return True


def load_state(store, name):
    try:
        with open(os.path.join(store, "state", name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def save_state(store, name, data):
    os.makedirs(os.path.join(store, "state"), exist_ok=True)
    with open(os.path.join(store, "state", name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def interval_now():
    n = dt.datetime.now(KST)
    m = n.hour * 60 + n.minute
    if n.weekday() < 5 and 9 * 60 - 5 <= m <= 15 * 60 + 40:
        return 30   # 국내 장중은 30초 간격
    return 60 if 6 <= n.hour < 24 else 300  # 심야(0~6시)는 5분 간격


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--loop-minutes", type=float, default=55)
    ap.add_argument("--out", default="out")
    a = ap.parse_args()

    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db = None if a.no_push else DataBranch(repo_dir, os.path.join(repo_dir, "..", "_market_pulse_data"))
    store = db.dir if db else a.out
    os.makedirs(store, exist_ok=True)
    prev = db.restore() if db else None
    SUM_CACHE.update(load_state(store, "summaries.json"))
    deadline = time.time() + a.loop_minutes * 60

    while True:
        try:
            snap = build_snapshot(prev)
            n_ok = sum(1 for s in snap["sources"] if s["ok"])
            # 키워드 알림·타임라인: 실패해도 데이터 업로드는 계속
            try:
                astate, astatus = A.process(snap, load_state(store, "alerts.json"), repo_dir, log)
                save_state(store, "alerts.json", astate)
            except Exception as ex:  # noqa: BLE001
                astatus = {"enabled": False, "error": f"알림 처리 오류: {type(ex).__name__}"}
            snap["alerts"] = astatus
            try:  # 사이트 불편사항 → 텔레그램
                fstate, fstatus = FB.process(load_state(store, "feedback.json"), log)
                save_state(store, "feedback.json", fstate)
                if fstatus.get("error"):
                    log(f"   ✗ 불편사항: {fstatus['error']}")
            except Exception as ex:  # noqa: BLE001
                log("불편사항 처리 오류:", repr(ex))
            save_state(store, "summaries.json", SUM_CACHE)
            try:
                update_timeline(store, snap)
            except Exception as ex:  # noqa: BLE001
                log("타임라인 기록 실패:", repr(ex))
            st = db.publish(snap) if db else write_snapshot(store, snap, pretty=True)
            ex = snap["extras"]
            log(f"[{st}] 소스 {n_ok}/{len(snap['sources'])} 성공 · 이슈 "
                + " / ".join(f"{k}:{len(v['issues'])}" for k, v in snap["tabs"].items())
                + " · 추가지표 " + " ".join(f"{k}:{'O' if v.get('ok') else 'X'}" for k, v in ex.items())
                + f" · {snap['took_ms']}ms")
            for s in snap["sources"]:
                if not s["ok"]:
                    log(f"   ✗ {s['label']}: {s['error']}")
            for k, v in ex.items():
                if not v.get("ok") and not v.get("missing_key"):
                    log(f"   ✗ 추가지표 {k}: {v.get('error')}")
            if astatus.get("error"):
                log(f"   ✗ 텔레그램: {astatus['error']}")
            prev = snap
        except Exception as ex:  # noqa: BLE001
            log("수집 실패:", repr(ex))
        if a.once:
            break
        # 다음 수집은 '정각 분'(심야엔 5분 단위)에 시작 → 화면은 매분 40초에 확인
        step = interval_now()
        nxt = (int(time.time() // step) + 1) * step
        if nxt > deadline:
            break
        time.sleep(max(1, nxt - time.time()))


if __name__ == "__main__":
    main()
