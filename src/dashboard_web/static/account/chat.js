// account/chat.js — PNL-CHAT 채팅(패널, ?chat=1). 상태: empty · running · error · ask · list.
// 서버의 대화(chat_sessions)를 이어 쓴다: POST /chat → job_id → /chat/status 폴링.
// 답을 쓰는 동안 새로고침해도 sessionStorage의 job으로 돌아와 이어서 보여 준다.
//   ACC.chat.open({ ask?: symbol_id })   '이 종목 묻기' — 새 대화 + 입력에 종목을 넣어 둔다
(function () {
  'use strict';
  const S = window.S;
  const ACC = window.ACC;
  const { el, clear, svgIco } = S;
  const POLL_MS = 3000;
  const SID_KEY = 'pdash.chat.' + ACC.id;          // last thread id (no content)
  const JOB_KEY = 'pdash.chatjob.' + ACC.id;       // { sid, job, q, t0 } while a reply is being written

  let pnl = null, log = null, input = null, sendBtn = null, titleBtn = null;
  let sid = null, threads = [], busy = false, pending = null, askSym = null, tick = null;

  const store = {
    get(k) { try { return sessionStorage.getItem(k) || localStorage.getItem(k); } catch (e) { return null; } },
    setSid(v) { try { if (v) localStorage.setItem(SID_KEY, v); else localStorage.removeItem(SID_KEY); } catch (e) { /* */ } },
    job() { try { return JSON.parse(sessionStorage.getItem(JOB_KEY) || 'null'); } catch (e) { return null; } },
    setJob(j) { try { if (j) sessionStorage.setItem(JOB_KEY, JSON.stringify(j)); else sessionStorage.removeItem(JOB_KEY); } catch (e) { /* */ } },
  };
  const api = (path, opts) => S.api(ACC.path(path), Object.assign({ toast: false }, opts || {}));
  const POST = body => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });

  // ── markdown subset (untrusted text → text nodes only, no markup parsing) ────────
  const MD_INLINE = /\*\*([^*]+)\*\*|`([^`]+)`/g;
  const isRow = s => /^\s*\|.*\|\s*$/.test(s || '');
  const isSep = s => /^\s*\|[\s:|-]+\|\s*$/.test(s || '');
  const cellsOf = s => s.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
  const NUMERIC = /^[-+−]?[₩$]?[\d,.\s%]+$/;
  function mdInline(text, parent) {
    MD_INLINE.lastIndex = 0;
    let last = 0, m;
    while ((m = MD_INLINE.exec(text))) {
      if (m.index > last) parent.appendChild(document.createTextNode(text.slice(last, m.index)));
      parent.appendChild(el(m[1] != null ? 'strong' : 'code', {}, [m[1] != null ? m[1] : m[2]]));
      last = MD_INLINE.lastIndex;
    }
    if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
    return parent;
  }
  function renderReply(src, box) {
    clear(box);
    const lines = String(src == null ? '' : src).split('\n');
    let buf = [];
    const flush = () => {
      const text = buf.join('\n').replace(/^\n+|\n+$/g, '');
      buf = [];
      if (text) box.appendChild(mdInline(text, el('p', { className: 'md-p' })));
    };
    for (let i = 0; i < lines.length; i++) {
      if (isRow(lines[i]) && isSep(lines[i + 1])) {
        flush();
        const head = cellsOf(lines[i]);
        const rows = [];
        let j = i + 2;
        while (j < lines.length && isRow(lines[j])) rows.push(cellsOf(lines[j++]));
        box.appendChild(el('div', { className: 'md-scroll' }, [el('table', { className: 'md-tbl' }, [
          el('thead', {}, [el('tr', {}, head.map(h => mdInline(h, el('th'))))]),
          el('tbody', {}, rows.map(r => el('tr', {}, r.map(c => {
            const td = mdInline(c, el('td'));
            if (NUMERIC.test(c.replace(/[*`]/g, '').trim())) td.className = 'num';
            return td;
          }))))])]));
        i = j - 1;
      } else if (/^\s*(-{3,}|\*{3,})\s*$/.test(lines[i])) {
        flush();
        box.appendChild(el('hr', { className: 'md-hr' }));
      } else buf.push(lines[i]);
    }
    flush();
    return box;
  }

  // ── messages ───────────────────────────────────────────────────────
  function bubbleUser(text) {
    return el('div', { className: 'ch-msg is-user' }, [el('p', {}, [text])]);
  }
  function bubbleBot(text) {
    const body = el('div', { className: 'ch-md' });
    renderReply(text, body);
    return el('div', { className: 'ch-msg is-bot' }, [el('span', { className: 'ch-who' }, ['Claude']), body]);
  }
  function scrollEnd() { log.scrollTop = log.scrollHeight; }
  function emptyState() {
    clear(log);
    const sym = askSym && symName(askSym);
    const name = sym || ((ACC.data && ACC.data.account && ACC.data.account.name) || ACC.id);
    const ex = sym ? [] : ['비중이 가장 큰 종목은 괜찮은가요?', '오늘 손익을 가장 크게 움직인 종목은?', '최근 3개월 거래를 정리해 줘요'];
    log.appendChild(el('div', { className: 'ch-empty' }, [
      el('h3', {}, [name + '에 대해 물어봐요']),
      el('p', { className: 't-muted' }, ['보유·거래·분석을 읽고 답해요']),
      sym ? el('p', { className: 't-muted' }, ['입력에 종목을 넣어 뒀어요 · 이어 써서 보내요']) : null,
      ex.length ? el('div', { className: 'ch-examples' }, ex.map(q => el('button', { type: 'button', className: 'btn ch-ex',
        onClick: () => { input.value = q; send(); } }, [el('span', {}, [q]), svgIco('chevron-right')]))) : null]));
  }
  function symName(sidOrSym) {
    const h = ACC.holding(sidOrSym);
    return h ? (h.name_kr || h.name_en || h.code) : String(sidOrSym).split(':')[1] || sidOrSym;
  }
  function paint(msgs) {
    clear(log);
    if (!msgs.length) { emptyState(); return; }
    msgs.forEach(m => log.appendChild(m.role === 'user' ? bubbleUser(m.content) : bubbleBot(m.content)));
    scrollEnd();
  }
  function showRunning(t0) {
    stopTick();
    pending = el('div', { className: 'ch-run', role: 'status' }, [
      el('span', { className: 'ch-run-line' }, [S.spinner(), el('b', {}, ['답을 쓰는 중']), el('span', { className: 'ch-clock t-num' }, ['0:00'])]),
      el('p', { className: 't-muted t-caption' }, ['새로고침해도 이 대화로 돌아와 이어서 보여요'])]);
    log.appendChild(pending);
    const clock = pending.querySelector('.ch-clock');
    const upd = () => { const s = Math.max(0, Math.round((Date.now() - t0) / 1000)); clock.textContent = Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); };
    upd();
    tick = setInterval(upd, 1000);
    scrollEnd();
  }
  function stopTick() { if (tick) { clearInterval(tick); tick = null; } }
  function setBusy(on) {
    busy = on;
    sendBtn.disabled = on;
    input.placeholder = on ? '답을 다 쓰면 보낼 수 있어요' : '이 계좌에 대해 물어봐요';
    input.readOnly = on;
  }

  // ── threads ────────────────────────────────────────────────────────
  function setTitle() {
    const t = threads.find(x => x.session_id === sid);
    titleBtn.querySelector('.ch-title-text').textContent = t ? t.title : S.LABELS.newChat;
  }
  async function loadThreads() {
    try { threads = (await api('/chats')).chats || []; } catch (e) { if (e.forbidden) return false; threads = []; }
    return true;
  }
  async function openThread(id) {
    sid = id || null;
    store.setSid(sid);
    setTitle();
    if (!sid) { emptyState(); return; }
    clear(log);
    log.appendChild(S.skeleton(['70%', '50%', '80%']));
    try {
      const r = await api('/chats/' + encodeURIComponent(sid));
      paint((r.chat || {}).messages || []);
      const job = store.job();
      if (job && job.sid === sid) resume(job);
      else if (r.running) resume({ sid, job: null, t0: Date.now() });
    } catch (e) {
      if (e.forbidden) return;
      sid = null; store.setSid(null); setTitle(); emptyState();
    }
  }
  function threadMenu() {
    const items = threads.map(t => ({ label: t.title, sub: S.fmtDate(t.updated_at || t.created_at),
      trailing: t.session_id === sid ? svgIco('circle-check') : null, onSelect: () => { if (!busy) openThread(t.session_id); } }));
    if (sid) items.push({ label: '이 대화 삭제', icon: 'x', danger: true, onSelect: delThread });
    if (!items.length) items.push({ label: '저장된 대화가 없어요', disabled: true });
    S.menu(titleBtn, items);
  }
  async function newThread() {
    if (busy) return;
    sid = null; store.setSid(null); askSym = null;
    setTitle();
    emptyState();
    input.value = '';
    input.focus();
  }
  async function delThread() {
    if (!sid) return;
    const ok = await S.confirmModal({ title: '이 대화를 삭제할까요?', body: '대화 기록이 함께 지워져요 · 되돌릴 수 없어요',
      danger: true, confirmLabel: S.LABELS.delete });
    if (!ok) return;
    try { await api('/chats/' + encodeURIComponent(sid), { method: 'DELETE' }); } catch (e) { if (e.forbidden) return; }
    await loadThreads();
    await openThread(threads[0] ? threads[0].session_id : null);
    ACC.toast('대화를 삭제했어요');
  }

  // ── ask ────────────────────────────────────────────────────────────
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  async function send() {
    const q = input.value.trim();
    if (!q || busy) return;
    const old = log.querySelector('.ch-fail');
    if (old) old.remove();
    if (log.querySelector('.ch-empty')) clear(log);
    const ub = bubbleUser(q);
    log.appendChild(ub);
    setBusy(true);
    const t0 = Date.now();
    showRunning(t0);
    let job;
    try {
      if (!sid) {
        const title = q.length > 24 ? q.slice(0, 24) + '…' : q;
        sid = (await api('/chats', POST({ title }))).session_id;
        store.setSid(sid);
      }
      const r = await api('/chat', POST({ session_id: sid, message: q }));
      if (!r.ok) { const e = new Error(r.detail || '보내지 못했어요'); e.status = 409; throw e; }
      job = r.job_id;
    } catch (e) {
      fail(ub, q, e);
      return;
    }
    input.value = '';
    askSym = null;
    store.setJob({ sid, job, t0 });
    loadThreads().then(setTitle);
    await poll({ sid, job, t0 });
  }
  function fail(ub, q, e) {
    stopTick();
    if (pending) { pending.remove(); pending = null; }
    setBusy(false);
    if (e && e.forbidden) return;
    ub.classList.add('is-failed');
    const retry = el('button', { type: 'button', className: 'btn pin-link', onClick: () => { ub.remove(); input.value = q; send(); } }, [S.LABELS.retry]);
    log.appendChild(el('p', { className: 'ch-fail', role: 'alert' }, [svgIco('circle-x'), '보내지 못했어요', el('span', { 'aria-hidden': 'true' }, [' · ']), retry]));
    input.value = q;
    scrollEnd();
  }
  function resume(j) {
    if (busy) return;
    setBusy(true);
    showRunning(j.t0 || Date.now());
    poll(j);
  }
  async function poll(j) {
    let reply = null, err = null;
    for (;;) {
      await sleep(POLL_MS);
      if (sid !== j.sid) { stopTick(); setBusy(false); return; }        // switched threads
      try {
        if (j.job) {
          const st = await api('/chat/status?job_id=' + encodeURIComponent(j.job));
          if (st.state === 'done') { reply = st.reply; break; }
          if (st.state === 'error') { err = new Error(st.detail || '답을 만들지 못했어요'); break; }
          if (st.state !== 'unknown') continue;
        }
        // registry lost the job (restart) or we only know the thread runs: read the thread
        const r = await api('/chats/' + encodeURIComponent(j.sid));
        if (!r.running) {
          const last = ((r.chat || {}).messages || []).slice(-1)[0];
          if (last && last.role === 'assistant') reply = last.content;
          else err = new Error('답을 받지 못했어요');
          break;
        }
      } catch (e) {
        if (e.forbidden) { stopTick(); setBusy(false); return; }
        // a network blip: keep polling
      }
    }
    store.setJob(null);
    stopTick();
    if (pending) { pending.remove(); pending = null; }
    setBusy(false);
    if (err) {
      log.appendChild(el('p', { className: 'ch-fail', role: 'alert' }, [svgIco('circle-x'), err.message]));
    } else {
      log.appendChild(bubbleBot(reply));
    }
    scrollEnd();
  }

  // ── panel ──────────────────────────────────────────────────────────
  function build() {
    pnl = S.panel({ title: '', onClose: () => { document.body.classList.remove('chat-open'); ACC.closePanel('chat'); },
      keepOpenOn: '.topbar, .s-menu-scrim, .s-scrim, #toast-host' });
    pnl.el.classList.add('pnl-chat');
    titleBtn = el('button', { type: 'button', className: 'btn ch-title', 'aria-haspopup': 'menu', onClick: () => threadMenu() },
      [el('span', { className: 'ch-title-text ellipsis' }, [S.LABELS.newChat]), svgIco('chevron-down')]);
    pnl.titleEl.replaceWith(el('h2', { className: 'ch-h', id: pnl.el.getAttribute('aria-labelledby') }, [titleBtn]));
    pnl.head.appendChild(el('button', { type: 'button', className: 'btn btn-sm ch-new', onClick: newThread }, [S.LABELS.newChat]));
    log = el('div', { className: 'ch-log', role: 'log', 'aria-live': 'polite' });
    input = el('textarea', { className: 'input ch-input', rows: '1', placeholder: '이 계좌에 대해 물어봐요', 'aria-label': '질문' });
    sendBtn = el('button', { type: 'button', className: 'btn btn-primary ch-send', onClick: send }, [S.LABELS.send]);
    input.addEventListener('keydown', e => {
      if (e.key !== 'Enter' || e.shiftKey) return;
      if (e.isComposing || e.keyCode === 229) { setTimeout(send, 0); return; }   // Korean IME commits after this
      e.preventDefault();
      send();
    });
    input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 160) + 'px'; });
    pnl.body.append(log, el('div', { className: 'ch-bar' }, [input, sendBtn]));
  }
  let loaded = false;
  async function show(o) {
    o = o || {};
    if (!pnl) build();
    document.body.classList.add('chat-open');
    pnl.open();
    if (o.ask) {
      if (!busy) { sid = null; store.setSid(null); }
      askSym = o.ask;
      if (!loaded) { loaded = true; await loadThreads(); }
      setTitle();
      if (!busy) {
        emptyState();
        input.value = symName(o.ask) + '(' + o.ask + ') ';
      }
      setTimeout(() => { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }, 0);
      return;
    }
    if (!loaded) {
      loaded = true;
      if (!(await loadThreads())) return;
      const want = store.get(SID_KEY);
      const job = store.job();
      const target = job && threads.some(t => t.session_id === job.sid) ? job.sid
        : threads.some(t => t.session_id === want) ? want : (threads[0] ? threads[0].session_id : null);
      await openThread(target);
    }
    setTimeout(() => input.focus(), 0);
  }

  let nextAsk = null;
  ACC.panels.chat = {
    isOpen: () => !!pnl && pnl.isOpen(),
    open() { const a = nextAsk; nextAsk = null; show({ ask: a }); },
    close() { if (pnl) { pnl.close(); document.body.classList.remove('chat-open'); } },
  };
  ACC.chat = {
    open(o) {
      o = o || {};
      if (ACC.panels.chat.isOpen()) { show(o); return; }
      nextAsk = o.ask || null;
      ACC.openPanel('chat');
    },
    close() { ACC.closePanel('chat'); },
  };
})();
