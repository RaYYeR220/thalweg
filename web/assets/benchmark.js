// Benchmark: every hydrologist's answer per video, our two brains, and the refusal suite.
import { api } from './api.js';
import { h, $, $$, fill, s, svgText, svgTitle, sig, fixed, pct, onWidth, errorText, chip } from './ui.js';

const VIDEO = {
  vgc1: { n: 1, name: 'La Vence', where: 'France · ground camera', img: 'vgc1' },
  vgc2: { n: 2, name: 'West Normanby', where: 'Australia · drone', img: 'vgc2' },
  vgc3: { n: 3, name: 'Le Séran', where: 'France · 3D bank camera', img: 'vgc3' },
  vgc4: { n: 4, name: 'Russell River', where: 'Australia · drone', img: 'vgc4' },
  vgc5: { n: 5, name: 'Storåne', where: 'Norway · hand-held', img: 'vgc5' },
  vgc6: { n: 6, name: 'Le Groin', where: 'France · 3D bank camera', img: 'vgc6' },
  vgc7: { n: 7, name: 'Herbert River', where: 'Australia · drone', img: 'vgc7' },
  vgc8: { n: 8, name: 'Finsalbekken', where: 'Norway · hand-held', img: 'vgc8' },
};
const TAGS = { frozen: { auto: 'vgc-autopilot', llm: 'vgc-llm-sonnet' }, v11: { auto: 'vgc-autopilot-v1.1', llm: 'vgc-llm-sonnet-v1.1' } };
const REFUSAL = [['refusal-autopilot', 'autopilot', false], ['refusal-llm-sonnet', 'LLM', false], ['refusal-autopilot-v1.1', 'autopilot', true], ['refusal-llm-sonnet-v1.1', 'LLM', true]];
const TRANSFORM = { none: 'unaltered clip', shake: 'AR(1) jitter, several px per frame', night: 'gain ×0.07 plus noise', glare: '55 % of the frame saturated', frozen: 'one frame repeated', still: 'one frame plus sensor noise', moved: '28 / −18 px shift from the reference view', level: 'sensor level +1.5 m' };
const C = { faint: 'rgba(237,230,218,.5)', grid: 'rgba(237,230,218,.09)', expert: 'rgba(201,191,174,.62)', ice: '#9fe3e6', foam: '#f6f2ea', hold: '#e0a95b', band: 'rgba(159,227,230,.12)' };
const mono = { 'font-family': 'IBM Plex Mono, ui-monospace, monospace' };
let B = null, ver = 'frozen';
const unplots = [];

const err = (q, ref) => (q - ref) / ref * 100;

function rowOf(tag, video) { return ((B.runs[tag] || {}).rows || []).find(r => r.video === video); }

/* ---------- one dot plot ---------- */
function dotPlot(box, W, video) {
  const ex = B.experts[video];
  const a = rowOf(TAGS[ver].auto, video), l = rowOf(TAGS[ver].llm, video);
  const narrow = W < 560;
  const Hd = 118, m = { l: 10, r: 10 }, L = 60;
  const X = v => m.l + (Math.max(-L, Math.min(L, v)) + L) / (2 * L) * (W - m.l - m.r);
  const svg = s('svg', { viewBox: `0 0 ${W} ${Hd}`, width: W, height: Hd, role: 'img', 'aria-label': `Video ${VIDEO[video].n}: ${ex.n} hydrologists' errors against the reference, with our answers` });
  const g = s('g', { ...mono, 'font-size': 10 }, svg);
  const yTop = 22, yMid = 58, yBot = 94;
  // reference band and grid
  const u = ex.u_ref_pct;
  s('rect', { x: X(-u), y: 8, width: X(u) - X(-u), height: Hd - 30, fill: C.band, rx: 2 }, g);
  for (const t of [-50, -25, 0, 25, 50]) {
    s('line', { x1: X(t), x2: X(t), y1: 8, y2: Hd - 22, stroke: t === 0 ? 'rgba(159,227,230,.5)' : C.grid, 'stroke-dasharray': t === 0 ? null : '2 3' }, g);
    if (!narrow || t % 50 === 0) svgText(g, X(t), Hd - 7, `${t > 0 ? '+' : t < 0 ? '−' : ''}${Math.abs(t)} %`, { 'text-anchor': 'middle', fill: C.faint });
  }
  // experts: a simple beeswarm around the middle line
  const errs = ex.experts_q.map(q => ({ q, e: err(q, ex.q_ref) })).sort((p, q) => p.e - q.e);
  const placed = [];
  let below = 0, above = 0;
  for (const p of errs) {
    if (p.e < -L) { below++; continue; }
    if (p.e > L) { above++; continue; }
    const x = X(p.e);
    let k = 0, y = yMid;
    for (; k < 12; k++) { y = yMid + (k % 2 ? 1 : -1) * Math.ceil(k / 2) * 7.5; if (!placed.some(o => Math.abs(o.x - x) < 7.5 && Math.abs(o.y - y) < 7)) break; }
    placed.push({ x, y });
    const c = s('circle', { cx: x, cy: y, r: 3.6, fill: C.expert }, g);
    svgTitle(c, `a hydrologist: ${sig(p.q)} m³/s (${pct(p.e)})`);
  }
  if (below) { svgText(g, X(-L) + 2, yMid - 12, `◂ ${below}`, { fill: C.expert }); svgTitle(g.lastChild, `${below} answers beyond −${L} %, including wrong-sign discharges`); }
  if (above) { svgText(g, X(L) - 2, yMid - 12, `${above} ▸`, { fill: C.expert, 'text-anchor': 'end' }); svgTitle(g.lastChild, `${above} answers beyond +${L} %`); }

  const marker = (row, y, kind) => {
    if (!row) return;
    const published = row.decision === 'PUBLISH';
    const e = published ? row.published.err_pct : row.estimate && row.estimate.err_pct;
    if (e == null) return;
    const x = X(e), off = Math.abs(e) > L;
    const grp = s('g', {}, g);
    s('line', { x1: x, x2: x, y1: y, y2: yMid, stroke: kind === 'auto' ? 'rgba(159,227,230,.4)' : 'rgba(246,242,234,.35)', 'stroke-width': 1 }, grp);
    if (!published) s('circle', { cx: x, cy: y, r: 6, fill: '#14100b', stroke: C.hold, 'stroke-width': 1.5, 'stroke-dasharray': '2 2' }, grp);
    else if (kind === 'auto') s('path', { d: `M${x} ${y - 7}L${x + 7} ${y}L${x} ${y + 7}L${x - 7} ${y}Z`, fill: C.ice, stroke: '#14100b', 'stroke-width': 1 }, grp);
    else s('circle', { cx: x, cy: y, r: 5.5, fill: '#14100b', stroke: C.foam, 'stroke-width': 2 }, grp);
    const label = `${kind === 'auto' ? 'autopilot' : 'LLM'} ${published ? pct(e) : 'escalated'}${off ? ' (off scale)' : ''}`;
    const right = x < W * 0.62;
    svgText(grp, x + (right ? 12 : -12), y + 3.5, label, { 'text-anchor': right ? 'start' : 'end', fill: !published ? C.hold : kind === 'auto' ? C.ice : C.foam });
    svgTitle(grp, published ? `${kind === 'auto' ? 'autopilot' : 'LLM agent'}: published ${sig(row.q_published)} m³/s, ${pct(e)}, rank ${row.published.expert_rank} of ${row.published.n_experts + 1}` : `${kind === 'auto' ? 'autopilot' : 'LLM agent'}: escalated; its unpublished estimate was ${sig(row.q_estimate)} m³/s (${pct(e)})`);
  };
  marker(a, yTop, 'auto');
  marker(l, yBot, 'llm');
  fill(box, svg);
}

function resultLine(row, who) {
  if (!row) return null;
  if (row.decision !== 'PUBLISH') {
    const e = row.estimate && row.estimate.err_pct;
    return h('div.vr', h('span.vw', who), h('span.k-hold', 'escalated'), h('small', e != null ? `est. ${pct(e)}, not published` : 'not published'));
  }
  const p = row.published;
  return h('div.vr', h('span.vw', who), h('b', pct(p.err_pct)), h('small', `rank ${p.expert_rank}/${p.n_experts + 1}${p.within_ref_u ? ' · inside ref. U' : ''}`));
}

function videoRow(video) {
  const ex = B.experts[video], meta = VIDEO[video];
  const a = rowOf(TAGS[ver].auto, video), l = rowOf(TAGS[ver].llm, video);
  const plot = h('div.vplot');
  const row = h('article.vrow', { 'aria-labelledby': `v-${video}` },
    h('div.vmeta',
      h('img', { src: `/assets/img/${meta.img}.jpg`, alt: '', loading: 'lazy', width: 96, height: 72 }),
      h('div',
        h('h3', { id: `v-${video}` }, h('span.vn', String(meta.n)), meta.name),
        h('p', meta.where),
        h('p.mono', `ref. ${sig(ex.q_ref, 4)} m³/s ± ${fixed(ex.u_ref_pct, 1)} %`),
        h('p.mono', `${ex.n} hydrologists · median error ${fixed(ex.median_abs_err_pct, 1)} %`))),
    plot,
    h('div.vres', resultLine(a, 'autopilot'), resultLine(l, 'LLM')));
  unplots.push(onWidth(plot, w => dotPlot(plot, w, video)));
  return row;
}

function summary() {
  const card = (tag, who) => {
    const r = B.runs[tag]; if (!r) return null;
    const sm = r.summary;
    return h('div.bm-card',
      h('p.chap', `${who} · ${r.label}`),
      h('div.stats',
        h('div.stat', h('b', `${fixed(sm.mae_published_pct, 1)} %`), `mean absolute error, ${sm.published} of ${sm.test_videos} published`),
        h('div.stat', h('b', `${sm.beats_median_expert} of ${sm.test_videos}`), 'videos better than the median hydrologist'),
        h('div.stat', h('b', `${Math.round(sm.mean_beats_share * 100)} %`), 'of hydrologists beaten, on average'),
        h('div.stat', h('b', `${sm.within_ref_uncertainty} of ${sm.published}`), 'published values inside the reference uncertainty')));
  };
  fill($('#summary'), card(TAGS[ver].auto, 'Autopilot'), card(TAGS[ver].llm, 'LLM agent'));
}

function videos() {
  while (unplots.length) unplots.pop()();
  fill($('#videos'), ['vgc2', 'vgc3', 'vgc4', 'vgc5', 'vgc6', 'vgc7'].filter(v => B.experts[v]).map(videoRow));
  fill($('#devvideos'), ['vgc1', 'vgc8'].filter(v => B.experts[v]).map(v => { const r = videoRow(v); r.classList.add('dev'); return r; }));
}

function refusal() {
  const runs = REFUSAL.filter(([t]) => B.refusal[t]);
  const cases = B.refusal[runs[0][0]].rows;
  const head = h('tr', h('th', { scope: 'col' }, 'Case'), h('th', { scope: 'col' }, 'What was done to the clip'), h('th', { scope: 'col' }, 'Must reach'),
    runs.map(([t, who, v11]) => h('th', { scope: 'col', class: v11 ? 'v11' : null }, who, h('span.sub', v11 ? 'v1.1 · not blind' : 'frozen'))));
  const body = cases.map(c => h('tr',
    h('td.strong', h('code', c.case)),
    h('td', TRANSFORM[c.transform] || c.transform),
    h('td', c.expected.map(k => chip(k))),
    runs.map(([t, , v11]) => {
      const r = B.refusal[t].rows.find(x => x.case === c.case);
      if (!r) return h('td', '–');
      const reason = (r.reasons || [])[0] || '';
      return h('td', { class: `rc ${r.correct ? 'ok' : 'miss'} ${v11 ? 'v11' : ''}`, title: reason },
        chip(r.decision), h('span.mk', { 'aria-label': r.correct ? 'correct' : 'not the expected decision' }, r.correct ? '✓' : '✗'));
    })));
  const foot = [
    h('tr.tot', h('th', { scope: 'row', colspan: 3 }, 'Correct decisions'), runs.map(([t, , v11]) => h('td', { class: v11 ? 'v11' : null }, `${B.refusal[t].correct} / ${B.refusal[t].cases}`))),
    h('tr.tot.wp', h('th', { scope: 'row', colspan: 3 }, 'Wrong publications'), runs.map(([t, , v11]) => h('td', { class: v11 ? 'v11' : null }, h('b', String(B.refusal[t].wrong_publishes))))),
  ];
  fill($('#refusal'), h('div.tscroll', h('table.dtable.rtable', h('caption.sr-only', 'Refusal suite: expected and actual decisions per run'), h('thead', head), h('tbody', body), h('tfoot', foot))),
    h('p.note-sm', 'Hover a decision for the reason the run gave. The frozen misses: a dark clip was escalated as a moved camera, and both hand-held video 8 clips were escalated after stabilization was rejected. All of them were refusals, never a guessed number.'));
}

function setVer(v) {
  ver = v;
  for (const b of $$('#ver button')) b.setAttribute('aria-pressed', String(b.dataset.ver === v));
  $('#ver-note').textContent = v === 'frozen' ? 'The frozen build, tagged eval-freeze before any held-out video was scored.' : 'After fixes prompted by the refusal suite: per-frame stabilization and "unverified" is not "moved".';
  fill($('#ver-banner'), v === 'v11' ? h('p.notice.warn.bm-banner', h('b', 'v1.1 is not blind. '), 'These runs came after we had seen the frozen results. They are shown next to the frozen runs, never in place of them.') : null);
  document.body.classList.toggle('v11', v === 'v11');
  summary(); videos();
}

async function main() {
  try {
    B = await api.publicJSON('benchmark.json');
  } catch (e) {
    for (const id of ['#summary', '#videos', '#refusal']) fill($(id), h('p.notice.err', 'benchmark.json could not be loaded: ', errorText(e)));
    return;
  }
  $('#ver').addEventListener('click', e => { const b = e.target.closest('button[data-ver]'); if (b && b.dataset.ver !== ver) setVer(b.dataset.ver); });
  setVer(new URLSearchParams(location.search).get('v') === '1.1' ? 'v11' : 'frozen');
  refusal();
}
main();
