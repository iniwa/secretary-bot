/** ファイルアップロードフロー（予備） */
import { api } from '../api.js';
import { escapeHtml, toast } from '../app.js';

export function render() {
  return `
    <div class="page-header">
      <h2>📤 アップロード（予備フロー）</h2>
    </div>
    <p class="text-muted text-sm mb-2">
      通常はワークベンチの「今の画面を解析」を使ってください。画面キャプチャができない場合にここから画像をアップロードできます。
    </p>
    <div class="drop-zone" id="drop-zone">
      <div class="big-icon">📁</div>
      <div>画像をドラッグ＆ドロップ or クリックで選択</div>
      <div class="text-muted text-sm mt-1">PNG / JPEG対応</div>
      <input type="file" id="file-input" accept="image/*" multiple class="hidden" />
    </div>
    <div id="upload-status" class="mt-2"></div>
  `;
}

export async function mount() {
  const dz = document.getElementById('drop-zone');
  const input = document.getElementById('file-input');
  dz.addEventListener('click', () => input.click());
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('drag-over'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drag-over'));
  dz.addEventListener('drop', (e) => {
    e.preventDefault();
    dz.classList.remove('drag-over');
    handleFiles(e.dataTransfer.files);
  });
  input.addEventListener('change', (e) => handleFiles(e.target.files));
}

async function handleFiles(files) {
  const arr = Array.from(files || []);
  if (!arr.length) return;
  const status = document.getElementById('upload-status');
  const results = [];
  for (const file of arr) {
    results.push({ file, state: 'sending' });
  }
  renderStatus(results);
  await Promise.all(arr.map(async (file, i) => {
    try {
      const fd = new FormData();
      fd.append('file', file);
      const job = await api('/jobs/upload', { method: 'POST', body: fd });
      results[i] = { file, state: 'ok', job };
    } catch (err) {
      results[i] = { file, state: 'error', error: err.message };
    }
    renderStatus(results);
  }));
  toast('アップロード完了。解析ワークベンチでジョブを確認してください', 'success');
}

function renderStatus(results) {
  const status = document.getElementById('upload-status');
  status.innerHTML = `
    <div class="card">
      <h3 class="mb-1">送信状況</h3>
      ${results.map(r => `
        <div class="row mb-1">
          <span>${escapeHtml(r.file.name)}</span>
          <span class="text-muted text-sm">${(r.file.size / 1024).toFixed(1)} KB</span>
          <div class="flex-1"></div>
          ${r.state === 'sending' ? '<span class="spinner"></span>'
            : r.state === 'ok' ? `<span class="text-sm" style="color:var(--success);">✓ job #${r.job?.id ?? '?'}</span>`
            : `<span class="text-sm" style="color:var(--error);">× ${escapeHtml(r.error)}</span>`}
        </div>
      `).join('')}
      <div class="mt-2"><a href="#/capture" class="btn btn-sm btn-primary">→ ワークベンチへ</a></div>
    </div>
  `;
}
