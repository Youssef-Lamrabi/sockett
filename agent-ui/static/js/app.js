/* =========================================================================
  Agent Copilot – App JS (model mgmt + chat UX + smooth scroll + markdown)
========================================================================= */
import {
  parseTaggedBlocks,
  renderAssistantEvent,
  renderUserMessage,
  renderLogBlock,
  clearChat,
  clearLogs,
  escapeHtml,
  showAssistantTyping,
  hideAssistantTyping,
  renderUserMessageWithAttachments,
  renderAssistantMarkdownStatic,
} from './agent_render.js';

const api = {
  token: null,
  setToken(t) { this.token = t; localStorage.setItem('agent_token', t || ''); },
  headers() { return this.token ? { 'Authorization': 'Bearer ' + this.token } : {}; }
};
let composerBusy = false;

// Abort the active stream if the tab is closed/refreshed
let currentChatController = null;
window.addEventListener('beforeunload', (e) => {
  // F-7: warn user if a run is in progress — the run continues server-side
  // but the user will lose real-time visibility
  if (composerBusy) {
    e.preventDefault();
    // Standard cross-browser way to trigger the "Leave site?" dialog
    e.returnValue = 'Un pipeline est en cours. Quitter la page interrompra le suivi en temps réel (le run continue côté serveur).';
    return e.returnValue;
  }
  try { currentChatController?.abort(); } catch { }
});

/* -------------------------- Helpers ------------------------------------ */
function el(id) { return document.getElementById(id); }
function append(parent, html) { const div = document.createElement('div'); div.innerHTML = html; parent.appendChild(div.firstElementChild); }

function updateSessionTitleInSidebar(sid, title) {
  const item = document.querySelector(`.session-item[data-sid="${sid}"] .title`);
  if (item) item.textContent = title;
}

async function loadSessionDetails(sid) {
  const res = await fetch(`/api/sessions/${sid}`, { headers: { ...api.headers() } });
  if (!res.ok) return null;
  const s = await res.json();
  if (s?.interaction_mode) {
    localStorage.setItem('agent_mode', s.interaction_mode); // keep in sync
    updateModeUI(s.interaction_mode);
  }
  return s;
}

async function setSessionMode(mode) {
  const sid = getCurrentSessionId();
  if (!sid) return;
  const res = await fetch(`/api/sessions/${sid}/mode`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...api.headers() },
    body: JSON.stringify({ interaction_mode: mode })
  });
  if (!res.ok) {
    notify('error', 'Failed to set mode');
    return;
  }
  localStorage.setItem('agent_mode', mode);
  updateModeUI(mode);
}

/* ------------------- Sticky-to-bottom (chat & logs) -------------------- */
// Both panes smart-scroll; pause while the user is interacting.

let chatStickToBottom = true;
let chatUserDragging = false;

let logsStickToBottom = true;
let logsUserDragging = false;

function isNearBottom(node, threshold = 60) {
  if (!node) return true;
  return node.scrollHeight - node.scrollTop - node.clientHeight < threshold;
}

// CHAT: smooth scroll (unless user is dragging / scrolled up)
function scrollChatSmooth(force = false) {
  const chat = el('chat'); if (!chat) return;
  if (!force && (!chatStickToBottom || chatUserDragging)) return;

  requestAnimationFrame(() => {
    chat.scrollTo({ top: chat.scrollHeight, behavior: 'smooth' });
    // double-tap to overcome layout thrash
    setTimeout(() => {
      if (chatStickToBottom && !chatUserDragging) {
        chat.scrollTo({ top: chat.scrollHeight, behavior: 'smooth' });
      }
    }, 60);
  });
}

// LOGS: smooth scroll (unless user is dragging / scrolled up)
function scrollLogsSmooth(force = false) {
  const logs = el('logs'); if (!logs) return;
  if (!force && (!logsStickToBottom || logsUserDragging)) return;

  requestAnimationFrame(() => {
    logs.scrollTo({ top: logs.scrollHeight, behavior: 'smooth' });
    setTimeout(() => {
      if (logsStickToBottom && !logsUserDragging) {
        logs.scrollTo({ top: logs.scrollHeight, behavior: 'smooth' });
      }
    }, 60);
  });
}

// ---------------------- Attachments (pending in composer) -----------------
let pendingUploads = []; // {id, name, type, size, localUrl, serverPath, status:'uploading'|'ready'|'error'}

function uid() { return 'att-' + Math.random().toString(36).slice(2); }
function formatBytes(b = 0) { if (!b) return '0 B'; const u = ['B', 'KB', 'MB', 'GB', 'TB']; const i = Math.floor(Math.log(b) / Math.log(1024)); return (b / Math.pow(1024, i)).toFixed(i ? 1 : 0) + ' ' + u[i]; }
function isImageLike(mime = '', name = '') { return /^image\//i.test(mime) || /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(name); }

function renderAttachDock() {
  const dock = el('attach-dock'); if (!dock) return;
  if (!pendingUploads.length) { dock.innerHTML = ''; return; }

  dock.innerHTML = `
    <div style="display:flex;flex-wrap:wrap;gap:8px;margin:0 0 8px 0;">
      ${pendingUploads.map(a => {
    const img = isImageLike(a.type, a.name) && a.localUrl;
    const statusIcon = a.status === 'uploading'
      ? `<i class="fa fa-spinner fa-spin" aria-hidden="true"></i>`
      : (a.status === 'ready'
        ? `<i class="fa fa-check" aria-hidden="true"></i>`
        : `<i class="fa fa-exclamation-triangle" aria-hidden="true"></i>`);
    return `
          <div class="tiny-att" data-id="${a.id}"
               style="display:flex;align-items:center;gap:6px;background:#F8FAFC;border:1px solid #E2E8F0;border-radius:999px;padding:4px 8px;">
            <div style="width:22px;height:22px;border-radius:6px;overflow:hidden;background:#EDF2F7;display:flex;align-items:center;justify-content:center;">
              ${img ? `<img src="${escapeHtml(a.localUrl)}" alt="" style="width:100%;height:100%;object-fit:cover;">`
        : `<span style="font-size:11px;font-weight:700;opacity:.7;">.${escapeHtml(a.name.split('.').pop()?.toUpperCase() || 'FILE')}</span>`}
            </div>
            <div style="max-width:200px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
              <strong style="font-size:12px;">${escapeHtml(a.name)}</strong>
              <span class="muted" style="font-size:11px;opacity:.7;"> • ${formatBytes(a.size)}</span>
            </div>
            <span class="stat" title="${escapeHtml(a.status)}" style="opacity:.7;">${statusIcon}</span>
            <button class="rm" title="Remove"
                    style="border:none;background:transparent;cursor:pointer;opacity:.6;padding:2px 4px;">
              ✕
            </button>
          </div>
        `;
  }).join('')}
    </div>
  `;

  // remove handlers
  dock.querySelectorAll('.tiny-att .rm').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const id = btn.closest('.tiny-att')?.dataset?.id;
      if (!id) return;
      const idx = pendingUploads.findIndex(x => x.id === id);
      if (idx >= 0) {
        const u = pendingUploads[idx];
        if (u.localUrl) URL.revokeObjectURL(u.localUrl);
        pendingUploads.splice(idx, 1);
        renderAttachDock();
      }
    });
  });
}


/* App show/hide */
function showApp() { const a = el('auth-panel'); const app = el('app'); if (!a || !app) return; a.classList.add('hidden'); app.classList.remove('hidden'); a.style.display = 'none'; app.style.display = 'block'; }
function showAuth() { const a = el('auth-panel'); const app = el('app'); if (!a || !app) return; app.classList.add('hidden'); a.classList.remove('hidden'); app.style.display = 'none'; a.style.display = 'grid'; }

/* Toasts */
function notify(type, msg, timeout = 3000) {
  const wrap = el('toasts'); if (!wrap) return;
  const t = document.createElement('div'); t.className = `toast ${type}`;
  t.innerHTML = `<span>${escapeHtml(msg)}</span>`;
  wrap.appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; t.style.transform = 'translateY(6px)'; }, timeout - 300);
  setTimeout(() => wrap.removeChild(t), timeout);
}

/* Tabs (Login/Register) */
function tabAuth() {
  const tabLogin = el('tab-login'), tabRegister = el('tab-register');
  const login = el('login-form'), reg = el('register-form');
  if (tabLogin) {
    tabLogin.onclick = (e) => {
      e.preventDefault();
      tabLogin.classList.add('active'); tabRegister?.classList.remove('active');
      login?.classList.add('active'); reg?.classList.remove('active');
      tabLogin.setAttribute('aria-selected', 'true'); tabRegister?.setAttribute('aria-selected', 'false');
      reg?.setAttribute('aria-hidden', 'true'); login?.setAttribute('aria-hidden', 'false');
      clearAuthErrors(); el('login-email')?.focus();
    };
  }
  if (tabRegister) {
    tabRegister.onclick = (e) => {
      e.preventDefault();
      tabRegister.classList.add('active'); tabLogin?.classList.remove('active');
      reg?.classList.add('active'); login?.classList.remove('active');
      tabRegister.setAttribute('aria-selected', 'true'); tabLogin?.setAttribute('aria-selected', 'false');
      login?.setAttribute('aria-hidden', 'true'); reg?.setAttribute('aria-hidden', 'false');
      clearAuthErrors(); el('reg-name')?.focus();
    };
  }
}

function clearAuthErrors() {
  ['login-error', 'register-error', 'login-feedback', 'register-feedback'].forEach(id => {
    const n = el(id); if (n) { n.textContent = ''; n.classList.add('hidden'); }
  });
  el('auth-card')?.classList.remove('shake');
}

/* Password toggles */
function wirePasswordToggles() {
  document.querySelectorAll('.toggle-pass').forEach(btn => {
    btn.addEventListener('click', () => {
      const targetId = btn.getAttribute('data-target'); const input = el(targetId);
      if (!input) return;
      const visible = input.type === 'text';
      input.type = visible ? 'password' : 'text';
      btn.setAttribute('aria-pressed', String(!visible));
    });
  });
}

/* Clear errors while typing + Enter to submit on auth forms */
function wireFieldListeners() {
  ['login-email', 'login-pass', 'reg-name', 'reg-email', 'reg-pass'].forEach(id => {
    const n = el(id); if (!n) return;
    n.addEventListener('input', clearAuthErrors);
  });
  // Enter -> Login
  ['login-email', 'login-pass'].forEach(id => {
    const n = el(id); if (!n) return;
    n.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); el('btn-login')?.click(); }
    });
  });
  // Enter -> Register
  ['reg-name', 'reg-email', 'reg-pass'].forEach(id => {
    const n = el(id); if (!n) return;
    n.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); el('btn-register')?.click(); }
    });
  });
}

async function loadSessionMessages(sid) {
  const res = await fetch(`/api/sessions/${sid}/messages`, { headers: { ...api.headers() } });
  if (!res.ok) return;
  const msgs = await res.json();
  const chat = el('chat');
  if (chat) {
    Array.from(chat.children).forEach(child => {
      if (child.id !== 'empty-state') child.remove();
    });
  }
  const logs = el('logs'); if (logs) logs.innerHTML = '';   // reset right pane

  for (const m of msgs) {
    if (m.role === 'user') {
      renderUserMessage(m.content);
    }
    else if (m.role === 'assistant') {
      // left pane: final assistant text
      renderAssistantMarkdownStatic(m.content);

      // right pane: replay saved tool/log blocks (if any)
      const savedLogs = m.logs || [];
      for (const L of savedLogs) {
        renderLogBlock(String(L.tag || '').toUpperCase(), L.body || '');
      }
      // remove any spinner visuals (history is not "live")
      // light-touch way: drop a terminal status which also clears spinners
      if (savedLogs.length) {
        renderAssistantEvent({ type: 'block', tag: 'STATUS', text: '<status:done>' });
      }
    }
  }
  updateEmptyState();
}

function updateEmptyState() {
  const chat = el('chat');
  const emptyState = el('empty-state');
  if (!chat || !emptyState) return;
  const hasMessages = chat.querySelectorAll('.msg').length > 0;
  emptyState.classList.toggle('hidden', hasMessages);
}


function autoGrowTextArea(t) {
  if (!t) return;
  t.style.height = 'auto';
  const max = 420; // keep in sync with CSS
  t.style.height = Math.min(t.scrollHeight, max) + 'px';
  t.style.overflow = (Math.min(t.scrollHeight, max) >= max) ? "auto" : "hidden";
}
function focusComposer() {
  const msg = el('message');
  if (msg) {
    msg.value = '';
    autoGrowTextArea(msg);
    msg.focus();
  }
}
function markSessionActive(sid) {
  const list = el('sessions-list');
  if (!list) return;
  list.querySelectorAll('.session-item').forEach(x => x.classList.remove('active'));
  const item = list.querySelector(`.session-item[data-sid="${sid}"]`);
  if (item) item.classList.add('active');
}

function getAgentMode() {
  return localStorage.getItem('agent_mode') || 'auto'; // 'auto' | 'feedback'
}
function setAgentMode(m) {
  localStorage.setItem('agent_mode', m);
  updateModeUI(m);
}
function updateModeUI(m) {
  const autoBtn = document.getElementById('mode-auto');
  const fbBtn = document.getElementById('mode-feedback');
  if (autoBtn) autoBtn.classList.toggle('active', m === 'auto');
  if (fbBtn) fbBtn.classList.toggle('active', m === 'feedback');
}

/* ---------------------------- Auth API ---------------------------------- */
async function register() {
  const email = el('reg-email')?.value.trim();
  const name = el('reg-name')?.value.trim();
  const password = el('reg-pass')?.value;
  const feedback = el('register-feedback'), errorBox = el('register-error');
  const btn = el('btn-register'); if (btn) { btn.classList.add('is-loading'); btn.disabled = true; }
  if (feedback) feedback.textContent = 'Creating your account…'; if (errorBox) { errorBox.classList.add('hidden'); errorBox.textContent = ''; }

  try {
    const res = await fetch('/api/auth/register', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, name, password })
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || 'Unable to register. Please check your details.');
    }
    if (feedback) feedback.textContent = 'Account created. You can login now.';
    notify('success', 'Registered successfully');
    el('tab-login')?.click();
  } catch (err) {
    if (feedback) feedback.textContent = '';
    if (errorBox) {
      errorBox.textContent = (err && err.message) ? err.message : 'Register failed';
      errorBox.classList.remove('hidden');
    }
    el('auth-card')?.classList.add('shake');
    notify('error', errorBox?.textContent || 'Register failed');
  } finally {
    if (btn) { btn.classList.remove('is-loading'); btn.disabled = false; }
  }
}

async function login() {
  const email = el('login-email')?.value.trim();
  const password = el('login-pass')?.value;
  const feedback = el('login-feedback'), errorBox = el('login-error');
  const btn = el('btn-login'); if (btn) { btn.classList.add('is-loading'); btn.disabled = true; }
  if (feedback) feedback.textContent = 'Signing you in…'; if (errorBox) { errorBox.classList.add('hidden'); errorBox.textContent = ''; }

  const form = new URLSearchParams(); form.append('username', email); form.append('password', password);

  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: form
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || 'Incorrect email or password');
    }
    const data = await res.json();
    if (!data || !data.access_token) throw new Error('No token returned by server');

    api.setToken(data.access_token);
    if (feedback) feedback.textContent = '';
    notify('success', 'Logged in');

    await afterLogin(); // update UI
    try { window.location.assign('/dashboard'); } catch (_) { }
  } catch (err) {
    if (feedback) feedback.textContent = '';
    if (errorBox) {
      errorBox.textContent = (err && err.message) ? err.message : 'Login failed';
      errorBox.classList.remove('hidden');
    }
    el('auth-card')?.classList.add('shake');
    notify('error', errorBox?.textContent || 'Login failed');
  } finally {
    if (btn) { btn.classList.remove('is-loading'); btn.disabled = false; }
  }
}

async function me() {
  const res = await fetch('/api/auth/me', { headers: { ...api.headers() } });
  return res.ok ? res.json() : null;
}

// async function afterLogin() {
//   const u = await me();
//   if (!u) { showAuth(); return; }
//   const nameNode = el('profile-name'), mailNode = el('profile-email'), avatar = el('profile-avatar');
//   if (nameNode) nameNode.textContent = u.name || 'User';
//   if (mailNode) mailNode.textContent = u.email || '';
//   if (avatar) avatar.textContent = (u.name || u.email || 'U').charAt(0).toUpperCase();
//   if (el('app')) { await loadSessions(); showApp(); }
// }
async function afterLogin() {
  const u = await me();
  if (!u) { showAuth(); return; }
  const nameNode = el('profile-name'), mailNode = el('profile-email'), avatar = el('profile-avatar');
  if (nameNode) nameNode.textContent = u.name || 'User';
  if (mailNode) mailNode.textContent = u.email || '';
  if (avatar) avatar.textContent = (u.name || u.email || 'U').charAt(0).toUpperCase();
  if (el('app')) {
    await loadSessions();
    showApp();
  }
}

/* ------------------------ Sessions (drawer UI) --------------------------- */
let sessionsCache = [];
async function createSession(focusAfter = false) {
  // Require a model first
  const modelSel = document.getElementById('model-select');
  if (!modelSel?.value) {
    notify('info', 'Add a model in Settings first');
    openSettings();
    return null;
  }

  const model = modelSel.value;
  const interaction_mode = getAgentMode();
  const res = await fetch('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...api.headers() },
    body: JSON.stringify({ title: 'New Chat', model, interaction_mode })
  });
  if (!res.ok) {
    notify('error', 'Failed to create session');
    return null;
  }
  const data = await res.json();

  // Reload sessions list only — skip loading messages (new session is empty)
  await loadSessions(false, false);
  markSessionActive(String(data.id));

  // Clear any previous chat content and show the empty state
  clearChat();

  updateEmptyState();
  closeSessionsDrawer();
  if (focusAfter) focusComposer();

  notify('success', 'New chat created');
  return data.id;
}
async function setSessionModelAuto() {
  const sid = getCurrentSessionId();
  const sel = document.getElementById('model-select'); const model = sel ? sel.value : null;
  if (!sid || !model) return;
  const res = await fetch(`/api/sessions/${sid}/model`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...api.headers() },
    body: JSON.stringify({ model })
  });
  if (!res.ok) { notify('error', 'Failed to set model'); return; }
  localStorage.setItem('last_model', model);
  notify('success', `Model set to ${model}`);
}

async function loadSessions(andOpen = false, loadMessages = true) {
  const res = await fetch('/api/sessions', { headers: { ...api.headers() } });
  if (!res.ok) return;
  const data = await res.json(); sessionsCache = data || [];
  renderSessionsList();
  if (loadMessages && sessionsCache.length) {
    const sid = String(sessionsCache[0].id);
    markSessionActive(sid);
    await loadSessionMessages(sid);
    await loadSessionDetails(sid);
  }
  if (andOpen) openSessionsDrawer();
}

function getCurrentSessionId() {
  const btn = document.querySelector('.session-item.active');
  if (btn) return btn.dataset.sid;
  return sessionsCache[0]?.id || null;
}
function renderSessionsList() {
  const list = el('sessions-list'); if (!list) return;
  list.innerHTML = '';
  if (!sessionsCache.length) {
    append(list, `<div class="muted" style="padding:10px;">No chats yet. Click “New Chat”.</div>`); return;
  }
  sessionsCache.forEach((s, i) => {
    const title = s.title || `Chat ${i + 1}`, model = s.model || '';
    const activeClass = i === 0 ? 'active' : '';
    append(list, `
      <div class="session-item ${activeClass}" data-sid="${s.id}">
        <div class="open">
          <div class="title">${escapeHtml(title)}</div>
          <div class="meta">${escapeHtml(model)}</div>
        </div>
      </div>
    `);
  });

  // select highlight
  list.querySelectorAll('.session-item').forEach(item => {
    item.addEventListener('click', () => {
      list.querySelectorAll('.session-item').forEach(x => x.classList.remove('active'));
      item.classList.add('active');
    });
  });

  // open + load history
  list.querySelectorAll('.session-item .open').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation(); closeSessionsDrawer();
      list.querySelectorAll('.session-item').forEach(x => x.classList.remove('active'));
      btn.parentElement.classList.add('active');
      await loadSessionMessages(btn.parentElement.dataset.sid);
      await loadSessionDetails(btn.parentElement.dataset.sid);
      notify('info', 'Chat opened');
    });
  });

}

/* -------------------------- Uploads ------------------------------------- */
async function uploadFile() {
  const input = document.querySelector('#composer input[type=file]');
  const f = input?.files?.[0]; if (!f) return;

  // 1) create a pending chip immediately (local preview)
  const rec = {
    id: uid(),
    name: f.name,
    type: f.type || '',
    size: f.size || 0,
    localUrl: URL.createObjectURL(f),
    serverPath: null,
    status: 'uploading'
  };
  pendingUploads.push(rec);
  renderAttachDock();

  // 2) upload to server
  try {
    const form = new FormData();
    form.append('file', f);
    const res = await fetch('/api/upload', { method: 'POST', headers: { ...api.headers() }, body: form });
    if (!res.ok) throw new Error(await res.text() || 'Upload failed');

    const data = await res.json(); // expect { path }
    rec.serverPath = data?.path || null;
    rec.status = 'ready';
    notify('success', `Uploaded: ${f.name}`);
    // Workspace hook: file just landed on the server; refresh if panel is open
    try { window.refreshWorkspaceSoon?.(300); } catch {}
  } catch (err) {
    console.error(err);
    rec.status = 'error';
    notify('error', `Upload failed: ${f.name}`);
  } finally {
    // 3) clear the input so same file can be chosen again
    if (input) input.value = '';
    renderAttachDock();
  }
}

/* --------------------- while agent respond ------------------------------ */
function setComposerBusy(busy) {
  composerBusy = busy;

  const send = el('send');
  const stop = el('stop');
  const file = document.querySelector('#composer input[type=file]');
  const msg = el('message');

  // Toggle controls
  if (send) {
    send.disabled = busy;
    send.classList.toggle('is-busy', busy);
    send.setAttribute('aria-busy', String(busy));
    // 👇 Hide Send when running; show when idle
    send.style.display = busy ? 'none' : '';
  }
  if (stop) {
    // 👇 Show Stop when running; hide when idle
    stop.style.display = busy ? '' : 'none';
    stop.disabled = !busy;
    if (!busy) stop.removeAttribute('aria-busy');
  }
  if (file) file.disabled = busy;

  // optional: visual hint on the textarea, but still allow typing
  if (msg) msg.classList.toggle('is-busy', busy);
}


/* ------------------------- Stop current run ----------------------------- */
function stopRun() {
  // abort the active stream; send()’s catch(AbortError) will log and clean up
  if (!currentChatController) return;
  try { currentChatController.abort(); } catch { }
  // small immediate feedback (optional)
  const stop = el('stop');
  if (stop) { stop.disabled = true; stop.setAttribute('aria-busy', 'true'); }
}
window.stopCurrentRun = stopRun;


/* ------------------------- Send message --------------------------------- */
async function send() {
  if (composerBusy) return;

  // Guard: model + session
  if (!document.getElementById('model-select')?.value) {
    notify('info', 'Add a model in Settings first'); openSettings(); return;
  }
  const sid = getCurrentSessionId(); if (!sid) { notify('error', 'Create a session first'); return; }

  const textarea = el('message');
  const msg = textarea?.value?.trim(); if (!msg) return;

  // // User bubble
  // renderUserMessage(msg);
  // if (textarea) { textarea.value = ''; textarea.style.height = 'auto'; textarea.focus(); }
  // Don't allow sending while any file is still uploading
  if (pendingUploads.some(x => x.status === 'uploading')) {
    notify('info', 'Please wait for files to finish uploading.');
    return;
  }
  // Ready attachments to render + send
  const ready = pendingUploads.filter(x => x.status === 'ready');
  if (ready.length) {
    renderUserMessageWithAttachments(msg, ready.map(a => ({
      name: a.name,
      type: a.type,
      path: a.serverPath,        // backend path
      url: a.serverPath,         // used for browser if accessible
      previewUrl: a.localUrl     // fallback/local preview
    })));
  } else {
    renderUserMessage(msg);
  }
  // clear composer text + dock (but keep previews alive until after we render)
  if (textarea) { textarea.value = ''; textarea.style.height = 'auto'; textarea.focus(); }
  pendingUploads.forEach(a => { if (a.localUrl) URL.revokeObjectURL(a.localUrl); });
  pendingUploads = [];
  renderAttachDock();


  // Abort any existing run
  if (currentChatController) currentChatController.abort();
  currentChatController = new AbortController();

  // Busy + typing
  setComposerBusy(true);
  showAssistantTyping();

  const interaction_mode = localStorage.getItem('agent_mode') || 'auto'; // if you added the toggle
  const headers = { 'Content-Type': 'application/json', ...api.headers() };
  // const body = JSON.stringify({ message: msg, stream: true, interaction_mode });
  const attachments = ready.map(a => ({
    path: a.serverPath,
    name: a.name,
    mime: a.type,
    size: a.size
  }));
  const body = JSON.stringify({ message: msg, stream: true, interaction_mode, attachments });

  // F-6: SSE auto-reconnect — max 5 attempts, 3 s delay, visual indicator
  const SSE_MAX_RETRIES = 5;
  const SSE_RETRY_DELAY = 3000; // ms
  let streamCompletedOk = false;
  let attempt = 0;

  function _showReconnectBanner(n, max) {
    const logsEl = document.getElementById('logs');
    if (!logsEl) return;
    let banner = document.getElementById('_sse_reconnect_banner');
    if (!banner) {
      banner = document.createElement('div');
      banner.id = '_sse_reconnect_banner';
      banner.style.cssText = 'padding:6px 12px;margin:4px 0;border-radius:8px;font-size:13px;' +
        'background:#FFF3CD;border:1px solid #FFEAA7;color:#856404;display:flex;align-items:center;gap:8px;';
      logsEl.appendChild(banner);
    }
    banner.innerHTML = `<i class="fa fa-refresh fa-spin"></i> Reconnexion en cours… (tentative ${n}/${max})`;
    logsEl.scrollTop = logsEl.scrollHeight;
  }

  function _clearReconnectBanner() {
    const b = document.getElementById('_sse_reconnect_banner');
    if (b) b.remove();
  }

  function _showReconnectFailed() {
    _clearReconnectBanner();
    const logsEl = document.getElementById('logs');
    if (!logsEl) return;
    const div = document.createElement('div');
    div.style.cssText = 'padding:8px 12px;margin:4px 0;border-radius:8px;font-size:13px;' +
      'background:#FFE3E3;border:1px solid #F5B7B7;color:#8b0000;display:flex;align-items:center;gap:8px;';
    div.innerHTML = `<i class="fa fa-times-circle"></i> La connexion a échoué après ${SSE_MAX_RETRIES} tentatives.
      <button onclick="location.reload()" style="margin-left:auto;padding:2px 10px;cursor:pointer;
        border:1px solid #c00;border-radius:6px;background:#fff;color:#8b0000;font-size:12px;">
        Réessayer manuellement
      </button>`;
    logsEl.appendChild(div);
    logsEl.scrollTop = logsEl.scrollHeight;
  }

  while (attempt <= SSE_MAX_RETRIES && !streamCompletedOk) {
    if (attempt > 0) {
      _showReconnectBanner(attempt, SSE_MAX_RETRIES);
      await new Promise(r => setTimeout(r, SSE_RETRY_DELAY));
      // Re-create abort controller for the new attempt
      if (currentChatController) currentChatController.abort();
      currentChatController = new AbortController();
    }

    try {
      const resp = await fetch(`/api/sessions/${sid}/messages`, {
        method: 'POST',
        headers,
        body,
        signal: currentChatController.signal
      });
      if (!resp.ok) { throw new Error('Send failed'); }

      _clearReconnectBanner();

      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += dec.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();

        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const evt = JSON.parse(line);

            if (evt.type === 'meta' && evt.session_id && evt.session_title) {
              updateSessionTitleInSidebar(String(evt.session_id), String(evt.session_title));
              continue;
            }

            // Track normal stream completion
            if (evt.type === 'done') streamCompletedOk = true;

            // Workspace hook: each block (especially STATUS / OBSERVE) likely
            // means a step finished and may have produced new files. Debounced
            // and no-op when the panel is closed.
            if (evt.type === 'block' || evt.type === 'done') {
              try { window.refreshWorkspaceSoon?.(); } catch {}
            }

            renderAssistantEvent(evt);
          } catch { /* ignore parse errors */ }
        }
      }

      if (buffer.trim()) {
        try {
          const evt = JSON.parse(buffer.trim());
          if (evt.type === 'done') streamCompletedOk = true;
          renderAssistantEvent(evt);
        } catch { }
      }

      // If we reach here without a 'done', the stream dropped silently
      if (!streamCompletedOk && attempt < SSE_MAX_RETRIES) {
        attempt++;
        continue; // retry
      }
      break; // either done=true, or exhausted retries

    } catch (err) {
      if (err?.name === 'AbortError') {
        try { await fetch(`/api/sessions/${sid}/cancel`, { method: 'POST', headers: { ...api.headers() } }); } catch { }
        if (window.AgentRender?.renderLogBlock) window.AgentRender.renderLogBlock('STATUS', 'Canceled by user');
        _clearReconnectBanner();
        break; // user-initiated cancel — do not retry
      }
      // Network / server error
      attempt++;
      if (attempt > SSE_MAX_RETRIES) {
        _showReconnectFailed();
        break;
      }
      // else: retry loop continues
    }
  }

  if (!streamCompletedOk && attempt > SSE_MAX_RETRIES) {
    // All retries exhausted without clean completion — already showed banner above
  } else if (!streamCompletedOk) {
    // Edge case: exited loop but no explicit done and no error
    hideAssistantTyping();
  }

  hideAssistantTyping();
  setComposerBusy(false);
  currentChatController = null;
  // Workspace hook: final refresh at end of stream (covers files produced by
  // the finalizer node that may arrive after the last streamed block).
  try { window.refreshWorkspaceSoon?.(500); } catch {}
}

// Render a simple assistant bubble (no typing effect, safe HTML)
function renderAssistantHistoryPlain(text) {
  const chat = el('chat'); if (!chat) return;
  const div = document.createElement('div');
  div.className = 'msg assistant';
  div.innerHTML = `<div class="bubble">${escapeHtml(text || '').replace(/\n/g, '<br>')}</div>`;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}




/* -------------------------- Drawer controls ----------------------------- */
function openSessionsDrawer() { const d = el('sessions-drawer'); if (d) { d.classList.add('active'); d.setAttribute('aria-hidden', 'false'); } }
function closeSessionsDrawer() { const d = el('sessions-drawer'); if (d) { d.classList.remove('active'); d.setAttribute('aria-hidden', 'true'); } }

/* ------------------------------ Logout ---------------------------------- */
async function logout() {
  try { await fetch('/api/auth/logout', { method: 'POST' }); } catch (_) { }
  api.setToken(''); localStorage.removeItem('agent_token');
  window.location.assign('/login');
}

/* ------------------------------ Boot ------------------------------------ */
function boot() {
  tabAuth(); wirePasswordToggles(); wireFieldListeners();

  // Settings modal buttons...
  const openSet = el('btn-open-settings'); if (openSet) openSet.onclick = (e) => { e.preventDefault(); openSettings(); };
  const closeSet = el('settings-close'); if (closeSet) closeSet.onclick = (e) => { e.preventDefault(); closeSettings(); };
  const saveSet = el('settings-save-old'); if (saveSet) saveSet.onclick = (e) => { e.preventDefault(); saveSettings(); };

  // Close modal by clicking backdrop
  const modal = el('settings-modal');
  if (modal) modal.addEventListener('click', (e) => { if (e.target === modal) closeSettings(); });

  // Auth + guard
  api.setToken(localStorage.getItem('agent_token') || '');
  guardDashboard().then(async () => {
    await refreshModelSelectFromServer(localStorage.getItem('last_model') || undefined);
    if (!document.getElementById('model-select')?.value) {
      notify('info', 'No models configured yet. Add one in Settings.');
      openSettings();
    }
  });

  // Auth buttons...
  el('btn-register')?.addEventListener('click', (e) => { e.preventDefault(); register(); });
  el('btn-login')?.addEventListener('click', (e) => { e.preventDefault(); login(); });

  // Sessions: model change -> update session model AND persist to localStorage
  const modelSel = document.getElementById('model-select');
  if (modelSel) {
    modelSel.addEventListener('change', (e) => {
      const v = e.target.value;
      localStorage.setItem('last_model', v);
      setSessionModelAuto();
    });
  }

  // Drawer open/close...
  el('btn-open-sessions')?.addEventListener('click', (e) => { e.preventDefault(); openSessionsDrawer(); });
  const closeSessBtn = document.querySelector('#close-sessions, [id="close-sessions close-history"], .close-history');
  closeSessBtn?.addEventListener('click', (e) => { e.preventDefault(); closeSessionsDrawer(); });
  el('sessions-backdrop')?.addEventListener('click', () => closeSessionsDrawer());

  // New Chat flow...
  const newChatFlow = async (e) => {
    e?.preventDefault?.();
    await createSession();
    closeSessionsDrawer();
    clearChat(); clearLogs();
    const msg = el('message'); if (msg) { msg.value = ''; msg.focus(); }
    updateEmptyState();
  };
  el('btn-new-chat')?.addEventListener('click', newChatFlow);
  el('drawer-new')?.addEventListener('click', newChatFlow);
  el('btn-new-session')?.addEventListener('click', newChatFlow);

  // Chat send
  el('send')?.addEventListener('click', (e) => { e.preventDefault(); send(); });

  // Textarea behavior...
  const msgInput = el('message');
  if (msgInput) {
    autoGrowTextArea(msgInput);
    msgInput.addEventListener('input', () => autoGrowTextArea(msgInput));
    msgInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (composerBusy) return;
        send();
      }
    });
  }

  // Uploads
  const uploadInput = document.querySelector('#composer input[type=file]');
  if (uploadInput) uploadInput.addEventListener('change', uploadFile);

  // Logout
  el('btn-logout')?.addEventListener('click', (e) => { e.preventDefault(); logout(); });

  // Splitter + models add
  initSplitter();
  el('mdl-add')?.addEventListener('click', (e) => { e.preventDefault(); addModel(); });
  el('settings-save')?.addEventListener('click', (e) => { e.preventDefault(); addModel(); });

  // Initialize mode UI from persisted value
  updateModeUI(getAgentMode());
  // document.getElementById('mode-auto')?.addEventListener('click', (e) => {
  //   e.preventDefault();
  //   setAgentMode('auto');
  //   notify('info', 'Auto mode enabled');
  // });
  // document.getElementById('mode-feedback')?.addEventListener('click', (e) => {
  //   e.preventDefault();
  //   setAgentMode('feedback');
  //   notify('info', 'Human-in-loop mode enabled');
  // });
  async function onModeChange(nextMode) {
    const isRunning = !!currentChatController;
    if (isRunning) {
      const startNew = confirm(
        'A response is currently running.\n\n' +
        '• OK = start a NEW conversation now with this mode\n' +
        '• Cancel = keep this chat; the mode will apply on the next message.'
      );
      if (startNew) {
        // make a fresh session immediately in requested mode
        const createdId = await createSession(true);
        if (createdId) {
          await setSessionMode(nextMode);
          setAgentMode(nextMode);
          notify('success', (nextMode === 'auto' ? 'Auto' : 'Human-in-the-loop') + ' mode enabled (new chat).');
        }
        return;
      }
      // apply to current session but only effective on next send
      await setSessionMode(nextMode);
      setAgentMode(nextMode);
      notify('info', (nextMode === 'auto' ? 'Auto' : 'Human-in-the-loop') + ' mode will apply to the next message.');
      return;
    }
    await setSessionMode(nextMode);
    setAgentMode(nextMode);
    notify('success', (nextMode === 'auto' ? 'Auto' : 'Human-in-the-loop') + ' mode enabled.');
  }

  document.getElementById('mode-auto')?.addEventListener('click', (e) => { e.preventDefault(); onModeChange('auto'); });
  document.getElementById('mode-feedback')?.addEventListener('click', (e) => { e.preventDefault(); onModeChange('feedback'); });


  // Stick-to-bottom listeners (chat) — don't fight the user
  const chat = el('chat');
  if (chat) {
    // update “near bottom” as they scroll
    chat.addEventListener('scroll', () => {
      chatStickToBottom = isNearBottom(chat);
    });

    // pause auto-scroll while interacting
    ['pointerdown', 'touchstart'].forEach(ev =>
      chat.addEventListener(ev, () => { chatUserDragging = true; })
    );
    ['pointerup', 'touchend', 'mouseleave'].forEach(ev =>
      chat.addEventListener(ev, () => {
        chatUserDragging = false;
        chatStickToBottom = isNearBottom(chat);
      })
    );
    chat.addEventListener('wheel', () => {
      chatStickToBottom = isNearBottom(chat);
    });

    // observe DOM changes under #chat and auto-scroll if allowed
    const chatMo = new MutationObserver(() => { scrollChatSmooth(); updateEmptyState(); });
    chatMo.observe(chat, { childList: true, subtree: true });

    // also honor explicit renderer signals (if agent_render dispatches them)
    window.addEventListener('chat:changed', () => scrollChatSmooth());
  }


  // Stick-to-bottom listeners (logs) — don't fight the user
  const logs = el('logs');
  if (logs) {
    // track “near bottom”
    logs.addEventListener('scroll', () => {
      logsStickToBottom = isNearBottom(logs);
    });

    // if the user starts interacting with the scrollbar/content, pause auto-stick
    ['pointerdown', 'touchstart'].forEach(ev =>
      logs.addEventListener(ev, () => { logsUserDragging = true; })
    );
    // when interaction ends, recompute and possibly resume stickiness
    ['pointerup', 'touchend', 'mouseleave'].forEach(ev =>
      logs.addEventListener(ev, () => {
        logsUserDragging = false;
        logsStickToBottom = isNearBottom(logs);
      })
    );
    // wheel scrolling also updates stickiness
    logs.addEventListener('wheel', () => {
      logsStickToBottom = isNearBottom(logs);
    });

    // as a safety net, observe DOM changes under #logs and auto-scroll if allowed
    const mo = new MutationObserver(() => scrollLogsSmooth());
    mo.observe(logs, { childList: true, subtree: true });
  }

  // also scroll when renderers signal that logs changed
  window.addEventListener('logs:changed', () => scrollLogsSmooth());

  // Stop button
  el('stop')?.addEventListener('click', (e) => { e.preventDefault(); stopRun(); });
  // ESC to stop
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && currentChatController) stopRun();
  });

  setComposerBusy(false);
  renderAttachDock();
  updateEmptyState();

  // Prompt suggestion cards
  document.querySelectorAll('.prompt-card').forEach(card => {
    card.addEventListener('click', () => {
      const prompt = card.dataset.prompt;
      const textarea = el('message');
      if (textarea && prompt) {
        textarea.value = prompt;
        autoGrowTextArea(textarea);
        textarea.focus();
      }
    });
  });

  // File Explorer Panel
  (function () {
    const feBtn = el('btn-file-explorer');
    const feEl = el('file-explorer');
    const feCloseBtn = el('fe-close');
    const feSplitter = el('fe-splitter');
    const contentArea = el('content-area');

    function openFE() {
      contentArea?.classList.add('fe-open');
      feEl?.setAttribute('aria-hidden', 'false');
      feBtn?.classList.add('active');
      feBtn?.setAttribute('aria-expanded', 'true');
    }
    function closeFE() {
      contentArea?.classList.remove('fe-open');
      feEl?.setAttribute('aria-hidden', 'true');
      feBtn?.classList.remove('active');
      feBtn?.setAttribute('aria-expanded', 'false');
    }

    feBtn?.addEventListener('click', () => {
      const isOpen = contentArea?.classList.contains('fe-open');
      if (isOpen) { closeFE(); return; }
      openFE();
      // Workspace files refresh on every open so the user sees the latest state
      try { refreshWorkspaceFiles(); } catch { /* defined below; ignore if not yet wired */ }
    });
    feCloseBtn?.addEventListener('click', closeFE);

    // Inject a small refresh button into the workspace header (no HTML change)
    (function injectRefreshBtn() {
      const header = document.querySelector('#file-explorer .fe-header');
      const closeBtn = document.getElementById('fe-close');
      if (!header || !closeBtn) return;
      if (document.getElementById('fe-refresh')) return; // idempotent
      const btn = document.createElement('button');
      btn.id = 'fe-refresh';
      btn.type = 'button';
      btn.className = 'icon fe-refresh-btn';
      btn.title = 'Refresh workspace';
      btn.setAttribute('aria-label', 'Refresh workspace');
      btn.innerHTML = '<i class="fa fa-sync-alt" aria-hidden="true"></i>';
      closeBtn.parentNode.insertBefore(btn, closeBtn);
      btn.addEventListener('click', () => {
        if (btn.disabled) return;
        btn.disabled = true;
        btn.classList.add('spinning');
        // Force-refresh bypasses the signature cache by resetting it
        try { _wsCurrentSig = ''; } catch {}
        Promise.resolve(refreshWorkspaceFiles())
          .finally(() => {
            setTimeout(() => {
              btn.classList.remove('spinning');
              btn.disabled = false;
            }, 450);
          });
      });
    })();

    // Drag the splitter bar to resize the workspace panel
    if (feSplitter && feEl && contentArea) {
      const MIN_W = 200, MAX_W = 600;
      let down = false, startX = 0, startW = 0;
      feSplitter.addEventListener('mousedown', (e) => {
        down = true; startX = e.clientX; startW = feEl.offsetWidth;
        contentArea.classList.add('resizing');
        feEl.style.transition = 'none';
        feSplitter.style.transition = 'none';
        document.body.style.userSelect = 'none';
      });
      window.addEventListener('mousemove', (e) => {
        if (!down) return;
        const w = Math.max(MIN_W, Math.min(MAX_W, startW + (startX - e.clientX)));
        feEl.style.width = w + 'px';
      });
      window.addEventListener('mouseup', () => {
        if (!down) return;
        down = false;
        contentArea.classList.remove('resizing');
        feEl.style.transition = '';
        feSplitter.style.transition = '';
        document.body.style.userSelect = '';
        const w = feEl.offsetWidth;
        contentArea.style.setProperty('--fe-col', w + 'px');
        localStorage.setItem('fe_width', w + 'px');
      });
      const savedW = localStorage.getItem('fe_width');
      if (savedW) contentArea.style.setProperty('--fe-col', savedW);
    }
  })();

  // review btn
  window.addEventListener('review:approve', (e) => {
    const msg = (e?.detail && e.detail.msg) || 'Approved — please continue.';
    const t = document.getElementById('message');
    if (t) {
      t.value = msg;
      // keep your auto-grow behavior
      try { if (typeof autoGrowTextArea === 'function') autoGrowTextArea(t); } catch { }
    }
    if (!composerBusy) {
      // use your existing send() to submit immediately
      // (send is in scope inside app.js)
      send();
    }
  });
}

window.addEventListener('DOMContentLoaded', boot);

/* Global error -> toast */
window.addEventListener('unhandledrejection', (e) => {
  notify('error', e?.reason?.message || 'Unexpected error');
});
window.addEventListener('error', (e) => {
  notify('error', e?.message || 'Unexpected error');
});

/* =========================================================================
   Workspace Files Browser (right-side panel)
   - 2 sections: Uploaded (top) + Generated (bottom)
   - Click an item -> floating preview modal (text / image / iframe)
   - Auto-refresh: on panel open, after upload, on stream blocks (debounced),
                   on stream end. Never polls when panel is closed.
   ========================================================================= */
function _wsHumanSize(n) {
  if (n == null) return '';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + ' MB';
  return (n / 1024 / 1024 / 1024).toFixed(2) + ' GB';
}
function _wsHumanTime(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    const now = new Date();
    const diff = (now - d) / 1000;
    if (diff < 60) return 'just now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    return d.toLocaleString();
  } catch { return ''; }
}
function _wsIconForFile(name) {
  const ext = (name || '').split('.').pop().toLowerCase();
  const map = {
    fasta: 'fa-dna', fa: 'fa-dna', fna: 'fa-dna', faa: 'fa-dna',
    fastq: 'fa-dna', fq: 'fa-dna',
    gff: 'fa-list', gff3: 'fa-list', bed: 'fa-list', vcf: 'fa-list',
    txt: 'fa-file-alt', md: 'fa-file-alt', log: 'fa-file-alt',
    json: 'fa-code', yaml: 'fa-code', yml: 'fa-code', xml: 'fa-code',
    tsv: 'fa-table', csv: 'fa-table',
    html: 'fa-file-code', htm: 'fa-file-code',
    png: 'fa-image', jpg: 'fa-image', jpeg: 'fa-image',
    gif: 'fa-image', svg: 'fa-image', webp: 'fa-image', bmp: 'fa-image',
    pdf: 'fa-file-pdf',
    zip: 'fa-file-archive', gz: 'fa-file-archive',
    tar: 'fa-file-archive', bz2: 'fa-file-archive',
    bam: 'fa-database', sam: 'fa-database', cram: 'fa-database',
  };
  return map[ext] || 'fa-file';
}
function _wsEscHtml(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function _wsFileItemHtml(f) {
  return `
    <button class="fe-file-item" type="button"
            data-path="${_wsEscHtml(f.rel_path)}"
            title="${_wsEscHtml(f.rel_path)}">
      <i class="fa ${_wsIconForFile(f.name)}" aria-hidden="true"></i>
      <span class="fe-file-name">${_wsEscHtml(f.name)}</span>
      <span class="fe-file-meta">${_wsEscHtml(_wsHumanSize(f.size))} · ${_wsEscHtml(_wsHumanTime(f.mtime))}</span>
    </button>
  `;
}

let _wsCurrentSig = '';   // signature of last-rendered list, to avoid unnecessary repaint
async function refreshWorkspaceFiles() {
  const sid = (typeof getCurrentSessionId === 'function') ? getCurrentSessionId() : null;
  const body = document.querySelector('#file-explorer .fe-body');
  if (!body) return;
  if (!sid) { _wsRenderEmpty(body, 'No active session'); return; }
  try {
    const res = await fetch(`/api/sessions/${sid}/files`, { headers: { ...api.headers() } });
    if (!res.ok) {
      _wsRenderEmpty(body, `Unable to load workspace (${res.status})`);
      return;
    }
    const data = await res.json();
    const uploads = Array.isArray(data?.uploads) ? data.uploads : [];
    const generated = Array.isArray(data?.generated) ? data.generated : [];
    const sig = JSON.stringify([uploads.map(f => [f.rel_path, f.size, f.mtime]),
                                 generated.map(f => [f.rel_path, f.size, f.mtime])]);
    if (sig === _wsCurrentSig) return;   // nothing changed, skip repaint to avoid flicker
    _wsCurrentSig = sig;
    _wsRenderLists(body, uploads, generated);
  } catch (err) {
    /* silent — keep last good render */
  }
}
function _wsRenderEmpty(body, hint = '') {
  _wsCurrentSig = '';
  body.innerHTML = `
    <div class="fe-empty-state">
      <i class="fa fa-folder-o" aria-hidden="true"></i>
      <p>No files yet</p>
      <span>${_wsEscHtml(hint || 'Files generated by the agent during this session will appear here')}</span>
    </div>
  `;
}
function _wsRenderLists(body, uploads, generated) {
  if (uploads.length === 0 && generated.length === 0) {
    _wsRenderEmpty(body);
    return;
  }
  body.innerHTML = `
    <div class="fe-section" data-kind="uploads">
      <div class="fe-section-title">
        <i class="fa fa-cloud-upload-alt" aria-hidden="true"></i>
        <span>Uploaded</span>
        <span class="fe-count">${uploads.length}</span>
      </div>
      <div class="fe-file-list">
        ${uploads.length
          ? uploads.map(_wsFileItemHtml).join('')
          : '<div class="fe-empty-mini">No uploads yet</div>'}
      </div>
    </div>
    <div class="fe-divider" aria-hidden="true"></div>
    <div class="fe-section" data-kind="generated">
      <div class="fe-section-title">
        <i class="fa fa-cogs" aria-hidden="true"></i>
        <span>Generated</span>
        <span class="fe-count">${generated.length}</span>
      </div>
      <div class="fe-file-list">
        ${generated.length
          ? generated.map(_wsFileItemHtml).join('')
          : '<div class="fe-empty-mini">No outputs yet — run a step to generate files</div>'}
      </div>
    </div>
  `;
  body.querySelectorAll('.fe-file-item').forEach(btn => {
    btn.addEventListener('click', () => openFilePreview(btn.dataset.path));
  });
}

/* --- Preview modal (text / image / iframe) ----------------------------- */
function openFilePreview(relPath) {
  const sid = (typeof getCurrentSessionId === 'function') ? getCurrentSessionId() : null;
  if (!sid || !relPath) return;
  const url = `/api/sessions/${sid}/files/raw?path=${encodeURIComponent(relPath)}`;

  // Build modal
  const baseName = relPath.split('/').pop() || 'download';
  const modal = document.createElement('div');
  modal.className = 'fe-preview-modal';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-label', `Preview ${relPath}`);
  modal.innerHTML = `
    <div class="fe-preview-backdrop"></div>
    <div class="fe-preview-card">
      <div class="fe-preview-header">
        <i class="fa ${_wsIconForFile(relPath)}" aria-hidden="true"></i>
        <span class="fe-preview-name">${_wsEscHtml(relPath)}</span>
        <button class="fe-preview-dl icon" type="button"
                title="Download" aria-label="Download file">
          <i class="fa fa-download" aria-hidden="true"></i>
        </button>
        <button class="fe-preview-close icon" type="button" aria-label="Close preview">✕</button>
      </div>
      <div class="fe-preview-body"><div class="fe-preview-loading">Loading…</div></div>
    </div>
  `;
  document.body.appendChild(modal);

  const close = () => { modal.remove(); document.removeEventListener('keydown', escHandler); };
  function escHandler(e) { if (e.key === 'Escape') close(); }
  modal.querySelector('.fe-preview-close').addEventListener('click', close);
  modal.querySelector('.fe-preview-backdrop').addEventListener('click', close);
  document.addEventListener('keydown', escHandler);

  // Robust download: fetch with Bearer header → blob → trigger client-side
  // download. This works regardless of cookie state and forces the correct
  // filename (browsers ignore "download" attr for cross-origin or 401 cases).
  modal.querySelector('.fe-preview-dl').addEventListener('click', () => {
    _wsDownloadFile(url, baseName).catch(err => {
      try { notify('error', `Download failed: ${err?.message || err}`); } catch {}
    });
  });

  _wsLoadPreviewContent(url, relPath, modal.querySelector('.fe-preview-body'));
}

async function _wsDownloadFile(url, filename) {
  const btn = document.querySelector('.fe-preview-modal .fe-preview-dl');
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(url, { headers: { ...api.headers() } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = filename || 'download';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      try { document.body.removeChild(a); } catch {}
      URL.revokeObjectURL(blobUrl);
    }, 200);
  } finally {
    if (btn) btn.disabled = false;
  }
}
async function _wsLoadPreviewContent(url, relPath, container) {
  const ext = (relPath || '').split('.').pop().toLowerCase();
  const imgExt = new Set(['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp']);
  const iframeExt = new Set(['pdf', 'html', 'htm']);

  if (imgExt.has(ext)) {
    container.innerHTML = `<img class="fe-preview-img" src="${url}" alt="${_wsEscHtml(relPath)}" />`;
    return;
  }
  if (iframeExt.has(ext)) {
    container.innerHTML = `<iframe class="fe-preview-iframe" src="${url}" sandbox="allow-same-origin"></iframe>`;
    return;
  }
  // Default: text-like — fetch as bytes, decode UTF-8 with replacement, cap at 2 MB
  try {
    const res = await fetch(url, { headers: { ...api.headers() } });
    if (!res.ok) {
      container.innerHTML = `<div class="muted">Failed to load (HTTP ${res.status}).</div>`;
      return;
    }
    const buf = await res.arrayBuffer();
    const TEXT_CAP = 2 * 1024 * 1024;
    const slice = buf.byteLength > TEXT_CAP ? buf.slice(0, TEXT_CAP) : buf;
    const dec = new TextDecoder('utf-8', { fatal: false });
    let text = dec.decode(slice);
    if (buf.byteLength > TEXT_CAP) {
      text += `\n\n--- truncated (file is ${_wsHumanSize(buf.byteLength)} total) — use download ---`;
    }
    container.innerHTML = `<pre class="fe-preview-pre">${_wsEscHtml(text)}</pre>`;
  } catch (e) {
    container.innerHTML = `<div class="muted">Cannot preview: ${_wsEscHtml(e?.message || 'error')}</div>`;
  }
}

/* --- Auto-refresh helpers (debounced; only acts when panel is open) ---- */
let _wsDebounceTimer = null;
function refreshWorkspaceSoon(delay = 800) {
  const open = document.getElementById('content-area')?.classList.contains('fe-open');
  if (!open) return;
  clearTimeout(_wsDebounceTimer);
  _wsDebounceTimer = setTimeout(() => { refreshWorkspaceFiles().catch(() => {}); }, delay);
}
// Expose globally so other modules can trigger refreshes without imports
window.refreshWorkspaceFiles = refreshWorkspaceFiles;
window.refreshWorkspaceSoon = refreshWorkspaceSoon;

/* ========================== Settings Modal ============================== */
function openSettings() {
  const m = el('settings-modal'); if (!m) return;
  document.body.style.overflow = 'hidden';
  Promise.all([loadProviderIntoForm(), loadModelsIntoUI()])
    .finally(() => { m.classList.add('show'); m.classList.remove('hidden'); });
}
function closeSettings() {
  const m = el('settings-modal'); if (!m) return;
  m.classList.remove('show');
  setTimeout(() => { m.classList.add('hidden'); document.body.style.overflow = ''; }, 180);
}

/* Provider form */
async function loadProviderIntoForm() {
  try {
    const res = await fetch('/api/settings/provider', { headers: { ...api.headers() } });
    if (!res.ok) throw new Error('Failed to load provider settings');
    const cfg = await res.json();

    const src = el('set-source'); const base = el('set-base-url');
    const key = el('set-api-key'); const def = el('set-default-model');
    if (src) src.value = cfg.source || '';
    if (base) base.value = cfg.base_url || '';
    if (key) key.value = cfg.api_key || '***';
    if (def) def.value = cfg.default_model || '';

    await refreshModelSelectFromServer(cfg.default_model || undefined);
  } catch (e) {
    notify('error', e.message || 'Could not load settings');
  }
}

async function saveSettings() {
  const src = el('set-source')?.value || null;
  const base = el('set-base-url')?.value || null;
  const key = el('set-api-key')?.value ?? null;
  const def = el('set-default-model')?.value || null;

  try {
    const res = await fetch('/api/settings/provider', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...api.headers() },
      body: JSON.stringify({ source: src, base_url: base || null, api_key: key, default_model: def })
    });
    if (!res.ok) {
      const t = await res.text();
      throw new Error(t || 'Failed to save settings');
    }
    notify('success', 'Settings saved');
    await refreshModelSelectFromServer(def || undefined);
    closeSettings();
  } catch (e) {
    notify('error', e.message || 'Save failed');
  }
}

/* ============================ Models API ================================ */
async function fetchModels() {
  const res = await fetch('/api/settings/models', { headers: { ...api.headers() } });
  if (!res.ok) throw new Error('Failed to load models');
  return res.json(); // { system_default, user_models: [...] }
}

async function addModel() {
  const name = el('mdl-name')?.value.trim();
  const source = el('mdl-source')?.value;
  const base_url = el('mdl-base-url')?.value || null;
  const api_key = el('mdl-api-key')?.value || null;
  if (!name) { notify('error', 'Model name required'); return; }

  const res = await fetch('/api/settings/models', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...api.headers() },
    body: JSON.stringify({ name, source, base_url, api_key })
  });
  if (!res.ok) {
    notify('error', (await res.text()) || 'Add failed'); return;
  }
  if (el('mdl-name')) el('mdl-name').value = '';
  if (el('mdl-base-url')) el('mdl-base-url').value = '';
  if (el('mdl-api-key')) el('mdl-api-key').value = '';
  notify('success', 'Model added');
  await loadModelsIntoUI();
  await refreshModelSelectFromServer(name);
}

async function deleteModel(mid) {
  const res = await fetch(`/api/settings/models/${mid}`, {
    method: 'DELETE', headers: { ...api.headers() }
  });
  if (!res.ok) { notify('error', 'Delete failed'); return; }
  notify('success', 'Model deleted');
  await loadModelsIntoUI();
  await refreshModelSelectFromServer();
}

/* Render models list in modal (system default + user models) */
async function loadModelsIntoUI() {
  const list = el('models-list'); if (!list) return;
  list.innerHTML = '';
  let data;
  try {
    data = await fetchModels();
  } catch (e) {
    list.innerHTML = `<div class="muted">Could not load models.</div>`;
    return;
  }

  if (data.system_default?.model) {
    const s = data.system_default;
    const badge = document.createElement('div');
    badge.className = 'model-chip system';
    badge.innerHTML = `
      <div class="chip-head">
        <span class="chip-name">System default</span>
        <span class="chip-meta">${escapeHtml(s.model)}${s.source ? ' • ' + escapeHtml(s.source) : ''}</span>
      </div>
      <div class="chip-foot muted">${escapeHtml(s.base_url || '')}</div>`;
    list.appendChild(badge);
  }

  if (!data.user_models?.length) {
    const empty = document.createElement('div');
    empty.className = 'muted'; empty.style.padding = '6px 0';
    empty.textContent = 'No personal models yet.';
    list.appendChild(empty);
    return;
  }
  data.user_models.forEach(m => {
    const item = document.createElement('div');
    item.className = 'model-chip';
    item.innerHTML = `
      <div class="chip-head">
        <span class="chip-name">${escapeHtml(m.name)}</span>
        <span class="chip-meta">${escapeHtml(m.source || '')}</span>
        <button class="chip-del" title="Remove" aria-label="Remove">&times;</button>
      </div>
        <div class="chip-foot muted">${escapeHtml(m.base_url || '')}</div>`;
    item.querySelector('.chip-del').onclick = () => deleteModel(m.id);
    list.appendChild(item);
  });
}

/* Populate #model-select only from backend (system default + user models) */
async function refreshModelSelectFromServer(preferValue) {
  const sel = el('model-select'); if (!sel) return;
  sel.innerHTML = '';
  let data;
  try {
    data = await fetchModels();
  } catch (e) {
    const opt = document.createElement('option');
    opt.value = ''; opt.textContent = '— Select a model in Settings —';
    opt.disabled = true; opt.selected = true; sel.appendChild(opt);
    return;
  }

  const options = [];
  if (data.system_default?.model) {
    options.push({ value: data.system_default.model, label: `System: ${data.system_default.model}` });
  }
  (data.user_models || []).forEach(m => {
    options.push({ value: m.name, label: m.name });
  });

  if (!options.length) {
    const opt = document.createElement('option');
    opt.value = ''; opt.textContent = '— Select a model in Settings —'; opt.disabled = true; opt.selected = true;
    sel.appendChild(opt);
    return;
  }

  options.forEach(o => {
    const opt = document.createElement('option');
    opt.value = o.value; opt.textContent = o.label;
    sel.appendChild(opt);
  });

  // choose selected: prefer explicit arg, then localStorage, then system default, then first item
  const stored = localStorage.getItem('last_model');
  const fallback = data.system_default?.model || options[0]?.value;
  const preferred = preferValue || stored || fallback;

  if (preferred && options.some(o => o.value === preferred)) {
    sel.value = preferred;
  } else if (!sel.value && options.length) {
    sel.value = options[0].value;
  }
}

/* ------------------------------ Guard ----------------------------------- */
async function guardDashboard() {
  const isDashboard = !!document.getElementById('layout');
  if (!isDashboard) return;

  api.setToken(localStorage.getItem('agent_token') || '');
  if (!api.token) { window.location.replace('/login'); return; }

  const u = await me();
  if (!u) {
    localStorage.removeItem('agent_token');
    window.location.replace('/login');
    return;
  }
  await afterLogin();
}

/* ------------------------------ Splitter -------------------------------- */
function initSplitter() {
  const layout = el('layout'); const left = el('left'); const split = el('splitter');
  if (!layout || !left || !split) return;

  const min = 220, max = 680;
  let down = false, startX = 0, startW = 0;

  function apply(w) { layout.style.gridTemplateColumns = `${w}px 6px 1fr`; }

  const saved = parseInt(localStorage.getItem('sidebar_w') || '', 10);
  if (saved && saved > min && saved < max) apply(saved);

  split.addEventListener('mousedown', (e) => {
    down = true; startX = e.clientX; startW = left.offsetWidth;
    layout.classList.add('resizing'); document.body.style.userSelect = 'none';
  });
  window.addEventListener('mousemove', (e) => {
    if (!down) return;
    const dx = e.clientX - startX;
    const w = Math.max(min, Math.min(max, startW + dx));
    apply(w);
  });
  window.addEventListener('mouseup', () => {
    if (!down) return;
    down = false; layout.classList.remove('resizing'); document.body.style.userSelect = '';
    const w = left.offsetWidth; localStorage.setItem('sidebar_w', String(w));
  });
}
