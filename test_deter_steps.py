"""DETERSIVO 第 ① ③ ④ 步的纯函数（2026-11-16）"""
import os
from datetime import date, datetime
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import deter_steps as DS
import order_tracking as OT

TODAY = date(2026, 11, 16)          # 星期一


def test_price_state_this_week_last_week_and_mail():
    assert DS.price_state(datetime(2026, 11, 16, 8), TODAY)[1] == "ok"
    assert DS.price_state(datetime(2026, 11, 12), TODAY)[0] == "上周的"
    assert DS.price_state(datetime(2026, 11, 2), TODAY)[0] == "02/11 的"
    assert DS.price_state(datetime(2026, 11, 16), TODAY, mail_newer=True)[1] == "warn"


def test_ready_items_flags_unknown_store_old_price_and_missing_grid():
    items = DS.ready_items([(1, None), (2, None), (None, None)],
                           [("MELONI", datetime(2026, 11, 5), False), ("PETRILLO", datetime(2026, 11, 16), False)],
                           {1: ["e.xlsx"]}, None, TODAY)
    titles = [t for _s, t, _x in items]
    assert items[0][0] == 1 and "S.FILIPPO" in items[0][2]
    assert any("认不出门店" in t for t in titles)
    assert any(s == 0 and "MELONI" in t for s, t, _x in items)
    assert any(s == 1 and "1 份报价是本周的" in t for s, t, _x in items)
    assert any("没有 Griglia" in t for t in titles)


def test_batch_orders_latest_day_even_after_confirmed():
    def o(day, store, sup, **kw):
        return OT.draft_id("wholesale", day, store, sup), dict(origin="wholesale", store=store, supplier=sup, **kw)
    orders = dict([o("2026-11-09", "1", "MELONI", draft=True),
                   o("2026-11-16", "1", "MELONI", draft=True),
                   o("2026-11-16", "2", "PETRILLO", date="2026-11-16", sent={"at": "x"}),   # 发过了：单号不变
                   o("2026-11-16", "3", "MEGA CASH", draft=True, state="cancel")])
    orders["x"] = {"origin": "store_orders", "store": "1", "supplier": "A"}
    day, rows = DS.batch_orders(orders, today=TODAY)
    assert day == "2026-11-16"
    assert [r[1]["supplier"] for r in rows] == ["MELONI", "PETRILLO"]
    assert DS.send_state(rows[0][1])[0] == "待发送" and DS.send_state(rows[1][1])[0] == "已发送"


def test_compare_rows_states():
    batch = {"1": "/r/FOIANO", "2": "/r/MARLIA", "4": "/r/S.MARCO", "5": "/r/S.CONCORDIO"}
    rows = DS.compare_rows(batch, ["/r/S.CONCORDIO"], [("FOIANO", True, ""), ("MARLIA", False, "报价打不开")],
                           "/r/S.MARCO", True, {"FOIANO": (3810, 624, 601, 3, 23, 48)})
    st = {r[0]: r[1] for r in rows}
    assert st == {"FOIANO": "done", "MARLIA": "fail", "S.MARCO": "now", "S.CONCORDIO": "wait"}
    assert rows[0][2] == "601 / 624 分给 3 家 · 缺 23" and rows[0][4] == "€3,810"


def test_result_store_chips_group_whole_batch_date_folder(monkeypatch):
    """比价结果顶上的门店：同一个「4 比价结果\\日期」里的几家门店都列出来（以前只按门店文件夹比 → 只有一家）"""
    import os
    import history
    import result_page as RP
    root = os.path.join("C:" + os.sep, "W", "4 比价结果", "2026-11-18")
    runs = [
        {"id": "a", "time": "2026-11-18 12:40:00", "folder": os.path.join(root, "FOIANO"), "store": "FOIANO"},
        {"id": "b", "time": "2026-11-18 12:50:00", "folder": os.path.join(root, "MARLIA"), "store": "MARLIA"},
        {"id": "c", "time": "2026-11-18 12:57:00", "folder": os.path.join(root, "S.MARCO"), "store": "S.MARCO"},
        {"id": "d", "time": "2026-11-18 12:58:00", "folder": os.path.join(root, "S.MARCO"), "store": "S.MARCO"},
        {"id": "e", "time": "2026-11-11 10:00:00",
         "folder": os.path.join("C:" + os.sep, "W", "4 比价结果", "2026-11-11", "FOIANO"), "store": "FOIANO"},
    ]
    monkeypatch.setattr(history, "list_runs", lambda: runs)
    got = RP._batch_runs({"history_id": "d"})
    assert [e["id"] for e in got] == ["a", "b", "d"]
