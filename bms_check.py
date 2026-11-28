"""
BMS 凑单核对（2026-11-28 用户：「没标 CEN 的不一定不是 BMS 单，有可能是同事忘了标；之前的记录从程序生成的记录或者订单文件去看；
门店单和 BMS 单不要弄混淆，要百分百准确」）。不依赖界面，只读。

程序自己认的（不用人看）只有 PIANO 里 Gg.cons. 写了 CEN 的行（order_tracking.groups）。
这里找「可能也是 BMS 凑单、但没标 CEN」的行，列出来给人确认，确认了才在 PIANO 那一行写上 CEN：
  · 06 RECORD CENTRALE ACQUISTI 里文件名写了 BMS 或 nPDV 的订单文件（用户 2026-11-28：nPDV 也是送 BMS 仓库分货），
    文件里这家店那一页的金额（箱数 × 每箱件数 × 单价）= PIANO 这家店的一行（差 ≤ €0.05），同一家供应商，下单日期前后 3 天
  · 只对上一行才算（对上两行以上的不猜，写在「对不上的」里）
  · 文件汇总页这家店写的是 DIRETTO（直送）的：证据互相矛盾，默认不勾，要人看（sure = False）
  · 标了 CEN 但下单日期空着的行：文件里对上了 → 建议填文件的日期（这样才能和同一张单的别的店合在一起）
文件名里没写 BMS / nPDV 的（程序以前生成的 Ordine_供应商_日月）不算证据：里面也有直送门店的单。
"""
import os
import re
from datetime import date

import order_record as REC
import order_tracking as OT

AMOUNT_TOL = 0.05        # 金额差多少以内算一样（€）
DAYS_TOL = 3             # 下单日期和文件日期差几天以内
SCAN_DAYS = 120          # 看多久以内的订单文件

_BMS_NAME = re.compile(r"(?<![A-Z])BMS(?![A-Z])|(?<!\d)[2-5]\s*PDV", re.I)


def bms_named(name):
    """文件名写明是 BMS / 几家店一起（nPDV）的订单"""
    return bool(_BMS_NAME.search(os.path.basename(str(name or ""))))


def file_shares(lines):
    """read_lines 的结果 → {门店号: 金额}（只要 1..5 的门店页）"""
    out = {}
    for sid, xs in (lines or {}).items():
        if sid not in OT.BMS_ABBR:
            continue
        a = sum(float(x.get("colli") or 0) * float(x.get("pack") or 1) * float(x.get("price") or 0) for x in xs)
        if a > 0:
            out[sid] = round(a, 2)
    return out


def file_marks(path):
    """
    订单文件汇总页（GRIGLIA PER TUTTI PDV）门店名上一行写的去向 {门店号: "DIRETTO" / "BMS"}（程序生成的文件有；
    同事改过的文件有时还留着旧的）。读不了 → {}
    """
    import store_orders as SO
    from openpyxl import load_workbook
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception:
        return {}
    try:
        g = [t for t in wb.sheetnames if "TUTTI" in t.upper()]
        if not g:
            return {}
        rows = [list(r) for r in wb[g[0]].iter_rows(min_row=1, max_row=15, values_only=True)]
        names = {SO._norm(v): str(sid) for sid, v in SO.STORE_SHEETS.items()}
        out = {}
        for i in range(1, len(rows)):
            for j, v in enumerate(rows[i]):
                sid = names.get(SO._norm(v)) if v else None
                if sid and j < len(rows[i - 1]):
                    above = str(rows[i - 1][j] or "").strip().upper()
                    if above in ("DIRETTO", "BMS"):
                        out.setdefault(sid, above)
        return out
    except Exception:
        return {}
    finally:
        wb.close()


def _excel_orders(st):
    """PIANO 里的门店单（不是 BMS 那一块、不是程序拆的那一份、没取消）"""
    for oid, o in st.orders.items():
        if o.get("part_of") or str(o.get("store")) not in OT.BMS_ABBR or not o.get("ref"):
            continue
        if o.get("state") == "cancel" or OT.is_draft(o):
            continue
        yield oid, o


def find(st, files, index, log=None):
    """
    files = order_record.list_files 的结果（只看 06 那个文件夹的）；index = order_record.Index。
    → {"add": [要标 CEN 的], "date": [CEN 行要填日期的], "unsure": [对上好几行 / 文件里没有门店页的，只列出来]}
      每一项 {"id", "store", "supplier", "date", "amount", "ref", "file", "file_date", "file_amount", "why", "cen_others"}
    """
    orders = list(_excel_orders(st))
    cen_keys = {}
    for oid, o in orders:
        if o.get("cen"):
            cen_keys.setdefault(OT.sup_key(o.get("supplier")), []).append(OT.to_date(o.get("date")))
    add, fix, unsure, seen = [], [], [], set()
    for f in sorted(files, key=lambda f: (f["date"], f["rel"])):
        if f.get("store") != "C" or not bms_named(f["rel"]):
            continue
        shares = file_shares(index.lines(f, log))
        marks = file_marks(f["path"]) if shares else {}
        if not shares:
            unsure.append({"file": f["rel"], "file_date": f["date"], "why": "文件里没有门店页 / 没有数量"})
            continue
        for sid, amt in sorted(shares.items()):
            hits, undated = [], []
            for oid, o in orders:
                if str(o.get("store")) != sid or o.get("amount") is None:
                    continue
                if abs(float(o["amount"]) - amt) > AMOUNT_TOL:
                    continue
                if REC.supplier_score(o.get("supplier"), f["words"]) < 0.9:
                    continue
                d = OT.to_date(o.get("date"))
                if d is None:
                    if o.get("cen"):
                        undated.append((oid, o))
                    continue
                if abs((d - f["date"]).days) <= DAYS_TOL:
                    hits.append((oid, o))
            base = {"store": sid, "file": f["rel"], "file_date": f["date"], "file_amount": amt}
            if len(hits) == 1:
                oid, o = hits[0]
                if o.get("cen") or oid in seen:
                    continue
                seen.add(oid)
                k = OT.sup_key(o.get("supplier"))
                d = OT.to_date(o.get("date"))
                others = any(x and abs((x - d).days) <= DAYS_TOL for x in cen_keys.get(k, []))
                direct = marks.get(sid) == "DIRETTO"
                why = "同一张单别的店已标 CEN" if others else "文件名写的是 BMS / 几家店一起"
                if direct:
                    why += "；但文件汇总页这家店写的是 DIRETTO，要看一下"
                add.append(dict(base, id=oid, supplier=o.get("supplier"), date=d, amount=o.get("amount"),
                                ref=o.get("ref"), cen_others=others, mark=marks.get(sid), sure=not direct,
                                why=why))
            elif len(hits) > 1:
                unsure.append(dict(base, why="对上了好几行（{0}），不猜".format(
                    ", ".join(str(o.get("ref")) for _i, o in hits)), ids=[i for i, _o in hits]))
            elif len(undated) == 1 and undated[0][0] not in seen:
                oid, o = undated[0]
                seen.add(oid)
                fix.append(dict(base, id=oid, supplier=o.get("supplier"), date=None, amount=o.get("amount"),
                                ref=o.get("ref"), new_date=f["date"], why="标了 CEN、下单日期空着"))
    return {"add": add, "date": fix, "unsure": unsure}


def scan(settings, st, log=None, today=None):
    """读 06 RECORD CENTRALE ACQUISTI 里最近 SCAN_DAYS 天的订单文件（读过的用缓存）→ find()"""
    root = REC.record_root(settings)
    if not root or not os.path.isdir(root):
        raise FileNotFoundError("找不到订单明细文件夹（01 RECORD ORDINE）")
    files = [f for f in REC.list_files(root, today=today, days=SCAN_DAYS) if f.get("store") == "C"]
    index = REC.Index(REC.cache_path())
    try:
        return find(st, files, index, log)
    finally:
        index.save()


def apply(st, add_ids=(), dates=None):
    """
    确认了：add_ids 这几行标 CEN，dates {单号: 日期} 填下单日期。写进订单跟踪（来源 app），PIANO 那一行同步时写上 CEN / 日期
    （浅蓝底）。返回改之前的字段（撤销用）
    """
    before, ch = [], []
    for oid in add_ids:
        o = st.orders.get(oid)
        if o is None or o.get("cen"):
            continue
        before.append((oid, {"cen": o.get("cen")}))
        ch.append((oid, {"cen": True}))
    for oid, d in (dates or {}).items():
        o = st.orders.get(oid)
        if o is None or not d:
            continue
        before.append((oid, {"date": o.get("date")}))
        ch.append((oid, {"date": OT.iso(d)}))
    st.set_many(ch)
    OT.materialize_groups(st)
    return before


def undo(st, before):
    st.set_many(before)
    OT.materialize_groups(st)
