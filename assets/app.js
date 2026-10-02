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
  const POLL_SECS = [10, 40]; // 수집기는 0초·30초에 시작(장중) → 10초·40초에 확인

  const TABS = { briefing: "브리핑", economy: "경제", stocks: "주식시장", realestate: "부동산", ipo: "IPO", ib: "IB", funding: "비상장 투자", policy: "정책·발표", timeline: "하루 흐름", calendar: "일정" };
  const SUBS = { all: "전체", kr: "국내", global: "해외" };
  const IB_SUBS = { all: "전체", ecm: "유증·블록딜·메자닌", mna: "M&A" };
  const SIDE_MARKETS = {
    economy: ["KRW=X", "JPYKRW=X", "^TNX", "DX-Y.NYB", "CL=F", "GC=F"],
    stocks: ["^KS11", "^KQ11", "^GSPC", "^IXIC", "^SOX", "^N225", "^VIX", "BTC-USD"],
    realestate: ["^TNX", "KRW=X", "^KS11"],
    ipo: ["^KQ11", "^KS11", "^IXIC", "^VIX"],
    ib: ["^KS11", "^KQ11", "^VIX", "KRW=X"],
    funding: ["^KQ11", "^IXIC", "^KS11", "^VIX"],
  };

  const S = {
    snap: null, tab: "briefing", sub: "all", ibSub: "all", org: "all", open: new Set(),
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
      ["economy", "stocks", "realestate", "ipo", "ib", "funding"].flatMap((t) => (snap.tabs[t]?.issues || []).slice(0, 10)).filter((i) => i.is_new).map((i) => i.link));
    S.flashOnce = true;
    if (S.tab === "timeline" && S.tl.dates && S.tl.sel === S.tl.dates[0]) loadTimeline();
    renderAll();
    S.flashOnce = false;
  }

  function schedule() {
    const now = new Date();
    const sec = now.getSeconds();
    let wait = Math.min(...POLL_SECS.map((p) => ((p - sec) + 60) % 60 || 60));
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
    }).join("") + rateTiles();
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

  // 빠른 보기: 화면에 그린 이슈를 번호로 기억했다가 제목을 누르면 요약 창으로 보여 줌
  S.q = [];
  const qref = (obj) => { S.q.push(obj); return S.q.length - 1; };
  const qbtn = (obj, label, cls = "hl") => `<button type="button" class="${cls}" data-q="${qref(obj)}">${esc(label)}</button>`;

  function issueHTML(iss, i, opts = {}) {
    const mv = iss.is_new ? `<span class="mv new" title="직전 갱신 대비 새로 진입">NEW</span>`
      : iss.delta > 0 ? `<span class="mv up" title="순위 상승">▲${iss.delta}</span>`
      : iss.delta < 0 ? `<span class="mv down" title="순위 하락">▼${-iss.delta}</span>` : "";
    const why = iss.reasons.map((r, k) => `<span class="${k === 0 && iss.count >= 5 ? "hot" : ""}">${esc(r)}</span>`).join("");
    const others = iss.articles.filter((a) => a.link !== iss.link);
    const tabTag = opts.tabLabel ? `<span>${esc(opts.tabLabel)}</span>` : "";
    return `<li class="issue ${i === 0 && !opts.noLead ? "lead" : ""} ${S.flashOnce && S.fresh?.has(iss.link) ? "flash" : ""}">
      <div class="rank"><b class="num">${i + 1}</b><div class="heat" title="이슈 강도 ${iss.heat}"><i style="height:${Math.max(8, iss.heat)}%;background:${heatColor(iss.heat)}"></i></div>${mv}</div>
      <div>
        <h3>${qbtn({ ...iss, _tabLabel: opts.tabLabel, _rank: i + 1 }, iss.title)}</h3>
        ${iss.summary ? `<p class="sum">${esc(iss.summary.split("\n\n")[0])}</p>` : ""}
        <div class="meta">
          ${tabTag}<span class="src">${esc(iss.outlet)}</span><span>${ago(iss.ts)}</span>
          ${iss.rising ? `<span class="why"><span class="hot">급부상</span></span>` : ""}
          <span class="why">${why}</span>
          ${others.length ? `<span class="hint">다른 보도 ${others.length}건</span>` : ""}
        </div>
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
    ipo: "더벨·딜사이트 우선 · 프리IPO·주관사·상장 추진/연기/철회·몸값·심사·제도·리그테이블 (수요예측·청약 등 일정 기사 제외)",
    ib: "더벨·딜사이트 우선 · 대형 유상증자·블록딜·메자닌(CB·EB·BW)·M&A·리그테이블",
    funding: "더벨·딜사이트·바이오스펙테이터 우선 · 비상장사 시드~시리즈 투자유치, 대규모 펀딩, 기업가치",
  };

  function renderMain() {
    const main = $("#main");
    if (!S.snap) return;
    S.q = [];
    const t = S.tab;
    if (t === "briefing") main.innerHTML = briefingHTML();
    else if (t === "policy") main.innerHTML = policyHTML();
    else if (t === "calendar") main.innerHTML = calendarHTML();
    else if (t === "timeline") main.innerHTML = timelineHTML();
    else main.innerHTML = tabHTML(t);
    drawCharts();
  }

  function tabHTML(t) {
    const data = S.snap.tabs[t] || { issues: [], keywords: [] };
    let issues = data.issues;
    let chips = "";
    if (t === "stocks" || t === "ib") {
      const subs = t === "ib" ? IB_SUBS : SUBS;
      const cur = t === "ib" ? S.ibSub : S.sub;
      chips = `<div class="chips" role="group" aria-label="구분">${Object.entries(subs).map(([k, v]) =>
        `<button class="chip" data-${t === "ib" ? "ibsub" : "sub"}="${k}" aria-pressed="${cur === k}">${v}</button>`).join("")}</div>`;
      if (cur && cur !== "all") issues = issues.filter((i) => i.sub === cur);
    }
    const list = issues.length ? issues.map((iss, i) => issueHTML(iss, i)).join("")
      : `<li class="empty">지금은 이 구분에 해당하는 이슈가 없어요. 다른 구분을 눌러 보세요.</li>`;
    const evCats = { economy: ["금리", "경제"], stocks: ["금리", "주식"], realestate: ["금리", "부동산"], ipo: ["금리", "주식"], ib: ["금리", "주식"], funding: ["금리", "주식"] }[t];
    const top = t === "stocks" ? marketPanelHTML() : t === "realestate" ? realestateIndicatorsHTML() : "";
    return `${top}<div class="grid">
      <section class="panel" aria-label="${TABS[t]} 이슈">
        <div class="list-head"><div><h1>${TABS[t]} 핵심 이슈</h1><p>${TAB_INTRO[t]} · 기사 ${data.total_articles ?? 0}건을 ${data.issues.length}개 이슈로 묶음</p></div>${chips}</div>
        <ol class="issues">${list}</ol>
      </section>
      <aside class="side">
        <div class="panel"><h2 class="sec">많이 언급되는 키워드 <small>상위 이슈 기준</small></h2>${kwHTML(data.keywords)}</div>
        ${t !== "stocks" ? `<div class="panel"><h2 class="sec">국내 금리 <small>한국은행</small></h2>${ratesPanel()}</div>` : ""}
        <div class="panel"><h2 class="sec">관련 지표</h2>${miniHTML(SIDE_MARKETS[t])}</div>
        <div class="panel"><h2 class="sec">다가오는 일정</h2>${evHTML(sideEvents(evCats))}</div>
      </aside>
    </div>`;
  }

  function briefingHTML() {
    const tabs = ["economy", "stocks", "realestate", "ipo", "ib", "funding"];
    const line = ["^KS11", "^KQ11", "KRW=X", "^GSPC", "^IXIC", "^TNX"].map(quote).filter((m) => m && m.price != null)
      .map((m) => `<span><strong>${esc(m.name)}</strong><span class="num">${fmt(m.price, m.digits)}</span> <span class="num ${dir(m.change)}">${pct(m.pct)}</span></span>`).join("")
      + (XT().flow?.markets?.KOSPI ? `<span><strong>외국인(코스피)</strong><span class="num ${dir(XT().flow.markets.KOSPI.foreign)}">${eok(XT().flow.markets.KOSPI.foreign)}</span></span>` : "");
    const picks = tabs.map((t) => {
      const is = S.snap.tabs[t]?.issues || [];
      if (!is.length) return `<div class="panel pick"><div class="lbl">${TABS[t]}</div><p class="hint">이슈를 모으는 중이에요.</p></div>`;
      const [a, ...rest] = is;
      return `<div class="panel pick">
        <div class="lbl"><span>${TABS[t]} 1위</span><a href="#${t}" data-go="${t}">전체 보기</a></div>
        <h3>${qbtn({ ...a, _tabLabel: TABS[t], _rank: 1 }, a.title)}</h3>
        <div class="meta"><span class="src">${esc(a.outlet)}</span><span>${ago(a.ts)}</span><span class="why">${a.reasons.slice(0, 2).map((r) => `<span>${esc(r)}</span>`).join("")}</span></div>
        <ol>${rest.slice(0, 3).map((r, k) => `<li>${qbtn({ ...r, _tabLabel: TABS[t], _rank: k + 2 }, r.title)}</li>`).join("")}</ol>
      </div>`;
    }).join("");
    const all = tabs.flatMap((t) => (S.snap.tabs[t]?.issues || []).map((i) => ({ ...i, _tab: t })))
      .sort((a, b) => b.score - a.score).slice(0, 10);
    const rising = tabs.flatMap((t) => (S.snap.tabs[t]?.issues || []).filter((i) => i.rising || i.is_new).map((i) => ({ ...i, _tab: t }))).slice(0, 6);
    return `<div class="panel summary-line">${line || `<span class="hint">시세를 받는 중이에요.</span>`}</div>
      <div class="brief-top">${picks}</div>
      <div class="grid">
        <section class="panel" aria-label="전체 이슈 순위">
          <div class="list-head"><div><h1>지금 가장 뜨거운 이슈</h1><p>경제·주식·부동산·IPO·IB·비상장 투자 전체를 같은 기준으로 줄 세운 상위 10개</p></div></div>
          <ol class="issues">${all.map((iss, i) => issueHTML(iss, i, { tabLabel: TABS[iss._tab] })).join("")}</ol>
        </section>
        <aside class="side">
          <div class="panel"><h2 class="sec">새로 떠오르는 이슈 <small>최근 2시간</small></h2>${rising.length
            ? `<div class="mini">${rising.map((r) => `<div class="row" style="grid-template-columns:1fr">${qbtn({ ...r, _tabLabel: TABS[r._tab] }, r.title)}</div>`).join("")}</div>`
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
      <div class="list-head"><div><h1>정책·공식발표</h1><p>재경부·금융위·국토부·한은 발표를 다룬 신뢰 매체 기사와 미 연준 보도자료, 최신순</p></div>
        <div class="chips">${["all", ...orgs].map((o) => `<button class="chip" data-org="${esc(o)}" aria-pressed="${S.org === o}">${o === "all" ? "전체" : esc(o)}</button>`).join("")}</div></div>
      ${list.length ? list.map((p) => `<div class="pol"><span class="org">${esc(p.outlet)}</span>
        <div class="tt">${qbtn({ ...p, _policy: true, _tabLabel: "정책·발표" }, p.title)}${p.summary ? `<p>${esc(p.summary)}</p>` : ""}</div>
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
      <details><summary>추가 지표·알림 상태</summary>
        <table>${Object.entries({ rates: "국내 금리 (한국은행 ECOS)", realestate: "부동산 주간 통계 (한국부동산원)", flow: "투자자별 순매수 (네이버 금융)", sectors: "업종 등락 (섹터 ETF·Yahoo)", bigcaps: "대형주 (Yahoo)" }).map(([k, nm]) => {
          const v = (s.extras || {})[k] || {};
          const st = v.ok ? "정상" : v.missing_key ? "키 없음" : v.stale ? "지연" : "실패";
          return `<tr><td class="${v.ok ? "ok" : "bad"}">${st}</td><td>${nm}</td><td>${v.fetched_at ? ago(v.fetched_at) : ""}</td><td>${esc(v.error || "")}</td></tr>`;
        }).join("")}
        <tr><td class="${s.alerts?.enabled ? "ok" : "bad"}">${s.alerts?.enabled ? "켜짐" : "꺼짐"}</td><td>텔레그램 키워드 알림</td><td>${s.alerts?.last_sent ? "마지막 전송 " + ago(s.alerts.last_sent) : ""}</td><td>${esc(s.alerts?.error || (s.alerts?.keywords?.length ? "키워드: " + s.alerts.keywords.join(", ") : ""))}</td></tr></table>
      </details>
      <p>시세는 Yahoo Finance 기준이며 지연될 수 있습니다. 투자 판단의 참고 자료로만 사용하세요. 마지막 수집 ${esc(s.generated_kst)} (KST)</p>`;
  }

  // ───────── 추가 지표 (금리·수급·업종·대형주·부동산) ─────────
  const XT = () => S.snap?.extras || {};
  const bp = (v) => v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + fmt(Math.abs(v * 100), 1) + "bp";
  const eok = (v) => v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + fmt(Math.abs(v), 0) + "억";
  const RATE_KEYS = ["base", "ktb3", "ktb10", "cd91"];

  function rateTiles() {
    const r = XT().rates?.series || {};
    return ["ktb3", "ktb10", "base"].filter((k) => r[k]).map((k) => {
      const s = r[k];
      const vals = (s.series || []).map((p) => p[1]);
      return `<div class="tick" title="${esc(s.name)} ${esc(s.date)} 기준 (한국은행)">
        <span class="n">${esc(s.name)}<span class="badge">일별</span></span>
        <span class="p num">${fmt(s.value, s.digits)}%</span>
        <span class="c num ${dir(s.change)}">${bp(s.change)}</span>
        ${spark(vals.slice(-30), vals.length > 30 ? vals[vals.length - 31] : vals[0])}
      </div>`;
    }).join("");
  }

  function ratesPanel() {
    const r = XT().rates;
    if (!r?.series) return r?.missing_key
      ? `<p class="hint">한국은행 인증키(ECOS_KEY)를 넣으면 국고채·기준금리·CD 금리가 표시돼요.</p>`
      : `<p class="hint">국내 금리를 받는 중이에요.</p>`;
    return `<div class="mini">${RATE_KEYS.filter((k) => r.series[k]).map((k) => {
      const s = r.series[k];
      const d = String(s.date);
      return `<div class="row"><span>${esc(s.name)}<br><small class="hint">${d.slice(4, 6)}/${d.slice(6, 8)} 기준</small></span>
        <span class="v num">${fmt(s.value, s.digits)}%</span><span class="c num ${dir(s.change)}">${bp(s.change)}</span></div>`;
    }).join("")}</div>`;
  }

  function flowHTML() {
    const f = XT().flow;
    if (!f?.markets) return `<p class="hint">투자자별 매매동향을 받는 중이에요.</p>`;
    const names = { individual: "개인", foreign: "외국인", institution: "기관" };
    const all = Object.values(f.markets).flatMap((m) => Object.values(m));
    const mx = Math.max(1, ...all.map(Math.abs));
    const st = marketState("kr");
    return Object.entries(f.markets).map(([mk, m]) => `<div class="flow-m">
      <div class="flow-h"><b>${mk === "KOSPI" ? "코스피" : "코스닥"}</b><span class="hint">${st === "장중" ? "장중 잠정" : "장 마감 기준"}</span></div>
      ${Object.entries(names).map(([k, nm]) => {
        const v = m[k], w = Math.abs(v) / mx * 50;
        return `<div class="fb"><span class="who">${nm}</span>
          <span class="track" role="img" aria-label="${nm} ${eok(v)}"><i class="${v >= 0 ? "pos" : "neg"}" style="width:${w.toFixed(1)}%"></i></span>
          <span class="v num ${dir(v)}">${eok(v)}</span></div>`;
      }).join("")}
    </div>`).join("");
  }

  function sectorsHTML() {
    const s = XT().sectors;
    if (!s?.top) return `<p class="hint">업종 등락을 받는 중이에요.</p>`;
    const mx = Math.max(0.5, ...s.top.concat(s.bottom).map((r) => Math.abs(r.pct)));
    const col = (rows) => rows.map((r) => `<div class="sb"><span class="nm">${esc(r.name)}</span>
      <span class="bar"><i class="${r.pct >= 0 ? "pos" : "neg"}" style="width:${(Math.abs(r.pct) / mx * 100).toFixed(0)}%"></i></span>
      <span class="v num ${dir(r.pct)}">${pct(r.pct)}</span></div>`).join("");
    return `<p class="hint" style="margin:0 0 8px">${s.count}개 업종 ETF 중 <span class="up">${s.up}개 상승</span> · <span class="down">${s.down}개 하락</span></p>
      <div class="sect"><div><h3 class="mini-h">강한 업종</h3>${col(s.top.filter((r) => r.pct > 0)) || `<p class="hint">오른 업종이 없어요.</p>`}</div><div><h3 class="mini-h">약한 업종</h3>${col(s.bottom.filter((r) => r.pct < 0)) || `<p class="hint">내린 업종이 없어요.</p>`}</div></div>`;
  }

  function bigcapsHTML() {
    const b = XT().bigcaps;
    if (!b?.stocks) return `<p class="hint">대형주 시세를 받는 중이에요.</p>`;
    return `<div class="caps">${b.stocks.map((c) => {
      const a = Math.min(1, Math.abs(c.pct || 0) / 4);
      const bg = c.pct > 0 ? `color-mix(in srgb, var(--up) ${(8 + a * 30).toFixed(0)}%, var(--panel))`
        : c.pct < 0 ? `color-mix(in srgb, var(--down) ${(8 + a * 30).toFixed(0)}%, var(--panel))` : "var(--panel-2)";
      return `<div class="cap" style="background:${bg}" title="${esc(c.name)} ${fmt(c.price, 0)}원">
        <span class="nm">${esc(c.name)}</span><span class="num v">${pct(c.pct)}</span></div>`;
    }).join("")}</div>`;
  }

  function marketPanelHTML() {
    const ex = XT();
    if (!ex.flow && !ex.sectors && !ex.bigcaps) return "";
    return `<section class="panel mp" aria-label="수급·시황">
      <div class="mp-col"><h2 class="sec">투자자별 순매수 <small>억원</small></h2>${flowHTML()}</div>
      <div class="mp-col"><h2 class="sec">업종 등락 <small>업종 대표 ETF 기준</small></h2>${sectorsHTML()}</div>
      <div class="mp-col"><h2 class="sec">대형주 <small>전일 대비</small></h2>${bigcapsHTML()}</div>
    </section>`;
  }

  // ───────── 선 그래프 (SVG, 마우스를 올리면 값 표시) ─────────
  S.charts = {};
  function chartBox(id, spec) {
    S.charts[id] = spec;
    const legend = spec.series.length > 1 ? `<div class="lg">${spec.series.map((s) => {
      const last = s.pts[s.pts.length - 1];
      return `<span><i style="background:${s.color}"></i>${esc(s.name)} <b class="num">${last ? spec.fmt(last[1]) : "–"}</b></span>`;
    }).join("")}</div>` : "";
    return `<figure class="chart"><figcaption><b>${esc(spec.title)}</b>${spec.sub ? `<span class="hint">${esc(spec.sub)}</span>` : ""}</figcaption>
      ${legend}<div class="lc" data-chart="${id}"></div></figure>`;
  }

  function drawCharts() {
    document.querySelectorAll(".lc[data-chart]").forEach((el) => {
      const spec = S.charts[el.dataset.chart];
      if (!spec) return;
      const W = Math.max(240, el.clientWidth), H = spec.height || 180;
      const m = { l: 44, r: 10, t: 8, b: 24 };
      const labels = spec.labels;
      const n = labels.length;
      if (n < 2) { el.innerHTML = `<p class="hint">자료가 쌓이는 중이에요.</p>`; return; }
      const vals = spec.series.flatMap((s) => s.pts.map((p) => p[1])).filter((v) => v != null);
      let lo = Math.min(...vals), hi = Math.max(...vals);
      if (spec.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
      const pad = (hi - lo) * 0.12 || Math.abs(hi) * 0.05 || 1; lo -= pad; hi += pad;
      const x = (i) => m.l + (i / (n - 1)) * (W - m.l - m.r);
      const y = (v) => m.t + (1 - (v - lo) / (hi - lo)) * (H - m.t - m.b);
      const step = Math.pow(10, Math.floor(Math.log10((hi - lo) / 4)));
      const tickStep = [1, 2, 2.5, 5, 10].map((k) => k * step).find((s) => (hi - lo) / s <= 5);
      const ticks = []; for (let v = Math.ceil(lo / tickStep) * tickStep; v <= hi; v += tickStep) ticks.push(+v.toFixed(6));
      const idx = Object.fromEntries(labels.map((l, i) => [l, i]));
      const lines = spec.series.map((s) => {
        const d = s.pts.filter((p) => p[1] != null && idx[p[0]] != null).map((p, k) => `${k ? "L" : "M"}${x(idx[p[0]]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
        return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
      }).join("");
      const xl = [0, Math.floor((n - 1) / 2), n - 1].map((i) => `<text x="${x(i)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === n - 1 ? "end" : "middle"}">${esc(spec.xfmt(labels[i]))}</text>`).join("");
      el.innerHTML = `<svg width="${W}" height="${H}" role="img" aria-label="${esc(spec.title)}">
        <g class="grid-l">${ticks.map((v) => `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}" ${spec.zero && v === 0 ? 'class="z"' : ""}/>
          <text x="${m.l - 6}" y="${y(v) + 4}" text-anchor="end">${esc(spec.tfmt(v))}</text>`).join("")}</g>
        <g class="ax">${xl}</g>${lines}
        <line class="cross" x1="0" x2="0" y1="${m.t}" y2="${H - m.b}" style="display:none"/>
        <g class="dots"></g><rect x="${m.l}" y="0" width="${W - m.l - m.r}" height="${H}" fill="transparent"/></svg>
        <div class="tip" style="display:none"></div>`;
      const svg = el.querySelector("svg"), tip = el.querySelector(".tip"), cross = el.querySelector(".cross"), dots = el.querySelector(".dots");
      const move = (cx) => {
        const r = svg.getBoundingClientRect();
        const i = Math.max(0, Math.min(n - 1, Math.round((cx - r.left - m.l) / (W - m.l - m.r) * (n - 1))));
        const px = x(i);
        cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.style.display = "";
        const rows = spec.series.map((s) => [s, s.pts.find((p) => p[0] === labels[i])]).filter(([, p]) => p && p[1] != null);
        dots.innerHTML = rows.map(([s, p]) => `<circle cx="${px}" cy="${y(p[1])}" r="4" fill="${s.color}" stroke="var(--panel)" stroke-width="2"/>`).join("");
        tip.innerHTML = `<b>${esc(spec.xfull(labels[i]))}</b>` + rows.map(([s, p]) => `<div><i style="background:${s.color}"></i>${spec.series.length > 1 ? esc(s.name) + " " : ""}<span class="num">${esc(spec.fmt(p[1]))}</span></div>`).join("");
        tip.style.display = "";
        const left = px + 12 + tip.offsetWidth > W ? px - 12 - tip.offsetWidth : px + 12;
        tip.style.left = left + "px";
      };
      svg.addEventListener("mousemove", (e) => move(e.clientX));
      svg.addEventListener("touchstart", (e) => move(e.touches[0].clientX), { passive: true });
      svg.addEventListener("touchmove", (e) => move(e.touches[0].clientX), { passive: true });
      svg.addEventListener("mouseleave", () => { tip.style.display = "none"; cross.style.display = "none"; dots.innerHTML = ""; });
    });
  }

  const SERIES_COLORS = ["var(--s1)", "var(--s2)", "var(--s3)"];
  function weekLabel(lab) {
    const m = String(lab).match(/(\d{4})년\s*(\d+)월(?:\s*(\d+)주)?/);
    if (!m) return String(lab).slice(-6);
    return m[3] ? `${+m[2]}월 ${m[3]}주` : `${m[1].slice(2)}.${String(m[2]).padStart(2, "0")}`;
  }

  function realestateIndicatorsHTML() {
    const re = XT().realestate, rates = XT().rates?.series || {};
    const parts = [];
    if (re?.series) {
      const per = re.cycle === "MM" ? "월간" : "주간";
      for (const [kind, title] of [["sale", `아파트 매매가격 ${per} 변동률`], ["jeonse", `아파트 전세가격 ${per} 변동률`]]) {
        const ser = re.series[kind] || {};
        const regions = ["전국", "수도권", "서울"].filter((r) => ser[r]?.length);
        if (!regions.length) continue;
        const base = ser[regions[0]];
        const labelOf = Object.fromEntries(regions.flatMap((r) => ser[r].map((p) => [p[0], p[1]])));
        const labels = [...new Set(regions.flatMap((r) => ser[r].map((p) => p[0])))].sort();
        parts.push(chartBox("re_" + kind, {
          title, sub: `한국부동산원 · ${weekLabel(base[base.length - 1][1])} 기준`, zero: true, labels,
          series: regions.map((r, i) => ({ name: r, color: SERIES_COLORS[i], pts: ser[r].map((p) => [p[0], p[2]]) })),
          fmt: (v) => (v > 0 ? "+" : "") + v.toFixed(2) + "%", tfmt: (v) => v.toFixed(2),
          xfmt: (l) => weekLabel(labelOf[l] || l), xfull: (l) => labelOf[l] || l,
        }));
      }
    }
    const reNote = !re?.series ? `<div class="chart ph"><p class="hint">${re?.missing_key
      ? "한국부동산원 인증키(REB_KEY)를 넣으면 주간 아파트 매매·전세 변동률 그래프가 표시돼요."
      : "부동산원 주간 통계를 받는 중이에요. 계속 비어 있으면 하단 '수집 상태'를 확인해 주세요."}</p></div>` : "";
    const one = (k, title, src) => {
      const s = rates[k];
      if (!s?.series?.length) return "";
      const isM = s.cycle === "M";
      return chartBox("rt_" + k, {
        title, sub: `${src} · 최근 ${fmt(s.value, s.digits)}%`, labels: s.series.map((p) => p[0]), height: 150,
        series: [{ name: s.name, color: SERIES_COLORS[0], pts: s.series }],
        fmt: (v) => v.toFixed(s.digits) + "%", tfmt: (v) => v.toFixed(2),
        xfmt: (l) => isM ? `${l.slice(2, 4)}.${l.slice(4, 6)}` : `${l.slice(4, 6)}/${l.slice(6, 8)}`,
        xfull: (l) => isM ? `${l.slice(0, 4)}년 ${+l.slice(4, 6)}월` : `${l.slice(0, 4)}.${l.slice(4, 6)}.${l.slice(6, 8)}`,
      });
    };
    const small = one("mortgage", "주택담보대출 금리 (신규취급)", "한국은행·월별") + one("ktb3", "국고채 3년", "한국은행·일별");
    const smallNote = !small ? `<div class="chart ph"><p class="hint">${XT().rates?.missing_key ? "한국은행 인증키(ECOS_KEY)를 넣으면 주담대 금리·국고채 그래프가 표시돼요." : "금리 자료를 받는 중이에요."}</p></div>` : "";
    return `<section class="panel re-ind" aria-label="부동산 지표">
      <div class="list-head"><div><h1>부동산 지표</h1><p>집값 흐름과 대출 금리를 한눈에 · 그래프에 마우스를 올리면 시점별 값이 보여요</p></div></div>
      <div class="charts2">${parts.join("") || reNote}</div>
      <div class="charts2">${small || smallNote}</div>
    </section>`;
  }

  // ───────── 하루 흐름 (타임라인) ─────────
  S.tl = { dates: null, days: {}, sel: null, loading: false };
  async function loadTimeline(date) {
    if (S.tl.loading) return;
    S.tl.loading = true;
    try {
      const wasLatest = !S.tl.sel || (S.tl.dates && S.tl.sel === S.tl.dates[0]);
      // 날짜 목록은 매번 새로 받아 자정이 지나면 새 날짜가 생기게
      S.tl.dates = (await getJSON(`${BASE}timeline/index.json?t=${Math.floor(Date.now() / 60e3)}`)).dates || [];
      const d = date || (wasLatest ? S.tl.dates[0] : S.tl.sel);
      if (d) {
        const latest = S.tl.dates[0] === d;
        if (!S.tl.days[d] || latest) {
          const day = await getJSON(`${BASE}timeline/${d}.json?t=${latest ? Math.floor(Date.now() / 60e3) : 0}`);
          S.tl.days[d] = day;
        }
        if (!date || S.tl.want === d || !S.tl.want) S.tl.sel = d;
      }
      S.tl.err = null;
    } catch (e) { S.tl.err = e.message; }
    finally { S.tl.loading = false; }
    if (S.tab === "timeline") renderMain();
  }
  const bgr = (t) => { const s = String(t).toLowerCase().replace(/[^가-힣a-z0-9]/g, ""); const o = new Set(); for (let i = 0; i < s.length - 1; i++) o.add(s.slice(i, i + 2)); return o; };
  const jac = (a, b) => { let c = 0; a.forEach((x) => { if (b.has(x)) c++; }); return c / Math.max(1, a.size + b.size - c); };
  const toMin = (t) => +t.slice(0, 2) * 60 + +t.slice(3, 5);
  const dur = (a, b) => { const d = toMin(b) - toMin(a) + 10; return d >= 60 ? `${Math.floor(d / 60)}시간${d % 60 ? " " + (d % 60) + "분" : ""}` : `${d}분`; };

  function segmentsOf(slots, tab) {
    const segs = [];
    for (const s of slots) {
      const top = s.tabs?.[tab]?.[0];
      if (!top) continue;
      const cur = segs[segs.length - 1];
      if (cur && jac(cur.bg, bgr(top.title)) >= 0.45) { cur.end = s.t; cur.max = Math.max(cur.max, top.count || 0); }
      else segs.push({ start: s.t, end: s.t, title: top.title, link: top.link, outlet: top.outlet, max: top.count || 0, bg: bgr(top.title) });
    }
    return segs.reverse();
  }

  function timelineHTML() {
    if (!S.tl.dates && !S.tl.err) { loadTimeline(); return `<div class="loading"><div class="skel w60"></div><div class="skel w80"></div></div>`; }
    if (!S.tl.dates?.length) {
      if (!S.tl.retryAt || Date.now() > S.tl.retryAt) { S.tl.retryAt = Date.now() + 60e3; S.tl.err = null; setTimeout(() => loadTimeline(), 0); }
      return `<div class="banner info">하루 흐름 기록이 아직 없어요. 수집기가 10분마다 기록을 쌓기 시작하면 여기에 표시돼요.</div>`;
    }
    if (!S.tl.sel) S.tl.sel = S.tl.dates[0];
    const day = S.tl.days[S.tl.sel];
    const chips = (S.tl.dates || []).slice(0, 14).map((d) => `<button class="chip" data-tl="${d}" aria-pressed="${d === S.tl.sel}">${d.slice(5).replace("-", "/")}${d === S.tl.dates[0] ? " 오늘" : ""}</button>`).join("");
    if (!day && S.tl.err) return `<section class="panel"><div class="list-head"><div class="chips">${chips}</div></div><div class="empty">이 날짜 기록을 불러오지 못했어요. 잠시 뒤 다시 눌러 보세요.</div></section>`;
    if (!day) { loadTimeline(S.tl.sel); return `<section class="panel"><div class="list-head"><div class="chips">${chips}</div></div><div class="empty">불러오는 중이에요.</div></section>`; }
    const slots = day.slots || [];
    const cols = ["economy", "stocks", "realestate", "ipo", "ib", "funding"].map((t) => {
      const segs = segmentsOf(slots, t);
      return `<div class="tl-col"><h2 class="sec">${TABS[t]} 1위 변화 <small>${segs.length}번 바뀜</small></h2>
        ${segs.length ? `<ol class="tl">${segs.map((g) => `<li><span class="tm num">${g.start}${g.end !== g.start ? "–" + g.end : ""}<small>${dur(g.start, g.end)}</small></span>
          <div>${qbtn({ title: g.title, link: g.link, outlet: g.outlet, _tl: true, _tabLabel: TABS[t] }, g.title)}<div class="meta"><span class="src">${esc(g.outlet || "")}</span>${g.max ? `<span>최대 ${g.max}개 매체</span>` : ""}</div></div></li>`).join("")}</ol>`
          : `<p class="hint">기록이 없어요.</p>`}</div>`;
    }).join("");
    const first = slots[0], last = slots[slots.length - 1];
    const mk = (k, nm, d) => first?.mkt?.[k] != null && last?.mkt?.[k] != null
      ? `<span><strong>${nm}</strong><span class="num">${fmt(first.mkt[k], d)} → ${fmt(last.mkt[k], d)}</span> <span class="num ${dir(last.mkt[k] - first.mkt[k])}">${pct((last.mkt[k] / first.mkt[k] - 1) * 100)}</span></span>` : "";
    return `<section class="panel">
      <div class="list-head"><div><h1>하루 흐름</h1><p>10분마다 기록한 분야별 1위 이슈가 언제 바뀌었는지 보여 줘요 · ${slots.length ? `${first.t}~${last.t} 기록` : "기록 없음"}</p></div><div class="chips">${chips}</div></div>
      <div class="summary-line" style="margin:0;border-top:1px solid var(--line)">${mk("kospi", "코스피", 2)}${mk("kosdaq", "코스닥", 2)}${mk("usdkrw", "원/달러", 2)}</div>
      <div class="tl-grid">${cols}</div>
    </section>`;
  }

  // ───────── 빠른 보기 창 ─────────
  function findIssue(o) {
    // 타임라인 등 일부 정보만 있는 항목은 현재 이슈 목록에서 같은 기사/비슷한 제목을 찾아 보강
    if (o.articles) return o;
    const all = ["economy", "stocks", "realestate", "ipo", "ib", "funding"].flatMap((t) => (S.snap?.tabs[t]?.issues || []).map((i) => ({ ...i, _tabLabel: TABS[t] })));
    const b = bgr(o.title);
    return all.find((i) => i.link === o.link || i.articles.some((a) => a.link === o.link))
      || all.find((i) => jac(bgr(i.title), b) >= 0.5) || o;
  }

  function openQV(o, opener) {
    if (!o) return;
    const iss = o._policy ? o : { ...findIssue(o), _tabLabel: o._tabLabel || findIssue(o)._tabLabel, _rank: o._rank };
    const arts = iss.articles?.length ? iss.articles : [{ title: iss.title, link: iss.link, outlet: iss.outlet, ts: iss.ts }];
    const body = iss._policy ? (iss.lede || "") : (iss.summary || "");
    const paras = body ? body.split(/\n\n+/) : [];
    const src = iss._policy ? iss.lede_src : iss.summary_src;
    const main = arts.find((a) => src && a.link === src.link) || arts.find((a) => a.link === iss.link) || arts[0];
    const others = arts.filter((a) => a !== main);
    const chips = [iss._tabLabel, iss._rank ? `${iss._rank}위` : "", iss.count ? `${iss.count}개 매체 보도` : "", ...(iss.reasons || []).slice(1)]
      .filter(Boolean).map((c) => `<span>${esc(c)}</span>`).join("");
    $("#qvBody").innerHTML = `
      <div class="why qv-chips">${chips}</div>
      <h2 id="qvTitle">${esc(iss.title)}</h2>
      <div class="meta"><span class="src">${esc(iss.outlet || "")}</span>${iss.ts ? `<span>${ago(iss.ts)}</span>` : ""}</div>
      <div class="qv-sum">
        ${paras.length ? paras.map((p) => `<p>${esc(p)}</p>`).join("")
          : `<p class="hint">${iss._policy ? esc(iss.summary || "") + (iss.summary ? " · " : "") + "요약을 준비하는 중이에요. 1~2분 뒤 다시 열면 보일 수 있어요." : "요약을 준비하는 중이에요. 원문을 여는 데 1~2분 걸릴 수 있고, 일부 언론사는 원문을 열 수 없어 아래 출처 링크로만 제공돼요."}</p>`}
      </div>
      <div class="qv-src">
        <h3>출처</h3>
        <a class="qv-main" ${ext(main.link)}><b>${esc(main.outlet || "")}</b><span>${esc(main.title)}</span><em>원문 보기 ↗</em></a>
        ${src && paras.length ? `<p class="hint qv-note">위 요약은 ${esc(src.outlet)} 기사 앞부분을 정리한 내용이에요.</p>` : ""}
        ${others.length ? `<h3>같은 이슈 다른 보도 <small>${others.length}건</small></h3>
          <ul class="qv-list">${others.map((a) => `<li><a ${ext(a.link)}><b>${esc(a.outlet)}</b><span>${esc(a.title)}</span>${a.ts ? `<small>${ago(a.ts)}</small>` : ""}</a></li>`).join("")}</ul>` : ""}
      </div>`;
    const qv = $("#qv");
    S.qvOpener = opener;
    qv.hidden = false;
    document.body.classList.add("qv-open");
    requestAnimationFrame(() => qv.classList.add("on"));
    $("#qv .qv-x").focus();
  }

  function closeQV() {
    const qv = $("#qv");
    if (qv.hidden) return;
    qv.classList.remove("on");
    document.body.classList.remove("qv-open");
    setTimeout(() => qv.classList.remove("fb"), 180);
    setTimeout(() => { qv.hidden = true; }, 180);
    S.qvOpener?.focus?.();
  }
  $("#qv").addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closeQV(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeQV(); });

  // ───────── 불편사항 접수 (사이트 안에서 바로 전송) ─────────
  // 보낸 내용은 관리자의 구글 설문 응답함으로 조용히 저장됨 (이용자에게는 구글 화면이 보이지 않음)
  function openFeedback() {
    const cfg = window.MP_FEEDBACK || {};
    const ready = cfg.form && cfg.field;
    $("#qvBody").innerHTML = `
      <h2 id="qvTitle">불편사항·의견 보내기</h2>
      <p class="hint">불편했던 점, 있었으면 하는 기능을 편하게 적어 주세요. <b>익명으로 접수돼요</b> — 이름·이메일·로그인 정보는 받지 않고, 내용은 사이트 관리자에게만 전달돼요.</p>
      <form id="fbForm" class="fb-form" novalidate>
        <label for="fbText">내용</label>
        <textarea id="fbText" rows="7" maxlength="2000" placeholder="예) IPO 탭에 해외 기사가 너무 많아요 / 모바일에서 글자가 작아요" required></textarea>
        <input id="fbHp" type="text" tabindex="-1" autocomplete="off" class="fb-hp" aria-hidden="true">
        <div class="fb-row">
          <span class="hint" id="fbMsg">${ready ? "" : "관리자가 아직 접수함을 연결하지 않았어요."}</span>
          <button type="submit" class="fb-send" ${ready ? "" : "disabled"}>보내기</button>
        </div>
      </form>`;
    const qv = $("#qv");
    qv.hidden = false; qv.classList.add("fb");
    document.body.classList.add("qv-open");
    requestAnimationFrame(() => qv.classList.add("on"));
    setTimeout(() => $("#fbText")?.focus(), 50);

    $("#fbForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const text = $("#fbText").value.trim(), msg = $("#fbMsg"), btn = $("#fbForm .fb-send");
      if ($("#fbHp").value) return;                       // 자동 스팸 차단
      if (text.length < 2) { msg.textContent = "내용을 적어 주세요."; $("#fbText").focus(); return; }
      let last = 0; try { last = +localStorage.getItem("mp-fb-last") || 0; } catch (err) { /* 무시 */ }
      if (Date.now() - last < 30e3) { msg.textContent = "방금 보내셨어요. 30초 뒤에 다시 보낼 수 있어요."; return; }
      const where = `[${TABS[S.tab] || S.tab} 탭 · ${innerWidth < 600 ? "모바일" : "PC"} · ${new Date().toLocaleString("ko-KR")}]`;
      const body = new URLSearchParams();
      body.append(cfg.field, `${text}\n\n${where}`);
      btn.disabled = true; msg.textContent = "보내는 중…";
      try {
        await fetch(cfg.form, { method: "POST", mode: "no-cors", body });   // 구글 설문은 응답 내용을 돌려주지 않음(no-cors)
        try { localStorage.setItem("mp-fb-last", String(Date.now())); } catch (err) { /* 무시 */ }
        $("#fbForm").innerHTML = `<div class="fb-done"><b>보내 주셔서 고마워요.</b><p class="hint">관리자에게 전달됐어요. 반영되면 사이트에 바로 적용할게요.</p></div>`;
      } catch (err) {
        btn.disabled = false;
        msg.textContent = "전송에 실패했어요. 인터넷 연결을 확인하고 다시 눌러 주세요.";
      }
    });
  }
  $("#feedbackBtn").addEventListener("click", openFeedback);

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
    const q = e.target.closest("[data-q]");
    if (q) { openQV(S.q[+q.dataset.q], q); return; }
    const s = e.target.closest("[data-sub]");
    if (s) { S.sub = s.dataset.sub; renderMain(); return; }
    const ibs = e.target.closest("[data-ibsub]");
    if (ibs) { S.ibSub = ibs.dataset.ibsub; renderMain(); return; }
    const tl = e.target.closest("[data-tl]");
    if (tl) { S.tl.want = S.tl.sel = tl.dataset.tl; loadTimeline(tl.dataset.tl); renderMain(); return; }
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

  let rT; window.addEventListener("resize", () => { clearTimeout(rT); rT = setTimeout(drawCharts, 150); });
  if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("sw.js").catch(() => {});

  // ───────── 시작 ─────────
  S.tab = TABS[location.hash.slice(1)] ? location.hash.slice(1) : "briefing";
  renderTabs();
  poll(true).finally(schedule);
})();
