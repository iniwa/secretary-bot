/** Toast 通知（src/web/static/js/app.js と同じ API）。
 *
 * app.js から分離した理由: 各ページ (.js) が `../app.js` から toast を import すると、
 * HTML の <script src="app.js?v=..."> 経由で読み込まれた app.js とは
 * URL が異なるため別モジュールとして二重評価され、ルーター state
 * (containers / _navChain) が複数生成されてページが多重表示される。
 */
export function toast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => {
    el.classList.add('removing');
    el.addEventListener('animationend', () => el.remove());
  }, 3000);
}
