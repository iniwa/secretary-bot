/** Timer page — active timers with real-time countdown. */
import { api } from '../api.js';
import { toast } from '../app.js';

let countdownInterval = null;
let refreshInterval = null;
let timers = [];

function $(id) { return document.getElementById(id); }

// ============================================================
// Formatting
// ============================================================
function formatCountdown(totalSec) {
  if (totalSec <= 0) return '00:00';
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = Math.floor(totalSec % 60);
  const mm = String(m).padStart(2, '0');
  const ss = String(s).padStart(2, '0');
  if (h > 0) return `${String(h).padStart(2, '0')}:${mm}:${ss}`;
  return `${mm}:${ss}`;
}

function progressPercent(timer) {
  const totalSec = timer.minutes * 60;
  if (totalSec <= 0) return 100;
  const elapsed = totalSec - timer.remaining_sec;
  return Math.min(100, Math.max(0, (elapsed / totalSec) * 100));
}

function isCompleted(timer) {
  return timer.remaining_sec <= 0;
}

// ============================================================
// Render
// ============================================================
export function render() {
  return `
<style>
  .timer-page {
    max-width: 900px;
    margin: 0 auto;
  }
  .timer-page-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 1.25rem;
  }
  .timer-page-header h2 {
    font-size: 1.125rem;
    font-weight: 600;
    color: var(--text-primary);
    margin: 0;
  }
  .timer-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 1rem;
  }
  @media (max-width: 768px) {
    .timer-grid {
      grid-template-columns: 1fr;
    }
  }
  .timer-card {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 1.25rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    transition: border-color var(--ease);
  }
  .timer-card:hover {
    border-color: var(--border-hover);
  }
  .timer-card.completed {
    border-color: var(--success);
    border-color: rgba(34, 197, 94, 0.3);
  }
  .timer-card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
  }
  .timer-message {
    font-size: 0.875rem;
    font-weight: 500;
    color: var(--text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
  }
  .timer-duration {
    font-size: 0.75rem;
    color: var(--text-muted);
    white-space: nowrap;
  }
  .timer-countdown {
    font-family: 'Cascadia Code', 'Fira Code', 'SF Mono', monospace;
    font-size: 2.25rem;
    font-weight: 700;
    color: var(--text-primary);
    text-align: center;
    letter-spacing: 0.04em;
    line-height: 1.2;
    padding: 0.5rem 0;
  }
  .timer-card.completed .timer-countdown {
    color: var(--success);
    font-size: 1.5rem;
  }
  .timer-progress-track {
    width: 100%;
    height: 4px;
    background: var(--bg-overlay);
    border-radius: 2px;
    overflow: hidden;
  }
  .timer-progress-bar {
    height: 100%;
    background: var(--accent);
    border-radius: 2px;
    transition: width 1s linear;
  }
  .timer-card.completed .timer-progress-bar {
    background: var(--success);
    width: 100% !important;
  }
  .timer-footer {
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .badge-completed {
    background: var(--success-muted);
    color: var(--success);
    font-size: 0.75rem;
    font-weight: 600;
    padding: 0.2rem 0.6rem;
    border-radius: 999px;
  }
  .timer-empty {
    text-align: center;
    padding: 4rem 2rem;
    color: var(--text-muted);
    font-size: 0.9375rem;
  }
  .timer-empty-icon {
    font-size: 2.5rem;
    margin-bottom: 0.75rem;
    opacity: 0.4;
  }
</style>

<div class="timer-page">
  <div class="timer-page-header">
    <h2>Active Timers</h2>
    <button class="btn btn-sm" id="timer-refresh">Refresh</button>
  </div>
  <div id="timer-container">
    <div class="timer-empty">Loading...</div>
  </div>
</div>`;
}

// ============================================================
// Timer card rendering
// ============================================================
function renderTimerCard(timer) {
  const done = isCompleted(timer);
  const pct = progressPercent(timer);
  const doneClass = done ? ' completed' : '';
  const durationLabel = timer.minutes >= 60
    ? `${Math.floor(timer.minutes / 60)}h ${timer.minutes % 60}m`
    : `${timer.minutes}m`;

  return `
    <div class="timer-card${doneClass}" data-timer-id="${timer.id}">
      <div class="timer-card-header">
        <div class="timer-message" title="${escAttr(timer.message)}">${esc(timer.message)}</div>
        <div class="timer-duration">${durationLabel}</div>
      </div>
      <div class="timer-countdown" data-countdown="${timer.id}">
        ${done ? 'Completed!' : formatCountdown(timer.remaining_sec)}
      </div>
      <div class="timer-progress-track">
        <div class="timer-progress-bar" data-progress="${timer.id}" style="width: ${pct}%"></div>
      </div>
      ${done ? '<div class="timer-footer"><span class="badge-completed">Completed!</span></div>' : ''}
    </div>`;
}

function renderTimers() {
  const container = $('timer-container');
  if (!container) return;

  if (timers.length === 0) {
    container.innerHTML = `
      <div class="timer-empty">
        <div class="timer-empty-icon">&#9202;</div>
        <div>No active timers</div>
      </div>`;
    return;
  }

  container.innerHTML = `<div class="timer-grid">${timers.map(renderTimerCard).join('')}</div>`;
}

// ============================================================
// Helpers
// ============================================================
function esc(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function escAttr(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ============================================================
// Data fetching
// ============================================================
async function fetchTimers() {
  try {
    const data = await api('/api/units/timers');
    timers = (data?.items || []).map(t => ({ ...t }));
    renderTimers();
  } catch (err) {
    toast('Failed to load timers', 'error');
    console.error(err);
  }
}

// ============================================================
// Countdown tick
// ============================================================
function tick() {
  let anyActive = false;

  for (const timer of timers) {
    if (timer.remaining_sec <= 0) continue;
    anyActive = true;
    timer.remaining_sec = Math.max(0, timer.remaining_sec - 1);

    const countdownEl = document.querySelector(`[data-countdown="${timer.id}"]`);
    const progressEl = document.querySelector(`[data-progress="${timer.id}"]`);
    const cardEl = document.querySelector(`[data-timer-id="${timer.id}"]`);

    if (countdownEl) {
      if (timer.remaining_sec <= 0) {
        countdownEl.textContent = 'Completed!';
        if (cardEl) {
          cardEl.classList.add('completed');
          // Add completed badge if not present
          const footer = cardEl.querySelector('.timer-footer');
          if (!footer) {
            cardEl.insertAdjacentHTML('beforeend',
              '<div class="timer-footer"><span class="badge-completed">Completed!</span></div>');
          }
        }
      } else {
        countdownEl.textContent = formatCountdown(timer.remaining_sec);
      }
    }

    if (progressEl) {
      progressEl.style.width = `${progressPercent(timer)}%`;
    }
  }

  // If no active timers remain, we can stop ticking (refresh will restart)
  if (!anyActive && countdownInterval) {
    clearInterval(countdownInterval);
    countdownInterval = null;
  }
}

function startCountdown() {
  if (countdownInterval) clearInterval(countdownInterval);
  const hasActive = timers.some(t => t.remaining_sec > 0);
  if (hasActive) {
    countdownInterval = setInterval(tick, 1000);
  }
}

// ============================================================
// Mount / Unmount
// ============================================================
export async function mount() {
  $('timer-refresh')?.addEventListener('click', async () => {
    await fetchTimers();
    startCountdown();
    toast('Timers refreshed', 'info');
  });

  await fetchTimers();
  startCountdown();

  // Auto-refresh every 30 seconds to sync with server
  refreshInterval = setInterval(async () => {
    await fetchTimers();
    startCountdown();
  }, 30000);
}

export function unmount() {
  if (countdownInterval) {
    clearInterval(countdownInterval);
    countdownInterval = null;
  }
  if (refreshInterval) {
    clearInterval(refreshInterval);
    refreshInterval = null;
  }
  timers = [];
}
