// SCR-NEWS helpers shared by news/theme-panel.js and news/news.js — pure
// functions over the news routes' shapes. Nothing here knows an account.
(function () {
'use strict';
const WEEKDAY = ['일', '월', '화', '수', '목', '금', '토'];

// 'YYYY-MM-DD' ± n calendar days (no time zone involved)
function addDays(date, n) {
  const [y, m, d] = date.split('-').map(Number);
  const t = new Date(Date.UTC(y, m - 1, d + n));
  return t.toISOString().slice(0, 10);
}
// the KST calendar day of an instant (Date | ISO)
const kstDay = S.kstDay;
function kstNow() {
  const f = new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Seoul', hour: 'numeric', minute: 'numeric', hourCycle: 'h23' })
    .formatToParts(new Date());
  const p = {}; f.forEach(x => { p[x.type] = x.value; });
  return { day: kstDay(new Date()), hh: +p.hour, mm: +p.minute };
}
// An item's time. Feed timestamps without an offset are KST wall clock
// (the Korean feeds), so they get +09:00 instead of the browser's zone.
function itemTs(x) {
  const v = x.published_iso || x.published || x.fetched_at;
  if (!v) return null;
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(v)) return v + '+09:00';
  return v;
}
// the day an item was collected on (the grid's day key)
function itemDay(x) { return x.fetched_at ? kstDay(x.fetched_at) : (itemTs(x) ? kstDay(itemTs(x)) : ''); }

// mood score → '+0.38' / '−0.41' / '—'
const fmtScore = S.fmtScore;
// text tone of a score: same boundaries as the cell (인계 §3.5)
function scoreTone(v) {
  const b = S.moodBand(v).band;
  return b === 'none' || b === 'error' ? 'none'
    : b.startsWith('positive') ? 'pos' : b.startsWith('negative') ? 'neg' : 'neutral';
}
// <span class="score s-…">+0.38<span class="ar">▲</span></span>
function scoreEl(v) {
  const m = S.moodBand(v);
  return S.el('span', { className: 'score s-' + scoreTone(v) }, [fmtScore(v),
    m.arrow ? S.el('span', { className: 'ar', 'aria-hidden': 'true' }, [m.arrow]) : null,
    m.arrow ? S.el('span', { className: 'sr-only' }, [' ' + m.label]) : null]);
}

// last_shift 'YYYY-MM-DD: deteriorating→improving' → { date, from, to }
const TREND_KO = { improving: '개선', deteriorating: '악화', flat: '유지' };
function parseShift(s) {
  const m = /^(\d{4}-\d{2}-\d{2}):\s*(\w+)\s*→\s*(\w+)/.exec(s || '');
  return m ? { date: m[1], from: TREND_KO[m[2]] || m[2], to: TREND_KO[m[3]] || m[3] } : null;
}
function shiftText(s) {
  const p = parseShift(s);
  return p ? '마지막 전환 ' + S.fmtDate(p.date) + ' · ' + p.from + ' → ' + p.to : '';
}

// five dots, the filled ones = importance (1–5)
function dots(n) {
  const k = Math.max(0, Math.min(5, Math.round(+n || 0)));
  const wrap = S.el('span', { className: 'dots', role: 'img', 'aria-label': '중요도 5점 중 ' + k });
  for (let i = 0; i < 5; i++) wrap.appendChild(S.el('i', { className: i < k ? 'on' : null }));
  return wrap;
}

// a feed source id → the name a reader knows (S.srcName), else the id
function srcLabel(id) { return (S.srcName && S.srcName(id)) || id || ''; }

// 을/를 after a name: batchim → 을, otherwise 를 (non-Hangul endings read as 를)
function objParticle(name) {
  const c = (name || '').charCodeAt((name || '').length - 1);
  if (c >= 0xAC00 && c <= 0xD7A3) return (c - 0xAC00) % 28 ? '을' : '를';
  return '를';
}

window.NEWS_UTIL = { WEEKDAY, addDays, kstDay, kstNow, itemTs, itemDay, fmtScore, scoreTone, scoreEl,
  parseShift, shiftText, TREND_KO, dots, srcLabel, objParticle };
})();
