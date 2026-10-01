# -*- coding: utf-8 -*-
"""
이슈 '빠른 보기' 요약
 1) 언론사 RSS 가 주는 기사 앞부분(요약)을 우선 사용
 2) 요약이 짧거나 없으면, 원문 기사(언론사 직접 링크)를 열어 첫 1~2문단을 가져옴 (상위 이슈만, 결과는 저장해 재사용)
 * Google 뉴스 링크는 실제 기사 주소가 숨겨져 있어 원문을 열 수 없음 → 다른 매체의 직접 링크가 있을 때만 보강
"""
import hashlib
import html
import re
import time
from concurrent.futures import ThreadPoolExecutor

MAX_CHARS = 460          # 요약 최대 길이(약 2문단)
ENRICH_TOP = 12          # 탭별 상위 몇 개 이슈까지 원문 보강
MAX_FETCH_PER_RUN = 10   # 1분에 새로 여는 원문 수 상한
CACHE_MAX = 800

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[ \t\r\f\v]+")
# "(서울=연합뉴스) 홍길동 기자 = " / "[이데일리 홍길동 기자] " / "아시아경제 홍길동 기자" 같은 머리말
BYLINE_RE = re.compile(
    r"^\s*(\([^)]{0,30}=[^)]{0,30}\)\s*[^=\n]{0,30}?(기자|특파원|에디터)?\s*=\s*"
    r"|\[[^\]]{0,30}(기자|특파원)\]\s*"
    r"|[가-힣A-Za-z]{2,10}\s+[가-힣]{2,4}\s*(기자|특파원)\s*[=:]?\s*)")
JUNK_RE = re.compile(r"(무단\s*전재|재배포\s*금지|Copyright|ⓒ|©|저작권자|기사제보|구독\s*신청|[\w.]+@[\w.]+\.\w+)", re.I)


def _plain(s):
    s = html.unescape(TAG_RE.sub(" ", html.unescape(s or "")))
    return WS_RE.sub(" ", s).strip()


def clean_summary(text):
    t = _plain(text)
    t = BYLINE_RE.sub("", t, count=1)
    t = re.sub(r"\s*(…|\.\.\.)\s*$", "…", t)
    return t.strip()


def to_paragraphs(text, max_chars=MAX_CHARS):
    """문장 단위로 잘라 최대 2문단으로."""
    t = clean_summary(text)
    if not t:
        return ""
    sents = re.split(r"(?<=[다요음함됨\.\?!])\s+(?=[^\s])", t)
    out, total = [], 0
    for s in sents:
        if total + len(s) > max_chars and out:
            break
        out.append(s)
        total += len(s) + 1
    if len(out) == 1 and len(out[0]) > max_chars:
        out[0] = out[0][:max_chars].rsplit(" ", 1)[0] + "…"
    if len(out) >= 3:
        mid = (len(out) + 1) // 2
        return " ".join(out[:mid]) + "\n\n" + " ".join(out[mid:])
    return " ".join(out)


def _decode(raw):
    m = re.search(rb"charset=[\"']?([\w\-]+)", raw[:3000], re.I)
    enc = (m.group(1).decode().lower() if m else "utf-8")
    if enc in ("euc-kr", "ks_c_5601-1987", "ksc5601"):
        enc = "cp949"
    try:
        return raw.decode(enc)
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


def extract_lede(raw):
    """기사 HTML 에서 앞부분 1~3문단 추출 (없으면 og:description)."""
    txt = _decode(raw)
    og = ""
    for pat in (r'<meta[^>]+property=["\']og:description["\'][^>]*content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']og:description',
                r'<meta[^>]+name=["\']description["\'][^>]*content=["\']([^"\']+)'):
        m = re.search(pat, txt, re.I)
        if m:
            og = _plain(m.group(1))
            break
    body = re.sub(r"<(script|style|noscript|figure|figcaption|aside|nav|header|footer)[^>]*>.*?</\1>", " ",
                  txt, flags=re.S | re.I)
    paras = []
    # 1) <p> 문단  2) <br> 로 나뉜 본문(일부 국내 매체)
    for p in re.findall(r"<p[^>]*>(.*?)</p>", body, re.S | re.I):
        s = _plain(p)
        if len(s) >= 40 and not JUNK_RE.search(s):
            paras.append(s)
        if sum(map(len, paras)) > MAX_CHARS * 1.3:
            break
    if sum(map(len, paras)) < 120:
        m = re.search(r'(id|class)=["\'][^"\']*(article[_-]?(body|view|content|txt)|news[_-]?(cnt|content|body)|'
                      r'story-news|art_txt|view_con)[^"\']*["\'][^>]*>(.*?)</(div|section|article)>', body, re.S | re.I)
        if m:
            chunk = re.sub(r"<br\s*/?>", "\n", m.group(5), flags=re.I)
            paras = [s for s in (_plain(x) for x in chunk.split("\n")) if len(s) >= 40 and not JUNK_RE.search(s)]
    text = " ".join(paras)
    if len(text) < max(120, len(og)):
        text = og
    return to_paragraphs(text)


def best_rss_summary(members, outlet_weight):
    """클러스터 기사들의 RSS 요약 중 가장 쓸 만한 것 (권위 → 길이)."""
    cands = [m for m in members if len(clean_summary(m.get("summary", ""))) >= 40]
    if not cands:
        return "", None
    m = max(cands, key=lambda m: (outlet_weight(m["outlet"]) >= 0.95, len(m["summary"][:400]), outlet_weight(m["outlet"])))
    return to_paragraphs(m["summary"]), {"outlet": m["outlet"], "link": m["link"], "title": m["title"]}


def enrich(tabs, http_get, cache, log=print):
    """요약이 부족한 상위 이슈를 원문 앞부분으로 보강. cache: {link: [시각, 요약]}"""
    todo = []
    for tab in ("economy", "stocks", "realestate", "ipo", "ib"):
        for iss in tabs.get(tab, {}).get("issues", [])[:ENRICH_TOP]:
            if len(iss.get("summary", "")) >= 150:
                continue
            direct = [a for a in iss["articles"] if "news.google.com" not in a["link"]]
            if not direct:
                continue
            a = direct[0]
            hit = cache.get(a["link"])
            if hit is not None:
                if hit[1]:
                    iss["summary"], iss["summary_src"] = hit[1], {"outlet": a["outlet"], "link": a["link"], "title": a["title"]}
                continue
            todo.append((iss, a))
    todo = todo[:MAX_FETCH_PER_RUN]

    def work(pair):
        iss, a = pair
        try:
            return iss, a, extract_lede(http_get(a["link"], timeout=6, fixture_key="art_" + hashlib.md5(a["link"].encode()).hexdigest()[:12]))
        except Exception:  # noqa: BLE001
            return iss, a, ""

    if todo:
        with ThreadPoolExecutor(max_workers=6) as ex:
            for iss, a, text in ex.map(work, todo):
                cache[a["link"]] = [int(time.time()), text]
                if text and len(text) > len(iss.get("summary", "")):
                    iss["summary"], iss["summary_src"] = text, {"outlet": a["outlet"], "link": a["link"], "title": a["title"]}
    # 오래된 저장분 정리
    if len(cache) > CACHE_MAX:
        for k in sorted(cache, key=lambda k: cache[k][0])[: len(cache) - CACHE_MAX]:
            cache.pop(k, None)
    return len(todo)
