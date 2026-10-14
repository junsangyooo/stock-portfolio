// account/trade-dialog.js — DLG-TRADE 거래 기록.
//   ACC.openTrade({ symbol_id?, side?: 'buy'|'sell', date?: 'YYYY-MM-DD', qty?, price?, note? })
// 매수·매도 하나를 POST /api/accounts/{id}/trades 한 번으로 기록한다 — 보유·
// 평단·예수금·이력이 서버의 한 트랜잭션에서 함께 움직인다(ops notes §3.1).
// 미리보기(수량·평단·예수금·이번 매도 손익)는 서버와 같은 셈을 화면에서 한다.
// 실패(네트워크·5xx)는 다시 보내지 않고 GET /trades로 생겼는지 먼저 본다
// (S.dialog의 verify), 403은 PIN 다시 입력 뒤 1회 재전송(S.dialog의 account).
(function () {
  'use strict';
  const S = window.S;
  const ACC = window.ACC;
  if (!S || !ACC) return;
  const { el, clear, svgIco, LABELS } = S;
  const stk = ACC.stk;
  const CLOSE = { KR: [15, 30], US: [16, 0] };
  const parseNum = s => { const t = String(s == null ? '' : s).replace(/[,\s]/g, ''); return t === '' ? null : Number(t); };
  const EPS = 1e-9;
  // 198400 → '198,400' (inputs keep every digit the person or the quote gave)
  const GROUP = new Intl.NumberFormat('en-US', { maximumFractionDigits: 4 });
  const grouped = v => (v == null || isNaN(v) ? '' : GROUP.format(+v));

  ACC.openTrade = function (o) {
    o = o || {};
    if (!stk) return;
    const st = {
      sid: o.symbol_id ? String(o.symbol_id).toUpperCase() : null,
      side: o.side === 'sell' ? 'sell' : 'buy',
      date: null,                   // null = 오늘
      custom: false,
      qty: o.qty != null ? String(o.qty) : '', price: o.price != null ? String(o.price) : '', fee: '', note: o.note || '',
      priceTouched: o.price != null, meta: undefined, quote: undefined, bars: undefined, prevOpen: false,
    };
    if (o.date && o.date !== today(st)) { st.date = o.date; st.custom = true; }
    if (st.side === 'sell' && !held(st)) st.side = 'buy';
    const opened = Math.floor(Date.now() / 1000);
    let d = null;
    const box = el('div', { className: 'tdl' });

    function region() { return st.sid ? stk.split(st.sid)[0] : 'KR'; }
    function today() { return S.marketToday(region()); }
    function hold() { return st.sid ? ACC.holding(st.sid) : null; }
    function held() { return ACC.held(hold()); }
    function ccy() { return stk.ccyOf(hold(), st.meta, st.sid); }
    function dateOf() { return st.date || today(); }
    function barDates() {
      if (!st.bars) return null;
      return st.bars.map(b => stk.barDate(b.ts, region()));
    }
    // a closed day → the trading day before it (from the daily bars)
    function holidayPrev(date) {
      const wd = new Date(date + 'T00:00:00Z').getUTCDay();
      const ds = barDates();
      let closed = wd === 0 || wd === 6;
      if (!closed && ds && ds.length && date <= ds[ds.length - 1] && ds.indexOf(date) < 0) closed = true;
      if (!closed) return null;
      const before = ds ? ds.filter(x => x < date) : [];
      if (before.length) return before[before.length - 1];
      let p = date;
      do { p = stk.addDays(p, -1); } while ([0, 6].indexOf(new Date(p + 'T00:00:00Z').getUTCDay()) >= 0);
      return p;
    }
    function closeOf(date) {
      const ds = barDates();
      if (!ds) return null;
      const i = ds.indexOf(date);
      return i < 0 ? null : st.bars[i].close;
    }
    // the price the head shows, and whether it is fresh enough to prefill
    function currentPrice() {
      const h = hold();
      if (h && h.price != null) return { price: h.price, ok: h.q && (h.q.state === 'fresh' || h.q.state === 'delayed') };
      if (st.quote && st.quote.price != null) {
        const mk = ACC.market(region());
        const q = S.quoteState({ quoteTs: st.quote.ts, market: region(), session: mk.session, phase: mk.phase });
        return { price: st.quote.price, ok: q.state === 'fresh' || q.state === 'delayed' };
      }
      return { price: null, ok: false };
    }
    function maybePrefill() {
      if (st.priceTouched || !st.sid) return;
      const cp = currentPrice();
      st.price = !st.date && cp.ok && cp.price != null ? grouped(cp.price) : '';
    }

    // ── 값 · 미리보기 ──
    function calc() {
      const h = hold();
      const H = h ? +h.qty || 0 : 0, A = h && h.avg_cost != null ? +h.avg_cost : null;
      const q = parseNum(st.qty), p = parseNum(st.price), f = parseNum(st.fee) == null ? 0 : parseNum(st.fee);
      const bal = ACC.data && ACC.data.balances ? ACC.data.balances[ccy()] : null;
      const C = bal == null ? null : +bal;
      const okQ = q != null && isFinite(q) && q > 0, okP = p != null && isFinite(p) && p > 0, okF = f != null && isFinite(f) && f >= 0;
      const over = st.side === 'sell' && okQ && q > H + EPS;
      const full = st.side === 'sell' && okQ && !over && Math.abs(q - H) <= EPS;
      const r = { H, A, q, p, f, C, okQ, okP, okF, over, full, valid: !!st.sid && okQ && okP && okF && !over };
      if (okQ && !over) {
        r.newQty = st.side === 'buy' ? H + q : Math.max(0, H - q);
        if (st.side === 'buy') r.newAvg = okP ? ((A != null && H > 0 ? A : p) * H + p * q) / (H + q) : null;
        else r.newAvg = r.newQty > EPS ? A : null;
      }
      if (okQ && okP && okF && !over && C != null) r.newCash = st.side === 'buy' ? C - (p * q + f) : C + p * q - f;
      if (st.side === 'sell' && okQ && okP && okF && !over && A != null) r.pnl = (p - A) * q - f;
      return r;
    }

    // ── 그리기 ──
    let refs = {};
    function render() {
      // a late load re-draws the form: keep the caret where the person is typing
      const act = document.activeElement;
      const keepId = act && box.contains(act) ? act.id : null;
      const caret = keepId && act.selectionStart != null ? [act.selectionStart, act.selectionEnd] : null;
      draw();
      if (keepId) {
        const n = document.getElementById(keepId);
        if (n) { n.focus(); if (caret && n.setSelectionRange) { try { n.setSelectionRange(caret[0], caret[1]); } catch (e) { /* date */ } } }
      }
    }
    function draw() {
      clear(box);
      refs = {};
      refs.warn = el('div', { className: 'tdl-warn' });
      box.appendChild(refs.warn);
      const form = el('div', { className: 'tdl-form form-stack' });
      const prev = el('aside', { className: 'tdl-prev', 'aria-label': '기록하면 바뀌는 것' });
      box.appendChild(el('div', { className: 'tdl-cols' }, [form, prev]));
      refs.prev = prev;
      if (!st.sid) { form.appendChild(symbolPicker()); prev.hidden = true; return; }
      const h = hold(), c = ccy();
      const nm = stk.displayName(h, st.meta, st.sid);
      form.appendChild(el('div', { className: 'tdl-sym' }, [
        el('div', { className: 'tdl-sym-n' }, [el('strong', { className: 'ellipsis', title: nm }, [nm]), el('span', { className: 't-caption t-muted' }, [region()])]),
        el('span', { className: 't-caption t-muted' }, [h && held() ? '보유 ' + S.fmtQty(h.qty) + '주 · 평단 ' + S.fmtPrice(h.avg_cost, c) : h ? '관심 · 수량 없음' : '이 계좌에 없음']),
      ]));
      // 구분
      const side = S.segmented({ label: '구분', value: st.side, items: [{ value: 'buy', label: LABELS.buy }, { value: 'sell', label: LABELS.sell }],
        onChange: val => { st.side = val; render(); } });
      if (!held()) { side.querySelector('[data-value="sell"]').disabled = true; side.querySelector('[data-value="sell"]').title = '보유가 있을 때만 매도를 기록해요'; }
      form.appendChild(el('div', { className: 'field' }, [el('span', { className: 'field-label' }, ['구분']), side]));
      // 날짜
      form.appendChild(dateField());
      // 수량
      refs.qty = el('input', { className: 'input', id: 'tdl-qty', inputmode: 'decimal', autocomplete: 'off', value: st.qty, 'aria-describedby': 'tdl-qty-err',
        onInput: e => { st.qty = e.target.value; refresh(); } });
      refs.qtyErr = el('p', { className: 'field-error', id: 'tdl-qty-err', role: 'alert' });
      const qtyKids = [el('div', { className: 'tdl-inp' }, [refs.qty, el('span', { className: 'tdl-suffix', 'aria-hidden': 'true' }, ['주'])])];
      if (st.side === 'sell') qtyKids.push(el('button', { type: 'button', className: 'btn chip', onClick: () => { st.qty = String(h.qty); refs.qty.value = st.qty; refresh(); } }, [LABELS.sellAll]));
      form.appendChild(el('div', { className: 'field' }, [
        el('div', { className: 'tdl-lab' }, [el('label', { className: 'field-label', for: 'tdl-qty' }, ['수량']),
          h && held() ? el('span', { className: 'field-label' }, ['보유 ' + S.fmtQty(h.qty) + '주']) : null]),
        el('div', { className: 'tdl-row' }, qtyKids), refs.qtyErr]));
      // 가격
      refs.price = el('input', { className: 'input', id: 'tdl-price', inputmode: 'decimal', autocomplete: 'off', value: st.price, placeholder: '가격 입력',
        onInput: e => { st.price = e.target.value; st.priceTouched = true; refresh(); } });
      refs.priceChip = el('div', { className: 'tdl-chipslot' });
      form.appendChild(el('div', { className: 'field' }, [el('label', { className: 'field-label', for: 'tdl-price' }, ['가격 (' + (c === 'USD' ? '$' : '₩') + ')']),
        el('div', { className: 'tdl-inp' }, [refs.price, refs.priceChip])]));
      // 수수료 · 메모
      refs.fee = el('input', { className: 'input', id: 'tdl-fee', inputmode: 'decimal', autocomplete: 'off', value: st.fee, placeholder: '0',
        onInput: e => { st.fee = e.target.value; refresh(); } });
      form.appendChild(el('div', { className: 'field' }, [el('label', { className: 'field-label', for: 'tdl-fee' }, ['수수료 (' + (c === 'USD' ? '$' : '₩') + ')']), refs.fee]));
      refs.note = el('input', { className: 'input', id: 'tdl-note', autocomplete: 'off', value: st.note, placeholder: '선택', maxlength: '200',
        onInput: e => { st.note = e.target.value; } });
      form.appendChild(el('div', { className: 'field' }, [el('label', { className: 'field-label', for: 'tdl-note' }, ['메모']), refs.note]));
      refs.full = el('div', { className: 'tdl-full' });
      form.appendChild(refs.full);
      refresh();
    }
    function dateField() {
      const t = today(), y = stk.addDays(t, -1);
      const cur = dateOf();
      const chip = (label, val, pressed) => el('button', { type: 'button', className: 'btn chip', 'aria-pressed': pressed ? 'true' : 'false',
        onClick: () => { st.custom = false; st.pickerOpen = false; st.date = val === t ? null : val; if (!st.priceTouched) maybePrefill(); render(); } }, [label]);
      const custom = st.custom && st.date && st.date !== y;
      const picker = el('input', { className: 'input tdl-date', type: 'date', max: t, value: cur, 'aria-label': '거래 날짜',
        onChange: e => {
          const val = e.target.value;
          if (!val) return;
          if (val > t) { e.target.value = cur; ACC.toast('미래 날짜는 고를 수 없어요'); return; }
          st.custom = true; st.date = val === t ? null : val;
          if (!st.priceTouched) maybePrefill();
          render();
        } });
      picker.hidden = !st.pickerOpen;
      const kids = [
        chip('오늘', t, !st.date),
        chip('어제', y, st.date === y && !custom),
        el('button', { type: 'button', className: 'btn chip', 'aria-pressed': custom ? 'true' : 'false',
          onClick: () => { st.custom = true; st.pickerOpen = true; picker.hidden = false; try { picker.showPicker(); } catch (e) { picker.focus(); } } },
          [custom ? S.fmtDate(st.date) : LABELS.pickDate]),
      ];
      const tz = stk.tz(region());
      const when = !st.date ? S.fmtDateTime(new Date(), tz)
        : S.fmtDate(st.date) + ' ' + CLOSE[region()].map(n => String(n).padStart(2, '0')).join(':') + ' · 장 마감 기준';
      const out = el('div', { className: 'field' }, [el('span', { className: 'field-label' }, ['날짜']),
        el('div', { className: 'tdl-chips', role: 'group', 'aria-label': '날짜' }, kids), picker,
        el('span', { className: 't-caption t-muted' }, [when])]);
      const prevDay = holidayPrev(cur);
      if (prevDay) {
        out.appendChild(el('p', { className: 't-caption t-warn', role: 'status' }, ['휴장일이에요 · 직전 거래일 ' + S.fmtDate(prevDay) + '로 바꿀까요?']));
        out.appendChild(el('div', {}, [el('button', { type: 'button', className: 'btn chip', onClick: () => {
          st.custom = prevDay !== y; st.date = prevDay === t ? null : prevDay; render(); } }, [LABELS.change])]));
      }
      return out;
    }
    function refresh() {
      if (!st.sid || !refs.qty) return;
      const r = calc(), c = ccy();
      // 수량 칸
      refs.qty.setAttribute('aria-invalid', r.over ? 'true' : 'false');
      refs.qtyErr.textContent = r.over ? '보유 ' + S.fmtQty(r.H) + '주보다 많아요' : '';
      refs.qtyErr.hidden = !r.over;
      // 가격 칩: 오늘 = 현재가 · 지난날 = 그날 종가 (일봉이 있을 때)
      clear(refs.priceChip);
      const cp = currentPrice();
      const day = dateOf();
      const closeDay = holidayPrev(day) || day;
      if (!st.date && cp.price != null) {
        refs.priceChip.appendChild(el('button', { type: 'button', className: 'btn chip chip-quiet', title: '현재가로 채우기',
          onClick: () => { st.price = grouped(cp.price); refs.price.value = st.price; st.priceTouched = true; refresh(); } }, [LABELS.currentPrice]));
      } else if (st.date && closeOf(closeDay) != null) {
        const px = closeOf(closeDay);
        refs.priceChip.appendChild(el('button', { type: 'button', className: 'btn chip chip-quiet', title: '그날 종가로 채우기',
          onClick: () => { st.price = grouped(px); refs.price.value = st.price; st.priceTouched = true; refresh(); } }, [S.fmtDate(closeDay) + ' 종가']));
      }
      // 전량 · 마이너스 예수금
      clear(refs.full); clear(refs.warn);
      if (r.full) refs.full.appendChild(S.banner({ kind: 'warn', title: '전량 매도라 목록에서 빠져요', sub: '거래 이력에는 남아요' }));
      if (r.newCash != null && r.newCash < 0) refs.warn.appendChild(S.banner({ kind: 'warn', title: '예수금이 마이너스가 돼요', sub: '기록은 할 수 있어요 · 나중에 예수금을 맞춰요' }));
      // 미리보기
      const to = (a, b) => [a, ' → ', b];
      const qtyLine = to(S.fmtQty(r.H), r.newQty == null ? '—' : S.fmtQty(r.newQty) + '주');
      let avgLine;
      if (r.newQty == null) avgLine = [S.fmtPrice(r.A, c) + ' → —'];
      else if (st.side === 'sell' && r.newQty > EPS) avgLine = [S.fmtPrice(r.A, c) + ' 그대로'];
      else if (r.newAvg == null) avgLine = to(S.fmtPrice(r.A, c), '—');
      else if (r.A != null && Math.abs(r.newAvg - r.A) < 1e-6) avgLine = [S.fmtPrice(r.A, c) + ' 그대로'];
      else avgLine = r.A == null ? ['— → ' + S.fmtPrice(r.newAvg, c)] : to(S.fmtPrice(r.A, c), S.fmtPrice(r.newAvg, c));
      const cashLine = r.C == null ? ['입력 전'] : to(S.fmtMoney(r.C, c), r.newCash == null ? '—' : S.fmtMoney(r.newCash, c));
      const pv = refs.prev;
      clear(pv);
      pv.hidden = false;
      const item = (label, kids, cls) => el('div', { className: 'tdl-pv' }, [el('span', { className: 't-caption t-muted' }, [label]),
        el('span', { className: 'tdl-pv-v ' + (cls || '') }, kids)]);
      const detail = el('div', { className: 'tdl-pv-detail' + (st.prevOpen ? ' is-open' : ''), id: 'tdl-pv-detail' }, [
        el('p', { className: 't-caption t-muted sm-up' }, ['기록하면']),
        item('수량', qtyLine), item('평단', avgLine), item('예수금', cashLine, r.newCash != null && r.newCash < 0 ? 't-warn' : ''),
        st.side === 'sell'
          ? el('div', { className: 'tdl-pv tdl-pv-pnl' }, [el('span', { className: 't-caption t-muted', title: '(매도가 − 평단) × 수량 − 수수료' }, ['이번 매도 손익 ', svgIco('info')]),
              el('span', { className: 'tdl-pv-v tdl-pnl ' + (r.pnl == null ? '' : r.pnl > 0 ? 't-up' : r.pnl < 0 ? 't-down' : '') }, [r.pnl == null ? '—' : S.fmtPnl(r.pnl, c)])])
          : el('p', { className: 't-caption t-muted tdl-pv-note' }, ['매수 수수료는 평단에 없어요']),
      ]);
      const sumTxt = '수량 ' + qtyLine.join('') + ' · 예수금 ' + (r.C == null ? '입력 전' : r.newCash == null ? '—' : S.fmtMoney(r.newCash, c));
      const toggle = el('button', { type: 'button', className: 'btn tdl-pv-toggle sm-down', 'aria-expanded': st.prevOpen ? 'true' : 'false', 'aria-controls': 'tdl-pv-detail',
        onClick: () => { st.prevOpen = !st.prevOpen; detail.classList.toggle('is-open', st.prevOpen); toggle.setAttribute('aria-expanded', st.prevOpen ? 'true' : 'false'); } }, [
        el('span', { className: 'tdl-pv-sum' }, [el('span', { className: 't-caption' }, [sumTxt]),
          st.side === 'sell' ? el('span', { className: 't-caption' }, ['이번 매도 손익 ',
            el('span', { className: r.pnl == null ? '' : r.pnl > 0 ? 't-up' : r.pnl < 0 ? 't-down' : '' }, [r.pnl == null ? '—' : S.fmtPnl(r.pnl, c)])])
            : el('span', { className: 't-caption t-muted' }, ['평단 ', avgLine.join('')])]),
        svgIco('chevron-down')]);
      pv.appendChild(toggle);
      pv.appendChild(detail);
      // 기록 버튼
      if (d) {
        const main = d.el.querySelector('.dlg-main');
        if (main && !main.dataset.lockedBy) main.disabled = !r.valid;
      }
    }

    // ── 종목 고르기 (계좌 목록의 「거래 기록」에서 열 때) ──
    function symbolPicker() {
      const list = el('div', { className: 'tdl-results', role: 'listbox', 'aria-label': '종목 검색 결과' });
      const inp = el('input', { className: 'input', type: 'search', placeholder: '종목 이름이나 코드', autocomplete: 'off', 'aria-label': '종목 검색' });
      let seq = 0;
      const pick = sid => { st.sid = sid; st.meta = undefined; st.quote = undefined; st.bars = undefined; if (st.side === 'sell' && !held()) st.side = 'buy'; loadSym(); render(); };
      const row = (sid, nm, sub) => el('button', { type: 'button', className: 'btn tdl-res', role: 'option', onClick: () => pick(sid) },
        [el('span', { className: 'ellipsis' }, [nm]), el('span', { className: 't-caption t-muted' }, [sub])]);
      function show(q) {
        clear(list);
        const hs = (ACC.data && ACC.data.holdings || []).filter(h => !q || (h.name_kr || '').toLowerCase().indexOf(q.toLowerCase()) >= 0 || h.symbol_id.toLowerCase().indexOf(q.toLowerCase()) >= 0);
        hs.slice(0, 8).forEach(h => list.appendChild(row(h.symbol_id, h.name_kr || h.code, (ACC.held(h) ? '보유 ' + S.fmtQty(h.qty) + '주' : '관심') + ' · ' + h.symbol_id.replace(':', ' · '))));
        if (!q) return;
        const my = ++seq;
        S.api('/api/universe/search?q=' + encodeURIComponent(q) + '&limit=8', { toast: false }).then(r => {
          if (my !== seq) return;
          (r.results || []).filter(x => !hs.some(h => h.symbol_id === x.symbol_id)).forEach(x =>
            list.appendChild(row(x.symbol_id, x.name_kr || x.name_en || x.code, x.symbol_id.replace(':', ' · ') + ' · ' + (x.market || ''))));
          if (!list.firstChild) list.appendChild(el('p', { className: 't-caption t-muted' }, ['찾는 종목이 없어요']));
        }, () => { if (my === seq && !list.firstChild) list.appendChild(el('p', { className: 't-caption t-error' }, ['검색하지 못했어요 · 잠시 뒤 다시 해 주세요'])); });
      }
      let tmr = null;
      inp.addEventListener('input', () => { clearTimeout(tmr); tmr = setTimeout(() => show(inp.value.trim()), 200); });
      show('');
      setTimeout(() => inp.focus(), 0);
      return el('div', { className: 'field' }, [el('span', { className: 'field-label' }, ['종목']), inp, list]);
    }

    function loadSym() {
      if (!st.sid) return;
      const sid = st.sid;
      stk.meta(sid).then(m => { if (st.sid !== sid) return; st.meta = m; render(); }, () => { st.meta = null; });
      stk.daily(sid).then(b => { if (st.sid !== sid) return; st.bars = b; render(); }, () => { st.bars = null; });
      if (!hold()) {
        // no prefill from this late quote: it would count as an edit (버리기 확인)
        stk.quote(sid).then(q => { if (st.sid !== sid) return; st.quote = q; refresh(); }, () => { st.quote = null; });
      }
    }

    maybePrefill();
    loadSym();
    render();
    d = S.dialog({
      title: LABELS.recordTrade, wide: true, body: box, primary: { label: LABELS.record },
      account: ACC.accountRef(), confirmDiscard: true,
      verifyText: { title: '기록됐는지 확인 중', sub: '응답이 끊겨서 거래 이력을 확인하고 있어요' },
      failText: { title: '저장하지 못했어요', sub: '거래 이력에 없는 걸 확인했어요 · 입력은 그대로예요' },
      onSubmit: async ctx => {
        const r = calc();
        if (!r.valid) { const e = new Error(r.over ? '보유보다 많이 팔 수 없어요' : '수량·가격을 확인해 주세요'); e.status = 400; throw e; }
        const body = { symbol_id: st.sid, side: st.side, qty: r.q, price: r.p, fee: r.f, note: (st.note || '').trim() || null };
        if (st.date) body.traded_at = stk.zonedEpoch(st.date, CLOSE[region()][0], CLOSE[region()][1], stk.tz(region()));
        st.sent = body;
        try {
          return await ctx.api(ACC.path('/trades'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        } catch (e) {
          if (e.status === 400 || e.status === 404) {
            const msg = /more than held/.test(e.message) ? '보유보다 많이 팔 수 없어요'
              : /not in universe/.test(e.message) ? '종목 목록에 없는 종목이에요'
              : /qty/.test(e.message) ? '수량을 확인해 주세요' : /price/.test(e.message) ? '가격을 확인해 주세요'
              : /fee/.test(e.message) ? '수수료를 확인해 주세요' : '입력을 확인해 주세요';
            const x = new Error(msg); x.status = e.status; throw x;
          }
          throw e;
        }
      },
      // the write may have landed before the line dropped: look before resending
      verify: async () => {
        const b = st.sent;
        if (!b) return false;
        const r = await S.api(ACC.path('/trades') + '?symbol_id=' + encodeURIComponent(b.symbol_id) + '&limit=5', { toast: false, gate: false });
        const hit = (r.trades || []).find(t => t.side === b.side && Math.abs(t.qty - b.qty) < EPS && Math.abs(t.price - b.price) < 1e-6
          && Math.abs((t.fee || 0) - b.fee) < 1e-6 && (b.traded_at == null || t.traded_at === b.traded_at) && (t.created_at || 0) >= opened - 5);
        return hit ? { trade: hit, verified: true } : false;
      },
    });
    refresh();
    // a banner (확인 중 · 실패 · 마이너스 예수금) sits at the top of a sheet the
    // person has scrolled to its bottom to press 기록 — bring it into view
    const slot = d.el.querySelector('.s-dialog-banner');
    if (slot && window.MutationObserver) {
      const mo = new MutationObserver(() => { if (slot.firstChild) d.el.scrollTo({ top: 0, behavior: 'auto' }); });
      mo.observe(slot, { childList: true });
      d.done.then(() => mo.disconnect());
    }
    d.done.then(res => {
      if (!res || !st.sent) return;
      const b = st.sent;
      const nm = stk.displayName(hold(), st.meta, b.symbol_id);
      const removed = res.holding ? !!res.holding.removed : (b.side === 'sell' && Math.abs((hold() ? hold().qty : 0) - b.qty) < EPS);
      const view = { label: LABELS.viewTrade, onClick: () => ACC.go({ v: 'trades', s: b.symbol_id }) };
      if (removed) ACC.toast('전량 매도를 기록했어요 · 거래 이력에 남아요', { action: view });
      else ACC.toast((b.side === 'buy' ? LABELS.buy : LABELS.sell) + ' ' + S.fmtQty(b.qty) + '주를 기록했어요 · ' + nm, { action: view });
      stk.forget('daily:' + b.symbol_id);
      Promise.resolve(ACC.reload({ quiet: true })).then(() => stk.emit('trade', { symbol_id: b.symbol_id, removed, trade: res.trade || null }));
    });
  };
})();
