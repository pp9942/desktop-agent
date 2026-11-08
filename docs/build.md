# 打包：从源码到一个能双击的包

## 为什么选 PyInstaller

三条路都试过权衡：

| 方案 | 优点 | 代价 |
|------|------|------|
| Electron | 生态最熟 | 多一套 node 工具链，产物 100MB 起，前端要重写成 React |
| Tauri | 产物小 | 需要 Rust 工具链，课程环境装不起来 |
| **PyInstaller + pywebview** | 和 Python 后端同一套构建，一条命令 | 产物大、需要处理资源路径 |

选第三条：这个项目的重点在循环和工具，不在前端工程化。
界面用原生 JS 写，没有构建步骤，`ui/` 直接当静态资源带走。

## 命令

```bash
pip install -r requirements-dev.txt
pyinstaller build.spec
```

产物：

```
dist/DesktopAgent/
├── DesktopAgent.exe      ← 双击这个
├── ui/                   ← 会被解包到 _MEIPASS
└── _internal/            ← Python 运行时与依赖
```

`--headless` 仍然可用，方便排查「窗口没起来但后端是好的」：

```bash
dist\DesktopAgent\DesktopAgent.exe --headless --port 8756
```

## 两个必踩的坑

**1. 资源路径**

开发时 `Path(__file__).parent.parent` 能找到 `ui/`，
打包后所有数据都被解到 `sys._MEIPASS`，源码目录根本不存在。

```python
def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent
```

**2. 可写目录**

exe 可能装在 `C:\Program Files\`，那里不可写。
所以会话、笔记、日志、**工作区**这类运行产物统一放 `Path.home() / ".desktop-agent"`，绝不往 exe 旁边写。

```python
default_ws = user_dir() / "workspace" if getattr(sys, "frozen", False) else resource_dir() / "workspace"
```

开发时工作区在项目里（`desktop-agent/workspace`），打包后自动换成
`%USERPROFILE%\.desktop-agent\workspace` —— 否则用户改的文件会写进 `_MEIPASS`，退出就没了。

```python
def user_dir() -> Path:
    path = Path.home() / ".desktop-agent"
    path.mkdir(parents=True, exist_ok=True)
    return path
```

## 打包版里的 MCP

打包后没有 `python` 解释器可用，`mcp.json` 里写 `"command": "python"` 会直接失败。

处理办法：`mcp.json` 给内置 server 加一个 `"bundled": true`，
frozen 时客户端把**同一个 exe** 用 `--run-mcp-server notes` 拉起来当 stdio server：

```python
if getattr(sys, "frozen", False) and self.spec.get("bundled"):
    args = ["--run-mcp-server", self.name]
```

`app.py` 在 `main()` 最前面拦这个参数，转去跑 `mcp_servers.notes_server.server.serve()`。
`console=False` 不影响 stdio —— 管道是显式传给子进程的，不依赖控制台窗口。

外部第三方 server（node / 别的 python 环境）仍然按原样配置，不受影响。

## 依赖 WebView2

pywebview 在 Windows 上走 Edge WebView2。Win11 自带；
Win10 如果没有，需要装一次 Evergreen Runtime。启动时如果窗口是白屏，基本就是这个问题。

## 体积与启动

onedir 产物实测 51MB（Python 运行时 + FastAPI/MCP + pywebview 的绑定）。
冷启动 3–5 秒，大头是 uvicorn 起来 + WebView2 初始化。

用 onedir 而不是 onefile：onefile 每次启动都要解压到临时目录，冷启动会翻倍，
而且杀毒软件更容易误报。

## 验收步骤（和评分表一致）

1. 在一台没装过 Python 的机器上：下载 zip → 解压 → 双击 `DesktopAgent.exe`；
2. 窗口出现，标题栏是 `Desktop Agent`，没有终端窗口；
3. 在设置里填 Key（或先放 `.env` 到 `%USERPROFILE%\.desktop-agent\`）；
4. 发一句「mean 对空列表处理有问题，改掉并跑测试」，看到 read → edit（失败）→ edit（成功）→ bash 四张卡片；
5. 点「停止」，正在跑的命令结束，循环退出，界面不再转圈。
