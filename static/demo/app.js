/* DocuBot demo app — vanilla JS, no build step.
   Talks to the same API the OpenAPI schema documents: JWT auth, document
   upload/polling, and SSE-streamed RAG answers with numbered citations. */

"use strict";

const API = "/api/v1";
const TOKEN_KEY = "docubot.tokens";

const $ = (id) => document.getElementById(id);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

/* ============ tiny helpers ============ */

function toast(message, kind = "info", ms = 3800) {
  const box = el("div", `toast ${kind}`);
  box.textContent = message;
  $("toasts").appendChild(box);
  setTimeout(() => box.remove(), ms);
}

function humanSize(bytes) {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
}

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* Minimal markdown: **bold**, `code`, line breaks — safe (escaped) by design. */
function renderAnswer(text) {
  const escaped = escapeHtml(text);
  const withCitations = escaped.replace(/\[(\d)\]/g, '<span class="citation">[$1]</span>');
  return withCitations
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

/* ============ auth state ============ */

let tokens = null;
try {
  tokens = JSON.parse(localStorage.getItem(TOKEN_KEY) || "null");
} catch {
  tokens = null;
}

function saveTokens(next) {
  tokens = next;
  localStorage.setItem(TOKEN_KEY, JSON.stringify(next));
}

function accessToken() {
  return tokens ? tokens.access : null;
}

async function refreshTokens() {
  if (!tokens) return false;
  const response = await fetch(`${API}/auth/refresh/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh: tokens.refresh }),
  });
  if (!response.ok) {
    saveTokens(null);
    return false;
  }
  saveTokens({ ...tokens, ...await response.json() });
  return true;
}

/* Authenticated fetch; transparently refreshes once on 401. */
async function api(path, options = {}, retry = true) {
  const headers = new Headers(options.headers || {});
  if (accessToken()) headers.set("Authorization", `Bearer ${accessToken()}`);
  const response = await fetch(`${API}${path}`, { ...options, headers });
  if (response.status === 401 && retry && (await refreshTokens())) {
    return api(path, options, false);
  }
  return response;
}

async function apiJson(path, options) {
  const response = await api(path, options);
  let body = null;
  try {
    body = await response.json();
  } catch {
    body = {};
  }
  if (!response.ok) {
    const detail = body.detail || JSON.stringify(body).slice(0, 200);
    throw new Error(`${response.status}: ${detail}`);
  }
  return body;
}

/* ============ app state ============ */

const state = {
  conversations: [],
  activeConversation: null,
  documents: [],
  pollTimer: null,
  streamAbort: null,
  busy: false,
};

/* ============ screens ============ */

function showAuth() {
  $("auth-screen").hidden = false;
  $("app").hidden = true;
}

function showApp(username) {
  $("auth-screen").hidden = true;
  $("app").hidden = false;
  $("user-name").textContent = username;
  loadConversations();
  loadDocuments().catch(() => {});
  checkHealth();
  setInterval(checkHealth, 30000);
}

/* ============ health dot ============ */

async function checkHealth() {
  const dot = $("health-dot");
  try {
    const response = await fetch(`${API}/health/`);
    const body = await response.json();
    dot.className = `health-dot ${body.status === "ok" ? "ok" : "error"}`;
    dot.title = `API: ${body.status} — db ${body.components.database}, cache ${body.components.cache}, celery ${body.components.celery}`;
  } catch {
    dot.className = "health-dot error";
    dot.title = "API unreachable";
  }
}

/* ============ conversations ============ */

async function loadConversations() {
  const body = await apiJson("/conversations/");
  state.conversations = body.results || body;
  renderConversations();
}

function renderConversations() {
  const list = $("conversation-list");
  list.replaceChildren();
  for (const conversation of state.conversations) {
    const item = el("button", "conversation-item");
    item.type = "button";
    item.appendChild(el("span", "title", conversation.title || "Untitled conversation"));
    item.appendChild(el("span", "count", String(conversation.message_count)));
    if (conversation.id === state.activeConversation) item.classList.add("active");
    item.addEventListener("click", () => openConversation(conversation));
    list.appendChild(item);
  }
}

async function createConversation() {
  const conversation = await apiJson("/conversations/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  await loadConversations();
  openConversation(conversation);
  $("ask-input").focus();
}

async function openConversation(conversation) {
  state.activeConversation = conversation.id;
  $("chat-title").textContent = conversation.title || "Untitled conversation";
  renderConversations();
  const messages = $("messages");
  messages.replaceChildren();
  const history = await apiJson(`/conversations/${conversation.id}/messages/`);
  for (const message of (history.results || history).slice().reverse()) {
    appendMessage(message.role, message.content, {
      sources: message.sources,
      model: message.model,
      promptTokens: message.prompt_tokens,
      completionTokens: message.completion_tokens,
      streaming: false,
    });
  }
}

/* ============ message rendering ============ */

function scrollMessages() {
  const box = $("messages");
  box.scrollTop = box.scrollHeight;
}

function appendMessage(role, content, { sources = [], model = "", promptTokens = null, completionTokens = null, streaming = false } = {}) {
  const empty = document.querySelector(".empty-state");
  if (empty) empty.remove();

  const bubble = el("div", `bubble ${role}`);
  if (role === "assistant") {
    bubble.innerHTML = renderAnswer(content);
  } else {
    bubble.textContent = content;
  }
  if (streaming) bubble.classList.add("streaming");

  const wrap = el("div");
  wrap.style.display = "contents";
  wrap.appendChild(bubble);

  if (role === "assistant" && (sources.length || model || completionTokens)) {
    const meta = el("div", "bubble-meta");
    sources.forEach((source, index) => {
      const chip = el("span", "source-chip");
      chip.title = `score ${source.score ?? "—"} — ${source.snippet ?? ""}`;
      chip.appendChild(el("span", "n", `[${index + 1}]`));
      chip.appendChild(el("span", "t", source.document_title || "document"));
      meta.appendChild(chip);
    });
    if (model || promptTokens != null || completionTokens != null) {
      const parts = [model, promptTokens != null ? `${promptTokens}p` : null, completionTokens != null ? `${completionTokens}c` : null]
        .filter(Boolean)
        .join(" · ");
      meta.appendChild(el("span", "token-note", parts));
    }
    wrap.appendChild(meta);
  }

  $("messages").appendChild(wrap);
  scrollMessages();
  return bubble;
}

/* ============ ask (SSE streaming) ============ */

async function ask(question) {
  if (state.busy || !state.activeConversation) return;
  state.busy = true;
  $("ask-input").value = "";
  appendMessage("user", question);

  const bubble = appendMessage("assistant", "", { streaming: true });
  bubble.innerHTML = '<span class="typing-dots"><i></i><i></i><i></i></span>';

  const controller = new AbortController();
  state.streamAbort = controller;
  let streamed = "";
  let sourcesShown = false;

  try {
    const response = await fetch(
      `${API}/conversations/${state.activeConversation}/messages/stream/`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken()}`,
        },
        body: JSON.stringify({ content: question }),
        signal: controller.signal,
      },
    );

    if (response.status === 401 && (await refreshTokens())) {
      state.busy = false;
      return ask(question);
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let index;
      while ((index = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        handleEvent(block);
      }
    }

    function handleEvent(block) {
      let name = "message";
      let payload = {};
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) name = line.slice(7).trim();
        else if (line.startsWith("data: ")) payload = JSON.parse(line.slice(6));
      }

      if (name === "sources" && !sourcesShown) {
        sourcesShown = true;
        const meta = el("div", "bubble-meta");
        payload.sources.forEach((source, i) => {
          const chip = el("span", "source-chip");
          chip.title = `score ${source.score} — ${source.snippet}`;
          chip.appendChild(el("span", "n", `[${i + 1}]`));
          chip.appendChild(el("span", "t", source.document_title));
          meta.appendChild(chip);
        });
        bubble.after(meta);
      } else if (name === "delta") {
        streamed += payload.text;
        bubble.innerHTML = renderAnswer(streamed);
        scrollMessages();
      } else if (name === "done") {
        bubble.classList.remove("streaming");
        if (!streamed) bubble.innerHTML = renderAnswer(payload.content || "");
        const note = el("span", "token-note");
        const parts = [payload.model, payload.prompt_tokens != null ? `${payload.prompt_tokens}p` : null, payload.completion_tokens != null ? `${payload.completion_tokens}c` : null].filter(Boolean).join(" · ");
        note.textContent = parts;
        const lastMeta = [...bubble.parentElement.children].reverse().find((n) => n.classList?.contains("bubble-meta") && !n.dataset.done);
        if (lastMeta && parts) {
          lastMeta.dataset.done = "1";
          lastMeta.appendChild(note);
        }
      } else if (name === "error") {
        bubble.classList.remove("streaming");
        bubble.innerHTML = `<span style="color:var(--red)">⚠ ${escapeHtml(payload.detail)}</span>`;
      }
    }
  } catch (error) {
    if (error.name === "AbortError") {
      bubble.classList.remove("streaming");
    } else {
      bubble.classList.remove("streaming");
      bubble.innerHTML = `<span style="color:var(--red)">⚠ ${escapeHtml(error.message)}</span>`;
    }
  } finally {
    state.busy = false;
    state.streamAbort = null;
    scrollMessages();
    loadConversations().catch(() => {});
  }
}

/* ============ documents ============ */

async function loadDocuments() {
  const body = await apiJson("/documents/?page_size=50");
  state.documents = body.results || body;
  renderDocuments();
  scheduleDocumentPolling();
}

function renderDocuments() {
  const list = $("document-list");
  list.replaceChildren();
  if (!state.documents.length) {
    list.appendChild(el("p", "", "No documents yet — upload your first PDF or TXT above."));
    return;
  }
  for (const document of state.documents) {
    const card = el("div", "document-card");
    const left = el("div");
    left.appendChild(el("div", "name", document.title));
    left.appendChild(
      el("div", "meta", `${humanSize(document.size_bytes)} · ${document.chunk_count} chunks`),
    );
    if (document.error_message) {
      left.appendChild(el("div", "meta", `⚠ ${document.error_message}`));
    }
    card.appendChild(left);

    const right = el("div", "actions");
    const badge = el("span", `badge ${document.status}`, document.status);
    right.appendChild(badge);

    if (document.status === "failed") {
      const retry = el("button", "icon-btn", "↻");
      retry.title = "Reprocess";
      retry.addEventListener("click", () => reprocessDocument(document));
      right.appendChild(retry);
    }
    const remove = el("button", "icon-btn", "🗑");
    remove.title = "Delete";
    remove.addEventListener("click", () => deleteDocument(document));
    right.appendChild(remove);
    card.appendChild(right);
    list.appendChild(card);
  }
}

function scheduleDocumentPolling() {
  const active = state.documents.some((d) => ["pending", "processing"].includes(d.status));
  if (state.pollTimer) clearInterval(state.pollTimer);
  if (active) state.pollTimer = setInterval(() => loadDocuments().catch(() => {}), 2500);
}

async function uploadFiles(files) {
  const progress = $("upload-progress");
  for (const file of files) {
    progress.hidden = false;
    progress.className = "upload-progress";
    progress.textContent = `Uploading ${file.name}…`;
    const form = new FormData();
    form.append("file", file);
    try {
      await apiJson("/documents/", { method: "POST", body: form });
      progress.className = "upload-progress done";
      progress.textContent = `✓ ${file.name} uploaded — processing in the background`;
      await loadDocuments();
    } catch (error) {
      progress.className = "upload-progress error";
      progress.textContent = `✗ ${file.name}: ${error.message}`;
    }
  }
  setTimeout(() => (progress.hidden = true), 3000);
}

async function deleteDocument(document) {
  if (!confirm(`Delete "${document.title}"?`)) return;
  await apiJson(`/documents/${document.id}/`, { method: "DELETE" });
  await loadDocuments();
  toast("Document deleted", "success");
}

async function reprocessDocument(document) {
  try {
    await apiJson(`/documents/${document.id}/reprocess/`, { method: "POST" });
    toast("Reprocessing queued", "success");
    await loadDocuments();
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ============ auth flows ============ */

async function handleAuth(endpoint, payload, form) {
  const errorBox = $("auth-error");
  errorBox.hidden = true;
  try {
    const response = await fetch(`${API}/auth/${endpoint}/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    if (!response.ok) {
      const first = body.detail || Object.values(body).flat().join(" ") || `HTTP ${response.status}`;
      throw new Error(first);
    }
    if (endpoint === "register") {
      // auto-login right after registration
      return handleAuth("login", { username: payload.username, password: payload.password }, form);
    }
    saveTokens(body);
    const me = await apiJson("/auth/me/");
    showApp(me.username);
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  }
}

function logout() {
  if (state.streamAbort) state.streamAbort.abort();
  saveTokens(null);
  showAuth();
}

/* ============ wiring ============ */

document.addEventListener("DOMContentLoaded", () => {
  // tabs
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      $("login-form").hidden = tab.dataset.tab !== "login";
      $("register-form").hidden = tab.dataset.tab !== "register";
    });
  });

  $("login-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    handleAuth("login", Object.fromEntries(form), event.target);
  });
  $("register-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    handleAuth("register", Object.fromEntries(form), event.target);
  });
  $("logout-btn").addEventListener("click", logout);

  // chat
  $("new-conversation").addEventListener("click", createConversation);
  $("ask-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const question = $("ask-input").value.trim();
    if (question) ask(question);
  });

  // library drawer
  $("toggle-library").addEventListener("click", async () => {
    $("library-overlay").hidden = false;
    await loadDocuments().catch(() => {});
  });
  $("close-library").addEventListener("click", () => ($("library-overlay").hidden = true));
  $("library-overlay").addEventListener("click", (event) => {
    if (event.target === $("library-overlay")) $("library-overlay").hidden = true;
  });

  // uploads: click + drag & drop
  $("file-input").addEventListener("change", (event) => {
    uploadFiles([...event.target.files]);
    event.target.value = "";
  });
  const dropzone = document.querySelector(".dropzone");
  ;["dragover", "dragenter"].forEach((name) =>
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.add("dragover");
    }),
  );
  ;["dragleave", "drop"].forEach((name) =>
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.remove("dragover");
      if (name === "drop" && event.dataTransfer?.files?.length) {
        uploadFiles([...event.dataTransfer.files]);
      }
    }),
  );

  // restore session
  if (tokens) {
    apiJson("/auth/me/")
      .then((me) => showApp(me.username))
      .catch(() => {
        saveTokens(null);
        showAuth();
      });
  } else {
    showAuth();
  }
});
