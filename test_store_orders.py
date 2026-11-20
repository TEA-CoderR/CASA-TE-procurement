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
        self.assertEqual(p["stores"][2]["status"], "wait")             # 凑不够 BMS → 这次不订
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
        p = S.plan_supplier(sc, dem, dict(FILL, far_stores=[]), [1, 3])
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
        self.assertEqual(p["stores"][1]["status"], "wait")

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
        self.assertEqual(p["stores"][1]["status"], "wait")

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

    def _prio_setup(self):
        items = [{"row": r, "code": str(r), "ean": "", "desc": f"P{r}", "pack": 1, "price": 1.0} for r in range(6, 12)]
        sc = _scheda(moq_colli=10, items=items)
        calm = {"daily": 1.0, "inv": 20, "decision": ""}
        dem = {1: {"6": dict(calm, sugg=12)}, 2: {"7": dict(calm, sugg=15)},           # 1、2 号店自己够
               3: {"8": dict(calm, sugg=4)}, 4: {"9": dict(calm, sugg=6)}, 5: {"10": dict(calm, sugg=5)}}
        return sc, dem

    def test_bms_priority_order_stops_when_enough(self):
        """能单独送店的单独送店；订不起来的按 3-4-5-2-1 并进 BMS，够起订量就停，剩下的这次不订"""
        sc, dem = self._prio_setup()
        p = S.plan_supplier(sc, dem, dict(FILL, max_colli_gap=0), [1, 2, 3, 4, 5])
        b = p["bms"]
        self.assertEqual(b["stores"], [3, 4])                          # 3 号 4 箱 + 4 号 6 箱 = 10 箱，够了
        self.assertEqual(b["status"], "direct")
        self.assertEqual(p["stores"][5]["status"], "wait")
        self.assertEqual({s: p["stores"][s]["status"] for s in (1, 2)}, {1: "direct", 2: "direct"})
        self.assertEqual(b["lenders"], {})
        # 改优先顺序：5 号先 → 5 + 4 = 11 箱
        p = S.plan_supplier(sc, dem, dict(FILL, max_colli_gap=0, bms_order=[5, 4, 3, 2, 1]), [1, 2, 3, 4, 5])
        self.assertEqual(p["bms"]["stores"], [5, 4])
        self.assertEqual(p["stores"][3]["status"], "wait")
        # 供应商单独设：3 号店不参与 → 4 + 5 = 11 箱
        fill = dict(FILL, max_colli_gap=0, supplier_cfg={sc["name"]: {"bms_stores": [2, 4, 5]}})
        p = S.plan_supplier(sc, dem, fill, [1, 2, 3, 4, 5])
        self.assertEqual(p["bms"]["stores"], [4, 5])
        self.assertEqual(p["stores"][3]["status"], "wait")

    def test_bms_not_enough_means_wait_never_pull_direct_stores(self):
        sc, dem = self._prio_setup()
        dem[4]["9"]["sugg"] = 1
        dem[5]["10"]["sugg"] = 1                                        # 3+4+5 只有 6 箱
        p = S.plan_supplier(sc, dem, dict(FILL, max_colli_gap=0), [1, 2, 3, 4, 5])
        self.assertEqual(p["bms"]["status"], "short")
        self.assertEqual({s: p["stores"][s]["status"] for s in (3, 4, 5)}, {3: "wait", 4: "wait", 5: "wait"})
        self.assertEqual(p["stores"][2]["status"], "direct")            # 自己够的照样直送，不拉进 BMS
        self.assertEqual(S.plan_totals([("周一", sc, p)])["total"], 12 + 15)

    def test_manual_pick_bms_stores(self):
        """手动决定哪几家进 BMS：自己够起订量的 2 号店也可以并进来"""
        sc, dem = self._prio_setup()
        dem[4]["9"]["sugg"] = 1
        dem[5]["10"]["sugg"] = 1
        p = S.plan_supplier(sc, dem, dict(FILL, max_colli_gap=0), [1, 2, 3, 4, 5], mode="manual",
                            picks={"stores": [2, 3, 4, 5]})
        b = p["bms"]
        self.assertEqual(b["status"], "direct")
        self.assertEqual(p["stores"][2]["status"], "pulled")
        self.assertEqual(p["stores"][1]["status"], "direct")
        p = S.plan_supplier(sc, dem, dict(FILL, max_colli_gap=0), [1, 2, 3, 4, 5], mode="manual",
                            picks={"stores": [4, 5]})
        self.assertEqual(p["bms"]["status"], "short")
        self.assertEqual(p["stores"][3]["status"], "wait")
        p = S.plan_supplier(sc, dem, dict(FILL, max_colli_gap=0), [1, 2, 3, 4, 5], mode="manual",
                            picks={"stores": [4, 5], "force": True})
        self.assertEqual(p["bms"]["status"], "forced")
        self.assertTrue(S.bms_ordered(p["bms"]))

    def test_urgent_share(self):
        sc, dem = self._prio_setup()
        dem[3] = {"8": {"daily": 1.0, "inv": 2, "sugg": 4}, "9": {"daily": 1.0, "inv": 30, "sugg": 4}}
        p = S.plan_supplier(sc, dem, dict(FILL, max_colli_gap=0), [1, 2, 3, 4, 5])
        share, n = S.plan_urgent(sc, p, 3)
        self.assertAlmostEqual(share, 0.5)                              # 一半的商品够卖不到一周（按个数）
        self.assertEqual(n, 1)
        share_b, _ = S.plan_urgent(sc, p, None)
        self.assertGreater(share_b, 0)

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

    def test_colli_yellow_only_on_ordered_rows_and_monthly_column(self):
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
            self.assertIn("MEDIA MENS.", heads_in_file)       # 2026-11-07 用户：月均要写进订单文件（意大利文，GIA 后面）
            g = heads_in_file.index("GIA")
            self.assertEqual(heads_in_file[g:g + 5], ["GIA", "ULT. ARRIVO", "QTA ULT. ARR.", "ULT. VENDITA", "MEDIA MENS."])
            self.assertNotIn("月均", heads_in_file)
            self.assertNotIn("需求", heads_in_file)
            self.assertIn("GIA", heads_in_file)


class FileExtraTest(unittest.TestCase):
    def test_file_extra_drops_internal_columns(self):
        heads = S.extra_headers()
        vals = list(range(len(heads)))
        extra, h2 = S.file_extra({2: {5: vals, 6: None}}, heads)
        self.assertEqual(h2[:10], ["GIA", "ULT. ARRIVO", "QTA ULT. ARR.", "ULT. VENDITA", "MEDIA MENS.",
                                   "ROT.30d", "ROT.60d", "ROT.90d", "ROT.180d", "Livello"])
        self.assertEqual(extra[2][5][1], heads.index("最后到货"))
        self.assertEqual(extra[2][5][3], heads.index("最后销售"))
        self.assertEqual(extra[2][5][4], heads.index("月均"))
        self.assertEqual(extra[2][5][5], heads.index("ROT.30d"))
        self.assertEqual(extra[2][5][9], heads.index("Livello"))
        self.assertEqual(len(h2), len(heads) - 3)
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


def test_monthly_and_need_follow_algorithm():
    """月均 / 需求：新算法按加权日均和目标库存算，原程序算法按销量周期算"""
    v1 = {"sales": [30, 60, 90, 180], "cycle": 30}
    assert S.monthly_and_need(v1) == (30.0, 30.0)                 # 180 ÷ 6；90 天日均 1 × 30
    v2 = dict(v1, daily=1.5, target=50.0, safety=5.0)
    assert S.monthly_and_need(v2) == (45.0, 45.0)                 # 1.5 × 30；目标 50 − 安全库存 5


def test_duplicate_codes_ass_and_colours(tmp_path):
    """
    同一个编号好几行（ASS. 整箱 + 各颜色每箱 1 件）：建议量只算在第一行；别的门店页按同一行号写，
    不能把颜色行的数量（件数当箱数）写到 ASS. 那一行（2026-10-23 LATEX：MARLIA 页 ASS. 变成 27 箱）
    """
    from openpyxl import Workbook, load_workbook
    p = str(tmp_path / "SCHEDA TEST.xlsx")
    wb = Workbook()
    wb.active.title = "SCHEDA ANAGRAFICA FORNITORI "
    for name in ("FOIANO", "MARLIA"):
        ws = wb.create_sheet(name)
        ws.append(["NO.", "Cod.Art.", "Codice EAN", "Descrizione Prodotto", "PZ*COLLI", "Colli", "PREZZO NETTO"])
        ws.append([1, 100, 800100, "CUSCINO ASS.", 24, None, 4.6])
        for k, col in enumerate(("ROSSO", "BLU", "VERDE"), 2):
            ws.append([k, 100, 800100, "CUSCINO " + col, 1, None, 4.6])
        ws.append([5, 200, 800200, "ALTRO", 6, None, 2.0])
    wb.save(p)
    sc = S.parse_scheda(p)
    assert sc["rowmap"] == {}                         # 两页一样：一行对一行
    for it in sc["items"]:
        it["db_code"] = it["code"]
    S.mark_duplicates([sc])
    assert [it.get("dup_of") for it in sc["items"]] == [None, 2, 2, 2, None]
    d = {"sugg": 27, "daily": 0.2, "inv": 0, "tier": "A", "cycle": 30}
    dem = {1: {"100": d}, 2: {"100": d}, "_algo": None}
    base = S.base_colli(sc, dem, [1, 2])
    assert base == {1: {2: 2}, 2: {2: 2}}             # 只有 ASS. 那一行：27 件 ÷ 24 → 2 箱
    out = str(tmp_path / "o.xlsx")
    S.write_order_file(sc, base, out)
    wb2 = load_workbook(out)
    for name in ("FOIANO", "MARLIA"):
        assert [wb2[name].cell(r, 6).value for r in range(2, 7)] == [2, None, None, None, None]


def _pdv_file(path, fatt_per_store=True, grids=("GRIGLIA ",), moq_amount=300):
    """门店采购（PDV）样子的 SCHEDA：ANAGRAFICA（起订金额）、一页或几页 GRIGLIA、Dati Filiale 页"""
    from openpyxl import Workbook
    wb = Workbook()
    ana = wb.active
    ana.title = "SCHEDA ANAGRAFICA FORNITORI"
    ana.cell(2, 2, "RAGIONE SOCIALE:")
    ana.cell(2, 3, "PROVA SRL")
    ana.cell(35, 2, "MINIMO ORDINE VOLUME:")
    ana.cell(36, 2, "MINIMO ORDINE IMPORTO:")
    ana.cell(36, 3, moq_amount)
    for k, g in enumerate(grids):
        ws = wb.create_sheet(g)
        ws.cell(1, 5, "Fatturazione :")
        ws.cell(1, 6, "Ragione Sociale: QUALCUNO")
        ws.cell(1, 7, "NUMERO ORDINE :")
        ws.cell(1, 8, "01032026-SF-01")
        ws.cell(2, 5, "Luogo di destinazione :")
        ws.cell(2, 6, "CASA & TE SRL \nVia d'arezzo 159/r Foiano")
        ws.cell(2, 7, "Data Ordine:")
        ws.cell(2, 8, "00/00/2026")
        for c, h in enumerate(["NO.", "Immagine", "Cod.Art.", "Codice Fornitore", "Codice EAN", "Descrizione Prodotto",
                               "PZ*COLLI", "Colli", "Qtà Ordine", "PREZZO\n NETTO", "IMPORTO"], 1):
            ws.cell(5, c, h)
        for i in range(3):
            r = 6 + i
            ws.cell(r, 3, str(100 * (k + 1) + i))
            ws.cell(r, 5, str(8000000000000 + 100 * (k + 1) + i))
            ws.cell(r, 6, f"ART {k}-{i}")
            ws.cell(r, 7, 6)
            ws.cell(r, 10, 2.0)
    ds = wb.create_sheet("Dati Filiale 2" if fatt_per_store else "Dati Filiali 1")
    ds.append(["Fatturazione", "Luogo di destinazione"])
    dests = ["CASA & TE SRL \nVia d'arezzo 159/r 52045 Foiano Della Chiana", "CASA & TE GROUP SRL\nViale europa 434 Capannori",
             "CASA & TE S.FILIPPO SRL\nVia di tiglio 1893 Lucca", "CASA & TE S.MARCO SRL\nVia del brennero 844",
             "CASA & TE S.CONCORDIO SRL\nVIA SAVONAROLA, 50"]
    fatts = ["Ragione Sociale: CASA & TE SRL\nSede legale：Via d'arezzo Foiano",
             "Ragione Sociale: CASA & TE GROUP SRL\nSede legale：Capannori",
             "Ragione Sociale: CASA & TE S.FILIPPO SRL\nSede legale：Via di tiglio",
             "Ragione Sociale: CASA & TE S.MARCO SRL\nSede legale：Via brennero",
             # 用户的文件里 S.CONCORDIO 这一行的 PEC 写成了 smarco：要按第一行（公司名）认
             "Ragione Sociale: CASA & TE S.CONCORDIO SRL\nPEC：casaete.smarco@pec.it"]
    for i in range(5):
        ds.append([fatts[i] if fatt_per_store or i == 0 else None, dests[i]])
    if not fatt_per_store:
        ds.cell(2, 1, "Ragione Sociale: BMS PARTNERS SRL\nSede legale：Via di tiglio 1893")
        ds.append([None, " BMS PARTNERS SRL\nVia di tiglio 1893 Lucca"])
    wb.save(path)


def test_pdv_scheda_one_order_per_store(tmp_path):
    """门店采购（2026-11-03）：一页 GRIGLIA、每家店各自达到起订量、一家店一个文件（送货地址 / 开票抬头 / 订单号）"""
    from openpyxl import load_workbook
    p = str(tmp_path / "SCHEDA PROVA 0726..xlsx")
    _pdv_file(p)
    sc = S.parse_scheda(p)
    assert sc["name"] == "PROVA" and sc.get("pdv") and sorted(sc["sheets"]) == [1, 2, 3, 4, 5]
    assert len(sc["items"]) == 3 and sc["moq_amount"] == 300
    assert sorted(sc["pdv"]["dest"]) == [1, 2, 3, 4, 5] and "CONCORDIO" in sc["pdv"]["fatt"][5]
    sc["day"] = "周一"
    rows = [it["row"] for it in sc["items"]]
    # FOIANO 30 箱 × 12 € = 360 ≥ 300 → 下单；MARLIA 5 箱 = 60 € → 这次不订（门店采购没有 BMS）
    colli = {1: {rows[0]: 30}, 2: {rows[1]: 5}}
    plan = S.plan_supplier(sc, {}, {"max_amount_gap": 20, "max_colli_gap": 1}, [1, 2, 3, 4, 5], colli)
    assert plan["stores"][1]["status"] == "direct" and plan["stores"][2]["status"] == "wait" and plan["bms"] is None
    written = S.write_plans([("周一", sc, plan)], str(tmp_path / "out"), "0311")
    assert len(written) == 1 and written[0][2] == "FOIANO" and written[0][0].endswith("Ordine_PROVA_FOIANO_0311.xlsx")
    ws = load_workbook(written[0][0])["GRIGLIA "]
    assert ws["H6"].value == 30 and ws["H7"].value is None
    assert "Foiano" in ws["F2"].value and ws["F1"].value.startswith("Ragione Sociale: CASA & TE SRL")
    assert ws["H1"].value.endswith("-FO-01") and ws["H2"].value.count("/") == 2
    # S.CONCORDIO 的文件：开票抬头是 S.CONCORDIO（不是被 PEC 带偏的 S.MARCO）
    out5 = str(tmp_path / "c.xlsx")
    S.write_pdv_file(sc, 5, {rows[2]: 1}, out5)
    ws5 = load_workbook(out5)["GRIGLIA "]
    assert "CONCORDIO" in ws5["F1"].value and "CONCORDIO" in ws5["F2"].value and ws5["H8"].value == 1


def test_pdv_invoice_to_bms_and_several_grids(tmp_path):
    """Dati Filiali 1：发票都开给 BMS、货送门店；SAIS 那种好几页 GRIGLIA：几页一起读，各自写回"""
    from openpyxl import load_workbook
    p = str(tmp_path / "SCHEDA SAIS 0726.xlsx")
    _pdv_file(p, fatt_per_store=False, grids=("GRIGLIA ESTIVI", "GRIGLIA INVERNO"))
    sc = S.parse_scheda(p)
    assert sc["pdv"]["fatt_all"].startswith("Ragione Sociale: BMS") and sc["pdv"]["sheets"] == ["GRIGLIA ESTIVI", "GRIGLIA INVERNO"]
    assert len(sc["items"]) == 6 and len({it["row"] for it in sc["items"]}) == 6
    last = sc["items"][-1]
    assert last["sheet"] == "GRIGLIA INVERNO" and last["row"] == 8 + S.PDV_ROW_BLOCK
    out = str(tmp_path / "o.xlsx")
    S.write_pdv_file(sc, 3, {sc["items"][0]["row"]: 2, last["row"]: 4}, out)
    wb = load_workbook(out)
    assert wb["GRIGLIA ESTIVI"]["H6"].value == 2 and wb["GRIGLIA INVERNO"]["H8"].value == 4
    assert wb["GRIGLIA INVERNO"]["H6"].value is None
    for g in ("GRIGLIA ESTIVI", "GRIGLIA INVERNO"):
        assert "BMS" in wb[g]["F1"].value and "FILIPPO" in wb[g]["F2"].value and wb[g]["H1"].value.endswith("-SF-01")


def test_day_folders_italian(tmp_path):
    """门店采购的星期文件夹叫「01 LUNEDI」…：和「周一」一样认"""
    for d in ("01 LUNEDI", "02 MARTEDI", "05 VENERDI"):
        os.makedirs(tmp_path / d)
    open(tmp_path / "01 LUNEDI" / "SCHEDA X 0726.xlsx", "wb").close()
    open(tmp_path / "01 LUNEDI" / "~$SCHEDA X 0726.xlsx", "wb").close()
    dd = S.day_dirs(str(tmp_path))
    assert sorted(dd) == ["周一", "周二", "周五"] and dd["周一"].endswith("01 LUNEDI")
    assert [(d, os.path.basename(p)) for d, p in S.list_schede(str(tmp_path))] == [("周一", "SCHEDA X 0726.xlsx")]


def test_moq_read_once_when_two_threads_ask():
    """打开程序时工作台和总仓采购同时读同一个 SCHEDA 的起订量：只读一次（2026-11-06）"""
    import threading
    import time
    import store_orders as S
    calls = []
    orig = S.parse_moq

    def slow(p):
        calls.append(p)
        time.sleep(0.2)
        return {"raw": p}
    S.parse_moq = slow
    try:
        moq = {}
        ts = [threading.Thread(target=S.load_moq_into, args=(moq, p)) for p in ("a", "a", "b", "a")]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert sorted(calls) == ["a", "b"] and moq == {"a": {"raw": "a"}, "b": {"raw": "b"}}
        S.parse_moq = lambda p: (_ for _ in ()).throw(ValueError("rotto"))
        assert "rotto" in S.load_moq_into(moq, "c")["raw"]
    finally:
        S.parse_moq = orig


def test_pdv_order_file_has_extra_columns_on_every_grid(tmp_path):
    """2026-11-16 用户：门店采购的订单文件也要附加列（GIA · ULT. ARRIVO · QTA ULT. ARR. · ULT. VENDITA · MEDIA MENS. …）"""
    from openpyxl import load_workbook
    p = str(tmp_path / "SCHEDA SAIS 0726.xlsx")
    _pdv_file(p, fatt_per_store=False, grids=("GRIGLIA ESTIVI", "GRIGLIA INVERNO"))
    sc = S.parse_scheda(p)
    for it in sc["items"]:
        it["db_code"] = it["code"]
    x = {"sugg": 6, "inv": 3, "inv_raw": 3, "sales": [2, 4, 6, 12], "tier": "B", "cycle": 30,
         "gia_all": [0, 1, 2, 3, 4, 5], "last_in": "03/11/2026", "last_in_qty": 12.0, "last_sale": "14/11/2026"}
    last = sc["items"][-1]
    dem = {3: {sc["items"][0]["code"]: x, last["code"]: dict(x, last_in="01/10/2026")}}
    extra = S.extra_data(sc, dem, {}, None)
    extra, heads = S.file_extra(extra, S.extra_headers())
    out = str(tmp_path / "o.xlsx")
    S.write_pdv_file(sc, 3, {sc["items"][0]["row"]: 2}, out, extra=extra.get(3), headers=heads)
    wb = load_workbook(out)
    for g, row, arr in (("GRIGLIA ESTIVI", 6, "03/11/2026"), ("GRIGLIA INVERNO", 8, "01/10/2026")):
        ws = wb[g]
        assert [ws.cell(5, c).value for c in range(12, 17)] == \
               ["GIA", "ULT. ARRIVO", "QTA ULT. ARR.", "ULT. VENDITA", "MEDIA MENS."]      # 接在 IMPORTO（K）后面
        assert [ws.cell(row, c).value for c in range(12, 16)] == [3, arr, 12, "14/11/2026"]
    assert wb["GRIGLIA ESTIVI"]["H6"].value == 2
    assert wb["GRIGLIA ESTIVI"].cell(7, 12).value is None          # 数据库没有的商品留空
