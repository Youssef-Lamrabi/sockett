/* =========================================================================
   Agent Copilot – App JS (cleaned for model management + modal behavior)
   ========================================================================= */

const api = {
    token: null,
    setToken(t) { this.token = t; localStorage.setItem('agent_token', t || ''); },
    headers() { return this.token ? { 'Authorization': 'Bearer ' + this.token } : {}; }
};

/* -------------------------- Helpers ------------------------------------ */
function el(id) { return document.getElementById(id); }
function append(parent, html) { const div = document.createElement('div'); div.innerHTML = html; parent.appendChild(div.firstElementChild); }
function escapeHtml(s) { return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

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
            clearAuthErrors();
            el('login-email')?.focus();
        };
    }
    if (tabRegister) {
        tabRegister.onclick = (e) => {
            e.preventDefault();
            tabRegister.classList.add('active'); tabLogin?.classList.remove('active');
            reg?.classList.add('active'); login?.classList.remove('active');
            tabRegister.setAttribute('aria-selected', 'true'); tabLogin?.setAttribute('aria-selected', 'false');
            login?.setAttribute('aria-hidden', 'true'); reg?.setAttribute('aria-hidden', 'false');
            clearAuthErrors();
            el('reg-name')?.focus();
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

function autoGrowTextArea(t) {
    if (!t) return;
    t.style.height = 'auto';
    // Match the max-height in CSS (220px) so it stops growing and scrolls
    const max = 420;
    t.style.height = Math.min(t.scrollHeight, max) + 'px';
}

function focusComposer() {
    const msg = el('message');
    if (msg) {
        msg.value = '';
        if (typeof autoGrowTextArea === 'function') autoGrowTextArea(msg);
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

async function afterLogin() {
    const u = await me();
    if (!u) { showAuth(); return; }
    const nameNode = el('profile-name'), mailNode = el('profile-email'), avatar = el('profile-avatar');
    if (nameNode) nameNode.textContent = u.name || 'User';
    if (mailNode) mailNode.textContent = u.email || '';
    if (avatar) avatar.textContent = (u.name || u.email || 'U').charAt(0).toUpperCase();
    if (el('app')) { await loadSessions(); showApp(); }
}

/* ------------------------ Sessions (drawer UI) --------------------------- */
let sessionsCache = [];
// async function createSession() {
//     // Guard: require a model
//     if (!document.getElementById('model-select')?.value) {
//         notify('info', 'Add a model in Settings first');
//         openSettings();
//         return;
//     }

//     const sel = document.getElementById('model-select');
//     const model = sel ? sel.value : '';
//     const res = await fetch('/api/sessions', {
//         method: 'POST', headers: { 'Content-Type': 'application/json', ...api.headers() },
//         body: JSON.stringify({ title: 'New Chat', model })
//     });
//     if (!res.ok) { notify('error', 'Failed to create session'); return; }
//     notify('success', 'New chat created');
//     await loadSessions(true);
// }
async function createSession(focusAfter = false) {
    // Require a model first
    const modelSel = document.getElementById('model-select');
    if (!modelSel?.value) {
        notify('info', 'Add a model in Settings first');
        openSettings();
        return null;
    }

    const model = modelSel.value;
    const res = await fetch('/api/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...api.headers() },
        body: JSON.stringify({ title: 'New Chat', model })
    });
    if (!res.ok) {
        notify('error', 'Failed to create session');
        return null;
    }
    const data = await res.json();

    // Reload sessions (so the new one appears), then mark it active
    await loadSessions(false);
    markSessionActive(String(data.id));

    // Clear current chat view and get ready to type
    const chat = el('chat'); if (chat) chat.innerHTML = '';
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
    notify('success', `Model set to ${model}`);
}
async function loadSessions(andOpen = false) {
    const res = await fetch('/api/sessions', { headers: { ...api.headers() } });
    if (!res.ok) { return; }
    const data = await res.json(); sessionsCache = data || [];
    renderSessionsList(); if (andOpen) openSessionsDrawer();
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
          <div>
            <div class="title">${escapeHtml(title)}</div>
            <div class="meta">${escapeHtml(model)}</div>
          </div>
          <button class="open">Open</button>
        </div>
      `);
    });
    list.querySelectorAll('.session-item').forEach(item => {
        item.addEventListener('click', () => {
            list.querySelectorAll('.session-item').forEach(x => x.classList.remove('active'));
            item.classList.add('active');
        });
    });
    list.querySelectorAll('.session-item .open').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation(); closeSessionsDrawer();
            list.querySelectorAll('.session-item').forEach(x => x.classList.remove('active'));
            btn.parentElement.classList.add('active');
            notify('info', 'Chat opened');
        });
    });
}

/* -------------------------- Uploads ------------------------------------- */
async function uploadFile() {
    const input = document.querySelector('#composer input[type=file]'); const f = input?.files?.[0]; if (!f) return;
    const form = new FormData(); form.append('file', f);
    const res = await fetch('/api/upload', { method: 'POST', headers: { ...api.headers() }, body: form });
    if (!res.ok) { notify('error', 'Upload failed'); return; }
    const data = await res.json();
    append(el('logs'), `<div class="tag-block">Uploaded: ${escapeHtml(data.path)}</div>`);
    notify('success', 'File uploaded'); input.value = '';
}

/* --------------------- Logs/Chat rendering ------------------------------ */
function parseTaggedBlocks(text) {
    const out = []; const rx = /<(execute|observe|observation|solution|think|subscribe|logs)>([\s\S]*?)<\/\1>/ig;
    let lastIndex = 0, m;
    while ((m = rx.exec(text))) {
        if (m.index > lastIndex) { out.push({ kind: 'text', text: text.slice(lastIndex, m.index) }); }
        let name = m[1].toUpperCase(); if (name === 'OBSERVATION') name = 'OBSERVE';
        out.push({ kind: 'block', tag: name, text: m[2] }); lastIndex = rx.lastIndex;
    }
    if (lastIndex < text.length) { out.push({ kind: 'text', text: text.slice(lastIndex) }); }
    return out;
}
function renderToLogs(evt) {
    const logs = el('logs'); if (!logs) return;
    if (evt.type === 'done') { append(logs, `<div class="tag-block">[done]</div>`); logs.scrollTop = logs.scrollHeight; return; }
    const text = evt.text || ''; const parts = parseTaggedBlocks(text);
    parts.forEach(p => {
        if (p.kind === 'text') { const t = p.text.trim(); if (t) { append(logs, `<div class="tag-block">${escapeHtml(t)}</div>`); } }
        else {
            const cls = p.tag.toLowerCase() === 'execute' ? 'tag-execute'
                : p.tag.toLowerCase() === 'observe' ? 'tag-observe' : p.tag.toLowerCase();
            append(logs, `<div class="tag-block ${cls}"><b>${p.tag}</b><div>${escapeHtml(p.text)}</div></div>`);
        }
    }); logs.scrollTop = logs.scrollHeight;
}
// function renderToChat(role, content) {
//     const chat = el('chat'); if (!chat) return;
//     append(chat, `<div class="msg ${role}"><b>${role}</b><div>${escapeHtml(content)}</div></div>`);
//     chat.scrollTop = chat.scrollHeight;
// }
function renderToChat(role, content) {
    const chat = el('chat'); if (!chat) return;
    const htmlSafe = escapeHtml(content || "").replace(/\n/g, '<br>');
    const bubble =
        role === 'user'
            ? `<div class="msg user"><div class="bubble">${htmlSafe}</div></div>`
            : `<div class="msg assistant"><div class="bubble">${htmlSafe}</div></div>`;
    append(chat, bubble);
    chat.scrollTop = chat.scrollHeight;
}

/* -------------------- Stream routing helpers ------------------------- */
const CHAT_BLOCK_TAGS = new Set(["SOLUTION", "FINAL", "ANSWER", "REVIEW", "SUMMARY"]);
const LOG_BLOCK_TAGS = new Set(["EXECUTE", "OBSERVE", "LOGS", "SUBSCRIBE", "STATUS", "NEXT"]);

// strip the outermost <tag>...</tag> or <STATUS:...> wrapper for display
function stripOuterTag(s = "") {
    if (!s) return "";
    // paired tag
    s = s.replace(/^<([a-z]+)(\s+[^>]*)?>/i, "");
    s = s.replace(/<\/[a-z]+\s*>$/i, "");
    // standalone e.g. <STATUS:...>
    s = s.replace(/^<[^>]+>\s*/i, "");
    return s;
}

function classForTag(tag) {
    switch (String(tag || "").toUpperCase()) {
        case "EXECUTE": return "tag-execute";
        case "OBSERVE": return "tag-observe";
        case "SUBSCRIBE": return "tag-subscribe";
        case "LOGS": return "tag-logs";
        default: return "";
    }
}

/* render a single parsed block to the logs pane */
function renderBlockToLogs(evt) {
    const logs = el('logs'); if (!logs) return;
    const tag = String(evt.tag || "").toUpperCase();
    const body = stripOuterTag(evt.text || "");
    const cls = classForTag(tag);
    append(logs, `<div class="tag-block ${cls}"><b>${escapeHtml(tag)}</b><div>${escapeHtml(body)}</div></div>`);
    logs.scrollTop = logs.scrollHeight;
}

/* ------------------------- Send message --------------------------------- */
async function send() {
    // Guard: require a model
    if (!document.getElementById('model-select')?.value) {
        notify('info', 'Add a model in Settings first');
        openSettings();
        return;
    }

    const sid = getCurrentSessionId(); if (!sid) { notify('error', 'Create a session first'); return; }
    const msg = el('message')?.value; if (!msg) return;
    renderToChat('user', msg); el('message').value = '';
    const stream = el('stream')?.checked;
    const headers = { 'Content-Type': 'application/json', ...api.headers() };
    const body = JSON.stringify({ message: msg, stream });
    // if (stream) {
    //     const resp = await fetch(`/api/sessions/${sid}/messages`, { method: 'POST', headers, body });
    //     if (!resp.ok) { notify('error', 'Send failed'); return; }
    //     const reader = resp.body.getReader(); const dec = new TextDecoder(); let buffer = '';
    //     while (true) {
    //         const { value, done } = await reader.read(); if (done) break;
    //         buffer += dec.decode(value, { stream: true });
    //         const lines = buffer.split('\n'); buffer = lines.pop();
    //         for (const line of lines) {
    //             if (!line.trim()) continue;
    //             try { const evt = JSON.parse(line); renderToLogs(evt); if (evt.type === 'message') { renderToChat('assistant', evt.text); } } catch { }
    //         }
    //     }
    //     if (buffer.trim()) { try { const evt = JSON.parse(buffer.trim()); renderToLogs(evt); } catch { } }
    // } 
    if (stream) {
        const resp = await fetch(`/api/sessions/${sid}/messages`, { method: 'POST', headers, body });
        if (!resp.ok) { notify('error', 'Send failed'); return; }

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

                    // end marker
                    if (evt.type === 'done') { renderBlockToLogs({ tag: 'STATUS', text: '<OK/>' }); continue; }

                    // plain assistant text (untagged)
                    if (evt.type === 'message') {
                        if ((evt.text || '').trim()) renderToChat('assistant', evt.text);
                        continue;
                    }

                    // tagged blocks
                    if (evt.type === 'block') {
                        const tag = String(evt.tag || '').toUpperCase();
                        if (CHAT_BLOCK_TAGS.has(tag)) {
                            const inner = stripOuterTag(evt.text || '');
                            if (inner.trim()) renderToChat('assistant', inner);
                        } else {
                            renderBlockToLogs(evt);
                        }
                        continue;
                    }

                    // optional: send 'think' to logs but lightly
                    if (evt.type === 'think') {
                        // comment out if you prefer to hide thoughts
                        renderBlockToLogs({ tag: 'THINK', text: evt.text || '' });
                        continue;
                    }

                } catch { /* ignore malformed line */ }
            }
        }

        // flush trailing chunk
        if (buffer.trim()) {
            try {
                const evt = JSON.parse(buffer.trim());
                if (evt.type === 'message') {
                    if ((evt.text || '').trim()) renderToChat('assistant', evt.text);
                } else if (evt.type === 'block') {
                    const tag = String(evt.tag || '').toUpperCase();
                    if (CHAT_BLOCK_TAGS.has(tag)) {
                        const inner = stripOuterTag(evt.text || '');
                        if (inner.trim()) renderToChat('assistant', inner);
                    } else {
                        renderBlockToLogs(evt);
                    }

                }
            } catch { }
        }
    }
    else {
        const res = await fetch(`/api/sessions/${sid}/messages`, { method: 'POST', headers, body });
        if (!res.ok) { notify('error', 'Send failed'); return; }
        const data = await res.json(); renderToChat('assistant', data.message || '');
    }
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
// function boot() {
//     tabAuth(); wirePasswordToggles(); wireFieldListeners();

//     // Settings modal buttons
//     const openSet = el('btn-open-settings'); if (openSet) openSet.onclick = (e) => { e.preventDefault(); openSettings(); };
//     const closeSet = el('settings-close'); if (closeSet) closeSet.onclick = (e) => { e.preventDefault(); closeSettings(); };
//     const saveSet = el('settings-save'); if (saveSet) saveSet.onclick = (e) => { e.preventDefault(); saveSettings(); };

//     // Close modal by clicking backdrop
//     const modal = el('settings-modal');
//     if (modal) {
//         modal.addEventListener('click', (e) => { if (e.target === modal) closeSettings(); });
//     }

//     // Auth + guard
//     api.setToken(localStorage.getItem('agent_token') || '');
//     guardDashboard().then(async () => {
//         // Populate model select from backend only
//         await refreshModelSelectFromServer();
//         if (!document.getElementById('model-select')?.value) {
//             notify('info', 'No models configured yet. Add one in Settings.');
//             openSettings();
//         }
//     });

//     // Auth buttons
//     el('btn-register')?.addEventListener('click', (e) => { e.preventDefault(); register(); });
//     el('btn-login')?.addEventListener('click', (e) => { e.preventDefault(); login(); });

//     // Sessions
//     el('btn-new-session')?.addEventListener('click', (e) => { e.preventDefault(); createSession(); });
//     el('drawer-new')?.addEventListener('click', (e) => { e.preventDefault(); createSession(); });
//     const modelSel = document.getElementById('model-select'); if (modelSel) modelSel.addEventListener('change', setSessionModelAuto);

//     // Drawer open/close
//     el('btn-open-sessions')?.addEventListener('click', (e) => { e.preventDefault(); openSessionsDrawer(); });
//     // HTML had an id typo; be robust here:
//     const closeSessBtn = document.querySelector('#close-sessions, [id="close-sessions close-history"], .close-history');
//     closeSessBtn?.addEventListener('click', (e) => { e.preventDefault(); closeSessionsDrawer(); });
//     el('sessions-backdrop')?.addEventListener('click', () => closeSessionsDrawer());

//     // Chat send
//     el('send')?.addEventListener('click', (e) => { e.preventDefault(); send(); });
//     // el('message')?.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); send(); } });
//     const msgInput = el('message');
//     if (msgInput) {
//         // Auto-grow on load & input
//         autoGrowTextArea(msgInput);
//         msgInput.addEventListener('input', () => autoGrowTextArea(msgInput));

//         // Enter => send, Shift+Enter => newline
//         msgInput.addEventListener('keydown', (e) => {
//             if (e.key === 'Enter' && !e.shiftKey) {
//                 e.preventDefault();
//                 send();
//             }
//         });
//     }

//     // Uploads
//     const uploadInput = document.querySelector('#composer input[type=file]'); if (uploadInput) uploadInput.addEventListener('change', uploadFile);

//     // Logout
//     el('btn-logout')?.addEventListener('click', (e) => { e.preventDefault(); logout(); });

//     // Splitter (dashboard only)
//     initSplitter();

//     // Models: add button in modal (if present in your HTML)
//     el('mdl-add')?.addEventListener('click', (e) => { e.preventDefault(); addModel(); });

//     const newTopBtn = el('btn-new-chat');
//     if (newTopBtn) newTopBtn.onclick = (e) => { e.preventDefault(); createSession(true); };
//     const drawerNew = el('drawer-new');
//     if (drawerNew) drawerNew.onclick = (e) => { e.preventDefault(); createSession(true); };
// }
function boot() {
    tabAuth(); wirePasswordToggles(); wireFieldListeners();

    // Settings modal buttons
    const openSet = el('btn-open-settings'); if (openSet) openSet.onclick = (e) => { e.preventDefault(); openSettings(); };
    const closeSet = el('settings-close'); if (closeSet) closeSet.onclick = (e) => { e.preventDefault(); closeSettings(); };
    const saveSet = el('settings-save'); if (saveSet) saveSet.onclick = (e) => { e.preventDefault(); saveSettings(); };

    // Close modal by clicking backdrop
    const modal = el('settings-modal');
    if (modal) modal.addEventListener('click', (e) => { if (e.target === modal) closeSettings(); });

    // Auth + guard
    api.setToken(localStorage.getItem('agent_token') || '');
    guardDashboard().then(async () => {
        await refreshModelSelectFromServer();
        if (!document.getElementById('model-select')?.value) {
            notify('info', 'No models configured yet. Add one in Settings.');
            openSettings();
        }
    });

    // Auth buttons
    el('btn-register')?.addEventListener('click', (e) => { e.preventDefault(); register(); });
    el('btn-login')?.addEventListener('click', (e) => { e.preventDefault(); login(); });

    // Sessions: model change -> update session model
    const modelSel = document.getElementById('model-select');
    if (modelSel) modelSel.addEventListener('change', setSessionModelAuto);

    // Drawer open/close
    el('btn-open-sessions')?.addEventListener('click', (e) => { e.preventDefault(); openSessionsDrawer(); });
    const closeSessBtn = document.querySelector('#close-sessions, [id="close-sessions close-history"], .close-history');
    closeSessBtn?.addEventListener('click', (e) => { e.preventDefault(); closeSessionsDrawer(); });
    el('sessions-backdrop')?.addEventListener('click', () => closeSessionsDrawer());

    // New Chat flow (topbar + drawer + legacy id)
    const newChatFlow = async (e) => {
        e?.preventDefault?.();
        await createSession();              // has model guard inside
        closeSessionsDrawer();
        const chat = el('chat'); if (chat) chat.innerHTML = '';
        const logs = el('logs'); if (logs) logs.innerHTML = '';
        const msg = el('message'); if (msg) { msg.value = ''; msg.focus(); }
    };
    el('btn-new-chat')?.addEventListener('click', newChatFlow);
    el('drawer-new')?.addEventListener('click', newChatFlow);
    // keep support for old id if it still exists
    el('btn-new-session')?.addEventListener('click', newChatFlow);

    // Chat send
    el('send')?.addEventListener('click', (e) => { e.preventDefault(); send(); });

    // Textarea: auto-grow + Enter=send, Shift+Enter=newline
    const msgInput = el('message');
    if (msgInput) {
        autoGrowTextArea(msgInput);
        msgInput.addEventListener('input', () => autoGrowTextArea(msgInput));
        msgInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                send();
            }
        });
    }

    // Uploads
    const uploadInput = document.querySelector('#composer input[type=file]');
    if (uploadInput) uploadInput.addEventListener('change', uploadFile);

    // Logout
    el('btn-logout')?.addEventListener('click', (e) => { e.preventDefault(); logout(); });

    // Splitter (dashboard only)
    initSplitter();

    // Models: add button in modal
    el('mdl-add')?.addEventListener('click', (e) => { e.preventDefault(); addModel(); });
}



window.addEventListener('DOMContentLoaded', boot);

/* Global error -> toast */
window.addEventListener('unhandledrejection', (e) => {
    notify('error', e?.reason?.message || 'Unexpected error');
});
window.addEventListener('error', (e) => {
    notify('error', e?.message || 'Unexpected error');
});

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

        // Keep the select strictly in sync with backend models
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
    // Clear inputs
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
    const list = el('models-list'); if (!list) return; // only if your HTML has this block
    list.innerHTML = '';
    let data;
    try {
        data = await fetchModels();
    } catch (e) {
        list.innerHTML = `<div class="muted">Could not load models.</div>`;
        return;
    }

    // System default
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

    // User models
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
        // Backend not ready or error -> placeholder only
        const opt = document.createElement('option');
        opt.value = ''; opt.textContent = '— Select a model in Settings —';
        opt.disabled = true; opt.selected = true; sel.appendChild(opt);
        return;
    }

    const options = [];

    // System default first
    if (data.system_default?.model) {
        options.push({ value: data.system_default.model, label: `System: ${data.system_default.model}` });
    }
    // User models
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

    if (preferValue && options.some(o => o.value === preferValue)) {
        sel.value = preferValue;
    } else if (!sel.value && options.length) {
        sel.value = options[0].value;
    }
}

/* ------------------------------ Guard ----------------------------------- */
async function guardDashboard() {
    const isDashboard = !!document.getElementById('layout'); // present only on /dashboard
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
