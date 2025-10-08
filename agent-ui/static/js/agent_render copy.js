const $ = (id) => document.getElementById(id);

/* -------------------- toggle: live typing or instant paste -------------- */
const CHAT_SIMULATE_TYPING = true;
const CHAT_TYPING_TICK_MS = 5;
const CHAT_TYPING_CHARS_PER_TICK = 1;

const CHAT_ALWAYS_NEW_BUBBLE_PER_CHAT_BLOCK = true;
const CHAT_LIVE_MARKDOWN_ON_NEWLINE = true;  // live MD preview only when newline arrives

/* ---------- Markdown (marked + DOMPurify if present; safe fallback) ---- */
function mdToHtml(md = "") {
  if (window.marked?.parse) {
    const raw = window.marked.parse(md, { breaks: true });
    return window.DOMPurify?.sanitize ? window.DOMPurify.sanitize(raw) : raw;
  }
  // minimal fallback
  let s = escapeHtml(md);
  s = s.replace(/```([\s\S]*?)```/g, (_, code) => `<pre><code>${code.replace(/^\n+|\n+$/g, '')}</code></pre>`);
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  s = s.replace(/^###### (.*)$/gm, '<h6>$1</h6>')
    .replace(/^##### (.*)$/gm, '<h5>$1</h5>')
    .replace(/^#### (.*)$/gm, '<h4>$1</h4>')
    .replace(/^### (.*)$/gm, '<h3>$1</h3>')
    .replace(/^## (.*)$/gm, '<h2>$1</h2>')
    .replace(/^# (.*)$/gm, '<h1>$1</h1>');
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  s = s.replace(/\n/g, '<br>');
  return s;
}

/* ------------------------------- Utils ---------------------------------- */
export function escapeHtml(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Kept for backward-compat
export function parseTaggedBlocks(text) {
  const out = [];
  const rx = /<(execute|observe|observation|solution|think|subscribe|logs)>([\s\S]*?)<\/\1>/ig;
  let last = 0, m;
  while ((m = rx.exec(text))) {
    if (m.index > last) out.push({ kind: 'text', text: text.slice(last, m.index) });
    let name = m[1].toUpperCase(); if (name === 'OBSERVATION') name = 'OBSERVE';
    out.push({ kind: 'block', tag: name, text: m[0] });
    last = rx.lastIndex;
  }
  if (last < text.length) out.push({ kind: 'text', text: text.slice(last) });
  return out;
}

/* -------------------- Chat bubbles + typing indicator ------------------- */
let typingEl = null;
let currentAssistantEl = null;

function beginAssistantMessage() {
  const chat = $('chat'); if (!chat) return null;
  const div = document.createElement('div');
  div.className = 'msg assistant';
  div.innerHTML = `<div class="bubble"><div class="live"></div></div>`;
  chat.appendChild(div);
  currentAssistantEl = div;
  chat.scrollTop = chat.scrollHeight;
  return div;
}
function liveContainer() {
  if (!currentAssistantEl) beginAssistantMessage();
  let c = currentAssistantEl?.querySelector('.bubble .live');
  if (!c && currentAssistantEl) {
    c = document.createElement('div');
    c.className = 'live';
    currentAssistantEl.querySelector('.bubble').appendChild(c);
  }
  return c;
}
function appendAssistantMarkdown(mdChunk = "") {
  if (!currentAssistantEl) beginAssistantMessage();
  const container = currentAssistantEl.querySelector('.bubble');
  const live = container.querySelector('.live'); if (live) live.remove();
  const html = mdToHtml(mdChunk);
  const frag = document.createElement('div');
  frag.innerHTML = html;
  container.appendChild(frag);
  const chat = $('chat'); if (chat) chat.scrollTop = chat.scrollHeight;
}

// create a standalone system bubble in chat (separate from streaming)
function renderChatCard(html) {
  const chat = $('chat'); if (!chat) return null;
  const div = document.createElement('div');
  div.className = 'msg assistant';
  div.innerHTML = `<div class="bubble">${html}</div>`;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}

export function renderUserMessage(content) {
  const chat = $('chat'); if (!chat) return;
  const item = document.createElement('div');
  item.className = 'msg user';
  item.innerHTML = `<div class="bubble user-bubble">${escapeHtml(content).replace(/\n/g, '<br>')}</div>`;
  chat.appendChild(item);
  chat.scrollTop = chat.scrollHeight;
}
export function renderAssistantMessage(content) {
  if (CHAT_SIMULATE_TYPING) pushAssistantChunkLive(content || "");
  else appendAssistantMarkdown(content || "");
}

export function showAssistantTyping() {
  const chat = $('chat'); if (!chat) return;
  if (typingEl) return;
  const div = document.createElement('div');
  div.className = 'msg assistant typing';
  div.innerHTML = `<div class="bubble"><span class="dots"><span></span><span></span><span></span></span></div>`;
  chat.appendChild(div);
  typingEl = div;
  chat.scrollTop = chat.scrollHeight;
}
export function hideAssistantTyping() {
  if (typingEl?.parentNode) typingEl.parentNode.removeChild(typingEl);
  typingEl = null;
}

export function renderUserMessageWithAttachments(content, attachments = []) {
  const chat = $('chat'); if (!chat) return;

  const grid = attachments && attachments.length ? `
    <div class="att-grid" style="display:flex;flex-wrap:wrap;gap:10px;margin:0 0 10px 0;">
      ${attachments.map(a => {
        const isImg = /^image\//i.test(a.type) || /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(a.name || '');
        const ext   = (a.name?.split('.').pop() || a.type?.split('/').pop() || '').toUpperCase();
        const src   = a.previewUrl || a.url || a.path || '';
        const safeName = escapeHtml(a.name || 'file');
        return `
          <div class="att-card" style="width:140px;border:1px solid #E3E6EA;border-radius:10px;overflow:hidden;background:#fff;">
            <div class="att-preview" style="height:90px;display:flex;align-items:center;justify-content:center;background:#F8F9FB;">
              ${isImg && src
                ? `<img src="${escapeHtml(src)}" alt="${safeName}" style="max-width:100%;max-height:100%;display:block;">`
                : `<div style="font-weight:700;font-family:ui-monospace,monospace;opacity:.7;">.${escapeHtml(ext || 'FILE')}</div>`
              }
            </div>
            <div class="att-meta" style="padding:6px 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
              <span style="background:#eef2ff;border:1px solid #c7d2fe;color:#3730a3;padding:0 6px;border-radius:999px;font-size:11px;margin-right:6px;">.${escapeHtml(ext || 'file')}</span>
              <span title="${safeName}" style="font-size:12px;opacity:.85;">${safeName}</span>
            </div>
          </div>
        `;
      }).join('')}
    </div>
  ` : ``;

  const item = document.createElement('div');
  item.className = 'msg user';
  item.innerHTML = `
    <div class="bubble user-bubble">
      ${grid}
      <div>${escapeHtml(content || '').replace(/\n/g, '<br>')}</div>
    </div>`;
  chat.appendChild(item);
  chat.scrollTop = chat.scrollHeight;
}

/* --------- live typing (only when CHAT_SIMULATE_TYPING = true) ---------- */
const TYPE_TICK_MS = CHAT_TYPING_TICK_MS;
const TYPE_CHARS_PER_TICK = CHAT_TYPING_CHARS_PER_TICK;
let liveBuf = "";
let liveFull = "";
let liveTimer = null;
let finalizePending = false;

function tickTypewriter() {
  if (!liveBuf.length) {
    clearInterval(liveTimer); liveTimer = null;
    if (finalizePending) { finalizePending = false; finalizeAssistantMessageLive(); }
    return;
  }
  const take = liveBuf.slice(0, TYPE_CHARS_PER_TICK);
  liveBuf = liveBuf.slice(TYPE_CHARS_PER_TICK);
  liveFull += take;

  const c = liveContainer(); if (!c) return;

  if (CHAT_LIVE_MARKDOWN_ON_NEWLINE && take.includes('\n')) {
    c.innerHTML = mdToHtml(liveFull);
  } else {
    c.innerHTML += escapeHtml(take).replace(/\n/g, '<br>');
  }

  const chat = $('chat'); if (chat) chat.scrollTop = chat.scrollHeight;
}

function pushAssistantChunkLive(chunk = "") {
  if (!chunk) return;
  liveContainer();
  hideAssistantTyping();

  const already = liveFull + liveBuf;
  if (chunk.startsWith(already)) {
    chunk = chunk.slice(already.length);
  }
  liveBuf += chunk;

  if (!liveTimer) {
    tickTypewriter();
    liveTimer = setInterval(tickTypewriter, TYPE_TICK_MS);
  }
}

function finalizeAssistantMessageLive() {
  const bubble = currentAssistantEl?.querySelector('.bubble');
  if (!bubble) { liveFull = ""; liveBuf = ""; return; }

  if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }

  if (liveBuf && liveBuf.length) {
    liveFull += liveBuf;
    liveBuf = "";
  }

  bubble.innerHTML = mdToHtml(liveFull || "");
  liveFull = "";

  // end streaming bubble; next text starts a new one AFTER any cards we insert
  currentAssistantEl = null;
}

/* ------------------------------ Logs pane ------------------------------- */
const COLLAPSIBLE_TAGS = new Set(['EXECUTE', 'OBSERVE', 'LOGS', 'THINK']);
// function renderCollapsibleLog(tag, body) {
//   const box = $('logs'); if (!box) return null;
//   const isExec = tag === 'EXECUTE';
//   const wrap = document.createElement('div');
//   wrap.className = 'tag-block';

//   const preOpen = isExec
//     ? `<pre style="margin:0;white-space:pre-wrap;background:#0d1117;color:#c9d1d9;padding:10px;border-radius:8px;overflow:auto;"><code>`
//     : `<pre style="margin:0;white-space:pre-wrap;"><code>`;
//   const preClose = `</code></pre>`;

//   wrap.innerHTML = `
//       <div class="collap-head" role="button" tabindex="0" aria-expanded="false"
//            style="display:flex;align-items:center;gap:8px;cursor:pointer; justify-content: space-between;">
//         <b>${escapeHtml(tag)}</b>
//         <div>
//         <span class="log-live" style="margin-left:auto;display:none;opacity:.7;">
//           <i class="fa fa-spinner fa-spin"></i>
//         </span>
//         <span class="chev" style="opacity:.7;margin-left:8px;">▼</span>
//         </div>
//       </div>
//       <div class="collap-body" style="display:none;margin-top:6px;">
//         ${preOpen}${escapeHtml(body || '')}${preClose}
//       </div>
//     `;

//   const head = wrap.querySelector('.collap-head');
//   const bodyEl = wrap.querySelector('.collap-body');
//   const chev = wrap.querySelector('.chev');

//   const toggle = () => {
//     const open = bodyEl.style.display !== 'none';
//     bodyEl.style.display = open ? 'none' : 'block';
//     head.setAttribute('aria-expanded', String(!open));
//     chev.textContent = open ? '▼' : '▲';
//   };
//   head.addEventListener('click', toggle);
//   head.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });

//   box.appendChild(wrap);
//   box.scrollTop = box.scrollHeight;

//   window.dispatchEvent(new Event('logs:changed'));
//   return wrap;
// }
function _startsWith(body, prefix) {
  return String(body || '').trim().toLowerCase().startsWith(String(prefix || '').toLowerCase());
}
function _firstLine(s) {
  return String(s || '').trim().split(/\r?\n/)[0] || '';
}
function _truncate(s, n = 140) {
  const t = String(s || '');
  return t.length <= n ? t : t.slice(0, n - 1) + '…';
}
function renderCollapsibleLog(tag, body) {
  const box = $('logs'); if (!box) return null;
  const isExec = tag === 'EXECUTE';
  const wrap = document.createElement('div');
  wrap.className = 'tag-block';

  // decide default-open + optional header snippet
  const trimmed = String(body || '').trim();
  const openByDefault =
    _startsWith(trimmed, 'all steps complete') ||       // "All steps complete. Finalizing…"
    _startsWith(trimmed, 'code execution out');         // "Code Execution output: …"

  let headerSnippet = '';
  if (_startsWith(trimmed, 'code execution out')) {
    const line = _firstLine(trimmed);
    // keep the prefix visible, truncate rest
    headerSnippet = _truncate(line, 160);
  }

  const preOpen = isExec
    ? `<pre style="margin:0;white-space:pre-wrap;background:#0d1117;color:#c9d1d9;padding:10px;border-radius:8px;overflow:auto;"><code>`
    : `<pre style="margin:0;white-space:pre-wrap;"><code>`;
  const preClose = `</code></pre>`;

  // split header into left (title + snippet) and right (spinner + chevron)
  wrap.innerHTML = `
    <div class="collap-head" role="button" tabindex="0" aria-expanded="false"
         style="display:flex;align-items:center;gap:8px;cursor:pointer;justify-content:space-between;">
      <div class="head-left" style="display:flex;align-items:center;gap:10px;min-width:0;">
        <b>${escapeHtml(tag)}</b>
        ${headerSnippet ? `
          <span class="head-snippet"
                style="opacity:.75;font-family:ui-monospace, SFMono-Regular, Menlo, monospace;
                       font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:52vw;">
            ${escapeHtml(headerSnippet)}
          </span>` : ``}
      </div>
      <div class="head-right" style="display:flex;align-items:center;gap:8px;">
        <span class="log-live" style="margin-left:auto;display:none;opacity:.7;">
          <i class="fa fa-spinner fa-spin"></i>
        </span>
        <span class="chev" style="opacity:.7;margin-left:8px;">▼</span>
      </div>
    </div>
    <div class="collap-body" style="display:none;margin-top:6px;">
      ${preOpen}${escapeHtml(body || '')}${preClose}
    </div>
  `;

  const head = wrap.querySelector('.collap-head');
  const bodyEl = wrap.querySelector('.collap-body');
  const chev = wrap.querySelector('.chev');

  const toggle = () => {
    const open = bodyEl.style.display !== 'none';
    bodyEl.style.display = open ? 'none' : 'block';
    head.setAttribute('aria-expanded', String(!open));
    chev.textContent = open ? '▼' : '▲';
  };
  head.addEventListener('click', toggle);
  head.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
  });

  box.appendChild(wrap);

  // default-open logic
  if (openByDefault) {
    bodyEl.style.display = 'block';
    head.setAttribute('aria-expanded', 'true');
    chev.textContent = '▲';
  }

  box.scrollTop = box.scrollHeight;
  window.dispatchEvent(new Event('logs:changed'));
  return wrap;
}


export function renderLogBlock(tag, body) {
  const box = $('logs'); if (!box) return;
  const upper = String(tag || '').toUpperCase();

  if (COLLAPSIBLE_TAGS.has(upper)) {
    const el = renderCollapsibleLog(upper, body);
    setLastLogLive(el);
    return;
  }

  if (upper === 'NEXT') {
    const el = renderNextCardFromRaw(`<NEXT:${body || ''}>`);
    setLastLogLive(el);
    return;
  }

  if (upper === 'STATUS') {
    const val = parseStatusValue(String(body || '')) || (String(body || '').trim() || '');
    const el = renderStatusBox(val);
    setLastLogLive(el);
    return;
  }

  const div = document.createElement('div');
  div.className = `tag-block ${upper ? 'tag-' + upper.toLowerCase() : ''}`;
  div.innerHTML = `
      <div class="log-head" style="display:flex;align-items:center;gap:8px; justify-content: space-between;">
        <b>${escapeHtml(upper)}</b>
        <span class="log-live" style="margin-left:auto;display:none;opacity:.7;">
          <i class="fa fa-spinner fa-spin"></i>
        </span>
      </div>
      <div style="margin-top:4px;">${escapeHtml(body || '')}</div>
    `;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;

  setLastLogLive(div);
  window.dispatchEvent(new Event('logs:changed'));

  if (upper === 'STATUS' && /\[done\]/i.test(String(body || ''))) {
    clearLiveSpinner();
  }
}

/* ------------------------ NEXT (switch) yellow card --------------------- */
function extractNextNode(raw = "") {
  const s = String(raw).trim();

  let m = s.match(/^<\s*next\s*:\s*([^>]+)>/i);
  if (m) return m[1].replace(/\s+/g, '');

  m = s.match(/^<\s*next\s*>([\s\S]*?)<\/\s*next\s*>/i);
  if (m) {
    let payload = m[1].trim();
    const kv = payload.match(/(?:^|\s)(?:node|agent)\s*=\s*([^|\n]+)/i);
    const name = kv ? kv[1] : payload.split(/[\|\n]/)[0];
    return String(name).replace(/\s+/g, '');
  }

  m = s.match(/next\s*:\s*([^\]>]+)/i);
  if (m) return m[1].replace(/\s+/g, '');

  return "Unknown";
}
function renderNextCardFromRaw(raw = "") {
  const node = extractNextNode(raw);
  const box = $('logs'); if (!box) return;

  const div = document.createElement('div');
  div.className = 'tag-block tag-next';
  div.style.background = '#FFF7CC';
  div.style.border = '1px solid #F3E6A4';
  div.style.borderRadius = '8px';
  div.style.padding = '8px 10px';

  div.innerHTML = `
      <div class="log-head" style="display:flex;align-items:center;gap:8px;">
        <b>NEXT</b>
        <span style="opacity:.6;">
            <i class='fa fa-arrow-right'></i>
        </span>
        <span class="next-node" style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;padding:2px 6px;border-radius:6px;background:#fffbe6;border:1px dashed #eadf9a;min-width:15em;">
          ${escapeHtml(node)}
        </span>
        <span class="log-live" style="margin-left:auto;display:none;opacity:.7;">
          <i class="fa fa-spinner fa-spin"></i>
        </span>
      </div>
    `;

  box.appendChild(div);
  box.scrollTop = box.scrollHeight;

  setLastLogLive(div);
  window.dispatchEvent(new Event('logs:changed'));
  return div;
}

/* -------------------- RUNNING + DESCRIPTION (pair) ---------------------- */
let currentRun = null;              // { id, step, desc, logsEl, chatEl }
let runSeqCounter = 0;
const runMap = new Map();           // id -> currentRun

function parseRunStepAttr(raw = "") {
  const m = String(raw).match(/step\s*=\s*["']?(\d+)/i);
  return m ? parseInt(m[1], 10) : null;
}

function renderRunCardInLogs(step = null, desc = "") {
  const box = $('logs'); if (!box) return null;
  const id = `run-${++runSeqCounter}`;

  const el = document.createElement('div');
  el.className = 'tag-block run-log-card';
  el.dataset.runId = id;
  el.style.background = '#EAF3FF';
  el.style.border = '1px solid #B7D3FF';
  el.style.borderRadius = '10px';
  el.style.padding = '10px';
  el.style.cursor = 'pointer';

  el.innerHTML = `
    <div class="run-head" style="display:flex;align-items:center;gap:10px;font-weight:600;">
      <span class="badge" style="background:#d8e8ff;border:1px dashed #9fbff7;border-radius:8px;padding:2px 8px;">
        ${step != null ? `Step ${step}` : 'Running'}
      </span>
      <span style="opacity:.6;"><i class="fa fa-play"></i></span>
      <span style="font-weight:500;">Execution</span>

      <button class="btn-stop-run" title="Stop"
              onclick="window.stopCurrentRun && window.stopCurrentRun()"
              style="margin-left:auto;background:#ffebee;border:1px solid #ef9a9a;color:#b00020;
                     border-radius:6px;padding:2px 8px;cursor:pointer;font-weight:600;">
        <i class="fa fa-stop" aria-hidden="true"></i> Stop
      </button>

      <span class="log-live" style="display:none;opacity:.7;">
        <i class="fa fa-spinner fa-spin"></i>
      </span>
    </div>
    <div class="run-desc-md" style="margin-top:6px;">${desc ? mdToHtml(desc) : ''}</div>
    <div class="muted" style="margin-top:6px;font-size:12px;color:#3b6ea9;">Click to show in chat</div>
  `;

  el.addEventListener('click', () => {
    const r = runMap.get(id);
    if (!r) return;
    if (!r.chatEl) r.chatEl = renderRunningCardInChat(r.step, r.desc);
    r.chatEl?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
  });

  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
  window.dispatchEvent(new Event('logs:changed'));
  return { id, el };
}

function renderRunningCardInChat(step = null, descText = null) {
  const html = `
    <div class="run-card" style="background:#F4F5F7;border:1px solid #D0D4DA;border-radius:10px;padding:10px">
      <div style="display:flex;gap:10px;align-items:center;font-weight:600;">
        <span class="badge" style="background:#e9ebef;border:1px dashed #c7ccd4;border-radius:8px;padding:2px 8px;">
          ${step != null ? `Step ${step}` : 'Running'}
        </span>
        <span style="font-weight:500;">Execution</span>
      </div>
      <div class="run-desc" style="margin-top:6px;">${descText ? mdToHtml(descText) : ''}</div>
    </div>`;
  return renderChatCard(html);
}
function updateRunCards(desc) {
  if (!currentRun) return;
  const md = mdToHtml(String(desc || '').trim());
  currentRun.desc = String(desc || '');

  const logsDesc = currentRun.logsEl?.querySelector('.run-desc-md');
  if (logsDesc) logsDesc.innerHTML = md;

  const chatDesc = currentRun.chatEl?.querySelector('.run-desc');
  if (chatDesc) chatDesc.innerHTML = md;
}

/* ------------------------- MISSING -> red list -------------------------- */
function parseMissingItems(raw = "") {
  const inner = String(raw).replace(/^<[^>]+>/, '').replace(/<\/[^>]+>$/, '').trim();
  const lines = inner.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  const items = [];
  for (let ln of lines) {
    if (ln.startsWith('-')) ln = ln.replace(/^-\s*/, '');
    if (!ln) continue;
    const m = ln.split('::');
    if (m.length >= 2) {
      items.push({ name: m[0].trim(), reason: m.slice(1).join('::').trim() });
    } else {
      items.push({ name: ln.trim(), reason: '' });
    }
  }
  return items;
}
function renderMissingInChat(raw = "") {
  const items = parseMissingItems(raw);
  if (!items.length) return null;
  const listHtml = items.map(it => `
    <li style="margin:2px 0;">
      <span style="font-weight:600;">${escapeHtml(it.name)}</span>
      ${it.reason ? `<span style="opacity:.8;"> — ${escapeHtml(it.reason)}</span>` : ''}
    </li>`).join('');
  const html = `
    <div class="missing-card" style="background:#FFF0F0;border:1px solid #F5B7B7;border-radius:10px;padding:10px">
      <div style="display:flex;gap:10px;align-items:center;font-weight:700;color:#B00020;">
        <i class="fa fa-exclamation-triangle" aria-hidden="true"></i>
        <span>Missing requirements</span>
      </div>
      <ul style="margin:8px 0 0 18px; color:#6b0000;">${listHtml}</ul>
    </div>`;
  return renderChatCard(html);
}

/* -------------------------- STATUS chip (logs) -------------------------- */
function parseStatusValue(raw = "") {
  const m = String(raw).match(/<\s*status\s*:\s*([^>]+)>/i);
  if (m) return m[1].trim();
  return String(raw).trim();
}
function renderStatusBox(value = "") {
  const box = $('logs'); if (!box) return null;
  const val = String(value || '').trim() || 'unknown';
  const lower = val.toLowerCase();
  let color = '#CCE6CC', border = '#9BD09B', text = '#0B6B0B', icon = 'fa-check';
  if (['running', 'in-progress', 'busy', 'done'].includes(lower)) { color = '#CCE6CC'; border = '#9BD09B'; text = '#0B6B0B'; icon = 'fa-check'; }
  if (['error', 'failed', 'fail', 'blocked'].includes(lower)) { color = '#FFE3E3'; border = '#F5B7B7'; text = '#8b0000'; icon = 'fa-times'; }

  const div = document.createElement('div');
  div.className = 'tag-block tag-status';
  div.innerHTML = `
    <div class="log-head" style="display:flex;align-items:center;gap:8px;justify-content: space-between;">
      <b>STATUS</b>
      <span class="status-chip" style="margin-left:8px;background:${color};border:1px solid ${border};color:${text};border-radius:999px;padding:2px 10px;display:inline-flex;gap:6px;align-items:center;">
        <i class="fa ${icon}"></i>
        <span>${escapeHtml(val)}</span>
      </span>
    </div>
  `;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  window.dispatchEvent(new Event('logs:changed'));
  return div;
}

/* ---------------------- Spinner handoff for logs ------------------------ */
let lastLiveLogEl = null;
function setLastLogLive(el) {
  if (!el) return;
  if (lastLiveLogEl && lastLiveLogEl !== el) {
    const prev = lastLiveLogEl.querySelector('.log-live');
    if (prev) prev.style.display = 'none';
  }
  const anchor = el.querySelector('.collap-head') || el.querySelector('.log-head') || el;
  let spin = anchor.querySelector('.log-live');
  if (!spin) {
    spin = document.createElement('span');
    spin.className = 'log-live';
    spin.style.marginLeft = 'auto';
    spin.style.opacity = '.7';
    spin.innerHTML = `<i class="fa fa-spinner fa-spin"></i>`;
    if (getComputedStyle(anchor).display !== 'flex') spin.style.cssText += ';float:right';
    anchor.appendChild(spin);
  }
  spin.style.display = 'inline';
  lastLiveLogEl = el;
}
function clearLiveSpinner() {
  if (!lastLiveLogEl) return;
  const s = lastLiveLogEl.querySelector('.log-live');
  if (s) s.remove();
  lastLiveLogEl = null;
}

/* ----------------- helpers to enforce in-order placement ---------------- */
function endCurrentChatStreamNow() {
  const bubbleHasContent = !!currentAssistantEl?.querySelector('.bubble')?.textContent?.trim();
  const hasTyping = (liveTimer != null) || (liveBuf && liveBuf.length) || (liveFull && liveFull.length);
  if (bubbleHasContent || hasTyping) {
    finalizeAssistantMessageLive();   // closes bubble so next content goes below
  }
}
function emitTextNow(text) {
  if (!text) return;
  if (CHAT_SIMULATE_TYPING) pushAssistantChunkLive(text);
  else appendAssistantMarkdown(text);
}

/* --------------- INLINE tag processing (strict in-order) ---------------- */
function processInlineSystemTagsInOrder(text = "") {
  let rest = String(text);

  // 1) Repeatedly find RUNNING and interleave at exact positions
  while (true) {
    const mRun = rest.match(/<\s*running\b[^>]*\/\s*>/i);
    if (!mRun) break;

    const idx = mRun.index;
    const tag = mRun[0];
    const before = rest.slice(0, idx);
    const after = rest.slice(idx + tag.length);

    // push text before the RUNNING tag to the current bubble
    if (before) emitTextNow(before);

    // close the current bubble, then insert RUN card now
    endCurrentChatStreamNow();

    const step = parseRunStepAttr(tag);
    const { id, el } = renderRunCardInLogs(step, '');
    currentRun = { id, step, desc: '', logsEl: el, chatEl: renderRunningCardInChat(step, '') };
    runMap.set(id, currentRun);
    setLastLogLive(el);

    // if a DESCRIPTION immediately follows, consume and apply it
    const mDesc = after.match(/^\s*<\s*description\s*>([\s\S]*?)<\/\s*description\s*>/i);
    if (mDesc) {
      updateRunCards(mDesc[1] || '');
      rest = after.slice(mDesc[0].length); // consume description
    } else {
      rest = after;
    }
  }

  // 2) Interleave MISSING in place (card in chat)
  while (true) {
    const mMiss = rest.match(/<\s*missing\s*>([\s\S]*?)<\/\s*missing\s*>/i);
    if (!mMiss) break;

    const idx = mMiss.index;
    const tag = mMiss[0];
    const before = rest.slice(0, idx);
    const after = rest.slice(idx + tag.length);

    if (before) emitTextNow(before);
    endCurrentChatStreamNow();
    renderMissingInChat(tag);
    rest = after;
  }

  // 3) STATUS chips -> logs only (remove from chat)
  rest = rest.replace(/<\s*status\s*:\s*([^>]+)>/ig, (_m, v) => { renderStatusBox(v); if (/^done$/i.test(String(v || '').trim())) clearLiveSpinner(); return ''; });

  // 4) NEXT -> logs only (remove from chat)
  rest = rest.replace(/<\s*next\s*:[^>]+>/ig, (full) => { renderNextCardFromRaw(full); return ''; });

  // 5) Strip OK and PRESENT from chat
  rest = rest.replace(/<\s*OK\s*\/\s*>/ig, '')
    .replace(/<\s*PRESENT\s*>[\s\S]*?<\/\s*PRESENT\s*>/ig, '');

  // 6) Clean stray empty bullets like "- "
  rest = rest.replace(/^\s*-\s*$/gm, '');

  return rest;
}

/* ------------------------- Stream event router -------------------------- */
const CHAT_BLOCK_TAGS = new Set(["SOLUTION", "FINAL", "ANSWER", "REVIEW", "SUMMARY"]);

export function renderAssistantEvent(evt) {
  if (!evt) return;

  if (evt.type === 'message') {
    let t = (evt.text || '');
    const remaining = processInlineSystemTagsInOrder(t);

    if (remaining.trim()) {
      emitTextNow(remaining);
    }
    return;
  }

  if (evt.type === 'block') {
    const raw = String(evt.text || '');
    const tag = String(evt.tag || '').toUpperCase();

    if (tag === 'STATUS' || tag.startsWith('STATUS:')) {
      const val = tag.includes(':') ? tag.split(':', 2)[1] : parseStatusValue(raw);
      renderStatusBox(val);
      if (/^done$/i.test(String(val || '').trim())) clearLiveSpinner();
      return;
    }

    if (tag === 'OK' || tag === 'PRESENT') return;

    if (tag === 'RUNNING') {
      // close current stream, then insert cards NOW
      endCurrentChatStreamNow();

      const step = parseRunStepAttr(raw);
      const { id, el } = renderRunCardInLogs(step, '');
      currentRun = { id, step, desc: '', logsEl: el, chatEl: renderRunningCardInChat(step, '') };
      runMap.set(id, currentRun);
      setLastLogLive(el);
      return;
    }

    if (tag === 'DESCRIPTION') {
      const inner = raw.replace(/^<[^>]+>/, '').replace(/<\/[^>]+>$/, '');
      updateRunCards(inner);
      return;
    }

    if (tag === 'NEXT') {
      renderNextCardFromRaw(raw);
      return;
    }

    if (tag === 'MISSING') {
      endCurrentChatStreamNow();
      renderMissingInChat(raw);
      return;
    }

    if (['EXECUTE', 'OBSERVE', 'LOGS', 'SUBSCRIBE', 'THINK'].includes(tag)) {
      const body = raw.replace(/^<[^>]+>/, '').replace(/<\/[^>]+>$/, '');
      renderCollapsibleLog(tag, body);
      return;
    }

    if (CHAT_BLOCK_TAGS.has(tag)) {
      const inner = raw.replace(/^<[^>]+>/, '').replace(/<\/[^>]+>$/, '');
      if (inner.trim()) {
        maybeStartNewChatTurn();
        emitTextNow(inner);
      }
      return;
    }

    // Fallback: process inline inside this block and render leftover text
    const rest = processInlineSystemTagsInOrder(raw.replace(/^<[^>]+>/, '').replace(/<\/[^>]+>$/, ''));
    if (rest.trim()) emitTextNow(rest);
    return;
  }

  if (evt.type === 'done') {
    if (CHAT_SIMULATE_TYPING) {
      if (liveTimer || liveBuf.length) {
        finalizePending = true;
      } else {
        finalizeAssistantMessageLive();
      }
    } else {
      finalizeAssistantMessageLive();
    }
    hideAssistantTyping();
    renderStatusBox('done');
    clearLiveSpinner();
  }
}

/* ----------------------------- Clear panes ------------------------------ */
export function clearChat() {
  const c = $('chat'); if (c) c.innerHTML = '';
  currentAssistantEl = null;
  liveBuf = ""; liveFull = "";
  if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }
  hideAssistantTyping();
}
export function clearLogs() { const l = $('logs'); if (l) l.innerHTML = ''; }
