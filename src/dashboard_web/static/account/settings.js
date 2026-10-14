// account/settings.js — SCR-ACC-SETTINGS 계좌 설정(패널, ?settings=1).
// 정보 저장(PATCH) · PIN 변경({current, pin}, 403 틀림 · 429 제한) · 위험 영역 계좌 삭제
// (DLG-CONFIRM: 이름 입력 + 현재 PIN → DELETE 본문 {pin} → '/' + 토스트).
(function () {
  'use strict';
  const S = window.S;
  const ACC = window.ACC;
  const { el, clear, svgIco } = S;
  const DONE_KEY = 'pdash.flash';
  let pnl = null;

  function typeOptions(cur) {
    const types = (window.AccountsMenu && window.AccountsMenu.TYPES) || [{ value: 'GENERAL', label: '일반' }, { value: 'ISA', label: 'ISA' }];
    const list = types.slice();
    if (cur && !list.some(t => t.value === cur)) list.push({ value: cur, label: cur });
    return list.map(t => el('option', { value: t.value, selected: t.value === cur }, [t.label]));
  }
  const uid = p => p + '-' + Math.random().toString(36).slice(2, 7);
  function field(label, control, id) {
    return el('div', { className: 'field' }, [el('label', { className: 'field-label', for: id }, [label]), control]);
  }
  function busyBtn(btn, on) {
    btn.disabled = on;
    const sp = btn.querySelector('.spin');
    if (on && !sp) btn.insertBefore(S.spinner(), btn.firstChild);
    if (!on && sp) sp.remove();
  }

  function infoSection(a) {
    const ids = { name: uid('sn'), type: uid('st'), broker: uid('sb'), memo: uid('sm') };
    const name = el('input', { className: 'input', id: ids.name, value: a.name || '', maxlength: '40', autocomplete: 'off' });
    const type = el('select', { className: 'input', id: ids.type }, typeOptions(a.account_type));
    const broker = el('input', { className: 'input', id: ids.broker, value: a.broker || '', maxlength: '30', autocomplete: 'off' });
    const ccy = S.segmented({ items: [{ value: 'KRW', label: 'KRW' }, { value: 'USD', label: 'USD' }], value: a.base_currency || 'KRW', label: '기본 통화' });
    const memo = el('input', { className: 'input', id: ids.memo, value: a.note || '', maxlength: '200', autocomplete: 'off', placeholder: '선택' });
    const msg = el('div', { className: 'set-msg' });
    const save = el('button', { type: 'button', className: 'btn btn-primary' }, [S.LABELS.save]);
    const inputs = [name, type, broker, memo];
    async function submit() {
      const n = name.value.trim();
      clear(msg);
      if (!n) { msg.appendChild(S.banner({ kind: 'error', title: '표시 이름을 넣어 주세요' })); name.focus(); return; }
      busyBtn(save, true); inputs.forEach(i => { i.disabled = true; });
      const body = { name: n, account_type: type.value, broker: broker.value.trim(), base_currency: ccy.value,
        note: memo.value.trim() };
      const send = () => S.api(ACC.path(''), { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body), gate: false, toast: false });
      try {
        try { await send(); }
        catch (e) {
          if (!e.forbidden) throw e;
          if (!(await S.pinPrompt({ account: ACC.accountRef() }))) throw e;
          await send();
        }
        ACC.toast('계좌 정보를 저장했어요');
        await ACC.reload({ quiet: true });
        if (ACC.meta) Object.assign(ACC.meta, { name: body.name, broker: body.broker, account_type: body.account_type });
      } catch (e) {
        msg.appendChild(S.banner({ kind: 'error', title: '저장하지 못했어요', sub: '입력은 그대로예요',
          action: { label: S.LABELS.retry, onClick: submit } }));
      } finally {
        busyBtn(save, false); inputs.forEach(i => { i.disabled = false; });
      }
    }
    save.addEventListener('click', submit);
    return el('section', { className: 'set-sec', 'aria-labelledby': 'set-info' }, [
      el('h3', { id: 'set-info' }, ['계좌 정보']),
      el('div', { className: 'field-row' }, [field('표시 이름', name, ids.name), field('유형', type, ids.type)]),
      el('div', { className: 'field-row' }, [field('증권사', broker, ids.broker),
        el('div', { className: 'field' }, [el('span', { className: 'field-label' }, ['기본 통화']), el('div', {}, [ccy])])]),
      field('메모', memo, ids.memo),
      el('p', { className: 'dlg-note' }, [svgIco('info'), '주소 /a/' + ACC.id]),
      el('div', {}, [save]), msg]);
  }

  function pinSection() {
    let cur = '', nw = '', again = '';
    const read = p => [...p.el.querySelectorAll('.pin-cell')].map(c => c.value).join('');
    const pCur = S.pinInput({ label: '현재 PIN', onComplete: () => pNew.focus() });
    const pNew = S.pinInput({ label: '새 PIN', onComplete: () => pAgain.focus() });
    const pAgain = S.pinInput({ label: '새 PIN 다시' });
    const msg = el('div', { className: 'set-msg' });
    const btn = el('button', { type: 'button', className: 'btn' }, ['PIN 변경']);
    async function submit() {
      cur = read(pCur); nw = read(pNew); again = read(pAgain);
      clear(msg);
      pCur.error(null); pAgain.error(null);
      if (!/^\d{6}$/.test(cur)) { pCur.error('현재 PIN 6자리를 넣어 주세요'); pCur.focus(); return; }
      if (!/^\d{6}$/.test(nw)) { pNew.error('새 PIN 6자리를 넣어 주세요'); pNew.focus(); return; }
      if (nw !== again) { pAgain.error('새 PIN이 서로 달라요 · 다시 넣어 주세요'); pAgain.clear(); return; }
      busyBtn(btn, true); [pCur, pNew, pAgain].forEach(p => p.disable(true));
      const send = () => S.api(ACC.path('/pin'), { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current: cur, pin: nw }), gate: false, toast: false });
      try {
        try { await send(); }
        catch (e) {
          if (!e.forbidden) throw e;
          if (!(await S.pinPrompt({ account: ACC.accountRef() }))) throw e;
          await send();
        }
        [pCur, pNew, pAgain].forEach(p => { p.disable(false); p.clear(); });
        ACC.toast('PIN을 바꿨어요');
      } catch (e) {
        [pCur, pNew, pAgain].forEach(p => p.disable(false));
        if (e.status === 403 || e.status === 429) {
          pCur.error(e.message || 'PIN이 맞지 않아요');
          pCur.clear();
        } else {
          msg.appendChild(S.banner({ kind: 'error', title: '바꾸지 못했어요', sub: '입력은 그대로예요',
            action: { label: S.LABELS.retry, onClick: submit } }));
        }
      } finally { busyBtn(btn, false); }
    }
    btn.addEventListener('click', submit);
    const lab = t => el('span', { className: 'field-label' }, [t]);
    return el('section', { className: 'set-sec', 'aria-labelledby': 'set-pin' }, [
      el('h3', { id: 'set-pin' }, ['PIN 변경']),
      el('div', { className: 'field' }, [lab('현재 PIN'), pCur.el]),
      el('div', { className: 'field' }, [lab('새 PIN'), pNew.el]),
      el('div', { className: 'field' }, [lab('새 PIN 다시'), pAgain.el]),
      el('p', { className: 'dlg-note' }, [svgIco('info'), '다른 기기는 계속 열려 있어요']),
      el('div', {}, [btn]), msg]);
  }

  function dangerSection(a) {
    return el('section', { className: 'set-sec set-danger', 'aria-labelledby': 'set-danger' }, [
      el('h3', { id: 'set-danger' }, ['위험 영역']),
      el('p', {}, ['계좌와 그 안의 기록이 지워져요 · 되돌릴 수 없어요']),
      el('div', {}, [el('button', { type: 'button', className: 'btn btn-danger', onClick: () => deleteAccount(a) }, ['계좌 삭제'])])]);
  }

  function deleteAccount(a) {
    const name = a.name || ACC.id;
    let typed, pin, pinVal = '';
    const id = uid('dn');
    const d = S.dialog({
      title: name + ' 계좌를 삭제할까요?',
      primary: { label: S.LABELS.delete, danger: true },
      keepCancel: true,
      account: ACC.accountRef(),
      failText: { title: '삭제하지 못했어요' },
      body: () => {
        typed = el('input', { className: 'input', id, autocomplete: 'off', spellcheck: 'false', placeholder: name });
        pin = S.pinInput({ label: '현재 PIN', onComplete: v => { pinVal = v; ready(); } });
        pin.el.addEventListener('input', () => { pinVal = [...pin.el.querySelectorAll('.pin-cell')].map(c => c.value).join(''); ready(); });
        typed.addEventListener('input', ready);
        return el('div', { className: 'form-stack' }, [
          el('p', { className: 'cf-body' }, ['계좌와 그 안의 기록이 지워져요 · 되돌릴 수 없어요']),
          el('div', { className: 'field' }, [el('label', { className: 'field-label', for: id }, ['확인하려면 계좌 이름 「' + name + '」를 입력해요']), typed]),
          el('div', { className: 'field' }, [el('span', { className: 'field-label' }, ['현재 PIN']), pin.el])]);
      },
      onSubmit: async ctx => {
        try {
          return await ctx.api(ACC.path(''), { method: 'DELETE', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pin: pinVal }) });
        } catch (e) {
          if (e.status === 403 && !e.forbidden || e.status === 429) {
            // wrong or locked-out PIN: shown on the cells, not as a failed save
            pin.error(e.message || 'PIN이 맞지 않아요');
          }
          throw e;
        }
      },
    });
    function ready() {
      const main = d.el.querySelector('.dlg-main');
      if (main) main.disabled = !(typed.value.trim() === name && /^\d{6}$/.test(pinVal));
    }
    ready();
    d.done.then(r => {
      if (!r) return;
      try { sessionStorage.setItem(DONE_KEY, name + ' 계좌를 삭제했어요'); } catch (e) { /* */ }
      try { if (localStorage.getItem('pdash.lastAccount') === ACC.id) S.rememberAccount(null); } catch (e) { /* */ }
      location.replace('/');
    });
  }

  let drawn = false;
  function render() {
    if (!pnl) return;
    clear(pnl.body);
    const a = (ACC.data && ACC.data.account) || null;
    drawn = !!a;
    if (!a) { pnl.body.appendChild(S.skeleton(['60%', '100%', '100%', '80%'])); return; }
    pnl.body.append(infoSection(a), pinSection(), dangerSection(a));
  }
  // opened by a deep link before the account was read: fill it once the data lands
  ACC.on('data', d => { if (d && pnl && pnl.isOpen() && !drawn) render(); });

  function ensure() {
    if (pnl) return pnl;
    pnl = S.panel({ title: '계좌 설정', onClose: () => ACC.closePanel('settings') });
    pnl.el.classList.add('pnl-settings');
    return pnl;
  }
  ACC.panels.settings = {
    isOpen: () => !!pnl && pnl.isOpen(),
    open() { ensure(); render(); pnl.open(); },
    close() { if (pnl) pnl.close(); },
  };
  ACC.openSettings = () => ACC.openPanel('settings');
})();
