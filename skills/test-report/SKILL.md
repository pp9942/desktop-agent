---
name: test-report
kind: script
description: 生成工作区测试汇总报告时使用——自带 collect.py 脚本，产出 JSON
triggers: 测试报告, test report, 测试汇总
---

生成测试报告不要自己逐个文件数，用本技能自带的脚本，保证口径一致：

1. 用 read 读一下脚本本体确认参数：`skills/test-report/scripts/collect.py`
   （与本 SKILL.md 同目录）。
2. 用 bash 运行（workspace 换成当前工作区的绝对路径）：

   ```
   python "<本技能目录>/test-report/scripts/collect.py" "<workspace>"
   ```

   注意给 timeout 60 以上；脚本只做收集和运行，不修改任何文件。
3. 脚本输出 JSON：`{"files": [...], "tests": n, "passed": n, "failed": [...]}`。
   把它整理成人类可读的三行小结：
   - 测试文件数 / 用例总数 / 通过数；
   - 失败用例逐行列出（文件 + 用例名 + 断言差异）；
   - 一句结论：能不能合并。

规则：
- 报告里的数字必须来自脚本的 JSON 输出，不要自己数、不要编造；
- 脚本失败（比如没有测试文件）时如实说「没有可收集的测试」，不要输出假报告；
- 如果用户想看的是某一次具体的失败详情，直接用 read 看对应测试文件，不用跑报告。
