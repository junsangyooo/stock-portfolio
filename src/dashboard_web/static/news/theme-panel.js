// SCR-THEME 분야 상세 — S.panel (1440 right 420–480, 390 a full sheet).
// States (인계 §7): default · loading · no_rollup (no weekly/monthly yet) ·
// no_today (no news on the shown day) · error; flat (fewer than five scores
// in 30 days) follows no_rollup's frame: a line of words instead of the chart.
// Data: GET /api/analysis/news/themes/{id} (theme_state + weekly + monthly)
// plus the page's 30-day row from /news/themes. '내 종목' comes from the page
// (the accounts open on this device only).
(function () {
'use strict';
const { el, clear, svgIco } = S;
const U = window.NEWS_UTIL;

// ctx: { row(tid), name(tid), day(), today(), collectedAt(), mine() → Promise, onClose(), keepOpenOn }
function create(ctx) {
  const dp = S.panel({ keepOpenOn: ctx.keepOpenOn, onClose: () => { cur = null; ctx.onClose(); } });
  dp.el.classList.add('tp');
  // head: [icon] [name / sub] — the shared head holds the h2; wrap it
  const ico = el('span', { className: 'tp-ico', 'aria-hidden': 'true' });
  const sub = el('p', { className: 'tp-sub' });
  const names = el('div', { className: 'tp-names' });
  const titleBox = el('div', { className: 'tp-title' }, [ico, names]);
  dp.titleEl.parentNode.insertBefore(titleBox, dp.titleEl);
  names.appendChild(dp.titleEl);
  names.appendChild(sub);

  let cur = null;      // theme id on screen
  let seq = 0;         // drops a slow answer for a theme no longer shown
  let detail = null;

  function dayWord() { return ctx.day() === ctx.today() ? '오늘' : S.fmtDate(ctx.day()); }
  function dayItems(row) {
    if (!row) return null;
    const p = row.series.find(s => s.date === ctx.day());
    return p ? p.items : 0;
  }
  function head(tid) {
    clear(ico); ico.appendChild(svgIco(S.newsThemeIcon(tid)));
    dp.titleEl.textContent = ctx.name(tid);
    const row = ctx.row(tid);
    const n = dayItems(row);
    const parts = [];
    if (detail && detail.updated) {
      parts.push('갱신 ' + (detail.updated === ctx.day() && ctx.collectedAt()
        ? S.fmtDateTime(ctx.collectedAt()) : S.fmtDate(detail.updated)));
    }
    if (n != null) parts.push(dayWord() + ' 기사 ' + S.fmtCount(n));
    sub.textContent = parts.join(' · ');
  }

  async function open(tid) {
    const mine = ++seq;
    cur = tid; detail = null;
    head(tid);
    clear(dp.body);
    dp.body.appendChild(el('div', { className: 'tp-sk', 'aria-hidden': 'true' }, [
      el('span', { className: 'sk big' }), S.skeleton(['20%', '100%', '100%', '60%']),
      S.skeleton(['20%', '100%']), S.skeleton(['20%', '100%', '100%'])]));
    dp.body.setAttribute('aria-busy', 'true');
    dp.open();
    let d;
    try {
      d = await S.api('/api/analysis/news/themes/' + encodeURIComponent(tid), { toast: false, gate: false });
    } catch (e) {
      if (mine !== seq) return;
      if (e && e.status === 404) { dp.close(); return; }   // an unknown field in the address
      dp.body.setAttribute('aria-busy', 'false');
      clear(dp.body);
      dp.body.appendChild(S.stateBlock({ kind: 'error', level: 'h3', title: '이 분야 소식을 불러오지 못했어요',
        body: '잠시 뒤 다시 시도해 주세요', actions: [{ label: S.LABELS.retry, onClick: () => open(tid) }] }));
      return;
    }
    if (mine !== seq) return;
    detail = d;
    render(tid);
  }

  function rerender() { if (cur && detail) render(cur); }

  function render(tid) {
    const row = ctx.row(tid);
    const focusInside = dp.body.contains(document.activeElement);
    head(tid);
    clear(dp.body);
    dp.body.setAttribute('aria-busy', 'false');
    const n = dayItems(row);
    const quiet = row ? n === 0 : false;
    dp.body.appendChild(moodBlock(row, quiet));
    // 오늘
    const bullets = row && row.today && row.today.date === ctx.day() ? row.today.bullets || [] : [];
    dp.body.appendChild(sec(dayWord(), quiet || !bullets.length
      ? [el('p', { className: 'muted' }, [dayWord() === '오늘' ? '오늘 소식이 없어요' : '이날 소식이 없어요'])]
      : [el('ul', { className: 'tp-bullets' }, bullets.slice(0, 4).map(b => el('li', {}, [b])))]));
    // 관심 이슈
    if (!quiet && (detail.watchlist || []).length) {
      dp.body.appendChild(sec('관심 이슈', [el('div', { className: 'chips wrap' },
        detail.watchlist.slice(0, 5).map(w => el('span', { className: 'chip' }, [w])))]));
    }
    // 내 종목
    const mineSec = sec('내 종목', [el('p', { className: 'muted small' }, ['불러오는 중'])]);
    dp.body.appendChild(mineSec);
    fillMine(mineSec, tid);
    // 최근 전개
    const devs = (detail.recent_developments || []).slice(0, 5);
    if (devs.length) {
      dp.body.appendChild(sec('최근 전개', [el('ul', { className: 'dev-list' }, devs.map(v => el('li', {}, [
        el('a', { className: 'dev', href: v.url || '#', target: '_blank', rel: 'noopener noreferrer' }, [
          el('span', { className: 'dev-pt ellipsis', title: v.point }, [v.point]),
          el('span', { className: 'dev-date' }, [S.fmtDate(v.date)]),
          U.dots(Math.round((v.weight || 0) * 5)),
        ])])))]));
    }
    dp.body.appendChild(rollups());
    if (focusInside) { const x = dp.el.querySelector('.s-panel-x'); if (x) x.focus(); }
  }

  function sec(title, kids) {
    return el('section', { className: 'tp-sec' }, [el('h3', {}, [title])].concat(kids));
  }

  async function fillMine(node, tid) {
    let m;
    try { m = await ctx.mine(); } catch (e) { m = { error: true }; }
    if (cur !== tid || !node.isConnected) return;
    const body = node.lastChild;
    let out;
    if (m.error) out = el('p', { className: 'muted small' }, ['내 종목을 불러오지 못했어요']);
    else if (m.noAccount) out = el('p', { className: 'muted small' }, ['이 기기에서 연 계좌가 없어요']);
    else {
      const list = (m.byTheme[tid] || []);
      out = list.length
        ? el('div', { className: 'chips' }, list.map(s => el('a', { className: 'chip mine-stock',
            href: '/a/' + encodeURIComponent(s.accounts[0].id) + '?s=' + encodeURIComponent(s.sid),
            title: s.name + ' · ' + s.accounts.map(a => a.name).join('·') },
            [svgIco('chevron-right'), el('span', {}, [s.name + ' · ' + s.accounts.map(a => a.name).join('·')])])))
        : el('p', { className: 'muted small' }, ['이 분야에 연결된 내 종목이 없어요']);
    }
    node.replaceChild(out, body);
  }

  // ── 분위기: score + 30-day range, then the line (or a sentence) ──
  function moodBlock(row, quiet) {
    const series = row ? row.series : [];
    const pts = series.filter(p => p.score != null);
    const dayPt = series.find(p => p.date === ctx.day());
    const score = quiet ? null : (dayPt && dayPt.score != null ? dayPt.score
      : detail.updated === ctx.day() ? detail.sentiment.score : null);
    const top = el('div', { className: 'tp-mood-top' }, [
      el('span', { className: 'lbl' }, ['분위기']), U.scoreEl(score)]);
    if (pts.length && !quiet) top.appendChild(rangeGauge(pts, score));
    const box = el('div', { className: 'tp-mood' }, [top]);
    const shift = U.shiftText((row && row.last_shift) || detail.last_shift);
    if (quiet) {
      const last = pts[pts.length - 1];
      box.appendChild(el('div', { className: 'tp-flat' }, [
        el('p', {}, [dayWord() + ' 기사가 없어 ' + (dayWord() === '오늘' ? '오늘 ' : '이날 ') + '점수는 없어요 · 30일 중 기록 '
          + pts.length + '일']),
        el('p', { className: 'sub' }, [[last ? '마지막 점수 ' + U.fmtScore(last.score) + ' · ' + S.fmtDate(last.date) : '',
          shift].filter(Boolean).join(' · ')]),
      ]));
    } else if (!row) {
      if (shift) box.appendChild(el('p', { className: 'tp-flat sub' }, [shift]));
    } else if (pts.length < 5) {
      box.appendChild(el('div', { className: 'tp-flat' }, [
        el('p', {}, ['기록 ' + pts.length + '일 · 선은 5일째부터 그려요']),
        shift ? el('p', { className: 'sub' }, [shift]) : null,
      ]));
    } else {
      box.appendChild(lineChart(series, U.parseShift((row && row.last_shift) || detail.last_shift)));
    }
    return box;
  }

  function rangeGauge(pts, score) {
    const vals = pts.map(p => p.score);
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const pct = v => ((Math.max(-1, Math.min(1, v)) + 1) / 2 * 100);
    const span = el('span', { className: 'rg-span' });
    span.style.left = pct(lo) + '%';
    span.style.width = Math.max(1, pct(hi) - pct(lo)) + '%';
    span.style.background = 'var(' + S.cssVar(S.moodBand(score != null ? score : vals[vals.length - 1]).token) + ')';
    const kids = [span];
    if (score != null) { const mk = el('span', { className: 'rg-mark' }); mk.style.left = pct(score) + '%'; kids.push(mk); }
    return el('span', { className: 'tp-range', title: '30일 범위 ' + U.fmtScore(lo) + ' ~ ' + U.fmtScore(hi) }, [
      '30일', el('span', { className: 'rg', role: 'img', 'aria-label': '30일 범위 ' + U.fmtScore(lo) + '부터 '
        + U.fmtScore(hi) + '까지' }, kids)]);
  }

  // 30-day line: nulls break the line; zero is a dashed rule; the last
  // shift is an open dot with its date; the newest point a filled dot.
  function lineChart(series, shift) {
    const NS = 'http://www.w3.org/2000/svg';
    const W = 400, H = 132, L = 34, R = 10, T = 10, B = 22;
    const svg = document.createElementNS(NS, 'svg');
    const mk = (tag, attrs, text) => {
      const n = document.createElementNS(NS, tag);
      for (const k in attrs) n.setAttribute(k === 'className' ? 'class' : k, attrs[k]);   // one key spelling, as in S.el
      if (text != null) n.textContent = text;
      svg.appendChild(n); return n;
    };
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    svg.setAttribute('class', 'tp-line');
    svg.setAttribute('role', 'img');
    const vals = series.filter(p => p.score != null).map(p => p.score);
    const lo = Math.max(-1, Math.min(-0.5, Math.floor(Math.min(...vals) * 2) / 2));
    const hi = Math.min(1, Math.max(0.5, Math.ceil(Math.max(...vals) * 2) / 2));
    const x = i => L + (W - L - R) * (series.length > 1 ? i / (series.length - 1) : 0.5);
    const y = v => T + (H - T - B) * (hi - v) / (hi - lo || 1);
    for (let v = hi; v >= lo - 1e-9; v -= 0.5) {
      const t = Math.round(v * 10) / 10;
      mk('text', { x: L - 6, y: y(t) + 4, 'text-anchor': 'end', className: 'axis' }, t === 0 ? '0' : (t > 0 ? '+' : '−') + Math.abs(t));
    }
    mk('line', { x1: L, x2: W - R, y1: y(0), y2: y(0), className: 'grid0' });
    let d = '', pen = false;
    series.forEach((p, i) => {
      if (p.score == null) { pen = false; return; }
      d += (pen ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(p.score).toFixed(1) + ' ';
      pen = true;
    });
    mk('path', { d: d.trim(), className: 'path' });
    const idx = [0, Math.floor((series.length - 1) / 2), series.length - 1];
    idx.forEach((i, k) => mk('text', { x: x(i), y: H - 4, className: 'axis',
      'text-anchor': k === 0 ? 'start' : k === 2 ? 'end' : 'middle' }, S.fmtDate(series[i].date)));
    if (shift) {
      const i = series.findIndex(p => p.date === shift.date);
      if (i >= 0 && series[i].score != null) {
        mk('circle', { cx: x(i), cy: y(series[i].score), r: 4, className: 'shift' });
        const below = y(series[i].score) < (H - B) / 2 + T;
        mk('text', { x: x(i), y: y(series[i].score) + (below ? 18 : -9), className: 'shift-t',
          'text-anchor': i > series.length * 0.75 ? 'end' : 'middle' }, S.fmtDate(shift.date) + ' 전환');
      }
    }
    let last = -1;
    series.forEach((p, i) => { if (p.score != null) last = i; });
    if (last >= 0) mk('circle', { cx: x(last), cy: y(series[last].score), r: 4, className: 'last s-' + U.scoreTone(series[last].score) });
    svg.setAttribute('aria-label', '30일 분위기 선 · ' + S.fmtDate(series[0].date) + '부터 '
      + S.fmtDate(series[series.length - 1].date) + '까지 · 마지막 ' + U.fmtScore(last >= 0 ? series[last].score : null));
    return svg;
  }

  // ── 주간 · 월간 ──
  function rollups() {
    const w = (detail.weekly || [])[0], m = (detail.monthly || [])[0];
    const cards = [];
    if (w) cards.push(rollCard('주간 · ' + S.fmtDate(w.week_start) + ' 주', w.summary, w.bullets));
    if (m) cards.push(rollCard('월간 · ' + (+m.month.slice(5, 7)) + '월', m.summary, m.bullets));
    if (!w) {
      const sun = nextSunday();
      cards.push(el('div', { className: 'roll-card roll-none' }, [
        el('h3', {}, [m ? '주간' : '주간 · 월간']),
        el('p', {}, ['첫 주간 요약은 일요일 저녁에 나와요']),
        el('p', { className: 'sub' }, [S.fmtDate(sun) + ' 저녁 첫 주간' + (m ? '' : ' · '
          + (+ctx.today().slice(5, 7)) + '월 마지막 일요일에 첫 월간')]),
      ]));
    } else if (!m) {
      cards.push(el('div', { className: 'roll-card roll-none' }, [
        el('h3', {}, ['월간']), el('p', {}, ['첫 월간 요약은 이달 마지막 일요일 저녁에 나와요'])]));
    }
    const wrap = el('div', { className: 'roll' }, cards);
    if (cards.length === 1) wrap.classList.add('is-one');
    return wrap;
  }
  function nextSunday() {
    const t = ctx.today();
    const [y, mo, d] = t.split('-').map(Number);
    const wd = new Date(Date.UTC(y, mo - 1, d)).getUTCDay();
    return U.addDays(t, (7 - wd) % 7);
  }
  function rollCard(title, summary, bullets) {
    const text = el('p', { className: 'roll-text is-clamped' }, [summary || '']);
    const list = (bullets || []).length
      ? el('ul', { hidden: true }, bullets.slice(0, 4).map(b => el('li', {}, [b]))) : null;
    const long = (summary || '').length > 110 || list;
    const btn = long ? el('button', { type: 'button', className: 'btn btn-sm', 'aria-expanded': 'false',
      onClick: () => {
        const on = btn.getAttribute('aria-expanded') !== 'true';
        btn.setAttribute('aria-expanded', on ? 'true' : 'false');
        text.classList.toggle('is-clamped', !on);
        if (list) list.hidden = !on;
        btn.textContent = on ? S.LABELS.collapse : S.LABELS.showAll;
      } }, [S.LABELS.showAll]) : null;
    return el('div', { className: 'roll-card' }, [el('h3', {}, [title]), text, list, btn]);
  }

  return { open, close: () => dp.close(), isOpen: dp.isOpen, current: () => cur, rerender, el: dp.el };
}

window.NewsThemePanel = { create };
})();
