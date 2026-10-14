// Shared floor for the screens — injected by server.py right after the
// theme tokens and before the page's own <script>. Everything is exported on
// ONE namespace, window.S, so a page global can never shadow it (a page's own
// api() with another prefix would otherwise reroute every shared call).
// Nothing here knows an account or a symbol — both arrive as arguments
// (ops notes §4, the screen edition).
// Colours come only from window.__THEME_TOKENS (theme.json); no literals.
// The public surface is documented for the screen builders in FOUNDATION.md
// (redesign stage 1).
(function () {
'use strict';
const T = Object.assign({ values: {}, themes: {} }, window.__THEME_TOKENS || {});

// ── DOM ──────────────────────────────────────────────────────────────
// Canonical regime:
//   className · style (string) · dataset (object) · on* → addEventListener
//   null/undefined attribute → omitted (setAttribute('disabled', null) would
//   stringify to "null" and wrongly disable) · true → '' · false → omitted
//   children: null/undefined skipped, string/number → text node
function el(tag, props, children) {
  const e = document.createElement(tag);
  if (props) {
    for (const k in props) {
      const val = props[k];
      if (val == null || val === false) continue;
      if (k === 'className') e.className = val;
      else if (k === 'style') e.style.cssText = val;   // not setAttribute: a nonce CSP blocks style attributes
      else if (k === 'dataset') { for (const d in val) e.dataset[d] = val[d]; }
      else if (k.startsWith('on') && typeof val === 'function') {
        e.addEventListener(k.slice(2).toLowerCase(), val);
      } else e.setAttribute(k, val === true ? '' : val);
    }
  }
  if (children) {
    for (const c of children) {
      if (c == null || c === false) continue;
      e.appendChild(typeof c === 'string' || typeof c === 'number'
        ? document.createTextNode(String(c)) : c);
    }
  }
  return e;
}
function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
// Lucide sprite instance — <svg class="lci"><use href="#i-name"/></svg>.
// Decorative by default (aria-hidden); the control that holds it carries the
// name. Every name used anywhere exists in SPRITE below (tests check).
function svgIco(name, cls) {
  const NS = 'http://www.w3.org/2000/svg';
  const s = document.createElementNS(NS, 'svg');
  s.setAttribute('class', 'lci' + (cls ? ' ' + cls : ''));
  s.setAttribute('aria-hidden', 'true');
  s.setAttribute('focusable', 'false');
  const u = document.createElementNS(NS, 'use');
  u.setAttribute('href', '#i-' + name);
  s.appendChild(u);
  return s;
}
function spinner() { return svgIco('loader-circle', 'spin'); }

// ── design tokens ────────────────────────────────────────────────────
// designColor returns the literal itself, not 'var(--x)': canvas cannot render
// a CSS variable. Takes a token name ('market/up', 'chart/grid').
function designColor(token) {
  return T.values[token] || T.values['text/primary'] || '';
}
// Non-colour token as its raw string, e.g. designToken('bp/sm') → '600px'.
function designToken(name) { return T.values[name] || ''; }
function hexRgb(hex) {
  const n = parseInt(hex.slice(1, 7), 16);
  return ((n >> 16) & 255) + ', ' + ((n >> 8) & 255) + ', ' + (n & 255);
}
function designRgba(token, alpha) {
  const h = designColor(token);
  return h ? 'rgba(' + hexRgb(h) + ', ' + alpha + ')' : '';
}
// The CSS variable name of a token — the one rule in theme.json:
// 'bg/default' → '--bg-default'.
function cssVar(token) { return '--' + token.replace(/\//g, '-'); }
// News field id → Lucide icon name (fields show by icon only, no colour).
function newsThemeIcon(id) {
  const t = T.themes[id];
  return (t && t.icon) || 'newspaper';
}
// Feed source id → display name (ids are internal slugs). Kept from the old
// news card: news/util.js and sources.html name sources through S.srcName.
const SRC_NAME = {
  electrek:'Electrek', electrek_ev:'Electrek',
  thelec_battery:'디일렉 배터리', thelec_semi:'디일렉', etnews_it:'전자신문',
  aitimes:'AI타임스', geeknews:'GeekNews', docdocdoc:'청년의사',
  statnews:'STAT News', biorxiv:'bioRxiv', semianalysis:'SemiAnalysis',
  robotreport:'Robot Report', irobotnews:'로봇신문',
  spacenews:'SpaceNews', payload:'Payload', finextra:'Finextra',
  hk_finance:'한국경제', mk_stock:'매일경제 증권', einfomax:'연합인포맥스',
  gcaptain:'gCaptain', splash247:'Splash 247', '3dpi':'3D Printing Industry',
  fed_press:'Fed', bok_press:'한국은행',
  reddit_ml:'r/MachineLearning', reddit_semi:'r/semiconductors',
  reddit_robotics:'r/robotics', reddit:'Reddit',
  // sources added 2026-09-02; the reddit_* and steeldaily/ferrotimes keys
  // above stay because articles collected under them are still in the window
  snmnews:'철강금속신문', steeldaily:'스틸데일리', ferrotimes:'페로타임즈',
  mining_com:'Mining.com', metalminer:'MetalMiner',
  oilprice:'OilPrice', eia_tie:'EIA', electimes:'전기신문',
  ess_news:'Energy-Storage.news', utilitydive:'Utility Dive',
  dcd:'Data Center Dynamics', defensenews:'Defense News',
  breakingdefense:'Breaking Defense', sec_press:'SEC',
  fiercebiotech:'Fierce Biotech', electrive:'electrive',
  cleantechnica:'CleanTechnica', theelec_en:'디일렉(영문)', eetimes:'EE Times',
  '3dprint_com':'3DPrint.com', voxelmatters:'VoxelMatters',
  nasa:'NASA', nsf:'NASASpaceflight', bea:'BEA',
  recyclingtoday:'Recycling Today', mk_industry:'매일경제 산업',
  arxiv_ai:'arXiv cs.AI', arxiv_robotics:'arXiv cs.RO',
  arxiv_hardware:'arXiv cs.AR', arxiv_qbio:'arXiv q-bio',
  arxiv_battery:'arXiv mtrl-sci',
};
function srcName(id) { return SRC_NAME[id] || id || ''; }

// ── label dictionary: one Korean word per shared action (인계 §3.6).
//    Pages take the word from here instead of picking a synonym. Three words
//    never appear on screen (tests): the ones for "remove", "release", "erase"
//    — 삭제 / 거르기 풀기 / 버리기 cover them.
const LABELS = {
  delete: '삭제', cancel: '취소', confirm: '확인', save: '저장', close: '닫기',
  reanalyze: '다시 분석', showAll: '전체 보기', collapse: '접기',
  clearFilter: '거르기 풀기', retry: '다시 시도', discard: '버리기', keepEditing: '계속 입력',
  undo: '되돌리기', viewTrade: '거래 보기', recordTrade: '거래 기록', buy: '매수', sell: '매도',
  addWatch: '관심 추가', fixHolding: '보유 정정', fixCash: '예수금 정정',
  lockDevice: '이 기기에서 잠그기', change: '바꾸기', sellAll: '전량', currentPrice: '현재가',
  newChat: '새 대화', send: '보내기', create: '만들기', addAccount: '계좌 추가', record: '기록',
  askStock: '이 종목 묻기', oldCoords: '지난 좌표 보기', pickDate: '날짜 선택',
  // chrome
  back: '뒤로', news: '수집', account: '계좌', chat: '채팅', accountMenu: '계좌 메뉴',
  refresh: '새로고침', forgotPin: 'PIN을 잊었어요', otherAccount: '다른 계좌',
};

// ── numbers (인계 §3.1) ───────────────────────────────────────────────
// Every formatter is pure: value in, string out; null/NaN → '—'.
// MINUS is U+2212 — the hyphen is shorter than '+' and reads as a dash.
const MINUS = '−';
const NF = {};
function nf(min, max) {
  const k = min + ':' + max;
  return NF[k] || (NF[k] = new Intl.NumberFormat('en-US',
    { minimumFractionDigits: min, maximumFractionDigits: max }));
}
function bad(v) { return v == null || v === '' || isNaN(v); }
function ccyDigits(ccy) { return ccy === 'USD' ? 2 : 0; }
function ccySymbol(ccy) { return ccy === 'USD' ? '$' : '₩'; }
// 54338800 → '54,338,800' (KRW, no decimals) · 12.5 → '12.50' (USD, two)
function fmtPrice(v, ccy) {
  if (bad(v)) return '—';
  const d = ccyDigits(ccy), a = Math.abs(+v);
  return (+v < 0 ? MINUS : '') + nf(d, d).format(a);
}
// '₩54,338,800' · '$12,340.56'
function fmtMoney(v, ccy) {
  if (bad(v)) return '—';
  const d = ccyDigits(ccy);
  return (+v < 0 ? MINUS : '') + ccySymbol(ccy) + nf(d, d).format(Math.abs(+v));
}
// Profit and loss carry a sign: '+₩1,982,400' · '−₩120,000' · '₩0'
function fmtPnl(v, ccy) {
  if (bad(v)) return '—';
  const d = ccyDigits(ccy), r = +(+v).toFixed(d);
  return (r > 0 ? '+' : r < 0 ? MINUS : '') + ccySymbol(ccy) + nf(d, d).format(Math.abs(r));
}
// pct is in percent units (31.2 means 31.2%): '+31.2%' · '−1.0%' · '0.0%'
function fmtPnlPct(pct, digits) {
  if (bad(pct)) return '—';
  const d = digits == null ? 1 : digits, r = +(+pct).toFixed(d);
  return (r > 0 ? '+' : r < 0 ? MINUS : '') + nf(d, d).format(Math.abs(r)) + '%';
}
// Price moves carry an arrow and no sign: '▲4,900' · '▼1,200' · '0'
function fmtChange(v, ccy) {
  if (bad(v)) return '—';
  const d = ccyDigits(ccy), r = +(+v).toFixed(d);
  return (r > 0 ? '▲' : r < 0 ? '▼' : '') + nf(d, d).format(Math.abs(r));
}
// '▲2.53%' · '▼1.06%' · '0.00%'
function fmtChangePct(pct, digits) {
  if (bad(pct)) return '—';
  const d = digits == null ? 2 : digits, r = +(+pct).toFixed(d);
  return (r > 0 ? '▲' : r < 0 ? '▼' : '') + nf(d, d).format(Math.abs(r)) + '%';
}
// Quantity: up to 4 decimals, trailing zeros dropped — 12 → '12' · 0.5 → '0.5'
function fmtQty(q) {
  if (bad(q)) return '—';
  return (+q < 0 ? MINUS : '') + nf(0, 4).format(Math.abs(+q));
}
// Plain count with grouping: 4296 → '4,296'
function fmtCount(n) { return bad(n) ? '—' : nf(0, 0).format(+n); }
// Asset axis in 만 units: 56000000 → '5,600만'
function fmtAxisMan(v) {
  if (bad(v)) return '';
  const m = Math.round(+v / 1e4);
  return (m < 0 ? MINUS : '') + nf(0, 0).format(Math.abs(m)) + '만';
}
// Colour class for a signed value: 'up' | 'down' | 'flat' (CSS .t-up/.t-down).
function trend(v) { return bad(v) || +v === 0 ? 'flat' : +v > 0 ? 'up' : 'down'; }

// ── dates (KST unless a market says otherwise) ───────────────────────
const WEEKDAY = ['일', '월', '화', '수', '목', '금', '토'];
const TZ = { KR: 'Asia/Seoul', US: 'America/New_York' };
// Wall-clock parts of `t` (Date | ISO | epoch seconds | 'YYYY-MM-DD') in a
// time zone. A bare date is a calendar date, not midnight UTC.
function parts(t, tz) {
  if (t == null || t === '') return null;
  if (typeof t === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(t)) {
    const [y, m, d] = t.split('-').map(Number);
    const wd = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
    return { y, m, d, hh: 0, mm: 0, wd, date: t };
  }
  const ms = t instanceof Date ? t.getTime() : typeof t === 'number' ? t * 1000 : new Date(t).getTime();
  if (isNaN(ms)) return null;
  const f = new Intl.DateTimeFormat('en-US', { timeZone: tz || TZ.KR, year: 'numeric',
    month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric', hourCycle: 'h23',
    weekday: 'short' }).formatToParts(new Date(ms));
  const p = {};
  f.forEach(x => { p[x.type] = x.value; });
  const y = +p.year, m = +p.month, d = +p.day;
  return { y, m, d, hh: +p.hour, mm: +p.minute,
    wd: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(p.weekday),
    date: y + '-' + String(m).padStart(2, '0') + '-' + String(d).padStart(2, '0') };
}
// '10/13(화)'
function fmtDate(t, tz) {
  const p = parts(t, tz);
  return p ? p.m + '/' + p.d + '(' + WEEKDAY[p.wd] + ')' : '';
}
// '10/13(화) 15:42'
function fmtDateTime(t, tz) {
  const p = parts(t, tz);
  return p ? fmtDate(p.date) + ' ' + String(p.hh).padStart(2, '0') + ':'
    + String(p.mm).padStart(2, '0') : '';
}
// 'HH:MM'
function fmtTime(t, tz) {
  const p = parts(t, tz);
  return p ? String(p.hh).padStart(2, '0') + ':' + String(p.mm).padStart(2, '0') : '';
}
// 'YYYY-MM-DD' of `now` in the market's own calendar
function marketToday(market, now) { const p = parts(now || new Date(), TZ[market] || TZ.KR); return p ? p.date : ''; }
// Weekdays from date a (exclusive) to date b (inclusive) — trading days with
// weekends removed and holidays not known (ops notes §11: the same approximation
// the analysis trigger uses; a holiday makes it one day early, never late).
function tradingDaysBetween(a, b) {
  if (!a || !b || a >= b) return 0;
  let n = 0;
  const d = new Date(a + 'T00:00:00Z'), end = new Date(b + 'T00:00:00Z');
  while (d < end) {
    d.setUTCDate(d.getUTCDate() + 1);
    const w = d.getUTCDay();
    if (w !== 0 && w !== 6) n++;
  }
  return n;
}
function prevWeekday(date) {
  const d = new Date(date + 'T00:00:00Z');
  do { d.setUTCDate(d.getUTCDate() - 1); } while (d.getUTCDay() === 0 || d.getUTCDay() === 6);
  return d.toISOString().slice(0, 10);
}
// Regular session hours in market-local minutes. Holidays are not known here:
// a weekday holiday reads as 'closed', and a page that knows better passes
// phase 'holiday' itself.
const SESSION = { KR: [9 * 60, 15 * 60 + 30], US: [9 * 60 + 30, 16 * 60] };
function marketPhase(market, now) {
  const p = parts(now || new Date(), TZ[market] || TZ.KR);
  if (!p) return 'closed';
  if (p.wd === 0 || p.wd === 6) return 'holiday';
  const s = SESSION[market] || SESSION.KR, mins = p.hh * 60 + p.mm;
  return mins >= s[0] && mins < s[1] ? 'open' : 'closed';
}

// ── 손익 라벨 (인계 §3.2) ────────────────────────────────────────────
// o: { phase: 'open'|'closed'|'holiday', session: last session 'YYYY-MM-DD'
//      (the representative symbol's last daily bar), today: 'YYYY-MM-DD' }
// → { pnl: '오늘 손익 · 장중', head: '현재가 · 오늘', day: '오늘' | 'M/D(요일)' }
function pnlLabel(o) {
  o = o || {};
  if (o.phase === 'open') return { pnl: '오늘 손익 · 장중', head: '현재가 · 오늘', day: '오늘' };
  const day = o.session && o.session !== o.today ? fmtDate(o.session) : '오늘';
  const tail = o.phase === 'holiday' ? '휴장' : '마감';
  return { pnl: day + ' 손익 · ' + tail, head: '현재가 · ' + day, day };
}

// ── 시세 상태 (인계 §3.3) ────────────────────────────────────────────
// o: { quoteTs (ISO | epoch s — when the quote was RECEIVED), market: 'KR'|'US',
//      session: last trading day 'YYYY-MM-DD', phase (marketPhase), now }
// → { state: 'fresh'|'delayed'|'stale'|'none', minutes, tradingDays, text,
//     inTotal, inToday }
// The quote's own session is its market-local date, or the weekday before
// when it arrived before the open (a 07:00 quote is yesterday's close).
const QUOTE_DELAY_MIN = 20, QUOTE_STALE_DAYS = 8;
function quoteState(o) {
  o = o || {};
  const now = o.now ? new Date(o.now).getTime() : Date.now();
  const ms = o.quoteTs == null ? NaN : typeof o.quoteTs === 'number' ? o.quoteTs * 1000 : new Date(o.quoteTs).getTime();
  const none = { state: 'none', minutes: null, tradingDays: null, text: '시세 없음', inTotal: false, inToday: false };
  if (isNaN(ms) || (now - ms) / 86400000 > QUOTE_STALE_DAYS) return none;
  const minutes = Math.max(0, Math.floor((now - ms) / 60000));
  const market = o.market || 'KR';
  const p = parts(ms / 1000, TZ[market] || TZ.KR);
  const open = (SESSION[market] || SESSION.KR)[0];
  let qs = p.date;
  if (p.wd === 0 || p.wd === 6 || p.hh * 60 + p.mm < open) qs = prevWeekday(p.date);
  if (o.session && qs < o.session) {
    const n = Math.max(1, tradingDaysBetween(qs, o.session));
    return { state: 'stale', minutes, tradingDays: n, text: n + '거래일 전', inTotal: true, inToday: false };
  }
  if (o.phase === 'open' && minutes > QUOTE_DELAY_MIN) {
    return { state: 'delayed', minutes, tradingDays: 0, text: minutes + '분 전', inTotal: true, inToday: true };
  }
  return { state: 'fresh', minutes, tradingDays: 0, text: '', inTotal: true, inToday: true };
}

// ── 분석 상태 (인계 §3.4) ────────────────────────────────────────────
// o: { date: analysis 'YYYY-MM-DD' | null, held: boolean, status ('facts_only'…),
//      running, failed, hung: booleans, now }
// → { state, days, line: 'solid'|'faded'|'hidden'|'keep', badge, keepPrevious }
//   state: fresh | overdue | old | watch | none | running | failed | hung | facts
//   badge: the words to show (null = draw the verdict itself; 'old' draws it dimmed)
const ANALYSIS_FRESH_DAYS = 14, ANALYSIS_OLD_DAYS = 30;
function analysisState(o) {
  o = o || {};
  const today = marketToday('KR', o.now);
  const days = o.date ? Math.max(0, Math.round(
    (Date.parse(today + 'T00:00:00Z') - Date.parse(String(o.date).slice(0, 10) + 'T00:00:00Z')) / 86400000)) : null;
  const R = (state, line, badge, keep) => ({ state, days, line, badge, keepPrevious: !!keep });
  if (o.running) return R('running', 'keep', '분석 중', true);
  if (o.hung) return R('hung', 'keep', '응답 없음', true);
  if (o.failed) return R('failed', 'keep', '분석 실패', true);
  if (!o.held) return R('watch', days != null && days <= ANALYSIS_OLD_DAYS ? 'faded' : 'hidden', '자동 분석 없음');
  if (days == null) return R('none', 'hidden', '분석 없음');
  if (o.status === 'facts_only') return R('facts', 'hidden', '사실만');
  if (days <= ANALYSIS_FRESH_DAYS) return R('fresh', 'solid', null);
  if (days <= ANALYSIS_OLD_DAYS) return R('overdue', 'faded', '갱신 누락');
  return R('old', 'hidden', null);
}
// 'low' effort → 빠른 분석, anything else → 정밀 분석
function depthLabel(effort) { return effort === 'low' ? '빠른 분석' : '정밀 분석'; }
// Verdict label → tone for colour (verdict/positive · verdict/negative-text · text/muted)
function verdictTone(label) {
  return label === 'buy' || label === 'strong_buy' ? 'positive'
    : label === 'sell' || label === 'strong_sell' ? 'negative' : 'neutral';
}
// Verdict label → Korean word. Kept from the old badge: the account screen's
// holding rows name the verdict through S.LABEL_KO.
const LABEL_KO = {
  strong_buy: '적극 긍정', buy: '긍정', neutral: '중립',
  sell: '부정', strong_sell: '적극 부정',
};

// ── 분위기 구간 (인계 §3.5) ──────────────────────────────────────────
// score −1…+1 → { band, token, arrow, label }. |s| ≥ 0.30 strong, ≥ 0.10 a
// side, the boundary goes to the stronger band. null → a day without news
// (white cell), opts.error → the route failed (skeleton fill).
const MOOD = {
  'positive-strong': ['mood/positive-strong', '▲', '강한 긍정'],
  positive: ['mood/positive', '▲', '긍정'],
  neutral: ['mood/neutral', '▶', '중립'],
  negative: ['mood/negative', '▼', '부정'],
  'negative-strong': ['mood/negative-strong', '▼', '강한 부정'],
  none: ['bg/default', '', '없음'],
  error: ['skeleton/fill', '', '불러오지 못함'],
};
function moodBand(score, opts) {
  let band;
  if (opts && opts.error) band = 'error';
  else if (bad(score)) band = 'none';
  else {
    const s = +score, a = Math.abs(s);
    band = a >= 0.30 ? (s > 0 ? 'positive-strong' : 'negative-strong')
      : a >= 0.10 ? (s > 0 ? 'positive' : 'negative') : 'neutral';
  }
  const m = MOOD[band];
  return { band, token: m[0], arrow: m[1], label: m[2] };
}

// ── feedback ─────────────────────────────────────────────────────────
// The one passing message (Figma Toast): dark pill at the bottom, optional
// action. A message already on screen is not repeated — 25 cards losing the
// network at once is one failure, not 25.
//   toast(msg, kind?, { action: { label, onClick }, ms })
//   kind: undefined | 'ok' | 'err' (err adds the circle-x icon)
function toast(msg, kind, opts) {
  opts = opts || {};
  let host = document.getElementById('toast-host');
  if (!host) {
    host = el('div', { id: 'toast-host', role: 'status', 'aria-live': 'polite' });
    document.body.appendChild(host);
  }
  if ([...host.children].some(c => c.dataset.msg === msg)) return;
  const t = el('div', { className: 'toast' + (kind ? ' ' + kind : ''), dataset: { msg } }, [
    kind === 'err' ? svgIco('circle-x') : null,
    el('span', { className: 'toast-msg' }, [msg]),
    opts.action ? el('button', { type: 'button', className: 'btn toast-act',
      onClick: () => { t.remove(); opts.action.onClick(); } }, [opts.action.label]) : null,
  ]);
  host.appendChild(t);
  setTimeout(() => t.remove(), opts.ms || (opts.action ? 6000 : 4000));
}
// fetch wrapper for absolute /api paths. Non-2xx → toast + throw, so a
// caller that forgets its catch still shows the failure; a network failure
// (fetch itself throws) is toasted too, or it would render as "no data".
// A 403 opens the page's PIN gate (S.onForbidden) only when the server says
// the session is missing (detail === 'PIN required', or no detail at all).
// Any other 403 is a real answer — wrong step-up PIN, "PIN을 먼저 설정하세요" —
// and is thrown with its detail so the caller shows it inline.
// opts (besides fetch's): gate: false — throw on 'PIN required' instead of
// opening the page gate (dialogs re-ask the PIN themselves); toast: false —
// no toast (the caller shows the failure inline).
// The thrown Error carries .status (0 = network), .detail and .forbidden.
async function api(path, opts) {
  opts = opts || {};
  const quiet = opts.toast === false;
  let r;
  try { r = await fetch(path, opts); }
  catch (e) {
    if (!quiet) toast('네트워크 오류', 'err');   // no path: 25 cards, one toast (dedupe by text)
    const err = new Error('네트워크 오류'); err.status = 0; err.network = true; throw err;
  }
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = body.detail;
    if (r.status === 403 && (!detail || detail === 'PIN required')) {
      const err = new Error('PIN required'); err.status = 403; err.forbidden = true;
      if (opts.gate !== false && typeof window.S.onForbidden === 'function') window.S.onForbidden();
      throw err;
    }
    const msg = typeof detail === 'string' ? detail : ('HTTP ' + r.status);
    if (!quiet) toast(msg, 'err');
    const err = new Error(msg); err.status = r.status; err.detail = detail; throw err;
  }
  return body;
}

// ── overlay stack: Esc and focus belong to the topmost layer only ─────
const LAYERS = [];
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape' || !LAYERS.length) return;
  const top = LAYERS[LAYERS.length - 1];
  if (top.onEscape) { e.stopPropagation(); e.preventDefault(); top.onEscape(); }
}, true);
function pushLayer(layer) { LAYERS.push(layer); document.body.classList.add('has-layer'); }
function popLayer(layer) {
  const i = LAYERS.indexOf(layer);
  if (i >= 0) LAYERS.splice(i, 1);
  if (!LAYERS.some(l => l.modal)) document.body.classList.remove('has-layer');
}

// ── banner (Figma Banner): kind 'error' | 'warn' | 'info' ────────────
//   banner({ kind, title, sub, icon, busy, action: { label, onClick } })
function banner(o) {
  o = o || {};
  const icon = o.busy ? spinner()
    : svgIco(o.icon || (o.kind === 'error' ? 'circle-x' : o.kind === 'warn' ? 'triangle-alert' : 'info'));
  return el('div', { className: 'banner banner-' + (o.kind || 'info'),
                     role: o.kind === 'error' ? 'alert' : 'status' }, [
    icon,
    el('div', { className: 'banner-text' }, [
      el('div', { className: 'banner-title' }, [o.title || '']),
      o.sub ? el('div', { className: 'banner-sub' }, [o.sub]) : null,
    ]),
    o.action ? el('button', { type: 'button', className: 'btn btn-sm', onClick: o.action.onClick },
      [o.action.label]) : null,
  ]);
}

// ── state block (Figma State block): empty · error · info · warn ──────
//   stateBlock({ kind, icon, title, body, level: 'h2', actions: [{ label, onClick, primary, icon }] })
function stateBlock(o) {
  o = o || {};
  const kind = o.kind || 'empty';
  const icon = o.icon || { empty: 'briefcase', error: 'circle-x', info: 'info', warn: 'triangle-alert' }[kind];
  return el('div', { className: 'state state-' + kind, role: kind === 'error' ? 'alert' : null }, [
    svgIco(icon, 'state-ico'),
    el(o.level || 'h2', { className: 'state-title' }, [o.title || '']),
    o.body ? el('p', { className: 'state-body' }, [o.body]) : null,
    (o.actions || []).length ? el('div', { className: 'state-actions' }, o.actions.map(a =>
      el('button', { type: 'button', className: 'btn' + (a.primary ? ' btn-primary' : ''), onClick: a.onClick },
        [a.icon ? svgIco(a.icon) : null, a.label]))) : null,
  ]);
}

// ── skeleton bars (Figma Skeleton bar) — blink so they read as loading,
//    not as the same grey a failed mood cell uses ──
//   skeleton(['30%', '100%', '60%']) → a block of bars (aria-hidden)
function skeleton(widths) {
  return el('div', { className: 'sk-block', 'aria-hidden': 'true' },
    (widths || ['40%', '100%', '100%', '70%']).map(w => el('span', { className: 'sk', style: 'width:' + w })));
}

// ── segmented control (Figma Segmented · Period selector) ────────────
//   segmented({ items: [{ value, label, count }], value, onChange(value),
//               size: 'lg' (390 '큼', 49px), label: aria-label, full: true })
//   → element with .value and .setValue(v). Buttons carry aria-pressed.
function segmented(o) {
  const wrap = el('div', { className: 'seg' + (o.size === 'lg' ? ' seg-lg' : '') + (o.full ? ' seg-full' : ''),
                           role: 'group', 'aria-label': o.label || null });
  const btns = o.items.map(it => el('button', { type: 'button', dataset: { value: it.value },
    onClick: () => { if (wrap.value !== it.value) { wrap.setValue(it.value); if (o.onChange) o.onChange(it.value); } } },
    [it.label, it.count != null ? el('span', { className: 'tab-count' }, [String(it.count)]) : null]));
  btns.forEach(b => wrap.appendChild(b));
  wrap.setValue = v => {
    wrap.value = v;
    btns.forEach(b => b.setAttribute('aria-pressed', b.dataset.value === String(v) ? 'true' : 'false'));
  };
  wrap.setValue(o.value != null ? o.value : o.items[0].value);
  return wrap;
}

// ── a11y / lifecycle helpers shared by every page ────────────────────
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

// Keep Tab inside `container` until the returned release() runs; restores
// focus to whatever had it before. `initial` (element) overrides the first
// focus target.
function trapFocus(container, initial) {
  const before = document.activeElement;
  function onKey(e) {
    if (e.key !== 'Tab') return;
    const items = Array.from(container.querySelectorAll(FOCUSABLE))
      .filter(n => n.offsetParent !== null);
    if (!items.length) { e.preventDefault(); return; }
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
  container.addEventListener('keydown', onKey);
  const target = initial || Array.from(container.querySelectorAll(FOCUSABLE))
    .find(n => n.offsetParent !== null);
  if (target) target.focus();
  return function release() {
    container.removeEventListener('keydown', onKey);
    if (before && document.contains(before) && typeof before.focus === 'function') before.focus();
  };
}

// setInterval that sleeps while the tab is hidden and fires once on return —
// the harness's "auto-updating content has a pause" without a visible control.
function whenVisible(fn, ms) {
  let id = null;
  function start() { if (id == null) id = setInterval(fn, ms); }
  function stop() { if (id != null) { clearInterval(id); id = null; } }
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stop(); else { fn(); start(); }
  });
  if (!document.hidden) start();
  return stop;
}

// ── form lock (submitting): every control inside goes disabled, and back ──
function lockControls(root, on) {
  root.querySelectorAll('input,select,textarea,button').forEach(c => {
    if (on) { if (!c.disabled) { c.dataset.lockedBy = '1'; c.disabled = true; } }
    else if (c.dataset.lockedBy) { delete c.dataset.lockedBy; c.disabled = false; }
  });
}
function formSnapshot(root) {
  return Array.from(root.querySelectorAll('input,select,textarea'))
    .map(c => c.type === 'checkbox' || c.type === 'radio' ? c.checked : c.value).join('\u0001');
}

// ── dialog (Figma Dialog shell / Bottom sheet shell) ─────────────────
// 1440: centred card over the scrim (480, `wide` 560). 390: bottom sheet with
// a grabber, a back arrow and one full-width main button. Behaviour shared by
// every dialog (인계 §3.8):
//   · submitting locks every input/selection and spins the main button
//   · a failure keeps the input, shows a banner (words only) and the main
//     button becomes 「다시 시도」
//   · network/5xx with `verify`: no blind resend — the dialog first asks
//     verify(err) whether the write landed ('…확인 중' banner); true → done
//   · 403 'PIN required' (ctx.api never opens the page gate): the PIN is
//     re-asked over the dialog, and on success the request goes once more
//   · Esc / ✕ / ← / scrim close it, unless submitting; `confirmDiscard`
//     asks 버리기 / 계속 입력 when inputs changed
// dialog({
//   title, body: Node | (d) => Node, wide, primary: { label, danger, icon },
//   cancelLabel, onSubmit: async (ctx) => result   (ctx.api = S.api without
//   gate/toast; ctx.dialog = this), verify: async (err) => boolean,
//   verifyText: { title, sub }, failText: { title, sub }, account: { id, name },
//   confirmDiscard, initialFocus: Element | 'cancel', keepCancel (390 shows
//   취소 beside the main button — confirmations), onClose(result)
// }) → { el, body, close(result), banner(o|null), busy(bool), done: Promise }
function dialog(o) {
  o = o || {};
  let resolveDone;
  const done = new Promise(r => { resolveDone = r; });
  const titleId = 'dlg-' + Math.random().toString(36).slice(2, 8);
  const bannerSlot = el('div', { className: 's-dialog-banner' });
  const bodyWrap = el('div', { className: 's-dialog-body' });
  const back = el('button', { type: 'button', className: 'btn dlg-icon sm-down', 'aria-label': LABELS.back,
    onClick: () => api_.close(null) }, [svgIco('arrow-left')]);
  const x = el('button', { type: 'button', className: 'btn dlg-icon sm-up', 'aria-label': LABELS.close,
    onClick: () => api_.close(null) }, [svgIco('x')]);
  const cancel = o.cancelLabel === false ? null
    : el('button', { type: 'button', className: 'btn dlg-cancel', onClick: () => api_.close(null) },
      [o.cancelLabel || LABELS.cancel]);
  const primary = o.primary || o.onSubmit ? el('button', {
    type: 'button', className: 'btn ' + ((o.primary || {}).danger ? 'btn-danger' : 'btn-primary') + ' dlg-main',
    onClick: () => submit() }, [el('span', { className: 'dlg-main-label' }, [(o.primary || {}).label || LABELS.confirm])])
    : null;
  const box = el('div', { className: 's-dialog' + (o.wide ? ' is-wide' : ''), role: 'dialog',
                          'aria-modal': 'true', 'aria-labelledby': titleId }, [
    el('div', { className: 's-grabber', 'aria-hidden': 'true' }),
    el('div', { className: 's-dialog-head' }, [back, el('h2', { id: titleId }, [o.title || '']), x]),
    bannerSlot, bodyWrap,
    el('div', { className: 's-dialog-foot' }, [cancel, primary]),
  ]);
  const scrim = el('div', { className: 's-scrim' }, [box]);
  scrim.addEventListener('mousedown', e => { if (e.target === scrim) api_.close(null); });
  let busy = false, snap = '', release = null, closed = false, pinTried = false;
  const layer = { modal: true, onEscape: () => api_.close(null) };

  function setBusy(on) {
    busy = on;
    box.setAttribute('aria-busy', on ? 'true' : 'false');
    lockControls(box, on);
    if (primary) {
      const old = primary.querySelector('.spin');
      if (on && !old) primary.insertBefore(spinner(), primary.firstChild);
      if (!on && old) old.remove();
    }
  }
  function setBanner(b) { clear(bannerSlot); if (b) bannerSlot.appendChild(banner(b)); }
  function setMainLabel(t) { if (primary) primary.querySelector('.dlg-main-label').textContent = t; }
  function fail(err) {
    const f = o.failText || {};
    setBanner({ kind: 'error', title: f.title || '저장하지 못했어요',
      sub: (err && err.status && err.status < 500 && err.message) ? err.message + ' · 입력은 그대로예요'
        : (f.sub || '잠시 뒤 다시 시도해 주세요 · 입력은 그대로예요') });
    setMainLabel(LABELS.retry);
  }
  async function submit() {
    if (busy) return;
    if (!o.onSubmit) { close(true, true); return; }
    setBanner(null);
    setBusy(true);
    const ctx = { dialog: api_, api: (p, opt) => api(p, Object.assign({}, opt, { gate: false, toast: false })) };
    let result;
    try {
      result = await o.onSubmit(ctx);
    } catch (err) {
      if (err && err.forbidden && o.account && !pinTried) {
        pinTried = true;
        setBusy(false);
        const ok = await pinPrompt({ account: o.account });
        if (ok) return submit();               // the original request, once more
        fail(err); return;
      }
      if (err && (err.network || err.status >= 500) && o.verify) {
        const v = o.verifyText || {};
        setBanner({ kind: 'warn', busy: true, title: v.title || '저장됐는지 확인 중',
          sub: v.sub || '응답이 끊겨서 다시 확인하고 있어요' });
        let landed = false;
        try { landed = await o.verify(err); } catch (e) { landed = false; }
        if (landed) { setBusy(false); api_.close(landed === true ? true : landed, true); return; }
        setBusy(false);
        const f = o.failText || {};
        setBanner({ kind: 'error', title: f.title || '저장하지 못했어요',
          sub: f.sub || '저장되지 않은 걸 확인했어요 · 입력은 그대로예요' });
        setMainLabel(LABELS.retry);
        return;
      }
      setBusy(false);
      fail(err);
      return;
    }
    setBusy(false);
    api_.close(result === undefined ? true : result, true);
  }
  async function close(result, force) {
    if (closed || (busy && !force)) return;
    if (!force && o.confirmDiscard && formSnapshot(bodyWrap) !== snap) {
      const ok = await confirmModal({ title: '입력한 내용을 버릴까요?', body: '임시 저장은 없어요',
        confirmLabel: LABELS.discard, cancelLabel: LABELS.keepEditing, danger: true });
      if (!ok) return;
    }
    closed = true;
    popLayer(layer);
    scrim.remove();
    if (release) release();
    if (o.onClose) o.onClose(result);
    resolveDone(result);
  }
  const api_ = { el: box, body: bodyWrap, close, banner: setBanner, busy: setBusy, done,
                 setMainLabel, submit };
  const content = typeof o.body === 'function' ? o.body(api_) : o.body;
  if (content) bodyWrap.appendChild(typeof content === 'string' ? el('p', { className: 's-dialog-text' }, [content]) : content);
  if (!bodyWrap.firstChild) bodyWrap.hidden = true;
  if (primary) box.querySelector('.s-dialog-foot').classList.add('has-main');
  if (o.keepCancel) box.querySelector('.s-dialog-foot').classList.add('keep-cancel');
  pushLayer(layer);
  document.body.appendChild(scrim);
  snap = formSnapshot(bodyWrap);
  if (o.initialFocus === 'cancel' && cancel) {
    release = trapFocus(box, cancel);
    cancel.focus();
  } else {
    release = trapFocus(box, o.initialFocus instanceof Element ? o.initialFocus
      : bodyWrap.querySelector('input,select,textarea') || primary || x);
  }
  return api_;
}

// Replaces native confirm(): resolves true/false. Esc cancels, initial focus
// on 취소 (the safe button), focus returns to whatever opened it.
//   confirmModal({ title, body, confirmLabel, cancelLabel, danger })
function confirmModal(o) {
  o = o || {};
  const d = dialog({ title: o.title || LABELS.confirm, body: o.body || null,
    primary: { label: o.confirmLabel || LABELS.confirm, danger: !!o.danger },
    cancelLabel: o.cancelLabel || LABELS.cancel, initialFocus: 'cancel', keepCancel: true });
  // role: 'dialog' and the Escape route live in dialog(); 취소 resolves false
  return d.done.then(v => v === true);
}

// ── PIN input (Figma PIN input): six one-digit boxes ─────────────────
//   pinInput({ onComplete(pin), label }) → { el, clear(), error(msg|null), focus(), disable(on) }
function pinInput(o) {
  o = o || {};
  const msg = el('p', { className: 'pin-msg', role: 'alert' });
  msg.hidden = true;
  const cells = [];
  for (let i = 0; i < 6; i++) {
    cells.push(el('input', { className: 'pin-cell', type: 'password', inputmode: 'numeric',
      autocomplete: i === 0 ? 'one-time-code' : 'off', maxlength: '1', pattern: '[0-9]*',
      'aria-label': (o.label || 'PIN') + ' ' + (i + 1) + '번째 자리' }));
  }
  const wrap = el('div', { className: 'pin-input' }, [el('div', { className: 'pin-cells' }, cells), msg]);
  function value() { return cells.map(c => c.value).join(''); }
  function check() {
    const v = value();
    if (/^\d{6}$/.test(v) && o.onComplete) o.onComplete(v);
  }
  cells.forEach((c, i) => {
    c.addEventListener('input', () => {
      const d = c.value.replace(/\D/g, '');
      if (d.length > 1) {                       // paste or autofill of several digits
        d.slice(0, 6 - i).split('').forEach((ch, k) => { cells[i + k].value = ch; });
        cells[Math.min(5, i + d.length - 1)].focus();
      } else {
        c.value = d;
        if (d && i < 5) cells[i + 1].focus();
      }
      if (!msg.hidden) api_.error(null);
      check();
    });
    c.addEventListener('keydown', e => {
      if (e.key === 'Backspace' && !c.value && i > 0) { cells[i - 1].value = ''; cells[i - 1].focus(); e.preventDefault(); }
      else if (e.key === 'ArrowLeft' && i > 0) cells[i - 1].focus();
      else if (e.key === 'ArrowRight' && i < 5) cells[i + 1].focus();
    });
  });
  const api_ = {
    el: wrap,
    clear() { cells.forEach(c => { c.value = ''; }); cells[0].focus(); },
    error(t) { msg.textContent = t || ''; msg.hidden = !t; wrap.classList.toggle('is-error', !!t); },
    focus() { (cells.find(c => !c.value) || cells[5]).focus(); },
    disable(on) { cells.forEach(c => { c.disabled = !!on; }); },
  };
  return api_;
}
// POST /api/accounts/{id}/auth → 'ok' | { error: words } | 'no_pin'
async function authPin(accountId, pin) {
  let r;
  try {
    r = await fetch('/api/accounts/' + encodeURIComponent(accountId) + '/auth', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin }) });
  } catch (e) { return { error: '연결이 끊겼어요 · 다시 넣어 주세요' }; }
  if (r.ok) return 'ok';
  const d = await r.json().catch(() => ({}));
  if (r.status === 403 && d.detail === 'PIN을 먼저 설정하세요') return 'no_pin';
  if (r.status === 403 || r.status === 429) return { error: d.detail || 'PIN이 맞지 않아요' };
  if (r.status === 404) return { error: '계좌를 찾지 못했어요' };
  return { error: '확인하지 못했어요 · 잠시 뒤 다시 넣어 주세요' };
}
// The account line under a name: '키움 · ISA'
function accountSub(a) { return a ? (a.sub || [a.broker, a.account_type].filter(Boolean).join(' · ')) : ''; }

// DLG-PIN: re-ask the PIN over an open dialog. Resolves true once the
// session cookie is back, false when closed.
//   pinPrompt({ account: { id, name }, message, hint })
function pinPrompt(o) {
  o = o || {};
  const a = o.account || {};
  let pin;
  const d = dialog({
    title: 'PIN 다시 입력',
    body: () => {
      pin = pinInput({ label: (a.name || a.id) + ' PIN', onComplete: async v => {
        pin.disable(true);
        const res = await authPin(a.id, v);
        pin.disable(false);
        if (res === 'ok') { d.close(true, true); return; }
        pin.error(res === 'no_pin' ? 'PIN이 없어 열 수 없어요' : res.error);
        pin.clear();
      } });
      return el('div', { className: 'pin-prompt' }, [
        el('p', {}, [o.message || (a.name || '계좌') + ' 세션이 끝났어요 · 입력한 내용은 그대로예요']),
        pin.el,
        el('p', { className: 't-caption t-muted' }, [o.hint || '6자리를 넣으면 다시 보내요']),
      ]);
    },
    cancelLabel: LABELS.cancel,
  });
  return d.done.then(v => v === true);
}

// SCR-*.locked: the PIN gate inside the page (1440 a centred card, 390 the
// whole body). The page keeps its top bar and bottom tab.
//   pinGate({ account: { id, name, broker, account_type } , mount (default #main),
//             onUnlock (default reload), onOther (default go to '/') })
// Typical use: S.onForbidden = () => S.pinGate({ account, onUnlock: load })
function pinGate(o) {
  o = o || {};
  const a = o.account || {};
  const mount = o.mount || document.getElementById('main') || document.body;
  clear(mount);
  mount.setAttribute('aria-busy', 'false');
  document.body.classList.add('is-locked');          // 390: the gate is the whole white body
  const unlock = () => { document.body.classList.remove('is-locked'); if (o.onUnlock) o.onUnlock(); else location.reload(); };
  const note = el('div', { className: 'pin-note' }, [svgIco('info'), '계좌 주인에게 PIN 재설정을 부탁해요']);
  note.hidden = true;
  const card = el('section', { className: 'pin-gate', 'aria-labelledby': 'pin-gate-name' });
  function head() {
    return [el('div', { className: 'pin-lock' }, [svgIco('lock')]),
      el('h1', { id: 'pin-gate-name', className: 'pin-name' }, [a.name || a.id || '계좌']),
      accountSub(a) ? el('p', { className: 'pin-sub' }, [accountSub(a)]) : null];
  }
  function showNoPin() {
    clear(card);
    head().forEach(n => { if (n) card.appendChild(n); });
    card.appendChild(el('div', { className: 'pin-note' }, [svgIco('info'),
      'PIN이 없어 열 수 없어요 · 계좌 주인에게 설정을 부탁해요']));
    card.appendChild(el('div', { className: 'pin-links' }, [otherBtn()]));
  }
  function otherBtn() {
    return el('button', { type: 'button', className: 'btn pin-link',
      onClick: () => (o.onOther ? o.onOther() : location.assign('/')) }, [LABELS.otherAccount]);
  }
  const pin = pinInput({ label: (a.name || a.id) + ' PIN', onComplete: async v => {
    pin.disable(true);
    const res = await authPin(a.id, v);
    pin.disable(false);
    if (res === 'ok') { unlock(); return; }
    if (res === 'no_pin') { showNoPin(); return; }
    pin.error(res.error);
    pin.clear();
  } });
  head().forEach(n => { if (n) card.appendChild(n); });
  card.appendChild(el('p', { className: 'pin-label' }, ['PIN 6자리']));
  card.appendChild(pin.el);
  card.appendChild(el('div', { className: 'pin-links' }, [
    el('button', { type: 'button', className: 'btn pin-link', onClick: () => { note.hidden = !note.hidden; } },
      [LABELS.forgotPin]),
    el('span', { className: 'pin-dot', 'aria-hidden': 'true' }, ['·']),
    otherBtn(),
  ]));
  card.appendChild(note);
  mount.appendChild(el('div', { className: 'pin-gate-wrap' }, [card]));
  if (o.noPin) showNoPin(); else pin.focus();
  return { el: card, showNoPin };
}

// ── side panel (Figma PNL-*): 1440 right 420–480 below the top bar, 390 a
//    full sheet with a back arrow. Non-blocking (no scrim), keeps focus
//    inside while open, Esc closes.
//   panel({ title, onClose, keepOpenOn: CSS selector — clicks there do not close })
//   → { el, head (extra slot after the title), titleEl, body, open(), close(), isOpen() }
function panel(o) {
  o = o || {};
  const id = 'pnl-' + Math.random().toString(36).slice(2, 8);
  const titleEl = el('h2', { id }, [o.title || '']);
  const extra = el('div', { className: 's-panel-extra' });
  const closeBtn = (cls, ico, label) => el('button', { type: 'button', className: 'btn dlg-icon ' + cls,
    'aria-label': label, onClick: () => api_.close() }, [svgIco(ico)]);
  const body = el('div', { className: 's-panel-body' });
  const box = el('aside', { className: 's-panel', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': id }, [
    el('div', { className: 's-panel-head' }, [closeBtn('sm-down', 'arrow-left', LABELS.back), titleEl, extra,
      closeBtn('sm-up s-panel-x', 'x', LABELS.close)]),
    body,
  ]);
  box.hidden = true;
  document.body.appendChild(box);
  let release = null;
  const layer = { modal: false, onEscape: () => api_.close() };
  function outside(e) {
    if (box.hidden || box.contains(e.target) || LAYERS[LAYERS.length - 1] !== layer) return;
    if (o.keepOpenOn && e.target.closest(o.keepOpenOn)) return;
    api_.close();
  }
  const api_ = {
    el: box, head: extra, titleEl, body,
    isOpen: () => !box.hidden,
    open() {
      if (!box.hidden) return;
      box.hidden = false;
      pushLayer(layer);
      document.body.classList.add('has-panel');
      release = trapFocus(box, box.querySelector(window.matchMedia('(min-width: 600px)').matches
        ? '.s-panel-x' : '.s-panel-head .sm-down'));
      document.addEventListener('mousedown', outside);
    },
    close() {
      if (box.hidden) return;
      box.hidden = true;
      popLayer(layer);
      document.body.classList.remove('has-panel');
      document.removeEventListener('mousedown', outside);
      const r = release; release = null;
      if (r) r();
      if (o.onClose) o.onClose();
    },
  };
  return api_;
}

// ── menu (Figma Menu: popover 1440 / sheet 390) ──────────────────────
//   menu(anchor, [{ label, sub, icon, trailing, danger, href, onSelect, disabled }])
//   Arrow keys move, Esc closes and returns focus to the anchor.
function menu(anchor, items) {
  const wide = window.matchMedia('(min-width: 600px)').matches;
  const list = el('div', { className: 's-menu' + (wide ? '' : ' is-sheet'), role: 'menu' },
    items.map(it => {
      const kids = [it.icon ? svgIco(it.icon) : null,
        el('span', { className: 's-menu-text' }, [el('span', { className: 's-menu-label' }, [it.label]),
          it.sub ? el('span', { className: 's-menu-sub' }, [it.sub]) : null]),
        it.trailing ? el('span', { className: 's-menu-trail' }, [it.trailing]) : null];
      const cls = 's-menu-item' + (it.danger ? ' is-danger' : '');
      return it.href
        ? el('a', { className: cls, role: 'menuitem', href: it.href }, kids)
        : el('button', { type: 'button', className: 'btn ' + cls, role: 'menuitem', disabled: !!it.disabled,
            onClick: () => { closeMenu(); if (it.onSelect) it.onSelect(); } }, kids);
    }));
  const scrim = el('div', { className: 's-menu-scrim' + (wide ? ' is-clear' : '') }, [list]);
  const layer = { modal: !wide, onEscape: () => closeMenu() };
  function closeMenu() {
    if (!scrim.isConnected) return;
    scrim.remove(); popLayer(layer);
    if (anchor && anchor.setAttribute) anchor.setAttribute('aria-expanded', 'false');
    if (anchor && anchor.focus) anchor.focus();
  }
  scrim.addEventListener('mousedown', e => { if (e.target === scrim) closeMenu(); });
  list.addEventListener('keydown', e => {
    const its = Array.from(list.querySelectorAll('.s-menu-item:not([disabled])'));
    const i = its.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); its[(i + 1) % its.length].focus(); }
    if (e.key === 'ArrowUp') { e.preventDefault(); its[(i - 1 + its.length) % its.length].focus(); }
    if (e.key === 'Tab') closeMenu();
  });
  pushLayer(layer);
  document.body.appendChild(scrim);
  if (wide && anchor) {
    const r = anchor.getBoundingClientRect();
    list.style.top = (r.bottom + 4) + 'px';
    list.style.left = Math.max(8, Math.min(r.left, document.documentElement.clientWidth - list.offsetWidth - 8)) + 'px';
  }
  if (anchor && anchor.setAttribute) anchor.setAttribute('aria-expanded', 'true');
  const first = list.querySelector('.s-menu-item:not([disabled])');
  if (first) first.focus();
  return { close: closeMenu, el: list };
}

// ── chrome: top bar + bottom tab (Figma TopBar/Desktop · TopBar/Mobile · Bottom tab)
// The last account a person opened on this device, id only (never amounts —
// 인계 §3.8), so the 계좌 tab has somewhere to go.
const LAST_KEY = 'pdash.lastAccount';
function rememberAccount(id) { try { if (id) localStorage.setItem(LAST_KEY, id); else localStorage.removeItem(LAST_KEY); } catch (e) { /* private mode */ } }
function lastAccountHref() {
  try { const id = localStorage.getItem(LAST_KEY); return id ? '/a/' + encodeURIComponent(id) : '/'; }
  catch (e) { return '/'; }
}
// topBar({
//   active: 'news' | 'account' | null,      which top-level place this page is
//   account: { id, name } | null,           the account context (계좌·종목·거래 이력)
//   title: '소스 현황',                       390 left title instead of the wordmark
//   back: '/a/x' | () => {},                390 back arrow before the title
//   onAccounts(button),                      계좌▾ (default: the account list at '/')
//   onChat(), onMenu(button),                채팅 · ⋯ — omit both while locked
//   actions: [Node],                         390 right-side extras (e.g. 새로고침)
//   wideActions: [Node],                     1440 right-side extras
// }) → <header class="topbar">
function topBar(o) {
  o = o || {};
  const acc = o.account;
  const accountsBtn = (cls, label) => el('button', { type: 'button', className: 'btn ' + cls,
    'aria-haspopup': 'menu', 'aria-expanded': 'false',
    'aria-current': o.active === 'account' ? 'page' : null,
    onClick: e => (o.onAccounts ? o.onAccounts(e.currentTarget) : location.assign('/')) },
    label);
  // 390 left
  const left = [];
  if (o.back) {
    left.push(typeof o.back === 'string'
      ? el('a', { className: 'btn tb-icon', href: o.back, 'aria-label': LABELS.back }, [svgIco('arrow-left')])
      : el('button', { type: 'button', className: 'btn tb-icon', 'aria-label': LABELS.back, onClick: o.back },
        [svgIco('arrow-left')]));
  }
  if (o.title) left.push(el('span', { className: 'tb-title' }, [o.title]));
  else if (acc) left.push(accountsBtn('tb-title tb-switch', [el('span', { className: 'ellipsis' }, [acc.name || acc.id]), svgIco('chevron-down')]));
  else left.push(el('a', { className: 'tb-wordmark', href: '/' }, ['portfolio']));
  const right = [];
  (o.wideActions || []).forEach(n => { n.classList.add('sm-up'); right.push(n); });
  (o.actions || []).forEach(n => { n.classList.add('sm-down'); right.push(n); });
  if (o.onChat) {
    right.push(el('button', { type: 'button', className: 'btn tb-act', 'aria-label': LABELS.chat, onClick: o.onChat },
      [svgIco('message-circle'), el('span', { className: 'sm-up' }, [LABELS.chat])]));
  }
  if (o.onMenu) {
    right.push(el('button', { type: 'button', className: 'btn tb-icon', 'aria-label': LABELS.accountMenu,
      'aria-haspopup': 'menu', 'aria-expanded': 'false', onClick: e => o.onMenu(e.currentTarget) },
      [svgIco('ellipsis')]));
  }
  return el('header', { className: 'topbar' }, [
    el('div', { className: 'tb-compact sm-down' }, left),
    el('a', { className: 'tb-wordmark sm-up', href: '/' }, ['portfolio']),
    el('nav', { className: 'tb-nav sm-up', 'aria-label': '주 메뉴' }, [
      el('a', { className: 'tb-nav-item', href: '/', 'aria-current': o.active === 'news' ? 'page' : null },
        [svgIco('newspaper'), LABELS.news]),
      accountsBtn('tb-nav-item', [svgIco('chart-line'),
        el('span', { className: 'ellipsis' }, [acc ? (acc.name || acc.id) : LABELS.account]), svgIco('chevron-down')]),
    ]),
    el('div', { className: 'tb-spacer' }),
    el('div', { className: 'tb-right' }, right),
  ]);
}
// bottomTab(active: 'news' | 'account' | null, { onAccounts, accountHref })
// 390 only (hidden from 600px). Pages without it (종목·거래 이력) skip it.
function bottomTab(active, o) {
  o = o || {};
  const item = (key, href, icon, label) => el('a', { className: 'bt-item', href,
    'aria-current': active === key ? 'page' : null }, [svgIco(icon), el('span', {}, [label])]);
  const acc = item('account', o.accountHref || lastAccountHref(), 'chart-line', LABELS.account);
  if (o.onAccounts) acc.addEventListener('click', e => { e.preventDefault(); o.onAccounts(acc); });
  return el('nav', { className: 'bottomtab', 'aria-label': '아래 메뉴' }, [
    item('news', '/', 'newspaper', LABELS.news), acc]);
}
// chrome({ topBar: {…}, bottomTab: 'news' | 'account' | null | false, bottomTabOpts })
// Puts the top bar right after the skip link and the bottom tab at the end of
// <body>; returns both so a page can swap them (e.g. after unlocking).
function chrome(o) {
  o = o || {};
  const bar = topBar(o.topBar || {});
  const skip = document.querySelector('.skip-link');
  document.body.insertBefore(bar, skip ? skip.nextSibling : document.body.firstChild);
  let tab = null;
  if (o.bottomTab !== false) {
    tab = bottomTab(o.bottomTab || null, o.bottomTabOpts);
    document.body.appendChild(tab);
    document.body.classList.add('has-bottomtab');
  }
  return { topBar: bar, bottomTab: tab };
}

// ── icon sprite (Lucide 1.55.0, ISC, lucide.dev) ─────────────────────
// Every icon the design file uses (Figma 디자인 page, Icon/*), the 23 field
// icons included. Stroke attributes come from .lci in shared.css (inherited
// through <use>). Inserted at the top of <body> once it exists; <use href>
// resolves live, so anything built before that moment picks its symbol up.
const SPRITE =
'<svg xmlns="http://www.w3.org/2000/svg" id="lucide-sprite" aria-hidden="true">' +
  '<symbol id="i-activity" viewBox="0 0 24 24"><path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2"/></symbol>' +
  '<symbol id="i-arrow-left-right" viewBox="0 0 24 24"><path d="M8 3 4 7l4 4"/><path d="M4 7h16"/><path d="m16 21 4-4-4-4"/><path d="M20 17H4"/></symbol>' +
  '<symbol id="i-arrow-left" viewBox="0 0 24 24"><path d="m12 19-7-7 7-7"/><path d="M19 12H5"/></symbol>' +
  '<symbol id="i-arrow-up-down" viewBox="0 0 24 24"><path d="m21 16-4 4-4-4"/><path d="M17 20V4"/><path d="m3 8 4-4 4 4"/><path d="M7 4v16"/></symbol>' +
  '<symbol id="i-arrow-up-to-line" viewBox="0 0 24 24"><path d="M5 3h14"/><path d="m18 13-6-6-6 6"/><path d="M12 7v14"/></symbol>' +
  '<symbol id="i-atom" viewBox="0 0 24 24"><circle cx="12" cy="12" r="1"/><path d="M20.2 20.2c2.04-2.03.02-7.36-4.5-11.9-4.54-4.52-9.87-6.54-11.9-4.5-2.04 2.03-.02 7.36 4.5 11.9 4.54 4.52 9.87 6.54 11.9 4.5Z"/><path d="M15.7 15.7c4.52-4.54 6.54-9.87 4.5-11.9-2.03-2.04-7.36-.02-11.9 4.5-4.52 4.54-6.54 9.87-4.5 11.9 2.03 2.04 7.36.02 11.9-4.5Z"/></symbol>' +
  '<symbol id="i-ban" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M4.929 4.929 19.07 19.071"/></symbol>' +
  '<symbol id="i-battery-charging" viewBox="0 0 24 24"><path d="m11 7-3 5h4l-3 5"/><path d="M14.856 6H16a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.935"/><path d="M22 14v-4"/><path d="M5.14 18H4a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h2.936"/></symbol>' +
  '<symbol id="i-bot" viewBox="0 0 24 24"><path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/></symbol>' +
  '<symbol id="i-brain-circuit" viewBox="0 0 24 24"><path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path d="M9 13a4.5 4.5 0 0 0 3-4"/><path d="M6.003 5.125A3 3 0 0 0 6.401 6.5"/><path d="M3.477 10.896a4 4 0 0 1 .585-.396"/><path d="M6 18a4 4 0 0 1-1.967-.516"/><path d="M12 13h4"/><path d="M12 18h6a2 2 0 0 1 2 2v1"/><path d="M12 8h8"/><path d="M16 8V5a2 2 0 0 1 2-2"/><circle cx="16" cy="13" r=".5"/><circle cx="18" cy="3" r=".5"/><circle cx="20" cy="21" r=".5"/><circle cx="20" cy="8" r=".5"/></symbol>' +
  '<symbol id="i-briefcase" viewBox="0 0 24 24"><path d="M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/><rect width="20" height="14" x="2" y="6" rx="2"/></symbol>' +
  '<symbol id="i-car" viewBox="0 0 24 24"><path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1.1.4-1.4.9l-1.4 2.9A3.7 3.7 0 0 0 2 12v4c0 .6.4 1 1 1h2"/><circle cx="7" cy="17" r="2"/><path d="M9 17h6"/><circle cx="17" cy="17" r="2"/></symbol>' +
  '<symbol id="i-chart-line" viewBox="0 0 24 24"><path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="m19 9-5 5-4-4-3 3"/></symbol>' +
  '<symbol id="i-chevron-down" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></symbol>' +
  '<symbol id="i-chevron-right" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></symbol>' +
  '<symbol id="i-circle-check" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="m16 9-5.5 5.5L8 12"/></symbol>' +
  '<symbol id="i-circle-dashed" viewBox="0 0 24 24"><path d="M10.1 2.182a10 10 0 0 1 3.8 0"/><path d="M13.9 21.818a10 10 0 0 1-3.8 0"/><path d="M17.609 3.721a10 10 0 0 1 2.69 2.7"/><path d="M2.182 13.9a10 10 0 0 1 0-3.8"/><path d="M20.279 17.609a10 10 0 0 1-2.7 2.69"/><path d="M21.818 10.1a10 10 0 0 1 0 3.8"/><path d="M3.721 6.391a10 10 0 0 1 2.7-2.69"/><path d="M6.391 20.279a10 10 0 0 1-2.69-2.7"/></symbol>' +
  '<symbol id="i-circle-x" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/></symbol>' +
  '<symbol id="i-cpu" viewBox="0 0 24 24"><path d="M12 20v2"/><path d="M12 2v2"/><path d="M17 20v2"/><path d="M17 2v2"/><path d="M2 12h2"/><path d="M2 17h2"/><path d="M2 7h2"/><path d="M20 12h2"/><path d="M20 17h2"/><path d="M20 7h2"/><path d="M7 20v2"/><path d="M7 2v2"/><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="8" y="8" width="8" height="8" rx="1"/></symbol>' +
  '<symbol id="i-download" viewBox="0 0 24 24"><path d="M12 15V3"/><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/></symbol>' +
  '<symbol id="i-ellipsis" viewBox="0 0 24 24"><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/></symbol>' +
  '<symbol id="i-external-link" viewBox="0 0 24 24"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></symbol>' +
  '<symbol id="i-factory" viewBox="0 0 24 24"><path d="M12 16h.01"/><path d="M16 16h.01"/><path d="M3 19a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8.5a.5.5 0 0 0-.769-.422l-4.462 2.844A.5.5 0 0 1 15 10.5v-2a.5.5 0 0 0-.769-.422L9.77 10.922A.5.5 0 0 1 9 10.5V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2z"/><path d="M8 16h.01"/></symbol>' +
  '<symbol id="i-flame" viewBox="0 0 24 24"><path d="M12 3q1 4 4 6.5t3 5.5a1 1 0 0 1-14 0 5 5 0 0 1 1-3 1 1 0 0 0 5 0c0-2-1.5-3-1.5-5q0-2 2.5-4"/></symbol>' +
  '<symbol id="i-gamepad-2" viewBox="0 0 24 24"><line x1="6" x2="10" y1="11" y2="11"/><line x1="8" x2="8" y1="9" y2="13"/><line x1="15" x2="15.01" y1="12" y2="12"/><line x1="18" x2="18.01" y1="10" y2="10"/><path d="M17.32 5H6.68a4 4 0 0 0-3.978 3.59c-.006.052-.01.101-.017.152C2.604 9.416 2 14.456 2 16a3 3 0 0 0 3 3c1 0 1.5-.5 2-1l1.414-1.414A2 2 0 0 1 9.828 16h4.344a2 2 0 0 1 1.414.586L17 18c.5.5 1 1 2 1a3 3 0 0 0 3-3c0-1.545-.604-6.584-.685-7.258-.007-.05-.011-.1-.017-.151A4 4 0 0 0 17.32 5z"/></symbol>' +
  '<symbol id="i-gem" viewBox="0 0 24 24"><path d="M10.5 3 8 9l4 13 4-13-2.5-6"/><path d="M17 3a2 2 0 0 1 1.6.8l3 4a2 2 0 0 1 .013 2.382l-7.99 10.986a2 2 0 0 1-3.247 0l-7.99-10.986A2 2 0 0 1 2.4 7.8l2.998-3.997A2 2 0 0 1 7 3z"/><path d="M2 9h20"/></symbol>' +
  '<symbol id="i-globe" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/></symbol>' +
  '<symbol id="i-heart-pulse" viewBox="0 0 24 24"><path d="M2 9.5a5.5 5.5 0 0 1 9.591-3.676.56.56 0 0 0 .818 0A5.49 5.49 0 0 1 22 9.5c0 2.29-1.5 4-3 5.5l-5.492 5.313a2 2 0 0 1-3 .019L5 15c-1.5-1.5-3-3.2-3-5.5"/><path d="M3.22 13H9.5l.5-1 2 4.5 2-7 1.5 3.5h5.27"/></symbol>' +
  '<symbol id="i-info" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></symbol>' +
  '<symbol id="i-landmark" viewBox="0 0 24 24"><path d="M10 18v-7"/><path d="M11.119 2.205a2 2 0 0 1 1.762 0l7.84 3.846A.5.5 0 0 1 20.5 7h-17a.5.5 0 0 1-.22-.949z"/><path d="M14 18v-7"/><path d="M18 18v-7"/><path d="M3 22h18"/><path d="M6 18v-7"/></symbol>' +
  '<symbol id="i-loader-circle" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></symbol>' +
  '<symbol id="i-lock" viewBox="0 0 24 24"><rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></symbol>' +
  '<symbol id="i-message-circle" viewBox="0 0 24 24"><path d="M2.992 16.342a2 2 0 0 1 .094 1.167l-1.065 3.29a1 1 0 0 0 1.236 1.168l3.413-.998a2 2 0 0 1 1.099.092 10 10 0 1 0-4.777-4.719"/></symbol>' +
  '<symbol id="i-moon" viewBox="0 0 24 24"><path d="M20.985 12.486a9 9 0 1 1-9.473-9.472c.405-.022.617.46.402.803a6 6 0 0 0 8.268 8.268c.344-.215.825-.004.803.401"/></symbol>' +
  '<symbol id="i-newspaper" viewBox="0 0 24 24"><path d="M15 18h-5"/><path d="M18 14h-8"/><path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-4 0v-9a2 2 0 0 1 2-2h2"/><rect width="8" height="4" x="10" y="6" rx="1"/></symbol>' +
  '<symbol id="i-pencil" viewBox="0 0 24 24"><path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/></symbol>' +
  '<symbol id="i-pill" viewBox="0 0 24 24"><path d="m10.5 20.5 10-10a4.95 4.95 0 1 0-7-7l-10 10a4.95 4.95 0 1 0 7 7Z"/><path d="m8.5 8.5 7 7"/></symbol>' +
  '<symbol id="i-plus" viewBox="0 0 24 24"><path d="M5 12h14"/><path d="M12 5v14"/></symbol>' +
  '<symbol id="i-printer" viewBox="0 0 24 24"><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><path d="M6 9V3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v6"/><rect x="6" y="14" width="12" height="8" rx="1"/></symbol>' +
  '<symbol id="i-refresh-cw" viewBox="0 0 24 24"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></symbol>' +
  '<symbol id="i-rocket" viewBox="0 0 24 24"><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/><path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09"/><path d="M9 12a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.4 22.4 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 .05 5 .05"/></symbol>' +
  '<symbol id="i-search" viewBox="0 0 24 24"><path d="m21 21-4.34-4.34"/><circle cx="11" cy="11" r="8"/></symbol>' +
  '<symbol id="i-server" viewBox="0 0 24 24"><rect width="20" height="8" x="2" y="2" rx="2" ry="2"/><rect width="20" height="8" x="2" y="14" rx="2" ry="2"/><line x1="6" x2="6.01" y1="6" y2="6"/><line x1="6" x2="6.01" y1="18" y2="18"/></symbol>' +
  '<symbol id="i-shield" viewBox="0 0 24 24"><path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/></symbol>' +
  '<symbol id="i-ship" viewBox="0 0 24 24"><path d="M12 2v2"/><path d="M12 9.189V13"/><path d="M19 12V6a2 2 0 00-2-2H7a2 2 0 00-2 2v6"/><path d="M19.38 19A11.6 11.6 0 0021 13l-8.188-3.639a2 2 0 00-1.624 0L3 13.001a11.6 11.6 0 002.81 7.76"/><path d="M2 20c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1s1.2 1 2.5 1c2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/></symbol>' +
  '<symbol id="i-shopping-cart" viewBox="0 0 24 24"><path d="m2.05 2.05 1.099-.028a1 1 0 0 1 1.008.815l2.69 14.347A1 1 0 0 0 7.83 18H18"/><path d="M4.563 5h16.435a1 1 0 0 1 .981 1.204l-1.026 6.226A2 2 0 0 1 18.962 14H6.25"/><circle cx="18" cy="20" r="2"/><circle cx="8" cy="20" r="2"/></symbol>' +
  '<symbol id="i-star" viewBox="0 0 24 24"><path d="M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z"/></symbol>' +
  '<symbol id="i-trending-down" viewBox="0 0 24 24"><path d="M16 17h6v-6"/><path d="m22 17-8.5-8.5-5 5L2 7"/></symbol>' +
  '<symbol id="i-triangle-alert" viewBox="0 0 24 24"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/></symbol>' +
  '<symbol id="i-wallet" viewBox="0 0 24 24"><path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1"/><path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4"/></symbol>' +
  '<symbol id="i-wrench" viewBox="0 0 24 24"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.106-3.105c.32-.322.863-.22.983.218a6 6 0 0 1-8.259 7.057l-7.91 7.91a1 1 0 0 1-2.999-3l7.91-7.91a6 6 0 0 1 7.057-8.259c.438.12.54.662.219.984z"/></symbol>' +
  '<symbol id="i-x" viewBox="0 0 24 24"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></symbol>' +
  '<symbol id="i-zap" viewBox="0 0 24 24"><path d="M15.914 4a1.5 1.5 0 00-2.474-1.561l-9 9A1.5 1.5 0 005.5 14h4.002a.5.5 0 01.471.666L8.086 20a1.5 1.5 0 002.475 1.56l9-9A1.5 1.5 0 0018.5 10h-3.997a.5.5 0 01-.472-.667z"/></symbol>' +
  '</svg>';
function insertSprite() {
  if (document.getElementById('lucide-sprite')) return;
  document.body.insertAdjacentHTML('afterbegin', SPRITE);
  // CSSOM, not a style attribute: a nonce style-src blocks the latter.
  document.getElementById('lucide-sprite').style.display = 'none';
}
if (document.body) insertSprite();
else document.addEventListener('DOMContentLoaded', insertSprite);

window.S = {
  // DOM · tokens · words
  el, clear, svgIco, spinner, designColor, designRgba, designToken, cssVar, newsThemeIcon, srcName, LABELS,
  // numbers · dates · rules (인계 §3.1–3.5)
  fmtPrice, fmtMoney, fmtPnl, fmtPnlPct, fmtChange, fmtChangePct, fmtQty, fmtCount, fmtAxisMan, trend,
  fmtDate, fmtDateTime, fmtTime, marketToday, marketPhase, tradingDaysBetween,
  pnlLabel, quoteState, analysisState, depthLabel, verdictTone, LABEL_KO, moodBand,
  // feedback · requests
  toast, api, banner, stateBlock, skeleton,
  // controls · overlays
  segmented, dialog, confirmModal, pinInput, pinPrompt, pinGate, panel, menu, trapFocus, whenVisible,
  // chrome
  topBar, bottomTab, chrome, rememberAccount, lastAccountHref, accountSub,
};
})();
