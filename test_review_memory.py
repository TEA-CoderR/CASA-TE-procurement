# -*- coding: utf-8 -*-
"""审核记录（review_memory）+ 清单读法（Cod.Barre、只有条码的新行、表格范围）+ 文件名里的门店编号 + 门店报告汇总文件"""
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timedelta

from openpyxl import Workbook, load_workbook

import reorder_service as R
import review_memory as RM
import workspace as W
from check_rules import _set_box, _edited, write_problem_report, report_name


def _foiano_list(path, rows=None):
    """FOIANO 那种清单：Cod.Art. / Cod.Barre / Descrizione …，最后几行只有条码（新商品）"""
    wb = Workbook()
    ws = wb.active
    ws.append(["Cod.Art.", "Cod.Barre", "Descrizione", "Cod.For.", "For.Art.", "Quantità", "Prz.Acq. ", "Prz.Ven. ", "Giacenza"])
    for r in rows or [("348138", "8030976034350", "INTRA GOURMAND", 566, "", 1, 1.78, 3.49, 2),
                      ("349360", "8002410046830", "BOROTALCO", 566, "", 2, 2.4, 4.49, 9),
                      ("205344", "8008970055398", "VIDAL", 566, "", 1, 1.1, 1.49, 7),
                      (None, "8004260019033", "NICKY MAGIA NATALE CARTA CUCINA", None, None, 7, 1.99, None, None)]:
        ws.append(list(r))
    wb.save(path)
    return path


def _shrink_dimension(path, ref):
    """把文件里记的表格范围改小（有的软件存的文件就是这样：后来加的行不在范围里）"""
    tmp = path + ".tmp"
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for it in zin.infolist():
            data = zin.read(it.filename)
            if it.filename == "xl/worksheets/sheet1.xml":
                txt = data.decode("utf-8")
                if "<dimension" in txt:
                    txt = re.sub(r'<dimension ref="[^"]*"/>', f'<dimension ref="{ref}"/>', txt)
                else:
                    txt = txt.replace("<sheetData", f'<dimension ref="{ref}"/><sheetData', 1)
                data = txt.encode("utf-8")
            zout.writestr(it, data)
    os.replace(tmp, path)


def test_list_reads_cod_barre_new_rows_and_whole_sheet():
    d = tempfile.mkdtemp()
    p = _foiano_list(os.path.join(d, "FOIANO - DETER1.xlsx"))
    info = R.parse_order_file(p)
    assert info["fields"]["ean"] == "Cod.Barre"
    assert [x["ean"] for x in info["rows"]][-1] == "8004260019033" and len(info["rows"]) == 4
    assert info["rows"][0]["code"] == "348138" and info["rows"][0]["ean"] == "8030976034350"
    # 以前的读法：条码列认不出，最后只有条码的那行被跳过
    old = R.parse_order_file(p, legacy=True)
    assert len(old["rows"]) == 3 and "ean" not in old["fields"]
    # 文件里记的范围只到第 3 行：照样读到最后
    _shrink_dimension(p, "A1:I3")
    assert len(R.parse_order_file(p)["rows"]) == 4


def test_codes_kept_when_most_new_rows_have_only_ean():
    """S.MARCO：441 行里 281 行只有条码 → 以前把写了的商品编号全清掉；现在只看写了编号的行"""
    d = tempfile.mkdtemp()
    rows = [("209650", "8002910029470", "SOLE", 435, "", 1, 2.67, 4.99, 5)] + \
           [(None, f"80000000{i:05d}", f"NEW {i}", None, None, 1, 1.0, None, None) for i in range(5)]
    info = R.parse_order_file(_foiano_list(os.path.join(d, "S.MARCO - DETER4.xlsx"), rows))
    assert info["rows"][0]["code"] == "209650" and not info["warnings"]


def test_store_number_at_end_of_file_name():
    assert W.guess_store("DETER3.xlsx") == 3 and R.guess_store(r"C:\x\DETER5.xlsx") == 5
    assert W.guess_store("lista DETER 4 (1).xlsx") == 4          # Windows 复制的「(1)」不算
    assert W.guess_store("FOIANO - DETER2.xlsx") == 1             # 有门店名先看门店名
    assert W.guess_store("Ordine 1810.xlsx") is None and W.guess_store("lista 05.xlsx") is None
    assert W.guess_store("DETER7.xlsx") is None and W.guess_store("lista.xlsx") is None


def _row(code, ean, lbox, sbox, store=1, pack=6):
    x = {"store": store, "code": code, "ean": ean, "name": f"ART {code}", "lbox": lbox, "sbox": sbox, "pack": pack}
    _set_box(x, sbox)
    return x


def test_review_kept_new_and_changed_after_recompute():
    root = tempfile.mkdtemp()
    rows = [_row("1", "801", 2, 2), _row("2", "802", 3, 1), _row("3", "803", 1, 0)]
    _set_box(rows[1], 3)                       # 人改了：系统 1 箱 → 3 箱
    RM.save(root, 1, RM.build(rows, 1, confirmed=True, edited=_edited, who="marco@PC"))
    # 清单改了：第 3 个 1 → 2 箱、加了第 4 个；重新计算后系统建议也变了
    new = [_row("1", "801", 2, 1), _row("2", "802", 3, 1), _row("3", "803", 2, 1), _row("4", "804", 1, 1)]
    n = RM.apply(new, RM.load(root, 1), _set_box)
    assert n == {"kept": 2, "changed": 1, "new": 1}
    assert [x["fbox"] for x in new] == [2, 3, 1, 1]          # 审过的照上次定的；改过 / 新加的按系统
    assert [x.get("review") for x in new] == ["kept", "kept", "changed", "new"] and new[2]["review_prev"] == 1
    assert new[0]["final"] == 12                              # 件数跟着箱数
    # 再存一次（还没确认）：沿用的照样算确认过，新加的不记 → 下次照样要审
    mem = RM.build(new, 1, prev={"confirmed": RM.load(root, 1)["confirmed"]}, edited=_edited)
    assert set(mem["items"]) == {"801", "802"} and mem["items"]["801"]["ok"] and mem["confirmed"]
    RM.save(root, 1, mem)
    again = [_row("1", "801", 2, 1), _row("4", "804", 1, 1)]
    assert RM.apply(again, RM.load(root, 1), _set_box) == {"kept": 1, "changed": 0, "new": 1}


def test_review_not_confirmed_keeps_only_edits_and_old_records_ignored():
    root = tempfile.mkdtemp()
    rows = [_row("1", "801", 2, 2), _row("2", "802", 3, 1)]
    _set_box(rows[1], 0)
    RM.save(root, 1, RM.build(rows, 1, edited=_edited))      # 审到一半（没点确认）
    new = [_row("1", "801", 2, 1), _row("2", "802", 3, 1), _row("3", "803", 1, 1)]
    assert RM.apply(new, RM.load(root, 1), _set_box) == {"kept": 1, "changed": 0, "new": 0}
    assert [x["fbox"] for x in new] == [1, 0, 1]
    # 超过 5 天的（上一周的清单）不用
    assert RM.load(root, 1, now=datetime.now() + timedelta(days=RM.KEEP_DAYS + 1)) is None
    # 换成替代商品的：按清单里的原条码找
    x = _row("9", "899", 3, 1)
    x["repl"] = {"old_ean": "802", "old_code": "2"}
    assert RM.key_of(x) == "802"


def test_restore_from_last_confirmed_main_order():
    """以前的版本没有审核记录：从上次「确认，开始比价」的主订单补；以前的程序读不到的行（只有条码的新商品）要审"""
    root = tempfile.mkdtemp()
    os.makedirs(W.lists_dir(root))
    lst = _foiano_list(os.path.join(W.lists_dir(root), "FOIANO - DETER1.xlsx"))
    past = datetime.now() - timedelta(hours=3)
    os.utime(lst, (past.timestamp(), past.timestamp()))
    folder = R.make_store_folder(R.batch_store_folder(root, 1))
    # 当时：348138 订 3 箱（调货前 4 箱）、349360 不订（主订单不写 0 箱）、205344 订 1 箱
    rows = [dict(_row("348138", "8030976034350", 1, 4), fbox=3), dict(_row("205344", "8008970055398", 1, 1))]
    path, n, _ = R.write_checked_order(rows, folder, 1)
    R.save_snapshot(path, 1, {}, rows, transfers=[{"from": 2, "to": 1, "code": "348138", "ean": "8030976034350",
                                                   "colli_before": 4, "colli_after": 3}])
    now = datetime.now() + timedelta(minutes=10)
    new = [_row("348138", "8030976034350", 1, 2), _row("349360", "8002410046830", 2, 2),
           _row("205344", "8008970055398", 1, 2), _row("", "8004260019033", 7, 7)]
    got = RM.restore(root, {1: new}, {1: lst}, _set_box, now=now)
    assert got[1]["source"] == "main_order" and (got[1]["kept"], got[1]["new"]) == (3, 1)
    assert [x["fbox"] for x in new] == [4, 0, 1, 7] and new[3]["review"] == "new"
    # 清单在确认以后又改过：主订单里没有的不能当成「当时定了不订」
    later = datetime.now() + timedelta(minutes=5)
    os.utime(lst, (later.timestamp(), later.timestamp()))
    new2 = [_row("349360", "8002410046830", 2, 2)]
    RM.restore(root, {1: new2}, {1: lst}, _set_box, now=now)
    assert new2[0]["review"] == "new" and new2[0]["fbox"] == 2
    # 有了审核记录就用记录，不再看主订单
    RM.save(root, 1, RM.build(new, 1, confirmed=True, edited=_edited))
    shutil.rmtree(os.path.join(root, R.BATCH_DIR))
    new3 = [_row("349360", "8002410046830", 2, 1)]
    assert RM.restore(root, {1: new3}, {1: lst}, _set_box)[1]["source"] == "app" and new3[0]["fbox"] == 0


def test_store_report_one_file_every_store_a_sheet():
    d = tempfile.mkdtemp()
    rows = [{"store": 2, "code": "3", "ean": "8000000000035", "name": "FADOR A", "in_db": True, "pack": 6,
             "in_grid": False, "in_quote": True, "lbox": 4, "fbox": 4}]
    p = write_problem_report(rows, os.path.join(d, report_name(None)), stores=[1, 2, 3, 4, 5])
    wb = load_workbook(p)
    assert wb.sheetnames == ["FOIANO", "MARLIA", "S.FILIPPO", "S.MARCO", "S.CONCORDIO"]
    assert "Nessun articolo" in str(wb["FOIANO"]["A4"].value)
    assert os.path.basename(p).startswith("Articoli_da_verificare_") and "FOIANO" not in os.path.basename(p)
