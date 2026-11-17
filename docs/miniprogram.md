# 微信小程序端

小程序就是另一个前端：本机 FastAPI 后端、agent 循环、四个工具、MCP、技能系统全部复用，
小程序只连 `/ws` 消费事件流。**API Key 永远只在 Python 后端**，小程序拿不到。

```
miniprogram/
├── project.config.json   # appid 用 touristappid（游客模式），urlCheck=false 免配合法域名
├── app.json / app.js / app.wxss
└── pages/index/          # 对话流 + 工具卡片 + 输入框，对应桌面版中间栏
```

## 跑起来（开发者工具里，5 分钟）

1. 下载安装「微信开发者工具」（stable 版）
2. 打开工具 → 导入项目 → 目录选 `desktop-agent/miniprogram` → AppID 选「测试号/游客模式」
3. 后端先跑起来：
   ```
   cd desktop-agent
   python app.py --headless --port 8756 --mock      # 离线演示
   python app.py --headless --port 8756             # 真实模型（Key 在 ~/.desktop-agent/config.json）
   ```
4. 小程序顶部的地址默认 `ws://127.0.0.1:8756/ws`，直接发任务即可；
   工具卡片、失败标红、流式输出、停止/清空，行为与桌面版一致

`project.config.json` 里 `urlCheck: false` 已写好，不用手动勾"不校验合法域名"。

## 真机预览（手机上跑）

手机连不了 127.0.0.1，要做三件事：

1. 后端改听局域网：`python app.py --headless --port 8756 --host 0.0.0.0`
2. 查本机内网 IP（`ipconfig` 里的 IPv4，如 192.168.1.5）
3. 小程序顶部地址改成 `ws://192.168.1.5:8756/ws`，手机与电脑连同一个 WiFi，
   开发者工具里点「预览」扫码

## 公网部署（如果要发版给真实用户）

- 域名必须 HTTPS 且已备案，WebSocket 用 `wss://你的域名/ws`（Nginx 反代到本机端口）
- **必须设 token**：后端 `AGENT_WS_TOKEN=一串随机字符 python app.py --headless ...`，
  小程序地址写成 `wss://你的域名/ws?token=一串随机字符` —— 否则任何人连上就能烧你的模型额度
- 发布审核需要注册正式小程序账号（个人可注册），备案周期 1–2 周

## 协议（与桌面 UI 完全一致）

客户端发：`{"type":"chat","text":...}`、`{"type":"stop"}`、`{"type":"reset"}`
服务端发：`hello`（配置+MCP 状态）、`history`、`assistant_delta`、`step`、
`tool_start`、`tool_end`、`notice`、`done`、`reset`

后端回归：`python -m pytest tests`（24 项）。事件流完整性已在离线端到端中逐条核对。
