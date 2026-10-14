// SCR-NEWS 수집 (`/`) — 23 fields × 7 days of mood, 오늘의 핵심, 뉴스 흐름 · 소셜.
// States (인계 §7 '수집'): default · full · loading · predawn · error · mine ·
// mine_none · empty_expanded · theme_route_error · after-lock.
// Data: GET /api/analysis/news/themes?days=30 (grid, 30-day lines) and
// GET /api/analysis/news/feed (cards, flow, first lines when the grid route
// fails) — both open without a PIN. '내 종목' reads only the accounts open on
// this device (GET /api/accounts/session → each GET /api/accounts/{id}) and
// hands their symbols to /news/symbol-themes (ops notes §4: the shared route
// never reads a book).
(function () {
'use strict';
const { el, clear, svgIco } = S;
const U = window.NEWS_UTIL;

const SLOTS = [6, 17];                 // KST hours of the two scheduled collections
const POLL_MS = 10 * 60 * 1000;
const CACHE_KEY = 'pdash.newsCache.v1';
const FLOW_FIRST = { news: 10, social: 6 };
const CORE_N = 6;

const ST = {
  feed: null, themes: null,            // last good payloads
  feedErr: false, themesErr: false,
  themesRetried: false,
  okAt: null,                          // when the page last read both routes
  stale: false,                        // showing an older read after a failure
  day: null, today: null, predawn: false,
  mine: false, mineData: null, minePromise: null,
  quietOpen: false, flowTab: 'news', flowAll: { news: false, social: false },
};

const $ = id => document.getElementById(id);
const main = $('main');

// ── chrome ──────────────────────────────────────────────────────────
function openAccounts(btn) {
  if (window.AccountsMenu && typeof window.AccountsMenu.open === 'function') { window.AccountsMenu.open(btn); return; }
  fallbackAccountsMenu(btn);
}
// Used only until common/accounts-menu.js (계좌 담당) is in place: names
// and the lock, never amounts.
async function fallbackAccountsMenu(btn) {
  let list = [], open = [];
  try {
    [list, open] = await Promise.all([
      S.api('/api/accounts', { toast: false }).then(d => d.accounts || []),
      S.api('/api/accounts/session', { toast: false }).then(d => (d.open || []).map(o => o.account_id)),
    ]);
  } catch (e) { S.toast('계좌 목록을 불러오지 못했어요', 'err'); return; }
  S.menu(btn, list.map(a => ({ label: a.name || a.account_id, sub: S.accountSub(a),
    icon: open.includes(a.account_id) ? 'chart-line' : 'lock', href: '/a/' + encodeURIComponent(a.account_id) })));
}
const mineBtns = [];
function mineButton(extra) {
  const b = el('button', { type: 'button', className: 'btn chip mine-btn ' + (extra || ''), 'aria-pressed': 'false',
    onClick: () => setMine(!ST.mine) }, []);
  mineBtns.push(b);
  return b;
}
function paintMineButtons() {
  mineBtns.forEach(b => {
    clear(b);
    b.setAttribute('aria-pressed', ST.mine ? 'true' : 'false');
    if (ST.mine) { b.appendChild(document.createTextNode('내 종목')); b.appendChild(svgIco('x')); b.setAttribute('aria-label', '내 종목 거르기 풀기'); }
    else { b.appendChild(svgIco('briefcase')); b.appendChild(document.createTextNode('내 종목')); b.removeAttribute('aria-label'); }
  });
}
const hasLast = S.lastAccountHref() !== '/';
S.chrome({
  topBar: { active: 'news', onAccounts: openAccounts, actions: [mineButton('tb-mine')] },
  bottomTab: 'news',
  bottomTabOpts: hasLast ? {} : { onAccounts: openAccounts },
});
$('nh-tools').appendChild(mineButton());
paintMineButtons();

// legend: 부정 ■■■■■ 긍정 □ 없음 (the cell colours, no text on cells)
function legend() {
  const sw = b => el('span', { className: 'sw b-' + b, 'aria-hidden': 'true' });
  return [el('span', {}, ['부정']), sw('negative-strong'), sw('negative'), sw('neutral'), sw('positive'),
    sw('positive-strong'), el('span', {}, ['긍정']), el('span', { className: 'sw b-none lg-none', 'aria-hidden': 'true' }),
    el('span', {}, ['없음'])];
}
legend().forEach(n => $('mg-legend').appendChild(n));
legend().forEach(n => $('nh-legend').appendChild(n));

// ── the shown day ───────────────────────────────────────────────────
// Before the day's summaries exist (오전 6시 수집 전, or while they are being
// made) the page shows the newest summarised day and says so (predawn).
function pickDay() {
  const now = U.kstNow();
  ST.today = now.day;
  let newest = null;
  if (ST.themes) {
    ST.themes.themes.forEach(t => {
      t.series.forEach(p => { if (p.score != null && (!newest || p.date > newest)) newest = p.date; });
    });
  } else if (ST.feed) {
    (ST.feed.summary || []).forEach(s => { if (!newest || s.date > newest) newest = s.date; });
  }
  ST.day = newest || now.day;
  ST.predawn = ST.day < now.day;
}
function predawnWhy(short) {
  const { hh } = U.kstNow();
  if (hh < SLOTS[0]) return short ? '수집 전' : '오늘 수집 전이에요';
  if (hh < SLOTS[0] + 2) return short ? '요약 중' : '오늘 요약을 만드는 중이에요';
  return short ? '요약 전' : '오늘 요약이 아직 없어요';
}
function nextRun() {
  const { hh } = U.kstNow();
  const h = SLOTS.find(s => s > hh);
  return String(h != null ? h : SLOTS[0]).padStart(2, '0') + ':00';
}
// the newest collection stamp among the feed's items on the shown day
function collectedAt() {
  if (!ST.feed) return null;
  let best = null;
  ST.feed.news.concat(ST.feed.social).forEach(x => {
    if (x.fetched_at && U.itemDay(x) === ST.day && (!best || x.fetched_at > best)) best = x.fetched_at;
  });
  return best;
}
function sevenDays() {
  const out = [];
  for (let i = 6; i >= 0; i--) out.push(U.addDays(ST.day || ST.today, -i));
  return out;
}

// ── rows ────────────────────────────────────────────────────────────
// One row per field from /news/themes; when that route fails the rows come
// from the feed's summaries (first lines only, cells grey).
function rows() {
  const days = sevenDays();
  if (ST.themes && !ST.themesErr) {
    return ST.themes.themes.map(t => {
      const by = {};
      t.series.forEach(p => { by[p.date] = p; });
      const p = by[ST.day] || { score: null, items: 0 };
      const bullets = t.today && t.today.date === ST.day ? t.today.bullets || [] : summaryOf(t.theme_id);
      return { tid: t.theme_id, name: t.name_kr, cells: days.map(d => by[d] || { date: d, score: null, items: 0 }),
        score: p.score, items: p.items || 0, first: bullets[0] || '', err: false };
    }).sort((a, b) => b.items - a.items || a.name.localeCompare(b.name, 'ko'));
  }
  const names = {};
  (ST.feed ? ST.feed.themes : []).forEach(t => { names[t.id] = t.name_kr; });
  (ST.feed ? ST.feed.summary : []).forEach(s => { names[s.theme] = s.theme_kr; });
  return Object.keys(names).map(tid => ({ tid, name: names[tid], cells: days.map(d => ({ date: d, score: null })),
    score: null, items: null, first: summaryOf(tid)[0] || '', err: true }));
}
function summaryOf(tid) {
  const s = ST.feed && (ST.feed.summary || []).find(x => x.theme === tid && x.date === ST.day);
  return s ? s.bullets || [] : [];
}
function rowOf(tid) {
  return ST.themes && !ST.themesErr ? ST.themes.themes.find(t => t.theme_id === tid) || null : null;
}
function nameOf(tid) {
  const r = rowOf(tid);
  if (r) return r.name_kr;
  const f = ST.feed && (ST.feed.themes || []).find(t => t.id === tid);
  return f ? f.name_kr : tid;
}

// ── head ────────────────────────────────────────────────────────────
function renderHead() {
  const when = $('nh-when');
  clear(when);
  if (!ST.feed && !ST.themes) { when.textContent = '수집'; clear($('nh-meta')); return; }
  if (ST.predawn) {
    when.appendChild(el('span', { className: 'sm-up' }, [S.fmtDate(ST.day) + ' 요약 · ' + predawnWhy(false)]));
    when.appendChild(el('span', { className: 'sm-down' }, [S.fmtDate(ST.day) + ' 요약 · ' + predawnWhy(true)]));
  } else {
    const at = collectedAt();
    when.textContent = S.fmtDate(ST.day) + (at ? ' ' + S.fmtTime(at) : '') + ' 수집';
  }
  const meta = $('nh-meta');
  clear(meta);
  if (ST.themes && !ST.themesErr) {
    const day = (ST.themes.articles || []).find(p => p.date === ST.day);
    if (day) meta.appendChild(el('span', {}, ['기사 ', el('b', {}, [S.fmtCount(day.items)])]));
  }
  meta.appendChild(el('span', {}, ['다음 ' + nextRun()]));
}

// ── grid ────────────────────────────────────────────────────────────
function renderGridHead() {
  const days = sevenDays();
  const tr = el('tr', {}, [el('th', { scope: 'col', className: 'c-name' }, ['분야'])].concat(days.map(d => {
    const [y, m, dd] = d.split('-').map(Number);
    const wd = U.WEEKDAY[new Date(Date.UTC(y, m - 1, dd)).getUTCDay()];
    return el('th', { scope: 'col', className: d === ST.day ? 'is-today' : null, abbr: S.fmtDate(d) }, [
      el('span', { className: 'wd' }, [wd]),
      el('span', { className: 'dd sm-up' }, [m + '/' + dd]),
      el('span', { className: 'dd sm-down' }, [String(dd).padStart(2, '0')]),
    ]);
  })).concat([
    el('th', { scope: 'col', className: 'c-score' }, [ST.day && ST.day !== ST.today ? S.fmtDate(ST.day) : '오늘']),
    el('th', { scope: 'col', className: 'c-count' }, ['기사']),
    el('th', { scope: 'col', className: 'c-line' }, ['첫 줄']),
  ]));
  const head = $('mg-head');
  clear(head); head.appendChild(tr);
}

function mineCount(tid) {
  const m = ST.mineData;
  return m && m.byTheme && m.byTheme[tid] ? m.byTheme[tid].length : 0;
}
function rowEl(r) {
  const n = ST.mine ? mineCount(r.tid) : 0;
  const dim = ST.mine && ST.mineData && !ST.mineData.error && !n;
  const tr = el('tr', { className: 'mg-row' + (dim ? ' is-dim' : '') + (r.items === 0 ? ' is-quiet' : '')
    + (themePanel.current() === r.tid ? ' is-open' : ''), dataset: { tid: r.tid } }, [
    el('th', { scope: 'row', className: 'c-name' }, [el('button', { type: 'button', className: 'btn mg-name',
      'aria-haspopup': 'dialog', title: r.name }, [
      svgIco(S.newsThemeIcon(r.tid)),
      el('span', { className: 'nm ellipsis' }, [r.name]),
      n ? el('span', { className: 'mine-n' }, ['내 ' + n]) : null,
    ])]),
  ].concat(r.cells.map(c => {
    const m = S.moodBand(c.score, { error: r.err });
    return el('td', {}, [el('span', { className: 'mc b-' + m.band + (c.date === ST.day && !r.err ? ' is-today' : ''),
      role: 'img', 'aria-label': S.fmtDate(c.date) + ' ' + m.label + (c.score != null ? ' ' + U.fmtScore(c.score) : '') })]);
  })).concat([
    el('td', { className: 'c-score' }, [r.err ? el('span', { className: 'score s-none' }, ['—']) : U.scoreEl(r.items === 0 ? null : r.score)]),
    el('td', { className: 'c-count' }, [r.items == null ? '—' : S.fmtCount(r.items)]),
    el('td', { className: 'c-line ellipsis', title: r.first || null }, [r.items === 0 ? (ST.day === ST.today ? '오늘 소식이 없어요' : '이날 소식이 없어요') : (r.first || '—')]),
  ]));
  return tr;
}
function renderGrid() {
  renderGridHead();
  const body = $('mg-body'), quietBody = $('mg-quiet'), fold = $('mg-fold-slot');
  clear(body); clear(quietBody); clear(fold);
  const all = rows();
  const active = all.filter(r => r.items !== 0);
  const quiet = all.filter(r => r.items === 0);
  active.forEach(r => body.appendChild(rowEl(r)));
  quiet.forEach(r => quietBody.appendChild(rowEl(r)));
  quietBody.hidden = !ST.quietOpen || !quiet.length;
  if (quiet.length) {
    const btn = el('button', { type: 'button', className: 'btn mg-fold', 'aria-expanded': ST.quietOpen ? 'true' : 'false',
      'aria-controls': 'mg-quiet', onClick: () => {
        ST.quietOpen = !ST.quietOpen;
        btn.setAttribute('aria-expanded', ST.quietOpen ? 'true' : 'false');
        quietBody.hidden = !ST.quietOpen;
        // the folded rows sit after the button: move the table part below it
        placeQuiet();
      } }, [
      el('span', {}, [(ST.day === ST.today ? '소식 없는 분야 ' : '이날 소식 없는 분야 ') + quiet.length]),
      el('span', { className: 'fold-names ellipsis' }, [quiet.map(r => r.name).join(', ')]),
      svgIco('chevron-down', 'chev'),
    ]);
    fold.appendChild(btn);
  }
  placeQuiet();
  renderGridSub();
}
// folded rows render below the fold button, in a table of their own that
// shares the grid's columns
function placeQuiet() {
  const slot = $('mg-fold-slot');
  let t = $('mg-quiet-table');
  if (!t) {
    t = el('table', { className: 'mg', id: 'mg-quiet-table', 'aria-label': '소식 없는 분야' });
    t.appendChild($('mg').querySelector('colgroup').cloneNode(true));
    slot.after(t);
  }
  t.appendChild($('mg-quiet'));
  t.hidden = $('mg-quiet').hidden;
}
function renderGridSub() {
  const sub = $('mg-sub');
  const note = $('mg-note');
  clear(note);
  if (ST.themesErr && ST.feed) {
    note.appendChild(S.banner({ kind: 'warn', title: '분위기 점수를 불러오지 못했어요',
      busy: !ST.themesRetried,
      sub: ST.themesRetried ? '첫 줄은 그대로 보여요 · 잠시 뒤 다시 시도해 주세요' : '한 번 더 시도하는 중이에요 · 첫 줄은 그대로 보여요',
      action: ST.themesRetried ? { label: S.LABELS.retry, onClick: () => load() } : null }));
  }
  if (!ST.mine) { sub.textContent = '기사 많은 순 · 굵은 칸이 ' + (ST.day === ST.today ? '오늘' : S.fmtDate(ST.day)) + ' · 줄을 누르면 자세히'; return; }
  const m = ST.mineData;
  if (!m) { sub.textContent = '내 종목 불러오는 중'; return; }
  if (m.error) {
    sub.textContent = '';
    note.appendChild(infoBlock('내 종목을 불러오지 못했어요', '잠시 뒤 다시 시도해 주세요',
      [{ label: S.LABELS.retry, onClick: () => { ST.mineData = null; ST.minePromise = null; setMine(true); } }]));
    return;
  }
  if (m.noAccount) {
    sub.textContent = '';
    note.appendChild(infoBlock('이 기기에서 연 계좌가 없어요', '계좌를 열면 보유 종목의 분야만 골라 볼 수 있어요',
      [{ label: '계좌 열기', onClick: e => openAccounts(e.currentTarget) },
       { label: S.LABELS.clearFilter, onClick: () => setMine(false) }]));
    return;
  }
  const accNames = m.accounts.map(a => a.name).join('·');
  const mineTids = Object.keys(m.byTheme);
  if (!mineTids.length) {
    sub.textContent = '';
    note.appendChild(infoBlock(m.empty ? '열린 계좌에 보유 종목이 없어요' : '보유 종목이 들어간 분야가 없어요', accNames,
      [{ label: S.LABELS.clearFilter, onClick: () => setMine(false) }]));
    return;
  }
  const withNews = rows().filter(r => m.byTheme[r.tid] && r.items !== 0);
  sub.textContent = '내 종목 분야 ' + mineTids.length + ' · ' + accNames + ' · 나머지는 흐리게';
  if (!withNews.length && !ST.themesErr) {
    note.appendChild(infoBlock((ST.day === ST.today ? '오늘' : S.fmtDate(ST.day)) + ' 내 종목 분야 소식이 없어요',
      accNames + ' 보유 분야 · ' + mineTids.map(nameOf).join(' · '),
      [{ label: S.LABELS.clearFilter, onClick: () => setMine(false) }]));
  }
}
function infoBlock(title, sub, actions) {
  return el('div', { className: 'mg-info', role: 'status' }, [svgIco('info'),
    el('div', { className: 'mg-info-text' }, [el('p', { className: 'mg-info-title' }, [title]),
      sub ? el('p', { className: 'mg-info-sub' }, [sub]) : null]),
    (actions || []).length ? el('div', { className: 'mg-info-acts' }, actions.map(a =>
      el('button', { type: 'button', className: 'btn btn-sm', onClick: a.onClick }, [a.label]))) : null,
  ]);
}

// a row opens the field panel; the name is the keyboard target
document.querySelector('.mg-card').addEventListener('click', onRowClick);
function onRowClick(e) {
  const tr = e.target.closest('tr.mg-row');
  if (!tr) return;
  openTheme(tr.dataset.tid, true);
}

// ── 오늘의 핵심 ──────────────────────────────────────────────────────
function themeSet() { return ST.mine && ST.mineData && ST.mineData.byTheme ? ST.mineData.byTheme : null; }
function passesMine(x) {
  const set = themeSet();
  if (!ST.mine) return true;
  if (!set) return false;
  return (x.themes || []).some(t => set[t.id]);
}
function timeOf(x) {
  const ts = U.itemTs(x);
  return U.itemDay(x) === ST.today ? S.fmtTime(ts) : S.fmtDateTime(ts);
}
function renderCore() {
  const list = $('core-list'), sub = $('core-sub');
  clear(list);
  if (!ST.feed) {
    sub.textContent = '';
    list.appendChild(el('div', { className: 'core-empty' }, [ST.feedErr ? '소식을 불러오지 못했어요' : '']));
    return;
  }
  const items = ST.feed.news.concat(ST.feed.social)
    .filter(x => (x.importance || 0) >= 4 && U.itemDay(x) === ST.day && passesMine(x))
    .sort((a, b) => (b.importance - a.importance) || String(U.itemTs(b)).localeCompare(String(U.itemTs(a))))
    .slice(0, CORE_N);
  sub.textContent = ST.mine ? '내 종목 분야 ' + items.length + '건' : '중요도 4 이상 ' + items.length + '건';
  if (!items.length) {
    list.appendChild(el('div', { className: 'core-empty' }, [ST.mine ? '내 종목 분야에 중요한 소식이 없어요'
      : (ST.day === ST.today ? '오늘' : '이날') + ' 중요한 소식이 아직 없어요']));
    return;
  }
  items.forEach(x => {
    const t = (x.themes || [])[0];
    list.appendChild(el('a', { className: 'core-card', href: x.url || '#', target: '_blank', rel: 'noopener noreferrer' }, [
      el('div', { className: 'core-top' }, [
        t ? el('span', { className: 'chip' }, [svgIco(S.newsThemeIcon(t.id)), t.kr]) : el('span', {}),
        U.dots(x.importance),
      ]),
      el('p', { className: 'core-title clamp-2' }, [x.title]),
      el('p', { className: 'core-meta' }, [U.srcLabel(x.source) + ' · ' + timeOf(x), svgIco('external-link'),
        el('span', { className: 'sr-only' }, ['새 탭에서 열려요'])]),
    ]));
  });
}

// ── 뉴스 흐름 · 소셜 ─────────────────────────────────────────────────
const seg = S.segmented({ items: [{ value: 'news', label: '뉴스' }, { value: 'social', label: '소셜' }],
  value: 'news', full: true, label: '흐름 고르기', onChange: v => { ST.flowTab = v; paintFlowTabs(); } });
$('flow-seg').appendChild(seg);
function paintFlowTabs() {
  $('flow-news').hidden = ST.flowTab !== 'news';
  $('flow-social').hidden = ST.flowTab !== 'social';
}
function renderFlow(kind) {
  const ul = $('flow-' + kind + '-list'), foot = $('flow-' + kind + '-foot');
  clear(ul); clear(foot);
  if (!ST.feed) { ul.appendChild(el('li', { className: 'flow-empty' }, [ST.feedErr ? '소식을 불러오지 못했어요' : ''])); return; }
  const all = (ST.feed[kind] || []).filter(passesMine);
  const shown = ST.flowAll[kind] ? all : all.slice(0, FLOW_FIRST[kind]);
  if (!all.length) {
    ul.appendChild(el('li', { className: 'flow-empty' }, [ST.mine ? '내 종목 분야 소식이 없어요' : '소식이 아직 없어요']));
  }
  shown.forEach(x => {
    const t = (x.themes || [])[0];
    const meta = [t ? t.kr : null, U.srcLabel(x.source), timeOf(x)].filter(Boolean).join(' · ');
    ul.appendChild(el('li', {}, [el('a', { className: 'fi', href: x.url || '#', target: '_blank', rel: 'noopener noreferrer' }, [
      el('div', { className: 'fi-body' }, [
        el('p', { className: 'fi-meta' }, [t ? svgIco(S.newsThemeIcon(t.id)) : null, el('span', { className: 'ellipsis' }, [meta])]),
        el('p', { className: 'fi-title' }, [x.title]),
      ]),
      svgIco('external-link', 'fi-ext'),
      el('span', { className: 'sr-only' }, ['새 탭에서 열려요']),
    ])]));
  });
  if (all.length > FLOW_FIRST[kind]) {
    foot.appendChild(el('button', { type: 'button', className: 'btn btn-sm', 'aria-expanded': ST.flowAll[kind] ? 'true' : 'false',
      onClick: () => { ST.flowAll[kind] = !ST.flowAll[kind]; renderFlow(kind); } },
      [ST.flowAll[kind] ? S.LABELS.collapse : S.LABELS.showAll]));
  }
}

// ── 내 종목 ──────────────────────────────────────────────────────────
// Only the books this device has open; their names and symbols never leave
// the page except as the symbol list the shared route asks for.
function loadMine() {
  if (ST.minePromise) return ST.minePromise;
  ST.minePromise = (async () => {
    const q = { toast: false, gate: false };
    const sess = await S.api('/api/accounts/session', q);
    const openIds = (sess.open || []).map(o => o.account_id);
    if (!openIds.length) return { noAccount: true, accounts: [], byTheme: {} };
    const names = {};
    try { (await S.api('/api/accounts', q)).accounts.forEach(a => { names[a.account_id] = a.name || a.account_id; }); }
    catch (e) { /* names fall back to ids */ }
    const books = await Promise.all(openIds.map(id => S.api('/api/accounts/' + encodeURIComponent(id), q).catch(() => null)));
    const accounts = [], held = {};
    books.forEach((b, i) => {
      if (!b) return;
      const acc = { id: openIds[i], name: (b.account && b.account.name) || names[openIds[i]] || openIds[i] };
      accounts.push(acc);
      (b.holdings || []).filter(h => +h.qty > 0).forEach(h => {
        (held[h.symbol_id] = held[h.symbol_id] || { sid: h.symbol_id,
          name: h.name_kr || h.name || h.code || String(h.symbol_id).replace(/^[A-Z]+:/, ''), accounts: [] })
          .accounts.push(acc);
      });
    });
    if (!accounts.length) throw new Error('books unreadable');
    const sids = Object.keys(held);
    const byTheme = {};
    if (!sids.length) return { accounts, byTheme, empty: true };
    const map = await S.api('/api/analysis/news/symbol-themes?symbols=' + encodeURIComponent(sids.slice(0, 100).join(',')), q);
    Object.entries(map.symbols || {}).forEach(([sid, tids]) => {
      (tids || []).forEach(t => { (byTheme[t] = byTheme[t] || []).push(held[sid]); });
    });
    return { accounts, byTheme };
  })().catch(e => { ST.minePromise = null; throw e; });
  return ST.minePromise;
}
async function setMine(on) {
  ST.mine = on;
  paintMineButtons();
  if (!on) { renderAll(); return; }
  ST.minePromise = null;               // re-read: a trade since the last look moves the fields
  if (!ST.mineData) renderAll();
  try { ST.mineData = await loadMine(); }
  catch (e) { ST.mineData = { error: true }; }
  if (ST.mine) renderAll();
  themePanel.rerender();
}

// ── field panel (SCR-THEME) + ?theme= ────────────────────────────────
let closingFromHistory = false;
const themePanel = window.NewsThemePanel.create({
  keepOpenOn: '.mg-row, .mg-name',
  row: rowOf, name: nameOf,
  day: () => ST.day, today: () => ST.today, collectedAt,
  mine: () => (ST.mineData && !ST.mineData.error ? Promise.resolve(ST.mineData) : loadMine().then(m => { ST.mineData = m; return m; })),
  onClose: () => {
    markOpen(null);
    if (closingFromHistory) { closingFromHistory = false; return; }
    const u = new URL(location.href);
    if (!u.searchParams.has('theme')) return;
    if (history.state && history.state.newsTheme) history.back();
    else { u.searchParams.delete('theme'); history.replaceState(null, '', u.pathname + u.search + u.hash); }
  },
});
function markOpen(tid) {
  document.querySelectorAll('tr.mg-row').forEach(tr => tr.classList.toggle('is-open', tr.dataset.tid === tid));
}
function openTheme(tid, push) {
  if (!tid) return;
  const u = new URL(location.href);
  if (push && u.searchParams.get('theme') !== tid) {
    const already = u.searchParams.has('theme');
    u.searchParams.set('theme', tid);
    // another field while the panel is open swaps its content, not history
    if (already) history.replaceState(history.state, '', u.pathname + u.search + u.hash);
    else history.pushState({ newsTheme: true }, '', u.pathname + u.search + u.hash);
  }
  markOpen(tid);
  themePanel.open(tid);
}
window.addEventListener('popstate', () => {
  const tid = new URL(location.href).searchParams.get('theme');
  if (tid && tid !== themePanel.current()) openTheme(tid, false);
  else if (!tid && themePanel.isOpen()) { closingFromHistory = true; themePanel.close(); }
});

// ── load ─────────────────────────────────────────────────────────────
function readCache() {
  try { const c = JSON.parse(localStorage.getItem(CACHE_KEY) || 'null'); return c && c.feed ? c : null; }
  catch (e) { return null; }
}
function writeCache() {
  try { localStorage.setItem(CACHE_KEY, JSON.stringify({ feed: ST.feed, themes: ST.themes, okAt: ST.okAt })); }
  catch (e) { /* private mode or full: the page works without it */ }
}
function renderLoading() {
  main.setAttribute('aria-busy', 'true');
  ST.today = U.kstNow().day; ST.day = null;
  renderGridHead();
  const body = $('mg-body');
  clear(body);
  for (let i = 0; i < 23; i++) {
    body.appendChild(el('tr', { 'aria-hidden': 'true' }, [el('td', { colspan: '11' }, [el('span', { className: 'sk mg-sk-row' })])]));
  }
  const list = $('core-list');
  clear(list);
  for (let i = 0; i < 4; i++) list.appendChild(el('div', { className: 'core-sk' }, [S.skeleton(['30%', '100%', '45%'])]));
  $('flow-wrap').hidden = true;
}
function renderError() {
  // nothing read and nothing kept: one block in place of the page
  main.setAttribute('aria-busy', 'false');
  $('nl').hidden = true;
  $('nh-legend').hidden = true;
  const b = $('nh-banner');
  clear(b);
  b.appendChild(el('div', { className: 'card' }, [S.stateBlock({ kind: 'error', title: '소식을 불러오지 못했어요',
    body: '잠시 뒤 다시 시도해 주세요', actions: [{ label: S.LABELS.retry, onClick: () => { clear(b); $('nl').hidden = false; renderLoading(); load(); } }] })]));
  renderHead();
}
function renderBanner() {
  const b = $('nh-banner');
  clear(b);
  if (!ST.stale && !ST.feedErr) return;
  const at = collectedAt();
  const checked = ST.okAt ? (U.kstDay(ST.okAt) === ST.today ? S.fmtTime(ST.okAt) : S.fmtDateTime(ST.okAt)) : null;
  b.appendChild(S.banner({ kind: 'error', title: '소식을 불러오지 못했어요',
    sub: ST.stale ? [at ? '아래는 ' + (U.kstDay(at) === ST.today ? S.fmtTime(at) : S.fmtDateTime(at)) + '에 모은 소식이에요' : '아래는 지난번에 불러온 소식이에요',
      checked ? checked + ' 확인' : null].filter(Boolean).join(' · ') : '잠시 뒤 다시 시도해 주세요',
    action: { label: S.LABELS.retry, onClick: () => load() } }));
}
function renderAll() {
  main.setAttribute('aria-busy', 'false');
  $('nl').hidden = false;
  $('nh-legend').hidden = false;
  $('flow-wrap').hidden = false;
  $('nl').classList.toggle('is-stale', ST.stale);
  pickDay();
  renderHead();
  renderBanner();
  renderGrid();
  renderCore();
  renderFlow('news');
  renderFlow('social');
  paintFlowTabs();
}

let loading = null;
async function load() {
  if (loading) return loading;
  loading = (async () => {
    const q = { toast: false, gate: false };
    const [f, t] = await Promise.allSettled([
      S.api('/api/analysis/news/feed', q),
      S.api('/api/analysis/news/themes?days=30', q),
    ]);
    const feedOk = f.status === 'fulfilled', themesOk = t.status === 'fulfilled';
    if (feedOk) ST.feed = f.value;
    if (themesOk) ST.themes = t.value;
    ST.feedErr = !feedOk;
    if (themesOk) { ST.themesErr = false; ST.themesRetried = false; }
    if (feedOk && themesOk) { ST.okAt = new Date().toISOString(); ST.stale = false; writeCache(); }
    if (!feedOk) {
      if (!ST.feed) {
        const c = readCache();
        if (c) { ST.feed = c.feed; ST.themes = ST.themes || c.themes; ST.okAt = c.okAt; }
      }
      if (!ST.feed && !ST.themes) { renderError(); return; }
      ST.stale = !!ST.feed;
    }
    if (!themesOk) {
      if (ST.themes && !feedOk) { /* stale view keeps the grid it had */ }
      else { ST.themesErr = true; ST.themes = null; }
    }
    renderAll();
    if (ST.themesErr && !ST.themesRetried) setTimeout(retryThemes, 3000);
    const open = new URL(location.href).searchParams.get('theme');
    if (open && !themePanel.isOpen()) openTheme(open, false);
    else if (themePanel.isOpen()) themePanel.rerender();
  })().finally(() => { loading = null; });
  return loading;
}
// the grid route failed once: try it again by itself, then hand over the button
async function retryThemes() {
  try {
    ST.themes = await S.api('/api/analysis/news/themes?days=30', { toast: false, gate: false });
    ST.themesErr = false;
    if (ST.feed) { ST.okAt = new Date().toISOString(); writeCache(); }
  } catch (e) { ST.themesRetried = true; }
  renderAll();
  if (themePanel.isOpen()) themePanel.rerender();
}

// ── after-lock: the account page locks this device's session and comes
//    back here (location.replace('/')). It leaves the account id in
//    sessionStorage 'pdash.locked' (or ?locked=<id>); the toast names it.
async function afterLock() {
  let id = null;
  const u = new URL(location.href);
  if (u.searchParams.has('locked')) {
    id = u.searchParams.get('locked');
    u.searchParams.delete('locked');
    history.replaceState(history.state, '', u.pathname + u.search + u.hash);
  }
  try {
    const v = sessionStorage.getItem('pdash.locked');
    if (v) { sessionStorage.removeItem('pdash.locked'); if (!id) { try { id = JSON.parse(v).id || v; } catch (e) { id = v; } } }
  } catch (e) { /* storage blocked */ }
  if (!id) return;
  let name = id;
  try { const a = (await S.api('/api/accounts', { toast: false })).accounts.find(x => x.account_id === id); if (a && a.name) name = a.name; }
  catch (e) { /* the id stands in */ }
  S.toast(name + U.objParticle(name) + ' 이 기기에서 잠갔어요');
}

renderLoading();
load();
afterLock();
S.whenVisible(load, POLL_MS);
// a page restored from the back/forward cache re-reads
window.addEventListener('pageshow', e => { if (e.persisted) load(); });
})();
