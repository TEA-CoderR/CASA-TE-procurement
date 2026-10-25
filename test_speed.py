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
    try:
        r = m.query_reorder_data(1, [1, 2], [30, 60])
    finally:
        D.pd.read_sql = old
    assert list(r["inventory"]["store_inventory"]) == [5, 6]
    assert list(r["suppliers"]["IDFornitore"]) == [9, 9]
    assert list(r["sales_1"]["sales_qty"]) == [3]
    assert list(r["sales_2"]["sales_qty"]) == [7, 1]
    assert list(r["sales_after_last_in"]["SalesAfterLastIn"]) == [4, 0]
    assert len(r["last_sale_date"]) == 2
