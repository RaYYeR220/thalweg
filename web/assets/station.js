// The live station: hydrograph, rating curve, evidence, warning state, runs, and the run button.
import { api, runArtifact, ApiError } from './api.js';
import { h, $, fill, s, svgText, svgTitle, sig, fixed, qRange, clipTime, hhmm, dayMon, parseT, ago, chip, kindClass, onWidth, errorText } from './ui.js';

const STATION = 'dart';
const HOUR = 3.6e6;
const C = { ink: '#ede6da', dim: 'rgba(237,230,218,.72)', faint: 'rgba(237,230,218,.52)', grid: 'rgba(237,230,218,.08)', ice: '#9fe3e6', band: 'rgba(159,227,230,.14)', gap: 'rgba(0,0,0,.22)', pub: '#b9cf9a', hold: '#e0a95b', esc: '#e8866a', run: '#9fe3e6' };
const KCOL = { PUBLISH: C.pub, HOLD: C.hold, ESCALATE: C.esc, RUNNING: C.run };
const mono = { 'font-family': 'IBM Plex Mono, ui-monospace, monospace' };

const state = { station: null, gaugings: [], runsAll: [], latest: null, physics: null, episode: null, episodes: [] };
const t = x => +parseT(x);

/* ---------- scales ---------- */
function niceTicks(lo, hi, n = 5) {
  const span = hi - lo || 1, raw = span / n, mag = 10 ** Math.floor(Math.log10(raw)), e = raw / mag;
  const step = (e >= 7.5 ? 10 : e >= 3.5 ? 5 : e >= 1.5 ? 2 : 1) * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-6; v += step) out.push(+v.toFixed(10));
  return { ticks: out, step, dec: Math.max(0, -Math.floor(Math.log10(step) + 1e-9)) };
}
function timeTicks(t0, t1, maxN) {
  const steps = [1, 2, 3, 6, 12, 24, 48, 96, 168, 336, 720].map(x => x * HOUR);
  const step = steps.find(st => (t1 - t0) / st <= maxN) || steps[steps.length - 1];
  const out = [];
  for (let v = Math.ceil(t0 / step) * step; v <= t1; v += step) out.push(v);
  return { ticks: out, step };
}

/* ---------- windows: the storm itself, and the calibration clips from the rest of 2018 ---------- */
const STORM = [Date.UTC(2018, 9, 10), Date.UTC(2018, 9, 14)], AFTER = [Date.UTC(2018, 9, 14), Date.UTC(2018, 9, 21)];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function span(t0, t1) {
  const a = new Date(t0), b = new Date(t1);
  if (a.getUTCMonth() === b.getUTCMonth()) return `${a.getUTCDate()}${a.getUTCDate() !== b.getUTCDate() ? '–' + b.getUTCDate() : ''} ${MONTHS[b.getUTCMonth()]} ${b.getUTCFullYear()}`;
  return `${MONTHS[a.getUTCMonth()]}–${MONTHS[b.getUTCMonth()]} ${b.getUTCFullYear()}`;
}
function episodes(levels) {
  const ts = levels.map(([x]) => t(x)).filter(Number.isFinite).sort((a, b) => a - b);
  const within = r => x => x >= r[0] && x < r[1];
  const defs = [
    { key: 'storm', has: within(STORM), label: 'Storm Callum', storm: true },
    { key: 'after', has: within(AFTER), label: 'After the storm' },
    { key: 'other', has: x => !within(STORM)(x) && !within(AFTER)(x), label: 'Other clips' },
  ];
  const out = [];
  for (const d of defs) {
    const xs = ts.filter(d.has);
    if (xs.length) out.push({ ...d, t0: xs[0], t1: xs[xs.length - 1], sub: span(xs[0], xs[xs.length - 1]) });
  }
  return out;
}

/* ---------- hydrograph: discharge panel over a stage panel, one time axis ---------- */
function hydrograph(box, W) {
  const ep = state.episode;
  const levels = (state.station.levels || []).map(([x, v]) => [t(x), +v]).filter(([x]) => ep.has(x)).sort((a, b) => a[0] - b[0]);
  const gs = state.gaugings.filter(g => ep.has(t(g.t)));
  const latestT = state.latest ? t(state.latest.t) : null;
  const latestIn = latestT != null && ep.has(latestT);
  const fc = latestIn && state.physics && Array.isArray(state.physics.forecast) ? state.physics.forecast.map(r => ({ t: t(r.t), h: +r.h, sd: +r.sd })) : [];
  const thr = (state.physics && state.physics.threshold) || {};
  const thrQ = thr.q != null ? +thr.q : (state.station.thresholds || {}).warning_q;
  const thrH = thr.stage != null ? +thr.stage : null;

  const narrow = W < 640;
  const Hd = narrow ? 380 : 440;
  const m = { l: narrow ? 46 : 58, r: narrow ? 10 : 16, t: 38, b: 30 }, gap = 48;
  let t0 = ep.t0, t1 = Math.max(ep.t1, fc.length ? fc[fc.length - 1].t : 0);
  if (t1 - t0 < 4 * HOUR) { const c = (t0 + t1) / 2; t0 = c - 2 * HOUR; t1 = c + 2 * HOUR; }
  const padT = (t1 - t0) * 0.02; t0 -= padT; t1 += padT;
  const X = x => m.l + (x - t0) / (t1 - t0) * (W - m.l - m.r);
  const inner = Hd - m.t - m.b - gap, qH = inner * 0.44, sH = inner - qH;
  const qY0 = m.t, sY0 = m.t + qH + gap;

  // discharge scale
  const qData = Math.max(0, ...gs.map(g => Math.max(+g.q_hi || 0, +g.q_lo || 0, +g.q || 0)));
  const showThrQ = thrQ != null && thrQ <= Math.max(qData, 1e-9) * 2.2;
  const qTop = Math.max(showThrQ ? thrQ * 1.08 : 0, qData * 1.12, qData ? 0 : 1);
  const qT = niceTicks(0, qTop, narrow ? 3 : 4); const qMax = qT.ticks[qT.ticks.length - 1] < qTop ? qTop : qT.ticks[qT.ticks.length - 1];
  const Yq = q => qY0 + qH - Math.max(0, q) / qMax * qH;

  // stage scale
  const hv = levels.map(l => l[1]).concat(fc.map(f => f.h));
  let hLo = Math.min(...hv), hHi = Math.max(...hv);
  if (!Number.isFinite(hLo)) { hLo = 0; hHi = 1; }
  const showThrH = thrH != null && thrH <= hHi + Math.max(0.6, (hHi - hLo) * 0.6) && thrH >= hLo - 0.5;
  if (showThrH) { hLo = Math.min(hLo, thrH); hHi = Math.max(hHi, thrH); }
  const hPad = Math.max(0.04, (hHi - hLo) * 0.14); hLo -= hPad; hHi += hPad;
  const hT = niceTicks(hLo, hHi, narrow ? 3 : 4);
  const Yh = v => sY0 + sH - (v - hLo) / (hHi - hLo) * sH;

  const svg = s('svg', { viewBox: `0 0 ${W} ${Hd}`, width: W, height: Hd, role: 'img', 'aria-label': `Hydrograph, ${ep.sub}: ${gs.length} published gaugings and ${levels.length} stage readings` });
  const g = s('g', { ...mono, 'font-size': narrow ? 10 : 11 }, svg);
  const defs = s('defs', {}, svg);
  const clipId = 'hc' + Math.random().toString(36).slice(2, 7);
  s('rect', { x: m.l, y: sY0, width: W - m.l - m.r, height: sH }, s('clipPath', { id: clipId }, defs));

  // gaps in the footage (night)
  const shadeGaps = t1 - t0 < 6 * 24 * HOUR;
  for (let k = 1; shadeGaps && k < levels.length; k++) {
    const a = levels[k - 1][0], b = levels[k][0];
    if (b - a > 3 * HOUR) {
      const x0 = X(a), x1 = X(b);
      s('rect', { x: x0, y: m.t, width: Math.max(0, x1 - x0), height: Hd - m.t - m.b, fill: C.gap }, g);
      if (x1 - x0 > 96) svgText(g, (x0 + x1) / 2, sY0 + sH - 8, b - a > 9 * HOUR ? 'no footage · night' : 'no footage', { 'text-anchor': 'middle', fill: C.faint });
    }
  }
  // future shade after the latest clip
  if (latestIn && fc.length) s('rect', { x: X(latestT), y: m.t, width: Math.max(0, W - m.r - X(latestT)), height: Hd - m.t - m.b, fill: 'rgba(237,230,218,.025)' }, g);

  // grids + axis labels
  for (const q of qT.ticks) { if (q > qMax + 1e-9) continue; s('line', { x1: m.l, x2: W - m.r, y1: Yq(q), y2: Yq(q), stroke: C.grid }, g); svgText(g, m.l - 8, Yq(q) + 3.5, q.toFixed(qT.dec), { 'text-anchor': 'end', fill: C.faint }); }
  for (const v of hT.ticks) { if (v < hLo || v > hHi) continue; s('line', { x1: m.l, x2: W - m.r, y1: Yh(v), y2: Yh(v), stroke: C.grid }, g); svgText(g, m.l - 8, Yh(v) + 3.5, v.toFixed(Math.max(1, hT.dec)).replace(/^(\d)(\d{3})\./, '$1$2.'), { 'text-anchor': 'end', fill: C.faint }); }
  const tt = timeTicks(t0, t1, Math.floor((W - m.l - m.r) / 78));
  for (const x of tt.ticks) {
    const d = new Date(x), mid = d.getUTCHours() === 0 && d.getUTCMinutes() === 0;
    s('line', { x1: X(x), x2: X(x), y1: Hd - m.b, y2: Hd - m.b + 4, stroke: C.faint }, g);
    svgText(g, X(x), Hd - m.b + 17, mid || tt.step >= 24 * HOUR ? dayMon(d) : hhmm(d), { 'text-anchor': 'middle', fill: mid ? C.ink : C.faint });
  }
  svgText(g, m.l, qY0 - 9, 'discharge, m³/s', { fill: C.dim });
  svgText(g, m.l, sY0 - 9, 'stage, m (station datum)', { fill: C.dim });

  // thresholds
  if (thrQ != null) {
    if (showThrQ) {
      s('line', { x1: m.l, x2: W - m.r, y1: Yq(thrQ), y2: Yq(thrQ), stroke: C.ice, 'stroke-dasharray': '3 4', 'stroke-width': 1.2 }, g);
      svgText(g, W - m.r, Yq(thrQ) - 6, `warning threshold · QMED ${sig(thrQ, 4)} m³/s`, { 'text-anchor': 'end', fill: C.ice });
    } else {
      svgText(g, W - m.r, qY0 - 9, narrow ? `QMED ${sig(thrQ, 4)} ↑` : `warning threshold ${sig(thrQ, 4)} m³/s, off scale ↑`, { 'text-anchor': 'end', fill: C.ice });
    }
  }
  if (showThrH) {
    s('line', { x1: m.l, x2: W - m.r, y1: Yh(thrH), y2: Yh(thrH), stroke: C.ice, 'stroke-dasharray': '3 4', 'stroke-width': 1.2 }, g);
    svgText(g, W - m.r, Yh(thrH) - 6, `${thrH.toFixed(2)} m · threshold through the rating`, { 'text-anchor': 'end', fill: C.ice });
  } else {
    svgText(g, W - m.r, sY0 - 9, thrH != null ? `${narrow ? '' : 'threshold '}${thrH.toFixed(2)} m ↑` : (narrow ? 'no rating yet' : 'no stage threshold until a rating curve exists'), { 'text-anchor': 'end', fill: thrH != null ? C.ice : C.faint });
  }

  // nowcast band
  if (fc.length) {
    const up = fc.map(f => [X(f.t), Yh(f.h + 1.96 * f.sd)]), lo = fc.map(f => [X(f.t), Yh(f.h - 1.96 * f.sd)]).reverse();
    s('path', { d: 'M' + up.concat(lo).map(p => p.map(n => n.toFixed(1)).join(',')).join('L') + 'Z', fill: C.band, 'clip-path': `url(#${clipId})` }, g);
    s('path', { d: 'M' + fc.map(f => X(f.t).toFixed(1) + ',' + Yh(f.h).toFixed(1)).join('L'), stroke: 'rgba(159,227,230,.75)', 'stroke-dasharray': '2 3', fill: 'none', 'stroke-width': 1.3, 'clip-path': `url(#${clipId})` }, g);
  }

  // stage line, broken at gaps
  let d = '', prev = null;
  for (const [x, v] of levels) { d += (prev == null || x - prev > 3 * HOUR ? 'M' : 'L') + X(x).toFixed(1) + ',' + Yh(v).toFixed(1); prev = x; }
  if (d) s('path', { d, stroke: C.ink, 'stroke-width': 1.7, fill: 'none', 'stroke-linejoin': 'round', opacity: 0.9 }, g);
  for (const [x, v] of levels) { const c = s('circle', { cx: X(x), cy: Yh(v), r: levels.length > 80 ? 1.3 : 2, fill: C.ink, opacity: 0.75 }, g); svgTitle(c, `${clipTime(new Date(x))} · stage ${v.toFixed(3)} m`); }

  // decision rug between the panels
  const rugY = qY0 + qH + 9;
  const runsIn = state.runsAll.filter(r => r.t && ep.has(t(r.t)));
  if (runsIn.length) {
    svgText(g, m.l - 8, rugY + 8.5, 'runs', { 'text-anchor': 'end', fill: C.faint, 'font-size': 9.5 });
    for (const r of runsIn.slice().reverse()) {
      const k = r.status === 'RUNNING' ? 'RUNNING' : (r.decision || {}).kind;
      const a = s('a', { href: `/run/?id=${encodeURIComponent(r.run_id)}` }, g);
      s('rect', { x: X(t(r.t)) - 1.5, y: rugY, width: 3, height: 10, rx: 1, fill: KCOL[k] || C.faint }, a);
      svgTitle(a, `${clipTime(r.t)} · ${k || r.status} · ${r.brain}${r.model ? ' ' + r.model : ''}`);
    }
  }

  // gaugings with 95 % bars, each one a link to its run
  for (const gg of gs) {
    const x = X(t(gg.t)), lo = Math.min(+gg.q_lo, +gg.q_hi), hi = Math.max(+gg.q_lo, +gg.q_hi);
    const a = s('a', { href: `/run/?id=${encodeURIComponent(gg.run_id)}`, class: 'gpt' }, g);
    svgTitle(a, `${clipTime(gg.t)} · ${sig(gg.q)} m³/s (95 % ${qRange(lo, hi)}) · ${gg.method} · stage ${(+gg.level).toFixed(3)} m`);
    if (Number.isFinite(lo) && Number.isFinite(hi)) {
      s('line', { x1: x, x2: x, y1: Yq(lo), y2: Yq(hi), stroke: C.ink, 'stroke-width': 1.2, opacity: 0.7 }, a);
      s('line', { x1: x - 3, x2: x + 3, y1: Yq(lo), y2: Yq(lo), stroke: C.ink, opacity: 0.7 }, a);
      s('line', { x1: x - 3, x2: x + 3, y1: Yq(hi), y2: Yq(hi), stroke: C.ink, opacity: 0.7 }, a);
    }
    s('circle', { cx: x, cy: Yq(+gg.q), r: 3.6, fill: C.ink, stroke: '#0c0906', 'stroke-width': 1 }, a);
    s('circle', { cx: x, cy: Yq(+gg.q), r: 9, fill: 'transparent' }, a);
  }
  if (!gs.length) svgText(g, (m.l + W - m.r) / 2, qY0 + qH / 2 + 4, 'no published gaugings in this window yet', { 'text-anchor': 'middle', fill: C.faint });
  const note = $('#hydro-note');
  if (note) {
    const other = latestT != null ? state.episodes.find(e => e.has(latestT)) : null;
    note.textContent = fc.length ? `The nowcast starts at the latest clip, ${clipTime(new Date(latestT))}, and runs 3 h ahead.`
      : state.physics && state.physics.forecast && state.physics.forecast.length && other ? `The 3 h nowcast starts at the latest clip, ${clipTime(new Date(latestT))}, in the “${other.label}” window.`
      : 'No nowcast to draw yet.';
  }

  // latest clip marker
  if (latestIn) {
    const x = X(latestT);
    s('line', { x1: x, x2: x, y1: 16, y2: Hd - m.b, stroke: C.ice, 'stroke-width': 1 }, g);
    const right = x < W - 150;
    svgText(g, x + (right ? 6 : -6), 12, `latest clip ${hhmm(new Date(latestT))}`, { fill: C.ice, 'text-anchor': right ? 'start' : 'end' });
  }
  fill(box, svg);
}

/* ---------- rating curve: stage (y) against discharge (x) ---------- */
function ratingChart(box, W) {
  const rating = state.physics && state.physics.rating;
  const pts = state.gaugings.filter(g => +g.q > 0 && Number.isFinite(+g.level));
  const Hd = W < 480 ? 280 : 320, m = { l: 62, r: 14, t: 18, b: 40 };
  if (!pts.length) { fill(box, h('p.empty', 'No published gaugings yet, so there is nothing to fit.')); return; }
  const qs = pts.flatMap(p => [+p.q, Math.min(+p.q_lo, +p.q_hi), Math.max(+p.q_lo, +p.q_hi)]).filter(v => v > 0);
  let qLo = Math.min(...qs), qHi = Math.max(...qs);
  let hLo = Math.min(...pts.map(p => +p.level)), hHi = Math.max(...pts.map(p => +p.level));
  const curve = [];
  if (rating) {
    const a = +rating.a, b = +rating.b, h0 = +rating.h0;
    const from = Math.max(h0 + 1e-3, hLo - (hHi - hLo) * 0.3 - 0.02), to = hHi + (hHi - hLo) * 0.3 + 0.02;
    const qFloor = qLo / 3, qCeil = qHi * 3;
    for (let k = 0; k <= 80; k++) { const hv = from + (to - from) * k / 80; const q = a * Math.pow(hv - h0, b); if (q >= qFloor && q <= qCeil && Number.isFinite(q)) curve.push([q, hv]); }
    if (curve.length) { qLo = Math.min(qLo, curve[0][0]); qHi = Math.max(qHi, curve[curve.length - 1][0]); hLo = Math.min(hLo, curve[0][1]); hHi = Math.max(hHi, curve[curve.length - 1][1]); }
  }
  const log = qHi / qLo > 12;
  if (log) { qLo /= 1.4; qHi *= 1.4; } else { const p = (qHi - qLo) * 0.12 || qHi * 0.2; qLo = Math.max(0, qLo - p); qHi += p; }
  const hp = Math.max(0.02, (hHi - hLo) * 0.12); hLo -= hp; hHi += hp;
  const X = q => m.l + (log ? (Math.log10(q) - Math.log10(qLo)) / (Math.log10(qHi) - Math.log10(qLo)) : (q - qLo) / (qHi - qLo)) * (W - m.l - m.r);
  const Y = v => m.t + (hHi - v) / (hHi - hLo) * (Hd - m.t - m.b);
  const svg = s('svg', { viewBox: `0 0 ${W} ${Hd}`, width: W, height: Hd, role: 'img', 'aria-label': `Rating curve: ${pts.length} published gaugings${rating ? ' and the fitted curve' : ''}` });
  const g = s('g', { ...mono, 'font-size': 10.5 }, svg);
  const hT = niceTicks(hLo, hHi, 4);
  for (const v of hT.ticks) { if (v < hLo || v > hHi) continue; s('line', { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), stroke: C.grid }, g); svgText(g, m.l - 8, Y(v) + 3.5, v.toFixed(Math.max(2, hT.dec)), { 'text-anchor': 'end', fill: C.faint }); }
  const xt = [];
  if (log) { const dec = Math.log10(qHi / qLo); const ks = dec > 2.5 ? [1] : dec > 1.4 ? [1, 3] : [1, 2, 5]; for (let e = Math.floor(Math.log10(qLo)); e <= Math.ceil(Math.log10(qHi)); e++) for (const k of ks) { const v = k * 10 ** e; if (v >= qLo && v <= qHi) xt.push(v); } }
  else xt.push(...niceTicks(qLo, qHi, 4).ticks.filter(v => v >= qLo && v <= qHi));
  for (const v of xt) { s('line', { x1: X(v), x2: X(v), y1: m.t, y2: Hd - m.b, stroke: C.grid }, g); svgText(g, X(v), Hd - m.b + 16, sig(v, 2).replace(/\.0+$/, ''), { 'text-anchor': 'middle', fill: C.faint }); }
  svgText(g, W - m.r, Hd - 6, `discharge, m³/s${log ? ' (log)' : ''}`, { 'text-anchor': 'end', fill: C.dim });
  svgText(g, m.l, m.t - 6, 'stage, m', { fill: C.dim });
  if (curve.length > 1) s('path', { d: 'M' + curve.map(([q, v]) => X(q).toFixed(1) + ',' + Y(v).toFixed(1)).join('L'), stroke: C.ice, 'stroke-width': 1.6, fill: 'none' }, g);
  for (const p of pts) {
    const lo = Math.min(+p.q_lo, +p.q_hi), hi = Math.max(+p.q_lo, +p.q_hi), y = Y(+p.level);
    const a = s('a', { href: `/run/?id=${encodeURIComponent(p.run_id)}` }, g);
    svgTitle(a, `${clipTime(p.t)} · ${sig(p.q)} m³/s at ${(+p.level).toFixed(3)} m`);
    if (lo > 0) s('line', { x1: X(lo), x2: X(hi), y1: y, y2: y, stroke: C.ink, opacity: 0.6 }, a);
    s('circle', { cx: X(+p.q), cy: y, r: 3.4, fill: C.ink, stroke: '#0c0906' }, a);
  }
  fill(box, svg);
  const note = $('#rating-note');
  if (rating) note.textContent = `Q = a(h − h₀)^b with a = ${sig(rating.a)}, b = ${fixed(rating.b, 2)}, h₀ = ${fixed(rating.h0, 3)} m, fitted to ${rating.n} gaugings (relative RMSE ${fixed(rating.rmse_rel * 100, 1)} %). Refitted after every published gauging.`;
  else note.textContent = `No curve yet. The station fits Q = a(h − h₀)^b once it has five published gaugings at three or more distinct levels; it has ${state.physics ? state.physics.n_gaugings : pts.length}. Until then the stage threshold can't be derived from QMED.`;
}

/* ---------- panels ---------- */
function renderHeader() {
  const st = state.station;
  const [place, cam] = String(st.name || '').split(/\),\s*/);
  const facts = [
    ['camera', `${cam || 'fixed bank camera'} · ${st.camera === '3d' ? '3D survey' : 'planar survey'}, ${st.grp_count} GCPs`],
    ['warning', `${sig((st.thresholds || {}).warning_q, 4)} m³/s · QMED`],
    ['source', (st.thresholds || {}).source ? st.thresholds.source.replace('m3/s', 'm³/s') : '–'],
    ['α', `${st.alpha} · ${st.alpha_source || ''}`],
    ['data', `${(st.sources || {}).dataset || ''}${(st.sources || {}).license ? ', ' + st.sources.license : ''}`],
  ];
  fill($('#st-facts'), facts.flatMap(([k, v]) => [h('dt', k), h('dd', v)]));
  if (st.notes) $('#st-notes').textContent = st.notes;
}

function renderStats() {
  const st = state.station, ph = state.physics || {};
  const lastRun = state.runsAll.find(r => r.status !== 'RUNNING');
  const items = [];
  items.push(h('div.stat', h('b', st.last_q != null ? sig(st.last_q) : '–', h('small', 'm³/s')), `latest published gauging · 95 % ${qRange(st.last_q_lo, st.last_q_hi)}`, h('br'), `${st.last_method || ''} · clip ${clipTime(st.last_q_t)}`));
  items.push(h('div.stat', h('b', st.last_level != null ? (+st.last_level).toFixed(2) : '–', h('small', 'm')), 'stage at the latest clip', h('br'), `clip ${clipTime(st.last_t)}`));
  if (ph.stage_rate_m_per_h != null) items.push(h('div.stat', h('b', `${ph.stage_rate_m_per_h >= 0 ? '+' : '−'}${Math.abs(ph.stage_rate_m_per_h * 100).toFixed(1)}`, h('small', 'cm/h')), 'stage trend, Kalman nowcast', h('br'), `${ph.n_gaugings ?? '–'} gaugings in the station's fit`));
  if (lastRun) {
    const k = (lastRun.decision || {}).kind || lastRun.status;
    items.push(h('div.stat.laststat', h('b', { class: kindClass(k) ? 'k-' + kindClass(k) : '' }, h('span.kword', (k || '–').toLowerCase())), 'latest decision', h('br'), h('a', { href: `/run/?id=${encodeURIComponent(lastRun.run_id)}` }, `${lastRun.run_id}`), ` · ${ago(lastRun.ended || lastRun.started)}`));
  }
  fill($('#st-stats'), items);
}

function renderEpisodes() {
  const seg = $('#episodes');
  fill(seg, state.episodes.map(e => h('button', { type: 'button', 'aria-pressed': String(e === state.episode), title: e.sub, onclick: () => { state.episode = e; renderEpisodes(); drawHydro(); } }, e.label, h('span.n', e.sub.replace(' 2018', '')))));
  seg.hidden = state.episodes.length < 2;
}

let unHydro = null, unRating = null;
function drawHydro() {
  const box = $('#hydro');
  if (!state.episode) { fill(box, h('p.empty', 'No stage readings yet. The series fills in as the station gauges clips.')); return; }
  if (unHydro) unHydro();
  unHydro = onWidth(box, w => hydrograph(box, w));
}
function drawRating() {
  const box = $('#rating');
  if (unRating) unRating();
  unRating = onWidth(box, w => ratingChart(box, w));
}

function renderEvidence() {
  const run = state.runsAll.find(r => (r.decision || {}).kind === 'PUBLISH');
  const box = $('#evidence');
  if (!run) { fill(box, h('p.empty', 'No published gauging yet. Evidence from the latest published run will appear here.')); $('#ev-meta').textContent = ''; return; }
  const d = run.decision;
  fill($('#ev-meta'), h('a', { href: `/run/?id=${encodeURIComponent(run.run_id)}` }, run.run_id), ` · clip ${clipTime(run.t)}`);
  const figs = [
    ['frame_overlay.jpg', 'Camera frame', 'the clip as recorded, with the surveyed control points and the gauged section'],
    ['field_lspiv.jpg', 'LSPIV', 'correlation vectors on the orthorectified water; red ones failed a filter'],
    ['field_stiv.jpg', 'STIV', 'velocities from space-time streak angles along lines parallel to the flow'],
    ['sti_mid.jpg', 'Space-time image', 'one line at mid-section: each streak is a surface tracer, its slope is its speed'],
  ];
  const fig = ([name, title, cap], big) => {
    const url = runArtifact(run.run_id, name);
    const f = h('figure.fig' + (big ? '.big' : ''),
      h('a.zoom', { href: url, target: '_blank', rel: 'noopener', 'aria-label': `${title}, full size` }, h('div.im', h('img', { src: url, alt: `${title}: ${cap}`, loading: 'lazy', decoding: 'async' }))),
      h('figcaption', h('b', title), cap));
    f.querySelector('img').addEventListener('error', () => f.remove());
    return f;
  };
  fill(box,
    h('div.ev-q', h('span.chip.pub', 'PUBLISH'), h('span.mono', `${sig(d.q)} m³/s · 95 % ${qRange(d.q_lo, d.q_hi)} · ${d.method || ''}`)),
    fig(figs[0], true),
    h('div.ev-row', figs.slice(1).map(f => fig(f))));
}

async function renderWarning() {
  const box = $('#warn');
  let drafts = [], signed = [];
  try {
    const [a, b] = await Promise.all([api.reviews('DRAFT'), api.reviews('SIGNED')]);
    drafts = (a.reviews || []).filter(w => w.station_id === STATION);
    signed = (b.reviews || []).filter(w => w.station_id === STATION);
  } catch (err) {
    fill(box, h('p.notice.err', 'The review queue could not be read: ', errorText(err)));
    return;
  }
  const w = drafts[0];
  $('#warn-meta').textContent = drafts.length ? `${drafts.length} open` : 'no open draft';
  if (w) {
    const eta = w.eta_s != null && w.created ? clipTime(new Date(t(w.created) + w.eta_s * 1000), { date: false }) : '–';
    fill(box,
      h('div.wstate', h('span.chip.draft.lg', 'DRAFT · AWAITING SIGNATURE')),
      h('blockquote.wtext', w.text),
      h('dl.kv',
        h('dt', 'p(cross)'), h('dd', w.p_cross != null ? fixed(w.p_cross, 3) + ' within 3 h' : '–'),
        h('dt', 'ETA'), h('dd', eta + ' clip time'),
        h('dt', 'drafted'), h('dd', `${clipTime(w.created)} by `, h('a', { href: `/run/?id=${encodeURIComponent(w.run_id)}` }, 'the station, after a published gauging')),
        h('dt', 'expires'), h('dd', 'unsigned after 6 h')),
      h('a.btn.primary.wbtn', { href: '/review/' }, 'Open the review queue'),
      h('p.lk', 'The agent has no path to SIGNED.'));
  } else {
    fill(box,
      h('div.wstate', h('span.chip.plain', 'NO OPEN DRAFT')),
      h('p.prose.wexp', 'After each published gauging the station runs a stage nowcast. When the chance of crossing the threshold within 3 h reaches 0.5, it drafts a warning and the workflow stops until a hydrologist signs or rejects it.'),
      signed.length ? h('p.wlast.mono', `last signed: ${clipTime(signed[0].created)} · ${signed.length} on record`) : null,
      h('a.btn.wbtn', { href: '/review/' }, 'Review queue'));
  }
}

function runRows(runs) {
  if (!runs.length) return h('p.empty', 'No runs yet.');
  const body = runs.map(r => {
    const d = r.decision || {}, k = r.status === 'RUNNING' ? 'RUNNING' : d.kind || r.status;
    const pub = k === 'PUBLISH';
    const href = `/run/?id=${encodeURIComponent(r.run_id)}`;
    const reasons = (d.reasons || []).join(' · ');
    return h('tr',
      h('td.strong', { 'data-l': 'clip' }, h('a', { href }, clipTime(r.t))),
      h('td', { 'data-l': 'decision' }, chip(k)),
      h('td.r', { 'data-l': 'Q' }, d.q != null ? (pub ? h('span.qv', sig(d.q)) : h('span.est', { title: 'unpublished estimate, kept for reviewers' }, `est. ${sig(d.q)}`)) : '–', d.q != null && pub ? h('span.rng', ` ${qRange(d.q_lo, d.q_hi)}`) : null),
      h('td', { 'data-l': 'method' }, d.method || '–'),
      h('td', { 'data-l': 'brain' }, h('span', { class: `tag ${r.brain}` }, r.brain || '–'), r.model ? h('span.model', ` ${r.model}`) : null),
      h('td.r', { 'data-l': 'gate' }, r.policy_denials ? h('span.k-esc', `${r.policy_denials} denied`) : r.status === 'RUNNING' ? '…' : '0 denied'),
      h('td.r', { 'data-l': 'took' }, r.latency_s != null ? `${Math.round(r.latency_s)} s` : r.status === 'RUNNING' ? ago(r.started) : '–'),
      h('td.reason', { 'data-l': 'reasons', title: reasons }, reasons ? (reasons.length > 150 ? reasons.slice(0, 148) + '…' : reasons) : r.status === 'RUNNING' ? 'running on the worker' : '–'),
      h('td.r.go', h('a', { href, 'aria-label': `Open the trace of run ${r.run_id}` }, 'trace →')));
  });
  return h('div.tscroll', h('table.dtable.runs-t',
    h('caption.sr-only', 'Agent runs on the River Dart station, newest first'),
    h('thead', h('tr', ['Clip time (2018)', 'Decision', 'Q m³/s, 95 %', 'Method', 'Brain', 'Gate', 'Took', 'Reasons', ''].map((x, i) => h('th', { scope: 'col', class: [2, 5, 6, 8].includes(i) ? 'r' : null }, x)))),
    h('tbody', body)));
}
function renderRuns() {
  const runs = state.runsAll.slice(0, 30);
  fill($('#runs-list'), runRows(runs));
  const n = state.runsAll.length, c = { PUBLISH: 0, HOLD: 0, ESCALATE: 0 };
  for (const r of state.runsAll) { const k = (r.decision || {}).kind; if (k in c) c[k]++; }
  $('#runs-meta').textContent = `last ${Math.min(30, n)} of ${n} loaded · ${c.PUBLISH} published, ${c.HOLD} held, ${c.ESCALATE} escalated`;
}

function renderRunButton() {
  const btn = $('#run-btn'), latest = state.runsAll[0];
  if (!latest || !latest.clip_key) { btn.disabled = true; $('#run-clip').textContent = 'No clip available yet.'; return; }
  $('#run-clip').textContent = `clip ${clipTime(latest.t)} · ${latest.clip_key.split('/').pop()}`;
  btn.disabled = false;
  btn.onclick = async () => {
    const msg = $('#run-msg');
    btn.disabled = true;
    fill(btn, h('span.spin', { 'aria-hidden': 'true' }), 'Starting the execution…');
    fill(msg);
    try {
      const { data } = await api.startRun({ station_id: STATION, clip_key: latest.clip_key, brain: 'llm' });
      fill(btn, 'Opening the trace…');
      location.href = `/run/?id=${encodeURIComponent(data.run_id)}${data.deduplicated ? '&joined=1' : ''}`;
    } catch (err) {
      btn.disabled = false;
      fill(btn, 'Run the agent on the latest clip');
      if (err instanceof ApiError && err.status === 429) fill(msg, h('p.notice.warn', h('b', 'Today\'s 40 runs are used up. '), 'The cap resets at midnight UTC. Every earlier run below has its full trace.'));
      else fill(msg, h('p.notice.err', 'The run did not start: ', errorText(err)));
    }
  };
}

/* ---------- data ---------- */
async function latestPhysics() {
  // A nowcast is only drawn when its anchor agrees with the stage series at that clip time:
  // the most recently processed run first, then the latest clip in time.
  const done = state.runsAll.filter(x => x.status !== 'RUNNING' && x.t);
  const byStarted = done.slice(0, 5);
  const byClip = done.slice().sort((a, b) => t(b.t) - t(a.t)).slice(0, 2);
  const seen = new Set(), cands = [...byStarted, ...byClip].filter(r => !seen.has(r.run_id) && seen.add(r.run_id));
  const lv = new Map((state.station.levels || []).map(([x, v]) => [t(x), +v]));
  let fallback = null;
  for (const r of cands) {
    let tr = null;
    try { tr = await api.trace(r.run_id); } catch { tr = null; }
    if (!tr || !tr.physics) continue;
    if (!fallback) fallback = { run: r, physics: tr.physics };
    const f0 = (tr.physics.forecast || [])[0];
    const h0 = f0 ? lv.get(t(f0.t)) : undefined;
    if (f0 && h0 != null && Math.abs(+f0.h - h0) < 0.08) return { run: r, physics: tr.physics };
  }
  return fallback ? { run: fallback.run, physics: { ...fallback.physics, forecast: [] } } : { run: done[0] || null, physics: null };
}

async function load(first) {
  try {
    const [station, g, runs] = await Promise.all([api.station(STATION), api.gaugings(STATION, 500), api.runs(STATION, 200)]);
    state.station = station; state.gaugings = g.gaugings || []; state.runsAll = runs.runs || [];
  } catch (err) {
    if (first) for (const id of ['#hydro', '#rating', '#evidence', '#runs-list']) fill($(id), h('p.notice.err', 'The station API could not be reached: ', errorText(err)));
    return;
  }
  const lp = await latestPhysics();
  state.latest = lp.run; state.physics = lp.physics;
  const eps = episodes(state.station.levels || []);
  const prev = state.episode;
  state.episodes = eps;
  const lt = state.latest ? t(state.latest.t) : null;
  state.episode = (prev && eps.find(e => e.key === prev.key)) || eps.find(e => e.storm) || eps.find(e => lt != null && lt >= e.t0 && lt <= e.t1) || eps[eps.length - 1] || null;
  renderHeader(); renderStats(); renderEpisodes(); drawHydro(); drawRating(); renderEvidence(); renderRuns(); renderRunButton();
  if (first) renderWarning();
  if (first && location.hash === '#runs') $('#runs').scrollIntoView();
}

load(true);
setInterval(() => { if (!document.hidden) { load(false); renderWarning(); } }, 45000);
