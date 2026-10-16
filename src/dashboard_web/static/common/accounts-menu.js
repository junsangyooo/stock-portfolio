// common/accounts-menu.js — MNU-ACCOUNTS 계좌 전환 메뉴 + SCR-ACC-NEW 계좌 추가.
// 계좌 화면과 수집 화면이 함께 include한다(common/accounts-menu.css도).
//
//   AccountsMenu.open(anchorButton | null, { current?: account_id })
//     1440 상단 바 아래 펼침 메뉴, 390 하단 시트. 계좌 행 = 이름 · 증권사·유형,
//     이 기기에서 열린 계좌는 통화별 총자산(GET /api/accounts/session), 잠긴 계좌는 🔒.
//     잠김 확인은 /session 한 번뿐이라 공통 PIN 게이트를 띄우지 않는다(403 없음).
//     금액은 저장소에 남기지 않는다.
//   AccountsMenu.openNew()   계좌 추가 대화상자 → POST /api/accounts → 자동 인증 → /a/{id}
//
// 데이터 경계: 다른 계좌의 금액은 그 계좌 세션이 이 기기에 있을 때만 서버가 준다.
(function () {
  'use strict';
  const S = window.S;
  const { el, clear, svgIco } = S;
  const TYPES = [{ value: 'GENERAL', label: '일반' }, { value: 'ISA', label: 'ISA' },
    { value: 'PENSION', label: '연금저축' }, { value: 'IRP', label: 'IRP' }, { value: 'OVERSEAS', label: '해외' }];
  const TYPE_KO = Object.fromEntries(TYPES.map(t => [t.value, t.label]));
  const sub = a => [a.broker, TYPE_KO[a.account_type] || a.account_type].filter(Boolean).join(' · ');

  async function fetchRows() {
    const [list, sess] = await Promise.all([
      S.api('/api/accounts', { toast: false, gate: false }),
      S.api('/api/accounts/session', { toast: false, gate: false }).catch(() => ({ open: [] })),
    ]);
    const open = new Map((sess.open || []).map(o => [o.account_id, o.totals || []]));
    return (list.accounts || []).map(a => ({ a, totals: open.get(a.account_id) || null }));
  }

  function rowNode(r, current, onPick) {
    const a = r.a;
    const isCur = a.account_id === current;
    const trailing = r.totals
      ? el('span', { className: 'am-totals' }, [
          el('span', { className: 'am-amounts' }, (r.totals.filter(t => t.total || r.totals.length === 1).length
            ? r.totals.filter(t => t.total || r.totals.length === 1) : r.totals.slice(0, 1))
            .map(t => el('b', { className: 't-num' }, [S.fmtMoney(t.total, t.currency)]))),
          isCur ? svgIco('circle-check', 'am-check') : null])
      : el('span', { className: 'am-lock', 'aria-label': '잠김' }, [svgIco('lock')]);
    return el('a', { className: 'am-row' + (isCur ? ' is-current' : ''), href: '/a/' + encodeURIComponent(a.account_id),
      role: 'menuitem', 'aria-current': isCur ? 'page' : null, onClick: onPick }, [
      el('span', { className: 'am-text' }, [el('span', { className: 'am-name ellipsis', title: a.name }, [a.name]),
        el('span', { className: 'am-sub' }, [sub(a)])]),
      trailing]);
  }

  // One surface for both widths: a popover under the anchor (≥600) or a bottom sheet.
  function open(anchor, o) {
    o = o || {};
    const wide = window.matchMedia('(min-width: 600px)').matches;
    const list = el('div', { className: 'am-list', role: 'menu', 'aria-label': '계좌' });
    const addBtn = el('button', { type: 'button', className: 'btn am-add', onClick: () => { close(); openNew(); } },
      [svgIco('plus'), S.LABELS.addAccount]);
    const box = el('div', { className: 'am-box' + (wide ? ' is-pop' : ' is-sheet'), role: 'dialog', 'aria-modal': wide ? null : 'true',
      'aria-label': '계좌' }, [
      wide ? null : el('div', { className: 's-grabber', 'aria-hidden': 'true' }),
      wide ? null : el('div', { className: 'am-head' }, [
        el('button', { type: 'button', className: 'btn dlg-icon', 'aria-label': S.LABELS.back, onClick: () => close() }, [svgIco('arrow-left')]),
        el('h2', {}, [S.LABELS.account])]),
      list, el('div', { className: 'am-foot' }, [addBtn])]);
    const scrim = el('div', { className: 'am-scrim' + (wide ? ' is-clear' : '') }, [box]);
    scrim.addEventListener('mousedown', e => { if (e.target === scrim) close(); });
    let release = null;
    const onKey = e => {
      if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); close(); }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        const its = [...box.querySelectorAll('.am-row, .am-add, .am-retry')];
        const i = its.indexOf(document.activeElement);
        if (!its.length) return;
        e.preventDefault();
        its[(i + (e.key === 'ArrowDown' ? 1 : -1) + its.length) % its.length].focus();
      }
    };
    function close() {
      if (!scrim.isConnected) return;
      scrim.remove();
      document.removeEventListener('keydown', onKey, true);
      document.body.classList.remove('has-layer');
      if (release) release();
      if (anchor && anchor.setAttribute) { anchor.setAttribute('aria-expanded', 'false'); anchor.focus(); }
    }
    document.addEventListener('keydown', onKey, true);
    document.body.appendChild(scrim);
    if (!wide) document.body.classList.add('has-layer');
    if (wide && anchor) {
      const r = anchor.getBoundingClientRect();
      box.style.top = (r.bottom + 4) + 'px';
      box.style.left = Math.max(8, Math.min(r.left, document.documentElement.clientWidth - 368)) + 'px';
    } else if (wide) {
      box.style.top = '64px';
      box.style.left = Math.max(8, (document.documentElement.clientWidth - 360) / 2) + 'px';
    }
    if (anchor && anchor.setAttribute) anchor.setAttribute('aria-expanded', 'true');
    release = S.trapFocus(box, addBtn);

    function loading() {
      clear(list);
      list.appendChild(el('div', { className: 'am-skel' }, [S.skeleton(['60%', '40%', '70%', '45%'])]));
    }
    async function load() {
      loading();
      let rows;
      try { rows = await fetchRows(); }
      catch (e) {
        clear(list);
        list.appendChild(S.stateBlock({ kind: 'error', level: 'h3', title: '계좌를 불러오지 못했어요',
          actions: [{ label: S.LABELS.retry, onClick: load }] }));
        const b = list.querySelector('.state-actions .btn'); if (b) { b.classList.add('am-retry'); b.focus(); }
        return;
      }
      clear(list);
      if (!rows.length) {
        list.appendChild(S.stateBlock({ kind: 'empty', icon: 'briefcase', level: 'h3', title: '계좌가 없어요',
          body: '계좌를 추가하면 여기서 바꿔 볼 수 있어요' }));
        box.classList.add('is-empty');
        addBtn.className = 'btn btn-primary am-add';
        addBtn.focus();
        return;
      }
      rows.forEach(r => list.appendChild(rowNode(r, o.current, () => close())));
      const cur = list.querySelector('.is-current') || list.querySelector('.am-row');
      if (cur) cur.focus();
    }
    load();
    return { close };
  }

  // ── SCR-ACC-NEW 계좌 추가 ─────────────────────────────────────────────
  function openNew() {
    let nameIn, idIn, typeSel, brokerIn, ccySeg, memoIn, pin1, pin2, idHint, pinMsg, keyIn;
    let p1 = '', p2 = '';
    let idTouched = false;
    const mk = (p) => p + '-' + Math.random().toString(36).slice(2, 7);
    const ids = { name: mk('an'), id: mk('ai'), type: mk('at'), broker: mk('ab'), memo: mk('am'), key: mk('ak') };
    function field(label, control, id, hint) {
      return el('div', { className: 'field' }, [el('label', { className: 'field-label', for: id }, [label]), control, hint || null]);
    }
    function slug(s) {
      return String(s || '').normalize('NFKD').replace(/[^A-Za-z0-9]/g, '').toLowerCase().slice(0, 24);
    }
    let d;
    function ready() {
      const ok = nameIn.value.trim() && /^[A-Za-z0-9_-]{2,32}$/.test(idIn.value.trim()) && /^\d{6}$/.test(p1) && p1 === p2 && keyIn.value.length > 0;
      const main = d && d.el.querySelector('.dlg-main');
      if (main) main.disabled = !ok;
    }
    d = S.dialog({
      title: S.LABELS.addAccount,
      confirmDiscard: true,
      primary: { label: S.LABELS.create },
      body: () => {
        nameIn = el('input', { className: 'input', id: ids.name, maxlength: '40', autocomplete: 'off', placeholder: '예: 공모주' });
        idIn = el('input', { className: 'input', id: ids.id, maxlength: '32', autocomplete: 'off', spellcheck: 'false',
          autocapitalize: 'off', placeholder: 'gongmoju' });
        idHint = el('p', { className: 'field-hint' }, ['주소에 쓰여요 · 영문·숫자']);
        typeSel = el('select', { className: 'input', id: ids.type }, TYPES.map(t => el('option', { value: t.value }, [t.label])));
        brokerIn = el('input', { className: 'input', id: ids.broker, maxlength: '30', autocomplete: 'off', placeholder: '예: 토스증권' });
        ccySeg = S.segmented({ items: [{ value: 'KRW', label: 'KRW' }, { value: 'USD', label: 'USD' }], value: 'KRW', label: '기본 통화' });
        memoIn = el('input', { className: 'input', id: ids.memo, maxlength: '200', autocomplete: 'off', placeholder: '선택' });
        // the owner's creation key: asked here once, never stored on the device
        keyIn = el('input', { className: 'input', id: ids.key, type: 'password', autocomplete: 'off', spellcheck: 'false' });
        keyIn.addEventListener('input', () => ready());
        pinMsg = el('p', { className: 'field-error', role: 'alert' });
        pinMsg.hidden = true;
        pin1 = S.pinInput({ label: 'PIN', onComplete: v => { p1 = v; check(); pin2.focus(); } });
        pin2 = S.pinInput({ label: 'PIN 다시', onComplete: v => { p2 = v; check(); } });
        const track = (pin, set) => pin.el.addEventListener('input', () => {
          set([...pin.el.querySelectorAll('.pin-cell')].map(c => c.value).join('')); check(true);
        });
        track(pin1, v => { p1 = v; });
        track(pin2, v => { p2 = v; });
        function check(quiet) {
          const both = /^\d{6}$/.test(p1) && /^\d{6}$/.test(p2);
          const bad = both && p1 !== p2;
          if (bad) {
            pinMsg.textContent = 'PIN이 서로 달라요 · 다시 넣어 주세요';
            pinMsg.hidden = false;
            pin2.el.classList.add('is-error');
            if (!quiet) { pin2.clear(); p2 = ''; }
          } else if (!quiet || p2.length) {
            pinMsg.hidden = true;
            pin2.el.classList.remove('is-error');
          }
          ready();
        }
        nameIn.addEventListener('input', () => { if (!idTouched) idIn.value = slug(nameIn.value); ready(); });
        idIn.addEventListener('input', () => {
          idTouched = true;
          const v = idIn.value.trim();
          const bad = v && !/^[A-Za-z0-9_-]+$/.test(v);
          idIn.setAttribute('aria-invalid', bad ? 'true' : 'false');
          idHint.className = bad ? 'field-error' : 'field-hint';
          idHint.textContent = bad ? '영문·숫자만 쓸 수 있어요' : '주소에 쓰여요 · 영문·숫자';
          ready();
        });
        return el('div', { className: 'form-stack' }, [
          el('div', { className: 'field-row' }, [field('표시 이름', nameIn, ids.name), field('ID', idIn, ids.id, idHint)]),
          el('div', { className: 'field-row' }, [field('유형', typeSel, ids.type), field('증권사', brokerIn, ids.broker)]),
          el('div', { className: 'field' }, [el('span', { className: 'field-label' }, ['기본 통화']), el('div', {}, [ccySeg])]),
          field('메모', memoIn, ids.memo),
          el('div', { className: 'field' }, [el('span', { className: 'field-label' }, ['PIN 6자리']), pin1.el]),
          el('div', { className: 'field' }, [el('span', { className: 'field-label' }, ['PIN 다시']), pin2.el, pinMsg]),
          field('계좌 신설 키', keyIn, ids.key, el('p', { className: 'field-hint' }, ['계좌를 만들 때만 넣어요 · 이후에는 PIN으로 열어요'])),
        ]);
      },
      failText: { title: '계좌를 만들지 못했어요' },
      onSubmit: async ctx => {
        const id = idIn.value.trim();
        const body = { account_id: id, name: nameIn.value.trim(), account_type: typeSel.value,
          broker: brokerIn.value.trim() || null, base_currency: ccySeg.value, note: memoIn.value.trim() || null, pin: p1,
          create_key: keyIn.value };
        try {
          await ctx.api('/api/accounts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        } catch (e) {
          if (e.status === 409) { const x = new Error('이미 있는 ID예요 · 다른 ID를 넣어 주세요'); x.status = 409; throw x; }
          if (e.status === 400) { const x = new Error('PIN은 숫자 6자리예요'); x.status = 400; throw x; }
          throw e;
        }
        // the PIN was just set here: open the new book on this device right away
        try {
          await ctx.api('/api/accounts/' + encodeURIComponent(id) + '/auth', { method: 'POST',
            headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin: p1 }) });
        } catch (e) { /* the gate on the new page asks for it */ }
        return { id };
      },
    });
    ready();
    d.done.then(r => {
      if (r && r.id) location.assign('/a/' + encodeURIComponent(r.id));
    });
    return d.done;
  }

  // A message left by the page before it navigated here (계좌 삭제 → '/').
  function flash() {
    let m = null;
    try { m = sessionStorage.getItem('pdash.flash'); sessionStorage.removeItem('pdash.flash'); } catch (e) { /* */ }
    if (m) S.toast(m);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', flash); else flash();

  window.AccountsMenu = { open, openNew, typeLabel: t => TYPE_KO[t] || t, TYPES };
})();
