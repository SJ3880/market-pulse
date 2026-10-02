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
ENRICH_TOP = 30          # 탭별 몇 위까지 원문 보강 (사실상 전부)
MAX_FETCH_PER_RUN = 14   # 1분에 새로 여는 원문 수 상한
MAX_DECODE_PER_RUN = 10  # 1분에 새로 푸는 Google 링크 수 상한 (과도한 요청 방지)
CACHE_MAX = 3000

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[ \t\r\f\v]+")
# "(서울=연합뉴스) 홍길동 기자 = " / "[이데일리 홍길동 기자] " / "아시아경제 홍길동 기자" 같은 머리말
BYLINE_RE = re.compile(
    r"^\s*(\([^)]{0,30}=[^)]{0,30}\)\s*[^=\n]{0,30}?(기자|특파원|에디터)?\s*=\s*"
    r"|\[[^\]]{0,30}(기자|특파원)\]\s*"
    r"|[가-힣A-Za-z]{2,10}\s+[가-힣]{2,4}\s*(기자|특파원)\s*[=:]?\s*)")
# 유료벽·로그인·점검 안내 같은 '본문 아님' 문구 → 요약으로 쓰지 않음
PAYWALL_RE = re.compile(r"(유료\s*회원|유료회원\s*전용|회원\s*전용|로그인\s*(해\s*주세요|후\s*(이용|확인))|결제\s*후\s*확인|"
                        r"구독자\s*전용|프리미엄\s*기사|서버\s*점검|subscribe to (read|continue)|subscribers only|paywall)", re.I)
JUNK_RE = re.compile(r"(무단\s*전재|재배포\s*금지|Copyright|ⓒ|©|저작권자|기사제보|구독\s*신청|[\w.]+@[\w.]+\.\w+)", re.I)


def _plain(s):
    s = html.unescape(TAG_RE.sub(" ", html.unescape(s or "")))
    s = re.sub(r"<[^>]*$", " ", s)  # 잘린 태그(예: '<img src=')
    return WS_RE.sub(" ", s).strip()


def usable(text):
    """요약으로 쓸 만한 글인지 (유료 안내·태그 찌꺼기·너무 짧은 글 제외)."""
    t = (text or "").strip()
    return len(re.sub(r"[^가-힣A-Za-z]", "", t)) >= 30 and not PAYWALL_RE.search(t)


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
        if len(s) >= 40 and not JUNK_RE.search(s) and not PAYWALL_RE.search(s):
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
    out = to_paragraphs(text)
    return out if usable(out) else ""


def best_rss_summary(members, outlet_weight):
    """클러스터 기사들의 RSS 요약 중 가장 쓸 만한 것 (권위 → 길이)."""
    cands = [m for m in members if len(clean_summary(m.get("summary", ""))) >= 40 and usable(clean_summary(m["summary"]))]
    if not cands:
        return "", None
    m = max(cands, key=lambda m: (outlet_weight(m["outlet"]) >= 0.95, len(m["summary"][:400]), outlet_weight(m["outlet"])))
    return to_paragraphs(m["summary"]), {"outlet": m["outlet"], "link": m["link"], "title": m["title"]}


# ─────────────────────────── Google 뉴스 링크 → 원문 주소 ───────────────────────────
# Google 뉴스 RSS 링크는 실제 기사 주소를 감춰 둠. 뉴스 화면이 쓰는 방식 그대로 원문 주소를 받아 옴.
GN_STATE = {"fail": 0, "pause_until": 0}
GN_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
         "Chrome/126.0 Safari/537.36")


def decode_gnews(link, timeout=8):
    import json as _json
    import urllib.parse
    import urllib.request
    m = re.search(r"/articles/([^?/#]+)", link)
    if not m:
        return None
    aid = m.group(1)
    req = urllib.request.Request(f"https://news.google.com/rss/articles/{aid}?hl=ko&gl=KR&ceid=KR:ko",
                                 headers={"User-Agent": GN_UA, "Accept-Language": "ko"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        page = r.read().decode("utf-8", "replace")
    sig = re.search(r'data-n-a-sg="([^"]+)"', page)
    ts = re.search(r'data-n-a-ts="([^"]+)"', page)
    if not (sig and ts):
        return None
    inner = _json.dumps(["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "KR:ko", None, 1, None, None, None,
                                          None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
                         aid, int(ts.group(1)), sig.group(1)])
    body = "f.req=" + urllib.parse.quote(_json.dumps([[["Fbv4je", inner, None, "generic"]]]))
    req = urllib.request.Request("https://news.google.com/_/DotsSplashUi/data/batchexecute", data=body.encode(),
                                 headers={"User-Agent": GN_UA,
                                          "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        txt = r.read().decode("utf-8", "replace")
    m = re.search(r'garturlres\\",\\"(https?://[^"\\]+)', txt)
    return m.group(1) if m else None


def _resolve(a, cache):
    """기사 원문 주소 (Google 링크면 풀어서). 실패가 이어지면 30분 쉼."""
    link = a["link"]
    if "news.google.com" not in link:
        return link
    key = "url:" + link
    if key in cache:
        return cache[key][1] or None
    if time.time() < GN_STATE["pause_until"]:
        return None
    try:
        url = decode_gnews(link)
        GN_STATE["fail"] = 0
    except Exception:  # noqa: BLE001
        url = None
        GN_STATE["fail"] += 1
        if GN_STATE["fail"] >= 3:
            GN_STATE["pause_until"] = time.time() + 1800
            GN_STATE["fail"] = 0
        return None
    cache[key] = [int(time.time()), url or ""]
    return url


def enrich(tabs, http_get, cache, policy=None, log=print, outlet_weight=lambda o: 0.8):
    """요약이 부족한 이슈를 원문 앞부분으로 보강 (모든 탭·정책 발표).
    cache: {원문링크: [시각, 요약], 'url:'+구글링크: [시각, 원문주소]}"""
    targets = []
    for tab in ("ipo", "ib", "funding", "crypto", "economy", "stocks", "realestate"):
        targets += [("issue", iss) for iss in tabs.get(tab, {}).get("issues", [])[:ENRICH_TOP]]
    targets += [("policy", p) for p in (policy or [])[:20]]

    todo, decodes = [], 0
    for kind, iss in targets:
        have = iss.get("summary", "") if kind == "issue" else iss.get("lede", "")
        if not usable(have):  # 유료 안내문 등은 지우고 다시 찾기
            have = ""
            if kind == "issue":
                iss["summary"], iss["summary_src"] = "", None
        if len(have) >= 150:
            continue
        arts = iss.get("articles") or [{"title": iss["title"], "link": iss["link"], "outlet": iss.get("media") or iss["outlet"]}]
        # 직접 링크 우선, 그다음 권위 높은 매체
        arts = sorted(arts, key=lambda a: ("news.google.com" in a["link"], -outlet_weight(a["outlet"])))[:3]
        picked = None
        for a in arts:
            if "news.google.com" in a["link"] and ("url:" + a["link"]) not in cache:
                if decodes >= MAX_DECODE_PER_RUN:
                    continue
                decodes += 1
            url = _resolve(a, cache)
            if not url:
                continue
            hit = cache.get(url)
            if hit is not None:
                if hit[1] and usable(hit[1]):
                    _apply(kind, iss, hit[1], a, url)
                    picked = "done"
                    break
                continue  # 예전에 실패한 원문
            picked = (a, url)
            break
        if picked and picked != "done":
            todo.append((kind, iss, picked[0], picked[1]))
    todo = todo[:MAX_FETCH_PER_RUN]

    def work(job):
        kind, iss, a, url = job
        try:
            return job, extract_lede(http_get(url, timeout=6, fixture_key="art_" + hashlib.md5(url.encode()).hexdigest()[:12]))
        except Exception:  # noqa: BLE001
            return job, ""

    if todo:
        with ThreadPoolExecutor(max_workers=6) as ex:
            for (kind, iss, a, url), text in ex.map(work, todo):
                cache[url] = [int(time.time()), text]
                if text:
                    _apply(kind, iss, text, a, url)
    if len(cache) > CACHE_MAX:
        for k in sorted(cache, key=lambda k: cache[k][0])[: len(cache) - CACHE_MAX]:
            cache.pop(k, None)
    return len(todo)


def _apply(kind, iss, text, a, url):
    if not usable(text):
        return
    src = {"outlet": a["outlet"], "link": url, "title": a["title"]}
    if kind == "issue":
        if len(text) > len(iss.get("summary", "")):
            iss["summary"], iss["summary_src"] = text, src
    else:
        iss["lede"], iss["lede_src"] = text, src
