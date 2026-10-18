"""
检查界面文字是否都有翻译：找出代码里 tr("中文…") 用到、但 translations.py 里还没有的文字。
用法：python tools/check_translations.py      （在项目根目录运行；有缺的会列出来并返回 1）
另外检查翻译里的 {0}、{1:g} 这类占位符和中文原文是否一致。
"""
import ast
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from translations import TRANSLATIONS  # noqa: E402

SKIP = ("test_", "main_旧版备份", "main_经典样式备份", "benchmark_")


def has_cjk(s):
    return any("一" <= c <= "鿿" for c in s)


def main():
    missing = {}
    for f in sorted(glob.glob(os.path.join(ROOT, "*.py")) + glob.glob(os.path.join(ROOT, "fenpei", "*.py"))):
        name = os.path.basename(f)
        if name.startswith(SKIP):
            continue
        try:
            tree = ast.parse(open(f, encoding="utf-8").read())
        except SyntaxError as e:
            print(f"语法错误 {name}: {e}")
            continue
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and getattr(n.func, "id", None) == "tr" and n.args
                    and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
                s = n.args[0].value
                if has_cjk(s) and s not in TRANSLATIONS:
                    missing.setdefault(s, f"{name}:{n.lineno}")
    bad = []
    for k, v in TRANSLATIONS.items():
        ph = sorted(re.findall(r"\{[^}]*\}", k))
        for t in v:
            if sorted(re.findall(r"\{[^}]*\}", t)) != ph:
                bad.append(k)
                break
    for s, where in missing.items():
        print(f"缺翻译  {where}  {s!r}")
    for k in bad:
        print(f"占位符不一致  {k!r}")
    print(f"\n缺 {len(missing)} 条，占位符不一致 {len(bad)} 条（共 {len(TRANSLATIONS)} 条翻译）")
    print("注意：tr(变量) 这种动态文字（例如列表里的步骤名）检查不到，要自己确认。")
    return 1 if (missing or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
