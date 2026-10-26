// One agent run, live: polls progress.json until done and renders every step of the trace.
import { api, artifactUrl } from './api.js';
import { h, $, fill, sig, fixed, qRange, secs, clipTime, ago, wall, chip, kindClass, qs, sleep, errorText, parseT } from './ui.js';

const id = qs('id');
const POLL_MS = 2000, GIVE_UP_MS = 20 * 60 * 1000;
const view = { rendered: 0, groups: new Set(), done: false, run: null, trace: null, started: Date.now(), notFound: 0 };

/* ---------- what the gate's words mean ---------- */
const GLOSS = [
  [/^methods_disagree/, 'no two methods agree within 15 %'],
  [/^method_not_in_agreeing_pair/, 'the chosen method is not one of the agreeing pair'],
  [/^basis_method_mismatch/, 'the claimed basis names a different method'],
  [/^basis_false:bed_not_visible/, 'static ratio is not above 1.2, so the riverbed is not visible and STIV has no special claim'],
  [/^basis_false:surface_not_seeded/, 'static ratio is above 0.5, so the surface is not well seeded for LSPIV'],
  [/^unknown_basis/, 'publication needs a basis the gate knows'],
  [/^coverage_low/, 'the measured panels cover less than 60 % of the section'],
  [/^valid_vectors_low/, 'fewer than half of the vectors passed the filters'],
  [/^confidence_low/, 'mean correlation score below 0.5 for a single-method basis'],
  [/^geometry_unverified/, 'GRP reprojection error above 0.6 % of the image diagonal'],
  [/^camera_motion_uncorrected/, 'the camera moved and stabilization was rejected'],
  [/^stabilization_residual_high/, 'stabilization residual above 2 px'],
  [/^camera_pose_unverified/, 'the view could not be checked against the surveyed reference'],
  [/^image_quality/, 'image quality blocks velocimetry'],
  [/^frozen_frames/, 'more than 20 % of frames are repeats'],
  [/^no_discharge/, 'no discharge has been computed yet'],
  [/^not_diagnosed/, 'diagnostics have not run'],
  [/^bad_args:missing:(\w+)/, m => `the call has no ${m[1]}; every call must carry one`],
  [/^bad_args:unexpected:(\w+)/, m => `${m[1]} is not an argument of this tool`],
  [/^bad_args:type:(\w+)/, m => `${m[1]} has the wrong type`],
  [/^param_bounds:(\w+)/, m => `${m[1]} is outside its allowed range`],
  [/^prerequisite_missing:(\w+)/, m => `needs ${m[1]} first`],
  [/^no_field:(\w+)/, m => `no ${m[1]} velocity field has been measured`],
  [/^already_decided/, 'the run already has a decision'],
  [/^unknown_tool/, 'no such tool exists'],
];
function gloss(code) {
  for (const [re, g] of GLOSS) { const m = re.exec(code); if (m) return typeof g === 'function' ? g(m) : g; }
  return '';
}

/* ---------- result summaries: the numbers that matter per tool ---------- */
const f3 = v => (typeof v === 'number' ? sig(v) : String(v));
const ms_ = v => `${sig(v)} m/s`;
const px = v => `${sig(v)} px`;
const pc = v => `${Math.round(v * 100)} %`;
const SUM = {
  probe_clip: s => [['frame', `${s.width}×${s.height}`], ['fps', fixed(s.fps, 2)], ['frames', s.frames], ['duration', `${fixed(s.duration_s, 1)} s`], ['matches station', s.matches_station_size ? 'yes' : 'no']],
  load_frames: s => [['frames', s.frames], ['Δt', `${fixed(s.dt_s, 3)} s`], ['duration', `${fixed(s.duration_s, 2)} s`], ['frozen', pc(s.frozen_frac || 0)], ...(s.warnings || []).map(w => ['warning', w])],
  check_camera_pose: s => s.checked === false ? [['checked', 'no'], ['why', String(s.reason || '').replace(/_/g, ' ')]] : Object.entries(s).filter(([, v]) => typeof v !== 'object').map(([k, v]) => [k.replace(/_/g, ' '), f3(v)]),
  stabilize: s => [['method', s.method], ['residual p95', px(s.residual_px_p95)], ['motion p95', px(s.motion_px_p95)], ['min inlier ratio', fixed(s.inlier_ratio_min, 2)], ['keyframes', s.keyframes], ...((s.notes || []).length ? [['notes', s.notes.join(', ')]] : [])],
  orthorectify: s => [['model', s.kind === '3d' ? '3D, solvePnP' : s.kind], ['GRP reprojection', `${fixed(s.reproj_rmse_px, 2)} px RMSE${s.reproj_rmse_rel_diag != null ? ` (${fixed(s.reproj_rmse_rel_diag * 100, 2)} % of diag)` : ''}`], ['resolution', `${Math.round(s.res_m * 1000)} mm/px`], ['ortho', (s.ortho_shape || []).join('×')], ['water px', s.water_px]],
  diagnose: s => [['static ratio', `${fixed(s.static_ratio, 2)}${s.static_ratio > 1.2 ? ' · bed visible (> 1.2)' : s.static_ratio <= 0.5 ? ' · seeded surface (≤ 0.5)' : ''}`, s.static_ratio > 1.2 || s.static_ratio <= 0.5], ['moving energy', fixed(s.moving_energy, 2)], ['tracer density', fixed(s.tracer_density, 2)], ['glare', pc(s.glare_frac || 0)], ['dark', pc(s.dark_frac || 0)], ['contrast', s.contrast], ['flags', (s.flags || []).join(', ') || 'none', (s.flags || []).length > 0]],
  coarse_motion: s => [['speed p50', ms_(s.speed_p50_ms)], ['speed p90', ms_(s.speed_p90_ms)], ['direction', `${fixed(s.direction_deg, 1)}°`], ['coherence', fixed(s.coherence, 2)], ...(s.suggested ? [['suggests', `window ${s.suggested.ia_px} px · search ${s.suggested.search_px} px · step ${s.suggested.pair_step} · STIV ${s.suggested.stiv_window} frames`]] : [])],
  run_lspiv: s => vel(s), run_stiv: s => vel(s), run_dense_flow: s => vel(s),
  compute_discharge: s => [['Q', `${sig(s.q)} m³/s`, true], ['95 %', qRange(s.q_lo, s.q_hi)], ['method', s.method], ['coverage', pc(s.coverage)], ['area', `${sig(s.area)} m²`], ['width', `${sig(s.wetted_width)} m`], ['mean velocity', ms_(s.v_mean)], ['α', s.alpha], ...(s.uncertainty ? [['budget 95 %', `±${fixed(s.uncertainty.total_95 * 100, 1)} % (α ${fixed(s.uncertainty.alpha * 100, 1)}, velocity ${fixed(s.uncertainty.velocity * 100, 1)}, level ${fixed(s.uncertainty.level * 100, 1)})`]] : [])],
  compare_methods: s => [...Object.entries(s.methods || {}).map(([k, v]) => [k, `${sig(v)} m³/s`]), ['spread', `${fixed((s.spread || 0) * 100, 1)} % · tolerance 15 %`, true]],
};
function vel(s) {
  const rej = Object.entries(s.rejected || {}).filter(([, v]) => v).map(([k, v]) => `${v} ${k.replace(/_/g, ' ')}`).join(', ');
  return [['valid', `${s.valid} of ${s.vectors} (${pc(s.valid_frac || 0)})`, true], ['speed p50', ms_(s.speed_p50), true], ['speed p90', ms_(s.speed_p90)], ['mean score', fixed(s.score_mean, 2)], ['direction', `${fixed(s.direction_deg, 1)}°`], ...(rej ? [['rejected', rej]] : [])];
}
function summary(step) {
  const s = step.summary;
  if (!s || typeof s !== 'object') return [];
  if (['publish_gauging', 'hold_and_request_recapture', 'escalate_to_hydrologist'].includes(step.tool)) return [];
  try {
    if (SUM[step.tool]) return SUM[step.tool](s).filter(r => r[1] != null && r[1] !== '');
  } catch { /* fall through to the generic view */ }
  return Object.entries(s).filter(([k, v]) => v != null && typeof v !== 'object' && k !== 'evidence').slice(0, 10).map(([k, v]) => [k.replace(/_/g, ' '), f3(v)]);
}

/* ---------- rendering ---------- */
const ACTOR = { system: 'system', autopilot: 'autopilot', llm: 'LLM' };
function groupHeader(step, run) {
  const brain = step.actor !== 'system';
  const key = brain ? 'brain' : 'system';
  if (view.groups.has(key)) return null;
  view.groups.add(key);
  return h('li.tgroup', h('span', brain ? `The brain decides · ${run.brain === 'llm' ? `LLM${run.model ? ' · ' + run.model : ''}` : 'autopilot'}` : 'Mandatory perception · system'));
}
function evidenceThumbs(keys) {
  if (!keys || !keys.length) return null;
  return h('div.sev', keys.map(k => {
    const url = artifactUrl(k), name = k.split('/').pop();
    const a = h('a.thumb', { href: url, target: '_blank', rel: 'noopener', 'aria-label': `Open ${name}` }, h('img', { src: url, alt: `Evidence: ${name}`, loading: 'lazy', decoding: 'async' }), h('span', name));
    a.addEventListener('click', e => { if (e.metaKey || e.ctrlKey) return; e.preventDefault(); openLightbox(url, name); });
    a.querySelector('img').addEventListener('error', () => a.classList.add('missing'));
    return a;
  }));
}
function stepRow(step, run) {
  const v = step.verdict || { allowed: true, reasons: [] };
  const denied = v.allowed === false;
  const brain = step.actor !== 'system';
  const args = Object.entries(step.args || {}).filter(([k]) => k !== 'reason');
  const reason = (step.args && step.args.reason) || step.reason;
  const rows = summary(step);
  const decisionTool = ['publish_gauging', 'hold_and_request_recapture', 'escalate_to_hydrologist'].includes(step.tool);
  return h('li', { class: `step ${denied ? 'denied' : ''} ${brain ? 'brainstep' : 'sysstep'} ${decisionTool ? 'dstep' : ''}`.replace(/\s+/g, ' ').trim(), id: `step-${step.i}` },
    h('div.rail', h('span.idx', String(step.i).padStart(2, '0'))),
    h('div.sbody',
      h('div.sh',
        h('span', { class: `tag ${step.actor}` }, ACTOR[step.actor] || step.actor),
        h('code.tool', step.tool),
        denied ? h('span.vd.no', 'DENIED') : h('span.vd.ok', 'allowed'),
        h('span.ms', step.t_ms != null ? secs(step.t_ms) : '')),
      brain && reason ? h('blockquote.why', reason) : (!brain ? h('p.sysnote', reason || 'mandatory perception step') : null),
      args.length ? h('div.args', args.map(([k, val]) => h('code', `${k} = ${typeof val === 'object' ? JSON.stringify(val) : val}`))) : null,
      denied ? h('div.deny', { role: 'note' },
        h('p.dh', h('b', 'Denied by the policy gate.'), ' The call did not run; the denial went back to the brain.'),
        h('ul', (v.reasons || []).map(r => h('li', h('code', r), gloss(r) ? h('span', ` · ${gloss(r)}`) : null)))) : null,
      rows.length ? (brain || step.tool === 'diagnose'
        ? h('dl.res', rows.map(([k, val, hi]) => h('div', { class: hi ? 'hi' : null }, h('dt', k), h('dd', String(val)))))
        : h('p.sres', rows.map(([k, val, hi]) => h('span', { class: hi ? 'hi' : null }, h('i', k), ' ', String(val))))) : null,
      step.error ? h('p.notice.err.serr', h('b', 'Tool error: '), String(step.error)) : null,
      evidenceThumbs(step.evidence)));
}

function renderSteps(doc, run) {
  const steps = doc.steps || [];
  const list = $('#trace');
  for (let k = view.rendered; k < steps.length; k++) {
    const gh = groupHeader(steps[k], run || doc);
    if (gh) list.appendChild(gh);
    const row = stepRow(steps[k], run || doc);
    row.classList.add('enter');
    list.appendChild(row);
    requestAnimationFrame(() => row.classList.remove('enter'));
  }
  view.rendered = steps.length;
}

function renderGate(doc, run) {
  const steps = (doc && doc.steps) || [];
  const denied = steps.filter(x => x.verdict && x.verdict.allowed === false).length;
  const brainCalls = steps.filter(x => x.actor !== 'system').length;
  const status = view.done ? (((doc && doc.decision) || {}).kind || (run && run.status)) : (run && run.status !== 'RUNNING' ? run.status : steps.length ? 'RUNNING' : 'QUEUED');
  const t0 = run && run.started ? parseT(run.started) : null;
  const t1 = run && run.ended ? parseT(run.ended) : null;
  const elapsed = t0 ? Math.max(0, Math.round(((t1 || new Date()) - t0) / 1000)) : null;
  fill($('#gate'),
    h('div.gs', status === 'QUEUED' ? h('span.chip.run', 'QUEUED') : chip(status), elapsed != null ? h('span.mono.el', `${elapsed} s${t1 ? '' : ' elapsed'}`) : null),
    h('div.meter', { role: 'img', 'aria-label': `${steps.length} calls through the policy gate, ${denied} denied` }, steps.map(x => h('i', { class: x.verdict && x.verdict.allowed === false ? 'no' : x.actor === 'system' ? 'sys' : 'ok', title: `${x.i} · ${x.tool}` })), view.done ? null : h('i.next')),
    h('p.gl', h('b', String(steps.length)), ' calls through the gate · ', h('b', String(brainCalls)), ' chosen by the brain · ', h('b', { class: denied ? 'k-esc' : '' }, String(denied)), ' denied'));
}

function renderFacts(doc, run) {
  const r = run || {}, d = doc || {};
  const clipT = r.t || (d.clip && d.clip.t);
  const rows = [
    ['station', h('a', { href: '/station/' }, 'River Dart at Austins Bridge')],
    ['clip', `${clipTime(clipT)} (2018 clip time)`],
    ['file', (r.clip_key || '').split('/').pop() || (typeof d.clip === 'string' ? d.clip : '–')],
    ['stage', r.level != null ? `${(+r.level).toFixed(3)} m` : d.clip && d.clip.level != null ? `${(+d.clip.level).toFixed(3)} m` : '–'],
    ['brain', `${r.brain || d.brain || '–'}${(r.model || d.model) ? ' · ' + (r.model || d.model) : ''}`],
    ['requested by', r.requested_by || d.requested_by || '–'],
    ['started', r.started ? `${wall(r.started)} · ${ago(r.started)}` : '–'],
    ['latency', r.latency_s != null ? `${Math.round(r.latency_s)} s` : '–'],
    ['LLM cost', (r.cost_usd ?? d.cost_usd) != null ? `$${fixed(r.cost_usd ?? d.cost_usd, 4)}` : '–'],
    ['trace', h('a', { href: artifactUrl(`runs/${id}/trace.json`), target: '_blank', rel: 'noopener' }, 'trace.json')],
  ];
  fill($('#facts'), rows.flatMap(([k, v]) => [h('dt', k), h('dd', v)]));
  $('#facts-meta').textContent = id;
}

function renderTiming(doc, run) {
  const t = (doc && doc.timings) || (run && run.timings);
  const box = $('#timing');
  if (!t || !Object.keys(t).length) return;
  const entries = Object.entries(t).filter(([, v]) => v > 0);
  const total = entries.reduce((a, [, v]) => a + v, 0);
  fill(box,
    h('div.tbar', entries.map(([k, v], i) => h('i', { style: `flex:${v};opacity:${0.35 + 0.65 * (1 - i / entries.length)}`, title: `${k}: ${fixed(v, 2)} s` }))),
    h('dl.tlist', entries.map(([k, v]) => h('div', h('dt', k.replace(/_/g, ' ')), h('dd', `${fixed(v, 1)} s`)))),
    h('p.note-sm', `${fixed(total, 1)} s of measurement on the Graviton4 worker${run && run.latency_s ? `, ${Math.round(run.latency_s)} s end to end` : ''}.`));
}

function headline(doc, run) {
  const d = (doc && doc.decision) || (run && run.decision) || {};
  const kind = view.done ? d.kind || (run && run.status) : null;
  const title = $('#run-title');
  fill(title);
  if (!view.done) {
    if (!doc || !(doc.steps || []).length) title.append('Waiting for ', h('em', 'the worker.'));
    else title.append('The agent is ', h('em', 'at work.'));
  } else if (kind === 'PUBLISH') {
    const q = d.q && typeof d.q === 'object' ? d.q.q : d.q;
    title.append('Published ', h('em', `${sig(q)} m³/s.`));
  } else if (kind === 'HOLD') title.append('Held. ', h('em', 'No number went out.'));
  else if (kind === 'ESCALATE') title.append('Escalated ', h('em', 'to a hydrologist.'));
  else title.append('Run ', h('em', String(kind || 'finished').toLowerCase() + '.'));
  document.title = `${title.textContent} · Thalweg run`;
  fill($('#run-chap'), 'Agent run · ', h('span.rid', id));
  const r = run || {};
  const clipT = r.t || (doc && doc.clip && doc.clip.t);
  $('#run-lede').textContent = `River Dart at Austins Bridge, clip of ${clipTime(clipT)} (2018 clip time). ${r.brain === 'llm' || (doc && doc.brain === 'llm') ? `LLM brain${(r.model || (doc && doc.model)) ? ' (' + (r.model || doc.model) + ')' : ''}` : 'Deterministic autopilot'}, on the Graviton4 worker running COOL's OpenCV 5.${view.done ? '' : ' This page follows the trace as the worker writes it.'}`;
}

function renderDecision(doc, run) {
  const d = (doc && doc.decision) || {};
  const rd = (run && run.decision) || {};
  const kind = d.kind || rd.kind || (run && run.status);
  const cls = kindClass(kind);
  const qo = d.q && typeof d.q === 'object' ? d.q : (rd.q != null ? { q: rd.q, q_lo: rd.q_lo, q_hi: rd.q_hi, method: rd.method } : null);
  const reasons = d.reasons || rd.reasons || [];
  const est = (doc && doc.estimates) || {};
  const pub = kind === 'PUBLISH';
  const ph = (doc && doc.physics) || null;
  const wd = doc && doc.warning_draft;
  const word = { PUBLISH: 'Publish', HOLD: 'Hold', ESCALATE: 'Escalate' }[kind] || kind;

  const estRows = Object.entries(est).filter(([, e]) => e && e.q != null);
  let estBlock = null;
  if (estRows.length) {
    const vals = estRows.flatMap(([, e]) => [e.q, e.q_lo, e.q_hi]).filter(Number.isFinite);
    const lo = Math.min(0, ...vals), hi = Math.max(...vals) * 1.08 || 1;
    const X = v => ((v - lo) / (hi - lo)) * 100;
    estBlock = h('div.est',
      h('p.eh', 'Per-method discharge', h('span', pub ? ' measured in this run' : ' measured in this run, none published')),
      estRows.map(([m, e]) => {
        const a = Math.min(e.q_lo, e.q_hi), b = Math.max(e.q_lo, e.q_hi);
        const used = pub && qo && (qo.method === m || (qo.method || '').split('+').includes(m));
        return h('div', { class: `erow ${used ? 'used' : ''}` },
          h('span.em', m),
          h('span.eb', h('i.rng', { style: `left:${X(a)}%;width:${Math.max(0.6, X(b) - X(a))}%` }), h('i.pt', { style: `left:${X(e.q)}%` }), lo < 0 ? h('i.zero', { style: `left:${X(0)}%` }) : null),
          h('span.eqv', `${sig(e.q)} m³/s`, h('small', ` ${qRange(e.q_lo, e.q_hi)} · cov ${Math.round((e.coverage || 0) * 100)} %`)));
      }));
  }

  const physBlock = ph ? h('div.phys',
    h('p.eh', 'Station physics after this run'),
    h('dl.kv',
      h('dt', 'rating curve'), h('dd', ph.rating ? `Q = ${sig(ph.rating.a)}(h − ${fixed(ph.rating.h0, 3)})^${fixed(ph.rating.b, 2)}, n = ${ph.rating.n}` : `not fitted yet (${ph.n_gaugings} gauging${ph.n_gaugings === 1 ? '' : 's'}; needs 5 at 3 levels)`),
      h('dt', 'stage trend'), h('dd', ph.stage_rate_m_per_h != null ? `${ph.stage_rate_m_per_h >= 0 ? '+' : '−'}${Math.abs(ph.stage_rate_m_per_h * 100).toFixed(1)} cm/h` : '–'),
      h('dt', '3 h nowcast'), h('dd', ph.forecast && ph.forecast.length ? `${(+ph.forecast[ph.forecast.length - 1].h).toFixed(2)} m ± ${(1.96 * ph.forecast[ph.forecast.length - 1].sd).toFixed(2)} at ${clipTime(ph.forecast[ph.forecast.length - 1].t, { date: false })}` : '–'),
      h('dt', 'threshold'), h('dd', ph.threshold ? `${ph.threshold.q != null ? sig(ph.threshold.q, 4) + ' m³/s' : '–'}${ph.threshold.stage != null ? ' · stage ' + fixed(ph.threshold.stage, 2) + ' m' : ' · stage needs the rating'}` : '–'),
      h('dt', 'warning'), h('dd', wd ? h('a', { href: '/review/' }, `drafted (p ${fixed(wd.p_cross, 2)}); waiting for a signature`) : 'no draft'))) : null;

  fill($('#decision'), h('section', { class: `dcard d-${cls}`, 'aria-labelledby': 'dk' },
    h('p.chap', 'Decision'),
    h('div.dtop',
      h('h2.dk', { id: 'dk', class: `k-${cls}` }, word),
      pub && qo ? h('div.dq', sig(qo.q), h('small', 'm³/s')) : null),
    pub && qo ? h('p.dmeta', `95 % ${qRange(qo.q_lo, qo.q_hi)} m³/s · method ${d.method || qo.method || '–'}${d.basis ? ' · basis ' : ''}`, d.basis ? h('code', d.basis) : null) : null,
    reasons.length ? h('ul.dreasons', reasons.map(r => h('li', r))) : null,
    !pub && qo && qo.q != null ? h('p.unpub', `Unpublished estimate kept for the reviewer: ${sig(qo.q)} m³/s (${qRange(qo.q_lo, qo.q_hi)}). It is never shown as a gauging.`) : null,
    d.recapture_spec ? h('p.unpub', `Recapture requested: ${JSON.stringify(d.recapture_spec)}`) : null,
    estBlock, physBlock));
}

function renderWorking(doc, run) {
  const box = $('#working');
  if (view.done) { fill(box); return; }
  const n = ((doc && doc.steps) || []).length;
  const msg = !run ? 'Looking for the run…' : !n ? 'The execution has started; the worker picks up the clip, downloads it and probes it. The first steps usually appear within 15 s.' : 'Next step running on the worker…';
  fill(box, h('div.working', h('span.pulse', { 'aria-hidden': 'true' }), h('span', msg)));
}

function renderAll(doc, run) {
  headline(doc, run);
  if (doc) renderSteps(doc, run);
  renderGate(doc, run);
  renderFacts(doc, run);
  renderTiming(doc, run);
  renderWorking(doc, run);
  if (view.done) renderDecision(doc, run);
}

/* ---------- lightbox ---------- */
function openLightbox(url, name) {
  const dlg = $('#lightbox');
  $('#lb-img').src = url; $('#lb-img').alt = `Evidence: ${name}`; $('#lb-cap').textContent = name;
  if (typeof dlg.showModal === 'function') dlg.showModal(); else window.open(url, '_blank');
}
$('#lightbox').addEventListener('click', e => { if (e.target.id === 'lightbox' || e.target.id === 'lb-img') e.currentTarget.close(); });

/* ---------- polling ---------- */
async function main() {
  if (!id) {
    $('#run-title').textContent = 'No run selected.';
    fill($('#run-notice'), h('p.notice', 'Open a run from the ', h('a', { href: '/station/#runs' }, 'station\'s run list'), ', or start one there.'));
    return;
  }
  if (qs('joined')) fill($('#run-notice'), h('p.notice', h('b', 'A run on this clip with the same brain was already in progress, '), 'so this page follows that one instead of starting a second.'));
  let tick = 0, doc = null;
  while (!view.done && Date.now() - view.started < GIVE_UP_MS) {
    try {
      const run = tick % 2 === 0 || !view.run ? await api.run(id).catch(e => { if (e.status === 404) return null; throw e; }) : view.run;
      if (run) view.run = run;
      // progress.json appears with the first step; don't ask for it in the first seconds of a fresh run
      const young = view.run && view.run.status === 'RUNNING' && !doc && Date.now() - parseT(view.run.started) < 9000;
      const p = young ? null : await api.progress(id);
      if (!run && !p) {
        view.notFound++;
        if (view.notFound > 4) { $('#run-title').textContent = 'No run with this id.'; fill($('#run-notice'), h('p.notice.err', `Run ${id} does not exist. Pick one from the `, h('a', { href: '/station/#runs' }, 'station\'s run list'), '.')); fill($('#working')); return; }
      }
      if (p) doc = p;
      if (p && p.done) view.done = true;
      else if (view.run && view.run.status && view.run.status !== 'RUNNING') {
        // finished, but progress.json lags or the run failed closed: the trace is the record
        const tr = await api.trace(id).catch(() => null);
        if (tr) doc = tr;
        if (tr || tick > 3) view.done = true;
      }
      renderAll(doc, view.run);
    } catch (err) {
      fill($('#run-notice'), h('p.notice.err', 'Lost contact with the API, retrying: ', errorText(err)));
    }
    tick++;
    if (!view.done) await sleep(POLL_MS);
  }
  if (!view.done) fill($('#working'), h('p.notice.warn', 'Stopped following this run after 20 minutes. Reload to check again.'));
  else fill($('#run-notice'), qs('joined') ? $('#run-notice').firstChild : null);
}
main();
