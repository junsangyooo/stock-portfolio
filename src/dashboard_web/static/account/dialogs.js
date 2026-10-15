// account/dialogs.js — 계좌 문맥의 대화상자: DLG-ADD 종목 추가 · DLG-CASH 예수금 정정 ·
// DLG-CONFIRM 공용 확인. 모두 S.dialog 위에 선다(제출 중 잠금 · 실패 시 입력 유지 +
// 다시 시도 · 403이면 PIN 다시 입력 후 1회 재전송).
//   ACC.openAdd({ symbol?: universe row | symbol_id, mode?: 'watch'|'existing' }) → Promise
//   ACC.openCash(currency?) → Promise
//   ACC.confirm({ title, body, note, danger, confirmLabel, action: async (ctx) => result,
//                 extra: { label, onClick } }) → Promise<result | null>
//     action(ctx): ctx.api는 게이트·토스트를 끈 S.api. 던지면 대화상자가 실패를 보여 준다.
//     extra: 오른쪽 주 버튼(예: 매도 기록) — 그때 danger 버튼은 왼쪽으로 간다.
//   ACC.numberInput({ value, ccy, suffix, label }) → { el, input, value() }  (쉼표 서식)
(function () {
  'use strict';
  const S = window.S;
  const ACC = window.ACC;
  const { el, clear, svgIco, parseNum } = S;

  function fmtPlain(v, ccy) {
    if (v == null || isNaN(v)) return '';
    const digits = ccy === 'USD' ? 2 : 0;
    return Number(v).toLocaleString('en-US', { maximumFractionDigits: ccy === 'qty' ? 4 : digits });
  }

  // a money / quantity box with a prefix (₩ $) or suffix (주), comma-formatted on blur
  ACC.numberInput = function (o) {
    o = o || {};
    const input = el('input', { className: 'input num-input', inputmode: 'decimal', autocomplete: 'off',
      value: o.value != null ? fmtPlain(o.value, o.ccy) : '', placeholder: o.placeholder || '',
      'aria-label': o.label || null, id: o.id || null });
    input.addEventListener('blur', () => {
      const v = parseNum(input.value);
      if (v != null && !isNaN(v)) input.value = fmtPlain(v, o.ccy);
    });
    input.addEventListener('focus', () => { input.value = input.value.replace(/,/g, ''); });
    const prefix = o.ccy === 'KRW' ? '₩' : o.ccy === 'USD' ? '$' : null;
    const wrap = el('div', { className: 'num-wrap' + (prefix ? ' has-prefix' : '') + (o.suffix ? ' has-suffix' : '') }, [
      prefix ? el('span', { className: 'num-affix is-pre', 'aria-hidden': 'true' }, [prefix]) : null,
      input,
      o.suffix ? el('span', { className: 'num-affix is-suf', 'aria-hidden': 'true' }, [o.suffix]) : null]);
    return { el: wrap, input, value: () => parseNum(input.value) };
  };
  function field(label, control, hint, id) {
    const lab = el('label', { className: 'field-label', for: id || null }, [label]);
    return el('div', { className: 'field' }, [lab, control, hint ? el('p', { className: 'field-hint' }, [hint]) : null]);
  }
  let uid = 0;
  const nid = p => p + '-' + (++uid);

  // ── DLG-CONFIRM ─────────────────────────────────────────────────────
  ACC.confirm = function (o) {
    o = o || {};
    const body = () => {
      const kids = [];
      if (o.body) kids.push(typeof o.body === 'string' ? el('p', { className: 'cf-body' }, [o.body]) : o.body);
      if (o.note) kids.push(el('p', { className: 'dlg-note' }, [svgIco('info'), o.note]));
      return kids.length ? el('div', { className: 'form-stack' }, kids) : null;
    };
    const d = S.dialog({
      title: o.title || S.LABELS.confirm,
      body,
      primary: { label: o.confirmLabel || (o.danger ? S.LABELS.delete : S.LABELS.confirm), danger: !!o.danger },
      cancelLabel: S.LABELS.cancel,
      keepCancel: true,
      initialFocus: 'cancel',
      account: ACC.accountRef(),
      onSubmit: o.action ? ctx => o.action(ctx) : null,
      onClose: o.onClose,
    });
    if (o.extra) {
      const foot = d.el.querySelector('.s-dialog-foot');
      foot.classList.add('has-extra');
      foot.appendChild(el('button', { type: 'button', className: 'btn btn-primary dlg-extra',
        onClick: () => { d.close(null, true); o.extra.onClick(); } }, [o.extra.label]));
    }
    return d.done.then(v => (v === null || v === undefined ? null : v));
  };

  // ── DLG-CASH 예수금 정정 ─────────────────────────────────────────────
  ACC.openCash = function (ccy) {
    const d0 = ACC.data || {};
    const bal = Object.assign({}, d0.balances || {});
    const base = ((d0.account || {}).base_currency) || 'KRW';
    const shown = [ccy || base];
    Object.keys(bal).forEach(c => { if (!shown.includes(c) && bal[c]) shown.push(c); });
    const rows = {};
    let list, addBtn;
    function rowFor(c) {
      const id = nid('cash');
      const now = Object.prototype.hasOwnProperty.call(bal, c) ? bal[c] : null;
      const box = ACC.numberInput({ ccy: c, value: now, label: c + ' 예수금', id });
      const diff = el('p', { className: 'cash-diff' });
      const upd = () => {
        const v = box.value();
        clear(diff);
        if (v == null || isNaN(v) || now == null) { diff.hidden = true; return; }
        diff.hidden = false;
        diff.append(el('span', { className: 't-muted' }, ['차이 ']),
          el('b', { className: 't-num' }, [S.fmtPnl(v - now, c)]));
      };
      box.input.addEventListener('input', upd);
      upd();
      rows[c] = box;
      return el('div', { className: 'cash-row' }, [
        el('div', { className: 'cash-row-head' }, [el('label', { for: id, className: 'cash-ccy' }, [c]),
          el('span', { className: 't-muted t-caption' }, [now == null ? '입력 전' : '지금 ' + S.fmtMoney(now, c)])]),
        box.el, diff]);
    }
    function addCcy() {
      const next = ['KRW', 'USD'].find(c => !shown.includes(c));
      if (!next) return;
      shown.push(next);
      list.appendChild(rowFor(next));
      rows[next].input.focus();
      addBtn.hidden = !['KRW', 'USD'].some(c => !shown.includes(c));
    }
    const d = S.dialog({
      title: S.LABELS.fixCash,
      confirmDiscard: true,
      account: ACC.accountRef(),
      primary: { label: S.LABELS.save },
      body: () => {
        list = el('div', { className: 'form-stack' }, shown.map(rowFor));
        addBtn = el('button', { type: 'button', className: 'btn chip cash-add', onClick: addCcy }, [svgIco('plus'), '통화 추가']);
        addBtn.hidden = !['KRW', 'USD'].some(c => !shown.includes(c));
        return el('div', { className: 'form-stack' }, [
          el('p', { className: 'dlg-note' }, [svgIco('info'), '입출금 기록이 아니라 숫자만 맞춰요']),
          list, el('div', {}, [addBtn])]);
      },
      onSubmit: async ctx => {
        const out = {};
        for (const c of Object.keys(rows)) {
          const v = rows[c].value();
          if (v == null) continue;
          if (isNaN(v) || v < 0) { const e = new Error(c + ' 금액을 숫자로 넣어 주세요'); e.status = 400; throw e; }
          out[c] = v;
        }
        if (!Object.keys(out).length) { const e = new Error('금액을 넣어 주세요'); e.status = 400; throw e; }
        return ctx.api(ACC.path('/balances'), { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ balances: out }) });
      },
    });
    return d.done.then(r => {
      if (r) { ACC.toast('예수금을 맞췄어요'); ACC.reload({ quiet: true }); }
      return r;
    });
  };

  // ── DLG-ADD 종목 추가 (관심 | 기존 보유 등록) ───────────────────────────
  ACC.openAdd = function (o) {
    o = o || {};
    let sym = typeof o.symbol === 'string' ? { symbol_id: o.symbol } : o.symbol || null;
    let mode = o.mode === 'existing' ? 'existing' : 'watch';
    let qty, avg, memo, dlg;
    const host = el('div', { className: 'add-host' });

    function pickStep() {
      clear(host);
      const input = el('input', { type: 'search', className: 'input', placeholder: '이름이나 코드', autocomplete: 'off',
        'aria-label': '추가할 종목 찾기' });
      const results = el('div', { className: 'add-results', role: 'listbox', 'aria-label': '찾은 종목' });
      let seq = 0;
      input.addEventListener('input', async () => {
        const q = input.value.trim();
        const my = ++seq;
        clear(results);
        if (q.length < 2) return;
        results.appendChild(el('p', { className: 't-muted t-caption' }, ['찾는 중']));
        let found;
        try { found = (await S.api('/api/universe/search?q=' + encodeURIComponent(q) + '&limit=8', { toast: false })).results || []; }
        catch (e) { found = null; }
        if (my !== seq) return;
        clear(results);
        if (found === null) { results.appendChild(el('p', { className: 't-error t-caption' }, ['찾지 못했어요 · 잠시 뒤 다시 해 주세요'])); return; }
        const own = new Set(((ACC.data || {}).holdings || []).map(h => h.symbol_id));
        if (!found.length) { results.appendChild(el('p', { className: 't-muted t-caption' }, ['맞는 종목이 없어요'])); return; }
        found.forEach(r => results.appendChild(el('button', { type: 'button', className: 'btn lr-item', role: 'option',
          disabled: own.has(r.symbol_id),
          onClick: () => { sym = r; formStep(); } }, [
          el('span', { className: 'ellipsis' }, [r.name_kr || r.name_en || r.code]),
          el('span', { className: 't-muted' }, [own.has(r.symbol_id) ? '이미 목록에 있어요' : r.code + ' · ' + (r.is_etf ? 'ETF' : r.market || '')])])));
      });
      host.append(field('종목', input, null), results);
      setTimeout(() => input.focus(), 0);
      if (dlg) dlg.setMainLabel(S.LABELS.save);
    }

    function formStep() {
      clear(host);
      const sid = sym.symbol_id;
      const region = ACC.region(sid);
      const ccy = sym.currency || (region === 'US' ? 'USD' : 'KRW');
      const name = sym.name_kr || sym.name_en || sym.code || sid;
      const priceLine = el('p', { className: 'add-price t-muted t-caption' }, ['현재가 불러오는 중']);
      S.api('/api/market/quotes?symbols=' + encodeURIComponent(sid) + '&max_age_s=300', { toast: false }).then(r => {
        const q = (r.quotes || {})[sid];
        priceLine.textContent = q && q.price != null
          ? '현재가 ' + S.fmtPrice(q.price, ccy) + (q.ts ? ' · ' + S.fmtTime(q.ts) : '') : '현재가 없음';
      }).catch(() => { priceLine.textContent = '현재가를 받지 못했어요'; });
      const radio = (value, title, sub) => {
        const id = nid('mode');
        const inp = el('input', { type: 'radio', name: 'add-mode', value, id, checked: mode === value,
          onChange: () => { mode = value; paint(); } });
        return el('label', { className: 'add-mode', for: id }, [inp,
          el('span', { className: 'add-mode-text' }, [el('b', {}, [title]), el('span', { className: 't-muted t-caption' }, [sub])])]);
      };
      const qid = nid('qty'), aid = nid('avg'), mid = nid('memo');
      qty = ACC.numberInput({ ccy: 'qty', suffix: '주', id: qid });
      avg = ACC.numberInput({ ccy, id: aid });
      memo = el('input', { className: 'input', id: mid, placeholder: '선택', maxlength: '200', autocomplete: 'off' });
      const cost = el('b', { className: 't-num' }, ['—']);
      const existing = el('div', { className: 'form-stack add-existing' }, [
        el('div', { className: 'field-row' }, [field('수량', qty.el, null, qid), field('평단 (' + (ccy === 'USD' ? '$' : '₩') + ')', avg.el, null, aid)]),
      ]);
      const sumBox = el('dl', { className: 'add-sum' }, [
        el('div', {}, [el('dt', {}, ['매입금액']), el('dd', {}, [cost])]),
        el('div', {}, [el('dt', {}, ['예수금']), el('dd', {}, ['예수금은 그대로예요'])])]);
      const toTrade = el('button', { type: 'button', className: 'btn pin-link add-trade',
        onClick: () => { dlg.close(null, true); ACC.openTrade && ACC.openTrade({ symbol_id: sid, side: 'buy' }); } },
        ['오늘 산 거라면 거래 기록으로', svgIco('chevron-right')]);
      const watchNote = el('p', { className: 'dlg-note' }, [svgIco('info'), '관심 종목은 자동 분석 대상이 아니에요']);
      const upd = () => {
        const q = qty.value(), a = avg.value();
        cost.textContent = q > 0 && a > 0 ? S.fmtMoney(q * a, ccy) : '—';
      };
      qty.input.addEventListener('input', upd);
      avg.input.addEventListener('input', upd);
      function paint() {
        const ex = mode === 'existing';
        existing.hidden = !ex; sumBox.hidden = !ex; toTrade.hidden = !ex; watchNote.hidden = ex;
        host.querySelectorAll('.add-mode').forEach(l => l.classList.toggle('is-on', l.querySelector('input').checked));
      }
      host.append(
        el('div', { className: 'add-sym' }, [
          el('p', {}, [el('b', { className: 'add-name' }, [name]), el('span', { className: 't-muted t-caption' }, [' ' + (sym.is_etf ? 'ETF' : region)])]),
          priceLine]),
        el('div', { className: 'add-modes', role: 'radiogroup', 'aria-label': '추가 방식' }, [
          radio('watch', '관심', '수량 없이 따라봐요'), radio('existing', '기존 보유 등록', '이미 들고 있던 종목을 넣어요')]),
        existing,
        field('메모', memo, null, mid),
        sumBox, watchNote, toTrade);
      paint();
      if (o.symbol == null && dlg) {
        // came through the search step: let the person go back to it
        host.insertBefore(el('button', { type: 'button', className: 'btn pin-link add-back',
          onClick: () => { sym = null; pickStep(); } }, [svgIco('arrow-left'), '다른 종목']), host.firstChild);
      }
      setTimeout(() => (mode === 'existing' ? qty.input : host.querySelector('input[type=radio]:checked')).focus(), 0);
    }

    dlg = S.dialog({
      title: '종목 추가',
      confirmDiscard: true,
      account: ACC.accountRef(),
      primary: { label: S.LABELS.save },
      body: () => host,
      onSubmit: async ctx => {
        if (!sym) { const e = new Error('추가할 종목을 골라 주세요'); e.status = 400; throw e; }
        const body = { symbol_id: sym.symbol_id, create_only: true, note: memo.value.trim() || null };
        if (mode === 'existing') {
          const q = qty.value(), a = avg.value();
          if (!(q > 0)) { const e = new Error('수량을 넣어 주세요'); e.status = 400; throw e; }
          if (!(a > 0)) { const e = new Error('평단을 넣어 주세요'); e.status = 400; throw e; }
          body.qty = q; body.avg_cost = a;
        }
        const r = await ctx.api(ACC.path('/holdings'), { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body) });
        return Object.assign({ mode }, r);
      },
    });
    if (sym) formStep(); else pickStep();
    return dlg.done.then(r => {
      if (r && r !== true) {
        const sid = sym.symbol_id;
        ACC.toast(r.mode === 'existing' ? '보유에 등록했어요' : '관심에 더했어요',
          { action: { label: '보기', onClick: () => ACC.go({ s: sid }) } });
        if (r.backfill_failed) setTimeout(() => ACC.toast('가격 기록은 다음 갱신 때 받아요'), 400);
        ACC.reload({ quiet: true });
      }
      return r;
    });
  };
})();
