/* Desktop Agent 前端：只负责显示，不直接碰模型。 */

const $ = (sel) => document.querySelector(sel);

const state = {
  ws: null,
  running: false,
  currentFile: "",
  tab: "code",
  streamEl: null,
  lastDiff: null,
  treeOpen: new Set(["."]),
  files: new Map(),
};

const els = {
  tree: $("#tree"),
  editor: $("#editor"),
  messages: $("#messages"),
  input: $("#input"),
  send: $("#btn-send"),
  stop: $("#btn-stop"),
  reset: $("#btn-reset"),
  refresh: $("#btn-refresh"),
  model: $("#model-badge"),
  mcp: $("#mcp-badge"),
  conn: $("#conn-badge"),
  connText: $("#conn-text"),
  step: $("#step-indicator"),
  wsPath: $("#workspace-path"),
  hint: $("#tab-hint"),
  toast: $("#toast"),
};

/* ------------------------------ 工具函数 ------------------------------ */
function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function toast(text) {
  els.toast.textContent = text;
  els.toast.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => els.toast.classList.remove("show"), 2200);
}

function fmtSize(bytes) {
  if (!bytes) return "";
  if (bytes < 1024) return bytes + " B";
  return Math.round(bytes / 1024) + " K";
}

function argSummary(name, args) {
  if (!args) return "";
  if (name === "bash") return args.command || "";
  if (name === "read") return `${args.path || ""}${args.offset ? ` @${args.offset}` : ""}`;
  if (name === "write") return `${args.path || ""} · ${(args.content || "").length} 字符`;
  if (name === "edit") return args.path || "";
  const first = Object.values(args)[0];
  return typeof first === "string" ? first.slice(0, 60) : "";
}

function nearBottom() {
  const box = els.messages;
  return box.scrollHeight - box.scrollTop - box.clientHeight < 120;
}

function scrollDown(force = false) {
  if (force || nearBottom()) {
    els.messages.scrollTop = els.messages.scrollHeight;
  }
}

/* ------------------------------ 连接 ------------------------------ */
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  state.ws = ws;

  ws.onopen = () => {
    els.conn.className = "pill ok";
    els.connText.textContent = "已连接";
  };

  ws.onclose = () => {
    els.conn.className = "pill bad";
    els.connText.textContent = "断开";
    setRunning(false);
    setTimeout(connect, 1200);
  };

  ws.onmessage = (event) => {
    let data = null;
    try {
      data = JSON.parse(event.data);
    } catch {
      return;
    }
    handle(data);
  };
}

function send(payload) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify(payload));
  }
}

/* ------------------------------ 事件 ------------------------------ */
function handle(ev) {
  switch (ev.type) {
    case "hello":
    case "state": {
      const cfg = ev.config || {};
      els.model.textContent = cfg.mock ? "mock 离线演示" : cfg.model || "—";
      els.wsPath.textContent = cfg.workspace || "";
      els.wsPath.title = cfg.workspace || "";
      if (cfg.mock) els.model.classList.add("ok");
      renderMcp(ev.mcp);
      break;
    }
    case "history":
      replay(ev.messages || []);
      break;
    case "step":
      els.step.textContent = `第 ${ev.n} / ${ev.max} 步`;
      break;
    case "assistant_delta":
      appendDelta(ev.text);
      break;
    case "tool_start":
      startTool(ev);
      break;
    case "tool_end":
      endTool(ev);
      break;
    case "file_changed":
      onFileChanged(ev);
      break;
    case "notice":
      addNotice(ev.message);
      break;
    case "error":
      addError(ev.message);
      setRunning(false);
      break;
    case "done":
      finish(ev);
      break;
    case "reset":
      els.messages.innerHTML = "";
      state.streamEl = null;
      els.editor.innerHTML = '<div class="placeholder">从左边选一个文件</div>';
      state.currentFile = "";
      els.hint.textContent = "";
      break;
    default:
      break;
  }
}

function renderMcp(list) {
  if (!list || !list.length) {
    els.mcp.textContent = "MCP 未配置";
    els.mcp.className = "pill";
    els.mcp.title = "mcp.json 里没有启用的 server";
    return;
  }
  const connected = list.filter((s) => s.connected);
  const totalTools = connected.reduce((n, s) => n + (s.tools || []).length, 0);
  els.mcp.textContent = connected.length ? `MCP ${connected.length} · ${totalTools} 工具` : "MCP 断开";
  els.mcp.className = "pill " + (connected.length ? "ok" : "bad");
  els.mcp.title = list
    .map((s) => `${s.name}: ${s.connected ? (s.tools || []).join(", ") : s.error || "未连接"}`)
    .join("\n");
}

/* ------------------------------ 对话渲染 ------------------------------ */
function addMessage(role, text) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  wrap.innerHTML = `<div class="who">${role === "user" ? "你" : "智能体"}</div>`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text || "";
  wrap.appendChild(bubble);
  els.messages.appendChild(wrap);
  scrollDown(true);
  return bubble;
}

function replay(messages) {
  const hint = els.messages.querySelector(".hint-card");
  if (messages.length && hint) hint.remove();
  for (const msg of messages) {
    if (!msg.content) continue;
    addMessage(msg.role === "user" ? "user" : "assistant", msg.content);
  }
  state.streamEl = null;
}

function appendDelta(text) {
  if (!text) return;
  if (!state.streamEl) {
    const wrap = document.createElement("div");
    wrap.className = "msg assistant";
    wrap.innerHTML = '<div class="who">智能体</div>';
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    wrap.appendChild(bubble);
    els.messages.appendChild(wrap);
    state.streamEl = bubble;
    const caret = document.createElement("span");
    caret.className = "caret";
    bubble.after(caret);
    bubble._caret = caret;
  }
  const caret = state.streamEl._caret;
  state.streamEl.textContent += text;
  if (caret) state.streamEl.after(caret);
  scrollDown();
}

function endStream() {
  if (state.streamEl && state.streamEl._caret) {
    state.streamEl._caret.remove();
    state.streamEl._caret = null;
  }
  state.streamEl = null;
}

function addNotice(text) {
  endStream();
  const el = document.createElement("div");
  el.className = "notice";
  el.textContent = text;
  els.messages.appendChild(el);
  scrollDown(true);
}

function addError(text) {
  endStream();
  const el = document.createElement("div");
  el.className = "error-line";
  el.textContent = text;
  els.messages.appendChild(el);
  scrollDown(true);
}

/* ------------------------------ 工具卡片 ------------------------------ */
function startTool(ev) {
  endStream();
  const hint = els.messages.querySelector(".hint-card");
  if (hint) hint.remove();

  const card = document.createElement("div");
  card.className = "tool running";
  card.dataset.id = ev.id;
  card.innerHTML = `
    <div class="tool-head">
      <span class="spinner"></span>
      <span class="tool-name">${esc(ev.name)}</span>
      <span class="tool-arg">${esc(argSummary(ev.name, ev.args))}</span>
      <span class="tool-time">…</span>
    </div>
    <div class="tool-sum"></div>
    <div class="tool-body"></div>`;
  card.querySelector(".tool-head").addEventListener("click", () => card.classList.toggle("open"));
  els.messages.appendChild(card);
  scrollDown(true);
}

function endTool(ev) {
  const card = els.messages.querySelector(`.tool[data-id="${CSS.escape(ev.id)}"]`);
  if (!card) return;
  card.classList.remove("running");
  card.classList.add(ev.ok ? "ok" : "fail");
  if (!ev.ok) card.classList.add("open");

  const head = card.querySelector(".tool-head");
  const spinner = head.querySelector(".spinner");
  if (spinner) {
    const dot = document.createElement("span");
    dot.className = ev.source === "mcp" ? "dot-mcp" : ev.ok ? "dot-ok" : "dot-fail";
    spinner.replaceWith(dot);
  }
  card.querySelector(".tool-time").textContent = ev.ok
    ? `${ev.duration}s`
    : `${ev.duration}s · 失败`;

  const sum = card.querySelector(".tool-sum");
  if (sum) {
    sum.textContent = ev.summary || "";
    if (ev.ok && !ev.summary) sum.style.display = "none";
  }

  const body = card.querySelector(".tool-body");
  if (providesDiff(ev)) {
    body.innerHTML = renderDiff(ev.diff);
  } else {
    body.textContent = ev.output || "";
  }
  scrollDown();
}

function providesDiff(ev) {
  return Boolean(ev.diff && ev.diff.includes("@@"));
}

/* ------------------------------ 文件与 diff ------------------------------ */
function onFileChanged(ev) {
  state.lastDiff = { path: ev.path, diff: ev.diff };
  els.hint.textContent = `${ev.path} 已修改`;
  if (state.currentFile === ev.path) loadFile(ev.path, { quiet: true });
  if (state.tab === "diff") renderDiffTab();
}

function renderDiff(text) {
  const lines = String(text || "").split("\n");
  return lines
    .map((line) => {
      let cls = "";
      if (line.startsWith("@@")) cls = "diff-meta";
      else if (line.startsWith("+++") || line.startsWith("---")) cls = "diff-meta";
      else if (line.startsWith("+")) cls = "diff-add";
      else if (line.startsWith("-")) cls = "diff-del";
      return `<span class="${cls}">${esc(line) || " "}</span>`;
    })
    .join("\n");
}

function renderDiffTab() {
  if (!state.lastDiff) {
    els.editor.innerHTML = '<div class="placeholder">还没有改动。让智能体改一个文件试试。</div>';
    return;
  }
  els.editor.innerHTML = `
    <div class="code-head"><span class="path">${esc(state.lastDiff.path)}</span><span class="badge">diff</span></div>
    <div class="line"><span class="tx">${renderDiff(state.lastDiff.diff)}</span></div>`;
}

function renderCode(path, text) {
  const lines = text.split("\n");
  const width = String(lines.length).length;
  const body = lines
    .map((line, i) => {
      const n = String(i + 1).padStart(width, " ");
      return `<div class="line"><span class="ln">${n}</span><span class="tx">${esc(line) || " "}</span></div>`;
    })
    .join("");
  els.editor.innerHTML = `
    <div class="code-head">
      <span class="path">${esc(path)}</span>
      <span class="badge">${lines.length} 行</span>
    </div>${body}`;
}

async function loadFile(path, opts = {}) {
  try {
    const res = await fetch(`/api/file?path=${encodeURIComponent(path)}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      toast(err.detail || "打不开这个文件");
      return;
    }
    const data = await res.json();
    state.currentFile = data.path;
    state.files.set(data.path, data.text);
    markActive();
    if (state.tab !== "code") switchTab("code");
    renderCode(data.path, data.text);
    if (!opts.quiet) els.hint.textContent = "";
  } catch (e) {
    toast("读取失败：" + e.message);
  }
}

function markActive() {
  els.tree.querySelectorAll(".node").forEach((node) => {
    node.classList.toggle("active", node.dataset.path === state.currentFile);
  });
}

/* ------------------------------ 文件树 ------------------------------ */
async function loadTree(path = ".", container = els.tree, depth = 0) {
  if (depth === 0) container.innerHTML = "";
  let data;
  try {
    const res = await fetch(`/api/tree?path=${encodeURIComponent(path)}`);
    data = await res.json();
  } catch {
    return;
  }

  for (const item of data.items || []) {
    const node = document.createElement("div");
    node.className = "node" + (item.dir ? " dir" : "");
    node.dataset.path = item.path;
    node.innerHTML = `
      <span class="glyph">${item.dir ? "▸" : "·"}</span>
      <span class="name">${esc(item.name)}</span>
      ${item.dir ? "" : `<span class="size">${fmtSize(item.size)}</span>`}`;
    container.appendChild(node);

    if (item.dir) {
      const children = document.createElement("div");
      children.className = "children";
      children.style.display = state.treeOpen.has(item.path) ? "block" : "none";
      node.classList.toggle("open", state.treeOpen.has(item.path));
      node.querySelector(".glyph").textContent = state.treeOpen.has(item.path) ? "▾" : "▸";

      node.addEventListener("click", async (e) => {
        e.stopPropagation();
        const open = children.style.display === "block";
        if (!open) {
          children.style.display = "block";
          node.querySelector(".glyph").textContent = "▾";
          state.treeOpen.add(item.path);
          if (!children.dataset.loaded) {
            await loadTree(item.path, children, depth + 1);
            children.dataset.loaded = "1";
          }
        } else {
          children.style.display = "none";
          node.querySelector(".glyph").textContent = "▸";
          state.treeOpen.delete(item.path);
        }
      });
      container.appendChild(children);
      if (state.treeOpen.has(item.path)) {
        await loadTree(item.path, children, depth + 1);
        children.dataset.loaded = "1";
      }
    } else {
      node.addEventListener("click", () => loadFile(item.path));
    }
  }
}

/* ------------------------------ 标签页 ------------------------------ */
function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  if (tab === "diff") renderDiffTab();
  else if (state.currentFile && state.files.has(state.currentFile)) {
    renderCode(state.currentFile, state.files.get(state.currentFile));
  } else {
    els.editor.innerHTML = '<div class="placeholder">从左边选一个文件</div>';
  }
}

/* ------------------------------ 运行状态 ------------------------------ */
function setRunning(running) {
  state.running = running;
  els.stop.disabled = !running;
  els.stop.textContent = running ? "停止" : "停止";
  els.send.disabled = running;
  els.input.disabled = running;
  if (!running) els.step.textContent = "";
}

function finish(ev) {
  endStream();
  setRunning(false);
  if (ev.reason === "max_steps") {
    addNotice(`到达步数上限（${ev.steps} 步）`);
  }
  scrollDown(true);
  send({ type: "ping" });
}

/* ------------------------------ 交互 ------------------------------ */
els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("#composer").requestSubmit();
  }
});

els.input.addEventListener("input", () => {
  els.input.style.height = "auto";
  els.input.style.height = Math.min(140, els.input.scrollHeight) + "px";
});

function sendMessage(text) {
  const hint = els.messages.querySelector(".hint-card");
  if (hint) hint.remove();
  addMessage("user", text);
  setRunning(true);
  send({ type: "chat", text });
}

$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = els.input.value.trim();
  if (!text || state.running) return;
  els.input.value = "";
  els.input.style.height = "auto";
  sendMessage(text);
});

els.stop.addEventListener("click", () => {
  send({ type: "stop" });
  els.stop.disabled = true;
  els.stop.textContent = "停止中…";
  addNotice("正在中断…");
});

els.reset.addEventListener("click", () => {
  send({ type: "reset" });
});

els.refresh.addEventListener("click", () => loadTree("."));

/* ---------------- 设置 ---------------- */
const settingsModal = $("#settings");

async function openSettings() {
  try {
    const data = await (await fetch("/api/settings")).json();
    $("#set-base").value = data.base_url || "";
    $("#set-model").value = data.model || "";
    $("#set-key").value = "";
    $("#set-key").placeholder = data.has_key ? "已配置，留空表示不修改" : "sk-…（没有 Key 就用 --mock 先看效果）";
    settingsModal.hidden = false;
    $("#set-base").focus();
  } catch {
    toast("读不到当前配置");
  }
}

function closeSettings() {
  settingsModal.hidden = true;
}

async function saveSettings() {
  const payload = {
    api_key: $("#set-key").value.trim(),
    base_url: $("#set-base").value.trim(),
    model: $("#set-model").value.trim(),
  };
  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      toast(data.detail || "保存失败");
      return;
    }
    els.model.textContent = data.config.model;
    els.model.classList.remove("ok");
    closeSettings();
    toast("已保存，下一轮对话生效");
  } catch (e) {
    toast("保存失败：" + e.message);
  }
}

$("#btn-settings").addEventListener("click", openSettings);
$("#settings-close").addEventListener("click", closeSettings);
$("#settings-cancel").addEventListener("click", closeSettings);
$("#settings-save").addEventListener("click", saveSettings);
settingsModal.addEventListener("click", (e) => {
  if (e.target === settingsModal) closeSettings();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !settingsModal.hidden) closeSettings();
});

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

/* ------------------------------ 启动 ------------------------------ */
connect();
loadTree(".");

/* 截图 / 现场演示用：?demo=1 自动跑一遍完整流程 */
const demo = new URLSearchParams(location.search).get("demo");
if (demo) {
  const kick = (tries = 40) => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      setTimeout(() => sendMessage("mean 对空列表处理有问题，改掉并跑测试"), 300);
      return;
    }
    if (tries > 0) setTimeout(() => kick(tries - 1), 150);
  };
  kick();
}
