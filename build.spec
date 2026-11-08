# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

pyinstaller build.spec
产物：dist/DesktopAgent/DesktopAgent.exe（onedir，双击即用）
"""

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    # ui/ 和 mcp_servers/ 必须以数据形式带出去，打包后从 sys._MEIPASS 读
    datas=[
        ("ui", "ui"),
        ("mcp_servers", "mcp_servers"),
        ("mcp.json", "."),
        (".env.example", "."),
    ],
    hiddenimports=[
        # uvicorn 的协议实现是按字符串动态导入的，静态分析看不到
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.lifespan.on",
        "websockets.legacy",
        "websockets.legacy.server",
        # MCP
        "mcp.server.mcpserver",
        "mcp.client.stdio",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DesktopAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # 不弹黑框：双击就是窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DesktopAgent",
)
