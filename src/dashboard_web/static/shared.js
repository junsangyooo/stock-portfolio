// Shared floor for the three screens — injected by server.py right after the
// theme tokens and before the page's own <script>. Everything is exported on
// ONE namespace, window.S, so a page global can never shadow it (quant.html's
// api() prefixes /api/quant; a global api here would send the news feed to
// /api/quant/api/analysis/news/feed). Nothing here knows an account or a
// symbol — both arrive as arguments (ops notes §4, the screen edition).
// Colours come only from window.__THEME_TOKENS (theme.json); no literals.
(function () {
'use strict';
const T = window.__THEME_TOKENS || { colors: {}, tokens: {}, themes: {} };

// ── DOM ──────────────────────────────────────────────────────────────
// Canonical regime (account.html's + quant.html's boolean branch):
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
      if (c == null) continue;
      e.appendChild(typeof c === 'string' || typeof c === 'number'
        ? document.createTextNode(String(c)) : c);
    }
  }
  return e;
}
function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
// Lucide sprite instance — <svg class="lci"><use href="#i-name"/></svg>.
// Every name a fragment references exists in SPRITE below (tests check).
function svgIco(name) {
  const NS = 'http://www.w3.org/2000/svg';
  const s = document.createElementNS(NS, 'svg');
  s.setAttribute('class', 'lci');
  const u = document.createElementNS(NS, 'use');
  u.setAttribute('href', '#i-' + name);
  s.appendChild(u);
  return s;
}

// ── design tokens ────────────────────────────────────────────────────
// designColor returns the hex itself, not 'var(--x)': canvas cannot render
// a CSS variable. The tokens are the same ones the <style id="theme-vars">
// carries, injected once per page load.
function designColor(token) { return T.colors[token] || T.colors.fg || ''; }
// Non-colour token (space-*, text-*, bp-*, …) as its raw string, e.g.
// designToken('bp-sm') → '600px' for matchMedia.
function designToken(name) { return T.tokens[name] || ''; }
function hexRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return ((n >> 16) & 255) + ', ' + ((n >> 8) & 255) + ', ' + (n & 255);
}
function designRgba(token, alpha) {
  const h = designColor(token);
  return h ? 'rgba(' + hexRgb(h) + ', ' + alpha + ')' : '';
}
// News theme id → token. Unknown ids fall back loudly-but-safely to the
// neutral colour and the generic icon; test_every_news_theme_has_a_token
// keeps themes.json and theme.json from drifting apart silently.
function newsThemeColor(id) {
  const t = T.themes[id];
  return (t && t.color) || designColor('muted');
}
function newsThemeIcon(id) {
  const t = T.themes[id];
  return (t && t.icon) || 'newspaper';
}

// ── label dictionary: one Korean verb per shared action (B9). Pages take
//    the word from here instead of picking a synonym (삭제, never 제거).
const LABELS = {
  delete: '삭제', cancel: '취소', confirm: '확인', save: '저장', close: '닫기',
  reanalyze: '다시 분석', pause: '정지', resume: '재개',
  showAll: '전체 보기', collapse: '접기',
};

// ── time ─────────────────────────────────────────────────────────────
// ISO string or epoch seconds. Bad input → '', the future clamps to 0.
function timeAgo(v) {
  if (!v) return '';
  const t = typeof v === 'number' ? v * 1000 : new Date(v).getTime();
  if (isNaN(t)) return '';
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 3600) return Math.floor(s / 60) + '분 전';
  if (s < 86400) return Math.floor(s / 3600) + '시간 전';
  return Math.floor(s / 86400) + '일 전';
}
// article publish time preferred; fetch time is the same for every card in
// a daily batch so it says nothing about recency
function itemTime(x) { return timeAgo(x.published_iso || x.fetched_at); }
// en-GB is the one locale that is always plain 'HH:MM:SS' — ko-KR gives
// '오후 12:12:53' or '12시 12분 53초' depending on the engine.
function kstClock() {
  return new Date().toLocaleTimeString('en-GB', { timeZone: 'Asia/Seoul' });
}

// ── numbers ──────────────────────────────────────────────────────────
const isUS = sid => (sid || '').startsWith('US:');
function money(v, sid) {
  if (v == null || isNaN(v)) return '—';
  return isUS(sid) ? '$' + Number(v).toLocaleString('en-US',
           { minimumFractionDigits: 2, maximumFractionDigits: 2 })
                   : Number(v).toLocaleString('ko-KR',
           { maximumFractionDigits: 0 }) + '원';
}
function ccyMoney(v, ccy) {
  if (v == null || isNaN(v)) return '—';
  return ccy === 'USD' ? '$' + Number(v).toLocaleString('en-US',
           { maximumFractionDigits: 2 })
                       : Number(v).toLocaleString('ko-KR',
           { maximumFractionDigits: 0 }) + '원';
}
const pct = v => (v == null || !isFinite(v)) ? '—'   // prev_close 0 → Infinity, not '+Infinity%'
  : (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%';
const signCls = v => v == null ? '' : (v >= 0 ? 'pos' : 'neg');

// ── feedback ─────────────────────────────────────────────────────────
// The one error channel: a failure must never look like an empty screen.
// A message already on screen is not repeated — 25 cards losing the network
// at once is one failure, not 25.
function toast(msg, kind) {
  let host = document.getElementById('toast-host');
  if (!host) {
    host = el('div', { id: 'toast-host', role: 'status', 'aria-live': 'polite' });
    document.body.appendChild(host);
  }
  if ([...host.children].some(c => c.textContent === msg)) return;
  const t = el('div', { className: 'toast' + (kind ? ' ' + kind : '') }, [msg]);
  host.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}
// fetch wrapper for absolute /api paths. Non-2xx → toast + throw, so a
// caller that forgets its catch still shows the failure; a network failure
// (fetch itself throws) is toasted too, or it would render as "no data".
// A 403 opens the page's PIN gate (S.onForbidden) only when the server says
// the session is missing (detail === 'PIN required', or no detail at all).
// Any other 403 is a real answer — wrong step-up PIN, "PIN을 먼저 설정하세요" —
// and is thrown with its detail so the caller shows it inline.
async function api(path, opts) {
  let r;
  try { r = await fetch(path, opts); }
  catch (e) { toast('네트워크 오류', 'err'); throw e; }   // no path: 25 cards, one toast (dedupe by text)
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = body.detail;
    if (r.status === 403 && (!detail || detail === 'PIN required')
        && typeof window.S.onForbidden === 'function') {
      window.S.onForbidden();
      throw new Error('PIN required');
    }
    const msg = (r.status === 403 && !detail) ? '로그인이 필요합니다'
      : (detail || ('HTTP ' + r.status));
    toast(msg, 'err');
    throw new Error(msg);
  }
  return body;
}

// ── confirm dialog ───────────────────────────────────────────────────
// Replaces native confirm(): resolves true/false. Esc cancels, initial focus
// on 취소 (the safe button), focus returns to whatever opened it.
function confirmModal(o) {
  o = o || {};
  return new Promise(resolve => {
    const trigger = document.activeElement;
    const cancel = el('button', { className: 's-btn', type: 'button' }, [o.cancelLabel || LABELS.cancel]);
    const ok = el('button', { className: 's-btn ' + (o.danger ? 'danger' : 'primary'),
                              type: 'button' }, [o.confirmLabel || LABELS.confirm]);
    const box = el('div', { className: 's-dialog', role: 'dialog', 'aria-modal': 'true',
                            'aria-labelledby': 's-dialog-title' }, [
      el('h3', { id: 's-dialog-title' }, [o.title || LABELS.confirm]),
      o.body ? el('div', { className: 's-dialog-body' }, [o.body]) : null,
      el('div', { className: 's-dialog-actions' }, [cancel, ok]),
    ]);
    const ovl = el('div', { className: 's-ovl' }, [box]);
    function done(v) {
      ovl.remove();
      document.removeEventListener('keydown', onKey, true);
      if (trigger && typeof trigger.focus === 'function') trigger.focus();
      resolve(v);
    }
    function onKey(e) { if (e.key === 'Escape') { e.stopPropagation(); done(false); } }
    document.addEventListener('keydown', onKey, true);
    cancel.addEventListener('click', () => done(false));
    ok.addEventListener('click', () => done(true));
    ovl.addEventListener('mousedown', e => { if (e.target === ovl) done(false); });
    document.body.appendChild(ovl);
    cancel.focus();
  });
}

// ── news card ────────────────────────────────────────────────────────
// source_id → display name (feed ids are internal slugs)
const SRC_NAME = {
  electrek:'Electrek', electrek_ev:'Electrek',
  thelec_battery:'디일렉', thelec_semi:'디일렉', etnews_it:'전자신문',
  aitimes:'AI타임스', geeknews:'GeekNews', docdocdoc:'청년의사',
  statnews:'STAT News', biorxiv:'bioRxiv', semianalysis:'SemiAnalysis',
  robotreport:'Robot Report', irobotnews:'로봇신문',
  spacenews:'SpaceNews', payload:'Payload', finextra:'Finextra',
  hk_finance:'한국경제', mk_stock:'매일경제', einfomax:'연합인포맥스',
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
  cleantechnica:'CleanTechnica', theelec_en:'TheElec', eetimes:'EE Times',
  '3dprint_com':'3DPrint.com', voxelmatters:'VoxelMatters',
  nasa:'NASA', nsf:'NASASpaceflight', bea:'BEA',
  recyclingtoday:'Recycling Today', mk_industry:'매일경제',
  arxiv_ai:'arXiv cs.AI', arxiv_robotics:'arXiv cs.RO',
  arxiv_hardware:'arXiv cs.AR', arxiv_qbio:'arXiv q-bio',
  arxiv_battery:'arXiv mtrl-sci',
};
function srcName(id) { return SRC_NAME[id] || id || ''; }
function hexA(hex, a) { return 'rgba(' + hexRgb(hex) + ',' + a + ')'; }

// One feed item → one .news-card. landing and quant must render the same
// DOM (SC-7), which is why the builder lives here and not in a page.
function newsCard(n) {
  const c = newsThemeColor(n.theme);
  // thumbnail: media/og image → source favicon → theme icon
  const ico = newsThemeIcon(n.theme);
  const fallback = el('span', { className:'placeholder-ico' },
    n.favicon
      ? [el('img', { className:'fav-img', src:n.favicon, alt:'',
          onError: function () { this.replaceWith(svgIco(ico)); } })]
      : [svgIco(ico)]);
  const badge = (n.themes || []).map(t => t.kr).join('·') || n.theme_kr || '';
  const thumbKids = [el('span', {
    className:'tag', style:`color:${c}; border-color:${c};`,
  }, [badge])];
  if ((n.importance || 0) >= 4) {
    thumbKids.push(el('span', { className:'imp' },
      ['★'.repeat(Math.min(5, n.importance))]));
  }
  if (n.image) {
    const img = el('img', { className:'thumb-img', src:n.image, loading:'lazy', alt:'',
      onError: function () { this.remove(); fallback.style.setProperty('display', ''); } });
    fallback.style.setProperty('display', 'none');
    thumbKids.push(img, fallback);
  } else {
    thumbKids.push(fallback);
  }
  return el('div', {
    className: 'news-card',
    style: `--tc:${c};` + (n.url ? ' cursor:pointer;' : ''),
    onClick: n.url ? () => window.open(n.url, '_blank', 'noopener') : null,
  }, [
    el('div', {
      // no-img thumbs carry no information — mobile drops them entirely
      className:'news-thumb' + (n.image ? '' : ' no-img'),
      style:`background:linear-gradient(135deg, ${hexA(c, 0.16)}, ${hexA(c, 0.05)});`,
    }, thumbKids),
    el('div', { className:'news-body' }, [
      el('div', { className:'news-title' }, [n.title || '']),
      // one-liner from the nightly digest (only starred items have one)
      n.gist ? el('div', { className:'news-sum' }, [n.gist]) : null,
      el('div', { className:'news-meta' }, [
        el('span', { className:'src' }, [
          n.favicon ? el('img', { className:'src-ico', src:n.favicon, alt:'',
            onError: function () { this.remove(); } }) : null,
          srcName(n.source),
        ]),
        el('span', { className:'when' }, [itemTime(n)]),
        // mobile drops the thumb overlay, so stars move into the meta row
        (n.importance || 0) >= 4
          ? el('span', { className:'imp-m' },
              ['★'.repeat(Math.min(5, n.importance))]) : null,
      ]),
    ]),
  ]);
}
// Fill `mount` (a .news-grid) with cards, or the empty-state line.
function newsGrid(items, mount) {
  clear(mount);
  if (!items.length) {
    mount.appendChild(el('div', {
      style: 'color:var(--dim); font-size:11px; grid-column:1/-1; padding:18px; text-align:center;',
    }, ['수집된 뉴스 없음 (매일 06:00 자동 수집)']));
    return;
  }
  for (const n of items) mount.appendChild(newsCard(n));
}

// ── analysis badge (Level 0) ─────────────────────────────────────────
// The engine judges the symbol only (label); "what it means for me" is the
// account screen's business. Both pages draw the same badge from one entry
// of GET /api/analysis/summary.
const LABEL_KO = {
  strong_buy: '적극 긍정', buy: '긍정', neutral: '중립',
  sell: '부정', strong_sell: '적극 부정',
};
// 'YYYY-MM-DD' (or a 'YYYYMMDD' stem) → {days, cls(ok|warn|old), text}
function freshInfo(dateStr) {
  if (!dateStr) return null;
  let s = String(dateStr).slice(0, 10);
  if (/^\d{8}$/.test(s)) s = s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6);
  const d = new Date(s + 'T00:00:00');
  if (Number.isNaN(d.getTime())) return null;
  const days = Math.max(0, Math.floor((Date.now() - d.getTime()) / 86400000));
  return {
    days,
    cls: days <= 7 ? 'ok' : (days <= 30 ? 'warn' : 'old'),
    text: days === 0 ? '오늘' : days + '일 전',
  };
}
// In-place update of a .sc-verdict-badge span. No entry → hidden (never a
// synthetic badge); facts_only → 「판단 없음」, not a neutral label.
function verdictBadge(span, entry, etaKo) {
  if (!span) return;
  if (entry && entry.running) {
    span.hidden = false;
    span.className = 'sc-verdict-badge running';
    span.textContent = '⏳ 분석중';
    span.title = '종합 분석 실행 중' + (etaKo ? ' (' + etaKo + ')' : '');
    return;
  }
  const factsOnly = !!(entry && entry.status === 'facts_only');
  const ko = factsOnly ? '판단 없음' : LABEL_KO[entry && entry.label];
  if (!ko) { span.hidden = true; return; }
  const f = freshInfo(entry.date);
  span.hidden = false;
  span.className = 'sc-verdict-badge act-' + (factsOnly ? 'neutral' : entry.label) +
    (f && f.cls === 'old' ? ' stale' : '');
  span.textContent = ko + (f ? ' · ' + f.text : '') +
    (f && f.cls === 'old' ? ' ⚠' : '');
  span.title = '종합 판단 (' + (entry.date || '?') + ')' +
    (f && f.cls === 'old' ? ' — 오래된 분석입니다. 다시 분석을 권장합니다.' : '');
}

// ── charts (canvas — colours via designColor, never var(--x)) ────────
const KST_OFFSET_MS = 9 * 3600 * 1000;
// bar unix-ts (KST) → x-axis label. mode: 'MD' | 'YM' | 'Y' | 'HM'
function fmtBarDate(ts, mode) {
  if (!ts) return '';
  const d = new Date(ts * 1000 + KST_OFFSET_MS);
  const y = d.getUTCFullYear(), m = d.getUTCMonth() + 1, day = d.getUTCDate();
  if (mode === 'Y') return String(y);
  if (mode === 'YM') return String(y).slice(2) + '.' + ('0' + m).slice(-2);
  if (mode === 'HM') return ('0' + d.getUTCHours()).slice(-2) + ':' +
                             ('0' + d.getUTCMinutes()).slice(-2);
  return m + '/' + day;
}

// drawChart(canvas, candles, style, opts)
//   opts.dates       — draw an x-axis with date labels (default false)
//   opts.dateMode    — 'MD' | 'YM' | 'Y' (label format)
//   opts.labelEvery  — label every Nth candle (auto if omitted)
// candles each carry .ts (unix seconds). Window/pan slicing is done by the
// caller; drawChart just renders whatever slice it's handed.
function drawChart(canvas, candles, style, opts) {
  opts = opts || {};
  const showDates = !!opts.dates;
  const w = canvas.clientWidth || 400;
  const h = canvas.clientHeight || 180;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  if (!candles.length) {
    ctx.fillStyle = designColor('dim');
    ctx.font = '11px JetBrains Mono';
    ctx.fillText('데이터 없음', (w / 2) - 30, h / 2);
    return;
  }

  const PAD_L = 44, PAD_R = 8, PAD_T = 6;
  const PAD_B = showDates ? 28 : 16;
  const plotW = w - PAD_L - PAD_R;
  const plotH = h - PAD_T - PAD_B;

  let lo = Infinity, hi = -Infinity;
  candles.forEach(c => {
    if (c.low < lo) lo = c.low;
    if (c.high > hi) hi = c.high;
  });
  const pad = (hi - lo) * 0.08 || hi * 0.005 || 1;
  lo -= pad; hi += pad;
  const yOf = v => PAD_T + (1 - (v - lo) / (hi - lo)) * plotH;

  // y gridlines + price labels
  ctx.strokeStyle = designRgba('dim', 0.18);
  ctx.fillStyle = designColor('dim');
  ctx.font = '9px JetBrains Mono';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const v = lo + (hi - lo) * i / 4;
    const y = yOf(v);
    ctx.beginPath();
    ctx.moveTo(PAD_L, y); ctx.lineTo(w - PAD_R, y);
    ctx.stroke();
    ctx.fillText(v >= 1000 ? v.toFixed(0) : v.toFixed(2), 4, y + 3);
  }

  const slot = plotW / candles.length;

  // x-axis date labels (only when requested — expand chart)
  if (showDates) {
    const mode = opts.dateMode || 'MD';
    const every = opts.labelEvery || Math.max(1, Math.ceil(candles.length / 8));
    ctx.fillStyle = designColor('dim');
    ctx.strokeStyle = designRgba('dim', 0.10);
    ctx.font = '9px JetBrains Mono';
    for (let i = 0; i < candles.length; i += every) {
      const x = PAD_L + (i + 0.5) * slot;
      // faint vertical guide
      ctx.beginPath(); ctx.moveTo(x, PAD_T); ctx.lineTo(x, PAD_T + plotH); ctx.stroke();
      const label = fmtBarDate(candles[i].ts, mode);
      ctx.fillText(label, x - 14, h - 8);
    }
  }

  if (style === 'line') {
    ctx.strokeStyle = designColor('cyan');
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    candles.forEach((c, i) => {
      const x = PAD_L + (i + 0.5) * slot;
      const y = yOf(c.close);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    const last = candles[candles.length - 1];
    const xL = PAD_L + (candles.length - 0.5) * slot;
    const yL = yOf(last.close);
    ctx.fillStyle = designColor('cyan');
    ctx.beginPath(); ctx.arc(xL, yL, 3, 0, Math.PI * 2); ctx.fill();
  } else {
    const bw = Math.max(1, slot * 0.6);
    candles.forEach((c, i) => {
      const x = PAD_L + (i + 0.5) * slot;
      const isUp = c.close >= c.open;
      const color = isUp ? designColor('green') : designColor('red');
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(x, yOf(c.high));
      ctx.lineTo(x, yOf(c.low));
      ctx.stroke();
      const yo = yOf(c.open), yc = yOf(c.close);
      const top = Math.min(yo, yc);
      const bh = Math.max(1, Math.abs(yc - yo));
      if (isUp) {
        ctx.fillStyle = designRgba('green', 0.5);
        ctx.fillRect(x - bw / 2, top, bw, bh);
        ctx.strokeRect(x - bw / 2, top, bw, bh);
      } else {
        ctx.fillRect(x - bw / 2, top, bw, bh);
      }
    });
  }

  // ── analysis overlay: S/R horizontal lines (visible-window) ──
  if (opts.srLevels && opts.srLevels.length) {
    ctx.font = '9px JetBrains Mono';
    ctx.setLineDash([4, 3]);
    ctx.lineWidth = 1;
    opts.srLevels.forEach(lv => {
      if (lv.price < lo || lv.price > hi) return;   // skip off-screen levels
      const y = yOf(lv.price);
      const col = lv.kind === 'r' ? designColor('red') : designColor('green');
      ctx.strokeStyle = col;
      ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(w - PAD_R, y); ctx.stroke();
      ctx.fillStyle = col;
      const px = lv.price >= 1000 ? lv.price.toFixed(0) : lv.price.toFixed(2);
      ctx.fillText((lv.kind === 'r' ? '저항 ' : '지지 ') + px + ' ·' + lv.tests, PAD_L + 4, y - 3);
    });
    ctx.setLineDash([]);
  }
}

// Asset curve — pure: knows neither a DOM id nor an API. `snapshots` are
// {date, total_assets} rows; opts.fmt formats the y labels.
function drawCurve(canvas, snapshots, opts) {
  opts = opts || {};
  const fmt = opts.fmt || (v => ccyMoney(v, opts.currency));
  const w = canvas.clientWidth || 800, h = opts.height || 320;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  if (snapshots.length < 2) return;

  const PAD_L = 70, PAD_R = 14, PAD_T = 14, PAD_B = 28;
  const plotW = w - PAD_L - PAD_R;
  const plotH = h - PAD_T - PAD_B;

  let lo = Infinity, hi = -Infinity;
  snapshots.forEach(s => {
    const v = s.total_assets;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  });
  const pad = (hi - lo) * 0.08 || hi * 0.005 || 1;
  lo -= pad; hi += pad;

  const px = i => PAD_L + (i / (snapshots.length - 1)) * plotW;
  const py = v => PAD_T + (1 - (v - lo) / (hi - lo)) * plotH;
  const first = snapshots[0], last = snapshots[snapshots.length - 1];
  const isUp = last.total_assets - first.total_assets >= 0;

  // y grid
  ctx.strokeStyle = designRgba('dim', 0.18);
  ctx.fillStyle = designColor('dim');
  ctx.font = '10px JetBrains Mono';
  for (let i = 0; i <= 5; i++) {
    const v = lo + (hi - lo) * i / 5;
    const y = py(v);
    ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(w - PAD_R, y); ctx.stroke();
    ctx.fillText(fmt(v), 4, y + 3);
  }

  // baseline (start value)
  const yBase = py(first.total_assets);
  ctx.strokeStyle = designColor('dim');
  ctx.setLineDash([3, 3]);
  ctx.beginPath(); ctx.moveTo(PAD_L, yBase); ctx.lineTo(w - PAD_R, yBase); ctx.stroke();
  ctx.setLineDash([]);

  // X axis dates (first, mid, last)
  ctx.fillStyle = designColor('dim');
  ctx.fillText(first.date, PAD_L, h - 8);
  if (snapshots.length >= 3) {
    const m = snapshots[Math.floor(snapshots.length / 2)];
    ctx.fillText(m.date, PAD_L + plotW / 2 - 30, h - 8);
  }
  ctx.fillText(last.date, w - PAD_R - 60, h - 8);

  // line + fill
  const color = isUp ? designColor('green') : designColor('red');
  ctx.fillStyle = isUp ? designRgba('green', 0.18) : designRgba('red', 0.18);
  ctx.beginPath();
  ctx.moveTo(px(0), yBase);
  snapshots.forEach((s, i) => ctx.lineTo(px(i), py(s.total_assets)));
  ctx.lineTo(px(snapshots.length - 1), yBase);
  ctx.closePath();
  ctx.fill();

  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  snapshots.forEach((s, i) => {
    const x = px(i), y = py(s.total_assets);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();

  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(px(snapshots.length - 1), py(last.total_assets), 4, 0, Math.PI * 2);
  ctx.fill();
}

// ── icon sprite (Lucide, ISC, lucide.dev) ────────────────────────────
// Inserted at the top of <body> once it exists; <use href> resolves live,
// so cards built before that moment still pick their symbol up.
const SPRITE =
'<svg xmlns="http://www.w3.org/2000/svg" id="lucide-sprite" aria-hidden="true">' +
  '<symbol id="i-lock" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></symbol>' +
  '<symbol id="i-newspaper" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18h-5"/><path d="M18 14h-8"/><path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-4 0v-9a2 2 0 0 1 2-2h2"/><rect width="8" height="4" x="10" y="6" rx="1"/></symbol>' +
  '<symbol id="i-battery-charging" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m11 7-3 5h4l-3 5"/><path d="M14.856 6H16a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.935"/><path d="M22 14v-4"/><path d="M5.14 18H4a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h2.936"/></symbol>' +
  '<symbol id="i-bot" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2"/><path d="M20 14h2"/><path d="M15 13v2"/><path d="M9 13v2"/></symbol>' +
  '<symbol id="i-dna" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m10 16 1.5 1.5"/><path d="m14 8-1.5-1.5"/><path d="M15 2c-1.798 1.998-2.518 3.995-2.807 5.993"/><path d="m16.5 10.5 1 1"/><path d="m17 6-2.891-2.891"/><path d="M2 15c6.667-6 13.333 0 20-6"/><path d="m20 9 .891.891"/><path d="M3.109 14.109 4 15"/><path d="m6.5 12.5 1 1"/><path d="m7 18 2.891 2.891"/><path d="M9 22c1.798-1.998 2.518-3.995 2.807-5.993"/></symbol>' +
  '<symbol id="i-cpu" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20v2"/><path d="M12 2v2"/><path d="M17 20v2"/><path d="M17 2v2"/><path d="M2 12h2"/><path d="M2 17h2"/><path d="M2 7h2"/><path d="M20 12h2"/><path d="M20 17h2"/><path d="M20 7h2"/><path d="M7 20v2"/><path d="M7 2v2"/><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="8" y="8" width="8" height="8" rx="1"/></symbol>' +
  '<symbol id="i-biceps-flexed" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.409 13.017A5 5 0 0 1 22 15c0 3.866-4 7-9 7-4.077 0-8.153-.82-10.371-2.462-.426-.316-.631-.832-.62-1.362C2.118 12.723 2.627 2 10 2a3 3 0 0 1 3 3 2 2 0 0 1-2 2c-1.105 0-1.64-.444-2-1"/><path d="M15 14a5 5 0 0 0-7.584 2"/><path d="M9.964 6.825C8.019 7.977 9.5 13 8 15"/></symbol>' +
  '<symbol id="i-rocket" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/><path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09"/><path d="M9 12a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.4 22.4 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 .05 5 .05"/></symbol>' +
  '<symbol id="i-landmark" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 18v-7"/><path d="M11.119 2.205a2 2 0 0 1 1.762 0l7.84 3.846A.5.5 0 0 1 20.5 7h-17a.5.5 0 0 1-.22-.949z"/><path d="M14 18v-7"/><path d="M18 18v-7"/><path d="M3 22h18"/><path d="M6 18v-7"/></symbol>' +
  '<symbol id="i-ship" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 10.189V14"/><path d="M12 2v3"/><path d="M19 13V7a2 2 0 0 0-2-2H7a2 2 0 0 0-2 2v6"/><path d="M19.38 20A11.6 11.6 0 0 0 21 14l-8.188-3.639a2 2 0 0 0-1.624 0L3 14a11.6 11.6 0 0 0 2.81 7.76"/><path d="M2 21c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1s1.2 1 2.5 1c2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/></symbol>' +
  '<symbol id="i-printer" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><path d="M6 9V3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v6"/><rect x="6" y="14" width="12" height="8" rx="1"/></symbol>' +
  '<symbol id="i-globe" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/></symbol>' +
  '<symbol id="i-gamepad-2" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" x2="10" y1="11" y2="11"/><line x1="8" x2="8" y1="9" y2="13"/><line x1="15" x2="15.01" y1="12" y2="12"/><line x1="18" x2="18.01" y1="10" y2="10"/><path d="M17.32 5H6.68a4 4 0 0 0-3.978 3.59c-.006.052-.01.101-.017.152C2.604 9.416 2 14.456 2 16a3 3 0 0 0 3 3c1 0 1.5-.5 2-1l1.414-1.414A2 2 0 0 1 9.828 16h4.344a2 2 0 0 1 1.414.586L17 18c.5.5 1 1 2 1a3 3 0 0 0 3-3c0-1.545-.604-6.584-.685-7.258-.007-.05-.011-.1-.017-.151A4 4 0 0 0 17.32 5z"/></symbol>' +
  '<symbol id="i-shopping-cart" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m2.05 2.05 1.099-.028a1 1 0 0 1 1.008.815l2.69 14.347A1 1 0 0 0 7.83 18H18"/><path d="M4.563 5h16.435a1 1 0 0 1 .981 1.204l-1.026 6.226A2 2 0 0 1 18.962 14H6.25"/><circle cx="18" cy="20" r="2"/><circle cx="8" cy="20" r="2"/></symbol>' +
  '<symbol id="i-atom" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="1"/><path d="M20.2 20.2c2.04-2.03.02-7.36-4.5-11.9-4.54-4.52-9.87-6.54-11.9-4.5-2.04 2.03-.02 7.36 4.5 11.9 4.54 4.52 9.87 6.54 11.9 4.5Z"/><path d="M15.7 15.7c4.52-4.54 6.54-9.87 4.5-11.9-2.03-2.04-7.36-.02-11.9 4.5-4.52 4.54-6.54 9.87-4.5 11.9 2.03 2.04 7.36.02 11.9-4.5Z"/></symbol>' +
  '<symbol id="i-car" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1.1.4-1.4.9l-1.4 2.9A3.7 3.7 0 0 0 2 12v4c0 .6.4 1 1 1h2"/><circle cx="7" cy="17" r="2"/><path d="M9 17h6"/><circle cx="17" cy="17" r="2"/></symbol>' +
  '<symbol id="i-pickaxe" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m14 13-8.381 8.38a1 1 0 0 1-3.001-3L11 9.999"/><path d="M15.973 4.027A13 13 0 0 0 5.902 2.373c-1.398.342-1.092 2.158.277 2.601a19.9 19.9 0 0 1 5.822 3.024"/><path d="M16.001 11.999a19.9 19.9 0 0 1 3.024 5.824c.444 1.369 2.26 1.676 2.603.278A13 13 0 0 0 20 8.069"/><path d="M18.352 3.352a1.205 1.205 0 0 0-1.704 0l-5.296 5.296a1.205 1.205 0 0 0 0 1.704l2.296 2.296a1.205 1.205 0 0 0 1.704 0l5.296-5.296a1.205 1.205 0 0 0 0-1.704z"/></symbol>' +
  '<symbol id="i-zap" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15.914 4a1.5 1.5 0 00-2.474-1.561l-9 9A1.5 1.5 0 005.5 14h4.002a.5.5 0 01.471.666L8.086 20a1.5 1.5 0 002.475 1.56l9-9A1.5 1.5 0 0018.5 10h-3.997a.5.5 0 01-.472-.667z"/></symbol>' +
  '<symbol id="i-wrench" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.106-3.105c.32-.322.863-.22.983.218a6 6 0 0 1-8.259 7.057l-7.91 7.91a1 1 0 0 1-2.999-3l7.91-7.91a6 6 0 0 1 7.057-8.259c.438.12.54.662.219.984z"/></symbol>' +
  '<symbol id="i-server" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="20" height="8" x="2" y="2" rx="2" ry="2"/><rect width="20" height="8" x="2" y="14" rx="2" ry="2"/><line x1="6" x2="6.01" y1="6" y2="6"/><line x1="6" x2="6.01" y1="18" y2="18"/></symbol>' +
  '<symbol id="i-shield" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/></symbol>' +
  '<symbol id="i-clipboard-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="8" height="4" x="8" y="2" rx="1" ry="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="m9 14 2 2 4-4"/></symbol>' +
  '<symbol id="i-scale" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v18"/><path d="m19 8 3 8a5 5 0 0 1-6 0zV7"/><path d="M3 7h1a17 17 0 0 0 8-2 17 17 0 0 0 8 2h1"/><path d="m5 8 3 8a5 5 0 0 1-6 0zV7"/><path d="M7 21h10"/></symbol>' +
  '<symbol id="i-flame" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3q1 4 4 6.5t3 5.5a1 1 0 0 1-14 0 5 5 0 0 1 1-3 1 1 0 0 0 5 0c0-2-1.5-3-1.5-5q0-2 2.5-4"/></symbol>' +
  '<symbol id="i-factory" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16h.01"/><path d="M16 16h.01"/><path d="M3 19a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8.5a.5.5 0 0 0-.769-.422l-4.462 2.844A.5.5 0 0 1 15 10.5v-2a.5.5 0 0 0-.769-.422L9.77 10.922A.5.5 0 0 1 9 10.5V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2z"/><path d="M8 16h.01"/></symbol>' +
  '</svg>';
document.addEventListener('DOMContentLoaded', () => {
  document.body.insertAdjacentHTML('afterbegin', SPRITE);
  // CSSOM, not a style attribute: a nonce style-src blocks the latter.
  document.getElementById('lucide-sprite').style.display = 'none';
});


// ── a11y / lifecycle helpers shared by every page ────────────────────
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

// Keep Tab inside `container` until the returned release() runs; restores
// focus to whatever had it before. Pages call this from their modal openers.
function trapFocus(container) {
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
  const initial = container.querySelector(FOCUSABLE);
  if (initial) initial.focus();
  return function release() {
    container.removeEventListener('keydown', onKey);
    if (before && document.contains(before)) before.focus();
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

window.S = {
  el, clear, svgIco,
  designColor, designRgba, designToken, newsThemeColor, newsThemeIcon, LABELS,
  timeAgo, itemTime, kstClock,
  money, ccyMoney, pct, signCls,
  toast, api, confirmModal, trapFocus, whenVisible,
  srcName, newsCard, newsGrid,
  LABEL_KO, freshInfo, verdictBadge,
  fmtBarDate, drawChart, drawCurve,
};
})();
