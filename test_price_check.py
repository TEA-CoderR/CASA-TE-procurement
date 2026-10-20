import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import price_check as P


FILES = {
    "MEGA CASH": {"1": {"price": 1.00, "pack": 12, "desc": "A"}, "2": {"price": 2.00, "pack": 6, "desc": "B"},
                  "9": {"price": 3.00, "pack": 1, "desc": "X"}},
    "MELONI": {"1": {"price": 0.90, "pack": 6, "desc": "A"}, "3": {"price": 5.00, "pack": 1, "desc": "C"}},
}
DB = {"1": {"code": "10", "name": "A", "price": 1.02, "pack": 12, "supplier": "MEGA CASH & CARRY SRL"},
      "2": {"code": "20", "name": "B", "price": 2.50, "pack": 12, "supplier": "ALTRO"},
      "3": {"code": "30", "name": "C", "price": None, "pack": None, "supplier": "MELONI SPA"}}


def test_compare_counts():
    res = P.compare(FILES, DB)
    mega, meloni = res["files"]
    assert (mega["items"], mega["in_db"], mega["same_sup"]) == (3, 2, 1)
    assert (mega["price_close"], mega["price_far"], mega["pack_same"], mega["pack_diff"]) == (1, 1, 1, 1)
    assert (meloni["in_db"], meloni["same_sup"], meloni["no_db_price"]) == (2, 1, 1)
    assert meloni["price_far"] == 1                  # 条码 1：数据库 1.02 和 MELONI 0.90 差 13%
    t = res["total"]
    assert (t["eans"], t["in_db"], t["multi"], t["spread"]) == (4, 3, 1, 1)
    assert [r["quotes"] for r in res["rows"] if r["ean"] == "1"] == [2, 2]


def test_same_supplier():
    assert P.same_supplier("MEGA CASH", "MEGA CASH & CARRY SRL")
    assert not P.same_supplier("formattato_listino", "LISTINO SRL")
    assert not P.same_supplier("MEGA", "")


def test_conclusion_and_export():
    res = P.compare(FILES, DB)
    lines = P.conclusion(res)
    assert lines[0].startswith("2 份报价表，一共 4 个条码，数据库里找得到 3 个")
    assert "1 个商品有两家以上批发商报价" in lines[1]
    path = os.path.join(tempfile.mkdtemp(), "x.xlsx")
    P.export(res, path)
    from openpyxl import load_workbook
    wb = load_workbook(path)
    assert wb.sheetnames == ["汇总", "明细"] and wb["明细"].max_row == 6
