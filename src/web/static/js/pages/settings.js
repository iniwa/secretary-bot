/** Settings page. */
import { api, apiBatch } from '../api.js';
import { toast } from '../app.js';

function $(id) { return document.getElementById(id); }

export function render() {
  return `
<style>
  .settings-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 1.25rem;
  }
  @media (max-width: 860px) {
    .settings-grid { grid-template-columns: 1fr; }
  }
  .settings-grid .card-header {
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.6rem;
  }
  .form-group {
    margin-bottom: 0.85rem;
  }
  .form-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
  }
  .form-row .form-input {
    width: auto;
    max-width: 180px;
  }
  .form-hint {
    font-size: 0.7rem;
    color: var(--text-muted);
    margin-top: 0.2rem;
  }
  .card-footer {
    display: flex;
    justify-content: flex-end;
    margin-top: 1rem;
    padding-top: 0.75rem;
    border-top: 1px solid var(--border);
  }
  .warning-text {
    font-size: 0.75rem;
    color: var(--warning);
    background: var(--warning-muted);
    padding: 0.5rem 0.75rem;
    border-radius: var(--radius-sm);
    margin-bottom: 0.85rem;
  }
  /* Toggle switch */
  .toggle-switch {
    position: relative;
    display: inline-block;
    width: 40px;
    height: 22px;
    flex-shrink: 0;
  }
  .toggle-switch input {
    opacity: 0;
    width: 0;
    height: 0;
  }
  .toggle-slider {
    position: absolute;
    inset: 0;
    background: var(--bg-overlay);
    border: 1px solid var(--border);
    border-radius: 999px;
    transition: all var(--ease);
    cursor: pointer;
  }
  .toggle-slider::before {
    content: '';
    position: absolute;
    width: 16px;
    height: 16px;
    left: 2px;
    top: 2px;
    background: var(--text-muted);
    border-radius: 50%;
    transition: all var(--ease);
  }
  .toggle-switch input:checked + .toggle-slider {
    background: var(--accent-muted);
    border-color: var(--accent);
  }
  .toggle-switch input:checked + .toggle-slider::before {
    transform: translateX(18px);
    background: var(--accent);
  }
  .persona-textarea {
    min-height: 200px;
    font-size: 0.8125rem;
    line-height: 1.65;
  }
  .card-full {
    grid-column: 1 / -1;
  }
</style>

<div class="settings-grid">

  <!-- LLM Settings -->
  <div class="card">
    <div class="card-header"><h3>LLM Settings</h3></div>
    <div class="form-group">
      <label class="form-label">Ollama Model</label>
      <select id="s-ollama-model" class="form-input"></select>
    </div>
    <div class="form-group">
      <label class="form-label">Ollama Timeout (sec)</label>
      <input type="number" id="s-ollama-timeout" class="form-input" min="1" step="1">
    </div>
    <div class="form-group">
      <label class="form-label">Gemini Model</label>
      <input type="text" id="s-gemini-model" class="form-input" placeholder="gemini-...">
    </div>
    <div class="card-footer">
      <button class="btn btn-primary btn-sm" id="s-llm-save">Save</button>
    </div>
  </div>

  <!-- Gemini Settings -->
  <div class="card">
    <div class="card-header"><h3>Gemini Settings</h3></div>
    <div class="warning-text">Gemini API usage incurs costs. Enable only what you need.</div>
    <div class="form-group">
      <div class="form-row">
        <label class="form-label" style="margin-bottom:0">Conversation</label>
        <label class="toggle-switch">
          <input type="checkbox" id="s-gemini-conversation">
          <span class="toggle-slider"></span>
        </label>
      </div>
    </div>
    <div class="form-group">
      <div class="form-row">
        <label class="form-label" style="margin-bottom:0">Memory Extraction</label>
        <label class="toggle-switch">
          <input type="checkbox" id="s-gemini-memory">
          <span class="toggle-slider"></span>
        </label>
      </div>
    </div>
    <div class="form-group">
      <div class="form-row">
        <label class="form-label" style="margin-bottom:0">Unit Routing</label>
        <label class="toggle-switch">
          <input type="checkbox" id="s-gemini-routing">
          <span class="toggle-slider"></span>
        </label>
      </div>
    </div>
    <div class="form-group">
      <label class="form-label">Monthly Token Limit</label>
      <input type="number" id="s-gemini-token-limit" class="form-input" min="0" step="1000">
    </div>
    <div class="card-footer">
      <button class="btn btn-primary btn-sm" id="s-gemini-save">Save</button>
    </div>
  </div>

  <!-- Heartbeat Settings -->
  <div class="card">
    <div class="card-header"><h3>Heartbeat Settings</h3></div>
    <div class="form-group">
      <label class="form-label">Interval with Ollama (min)</label>
      <input type="number" id="s-hb-with" class="form-input" min="1" step="1">
    </div>
    <div class="form-group">
      <label class="form-label">Interval without Ollama (min)</label>
      <input type="number" id="s-hb-without" class="form-input" min="1" step="1">
    </div>
    <div class="form-group">
      <label class="form-label">Compact Threshold (messages)</label>
      <input type="number" id="s-hb-compact" class="form-input" min="1" step="1">
    </div>
    <div class="card-footer">
      <button class="btn btn-primary btn-sm" id="s-hb-save">Save</button>
    </div>
  </div>

  <!-- Chat Settings -->
  <div class="card">
    <div class="card-header"><h3>Chat Settings</h3></div>
    <div class="form-group">
      <label class="form-label">History Window (min)</label>
      <input type="number" id="s-chat-history" class="form-input" min="0" step="1">
      <div class="form-hint">0 = unlimited</div>
    </div>
    <div class="card-footer">
      <button class="btn btn-primary btn-sm" id="s-chat-save">Save</button>
    </div>
  </div>

  <!-- Rakuten Search Settings -->
  <div class="card">
    <div class="card-header"><h3>Rakuten Search Settings</h3></div>
    <div class="form-group">
      <label class="form-label">Max Results</label>
      <input type="number" id="s-rakuten-max" class="form-input" min="1" step="1">
    </div>
    <div class="form-group">
      <div class="form-row">
        <label class="form-label" style="margin-bottom:0">Fetch Details</label>
        <label class="toggle-switch">
          <input type="checkbox" id="s-rakuten-details">
          <span class="toggle-slider"></span>
        </label>
      </div>
    </div>
    <div class="card-footer">
      <button class="btn btn-primary btn-sm" id="s-rakuten-save">Save</button>
    </div>
  </div>

  <!-- Persona (full width) -->
  <div class="card card-full">
    <div class="card-header"><h3>Persona</h3></div>
    <div class="form-group">
      <textarea id="s-persona-text" class="form-input persona-textarea" placeholder="Persona text..."></textarea>
    </div>
    <div class="card-footer">
      <button class="btn btn-primary btn-sm" id="s-persona-save">Save</button>
    </div>
  </div>

</div>`;
}

// ---- helpers ----

async function saveSection(path, body, label) {
  try {
    await api(path, { method: 'POST', body });
    toast(`${label} saved`, 'success');
  } catch (err) {
    console.error(`Save ${label}:`, err);
    toast(`Failed to save ${label}`, 'error');
  }
}

// ---- mount ----

export async function mount() {
  // Fetch all config in parallel
  const [llm, gemini, persona, heartbeat, chatCfg, rakuten, ollamaModels] = await apiBatch([
    ['/api/llm-config'],
    ['/api/gemini-config'],
    ['/api/persona'],
    ['/api/heartbeat-config'],
    ['/api/chat-config'],
    ['/api/rakuten-config'],
    ['/api/ollama-models'],
  ]);

  // -- LLM --
  const modelSelect = $('s-ollama-model');
  if (ollamaModels?.models) {
    ollamaModels.models.forEach(m => {
      const opt = document.createElement('option');
      opt.value = m;
      opt.textContent = m;
      modelSelect.appendChild(opt);
    });
  }
  if (llm) {
    modelSelect.value = llm.ollama_model || '';
    // If current model not in list, add it
    if (modelSelect.value !== (llm.ollama_model || '') && llm.ollama_model) {
      const opt = document.createElement('option');
      opt.value = llm.ollama_model;
      opt.textContent = llm.ollama_model;
      modelSelect.prepend(opt);
      modelSelect.value = llm.ollama_model;
    }
    $('s-ollama-timeout').value = llm.ollama_timeout ?? '';
    $('s-gemini-model').value = llm.gemini_model || '';
  }

  // -- Gemini --
  if (gemini) {
    $('s-gemini-conversation').checked = !!gemini.conversation;
    $('s-gemini-memory').checked = !!gemini.memory_extraction;
    $('s-gemini-routing').checked = !!gemini.unit_routing;
    $('s-gemini-token-limit').value = gemini.monthly_token_limit ?? '';
  }

  // -- Persona --
  if (persona) {
    $('s-persona-text').value = persona.persona || '';
  }

  // -- Heartbeat --
  if (heartbeat) {
    $('s-hb-with').value = heartbeat.interval_with_ollama_minutes ?? '';
    $('s-hb-without').value = heartbeat.interval_without_ollama_minutes ?? '';
    $('s-hb-compact').value = heartbeat.compact_threshold_messages ?? '';
  }

  // -- Chat --
  if (chatCfg) {
    $('s-chat-history').value = chatCfg.history_minutes ?? '';
  }

  // -- Rakuten --
  if (rakuten) {
    $('s-rakuten-max').value = rakuten.max_results ?? '';
    $('s-rakuten-details').checked = !!rakuten.fetch_details;
  }

  // ---- Event listeners ----

  $('s-llm-save').addEventListener('click', () => {
    saveSection('/api/llm-config', {
      ollama_model: modelSelect.value,
      ollama_timeout: Number($('s-ollama-timeout').value),
      gemini_model: $('s-gemini-model').value,
    }, 'LLM config');
  });

  $('s-gemini-save').addEventListener('click', () => {
    saveSection('/api/gemini-config', {
      conversation: $('s-gemini-conversation').checked,
      memory_extraction: $('s-gemini-memory').checked,
      unit_routing: $('s-gemini-routing').checked,
      monthly_token_limit: Number($('s-gemini-token-limit').value),
    }, 'Gemini config');
  });

  $('s-persona-save').addEventListener('click', () => {
    saveSection('/api/persona', {
      persona: $('s-persona-text').value,
    }, 'Persona');
  });

  $('s-hb-save').addEventListener('click', () => {
    saveSection('/api/heartbeat-config', {
      interval_with_ollama_minutes: Number($('s-hb-with').value),
      interval_without_ollama_minutes: Number($('s-hb-without').value),
      compact_threshold_messages: Number($('s-hb-compact').value),
    }, 'Heartbeat config');
  });

  $('s-chat-save').addEventListener('click', () => {
    saveSection('/api/chat-config', {
      history_minutes: Number($('s-chat-history').value),
    }, 'Chat config');
  });

  $('s-rakuten-save').addEventListener('click', () => {
    saveSection('/api/rakuten-config', {
      max_results: Number($('s-rakuten-max').value),
      fetch_details: $('s-rakuten-details').checked,
    }, 'Rakuten config');
  });
}
