"""查数据库提速（2026-10-25）：几家门店同时查、报价读一次存起来——结果必须和以前一样"""
import os
import threading
import time

import reorder_service as R


def test_per_store_same_result_and_order():
    seen = []
    lock = threading.Lock()

    def fn(sid):
        time.sleep(0.01 * (5 - sid))
        with lock:
            seen.append(threading.current_thread().name)
        return sid * 10
    prog = []
    out = R.per_store([1, 2, 3, 4, 5], fn, lambda k, sid: prog.append(k))
    assert out == {1: 10, 2: 20, 3: 30, 4: 40, 5: 50}
    assert list(out) == [1, 2, 3, 4, 5]
    assert prog == [0, 1, 2, 3, 4]
    assert R.per_store([1, 2], fn, workers=1) == {1: 10, 2: 20}


def test_per_store_error_and_cancel():
    def fn(sid):
        if sid == 3:
            raise ValueError("x")
        return sid
    try:
        R.per_store([1, 2, 3], fn)
        assert False
    except ValueError:
        pass

    def cancel(k, sid):
        if k >= 1:
            raise InterruptedError("cancelled")
    try:
        R.per_store([1, 2, 3], lambda s: s, cancel)
        assert False
    except InterruptedError:
        pass


def test_revenue_cache(monkeypatch=None):
    calls = []

    def fake(db, sid, days=91):
        calls.append(sid)
        return {1: 2.0}
    old = R._query_store_revenue
    R._query_store_revenue = fake
    R._REV_CACHE.clear()
    try:
        class Db:
            engine = None
        assert R.query_store_revenue(Db(), 1) == {1: 2.0}
        assert R.query_store_revenue(Db(), 1) == {1: 2.0}
        assert R.query_store_revenue(Db(), 2) == {1: 2.0}
        assert calls == [1, 2]
    finally:
        R._query_store_revenue = old
        R._REV_CACHE.clear()


def test_price_file_cache(tmp_path=None):
    import tempfile
    import service as SV
    import settings
    d = tmp_path or tempfile.mkdtemp()
    f = os.path.join(str(d), "Prezzi_x.xlsx")
    with open(f, "wb") as fh:
        fh.write(b"1")
    calls = []

    def fake(path, log=None):
        calls.append(path)
        return {"800": {"price": 1.5, "pack": 6, "desc": "a"}}
    old_r, old_dir = SV._read_price_file, settings.app_dir
    SV._read_price_file = fake
    settings.app_dir = lambda: str(d)
    try:
        a = SV.read_price_file(f)
        b = SV.read_price_file(f)
        assert a == b == {"800": {"price": 1.5, "pack": 6, "desc": "a"}}
        assert len(calls) == 1
        time.sleep(0.02)
        with open(f, "wb") as fh:          # 文件改了 → 重新读
            fh.write(b"22")
        SV.read_price_file(f)
        assert len(calls) == 2
    finally:
        SV._read_price_file, settings.app_dir = old_r, old_dir


def test_query_reorder_data_parallel_keeps_results():
    """原程序的查询改成同时发：每一份结果还在原来的位置"""
    import pandas as pd
    from fenpei import database as D
    m = object.__new__(D.DatabaseManager)
    m.engine = None

    def fake_sql(q, engine, params=None):
        time.sleep(0.01)
        if "giacenza" in q:
            return pd.DataFrame({"CodArticolo": [1, 2], "store_inventory": [5, 6]})
        if "UNION ALL" in q:
            return pd.DataFrame({"period_index": [1, 2, 2], "CodArticolo": [1, 1, 2], "period_days": [30, 60, 60],
                                 "sales_qty": [3, 7, 1], "total_guadagno": [1, 2, 3], "total_net_sales": [4, 5, 6]})
        if "DISTINCT CodArticolo, IDFornitore" in q:
            return pd.DataFrame({"CodArticolo": [1, 2], "IDFornitore": [9, 9]})
        if "SalesAfterLastIn" in q:
            return pd.DataFrame({"CodArticolo": [1], "SalesAfterLastIn": [4]})
        if "LastSaleDate" in q:
            return pd.DataFrame({"CodArticolo": [1, 2], "LastSaleDate": ["2026-10-01", "2026-10-02"]})
        raise AssertionError(q)
    old = D.pd.read_sql
    D.pd.read_sql = fake_sql
    D.DatabaseManager.SINGLE_PASS_PERIODS = False
    try:
        r = m.query_reorder_data(1, [1, 2], [30, 60])
    finally:
        D.pd.read_sql = old
        D.DatabaseManager.SINGLE_PASS_PERIODS = True
    assert list(r["inventory"]["store_inventory"]) == [5, 6]
    assert list(r["suppliers"]["IDFornitore"]) == [9, 9]
    assert list(r["sales_1"]["sales_qty"]) == [3]
    assert list(r["sales_2"]["sales_qty"]) == [7, 1]
    assert list(r["sales_after_last_in"]["SalesAfterLastIn"]) == [4, 0]
    assert len(r["last_sale_date"]) == 2


def test_period_sales_one_pass_equals_union():
    """各周期销量一次查完（销售表只翻一遍）：和原来 UNION ALL 分开查的结果一模一样（用 sqlite 模拟销售表）"""
    import random
    import sqlite3
    from datetime import datetime, timedelta
    import pandas as pd
    from fenpei import database as D
    con = sqlite3.connect(":memory:", check_same_thread=False)
    lock = threading.Lock()
    con.execute("CREATE TABLE vendite_list_1 (CodArticolo INT, IDFornitore INT, Data TEXT, Qta REAL, Guadagno REAL,"
                " TotRighe REAL, TotCodIva REAL)")
    con.execute("CREATE TABLE prodotti (CodArticolo INT, Descrizione TEXT, CodBarre TEXT, ArticoloForn TEXT, PrezzoForn REAL,"
                " PrezzoVedi1 REAL, CodIva INT, qtasmallbox INT, Categoria INT, Sottocategoria INT, IDFornitore INT)")
    con.execute("CREATE TABLE giacenza (CodArticolo INT, G0 REAL, G1 REAL, G2 REAL, G3 REAL, G4 REAL, G5 REAL)")
    con.execute("CREATE TABLE categorie (Categorie INT, SottoCategorie INT, Descrizione TEXT)")
    con.execute("CREATE TABLE caricolistalist (CodArticolo INT, Cdata TEXT, Quantita REAL)")
    for c in range(1, 61):
        con.execute("INSERT INTO prodotti (CodArticolo) VALUES (?)", (c,))
    rnd = random.Random(7)
    now = datetime.now()
    for _ in range(3000):
        d = now - timedelta(days=rnd.randint(0, 400), hours=rnd.randint(0, 23))
        con.execute("INSERT INTO vendite_list_1 VALUES (?,?,?,?,?,?,?)",
                    (rnd.randint(1, 60), rnd.choice([5, 6]), d.strftime("%Y-%m-%d %H:%M:%S"),
                     rnd.choice([1, 2, -1, 0, None]), rnd.uniform(-1, 5), rnd.uniform(0, 30), rnd.uniform(0, 5)))

    old = D.pd.read_sql

    def fake_sql(q, engine, params=None):
        p = tuple(str(x) if hasattr(x, "strftime") else x for x in (params or ()))
        with lock:
            return old(q.replace("%s", "?"), con, params=p)
    m = object.__new__(D.DatabaseManager)
    m.engine = None
    D.pd.read_sql = fake_sql
    try:
        for sup, days, se in ((None, [30, 60, 90, 180], (None, None)), ([5], [30, 60, 90, 180, 120], (None, None)),
                              (None, [7, 30, 365], ((now - timedelta(days=50)).date(), (now - timedelta(days=10)).date()))):
            outs = []
            for single in (False, True):
                D.DatabaseManager.SINGLE_PASS_PERIODS = single
                r = m.query_reorder_data(1, list(range(1, 61)), days, sup, start_date=se[0], end_date=se[1])
                outs.append(r)
            for i in range(1, len(days) + 1):
                for k in (f"sales_{i}", f"margin_{i}"):
                    a = outs[0][k].sort_values("CodArticolo").reset_index(drop=True)
                    b = outs[1][k].sort_values("CodArticolo").reset_index(drop=True)
                    assert len(a) == len(b) and len(a) > 0, (k, len(a), len(b))
                    pd.testing.assert_frame_equal(a, b, check_dtype=False)
    finally:
        D.pd.read_sql = old
        D.DatabaseManager.SINGLE_PASS_PERIODS = True


def test_formulas_cached_and_value_sheet():
    """报价不开 Excel 直接读：只有公式结果都存在文件里才这样读；ValueSheet 和普通打开读到的一样"""
    import tempfile
    from openpyxl import Workbook, load_workbook
    import utils as U
    d = tempfile.mkdtemp()
    plain, formula = os.path.join(d, "a.xlsx"), os.path.join(d, "b.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.append(["CODICE EAN", "PREZZO NETTO", "PZ"])
    ws.append(["8001", 1.5, 6])
    ws.append([8002, "2,30", None])
    wb.save(plain)
    ws["D2"] = "=B2*C2"            # openpyxl 保存的公式没有计算结果 → 要用 Excel 算
    wb.save(formula)
    assert U.formulas_cached(plain) is False          # openpyxl 存的文件要求打开时重算 → 保守，用 Excel
    import zipfile
    excel_like = os.path.join(d, "c.xlsx")             # 和 Excel 存的一样：不要求重算
    with zipfile.ZipFile(plain) as zi, zipfile.ZipFile(excel_like, "w") as zo:
        for n in zi.namelist():
            data = zi.read(n)
            if n == "xl/workbook.xml":
                data = data.replace(b' fullCalcOnLoad="1"', b"")
            zo.writestr(n, data)
    assert U.formulas_cached(excel_like) is True
    assert U.formulas_cached(formula) is False
    assert U.formulas_cached(os.path.join(d, "x.xls")) is False
    full = load_workbook(plain, data_only=True).active
    v = U.read_values(plain)
    assert v.max_row == full.max_row
    for r in range(1, 5):
        for c in range(1, 5):
            assert v.cell(r, c).value == full.cell(r, c).value
    assert [x.value for x in v[1]] == ["CODICE EAN", "PREZZO NETTO", "PZ"]
    assert list(v.iter_rows(min_row=2, max_row=2, values_only=True)) == [("8001", 1.5, 6)]
    assert U.find_col(v, "PZ") == (3, 1)


def test_month_keys_and_series():
    """近两年每月销量：月份从旧到新，跨年对；没卖的月份 = 0"""
    from datetime import date
    k = R.month_keys(24, date(2026, 10, 25))
    assert len(k) == 24 and k[0] == (2024, 11) and k[-1] == (2026, 10) and k[-12] == (2025, 11)
    assert R.month_keys(3, date(2026, 1, 5)) == [(2025, 11), (2025, 12), (2026, 1)]
    assert R.monthly_series({(2026, 10): 5, (2025, 10): 2}, k)[-1] == 5.0
    assert R.monthly_series({(2026, 10): 5, (2025, 10): 2}, k)[-13] == 2.0
    assert sum(R.monthly_series({}, k)) == 0


def test_export_orders(tmp_path=None):
    """比价结果：Ordini 里只放采购了的供应商，名字 Ordine_门店_供应商_日期；distribuire 原样留着"""
    import tempfile
    import service as SV
    root = str(tmp_path or tempfile.mkdtemp())
    store_dir = os.path.join(root, "4 比价结果", "2026-10-25", "FOIANO")
    dist = os.path.join(store_dir, "distribuire")
    os.makedirs(dist)
    for f in ("Listino_01 - Offerta.xlsx", "formattato_059603.xlsx", "Canvass 44 New Grieco.xlsx", "缺失报告.xlsx"):
        with open(os.path.join(dist, f), "wb") as fh:
            fh.write(b"x")
    os.makedirs(os.path.join(store_dir, "Ordini"))
    with open(os.path.join(store_dir, "Ordini", "Ordine_FOIANO_OLD_2410.xlsx"), "wb") as fh:
        fh.write(b"old")
    old_valid = SV.is_valid_xlsx
    SV.is_valid_xlsx = lambda p: True
    try:
        rows = [{"row": 2, "qty": 3}, {"row": 3, "qty": 0}, {"row": 4, "qty": 2}]
        assign = {2: "Listino_01 - Offerta.xlsx", 3: "Canvass 44 New Grieco.xlsx", 4: "formattato_059603.xlsx"}
        got = SV.purchased_suppliers(rows, assign)
        assert got == {"Listino_01 - Offerta.xlsx", "formattato_059603.xlsx"}      # 数量 0 的不算采购
        out = SV.export_orders(dist, got, labels={"Listino_01 - Offerta.xlsx": "MELONI", "formattato_059603.xlsx": "MEGA CASH"},
                               settings={})
    finally:
        SV.is_valid_xlsx = old_valid
    names = sorted(os.listdir(os.path.join(store_dir, "Ordini")))
    assert names == ["Ordine_FOIANO_MEGA CASH_2510.xlsx", "Ordine_FOIANO_MELONI_2510.xlsx"]
    assert len(out) == 2 and os.path.exists(os.path.join(dist, "Canvass 44 New Grieco.xlsx"))
    assert os.path.exists(os.path.join(store_dir, "缺失报告_FOIANO_2510.xlsx"))
