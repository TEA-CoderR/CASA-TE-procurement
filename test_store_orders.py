"""门店配货：起订量判断、凑单、并入 BMS、写订单文件"""
import os
import glob
import tempfile
import unittest

import store_orders as S


def _scheda(moq_colli=0, moq_amount=0, items=None):
    items = items or [
        {"row": 6, "code": "1", "ean": "", "desc": "A", "pack": 10, "price": 1.0},   # 一箱 €10
        {"row": 7, "code": "2", "ean": "", "desc": "B", "pack": 6, "price": 2.5},    # 一箱 €15
        {"row": 8, "code": "3", "ean": "", "desc": "C", "pack": 12, "price": 0.5},   # 一箱 €6
    ]
    return {"name": "TEST", "path": "", "moq_colli": moq_colli, "moq_amount": moq_amount, "items": items,
            "sheets": dict(S.STORE_SHEETS), "cols": {"colli": 8}, "rowmap": {}}


def _d(sugg, daily=1.0, inv=0):
    return {"sugg": sugg, "daily": daily, "inv": inv}


FILL = {"max_amount_gap": 20, "max_colli_gap": 3, "max_stock_days": 60, "fill_bms": True}


class PlanTest(unittest.TestCase):
    def test_direct_when_store_reaches_moq_alone(self):
        sc = _scheda(moq_colli=5)
        dem = {1: {"1": _d(60)}, 2: {"1": _d(10)}}      # FOIANO 6 箱，MARLIA 1 箱
        p = S.plan_supplier(sc, dem, dict(FILL, max_colli_gap=0), [1, 2])
        self.assertEqual(p["stores"][1]["status"], "direct")
        self.assertEqual(p["stores"][1]["lines"], {6: 6})
        self.assertEqual(p["stores"][2]["status"], "bms")
        self.assertEqual(p["bms"]["stores"], [2])
        self.assertEqual(p["bms"]["status"], "short")

    def test_small_amount_gap_is_filled_with_least_excess(self):
        sc = _scheda(moq_amount=100)
        # 已有 9 箱 A = €90，差 €10：A 一箱 €10 刚好够
        dem = {1: {"1": _d(90, daily=2), "2": _d(0, daily=1), "3": _d(0, daily=3)}}
        p = S.plan_supplier(sc, dem, FILL, [1])
        st = p["stores"][1]
        self.assertEqual(st["status"], "filled")
        self.assertAlmostEqual(st["amount"], 100.0)
        self.assertEqual(st["added"], [(6, 1, 10.0)])

    def test_big_gap_goes_to_bms_and_bms_merges(self):
        sc = _scheda(moq_amount=100)
        dem = {1: {"1": _d(50)}, 3: {"1": _d(30), "2": _d(12)}}   # €50 / €30+€30
        p = S.plan_supplier(sc, dem, FILL, [1, 3])
        self.assertEqual(p["stores"][1]["status"], "bms")
        self.assertEqual(p["stores"][3]["status"], "bms")
        b = p["bms"]
        self.assertEqual(sorted(b["stores"]), [1, 3])
        self.assertEqual(b["lines"], {6: 8, 7: 2})
        self.assertAlmostEqual(b["amount"], 110.0)
        self.assertEqual(b["status"], "direct")
        self.assertEqual(b["per_store"], {1: {6: 5}, 3: {6: 3, 7: 2}})

    def test_fill_respects_stock_days(self):
        sc = _scheda(moq_colli=3)
        # 只差 1 箱，但加一箱后库存够卖 > 60 天 → 不凑，并入 BMS
        dem = {1: {"1": _d(20, daily=0.1, inv=0)}}
        p = S.plan_supplier(sc, dem, FILL, [1])
        self.assertEqual(p["stores"][1]["status"], "bms")

    def test_colli_gap_adds_fastest_seller(self):
        sc = _scheda(moq_colli=4)
        dem = {1: {"1": _d(30, daily=1), "3": _d(0, daily=5)}}
        p = S.plan_supplier(sc, dem, FILL, [1])
        st = p["stores"][1]
        self.assertEqual(st["status"], "filled")
        self.assertEqual(st["lines"], {6: 3, 8: 1})

    def test_zero_gap_setting_disables_fill(self):
        sc = _scheda(moq_amount=100)
        dem = {1: {"1": _d(90)}}
        p = S.plan_supplier(sc, dem, dict(FILL, max_amount_gap=0), [1])
        self.assertEqual(p["stores"][1]["status"], "bms")

    def test_no_moq_any_quantity_is_direct(self):
        sc = _scheda()
        p = S.plan_supplier(sc, {1: {"2": _d(1)}, 2: {}}, FILL, [1, 2])
        self.assertEqual(p["stores"][1]["status"], "direct")
        self.assertEqual(p["stores"][2]["status"], "none")
        self.assertIsNone(p["bms"])

    def test_manual_colli_overrides_suggestion(self):
        sc = _scheda(moq_colli=5)
        dem = {1: {"1": _d(10)}}                          # 建议 1 箱，不够 5 箱
        p = S.plan_supplier(sc, dem, dict(FILL, max_colli_gap=0), [1], colli={1: {6: 3, 7: 2}})
        st = p["stores"][1]
        self.assertEqual(st["status"], "direct")
        self.assertEqual(st["lines"], {6: 3, 7: 2})
        p = S.plan_supplier(sc, dem, FILL, [1], colli={1: {}})   # 手动改成全部不订
        self.assertEqual(p["stores"][1]["status"], "none")

    def test_bms_status_text_and_output_folder(self):
        self.assertEqual(S.status_text(S.BMS, "direct"), "BMS 分货")
        self.assertEqual(S.status_text(S.BMS, "filled"), "凑单后 BMS 分货")
        self.assertEqual(S.status_text("FOIANO", "direct"), "直接送店")
        from datetime import datetime
        self.assertEqual(S.output_root("D:/x", datetime(2026, 10, 17)), os.path.join("D:/x", "生成订单", "20261017"))

    def _rescue_setup(self):
        items = [{"row": r, "code": str(r), "ean": "", "desc": f"P{r}", "pack": 1, "price": 1.0} for r in range(6, 12)]
        sc = _scheda(moq_colli=10, items=items)
        calm = {"daily": 1.0, "inv": 20, "decision": "🟢 Ordine suggerito"}       # 库存够卖 20 天，不急
        urgent = {"daily": 1.0, "inv": 0, "decision": "🔴 Rifornimento urgente"}
        dem = {1: {"6": dict(calm, sugg=12)},                                    # 远店，直接送店，不能借
               2: {"6": dict(urgent, sugg=8), "7": dict(calm, sugg=7)},         # 15 箱，7 箱不急
               4: {"8": dict(calm, sugg=3)}, 5: {"9": dict(calm, sugg=3)}}     # 各 3 箱 → BMS 只有 6 箱
        return sc, dem

    def test_bms_merges_whole_store_before_borrowing(self):
        """2026-10-22：有能整单并进来的门店就整单并，不拆别的店的单借货（每家店的货只走一条路）"""
        sc, dem = self._rescue_setup()
        p = S.plan_supplier(sc, dem, dict(FILL, max_colli_gap=0), [1, 2, 4, 5])
        b = p["bms"]
        self.assertEqual(b["status"], "direct")
        self.assertEqual(b["lenders"], {})
        self.assertEqual(sorted(b["stores"]), [2, 4, 5])
        self.assertEqual(p["stores"][2]["status"], "pulled")
        self.assertEqual(p["stores"][1]["status"], "direct")            # 远店不动
        # 供应商单独设：2 号店不能并进 BMS → 没别的店可并，也借不到（2 号店也不能借）→ 不够
        fill = dict(FILL, max_colli_gap=0, supplier_cfg={sc["name"]: {"bms_stores": [4, 5]}})
        p = S.plan_supplier(sc, dem, fill, [1, 2, 4, 5])
        self.assertEqual(p["bms"]["status"], "short")
        self.assertEqual(p["stores"][2]["status"], "direct")
        # 供应商单独设：1 号店（总设置里是远店）可以并 → 并 1 号店（12 箱，比 2 号店 15 箱小）
        fill = dict(FILL, max_colli_gap=0, supplier_cfg={sc["name"]: {"bms_stores": [1, 2, 3, 4, 5]}})
        p = S.plan_supplier(sc, dem, fill, [1, 2, 4, 5])
        self.assertEqual(p["stores"][1]["status"], "pulled")
        self.assertEqual(p["stores"][2]["status"], "direct")

    def test_bms_borrows_non_urgent_from_same_city_not_far_store(self):
        sc, dem = self._rescue_setup()
        fill = dict(FILL, max_colli_gap=0, merge_direct=False)       # 不整单并入时才借货
        p = S.plan_supplier(sc, dem, fill, [1, 2, 4, 5])
        b = p["bms"]
        self.assertEqual(b["status"], "direct")
        self.assertEqual(b["colli"], 10)
        self.assertEqual(b["lenders"], {2: {7: 4}})                     # 只借 2 号店不急的那个商品
        self.assertEqual(p["stores"][2]["lines"], {6: 8, 7: 3})         # 2 号店还剩 11 箱，仍够起订量
        self.assertEqual(p["stores"][2]["status"], "direct")
        self.assertEqual(p["stores"][1]["lines"], {6: 12})              # 1 号店不动
        self.assertEqual(b["per_store"][2], {7: 4})
        # 关掉借货（也不整单并入）→ 还是不够
        p = S.plan_supplier(sc, dem, dict(fill, borrow=False, merge_direct=False), [1, 2, 4, 5])
        self.assertEqual(p["bms"]["status"], "short")
        self.assertEqual(p["stores"][2]["lines"], {6: 8, 7: 7})

    def test_colocated_store_merges_first(self):
        sc, dem = self._rescue_setup()
        dem[3] = {"10": {"daily": 1.0, "inv": 0, "sugg": 11, "decision": "🔴"}}
        p = S.plan_supplier(sc, dem, dict(FILL, max_colli_gap=0), [1, 2, 3, 4, 5])
        b = p["bms"]
        self.assertEqual(p["stores"][3]["status"], "merged")
        self.assertEqual(b["status"], "direct")
        self.assertEqual(sorted(b["stores"]), [3, 4, 5])
        self.assertEqual(b["lenders"], {})                              # 并了 3 号店就够了，不用借
        self.assertEqual(S.status_text("S.FILIPPO", "merged"), "并入 BMS（同一仓库）")

    def test_colocated_store_stays_direct_when_bms_is_enough(self):
        sc, dem = self._rescue_setup()
        dem[4]["8"]["sugg"] = 5
        dem[5]["9"]["sugg"] = 5                                         # BMS 自己就有 10 箱，够起订量
        dem[3] = {"10": {"daily": 1.0, "inv": 0, "sugg": 11, "decision": "🔴"}}
        p = S.plan_supplier(sc, dem, dict(FILL, max_colli_gap=0), [1, 2, 3, 4, 5])
        self.assertEqual(p["bms"]["status"], "direct")
        self.assertEqual(p["stores"][3]["status"], "direct")            # 3 号店照样直接送店，不并进 BMS
        self.assertEqual(sorted(p["bms"]["stores"]), [4, 5])
        self.assertEqual(p["bms"]["lenders"], {})

    def test_no_rescue_when_it_cannot_reach_moq(self):
        sc, dem = self._rescue_setup()
        dem[2]["7"]["inv"] = 0                                          # 2 号店的货也急 → 不能借
        p = S.plan_supplier(sc, dem, dict(FILL, max_colli_gap=0, borrow_urgent=False, merge_direct=False),
                            [1, 2, 4, 5])
        self.assertEqual(p["bms"]["status"], "short")
        self.assertEqual(p["bms"]["lenders"], {})
        self.assertEqual(p["stores"][2]["lines"], {6: 8, 7: 7})
        t = S.plan_totals([("周一", sc, p)])
        self.assertAlmostEqual(t["total"], 12 + 15)         # BMS 不够起订量 → 不下单，不算金额


    def test_borrow_urgent_part_above_lender_moq(self):
        """不紧急的借不到：把 2 号店超过起订量的部分（紧急的也行）借给 BMS，2 号店仍够起订量"""
        sc, dem = self._rescue_setup()
        dem[2]["7"]["inv"] = 0
        p = S.plan_supplier(sc, dem, dict(FILL, max_colli_gap=0, merge_direct=False), [1, 2, 4, 5])
        b = p["bms"]
        self.assertEqual(b["status"], "direct")
        self.assertEqual(b["colli"], 10)
        self.assertEqual(sum(sum(r.values()) for r in b["lenders"].values()), 4)
        self.assertEqual(p["stores"][2]["status"], "direct")
        self.assertGreaterEqual(p["stores"][2]["colli"], 10)            # 借完 2 号店还够起订量
        self.assertNotIn("pulled", b["how"])

    def test_merge_whole_store_when_borrowing_is_not_enough(self):
        """2 号店只超出 1 箱，借了也不够 → 把 2 号店整单并进 BMS，3、4 号店也订得起来"""
        sc, dem = self._rescue_setup()
        dem[2] = {"6": {"daily": 1.0, "inv": 0, "sugg": 11, "decision": "🔴"}}   # 11 箱，只多 1 箱
        p = S.plan_supplier(sc, dem, dict(FILL, max_colli_gap=0), [1, 2, 4, 5])
        b = p["bms"]
        self.assertEqual(b["status"], "direct")
        self.assertEqual(sorted(b["stores"]), [2, 4, 5])
        self.assertEqual(p["stores"][2]["status"], "pulled")
        self.assertEqual(b["lenders"], {})
        self.assertIn("pulled", b["how"])
        self.assertEqual(p["stores"][1]["status"], "direct")            # 远店不动
        self.assertEqual(S.status_text("MARLIA", "pulled"), "并入 BMS（帮其他店凑起订量）")
        # 关掉 ④ → 还是不够
        p = S.plan_supplier(sc, dem, dict(FILL, max_colli_gap=0, merge_direct=False), [1, 2, 4, 5])
        self.assertEqual(p["bms"]["status"], "short")
        self.assertEqual(p["stores"][2]["status"], "direct")
    def test_mode_direct_only(self):
        sc, dem = self._rescue_setup()
        dem[2]["7"]["inv"] = 0
        p = S.plan_supplier(sc, dem, dict(FILL, max_colli_gap=0), [1, 2, 4, 5], mode="direct_only")
        self.assertIsNone(p["bms"])
        self.assertEqual({s: st["status"] for s, st in p["stores"].items() if st["status"] != "none"},
                         {1: "direct", 2: "direct", 4: "dropped", 5: "dropped"})
        self.assertEqual(p["stores"][2]["lines"], {6: 8, 7: 7})          # 不借货，原样直送
        self.assertTrue(all(st["status"] in ("direct", "filled", "dropped", "none") for st in p["stores"].values()))

    def test_mode_all_bms_forces_order(self):
        sc, dem = self._rescue_setup()
        dem[2]["7"]["inv"] = 0
        p = S.plan_supplier(sc, dem, dict(FILL, max_colli_gap=0), [1, 2, 4, 5], mode="all_bms")
        b = p["bms"]
        self.assertTrue(S.bms_ordered(b))
        self.assertIn(b["status"], ("direct", "filled", "forced"))
        self.assertEqual(b["lenders"], {})
        ordered = [s for s, st in p["stores"].items() if st["status"] != "none"]
        self.assertEqual(sorted(b["stores"]), sorted(ordered))
        self.assertTrue(all(p["stores"][s]["status"] == "bms" for s in ordered))

    def test_supplier_name(self):
        self.assertEqual(S.supplier_short_name("SCHEDA DOPLA 0110 .xlsx"), "DOPLA")
        self.assertEqual(S.supplier_short_name("SCHEDA ITALKALI SOCIETA 2308.xlsx"), "ITALKALI SOCIETA")


SAMPLES = sorted(glob.glob("/mnt/user-data/uploads/采购配单/*/SCHEDA*.xlsx"))


@unittest.skipUnless(SAMPLES, "没有 SCHEDA 样本")
class WriteTest(unittest.TestCase):
    def test_write_keeps_everything_but_colli(self):
        import zipfile
        from openpyxl import load_workbook
        sc = S.parse_scheda(SAMPLES[-1])
        rows = [it["row"] for it in sc["items"]]
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "o.xlsx")
            S.write_order_file(sc, {1: {rows[0]: 3, rows[1]: 2}, 4: {rows[2]: 7}}, out)
            zin, zout = zipfile.ZipFile(sc["path"]), zipfile.ZipFile(out)
            self.assertEqual(sorted(zin.namelist()), sorted(zout.namelist()))
            for n in zin.namelist():
                if n.startswith("xl/media/") or n == "xl/cellimages.xml":
                    self.assertEqual(zin.read(n), zout.read(n))
            self.assertIn('fullCalcOnLoad="1"', zout.read("xl/workbook.xml").decode())
            wb = load_workbook(out)
            c = sc["cols"]["colli"]
            vals = {sid: {r: wb[name].cell(r, c).value for r in rows} for sid, name in sc["sheets"].items()}
            self.assertEqual(vals[1][rows[0]], 3)
            self.assertEqual(vals[1][rows[1]], 2)
            self.assertEqual(vals[4][rows[2]], 7)
            self.assertTrue(all(v in (None, 0) for v in vals[2].values()))
            ws = wb[sc["sheets"][1]]
            self.assertEqual(str(ws.cell(rows[0], c + 1).value).upper().replace("$", ""), f"=H{rows[0]}*G{rows[0]}")

    def test_colli_yellow_only_on_ordered_rows_and_no_chinese_columns(self):
        from openpyxl import load_workbook
        sc = S.parse_scheda(SAMPLES[-1])
        self.assertIn("sup_code", sc["cols"])                       # Codice Fornitore 读进来了
        self.assertTrue(any(it["sup_code"] for it in sc["items"]))
        rows = [it["row"] for it in sc["items"]]
        heads = S.extra_headers()
        extra, h2 = S.file_extra({1: {r: list(range(len(heads))) for r in rows}}, heads)
        self.assertFalse(set(h2) & set(S.FILE_SKIP_HEADS))
        self.assertEqual(len(extra[1][rows[0]]), len(h2))
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "o.xlsx")
            S.write_order_file(sc, {1: {rows[0]: 3}}, out, extra, h2)
            ws = load_workbook(out)[sc["sheets"][1]]
            c = sc["cols"]["colli"]
            self.assertEqual(ws.cell(rows[0], c).value, 3)
            self.assertEqual(ws.cell(rows[1], c).fill.fill_type, ws.cell(rows[1], c - 1).fill.fill_type)
            heads_in_file = [ws.cell(sc["header_row"], k).value for k in range(1, ws.max_column + 1)]
            self.assertNotIn("月均", heads_in_file)
            self.assertIn("GIA", heads_in_file)


class FileExtraTest(unittest.TestCase):
    def test_file_extra_drops_internal_columns(self):
        heads = S.extra_headers()
        vals = list(range(len(heads)))
        extra, h2 = S.file_extra({2: {5: vals, 6: None}}, heads)
        self.assertEqual(h2[:5], ["GIA", "ROT.30d", "ROT.60d", "ROT.90d", "ROT.180d"])
        self.assertEqual(h2[5], "Livello")
        self.assertEqual(extra[2][5][5], heads.index("Livello"))
        self.assertIsNone(extra[2][6])
        self.assertEqual(S.file_extra(None, None), (None, None))


if __name__ == "__main__":
    unittest.main()


def test_new_run_folder_never_overwrites(tmp_path):
    import store_orders as S
    from datetime import datetime
    d = tmp_path / "20261017"
    a = S.new_run_folder(str(d), datetime(2026, 10, 17, 9, 30))
    b = S.new_run_folder(str(d), datetime(2026, 10, 17, 9, 30))
    (d / "周四").mkdir()                      # 以前直接放在日期文件夹里的旧文件不受影响
    c = S.new_run_folder(str(d), datetime(2026, 10, 17, 14, 5))
    assert [p.split("/")[-1].split("\\")[-1] for p in (a, b, c)] == ["第01次_0930", "第02次_0930", "第03次_1405"]


class AddItemsTest(unittest.TestCase):
    """订货明细「加商品凑单」：列出同一个供应商没被选进来的商品，按销量推荐补齐起订量"""

    def _setup(self):
        sc = _scheda(moq_amount=40)
        dem = {2: {"1": {"daily": 2.0, "inv": 30, "sugg": 0},        # 卖得快、库存够（没被选进来）
                   "2": {"daily": 0.2, "inv": 1, "sugg": 6},          # 要订 1 箱 €15
                   "3": {"daily": 0.0, "inv": 5, "sugg": 0}}}         # 不动销
        return sc, dem

    def test_candidates_sorted_by_sales(self):
        sc, dem = self._setup()
        c = S.add_candidates(sc, 2, dem, {7: 1})
        self.assertEqual([x["row"] for x in c], [6, 7, 8])
        self.assertEqual(c[1]["ordered"], 1)
        self.assertAlmostEqual(c[0]["days_now"], 15.0)
        self.assertAlmostEqual(S.days_after(c[0], 2), (30 + 20) / 2.0)

    def test_suggest_fill(self):
        sc, dem = self._setup()
        add = S.suggest_fill(sc, 2, dem, {7: 1}, max_days=60)
        # 差 €25：加卖得快的 A（一箱 €10）… 直到够 €40
        lines = {7: 1}
        for r, n in add.items():
            lines[r] = lines.get(r, 0) + n
        self.assertGreaterEqual(S._totals(lines, {it["row"]: it for it in sc["items"]})[1], 40)
        self.assertNotIn(8, add)                                          # 不动销的不加
        self.assertEqual(S.suggest_fill(sc, 2, dem, {7: 3}), {})          # 已经够了
        self.assertIsNone(S.suggest_fill(sc, 2, dem, {7: 1}, max_days=16))   # 天数限制内凑不齐


class CoverDaysTest(unittest.TestCase):
    def test_cover_days(self):
        self.assertAlmostEqual(S.cover_days({"inv": 10, "daily": 2}), 5)
        self.assertAlmostEqual(S.cover_days({"inv": -3, "daily": 2}, 12), 6)      # 负库存当 0
        self.assertEqual(S.cover_days({"inv": 4, "daily": 0}), float("inf"))
        self.assertIsNone(S.cover_days({"inv": 0, "daily": 0}))
        self.assertIsNone(S.cover_days(None))
        self.assertEqual(S.days_text(float("inf")), "∞")
        self.assertEqual(S.days_text(12.4), "12")
        self.assertEqual(S.days_text(None), "")
