// 桌面智能体的微信小程序端。后端就是本机那个 FastAPI，协议与桌面 UI 完全一致：
// 发 {"type":"chat","text":...}，收 hello/history/assistant_delta/tool_start/tool_end/done。
// Key 永远在 Python 后端，小程序只拿事件流 —— 和桌面版同一条安全边界。

const DEFAULT_SERVER = "ws://127.0.0.1:8756/ws";

function eventBrief(tool) {
  if (tool.ok === true) return "成功";
  if (tool.ok === false) return "失败";
  return "运行中";
}

App({
  onLaunch() {
    // 无全局逻辑，连接管理在页面层
  },
  briefTool: eventBrief,
});
