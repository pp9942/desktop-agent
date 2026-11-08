# 一次完整运行记录

**环境**：Windows 11 · Python 3.14.3 · 工作区是 `seed` 生成的 `stats.py` / `test_stats.py`
**模型**：`mock-demo`（脚本化假模型，离线，不需要 Key）
**命令**：`python scripts/smoke.py`

> 说明：模型是脚本化的，所以「说什么」是写死的；但**下面每一次工具执行都是真的** ——
> 文件真的被读、真的被改、`pytest` 真的跑起来、diff 真的算出来。
> 换成真模型时，事件类型和工具行为完全一致，变的只是 assistant 的措辞。

任务：`mean 对空列表处理有问题，改掉并跑测试`

## 事件流

```
[step] {'n': 1, 'max': 20}

先读一下 stats.py，看看空列表是怎么处理的。
[tool_start] {'id': 'call_read_40207', 'name': 'read', 'args': {'path': 'stats.py'}}

[tool_end] {'id': 'call_read_40207', 'name': 'read', 'ok': True, 'duration': 0.0,
  'summary': 'stats.py · 共 29 行 · 显示 1-29',
  'output': 'stats.py · 共 29 行 · 显示 1-29\n 1| """三个统计函数，测试在 test_stats.py。"""\n 4| def mean(nums):\n 5|     if not nums:\n 6|         return 0\n 7|     total = 0\n ...'}

[step] {'n': 2, 'max': 20}
空列表时返回 0 在统计上没有意义，改成 None。
[tool_start] {'id': 'call_edit_90810', 'name': 'edit',
  'args': {'path': 'stats.py', 'old': '    if not nums:\n        return 0',
           'new': '    if not nums:\n        return None'}}

[tool_end] {'id': 'call_edit_90810', 'name': 'edit', 'ok': False, 'duration': 0.0,
  'summary': '这段原文在 stats.py 里出现了 3 次，无法确定改哪一处。',
  'output': '这段原文在 stats.py 里出现了 3 次，无法确定改哪一处。\n命中的行：[5, 14, 24]。请把 old 补上前后几行上下文，让它在文件里唯一。',
  'diff': '', 'source': 'local'}

[step] {'n': 3, 'max': 20}
匹配不唯一 —— 补上函数签名再改一次。
[tool_start] {'id': 'call_edit_70958', 'name': 'edit',
  'args': {'path': 'stats.py', 'old': 'def mean(nums):\n    if not nums:\n        return 0',
           'new': 'def mean(nums):\n    if not nums:\n        return None'}}

[tool_end] {'id': 'call_edit_70958', 'name': 'edit', 'ok': True, 'duration': 0.0,
  'summary': '已修改 stats.py（+1 -1 行，唯一匹配）',
  'diff': '--- a/stats.py\n+++ b/stats.py\n@@ -3,7 +3,7 @@\n \n def mean(nums):\n     if not nums:\n-        return 0\n+        return None\n     total = 0\n     for n in nums:\n         total += n\n'}

[file_changed] {'path': 'stats.py', 'diff': '...同上...'}

[step] {'n': 4, 'max': 20}
跑一遍测试。
[tool_start] {'id': 'call_bash_33101', 'name': 'bash',
  'args': {'command': 'python -m unittest -q', 'timeout': 120}}

[tool_end] {'id': 'call_bash_33101', 'name': 'bash', 'ok': True, 'duration': 0.18,
  'summary': 'OK',
  'output': '$ python -m unittest -q\n[stderr]\n----\nRan 5 tests in 0.000s\n\nOK',
  'meta': {'command': 'python -m unittest -q', 'exit_code': 0, 'duration': 0.18,
           'failed': False, 'timed_out': False}}

[step] {'n': 5, 'max': 20}
改好了，只动了 mean 一处：空列表现在返回 None，其余逻辑没碰。测试全绿。
[done] {'reason': 'finished', 'steps': 5}
```

## 这一段里真正值得看的三个点

**1. 失败是工具给的，不是模型编的**

第 2 步的 `edit` 被工具拒绝了：`old` 在文件里出现 3 次（`mean` / `median` / `mode` 各一处），
工具不猜，直接返回命中行号 `[5, 14, 24]`。模型据此补了 `def mean(nums):` 这行上下文，第 3 步才唯一。

如果 `edit` 做了模糊匹配，这一次会改到 `median` 上 —— 代码能跑，测试也可能过，但改错了地方。

**2. diff 是从文件真实算出来的**

第 3 步的 `diff` 字段是 `difflib.unified_diff` 对「改前 / 改后」两份文本算的，
前端直接把它渲染成带颜色的行；`file_changed` 事件让中间的编辑器同步刷新。

**3. 命令失败不是工具异常**

`bash` 的 `ok=True` 表示「工具执行成功」，`meta.exit_code` 才是命令自己的结果。
这样模型拿到的是观察值，不是一个被吞掉的异常 —— 它才有机会读 stderr 再决定下一步。

## 中断

另一次运行里，在第 2 步执行时点「停止」：

```
[step] {'n': 2, 'max': 20}
[tool_start] {'id': 'call_edit_...', 'name': 'edit', ...}
[notice] 已手动中断 —— 工具执行结果已保留。
[done] {'reason': 'stopped', 'steps': 2}
```

停止只设置一个事件：正在跑的工具会执行完（不会留下写了一半的文件），
循环在下一个检查点退出，已经产生的消息照常存盘。流式输出过程中点停止，
会在下一个 token 处立即中断。

---

# 真实模型的一次运行（含一次真实故障与修复）

**环境**：打包版 exe · DeepSeek `deepseek-chat`（真实 Key，配置在本机 `~/.desktop-agent/config.json`）
**任务**：`读一下 README.md，用一句话概括它是干什么的`

## 事件流（打包版 + 真实 API）

```
[step] {'n': 1, 'max': 20}
[tool_start] {'id': 'call_0', 'name': 'read', 'args': {'path': 'README.md'}}
[tool_end] {'id': 'call_0', 'name': 'read', 'ok': True, 'duration': 0.0}
[step] {'n': 2, 'max': 20}
[done] {'reason': 'finished', 'steps': 2}

final: 这是一个练习用的工作区，里面放了三个统计函数，并故意让 mean
       对空列表的处理出错，供智能体练习修复。
```

## 接真模型当天抓到的一个真 bug

第一次接上 DeepSeek 就 400：

```
HTTP 400: An assistant message with 'tool_calls' must be followed by tool
messages responding to each 'tool_call_id'.
```

**原因**：会话恢复时只保留了 `user/assistant` 消息，把 `tool` 消息过滤掉了。
上一次运行恰好死在工具执行中途，存盘的历史里有一条 `assistant(tool_calls)`
后面没跟工具响应 —— mock 模式从不重建这种历史，所以一直没暴露。

**修复**（`agent/loop.py` 的 `_sanitize_history`）：恢复和上下文压缩之后统一跑一遍
合法化 —— 孤儿 tool 消息丢弃，缺失的响应补一条「（会话在此中断，工具结果已丢失）」
占位。补占位而不是删掉 assistant 消息，是因为「我调过工具」这个事实对模型有用。

修复后带着同样的断裂历史重跑，一次通过 —— 上面的事件流就是修复后的真实输出。
对应测试：`tests/test_loop.py` 里的 `test_sanitize_*` 三条。
