// Small DOM, SVG and formatting helpers shared by the app pages.

/** h('div.panel#id', {attrs}, ...children): a tiny element builder. Strings become text nodes. */
export function h(tag, attrs, ...kids) {
  const m = /^([a-z0-9-]+)?((?:[.#][\w-]+)*)$/i.exec(tag);
  const el = document.createElement(m[1] || 'div');
  for (const part of m[2].match(/[.#][\w-]+/g) || []) {
    if (part[0] === '.') el.classList.add(part.slice(1)); else el.id = part.slice(1);
  }
  if (attrs && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) { kids.unshift(attrs); attrs = null; }
  for (const k in attrs || {}) {
    const v = attrs[k];
    if (v == null || v === false) continue;
    if (k === 'class') el.className += (el.className ? ' ' : '') + v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  append(el, kids);
  return el;
}
function append(el, kids) {
  for (const k of kids) {
    if (k == null || k === false) continue;
    if (Array.isArray(k)) append(el, k);
    else el.appendChild(k instanceof Node ? k : document.createTextNode(String(k)));
  }
}
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export function fill(el, ...kids) { el.replaceChildren(); append(el, kids); return el; }

const NS = 'http://www.w3.org/2000/svg';
/** s('line', {x1:..}, parent): an SVG element builder. */
export function s(tag, attrs, parent) {
  const el = document.createElementNS(NS, tag);
  for (const k in attrs || {}) if (attrs[k] != null) el.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(el);
  return el;
}
export function svgText(parent, x, y, text, attrs = {}) {
  const t = s('text', { x, y, ...attrs }, parent);
  t.textContent = text;
  return t;
}
export function svgTitle(parent, text) { const t = s('title', {}, parent); t.textContent = text; return t; }

/* ---------- numbers ---------- */
export function sig(x, n = 3) {
  if (x == null || !Number.isFinite(+x)) return '–';
  x = +x;
  if (x === 0) return '0';
  const d = Math.max(0, n - 1 - Math.floor(Math.log10(Math.abs(x))));
  return x.toFixed(Math.min(d, 4));
}
export const fixed = (x, d = 2) => (x == null || !Number.isFinite(+x) ? '–' : (+x).toFixed(d));
export const pct = (x, d = 1, signed = true) => (x == null || !Number.isFinite(+x) ? '–' : (signed && x > 0 ? '+' : x < 0 ? '−' : '') + Math.abs(+x).toFixed(d) + '%');
export const qText = q => (q == null ? '–' : `${sig(q)} m³/s`);
export function qRange(lo, hi) {
  if (lo == null || hi == null) return '';
  const a = Math.min(lo, hi), b = Math.max(lo, hi);
  return `${sig(a)}–${sig(b)}`;
}
export function secs(ms) {
  if (ms == null) return '–';
  return ms >= 1000 ? `${(ms / 1000).toFixed(ms >= 10000 ? 1 : 2)} s` : `${Math.round(ms)} ms`;
}
export const m3 = 'm³/s';

/* ---------- time ---------- */
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const pad = n => String(n).padStart(2, '0');
export const parseT = t => (t ? new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(t) ? t : t + 'Z') : null);
/** Clip times are the 2018 timestamps of the archived footage, shown as recorded (UTC). */
export function clipTime(t, { date = true, time = true } = {}) {
  const d = typeof t === 'string' ? parseT(t) : t;
  if (!d || Number.isNaN(+d)) return '–';
  const D = `${d.getUTCDate()} ${MON[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
  const T = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
  return date && time ? `${D} · ${T}` : date ? D : T;
}
export const hhmm = t => clipTime(t, { date: false });
export const dayMon = d => `${d.getUTCDate()} ${MON[d.getUTCMonth()]}`;
/** Wall-clock times (when a run actually executed) as "3 min ago". */
export function ago(t) {
  const d = typeof t === 'string' ? parseT(t) : t;
  if (!d) return '–';
  const s = Math.round((Date.now() - d) / 1000);
  if (s < 0) return 'just now';
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}
export function wall(t) {
  const d = typeof t === 'string' ? parseT(t) : t;
  if (!d) return '–';
  return d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

/* ---------- decisions ---------- */
export const KIND = {
  PUBLISH: { cls: 'pub', word: 'Publish' },
  HOLD: { cls: 'hold', word: 'Hold' },
  ESCALATE: { cls: 'esc', word: 'Escalate' },
  RUNNING: { cls: 'run', word: 'Running' },
  DRAFT: { cls: 'draft', word: 'Draft' },
  SIGNED: { cls: 'pub', word: 'Signed' },
  REJECTED: { cls: 'esc', word: 'Rejected' },
  EXPIRED: { cls: 'hold', word: 'Expired' },
};
export function chip(kind, extra = '') {
  const k = KIND[kind] || { cls: '', word: kind || '–' };
  return h('span', { class: `chip ${k.cls} ${extra}`.trim() }, (kind || '–').toUpperCase());
}
export const kindClass = kind => (KIND[kind] || {}).cls || '';

/* ---------- misc ---------- */
export function debounce(fn, ms = 150) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
/** Re-render a chart when its container changes width. */
export function onWidth(el, fn) {
  let w = 0;
  const run = () => { const nw = Math.round(el.clientWidth); if (nw && nw !== w) { w = nw; fn(nw); } };
  const ro = new ResizeObserver(debounce(run, 120));
  ro.observe(el);
  run();
  return () => ro.disconnect();
}
export const sleep = ms => new Promise(r => setTimeout(r, ms));
export const qs = name => new URLSearchParams(location.search).get(name);
export function errorText(err) {
  if (!err) return 'Something went wrong.';
  if (err.status === 0) return err.message;
  const b = err.body || {};
  return `${b.message || b.error || err.message} (HTTP ${err.status})`;
}
/** Reveal-on-scroll with a CSS failsafe; everything shows at once under reduced motion. */
export function reveal() {
  const els = document.querySelectorAll('[data-reveal]');
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (still || !('IntersectionObserver' in window)) { els.forEach(e => e.classList.add('in')); return; }
  const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }), { rootMargin: '0px 0px -8% 0px' });
  els.forEach(e => io.observe(e));
}
