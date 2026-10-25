"""订货建议：订货清单解析、门店识别、按件数订货换算"""
import sys
import types

if "win32com" not in sys.modules:
    w = types.ModuleType("win32com"); c = types.ModuleType("win32com.client"); w.client = c
    sys.modules["win32com"] = w; sys.modules["win32com.client"] = c

import reorder_service as R


def _xlsx(tmp_path, rows, title="ITO-INV-369", pre=()):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for r in pre:
        ws.append(r)
    for r in rows:
        ws.append(r)
    p = tmp_path / "ordine FOIANO.xlsx"
    wb.save(p)
    return str(p)


def test_parse_store_list_by_headers(tmp_path):
    p = _xlsx(tmp_path, [["商品编号", "商品条码", "商品名称", "商家编号", "商家编码", "数量", "入货价格 ", "销售价格 ", "库存"],
                         ["552", "4003790001970", "VILEDA", 1120, "X", 1, 0.6, 1.3, -10],   # 3 位编号也要认对
                         ["30450", 8001940004044, "PRO CLEAN", 1120, None, 2, 0.7, 1.4, ""]])
    info = R.parse_order_file(p)
    assert info["fields"]["code"] == "商品编号" and info["fields"]["ean"] == "商品条码"
    assert [x["code"] for x in info["rows"]] == ["552", "30450"]
    assert info["rows"][1]["ean"] == "8001940004044" and info["rows"][1]["qty"] == 2
    assert R.guess_store(p) == 1


def test_parse_header_not_on_first_row_and_only_ean(tmp_path):
    p = _xlsx(tmp_path, [["Codice EAN", "Descrizione", "Quantità"], ["8001940004044", "X", 3]],
              title="GRIGLIA", pre=[["Ordine settimanale"], []])
    info = R.parse_order_file(p)
    assert info["header_row"] == 3 and "code" not in info["fields"]
    assert info["rows"][0]["ean"] == "8001940004044" and info["warnings"]


def test_pieces_row_boxes_per_supplier():
    from service import _price_row_pieces, _order_qty
    r = _price_row_pieces(2, 10, {"A": 1.0, "M": 1.1}, {"A": 24, "M": 6})
    assert r["colli"] == {"A": 1, "M": 2}               # 10 件：A 一箱 24 件 → 1 箱；M 一箱 6 件 → 2 箱
    assert abs(r["prices"]["A"] * 10 - 24 * 1.0) < 1e-9  # 金额含凑整多买的件：A 24 件 × 1.0
    assert abs(r["prices"]["M"] * 10 - 12 * 1.1) < 1e-9  # M 12 件 × 1.1 = 13.2，比 A 便宜
    assert _order_qty(r, "M") == 2


def test_check_rows_verdicts_and_units():
    file_rows = [{"code": "1", "ean": "a", "name": "A", "qty": 2},     # 2 箱 × 6 = 12 件
                 {"code": "2", "ean": "b", "name": "B", "qty": 1},
                 {"code": "3", "ean": "c", "name": "C", "qty": 1},
                 {"code": "1", "ean": "a", "name": "A", "qty": 1},     # 重复行合并 → 3 箱 = 18 件
                 {"code": "", "ean": "z", "name": "Z", "qty": 4}]      # 数据库没有
    res = [{"code": "1", "ean": "a", "name": "A", "pack": 6, "suggested": 24},
           {"code": "2", "ean": "b", "name": "B", "pack": 6, "suggested": 0},
           {"code": "3", "ean": "c", "name": "C", "pack": 0, "suggested": 1}]
    rows = R.check_rows(file_rows, res, "colli")
    assert [(x["verdict"], x["file_pcs"], x["final"]) for x in rows] == \
        [("增加", 18, 24), ("不订", 6, 0), ("不变", 1, 1), ("数据库没有", 4, 4)]
    rows = R.check_rows(file_rows, res, "pcs")
    assert rows[0]["file_pcs"] == 3 and rows[0]["verdict"] == "增加"
    assert R.qty_unit("COLLI") == "colli" and R.qty_unit("数量") == "colli"     # 门店清单一律按箱


def _neg_db():
    import pandas as pd
    e = pd.DataFrame({"CodArticolo": [], "sales_qty": []})

    class DB:
        def query_reorder_data(self, *a):
            return {"inventory": pd.DataFrame({"CodArticolo": [1, 2], "store_inventory": [-50, 3],
                                               "total_inventory": [-10, 8]}),
                    "suppliers": pd.DataFrame(), "sales_1": e, "sales_2": e, "sales_3": e, "sales_4": e,
                    "margin_1": e, "margin_2": e, "margin_3": e}
    return DB()


def test_negative_stock_counts_as_zero(monkeypatch):
    """新算法：负库存计算时当 0，表里显示原值"""
    from fenpei import models
    seen = {}

    def fake(inventory_df, *a, **k):
        seen["inv"] = inventory_df
        return inventory_df
    monkeypatch.setattr(models.ProductDataProcessor, "process_reorder_data", staticmethod(fake))
    monkeypatch.setattr(R, "apply_v2", lambda db, sid, df, algo, *a, **k: df)
    out = R.compute_suggestions(_neg_db(), 1, ["1", "2"], algo={"engine": "v2"})
    assert list(seen["inv"]["store_inventory"]) == [0, 3]          # 计算用 0
    assert list(out["store_inventory_raw"]) == [-50, 3]             # 表里显示原来的负数
    rows = R.to_rows(out)
    assert sorted(x["store_inv"] for x in rows) == [-50, 3]
    assert list(seen["inv"]["total_inventory"]) == [0, 8]


def test_old_algorithm_keeps_inventory_as_is(monkeypatch):
    """原程序算法：库存原样交给原程序（负库存由原程序自己按 0 算，见下一个测试）"""
    from fenpei import models
    seen = {}

    def fake(inventory_df, *a, **k):
        seen["inv"] = inventory_df
        return inventory_df
    monkeypatch.setattr(models.ProductDataProcessor, "process_reorder_data", staticmethod(fake))
    for algo in (None, {"engine": "v1", "service": 0.99}):
        R.compute_suggestions(_neg_db(), 1, ["1", "2"], algo=algo)
        assert list(seen["inv"]["store_inventory"]) == [-50, 3]
        assert list(seen["inv"]["total_inventory"]) == [-10, 8]


def _proc(inv_rows, sales_1, sales_3, packaging=12):
    import pandas as pd
    from fenpei.models import ProductDataProcessor
    inv = pd.DataFrame([dict(CodArticolo=c, store_inventory=i, total_inventory=i, qtasmallbox=packaging,
                             PrezzoVedi1=1, PrezzoForn=1, CodIva=22) for c, i in inv_rows])
    mk = lambda d: pd.DataFrame({"CodArticolo": list(d), "sales_qty": list(d.values())})
    e = pd.DataFrame({"CodArticolo": [], "sales_qty": []})
    return ProductDataProcessor.process_reorder_data(
        inv, pd.DataFrame({"CodArticolo": [], "IDFornitore": []}),
        {"df_sales_1": mk(sales_1), "df_sales_2": mk(sales_3), "df_sales_3": mk(sales_3), "df_sales_4": mk(sales_3)},
        {}, 10.0, [30, 60, 90, 180], reorder_cycle=30)


def test_old_algorithm_counts_negative_stock_as_zero():
    """原程序本身：订货量按 max(0, 库存) 算 → 库存 -50 和库存 0 的建议量一样"""
    neg = _proc([(1, -50)], {1: 30}, {1: 90})
    zero = _proc([(1, 0)], {1: 30}, {1: 90})
    assert neg.loc[0, "suggested_order_qty"] == zero.loc[0, "suggested_order_qty"] > 0
    assert neg.loc[0, "store_inventory"] == -50                     # 原值保留


def test_fallback_tier_uses_30_day_equivalent():
    # 商品 1：30 天卖 0，90 天卖 30（≈ 每月 10）；平均 10 → 应该是 B，不是 A
    df = _proc([(1, 0)], {}, {1: 30})
    assert df.loc[0, "sales_tier"] == "B"


def test_no_db_pack_rounding_when_disabled():
    df = _proc([(1, 0)], {1: 30}, {1: 90}, packaging=12)
    assert df.loc[0, "suggested_order_qty"] % 12 == 0        # 原程序：按 12 取整
    df = _proc([(1, 0)], {1: 30}, {1: 90}, packaging=0)
    assert 30 <= df.loc[0, "suggested_order_qty"] < 36       # 不取整：约 30 天 × 1 件/天 × 波动系数


def test_apply_v2_overrides_columns():
    import pandas as pd
    import forecast as F
    df = pd.DataFrame({"CodArticolo": [1, 2, 3], "store_inventory": [2, 40, 0], "total_inventory": [2, 40, 5],
                       "PrezzoVedi1": [1, 1, 1]})
    weekly = {1: [7.0] * 60, 2: [7.0] * 60, 3: [0.0] * 60}
    out = R.apply_v2(None, 1, df, dict(F.DEFAULT_PARAMS), weekly=weekly, revenue={1: 100, 2: 100, 3: 0},
                     on_order={1: 3})
    assert out.loc[0, "suggested_order_qty"] > 0 and out.loc[0, "decision_text"].startswith("🔴")
    assert out.loc[1, "suggested_order_qty"] == 0
    assert out.loc[2, "suggested_order_qty"] == 0 and "不动销" in out.loc[2, "decision_text"]
    # 在途 3 件会从建议量里扣掉
    no_oo = R.apply_v2(None, 1, df, dict(F.DEFAULT_PARAMS), weekly=weekly, revenue={1: 100, 2: 100, 3: 0})
    assert no_oo.loc[0, "suggested_order_qty"] - out.loc[0, "suggested_order_qty"] == 3
    assert list(out["sales_tier"]) == ["A", "A", "C"]          # 两个各占 50% 销售额 → 都在前 80%


def test_apply_v2_supplier_cycles():
    """新算法按配置文件周期：每行用自己的订货周期，周期长的建议量更多"""
    import pandas as pd
    import forecast as F
    df = pd.DataFrame({"CodArticolo": [1, 2], "store_inventory": [0, 0], "total_inventory": [0, 0],
                       "PrezzoVedi1": [1, 1]})
    weekly = {1: [7.0] * 60, 2: [7.0] * 60}
    algo = dict(F.DEFAULT_PARAMS, review_days=7)
    out = R.apply_v2(None, 1, df, algo, weekly=weekly, revenue={1: 100, 2: 100}, cycles=[7, 60])
    assert list(out["reorder_cycle_used"]) == [7, 60]
    assert out.loc[1, "suggested_order_qty"] > out.loc[0, "suggested_order_qty"] + 40
    same = R.apply_v2(None, 1, df, algo, weekly=weekly, revenue={1: 100, 2: 100})
    assert list(same["reorder_cycle_used"]) == [7, 7]


def test_old_algorithm_per_supplier_groups_match_original():
    """原程序算法按供应商分组：结果和原程序一次只算一个 SCHEDA 完全一样（等级在同一家供应商里比）"""
    import pandas as pd

    def mk_data(codes):
        inv = pd.DataFrame([dict(CodArticolo=c, store_inventory=0, total_inventory=0, qtasmallbox=1, PrezzoVedi1=1,
                                 PrezzoForn=1, CodIva=22) for c in codes])
        sales = {1: 300, 2: 200, 3: 100, 11: 6, 12: 3, 13: 1}       # 供应商甲卖得多，乙卖得少
        s = pd.DataFrame({"CodArticolo": codes, "sales_qty": [sales[c] for c in codes]})
        m = pd.DataFrame({"CodArticolo": codes, "total_guadagno": [1.0] * len(codes), "total_net_sales": [2.0] * len(codes)})
        return {"inventory": inv, "suppliers": pd.DataFrame({"CodArticolo": codes, "IDFornitore": [501 if c < 10 else 502 for c in codes]}),
                "sales_1": s, "sales_2": s, "sales_3": s, "sales_4": s, "margin_1": m, "margin_2": m, "margin_3": m}

    class DB:
        def query_reorder_data(self, store, codes, *a):
            return mk_data([int(c) for c in codes])

    a, b = ["1", "2", "3"], ["11", "12", "13"]
    grouped = R.compute_suggestions(DB(), 1, a + b, algo={"engine": "v1"}, groups=[a, b], use_supplier_cycles=False)
    alone = pd.concat([R.compute_suggestions(DB(), 1, g, algo={"engine": "v1"}, use_supplier_cycles=False) for g in (a, b)],
                      ignore_index=True)
    cols = ["CodArticolo", "sales_tier", "decision_text", "suggested_order_qty"]
    assert grouped[cols].sort_values("CodArticolo").reset_index(drop=True).equals(
        alone[cols].sort_values("CodArticolo").reset_index(drop=True))
    tiers = dict(zip(grouped["CodArticolo"], grouped["sales_tier"]))
    mixed = R.compute_suggestions(DB(), 1, a + b, algo={"engine": "v1"}, use_supplier_cycles=False)
    mixed_t = dict(zip(mixed["CodArticolo"], mixed["sales_tier"]))
    assert tiers[11] != mixed_t[11]          # 混在一起算时，乙的畅销品会被甲压成低等级


def test_to_rows_cover_inputs():
    """库存够卖 / 订后够卖用的库存负数当 0；原程序算法没有日均时用最近一个周期销量 ÷ 天数"""
    import pandas as pd
    import store_orders as SO
    df = pd.DataFrame([{"CodArticolo": 1, "store_inventory": -13, "sales_1": 23, "sales_3": 88},
                       {"CodArticolo": 2, "store_inventory": 20, "sales_1": 0, "sales_3": 45, "daily_rate": None},
                       {"CodArticolo": 3, "store_inventory": 5, "sales_1": 30, "daily_rate": 2.0}])
    rows = {x["code"]: x for x in R.to_rows(df, days_list=[30, 60, 90, 180])}
    assert rows["1"]["inv0"] == 0 and abs(rows["1"]["daily0"] - 23 / 30) < 1e-9
    assert SO.cover_days({"inv": rows["1"]["inv0"], "daily": rows["1"]["daily0"]}) == 0     # 不会是负数
    assert abs(rows["2"]["daily0"] - 45 / 90) < 1e-9
    assert rows["3"]["daily0"] == 2.0


def test_reason_text():
    """「原因」列：一句话说清楚为什么订 / 不订、订多少"""
    assert "不用订" in R.reason_text(60, 1, 30, 0)
    assert "够卖 60 天" in R.reason_text(60, 1, 30, 0)
    t = R.reason_text(-5, 1, 30, 36)
    assert "按 0 算" not in t and "订 36 件" in t and "约卖 30 件" in t      # 负库存一律当 0，不另外说明
    assert "目标库存 40" in R.reason_text(4, 1, 30, 36, target=40)
    assert "没有销量" in R.reason_text(10, 0, 30, 0)
    assert "数据库里没有" in R.reason_text(0, 0, 30, 0, in_db=False)


def test_review_verdict():
    v = R.review_verdict
    assert v(2, 2) == ("same", "照建议订")
    assert v(0, 0) == ("none", "不订")
    assert v(2, 0) == ("none", "不订")
    assert v(2, 3) == ("more", "多订 1 箱")
    assert v(3, 1) == ("less", "少订 2 箱")
    assert v(2, 1, moved=True)[0] == "transfer"
    assert v(2, 1, in_db=False)[0] == "nodb"
    # 件数：差额整箱写箱，不是整箱写件
    assert v(12, 24, pack=12)[1] == "多订 1 箱"
    assert v(12, 17, pack=12)[1] == "多订 5 件"
    assert v(12, 12, pack=12, same_text="照清单订") == ("same", "照清单订")


def test_plain_reason():
    p = R.plain_reason
    assert p(31, 0.6, 60, 0) == ["现在库存 31 件，每天卖 0.6 件左右，够卖 52 天", "够卖到下次订货，不用订"]
    got = p(2, 0.5, 60, 24, boxes=2)
    assert "快断货了" in got[0] and got[1] == "接下来 60 天大约要卖 30 件，库存不够 → 建议订 2 箱（24 件）"
    assert "库存 0 件" in p(-3, 1, 30, 30)[0] and "按 0 算" not in p(-3, 1, 30, 30)[0]
    got = p(2, 0.13, 60, 12, boxes=1, transfer={"name": "S.FILIPPO", "inv": 24, "days": 240, "pieces": 6, "saved": 1})
    assert got[-1] == "S.FILIPPO 库存多：有 24 件，够卖 240 天，卖不完 → 调 6 件过来，这边少订 1 箱"
    assert "卖不动" in p(0, 0, 60, 0, transfer={"name": "A", "inv": 5, "days": float("inf"), "pieces": 5})[-1]
    assert p(1, 1, 30, 0, in_db=False) == ["数据库里找不到这个商品，没有销量和库存可以参考"]


def test_codes_from_eans_raises_when_db_fails():
    """数据库查不了要报错（以前悄悄返回空，整张清单都成了「数据库没有」）"""
    class BadDB:
        engine = None
    try:
        R.codes_from_eans(BadDB(), ["8001"])
    except RuntimeError as e:
        assert "数据库" in str(e)
    else:
        raise AssertionError("应该报错")
    assert R.codes_from_eans(BadDB(), []) == {}


def test_review_marks_and_pack_text():
    m = R.review_marks(True, True, 950, -5)
    assert [k for k, _, _ in m] == ["slow"] and "950" in m[0][2]          # 负库存不再提醒「库存不准」
    assert R.review_marks(False, True, 950, 3) == []          # 原程序算法不标慢销
    assert R.review_marks(True, False, 950, 3) == []          # 没订不标
    assert R.review_marks(True, True, 120, 0) == []
    got = R.plain_reason(0, 0.5, 30, 1, boxes=1, pack=24)
    assert got[-1].endswith("建议订 1 箱（要 1 件，一箱 24 件）")


def test_supplier_info_into_rows_and_snapshot(tmp_path):
    import json
    import reorder_service as R
    rows = [{"code": "7", "ean": "800"}, {"code": "8", "ean": "801"}]
    assert R.add_supplier_info(rows, {"7": ("AM1", "WOLSEN")}) == 1
    assert rows[0]["sup_code"] == "AM1" and "sup_code" not in rows[1]
    main = tmp_path / "ORDINE" / "o.xlsx"
    main.parent.mkdir()
    main.write_bytes(b"")
    p = R.save_snapshot(str(main), 2, {2: {"7": {"inv": 1, "daily": 0.5, "cycle": 30}}}, rows)
    data = json.load(open(p, encoding="utf-8"))
    assert data["by_ean"]["800"]["sup_code"] == "AM1" and data["by_ean"]["800"]["sup_name"] == "WOLSEN"


def test_checked_order_is_by_boxes(tmp_path):
    """检查清单确认的主订单：COLLI = 确定箱数，不写 PEZZI（比价按箱数分配供应商）"""
    from openpyxl import load_workbook
    import reorder_service as R
    rows = [{"code": "7", "ean": "800", "name": "A", "pack": 12, "final": 24, "fbox": 2},
            {"code": "8", "ean": "801", "name": "B", "pack": 0, "final": 3, "fbox": 3},
            {"code": "9", "ean": "802", "name": "C", "pack": 6, "final": 0, "fbox": 0}]
    path, n, skipped = R.write_checked_order(rows, str(tmp_path), 2)
    ws = load_workbook(path).active
    heads = [c.value for c in ws[1]]
    assert "PEZZI" not in heads and n == 2
    colli = heads.index("COLLI")
    assert [r[colli] for r in ws.iter_rows(min_row=2, values_only=True)] == [2, 3]



def test_check_boxes_no_more_than_list_and_new_item_protection():
    """
    检查门店清单（2026-10-25 用户定）：清单一律是箱；系统建议不多于清单（先不做多订）；
    系统说要订就至少 1 箱；新品按清单订
    """
    import order_check as OC
    algo = {"engine": "v1"}
    base = {"in_db": True, "pack": 12, "store": 1, "inv0": 0, "daily": None, "sales": [30, 60, 90, 180]}
    rows = [dict(base, file_qty=2, suggested=200),                       # 系统要 17 箱 → 按清单 2 箱
            dict(base, file_qty=3, suggested=5),                         # 系统要 5 件（不到一箱）→ 1 箱，但少订最多一半 → 2 箱
            dict(base, file_qty=1, suggested=0, inv0=50),                # 库存够 → 不订
            dict(base, file_qty=2, suggested=0, sales=[3, 5, 5, 5]),      # 两个月前还没卖过 → 新品，按清单
            dict(base, file_qty=1, suggested=0, sales=[0, 0, 0, 0]),      # 从没卖过 → 新品，按清单
            dict(base, file_qty=4, in_db=False, pack=0)]                 # 数据库没有 → 清单
    OC.init_boxes(rows, None, algo)
    assert [x["fbox"] for x in rows] == [2, 2, 0, 2, 1, 4]
    assert rows[0]["sys_box"] == 17 and rows[3].get("protect") == "new" and rows[2].get("protect") is None
    empty = [dict(base, file_qty=3, suggested=0, inv0=-2, sales=[0, 1, 2, 4])]   # 没货了、门店要 → 至少 1 箱（少订最多一半 → 2 箱）
    OC.init_boxes(empty, None, algo)
    assert empty[0]["fbox"] == 2 and empty[0]["protect"] == "empty"


def test_check_boxes_a_protection_and_cut_limit():
    """
    2026-10-25 用户定：A 类、库存不够卖一个周期、门店要了 → 至少 1 箱；
    少订最多砍掉清单一半，除非库存已经够卖 2 个订货周期以上
    """
    import order_check as OC
    algo = {"engine": "v1"}
    base = {"in_db": True, "pack": 12, "store": 1, "cycle": 30, "daily": None, "sales": [30, 60, 90, 180]}
    rows = [dict(base, file_qty=1, suggested=0, inv0=6, daily0=0.5, tier="A"),     # 够卖 12 天 < 30 → A 类保护 1 箱
            dict(base, file_qty=1, suggested=0, inv0=6, daily0=0.5, tier="C"),     # C 类：不保护，但少订上限 → 1 箱（清单 1 箱一半向上取整）
            dict(base, file_qty=1, suggested=0, inv0=60, daily0=0.5, tier="A"),    # 够卖 120 天 ≥ 2 周期 → 可以不订
            dict(base, file_qty=8, suggested=12, inv0=20, daily0=0.5, tier="B"),   # 系统 1 箱，清单 8 → 最少 4 箱
            dict(base, file_qty=8, suggested=12, inv0=40, daily0=0.5, tier="B")]   # 够卖 80 天 ≥ 60 → 按系统 1 箱
    OC.init_boxes(rows, None, algo)
    assert [x["fbox"] for x in rows] == [1, 1, 0, 4, 1]
    assert rows[0]["protect"] == "a_low" and rows[3].get("limited") and not rows[4].get("limited")
    rows2 = [dict(rows[3])]
    OC.init_boxes(rows2, None, algo, {"max_cut": 1.0})                            # 不限制
    assert rows2[0]["fbox"] == 1


def test_phantom_stock():
    """
    虚库存（2026-10-25）：账面有货、门店点名要，但按平时卖法早该卖出 3 件以上却一件没卖
    → A 类按清单订、B 类库存按 0 重算、C 类只提醒
    """
    from datetime import date, timedelta
    import order_check as OC
    today = date.today()
    ago = lambda n: (today - timedelta(days=n)).strftime("%Y-%m-%d")
    base = {"in_db": True, "pack": 6, "store": 1, "cycle": 30, "daily": None, "sales": [30, 60, 90, 180],
            "file_qty": 3, "suggested": 0, "inv0": 20, "daily0": 0.5}
    # 日均 0.5：3 件 ÷ 0.5 = 6 天，至少 7 天 → 7 天没卖就可疑
    assert OC.phantom_info(dict(base, last_sale=ago(8)), today)["days"] == 8
    assert OC.phantom_info(dict(base, last_sale=ago(5)), today) is None
    assert OC.phantom_info(dict(base, last_sale=ago(8), daily0=0.05), today) is None     # 慢销：要 60 天没卖才算
    assert OC.phantom_info(dict(base, last_sale=ago(8), file_qty=0), today) is None      # 门店没要
    assert OC.phantom_info(dict(base, last_sale=ago(8), inv0=0), today) is None          # 本来就没货（缺货保护管）
    rows = [dict(base, last_sale=ago(10), tier="A"),           # A：按清单 3 箱
            dict(base, last_sale=ago(10), tier="B"),           # B：库存按 0：0.5 × 30 = 15 件 → 3 箱
            dict(base, last_sale=ago(10), tier="C"),           # C：只提醒（库存够卖 40 天 < 60 → 少订上限 2 箱）
            dict(base, last_sale=ago(2), tier="A")]            # 最近还在卖：正常（库存 40 天 → 上限 2 箱）
    OC.init_boxes(rows, None, {"engine": "v1"})
    assert [x["fbox"] for x in rows] == [3, 3, 2, 2]
    assert rows[0]["protect"] == "phantom" and rows[1]["protect"] == "phantom"
    assert rows[2]["phantom"] and rows[2].get("protect") != "phantom" and not rows[3]["phantom"]
