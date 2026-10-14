// account/stock.js — SCR-STOCK 종목 보기 (/a/{id}?s=KR:005930).
// 한 화면 스크롤에 구역 아홉: 머리 · 내 위치 · 가격 차트 · 판정 · 근거 ·
// 신호판 · 재무 · 관련 소식 · 내 거래·메모. 1440 ≥1100 왼쪽 보유 레일 240,
// 390 앵커 줄 40 + 하단 바 64(매수|매도), 하단 탭 숨김(core.js).
//
// 계좌의 숫자(수량·평단·시세·시세 상태)는 ACC.data(core.js)에서, 종목의 사실
// (일봉·분석·지표·재무·소식)은 공유 경로에서 읽는다 — 화면은 계좌 사정을
// 붙이기만 한다(ops notes §4: 판정은 종목만, '내 평단 대비'는 화면이 계산).
//
// 다른 종목 담당 파일이 쓰는 공용 도우미는 ACC.stk에 둔다(trades.js ·
// trade-dialog.js · hold-fix.js가 이 파일 뒤에 실린다).
(function () {
  'use strict';
  const S = window.S;
  const ACC = window.ACC;
  if (!S || !ACC) return;
  const { el, clear, svgIco, LABELS } = S;

  // ── 공용 도우미 (ACC.stk) ──────────────────────────────────────────
  const stk = ACC.stk = ACC.stk || {};
  const subs = {};
  stk.on = (evt, fn) => { (subs[evt] = subs[evt] || []).push(fn); return () => { subs[evt] = (subs[evt] || []).filter(f => f !== fn); }; };
  stk.emit = (evt, arg) => (subs[evt] || []).slice().forEach(fn => { try { fn(arg); } catch (e) { console.error(e); } });

  stk.split = sid => { const i = String(sid || '').indexOf(':'); return i < 0 ? ['KR', String(sid || '')] : [sid.slice(0, i), sid.slice(i + 1)]; };
  stk.enc = sid => stk.split(sid).map(encodeURIComponent).join('/');
  stk.tz = region => (region === 'US' ? 'America/New_York' : 'Asia/Seoul');
  stk.ccyOf = (h, meta, sid) => (h && h.currency) || (meta && meta.currency) || (stk.split(sid)[0] === 'US' ? 'USD' : 'KRW');
  // A daily bar is stamped at the session's local midnight (KR) or UTC
  // midnight (US) — the same reading as core.js.
  stk.barDate = (ts, region) => new Date((ts + (region === 'KR' ? 9 * 3600 : 0)) * 1000).toISOString().slice(0, 10);
  // 받침 조사: '삼성전자를' · 'SK하이닉스를' · '준ISA로'
  function hasJong(word) {
    const ch = String(word || '').trim().slice(-1);
    const code = ch.charCodeAt(0);
    if (code >= 0xAC00 && code <= 0xD7A3) return (code - 0xAC00) % 28;
    if (/[0-9]/.test(ch)) return '013678'.indexOf(ch) >= 0 ? 1 : 0;
    if (/[lmnr]/i.test(ch)) return /[l]/i.test(ch) ? 8 : 1;
    return 0;
  }
  stk.josa = (word, pair) => {
    const j = hasJong(word);
    if (pair === '로') return word + (j && j !== 8 ? '으로' : '로');
    const [a, b] = pair.split('/');               // '을/를' '이/가' '은/는' '과/와'
    return word + (j ? a : b);
  };
  // epoch seconds of a wall-clock time on a calendar date in a time zone
  stk.zonedEpoch = (date, hh, mm, tz) => {
    const [y, m, d] = date.split('-').map(Number);
    const guess = Date.UTC(y, m - 1, d, hh, mm);
    const f = new Intl.DateTimeFormat('en-US', { timeZone: tz, hourCycle: 'h23', year: 'numeric',
      month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric' });
    const p = {};
    f.formatToParts(new Date(guess)).forEach(x => { p[x.type] = +x.value || x.value; });
    const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute);
    return Math.round((guess - (asUtc - guess)) / 1000);
  };
  stk.addDays = (date, n) => { const d = new Date(date + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
  stk.addMonths = (date, n) => { const d = new Date(date + 'T00:00:00Z'); d.setUTCMonth(d.getUTCMonth() + n); return d.toISOString().slice(0, 10); };
  stk.pct = (v, base) => (v == null || !base ? null : (v / base - 1) * 100);
  stk.displayName = (h, meta, sid) => (h && h.name_kr) || (meta && (meta.name_kr || meta.name_en)) || (h && h.name_en) || stk.split(sid)[1];

  // Small read cache shared by the stock view and the trade dialog.
  const cache = new Map();
  function cached(key, ttlMs, fn) {
    const hit = cache.get(key);
    if (hit && Date.now() - hit.at < ttlMs) return hit.p;
    const p = fn();
    cache.set(key, { at: Date.now(), p });
    p.catch(() => cache.delete(key));
    return p;
  }
  stk.forget = prefix => { [...cache.keys()].forEach(k => { if (k.startsWith(prefix)) cache.delete(k); }); };
  // GET wrappers — every failure is shown inline by the caller (toast: false);
  // a missing session still opens the page gate (core.js S.onForbidden).
  const get = path => S.api(path, { toast: false });
  stk.meta = sid => cached('meta:' + sid, 30 * 60e3, async () => {
    const [region, code] = stk.split(sid);
    const r = await get('/api/universe/search?q=' + encodeURIComponent(code) + '&region=' + encodeURIComponent(region) + '&limit=10');
    return (r.results || []).find(x => x.symbol_id === sid) || null;
  });
  stk.daily = sid => cached('daily:' + sid, 5 * 60e3, () =>
    get('/api/market/ohlcv/' + stk.enc(sid) + '?period=day&n=400').then(r => r.bars || []));
  // A symbol this account does not carry has no quote in ACC.data; warm one
  // like the account does on open (5 minutes).
  stk.quote = sid => cached('quote:' + sid, 5 * 60e3, () =>
    get('/api/market/quotes?symbols=' + encodeURIComponent(sid) + '&max_age_s=300')
      .then(r => ((r.quotes || {})[sid]) || null));

  // Every analysis-body path this screen reads (engine.normalize and
  // engine._facts_only both emit them — tests/web/test_ui_stock.py checks).
  const ANALYSIS_BODY_KEYS = ['date', 'status', 'label', 'score', 'confidence', 'summary_ko',
    'drivers', 'risks', 'limitations', 'outlook.scenarios', 'levels.support', 'levels.resistance',
    'levels.entry_zone', 'levels.invalidation', 'levels.targets'];
  void ANALYSIS_BODY_KEYS;

  // ── 표기 ───────────────────────────────────────────────────────────
  const fmtPrice = (v, ccy) => S.fmtPrice(v, ccy);
  const fmtPct = (p, d) => S.fmtPnlPct(p, d == null ? 1 : d);
  const trendCls = v => ({ up: 't-up', down: 't-down', flat: '' })[S.trend(v)];
  const LABEL_ORDER = ['strong_sell', 'sell', 'neutral', 'buy', 'strong_buy'];
  const LABEL_KO = { strong_buy: '적극 긍정', buy: '긍정', neutral: '중립', sell: '부정', strong_sell: '적극 부정' };
  const LABEL_ARROW = { strong_buy: '▲▲', buy: '▲', neutral: '●', sell: '▼', strong_sell: '▼▼' };
  const fmtScore = v => (v == null || isNaN(v) ? '—' : (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(+v).toFixed(2));
  const PERIODS = [
    { value: '1D', label: '1일', q: 'period=5m&n=80', meta: '1일 · 5분봉' },
    { value: '3M', label: '3M', q: 'period=day&n=63', meta: '3개월 · 일봉' },
    { value: '6M', label: '6M', q: 'period=day&n=126', meta: '6개월 · 일봉' },
    { value: '1Y', label: '1Y', q: 'period=day&n=250', meta: '1년 · 일봉' },
    { value: '5Y', label: '5Y', q: 'period=week&n=260', meta: '5년 · 주봉' },
  ];
  const OVERLAYS = [
    { key: 'avg', label: '평단' }, { key: 'stop', label: '손절' }, { key: 'target', label: '목표' },
    { key: 'entry', label: '진입' }, { key: 'level', label: '지지·저항' },
  ];
  // the chart's choices outlive one symbol (a person reading five holdings
  // keeps the same lens); never stored on the device
  const prefs = { period: '3M', kind: 'line', on: null, oldCoords: false, openWhy: false };

  // ── 신호판 낱말 (chart_rules.md의 state) ─────────────────────────────
  const CATS = [
    { key: 'trend', label: '추세' }, { key: 'momentum', label: '모멘텀' },
    { key: 'volatility', label: '변동성' }, { key: 'volume', label: '거래량' },
  ];
  const IND = {
    ema_alignment: 'EMA 배열', macd: 'MACD', adx_dmi: 'ADX', supertrend: '슈퍼트렌드', ichimoku: '일목',
    rsi: 'RSI', stochastic: '스토캐스틱', cci: 'CCI', williams_r: '윌리엄스 %R', mfi: 'MFI',
    bbands_pctb: '볼린저 %B', ttm_squeeze: 'TTM 스퀴즈', donchian: '돈치안',
    obv_trend: 'OBV', cmf: 'CMF',
  };
  const NUMF = {};
  const num = (v, d) => (v == null || isNaN(v) ? '' : (+v < 0 ? '−' : '') +
    (NUMF[d] = NUMF[d] || new Intl.NumberFormat('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })).format(Math.abs(+v)));
  // → { dir: 'up'|'down'|'flat'|'none', text }
  function signal(ind) {
    const s = ind.state, v = ind.value;
    if (!s || s === 'no_data') return { dir: 'none', text: '데이터 부족' };
    const R = (dir, text) => ({ dir, text });
    switch (ind.indicator) {
      case 'ema_alignment': return s === 'bull_stack' ? R('up', '정배열') : s === 'bear_stack' ? R('down', '역배열') : R('flat', '혼조');
      case 'macd': return ({ golden_cross: R('up', '골든크로스'), dead_cross: R('down', '데드크로스'),
        above_signal: R('up', '시그널 위'), below_signal: R('down', '시그널 아래') })[s] || R('flat', s);
      case 'adx_dmi': {
        if (s === 'strong_trend') {
          const d = ((ind.evidence || {}).di_direction);
          return R(d === 'down' ? 'down' : 'up', '강한 추세');
        }
        return R('flat', s === 'weak_trend' ? '약한 추세' : '추세 없음');
      }
      case 'supertrend': return s === 'up' ? R('up', '상승') : R('down', '하락');
      case 'ichimoku': return s === 'above_cloud' ? R('up', '구름 위') : s === 'below_cloud' ? R('down', '구름 아래') : R('flat', '구름 안');
      case 'rsi': case 'stochastic': case 'cci': case 'williams_r': case 'mfi': {
        const n = num(v, 0);
        return s === 'overbought' ? R('down', n + ' 과매수') : s === 'oversold' ? R('up', n + ' 과매도') : R('flat', n || '중립');
      }
      case 'bbands_pctb': return s === 'above_upper' ? R('up', num(v, 2) + ' 상단 위')
        : s === 'below_lower' ? R('down', num(v, 2) + ' 하단 아래') : R('flat', num(v, 2) + ' 안');
      case 'ttm_squeeze': return s === 'fired_up' ? R('up', '위로 풀림') : s === 'fired_down' ? R('down', '아래로 풀림') : R('flat', '압축 중');
      case 'donchian': return s === 'breakout_up' ? R('up', '상단 돌파') : s === 'breakout_down' ? R('down', '하단 돌파') : R('flat', '안');
      case 'obv_trend': return s === 'rising' ? R('up', '상승') : R('down', '하락');
      case 'cmf': return s === 'accumulation' ? R('up', '매집 ' + num(v, 2)) : s === 'distribution' ? R('down', '분산 ' + num(v, 2)) : R('flat', '중립');
      default: return R('flat', s);
    }
  }
  const DIR_MARK = { up: '▲', down: '▼', flat: '●', none: '–' };

  // ── SVG ────────────────────────────────────────────────────────────
  const NS = 'http://www.w3.org/2000/svg';
  function sv(tag, attrs, kids) {
    const n = document.createElementNS(NS, tag);
    for (const k in (attrs || {})) if (attrs[k] != null) n.setAttribute(k === 'cls' ? 'class' : k, attrs[k]);
    (kids || []).forEach(c => { if (c != null) n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    return n;
  }
  function niceTicks(lo, hi, want) {
    const span = hi - lo || Math.abs(hi) || 1;
    const raw = span / Math.max(1, want);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= want) || 10 * mag;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  }
  // measure text without a canvas font mismatch — the labels are short and
  // Pretendard's figures are tabular, so a per-char estimate is enough
  const textW = (t, size) => String(t).length * size * 0.56;

  // draw({ host, bars, kind, region, ccy, current, quoteNone, lastQuoteText,
  //        overlays: [{ key, cls, price | low+high, label, short, dashed, faded }], compact, period })
  function drawChart(o) {
    const host = o.host;
    clear(host);
    const W = Math.max(280, host.clientWidth || 600);
    const compact = o.compact;
    const H = compact ? 240 : 420;
    const fs = 12;                       // caption size (CSS) — only for the label-width estimate
    const bars = o.bars;
    const labelW = compact ? 96 : 200;
    const PAD = { l: compact ? 48 : 60, r: labelW, t: 14, b: 26 };
    const plotW = W - PAD.l - PAD.r, plotH = H - PAD.t - PAD.b;
    let lo = Infinity, hi = -Infinity;
    bars.forEach(b => { lo = Math.min(lo, o.kind === 'candle' ? b.low : b.close); hi = Math.max(hi, o.kind === 'candle' ? b.high : b.close); });
    if (o.current != null) { lo = Math.min(lo, o.current); hi = Math.max(hi, o.current); }
    const span0 = (hi - lo) || hi * 0.02 || 1;
    // overlays far outside the price range would squash the line flat;
    // they stay in the legend as 「차트 밖」 instead of being drawn
    const shown = [], outside = [];
    (o.overlays || []).forEach(ov => {
      const vals = ov.price != null ? [ov.price] : [ov.low, ov.high];
      if (vals.every(v => v >= lo - span0 * 1.2 && v <= hi + span0 * 1.2)) {
        shown.push(ov);
        vals.forEach(v => { lo = Math.min(lo, v); hi = Math.max(hi, v); });
      } else outside.push(ov);
    });
    const pad = (hi - lo) * 0.06 || hi * 0.01 || 1;
    lo -= pad; hi += pad;
    const y = v => PAD.t + (1 - (v - lo) / (hi - lo)) * plotH;
    const n = bars.length;
    const x = i => PAD.l + (n <= 1 ? plotW : (i / (n - 1)) * plotW);
    const svg = sv('svg', { cls: 'stk-chart-svg', width: W, height: H, viewBox: '0 0 ' + W + ' ' + H,
      role: 'img', 'aria-label': o.aria || '가격 차트' });

    // grid + y labels
    niceTicks(lo, hi, compact ? 4 : 6).forEach(v => {
      const yy = y(v);
      if (yy < PAD.t - 1 || yy > PAD.t + plotH + 1) return;
      svg.appendChild(sv('line', { cls: 'g-grid', x1: PAD.l, x2: PAD.l + plotW, y1: yy, y2: yy }));
      svg.appendChild(sv('text', { cls: 'g-axis', x: PAD.l - 8, y: yy + 4, 'text-anchor': 'end' },
        [fmtPrice(v, o.ccy)]));
    });
    // x labels
    const xl = [];
    if (o.period === '1D') {
      let lastH = null;
      bars.forEach((b, i) => { const hh = S.fmtTime(b.ts, stk.tz(o.region)).slice(0, 2); if (hh !== lastH && i > 0) xl.push([i, hh + ':00']); lastH = hh; });
    } else if (o.period === '5Y') {
      let lastY = null;
      bars.forEach((b, i) => { const yy = stk.barDate(b.ts, o.region).slice(0, 4); if (yy !== lastY) { if (i > 0) xl.push([i, yy]); lastY = yy; } });
    } else {
      let lastM = null;
      bars.forEach((b, i) => { const m = +stk.barDate(b.ts, o.region).slice(5, 7); if (m !== lastM) { if (i > 0 || n < 30) xl.push([i, m + '월']); lastM = m; } });
    }
    let lastX = -1e9;
    const gap = compact ? 34 : 44;
    xl.forEach(([i, t]) => {
      const xx = x(i);
      if (xx - lastX < gap || xx > PAD.l + plotW - 8) return;
      lastX = xx;
      svg.appendChild(sv('text', { cls: 'g-axis', x: xx, y: H - 6, 'text-anchor': 'middle' }, [t]));
    });

    // overlays under the price
    const labels = [];
    shown.forEach(ov => {
      const cls = 'ov ov-' + ov.cls + (ov.dashed ? ' is-dashed' : '') + (ov.faded ? ' is-faded' : '');
      if (ov.price == null) {
        const y1 = y(ov.high), y2 = y(ov.low);
        svg.appendChild(sv('rect', { cls: cls + ' ov-band', x: PAD.l, y: y1, width: plotW, height: Math.max(1, y2 - y1) }));
        svg.appendChild(sv('line', { cls: cls, x1: PAD.l, x2: PAD.l + plotW, y1, y2: y1 }));
        svg.appendChild(sv('line', { cls: cls, x1: PAD.l, x2: PAD.l + plotW, y1: y2, y2 }));
        labels.push({ y: (y1 + y2) / 2, text: compact ? ov.short : ov.label, cls: 'ovt ovt-' + ov.cls + (ov.faded ? ' is-faded' : '') });
      } else {
        const yy = y(ov.price);
        svg.appendChild(sv('line', { cls: cls, x1: PAD.l, x2: PAD.l + plotW, y1: yy, y2: yy }));
        labels.push({ y: yy, text: compact ? ov.short : ov.label, cls: 'ovt ovt-' + ov.cls + (ov.faded ? ' is-faded' : '') });
      }
    });

    // price
    if (o.kind === 'candle') {
      const bw = Math.max(1, Math.min(9, plotW / Math.max(n, 1) * 0.6));
      bars.forEach((b, i) => {
        const up = b.close >= b.open;
        const c = up ? 'cd-up' : 'cd-down';
        svg.appendChild(sv('line', { cls: 'cd-wick ' + c, x1: x(i), x2: x(i), y1: y(b.high), y2: y(b.low) }));
        const top = y(Math.max(b.open, b.close)), bot = y(Math.min(b.open, b.close));
        svg.appendChild(sv('rect', { cls: 'cd-body ' + c, x: x(i) - bw / 2, y: top, width: bw, height: Math.max(1, bot - top) }));
      });
    } else {
      const d = bars.map((b, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(b.close).toFixed(1)).join(' ');
      svg.appendChild(sv('path', { cls: 'g-price', d }));
    }
    // the last point: 현재 pill (or the last quote's date when there is none)
    const lastPx = o.current != null ? o.current : (n ? bars[n - 1].close : null);
    if (lastPx != null && n) {
      const lx = x(n - 1), ly = y(lastPx);
      svg.appendChild(sv('circle', { cls: 'g-dot', cx: lx, cy: ly, r: 3 }));
      if (o.quoteNone) {
        svg.appendChild(sv('text', { cls: 'g-axis', x: lx - 6, y: ly - 10, 'text-anchor': 'end' }, [o.lastQuoteText || '']));
      } else {
        labels.push({ y: ly, text: (compact ? '' : '현재 ') + fmtPrice(lastPx, o.ccy), pill: true });
      }
    }

    // right-hand labels, nudged apart
    const minGap = compact ? 15 : 18;
    labels.sort((a, b) => a.y - b.y);
    for (let i = 1; i < labels.length; i++) {
      if (labels[i].y - labels[i - 1].y < minGap) labels[i].y = labels[i - 1].y + minGap;
    }
    const bottom = PAD.t + plotH + 6;
    for (let i = labels.length - 1; i >= 0; i--) {
      const lim = i === labels.length - 1 ? bottom : labels[i + 1].y - minGap;
      if (labels[i].y > lim) labels[i].y = lim;
    }
    labels.forEach(l => {
      const lx = PAD.l + plotW + 6;
      if (l.pill) {
        const w = textW(l.text, fs) + 14;
        svg.appendChild(sv('rect', { cls: 'g-pill', x: lx, y: l.y - 10, width: w, height: 20, rx: 10 }));
        svg.appendChild(sv('text', { cls: 'g-pill-t', x: lx + 7, y: l.y + 4 }, [l.text]));
      } else {
        svg.appendChild(sv('text', { cls: l.cls, x: lx, y: l.y + 4 }, [l.text]));
      }
    });
    host.appendChild(svg);
    return { outside };
  }

  // ── 보기 ───────────────────────────────────────────────────────────
  let V = null;            // the mounted view's state

  function mount(root, params) {
    const sid = String(params.s || '').toUpperCase();
    const [region, code] = stk.split(sid);
    const v = V = {
      sid, region, code, root, alive: true, offs: [], timers: [],
      meta: undefined, bars: null, barsErr: null, analysis: undefined, anErr: null, job: null,
      facts: undefined, factsErr: null, fund: undefined, fundErr: null, news: undefined, newsErr: null,
      trades: undefined, tradesErr: null, quote: undefined, compact: !matchMedia('(min-width: 600px)').matches,
      wide: matchMedia('(min-width: 1100px)').matches,
    };
    root.classList.add('stk-page');
    document.body.classList.add('stk-has-bar');
    v.els = buildFrame(v);
    root.appendChild(v.els.frame);
    paintAll(v);

    // account data (core.js) — head, my position and the rail follow it
    v.offs.push(ACC.on('data', () => { if (v.alive) { paintAccount(v); } }));
    v.offs.push(stk.on('trade', t => { if (v.alive && t.symbol_id === v.sid) { loadTrades(v); } }));
    const mq = matchMedia('(min-width: 600px)'), mqw = matchMedia('(min-width: 1100px)');
    const onMq = () => { v.compact = !mq.matches; v.wide = mqw.matches; paintChart(v); paintSignals(v); paintNews(v); paintTrades(v); };
    mq.addEventListener('change', onMq); mqw.addEventListener('change', onMq);
    v.offs.push(() => { mq.removeEventListener('change', onMq); mqw.removeEventListener('change', onMq); });
    let rw = 0;
    const ro = new ResizeObserver(() => {
      const w = v.els.chartHost.clientWidth;
      if (w && Math.abs(w - rw) > 4) { rw = w; paintChart(v); }
    });
    ro.observe(v.els.chartHost);
    v.offs.push(() => ro.disconnect());

    // shared facts — in parallel, each section on its own
    stk.meta(sid).then(m => { v.meta = m; paintAll(v); }, () => { v.meta = null; paintAll(v); });
    loadBars(v);
    loadAnalysis(v);
    loadStatus(v);
    loadFacts(v);
    loadFund(v);
    loadNews(v);
    loadTrades(v);
    setupAnchors(v);
  }

  function unmount() {
    const v = V;
    V = null;
    if (!v) return;
    v.alive = false;
    v.offs.forEach(f => { try { f(); } catch (e) { /* */ } });
    v.timers.forEach(t => clearInterval(t));
    if (v.stopPoll) v.stopPoll();
    v.root.classList.remove('stk-page');
    document.body.classList.remove('stk-has-bar');
  }

  function update(params) {
    const sid = String(params.s || '').toUpperCase();
    if (V && V.sid === sid) return;
    const root = V ? V.root : document.getElementById('main');
    unmount();
    clear(root);
    mount(root, params);
  }

  // ── 상태 판정 ──────────────────────────────────────────────────────
  // 'held' (qty>0) · 'watch' (in the account, no quantity) · 'absent'
  function holdingOf(v) { return ACC.holding(v.sid); }
  function kindOf(v) {
    const h = holdingOf(v);
    return !h ? 'absent' : ACC.held(h) ? 'held' : 'watch';
  }
  function ccy(v) { return stk.ccyOf(holdingOf(v), v.meta, v.sid); }
  function name(v) { return stk.displayName(holdingOf(v), v.meta, v.sid); }
  function notFound(v) { return v.meta === null && ACC.data && !holdingOf(v); }
  // the price a person reads: the account's quote (core.js judged its age),
  // or for a symbol outside the account the warmed quote
  function priceInfo(v) {
    const h = holdingOf(v);
    const mk = ACC.market(v.region);
    if (h) {
      return { price: h.price, prev: h.prev_close, ts: h.quote_ts, q: h.q || S.quoteState({ quoteTs: h.quote_ts, market: v.region, session: mk.session, phase: mk.phase }) };
    }
    const qt = v.quote;
    if (qt && qt.price != null) {
      return { price: qt.price, prev: qt.prev_close, ts: qt.ts, q: S.quoteState({ quoteTs: qt.ts, market: v.region, session: mk.session, phase: mk.phase }) };
    }
    if (v.bars && v.bars.length && v.period === '3M') {
      const b = v.bars[v.bars.length - 1], p = v.bars[v.bars.length - 2];
      return { price: b.close, prev: p ? p.close : null, ts: null, barDate: stk.barDate(b.ts, v.region), q: { state: 'fresh', text: '' } };
    }
    return { price: null, prev: null, ts: null, q: { state: qt === undefined ? 'loading' : 'none', text: '시세 없음' } };
  }
  function aState(v) {
    const body = v.analysis && v.analysis.result ? v.analysis.result.body : null;
    const k = kindOf(v);
    const job = v.job || {};
    const base = S.analysisState({ date: body ? body.date : null, held: k !== 'watch', status: body ? body.status : null });
    const cur = S.analysisState({ date: body ? body.date : null, held: k !== 'watch', status: body ? body.status : null,
      running: job.state === 'running' || job.state === 'started', failed: job.state === 'error', hung: job.state === 'stale' });
    return { body, base, cur };
  }

  // ── 틀 ─────────────────────────────────────────────────────────────
  function section(id, area, kids, extraCls) {
    return el('section', { id, className: 'card stk-sec stk-' + area + (extraCls ? ' ' + extraCls : ''), 'aria-labelledby': id + '-h' }, kids);
  }
  function headTitle(id, text, kids) {
    return el('div', { className: 'stk-sec-head' }, [el('h2', { id: id + '-h' }, [text])].concat(kids || []));
  }
  function buildFrame(v) {
    const els = {};
    els.rail = el('nav', { className: 'stk-rail', 'aria-label': '보유 종목' });
    els.anchors = el('div', { className: 'stk-anchors sm-down', role: 'navigation', 'aria-label': '구역 이동' });
    els.head = el('div', { className: 'stk-head', id: 'stk-sec-summary' });
    els.posBody = el('div', { className: 'stk-pos-body' });
    els.posTitle = el('span', { className: 'meta' });
    els.pos = section('stk-sec-pos', 'pos', [headTitle('stk-sec-pos', '내 위치', [els.posTitle]), els.posBody]);
    els.chartMeta = el('span', { className: 'meta' });
    els.chartCtl = el('div', { className: 'stk-chart-ctl' });
    els.chartChips = el('div', { className: 'stk-chips' });
    els.chartHost = el('div', { className: 'stk-chart-host' });
    els.chartNote = el('span', { className: 'stk-chart-note t-caption t-muted' });
    els.chart = section('stk-sec-chart', 'chart', [
      el('div', { className: 'stk-sec-head stk-chart-head' }, [el('h2', { id: 'stk-sec-chart-h' }, ['가격']), els.chartMeta, els.chartCtl]),
      els.chartChips, els.chartHost]);
    els.verdictBody = el('div', { className: 'stk-verdict-body' });
    els.verdictBtn = el('div', { className: 'stk-sec-act' });
    els.verdict = section('stk-sec-verdict', 'verdict', [
      headTitle('stk-sec-verdict', '판정', [el('span', { className: 'chip' }, ['AI 판단']), els.verdictBtn]), els.verdictBody]);
    els.sigMeta = el('span', { className: 'meta' });
    els.sigLegend = el('div', { className: 'stk-legend sm-up' });
    els.sigBody = el('div', { className: 'stk-sig-body' });
    els.signals = section('stk-sec-signals', 'signals', [
      headTitle('stk-sec-signals', '신호판', [el('span', { className: 'chip' }, ['계산값']), els.sigMeta, els.sigLegend]), els.sigBody]);
    els.fundMeta = el('span', { className: 'meta' });
    els.fundAct = el('div', { className: 'stk-sec-act' });
    els.fundBody = el('div', { className: 'stk-fund-body' });
    els.fund = section('stk-sec-fund', 'fund', [
      headTitle('stk-sec-fund', '재무', [el('span', { className: 'chip' }, ['계산값']), els.fundMeta, els.fundAct]), els.fundBody]);
    els.newsAct = el('div', { className: 'stk-sec-act' });
    els.newsBody = el('div', { className: 'stk-news-body' });
    els.news = section('stk-sec-news', 'news', [headTitle('stk-sec-news', '관련 소식', [els.newsAct]), els.newsBody]);
    els.tradesMeta = el('span', { className: 'meta' });
    els.tradesAct = el('div', { className: 'stk-sec-act' });
    els.tradesBody = el('div', { className: 'stk-trades-body' });
    els.trades = section('stk-sec-trades', 'trades', [
      headTitle('stk-sec-trades', '내 거래·메모', [els.tradesMeta, els.tradesAct]), els.tradesBody]);
    els.bar = el('div', { className: 'stk-bar sm-down' });
    els.grid = el('div', { className: 'stk-grid' }, [els.pos, els.chart, els.verdict, els.signals, els.fund, els.news, els.trades]);
    els.content = el('div', { className: 'stk-main' }, [els.anchors, els.head, els.grid]);
    els.frame = el('div', { className: 'stk-frame' }, [els.rail, els.content, els.bar]);
    return els;
  }

  function paintAll(v) {
    if (!v.alive) return;
    if (notFound(v)) { paintNotFound(v); return; }
    if (v.nf) { v.nf = false; v.els.content.replaceChildren(v.els.anchors, v.els.head, v.els.grid); }
    paintAccount(v);
    paintChart(v);
    paintVerdict(v);
    paintSignals(v);
    paintFund(v);
    paintNews(v);
    paintTrades(v);
  }
  // everything that moves with ACC.data
  function paintAccount(v) {
    if (!v.alive) return;
    if (notFound(v)) { paintNotFound(v); return; }
    paintRail(v);
    paintHead(v);
    paintPos(v);
    paintBar(v);
    // the account-dependent parts of the shared sections (평단 line, 평단 대비,
    // 관심 상태) redraw when what they lean on changes
    const key = [!!ACC.data, kindOf(v), priceInfo(v).price, (holdingOf(v) || {}).avg_cost, (holdingOf(v) || {}).note].join('|');
    if (v.accKey !== key) {
      v.accKey = key;
      paintChart(v); paintVerdict(v); paintTrades(v);
    }
    if (ACC.data && kindOf(v) === 'absent' && v.quote === undefined) {
      v.quote = null;
      stk.quote(v.sid).then(q => { v.quote = q || null; paintAccount(v); paintChart(v); }, () => { v.quote = null; paintAccount(v); });
    }
    const title = ACC.data || v.meta ? name(v) : '종목';
    if (ACC.setChrome && v.chromeTitle !== title) { v.chromeTitle = title; ACC.setChrome({ title, back: () => goBack() }); }
  }
  function goBack() {
    if (history.state && history.state.acc && history.length > 1) history.back();
    else ACC.go({});
  }

  // ── 레일 (≥1100) ───────────────────────────────────────────────────
  function paintRail(v) {
    const r = v.els.rail;
    clear(r);
    const d = ACC.data;
    const accName = ACC.accountRef().name;
    const hs = d ? d.holdings || [] : [];
    const held = hs.filter(h => ACC.held(h)), watch = hs.filter(h => !ACC.held(h));
    r.appendChild(el('div', { className: 'stk-rail-head' }, [
      el('a', { className: 'btn stk-rail-back', href: location.pathname, 'aria-label': LABELS.back + ' · ' + accName,
        onClick: e => { e.preventDefault(); ACC.go({}); } }, [svgIco('arrow-left'), el('span', { className: 'ellipsis' }, [accName])]),
      d ? el('span', { className: 't-caption t-muted' }, [held.length + '종목']) : null,
    ]));
    if (!d) { r.appendChild(S.skeleton(['80%', '60%', '80%', '60%'])); return; }
    const row = h => {
      const cur = h.symbol_id === v.sid;
      const chg = h.q && h.q.state === 'stale' ? el('span', { className: 't-warn t-caption' }, [h.q.text])
        : h.q && h.q.state === 'none' ? el('span', { className: 't-warn t-caption' }, ['시세 없음'])
        : el('span', { className: 't-caption ' + trendCls(h.price - h.prev_close) }, [S.fmtChangePct(stk.pct(h.price, h.prev_close))]);
      return el('a', { className: 'stk-rail-row' + (cur ? ' is-current' : ''), href: '?s=' + encodeURIComponent(h.symbol_id),
        'aria-current': cur ? 'page' : null, title: h.name_kr || h.symbol_id,
        onClick: e => { e.preventDefault(); if (!cur) ACC.go({ s: h.symbol_id }); } }, [
        el('span', { className: 'stk-rail-name' }, [el('span', { className: 'ellipsis' }, [h.name_kr || h.name_en || h.code]),
          el('span', { className: 't-caption t-muted' }, [ACC.held(h) ? fmtPrice(h.price, h.currency) : '관심 · 수량 없음'])]),
        chg,
      ]);
    };
    if (held.length) { r.appendChild(el('p', { className: 'stk-rail-cap t-caption t-muted' }, ['보유'])); held.forEach(h => r.appendChild(row(h))); }
    if (watch.length) { r.appendChild(el('p', { className: 'stk-rail-cap t-caption t-muted' }, ['관심'])); watch.forEach(h => r.appendChild(row(h))); }
  }

  // ── 머리 ───────────────────────────────────────────────────────────
  function marketLabel(v) {
    const h = holdingOf(v), m = v.meta;
    const mk = (h && h.market) || (m && m.market) || '';
    const etf = (h && h.is_etf) || (m && m.is_etf);
    return [v.region, v.code, etf ? 'ETF' : mk].filter(Boolean).join(' · ');
  }
  function timeText(v, pi) {
    if (pi.barDate) return S.fmtDate(pi.barDate) + ' 종가';
    if (!pi.ts) return '';
    const tz = stk.tz(v.region);
    const today = S.marketToday(v.region);
    const p = S.fmtDate(pi.ts, tz);
    return (p === S.fmtDate(today) ? '오늘 ' : p + ' ') + S.fmtTime(pi.ts, tz);
  }
  function phaseChip(v) {
    const ph = ACC.market(v.region).phase;
    // server calendar (core.js): open · pre/post → 마감 · closed (주말·휴장일) → 휴장
    return el('span', { className: 'chip' }, [ph === 'open' ? '장중' : ph === 'closed' ? '휴장' : '마감']);
  }
  function paintHead(v) {
    const hd = v.els.head;
    clear(hd);
    const loading = !ACC.data && !v.meta;
    const k = kindOf(v);
    const c = ccy(v);
    const pi = priceInfo(v);
    const star = k === 'watch'
      ? el('button', { type: 'button', className: 'btn stk-star', 'aria-label': '관심에서 삭제', 'aria-pressed': 'true',
          title: '관심에서 삭제', onClick: () => unwatch(v) }, [svgIco('star')])
      : null;
    hd.appendChild(el('div', { className: 'stk-id' }, [
      el('h1', { className: 'stk-name' }, [loading ? '…' : name(v)]),
      el('span', { className: 'stk-code t-caption t-muted' }, [marketLabel(v)]),
      star,
    ]));
    const row = el('div', { className: 'stk-pricerow' });
    if (loading || pi.q.state === 'loading') {
      row.appendChild(el('span', { className: 'stk-price' }, ['—']));
    } else if (pi.q.state === 'none' || pi.price == null) {
      const last = pi.ts ? '마지막 시세 ' + S.fmtDate(pi.ts, stk.tz(v.region)) + ' · 8일 넘음' : '받은 시세가 없어요';
      row.appendChild(el('span', { className: 'stk-price' }, [(c === 'USD' ? '$' : '₩') + ' —']));
      row.appendChild(el('span', { className: 'stk-chgline' }, [
        el('span', { className: 't-caption t-muted' }, [last]),
        el('span', { className: 'chip stk-chip-warn' }, [svgIco('triangle-alert'), '시세 없음'])]));
    } else {
      const ch = pi.prev ? pi.price - pi.prev : null;
      const stale = pi.q.state === 'stale', delayed = pi.q.state === 'delayed';
      row.appendChild(el('span', { className: 'stk-price' + (stale ? ' is-stale' : '') }, [S.fmtMoney(pi.price, c)]));
      row.appendChild(el('span', { className: 'stk-chgline' }, [
        ch != null ? el('span', { className: 'stk-chg ' + trendCls(ch) }, [S.fmtChange(ch, c) + ' ' + S.fmtChangePct(stk.pct(pi.price, pi.prev))]) : null,
        el('span', { className: 't-caption ' + (delayed || stale ? 't-warn' : 't-muted') }, [
          timeText(v, pi) + (delayed || stale ? ' · ' + pi.q.text : '')]),
        phaseChip(v),
      ]));
    }
    hd.appendChild(row);
    // actions: 1440 매수 · 매도 · ⋯ — 390 ⋯ only (매수|매도 sit in the bottom bar)
    const acts = el('div', { className: 'stk-acts' });
    if (ACC.data) {
      if (k === 'absent') {
        acts.appendChild(el('button', { type: 'button', className: 'btn sm-up', onClick: () => addWatch(v) }, [LABELS.addWatch]));
        acts.appendChild(el('button', { type: 'button', className: 'btn btn-primary sm-up', onClick: () => openTrade(v, 'buy') }, [LABELS.buy]));
      } else {
        acts.appendChild(el('button', { type: 'button', className: 'btn btn-primary stk-act-main sm-up', onClick: () => openTrade(v, 'buy') }, [LABELS.buy]));
        if (k === 'held') acts.appendChild(el('button', { type: 'button', className: 'btn stk-act-main sm-up', onClick: () => openTrade(v, 'sell') }, [LABELS.sell]));
        acts.appendChild(el('button', { type: 'button', className: 'btn stk-more', 'aria-label': '종목 메뉴', 'aria-haspopup': 'menu',
          'aria-expanded': 'false', onClick: e => stockMenu(v, e.currentTarget) }, [svgIco('ellipsis')]));
      }
    }
    hd.appendChild(acts);
  }
  function paintBar(v) {
    const b = v.els.bar;
    clear(b);
    if (!ACC.data || notFound(v)) { b.hidden = true; return; }
    b.hidden = false;
    const k = kindOf(v);
    if (k === 'absent') {
      b.appendChild(el('button', { type: 'button', className: 'btn', onClick: () => addWatch(v) }, [LABELS.addWatch]));
      b.appendChild(el('button', { type: 'button', className: 'btn btn-primary', onClick: () => openTrade(v, 'buy') }, [LABELS.buy]));
    } else {
      b.appendChild(el('button', { type: 'button', className: 'btn btn-primary', onClick: () => openTrade(v, 'buy') }, [LABELS.buy]));
      if (k === 'held') b.appendChild(el('button', { type: 'button', className: 'btn', onClick: () => openTrade(v, 'sell') }, [LABELS.sell]));
      else b.classList.add('is-one');
    }
    b.classList.toggle('is-one', k === 'watch');
  }

  // ── 내 위치 ────────────────────────────────────────────────────────
  function cell(label, value, sub, cls) {
    return el('div', { className: 'stk-cell' }, [
      el('span', { className: 't-caption t-muted' }, [label]),
      el('span', { className: 'stk-cell-v ' + (cls || '') }, [value]),
      sub ? el('span', { className: 't-caption ' + (cls || 't-muted') }, [sub]) : null,
    ]);
  }
  function coordSpan(kind, text) { return el('span', { className: 'stk-coord stk-coord-' + kind }, [text]); }
  function paintPos(v) {
    const b = v.els.posBody, t = v.els.posTitle;
    clear(b); clear(t);
    const d = ACC.data;
    if (!d) { b.appendChild(S.skeleton(['100%', '50%', '100%'])); return; }
    const accName = ACC.accountRef().name;
    const k = kindOf(v), h = holdingOf(v), c = ccy(v), pi = priceInfo(v);
    if (k === 'absent') {
      t.textContent = accName;
      b.appendChild(el('p', { className: 'stk-pos-state' }, ['이 계좌에 없음']));
      const sold = v.lastSell;
      b.appendChild(el('p', { className: 't-caption t-muted' }, [sold
        ? S.fmtDateTime(sold.traded_at, stk.tz(v.region)) + ' 전량 매도 · 거래 이력에 남아 있어요'
        : '관심 추가나 매수 기록으로 따라볼 수 있어요']));
      return;
    }
    if (k === 'watch') {
      t.textContent = accName;
      b.appendChild(el('p', { className: 'stk-pos-state' }, [svgIco('star', 'stk-star-ico'), '관심 · 수량 없음']));
      b.appendChild(el('p', { className: 't-caption t-muted' }, ['수량 없이 따라보는 종목이에요 · 자동 분석 없음']));
      const line = coordsLine(v, 'entry');
      if (line) { line.classList.remove('is-faded'); b.appendChild(line); }
      return;
    }
    const tot = (d.totals || {})[c] || {};
    const none = pi.q.state === 'none' || pi.price == null;
    const evalV = none ? (h.avg_cost != null ? h.avg_cost * h.qty : null) : pi.price * h.qty;
    const weight = !none && tot.total_assets ? evalV / tot.total_assets * 100 : null;
    t.textContent = accName + (none ? ' · 비중 계산에서 빠짐' : weight != null ? ' · 계좌 비중 ' + weight.toFixed(1) + '%' : '');
    const pnl = none || h.avg_cost == null ? null : (pi.price - h.avg_cost) * h.qty;
    const pnlPct = none || h.avg_cost == null ? null : stk.pct(pi.price, h.avg_cost);
    b.appendChild(el('div', { className: 'stk-cells' }, [
      cell('수량', S.fmtQty(h.qty) + '주'),
      cell('평단', fmtPrice(h.avg_cost, c)),
      cell('평가', S.fmtMoney(evalV, c), none ? '매입가 기준' : null),
      none ? cell('손익', '—') : cell('손익', S.fmtPnl(pnl, c), fmtPct(pnlPct), trendCls(pnl)),
    ]));
    const label = ACC.market(v.region).label;
    const inToday = !none && pi.q.inToday !== false && pi.prev;
    const day = inToday ? (pi.price - pi.prev) * h.qty : null;
    b.appendChild(el('div', { className: 'stk-today' }, [
      el('span', { className: 't-caption t-muted' }, [label.pnl]),
      inToday ? el('span', { className: 'stk-today-v ' + trendCls(day) }, [S.fmtPnl(day, c) + ' ' + fmtPct(stk.pct(pi.price, pi.prev), 2)])
        : el('span', { className: 't-muted' }, [pi.q.state === 'stale' ? pi.q.text + ' · ' + label.day + ' 손익 제외' : '—']),
    ]));
    if (weight != null) {
      const bar = el('div', { className: 'stk-weight', role: 'img', 'aria-label': '계좌 비중 ' + weight.toFixed(1) + '%' }, [el('span', {})]);
      bar.firstChild.style.width = Math.max(0, Math.min(100, weight)) + '%';
      b.appendChild(bar);
    }
  }

  // ── 좌표 (판정·차트 공용) ───────────────────────────────────────────
  function levelsOf(v) {
    const st = aState(v);
    const body = st.body;
    if (!body || body.status === 'facts_only') return null;
    const lv = body.levels || {};
    const ez = lv.entry_zone, inv = lv.invalidation, tg = (lv.targets || [])[0];
    const llm = (a, k) => !!(a && a.anchoring && a.anchoring[k] && a.anchoring[k].llm_only);
    return {
      date: body.date,
      stop: inv && inv.price != null ? { price: inv.price, est: llm(inv, 'price') } : null,
      target: tg && tg.price != null ? { price: tg.price, est: !!tg.llm_only } : null,
      entry: ez && ez.low != null ? { low: ez.low, high: ez.high, est: llm(ez, 'low') || llm(ez, 'high') } : null,
      support: (lv.support || []).map(s => ({ price: s.price, est: !!s.llm_only })),
      resistance: (lv.resistance || []).map(s => ({ price: s.price, est: !!s.llm_only })),
    };
  }
  // 'avg' — 평단 대비 (held) · 'entry' — 진입 기준 · 현재가 대비 (watch/absent)
  function coordsLine(v, basis) {
    const st = aState(v);
    const L = levelsOf(v);
    if (!L) return null;
    const line = st.base.line;
    const c = ccy(v), h = holdingOf(v), pi = priceInfo(v);
    if (line === 'hidden' && st.base.state === 'old') {
      return el('p', { className: 't-caption stk-coords' }, ['지난 좌표는 숨겼어요 · 차트의 ' + LABELS.oldCoords + '로 켜요']);
    }
    if (line === 'hidden') return null;
    const base = basis === 'avg' ? (h && h.avg_cost) : pi.price;
    const p = x => (base ? ' (' + fmtPct(stk.pct(x, base)) + ')' : '');
    const est = o => (o.est ? ' · 추정' : '');
    const parts = [];
    if (basis !== 'avg' && L.entry) parts.push(coordSpan('entry', '진입 ' + fmtPrice(L.entry.low, c) + '–' + fmtPrice(L.entry.high, c) + est(L.entry) + p(L.entry.high < (base || 0) ? L.entry.high : L.entry.low)));
    if (L.stop) parts.push(coordSpan('stop', '손절 ' + fmtPrice(L.stop.price, c) + est(L.stop) + p(L.stop.price)));
    if (L.target) parts.push(coordSpan('target', '목표 ' + fmtPrice(L.target.price, c) + est(L.target) + p(L.target.price)));
    if (!parts.length) return null;
    const lead = basis === 'avg' ? '평단 대비' : '진입 기준 · 현재가 대비';
    const dated = line === 'faded' ? ' · ' + S.fmtDate(L.date) + ' 좌표' : '';
    return el('p', { className: 't-caption stk-coords' + (line === 'faded' ? ' is-faded' : '') },
      [el('span', { className: 't-muted' }, [lead + dated])].concat(parts));
  }

  // ── 가격 차트 ──────────────────────────────────────────────────────
  function defaultsOn(v) {
    if (!prefs.on) prefs.on = { avg: true, stop: true, target: true, entry: true, level: !v.compact };
    return prefs.on;
  }
  function loadBars(v) {
    const per = PERIODS.find(p => p.value === prefs.period) || PERIODS[1];
    v.period = per.value;
    v.bars = null; v.barsErr = null;
    paintChart(v);
    const key = v.sid + '|' + per.value;
    cached('bars:' + key, 5 * 60e3, () => get('/api/market/ohlcv/' + stk.enc(v.sid) + '?' + per.q).then(r => r.bars || []))
      .then(b => { if (!v.alive || v.period !== per.value) return; v.bars = b; paintChart(v); paintAccount(v); },
            e => { if (!v.alive) return; v.barsErr = e; paintChart(v); });
  }
  function chartOverlays(v) {
    const on = defaultsOn(v);
    const st = aState(v);
    const L = levelsOf(v);
    const k = kindOf(v), h = holdingOf(v), c = ccy(v), pi = priceInfo(v);
    const none = pi.q.state === 'none';
    const cur = pi.price;
    const pctT = x => (none || !cur ? '' : ' ' + fmtPct(stk.pct(x, cur)));
    const out = [];
    const line = st.base.line === 'hidden' && st.base.state === 'old' && prefs.oldCoords ? 'faded' : st.base.line;
    const ai = L && line !== 'hidden';
    const faded = line === 'faded';
    const dateTag = faded && L ? ' · ' + S.fmtDate(L.date) + ' 분석' : '';
    let dated = false;
    const tag = () => { if (dated) return ''; dated = true; return dateTag; };
    if (on.avg && k === 'held' && h.avg_cost != null) {
      out.push({ key: 'avg', cls: 'avg', price: h.avg_cost, dashed: true,
        label: '평단 ' + fmtPrice(h.avg_cost, c) + pctT(h.avg_cost), short: '평단' + pctT(h.avg_cost) });
    }
    if (ai && on.target && L.target) {
      out.push({ key: 'target', cls: 'target', price: L.target.price, dashed: L.target.est || faded, faded,
        label: '목표 ' + fmtPrice(L.target.price, c) + (L.target.est ? ' · 추정' : '') + tag() + pctT(L.target.price),
        short: '목표' + pctT(L.target.price) });
    }
    if (ai && on.entry && L.entry) {
      const near = cur != null && L.entry.high < cur ? L.entry.high : L.entry.low;
      out.push({ key: 'entry', cls: 'entry', low: L.entry.low, high: L.entry.high, dashed: L.entry.est || faded, faded,
        label: '진입 ' + fmtPrice(L.entry.low, c) + '–' + fmtPrice(L.entry.high, c) + (L.entry.est ? ' · 추정' : '') + tag() + pctT(near),
        short: '진입' + pctT(near) });
    }
    if (ai && on.stop && L.stop) {
      out.push({ key: 'stop', cls: 'stop', price: L.stop.price, dashed: L.stop.est || faded, faded,
        label: '손절 ' + fmtPrice(L.stop.price, c) + (L.stop.est ? ' · 추정' : '') + tag() + pctT(L.stop.price),
        short: '손절' + pctT(L.stop.price) });
    }
    // 지지·저항: computed levels, kept whatever the analysis' age (인계 §3.4)
    if (on.level) {
      levelList(v).forEach(s => {
        const w = s.kind === 'support' ? '지지' : '저항';
        out.push({ key: 'level', cls: 'level', price: s.price, dashed: s.est,
          label: w + ' ' + fmtPrice(s.price, c) + (s.est ? ' · 추정' : '') + pctT(s.price),
          short: w + (s.est ? ' · 추정' : '') + pctT(s.price) });
      });
    }
    return out;
  }
  // the two nearest supports below and resistances above the price
  function levelList(v) {
    const L = levelsOf(v);
    const pi = priceInfo(v);
    let sup = L ? L.support : [], res = L ? L.resistance : [];
    if (!sup.length && !res.length && v.facts && v.facts.structure) {
      const sr = v.facts.structure.support_resistance || {};
      if (sr.nearest_support) sup = [{ price: sr.nearest_support.price, est: false }];
      if (sr.nearest_resistance) res = [{ price: sr.nearest_resistance.price, est: false }];
    }
    const cur = pi.price;
    const pick = (arr, below) => arr.filter(s => s.price != null && (cur == null || (below ? s.price <= cur : s.price >= cur)))
      .sort((a, b) => Math.abs(a.price - (cur || 0)) - Math.abs(b.price - (cur || 0))).slice(0, 2);
    return pick(sup, true).map(s => Object.assign({ kind: 'support' }, s))
      .concat(pick(res, false).map(s => Object.assign({ kind: 'resistance' }, s)));
  }
  function paintChart(v) {
    if (!v.alive || notFound(v)) return;
    const per = PERIODS.find(p => p.value === prefs.period) || PERIODS[1];
    const e = v.els;
    e.chartMeta.textContent = per.meta;
    // controls (period · candle|line)
    clear(e.chartCtl);
    e.chartCtl.appendChild(S.segmented({ label: '기간', value: prefs.period, items: PERIODS.map(p => ({ value: p.value, label: p.label })),
      onChange: val => { prefs.period = val; loadBars(v); } }));
    e.chartCtl.appendChild(S.segmented({ label: '차트 형식', value: prefs.kind, items: [{ value: 'candle', label: '캔들' }, { value: 'line', label: '선' }],
      onChange: val => { prefs.kind = val; paintChart(v); } }));
    e.chartCtl.lastChild.classList.add('sm-up');
    // overlay chips
    clear(e.chartChips);
    const on = defaultsOn(v);
    const st = aState(v), L = levelsOf(v), k = kindOf(v);
    const has = { avg: k === 'held', stop: !!(L && L.stop), target: !!(L && L.target), entry: !!(L && L.entry), level: levelList(v).length > 0 };
    const aiHidden = st.base.line === 'hidden' && !prefs.oldCoords;
    OVERLAYS.forEach(o => {
      if (!has[o.key]) return;
      const off = (o.key === 'stop' || o.key === 'target' || o.key === 'entry') && aiHidden;
      e.chartChips.appendChild(el('button', { type: 'button', className: 'btn chip stk-ovchip',
        'aria-pressed': on[o.key] && !off ? 'true' : 'false', disabled: off,
        onClick: () => { on[o.key] = !on[o.key]; paintChart(v); } },
        [el('span', { className: 'stk-swatch sw-' + o.key, 'aria-hidden': 'true' }), o.label]));
    });
    if (st.base.state === 'old' && L) {
      e.chartChips.appendChild(el('button', { type: 'button', className: 'btn chip', 'aria-pressed': prefs.oldCoords ? 'true' : 'false',
        onClick: () => { prefs.oldCoords = !prefs.oldCoords; paintChart(v); paintVerdict(v); } }, [LABELS.oldCoords]));
    }
    const pi = priceInfo(v);
    // the legend's last words are always 「% = 현재가 대비」 (인계 §3.4)
    let note;
    if (!L) note = (st.base.state === 'facts' ? '판정 좌표 없음 · 사실만' : '분석 좌표 없음');
    else {
      const bits = ['좌표 ' + S.fmtDate(L.date) + ' 분석'];
      if (st.base.state === 'overdue') bits.push('갱신 누락');
      else if (st.base.state === 'old') bits.push('30일+');
      else if (k === 'watch') bits.push('관심');
      else bits.push('점선 = 추정');
      if (pi.q.state === 'none') bits.push('시세 없어 % 숨김');
      note = bits.join(' · ');
    }
    // chart body
    clear(e.chartHost);
    e.chartHost.classList.toggle('is-compact', v.compact);
    if (v.barsErr) {
      e.chartNote.textContent = '';
      e.chartHost.appendChild(S.stateBlock({ kind: 'error', level: 'h3', title: '가격을 불러오지 못했어요',
        body: '다른 구역은 그대로 볼 수 있어요', actions: [{ label: LABELS.retry, onClick: () => { stk.forget('bars:' + v.sid); loadBars(v); } }] }));
      return;
    }
    if (!v.bars) {
      e.chartNote.textContent = '';
      e.chartHost.appendChild(el('div', { className: 'stk-chart-sk', 'aria-hidden': 'true' }));
      return;
    }
    if (!v.bars.length) {
      e.chartNote.textContent = '';
      e.chartHost.appendChild(receiving('처음 보는 종목은 기록을 받는 데 몇 분 걸려요 · 잠시 뒤 다시 열어 주세요'));
      return;
    }
    const none = pi.q.state === 'none';
    const res = drawChart({
      host: e.chartHost, bars: v.bars, kind: prefs.kind, region: v.region, ccy: ccy(v), period: v.period,
      current: none ? null : pi.price, quoteNone: none,
      lastQuoteText: pi.ts ? S.fmtDate(pi.ts, stk.tz(v.region)) + ' 마지막 시세' : '',
      overlays: chartOverlays(v), compact: v.compact,
      aria: '가격 차트 · ' + per.meta + (pi.price != null ? ' · 현재 ' + fmtPrice(pi.price, ccy(v)) : ''),
    });
    const out = res.outside.map(o => o.label.split(' ')[0]).filter((x, i, a) => a.indexOf(x) === i);
    e.chartNote.textContent = note + (out.length ? ' · 차트 밖 ' + out.join('·') : '') + ' · % = 현재가 대비';
    e.chartChips.appendChild(e.chartNote);
  }
  function receiving(body) {
    return el('div', { className: 'state stk-receiving' }, [S.spinner(),
      el('h3', { className: 'state-title' }, ['가격 기록을 받는 중이에요']),
      el('p', { className: 'state-body' }, [body])]);
  }

  // ── 판정 ───────────────────────────────────────────────────────────
  function loadAnalysis(v) {
    v.anErr = null;
    get('/api/analysis/analysis/' + stk.enc(v.sid)).then(r => { if (!v.alive) return; v.analysis = r; paintVerdict(v); paintChart(v); },
      e => { if (!v.alive) return; v.anErr = e; paintVerdict(v); });
  }
  function loadStatus(v) {
    get('/api/analysis/analysis/' + stk.enc(v.sid) + '/status').then(r => { if (v.alive) setJob(v, r); }, () => { /* the card still reads */ });
  }
  function setJob(v, j) {
    const was = v.job && (v.job.state === 'running' || v.job.state === 'started');
    v.job = j;
    const running = j.state === 'running' || j.state === 'started';
    if (running && !v.stopPoll) {
      v.stopPoll = S.whenVisible(() => loadStatus(v), 5000);
      v.tick = setInterval(() => { if (v.alive) paintVerdict(v); }, 1000);
      v.timers.push(v.tick);
    }
    if (!running && v.stopPoll) { v.stopPoll(); v.stopPoll = null; clearInterval(v.tick); }
    if (was && j.state === 'done') { loadAnalysis(v); stk.forget('summary'); }
    paintVerdict(v);
  }
  async function runAnalysis(v) {
    if (v.job && (v.job.state === 'running' || v.job.state === 'started')) return;
    try {
      const r = await S.api('/api/analysis/analysis/' + stk.enc(v.sid) + '/run', { method: 'POST', toast: false });
      if (!v.alive) return;
      setJob(v, Object.assign({ started_at: new Date().toISOString() }, r.job || { state: 'running' }, { state: 'running' }));
    } catch (e) {
      if (!e.forbidden) ACC.toast('분석을 시작하지 못했어요 · 잠시 뒤 다시 해 주세요', { kind: 'err' });
    }
  }
  function confDots(conf, faded) {
    const n = Math.max(0, Math.min(5, Math.round((conf || 0) * 5)));
    return el('span', { className: 'stk-dots' + (faded ? ' is-faded' : ''), role: 'img', 'aria-label': '신뢰도 5점 중 ' + n },
      [0, 1, 2, 3, 4].map(i => el('span', { className: i < n ? 'on' : '' })));
  }
  function mmss(sec) { sec = Math.max(0, Math.floor(sec)); return Math.floor(sec / 60) + ':' + String(sec % 60).padStart(2, '0'); }
  function paintVerdict(v) {
    if (!v.alive || notFound(v)) return;
    const b = v.els.verdictBody, act = v.els.verdictBtn;
    clear(b); clear(act);
    if (v.anErr) {
      b.appendChild(S.stateBlock({ kind: 'error', level: 'h3', title: '판정을 불러오지 못했어요', body: '다른 구역은 그대로 볼 수 있어요',
        actions: [{ label: LABELS.retry, onClick: () => { v.anErr = null; v.analysis = undefined; paintVerdict(v); loadAnalysis(v); } }] }));
      return;
    }
    if (v.analysis === undefined || !ACC.data) { b.appendChild(S.skeleton(['40%', '100%', '100%', '100%', '55%'])); return; }
    if (v.bars && !v.bars.length && !(v.analysis && v.analysis.result)) {
      b.appendChild(receiving(kindOf(v) === 'watch' || kindOf(v) === 'absent'
        ? '받고 나면 분석할 수 있어요 · 관심 종목은 자동으로 분석하지 않아요' : '받고 나면 분석할 수 있어요'));
      return;
    }
    const st = aState(v), body = st.body, cur = st.cur, k = kindOf(v);
    const running = cur.state === 'running';
    act.appendChild(el('button', { type: 'button', className: 'btn btn-sm', disabled: running,
      onClick: () => runAnalysis(v) }, [running ? S.spinner() : svgIco('refresh-cw'), running ? '분석 중' : body ? LABELS.reanalyze : '분석']));
    const job = v.job || {};
    if (running) {
      const el0 = job.started_at ? (Date.now() - Date.parse(job.started_at)) / 1000 : 0;
      const prog = el('div', { className: 'stk-progress', role: 'progressbar', 'aria-label': '분석 진행', 'aria-valuemin': '0', 'aria-valuemax': '100' }, [el('span', {})]);
      const pct = Math.min(95, el0 / 420 * 100);
      prog.firstChild.style.width = Math.max(4, pct) + '%';
      prog.setAttribute('aria-valuenow', String(Math.round(pct)));
      b.appendChild(prog);
      b.appendChild(el('p', { className: 't-caption t-muted' }, ['보통 2~7분 · ' + mmss(el0) + ' 지남 · 취소할 수 없어요']));
    }
    if (!body) {
      b.appendChild(el('div', { className: 'stk-vrow' }, [el('span', { className: 't-caption t-muted' }, ['점수']),
        el('span', { className: 'stk-nodot' }, [el('span', { className: 'stk-ring', 'aria-hidden': 'true' }), '분석 없음'])]));
      b.appendChild(el('p', {}, [k === 'held' ? '아직 분석한 적이 없어요. 분석은 보통 2~7분 걸려요.'
        : '아직 분석한 적이 없어요 · 관심 종목은 자동으로 분석하지 않아요. 분석은 보통 2~7분 걸려요.']));
      paintFailNote(v, b, cur, job);
      b.appendChild(whyRow(v, null));
      return;
    }
    const fade = running || cur.state === 'old' || st.base.state === 'old';
    if (body.status === 'facts_only') {
      b.appendChild(el('div', { className: 'stk-vrow' + (running ? ' is-faded' : '') }, [el('span', { className: 't-caption t-muted' }, ['점수']),
        el('span', { className: 'chip' }, ['사실만']), el('span', { className: 't-caption t-muted' }, ['판정 없이 계산값만 남았어요'])]));
      b.appendChild(el('p', { className: running ? 'is-faded' : '' }, ['분석이 두 번 실패해 판정을 비웠어요. 신호판과 재무의 계산값으로 봐요.']));
      b.appendChild(stampLine(v, body, 'info', S.fmtDateTime(body.generated_at || body.date) + ' · 사실만'));
      paintFailNote(v, b, cur, job);
      b.appendChild(whyRow(v, null));
      return;
    }
    const tone = S.verdictTone(body.label);
    const badges = [];
    if (cur.state === 'overdue' || (st.base.state === 'overdue' && !running)) badges.push(el('span', { className: 'chip stk-chip-warn' }, [svgIco('triangle-alert'), '갱신 누락']));
    if (st.base.state === 'old' && !running) badges.push(el('span', { className: 'chip chip-quiet' }, ['30일+']));
    if (st.base.state === 'watch' && !running) badges.push(el('span', { className: 'chip chip-quiet' }, ['자동 분석 없음']));
    if (cur.state === 'failed') badges.push(el('span', { className: 'stk-flag t-error t-caption' }, [svgIco('circle-x'), '실패']));
    if (cur.state === 'hung') badges.push(el('span', { className: 'stk-flag t-warn t-caption' }, [svgIco('triangle-alert'), '응답 없음']));
    b.appendChild(el('div', { className: 'stk-vrow' + (fade ? ' is-faded' : '') }, [
      el('span', { className: 'stk-pill tone-' + tone }, [(LABEL_ARROW[body.label] || '●') + ' ' + (LABEL_KO[body.label] || '중립')]),
      el('span', { className: 't-caption t-muted sm-up' }, ['점수']),
      el('span', { className: 'stk-score tone-' + tone }, [fmtScore(body.score)]),
      el('span', { className: 't-caption t-muted' }, ['신뢰도']),
      confDots(body.confidence, fade),
    ].concat(badges)));
    const idx = LABEL_ORDER.indexOf(body.label);
    b.appendChild(el('div', { className: 'stk-scale' + (fade ? ' is-faded' : ''), 'aria-hidden': 'true' },
      LABEL_ORDER.map((l, i) => el('div', { className: 'stk-scale-i' + (i === idx ? ' on tone-' + tone : '') }, [el('span', { className: 'bar' }), el('span', { className: 't-caption' }, [LABEL_KO[l]])]))));
    if (body.summary_ko) b.appendChild(el('p', { className: 'stk-summary' + (fade ? ' is-faded' : '') }, [body.summary_ko]));
    const coords = coordsLine(v, k === 'held' ? 'avg' : 'entry');
    if (coords) { if (running) coords.classList.add('is-faded'); b.appendChild(coords); }
    const when = S.fmtDateTime(body.generated_at || body.date);
    const depth = S.depthLabel(body.effort);
    if (running) {
      b.appendChild(el('p', { className: 'stk-stamp t-caption' }, [S.spinner(), '이전 분석 ' + when + ' · 끝나면 바뀌어요']));
    } else if (st.base.state === 'overdue') {
      b.appendChild(stampLine(v, body, 'warn', when + ' 분석 · ' + st.base.days + '일 전'));
    } else if (st.base.state === 'old') {
      b.appendChild(stampLine(v, body, 'old', when + ' 분석 · ' + st.base.days + '일 전'));
    } else if (st.base.state === 'watch') {
      b.appendChild(stampLine(v, body, 'info', when + ' 분석 · 관심 종목은 자동으로 다시 보지 않아요'));
    } else {
      b.appendChild(stampLine(v, body, 'ok', when + ' · ' + depth));
    }
    paintFailNote(v, b, cur, job);
    b.appendChild(whyRow(v, body));
  }
  function stampLine(v, body, kind, text) {
    const ico = { ok: 'circle-check', warn: 'triangle-alert', old: 'triangle-alert', info: 'info' }[kind];
    return el('p', { className: 'stk-stamp t-caption stamp-' + kind }, [svgIco(ico), text]);
  }
  function paintFailNote(v, b, cur, job) {
    const t = job.started_at ? S.fmtDateTime(job.started_at) : '';
    if (cur.state === 'failed') {
      b.appendChild(el('p', { className: 'stk-stamp t-caption t-error' }, [svgIco('circle-x'), (t ? t + ' ' : '') + '다시 분석 실패 · ' + (cur.keepPrevious && aState(v).body ? '이전 결과를 보여요' : '다시 해 볼 수 있어요')]));
    } else if (cur.state === 'hung') {
      const mins = job.started_at ? Math.floor((Date.now() - Date.parse(job.started_at)) / 60000) : null;
      b.appendChild(el('p', { className: 'stk-stamp t-caption t-warn' }, [svgIco('triangle-alert'),
        (job.started_at ? S.fmtTime(job.started_at) + '에 시작한 분석이 ' + mins + '분째 답이 없어요' : '분석이 답이 없어요') + ' · 다시 분석할 수 있어요']));
    }
  }
  // 근거 (왜 · 리스크 · 시나리오 · 한계) + 이 종목 묻기
  function whyRow(v, body) {
    const wrap = el('div', { className: 'stk-why' });
    const ask = el('button', { type: 'button', className: 'btn stk-link', onClick: () => askStock(v) }, [svgIco('message-circle'), LABELS.askStock]);
    if (!body || body.status === 'facts_only') {
      wrap.appendChild(el('div', { className: 'stk-why-head is-end' }, [ask]));
      return wrap;
    }
    const pid = 'stk-why-' + v.code.replace(/\W/g, '');
    const panel = el('div', { className: 'stk-why-body', id: pid });
    panel.hidden = !prefs.openWhy;
    const toggle = el('button', { type: 'button', className: 'btn stk-why-toggle', 'aria-expanded': prefs.openWhy ? 'true' : 'false', 'aria-controls': pid,
      onClick: () => { prefs.openWhy = !prefs.openWhy; panel.hidden = !prefs.openWhy; toggle.setAttribute('aria-expanded', prefs.openWhy ? 'true' : 'false'); } },
      [el('span', { className: 'stk-why-t' }, ['근거']), el('span', { className: 't-caption t-muted' }, ['왜 · 리스크 · 시나리오 · 한계']), svgIco('chevron-down')]);
    const list = (title, items) => (items && items.length) ? el('div', { className: 'stk-why-sec' }, [
      el('h3', {}, [title]), el('ul', {}, items.map(t => el('li', {}, [String(t)])))]) : null;
    const CASE = { bull: '좋을 때', base: '기본', bear: '나쁠 때' };
    const scen = ((body.outlook || {}).scenarios || []).filter(Boolean);
    [list('왜', body.drivers), list('리스크', body.risks),
      scen.length ? el('div', { className: 'stk-why-sec' }, [el('h3', {}, ['시나리오'])].concat(scen.map(s => el('div', { className: 'stk-scen' }, [
        el('p', {}, [el('strong', {}, [(CASE[s.case] || s.case || '') + ' ']), s.narrative_ko || '']),
        (s.confirm || []).length ? el('p', { className: 't-caption' }, [el('span', { className: 't-muted' }, ['확인 · ']), s.confirm.join(' / ')]) : null,
        (s.invalidate || []).length ? el('p', { className: 't-caption' }, [el('span', { className: 't-muted' }, ['반증 · ']), s.invalidate.join(' / ')]) : null,
      ])))) : null,
      list('한계', body.limitations)].forEach(n => { if (n) panel.appendChild(n); });
    wrap.appendChild(el('div', { className: 'stk-why-head' }, [toggle, ask]));
    wrap.appendChild(panel);
    return wrap;
  }
  function askStock(v) {
    if (ACC.chat && ACC.chat.open) ACC.chat.open({ ask: v.sid, name: name(v) });
    else ACC.toast('채팅을 열지 못했어요');
  }

  // ── 신호판 ─────────────────────────────────────────────────────────
  function loadFacts(v) {
    v.factsErr = null;
    cached('facts:' + v.sid, 10 * 60e3, () => get('/api/analysis/chart/' + stk.enc(v.sid)))
      .then(r => { if (!v.alive) return; v.facts = r; paintSignals(v); paintChart(v); },
        e => { if (!v.alive) return; v.factsErr = e; paintSignals(v); });
  }
  function paintSignals(v) {
    if (!v.alive || notFound(v)) return;
    const b = v.els.sigBody;
    clear(b); clear(v.els.sigMeta); clear(v.els.sigLegend);
    v.els.sigLegend.append(el('span', { className: 't-up' }, ['▲ ']), '상승 신호', el('span', { className: 'stk-dot-n' }, ['● ']), '중립',
      el('span', { className: 't-down' }, [' ▼ ']), '하락 신호');
    v.els.sigMeta.textContent = '15개 지표';
    if (v.factsErr) {
      b.appendChild(S.stateBlock({ kind: 'error', level: 'h3', title: '신호판을 불러오지 못했어요', body: '다른 구역은 그대로 볼 수 있어요',
        actions: [{ label: LABELS.retry, onClick: () => { stk.forget('facts:' + v.sid); v.facts = undefined; paintSignals(v); loadFacts(v); } }] }));
      return;
    }
    if (v.facts === undefined) { b.appendChild(S.skeleton(['100%', '100%', '100%', '100%'])); return; }
    const tfs = (v.facts && v.facts.timeframes) || {};
    if (v.facts.error || !tfs['1d'] || !tfs['1d'].has_data) {
      b.appendChild(receiving('받고 나면 15개 지표를 계산해요'));
      return;
    }
    if (tfs['1d'].asof) v.els.sigMeta.textContent = '15개 지표 · ' + S.fmtDate(tfs['1d'].asof) + ' 종가';
    const TF = [['1d', '일'], ['1w', '주'], ['1mo', '월']];
    const byKey = {};
    TF.forEach(([tf]) => ((tfs[tf] || {}).indicators || []).forEach(i => { (byKey[i.indicator] = byKey[i.indicator] || {})[tf] = i; }));
    const catOf = id => { const any = byKey[id] && Object.values(byKey[id])[0]; return any ? any.category : null; };
    const ids = Object.keys(byKey);
    const mark = sg => el('span', { className: 'stk-sig dir-' + sg.dir }, [el('span', { className: 'stk-sig-m', 'aria-hidden': 'true' }, [DIR_MARK[sg.dir]]), sg.text]);
    if (!v.compact) {
      const cols = [['trend', 'volatility'], ['momentum', 'volume']];
      b.appendChild(el('div', { className: 'stk-sig-cols' }, cols.map(group => el('div', { className: 'stk-sig-col' }, group.map(cat => {
        const rows = ids.filter(id => catOf(id) === cat);
        if (!rows.length) return null;
        return el('table', { className: 'tbl stk-sig-tbl' }, [
          el('thead', {}, [el('tr', {}, [el('th', { scope: 'col' }, [(CATS.find(c => c.key === cat) || {}).label || cat])]
            .concat(TF.map(t => el('th', { scope: 'col' }, [t[1]]))))]),
          el('tbody', {}, rows.map(id => el('tr', {}, [el('th', { scope: 'row' }, [IND[id] || id])]
            .concat(TF.map(([tf]) => el('td', {}, [byKey[id][tf] ? mark(signal(byKey[id][tf])) : el('span', { className: 't-muted' }, ['—'])])))))),
        ]);
      })))));
      return;
    }
    // 390: one folded row per family — counts per timeframe, tap to open
    CATS.forEach(cat => {
      const rows = ids.filter(id => catOf(id) === cat.key);
      if (!rows.length) return;
      const counts = TF.map(([tf, lab]) => {
        const c = { up: 0, flat: 0, down: 0 };
        rows.forEach(id => { const i = byKey[id][tf]; if (i) { const s = signal(i); if (c[s.dir] != null) c[s.dir]++; } });
        return el('span', { className: 'stk-cnt' }, [el('span', { className: 't-muted' }, [lab + ' ']),
          c.up ? el('span', { className: 't-up' }, ['▲' + c.up]) : null, c.flat ? el('span', { className: 'stk-dot-n' }, ['●' + c.flat]) : null,
          c.down ? el('span', { className: 't-down' }, ['▼' + c.down]) : null]);
      });
      const id = 'stk-sig-' + cat.key;
      const detail = el('div', { className: 'stk-sig-detail', id }, [el('table', { className: 'tbl stk-sig-tbl' }, [
        el('thead', {}, [el('tr', {}, [el('th', { scope: 'col' }, ['지표'])].concat(TF.map(t => el('th', { scope: 'col' }, [t[1]]))))]),
        el('tbody', {}, rows.map(r => el('tr', {}, [el('th', { scope: 'row' }, [IND[r] || r])]
          .concat(TF.map(([tf]) => el('td', {}, [byKey[r][tf] ? mark(signal(byKey[r][tf])) : '—'])))))),
      ])]);
      detail.hidden = true;
      const btn = el('button', { type: 'button', className: 'btn stk-sig-row', 'aria-expanded': 'false', 'aria-controls': id,
        onClick: () => { detail.hidden = !detail.hidden; btn.setAttribute('aria-expanded', detail.hidden ? 'false' : 'true'); } },
        [el('span', { className: 'stk-sig-cat' }, [cat.label])].concat(counts).concat([svgIco('chevron-down')]));
      b.appendChild(btn);
      b.appendChild(detail);
    });
    b.appendChild(el('p', { className: 't-caption stk-legend-m' }, [el('span', { className: 't-up' }, ['▲ ']), '상승 신호 ',
      el('span', { className: 'stk-dot-n' }, ['● ']), '중립 ', el('span', { className: 't-down' }, ['▼ ']), '하락 신호']));
  }

  // ── 재무 ───────────────────────────────────────────────────────────
  function loadFund(v) {
    v.fundErr = null;
    cached('fund:' + v.sid, 30 * 60e3, () => get('/api/analysis/fundamental/' + stk.enc(v.sid) + '/facts'))
      .then(r => { if (!v.alive) return; v.fund = r; paintFund(v); }, e => { if (!v.alive) return; v.fundErr = e; paintFund(v); });
  }
  const shortPeriod = p => String(p || '').replace(/^20(\d\d)Q(\d)$/, '$1Q$2');
  function unitFor(vals, cy, perShare) {
    if (perShare) return { div: 1, label: cy === 'USD' ? '달러' : '원', d: cy === 'USD' ? 2 : 0 };
    const m = Math.max(...vals.map(x => Math.abs(x || 0)));
    if (cy === 'USD') return m >= 1e9 ? { div: 1e9, label: '십억 달러', d: 1 } : { div: 1e6, label: '백만 달러', d: 0 };
    return m >= 1e12 ? { div: 1e12, label: '조원', d: 1 } : { div: 1e8, label: '억원', d: 0 };
  }
  function paintFund(v) {
    if (!v.alive || notFound(v)) return;
    const b = v.els.fundBody;
    clear(b); clear(v.els.fundAct); v.els.fundMeta.textContent = '';
    if (v.fundErr) {
      b.appendChild(S.stateBlock({ kind: 'error', level: 'h3', title: '재무를 불러오지 못했어요', body: '다른 구역은 그대로 볼 수 있어요',
        actions: [{ label: LABELS.retry, onClick: () => { stk.forget('fund:' + v.sid); v.fund = undefined; paintFund(v); loadFund(v); } }] }));
      return;
    }
    if (v.fund === undefined) { b.appendChild(S.skeleton(['60%', '100%', '100%', '100%'])); return; }
    const f = v.fund || {};
    const h = holdingOf(v), m = v.meta;
    const etf = (h && h.is_etf) || (m && m.is_etf);
    if (etf || f.etf) {
      paintEtf(v, f.etf);
      return;
    }
    const da = f.data_available || {};
    if (f.error || (!da.financials && !da.valuation)) {
      b.appendChild(el('p', { className: 't-muted' }, ['재무 기록이 아직 없어요 · 공시가 들어오면 여기에 보여요']));
      return;
    }
    const c = ccy(v);
    v.els.fundMeta.textContent = f.latest_period ? shortPeriod(f.latest_period) + ' 기준' : '';
    // quarters only — an annual row (2025FY) in the run would read as a spike
    const series = (f.financials_series || []).filter(r => /Q\d$/.test(r.period || '')).slice(-5);
    if (series.length) {
      v.els.fundAct.appendChild(el('button', { type: 'button', className: 'btn stk-link', onClick: () => downloadCsv(v, series) },
        [svgIco('download'), 'CSV']));
    }
    const d = f.derived || {};
    const q = (f.quality || {}).piotroski_f;
    if (q && q.score != null && q.max) {
      const bar = el('span', { className: 'stk-qbar', 'aria-hidden': 'true' }, [el('span', {})]);
      bar.firstChild.style.width = Math.round(q.score / q.max * 100) + '%';
      b.appendChild(el('div', { className: 'stk-quality', title: '재무 건전성 점검 ' + q.max + '개 항목 중 맞는 수' }, [
        el('span', { className: 't-caption t-muted' }, ['품질']), el('span', { className: 'stk-q-v' }, [String(q.score)]),
        el('span', { className: 't-muted' }, ['/' + q.max]), bar]));
    }
    // 동종 백분위 (market/peer): where this value sits among the peer group
    // (basis); without one, PER·PBR fall back to the symbol's own 3-year range
    const band = f.valuation_band || {};
    const years = band.window_n ? Math.max(1, Math.round(band.window_n / 245)) : null;
    const peer = f.peer && !f.peer.error ? f.peer : null;
    const pm = k => (peer && peer.metrics && peer.metrics[k] && peer.metrics[k].percentile != null ? peer.metrics[k] : null);
    const rank = (k, own) => {
      const m = pm(k);
      if (m) return { p: m.percentile / 100, text: '동종 ' + Math.round(m.percentile) + '%', title: peer.basis + ' ' + m.n + '개 종목 중 아래에서 ' + Math.round(m.percentile) + '%' };
      if (own != null && band.available) return { p: own, text: (years ? '지난 ' + years + '년 중 ' : '') + Math.round(own * 100) + '%', title: '이 종목의 지난 값 범위에서의 위치' };
      return null;
    };
    const tile = (label, val, r) => {
      const kids = [el('span', { className: 't-caption t-muted' }, [label]), el('span', { className: 'stk-tile-v' }, [val])];
      if (r) {
        const tr = el('span', { className: 'stk-track', 'aria-hidden': 'true' }, [el('span', {})]);
        tr.firstChild.style.left = Math.round(Math.max(0, Math.min(1, r.p)) * 100) + '%';
        kids.push(el('span', { className: 'stk-tile-band', title: r.title }, [tr, el('span', { className: 't-caption t-muted' }, [r.text])]));
      }
      return el('div', { className: 'stk-tile' }, kids);
    };
    const x = (val, unit, dd) => (val == null || isNaN(val) ? '—' : num(val, dd) + unit);
    b.appendChild(el('div', { className: 'stk-tiles' }, [
      tile('PER', x(d.per, '배', 1), rank('per', band.per_pctile)),
      tile('PBR', x(d.pbr, '배', 1), rank('pbr', band.pbr_pctile)),
      tile('ROE', x(d.roe == null ? null : d.roe * 100, '%', 1)),
      tile('영업이익률', x(d.operating_margin == null ? null : d.operating_margin * 100, '%', 1)),
      tile('부채비율', x(d.debt_ratio, '%', 0)),
      tile('배당수익률', x(d.div_yield, '%', 1), rank('div_yield')),
    ]));
    if (peer && ['per', 'pbr', 'div_yield'].some(pm)) {
      b.appendChild(el('p', { className: 't-caption t-muted' }, ['동종: ' + peer.basis + ' · %는 동종 중 낮은 쪽부터 센 자리예요']));
    }
    // 5분기 막대
    const METRICS = [['revenue', '매출'], ['operating_income', '영업이익'], ['eps', 'EPS'], ['operating_cf', '현금흐름']]
      .filter(([k]) => series.some(r => r[k] != null));
    if (!METRICS.length) return;
    if (!v.fundMetric || !METRICS.some(m2 => m2[0] === v.fundMetric)) v.fundMetric = METRICS.find(m2 => m2[0] === 'operating_income') ? 'operating_income' : METRICS[0][0];
    const vals = series.map(r => r[v.fundMetric]);
    const u = unitFor(vals, c, v.fundMetric === 'eps');
    const max = Math.max(...vals.map(z => Math.abs(z || 0))) || 1;
    const bars = el('div', { className: 'stk-qbars', role: 'img',
      'aria-label': '5분기 ' + (METRICS.find(m2 => m2[0] === v.fundMetric) || [])[1] + ' ' + series.map((r, i) => shortPeriod(r.period) + ' ' + (vals[i] == null ? '없음' : num(vals[i] / u.div, u.d))).join(', ') },
      series.map((r, i) => {
        const val = vals[i];
        const col = el('div', { className: 'stk-qcol' + (i === series.length - 1 ? ' is-last' : '') + (val < 0 ? ' is-neg' : '') }, [
          el('span', { className: 't-caption' }, [val == null ? '—' : num(val / u.div, u.d)]),
          el('span', { className: 'stk-qfill' }),
          el('span', { className: 't-caption t-muted' }, [shortPeriod(r.period)]),
        ]);
        col.children[1].style.height = (val == null ? 0 : Math.max(2, Math.abs(val) / max * 100)) + '%';
        return col;
      }));
    b.appendChild(el('div', { className: 'stk-qhead' }, [
      el('span', { className: 't-caption t-muted' }, [series.length + '분기 · ' + u.label]),
      S.segmented({ label: '재무 지표', value: v.fundMetric, items: METRICS.map(([k2, l]) => ({ value: k2, label: l })),
        onChange: val => { v.fundMetric = val; paintFund(v); } }),
    ]));
    b.appendChild(bars);
  }
  // ETF: NAV · 괴리율 · 3개월 수익률 · 순자산 in place of company statements
  function paintEtf(v, e) {
    const b = v.els.fundBody;
    if (!e) {
      v.els.fundMeta.textContent = 'ETF';
      b.appendChild(el('p', { className: 't-muted' }, ['ETF라 기업 재무가 없어요 · 담은 종목의 재무는 각 종목에서 봐요']));
      return;
    }
    const c = ccy(v);
    v.els.fundMeta.textContent = 'ETF' + (e.updated_at ? ' · ' + S.fmtDate(e.updated_at) + ' 기준' : '');
    b.appendChild(el('p', { className: 't-caption t-muted' }, ['ETF라 기업 재무 대신 NAV·괴리율을 보여줘요']));
    const aum = e.aum_eokwon == null ? '—' : e.aum_eokwon >= 10000
      ? '₩' + num(e.aum_eokwon / 10000, 1) + '조' : '₩' + num(e.aum_eokwon, 0) + '억';
    const t = (label, val, cls) => el('div', { className: 'stk-tile' }, [el('span', { className: 't-caption t-muted' }, [label]),
      el('span', { className: 'stk-tile-v ' + (cls || '') }, [val])]);
    b.appendChild(el('div', { className: 'stk-tiles' }, [
      t('NAV', e.nav == null ? '—' : S.fmtMoney(e.nav, c)),
      t('괴리율', S.fmtPnlPct(e.premium_pct, 2)),
      t('3개월 수익률', S.fmtPnlPct(e.return_3m_pct, 1), trendCls(e.return_3m_pct)),
      t('순자산', aum),
      e.category ? t('분류', e.category) : null,
      e.value_mwon != null ? t('거래대금', '₩' + num(e.value_mwon / 100, 0) + '억') : null,
    ]));
  }
  function downloadCsv(v, series) {
    const cols = ['period', 'revenue', 'operating_income', 'net_income', 'eps', 'operating_cf', 'total_assets', 'total_liabilities', 'total_equity'];
    const head = ['분기', '매출', '영업이익', '순이익', 'EPS', '영업현금흐름', '자산', '부채', '자본'];
    const rows = [head.join(',')].concat(series.map(r => cols.map(k => (r[k] == null ? '' : String(r[k]))).join(',')));
    const blob = new Blob(['﻿' + rows.join('\n')], { type: 'text/csv;charset=utf-8' });
    const a = el('a', { href: URL.createObjectURL(blob), download: v.code + '_재무_' + series.length + '분기.csv' });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  // ── 관련 소식 ──────────────────────────────────────────────────────
  function loadNews(v) {
    v.newsErr = null;
    cached('news:' + v.sid, 10 * 60e3, () => get('/api/analysis/news/' + stk.enc(v.sid) + '/signals'))
      .then(r => { if (!v.alive) return; v.news = r; paintNews(v); }, e => { if (!v.alive) return; v.newsErr = e; paintNews(v); });
  }
  const TREND_KO = { improving: '좋아지는 중', rising: '좋아지는 중', up: '좋아지는 중', worsening: '나빠지는 중',
    deteriorating: '나빠지는 중', falling: '나빠지는 중', down: '나빠지는 중' };
  function paintNews(v) {
    if (!v.alive || notFound(v)) return;
    const b = v.els.newsBody;
    clear(b); clear(v.els.newsAct);
    if (v.newsErr) {
      b.appendChild(S.stateBlock({ kind: 'error', level: 'h3', title: '소식을 불러오지 못했어요', body: '다른 구역은 그대로 볼 수 있어요',
        actions: [{ label: LABELS.retry, onClick: () => { stk.forget('news:' + v.sid); v.news = undefined; paintNews(v); loadNews(v); } }] }));
      return;
    }
    if (v.news === undefined) { b.appendChild(S.skeleton(['100%', '80%', '100%', '80%'])); return; }
    const themes = (v.news && v.news.themes) || [];
    const t = themes[0];
    if (!t) {
      b.appendChild(el('p', { className: 't-muted' }, ['이 종목과 이어진 분야가 아직 없어요 · 분야가 붙으면 소식이 여기에 보여요']));
      return;
    }
    v.els.newsAct.appendChild(el('a', { className: 'btn chip', href: '/?theme=' + encodeURIComponent(t.theme_id), title: t.name_kr + ' 분야 보기' },
      [svgIco(S.newsThemeIcon(t.theme_id)), t.name_kr]));
    const sc = t.sentiment && t.sentiment.score;
    const mb = S.moodBand(sc);
    const marker = el('span', { className: 'stk-mood-track', 'aria-hidden': 'true' }, [el('span', { className: 'stk-mood-mark mood-' + mb.band })]);
    if (sc != null) marker.firstChild.style.left = Math.round((Math.max(-1, Math.min(1, sc)) + 1) / 2 * 100) + '%';
    b.appendChild(el('div', { className: 'stk-mood' }, [
      svgIco(S.newsThemeIcon(t.theme_id), 'stk-mood-ico'),
      el('div', { className: 'stk-mood-txt' }, [el('span', { className: 't-caption t-muted' }, [t.name_kr + ' 분위기']),
        el('span', { className: 'stk-mood-v mood-t-' + mb.band }, [sc == null ? '없음' : fmtScore(sc) + ' ' + mb.arrow])]),
      el('div', { className: 'stk-mood-side' }, [marker, el('span', { className: 't-caption t-muted' }, [
        sc == null ? '' : mb.label + (t.sentiment.trend && TREND_KO[t.sentiment.trend] ? ' · ' + TREND_KO[t.sentiment.trend] : '')])]),
    ]));
    const devs = (v.news.developments || []).slice(0, v.compact ? 3 : 4);
    if (!devs.length) { b.appendChild(el('p', { className: 't-muted' }, ['최근 7일 소식이 없어요'])); return; }
    b.appendChild(el('p', { className: 't-caption t-muted stk-news-cap' }, ['최근 전개']));
    b.appendChild(el('ul', { className: 'stk-news-list' }, devs.map(dv => el('li', {}, [
      S.safeUrl(dv.url) ? el('a', { href: S.safeUrl(dv.url), target: '_blank', rel: 'noopener noreferrer', className: 'stk-news-t' }, [dv.point])
        : el('span', { className: 'stk-news-t' }, [dv.point]),
      el('span', { className: 't-caption t-muted stk-news-m' }, [[dv.theme_kr, dv.date ? S.fmtDate(dv.date) : ''].filter(Boolean).join(' · '),
        S.safeUrl(dv.url) ? svgIco('external-link') : null]),
    ]))));
  }

  // ── 내 거래·메모 ───────────────────────────────────────────────────
  function loadTrades(v) {
    v.tradesErr = null;
    get(ACC.path('/trades') + '?symbol_id=' + encodeURIComponent(v.sid) + '&limit=6').then(r => {
      if (!v.alive) return;
      v.trades = r.trades || [];
      const last = v.trades[0];
      v.lastSell = last && last.side === 'sell' && !holdingOf(v) ? last : null;
      paintTrades(v); paintPos(v);
    }, e => { if (!v.alive) return; if (!e.forbidden) { v.tradesErr = e; paintTrades(v); } });
  }
  function paintTrades(v) {
    if (!v.alive || notFound(v)) return;
    const b = v.els.tradesBody;
    clear(b); clear(v.els.tradesAct); v.els.tradesMeta.textContent = '';
    if (v.tradesErr) {
      b.appendChild(S.stateBlock({ kind: 'error', level: 'h3', title: '거래를 불러오지 못했어요', body: '다른 구역은 그대로 볼 수 있어요',
        actions: [{ label: LABELS.retry, onClick: () => { v.trades = undefined; paintTrades(v); loadTrades(v); } }] }));
      return;
    }
    if (v.trades === undefined || !ACC.data) { b.appendChild(S.skeleton(['100%', '100%', '100%'])); return; }
    const h = holdingOf(v), c = ccy(v);
    const rows = v.trades.slice(0, 5);
    v.els.tradesMeta.textContent = v.trades.length > 5 ? '최근 5건' : rows.length + '건';
    if (h) {
      v.els.tradesAct.appendChild(el('button', { type: 'button', className: 'btn dlg-icon', 'aria-label': '메모 편집', title: '메모 편집',
        onClick: () => ACC.openHoldFix && ACC.openHoldFix(v.sid, { focus: 'note' }) }, [svgIco('pencil')]));
      b.appendChild(el('div', { className: 'stk-memo' + (h.note ? '' : ' is-empty') }, [svgIco('pencil'),
        el('span', {}, [h.note || '메모 없음'])]));
    }
    if (!rows.length) {
      b.appendChild(el('p', { className: 't-muted stk-trades-none' }, ['이 종목 거래 기록이 없어요']));
      return;
    }
    const tz = stk.tz(v.region);
    const sideCell = t => el('span', { className: t.side === 'buy' ? 't-up' : 't-down' }, [t.side === 'buy' ? LABELS.buy : LABELS.sell]);
    if (v.compact) {
      b.appendChild(el('table', { className: 'tbl stk-ttbl' }, [
        el('thead', {}, [el('tr', {}, [el('th', { scope: 'col' }, ['날짜']), el('th', { scope: 'col' }, ['구분']), el('th', { scope: 'col', className: 'r' }, ['수량×가격'])])]),
        el('tbody', {}, rows.map(t => el('tr', {}, [el('td', {}, [S.fmtDate(t.traded_at, tz)]), el('td', {}, [sideCell(t)]),
          el('td', { className: 'r' }, [S.fmtQty(t.qty) + '주 × ' + fmtPrice(t.price, t.currency || c)])]))),
      ]));
    } else {
      b.appendChild(el('table', { className: 'tbl stk-ttbl' }, [
        el('thead', {}, [el('tr', {}, ['날짜', '구분', '수량', '가격', '수수료', '금액'].map((x, i) =>
          el('th', { scope: 'col', className: i >= 2 ? 'r' : null }, [x])))]),
        el('tbody', {}, rows.map(t => el('tr', {}, [
          el('td', {}, [S.fmtDate(t.traded_at, tz)]), el('td', {}, [sideCell(t)]),
          el('td', { className: 'r' }, [S.fmtQty(t.qty) + '주']), el('td', { className: 'r' }, [S.fmtMoney(t.price, t.currency || c)]),
          el('td', { className: 'r t-muted' }, [S.fmtMoney(t.fee, t.currency || c)]),
          el('td', { className: 'r' }, [S.fmtMoney(t.qty * t.price, t.currency || c)]),
        ]))),
      ]));
    }
    if (v.trades.length > 5) {
      b.appendChild(el('div', { className: 'stk-more-row' }, [el('button', { type: 'button', className: 'btn btn-sm',
        onClick: () => ACC.go({ v: 'trades', s: v.sid, p: 'all' }) }, [LABELS.showAll])]));
    }
  }

  // ── 행동 ───────────────────────────────────────────────────────────
  function openTrade(v, side) {
    if (ACC.openTrade) ACC.openTrade({ symbol_id: v.sid, side });
  }
  function stockMenu(v, btn) {
    const k = kindOf(v);
    S.menu(btn, [
      { label: LABELS.fixHolding, icon: 'arrow-up-down', onSelect: () => ACC.openHoldFix && ACC.openHoldFix(v.sid) },
      { label: '메모 편집', icon: 'pencil', onSelect: () => ACC.openHoldFix && ACC.openHoldFix(v.sid, { focus: 'note' }) },
      { label: '종목 삭제', icon: 'x', danger: true, onSelect: () => deleteStock(v, k) },
    ]);
  }
  async function addWatch(v) {
    try {
      await S.api(ACC.path('/holdings'), { method: 'POST', toast: false, headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol_id: v.sid, create_only: true, qty: 0 }) });
    } catch (e) {
      if (e.forbidden) return;
      ACC.toast(e.status === 409 ? '이미 목록에 있는 종목이에요' : '관심에 추가하지 못했어요 · 잠시 뒤 다시 해 주세요', { kind: 'err' });
      return;
    }
    ACC.toast('관심에 추가했어요');
    ACC.reload({ quiet: true });
  }
  // ★ on a watch row: no confirmation, an undo instead (기획 SCR-STOCK)
  async function unwatch(v) {
    const h = holdingOf(v);
    if (!h) return;
    const uniName = v.meta && (v.meta.name_kr || v.meta.name_en);
    const keep = { symbol_id: h.symbol_id, qty: h.qty || 0, avg_cost: h.avg_cost, note: h.note,
      name: h.name_kr && uniName && h.name_kr !== uniName ? h.name_kr : null };
    try {
      await S.api(ACC.path('/holdings/' + encodeURIComponent(v.sid)), { method: 'DELETE', toast: false });
    } catch (e) {
      if (!e.forbidden) ACC.toast('관심에서 삭제하지 못했어요 · 잠시 뒤 다시 해 주세요', { kind: 'err' });
      return;
    }
    await ACC.reload({ quiet: true });
    ACC.toast('관심에서 삭제했어요', { action: { label: LABELS.undo, onClick: async () => {
      try {
        await S.api(ACC.path('/holdings'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(keep) });
        ACC.reload({ quiet: true });
      } catch (e) { /* S.api toasted it */ }
    } } });
  }
  function deleteStock(v, k) {
    const nm = name(v);
    const doDelete = async ctx => {
      const call = ctx && ctx.api ? ctx.api : (p, o) => S.api(p, Object.assign({ toast: false }, o));
      await call(ACC.path('/holdings/' + encodeURIComponent(v.sid)), { method: 'DELETE' });
    };
    const after = () => { ACC.toast(stk.josa(nm, '을/를') + ' 삭제했어요'); ACC.reload({ quiet: true }); };
    const o = {
      title: stk.josa(nm, '을/를') + ' 삭제할까요?',
      body: k === 'held' ? '판 거라면 매도 기록으로 남겨요' : '관심 목록에서 빠져요',
      note: '삭제는 예수금·거래 이력을 건드리지 않아요',
      danger: true, confirmLabel: LABELS.delete,
      extra: k === 'held' ? { label: '매도 기록', primary: true, onClick: () => openTrade(v, 'sell') } : null,
      action: doDelete, onDone: after,
    };
    if (typeof ACC.confirm === 'function') {
      Promise.resolve(ACC.confirm(o)).then(ok => { if (ok === true) after(); }, () => { /* the dialog showed it */ });
      return;
    }
    // fallback while dialogs.js is not there: the same three choices
    const d = S.dialog({ title: o.title, primary: { label: LABELS.delete, danger: true }, keepCancel: true, initialFocus: 'cancel',
      account: ACC.accountRef(),
      body: el('div', { className: 'form-stack' }, [el('p', {}, [o.body]),
        el('p', { className: 't-caption t-muted stk-note' }, [svgIco('info'), o.note])]),
      onSubmit: async ctx => { await doDelete(ctx); return true; } });
    if (o.extra) {
      const foot = d.el.querySelector('.s-dialog-foot');
      foot.appendChild(el('button', { type: 'button', className: 'btn btn-primary', onClick: () => { d.close(null, true); o.extra.onClick(); } }, [o.extra.label]));
    }
    d.done.then(r => { if (r === true) after(); });
  }

  // ── 앵커 줄 (390) ──────────────────────────────────────────────────
  const ANCHORS = [['요약', 'stk-sec-summary'], ['차트', 'stk-sec-chart'], ['판정', 'stk-sec-verdict'],
    ['재무', 'stk-sec-fund'], ['소식', 'stk-sec-news'], ['거래', 'stk-sec-trades']];
  function setupAnchors(v) {
    const a = v.els.anchors;
    clear(a);
    const btns = ANCHORS.map(([label, id]) => el('button', { type: 'button', className: 'btn chip', 'aria-pressed': id === 'stk-sec-summary' ? 'true' : 'false',
      dataset: { target: id }, onClick: () => {
        const t = document.getElementById(id);
        if (t) t.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
        setActive(id);
      } }, [label]));
    btns.forEach(b => a.appendChild(b));
    function setActive(id) { btns.forEach(b => b.setAttribute('aria-pressed', b.dataset.target === id ? 'true' : 'false')); }
    const seen = new Map();
    const io = new IntersectionObserver(ents => {
      ents.forEach(e => seen.set(e.target.id, e.isIntersecting ? e.boundingClientRect.top : null));
      const vis = ANCHORS.map(x => x[1]).filter(id => seen.get(id) != null);
      if (vis.length) setActive(vis[0]);
    }, { rootMargin: '-96px 0px -55% 0px' });
    ANCHORS.forEach(([, id]) => { const t = document.getElementById(id); if (t) io.observe(t); });
    v.offs.push(() => io.disconnect());
  }

  // ── 없는 종목 ──────────────────────────────────────────────────────
  function paintNotFound(v) {
    v.nf = true;
    paintRail(v);
    v.els.bar.hidden = true;
    const accName = ACC.accountRef().name;
    ACC.setChrome && ACC.setChrome({ title: '종목', back: () => goBack() });
    v.els.content.replaceChildren(el('div', { className: 'stk-notfound' }, [S.stateBlock({ kind: 'info', icon: 'search', level: 'h1',
      title: '종목을 찾을 수 없어요', body: stk.josa(v.sid, '은/는') + ' 종목 목록에 없어요 · 주소를 확인해 주세요',
      actions: [{ label: stk.josa(accName, '로'), onClick: () => ACC.go({}) }] })]));
  }

  ACC.views.stock = { mount, unmount, update };
})();
