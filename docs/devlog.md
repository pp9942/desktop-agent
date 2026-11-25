# 开发日志（如实记录）

> 本仓库的 git 于 11 月 8 日交付打包阶段初始化，此前的工作以本地开发为主、
> 集中提交；自 11 月 17 日起转为按增量单元持续推进，每个 commit 均对应
> 当日实际完成的工作。本日志与 `git log --stat` 一一对应，可逐条核对。

## 第一阶段：从零到可交付（11 月 3 日 – 11 月 8 日）

11 月 3 日 – 11 月 7 日（本地开发，代码于 11 月 8 日集中入库）：

- 确定架构：pywebview 桌面窗口 + FastAPI 后端 + WebSocket 事件流，
  前端永远不碰 API Key。
- 实现四个工具 `read / write / edit / bash`：行号分页、图片多模态、
  原子写、edit 唯一性校验、超时杀进程树、输出截断。
- 实现智能体循环：步数 / 单步超时 / token 预算三道护栏、可中断、
  会话持久化。
- 接入 stdio MCP 客户端，自建笔记 server 验证外部工具调用。
- 三栏界面：工具卡片、失败标红、diff 标签页、设置弹窗。

11 月 8 日（交付日，16 个 commit）：

- 真实模型联调暴露会话恢复 bug：历史中 tool_calls 断裂导致 DeepSeek 400，
  写了 `_sanitize_history` 修复并补 3 条测试。
- PyInstaller 打包（onedir，51 MB），真实模式端到端验证通过。
- 初始化 git 仓库，按模块拆成 16 个真实提交；打 tag v1.0.0，
  Release 附安装包（API 直传绕过网页 25 MB 限制）。
- 补 README 常见问题、现场演示检查清单、分工表。
- 交付 v1.0.0。

## 第二阶段：增量 A 技能系统（11 月 17 日，tag v1.1）

- `agent/skills.py`：技能扫描、frontmatter 解析、目录注入、触发试验。
- 三个技能（纯提示词 / 带脚本 / 走 MCP 各一）；`read` 工具加只读白名单
  让技能正文可按需读取，不新增任何工具。
- `ENABLE_SKILLS=0` 零注入退回 v1.0；`app inspect` 打印 skill_report.json。
- 验收数字：disclosure_ratio 5.91（≥5 达标）、6 条触发试验零误触发。
- 过程中修掉两个真 bug：frontmatter 用错分隔符导致元数据静默失效；
  Windows 下子进程 GBK 解码吞掉 JSON 转义序列。
- 顺带发现并修复打包配置遗漏 `skills/` 目录。

## 第三阶段：微信小程序端（11 月 17 日 – 11 月 25 日）

- 小程序只连 `/ws` 消费事件流，后端一行没改循环；
  `/ws` 加可选 token 鉴权，`--host` 支持真机预览。
- 用 V8 编译验证 JS 语法；离线端到端模拟小程序完整消息流，
  逐条核对 hello/history/delta/tool 卡片/done 事件全部真实出现。
- 11 月 25 日：`AGENT_DATA_DIR` 支持数据目录整体迁移（课堂演示隔离）；
  并入课堂演示文档。

## 当前：增量 B 定制层（11 月 25 日 开始）

按合理节奏逐日推进，每日一个可验收的增量（详见后续 commit）：

- 11 月 25 日：AGENTS.md 双层注入（全局 + 项目，带 500 token 预算）。
- 后续：三条 hook、只读 reviewer 子 agent、custom_report.json 验收。
