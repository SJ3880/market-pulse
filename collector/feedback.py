# -*- coding: utf-8 -*-
"""
불편사항 → 텔레그램 전달
 - 사이트 입력창의 글은 관리자의 구글 설문 응답 시트에 저장됨 (이용자에게는 구글 화면이 안 보임)
 - 수집기가 2분마다 그 시트(웹에 게시한 CSV, 주소는 GitHub Secrets 의 FEEDBACK_CSV_URL)를 읽어
   새 글만 텔레그램 알림 방으로 보냄. 봇 토큰은 사이트에 노출되지 않음.
"""
import csv
import html
import io
import os
import time
import urllib.request

from alerts import send_telegram

CHECK_EVERY = 120  # 초
_last_check = [0.0]


def _fetch_rows(url):
    req = urllib.request.Request(url + ("&" if "?" in url else "?") + f"t={int(time.time())}",
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        text = r.read().decode("utf-8-sig", "replace")
    rows = list(csv.reader(io.StringIO(text)))
    return rows[0] if rows else [], rows[1:]


def process(state, log=print):
    url = os.environ.get("FEEDBACK_CSV_URL", "").strip()
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    status = {"enabled": bool(url and token and chat)}
    if not status["enabled"]:
        return state, status
    if time.time() - _last_check[0] < CHECK_EVERY:
        return state, dict(status, **(state or {}).get("status", {}))
    _last_check[0] = time.time()
    state = dict(state or {})
    try:
        header, rows = _fetch_rows(url)
    except Exception as ex:  # noqa: BLE001
        status["error"] = f"접수 시트 읽기 실패: {type(ex).__name__}"
        state["status"] = status
        return state, status
    rows = [r for r in rows if any(c.strip() for c in r)]
    if "sent" not in state:  # 처음 연결: 기존 글은 건너뛰고 이후 글만
        state["sent"] = len(rows)
        try:
            send_telegram(token, chat, f"✅ <b>불편사항 접수함 연결 완료</b>\n이제 사이트에서 보낸 의견이 이 방으로 와요. (기존 {len(rows)}건은 건너뜀)")
        except Exception as ex:  # noqa: BLE001
            status["error"] = f"텔레그램 전송 실패: {type(ex).__name__}"
            state.pop("sent")
        state["status"] = status
        return state, status
    if state["sent"] > len(rows):  # 시트에서 글을 지운 경우
        state["sent"] = len(rows)
    new = rows[state["sent"]:]
    for r in new[:10]:
        when = r[0] if r else ""
        body = r[1] if len(r) > 1 else ""
        contact = r[2] if len(r) > 2 else ""
        msg = f"📝 <b>마켓 펄스 불편사항</b>\n{html.escape(body)[:3000]}"
        if contact.strip():
            msg += f"\n\n<b>연락처</b> {html.escape(contact)}"
        msg += f"\n<i>{html.escape(when)}</i>"
        try:
            send_telegram(token, chat, msg)
            state["sent"] += 1
        except Exception as ex:  # noqa: BLE001
            status["error"] = f"텔레그램 전송 실패: {type(ex).__name__}"
            break
    if new:
        log(f"   불편사항 {len(new)}건 텔레그램 전달")
    state["status"] = status
    return state, status
