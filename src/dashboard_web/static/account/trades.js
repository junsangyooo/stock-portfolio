// account/trades.js — SCR-TRADES 거래 이력 (/a/{id}?v=trades[&s=…][&p=1M|3M|1Y|all][&since=…&until=…]).
// 종목·기간으로 거르고, 기간 합계(매수·매도·수수료, 통화별)와 날짜별 거래를
// 본다. 행을 누르면 그 종목 보기. 거르기는 주소 쿼리에 있어 뒤로 가기로 돌아온다.
// 390: 하단 탭 숨김(core.js), 거르기 머리 고정.
(function () {
  'use strict';
  const S = window.S;
  const ACC = window.ACC;
  if (!S || !ACC) return;
  const { el, clear, svgIco, LABELS } = S;
  const stk = ACC.stk || {};

  const PRESETS = [
    { value: '1M', label: '1M', months: 1, words: '최근 1개월', head: '1개월' },
    { value: '3M', label: '3M', months: 3, words: '최근 3개월', head: '3개월' },
    { value: '1Y', label: '1Y', months: 12, words: '최근 1년', head: '1년' },
    { value: 'all', label: '전체', months: null, words: '전체 기간', head: '전체' },
    { value: 'range', label: '기간', months: null, words: '', head: '기간' },
  ];
  const LIMIT = 1000;                 // GET /trades caps a page here
  const today = () => S.marketToday('KR');
  const addMonths = (d, n) => {
    const x = new Date(d + 'T00:00:00Z'); x.setUTCMonth(x.getUTCMonth() + n); return x.toISOString().slice(0, 10);
  };
  const addDays = (d, n) => { const x = new Date(d + 'T00:00:00Z'); x.setUTCDate(x.getUTCDate() + n); return x.toISOString().slice(0, 10); };

  // the query → { sid, preset, since, until }
  function filterOf(p) {
    const preset = p.since || p.until ? 'range' : (PRESETS.some(x => x.value === p.p) ? p.p : '3M');
    const pr = PRESETS.find(x => x.value === preset);
    let since = p.since || null, until = p.until || null;
    if (preset !== 'range' && pr.months) { since = addDays(addMonths(today(), -pr.months), 1); until = null; }
    if (preset === 'all') { since = null; until = null; }
    return { sid: p.s ? String(p.s).toUpperCase() : null, preset, since, until };
  }
  function isDefault(f) { return !f.sid && f.preset === '3M'; }

  let V = null;

  function mount(root, params) {
    const v = V = { root, alive: true, offs: [], f: filterOf(params), rows: undefined, err: null, any: undefined };
    root.classList.add('trd-page');
    ACC.setChrome && ACC.setChrome({ title: '거래 이력', back: () => back() });
    v.els = {
      head: el('div', { className: 'trd-head' }),
      filters: el('div', { className: 'trd-filters' }),
      sum: el('section', { className: 'card trd-sum', 'aria-label': '기간 합계' }),
      list: el('section', { className: 'card trd-list', 'aria-label': '거래 목록' }),
    };
    root.appendChild(el('div', { className: 'trd' }, [v.els.head, v.els.filters, v.els.sum, v.els.list]));
    v.offs.push(ACC.on('data', () => { if (v.alive) { paintHead(v); paintFilters(v); if (v.rows !== undefined) paintBody(v); } }));
    if (stk.on) v.offs.push(stk.on('trade', () => { if (v.alive) { v.any = undefined; v.allSyms = null; load(v); } }));
    paintHead(v);
    paintFilters(v);
    load(v);
  }
  function unmount() {
    const v = V; V = null;
    if (!v) return;
    v.alive = false;
    v.offs.forEach(f => { try { f(); } catch (e) { /* */ } });
    v.root.classList.remove('trd-page');
  }
  function update(params) {
    if (!V) return;
    const f = filterOf(params);
    if (JSON.stringify(f) === JSON.stringify(V.f)) return;
    V.f = f;
    paintFilters(V);
    load(V);
  }
  function back() {
    if (history.state && history.state.acc && history.length > 1) history.back();
    else ACC.go({});
  }
  // a filter change is a new history entry: 뒤로 가기 undoes it
  function setFilter(v, patch) {
    const f = Object.assign({}, v.f, patch);
    const q = { v: 'trades' };
    if (f.sid) q.s = f.sid;
    if (f.preset === 'range') { if (f.since) q.since = f.since; if (f.until) q.until = f.until; }
    else if (f.preset !== '3M') q.p = f.preset;
    ACC.go(q, { keepScroll: true });
  }

  function paintHead(v) {
    const h = v.els.head;
    clear(h);
    const accName = ACC.accountRef().name;
    const tipId = 'trd-tip';
    const tip = el('div', { className: 'trd-tip', id: tipId, role: 'tooltip' }, ['거래는 고칠 수 없어요 · 보유 정정·예수금 정정으로 맞춰요']);
    tip.hidden = true;
    const info = el('button', { type: 'button', className: 'btn dlg-icon', 'aria-label': '거래 이력 안내', 'aria-describedby': tipId,
      'aria-expanded': 'false', onClick: () => { tip.hidden = !tip.hidden; info.setAttribute('aria-expanded', tip.hidden ? 'false' : 'true'); } },
      [svgIco('info')]);
    h.appendChild(el('a', { className: 'btn trd-back sm-up', href: location.pathname, onClick: e => { e.preventDefault(); ACC.go({}); } },
      [svgIco('arrow-left'), el('span', { className: 'ellipsis' }, [accName])]));
    h.appendChild(el('h1', { className: 'trd-title' }, ['거래 이력']));
    h.appendChild(info);
    h.appendChild(tip);
  }

  function symbolName(sid, rows) {
    const h = ACC.holding(sid);
    if (h && h.name_kr) return h.name_kr;
    const t = (rows || []).find(r => r.symbol_id === sid);
    return (t && t.name) || (stk.split ? stk.split(sid)[1] : sid);
  }
  function rangeWords(f) {
    if (f.preset === 'range') return [f.since ? S.fmtDate(f.since) : '처음', f.until ? S.fmtDate(f.until) : '오늘'].join(' – ');
    return PRESETS.find(x => x.value === f.preset).words;
  }

  function paintFilters(v) {
    const box = v.els.filters;
    clear(box);
    const f = v.f;
    const locked = v.any === false;          // no trade at all: nothing to filter
    if (f.sid) {
      box.appendChild(el('button', { type: 'button', className: 'btn chip chip-filter', 'aria-label': symbolName(f.sid, v.rows) + ' 거르기 풀기',
        onClick: () => setFilter(v, { sid: null }) }, [el('span', { className: 'ellipsis' }, [symbolName(f.sid, v.rows)]), svgIco('x')]));
    } else {
      box.appendChild(el('button', { type: 'button', className: 'btn trd-sym', 'aria-haspopup': 'menu', 'aria-expanded': 'false', disabled: locked,
        onClick: e => symbolMenu(v, e.currentTarget) }, [svgIco('search', 'sm-up'), el('span', { className: 'sm-up' }, ['종목 전체']),
        el('span', { className: 'sm-down' }, ['종목']), svgIco('chevron-down')]));
    }
    const seg = S.segmented({ label: '기간', value: f.preset, items: PRESETS.map(p => ({ value: p.value, label: p.label })),
      onChange: val => {
        if (val === 'range') { seg.setValue(f.preset); pickRange(v); return; }
        setFilter(v, { preset: val, since: null, until: null });
      } });
    if (locked) seg.querySelectorAll('button').forEach(b => { b.disabled = true; });
    box.appendChild(seg);
  }

  function symbolMenu(v, btn) {
    const seen = new Map();
    (ACC.data && ACC.data.holdings || []).forEach(h => seen.set(h.symbol_id, h.name_kr || h.code));
    (v.allSyms || []).forEach(t => { if (!seen.has(t.symbol_id)) seen.set(t.symbol_id, t.name || t.symbol_id); });
    const items = [{ label: '종목 전체', onSelect: () => setFilter(v, { sid: null }) }];
    seen.forEach((nm, sid) => items.push({ label: nm, sub: sid.replace(':', ' · '), onSelect: () => setFilter(v, { sid }) }));
    S.menu(btn, items);
  }

  function pickRange(v) {
    const t = today();
    const since = el('input', { className: 'input', type: 'date', max: t, value: v.f.since || addMonths(t, -3), 'aria-label': '시작일' });
    const until = el('input', { className: 'input', type: 'date', max: t, value: v.f.until || t, 'aria-label': '끝일' });
    const err = el('p', { className: 'field-error', role: 'alert' });
    err.hidden = true;
    S.dialog({
      title: '기간', primary: { label: LABELS.confirm },
      body: el('div', { className: 'form-stack' }, [
        el('div', { className: 'field-row' }, [
          el('label', { className: 'field' }, [el('span', { className: 'field-label' }, ['시작']), since]),
          el('label', { className: 'field' }, [el('span', { className: 'field-label' }, ['끝']), until])]),
        err]),
      onSubmit: async () => {
        if (since.value && until.value && since.value > until.value) {
          err.textContent = '시작일이 끝일보다 늦어요'; err.hidden = false;
          const e = new Error('시작일이 끝일보다 늦어요'); e.status = 400; throw e;
        }
        return { since: since.value || null, until: until.value || null };
      },
    }).done.then(r => { if (r && typeof r === 'object') setFilter(v, { preset: 'range', since: r.since, until: r.until }); });
  }

  async function load(v) {
    const f = v.f;
    v.rows = undefined; v.err = null;
    paintBody(v);
    const q = new URLSearchParams({ limit: String(LIMIT) });
    if (f.sid) q.set('symbol_id', f.sid);
    if (f.since) q.set('since', f.since);
    if (f.until) q.set('until', f.until);
    const want = JSON.stringify(f);
    try {
      const r = await S.api(ACC.path('/trades') + '?' + q.toString(), { toast: false });
      if (!v.alive || JSON.stringify(v.f) !== want) return;
      v.rows = r.trades || [];
      // empty under a filter: is the book empty, or only this window?
      if (!v.rows.length && v.any === undefined) {
        const all = await S.api(ACC.path('/trades') + '?limit=200', { toast: false });
        if (!v.alive) return;
        v.allSyms = all.trades || [];
        v.any = v.allSyms.length > 0;
      } else if (v.rows.length) {
        v.any = true;
        if (!v.allSyms) v.allSyms = isDefault(f) ? v.rows : null;
      }
    } catch (e) {
      if (!v.alive || e.forbidden) return;
      v.err = e;
    }
    paintFilters(v);
    paintBody(v);
  }

  function paintBody(v) {
    const sum = v.els.sum, list = v.els.list;
    clear(sum); clear(list);
    sum.hidden = false; list.hidden = false;
    const f = v.f;
    const pr = PRESETS.find(x => x.value === f.preset);
    const sumTitle = (f.preset === 'range' ? '기간' : pr.head) + ' 합계';
    if (v.err) {
      sum.hidden = true;
      list.appendChild(S.stateBlock({ kind: 'error', title: '거래 이력을 불러오지 못했어요', body: '잠시 뒤 다시 시도해 주세요',
        actions: [{ label: LABELS.retry, onClick: () => load(v) }] }));
      list.classList.add('is-state');
      return;
    }
    list.classList.remove('is-state');
    if (v.rows === undefined) {
      sum.appendChild(el('div', { className: 'trd-sum-head' }, [el('h2', {}, [sumTitle])]));
      sum.appendChild(S.skeleton(['60%']));
      list.appendChild(S.skeleton(['100%', '100%', '100%', '100%', '100%', '100%']));
      return;
    }
    if (!v.rows.length) {
      sum.hidden = true;
      list.classList.add('is-state');
      if (v.any === false) {
        list.appendChild(S.stateBlock({ kind: 'empty', icon: 'arrow-left-right', title: '아직 기록한 거래가 없어요',
          body: '매수·매도를 기록하면 여기에 날짜별로 쌓여요',
          actions: [{ label: LABELS.recordTrade, primary: true, onClick: () => ACC.openTrade && ACC.openTrade({}) }] }));
      } else {
        list.appendChild(S.stateBlock({ kind: 'empty', icon: 'search', title: '이 기간엔 거래가 없어요',
          body: [f.sid ? symbolName(f.sid, v.rows) : null, rangeWords(f)].filter(Boolean).join(' · '),
          actions: [{ label: LABELS.clearFilter, onClick: () => ACC.go({ v: 'trades', p: 'all' }) }] }));
      }
      return;
    }
    // 기간 합계, 통화별 (환율 없음)
    const by = {};
    v.rows.forEach(t => {
      const c = t.currency || 'KRW';
      const b = by[c] = by[c] || { buy: 0, sell: 0, fee: 0, n: 0 };
      b[t.side === 'buy' ? 'buy' : 'sell'] += t.qty * t.price;
      b.fee += t.fee || 0;
      b.n++;
    });
    const first = v.rows[v.rows.length - 1];
    // the route returns at most LIMIT rows (newest first): at the cap, the
    // sums cover only those, and the span starts at the oldest one shown
    const capped = v.rows.length >= LIMIT;
    const span = (f.since && !capped ? S.fmtDate(f.since) : S.fmtDate(first.traded_at)) + ' – ' + (f.until ? S.fmtDate(f.until) : S.fmtDate(today()));
    sum.appendChild(el('div', { className: 'trd-sum-head' }, [el('h2', {}, [sumTitle]),
      el('span', { className: 't-caption t-muted' }, [span + (Object.keys(by).length === 1 ? ' · ' + Object.keys(by)[0] + ' · ' + v.rows.length + '건' : ' · ' + v.rows.length + '건')]),
      capped ? el('span', { className: 'chip trd-capped' }, [svgIco('info'), '최근 ' + S.fmtCount(LIMIT) + '건 기준']) : null]));
    Object.keys(by).forEach(c => {
      const b = by[c];
      sum.appendChild(el('div', { className: 'trd-sum-row' }, [
        Object.keys(by).length > 1 ? el('span', { className: 'chip chip-quiet' }, [c]) : null,
        fig('매수', S.fmtMoney(b.buy, c)), fig('매도', S.fmtMoney(b.sell, c)), fig('수수료', S.fmtMoney(b.fee, c)),
      ]));
    });
    // 날짜별 (KST — the book's calendar)
    const groups = [];
    v.rows.forEach(t => {
      const day = S.fmtDate(t.traded_at);
      const g = groups[groups.length - 1];
      if (g && g.day === day) g.rows.push(t); else groups.push({ day, rows: [t] });
    });
    const go = t => ACC.go({ s: t.symbol_id });
    const sideEl = t => el('span', { className: 'trd-side ' + (t.side === 'buy' ? 't-up' : 't-down') }, [t.side === 'buy' ? LABELS.buy : LABELS.sell]);
    const code = t => (stk.split ? stk.split(t.symbol_id)[1] : t.symbol_id);
    // 1440: one table · 390: stacked rows
    const table = el('table', { className: 'tbl trd-tbl sm-up' }, [
      el('thead', {}, [el('tr', {}, [
        el('th', { scope: 'col' }, ['구분']), el('th', { scope: 'col' }, ['이름']), el('th', { scope: 'col', className: 'r' }, ['수량 × 가격']),
        el('th', { scope: 'col', className: 'r' }, ['수수료']), el('th', { scope: 'col', className: 'r' }, ['금액']), el('th', { scope: 'col' }, ['메모']),
        el('th', { scope: 'col' }, [el('span', { className: 'sr-only' }, ['열기'])])])]),
    ]);
    groups.forEach(g => {
      const tb = el('tbody', {}, [el('tr', { className: 'trd-day' }, [el('th', { colspan: '7', scope: 'rowgroup' }, [g.day])])]);
      g.rows.forEach(t => {
        const nm = t.name || symbolName(t.symbol_id);
        tb.appendChild(el('tr', { className: 'trd-row', tabindex: '0', 'aria-label': g.day + ' ' + (t.side === 'buy' ? '매수' : '매도') + ' ' + nm,
          onClick: () => go(t), onKeydown: e => { if (e.key === 'Enter') go(t); } }, [
          el('td', {}, [sideEl(t)]),
          el('td', { className: 'trd-name' }, [el('span', { className: 'ellipsis', title: nm }, [nm]), el('span', { className: 't-caption t-muted' }, [code(t)])]),
          el('td', { className: 'r' }, [S.fmtQty(t.qty) + '주 × ' + S.fmtMoney(t.price, t.currency)]),
          el('td', { className: 'r t-muted' }, [S.fmtMoney(t.fee, t.currency)]),
          el('td', { className: 'r trd-amt' }, [S.fmtMoney(t.qty * t.price, t.currency)]),
          el('td', { className: 'trd-memo t-caption' + (t.note ? '' : ' t-muted') }, [el('span', { className: 'ellipsis', title: t.note || '' }, [t.note || '—'])]),
          el('td', { className: 'r trd-go' }, [svgIco('chevron-right')]),
        ]));
      });
      table.appendChild(tb);
    });
    list.appendChild(table);
    const stack = el('div', { className: 'trd-stack sm-down' });
    groups.forEach(g => {
      stack.appendChild(el('p', { className: 'trd-day-m t-caption t-muted' }, [g.day]));
      g.rows.forEach(t => {
        const nm = t.name || symbolName(t.symbol_id);
        stack.appendChild(el('button', { type: 'button', className: 'btn trd-item', onClick: () => go(t) }, [
          el('span', { className: 'trd-item-l' }, [
            el('span', { className: 'trd-item-n' }, [sideEl(t), el('span', { className: 'clamp-2' }, [nm])]),
            el('span', { className: 't-caption t-muted' }, [S.fmtQty(t.qty) + '주 × ' + S.fmtMoney(t.price, t.currency) + ' · 수수료 ' + S.fmtMoney(t.fee, t.currency)]),
            t.note ? el('span', { className: 't-caption trd-item-memo' }, [t.note]) : null,
          ]),
          el('span', { className: 'trd-amt' }, [S.fmtMoney(t.qty * t.price, t.currency)]),
        ]));
      });
    });
    list.appendChild(stack);
  }
  function fig(label, value) {
    return el('div', { className: 'trd-fig' }, [el('span', { className: 't-caption t-muted' }, [label]), el('span', { className: 'trd-fig-v' }, [value])]);
  }

  ACC.views.trades = { mount, unmount, update };
})();
