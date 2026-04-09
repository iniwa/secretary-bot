/** API client wrapper. */

export async function api(path, opts = {}) {
  const { method = 'GET', body, params } = opts;
  let url = path;
  if (params) {
    const qs = new URLSearchParams(params).toString();
    if (qs) url += '?' + qs;
  }
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const res = await fetch(url, init);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`${res.status}: ${text.slice(0, 200)}`);
  }
  return res.json();
}

/** Parallel fetch — returns array of results (null on individual failure). */
export async function apiBatch(calls) {
  return Promise.all(
    calls.map(([path, opts]) =>
      api(path, opts).catch(err => {
        console.warn(`API error: ${path}`, err);
        return null;
      })
    )
  );
}
