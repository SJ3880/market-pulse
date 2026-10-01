# -*- coding: utf-8 -*-
"""
키워드 알림 (텔레그램)
 - 저장소 맨 위의 alerts.txt 에 적은 키워드가 제목에 들어간 이슈가 상위권에 오르면 텔레그램으로 보냅니다.
 - 같은 이슈는 7일 동안 다시 보내지 않습니다.
 - GitHub Secrets 에 TELEGRAM_TOKEN, TELEGRAM_CHAT_ID 가 있어야 작동합니다.
"""
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request

TAB_NAMES = {"economy": "경제", "stocks": "주식", "realestate": "부동산", "policy": "정책·발표"}
KEEP_SEC = 7 * 24 * 3600  # 같은 이슈는 7일간 다시 안 보냄
TOP_N = 15  # 탭별 상위 몇 위까지 볼지


def load_keywords(repo_dir):
    path = os.path.join(repo_dir, "alerts.txt")
    kws, rising = [], False
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                w = line.split("#", 1)[0].strip()
                if not w:
                    continue
                if w == "@급부상":
                    rising = True
                else:
                    kws.append(w)
    except FileNotFoundError:
        pass
    return kws, rising


def _bg(t):
    s = re.sub(r"[^가-힣A-Za-z0-9]", "", t.lower())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _seen(state, title):
    bg = _bg(title)
    for s in state.get("sent", []):
        sb = set(s["bg"])
        if len(bg & sb) / max(1, len(bg | sb)) >= 0.5:
            return True
    return False


def send_telegram(token, chat_id, text):
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def process(snap, state, repo_dir, log=print):
    """state 는 data 브랜치에 저장되는 dict. 바뀐 state 와 상태 요약을 돌려줌."""
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    kws, rising = load_keywords(repo_dir)
    status = {"enabled": bool(token and chat), "keywords": kws, "rising": rising}
    if not (token and chat) or not (kws or rising):
        return state, status

    now = time.time()
    state = dict(state or {})
    state["sent"] = [s for s in state.get("sent", []) if now - s["t"] < KEEP_SEC][-800:]
    first_time = not state.get("initialized")

    cands = []
    for tab in ("economy", "stocks", "realestate"):
        for iss in snap["tabs"].get(tab, {}).get("issues", [])[:TOP_N]:
            cands.append((tab, iss))
    for p in snap.get("policy", [])[:20]:
        cands.append(("policy", dict(p, count=None, rank=None, reasons=[])))

    hits = []
    for tab, iss in cands:
        title = iss["title"]
        matched = [k for k in kws if k.lower() in title.lower()]
        if not matched and rising and iss.get("rising"):
            matched = ["급부상"]
        if not matched or _seen(state, title):
            continue
        hits.append((tab, iss, matched))
        state["sent"].append({"t": now, "bg": sorted(_bg(title))})

    if first_time:
        state["initialized"] = True
        msg = ("✅ <b>마켓 펄스 알림 연결 완료</b>\n지켜보는 키워드: "
               + html.escape(", ".join(kws) or "없음") + ("\n급부상 이슈도 알려드려요." if rising else "")
               + f"\n지금 이미 올라와 있는 관련 이슈 {len(hits)}건은 건너뛰고, 이제부터 새로 오르는 것만 보냅니다.")
        try:
            send_telegram(token, chat, msg)
            status["last_sent"] = int(now)
        except Exception as ex:  # noqa: BLE001
            status["error"] = f"{type(ex).__name__}: {str(ex)[:120]}"
            state["initialized"] = False
            state["sent"] = state["sent"][: len(state["sent"]) - len(hits)]
        return state, status

    if not hits:
        return state, status
    lines = []
    for tab, iss, matched in hits[:8]:
        meta = [TAB_NAMES.get(tab, tab)]
        if iss.get("rank"):
            meta.append(f"{iss['rank']}위")
        if iss.get("count"):
            meta.append(f"{iss['count']}개 매체")
        meta.append(iss.get("outlet", ""))
        lines.append(f"🔔 <b>[{html.escape(', '.join(matched))}]</b> <a href=\"{html.escape(iss['link'])}\">"
                     f"{html.escape(iss['title'])}</a>\n<i>{html.escape(' · '.join(m for m in meta if m))}</i>")
    if len(hits) > 8:
        lines.append(f"…외 {len(hits) - 8}건")
    try:
        send_telegram(token, chat, "\n\n".join(lines))
        status["last_sent"] = int(now)
        status["sent_count"] = len(hits)
        log(f"   텔레그램 알림 {len(hits)}건 전송")
    except Exception as ex:  # noqa: BLE001
        status["error"] = f"{type(ex).__name__}: {str(ex)[:120]}"
        # 전송 실패 시 다음 분에 다시 시도하도록 기록 취소
        state["sent"] = state["sent"][: len(state["sent"]) - len(hits)]
    return state, status
