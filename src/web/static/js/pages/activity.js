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
let _rangeStart = '';             // YYYY-MM-DD。指定時は _days より優先
let _rangeEnd = '';
let _gameFilter = '';
let _dayFilter = '';              // YYYY-MM-DD。カレンダーから1日絞込み
let _sessionOffset = 0;
let _viewMode = 'bar';            // 'bar' | 'calendar'
let _calYear = 0;
let _calMonth = 0;                // 1〜12
const SESSION_PAGE_SIZE = 50;

function rangeParams() {
  const p = new URLSearchParams();
  if (_rangeStart && _rangeEnd) {
    p.set('start', _rangeStart);
    p.set('end', _rangeEnd);
  } else {
    p.set('days', String(_days));
  }
  return p;
}

function rangeActive() {
  return !!(_rangeStart && _rangeEnd);
}

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
    <div style="display:flex;gap:0.5rem;align-items:center;flex-wrap:wrap;margin-top:0.5rem">
      <label style="font-size:0.8125rem;color:var(--text-muted)">開始</label>
      <input type="date" id="a-range-start" class="form-input" style="min-width:140px">
      <span style="color:var(--text-muted)">〜</span>
      <label style="font-size:0.8125rem;color:var(--text-muted)">終了</label>
      <input type="date" id="a-range-end" class="form-input" style="min-width:140px">
      <button class="btn btn-sm" id="a-range-apply">適用</button>
      <button class="btn btn-sm" id="a-range-clear" style="display:none">範囲解除</button>
    </div>
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
      <div style="display:flex;gap:0.5rem;align-items:center">
        <div id="a-view-toggle" style="display:flex;gap:0.25rem">
          <button class="pill" data-view="bar">棒グラフ</button>
          <button class="pill" data-view="calendar">カレンダー</button>
        </div>
      </div>
    </div>

    <!-- 棒グラフビュー -->
    <div id="a-view-bar">
      <div id="a-daily-chart" style="display:flex;align-items:flex-end;gap:2px;height:180px;overflow-x:auto;padding:0.5rem 0;border-bottom:1px solid var(--border)"></div>
      <div id="a-daily-axis" style="display:flex;gap:2px;font-size:0.65rem;color:var(--text-muted);padding-top:4px;overflow-x:auto"></div>
    </div>

    <!-- カレンダービュー -->
    <div id="a-view-calendar" style="display:none">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.5rem">
        <button class="btn btn-sm" id="a-cal-prev">← 前月</button>
        <div id="a-cal-title" style="font-weight:600"></div>
        <button class="btn btn-sm" id="a-cal-next">次月 →</button>
      </div>
      <div class="activity-calendar" id="a-cal-grid"></div>
      <div style="display:flex;align-items:center;gap:0.75rem;margin-top:0.5rem;font-size:0.75rem;color:var(--text-muted)">
        <span>強度:</span>
        <span style="display:flex;align-items:center;gap:2px">
          <span class="cal-legend" style="background:var(--bg-elevated)"></span>
          <span class="cal-legend" style="background:hsla(210,60%,50%,0.25)"></span>
          <span class="cal-legend" style="background:hsla(210,60%,50%,0.5)"></span>
          <span class="cal-legend" style="background:hsla(210,60%,50%,0.75)"></span>
          <span class="cal-legend" style="background:hsla(210,60%,50%,1)"></span>
        </span>
        <span style="margin-left:auto" id="a-cal-month-total"></span>
      </div>
    </div>
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
  const qs = rangeParams().toString();
  const [summary, stats] = await apiBatch([
    [`/api/activity/summary?${qs}`, {}],
    [`/api/activity/stats?${qs}`, {}],
  ]);

  renderSummary(summary);
  renderGameRanking(stats?.games || []);
  renderFgRanking(stats?.foreground || []);
  renderRangeInfo(summary);
}

async function loadDaily() {
  if (_viewMode === 'calendar') {
    await loadCalendar();
    return;
  }
  if (rangeActive()) {
    const qs = rangeParams().toString();
    const data = await api(`/api/activity/daily?${qs}`).catch(() => null);
    renderDailyRange(data?.daily || [], _rangeStart, _rangeEnd);
    return;
  }
  // 棒グラフは最大365日に制限して視認性確保
  const days = _days === 0 ? 365 : _days;
  const data = await api(`/api/activity/daily?days=${days}`).catch(() => null);
  renderDaily(data?.daily || [], days);
}

async function loadCalendar() {
  const data = await api(
    `/api/activity/daily?year=${_calYear}&month=${_calMonth}`
  ).catch(() => null);
  renderCalendar(data?.daily || []);
}

async function loadSessions(reset) {
  if (reset) _sessionOffset = 0;
  const params = rangeParams();
  params.set('limit', String(SESSION_PAGE_SIZE));
  params.set('offset', String(_sessionOffset));
  if (_gameFilter) params.set('game', _gameFilter);
  if (_dayFilter) params.set('day', _dayFilter);
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
  if (rangeActive()) {
    el.textContent = `${_rangeStart} 〜 ${_rangeEnd}`;
  } else if (_days === 0) {
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

function renderDailyRange(daily, startStr, endStr) {
  const chart = document.getElementById('a-daily-chart');
  const axis = document.getElementById('a-daily-axis');
  if (!chart) return;
  const map = new Map(daily.map(d => [d.day, d]));
  // 連続日付列を start〜end で生成
  const series = [];
  const start = new Date(startStr + 'T00:00:00');
  const end = new Date(endStr + 'T00:00:00');
  for (let t = start.getTime(); t <= end.getTime(); t += 86400000) {
    const key = new Date(t).toISOString().slice(0, 10);
    series.push(map.get(key) || { day: key, total_sec: 0, games: [] });
  }
  if (!series.length) {
    chart.innerHTML = '<div style="color:var(--text-muted);padding:1rem;margin:auto">データなし</div>';
    axis.innerHTML = '';
    return;
  }
  const max = Math.max(...series.map(s => s.total_sec || 0), 1);
  const barWidth = Math.max(6, Math.floor(800 / series.length));
  chart.innerHTML = series.map(s => {
    const pct = ((s.total_sec || 0) / max) * 100;
    const hours = (s.total_sec / 3600).toFixed(1);
    let stack = '';
    if (s.games?.length && s.total_sec > 0) {
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
  const step = Math.max(1, Math.ceil(series.length / 10));
  axis.innerHTML = series.map((s, i) => {
    const label = (i === 0 || i === series.length - 1 || i % step === 0)
      ? s.day.slice(5) : '';
    return `<div style="flex:0 0 ${barWidth}px;text-align:center;overflow:hidden">${label}</div>`;
  }).join('');
}

function renderCalendar(daily) {
  const grid = document.getElementById('a-cal-grid');
  const title = document.getElementById('a-cal-title');
  const totalEl = document.getElementById('a-cal-month-total');
  if (!grid) return;

  title.textContent = `${_calYear}年${_calMonth}月`;

  const dailyMap = new Map(daily.map(d => [d.day, d]));
  const monthTotal = daily.reduce((s, d) => s + (d.total_sec || 0), 0);
  totalEl.textContent = `月合計: ${fmtDuration(monthTotal)}`;

  const monthMax = Math.max(...daily.map(d => d.total_sec || 0), 1);

  // 月初の曜日（日曜=0）
  const first = new Date(_calYear, _calMonth - 1, 1);
  const daysInMonth = new Date(_calYear, _calMonth, 0).getDate();
  const startDow = first.getDay();
  const todayStr = new Date().toISOString().slice(0, 10);

  const WEEK_LABELS = ['日', '月', '火', '水', '木', '金', '土'];
  let html = WEEK_LABELS.map(l =>
    `<div class="cal-head">${l}</div>`
  ).join('');

  for (let i = 0; i < startDow; i++) html += '<div class="cal-cell cal-empty"></div>';

  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${_calYear}-${String(_calMonth).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    const info = dailyMap.get(dateStr);
    const sec = info?.total_sec || 0;
    const intensity = sec > 0 ? Math.min(1, sec / monthMax) : 0;
    const bg = sec > 0 ? `hsla(210,60%,50%,${0.25 + intensity * 0.75})` : 'var(--bg-elevated)';
    const isSelected = _dayFilter === dateStr;
    const isToday = dateStr === todayStr;

    const games = info?.games || [];
    const topGame = games[0];
    const stripes = games.slice(0, 3).map(g => {
      const pct = (g.sec / sec) * 100;
      return `<div style="height:3px;width:${pct}%;background:${gameColor(g.game_name)};display:inline-block"></div>`;
    }).join('');

    const tooltip = games.length
      ? `${dateStr}: ${fmtDuration(sec)}\n` + games.map(g => `・${g.game_name}: ${fmtDuration(g.sec)}`).join('\n')
      : `${dateStr}: データなし`;

    html += `
      <div class="cal-cell ${isSelected ? 'cal-selected' : ''} ${isToday ? 'cal-today' : ''}"
           data-day="${dateStr}" title="${escapeHtml(tooltip)}"
           style="background:${bg};cursor:${sec > 0 ? 'pointer' : 'default'}">
        <div class="cal-date">${d}</div>
        <div class="cal-sec">${sec > 0 ? fmtDuration(sec) : ''}</div>
        <div class="cal-stripes" style="display:flex;gap:1px;margin-top:2px">${stripes}</div>
      </div>`;
  }

  grid.innerHTML = html;
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
    const filters = [];
    if (_dayFilter) filters.push(_dayFilter);
    if (_gameFilter) filters.push(`「${_gameFilter}」`);
    const filterText = filters.length ? ` (${filters.join(' / ')})` : '';
    meta.textContent = `${data.total} 件${filterText}`;
  }
  if (pager) {
    const from = sessions.length ? _sessionOffset + 1 : 0;
    const to = _sessionOffset + sessions.length;
    pager.textContent = `${from} - ${to} / ${data.total}`;
  }
  document.getElementById('a-session-prev').disabled = _sessionOffset <= 0;
  document.getElementById('a-session-next').disabled = _sessionOffset + sessions.length >= data.total;
  document.getElementById('a-clear-filter').style.display = (_gameFilter || _dayFilter) ? '' : 'none';
}

function updateViewMode() {
  document.querySelectorAll('#a-view-toggle .pill').forEach(el => {
    el.classList.toggle('active', el.dataset.view === _viewMode);
  });
  document.getElementById('a-view-bar').style.display = _viewMode === 'bar' ? '' : 'none';
  document.getElementById('a-view-calendar').style.display = _viewMode === 'calendar' ? '' : 'none';
}

function updatePeriodPills() {
  const ra = rangeActive();
  document.querySelectorAll('#a-period-pills .pill').forEach(el => {
    el.classList.toggle('active', !ra && Number(el.dataset.days) === _days);
  });
  const clearBtn = document.getElementById('a-range-clear');
  if (clearBtn) clearBtn.style.display = ra ? '' : 'none';
}

// ------------------------------------------------------------
// mount
// ------------------------------------------------------------
export async function mount() {
  // 初期月 = 今月
  const now = new Date();
  _calYear = now.getFullYear();
  _calMonth = now.getMonth() + 1;
  updateViewMode();

  // 期間pill
  document.getElementById('a-period-pills').addEventListener('click', ev => {
    const btn = ev.target.closest('.pill');
    if (!btn) return;
    _days = Number(btn.dataset.days);
    _rangeStart = '';
    _rangeEnd = '';
    document.getElementById('a-range-start').value = '';
    document.getElementById('a-range-end').value = '';
    refreshAll();
  });

  // 日付範囲適用
  document.getElementById('a-range-apply').addEventListener('click', () => {
    const s = document.getElementById('a-range-start').value;
    const e = document.getElementById('a-range-end').value;
    if (!s || !e) return;
    if (s > e) { alert('開始日が終了日より後になっています'); return; }
    _rangeStart = s;
    _rangeEnd = e;
    refreshAll();
  });
  document.getElementById('a-range-clear').addEventListener('click', () => {
    _rangeStart = '';
    _rangeEnd = '';
    document.getElementById('a-range-start').value = '';
    document.getElementById('a-range-end').value = '';
    refreshAll();
  });
  // Enterキーで適用
  ['a-range-start', 'a-range-end'].forEach(id => {
    document.getElementById(id).addEventListener('keydown', ev => {
      if (ev.key === 'Enter') document.getElementById('a-range-apply').click();
    });
  });

  // 表示モード切替
  document.getElementById('a-view-toggle').addEventListener('click', ev => {
    const btn = ev.target.closest('.pill');
    if (!btn) return;
    _viewMode = btn.dataset.view;
    updateViewMode();
    loadDaily();
  });

  // カレンダー月ナビ
  document.getElementById('a-cal-prev').addEventListener('click', () => {
    _calMonth--;
    if (_calMonth < 1) { _calMonth = 12; _calYear--; }
    loadCalendar();
  });
  document.getElementById('a-cal-next').addEventListener('click', () => {
    _calMonth++;
    if (_calMonth > 12) { _calMonth = 1; _calYear++; }
    loadCalendar();
  });

  // カレンダーセルクリック → その日のみに絞込み
  document.getElementById('a-cal-grid').addEventListener('click', ev => {
    const cell = ev.target.closest('.cal-cell');
    if (!cell || cell.classList.contains('cal-empty')) return;
    const day = cell.dataset.day;
    if (!day) return;
    _dayFilter = (_dayFilter === day) ? '' : day;
    loadCalendar();
    loadSessions(true);
    document.getElementById('a-session-table').scrollIntoView({ behavior: 'smooth', block: 'center' });
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
    _dayFilter = '';
    loadSessions(true);
    loadSummaryAndStats();
    if (_viewMode === 'calendar') loadCalendar();
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
  _dayFilter = '';
  _rangeStart = '';
  _rangeEnd = '';
  _sessionOffset = 0;
  _viewMode = 'bar';
}
