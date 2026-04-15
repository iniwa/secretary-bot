/** HoYoLAB 設定 — cookie 登録・同期 */
import { api } from '../api.js';
import { escapeHtml, toast, getUiPrefs, setUiPref } from '../app.js';
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
      <h3>🔐 自動ログイン（miHoYo / HoYoverse ID）</h3>
      <div class="hint">
        email/password を保存しておくと、cookie 失効時に自動で再取得します。
        2FA / captcha が有効なアカウントでは動作しません（その場合は手動 cookie 登録をご利用ください）。
        <br><strong>注意:</strong> password は平文で Pi 上の SQLite に保存されます。
      </div>
      <div class="form-grid">
        <label>email</label>
        <input type="email" id="f-email" placeholder="HoYoverse ID の email" autocomplete="off" />

        <label>password</label>
        <input type="password" id="f-password" placeholder="（未変更なら空欄）" autocomplete="new-password" />

        <label>自動ログイン</label>
        <label class="inline-label"><input type="checkbox" id="f-auto-enabled" /> 有効化</label>
      </div>
      <div class="row mt-2">
        <button class="btn btn-primary" id="save-cred-btn">資格情報を保存</button>
        <button class="btn" id="auto-login-btn">今すぐ自動ログイン</button>
        <button class="btn" id="refresh-btn">cookie 再取得</button>
        <button class="btn btn-danger" id="clear-cred-btn">資格情報を削除</button>
        <div class="flex-1"></div>
        <span id="cred-status" class="text-sm text-muted"></span>
      </div>
    </div>

    <div class="settings-section">
      <h3>🖥️ UI 表示設定</h3>
      <div class="hint">
        使わない機能のメニューを非表示にできます。設定はこのブラウザにのみ保存されます。
      </div>
      <div class="row mt-2">
        <label class="inline-label">
          <input type="checkbox" id="pref-show-capture" />
          画面キャプチャ機能を表示
        </label>
        <span class="text-sm text-muted">（Windows Agent 経由の VLM 解析。HoYoLAB 同期で基本足りるため既定は非表示）</span>
      </div>
      <div class="row mt-2">
        <label class="inline-label">
          <input type="checkbox" id="pref-show-upload" />
          画像アップロード機能を表示
        </label>
        <span class="text-sm text-muted">（HoYoLAB 同期で基本足りるため既定は非表示）</span>
      </div>
    </div>

    <div class="settings-section">
      <h3>同期</h3>
      <div class="hint">
        保存済みアカウントを使って、プロフィール（showcase）から装備情報を取り込みます。
      </div>
      <div class="row">
        <button class="btn btn-primary" id="sync-all-btn">全キャラ同期</button>
        <button class="btn" id="cleanup-empty-btn" title="ビルドが1件も無いキャラ（未所持シード）を削除">未所持キャラを削除</button>
        <button class="btn btn-danger" id="reset-btn" title="HoYoLAB 由来のディスク/ビルド/重複キャラを全削除">同期データをリセット</button>
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
  document.getElementById('save-cred-btn').addEventListener('click', saveCredentials);
  document.getElementById('auto-login-btn').addEventListener('click', autoLogin);
  document.getElementById('refresh-btn').addEventListener('click', refreshCookie);
  document.getElementById('clear-cred-btn').addEventListener('click', clearCredentials);
  document.getElementById('reset-btn').addEventListener('click', resetSynced);
  document.getElementById('cleanup-empty-btn').addEventListener('click', cleanupEmpty);

  const captureToggle = document.getElementById('pref-show-capture');
  captureToggle.checked = !!getUiPrefs().show_capture;
  captureToggle.addEventListener('change', (ev) => {
    setUiPref('show_capture', ev.target.checked);
    toast(ev.target.checked ? 'キャプチャ機能を表示しました' : 'キャプチャ機能を非表示にしました', 'info');
  });

  const uploadToggle = document.getElementById('pref-show-upload');
  uploadToggle.checked = !!getUiPrefs().show_upload;
  uploadToggle.addEventListener('change', (ev) => {
    setUiPref('show_upload', ev.target.checked);
    toast(ev.target.checked ? 'アップロード機能を表示しました' : 'アップロード機能を非表示にしました', 'info');
  });

  await loadAccount();
}

async function cleanupEmpty() {
  if (!confirm('ビルドが 1 件も無いキャラ（未所持シード）を削除します。よろしいですか？')) return;
  const btn = document.getElementById('cleanup-empty-btn');
  btn.disabled = true;
  try {
    const res = await api('/characters/cleanup-empty', { method: 'POST' });
    const names = (res.deleted || []).map(d => d.name_ja).join('、') || '（なし）';
    toast(`削除: ${res.chars} 件 — ${names}`, 'success');
  } catch (err) {
    toast(`削除失敗: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
  }
}

async function resetSynced() {
  if (!confirm('HoYoLAB 同期で作成されたディスク・ビルド・重複キャラを全削除します。よろしいですか？\n（プリセットの標準キャラは残ります）')) return;
  const btn = document.getElementById('reset-btn');
  btn.disabled = true;
  try {
    const res = await api('/hoyolab/reset', { method: 'POST' });
    toast(`リセット完了: discs=${res.discs} / builds=${res.builds} / chars=${res.chars}`, 'success');
  } catch (err) {
    toast(`リセット失敗: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
  }
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
    setVal('f-email', acc.email);
    const cb = document.getElementById('f-auto-enabled');
    if (cb) cb.checked = !!acc.auto_login_enabled;
    document.getElementById('save-status').textContent = acc.nickname
      ? `登録済み: ${acc.nickname}`
      : '登録済み';
    const cs = document.getElementById('cred-status');
    if (acc.last_auto_login_error) {
      cs.textContent = `前回エラー: ${acc.last_auto_login_error}`;
    } else if (acc.last_auto_login_at) {
      cs.textContent = `前回ログイン: ${acc.last_auto_login_at}`;
    } else if (acc.has_password) {
      cs.textContent = '資格情報あり（未ログイン）';
    }
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

async function saveCredentials() {
  const email = document.getElementById('f-email').value.trim();
  const password = document.getElementById('f-password').value;
  const autoEnabled = document.getElementById('f-auto-enabled').checked;
  if (!email || !password) {
    toast('email と password の両方を入力してください', 'warning');
    return;
  }
  try {
    await api('/hoyolab/credentials', {
      method: 'PUT',
      body: { email, password, auto_login_enabled: autoEnabled },
    });
    document.getElementById('f-password').value = '';
    toast('資格情報を保存しました', 'success');
    document.getElementById('cred-status').textContent = '保存済み';
  } catch (err) {
    toast(`保存失敗: ${err.message}`, 'error');
  }
}

async function autoLogin() {
  const email = document.getElementById('f-email').value.trim();
  const password = document.getElementById('f-password').value;
  const autoEnabled = document.getElementById('f-auto-enabled').checked;
  const uid = document.getElementById('f-uid').value.trim();
  const region = document.getElementById('f-region').value;
  const nickname = document.getElementById('f-nickname').value.trim() || null;
  if (!email || !password) {
    toast('email / password を入力してください', 'warning');
    return;
  }
  const btn = document.getElementById('auto-login-btn');
  const cs = document.getElementById('cred-status');
  btn.disabled = true;
  cs.textContent = 'ログイン中...';
  try {
    const res = await api('/hoyolab/auto-login', {
      method: 'POST',
      body: {
        email, password,
        uid: uid || null, region: region || null, nickname,
        save_credentials: autoEnabled,
      },
    });
    document.getElementById('f-password').value = '';
    toast('ログイン成功、cookie を取得しました', 'success');
    cs.textContent = `ログイン成功 (ltuid=${res.ltuid_v2})`;
    await loadAccount();
  } catch (err) {
    cs.textContent = '✗ ログイン失敗';
    toast(`ログイン失敗: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
  }
}

async function refreshCookie() {
  const btn = document.getElementById('refresh-btn');
  const cs = document.getElementById('cred-status');
  btn.disabled = true;
  cs.textContent = '更新中...';
  try {
    const res = await api('/hoyolab/refresh', { method: 'POST' });
    toast('cookie を再取得しました', 'success');
    cs.textContent = `更新成功 (ltuid=${res.ltuid_v2})`;
    await loadAccount();
  } catch (err) {
    cs.textContent = '✗ 更新失敗';
    toast(`更新失敗: ${err.message}`, 'error');
  } finally {
    btn.disabled = false;
  }
}

async function clearCredentials() {
  if (!confirm('保存された email/password を削除しますか？')) return;
  try {
    await api('/hoyolab/credentials', { method: 'DELETE' });
    document.getElementById('f-email').value = '';
    document.getElementById('f-password').value = '';
    document.getElementById('f-auto-enabled').checked = false;
    document.getElementById('cred-status').textContent = '削除しました';
    toast('資格情報を削除しました', 'success');
  } catch (err) {
    toast(`削除失敗: ${err.message}`, 'error');
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
