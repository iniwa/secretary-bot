/** Chat page — message display, SSE flow tracking, reply routing. */
import { api } from '../api.js';
import { toast } from '../app.js';

let chatBusy = false;
let replyContext = null;   // { unit, preview }
let sseConnection = null;  // persistent SSE
let pendingFlowId = null;  // flow_id we're waiting for

// ============================================================
// Markdown (lightweight — no external lib)
// ============================================================
function renderMarkdown(text) {
  let html = escapeHtml(text);
  // code blocks
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
  // inline code
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
  // bold
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  // italic
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
  // headers
  html = html.replace(/^### (.+)$/gm, '<h4>$1</h4>');
  html = html.replace(/^## (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^# (.+)$/gm, '<h2>$1</h2>');
  // blockquote
  html = html.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');
  // unordered list
  html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
  html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
  // links
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  // line breaks (but not inside pre)
  html = html.replace(/(?<!\n)\n(?!\n)/g, '<br>');
  // paragraphs (double newline)
  html = html.replace(/\n{2,}/g, '</p><p>');
  if (!html.startsWith('<')) html = '<p>' + html + '</p>';
  return html;
}

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function fmtTime(ts) {
  if (!ts) return '';
  const m = ts.match(/(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})/);
  if (!m) return ts;
  return `${parseInt(m[2])}/${parseInt(m[3])} ${m[4]}:${m[5]}`;
}

// ============================================================
// Render
// ============================================================
export function render() {
  return `
<div class="chat-page">
  <div class="chat-messages" id="chat-messages"></div>
  <div class="chat-input-area">
    <div class="reply-bar" id="reply-bar">
      <span class="reply-icon">&#8617;</span>
      <span class="reply-unit" id="reply-unit"></span>
      <span class="reply-preview" id="reply-preview"></span>
      <button class="reply-close" id="reply-close" title="Cancel">&times;</button>
    </div>
    <form class="chat-form" id="chat-form">
      <textarea id="chat-input" class="form-input" placeholder="Message... (Shift+Enter for newline)" rows="1" autocomplete="off"></textarea>
      <button type="submit" class="btn btn-primary chat-send" id="chat-send">Send</button>
    </form>
  </div>
</div>`;
}

// ============================================================
// Mount
// ============================================================
export async function mount() {
  const form = document.getElementById('chat-form');
  const input = document.getElementById('chat-input');
  const sendBtn = document.getElementById('chat-send');

  // Send
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    sendMessage(input, sendBtn);
  });

  // Enter key
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input, sendBtn);
    }
  });

  // Auto-resize
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = input.scrollHeight + 'px';
  });

  // Reply close
  document.getElementById('reply-close').addEventListener('click', clearReply);

  // Connect SSE
  connectSSE();

  // Load history
  await loadHistory();
  input.focus();
}

export function unmount() {
  if (sseConnection) {
    sseConnection.close();
    sseConnection = null;
  }
}

// ============================================================
// Message Display
// ============================================================
function appendMessage(role, text, unit, channel, channelName) {
  const container = document.getElementById('chat-messages');
  if (!container) return;

  const el = document.createElement('div');
  el.className = 'chat-msg chat-msg-' + role;
  if (channel && channel !== 'webgui') el.classList.add('chat-msg-external');

  if (role === 'assistant') {
    // Markdown body
    const body = document.createElement('div');
    body.className = 'chat-msg-body';
    body.innerHTML = renderMarkdown(text);
    el.appendChild(body);

    // Reply button
    const replyBtn = document.createElement('button');
    replyBtn.className = 'chat-reply-btn';
    replyBtn.innerHTML = '&#8617;';
    replyBtn.title = 'Reply to this unit';
    replyBtn.addEventListener('click', () => setReply(unit, text));
    el.appendChild(replyBtn);
  } else {
    el.innerHTML = escapeHtml(text).replace(/\n/g, '<br>');
  }

  // Badges
  const meta = document.createElement('div');
  meta.className = 'chat-msg-meta';
  if (unit && unit !== 'chat') {
    const badge = document.createElement('span');
    badge.className = 'badge badge-accent';
    badge.textContent = unit;
    meta.appendChild(badge);
  }
  if (channel && channel !== 'webgui') {
    const badge = document.createElement('span');
    badge.className = 'badge badge-info has-tooltip';
    const label = channel === 'discord' ? 'Discord' : channel === 'discord_dm' ? 'DM' : channel;
    badge.textContent = label;
    // Tooltip with channel name
    if (channelName) {
      const tip = document.createElement('span');
      tip.className = 'tooltip';
      tip.textContent = `#${channelName}`;
      badge.appendChild(tip);
    }
    meta.appendChild(badge);
  }
  if (meta.children.length) el.appendChild(meta);

  container.appendChild(el);
  el.scrollIntoView({ behavior: 'smooth' });
}

function appendThinking() {
  const container = document.getElementById('chat-messages');
  if (!container) return null;
  const el = document.createElement('div');
  el.className = 'chat-msg chat-msg-assistant chat-thinking';
  el.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span>';
  container.appendChild(el);
  el.scrollIntoView({ behavior: 'smooth' });
  return el;
}

// ============================================================
// Reply Context
// ============================================================
function setReply(unit, text) {
  replyContext = { unit: unit || null, preview: text.substring(0, 80) };
  const bar = document.getElementById('reply-bar');
  bar.classList.add('active');
  document.getElementById('reply-unit').textContent = unit ? `[${unit}]` : '';
  document.getElementById('reply-preview').textContent = replyContext.preview + (text.length > 80 ? '...' : '');
  document.getElementById('chat-input').focus();
}

function clearReply() {
  replyContext = null;
  document.getElementById('reply-bar')?.classList.remove('active');
}

// ============================================================
// Send
// ============================================================
async function sendMessage(input, sendBtn) {
  if (chatBusy) return;
  const msg = input.value.trim();
  if (!msg) return;

  input.value = '';
  input.style.height = 'auto';
  chatBusy = true;
  sendBtn.disabled = true;

  appendMessage('user', msg);
  const thinking = appendThinking();

  const payload = { message: msg };
  if (replyContext?.unit) payload.reply_unit = replyContext.unit;
  clearReply();

  function done() {
    chatBusy = false;
    sendBtn.disabled = false;
    pendingFlowId = null;
    input.focus();
  }

  try {
    const data = await api('/api/chat', { method: 'POST', body: payload });
    const flowId = data.flow_id;
    if (!flowId) {
      thinking?.remove();
      appendMessage('assistant', data.response || '(empty)', data.unit);
      done();
      return;
    }

    // Wait for REPLY event via SSE
    pendingFlowId = flowId;
    const timeout = setTimeout(() => {
      if (pendingFlowId === flowId) {
        thinking?.remove();
        appendMessage('assistant', '(timeout — no response received)');
        done();
      }
    }, 120000);

    // Store callback for SSE handler
    window.__chatCallback = (event) => {
      if (event.flow_id !== flowId || event.node !== 'REPLY') return false;
      clearTimeout(timeout);
      thinking?.remove();
      const detail = event.detail || {};
      appendMessage('assistant', detail.response || '(empty)', detail.unit);
      done();
      window.__chatCallback = null;
      return true;
    };
  } catch (err) {
    thinking?.remove();
    appendMessage('assistant', 'Error: ' + err.message);
    done();
  }
}

// ============================================================
// SSE
// ============================================================
function connectSSE() {
  if (sseConnection) sseConnection.close();

  const sse = new EventSource('/api/flow/stream');
  sseConnection = sse;

  sse.onmessage = (ev) => {
    try {
      const event = JSON.parse(ev.data);
      // Notification events (InnerMind speaks etc.)
      if (event.type === 'notification' && event.detail?.message) {
        toast(event.detail.message, 'info');
      }
      // Chat response callback
      if (window.__chatCallback) {
        window.__chatCallback(event);
      }
    } catch { /* ignore */ }
  };

  sse.onerror = () => {
    sse.close();
    sseConnection = null;
    setTimeout(connectSSE, 3000);
  };
}

// ============================================================
// History
// ============================================================
async function loadHistory() {
  try {
    const data = await api('/api/logs', { params: { limit: 50, bot_only: 1 } });
    const msgs = (data.logs || []).reverse();
    msgs.forEach(l => {
      appendMessage(l.role, l.content, l.unit, l.channel, l.channel_name);
    });
  } catch (err) {
    console.error('Chat history load failed:', err);
  }
}
