// The hydrologist's review queue: list warning drafts by state; sign or reject with the reviewer passcode.
import { api, artifactUrl, ApiError } from './api.js';
import { h, $, $$, fill, sig, fixed, clipTime, chip, errorText, parseT } from './ui.js';

const STATES = ['DRAFT', 'SIGNED', 'REJECTED', 'EXPIRED'];
const lists = {};
let current = 'DRAFT';
const thresholdCache = new Map();

function etaText(w) {
  if (w.eta_s == null || !w.created) return '–';
  if (+w.eta_s === 0) return 'already above';
  const at = new Date(+parseT(w.created) + w.eta_s * 1000);
  return `${clipTime(at, { date: false })} (in ${fixed(w.eta_s / 3600, 1)} h)`;
}

async function threshold(w, dd) {
  // The threshold the draft was computed against lives in the run's trace.
  if (!w.run_id) return;
  let p = thresholdCache.get(w.run_id);
  if (!p) { p = api.trace(w.run_id).catch(() => null); thresholdCache.set(w.run_id, p); }
  const tr = await p;
  const d = tr && tr.warning_draft;
  if (d && d.threshold_q != null) dd.textContent = `${sig(d.threshold_q, 4)} m³/s${d.threshold_stage != null ? ` · stage ${fixed(d.threshold_stage, 2)} m` : ''}`;
}

function card(w) {
  const state = w.state || current;
  const stCls = { DRAFT: 'draft', SIGNED: 'pub', REJECTED: 'esc', EXPIRED: 'hold' }[state] || '';
  const thrDD = h('dd', '231.2 m³/s · QMED');
  threshold(w, thrDD);
  const msg = h('div.amsg', { 'aria-live': 'polite' });
  const ev = (w.evidence || []).filter(Boolean);
  const el = h('article', { class: `paper wcard s-${stCls}`, 'aria-label': `Warning ${w.id}` },
    h('div.hd', h('span', `River Dart at Austins Bridge · ${clipTime(w.created)}`), h('span', { class: `st k-${stCls}` }, state === 'DRAFT' ? 'DRAFT · NOT ISSUED' : state)),
    h('blockquote', w.text || '(no text)'),
    h('dl.wfacts',
      h('div', h('dt', 'level now'), h('dd', w.level_now != null ? `${fixed(w.level_now, 2)} m` : '–')),
      h('div', h('dt', 'Q now'), h('dd', w.q_now != null ? `${sig(w.q_now)} m³/s` : '–')),
      h('div', h('dt', 'ETA'), h('dd', etaText(w))),
      h('div', h('dt', 'p(cross, 3 h)'), h('dd', w.p_cross != null ? fixed(w.p_cross, 3) : '–')),
      h('div', h('dt', 'threshold'), thrDD),
      h('div', h('dt', 'created'), h('dd', clipTime(w.created))),
      h('div', h('dt', 'expires'), h('dd', w.expires ? `${clipTime(w.expires)} · 6 h` : '6 h after drafting'))),
    h('p.evl', h('span', 'evidence'),
      ev.length ? ev.map(k => h('a', { href: artifactUrl(k), target: '_blank', rel: 'noopener' }, k.split('/').pop())) : h('em', 'none attached'),
      w.run_id ? h('a', { href: `/run/?id=${encodeURIComponent(w.run_id)}` }, 'the run that drafted it →') : null),
    state === 'DRAFT' ? actions(w, msg) : outcome(w, state),
    msg);
  return el;
}

function outcome(w, state) {
  if (state === 'SIGNED') return h('div.sigdone', h('span.lab', 'Signed'), h('span.who', w.signed_by || 'reviewer'), h('span.ex', w.signed_at ? `at ${new Date(w.signed_at).toLocaleString()}` : ''));
  if (state === 'REJECTED') return h('p.closed', h('b', 'Rejected'), w.rejected_note ? ` · “${w.rejected_note}”` : '', w.closed_at ? ` · ${new Date(w.closed_at).toLocaleString()}` : '');
  if (state === 'EXPIRED') return h('p.closed', h('b', 'Expired'), ' unsigned after six hours', w.closed_at ? ` · ${new Date(w.closed_at).toLocaleString()}` : '');
  return null;
}

function actions(w, msg) {
  const sign = h('button.btn.primary', { type: 'button' }, 'Sign and issue');
  const reject = h('button.btn.danger', { type: 'button' }, 'Reject');
  const arm = (btn, label, action) => {
    let armed = false, timer;
    btn.addEventListener('click', async () => {
      const pass = $('#passcode').value.trim();
      if (!pass) { fill(msg, h('p.notice.warn', 'Enter the reviewer passcode first.')); $('#passcode').focus(); return; }
      if (!armed) {
        armed = true; btn.dataset.label = btn.textContent; btn.textContent = label;
        timer = setTimeout(() => { armed = false; btn.textContent = btn.dataset.label; }, 5000);
        return;
      }
      clearTimeout(timer); armed = false;
      await act(w, action, pass, [sign, reject], msg);
    });
  };
  arm(sign, 'Confirm: sign and issue', 'sign');
  arm(reject, 'Confirm: reject', 'reject');
  return h('div.acts', sign, reject, h('p.lk', 'Two clicks: the first arms the button. The agent has no path to SIGNED.'));
}

async function act(w, action, passcode, buttons, msg) {
  buttons.forEach(b => { b.disabled = true; });
  fill(msg, h('p.loading', action === 'sign' ? 'Sending the signature to the paused execution…' : 'Sending the rejection…'));
  try {
    const { data } = await api.act(w.id, { action, passcode, note: $('#note').value.trim() });
    fill(msg, h('p.notice.ok', h('b', action === 'sign' ? 'Signed. ' : 'Rejected. '), `The execution resumed and will mark it ${data.next_state}${action === 'sign' ? ' and publish it' : ''}.`));
    $('#passcode').removeAttribute('aria-invalid');
    setTimeout(() => loadAll(), 3500);
  } catch (err) {
    buttons.forEach(b => { b.disabled = false; b.textContent = b.dataset.label || b.textContent; });
    if (err instanceof ApiError && err.status === 403) {
      $('#passcode').setAttribute('aria-invalid', 'true');
      fill(msg, h('p.notice.err', h('b', 'Wrong passcode. '), 'Nothing was sent to the workflow.'));
    } else if (err instanceof ApiError && err.status === 409) {
      fill(msg, h('p.notice.warn', h('b', 'Already resolved. '), `This warning is ${err.body.state || 'no longer a draft'}${err.body.message ? `: ${err.body.message}` : ''}.`));
      setTimeout(() => loadAll(), 2500);
    } else if (err instanceof ApiError && err.status === 404) {
      fill(msg, h('p.notice.err', h('b', 'Not found. '), 'This warning no longer exists.'));
    } else {
      fill(msg, h('p.notice.err', 'The decision was not recorded: ', errorText(err)));
    }
  }
}

const EMPTY = {
  DRAFT: 'No drafts are waiting. The station drafts one only after a published gauging whose nowcast crosses the threshold, so most of the time this list is empty.',
  SIGNED: 'Nothing has been signed yet. A signed warning is published to public/warnings.json.',
  REJECTED: 'No rejected drafts.',
  EXPIRED: 'No expired drafts. A draft expires when nobody signs or rejects it within six hours.',
};

function render() {
  for (const b of $$('#tabs button')) b.setAttribute('aria-selected', String(b.dataset.state === current));
  $('#cards').setAttribute('aria-labelledby', `tab-${current}`);
  const l = lists[current];
  if (l instanceof Error) { fill($('#cards'), h('p.notice.err', 'The review queue could not be read: ', errorText(l))); return; }
  if (!l) { fill($('#cards'), h('div.loading', 'Reading the queue…')); return; }
  fill($('#cards'), l.length ? l.map(card) : h('p.empty', EMPTY[current]));
}

async function loadAll() {
  await Promise.all(STATES.map(async st => {
    try {
      const r = await api.reviews(st);
      lists[st] = r.reviews || [];
      $(`#n-${st}`).textContent = String(lists[st].length);
    } catch (err) {
      lists[st] = err;
      $(`#n-${st}`).textContent = '!';
    }
  }));
  render();
}

$('#tabs').addEventListener('click', e => {
  const b = e.target.closest('button[data-state]');
  if (!b) return;
  current = b.dataset.state; render();
});
$('#tabs').addEventListener('keydown', e => {
  if (!['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
  const i = STATES.indexOf(current), j = (i + (e.key === 'ArrowRight' ? 1 : STATES.length - 1)) % STATES.length;
  current = STATES[j]; render(); $(`#tab-${current}`).focus();
});
const want = new URLSearchParams(location.search).get('state');
if (want && STATES.includes(want.toUpperCase())) current = want.toUpperCase();
render();
loadAll();
setInterval(() => { if (!document.hidden && !document.activeElement.closest('.wcard')) loadAll(); }, 60000);
