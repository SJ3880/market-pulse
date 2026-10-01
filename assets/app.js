/* 마켓 펄스 — 화면 스크립트
 * 1분마다 수집기가 올린 최신 스냅샷(JSON)을 찾아 화면을 갱신합니다.
 */
(() => {
  "use strict";

  // ───────── 데이터 위치 ─────────
  function dataBase() {
    const q = new URLSearchParams(location.search).get("data");
    if (q) return q.endsWith("/") ? q : q + "/";
    if (window.MP_DATA_BASE) return window.MP_DATA_BASE;
    const h = location.hostname;
    if (h.endsWith(".github.io")) {
      const owner = h.split(".")[0];
      const seg = location.pathname.split("/").filter(Boolean)[0];
      const repo = seg && !seg.includes(".") ? seg : h;
      return `https://raw.githubusercontent.com/${owner}/${repo}/data/`;
    }
    return "./out/";
  }
  const BASE = dataBase();
  const POLL_SEC = 60;
  const POLL_OFFSET = 40; // 수집기는 매분 0초에 시작해 20~30초 안에 올림 → 매분 40초에 확인

  const TABS = { briefing: "브리핑", economy: "경제", stocks: "주식시장", realestate: "부동산", policy: "정책·공식발표", calendar: "일정" };
  const SUBS = { all: "전체", kr: "국내", global: "해외", ipo: "IPO·공모" };
  const SIDE_MARKETS = {
    economy: ["KRW=X", "JPYKRW=X", "^TNX", "DX-Y.NYB", "CL=F", "GC=F"],
    stocks: ["^KS11", "^KQ11", "^GSPC", "^IXIC", "^SOX", "^N225", "^VIX", "BTC-USD"],
    realestate: ["^TNX", "KRW=X", "^KS11"],
  };

  const S = {
    snap: null, tab: "briefing", sub: "all", org: "all", open: new Set(),
    seen: {}, lastOk: 0, error: null, nextAt: 0,
  };

  // ───────── 유틸 ─────────
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pad = (n) => String(n).padStart(2, "0");
  const stampOf = (ms) => { const d = new Date(ms); return `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}-${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}`; };
  const kst = (ms) => new Date(ms + 9 * 3600e3); // getUTC* 로 읽으면 한국시간
  const hm = (sec) => { const d = kst(sec * 1000); return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`; };
  function ago(sec) {
    const diff = Date.now() / 1000 - sec;
    if (diff < 60) return "방금";
    if (diff < 3600) return `${Math.floor(diff / 60)}분 전`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}시간 전`;
    const d = kst(sec * 1000);
    return `${d.getUTCMonth() + 1}/${d.getUTCDate()} ${hm(sec)}`;
  }
  const fmt = (v, d) => v == null ? "–" : Number(v).toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d });
  const dir = (v) => v == null ? "flat" : v > 0 ? "up" : v < 0 ? "down" : "flat";
  const sign = (v, d = 2) => v == null ? "–" : (v > 0 ? "▲" : v < 0 ? "▼" : "") + fmt(Math.abs(v), d);
  const pct = (v) => v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + fmt(Math.abs(v), 2) + "%";
  const ext = (url) => /^https?:\/\//i.test(url || "") ? `href="${esc(url)}" target="_blank" rel="noopener noreferrer"` : "";

  // ───────── 데이터 가져오기 ─────────
  async function getJSON(url) {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  async function poll(force = false) {
    const now = Date.now();
    const have = S.snap ? S.snap.generated_at * 1000 : 0;
    const haveStamp = have ? stampOf(have) : "";
    const cands = [];
    for (let m = 0; m <= 15; m++) {
      const t = now - m * 60e3;
      if (m === 0 && new Date(now).getUTCSeconds() < 30) continue; // 이번 분 데이터는 아직 올라오기 전
      const st = stampOf(t);
      if (have && st <= haveStamp) break; // 이미 가진 것보다 새로운 것만
      cands.push(st);
    }
    // 분 단위 파일 + latest.json(매번 다른 주소로 요청해 캐시 우회)을 함께 확인
    const reqs = cands.map((st) => getJSON(`${BASE}snap/${st}.json`));
    reqs.push(getJSON(`${BASE}latest.json?t=${Math.floor(now / 1000)}`));
    let best = null;
    const res = await Promise.allSettled(reqs);
    for (const r of res) if (r.status === "fulfilled" && r.value?.generated_at && (!best || r.value.generated_at > best.generated_at)) best = r.value;
    if (!best && !S.snap) S.error = (res[res.length - 1].reason || {}).message || "not found";
    if (best && (!S.snap || best.generated_at > S.snap.generated_at)) {
      apply(best);
    } else {
      renderStatus();
      if (!S.snap) renderEmpty();
    }
  }

  function apply(snap) {
    const firstLoad = !S.snap;
    S.snap = snap; S.error = null; S.lastOk = Date.now();
    // 수집기가 직전 스냅샷과 비교해 표시한 '새 진입' 이슈 (첫 로딩 때는 강조하지 않음)
    S.fresh = firstLoad ? new Set() : new Set(
      ["economy", "stocks", "realestate"].flatMap((t) => (snap.tabs[t]?.issues || []).slice(0, 10)).filter((i) => i.is_new).map((i) => i.link));
    S.flashOnce = true;
    renderAll();
    S.flashOnce = false;
  }

  function schedule() {
    const now = new Date();
    let wait = ((POLL_OFFSET - now.getSeconds()) + 60) % 60 || 60;
    S.nextAt = Date.now() + wait * 1000;
    clearTimeout(S.timer);
    S.timer = setTimeout(async () => {
      try { if (!document.hidden) await poll(); } catch (e) { console.warn("poll 실패", e); } finally { schedule(); }
    }, wait * 1000);
  }

  // ───────── 렌더링 ─────────
  function renderAll() {
    renderStatus(); renderBoard(); renderTabs(); renderMain(); renderFoot();
  }

  function renderStatus() {
    const dot = $("#statusDot"), txt = $("#statusText");
    if (!S.snap) {
      dot.className = "dot" + (S.error ? " err" : "");
      txt.textContent = S.error ? "데이터를 아직 찾지 못했어요" : "불러오는 중";
      return;
    }
    const age = (Date.now() / 1000 - S.snap.generated_at) / 60;
    const left = Math.max(0, Math.round((S.nextAt - Date.now()) / 1000));
    dot.className = "dot " + (age < 4 ? "live" : age < 20 ? "stale" : "err");
    const base = `${hm(S.snap.generated_at)} 기준`;
    txt.textContent = age < 4 ? `${base} · ${left}초 뒤 갱신` : `${base} (${Math.round(age)}분 전 데이터) · ${left}초 뒤 확인`;
  }

  function spark(arr, prev, w = 44, h = 26) {
    if (!arr || arr.length < 2) return "";
    const vals = prev != null ? arr.concat([prev]) : arr;
    const min = Math.min(...vals), max = Math.max(...vals), span = max - min || 1;
    const pts = arr.map((v, i) => `${(i / (arr.length - 1) * w).toFixed(1)},${(h - 2 - (v - min) / span * (h - 4)).toFixed(1)}`).join(" ");
    const last = arr[arr.length - 1];
    const color = prev == null ? "var(--muted)" : last >= prev ? "var(--up)" : "var(--down)";
    const by = prev != null ? (h - 2 - (prev - min) / span * (h - 4)).toFixed(1) : null;
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">${by ? `<line x1="0" x2="${w}" y1="${by}" y2="${by}" stroke="var(--line)" stroke-dasharray="2 2"/>` : ""}<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
  }

  function marketState(group) {
    const n = new Date();
    const k = kst(n.getTime()), kd = k.getUTCDay(), km = k.getUTCHours() * 60 + k.getUTCMinutes();
    if (group === "kr") return kd > 0 && kd < 6 && km >= 540 && km <= 930 ? "장중" : "마감";
    if (group === "us") {
      const ny = new Date(n.toLocaleString("en-US", { timeZone: "America/New_York" }));
      const d = ny.getDay(), m = ny.getHours() * 60 + ny.getMinutes();
      return d > 0 && d < 6 && m >= 570 && m <= 960 ? "장중" : "마감";
    }
    return "";
  }

  const quote = (sym) => S.snap?.markets.find((m) => m.sym === sym);

  function renderBoard() {
    const el = $("#board");
    el.innerHTML = S.snap.markets.map((m) => {
      if (m.error || m.price == null) return `<div class="tick err"><span class="n">${esc(m.name)}</span><span class="p">수신 대기</span></div>`;
      const st = marketState(m.group);
      return `<div class="tick" title="${esc(m.name)} 전일 ${fmt(m.prev, m.digits)}">
        <span class="n">${esc(m.name)}${st ? `<span class="badge">${st}</span>` : ""}</span>
        <span class="p num">${fmt(m.price, m.digits)}</span>
        <span class="c num ${dir(m.change)}">${pct(m.pct)}</span>
        ${spark(m.spark, m.prev)}
      </div>`;
    }).join("");
  }

  function renderTabs() {
    document.querySelectorAll("#tabs button").forEach((b) => {
      const t = b.dataset.tab;
      b.setAttribute("aria-selected", t === S.tab ? "true" : "false");
      b.tabIndex = t === S.tab ? 0 : -1;
      let n = 0;
      if (S.snap?.tabs[t] && t !== S.tab) n = S.snap.tabs[t].issues.slice(0, 10).filter((i) => S.fresh?.has(i.link)).length;
      b.innerHTML = esc(TABS[t]) + (n ? `<span class="cnt" title="새로 올라온 이슈">${n}</span>` : "");
    });
  }

  function heatColor(h) {
    return h >= 80 ? "var(--heat-3)" : h >= 55 ? "var(--heat-2)" : h >= 30 ? "var(--heat-1)" : "var(--heat-0)";
  }

  function issueHTML(iss, i, opts = {}) {
    const open = S.open.has(iss.link);
    const mv = iss.is_new ? `<span class="mv new" title="직전 갱신 대비 새로 진입">NEW</span>`
      : iss.delta > 0 ? `<span class="mv up" title="순위 상승">▲${iss.delta}</span>`
      : iss.delta < 0 ? `<span class="mv down" title="순위 하락">▼${-iss.delta}</span>` : "";
    const why = iss.reasons.map((r, k) => `<span class="${k === 0 && iss.count >= 5 ? "hot" : ""}">${esc(r)}</span>`).join("");
    const others = iss.articles.filter((a) => a.link !== iss.link);
    const tabTag = opts.tabLabel ? `<span>${esc(opts.tabLabel)}</span>` : "";
    return `<li class="issue ${i === 0 && !opts.noLead ? "lead" : ""} ${S.flashOnce && S.fresh?.has(iss.link) ? "flash" : ""}">
      <div class="rank"><b class="num">${i + 1}</b><div class="heat" title="이슈 강도 ${iss.heat}"><i style="height:${Math.max(8, iss.heat)}%;background:${heatColor(iss.heat)}"></i></div>${mv}</div>
      <div>
        <h3><a ${ext(iss.link)}>${esc(iss.title)}</a></h3>
        ${iss.summary ? `<p class="sum">${esc(iss.summary)}</p>` : ""}
        <div class="meta">
          ${tabTag}<span class="src">${esc(iss.outlet)}</span><span>${ago(iss.ts)}</span>
          ${iss.rising ? `<span class="why"><span class="hot">급부상</span></span>` : ""}
          <span class="why">${why}</span>
          ${others.length ? `<button class="more" data-open="${esc(iss.link)}" aria-expanded="${open}">${open ? "접기" : `관련 기사 ${others.length}건`}</button>` : ""}
        </div>
        ${open ? `<ul class="related">${others.map((a) => `<li><span class="o">${esc(a.outlet)}</span><a ${ext(a.link)}>${esc(a.title)}</a></li>`).join("")}</ul>` : ""}
      </div>
    </li>`;
  }

  function kwHTML(kws) {
    if (!kws?.length) return `<p class="hint">키워드를 모으는 중이에요.</p>`;
    return `<div class="kw">${kws.map(([w], i) => `<span class="${i === 0 ? "k1" : i < 4 ? "k2" : ""}">${esc(w)}</span>`).join("")}</div>`;
  }

  function miniHTML(syms) {
    return `<div class="mini">${syms.map(quote).filter(Boolean).map((m) => `<div class="row">
      <span>${esc(m.name)}</span><span class="v num">${m.price == null ? "–" : fmt(m.price, m.digits)}</span>
      <span class="c num ${dir(m.change)}">${m.price == null ? "" : sign(m.change, m.digits)}<br>${pct(m.pct)}</span></div>`).join("")}</div>`;
  }

  // ───────── 일정 (공식 일정 + 정기 발표 규칙) ─────────
  const FOMC = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
    "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09", "2027-07-28", "2027-09-15", "2027-10-27", "2027-12-08"];
  const BOK = ["2026-01-15", "2026-02-26", "2026-04-10", "2026-05-28", "2026-07-16", "2026-08-27", "2026-10-22", "2026-11-26"];

  function events(days = 60) {
    const out = [];
    const today = kst(Date.now()); today.setUTCHours(0, 0, 0, 0);
    const end = new Date(today.getTime() + days * 86400e3);
    const add = (d, title, note, cat, rule = false) => { if (d >= today && d <= end) out.push({ d, title, note, cat, rule }); };
    const D = (s) => new Date(s + "T00:00:00Z");
    // 미국 서머타임(3월 둘째 일요일~11월 첫째 일요일) 여부에 따라 한국시간 발표 시각이 1시간 달라짐
    const usDST = (d) => {
      const y = d.getUTCFullYear();
      const nthSun = (mo, n) => { const x = new Date(Date.UTC(y, mo, 1)); while (x.getUTCDay() !== 0) x.setUTCDate(x.getUTCDate() + 1); x.setUTCDate(x.getUTCDate() + 7 * (n - 1)); return x; };
      return d >= nthSun(2, 2) && d < nthSun(10, 1);
    };
    FOMC.forEach((s) => {
      const d = D(s), dst = usDST(d);
      add(new Date(d.getTime() + 86400e3), "미국 FOMC 금리 결정", dst ? "한국시간 새벽 3시 발표 · 3시 30분 의장 회견" : "한국시간 새벽 4시 발표 · 4시 30분 의장 회견", "금리");
    });
    BOK.forEach((s) => add(D(s), "한국은행 금통위 기준금리 결정", "오전 9시대 발표 · 총재 기자간담회", "금리"));
    for (let m = 0; m < 3; m++) {
      const y = today.getUTCFullYear(), mo = today.getUTCMonth() + m;
      const first = new Date(Date.UTC(y, mo, 1));
      add(first, "수출입 동향 (전월)", "산업통상자원부 · 통상 매월 1일 오전 9시", "경제", true);
      let fri = new Date(first); while (fri.getUTCDay() !== 5) fri.setUTCDate(fri.getUTCDate() + 1);
      add(fri, "미국 고용보고서", `통상 첫째 금요일 · 한국시간 밤 ${usDST(fri) ? "9시 30분" : "10시 30분"} (일정 변동 가능)`, "경제", true);
      let thu = new Date(first); while (thu.getUTCDay() !== 4) thu.setUTCDate(thu.getUTCDate() + 1); thu.setUTCDate(thu.getUTCDate() + 7);
      const q = [2, 5, 8, 11].includes(thu.getUTCMonth());
      add(thu, q ? "선물·옵션 동시만기 (쿼드러플 위칭)" : "옵션 만기일", "매월 둘째 목요일 · 장 마감 변동성 유의", "주식", true);
    }
    for (let i = 0; i < days; i++) {
      const d = new Date(today.getTime() + i * 86400e3);
      if (d.getUTCDay() === 4) add(d, "주간 아파트 가격 동향", "한국부동산원 · 통상 목요일 오후 2시", "부동산", true);
    }
    return out.sort((a, b) => a.d - b.d);
  }

  function evHTML(list) {
    if (!list.length) return `<p class="hint">가까운 일정이 없어요.</p>`;
    const today = kst(Date.now()); today.setUTCHours(0, 0, 0, 0);
    return list.map((e) => {
      const dd = Math.round((e.d - today) / 86400e3);
      const wd = "일월화수목금토"[e.d.getUTCDay()];
      return `<div class="ev"><span class="d ${dd <= 2 ? "soon" : ""}">${dd === 0 ? "오늘" : "D-" + dd}</span>
        <span class="t">${esc(e.title)}<small>${esc(e.note)}</small></span>
        <span class="when">${e.d.getUTCMonth() + 1}/${e.d.getUTCDate()} (${wd})</span></div>`;
    }).join("");
  }

  function sideEvents(cats, n = 4) {
    const all = events(45).filter((e) => cats.includes(e.cat));
    const weekly = all.filter((e) => e.title.startsWith("주간"));
    const main = all.filter((e) => !e.title.startsWith("주간")).slice(0, n);
    return main.concat(weekly.slice(0, cats.includes("부동산") ? 1 : 0)).sort((a, b) => a.d - b.d);
  }

  // ───────── 탭 화면 ─────────
  const TAB_INTRO = {
    economy: "금리·환율·물가·수출 등 거시경제 이슈",
    stocks: "국내외 증시, 수급, IPO·공모주",
    realestate: "집값·전월세·대출규제·공급",
  };

  function renderMain() {
    const main = $("#main");
    if (!S.snap) return;
    const t = S.tab;
    if (t === "briefing") main.innerHTML = briefingHTML();
    else if (t === "policy") main.innerHTML = policyHTML();
    else if (t === "calendar") main.innerHTML = calendarHTML();
    else main.innerHTML = tabHTML(t);
  }

  function tabHTML(t) {
    const data = S.snap.tabs[t] || { issues: [], keywords: [] };
    let issues = data.issues;
    let chips = "";
    if (t === "stocks") {
      chips = `<div class="chips" role="group" aria-label="시장 구분">${Object.entries(SUBS).map(([k, v]) =>
        `<button class="chip" data-sub="${k}" aria-pressed="${S.sub === k}">${v}</button>`).join("")}</div>`;
      if (S.sub !== "all") issues = issues.filter((i) => i.sub === S.sub);
    }
    const list = issues.length ? issues.map((iss, i) => issueHTML(iss, i)).join("")
      : `<li class="empty">지금은 이 구분에 해당하는 이슈가 없어요. 다른 구분을 눌러 보세요.</li>`;
    const evCats = { economy: ["금리", "경제"], stocks: ["금리", "주식"], realestate: ["금리", "부동산"] }[t];
    return `<div class="grid">
      <section class="panel" aria-label="${TABS[t]} 이슈">
        <div class="list-head"><div><h1>${TABS[t]} 핵심 이슈</h1><p>${TAB_INTRO[t]} · 기사 ${data.total_articles ?? 0}건을 ${data.issues.length}개 이슈로 묶음</p></div>${chips}</div>
        <ol class="issues">${list}</ol>
      </section>
      <aside class="side">
        <div class="panel"><h2 class="sec">많이 언급되는 키워드 <small>상위 이슈 기준</small></h2>${kwHTML(data.keywords)}</div>
        <div class="panel"><h2 class="sec">관련 지표</h2>${miniHTML(SIDE_MARKETS[t])}</div>
        <div class="panel"><h2 class="sec">다가오는 일정</h2>${evHTML(sideEvents(evCats))}</div>
      </aside>
    </div>`;
  }

  function briefingHTML() {
    const tabs = ["economy", "stocks", "realestate"];
    const line = ["^KS11", "^KQ11", "KRW=X", "^GSPC", "^IXIC", "^TNX"].map(quote).filter((m) => m && m.price != null)
      .map((m) => `<span><strong>${esc(m.name)}</strong><span class="num">${fmt(m.price, m.digits)}</span> <span class="num ${dir(m.change)}">${pct(m.pct)}</span></span>`).join("");
    const picks = tabs.map((t) => {
      const is = S.snap.tabs[t]?.issues || [];
      if (!is.length) return `<div class="panel pick"><div class="lbl">${TABS[t]}</div><p class="hint">이슈를 모으는 중이에요.</p></div>`;
      const [a, ...rest] = is;
      return `<div class="panel pick">
        <div class="lbl"><span>${TABS[t]} 1위</span><a href="#${t}" data-go="${t}">전체 보기</a></div>
        <h3><a ${ext(a.link)}>${esc(a.title)}</a></h3>
        <div class="meta"><span class="src">${esc(a.outlet)}</span><span>${ago(a.ts)}</span><span class="why">${a.reasons.slice(0, 2).map((r) => `<span>${esc(r)}</span>`).join("")}</span></div>
        <ol>${rest.slice(0, 3).map((r) => `<li><a ${ext(r.link)}>${esc(r.title)}</a></li>`).join("")}</ol>
      </div>`;
    }).join("");
    const all = tabs.flatMap((t) => (S.snap.tabs[t]?.issues || []).map((i) => ({ ...i, _tab: t })))
      .sort((a, b) => b.score - a.score).slice(0, 10);
    const rising = tabs.flatMap((t) => (S.snap.tabs[t]?.issues || []).filter((i) => i.rising || i.is_new).map((i) => ({ ...i, _tab: t }))).slice(0, 6);
    return `<div class="panel summary-line">${line || `<span class="hint">시세를 받는 중이에요.</span>`}</div>
      <div class="brief-top">${picks}</div>
      <div class="grid">
        <section class="panel" aria-label="전체 이슈 순위">
          <div class="list-head"><div><h1>지금 가장 뜨거운 이슈</h1><p>경제·주식·부동산 전체를 같은 기준으로 줄 세운 상위 10개</p></div></div>
          <ol class="issues">${all.map((iss, i) => issueHTML(iss, i, { tabLabel: TABS[iss._tab] })).join("")}</ol>
        </section>
        <aside class="side">
          <div class="panel"><h2 class="sec">새로 떠오르는 이슈 <small>최근 2시간</small></h2>${rising.length
            ? `<div class="mini">${rising.map((r) => `<div class="row" style="grid-template-columns:1fr"><a ${ext(r.link)}>${esc(r.title)}</a></div>`).join("")}</div>`
            : `<p class="hint">최근 2시간 동안 급하게 번진 이슈는 없어요.</p>`}</div>
          <div class="panel"><h2 class="sec">다가오는 일정</h2>${evHTML(sideEvents(["금리", "경제", "주식"], 5))}</div>
        </aside>
      </div>`;
  }

  function policyHTML() {
    const pol = S.snap.policy || [];
    const orgs = [...new Set(pol.map((p) => p.outlet))];
    const list = S.org === "all" ? pol : pol.filter((p) => p.outlet === S.org);
    return `<section class="panel">
      <div class="list-head"><div><h1>정책·공식발표</h1><p>정부 부처와 중앙은행이 직접 낸 보도자료, 최신순</p></div>
        <div class="chips">${["all", ...orgs].map((o) => `<button class="chip" data-org="${esc(o)}" aria-pressed="${S.org === o}">${o === "all" ? "전체" : esc(o)}</button>`).join("")}</div></div>
      ${list.length ? list.map((p) => `<div class="pol"><span class="org">${esc(p.outlet)}</span>
        <div class="tt"><a ${ext(p.link)}>${esc(p.title)}</a>${p.summary ? `<p>${esc(p.summary)}</p>` : ""}</div>
        <span class="tm">${ago(p.ts)}</span></div>`).join("") : `<div class="empty">아직 받은 보도자료가 없어요. 하단 '수집 상태'에서 기관 연결을 확인해 보세요.</div>`}
    </section>`;
  }

  function calendarHTML() {
    const ev = events(60);
    return `<div class="grid"><section class="panel" style="padding:4px 18px 10px">
      <div class="list-head" style="padding-left:0;padding-right:0"><div><h1>앞으로 60일 주요 일정</h1><p>FOMC·금통위는 공식 일정, 나머지는 정기 발표 규칙으로 계산(실제와 다를 수 있음)</p></div></div>
      ${evHTML(ev)}</section>
      <aside class="side"><div class="panel"><h2 class="sec">읽는 법</h2><p class="hint" style="margin:0">D-2 이내 일정은 빨간색으로 표시돼요. 금리 결정일 전후에는 환율·채권금리·성장주 변동성이 커지는 경우가 많아요.</p></div></aside></div>`;
  }

  function renderEmpty() {
    $("#main").innerHTML = `<div class="banner">아직 수집된 데이터를 찾지 못했어요. 처음 설정했다면 GitHub의 Actions 탭에서 '실시간 수집'이 한 번 이상 성공했는지 확인해 주세요. (데이터 위치: ${esc(BASE)})</div>`;
  }

  function renderFoot() {
    const s = S.snap;
    const ok = s.sources.filter((x) => x.ok).length;
    const mk = s.markets.filter((m) => m.price != null).length;
    $("#foot").innerHTML = `
      <details><summary>이슈는 이렇게 고릅니다</summary>
        <ol>
          <li><b>믿을 만한 매체만</b>: 통신사·경제지·종합지·방송과 Reuters·Bloomberg·WSJ·FT·CNBC, 정부·중앙은행 보도자료만 집계합니다.</li>
          <li><b>같은 사건은 하나로</b>: 제목이 비슷한 기사를 묶어 한 이슈로 보고, 몇 개 매체가 다뤘는지 셉니다(같은 매체 중복은 1회).</li>
          <li><b>점수</b>: 보도 매체 수 × 최신성(6시간마다 영향 감소) × 출처 권위 × 시장 영향 키워드(금리·환율·외국인·DSR 등) 가점 × 포털 주요뉴스 가점.</li>
          <li><b>급부상</b>: 최근 2시간 안에 3곳 이상이 새로 다룬 3시간 이내 이슈. <b>NEW·▲▼</b>는 직전 갱신 대비 순위 변화입니다.</li>
        </ol>
      </details>
      <details><summary>수집 상태: 뉴스 소스 ${ok}/${s.sources.length} 정상 · 시장지표 ${mk}/${s.markets.length} 정상 · 수집 ${(s.took_ms / 1000).toFixed(1)}초</summary>
        <table>${s.sources.map((x) => `<tr><td class="${x.ok ? "ok" : "bad"}">${x.ok ? "정상" : "실패"}</td><td>${esc(x.label)}</td><td class="num">${x.count}건</td><td>${esc(x.error || "")}</td></tr>`).join("")}</table>
      </details>
      <p>시세는 Yahoo Finance 기준이며 지연될 수 있습니다. 투자 판단의 참고 자료로만 사용하세요. 마지막 수집 ${esc(s.generated_kst)} (KST)</p>`;
  }

  // ───────── 이벤트 ─────────
  function setTab(t, focus = false) {
    if (!TABS[t]) t = "briefing";
    S.tab = t;
    if (location.hash !== "#" + t) history.replaceState(null, "", "#" + t);
    renderTabs(); renderMain();
    if (focus) window.scrollTo({ top: 0 });
  }

  $("#tabs").addEventListener("click", (e) => { const b = e.target.closest("button[data-tab]"); if (b) setTab(b.dataset.tab, true); });
  $("#tabs").addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
    const keys = Object.keys(TABS), i = keys.indexOf(S.tab);
    const n = keys[(i + (e.key === "ArrowRight" ? 1 : keys.length - 1)) % keys.length];
    setTab(n); $(`#tabs button[data-tab="${n}"]`).focus();
  });
  $("#main").addEventListener("click", (e) => {
    const o = e.target.closest("[data-open]");
    if (o) { const k = o.dataset.open; S.open.has(k) ? S.open.delete(k) : S.open.add(k); renderMain(); return; }
    const s = e.target.closest("[data-sub]");
    if (s) { S.sub = s.dataset.sub; renderMain(); return; }
    const g = e.target.closest("[data-org]");
    if (g) { S.org = g.dataset.org; renderMain(); return; }
    const go = e.target.closest("[data-go]");
    if (go) { e.preventDefault(); setTab(go.dataset.go, true); }
  });
  window.addEventListener("hashchange", () => setTab(location.hash.slice(1)));
  $("#refreshBtn").addEventListener("click", async () => {
    const b = $("#refreshBtn"); b.classList.remove("spin"); void b.offsetWidth; b.classList.add("spin");
    await poll(true);
  });
  $("#themeBtn").addEventListener("click", () => {
    const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("mp-theme", next); } catch (e) { /* 저장 불가 환경 */ }
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  setInterval(renderStatus, 1000);
  setInterval(() => { if (S.snap && (S.tab !== "calendar")) { /* '몇 분 전' 표시 갱신 */ renderMain(); } }, 60e3 * 5);

  // ───────── 시작 ─────────
  S.tab = TABS[location.hash.slice(1)] ? location.hash.slice(1) : "briefing";
  renderTabs();
  poll(true).finally(schedule);
})();
