"""采购订单跟踪 ↔ PIANO Excel 双向同步（excel_sync + order_tracking.sync_excel 三方比较）"""
import os
import time
import tempfile
from datetime import date, datetime

import order_tracking as OT
import excel_sync as XS


def _piano(path, rows_foiano, budget=50000):
    """一张很小的 PIANO：NOV 表，两块门店（FOIANO、S.FILIPPO），第 3 行表头"""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "NOV"
    for c, title in ((1, "Gli ordini per Foiano"), (10, "Gli ordini per San Filippo")):
        ws.cell(1, c).value = title
        ws.cell(1, c + 2).value = "BADGET ACQUISTO"
        ws.cell(1, c + 5).value = budget
        ws.cell(2, c + 2).value = "1ª settimana:→"
        for k, h in enumerate(["Data Ordine", "Fornitore", "Ordine Da", "Importo", "Data confermato",
                               "Data arrivato", "Gg. cons."]):
            ws.cell(3, c + k).value = h
    for i, (d, sup, amt, conf, arr) in enumerate(rows_foiano):
        r = 4 + i
        ws.cell(r, 1).value, ws.cell(r, 2).value, ws.cell(r, 3).value = d, sup, "LUCAS"
        ws.cell(r, 4).value, ws.cell(r, 5).value, ws.cell(r, 6).value = amt, conf, arr
    wb.save(path)


def _set_mtime(path, ts):
    os.utime(path, (ts, ts))


def _setup():
    d = tempfile.mkdtemp()
    x = os.path.join(d, "PIANO ACQUISTO MESILE  NUOVO.xlsx")
    _piano(x, [(datetime(2026, 11, 2), "SAIS", 100.0, None, None),
               (datetime(2026, 11, 3), "LAMPA", 200.0, datetime(2026, 11, 3), None),
               (datetime(2026, 11, 3), "COLZANI", 80.0, None, None)])
    _set_mtime(x, time.time() - 3600)
    st = OT.Store(d, me="pc1", host="h1").reload()
    OT.import_file(st, x)
    return d, x, st


def _by_sup(st, sup):
    return next(oid for oid, o in st.orders.items() if o.get("supplier") == sup)


def test_program_edits_written_to_excel_and_read_back_clean():
    d, x, st = _setup()
    sais = _by_sup(st, "SAIS")
    st.set(sais, {"arrived": "2026-11-04", "note": "manca 1 collo", "state": "partial"})
    new = st.add({"date": "2026-11-04", "store": "3", "supplier": "MISS SPACE", "amount": 50.5, "buyer": "XH"})
    draft = st.add({"date": "2026-11-04", "store": "1", "supplier": "DRAFT SUP", "draft": True})
    st.set_budget("2026-11", "1", 60000)
    kinds = sorted(k for k, _o, _v in XS.pending(st, "pc1"))
    assert kinds == ["append", "budget", "update"]                # 待登记的不写
    res = XS.writeback(st, x, "pc1")
    assert res["status"] == "ok" and res["written"] == 3 and not res["failed"]
    rows, budgets, _ = OT.read_excel(x)
    sr = next(r for r in rows if r["supplier"] == "SAIS")
    assert sr["arrived"] == "2026-11-04" and sr["state"] == "partial" and "manca 1 collo" in (sr["note"] or "")
    ms = next(r for r in rows if r["supplier"] == "MISS SPACE")
    assert ms["store"] == "3" and ms["amount"] == 50.5 and ms["buyer"] == "XH"
    assert ("2026-11", "1", 60000.0) in budgets
    assert not any(r["supplier"] == "DRAFT SUP" for r in rows)
    assert os.listdir(os.path.join(st.dir, XS.BACKUP_DIR))           # 写之前备份了原文件
    # 另一台电脑读这份 Excel：不多出单、没有要写的
    st2 = OT.Store(d, me="pc2", host="h2").reload()
    r2 = OT.import_file(st2, x)
    assert r2["new"] == 0 and r2["updated"] == 0 and r2["removed"] == 0
    assert XS.pending(st2, "pc2", everyone=True) == []
    assert st2.orders[new]["xid"] and len([o for o in st2.orders.values() if o.get("supplier") == "MISS SPACE"]) == 1
    # 确认待登记以后才写
    OT.confirm_drafts(st2, [draft])
    assert [k for k, _o, _v in XS.pending(st2, "pc2", everyone=True)] == ["append"]


def test_last_change_wins():
    from openpyxl import load_workbook
    d, x, st = _setup()
    lampa = _by_sup(st, "LAMPA")
    # ① 程序先改到货日期，之后 Excel 里有人改成别的日期 → Excel 晚，用 Excel 的
    st.set(lampa, {"arrived": "2026-11-05"})
    wb = load_workbook(x)
    wb["NOV"].cell(5, 6).value = datetime(2026, 11, 6)
    wb.save(x)
    _set_mtime(x, time.time() + 60)
    OT.import_file(st, x)
    assert st.orders[lampa]["arrived"] == "2026-11-06"
    assert not XS.pending(st, "pc1")
    # ② Excel 先改（存盘时间早），程序后改 → 程序的为准，并写回 Excel
    wb = load_workbook(x)
    wb["NOV"].cell(5, 4).value = 222.0
    wb.save(x)
    _set_mtime(x, time.time() - 600)
    st.set(lampa, {"amount": 250.0})
    OT.import_file(st, x)
    assert st.orders[lampa]["amount"] == 250.0
    assert XS.pending(st, "pc1") == [("update", lampa, {"amount": 250.0})]
    # ③ Excel 改了程序没动的格 → 直接用 Excel 的
    wb = load_workbook(x)
    wb["NOV"].cell(4, 3).value = "MARCO"
    wb.save(x)
    OT.import_file(st, x)
    assert st.orders[_by_sup(st, "SAIS")]["buyer"] == "MARCO"


def test_deleted_in_program_clears_excel_row_and_busy_file_waits():
    d, x, st = _setup()
    sais = _by_sup(st, "SAIS")
    st.delete([sais])
    assert [k for k, _o, _v in XS.pending(st, "pc1")] == ["clear"]
    lock = os.path.join(d, "~$" + os.path.basename(x))
    open(lock, "w").close()
    assert XS.writeback(st, x, "pc1")["status"] == "busy"           # 有人开着 Excel：先不写
    os.remove(lock)
    assert XS.writeback(st, x, "pc1")["status"] == "ok"
    rows, _b, _s = OT.read_excel(x)
    assert [r["supplier"] for r in rows] == ["LAMPA", "COLZANI"]
    assert XS.pending(st, "pc1", everyone=True) == []
    # Excel 里删掉一行 → 程序里也删
    from openpyxl import load_workbook
    wb = load_workbook(x)
    for c in range(1, 8):
        wb["NOV"].cell(5, c).value = None
    wb.save(x)
    _set_mtime(x, time.time() + 60)
    r = OT.import_file(st, x)
    assert r["removed"] == 1 and not any(o.get("supplier") == "LAMPA" for o in st.orders.values())


def test_other_pc_waits_then_takes_over():
    from datetime import timezone, timedelta
    d, x, st = _setup()
    sais = _by_sup(st, "SAIS")
    st.set(sais, {"note": "x"})
    st2 = OT.Store(d, me="pc2", host="h2").reload()
    assert XS.pending(st2, "pc2") == []                                  # pc1 改的，pc1 写
    later = datetime.now(timezone.utc) + timedelta(minutes=XS.TAKEOVER_MIN + 1)
    assert [k for k, _o, _v in XS.pending(st2, "pc2", now=later)] == ["update"]


def test_formula_amount_without_cached_value():
    assert OT._calc("=876.91/1.22") == 876.91 / 1.22
    assert OT._calc("=SUM(A1:A3)") is None and OT._calc(12) == 12


def test_program_cells_marked_light_blue_and_unmarked_after_manual_edit():
    from openpyxl import load_workbook
    d, x, st = _setup()
    sais = _by_sup(st, "SAIS")
    st.set(sais, {"arrived": "2026-11-04"})
    st.add({"date": "2026-11-04", "store": "1", "supplier": "NEW SUP", "amount": 10.0})
    assert XS.writeback(st, x, "pc1")["status"] == "ok"
    ws = load_workbook(x)["NOV"]
    rgb = lambda c: str(c.fill.fgColor.rgb).upper() if c.fill is not None and c.fill.fill_type else None
    assert rgb(ws.cell(4, 6)) == XS.MARK_RGB and rgb(ws.cell(4, 2)) is None       # 只标写了的格
    new_r = next(r for r in range(4, 10) if ws.cell(r, 2).value == "NEW SUP")
    assert all(rgb(ws.cell(new_r, c)) == XS.MARK_RGB for c in range(1, 8))         # 新增的整行
    # 有人在 Excel 里手改了程序写的到货日期 → 下次同步去掉这格的标记
    wb = load_workbook(x)
    wb["NOV"].cell(4, 6).value = datetime(2026, 11, 5)
    wb.save(x)
    _set_mtime(x, time.time() + 60)
    OT.import_file(st, x)
    assert st.orders[sais]["arrived"] == "2026-11-05"
    assert XS.pending(st, "pc1") == [("unmark", sais, ["arrived"])]
    assert XS.writeback(st, x, "pc1")["status"] == "ok"
    ws = load_workbook(x)["NOV"]
    assert rgb(ws.cell(4, 6)) is None and rgb(ws.cell(new_r, 1)) == XS.MARK_RGB
    assert XS.pending(st, "pc1", everyone=True) == []


def test_sync_conflict_copy_merged_back_and_moved_away():
    """2026-11-04 真实情况：程序写进去的版本被同步盘改名成 …_Conflict.xlsx，原表换回旧版本"""
    import shutil
    from openpyxl import load_workbook
    d, x, st = _setup()
    old = os.path.join(d, "old.xlsx")
    shutil.copy2(x, old)                                            # 写之前的原表
    lampa = _by_sup(st, "LAMPA")
    st.set(lampa, {"arrived": "2026-11-04"})
    new = st.add({"date": "2026-11-04", "store": "1", "supplier": "NEW SUP", "amount": 10.0})
    assert XS.writeback(st, x, "pc1")["status"] == "ok"
    # 同步盘：程序写的这份变成冲突副本（副本里还有人手加了一行），原表换回旧版本、旁边同事又改了一格
    cp = os.path.join(d, "PIANO ACQUISTO MESILE  NUOVO_LAPTOP-PC1_11月-04-152013-2026_Conflict.xlsx")
    wb = load_workbook(x)
    wb["NOV"].cell(8, 1).value, wb["NOV"].cell(8, 2).value, wb["NOV"].cell(8, 4).value = \
        datetime(2026, 11, 4), "HANDMADE", 33.0
    wb.save(cp)
    _set_mtime(cp, time.time() - 30)
    shutil.copy2(old, x)
    wb = load_workbook(x)
    wb["NOV"].cell(6, 6).value = datetime(2026, 11, 5)               # COLZANI 到货：同事在原表里改的，要留着
    wb.save(x)
    _set_mtime(x, time.time() + 60)
    assert OT.find_excel(d) == x
    OT.import_file(st, x)                                           # 程序先跟着原表「改回去」
    assert st.orders[lampa].get("arrived") is None and new not in st.orders
    res = XS.writeback(st, x, "pc1")
    assert res["status"] == "ok", res
    assert res["conflicts"] == {"files": 1, "restored": 1, "added": 2}
    assert not XS.conflict_copies(x)                                 # 副本挪走了
    assert os.listdir(os.path.join(st.dir, XS.BACKUP_DIR, XS.CONFLICT_DIR))
    rows, _b, _s = OT.read_excel(x)
    by = {r["supplier"]: r for r in rows}
    assert by["LAMPA"]["arrived"] == "2026-11-04"                   # 恢复并重新写进去
    assert "NEW SUP" in by and "HANDMADE" in by                     # 程序新增的、副本里手加的都补回来
    assert by["COLZANI"]["arrived"] == "2026-11-05"                 # 原表里同事的修改没被盖掉
    assert st.orders[new].get("supplier") == "NEW SUP"
    assert XS.pending(st, "pc1", everyone=True) == []
    st2 = OT.Store(d, me="pc2", host="h2").reload()                 # 另一台电脑读：不多出单
    r2 = OT.import_file(st2, x)
    assert r2["new"] == 0 and r2["removed"] == 0
    assert len([o for o in st2.orders.values() if o.get("supplier") == "HANDMADE"]) == 1
