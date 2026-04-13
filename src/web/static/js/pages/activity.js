/** Activity page — Main PC のゲーム/プロセスプレイ履歴ビューア。 */
import { api, apiBatch } from '../api.js';

// ------------------------------------------------------------
// state
// ------------------------------------------------------------
const PRESETS = [
  { key: '7',   label: '7日' },
  { key: '30',  label: '30日' },
  { key: '90',  label: '90日' },
  { key: '365', label: '1年' },
  { key: '0',   label: '全期間' },
];

let _days = 30;
let _gameFilter = '';
let _sessionOffset = 0;
const SESSION_PAGE_SIZE = 50;

// ------------------------------------------------------------
// helpers
// ------------------------------------------------------------
function fmtDuration(sec) {
  if (!sec || sec < 0) return '0分';
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h >= 100) return `${h}時間`;
  if (h > 0) return `${h}時間${m}分`;
  if (m > 0) return `${m}分`;
  return `${sec}秒`;
}

function fmtDateTime(iso) {
  if (!iso) return '---';
  // "YYYY-MM-DD HH:MM:SS" or "YYYY-MM-DDTHH:MM:SS(+09:00)"
  return iso.replace('T', ' ').slice(0, 16);
}

function fmtDate(iso) {
  if (!iso) return '---';
  return iso.slice(0, 10);
}

function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ゲーム名 → 決定的な色（HSL）
function gameColor(name) {
  if (!name) return '#888';
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) | 0;
  const hue = Math.abs(h) % 360;
  return `hsl(${hue}, 55%, 55%)`;
}

// ------------------------------------------------------------
// render
// ------------------------------------------------------------
export function render() {
  const pills = PRESETS.map(p =>
    `<button class="pill" data-days="${p.key}">${p.label}</button>`
  ).join('');

  return `
<div class="activity-page" style="display:flex;flex-direction:column;gap:1rem">

  <section class="card">
    <div class="card-header">
      <h3>期間</h3>
      <div id="a-range-info" class="mono" style="font-size:0.8125rem;color:var(--text-muted)"></div>
    </div>
    <div id="a-period-pills" style="display:flex;gap:0.5rem;flex-wrap:wrap">${pills}</div>
  </section>

  <section class="quick-stats" id="a-summary">
    <div class="mini-card"><div class="mini-value" id="a-sum-total">-</div><div class="mini-label">総プレイ時間</div></div>
    <div class="mini-card"><div class="mini-value" id="a-sum-sessions">-</div><div class="mini-label">セッション数</div></div>
    <div class="mini-card"><div class="mini-value" id="a-sum-active">-</div><div class="mini-label">アクティブ日数</div></div>
    <div class="mini-card"><div class="mini-value" id="a-sum-longest">-</div><div class="mini-label">最長セッション</div></div>
    <div class="mini-card"><div class="mini-value" id="a-sum-games">-</div><div class="mini-label">ゲーム種類</div></div>
  </section>

  <section class="card">
    <div class="card-header">
      <h3>日別プレイ時間</h3>
      <span class="stat-label" id="a-daily-hint" style="font-size:0.75rem;color:var(--text-muted)">積み上げはゲーム別</span>
    </div>
    <div id="a-daily-chart" style="display:flex;align-items:flex-end;gap:2px;height:180px;overflow-x:auto;padding:0.5rem 0;border-bottom:1px solid var(--border)"></div>
    <div id="a-daily-axis" style="display:flex;gap:2px;font-size:0.65rem;color:var(--text-muted);padding-top:4px;overflow-x:auto"></div>
  </section>

  <section style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:1rem">
    <div class="card">
      <div class="card-header">
        <h3>ゲーム別ランキング</h3>
        <button class="btn btn-sm" id="a-clear-filter" style="display:none">フィルタ解除</button>
      </div>
      <div id="a-game-ranking" style="display:flex;flex-direction:column;gap:0.5rem"></div>
    </div>

    <div class="card">
      <div class="card-header">
        <h3>ゲーム中以外の作業アプリ</h3>
      </div>
      <div id="a-fg-ranking" style="display:flex;flex-direction:column;gap:0.5rem"></div>
    </div>
  </section>

  <section class="card">
    <div class="card-header">
      <h3>セッション履歴</h3>
      <div id="a-session-meta" style="font-size:0.8125rem;color:var(--text-muted)"></div>
    </div>
    <div style="overflow-x:auto">
      <table class="data-table" id="a-session-table" style="width:100%;font-size:0.8125rem">
        <thead>
          <tr>
            <th style="text-align:left">ゲーム</th>
            <th style="text-align:left">開始</th>
            <th style="text-align:left">終了</th>
            <th style="text-align:right">時間</th>
          </tr>
        </thead>
        <tbody id="a-session-rows"></tbody>
      </table>
    </div>
    <div style="display:flex;justify-content:space-between;align-items:center;padding-top:0.5rem">
      <button class="btn btn-sm" id="a-session-prev">← 前</button>
      <span id="a-session-pager" class="mono" style="font-size:0.8125rem;color:var(--text-muted)">-</span>
      <button class="btn btn-sm" id="a-session-next">次 →</button>
    </div>
  </section>
</div>
`;
}

// ------------------------------------------------------------
// data loading / rendering
// ------------------------------------------------------------
async function refreshAll() {
  updatePeriodPills();
  await Promise.all([
    loadSummaryAndStats(),
    loadDaily(),
    loadSessions(true),
  ]);
}

async function loadSummaryAndStats() {
  const [summary, stats] = await apiBatch([
    [`/api/activity/summary?days=${_days}`, {}],
    [`/api/activity/stats?days=${_days}`, {}],
  ]);

  renderSummary(summary);
  renderGameRanking(stats?.games || []);
  renderFgRanking(stats?.foreground || []);
  renderRangeInfo(summary);
}

async function loadDaily() {
  // 全期間でも棒グラフは最大 365 日に制限して視認性確保
  const days = _days === 0 ? 365 : _days;
  const data = await api(`/api/activity/daily?days=${days}`).catch(() => null);
  renderDaily(data?.daily || [], days);
}

async function loadSessions(reset) {
  if (reset) _sessionOffset = 0;
  const params = new URLSearchParams({
    days: String(_days),
    limit: String(SESSION_PAGE_SIZE),
    offset: String(_sessionOffset),
  });
  if (_gameFilter) params.set('game', _gameFilter);
  const data = await api(`/api/activity/sessions?${params.toString()}`).catch(() => null);
  renderSessions(data || { sessions: [], total: 0 });
}

function renderSummary(s) {
  const $ = id => document.getElementById(id);
  if (!s) return;
  $('a-sum-total').textContent = fmtDuration(s.total_sec);
  $('a-sum-sessions').textContent = s.sessions ?? 0;
  $('a-sum-active').textContent = `${s.active_days ?? 0}日`;
  $('a-sum-longest').textContent = fmtDuration(s.longest_sec);
  $('a-sum-games').textContent = s.distinct_games ?? 0;
}

function renderRangeInfo(s) {
  const el = document.getElementById('a-range-info');
  if (!el) return;
  if (_days === 0) {
    const earliest = s?.earliest ? fmtDate(s.earliest) : '---';
    el.textContent = `全期間（${earliest} 〜 現在）`;
  } else {
    el.textContent = `過去 ${_days} 日`;
  }
}

function renderGameRanking(games) {
  const root = document.getElementById('a-game-ranking');
  if (!root) return;
  if (!games.length) {
    root.innerHTML = '<div style="color:var(--text-muted);padding:1rem;text-align:center">データなし</div>';
    return;
  }
  const max = Math.max(...games.map(g => g.sec || 0), 1);
  root.innerHTML = games.slice(0, 20).map(g => {
    const pct = Math.max(2, ((g.sec || 0) / max) * 100);
    const color = gameColor(g.game_name);
    const active = _gameFilter === g.game_name;
    return `
      <div class="game-row" data-game="${escapeHtml(g.game_name)}"
           style="cursor:pointer;padding:4px 6px;border-radius:4px;${active ? 'background:var(--bg-hover)' : ''}">
        <div style="display:flex;justify-content:space-between;gap:0.5rem;font-size:0.8125rem;margin-bottom:3px">
          <span style="font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(g.game_name)}</span>
          <span class="mono" style="color:var(--text-muted)">${fmtDuration(g.sec)} · ${g.sessions}回</span>
        </div>
        <div style="height:6px;background:var(--bg-elevated);border-radius:3px;overflow:hidden">
          <div style="height:100%;width:${pct}%;background:${color}"></div>
        </div>
      </div>`;
  }).join('');
}

function renderFgRanking(fg) {
  const root = document.getElementById('a-fg-ranking');
  if (!root) return;
  // during_game=0 のみ（純粋な作業時間）
  const work = fg.filter(r => !r.during_game).slice(0, 15);
  if (!work.length) {
    root.innerHTML = '<div style="color:var(--text-muted);padding:1rem;text-align:center">データなし</div>';
    return;
  }
  const max = Math.max(...work.map(r => r.sec || 0), 1);
  root.innerHTML = work.map(r => {
    const pct = Math.max(2, ((r.sec || 0) / max) * 100);
    return `
      <div style="padding:4px 6px">
        <div style="display:flex;justify-content:space-between;gap:0.5rem;font-size:0.8125rem;margin-bottom:3px">
          <span class="mono" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(r.process_name)}</span>
          <span class="mono" style="color:var(--text-muted)">${fmtDuration(r.sec)}</span>
        </div>
        <div style="height:5px;background:var(--bg-elevated);border-radius:3px;overflow:hidden">
          <div style="height:100%;width:${pct}%;background:var(--accent)"></div>
        </div>
      </div>`;
  }).join('');
}

function renderDaily(daily, expectedDays) {
  const chart = document.getElementById('a-daily-chart');
  const axis = document.getElementById('a-daily-axis');
  if (!chart) return;
  if (!daily.length) {
    chart.innerHTML = '<div style="color:var(--text-muted);padding:1rem;margin:auto">データなし</div>';
    axis.innerHTML = '';
    return;
  }
  // 日付の連続性を保つため、抜けている日は 0 で埋める
  const map = new Map(daily.map(d => [d.day, d]));
  const today = new Date();
  const series = [];
  for (let i = expectedDays - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    series.push(map.get(key) || { day: key, total_sec: 0, games: [] });
  }
  const max = Math.max(...series.map(s => s.total_sec || 0), 1);
  const barWidth = Math.max(6, Math.floor(800 / series.length));

  chart.innerHTML = series.map(s => {
    const pct = ((s.total_sec || 0) / max) * 100;
    const hours = (s.total_sec / 3600).toFixed(1);
    let stack = '';
    if (s.games?.length && s.total_sec > 0) {
      // ゲーム別に色を分けて積み上げ
      stack = s.games.map(g => {
        const h = (g.sec / s.total_sec) * 100;
        return `<div style="height:${h}%;background:${gameColor(g.game_name)}" title="${escapeHtml(g.game_name)}: ${fmtDuration(g.sec)}"></div>`;
      }).join('');
    }
    return `
      <div class="daily-bar" data-day="${s.day}" title="${s.day}: ${hours}h"
           style="flex:0 0 ${barWidth}px;height:100%;display:flex;align-items:flex-end;cursor:pointer">
        <div style="width:100%;height:${pct}%;display:flex;flex-direction:column-reverse;background:var(--bg-elevated);border-radius:2px 2px 0 0;overflow:hidden">
          ${stack}
        </div>
      </div>`;
  }).join('');

  // 軸: 週間隔でラベル、それ以外は空
  axis.innerHTML = series.map((s, i) => {
    const label = (i === 0 || i === series.length - 1 || i % 7 === 0)
      ? s.day.slice(5) : '';
    return `<div style="flex:0 0 ${barWidth}px;text-align:center;overflow:hidden">${label}</div>`;
  }).join('');
}

function renderSessions(data) {
  const tbody = document.getElementById('a-session-rows');
  const meta = document.getElementById('a-session-meta');
  const pager = document.getElementById('a-session-pager');
  if (!tbody) return;

  const sessions = data.sessions || [];
  if (!sessions.length) {
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted);padding:1rem">該当なし</td></tr>';
  } else {
    tbody.innerHTML = sessions.map(s => `
      <tr>
        <td><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${gameColor(s.game_name)};margin-right:6px;vertical-align:middle"></span>${escapeHtml(s.game_name)}</td>
        <td class="mono">${fmtDateTime(s.start_at)}</td>
        <td class="mono">${fmtDateTime(s.end_at)}</td>
        <td class="mono" style="text-align:right">${fmtDuration(s.duration_sec)}</td>
      </tr>
    `).join('');
  }

  if (meta) {
    const filter = _gameFilter ? `「${_gameFilter}」のみ` : '';
    meta.textContent = `${data.total} 件 ${filter}`.trim();
  }
  if (pager) {
    const from = sessions.length ? _sessionOffset + 1 : 0;
    const to = _sessionOffset + sessions.length;
    pager.textContent = `${from} - ${to} / ${data.total}`;
  }
  document.getElementById('a-session-prev').disabled = _sessionOffset <= 0;
  document.getElementById('a-session-next').disabled = _sessionOffset + sessions.length >= data.total;
  document.getElementById('a-clear-filter').style.display = _gameFilter ? '' : 'none';
}

function updatePeriodPills() {
  document.querySelectorAll('#a-period-pills .pill').forEach(el => {
    el.classList.toggle('active', Number(el.dataset.days) === _days);
  });
}

// ------------------------------------------------------------
// mount
// ------------------------------------------------------------
export async function mount() {
  // 期間pill
  document.getElementById('a-period-pills').addEventListener('click', ev => {
    const btn = ev.target.closest('.pill');
    if (!btn) return;
    _days = Number(btn.dataset.days);
    refreshAll();
  });

  // ゲームランキングクリックでフィルタ
  document.getElementById('a-game-ranking').addEventListener('click', ev => {
    const row = ev.target.closest('.game-row');
    if (!row) return;
    const game = row.dataset.game;
    _gameFilter = (_gameFilter === game) ? '' : game;
    loadSessions(true).then(() => {
      // ランキング側のアクティブ表示も更新したいので再レンダ
      loadSummaryAndStats();
    });
  });

  // フィルタ解除
  document.getElementById('a-clear-filter').addEventListener('click', () => {
    _gameFilter = '';
    loadSessions(true);
    loadSummaryAndStats();
  });

  // ページング
  document.getElementById('a-session-prev').addEventListener('click', () => {
    if (_sessionOffset <= 0) return;
    _sessionOffset = Math.max(0, _sessionOffset - SESSION_PAGE_SIZE);
    loadSessions(false);
  });
  document.getElementById('a-session-next').addEventListener('click', () => {
    _sessionOffset += SESSION_PAGE_SIZE;
    loadSessions(false);
  });

  await refreshAll();
}

export function unmount() {
  _gameFilter = '';
  _sessionOffset = 0;
}
