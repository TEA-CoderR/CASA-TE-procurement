"""门店扫码枪目录（2026-11-21）：Griglia + 这周报价 + 替代商品 + 直厂 → 扫码枪离线查的目录"""
import json
import sys
import types

import pytest

if "win32com" not in sys.modules:                 # utils 一 import 就要（云端没有 Excel）
    w = types.ModuleType("win32com"); c = types.ModuleType("win32com.client"); w.client = c
    sys.modules["win32com"] = w; sys.modules["win32com.client"] = c

import booking_api as BA
import scanner_catalog as SC
from test_booking_api import FakeSession


@pytest.fixture(autouse=True)
def _cred(monkeypatch, tmp_path):
    monkeypatch.setenv("CASATE_CRED_DIR", str(tmp_path / "cred"))


GRID = {"path": "/x/ELENCO_DETERSIVI.xlsx",
        "items": {"8001": {"name": "SVELTO 1000", "pack": 12}, "8002": {"name": "DIXAN 40"}, "8004": {"name": "NUOVO"}},
        "struck": {"8003": {"name": "SVELTO 980"}}}
QUOTES = {"MELONI": {"8001": {"price": 1.2, "desc": "SVELTO PIATTI 1000"}, "08005": {"price": 2, "desc": "FUORI GRIGLIA"},
                     "8002": {"price": 0, "desc": "omaggio"}},
          "PETRILLO": {"8001": {"price": 1.1, "desc": "SVELTO"}}}


def test_catalog_rows_griglia_state_wholesalers_substitute_direct():
    meta, rows = SC.build(GRID, QUOTES, {"8003": {"ean": "8004", "name": "NUOVO"}},
                          {"ean": {"5005": "BAMA", "8005": "BAMA"}, "items": {("BAMA", "5005"): [6.0, 1.2]}}, week="2026-W47")
    by = {r[0]: r for r in rows}
    assert meta["sources"] == ["MELONI", "PETRILLO"] and meta["directs"] == ["BAMA"]
    assert by["8001"] == ["8001", "SVELTO 1000", 1, [0, 1], "", "", -1, 12, None, -1]   # 箱规；进价不发给门店（2026-11-22）
    assert by["8002"][3] == []                      # 价格 0 的行不算有货
    assert by["8003"][2] == 2 and by["8003"][4:6] == ["8004", "NUOVO"]
    assert by["8005"] == ["8005", "FUORI GRIGLIA", 0, [0], "", "", 0, None, None, -1]   # 前面的 0 去掉
    assert by["5005"][2] == 0 and by["5005"][6] == 0
    assert by["5005"][7] == 6                       # 直厂商品的箱规从 SCHEDA 来
    assert meta["counts"] == {"griglia": 3, "stopped": 1, "items": 6}
    assert all(len(r) == 10 for r in rows)


def test_publish_in_parts_and_only_when_changed(tmp_path, monkeypatch):
    BA.set_token("k" * 40)
    monkeypatch.setattr(SC, "PART", 2)
    calls = []

    def answer(params, body):
        calls.append(body)
        return {"complete": body["part"] == body["parts"], "id": 9, "items": 6}
    s = FakeSession({("POST", "/api/erp/catalog"): (200, answer)})
    meta, rows = SC.build(GRID, QUOTES, {}, None)
    state = str(tmp_path / "state.json")
    res = SC.publish_if_changed({}, meta, rows, "Marco", session=s, log=lambda *_: None, state_path=state)
    assert res["id"] == 9 and [c["part"] for c in calls] == [1, 2, 3] and calls[0]["parts"] == 3
    assert "meta" in calls[0] and "meta" not in calls[1] and calls[0]["by"] == "Marco"
    assert sum(len(c["items"]) for c in calls) == len(rows)
    assert json.load(open(state))["id"] == 9
    assert SC.publish_if_changed({}, meta, rows, "Marco", session=s, log=lambda *_: None, state_path=state) is None
    assert len(calls) == 3                          # 没变不再发


def test_device_accounts_go_through_platform():
    BA.set_token("k" * 40)
    s = FakeSession({("POST", "/api/erp/scanners"): (201, {"device": {"id": 4, "name": "TC22 FOIANO"}, "pairCode": "ABCD2345"}),
                     ("POST", "/api/erp/scanners/4"): (200, {"device": {"id": 4, "active": False}})})
    d, code = SC.add_device({}, "TC22 FOIANO", "1", session=s)
    assert code == "ABCD2345" and s.calls[0][3] == {"name": "TC22 FOIANO", "entityCode": 1}
    assert SC.pretty_code(code) == "ABCD-2345"
    assert SC.set_active({}, 4, False, session=s)["active"] is False
    assert s.calls[1][3] == {"action": "deactivate"}


def test_rename_move_delete_and_sort_devices():
    BA.set_token("k" * 40)
    s = FakeSession({("POST", "/api/erp/scanners/4"): (200, {"device": {"id": 4, "name": "TC22 MARLIA"}, "deleted": 4})})
    assert SC.update_device({}, 4, " TC22 MARLIA ", session=s)["name"] == "TC22 MARLIA"
    assert s.calls[0][3] == {"action": "update", "name": "TC22 MARLIA"}        # 门店不改就不发
    SC.update_device({}, 4, "X", "BMS", session=s)
    assert s.calls[1][3] == {"action": "update", "name": "X", "entityCode": 0}
    SC.delete_device({}, 4, session=s)
    assert s.calls[2][3] == {"action": "delete"}
    devs = [{"id": 1, "name": "b", "active": False}, {"id": 2, "name": "z", "entityCode": 4, "active": True, "paired": False},
            {"id": 3, "name": "a", "entityCode": 4, "active": True, "paired": True}, {"id": 4, "name": "c", "entityCode": 1, "active": True}]
    assert [d["id"] for d in SC.sort_devices(devs)] == [4, 3, 2, 1]        # 在用的在前，停用的最后
    assert SC.waiting(devs) == 2


def test_store_data_row_advice_and_prices():
    from datetime import date
    m = {"stock": 9.0, "s30": 22, "s90": 60, "last_days": 1, "pack": 6.0}
    r = SC.store_row("8001", m, 1, 1.104, 2.49, {"price": 1.99, "end": date(2026, 11, 30)}, sys_box=3)
    assert r == ["8001", 9.0, 22.0, 1.0, 3, 1.1, 2.49, 1.99, "30/11", 6.0, 1, 0, None, None]   # 建议已经扣过在途（store_advice）
    assert SC.store_row("8001", {}, 0, None, None, None)[4] is None              # 不在 Griglia：没有建议箱数，价格照样有
    assert SC.store_row("8001", {"pack": 56}, 0, None, None, None, sys_box=1, pack=1)[9] == 1   # 审核用的箱规（批发商的）
    assert SC.cheapest(QUOTES, "8001") == 1.1 and SC.cheapest(QUOTES, "8002") is None


def test_stock_suspect_when_it_should_sell_but_nothing_sold_for_long():
    # 一天卖 1 件、12 件在店里、10 天一件没卖 → 不对劲
    assert SC.stock_suspect(12, 30, 90, 10)
    assert not SC.stock_suspect(12, 30, 90, 2)          # 前天还卖了
    assert not SC.stock_suspect(0, 30, 90, 30)          # 账面也没货
    assert not SC.stock_suspect(5, 0, 0, None)          # 本来就不卖的
    # 卖得慢（90 天 6 件 = 15 天一件）：45 天以上没卖才算
    assert not SC.stock_suspect(4, 0, 6, 30) and SC.stock_suspect(4, 0, 6, 45)
    assert SC.store_row("1", {"stock": 12, "s30": 30, "s90": 90, "last_days": 10}, 0, None, None, None)[11] == 1


def test_publish_sends_each_store_its_own_parts(monkeypatch, tmp_path):
    BA.set_token("k" * 40)
    monkeypatch.setattr(SC, "PART", 2)
    calls = []
    s = FakeSession({("POST", "/api/erp/catalog"): (200, lambda p, b: calls.append(b) or {"complete": b["part"] == b["parts"]})})
    meta, rows = SC.build(GRID, QUOTES, {}, None)
    SC.publish({}, meta, rows, "M", session=s, stores={2: [["8001", 3]], 1: [["8001", 9], ["8002", 0], ["8004", 1]]})
    assert [c.get("store") for c in calls] == [None, None, None, 1, 1, 2]
    assert all(c["parts"] == 6 for c in calls)
    # 门店数据变了也算变了：要重新发布
    state = str(tmp_path / "st.json")
    SC.publish_if_changed({}, meta, rows, "M", session=s, log=lambda *_: None, state_path=state, stores={1: [["8001", 9]]})
    n = len(calls)
    assert SC.publish_if_changed({}, meta, rows, "M", session=s, log=lambda *_: None, state_path=state, stores={1: [["8001", 9]]}) is None
    SC.publish_if_changed({}, meta, rows, "M", session=s, log=lambda *_: None, state_path=state, stores={1: [["8001", 8]]})
    assert len(calls) > n


def test_store_data_cached_for_a_few_hours(monkeypatch, tmp_path):
    runs = []
    monkeypatch.setattr(SC, "store_data", lambda *a, **k: runs.append(1) or {1: [["8001", 9]]})
    p = str(tmp_path / "c.json")
    assert SC.cached_store_data({}, [], {}, log=lambda *_: None, path=p, now=1000) == {1: [["8001", 9]]}
    assert SC.cached_store_data({}, [], {}, log=lambda *_: None, path=p, now=1000 + 3600) == {1: [["8001", 9]]}
    assert len(runs) == 1
    SC.cached_store_data({}, [], {}, log=lambda *_: None, path=p, now=1000 + 5 * 3600)
    SC.cached_store_data({}, [], {}, log=lambda *_: None, path=p, now=1000 + 5 * 3600, force=True)
    assert len(runs) == 3


LIST = {"id": 7, "entityCode": 1, "week": "2026-11-22", "lines": [
    {"ean": "8015100572212", "name": "DIXAN 40", "boxes": 2, "kind": "griglia", "updatedBy": "TC22 FOIANO 1"},
    {"ean": "8000036022017", "name": "AMUCHINA", "boxes": 1, "kind": "urgent", "reason": "Scaffale vuoto"},
    {"ean": "8004", "name": "NUOVO", "boxes": 1, "kind": "sub", "origEan": "8003", "origName": "SVELTO 980"},
    {"ean": "8001090213465", "name": "SVELTO", "boxes": 0, "kind": "remind"}]}


def test_scanner_list_written_like_a_store_list_with_urgent_column(tmp_path):
    import reorder_service as R
    p = SC.write_list_excel(LIST, str(tmp_path / SC.list_file_name(LIST)))
    assert SC.list_file_name(LIST) == "FOIANO - Lista scanner 2211.xlsx"
    info = R.parse_order_file(p)
    rows = {r["ean"]: r for r in info["rows"]}
    assert set(rows) == {"8015100572212", "8000036022017", "8004"}      # 只写要订的
    assert rows["8015100572212"]["qty"] == 2 and not rows["8015100572212"]["urgent"]
    assert rows["8000036022017"]["urgent"] and rows["8000036022017"]["reason"] == "Scaffale vuoto"
    assert "8003" in rows["8004"]["reason"]
    out = R.check_rows(info["rows"], [], "colli")
    assert {x["ean"]: x.get("urgent") for x in out}["8000036022017"] is True
    from openpyxl import load_workbook                      # 11-25 以前的文件表头是 URGENZA，照样认
    wb = load_workbook(p)
    assert wb.active["D1"].value == "NON IN GRIGLIA"
    wb.active["D1"] = "URGENZA"
    wb.save(p)
    assert {r["ean"]: r["urgent"] for r in R.parse_order_file(p)["rows"]}["8000036022017"]


def test_import_lists_places_file_marks_received_and_feedback(tmp_path, monkeypatch):
    import workspace as W
    BA.set_token("k" * 40)
    monkeypatch.setattr(SC, "_seen_path", lambda: str(tmp_path / "seen.json"))
    root = str(tmp_path / "ws")
    W.ensure(root)
    old = tmp_path / "ws" / W.LISTS / "FOIANO - vecchia.xlsx"
    SC.write_list_excel(LIST, str(old))
    posts = []
    s = FakeSession({("POST", "/api/erp/store-lists/7"): (200, lambda p, b: posts.append(b) or {"id": 7})})
    out = SC.import_lists({}, root, [LIST], session=s, tmp_dir=str(tmp_path / "tmp"), log=lambda *_: None)
    (sid, dst, moved), = out
    assert sid == 1 and dst.endswith("FOIANO - Lista scanner 2211.xlsx")
    assert moved and moved[0][0] == str(old) and not old.exists()     # 原来的挪进 _旧文件（撤销能放回）
    assert posts == [{"action": "received"}]
    # 确认比价后：最后定的箱数写回（少订 / 不订写备注）
    rows = [{"store": 1, "ean": "8015100572212", "fbox": 1}, {"store": 1, "ean": "8004", "fbox": 1},
            {"store": 2, "ean": "8000036022017", "fbox": 5}]
    monkeypatch.setattr(BA, "_call", lambda st, m, path, params=None, body=None, session=None, tok=None: posts.append(body) or {})
    assert SC.send_check_feedback({"booking": {"url": "https://x"}}, rows, [dst], log=lambda *_: None) == 1
    fb = posts[-1]["feedback"]["lines"]
    assert fb["8015100572212"] == {"boxes": 1, "note": "Ridotto da 2 a 1"}
    assert fb["8000036022017"] == {"boxes": 0, "note": "Non ordinato"}       # 门店 2 的不算
    assert fb["8004"] == {"boxes": 1} and "8001090213465" not in fb
    assert SC.send_check_feedback({"booking": {"url": "https://x"}}, rows, [dst], log=lambda *_: None) == 0   # 只发一次


def test_office_note_tells_store_why():
    assert SC.office_note({"list_reason": "Promozione o volantino", "fbox": 3, "sbox": 3}, 3, 3) == "Accettato · motivo: Promozione o volantino"
    assert SC.office_note({"transit_cut": 2, "fbox": 1, "sbox": 1}, 3, 1) == "Ridotto da 3 a 1: già in arrivo 2 colli"
    assert SC.office_note({"cover_now": 95.4, "fbox": 0, "sbox": 0}, 2, 0) == "Non ordinato: scorta per ~95 giorni"
    assert SC.office_note({"fbox": 1, "sbox": 2}, 2, 1) == "Ridotto da 2 a 1: deciso dall'ufficio acquisti"
    assert SC.office_note({"fbox": 4, "sbox": 2}, 2, 4) == "Aumentato da 2 a 4 dall'ufficio acquisti"
    assert SC.office_note({"list_reason": "URGENTE", "urgent": True, "fbox": 2, "sbox": 2}, 2, 2) in ("", "Accettato · motivo: URGENTE")
    assert SC.office_note(None, 2, 2) == ""


def _hist_db(monkeypatch):
    """sqlite 模拟：caricolistalist（到货）+ vendite_list_1（销量）"""
    import sqlite3
    import pandas as pd
    import reorder_service as R
    con = sqlite3.connect(":memory:")
    con.create_function("YEAR", 1, lambda s: int(str(s)[:4]))
    con.create_function("MONTH", 1, lambda s: int(str(s)[5:7]))
    con.execute("CREATE TABLE caricolistalist (CodArticolo INT, Cdata TEXT, Quantita REAL, Tipo TEXT, Fornitore INT, Negozio INT)")
    con.execute("CREATE TABLE vendite_list_1 (CodArticolo INT, Data TEXT, Qta REAL)")
    rows = [(1, "2026-11-12 09:00:00", 12, "CA", 7, 1), (1, "2026-11-12 15:00:00", 12, "CA", 7, 1),   # 同一天两行 → 24
            (1, "2026-10-15 10:00:00", 6, "CI", 4, 1),          # 从 S.MARCO 调过来
            (1, "2026-09-01 10:00:00", 6, "CI", 0, 1),          # 从仓库
            (1, "2026-11-20 10:00:00", -3, "CA", 7, 1),         # 退货：不算
            (1, "2026-11-20 10:00:00", 30, "CA", 7, 2),         # 别的门店：不算
            (1, "2026-11-21 10:00:00", 5, "UI", 7, 1),          # 调出：不算
            (2, "2024-12-20 10:00:00", 48, "CA", 7, 1)]         # 2025-01-08 以前（记录是错的）+ 一年以前：不算
    con.executemany("INSERT INTO caricolistalist VALUES (?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO vendite_list_1 VALUES (?,?,?)",
                    [(1, "2026-11-02", 3), (1, "2026-11-20", 2), (1, "2026-07-05", 9), (1, "2025-10-30", 50), (3, "2026-11-01", 1)])
    con.commit()

    def fake(q, db, params, tries=2, wait=3):
        p = tuple(str(x) if hasattr(x, "isoformat") else x for x in params)
        return pd.read_sql_query(q.replace("%s", "?"), con, params=p)
    monkeypatch.setattr(R, "read_sql_retry", fake)


def test_store_history_arrivals_months_and_other_stores(monkeypatch):
    from datetime import date
    _hist_db(monkeypatch)
    h = SC.store_history(object(), 1, ["1", "2", "3"], today=date(2026, 11, 27), sup_names={7: "MELONI"})
    assert h["1"]["arr"] == [[20261112, 24.0, 0, "MELONI", 2.0], [20261015, 6.0, 1, "S.MARCO"], [20260901, 6.0, 1, "BMS"]]
    m = h["1"]["months"]
    assert len(m) == 12 and m[-1] == 5 and m[-5] == 9 and sum(m) == 14       # 2025-10 在 12 个月以外
    assert "2" not in h                                                       # 只有错的老记录
    assert h["3"]["arr"] == [] and h["3"]["months"][-1] == 1
    row = SC.store_row("8001", {"stock": 6, "s30": 5}, 0, None, 2.5, None, hist=h["1"])
    assert len(row) == 14 and row[12][0][0] == 20261112 and row[13][-1] == 5
    stores = {1: [list(row)], 2: [["8001", 30.0], ["8002", 0.0]], 4: [["8001", 0.0]]}
    SC.add_others(stores)
    assert stores[1][0][14] == [["MARLIA", 30.0], ["S.MARCO", 0.0]]
    assert stores[2][0][14] == [["FOIANO", 6.0], ["S.MARCO", 0.0]]
    assert stores[2][1][14] is None                                           # 别家都没货：不发


def test_volantino_published_with_catalog_until_it_ends(tmp_path):
    from datetime import date
    res = {"rows": [
        {"store": 1, "ean": "08001", "price": 1.49, "expected": 54.0, "basis": "hist",
         "periods": [("OTT 26", "2026-10-15", "2026-11-01", 2.5, 1.0)], "warn": []},
        {"store": 2, "ean": "8009", "price": 4.99, "expected": 14.0, "basis": "est", "periods": [], "warn": ["金额 €1,200"]}]}
    v = SC.volantino_rows(res)
    assert v[1] == [["8001", 1.49, 54.0, 0, "OTT 26", 45, 0]] and v[2][0][3] == 1 and v[2][0][6] == 1
    path = str(tmp_path / "vol.json")
    SC.save_volantino({"on": True, "name": "DIC 26", "start": "2026-12-03", "end": "2026-12-20", "days": 18,
                       "stores": {str(k): x for k, x in v.items()}}, path)
    vol = SC.active_volantino(date(2026, 11, 28), path)
    stores = {1: [["8001", 6.0, 20.0]]}
    meta, out = SC.with_volantino({}, {"week": "x"}, stores, vol=vol)
    assert meta["volantino"]["items"] == 2 and meta["volantino"]["name"] == "DIC 26"
    assert out[1][0][15] == [1.49, 54.0, 0, "OTT 26", 45, 0] and len(stores[1][0]) == 3     # 缓存里的行不改
    assert out[2][0][0] == "8009" and out[2][0][15][0] == 4.99                              # 这家店没有门店数据行：新加一行
    assert SC.active_volantino(date(2026, 12, 21), path) is None                            # 结束了不再发
    SC.set_volantino_on(False, path)
    assert SC.active_volantino(date(2026, 11, 28), path) is None
    meta, out = SC.with_volantino({}, {}, stores, vol=None)
    assert meta["volantino"] is None and out is stores                                       # 扫码枪：「还没有上传」
