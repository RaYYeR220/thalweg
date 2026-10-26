// Performance: COOL vs the stock OpenCV 5 wheel on Graviton4 vs x86, from performance.json.
import { api } from './api.js';
import { h, $, fill, s, svgText, svgTitle, fixed, onWidth, errorText } from './ui.js';

const BUILDS = [
  { key: 'c8g-large-cool', name: 'Graviton4 · COOL', sub: 'c8g.large · OpenCV 5.1.0-dev' },
  { key: 'c8g-large-pip', name: 'Graviton4 · stock wheel', sub: 'c8g.large · opencv-python-headless 5.0' },
  { key: 'c7i-large-pip-x86', name: 'x86 · stock wheel', sub: 'c7i.large · opencv-python-headless 5.0' },
];
const STAGES = [['load', '#6e5c46'], ['stabilize', '#c9bfae'], ['orthorectify', '#4a3c2b'], ['diagnose', '#8f7f68'], ['coarse_motion', '#b3a48c'], ['lspiv', '#9fe3e6'], ['stiv', '#5aa9ad']];
const mono = { 'font-family': 'IBM Plex Mono, ui-monospace, monospace' };
let P = null;

function stackBars(box, W) {
  const rows = BUILDS.filter(b => P[b.key]);
  const narrow = W < 560;
  const lab = narrow ? 12 : 190, rowH = narrow ? 64 : 50, top = 6;
  const Hd = top + rows.length * rowH + 26;
  const max = Math.max(...rows.map(b => P[b.key].pipeline.median_total_s)) * (narrow ? 1 : 1.12);
  const X = v => lab + v / max * (W - lab - (narrow ? 58 : 8));
  const svg = s('svg', { viewBox: `0 0 ${W} ${Hd}`, width: W, height: Hd, role: 'img', 'aria-label': 'Seconds per clip for each build, split by pipeline stage' });
  const g = s('g', { ...mono, 'font-size': 11 }, svg);
  for (let t = 0; t <= max; t += 10) { s('line', { x1: X(t), x2: X(t), y1: top, y2: Hd - 22, stroke: 'rgba(237,230,218,.08)' }, g); svgText(g, X(t), Hd - 6, `${t} s`, { 'text-anchor': 'middle', fill: 'rgba(237,230,218,.5)' }); }
  rows.forEach((b, i) => {
    const p = P[b.key].pipeline, y = top + i * rowH + (narrow ? 22 : 8), bh = 22;
    svgText(g, 0, narrow ? y - 7 : y + 15, b.name, { fill: '#ede6da', 'font-size': 12 });
    let x0 = 0;
    for (const [k, col] of STAGES) {
      const v = p.median_stages_s[k] || 0; if (!v) continue;
      const r = s('rect', { x: X(x0) + 0.5, y, width: Math.max(0.5, X(x0 + v) - X(x0) - 1), height: bh, fill: col, rx: 2 }, g);
      svgTitle(r, `${b.name} · ${k.replace('_', ' ')}: ${fixed(v, 2)} s`);
      if (X(x0 + v) - X(x0) > 44) svgText(g, (X(x0) + X(x0 + v)) / 2, y + 15, k === 'stabilize' ? 'stab.' : k, { 'text-anchor': 'middle', fill: '#14100b', 'font-size': 10 });
      x0 += v;
    }
    const other = p.median_total_s - x0;
    if (other > 0.05) { const r = s('rect', { x: X(x0) + 0.5, y, width: Math.max(0.5, X(x0 + other) - X(x0) - 1), height: bh, fill: 'rgba(237,230,218,.16)', rx: 2 }, g); svgTitle(r, `other: ${fixed(other, 2)} s`); }
    svgText(g, X(p.median_total_s) + 8, y + 15, `${fixed(p.median_total_s, 1)} s`, { fill: '#ede6da', 'font-weight': 500 });
  });
  fill(box, svg);
}

function usdBars(box) {
  const rows = BUILDS.filter(b => P[b.key]);
  const max = Math.max(...rows.map(b => P[b.key].usd_per_1000_gaugings));
  const best = Math.min(...rows.map(b => P[b.key].usd_per_1000_gaugings));
  fill(box, rows.map(b => {
    const r = P[b.key];
    return h('div.ubar',
      h('div.ul', h('span', b.name), h('b', { class: r.usd_per_1000_gaugings === best ? 'k-ice' : '' }, `$${fixed(r.usd_per_1000_gaugings, 3)}`)),
      h('div.ut', h('i', { style: `width:${(r.usd_per_1000_gaugings / max * 100).toFixed(1)}%`, class: r.usd_per_1000_gaugings === best ? 'best' : '' })),
      h('p.us', `${fixed(r.clips_per_hour, 1)} clips/h · $${fixed(r.usd_per_hour, 4)}/h`));
  }));
}

function opsTable() {
  const rows = BUILDS.filter(b => P[b.key]);
  const ops = Object.keys(P[rows[0].key].ops_ms);
  const head = h('tr', h('th', { scope: 'col' }, 'operation'), rows.map(b => h('th.r', { scope: 'col' }, b.name)), h('th.r', { scope: 'col' }, 'COOL vs stock'), h('th.r', { scope: 'col' }, 'x86 vs Graviton4'));
  const body = ops.map(op => {
    const v = rows.map(b => P[b.key].ops_ms[op]);
    const min = Math.min(...v);
    const coolVsPip = v[0] / v[1], x86VsArm = v[2] / v[1];
    return h('tr', h('td.strong', h('code', op)), v.map(x => h('td.r', { class: x === min ? 'best' : null }, fixed(x, 3))),
      h('td.r', ratio(coolVsPip)), h('td.r', ratio(x86VsArm)));
  });
  fill($('#ops'), h('div.tscroll', h('table.dtable.optable', h('caption.sr-only', 'Per-operation timings in milliseconds'), h('thead', head), h('tbody', body))),
    h('p.note-sm', opsNote(rows, ops)));
}
function opsNote(rows, ops) {
  const r = (a, b, op) => P[a].ops_ms[op] / P[b].ops_ms[op];
  const list = xs => xs.map(o => (o.startsWith('dis_') ? 'DIS flow' : o.split('_')[0])).join(', ') || 'none';
  const coolFast = ops.filter(o => r(rows[0].key, rows[1].key, o) < 0.95), coolSlow = ops.filter(o => r(rows[0].key, rows[1].key, o) > 1.05);
  const x86Fast = ops.filter(o => r(rows[2].key, rows[1].key, o) < 0.95);
  return `Ratios above 1 mean the first build is slower; within 5 % counts as even. COOL is faster than the stock wheel on ${list(coolFast)}, slower on ${list(coolSlow)}, and even on the rest. x86 beats Graviton4 only on ${list(x86Fast)}.`;
}
function ratio(r) { return h('span', { class: r > 1.05 ? 'slow' : r < 0.95 ? 'fast' : 'even' }, `${fixed(r, 2)}×`); }

function proofs() {
  fill($('#proofs'), BUILDS.filter(b => P[b.key]).map(b => {
    const e = P[b.key].env;
    const pick = re => (e.build || []).find(l => re.test(l));
    const clean = l => (l ? l.split(':').slice(1).join(':').trim() : '–');
    return h('article', { class: `proof ${b.key.includes('cool') ? 'cool' : ''}` },
      h('p.chap', b.name),
      h('dl.kv',
        h('dt', 'cv2'), h('dd', e.cv2_version),
        h('dt', 'cv2.__file__'), h('dd', h('code', e.cv2_file)),
        h('dt', 'baseline'), h('dd', clean(pick(/^Baseline/))),
        h('dt', 'dispatched'), h('dd', clean(pick(/^Dispatched/)) || '–'),
        h('dt', 'custom HAL'), h('dd', clean(pick(/^Custom HAL/))),
        h('dt', 'instance'), h('dd', `${e.instance_type} · ${e.machine} · ${e.threads} threads`),
        h('dt', 'AMI'), h('dd', h('code', e.ami_id)),
        h('dt', 'product code'), h('dd', e.product_codes ? h('code', e.product_codes) : 'none (stock AMI)')));
  }));
}

function findings() {
  const c = P['c8g-large-cool'], p = P['c8g-large-pip'], x = P['c7i-large-pip-x86'];
  if (!c || !p || !x) return;
  fill($('#f1'), `${fixed(c.pipeline.median_total_s, 1)} s `, h('span', 'against'), ` ${fixed(p.pipeline.median_total_s, 1)} s`);
  fill($('#f2'), `$${fixed(p.usd_per_1000_gaugings, 2)} `, h('span', 'against'), ` $${fixed(x.usd_per_1000_gaugings, 2)}`);
  const save = (1 - p.usd_per_1000_gaugings / x.usd_per_1000_gaugings) * 100;
  const saveCool = (1 - c.usd_per_1000_gaugings / x.usd_per_1000_gaugings) * 100;
  $('#f2t').textContent = `Per 1000 gaugings with the same wheel, Graviton4 costs ${fixed(save, 0)} % less than x86 (c7i.large). COOL's AMI adds a software fee, which brings its cost to $${fixed(c.usd_per_1000_gaugings, 2)}, ${fixed(saveCool, 1)} % below x86.`;
}

async function main() {
  try {
    P = await api.publicJSON('performance.json');
  } catch (err) {
    for (const id of ['#bars-s', '#bars-usd', '#ops', '#proofs']) fill($(id), h('p.notice.err', 'performance.json could not be loaded: ', errorText(err)));
    return;
  }
  findings();
  const bs = $('#bars-s');
  onWidth(bs, w => stackBars(bs, w));
  fill($('#stage-legend'), STAGES.map(([k, col]) => h('span', h('i.dot', { style: `background:${col}` }), k.replace('_', ' '))), h('span', h('i.dot', { style: 'background:rgba(237,230,218,.3)' }), 'other'));
  usdBars($('#bars-usd'));
  opsTable();
  proofs();
}
main();
