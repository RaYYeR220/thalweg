// Landing figures: drawn from the measured fields of one development run on VGC video 8.
import { s as el, svgText, reveal } from './ui.js';

const FOAM = '#ede6da', ICE = '#9fe3e6';
const IMG = { frame: '/assets/img/frame.jpg', ortho: '/assets/img/ortho.jpg', sti: '/assets/img/sti.jpg' };
const mono = { 'font-family': 'IBM Plex Mono, ui-monospace, monospace' };

requestAnimationFrame(() => document.body.classList.add('ready'));
reveal();

/* bilinear sampler over a gridded field (null cells are invalid vectors) */
function sampler(g) {
  const { x0, y0, dx, dy, nx, ny, u, v, s } = g;
  const f = (X, Y) => {
    const fx = (X - x0) / dx, fy = (Y - y0) / dy;
    let i = Math.floor(fx), j = Math.floor(fy);
    if (fx < -0.5 || fy < -0.5 || fx > nx - 0.5 || fy > ny - 0.5) return null;
    i = Math.max(0, Math.min(nx - 2, i)); j = Math.max(0, Math.min(ny - 2, j));
    const tx = Math.max(0, Math.min(1, fx - i)), ty = Math.max(0, Math.min(1, fy - j));
    let su = 0, sv = 0, ss = 0, w = 0;
    const W = [(1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty];
    const O = [j * nx + i, j * nx + i + 1, (j + 1) * nx + i, (j + 1) * nx + i + 1];
    for (let k = 0; k < 4; k++) { const o = O[k]; if (u[o] == null) continue; su += u[o] * W[k]; sv += v[o] * W[k]; ss += s[o] * W[k]; w += W[k]; }
    if (w < 0.55) return null;
    return [su / w, sv / w, ss / w, w];
  };
  f.bounds = { x0, y0, x1: x0 + (nx - 1) * dx, y1: y0 + (ny - 1) * dy };
  return f;
}

function homography(H) {
  const apply = (M, x, y) => { const w = M[2][0] * x + M[2][1] * y + M[2][2]; return [(M[0][0] * x + M[0][1] * y + M[0][2]) / w, (M[1][0] * x + M[1][1] * y + M[1][2]) / w]; };
  return { fwd: (X, Y) => apply(H, X, Y) };
}

/* static arrows into an SVG; toScreen maps world metres to svg units */
function arrows(svg, g, toScreen, o = {}) {
  const grp = el('g', {}, svg);
  const every = o.every || 1, len = o.len || 0.18;
  for (let j = 0; j < g.ny; j += every) for (let i = 0; i < g.nx; i += every) {
    const k = j * g.nx + i; if (g.u[k] == null) continue;
    const X = g.x0 + i * g.dx, Y = g.y0 + j * g.dy, u = g.u[k], v = g.v[k], sp = Math.hypot(u, v);
    const a = toScreen(X, Y), b = toScreen(X + u * len, Y + v * len);
    const ang = Math.atan2(b[1] - a[1], b[0] - a[0]), L = Math.hypot(b[0] - a[0], b[1] - a[1]), hl = Math.min(L * 0.38, o.head || 3.2);
    const f = n => n.toFixed(1);
    el('path', { d: `M${f(a[0])},${f(a[1])}L${f(b[0])},${f(b[1])}M${f(b[0] - hl * Math.cos(ang - 0.45))},${f(b[1] - hl * Math.sin(ang - 0.45))}L${f(b[0])},${f(b[1])}L${f(b[0] - hl * Math.cos(ang + 0.45))},${f(b[1] - hl * Math.sin(ang + 0.45))}`,
      stroke: FOAM, 'stroke-width': o.sw || 1, fill: 'none', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', opacity: Math.min(1, 0.35 + sp / 1.4 * 0.65) }, grp);
  }
}

/* cross-section of the surveyed transect with the surface-velocity profile */
function section(svg, P, o) {
  const Wd = o.w, Hd = o.h, m = { l: 46, r: 20, t: 22, b: 36 };
  svg.setAttribute('viewBox', `0 0 ${Wd} ${Hd}`);
  const c = o.colors, g = el('g', { ...mono, 'font-size': 11 }, svg);
  const ys = P.transect.map(p => p[1]), zs = P.transect.map(p => p[2]);
  const yMin = Math.min(...ys), yMax = Math.max(...ys), ch = y => yMax - y;
  const L = yMax - yMin, vH = (Hd - m.t - m.b) * 0.42, dH = (Hd - m.t - m.b) - vH - 14;
  const X = d => m.l + d / L * (Wd - m.l - m.r);
  const vmax = o.vmax, Yv = v => m.t + vH - v / vmax * vH;
  const z0 = P.level, zmin = Math.min(...zs) - 0.05, zmax = 0.1, Yz = z => m.t + vH + 14 + (zmax - z) / (zmax - zmin) * dH;
  const bed = P.transect.map(p => [X(ch(p[1])), Yz(p[2])]);
  const pts = a => a.map(p => p.map(n => n.toFixed(1)).join(',')).join(' L');
  el('path', { d: `M${X(ch(yMax))},${Yz(z0)} L${pts(bed)} L${X(ch(yMin))},${Yz(z0)} Z`, fill: c.water }, g);
  el('path', { d: 'M' + pts(bed), stroke: c.bed, 'stroke-width': 1.4, fill: 'none' }, g);
  el('line', { x1: m.l, x2: Wd - m.r, y1: Yz(z0), y2: Yz(z0), stroke: c.surface, 'stroke-width': 1 }, g);
  svgText(g, Wd - m.r, Yz(z0) - 6, 'water level 0.00 m', { 'text-anchor': 'end', fill: c.dim });
  for (const p of P.transect) el('line', { x1: X(ch(p[1])), x2: X(ch(p[1])), y1: Yz(p[2]), y2: Yz(p[2]) + 5, stroke: c.bed }, g);
  el('line', { x1: m.l, x2: Wd - m.r, y1: Yv(0), y2: Yv(0), stroke: c.grid }, g);
  for (const v of [0.5, 1, 1.5, 2]) { el('line', { x1: m.l, x2: Wd - m.r, y1: Yv(v), y2: Yv(v), stroke: c.grid, 'stroke-dasharray': '1 3' }, g); svgText(g, m.l - 7, Yv(v) + 3.5, v.toFixed(1), { 'text-anchor': 'end', fill: c.faint }); }
  svgText(g, m.l, m.t - 8, 'surface velocity on the section, m/s', { fill: c.dim });
  const ls = sampler(P.lspiv); let d = '', run = [];
  const flush = () => { if (run.length >= 6) d += 'M' + run.join('L'); run = []; };
  for (let y = yMax; y >= yMin; y -= 0.02) { const f = ls(2.2, y); if (f) run.push(X(ch(y)).toFixed(1) + ',' + Yv(Math.hypot(f[0], f[1])).toFixed(1)); else flush(); }
  flush();
  el('path', { d, stroke: c.lspiv, 'stroke-width': 1.8, fill: 'none', 'stroke-linejoin': 'round' }, g);
  const st = P.stiv;
  for (let k = 0; k < st.x.length; k++) { if (!st.valid[k]) continue; el('circle', { cx: X(ch(st.y[k])), cy: Yv(Math.hypot(st.u[k], st.v[k])), r: 2.2, fill: 'none', stroke: c.stiv, 'stroke-width': 1.1 }, g); }
  for (let x = 0; x <= L + 1e-6; x += 0.5) { el('line', { x1: X(x), x2: X(x), y1: Hd - m.b + 2, y2: Hd - m.b + 6, stroke: c.faint }, g); svgText(g, X(x), Hd - m.b + 18, x.toFixed(1), { 'text-anchor': 'middle', fill: c.faint }); }
  svgText(g, Wd - m.r, Hd - 5, 'chainage across the section, m', { 'text-anchor': 'end', fill: c.faint });
}

async function figures() {
  let P;
  try {
    const r = await fetch('/assets/data/field-vgc8.json');
    if (!r.ok) throw new Error(r.status);
    P = await r.json();
  } catch {
    return; // the figures stay as plain frames; the text carries the chapter
  }
  const Hm = homography(P.H);
  const toOrtho = (X, Y) => [(X - P.grid.x0) / P.grid.res_m - 0.5, (P.grid.y1 - Y) / P.grid.res_m - 0.5];

  // fig 1: static bank mask
  const sStab = document.getElementById('figStab');
  const pat = el('pattern', { id: 'hatch', width: 16, height: 16, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)' }, el('defs', {}, sStab));
  el('line', { x1: 0, y1: 0, x2: 0, y2: 16, stroke: FOAM, 'stroke-width': 2, opacity: 0.5 }, pat);
  el('image', { href: IMG.frame, width: 1920, height: 1080 }, sStab);
  for (const poly of P.static_mask) el('polygon', { points: poly.map(p => p.join(',')).join(' '), fill: 'url(#hatch)', stroke: FOAM, 'stroke-width': 2.5, 'stroke-opacity': 0.8 }, sStab);

  // fig 2: oblique + ortho
  const o = document.getElementById('figObl'), r = document.getElementById('figOrtho');
  el('image', { href: IMG.frame, width: 1920, height: 1080 }, o);
  el('image', { href: IMG.ortho, width: 525, height: 575 }, r);
  const b = sampler(P.lspiv).bounds, foot = [[b.x0, b.y0], [b.x1, b.y0], [b.x1, b.y1], [b.x0, b.y1]];
  el('polygon', { points: foot.map(p => Hm.fwd(...p).join(',')).join(' '), fill: 'rgba(159,227,230,.08)', stroke: ICE, 'stroke-width': 3.5, 'stroke-dasharray': '12 9' }, o);
  el('polygon', { points: foot.map(p => toOrtho(...p).join(',')).join(' '), fill: 'rgba(159,227,230,.06)', stroke: ICE, 'stroke-width': 1.3, 'stroke-dasharray': '5 4' }, r);
  for (let X = 0; X <= 3.9; X += 0.5) { const a = toOrtho(X, 0.3), z = toOrtho(X, -4.3); el('line', { x1: a[0], x2: z[0], y1: a[1], y2: z[1], stroke: 'rgba(237,230,218,.22)', 'stroke-width': 0.7 }, r); }
  for (let Y = -4.0; Y <= 0.3; Y += 0.5) { const a = toOrtho(-0.3, Y), z = toOrtho(3.9, Y); el('line', { x1: a[0], x2: z[0], y1: a[1], y2: z[1], stroke: 'rgba(237,230,218,.22)', 'stroke-width': 0.7 }, r); }
  P.grp.forEach((g, i) => { el('circle', { cx: g.i, cy: g.j, r: 18, fill: 'none', stroke: FOAM, 'stroke-width': 4 }, o); svgText(o, g.i + 26, g.j - 20, String(i + 1), { ...mono, 'font-size': 40, fill: FOAM }); });

  // fig 3: three methods
  const vec = (id, g) => { const sv = document.getElementById(id); el('image', { href: IMG.ortho, width: 525, height: 575, opacity: 0.6 }, sv); arrows(sv, g, toOrtho, { every: 2, len: 0.16, sw: 1.1, head: 3 }); };
  vec('figL', P.lspiv); vec('figD', P.dis);
  const st = document.getElementById('figS');
  el('image', { href: IMG.sti, width: 201, height: 300, preserveAspectRatio: 'none' }, st);
  const slope = 0.972 * 0.03332 / 0.008;
  for (let c = -1200; c < 201; c += 64) el('line', { x1: c, y1: 0, x2: c + slope * 300, y2: 300, stroke: ICE, 'stroke-width': 0.9, opacity: 0.75 }, st);

  // fig 4: section
  section(document.getElementById('figSec'), P, { w: 760, h: 300, vmax: 2.0,
    colors: { water: 'rgba(110,92,70,.32)', bed: 'rgba(237,230,218,.62)', surface: ICE, grid: 'rgba(237,230,218,.1)', faint: 'rgba(237,230,218,.5)', dim: 'rgba(237,230,218,.7)', lspiv: FOAM, stiv: ICE } });
}

figures();
