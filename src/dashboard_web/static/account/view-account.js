// account/view-account.js — SCR-ACCOUNT 계좌 보기 (기본 보기, 쿼리 없음).
// 요약(총자산·손익·평가손익·예수금·제외 칩) · 기여 상위 3 · 자산 추이 ·
// 구성(도넛 | 지도) · 보유 목록(보유/관심·검색·정렬·거르기·판정 배지·좌표 한 줄) ·
// 통화별 묶음(환율 없음) · 최근 거래 5. 데이터는 ACC.data(core.js)와
// /snapshots · /trades · /api/analysis/summary.
(function () {
  'use strict';
  const S = window.S;
  const ACC = window.ACC;
  const { el, clear, svgIco } = S;
  const NS = 'http://www.w3.org/2000/svg';
  const SORT_KEY = 'pdash.sort';
  const COMP_KEY = 'pdash.compMode';
  const PERIODS = [{ value: '30', label: '1M' }, { value: '90', label: '3M' }, { value: '180', label: '6M' },
    { value: '365', label: '1Y' }, { value: '3650', label: '전체' }];
  const SORTS = [
    { value: 'eval', label: '평가금액' }, { value: 'pnl', label: '손익률' },
    { value: 'day', label: '오늘 등락' }, { value: 'name', label: '이름' }, { value: 'verdict', label: '판정' }];
  const AXES = [{ value: 'name', label: '종목' }, { value: 'market', label: '시장' },
    { value: 'sector', label: '업종' }, { value: 'type', label: '유형' }, { value: 'contrib', label: '기여' }];
  const ARROW = { strong_buy: '▲▲', buy: '▲', neutral: '●', sell: '▼', strong_sell: '▼▼' };

  // ── state (lives while the view is mounted) ─────────────────────────
  let root = null, offs = [], st = null;
  function fresh() {
    return {
      ccy: null, tab: 'held', filter: null, query: '', sort: load(SORT_KEY, 'eval'),
      period: '90', axis: 'name', mode: load(COMP_KEY, 'donut'),
      summary: {}, summaryFor: '', snaps: {}, snapErr: false, trades: null, tradesErr: false,
      els: {},
    };
  }
  function load(k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } }
  function save(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }

  // ── numbers per holding ─────────────────────────────────────────────
  const ccyOf = h => h.currency || 'KRW';
  const regionOfCcy = c => (c === 'USD' ? 'US' : 'KR');
  const evalOf = h => (h.held && h.price != null && h.q && h.q.inTotal ? h.price * h.qty : null);
  const costOf = h => (h.held && h.avg_cost != null ? h.avg_cost * h.qty : null);
  const dayOf = h => (h.held && h.price != null && h.prev_close != null && h.q && h.q.inToday
    ? (h.price - h.prev_close) * h.qty : null);
  const chgPct = h => (h.price != null && h.prev_close ? (h.price / h.prev_close - 1) * 100 : null);
  const pnlPct = h => (h.held && h.price != null && h.avg_cost ? (h.price / h.avg_cost - 1) * 100 : null);
  const nameOf = h => h.name_kr || h.name_en || h.code || h.symbol_id;

  function currencies(d) {
    const out = [];
    Object.keys(d.totals || {}).forEach(c => {
      const t = d.totals[c];
      if (t.n_tracked > 0 || (d.balances && d.balances[c])) out.push(c);
    });
    const base = (d.account && d.account.base_currency) || 'KRW';
    if (!out.length) out.push(base);
    const count = c => (d.holdings || []).filter(h => ccyOf(h) === c && h.held).length;
    out.sort((a, b) => count(b) - count(a) || (a === base ? -1 : b === base ? 1 : a.localeCompare(b)));
    return out;
  }

  // ── tiny DOM helpers ───────────────────────────────────────────────
  function svg(tag, attrs, kids) {
    const n = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(k => { if (attrs[k] != null) n.setAttribute(k, attrs[k]); });
    (kids || []).forEach(k => { if (k) n.appendChild(k); });
    return n;
  }
  function link(params, cls, kids, label) {
    const href = '?' + new URLSearchParams(params).toString().replace(/%3A/gi, ':');
    return el('a', { className: cls, href, 'aria-label': label || null,
      onClick: e => { if (e.metaKey || e.ctrlKey || e.shiftKey) return; e.preventDefault(); ACC.go(params); } }, kids);
  }
  const trendCls = v => ({ up: 't-up', down: 't-down', flat: '' })[S.trend(v)];
  function spark(values, cls) {
    const w = 52, h = 20;
    if (!values || values.length < 2) return el('span', { className: 'spark is-empty ' + (cls || ''), 'aria-hidden': 'true' });
    const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
    const pts = values.map((v, i) => (i / (values.length - 1) * w).toFixed(1) + ',' + (h - 1 - (v - lo) / span * (h - 2)).toFixed(1)).join(' ');
    const dir = S.trend(values[values.length - 1] - values[0]);
    return svg('svg', { 'class': 'spark ' + dir + ' ' + (cls || ''), viewBox: '0 0 ' + w + ' ' + h, width: w, height: h,
      'aria-hidden': 'true', preserveAspectRatio: 'none' }, [svg('polyline', { points: pts })]);
  }
  function chip(text, icon, cls, onClick) {
    const kids = [icon ? svgIco(icon) : null, el('span', {}, [text])];
    return onClick ? el('button', { type: 'button', className: 'btn chip ' + (cls || ''), onClick }, kids)
      : el('span', { className: 'chip ' + (cls || '') }, kids);
  }

  // ── analysis badge (§3.4) + 좌표 한 줄 ─────────────────────────────
  function verdictCell(h, compact) {
    const a = st.summary[h.symbol_id];
    if (!a && st.summaryFor === '') return el('span', { className: 'vd vd-wait' }, ['']);
    const date = a && a.date;
    const as = S.analysisState({ date, held: h.held, status: a && a.status });
    const kids = [];
    const hasLabel = a && a.label && as.state !== 'none' && as.state !== 'facts';
    if (hasLabel && as.state !== 'watch') {
      const tone = S.verdictTone(a.label);
      kids.push(el('span', { className: 'vd-label tone-' + tone }, [
        el('span', { className: 'vd-arrow', 'aria-hidden': 'true' }, [ARROW[a.label] || '●']),
        S.LABEL_KO[a.label] || a.label]));
      if (!compact) {
        if (a.score != null) {
          const bar = el('span', { className: 'vd-score', 'aria-hidden': 'true', title: '점수 ' + a.score },
            [el('i', { className: 'tone-' + tone, style: 'left:' + Math.round((Math.max(-1, Math.min(1, +a.score)) + 1) * 50) + '%' })]);
          kids.push(bar);
        }
        if (a.confidence != null) {
          const n = Math.max(0, Math.min(5, Math.round(+a.confidence * 5)));
          kids.push(el('span', { className: 'vd-dots', title: '확신 ' + Math.round(a.confidence * 100) + '%', 'aria-label': '확신 ' + n + '/5' },
            [0, 1, 2, 3, 4].map(i => el('i', { className: i < n ? 'on' : '' }))));
        }
      }
      kids.push(el('span', { className: 'vd-date' }, [S.fmtDate(date)]));
    }
    if (as.badge) {
      const warn = as.state === 'overdue';
      kids.push(el('span', { className: 'chip vd-badge' + (warn ? ' is-warn' : '') }, [warn ? svgIco('triangle-alert') : null, as.badge]));
    }
    return el('span', { className: 'vd' + (as.state === 'old' ? ' is-old' : '') }, kids);
  }
  function coordLine(h) {
    const a = st.summary[h.symbol_id];
    const c = a && a.coords;
    if (!c) return null;
    const ccy = ccyOf(h);
    if (h.held && h.avg_cost) {
      const part = (label, v) => (v == null ? null : label + ' ' + S.fmtPrice(v, ccy) + ' (' + S.fmtPnlPct((v / h.avg_cost - 1) * 100) + ')');
      const parts = [part('손절', c.stop), part('목표', c.target)].filter(Boolean);
      return parts.length ? '평단 대비 · ' + parts.join(' · ') : null;
    }
    if (!h.held && (c.entry_low != null || c.entry_high != null)) {
      const lo = c.entry_low != null ? S.fmtPrice(c.entry_low, ccy) : '';
      const hi = c.entry_high != null ? S.fmtPrice(c.entry_high, ccy) : '';
      return '진입 기준 · 진입구간 ' + (lo && hi ? lo + '~' + hi : lo || hi);
    }
    return null;
  }

  // ── page head ──────────────────────────────────────────────────────
  function headStatus(d) {
    const w = d.warm || {};
    const hs = (d.holdings || []).filter(h => ccyOf(h) === st.ccy);
    const ts = hs.map(h => h.quote_ts).filter(Boolean);
    const latest = ts.length ? Math.max(...ts.map(t => (typeof t === 'number' ? t : Date.parse(t) / 1000))) : null;
    const delayed = hs.filter(h => h.held && h.q && h.q.state === 'delayed');
    const when = t => (S.fmtDate(t) === S.fmtDate(Date.now() / 1000) ? S.fmtTime(t) : S.fmtDateTime(t));
    if (w.state === 'warming') return { text: '시세 받는 중', busy: true };
    if (w.state === 'error') return { text: '시세 못 받음' + (latest ? ' · ' + when(latest) + ' 기준' : ''), cls: 'is-muted' };
    if (!latest) return { text: hs.length ? '시세 없음' : '' };
    if (delayed.length) {
      const m = Math.max(...delayed.map(h => h.q.minutes));
      return { text: '시세 ' + when(latest) + ' · ' + m + '분 전', cls: 'is-warn' };
    }
    return { text: '시세 ' + when(latest) };
  }
  function pageHead(d) {
    const a = d ? d.account : (ACC.meta || {});
    const name = (a && a.name) || ACC.id;
    const sub = ACC.sub(a || {});
    const right = el('div', { className: 'acc-head-right' });
    if (d) {
      const ccys = currencies(d);
      if (ccys.length > 1) {
        right.appendChild(S.segmented({ items: ccys.map(c => ({ value: c, label: c })), value: st.ccy, label: '통화',
          onChange: v => { st.ccy = v; st.filter = null; render(); } }));
      }
      const s = headStatus(d);
      right.appendChild(el('span', { className: 'acc-status ' + (s.cls || ''), role: 'status' }, [s.text]));
      const busy = s.busy;
      right.appendChild(el('button', { type: 'button', className: 'btn tb-icon acc-refresh' + (busy ? ' is-busy' : ''),
        'aria-label': S.LABELS.refresh, disabled: busy, onClick: () => ACC.refreshQuotes() }, [busy ? S.spinner() : svgIco('refresh-cw')]));
    } else {
      right.appendChild(el('span', { className: 'acc-status' }, ['불러오는 중']));
      right.appendChild(el('button', { type: 'button', className: 'btn tb-icon acc-refresh', 'aria-label': S.LABELS.refresh, disabled: true }, [svgIco('refresh-cw')]));
    }
    return el('div', { className: 'acc-head' }, [
      el('div', { className: 'acc-head-left' }, [el('h1', { className: 'acc-name' }, [name]),
        sub ? el('span', { className: 'acc-sub' }, [sub]) : null]),
      right]);
  }

  // ── summary card ───────────────────────────────────────────────────
  function summaryCard(d) {
    const c = st.ccy;
    const t = (d.totals || {})[c] || {};
    const hs = (d.holdings || []).filter(h => ccyOf(h) === c);
    const held = hs.filter(h => h.held);
    const m = ACC.market(regionOfCcy(c));
    const lab = m.label;
    const cashKnown = d.balances && Object.prototype.hasOwnProperty.call(d.balances, c);
    const nothing = !held.length && !cashKnown;
    const missing = held.filter(h => h.q && h.q.state === 'none');
    const stale = held.filter(h => h.q && h.q.state === 'stale');
    const dayHs = held.filter(h => dayOf(h) != null);
    const dayPnl = dayHs.reduce((s, h) => s + dayOf(h), 0);
    const dayBase = dayHs.reduce((s, h) => s + h.prev_close * h.qty, 0);
    const total = t.total_assets;

    const chips = [];
    if (missing.length) {
      const cost = missing.reduce((s, h) => s + (costOf(h) || 0), 0);
      chips.push(chip('시세 없음 ' + missing.length + '종목 · 총자산에서 제외 ' + S.fmtMoney(cost, c) + '(매입가)', 'triangle-alert', 'acc-chip',
        () => { st.tab = 'held'; st.filter = { kind: 'missing', label: '시세 없음' }; renderList(); scrollToList(); }));
    }
    if (stale.length) {
      chips.push(chip('지난 시세 ' + stale.length + ' · ' + lab.day + ' 손익 제외', 'triangle-alert', 'acc-chip',
        () => { st.tab = 'held'; st.filter = { kind: 'stale', label: '지난 시세' }; renderList(); scrollToList(); }));
    }

    const cell = (label, main, sub, cls, extra) => el('div', { className: 'sum-cell ' + (cls || '') }, [
      el('div', { className: 'sum-label' }, [label, extra || null]), main, sub]);
    const big = (v, cls) => el('div', { className: 'sum-val t-num ' + (cls || '') }, [v]);
    const pct = (v, cls) => el('div', { className: 'sum-pct ' + (cls || '') }, [v]);

    const dayCell = cell(lab.pnl,
      big(dayHs.length ? S.fmtPnl(dayPnl, c) : '—', dayHs.length ? trendCls(dayPnl) : ''),
      dayHs.length ? pct(S.fmtPnlPct(dayBase ? dayPnl / dayBase * 100 : 0, 2), trendCls(dayPnl)) : null, 'is-day');
    if (dayHs.length) {
      dayCell.classList.add('is-click');
      dayCell.tabIndex = 0;
      dayCell.setAttribute('role', 'button');
      dayCell.setAttribute('aria-label', lab.pnl + ' — 구성에서 기여 보기');
      const go = () => { st.axis = 'contrib'; renderComp(); const n = st.els.comp; if (n) n.scrollIntoView({ behavior: 'smooth', block: 'start' }); };
      dayCell.addEventListener('click', go);
      dayCell.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
    }
    const pnlCell = cell('평가손익', big(held.length && t.cost ? S.fmtPnl(t.pnl, c) : '—', held.length ? trendCls(t.pnl) : ''),
      held.length && t.cost ? pct(S.fmtPnlPct(t.pnl_pct), trendCls(t.pnl)) : null);
    const cashBtn = el('button', { type: 'button', className: 'btn sum-edit', 'aria-label': S.LABELS.fixCash,
      onClick: () => ACC.openCash && ACC.openCash(c) }, [svgIco('pencil')]);
    const cashCell = cell('예수금', big(cashKnown ? S.fmtMoney(t.cash || 0, c) : '—'),
      cashKnown ? pct(total ? '총자산의 ' + (Math.round((t.cash || 0) / total * 1000) / 10).toFixed(1) + '%' : '', 'sm-up t-muted')
        : pct('입력 전', 't-muted'), 'is-cash', cashBtn);
    const evalCell = cell('평가금액', big(held.length ? S.fmtMoney(t.eval, c) : '—'), null, 'sm-down');

    const contrib = contribBlock(held, c, lab);
    const card = el('section', { className: 'card acc-sum', 'aria-label': '자산 요약' }, [
      el('div', { className: 'sum-top' }, [
        el('div', { className: 'sum-label' }, ['총자산']),
        el('div', { className: 'sum-total t-num' }, [nothing ? '—' : S.fmtMoney(total, c)]),
        chips.length ? el('div', { className: 'sum-chips' }, chips) : null]),
      el('div', { className: 'sum-grid' }, [dayCell, pnlCell, cashCell, evalCell]),
      contrib,
    ]);
    return card;
  }

  function contribBlock(held, c, lab) {
    const rows = held.filter(h => dayOf(h) != null && dayOf(h) !== 0)
      .sort((a, b) => Math.abs(dayOf(b)) - Math.abs(dayOf(a))).slice(0, 3);
    if (!rows.length) return null;
    const max = Math.max(...rows.map(h => Math.abs(dayOf(h))));
    return el('div', { className: 'sum-contrib' }, [
      el('div', { className: 'contrib-head' }, [el('span', {}, ['기여 상위 3']),
        el('span', {}, [lab.pnl.replace(' 손익', '')])]),
      el('ul', { className: 'contrib-list' }, rows.map(h => {
        const v = dayOf(h);
        return el('li', {}, [link({ s: h.symbol_id }, 'contrib-row', [
          el('span', { className: 'contrib-name ellipsis', title: nameOf(h) }, [nameOf(h)]),
          el('span', { className: 'contrib-track', 'aria-hidden': 'true' }, [
            el('i', { className: v > 0 ? 'up' : 'down', style: 'width:' + Math.max(4, Math.round(Math.abs(v) / max * 100)) + '%' })]),
          el('span', { className: 'contrib-val t-num ' + trendCls(v) }, [S.fmtPnl(v, c)]),
          svgIco('chevron-right'),
        ], nameOf(h) + ' ' + S.fmtPnl(v, c))]);
      })),
    ]);
  }

  // ── asset trend (snapshots) ────────────────────────────────────────
  function trendCard() {
    const box = el('div', { className: 'trend-box' });
    const seg = S.segmented({ items: PERIODS, value: st.period, label: '기간',
      onChange: v => { st.period = v; loadSnaps(); } });
    const card = el('section', { className: 'card acc-trend', 'aria-labelledby': 'trend-h' }, [
      el('div', { className: 'card-head trend-head' }, [
        el('h2', { id: 'trend-h' }, ['자산 추이']),
        el('span', { className: 'trend-legend', 'aria-hidden': 'true' }, [
          el('i', { className: 'lg-total' }), '총자산', el('i', { className: 'lg-base' }), '원금']),
        el('span', { className: 'trend-seg' }, [seg])]),
      box]);
    st.els.trend = box;
    st.els.trendCard = card;
    return card;
  }
  async function loadSnaps() {
    const key = st.ccy + ':' + st.period;
    const box = st.els.trend;
    if (!box) return;
    if (!st.snaps[key]) {
      clear(box);
      box.appendChild(el('div', { className: 'trend-skel skeleton-block' }));
      try {
        const r = await S.api(ACC.path('/snapshots?currency=' + encodeURIComponent(st.ccy) + '&days=' + st.period), { toast: false });
        st.snaps[key] = r.snapshots || [];
      } catch (e) {
        if (e.forbidden) return;
        clear(box);
        box.appendChild(S.stateBlock({ kind: 'error', title: '추이를 불러오지 못했어요',
          actions: [{ label: S.LABELS.retry, onClick: loadSnaps }] }));
        return;
      }
    }
    if (st.ccy + ':' + st.period !== key || !st.els.trend) return;
    drawTrend(st.els.trend, st.snaps[key], st.ccy);
  }
  function drawTrend(box, rows, ccy) {
    clear(box);
    box._draw = () => drawTrend(box, rows, ccy);
    if (!box._ro && window.ResizeObserver) {
      let lastW = box.clientWidth, t = null;
      box._ro = new ResizeObserver(() => {
        if (Math.abs(box.clientWidth - lastW) < 8) return;
        lastW = box.clientWidth;
        clearTimeout(t);
        t = setTimeout(() => { if (box.isConnected && box._draw) box._draw(); }, 120);
      });
      box._ro.observe(box);
    }
    const pts = rows.filter(r => r.total_assets != null);
    if (pts.length < 2) {
      box.appendChild(el('div', { className: 'trend-empty' }, [svgIco('chart-line'), el('span', {}, ['추이는 내일부터 그려져요'])]));
      return;
    }
    const wide = window.matchMedia('(min-width: 600px)').matches;
    const W = Math.max(260, Math.round(box.clientWidth || 640)), H = wide ? 280 : 160, PL = 0, PR = wide ? 64 : 52, PT = 10, PB = 26;
    const base = r => (r.cost || 0) + (r.cash || 0);
    const vals = pts.flatMap(r => [r.total_assets, base(r)]).filter(v => v > 0);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.08 || hi * 0.02 || 1;
    lo -= pad; hi += pad;
    const ticks = niceTicks(lo, hi, 4);
    lo = Math.min(lo, ticks[0]); hi = Math.max(hi, ticks[ticks.length - 1]);
    const x = i => PL + i / (pts.length - 1) * (W - PL - PR);
    const y = v => PT + (1 - (v - lo) / (hi - lo)) * (H - PT - PB);
    const path = f => pts.map((r, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(f(r)).toFixed(1)).join(' ');
    const stepPath = pts.map((r, i) => {
      const yy = y(base(r)).toFixed(1);
      return i ? 'H' + x(i).toFixed(1) + ' V' + yy : 'M' + x(0).toFixed(1) + ' ' + yy;
    }).join(' ');
    const axisFmt = v => (ccy === 'USD' ? '$' + S.fmtCount(Math.round(v)) : S.fmtAxisMan(v));
    const g = svg('g');
    ticks.forEach(t => {
      g.appendChild(svg('line', { 'class': 'tr-grid', x1: PL, x2: W - PR + 8, y1: y(t), y2: y(t) }));
      const tx = svg('text', { 'class': 'tr-ylab', x: W - PR + 12, y: y(t) + 4 });
      tx.textContent = axisFmt(t);
      g.appendChild(tx);
    });
    // x labels: month starts (≥ 60 days) or about 4 dates
    const long = pts.length > 45;
    let lastLabel = '';
    const every = Math.max(1, Math.round(pts.length / 4));
    pts.forEach((r, i) => {
      const mm = +r.date.slice(5, 7), dd = +r.date.slice(8, 10);
      const lab = long ? mm + '월' : mm + '/' + dd;
      const show = long ? lab !== lastLabel && (i > 0 || dd <= 3) : i % every === 0;
      if (long) lastLabel = lab;
      if (!show) return;
      const tx = svg('text', { 'class': 'tr-xlab', x: Math.min(x(i), W - PR - 20), y: H - 6 });
      tx.textContent = lab;
      g.appendChild(tx);
    });
    const last = pts[pts.length - 1];
    const hover = svg('g', { 'class': 'tr-hover' });
    const plot = svg('svg', { 'class': 'trend-svg', viewBox: '0 0 ' + W + ' ' + H, width: W, height: H, role: 'img',
      'aria-label': '자산 추이 ' + S.fmtDate(pts[0].date) + '부터 ' + S.fmtDate(last.date) + '까지, 마지막 ' + S.fmtMoney(last.total_assets, ccy) }, [
      g,
      svg('path', { 'class': 'tr-base', d: stepPath }),
      svg('path', { 'class': 'tr-line', d: path(r => r.total_assets) }),
      svg('circle', { 'class': 'tr-dot', cx: x(pts.length - 1), cy: y(last.total_assets), r: 4 }),
      hover]);
    const tip = el('div', { className: 'trend-tip', role: 'status' });
    tip.hidden = true;
    const wrap = el('div', { className: 'trend-plot' }, [plot, tip]);
    box.appendChild(wrap);
    function show(clientX) {
      const r = plot.getBoundingClientRect();
      const fx = (clientX - r.left) / r.width * W;
      const i = Math.max(0, Math.min(pts.length - 1, Math.round((fx - PL) / (W - PL - PR) * (pts.length - 1))));
      const p = pts[i];
      clear(hover);
      hover.appendChild(svg('line', { 'class': 'tr-cross', x1: x(i), x2: x(i), y1: PT, y2: H - PB }));
      hover.appendChild(svg('circle', { 'class': 'tr-hdot', cx: x(i), cy: y(p.total_assets), r: 4 }));
      hover.appendChild(svg('circle', { 'class': 'tr-hdot is-base', cx: x(i), cy: y(base(p)), r: 4 }));
      clear(tip);
      const row = (cls, label, v, vcls) => el('div', { className: 'tip-row' }, [el('i', { className: cls }), el('span', {}, [label]),
        el('b', { className: 't-num ' + (vcls || '') }, [v])]);
      const pnl = p.pnl != null ? p.pnl : (p.eval || 0) - (p.cost || 0);
      tip.append(el('div', { className: 'tip-date' }, [S.fmtDate(p.date)]),
        row('lg-total', '총자산', S.fmtMoney(p.total_assets, ccy)),
        row('lg-dim', '평가', S.fmtMoney(p.eval, ccy)),
        row('lg-dim', '현금', S.fmtMoney(p.cash, ccy)),
        row('lg-dim', '매입금액', S.fmtMoney(p.cost, ccy)),
        row('lg-dim', '손익', S.fmtPnl(pnl, ccy) + (p.cost ? ' ' + S.fmtPnlPct(pnl / p.cost * 100) : ''), trendCls(pnl)),
        el('div', { className: 'tip-note' }, ['점선 = 매입금액+예수금']));
      tip.hidden = false;
      const left = x(i) / W * r.width;
      tip.classList.toggle('is-left', left > r.width / 2);
      tip.style.cssText = 'left:' + Math.round(left) + 'px';
    }
    function hide() { clear(hover); tip.hidden = true; }
    plot.addEventListener('pointermove', e => show(e.clientX));
    plot.addEventListener('pointerdown', e => show(e.clientX));
    plot.addEventListener('pointerleave', hide);
  }
  function niceTicks(lo, hi, n) {
    const span = hi - lo;
    const raw = span / n;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || raw;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(v);
    return out.length >= 2 ? out : [lo, hi];
  }

  // ── composition (도넛 | 지도) ────────────────────────────────────────
  function groupKey(h, axis) {
    if (axis === 'market') return h.is_etf ? 'ETF' : (h.market || h.region || '기타');
    if (axis === 'sector') return h.sector || (h.is_etf ? 'ETF' : '업종 정보 없음');
    if (axis === 'type') return h.is_etf ? 'ETF' : '주식';
    return h.symbol_id;
  }
  function groups(held, axis) {
    const map = new Map();
    held.forEach(h => {
      const v = evalOf(h);
      if (v == null || v <= 0) return;
      const k = groupKey(h, axis);
      const g = map.get(k) || { key: k, label: axis === 'name' ? nameOf(h) : k, value: 0, day: 0, base: 0, n: 0, hs: [] };
      g.value += v; g.n += 1; g.hs.push(h);
      const dv = dayOf(h);
      if (dv != null) { g.day += dv; g.base += h.prev_close * h.qty; }
      map.set(k, g);
    });
    return [...map.values()].sort((a, b) => b.value - a.value);
  }
  function compCard() {
    const card = el('section', { className: 'card acc-comp', 'aria-labelledby': 'comp-h' });
    st.els.comp = card;
    renderComp();
    return card;
  }
  function renderComp() {
    const card = st.els.comp;
    const d = ACC.data;
    if (!card || !d) return;
    clear(card);
    const held = (d.holdings || []).filter(h => h.held && ccyOf(h) === st.ccy);
    const modeSeg = S.segmented({ items: [{ value: 'donut', label: '도넛' }, { value: 'map', label: '지도' }], value: st.mode,
      label: '구성 보기', onChange: v => { st.mode = v; save(COMP_KEY, v); renderComp(); } });
    const axisSeg = S.segmented({ items: AXES, value: st.axis, label: '구성 기준',
      onChange: v => { st.axis = v; renderComp(); } });
    axisSeg.classList.add('comp-axis');
    card.append(el('div', { className: 'card-head comp-head' }, [el('h2', { id: 'comp-h' }, ['구성']), modeSeg]), axisSeg);
    const body = el('div', { className: 'comp-body' });
    card.appendChild(body);
    if (!held.length) { body.appendChild(el('p', { className: 't-muted comp-none' }, ['보유 종목이 생기면 보여요'])); return; }
    if (st.axis === 'contrib') { body.appendChild(contribBars(held)); return; }
    const gs = groups(held, st.axis);
    if (!gs.length) { body.appendChild(el('p', { className: 't-muted comp-none' }, ['시세가 있는 보유 종목이 없어요'])); return; }
    body.appendChild(st.mode === 'map' ? treemap(gs) : donut(gs, held));
  }
  function applyGroupFilter(g) {
    st.tab = 'held';
    st.filter = st.axis === 'name'
      ? { kind: 'symbol', key: g.key, label: g.label }
      : { kind: 'axis', axis: st.axis, key: g.key, label: g.label };
    renderList();
    if (!window.matchMedia('(min-width: 600px)').matches) scrollToList();
  }
  function donut(gs, held) {
    const total = gs.reduce((s, g) => s + g.value, 0);
    const top = gs.slice(0, 5);
    const rest = gs.slice(5);
    const slices = top.map((g, i) => Object.assign({ color: i + 1 }, g));
    if (rest.length) {
      slices.push({ key: '__rest', label: '기타 ' + rest.reduce((s, g) => s + g.n, 0) + (st.axis === 'name' ? '종목' : '개'),
        value: rest.reduce((s, g) => s + g.value, 0), color: 6, rest: true });
    }
    const R = 42, C = 2 * Math.PI * R;
    let acc = 0;
    const arcs = slices.map(s => {
      const len = s.value / total * C;
      const c = svg('circle', { 'class': 'dn-slice series-' + s.color, cx: 60, cy: 60, r: R,
        'stroke-dasharray': Math.max(0, len - 1.5).toFixed(2) + ' ' + (C - Math.max(0, len - 1.5)).toFixed(2),
        'stroke-dashoffset': (-acc).toFixed(2), transform: 'rotate(-90 60 60)' });
      acc += len;
      return c;
    });
    const nStocks = held.filter(h => evalOf(h) != null).length;
    const ring = el('div', { className: 'dn-ring' }, [
      svg('svg', { viewBox: '0 0 120 120', 'class': 'dn-svg', role: 'img', 'aria-label': '구성 도넛' }, arcs),
      el('div', { className: 'dn-center' }, [el('b', { className: 't-num' }, [String(st.axis === 'name' ? nStocks : gs.length)]),
        el('span', {}, [st.axis === 'name' ? '종목' : AXES.find(a => a.value === st.axis).label])])]);
    const legend = el('ul', { className: 'dn-legend' }, slices.map(s => el('li', {}, [
      el('button', { type: 'button', className: 'btn dn-item', disabled: !!s.rest,
        onClick: () => applyGroupFilter(s) }, [
        el('i', { className: 'dot series-' + s.color, 'aria-hidden': 'true' }),
        el('span', { className: 'ellipsis', title: s.label }, [s.label]),
        el('span', { className: 't-num t-muted' }, [(s.value / total * 100).toFixed(1) + '%'])])])));
    return el('div', { className: 'dn' }, [ring, legend]);
  }
  function tint(pct) {
    if (pct == null) return 'tm-none';
    const a = Math.abs(pct);
    if (a < 0.005) return 'tm-flat';
    const lv = a >= 3 ? 3 : a >= 1 ? 2 : 1;
    return (pct > 0 ? 'tm-up-' : 'tm-down-') + lv;
  }
  function treemap(gs) {
    const box = el('div', { className: 'tm', role: 'list', 'aria-label': '보유 지도' });
    const total = gs.reduce((s, g) => s + g.value, 0);
    const MAX = 11;
    const items = gs.slice(0, MAX).map(g => ({ g, v: g.value / total }));
    if (gs.length > MAX) {
      const rest = gs.slice(MAX);
      const n = rest.reduce((s, g) => s + g.n, 0);
      items.push({ g: { key: '__rest', label: '기타 ' + n + (st.axis === 'name' ? '종목' : '개'), value: rest.reduce((s, g) => s + g.value, 0),
        day: 0, base: 0, rest: true }, v: rest.reduce((s, g) => s + g.value, 0) / total });
    }
    const rects = squarify(items, 0, 0, 100, 100);
    rects.forEach(r => {
      const g = r.item.g;
      const pct = g.base ? g.day / g.base * 100 : null;
      const tiny = r.w < 14 || r.h < 16;
      const label = g.label + (pct != null ? ' ' + S.fmtChangePct(pct) : '') + ' · ' + (g.value / total * 100).toFixed(1) + '%';
      const kids = tiny ? [] : [
        el('span', { className: 'tm-name ellipsis' }, [g.label]),
        pct != null ? el('span', { className: 'tm-chg' }, [S.fmtChangePct(pct)]) : null,
        el('span', { className: 'tm-w' }, [(g.value / total * 100).toFixed(1) + '%'])];
      const pos = 'left:' + r.x + '%;top:' + r.y + '%;width:' + r.w + '%;height:' + r.h + '%';
      const tile = g.rest
        ? el('div', { className: 'tm-tile tm-rest', role: 'listitem', style: pos, title: label }, kids)
        : el('button', { type: 'button', className: 'btn tm-tile ' + tint(pct), role: 'listitem', style: pos,
            title: label, 'aria-label': label, onClick: () => applyGroupFilter(g) }, kids);
      box.appendChild(tile);
    });
    return box;
  }
  // squarified treemap in a 100×100 box (percent units)
  function squarify(items, x, y, w, h) {
    const out = [];
    let rest = items.slice();
    while (rest.length) {
      const short = Math.min(w, h);
      const area = rest.reduce((s, i) => s + i.v, 0);
      const scale = (w * h) / area;
      let row = [rest[0]], best = worst(row, short, scale);
      for (let k = 1; k < rest.length; k++) {
        const next = row.concat(rest[k]);
        const wv = worst(next, short, scale);
        if (wv > best) break;
        row = next; best = wv;
      }
      const rowArea = row.reduce((s, i) => s + i.v, 0) * scale;
      if (w >= h) {
        const cw = rowArea / h;
        let cy = y;
        row.forEach(i => { const ch = i.v * scale / cw; out.push({ item: i, x, y: cy, w: cw, h: ch }); cy += ch; });
        x += cw; w -= cw;
      } else {
        const ch = rowArea / w;
        let cx = x;
        row.forEach(i => { const cw = i.v * scale / ch; out.push({ item: i, x: cx, y, w: cw, h: ch }); cx += cw; });
        y += ch; h -= ch;
      }
      rest = rest.slice(row.length);
    }
    return out;
    function worst(row, side, scale) {
      const areas = row.map(i => i.v * scale);
      const s = areas.reduce((a, b) => a + b, 0);
      return Math.max(...areas.map(a => Math.max(side * side * a / (s * s), (s * s) / (side * side * a))));
    }
  }
  function contribBars(held) {
    const rows = held.filter(h => dayOf(h) != null).sort((a, b) => dayOf(b) - dayOf(a));
    if (!rows.length) return el('p', { className: 't-muted comp-none' }, ['오늘 손익에 들어간 종목이 없어요']);
    const max = Math.max(...rows.map(h => Math.abs(dayOf(h)))) || 1;
    return el('ul', { className: 'cb-list' }, rows.map(h => {
      const v = dayOf(h);
      return el('li', {}, [link({ s: h.symbol_id }, 'cb-row', [
        el('span', { className: 'ellipsis', title: nameOf(h) }, [nameOf(h)]),
        el('span', { className: 'cb-track', 'aria-hidden': 'true' }, [
          el('i', { className: v >= 0 ? 'up' : 'down', style: 'width:' + Math.max(2, Math.abs(v) / max * 50) + '%' })]),
        el('span', { className: 't-num ' + trendCls(v) }, [S.fmtPnl(v, st.ccy)])])]);
    }));
  }

  // ── holdings list ──────────────────────────────────────────────────
  function listCard() {
    const card = el('section', { className: 'card acc-list', 'aria-labelledby': 'list-h' });
    st.els.list = card;
    renderList();
    return card;
  }
  function scrollToList() {
    const n = st.els.list;
    if (n) n.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  function matchesFilter(h, f) {
    if (!f) return true;
    if (f.kind === 'missing') return h.q && h.q.state === 'none';
    if (f.kind === 'stale') return h.q && h.q.state === 'stale';
    if (f.kind === 'symbol') return h.symbol_id === f.key;
    if (f.kind === 'axis') return groupKey(h, f.axis) === f.key;
    if (f.kind === 'text') {
      const q = f.key.toLowerCase();
      return [h.name_kr, h.name_en, h.code, h.symbol_id].some(s => s && String(s).toLowerCase().includes(q));
    }
    return true;
  }
  function sorted(hs) {
    const key = st.sort;
    const val = h => {
      if (key === 'pnl') return pnlPct(h);
      if (key === 'day') return chgPct(h);
      if (key === 'verdict') { const a = st.summary[h.symbol_id]; return a && a.score != null ? +a.score : null; }
      if (key === 'eval') return h.held ? (evalOf(h) != null ? evalOf(h) : costOf(h)) : null;
      return null;
    };
    if (key === 'name') return hs.slice().sort((a, b) => nameOf(a).localeCompare(nameOf(b), 'ko'));
    return hs.slice().sort((a, b) => {
      const va = val(a), vb = val(b);
      if (va == null && vb == null) return nameOf(a).localeCompare(nameOf(b), 'ko');
      if (va == null) return 1;
      if (vb == null) return -1;
      return vb - va;
    });
  }
  function renderList() {
    const card = st.els.list;
    const d = ACC.data;
    if (!card || !d) return;
    const focusSearch = document.activeElement && document.activeElement.classList.contains('ls-input');
    clear(card);
    const all = d.holdings || [];
    const held = all.filter(h => h.held), watch = all.filter(h => !h.held);
    const tabs = S.segmented({ items: [{ value: 'held', label: '보유', count: held.length }, { value: 'watch', label: '관심', count: watch.length }],
      value: st.tab, label: '목록', onChange: v => { st.tab = v; st.filter = null; renderList(); } });
    tabs.classList.add('ls-tabs');
    const head = el('div', { className: 'ls-head' }, [
      el('h2', { id: 'list-h', className: 'sr-only' }, ['보유 목록']),
      tabs,
      st.filter ? el('button', { type: 'button', className: 'btn chip chip-filter', 'aria-label': st.filter.label + ' — ' + S.LABELS.clearFilter,
        onClick: () => { st.filter = null; renderList(); } }, [el('span', { className: 'ellipsis' }, [st.filter.label]), svgIco('x')]) : null,
      searchBox(),
      el('button', { type: 'button', className: 'btn chip ls-sort sm-up', 'aria-haspopup': 'menu',
        onClick: e => sortMenu(e.currentTarget) }, [SORTS.find(s => s.value === st.sort).label, el('span', { 'aria-hidden': 'true' }, ['▾'])]),
      el('button', { type: 'button', className: 'btn tb-icon ls-icon sm-down', 'aria-label': '종목 검색 · 추가',
        onClick: () => openMobileSearch() }, [svgIco('search')]),
      el('button', { type: 'button', className: 'btn tb-icon ls-icon sm-down', 'aria-label': '정렬 · ' + SORTS.find(s => s.value === st.sort).label,
        'aria-haspopup': 'menu', onClick: e => sortMenu(e.currentTarget) }, [svgIco('arrow-up-down')]),
      el('button', { type: 'button', className: 'btn btn-primary ls-trade', onClick: () => ACC.openTrade && ACC.openTrade({}) },
        [svgIco('arrow-left-right'), S.LABELS.recordTrade]),
    ]);
    card.appendChild(head);
    if (focusSearch) { const i = card.querySelector('.ls-input'); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }

    const base = st.tab === 'held' ? held : watch;
    const rows = base.filter(h => matchesFilter(h, st.filter));
    if (!base.length) {
      card.appendChild(st.tab === 'held'
        ? S.stateBlock({ kind: 'empty', icon: 'briefcase', title: '보유 종목이 없어요',
            body: '들고 있던 종목은 기존 보유로, 새로 산 건 거래로 적어요',
            actions: [{ label: '기존 보유 등록', onClick: () => ACC.openAdd && ACC.openAdd({ mode: 'existing' }) },
              { label: S.LABELS.recordTrade, onClick: () => ACC.openTrade && ACC.openTrade({}) }] })
        : S.stateBlock({ kind: 'empty', icon: 'star', title: '관심 종목이 없어요',
            body: '검색해서 관심으로 더해 두면 판정과 좌표를 함께 볼 수 있어요' }));
      return;
    }
    if (!rows.length) {
      const f = st.filter || {};
      card.appendChild(S.stateBlock({ kind: 'empty', icon: 'search',
        title: f.kind === 'text' ? '「' + f.key + '」와 맞는 종목이 없어요' : '조건에 맞는 종목이 없어요',
        body: f.kind === 'text' ? '보유·관심에서 찾았어요 · 새 종목은 종목 추가로' : null,
        actions: [{ label: S.LABELS.clearFilter, onClick: () => { st.filter = null; renderList(); } }] }));
      return;
    }
    const lab = ACC.market(regionOfCcy(st.ccy)).label;
    card.appendChild(el('div', { className: 'ls-cols sm-up', 'aria-hidden': 'true' }, [
      el('span', {}, ['종목']), el('span', {}, ['30일']), el('span', { className: 'r' }, [lab.head]),
      el('span', { className: 'r' }, [st.tab === 'held' ? '평가금액' : '']), el('span', { className: 'r' }, [st.tab === 'held' ? '비중' : '']),
      el('span', { className: 'r' }, [st.tab === 'held' ? '손익' : '']), el('span', { className: 'r' }, ['판정'])]));
    // currency groups, the selected one first
    const ccys = [st.ccy].concat([...new Set(rows.map(ccyOf))].filter(c => c !== st.ccy));
    const multi = [...new Set(rows.map(ccyOf))].length > 1;
    ccys.forEach(c => {
      const gr = sorted(rows.filter(h => ccyOf(h) === c));
      if (!gr.length) return;
      const evalSum = (d.totals[c] || {}).eval || 0;
      if (multi) {
        const sum = gr.reduce((s, h) => s + (evalOf(h) || 0), 0);
        card.appendChild(el('div', { className: 'ls-group' }, [c + ' · ' + gr.length + '종목' + (st.tab === 'held' ? ' · ' + S.fmtMoney(sum, c) : '')]));
      }
      card.appendChild(el('ul', { className: 'ls-rows' }, gr.map(h => el('li', {}, [row(h, evalSum)]))));
    });
  }
  function row(h, evalSum) {
    const c = ccyOf(h);
    const ev = evalOf(h);
    const q = h.q || {};
    const none = q.state === 'none';
    const chg = chgPct(h);
    const pp = pnlPct(h);
    const w = ev != null && evalSum ? ev / evalSum * 100 : null;
    const tag = h.is_etf ? 'ETF' : h.region;
    const nameEl = el('span', { className: 'r-name' }, [
      el('span', { className: 'r-nm clamp-2', title: nameOf(h) }, [nameOf(h)]),
      el('span', { className: 'r-tag sm-up' }, [tag])]);
    const priceEl = none
      ? el('span', { className: 'r-price' }, [el('b', { className: 'r-none' }, ['시세 없음'])])
      : el('span', { className: 'r-price' }, [
          el('b', { className: 't-num' }, [S.fmtPrice(h.price, c)]),
          chg != null ? el('span', { className: 'r-chg t-num ' + trendCls(chg) }, [S.fmtChangePct(chg)]) : null,
          q.state === 'stale' ? el('span', { className: 'chip r-stale' }, [svgIco('triangle-alert'), q.text]) : null,
        ]);
    const evalEl = h.held
      ? (none ? el('span', { className: 'r-eval' }, [el('b', { className: 't-num' }, [S.fmtMoney(costOf(h), c)]),
          el('span', { className: 'r-basis' }, ['매입가 기준'])])
        : el('span', { className: 'r-eval' }, [el('b', { className: 't-num' }, [S.fmtMoney(ev, c)])]))
      : el('span', { className: 'r-eval' });
    const weightEl = el('span', { className: 'r-weight sm-up' }, h.held && w != null ? [
      el('span', { className: 'w-bar', 'aria-hidden': 'true' }, [el('i', { style: 'width:' + Math.max(4, Math.min(100, w * 2)) + '%' })]),
      el('span', { className: 't-num' }, [w.toFixed(1) + '%'])] : [el('span', { className: 't-muted' }, [h.held ? '—' : ''])]);
    const pnlEl = el('span', { className: 'r-pnl t-num ' + (pp != null ? trendCls(pp) : 't-muted') }, [h.held ? (pp != null && !none ? S.fmtPnlPct(pp) : '—') : '']);
    const coords = coordLine(h);
    const kids = [
      nameEl,
      el('span', { className: 'r-spark' }, [spark(h.spark)]),
      priceEl, evalEl, weightEl, pnlEl,
      el('span', { className: 'r-vd' }, [verdictCell(h)]),
      coords ? el('span', { className: 'r-coords' }, [coords]) : null,
    ];
    const r = link({ s: h.symbol_id }, 'ls-row' + (q.state === 'stale' ? ' is-stale' : '') + (h.held ? '' : ' is-watch') + (coords ? '' : ' no-coords'), kids);
    r.setAttribute('aria-label', nameOf(h));
    return r;
  }
  function sortMenu(btn) {
    S.menu(btn, SORTS.map(s => ({ label: s.label, trailing: s.value === st.sort ? svgIco('circle-check') : null,
      onSelect: () => { st.sort = s.value; save(SORT_KEY, s.value); renderList(); } })));
  }

  // search: one box — '내 목록' hits go to the stock, '추가할 종목' opens DLG-ADD
  let searchSeq = 0;
  function searchBox() {
    const input = el('input', { type: 'search', className: 'ls-input', placeholder: '종목 검색 · 추가', value: st.query,
      'aria-label': '종목 검색 · 추가', autocomplete: 'off', role: 'combobox', 'aria-expanded': 'false', 'aria-controls': 'ls-results' });
    const results = el('div', { className: 'ls-results', id: 'ls-results', role: 'listbox' });
    results.hidden = true;
    const wrap = el('div', { className: 'ls-search sm-up' }, [svgIco('search'), input,
      el('kbd', { className: 'ls-kbd', 'aria-hidden': 'true' }, ['/']), results]);
    input.addEventListener('input', () => { st.query = input.value; runSearch(input, results); });
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && input.value.trim()) {
        e.preventDefault();
        st.filter = { kind: 'text', key: input.value.trim(), label: input.value.trim() };
        st.query = '';
        results.hidden = true;
        renderList();
      } else if (e.key === 'Escape' && !results.hidden) { e.stopPropagation(); results.hidden = true; input.setAttribute('aria-expanded', 'false'); }
      else if (e.key === 'ArrowDown') { const f = results.querySelector('a,button'); if (f) { e.preventDefault(); f.focus(); } }
    });
    results.addEventListener('keydown', e => {
      const its = [...results.querySelectorAll('a,button')];
      const i = its.indexOf(document.activeElement);
      if (e.key === 'ArrowDown' && i < its.length - 1) { e.preventDefault(); its[i + 1].focus(); }
      if (e.key === 'ArrowUp') { e.preventDefault(); (i > 0 ? its[i - 1] : input).focus(); }
      if (e.key === 'Escape') { results.hidden = true; input.focus(); }
    });
    wrap.addEventListener('focusout', () => setTimeout(() => { if (!wrap.contains(document.activeElement)) { results.hidden = true; input.setAttribute('aria-expanded', 'false'); } }, 0));
    st.els.searchInput = input;
    return wrap;
  }
  async function runSearch(input, results) {
    const q = input.value.trim();
    const seq = ++searchSeq;
    if (q.length < 2) { results.hidden = true; input.setAttribute('aria-expanded', 'false'); return; }
    const all = (ACC.data && ACC.data.holdings) || [];
    const mine = all.filter(h => matchesFilter(h, { kind: 'text', key: q })).slice(0, 5);
    const draw = (found, loading) => {
      clear(results);
      results.appendChild(el('div', { className: 'lr-head' }, ['내 목록']));
      if (mine.length) mine.forEach(h => results.appendChild(link({ s: h.symbol_id }, 'lr-item', [
        el('span', { className: 'ellipsis' }, [nameOf(h)]), el('span', { className: 't-muted' }, [h.held ? '보유' : '관심'])])));
      else results.appendChild(el('div', { className: 'lr-none' }, ['없어요']));
      results.appendChild(el('div', { className: 'lr-head' }, ['추가할 종목']));
      if (loading) results.appendChild(el('div', { className: 'lr-none' }, [S.spinner(), ' 찾는 중']));
      else if (found === null) results.appendChild(el('div', { className: 'lr-none' }, ['찾지 못했어요 · 잠시 뒤 다시 해 주세요']));
      else {
        const own = new Set(all.map(h => h.symbol_id));
        const add = found.filter(r => !own.has(r.symbol_id)).slice(0, 6);
        if (!add.length) results.appendChild(el('div', { className: 'lr-none' }, ['없어요']));
        add.forEach(r => results.appendChild(el('button', { type: 'button', className: 'btn lr-item',
          onClick: () => { results.hidden = true; ACC.openAdd && ACC.openAdd({ symbol: r }); } }, [
          el('span', { className: 'ellipsis' }, [r.name_kr || r.name_en || r.code]),
          el('span', { className: 't-muted' }, [r.code + ' · ' + (r.is_etf ? 'ETF' : r.market || '')]),
          svgIco('plus')])));
      }
      results.hidden = false;
      input.setAttribute('aria-expanded', 'true');
    };
    draw(null, true);
    let found = null;
    try {
      const r = await S.api('/api/universe/search?q=' + encodeURIComponent(q) + '&limit=10', { toast: false });
      found = r.results || [];
    } catch (e) { found = null; }
    if (seq === searchSeq) draw(found, false);
  }
  function openMobileSearch() {
    const d = S.dialog({ title: '종목 검색 · 추가', cancelLabel: false, body: () => {
      const input = el('input', { type: 'search', className: 'input', placeholder: '이름이나 코드', 'aria-label': '종목 검색' });
      const results = el('div', { className: 'ls-results is-sheet', role: 'listbox' });
      results.hidden = true;
      input.addEventListener('input', () => runSearch(input, results));
      input.addEventListener('keydown', e => {
        if (e.key === 'Enter' && input.value.trim()) {
          e.preventDefault();
          st.filter = { kind: 'text', key: input.value.trim(), label: input.value.trim() };
          d.close(null, true);
          renderList();
        }
      });
      results.addEventListener('click', e => { if (e.target.closest('a,button')) d.close(null, true); });
      return el('div', { className: 'form-stack' }, [input, results]);
    } });
  }
  document.addEventListener('keydown', e => {
    if (e.key !== '/' || !root || !st || !st.els.searchInput) return;
    const t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
    if (document.body.classList.contains('has-layer')) return;
    e.preventDefault();
    st.els.searchInput.focus();
  });

  // ── recent trades ──────────────────────────────────────────────────
  function tradesCard() {
    const card = el('section', { className: 'card acc-trades', 'aria-labelledby': 'trades-h' });
    st.els.trades = card;
    renderTrades();
    return card;
  }
  async function loadTrades() {
    try {
      const r = await S.api(ACC.path('/trades?limit=5'), { toast: false });
      st.trades = r.trades || []; st.tradesErr = false;
    } catch (e) { if (e.forbidden) return; st.tradesErr = true; }
    renderTrades();
  }
  function renderTrades() {
    const card = st.els.trades;
    if (!card) return;
    clear(card);
    card.appendChild(el('div', { className: 'card-head' }, [el('h2', { id: 'trades-h' }, ['최근 거래']),
      link({ v: 'trades' }, 'btn pin-link tr-all', [S.LABELS.showAll])]));
    if (st.tradesErr) {
      card.appendChild(S.stateBlock({ kind: 'error', title: '거래를 불러오지 못했어요', actions: [{ label: S.LABELS.retry, onClick: loadTrades }] }));
      return;
    }
    if (!st.trades) { card.appendChild(S.skeleton(['90%', '80%', '85%'])); return; }
    if (!st.trades.length) { card.appendChild(el('p', { className: 't-muted tr-none' }, ['아직 기록한 거래가 없어요'])); return; }
    card.appendChild(el('ul', { className: 'tr-list' }, st.trades.map(t => {
      const amount = t.qty * t.price;
      const buy = t.side === 'buy';
      return el('li', {}, [link({ s: t.symbol_id }, 'tr-row', [
        el('span', { className: 'tr-date t-muted' }, [S.fmtDate(t.traded_at)]),
        el('span', { className: 'tr-main' }, [
          el('span', { className: 'tr-name' }, [el('span', { className: 'ellipsis', title: t.name || t.symbol_id }, [t.name || t.symbol_id]),
            el('span', { className: 'tr-side ' + (buy ? 't-up' : 't-down') }, [buy ? S.LABELS.buy : S.LABELS.sell])]),
          el('span', { className: 'tr-sub t-muted t-num' }, [S.fmtQty(t.qty) + '주 × ' + S.fmtPrice(t.price, t.currency)])]),
        el('b', { className: 'tr-amt t-num' }, [S.fmtMoney(amount, t.currency)])])]);
    })));
  }

  // ── empty account: three ways to start ─────────────────────────────
  function startCards() {
    const card = (icon, title, body, label, onClick) => el('div', { className: 'start-card' }, [
      el('span', { className: 'start-ico', 'aria-hidden': 'true' }, [svgIco(icon)]),
      el('h3', {}, [title]), el('p', { className: 't-muted' }, [body]),
      el('button', { type: 'button', className: 'btn', onClick }, [label])]);
    return el('section', { className: 'acc-start', 'aria-labelledby': 'start-h' }, [
      el('h2', { id: 'start-h' }, ['비어 있는 계좌예요']),
      el('p', { className: 't-muted start-lead' }, ['셋 중 하나로 시작해요']),
      el('div', { className: 'start-grid' }, [
        card('wallet', '예수금 입력', '지금 계좌에 있는 현금을 적어요', '예수금 입력', () => ACC.openCash && ACC.openCash(st.ccy)),
        card('briefcase', '기존 보유 등록', '이미 들고 있던 종목이에요 · 예수금은 그대로예요', '기존 보유 등록',
          () => ACC.openAdd && ACC.openAdd({ mode: 'existing' })),
        card('arrow-left-right', S.LABELS.recordTrade, '산 날짜·가격으로 적어요 · 예수금도 함께 바뀌어요', S.LABELS.recordTrade,
          () => ACC.openTrade && ACC.openTrade({}))])]);
  }

  // ── whole page ─────────────────────────────────────────────────────
  function skeletonPage() {
    clear(root);
    root.setAttribute('aria-busy', 'true');
    root.append(pageHead(null),
      el('div', { className: 'acc-top' }, [
        el('section', { className: 'card acc-sum' }, [S.skeleton(['20%', '50%', '30%', '40%', '35%', '90%', '90%', '90%'])]),
        el('section', { className: 'card acc-trend' }, [el('div', { className: 'card-head' }, [el('h2', {}, ['자산 추이'])]),
          el('div', { className: 'trend-skel skeleton-block' })])]),
      el('div', { className: 'acc-bottom' }, [
        el('section', { className: 'card acc-list is-loading' }, [S.skeleton(['100%', '100%', '100%', '100%', '100%', '100%'])])]));
  }
  function render() {
    const d = ACC.data;
    if (!root) return;
    if (!d) { skeletonPage(); return; }
    const ccys = currencies(d);
    if (!st.ccy || !ccys.includes(st.ccy)) st.ccy = ccys[0];
    root.removeAttribute('aria-busy');
    const scrollY = window.scrollY;
    const refocus = document.activeElement && document.activeElement.classList.contains('ls-input');
    clear(root);
    st.els = {};
    const empty = !(d.holdings || []).length && !(d.balances && Object.keys(d.balances).some(k => d.balances[k]));
    const banner = d.warm && d.warm.state === 'error' ? S.banner({ kind: 'error', title: '시세를 받지 못했어요',
      sub: '저장된 숫자를 보여 줘요', action: { label: S.LABELS.retry, onClick: () => ACC.warm() } }) : null;
    if (banner) banner.classList.add('acc-banner');
    root.append(pageHead(d), banner || '',
      el('div', { className: 'acc-top' }, [summaryCard(d), trendCard()]));
    if (empty) {
      root.appendChild(startCards());
    } else {
      const side = el('div', { className: 'acc-side' }, [compCard(), tradesCard()]);
      root.appendChild(el('div', { className: 'acc-bottom' }, [listCard(), side]));
    }
    loadSnaps();
    if (!empty) {
      if (st.trades == null || Date.now() - (st.tradesAt || 0) > 2000) { st.tradesAt = Date.now(); loadTrades(); }
    }
    loadSummary(d);
    window.scrollTo(0, scrollY);
    if (refocus) { const i = root.querySelector('.ls-input'); if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); } }
  }
  async function loadSummary(d) {
    const ids = (d.holdings || []).map(h => h.symbol_id).sort().join(',');
    if (!ids || ids === st.summaryFor) return;
    try {
      const r = await S.api('/api/analysis/summary?symbols=' + encodeURIComponent(ids), { toast: false });
      st.summary = r.symbols || {};
    } catch (e) { st.summary = {}; }
    st.summaryFor = ids;
    renderList();
  }

  ACC.views.account = {
    mount(r) {
      root = r;
      st = fresh();
      offs = [
        ACC.on('data', d => {
          if (!root) return;
          render();
        }),
        ACC.on('meta', () => { if (root && !ACC.data) skeletonPage(); }),
        ACC.on('warm', w => {
          if (!root || !ACC.data) return;
          const head = root.querySelector('.acc-head');
          if (head) head.replaceWith(pageHead(ACC.data));
          const hasBanner = !!root.querySelector('.acc-banner');
          if ((w.state === 'error') !== hasBanner) render();
          if (w.state === 'ok') {
            root.querySelectorAll('.sum-total, .sum-val').forEach(n => {
              n.classList.remove('is-flash'); void n.offsetWidth; n.classList.add('is-flash');
            });
          }
        }),
      ];
      ACC.setChrome({});
      render();
    },
    unmount() {
      offs.forEach(f => f());
      offs = [];
      root = null;
    },
    // expose for the dialogs: refresh the recent trades after a write
    refresh() { if (root) { st.snaps = {}; st.tradesAt = 0; render(); } },
  };
})();
