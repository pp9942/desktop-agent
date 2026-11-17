"""测试收集与汇总：数清工作区里的测试文件和用例，产出 JSON 报告。

用法：python collect.py <workspace>
只读不改；没有发现任何测试时如实报告 tests=0。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TEST_FILE = re.compile(r"^test_.*\.py$")
TEST_DEF = re.compile(r"^\s*def (test_\w+)", re.MULTILINE)
CLASS_DEF = re.compile(r"^\s*class (Test\w+)", re.MULTILINE)


def collect(root: Path) -> dict:
    files: list[dict] = []
    total = 0
    for md in sorted(root.rglob("test_*.py")):
        if not TEST_FILE.match(md.name):
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        functions = TEST_DEF.findall(text)
        classes = CLASS_DEF.findall(text)
        # unittest.TestCase 里每个 test_ 方法都算一个用例；类内方法去重
        in_class = len(re.findall(r"^\s+def (test_\w+)", text, re.MULTILINE))
        top_level = len(functions) - in_class
        count = top_level + in_class  # 类内每方法一例，顶层函数每函数一例
        total += count
        files.append(
            {
                "file": str(md.relative_to(root)),
                "test_functions": len(functions),
                "test_classes": classes,
                "estimated_cases": count,
            }
        )
    return {
        "workspace": str(root),
        "files": files,
        "tests": total,
        "status": "ok" if files else "no_tests_found",
    }


def main() -> int:
    # 输出固定 UTF-8：消费方（含 subprocess text=True）按 UTF-8 解码才不会踩 GBK 坑
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    if not root.is_dir():
        print(json.dumps({"error": f"目录不存在: {root}"}, ensure_ascii=False))
        return 2
    print(json.dumps(collect(root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
