"""2026-11-21：DETERSIVO 工作文件夹搬进同步盘、几台电脑共用；订单跟踪 BMS 写凑单门店、确认预警按工作日"""
import json
import os
from datetime import date, datetime, timedelta

import griglia as G
import order_tracking as OT
import service
import workspace as W


def test_find_shared_root_on_any_drive_or_home(tmp_path):
    d = tmp_path / "E"
    (d / "SynologyDrive" / "SynologyDrive" / "Ordine DETERSIVI").mkdir(parents=True)
    assert W.find_shared_root(home=str(tmp_path / "nohome"), drives=[str(d)]).endswith("Ordine DETERSIVI")
    assert W.find_shared_root(home=str(tmp_path / "nohome"), drives=[str(tmp_path / "X")]) is None


def test_who_works_shows_other_pc_recent_only(tmp_path, monkeypatch):
    root = str(tmp_path)
    now = datetime(2026, 11, 21, 12, 0)
    monkeypatch.setenv("COMPUTERNAME", "PC-B")
    W.mark_working(root, "Pamela", "审核清单", now=now - timedelta(minutes=10))
    monkeypatch.setenv("COMPUTERNAME", "PC-A")
    W.mark_working(root, "Lucas", "比价分配", now=now)
    busy = W.who_works(root, now=now)                         # 自己（PC-A）不算
    assert [(u, pc, w) for u, pc, w, _ in busy] == [("Pamela", "PC-B", "审核清单")]
    assert W.who_works(root, now=now + timedelta(minutes=45)) == []          # 30 分钟没动 = 不在做
    assert W.who_works(root, now=now + timedelta(days=2), minutes=7 * 24 * 60)[0][0] == "Pamela"


def test_latest_result_folder_and_paths_rebased(tmp_path):
    root = tmp_path / "Ordine DETERSIVI"
    for day, store in (("2026-11-14", "FOIANO"), ("2026-11-20", "MARLIA")):
        b = root / W.RESULTS / day / store
        (b / "_备份").mkdir(parents=True)
        (b / "ORDINE").mkdir()
        (b / "ORDINE" / "o.xlsx").write_bytes(b"x")
        st = {"ok": True, "suppliers": [], "state": {
            "ordine_file": os.path.join("Z:", os.sep, "Sync", "Ordine DETERSIVI", W.RESULTS, day, store, "ORDINE", "o.xlsx"),
            "assignment": {}, "baseline": {}, "sources": {}, "rows": []}}
        (b / "_备份" / "last_allocation.json").write_text(json.dumps(st), encoding="utf-8")
    new = W.latest_result_folder(str(root))
    assert new.endswith(os.path.join("2026-11-20", "MARLIA"))
    data = service.load_state(new)        # 同事电脑上同步盘在别的位置：路径换成这台电脑的
    assert data and data["state"]["ordine_file"] == os.path.join(new, "ORDINE", "o.xlsx")


def test_seen_merged_across_pcs(tmp_path):
    a = {"MELONI": {"since": "2026-11-01", "eans": {"1": ["2026-11-01", "2026-11-14"]}}}
    b = {"MELONI": {"since": "2026-10-20", "eans": {"1": ["2026-11-03", "2026-11-20"], "2": ["2026-11-20", "2026-11-20"]}},
         "PETRILLO": {"since": "2026-11-20", "eans": {}}}
    m = G.merge_seen(a, b)
    assert m["MELONI"]["since"] == "2026-10-20" and m["MELONI"]["eans"]["1"] == ["2026-11-01", "2026-11-20"]
    assert "2" in m["MELONI"]["eans"] and "PETRILLO" in m
    shared = str(tmp_path / "data")
    G.save_seen_all(str(tmp_path / "local.json"), b, shared, pc="PC-B")
    assert G.load_seen_all(str(tmp_path / "local2.json"), shared)["PETRILLO"]["since"] == "2026-11-20"


def test_bms_label_and_confirmation_warning_in_workdays(tmp_path):
    st = OT.Store(str(tmp_path), me="t", host="h")
    res = OT.register_drafts(st, "store_orders", [{"store": "BMS", "supplier": "ACME", "amount": 120, "colli": 3,
                                                  "file": "f.xlsx", "bms_of": ["5", "1", "2"]}], today=date(2026, 11, 20))
    assert res["new"] == 1
    oid = next(iter(st.orders))
    r = OT.rows(st, today=date(2026, 11, 20))[0]
    assert r["store_name"] == "BMS (FO, MA, SC)" and r["store"] == "BMS"
    # 周五发出：周一 1 个工作日（不黄）、周二 2 个（黄）、周四 4 个（红）
    st.set_many([(oid, {"draft": None, "date": "2026-11-20"})])
    lv = lambda d: OT.rows(st, today=d)[0]
    assert lv(date(2026, 11, 23))["wait_level"] == 0 and not lv(date(2026, 11, 23))["pending"]
    assert lv(date(2026, 11, 24))["wait_level"] == 1 and lv(date(2026, 11, 24))["pending"]
    assert lv(date(2026, 11, 26))["wait_level"] == 2
    assert OT.workdays_between(date(2026, 11, 20), date(2026, 11, 22)) == 0      # 周六周日不算


def test_register_store_orders_keeps_bms_members(tmp_path):
    written = [("f.xlsx", "ACME", "FOIANO", 2, 50.0, "direct", "s"),
               ("f.xlsx", "ACME", "BMS", 3, 120.0, "direct", "s", ("2", "4"))]
    OT.register_store_orders({"tracking": {"folder": str(tmp_path)}}, written)
    st = OT.Store(str(tmp_path), me="t", host="h").reload()
    names = sorted(r["store_name"] for r in OT.rows(st))
    assert names == ["BMS (MA, SM)", "FOIANO"]
