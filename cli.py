"""
命令行（2026-11-06 第 0 阶段）：不开程序窗口，直接调用业务命令（commands.py）。
用来验证「业务命令不依赖界面」，以后定时任务 / 采购服务器 / 智能体也走同样的命令。

用法（在程序文件夹里）：
    python cli.py check 门店清单.xlsx [更多清单…] [--store FOIANO] [--prices 报价文件夹] [--out 结果.csv]
        检查门店订货清单：和程序里「开始」一样查数据库、读报价、算建议箱数，打印汇总；--out 写出每个商品一行。
        只读：不生成主订单、不改任何文件。
    python cli.py drafts
        列出订单跟踪里「待登记」的订单（程序生成、还没确认已下单）。
    python cli.py confirm 单号 [单号…]
        确认已下单（会改共享的订单跟踪，先列出来问一次 y/N；--yes 跳过询问）。
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from i18n import tr, set_language  # noqa: E402
from settings import load_settings  # noqa: E402
import commands as CMD  # noqa: E402
import reorder_service as R  # noqa: E402

VERDICT_NAMES = {"none": "不订", "less": "少订", "more": "多订", "same": "照清单订", "nodb": "数据库没有",
                 "transfer": "建议调货"}


def _store_id(text):
    if text is None:
        return None
    t = str(text).strip()
    if t.isdigit():
        return int(t)
    for k, v in R.STORES.items():
        if str(v).strip().lower() == t.lower():
            return k
    raise SystemExit(tr("不认识的门店：{0}（可以写 {1}）", text, "、".join(str(v) for k, v in R.STORES.items() if k)))


def _price_dir(settings, given):
    if given:
        return given
    import workspace as W
    root = settings.get("wholesale_root") or ""
    return W.price_dir(root) if root and os.path.isdir(root) else ""


def cmd_check(a, settings):
    sid = _store_id(a.store)
    lists = CMD.load_lists([(sid, p) for p in a.files])
    price_dir = _price_dir(settings, a.prices)
    print(tr("门店清单：{0}", "、".join(f"{R.STORES.get(x['store'], x['store'])}（{len(x['rows'])} 行）" for x in lists)))
    print(tr("报价文件夹：{0}", price_dir or tr("（没有，用数据库的箱规）")))
    rows, meta = CMD.run_check(settings, lists, price_dir=price_dir, log=lambda s: print("  " + str(s)),
                               progress=CMD.PrintProgress(CMD.CHECK_STEPS))
    CMD.review(settings, rows, meta)
    count = {}
    for x in rows:
        count[x["verdict"]] = count.get(x["verdict"], 0) + 1
    amount = sum((x.get("fbox") or 0) * (x.get("pack") or 0) * (x.get("price") or 0) for x in rows)
    print()
    print(tr("共 {0} 个商品：", len(rows)) + " · ".join(f"{tr(VERDICT_NAMES.get(k, k))} {n}" for k, n in sorted(count.items())))
    print(tr("清单 {0} 箱 → 确定 {1} 箱", sum(x.get("lbox") or 0 for x in rows), sum(x.get("fbox") or 0 for x in rows)))
    if amount:
        print(tr("估算金额 €{0:,.2f}", amount))
    if a.out:
        cols = [("store", "门店"), ("code", "商品编号"), ("ean", "条码"), ("name", "名称"), ("lbox", "清单箱数"),
                ("sbox", "建议箱数"), ("fbox", "确定箱数"), ("pack", "每箱件数"), ("verdict", "判断")]
        with open(a.out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow([tr(h) for _, h in cols])
            for x in rows:
                w.writerow([R.STORES.get(x.get("store"), x.get("store")) if k == "store" else
                            tr(VERDICT_NAMES.get(x.get(k), x.get(k) or "")) if k == "verdict" else x.get(k, "")
                            for k, _ in cols])
        print(tr("已写出：{0}", a.out))
    return 0


def _store_name(v):
    try:
        return R.STORES.get(int(v), v)
    except (TypeError, ValueError):
        return v or ""


def cmd_drafts(a, settings):
    st = CMD.tracking_store(settings)
    ds = sorted(CMD.list_drafts(st), key=lambda o: (str(o.get("date") or ""), str(o.get("supplier") or "")))
    if not ds:
        print(tr("没有待登记的订单"))
        return 0
    for o in ds:
        print(f"{o['id']}\t{_store_name(o.get('store'))}\t{o.get('supplier') or ''}\t"
              f"€{float(o.get('amount') or 0):,.2f}")
    print(tr("共 {0} 张待登记", len(ds)))
    return 0


def cmd_confirm(a, settings):
    st = CMD.tracking_store(settings)
    found = [dict(st.orders[i], id=i) for i in a.ids if i in st.orders and st.orders[i].get("draft")]
    missing = [i for i in a.ids if i not in st.orders or not st.orders[i].get("draft")]
    if missing:
        print(tr("这些单号不存在或不是待登记，跳过：{0}", "、".join(missing)))
    if not found:
        return 1
    print(tr("要确认已下单（下单日期 = 今天，共享的订单跟踪里所有人都会看到）："))
    for o in found:
        print(f"  {o['id']}\t{_store_name(o.get('store'))}\t{o.get('supplier') or ''}\t€{float(o.get('amount') or 0):,.2f}")
    if not a.yes and input(tr("确定吗？(y/N) ")).strip().lower() not in ("y", "yes"):
        print(tr("没有改动"))
        return 1
    n = CMD.confirm_orders(st, [o["id"] for o in found], via="cli")
    print(tr("✓ 已确认 {0} 张", n))
    return 0


def cmd_mail(a, settings):
    """邮件核对（只读）：列出邮件说明的订单进展 / 重复的待登记 / 跟踪里没有的，不改任何东西"""
    res = CMD.mail_tracking(settings, days=a.days, via="cli")
    kinds = {"order": tr("已下单"), "confirm": tr("已确认"), "ship": tr("已发货"), "dup": tr("合并重复"),
             "missing": tr("补登记")}
    for s in res["suggest"] + res["dups"] + res["missing"]:
        m = s.get("mail") or {}
        d = m.get("date").strftime("%d/%m") if m.get("date") else ""
        print(f"{kinds.get(s['kind'], s['kind'])}\t{d}\t{_store_name(s.get('store'))}\t{s['supplier']}\t{m.get('subject', '')[:70]}")
    print(tr("读了 {0} 封信：邮件进展 {1} 条 · 重复的待登记 {2} 条 · 跟踪里没有 {3} 条（没改任何东西，要改请在程序里点「邮件核对」）",
             res["mails"], len(res["suggest"]), len(res["dups"]), len(res["missing"])))
    for e in res["errors"]:
        print("⚠ " + e)
    return 0


def main(argv=None):
    settings = load_settings()
    set_language(settings.get("language", "zh"))
    p = argparse.ArgumentParser(prog="cli.py", description=tr("CASA&TE 采购系统命令行"))
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help=tr("检查门店订货清单（只读）"))
    c.add_argument("files", nargs="+")
    c.add_argument("--store")
    c.add_argument("--prices")
    c.add_argument("--out")
    sub.add_parser("drafts", help=tr("列出待登记的订单"))
    c = sub.add_parser("confirm", help=tr("确认已下单"))
    c.add_argument("ids", nargs="+")
    c.add_argument("--yes", action="store_true")
    c = sub.add_parser("mail", help=tr("邮件核对（只读，列出要改的）"))
    c.add_argument("--days", type=int)
    a = p.parse_args(argv)
    return {"check": cmd_check, "drafts": cmd_drafts, "confirm": cmd_confirm, "mail": cmd_mail}[a.cmd](a, settings)


if __name__ == "__main__":
    sys.exit(main())
