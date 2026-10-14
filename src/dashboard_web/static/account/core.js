// account/core.js — /a/{id} 한 페이지의 뼈대: 전역 ACC · 라우터 · 계좌 읽기 ·
// 시세 데우기 · 잠금(PIN 게이트) · 상단 바/하단 탭.
//
// 계좌 화면 계약 (계좌·종목 담당 공용)
//   ACC.id                       주소의 계좌 id
//   ACC.meta                     공개 목록의 그 계좌 행 {name, broker, account_type, has_pin} (잠겨 있어도 있음)
//   ACC.data                     마지막 GET /api/accounts/{id} 응답 + core가 붙인 것(아래), 읽기 전엔 null
//   ACC.state                    'loading' | 'ready' | 'locked' | 'error' | 'not_found'
//   ACC.reload({quiet}) → Promise<data>   다시 읽고 'data' 알림
//   ACC.on(evt, fn) → off()      evt: 'data' (data) · 'route' (params) · 'warm' (warm) · 'meta' (공개 목록 행이 늦게 왔을 때)
//   ACC.go(params, {replace})    주소 쿼리를 params로 바꾸고 보기 전환 (뒤로 가기 복원)
//   ACC.params()                 지금 쿼리 {s, v, since, until, chat, settings, …}
//   ACC.views[name] = { mount(root, params), unmount(), update?(params) }   name: account · stock · trades
//   ACC.setChrome({ title, back })  390 상단 바 제목·뒤로 (종목·거래 이력 보기가 mount에서 부른다)
//   ACC.held(h) · ACC.region(sid) · ACC.holding(sid) · ACC.market(region) · ACC.lock() · ACC.toast(msg, opts)
//
// core가 ACC.data에 붙이는 것 (시세 판정, 인계 §3.2·§3.3)
//   data.markets[region] = { phase, session, today, label: S.pnlLabel(...) }   region 'KR'|'US'
//   data.warm = { state: 'idle'|'warming'|'ok'|'error', at }
//   holding.region · holding.code · holding.held (qty>0) · holding.q = S.quoteState(...)
//
// 다른 파일이 정의하는 것: ACC.openTrade · ACC.openHoldFix (종목 담당),
//   ACC.openAdd · ACC.openCash · ACC.confirm · ACC.chat · ACC.openSettings (계좌 담당)
(function () {
  'use strict';
  const S = window.S;
  const { el, clear } = S;
  const WARM_MAX_AGE = 300;          // 열 때 데우기: 5분 안의 시세면 그대로
  const POLL_MS = 5 * 60 * 1000;     // 장중 재수신 간격
  const RECHECK_MS = 30 * 1000;      // 탭 다시 보임 재확인 최소 간격

  const id = decodeURIComponent((location.pathname.split('/')[2] || '').trim());
  const listeners = {};
  const sessions = {};               // region → 'YYYY-MM-DD' (대표 종목의 마지막 일봉)
  let current = null;                // { name, view }
  let chromeEls = null;
  let chromeOpts = {};
  let warm = { state: 'idle', at: null };
  let poller = null;
  let gateShown = false;
  let lastCheck = 0;
  let loadSeq = 0;

  const ACC = window.ACC = {
    id,
    meta: null,
    data: null,
    state: 'loading',
    views: {},
    panels: {},
  };

  // ── events ─────────────────────────────────────────────────────────
  ACC.on = function (evt, fn) {
    (listeners[evt] = listeners[evt] || []).push(fn);
    return () => ACC.off(evt, fn);
  };
  ACC.off = function (evt, fn) {
    const l = listeners[evt] || [];
    const i = l.indexOf(fn);
    if (i >= 0) l.splice(i, 1);
  };
  function emit(evt, arg) {
    (listeners[evt] || []).slice().forEach(fn => {
      try { fn(arg); } catch (e) { console.error(e); }
    });
  }

  // ── small helpers ──────────────────────────────────────────────────
  ACC.region = sid => String(sid || '').split(':')[0] || 'KR';
  ACC.held = h => !!h && h.qty != null && +h.qty > 0;
  ACC.holding = sid => (ACC.data && ACC.data.holdings || []).find(h => h.symbol_id === sid) || null;
  ACC.market = region => (ACC.data && ACC.data.markets && ACC.data.markets[region]) || marketInfo(region);
  ACC.toast = (msg, opts) => S.toast(msg, (opts || {}).kind, opts || {});
  ACC.path = rest => '/api/accounts/' + encodeURIComponent(id) + (rest || '');
  ACC.typeLabel = t => (window.AccountsMenu ? window.AccountsMenu.typeLabel(t) : t);
  ACC.sub = a => (a ? [a.broker, ACC.typeLabel(a.account_type)].filter(Boolean).join(' · ') : '');
  ACC.accountRef = () => ({ id, name: (ACC.data && ACC.data.account && ACC.data.account.name) || (ACC.meta && ACC.meta.name) || id });

  function marketInfo(region) {
    const phase = S.marketPhase(region);
    const today = S.marketToday(region);
    const session = sessions[region] || null;
    return { phase, today, session, label: S.pnlLabel({ phase, session, today }) };
  }

  // ── routing ────────────────────────────────────────────────────────
  ACC.params = function () {
    const out = {};
    new URLSearchParams(location.search).forEach((v, k) => { out[k] = v; });
    return out;
  };
  function viewName(p) {
    if (p.v === 'trades') return 'trades';
    if (p.s) return 'stock';
    return 'account';
  }
  function query(params) {
    const q = new URLSearchParams();
    Object.keys(params || {}).forEach(k => {
      const v = params[k];
      if (v != null && v !== '' && v !== false) q.set(k, v === true ? '1' : String(v));
    });
    const s = q.toString().replace(/%3A/gi, ':');      // ?s=KR:005930 stays readable
    return location.pathname + (s ? '?' + s : '');
  }
  ACC.go = function (params, opts) {
    opts = opts || {};
    const next = opts.merge ? Object.assign(ACC.params(), params) : (params || {});
    const url = query(next);
    if (url !== location.pathname + location.search) {
      if (opts.replace) history.replaceState({ acc: 1 }, '', url);
      else history.pushState({ acc: 1 }, '', url);
    }
    route();
    if (!opts.keepScroll) window.scrollTo(0, 0);
  };
  window.addEventListener('popstate', () => route());

  function main() { return document.getElementById('main'); }

  function unmountCurrent() {
    if (!current) return;
    try { if (current.view && current.view.unmount) current.view.unmount(); } catch (e) { console.error(e); }
    current = null;
  }

  function route() {
    const params = ACC.params();
    if (ACC.state === 'locked' || ACC.state === 'error' || ACC.state === 'not_found') {
      emit('route', params);
      return;
    }
    const name = viewName(params);
    const view = ACC.views[name] || ACC.views.account;
    if (current && current.name === name && current.view === view && view.update) {
      view.update(params);
    } else {
      unmountCurrent();
      const root = main();
      clear(root);
      root.removeAttribute('aria-busy');
      chromeOpts = {};
      current = { name, view };
      renderChrome();
      if (view) {
        try { view.mount(root, params); } catch (e) { console.error(e); }
      }
    }
    syncPanels(params);
    emit('route', params);
  }

  // Panels (채팅·설정) follow the query too: ?chat=1 · ?settings=1.
  function syncPanels(params) {
    Object.keys(ACC.panels).forEach(k => {
      const pnl = ACC.panels[k];
      const want = !!params[k];
      try {
        if (want && !pnl.isOpen()) pnl.open(params);
        else if (!want && pnl.isOpen()) pnl.close({ fromRoute: true });
      } catch (e) { console.error(e); }
    });
  }
  // A panel opened by a person pushes ?{key}=1 so 뒤로 가기 closes it.
  ACC.openPanel = function (key, extra) {
    const p = Object.assign(ACC.params(), { [key]: '1' }, extra || {});
    ACC.go(p, { keepScroll: true });
  };
  ACC.closePanel = function (key) {
    if (!ACC.params()[key]) return;
    if (history.state && history.state.acc) history.back();
    else {
      const p = ACC.params();
      delete p[key];
      ACC.go(p, { replace: true, keepScroll: true });
    }
  };

  // ── chrome (top bar · bottom tab) ──────────────────────────────────
  ACC.setChrome = function (o) {
    chromeOpts = Object.assign({}, o || {});
    renderChrome();
  };
  function accountsMenu(btn) {
    if (window.AccountsMenu) window.AccountsMenu.open(btn, { current: id });
    else location.assign('/');
  }
  function accountMenu(btn) {
    S.menu(btn, [
      { label: '설정', onSelect: () => ACC.openSettings && ACC.openSettings() },
      { label: S.LABELS.lockDevice, icon: 'lock', onSelect: () => ACC.lock() },
    ]);
  }
  function renderChrome() {
    const name = current ? current.name : 'account';
    const open = ACC.state === 'ready' || ACC.state === 'loading';
    const ref = { id, name: ACC.accountRef().name };
    const sub = name !== 'account' && open;
    const bar = S.topBar({
      active: 'account',
      account: ACC.state === 'not_found' ? null : ref,
      title: sub ? chromeOpts.title || null : null,
      back: sub ? (chromeOpts.back || (() => ACC.go({}))) : null,
      onAccounts: accountsMenu,
      onChat: open && ACC.state === 'ready' ? () => ACC.chat && ACC.chat.open() : null,
      onMenu: open && ACC.state === 'ready' ? accountMenu : null,
      actions: chromeOpts.actions || [],
      wideActions: chromeOpts.wideActions || [],
    });
    if (!chromeEls) {
      chromeEls = S.chrome({ topBar: {}, bottomTab: 'account',
        bottomTabOpts: { accountHref: '/a/' + encodeURIComponent(id), onAccounts: accountsMenu } });
    }
    chromeEls.topBar.replaceWith(bar);
    chromeEls.topBar = bar;
    const showTab = !sub;
    chromeEls.bottomTab.hidden = !showTab;
    document.body.classList.toggle('has-bottomtab', showTab);
  }

  // ── data ───────────────────────────────────────────────────────────
  function enrich(d) {
    if (!d) return d;
    const regions = new Set((d.holdings || []).map(h => ACC.region(h.symbol_id)));
    Object.keys(d.totals || {}).forEach(c => regions.add(c === 'USD' ? 'US' : 'KR'));
    if (!regions.size) regions.add(((d.account || {}).base_currency === 'USD') ? 'US' : 'KR');
    d.markets = {};
    regions.forEach(r => { d.markets[r] = marketInfo(r); });
    (d.holdings || []).forEach(h => {
      const r = ACC.region(h.symbol_id);
      const m = d.markets[r];
      h.region = r;
      if (!h.code) h.code = String(h.symbol_id).split(':')[1] || '';
      h.held = ACC.held(h);
      h.q = S.quoteState({ quoteTs: h.quote_ts, market: r, session: m.session, phase: m.phase });
    });
    d.warm = warm;
    return d;
  }

  // The last trading day per market: the newest daily bar of one held symbol
  // (인계 §3.2 — quote_ts is when a quote arrived, not a trading day).
  async function loadSessions(d) {
    const pick = {};
    (d.holdings || []).forEach(h => {
      const r = ACC.region(h.symbol_id);
      if (!pick[r] || (!ACC.held(pick[r]) && ACC.held(h))) pick[r] = h;
    });
    const jobs = Object.keys(pick).filter(r => !sessions[r]).map(async r => {
      const [m, c] = pick[r].symbol_id.split(':');
      try {
        const res = await S.api('/api/market/ohlcv/' + encodeURIComponent(m) + '/' + encodeURIComponent(c) + '?n=1',
          { toast: false, gate: false });
        const b = (res.bars || [])[res.bars.length - 1];
        if (b && b.ts) sessions[r] = barDate(b.ts, r);
      } catch (e) { /* the label falls back to today */ }
    });
    await Promise.all(jobs);
    return jobs.length > 0;
  }
  // Daily bars are stamped at the session's local midnight (KR) or UTC
  // midnight (US); read the date in UTC+9 for KR and UTC for US so both
  // land on their own calendar day.
  function barDate(ts, region) {
    const shift = region === 'KR' ? 9 * 3600 : 0;
    return new Date((ts + shift) * 1000).toISOString().slice(0, 10);
  }

  async function fetchMeta() {
    if (ACC.meta) return ACC.meta;
    try {
      const r = await S.api('/api/accounts', { toast: false, gate: false });
      ACC.meta = (r.accounts || []).find(a => a.account_id === id) || null;
    } catch (e) { ACC.meta = null; }
    return ACC.meta;
  }

  ACC.reload = async function (opts) {
    opts = opts || {};
    const seq = ++loadSeq;
    let d;
    try {
      d = await S.api(ACC.path(''), { toast: false });
    } catch (err) {
      if (seq !== loadSeq) return ACC.data;
      if (err.forbidden) return ACC.data;                  // S.onForbidden already put up the gate
      if (err.status === 403 && /PIN을 먼저/.test(err.message || '')) { fetchMeta().then(() => showGate({ noPin: true })); return null; }
      if (err.status === 404) { showProblem('not_found'); return null; }
      if (opts.quiet && ACC.data) { ACC.toast('계좌를 다시 읽지 못했어요', { kind: 'err' }); return ACC.data; }
      showProblem('error', err);
      return null;
    }
    if (seq !== loadSeq) return ACC.data;
    lastCheck = Date.now();
    const wasReady = ACC.state === 'ready';
    ACC.data = enrich(d);
    ACC.state = 'ready';
    gateShown = false;
    document.body.classList.remove('is-locked');
    S.rememberAccount(id);
    if (!wasReady) { renderChrome(); route(); }
    else if (!opts.quiet) renderChrome();
    emit('data', ACC.data);
    if (await loadSessions(d)) {
      if (ACC.data === d) { enrich(d); emit('data', d); }
    }
    return ACC.data;
  };

  // ── 시세 데우기 (인계 §3.3) ────────────────────────────────────────
  function setWarm(state) {
    warm = { state, at: Date.now() };
    if (ACC.data) ACC.data.warm = warm;
    emit('warm', warm);
  }
  // ACC.warm()            열 때: 모든 보유, 5분 안의 시세면 그대로 ('시세 받는 중')
  // ACC.warm(true)        장중 5분마다: 열린 시장 종목만, 조용히
  // ACC.refreshQuotes()   ↻: 모든 보유를 새로 ('시세 받는 중')
  ACC.warm = async function (onlyOpen, maxAge) {
    const d = ACC.data;
    if (!d) return;
    let hs = (d.holdings || []);
    if (onlyOpen) hs = hs.filter(h => S.marketPhase(ACC.region(h.symbol_id)) === 'open');
    const syms = hs.map(h => h.symbol_id);
    if (!syms.length) { if (!onlyOpen) setWarm('ok'); return; }
    if (!onlyOpen) setWarm('warming');
    try {
      await S.api('/api/market/quotes?symbols=' + encodeURIComponent(syms.join(',')) +
        '&max_age_s=' + (maxAge != null ? maxAge : onlyOpen ? 60 : WARM_MAX_AGE), { toast: false });
    } catch (err) {
      if (err.forbidden) { stopPoll(); return; }
      if (!onlyOpen) setWarm('error');
      return;
    }
    await ACC.reload({ quiet: true });
    if (!onlyOpen || warm.state !== 'ok') setWarm('ok');
  };
  ACC.refreshQuotes = () => (warm.state === 'warming' ? Promise.resolve() : ACC.warm(false, 10));
  function startPoll() {
    if (poller) return;
    poller = S.whenVisible(() => {
      if (ACC.state !== 'ready' || !ACC.data) return;
      const anyOpen = Object.keys(ACC.data.markets || {}).some(r => S.marketPhase(r) === 'open');
      if (anyOpen) ACC.warm(true);
    }, POLL_MS);
  }
  function stopPoll() { if (poller) { poller(); poller = null; } }

  // ── 잠금 ───────────────────────────────────────────────────────────
  function showGate(o) {
    o = o || {};
    stopPoll();
    unmountCurrent();
    closeLayers();
    ACC.state = 'locked';
    ACC.data = null;
    renderChrome();
    const a = ACC.meta || {};
    const noPin = o.noPin || (ACC.meta && ACC.meta.has_pin === false);
    if (gateShown && !noPin) return;
    gateShown = true;
    S.pinGate({
      account: { id, name: a.name || id, broker: a.broker, account_type: ACC.typeLabel(a.account_type) },
      mount: main(),
      noPin,
      onUnlock: () => { gateShown = false; ACC.state = 'loading'; current = null; boot(); },
      onOther: () => window.AccountsMenu ? window.AccountsMenu.open(null, { current: id }) : location.assign('/'),
    });
  }
  function closeLayers() {
    Object.keys(ACC.panels).forEach(k => { try { ACC.panels[k].close({ fromRoute: true }); } catch (e) { /* */ } });
    document.querySelectorAll('.s-scrim, .s-menu-scrim').forEach(n => n.remove());
    document.body.classList.remove('has-layer', 'has-panel');
  }
  S.onForbidden = () => { fetchMeta().then(() => showGate()); };

  ACC.lock = async function () {
    try {
      await S.api(ACC.path('/lock'), { method: 'POST', toast: false, gate: false });
    } catch (e) {
      ACC.toast('잠그지 못했어요 · 잠시 뒤 다시 해 주세요', { kind: 'err' });
      return;
    }
    location.replace('/?locked=' + encodeURIComponent(ACC.id));
  };

  // 잠근 뒤 뒤로 가기(bfcache)·탭 다시 보임: 세션이 아직 있는지 다시 묻는다.
  function recheck() {
    if (ACC.state !== 'ready') return;
    if (Date.now() - lastCheck < RECHECK_MS) return;
    ACC.reload({ quiet: true });
  }
  window.addEventListener('pageshow', e => { if (e.persisted) { lastCheck = 0; recheck(); } });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) recheck(); });

  // ── page-level problems (계좌를 못 읽음 · 없는 계좌) ─────────────────
  let metaTried = false;
  function showProblem(kind, err) {
    if (!ACC.meta && !metaTried && kind !== 'not_found') {
      metaTried = true;
      fetchMeta().then(() => showProblem(kind, err));
      return;
    }
    stopPoll();
    unmountCurrent();
    ACC.state = kind;
    ACC.data = null;
    renderChrome();
    const root = main();
    clear(root);
    root.removeAttribute('aria-busy');
    const a = ACC.meta || {};
    root.appendChild(el('div', { className: 'acc-head' }, [
      el('div', { className: 'acc-head-left' }, [
        el('h1', { className: 'acc-name' }, [kind === 'not_found' ? S.LABELS.account : (a.name || id)]),
        kind !== 'not_found' && ACC.sub(a) ? el('span', { className: 'acc-sub' }, [ACC.sub(a)]) : null]),
      kind === 'not_found' ? null : el('div', { className: 'acc-head-right' }, [
        el('span', { className: 'acc-status' }, ['불러오지 못함']),
        el('button', { type: 'button', className: 'btn tb-icon acc-refresh', 'aria-label': S.LABELS.retry,
          onClick: () => { ACC.state = 'loading'; current = null; boot(); } }, [S.svgIco('refresh-cw')])])]));
    const block = kind === 'not_found'
      ? S.stateBlock({ kind: 'info', icon: 'search', level: 'h2', title: '계좌를 찾을 수 없어요',
          body: '주소가 바뀌었거나 삭제된 계좌예요',
          actions: [{ label: '다른 계좌 보기',
            onClick: () => window.AccountsMenu ? window.AccountsMenu.open(null, { current: id }) : location.assign('/') }] })
      : S.stateBlock({ kind: 'error', level: 'h2', title: '계좌를 불러오지 못했어요',
          body: (err && err.network) ? '연결을 확인하고 다시 시도해 주세요' : '잠시 뒤 다시 시도해 주세요',
          actions: [{ label: S.LABELS.retry,
            onClick: () => { ACC.state = 'loading'; current = null; boot(); } }] });
    root.appendChild(el('section', { className: 'card acc-problem' }, [block]));
    emit('data', null);
  }

  // ── boot ───────────────────────────────────────────────────────────
  async function boot() {
    ACC.state = 'loading';
    renderChrome();
    route();                                  // the view draws its loading state (ACC.data == null)
    fetchMeta().then(() => { if (ACC.state !== 'ready') { renderChrome(); emit('meta', ACC.meta); } });
    const d = await ACC.reload();
    if (!d) return;
    ACC.warm(false);
    startPoll();
  }

  if (!id) { location.replace('/'); return; }
  document.addEventListener('DOMContentLoaded', boot);
})();
