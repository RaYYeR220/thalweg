// Fetch helpers for the Thalweg API (/api/*) and evidence artifacts (/artifacts/*), same origin.

export class ApiError extends Error {
  constructor(status, body, url) {
    super((body && (body.message || body.error)) || `HTTP ${status}`);
    this.status = status;
    this.body = body || {};
    this.url = url;
  }
}

async function request(url, { method = 'GET', body, timeout = 20000, cache = 'no-store' } = {}) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeout);
  let res;
  try {
    res = await fetch(url, {
      method,
      cache,
      signal: ctl.signal,
      headers: body ? { 'content-type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    throw new ApiError(0, { error: err.name === 'AbortError' ? 'timeout' : 'network', message: err.name === 'AbortError' ? 'The request timed out.' : 'The network request failed.' }, url);
  } finally {
    clearTimeout(timer);
  }
  let data = null;
  const text = await res.text();
  if (text) {
    try { data = JSON.parse(text); } catch { data = { error: 'not_json', message: text.slice(0, 200) }; }
  }
  if (!res.ok) throw new ApiError(res.status, data, url);
  return { status: res.status, data };
}

export const getJSON = async (url, opts) => (await request(url, opts)).data;
export const postJSON = (url, body, opts) => request(url, { ...opts, method: 'POST', body });

/** GET that resolves to null on 404 instead of throwing. */
export async function getOrNull(url, opts) {
  try { return await getJSON(url, opts); } catch (err) { if (err.status === 404) return null; throw err; }
}

const enc = encodeURIComponent;
export const runArtifact = (runId, name) => `/artifacts/runs/${enc(runId)}/${name}`;
export const artifactUrl = key => '/artifacts/' + String(key).replace(/^\/+/, '');

export const api = {
  station: id => getJSON(`/api/stations/${enc(id)}`),
  gaugings: (id, limit = 500) => getJSON(`/api/stations/${enc(id)}/gaugings?limit=${limit}`),
  runs: (station, limit = 30) => getJSON(`/api/runs?station=${enc(station)}&limit=${limit}`),
  run: id => getJSON(`/api/runs/${enc(id)}`),
  startRun: body => postJSON('/api/runs', body, { timeout: 30000 }),
  reviews: state => getJSON(`/api/reviews?state=${enc(state)}`),
  act: (id, body) => postJSON(`/api/reviews/${enc(id)}`, body, { timeout: 30000 }),
  trace: id => getOrNull(runArtifact(id, 'trace.json'), { cache: 'default' }),
  progress: id => getOrNull(runArtifact(id, 'progress.json')),
  publicJSON: name => getJSON(`/artifacts/public/${name}`, { cache: 'default' }),
};
