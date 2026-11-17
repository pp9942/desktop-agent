// 页面逻辑：连接 /ws，按事件类型更新消息流和工具卡片。
// 设计对应桌面版三栏里的中间栏：助手流式文本 + 工具卡片 + 输入框。

const DEFAULT_URL = "ws://127.0.0.1:8756/ws";

Page({
  data: {
    url: DEFAULT_URL,
    connected: false,
    statusText: "未连接",
    model: "",
    mcpOk: false,
    msgs: [], // {id, role: 'user'|'assistant', text}
    tools: [], // {id, name, ok: true/false/null, duration, summary, args}
    input: "",
    busy: false,
    scrollInto: "",
  },

  onLoad() {
    const saved = wx.getStorageSync("server_url");
    if (saved) this.setData({ url: saved });
    this.connect();
  },

  onUnload() {
    if (this.task) {
      try {
        this.task.close({});
      } catch (e) {
        /* 已断开 */
      }
    }
  },

  onUrlInput(e) {
    this.setData({ url: e.detail.value });
  },

  connect() {
    const url = (this.data.url || DEFAULT_URL).trim();
    wx.setStorageSync("server_url", url);
    if (this.task) {
      try {
        this.task.close({});
      } catch (e) {
        /* 旧连接可能已断 */
      }
    }
    this.setData({ statusText: "连接中…", connected: false });
    this.task = wx.connectSocket({ url, protocols: [] });
    this.task.onOpen(() => this.setData({ connected: true, statusText: "已连接" }));
    this.task.onClose(() => this.setData({ connected: false, statusText: "已断开", busy: false }));
    this.task.onError(() =>
      this.setData({ connected: false, statusText: "连接失败（后端没开？）", busy: false })
    );
    this.task.onMessage((res) => this.onEvent(res.data));
  },

  onEvent(raw) {
    let ev;
    try {
      ev = JSON.parse(raw);
    } catch (e) {
      return;
    }
    const d = this.data;
    switch (ev.type) {
      case "hello":
        this.setData({
          model: (ev.config && ev.config.model) || "",
          mcpOk: Array.isArray(ev.mcp) ? ev.mcp.every((m) => m.connected) : false,
        });
        break;

      case "history":
        this.setData({
          msgs: (ev.messages || []).map((m, i) => ({
            id: "h" + i,
            role: m.role,
            text: m.content || "",
          })),
        });
        this.scrollToBottom();
        break;

      case "assistant_delta":
        this.appendAssistant(ev.text || "");
        break;

      case "tool_start":
        this.setData({
          tools: d.tools.concat([
            {
              id: ev.id || "t" + d.tools.length,
              name: ev.name || "tool",
              ok: null,
              duration: 0,
              summary: "",
              args: typeof ev.args === "object" ? JSON.stringify(ev.args) : String(ev.args || ""),
            },
          ]),
        });
        this.scrollToBottom();
        break;

      case "tool_end": {
        const tools = d.tools.map((t) =>
          t.id === (ev.id || t.id)
            ? Object.assign({}, t, {
                ok: !!ev.ok,
                duration: ev.duration || 0,
                summary: ev.summary || "",
              })
            : t
        );
        this.setData({ tools });
        break;
      }

      case "done":
        this.setData({ busy: false });
        this.scrollToBottom();
        break;

      case "notice":
        wx.showToast({ title: ev.message || "提示", icon: "none" });
        break;

      case "reset":
        this.setData({ msgs: [], tools: [], busy: false });
        break;

      case "pong":
      default:
        break;
    }
  },

  appendAssistant(delta) {
    const d = this.data;
    const msgs = d.msgs;
    const last = msgs[msgs.length - 1];
    if (last && last.role === "assistant" && !last.closed) {
      this.setData({
        msgs: msgs.slice(0, -1).concat([Object.assign({}, last, { text: last.text + delta })]),
      });
    } else {
      this.setData({
        msgs: msgs.concat([{ id: "a" + Date.now(), role: "assistant", text: delta }]),
      });
    }
    this.scrollToBottom();
  },

  scrollToBottom() {
    const d = this.data;
    const anchor = d.msgs.length ? d.msgs[d.msgs.length - 1].id : "chat-bottom";
    this.setData({ scrollInto: anchor });
  },

  onInput(e) {
    this.setData({ input: e.detail.value });
  },

  send() {
    const text = (this.data.input || "").trim();
    if (!text) return;
    if (!this.data.connected) {
      wx.showToast({ title: "还没连上后端", icon: "none" });
      return;
    }
    if (this.data.busy) {
      wx.showToast({ title: "上一轮还在跑", icon: "none" });
      return;
    }
    const d = this.data;
    this.setData({
      msgs: d.msgs.concat([{ id: "u" + Date.now(), role: "user", text }]),
      input: "",
      busy: true,
      tools: [],
    });
    this.task.send({ data: JSON.stringify({ type: "chat", text }) });
    this.scrollToBottom();
  },

  stop() {
    if (this.task && this.data.busy) {
      this.task.send({ data: JSON.stringify({ type: "stop" }) });
    }
  },

  reset() {
    if (this.task) {
      this.task.send({ data: JSON.stringify({ type: "reset" }) });
    }
  },
});
