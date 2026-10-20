"""门店之间调货：要的件数远小于一箱时，从别的门店调多出来的货，少订一箱"""
import transfers as TR


def _need(i, store, key, need, pack, colli, price):
    return {"id": i, "store": store, "key": key, "need": need, "pack": pack, "colli": colli, "price": price}


def test_surplus_keeps_one_cycle():
    assert TR.surplus({"inv": 30, "daily": 1, "cycle": 10}) == 20
    assert TR.surplus({"inv": 5, "daily": 1, "cycle": 10}) == 0
    assert TR.surplus({"inv": -4, "daily": 0, "cycle": 30}) == 0           # 负库存当 0
    assert TR.surplus({"inv": 7, "daily": 0, "cycle": 30}) == 7            # 不动销：都可以调


def test_options_waste_and_all():
    assert TR.options(3, 12, 1, "waste") == [(3, 0)]                       # 要 3 件、一箱 12：调 3 件，不订
    assert TR.options(10, 12, 1, "waste") == []                            # 只多 2 件，不值得调
    assert TR.options(14, 12, 2, "waste") == [(2, 1)]                      # 零头 2 件调，订 1 箱
    assert TR.options(14, 12, 2, "all") == [(14, 0), (2, 1)]               # 都要：能全调就全调


def test_plan_min_value_and_grouping():
    needs = [_need("a", 1, "X", 3, 12, 1, 2.0), _need("b", 2, "Y", 2, 12, 1, 5.0),
             _need("c", 2, "Z", 2, 6, 1, 25.0)]
    # 3 号店 X、Y 都多；4 号店 Z 多（2 件 × €25 = €50）
    avail = {(3, "X"): 10, (3, "Y"): 10, (4, "Z"): 5, (4, "X"): 3}
    moves, new = TR.plan(needs, avail, {"min_value": 50})
    # 4 号店：Z 2 件 €50，顺便把 X 3 件也一起拣（同一家店一次拣完）；3 号店剩下的 Y 只有 €10，不够 €50
    assert sorted((m["from"], m["to"], m["key"], m["pieces"]) for m in moves) == [(4, 1, "X", 3), (4, 2, "Z", 2)]
    assert new == {"a": 0, "c": 0}
    moves, new = TR.plan(needs, avail, {"min_value": 10})
    got = sorted((m["from"], m["key"]) for m in moves)
    assert (3, "Y") in got and new["b"] == 0


def test_plan_far_and_own_store():
    needs = [_need("a", 1, "X", 3, 12, 1, 30.0), _need("b", 3, "X", 3, 12, 1, 30.0)]
    avail = {(1, "X"): 20, (3, "X"): 2}
    moves, _ = TR.plan(needs, avail, {"min_value": 10}, far=[1])
    assert moves == []                    # 1 号店远：不调出也不调入；3 号店自己要，2 件也不够


def test_plan_shared_stock_not_overused():
    needs = [_need("a", 1, "X", 4, 12, 1, 10.0), _need("b", 2, "X", 4, 12, 1, 10.0)]
    moves, new = TR.plan(needs, {(3, "X"): 5}, {"min_value": 10})
    assert len(moves) == 1 and sum(m["pieces"] for m in moves) == 4          # 3 号店只有 5 件，只够一家


def test_store_orders_apply_transfers():
    import store_orders as S
    sc = {"path": "p", "name": "DOPLA", "items": [{"row": 5, "code": "100", "ean": "8", "desc": "PIATTI", "pack": 12,
                                                   "price": 6.0}], "sheets": {2: "MARLIA", 3: "S.FILIPPO"}}
    demand = {2: {"100": {"sugg": 12, "need": 3, "inv": 0, "daily": 0.1, "cycle": 30}},
              3: {"100": {"sugg": 0, "need": 0, "inv": 40, "daily": 0.5, "cycle": 30}}}
    calc = {"schede": [sc], "demand": demand, "base": {"p": {2: {5: 1}}}, "stores": [2, 3]}
    out = S.apply_transfers(calc, {"min_value": 10}, far_stores=[1])
    assert [(t["from"], t["to"], t["pieces"], t["value"]) for t in out] == [(3, 2, 3, 18.0)]
    assert calc["base"] == {"p": {2: {}}} and calc["base_orig"] == {"p": {2: {5: 1}}}
    S.apply_transfers(calc, {"enabled": False})
    assert calc["base"] == {"p": {2: {5: 1}}} and calc["transfers"] == []


def test_snapshot_roundtrip_and_wholesale(tmp_path):
    """批发商比价：检查门店清单时存的各门店库存快照 → 比价时算调货、够卖天数"""
    import os
    import reorder_service as RS
    import service
    root = tmp_path / "root"
    (root / "ORDINE").mkdir(parents=True)
    main = str(root / "ORDINE" / "确认订单_MARLIA.xlsx")
    snap = {2: {"1": {"inv": 0, "daily": 0.1, "cycle": 30, "ean": "800001"}},
            4: {"1": {"inv": 40, "daily": 0.5, "cycle": 30, "ean": "800001"}}}
    RS.save_snapshot(main, 2, snap)
    got = RS.load_snapshot(main)
    assert got["store"] == 2 and got["by_ean"]["800001"]["stores"][4]["inv"] == 40
    assert RS.load_snapshot(str(root / "ORDINE" / "别的.xlsx")) is None
    rows = [service._price_row_pieces(2, 3, {"MEGA": 1.0, "MELONI": 1.2}, {"MEGA": 12, "MELONI": 12}, "800001", "A")]
    assert service.attach_stock(rows, got) == 1 and rows[0]["daily"] == 0.1
    ts = service.wholesale_transfers(rows, ["MEGA", "MELONI"], got, {"store_orders": {"transfer": {"min_value": 1}}})
    assert [(t["from"], t["pieces"], t["colli_before"], t["colli_after"]) for t in ts] == [(4, 3, 1, 0)]
    assert rows[0]["qty"] == 0 and rows[0]["qty_orig"] == 3                  # 主订单件数减掉调货的
    assert ts[0]["supplier"] == "MEGA" and abs(ts[0]["value"] - 3.0) < 1e-9
    # 关掉调货 / 没有快照 → 不调
    rows2 = [service._price_row_pieces(2, 3, {"MEGA": 1.0}, {"MEGA": 12}, "800001", "A")]
    assert service.wholesale_transfers(rows2, ["MEGA"], got, {"store_orders": {"transfer": {"enabled": False}}}) == []
    assert service.wholesale_transfers(rows2, ["MEGA"], None, {}) == []


def test_only_checked_main_orders_are_used(tmp_path):
    """主订单只能由检查门店清单生成：没有库存快照的（旧系统的文件）不能用来比价"""
    import os
    import time
    import reorder_service as RS
    import service
    folder = tmp_path / "root" / "ORDINE"
    folder.mkdir(parents=True)
    assert RS.checked_main_orders(str(tmp_path / "nope")) == ([], [])
    old = folder / "ordine_vecchio.xlsx"
    old.write_bytes(b"x")
    (folder / "~$lock.xlsx").write_bytes(b"x")
    assert RS.checked_main_orders(str(folder)) == ([], ["ordine_vecchio.xlsx"])
    snap = {2: {"1": {"inv": 0, "daily": 0.1, "cycle": 30, "ean": "800001"}}}
    for n in ("确认订单_A.xlsx", "确认订单_B.xlsx"):
        p = folder / n
        p.write_bytes(b"x")
        RS.save_snapshot(str(p), 2, snap)
    t = time.time()
    os.utime(folder / "确认订单_A.xlsx", (t, t))
    os.utime(folder / "确认订单_B.xlsx", (t - 100, t - 100))
    assert RS.checked_main_orders(str(folder)) == (["确认订单_A.xlsx", "确认订单_B.xlsx"], ["ordine_vecchio.xlsx"])
    # 旧主订单直接拿去比价：不处理、文件不动
    logs = []
    s = service.run_process(str(old), str(tmp_path / "root" / "eliminare"), str(tmp_path / "root" / "distribuire"),
                            logs.append)
    assert s["error"] and not s["ok"] and old.read_bytes() == b"x"


def test_wholesale_transfer_status():
    """批发商比价界面上说清楚这次会不会算门店间调货（离得远的门店不参与）"""
    import service
    snap = {"store": 2, "by_ean": {}}
    assert service.transfer_status(None, {})[0] == "no_snapshot"
    assert service.transfer_status(snap, {})[0] == "on"
    assert service.transfer_status({"store": 1}, {})[0] == "far"                       # 默认 FOIANO 离得远
    assert service.transfer_status({"store": 1}, {"store_orders": {"fill": {"far_stores": []}}})[0] == "on"
    assert service.transfer_status(snap, {"store_orders": {"transfer": {"enabled": False}}})[0] == "off"
    code, text = service.transfer_status(snap, {})
    assert "FOIANO" not in text.split("从")[1].split("调")[0]                          # 远的门店不在调出名单里


def test_batch_transfers_across_store_lists():
    """几家门店的清单一起检查：先用别的门店多出来的货（调货），再订剩下的"""
    import reorder_service as RS
    rows = {2: [{"code": "100", "ean": "8001", "name": "PIATTI", "final": 3, "pack": 12, "price": 2.0, "in_db": True},
                {"code": "200", "ean": "8002", "name": "BICCHIERI", "final": 30, "pack": 10, "price": 1.0, "in_db": True}],
            3: [{"code": "200", "ean": "8002", "name": "BICCHIERI", "final": 2, "pack": 10, "price": 1.0, "in_db": True}],
            1: [{"code": "100", "ean": "8001", "name": "PIATTI", "final": 2, "pack": 12, "price": 2.0, "in_db": True}]}
    snap = {2: {"100": {"inv": 0, "daily": 0.1, "cycle": 30}, "200": {"inv": 0, "daily": 1, "cycle": 30}},
            3: {"100": {"inv": 0, "daily": 0, "cycle": 30}, "200": {"inv": 100, "daily": 1, "cycle": 30}},
            4: {"100": {"inv": 40, "daily": 0.5, "cycle": 30}, "200": {"inv": 90, "daily": 1, "cycle": 30}},
            1: {"100": {"inv": 0, "daily": 0.1, "cycle": 30}}}
    moves, new = RS.plan_batch_transfers(rows, snap, {"min_value": 1}, far=[1])
    by = {(m["to"], m["code"]): m for m in moves}
    # MARLIA 要 3 个 PIATTI（一箱 12）：从 S.MARCO 调 3 件，不用订
    assert by[(2, "100")]["from"] == 4 and by[(2, "100")]["pieces"] == 3 and new[(2, "100")] == 0
    # S.FILIPPO 自己也要订 BICCHIERI → 不能调出给 MARLIA；它要的 2 件从 S.MARCO 调
    assert by[(3, "200")]["from"] == 4 and new[(3, "200")] == 0
    assert all(not (m["from"] == 3 and m["code"] == "200") for m in moves)
    # FOIANO 离得远：不调进
    assert (1, "100") not in by
    # 调货关掉：什么都不调
    assert RS.plan_batch_transfers(rows, snap, {"enabled": False}) == ([], {})


def test_snapshot_keeps_planned_transfers(tmp_path):
    import reorder_service as RS
    main = str(tmp_path / "ORDINE" / "确认订单_MARLIA.xlsx")
    mv = [{"from": 4, "to": 2, "code": "100", "pieces": 3, "value": 6.0, "to_days": float("inf")}]
    RS.save_snapshot(main, 2, {2: {"100": {"inv": 0, "daily": 0.1, "cycle": 30, "ean": "8001"}}},
                     transfers=mv, transfers_file="x.xlsx")
    got = RS.load_snapshot(main)
    assert got["planned_transfers"][0]["from"] == 4 and got["transfers_file"] == "x.xlsx"
    RS.save_snapshot(main, 2, {2: {}})
    assert "planned_transfers" not in RS.load_snapshot(main)       # 单独检查（旧做法）：比价时再算


def test_batch_folders(tmp_path):
    """检查清单确认时自动建：根目录\\批发商比价\\日期\\门店\\{ORDINE, eliminare, distribuire}"""
    import os
    from datetime import datetime
    import reorder_service as RS
    root = str(tmp_path / "CasaTe")
    day = datetime(2026, 10, 20)
    f = RS.batch_store_folder(root, 2, day)
    assert f == os.path.join(root, "批发商比价", "2026-10-20", "MARLIA")
    RS.make_store_folder(f)
    assert sorted(os.listdir(f)) == ["ORDINE", "distribuire", "eliminare"]
    assert RS.batch_root_of(f) == root and RS.batch_root_of(RS.batch_folder(root, day)) == root
    assert RS.batch_root_of(root) is None and RS.batch_root_of("") is None


def test_transfer_status_planned():
    import service
    snap = {"store": 2, "planned_transfers": [{"from": 4, "to": 2}, {"from": 2, "to": 3}]}
    code, text = service.transfer_status(snap, {})
    assert code == "planned" and "1" in text
