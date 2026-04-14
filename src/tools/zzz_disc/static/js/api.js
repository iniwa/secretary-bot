/** ZZZ Disc Manager API client. base: /tools/zzz-disc/api */

const BASE = '/tools/zzz-disc/api';

export async function api(path, opts = {}) {
  const { method = 'GET', body, params, headers = {} } = opts;
  let url = BASE + path;
  if (params) {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
    ).toString();
    if (qs) url += '?' + qs;
  }
  const init = { method, headers: { ...headers } };
  if (body !== undefined) {
    if (body instanceof FormData) {
      init.body = body;
    } else {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
  }
  const res = await fetch(url, init);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`${res.status}: ${text.slice(0, 200)}`);
  }
  if (res.status === 204) return null;
  const ct = res.headers.get('content-type') || '';
  return ct.includes('application/json') ? res.json() : res.text();
}

export function sseConnect(path, handlers = {}) {
  const url = BASE + path;
  const es = new EventSource(url);
  if (handlers.onOpen) es.addEventListener('open', handlers.onOpen);
  if (handlers.onError) es.addEventListener('error', handlers.onError);
  if (handlers.onMessage) es.addEventListener('message', (ev) => {
    let data;
    try { data = JSON.parse(ev.data); } catch { data = ev.data; }
    handlers.onMessage(data, ev);
  });
  return es;
}
