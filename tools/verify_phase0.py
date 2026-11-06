"""第 0 阶段验收（只读：不生成订单、不改共享数据；第一次运行会把 settings.json 里的数据库密码搬进本机保管处）"""
import glob
import json
import os
import subprocess
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
ok_all = True


def step(title):
    print("\n==== " + title, flush=True)


def res(ok, text):
    global ok_all
    ok_all &= bool(ok)
    print(("  [OK]   " if ok else "  [FAIL] ") + text, flush=True)


step("1. 环境")
print("  Python", sys.version.split()[0], sys.platform, sys.executable)

step("2. 密码搬出 settings.json")
try:
    import settings as S
    import credentials as C
    before = json.load(open(S.SETTINGS_PATH, encoding="utf-8")).get("db", {})
    print("  搬之前 settings.json 里有数据库密码：", bool(before.get("password")))
    data = S.load_settings()
    after = json.load(open(S.SETTINGS_PATH, encoding="utf-8")).get("db", {})
    res(not after.get("password"), "现在 settings.json 里没有数据库密码")
    res("db.password" in C.names() or not before.get("password"), "本机保管处：" + C.cred_path() + "（" + "、".join(C.names()) + "）")
    import secure_store
    pw = (data.get("db") or {}).get("password")
    res((not pw) or bool(secure_store.unprotect(pw)), "程序读得出密码（不显示内容）" if pw else "这台电脑用的是内置的只读连接")
except Exception:
    res(False, "出错：\n" + traceback.format_exc())

step("3. 不开窗口检查一份真实门店清单（查真实数据库，只读）")
try:
    import workspace as W
    import commands as CMD
    import reorder_service as R
    root = data.get("wholesale_root") or W.default_root()
    files = [p for p in glob.glob(os.path.join(W.lists_dir(root), "**", "*.xls*"), recursive=True)
             if not os.path.basename(p).startswith(("~", "$")) and R.guess_store(p)]
    files.sort(key=os.path.getmtime, reverse=True)
    if not files:
        res(False, "门店清单文件夹里没找到清单：" + W.lists_dir(root))
    else:
        path = files[0]
        print("  清单：", path)
        t0 = time.time()
        lists = CMD.load_lists([(None, path)])
        rows, meta = CMD.run_check(data, lists, price_dir=W.price_dir(root), log=lambda s: print("   ", s, flush=True),
                                   progress=CMD.PrintProgress(CMD.CHECK_STEPS, out=lambda s: print("   ", s, flush=True)))
        CMD.review(data, rows, meta)
        cnt = {}
        for x in rows:
            cnt[x["verdict"]] = cnt.get(x["verdict"], 0) + 1
        res(len(rows) > 0 and any(x.get("in_db") for x in rows),
            f"{len(rows)} 个商品，用了 {time.time() - t0:.0f} 秒；判断：{cnt}；清单 {sum(x['lbox'] for x in rows)} 箱 → 确定 {sum(x['fbox'] for x in rows)} 箱")
        out = os.path.join(ROOT, "logs", "verify_phase0_rows.csv")
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            import csv
            w = csv.writer(f)
            w.writerow(["code", "ean", "name", "lbox", "sbox", "fbox", "pack", "verdict"])
            for x in rows:
                w.writerow([x.get("code"), x.get("ean"), x.get("name"), x["lbox"], x["sbox"], x["fbox"], x.get("pack"), x["verdict"]])
        print("  每个商品一行写在：", out)
except Exception:
    res(False, "出错：\n" + traceback.format_exc())

step("4. 命令行：列出待登记的订单（只读）")
try:
    import cli
    rc = cli.main(["drafts"])
    res(rc == 0, "cli.py drafts 正常")
except SystemExit as e:
    res(False, f"退出：{e}")
except Exception:
    res(False, "出错：\n" + traceback.format_exc())

step("5. 全部自动测试（Windows 上）")
try:
    import pytest  # noqa: F401
    tests = sorted(glob.glob("test_*.py"))
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    tail = "\n".join(p.stdout.strip().splitlines()[-25:])
    print(tail)
    res(p.returncode == 0, "pytest 全部通过" if p.returncode == 0 else "pytest 有没通过的")
except ImportError:
    print("  这台电脑没装 pytest，跳过")
except Exception:
    res(False, "出错：\n" + traceback.format_exc())

step("结果：" + ("全部通过" if ok_all else "有问题，见上面 [FAIL]"))
