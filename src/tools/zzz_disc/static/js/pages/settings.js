/** HoYoLAB 設定 — cookie 登録・同期 */
import { api } from '../api.js';
import { escapeHtml, toast } from '../app.js';
import { HOYOLAB_REGIONS } from '../labels.js';

export function render() {
  return `
    <div class="page-header">
      <h2>⚙️ HoYoLAB 設定</h2>
    </div>

    <div class="settings-section">
      <h3>アカウント情報</h3>
      <div class="hint">
        HoYoLAB で取得した cookie（<span class="mono">ltuid_v2</span> / <span class="mono">ltoken_v2</span>）を登録すると、戦績から自動的にディスク装備を取り込めます。
        ブラウザの DevTools → Application → Cookies → hoyolab.com から値を確認してください。
      </div>

      <div class="form-grid">
        <label>UID</label>
        <input type="text" id="f-uid" placeholder="ゲーム内 UID（例: 100012345）" />

        <label>リージョン</label>
        <select id="f-region">
          ${HOYOLAB_REGIONS.map(r => `<option value="${escapeHtml(r.value)}">${escapeHtml(r.label)}</option>`).join('')}
        </select>

        <label>ltuid_v2</label>
        <input type="text" id="f-ltuid" placeholder="cookie の ltuid_v2" autocomplete="off" />

        <label>ltoken_v2</label>
        <input type="password" id="f-ltoken" placeholder="cookie の ltoken_v2" autocomplete="off" />

        <label>ニックネーム</label>
        <input type="text" id="f-nickname" placeholder="（任意・表示用）" />
      </div>

      <div class="row mt-2">
        <button class="btn btn-primary" id="save-btn">保存</button>
        <button class="btn" id="test-btn">接続テスト</button>
        <div class="flex-1"></div>
        <span id="save-status" class="text-sm text-muted"></span>
      </div>
    </div>

    <div class="settings-section">
      <h3>同期</h3>
      <div class="hint">
        保存済みアカウントを使って、プロフィール（showcase）から装備情報を取り込みます。
      </div>
      <div class="row">
        <button class="btn btn-primary" id="sync-all-btn">全キャラ同期</button>
        <div class="flex-1"></div>
        <span id="sync-status" class="text-sm text-muted"></span>
      </div>
      <div id="sync-progress" class="hidden">
        <div class="progress-bar"><div class="progress-bar-fill" style="width:0%"></div></div>
      </div>
      <div id="sync-log" class="mt-2 text-sm"></div>
    </div>
  `;
}

export async function mount() {
  document.getElementById('save-btn').addEventListener('click', save);
  document.getElementById('test-btn').addEventListener('click', testConnection);
  document.getElementById('sync-all-btn').addEventListener('click', syncAll);
  await loadAccount();
}

async function loadAccount() {
  try {
    const acc = await api('/hoyolab/account');
    if (!acc) return;
    setVal('f-uid', acc.uid);
    setVal('f-region', acc.region);
    setVal('f-ltuid', acc.ltuid_v2);
    // ltoken はマスク済みで返る想定だがそのまま入れる
    setVal('f-ltoken', acc.ltoken_v2);
    setVal('f-nickname', acc.nickname);
    document.getElementById('save-status').textContent = acc.nickname
      ? `登録済み: ${acc.nickname}`
      : '登録済み';
  } catch (err) {
    // 未登録なら 404 で来る想定。無視
    if (!/404/.test(err.message)) console.warn(err);
  }
}

function setVal(id, v) {
  const el = document.getElementById(id);
  if (el && v != null) el.value = v;
}

function readForm() {
  return {
    uid: document.getElementById('f-uid').value.trim(),
    region: document.getElementById('f-region').value,
    ltuid_v2: document.getElementById('f-ltuid').value.trim(),
    ltoken_v2: document.getElementById('f-ltoken').value.trim(),
    nickname: document.getElementById('f-nickname').value.trim() || null,
  };
}

async function save() {
  const body = readForm();
  if (!body.uid || !body.ltuid_v2 || !body.ltoken_v2) {
    toast('UID / ltuid_v2 / ltoken_v2 は必須です', 'warning');
    return;
  }
  try {
    await api('/hoyolab/account', { method: 'PUT', body });
    toast('保存しました', 'success');
    document.getElementById('save-status').textContent = '保存済み';
  } catch (err) {
    toast(`保存失敗: ${err.message}`, 'error');
  }
}

async function testConnection() {
  const status = document.getElementById('save-status');
  status.textContent = 'テスト中...';
  try {
    const res = await api('/hoyolab/account', { method: 'GET', params: { test: 1 } });
    if (res?.ok || res?.nickname) {
      status.textContent = res.nickname ? `✓ 接続OK (${res.nickname})` : '✓ 接続OK';
      toast('接続成功', 'success');
    } else {
      status.textContent = '?';
      toast('応答はあったが内容不明', 'warning');
    }
  } catch (err) {
    status.textContent = '✗ 接続失敗';
    toast(`接続失敗: ${err.message}`, 'error');
  }
}

async function syncAll() {
  const btn = document.getElementById('sync-all-btn');
  const log = document.getElementById('sync-log');
  const progress = document.getElementById('sync-progress');
  const fill = progress.querySelector('.progress-bar-fill');
  const status = document.getElementById('sync-status');

  btn.disabled = true;
  status.textContent = '同期中...';
  progress.classList.remove('hidden');
  fill.style.width = '10%';
  log.innerHTML = '';

  try {
    const res = await api('/hoyolab/sync', { method: 'POST' });
    fill.style.width = '100%';
    const items = Array.isArray(res?.results) ? res.results : [];
    status.textContent = `完了: ${items.length} 件`;
    if (items.length) {
      log.innerHTML = `
        <div class="card">
          <h3 class="mb-1">結果</h3>
          ${items.map(r => `
            <div class="row mb-1">
              <span>${escapeHtml(r.name_ja || r.slug || '-')}</span>
              <div class="flex-1"></div>
              <span class="text-sm ${r.ok ? '' : 'text-muted'}" style="color:${r.ok ? 'var(--success)' : 'var(--error)'};">
                ${r.ok ? '✓ 取得' : `✗ ${escapeHtml(r.error || '失敗')}`}
              </span>
            </div>
          `).join('')}
        </div>
      `;
    } else if (res?.ok) {
      status.textContent = '同期完了';
      log.innerHTML = `<div class="text-muted text-sm">詳細情報なし</div>`;
    }
    toast('同期完了', 'success');
  } catch (err) {
    status.textContent = '同期失敗';
    toast(`同期失敗: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
    setTimeout(() => progress.classList.add('hidden'), 800);
  }
}
