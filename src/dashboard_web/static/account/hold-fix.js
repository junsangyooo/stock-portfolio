// account/hold-fix.js — DLG-HOLD-FIX 보유 정정.
//   ACC.openHoldFix(symbol_id, { focus: 'note' }?)   (메모 편집은 focus: 'note')
// 거래 없이 수량·평단·표시 이름·메모를 바로잡는다. POST /holdings는 보낸 값을
// 그대로 쓰므로 네 칸을 늘 함께 보낸다 — 메모만 고쳐도 수량이 지워지지 않게
// (인계 §4-9). 예수금은 그대로. 0주로 만드는 건 매도 기록이나 종목 삭제의 일.
(function () {
  'use strict';
  const S = window.S;
  const ACC = window.ACC;
  if (!S || !ACC) return;
  const { el, svgIco, LABELS } = S;
  const stk = ACC.stk;
  const parseNum = s => { const t = String(s == null ? '' : s).replace(/[,\s]/g, ''); return t === '' ? null : Number(t); };

  ACC.openHoldFix = function (sid, opts) {
    opts = opts || {};
    const h = ACC.holding(sid);
    if (!h) { ACC.toast('이 계좌에 없는 종목이에요'); return; }
    const wasHeld = ACC.held(h);
    const c = h.currency || (stk ? stk.ccyOf(h, null, sid) : 'KRW');
    const region = String(sid).split(':')[0];
    const field = (id, label, input, extra) => el('div', { className: 'field' }, [el('label', { className: 'field-label', for: id }, [label]), input].concat(extra || []));
    const qty = el('input', { className: 'input', id: 'hfx-qty', inputmode: 'decimal', autocomplete: 'off', value: h.qty != null ? S.fmtQty(h.qty).replace('−', '-') : '0',
      'aria-describedby': 'hfx-qty-err', onInput: check });
    const qtyErr = el('p', { className: 'field-error', id: 'hfx-qty-err', role: 'alert' });
    qtyErr.hidden = true;
    const avg = el('input', { className: 'input', id: 'hfx-avg', inputmode: 'decimal', autocomplete: 'off',
      value: h.avg_cost != null ? new Intl.NumberFormat('en-US', { maximumFractionDigits: 4 }).format(h.avg_cost) : '', placeholder: wasHeld ? '' : '수량이 있을 때 입력', onInput: check });
    const avgErr = el('p', { className: 'field-error', id: 'hfx-avg-err', role: 'alert' });
    avgErr.hidden = true;
    const nm = el('input', { className: 'input', id: 'hfx-name', autocomplete: 'off', value: h.name_kr || '', maxlength: '60' });
    const note = el('input', { className: 'input', id: 'hfx-note', autocomplete: 'off', value: h.note || '', placeholder: '선택', maxlength: '200' });
    const shown = { qty: qty.value, avg: avg.value };
    const body = el('div', { className: 'form-stack hfx' }, [
      el('div', { className: 'tdl-sym' }, [
        el('div', { className: 'tdl-sym-n' }, [el('strong', { className: 'ellipsis', title: h.name_kr || sid }, [h.name_kr || sid]),
          el('span', { className: 't-caption t-muted' }, [region])]),
        el('span', { className: 't-caption t-muted' }, [wasHeld ? '보유 ' + S.fmtQty(h.qty) + '주 · 평단 ' + S.fmtPrice(h.avg_cost, c) : '관심 · 수량 없음']),
      ]),
      el('p', { className: 't-caption t-muted hfx-note' }, [svgIco('info'), '거래 기록 없이 숫자만 바꿔요 · 예수금 그대로']),
      field('hfx-qty', '수량', el('div', { className: 'tdl-inp' }, [qty, el('span', { className: 'tdl-suffix', 'aria-hidden': 'true' }, ['주'])]), [qtyErr]),
      field('hfx-avg', '평단 (' + (c === 'USD' ? '$' : '₩') + ')', avg, [avgErr]),
      field('hfx-name', '표시 이름', nm, [el('p', { className: 't-caption t-muted' }, ['비우면 원래 종목명으로 돌아가요'])]),
      field('hfx-note', '메모', note),
    ]);
    let d = null;
    // → '' when valid, else the words that block 저장
    function check() {
      const q = parseNum(qty.value), a = parseNum(avg.value);
      let qe = '', ae = '';
      if (q == null || !isFinite(q) || q < 0) qe = '수량을 확인해 주세요';
      else if (q === 0 && wasHeld) qe = '0주로 만들려면 매도 기록이나 종목 삭제를 써요';
      if (q > 0 && (a == null || !isFinite(a) || a <= 0)) ae = '평단을 넣어 주세요';
      else if (a != null && (!isFinite(a) || a < 0)) ae = '평단을 확인해 주세요';
      qtyErr.textContent = qe; qtyErr.hidden = !qe; qty.setAttribute('aria-invalid', qe ? 'true' : 'false');
      avgErr.textContent = ae; avgErr.hidden = !ae; avg.setAttribute('aria-invalid', ae ? 'true' : 'false');
      if (d) {
        const main = d.el.querySelector('.dlg-main');
        if (main && !main.dataset.lockedBy) main.disabled = !!(qe || ae);
      }
      return qe || ae;
    }
    d = S.dialog({
      title: LABELS.fixHolding, body, primary: { label: LABELS.save }, account: ACC.accountRef(), confirmDiscard: true,
      initialFocus: opts.focus === 'note' ? note : qty,
      onSubmit: async ctx => {
        const bad = check();
        if (bad) { const e = new Error(bad); e.status = 400; throw e; }
        // a box left as it opened sends the stored value, not the rounded
        // text it shows (12.345678주 opens as '12.3457')
        const q = qty.value === shown.qty && h.qty != null ? +h.qty : parseNum(qty.value);
        const a = avg.value === shown.avg && h.avg_cost != null ? +h.avg_cost : parseNum(avg.value);
        const name = nm.value.trim();
        // every field goes, every time (a field left out would be written empty)
        // name: only a changed one is an override — sending the shown name back
        // would pin today's universe name to this row (null keeps what is
        // stored); an emptied box sends '' — back to the listed name
        const payload = { symbol_id: sid, qty: q, avg_cost: q > 0 ? a : (a || null), note: note.value.trim() || null,
          name: !name ? '' : name !== (h.name_kr || '') ? name : null };
        await ctx.api(ACC.path('/holdings'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        return true;
      },
    });
    check();
    if (opts.focus === 'note') { note.focus(); note.select(); }
    d.done.then(r => {
      if (r !== true) return;
      ACC.toast('보유를 정정했어요');
      ACC.reload({ quiet: true });
    });
  };
})();
