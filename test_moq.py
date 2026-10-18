"""门店配货：起订量读取（单位识别）、手动修改、写回 suppliers_config.json；手动改去向不误删别的星期的文件"""
import json
import math
import os

import store_orders as S


# ---------------------------------------------------------------------
# 起订量读取：按文字里的单位判断，不按填在哪一行
# ---------------------------------------------------------------------
def cell(v, kind="colli", name="X"):
    return S.parse_moq_cell(v, kind, name)


def test_amount_written_in_volume_row():
    # LAMPA：金额写在「箱数」那一行 → 金额 €100（8€ 是运费，不算）
    assert cell("100€(sotto 100€ c'è 8€ di trasp.)", "colli", "LAMPA SPA") == {"amount": 100.0, "uncertain": False}
    assert cell("300€, 8€ SPESA SOTTO 300€", "amount")["amount"] == 300
    assert cell("600 ERUO", "amount")["amount"] == 600          # 拼错的 EURO 也认


def test_pallets_any_row():
    assert cell("7 PANCALI", "amount")["pallets"] == 7            # 写在「金额」那一行也是板
    assert cell("                1 PEDANE ", "colli")["pallets"] == 1
    assert cell("1 BANCALE")["pallets"] == 1
    assert cell("4 PEDANE")["pallets"] == 4


def test_colli_and_plain_numbers():
    assert cell("1 COLLO")["colli"] == 1
    assert cell("40 COLLI", "amount")["colli"] == 40
    assert cell(400, "amount") == {"amount": 400.0}
    assert cell(13, "colli") == {"colli": 13.0}
    assert cell("口  SI :     口  NO") == {}
    assert cell(None) == {}


def test_two_amounts_pick_by_supplier_name():
    assert cell("500€ GIMI  600€ VILEDA", "amount", "FHP(VILEDA)") == {"amount": 600.0, "uncertain": False}
    assert cell("500€ GIMI  600€ VILEDA", "amount", "FHP (GIMI)") == {"amount": 500.0, "uncertain": False}
    r = cell("TOYS:400€        CRAYOLA 250€", "colli", "BINNEY&SMITH")
    assert r["amount"] == 400 and r["uncertain"]                  # 名字对不上 → 拿不准


def test_unsure_cases():
    assert cell("Quantità ordinabile in multipli di 10 colli.", "amount") == {"multiple": 10.0, "uncertain": True}
    assert cell("Su 1000€ 50+5%", "amount")["uncertain"]
    assert cell("900-1000", "amount") == {"amount": 900.0, "uncertain": True}


def test_override_and_effective():
    m = {"colli": 0, "amount": 0, "pallets": 7, "per_pallet": 0, "multiple": 0, "uncertain": False, "raw": "7 PANCALI",
         "company": "", "source": "file"}
    assert math.isinf(S.moq_effective(m)[0])                       # 按板但不知道每板几箱
    m2 = S.apply_override(m, {"pallets": 7, "per_pallet": 20, "amount": 0})
    assert m2["source"] == "manual" and S.moq_effective(m2) == (140.0, 0.0)
    assert S.moq_text(m2) == "7 板（20 箱/板）"
    assert S.moq_text(S.apply_override(m, {"amount": 250})) == "€250"


def _scheda(moq, name="TEST", path=""):
    items = [{"row": 6, "code": "1", "ean": "", "desc": "A", "pack": 10, "price": 1.0},
             {"row": 7, "code": "2", "ean": "", "desc": "B", "pack": 6, "price": 2.5}]
    sc = {"name": name, "path": path, "items": items, "sheets": dict(S.STORE_SHEETS), "cols": {"colli": 8}, "rowmap": {}}
    return S.set_moq(sc, moq)


FILL = {"max_amount_gap": 20, "max_colli_gap": 3, "max_stock_days": 60, "fill_bms": True}


def test_pallet_without_size_never_direct_but_can_force():
    sc = _scheda({"pallets": 1})
    dem = {1: {"1": {"sugg": 500, "daily": 5, "inv": 0}}, 2: {"1": {"sugg": 300, "daily": 5, "inv": 0}}}
    p = S.plan_supplier(sc, dem, FILL, [1, 2])
    assert p["stores"][1]["status"] == "bms" and p["bms"]["status"] == "short"
    p = S.plan_supplier(sc, dem, FILL, [1, 2], mode="all_bms")
    assert p["bms"]["status"] == "forced"
    sc = _scheda({"pallets": 1, "per_pallet": 40})                # 填了每板 40 箱 → 50 箱够
    p = S.plan_supplier(sc, dem, FILL, [1, 2])
    assert p["stores"][1]["status"] == "direct" and p["stores"][2]["status"] == "bms"


# ---------------------------------------------------------------------
# suppliers_config.json
# ---------------------------------------------------------------------
DETAILS = {"352": {"name": "LAMPA SPA", "min_order_amount": 100},
           "475": {"name": "INDUSTRAUMBRA SPA", "min_order_amount": 300},
           "1120": {"name": "FHP DI R. FREUDENBERG S.A.S(GIMI)", "min_order_amount": 1000},
           "962": {"name": "HD GROUP S.R.L.", "min_order_amount": 300},
           "312": {"name": "BEL FLOWERS SRL", "min_order_amount": 600}}


def test_match_config_supplier():
    assert S.match_config_supplier("LAMPA SPA", "", DETAILS) == "352"
    assert S.match_config_supplier("INDUSTRIAUMBRA", "", DETAILS) == "475"     # 拼错一个字母
    assert S.match_config_supplier("FHP (GIMI)", "", DETAILS) == "1120"
    assert S.match_config_supplier("FHP(VILEDA)", "", DETAILS) is None          # 只对上 FHP，不自动选
    assert S.match_config_supplier("HD", "", DETAILS) == "962"
    assert S.match_config_supplier("BEL FOLOWERS SRL", "", DETAILS) == "312"
    assert S.match_config_supplier("MISS SPACE", "", DETAILS) is None


def test_save_config_moq_keeps_format(tmp_path):
    p = tmp_path / "suppliers_config.json"
    cfg = {"version": "1.0", "supplier_groups": {"Lunedì": {"suppliers": [352]}},
           "supplier_details": {"352": {"name": "LAMPA SPA", "min_order_amount": 999, "nota": "", "reorder_cycle": 30},
                                "912": {"name": "STEFANPLAST S.P.A. SOCIETÀ", "min_order_amount": 1000}}}
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=4), encoding="utf-8")
    S.save_config_moq(str(p), "352", {"amount": 100.0, "colli": 0, "pallets": 0}, "LAMPA SPA")
    text = p.read_text(encoding="utf-8")
    assert "SOCIETÀ" in text and "\\u00" not in text                   # 中文 / 意大利字母不转义
    d = json.loads(text)
    assert d["supplier_details"]["352"]["min_order_amount"] == 100
    assert d["supplier_details"]["352"]["reorder_cycle"] == 30           # 别的字段不动
    assert d["supplier_details"]["912"]["min_order_amount"] == 1000
    assert d["supplier_groups"] == cfg["supplier_groups"]
    S.save_config_moq(str(p), "352", {"amount": 0, "pallets": 7, "per_pallet": 20}, "LAMPA SPA")
    d = json.loads(p.read_text(encoding="utf-8"))["supplier_details"]["352"]
    assert (d["min_order_amount"], d["min_order_pallets"], d["colli_per_pallet"]) == (0, 7, 20)


# ---------------------------------------------------------------------
# bug：同一家供应商在两个星期都有 SCHEDA，手动改其中一天的去向，不能删掉另一天的订单文件
# ---------------------------------------------------------------------
def test_reallocate_one_keeps_same_supplier_other_day(tmp_path, monkeypatch):
    def fake_write(sc, per_store, out):
        open(out, "w").close()
        return out
    monkeypatch.setattr(S, "write_order_file", fake_write)
    mon = _scheda({"colli": 0, "amount": 0}, "ACME", os.path.join("x", "周一", "SCHEDA ACME.xlsx"))
    wed = _scheda({"colli": 0, "amount": 0}, "ACME", os.path.join("x", "周三", "SCHEDA ACME.xlsx"))
    mon["day"], wed["day"] = "周一", "周三"
    full = lambda n: {"sugg": n, "daily": 1, "inv": 0, "sales": [30, 60, 90, 180], "inv_days": 0, "decision": "", "tier": "A"}
    dem = {1: {"1": full(30)}, 2: {"2": full(12)}}
    calc = {"schede": [mon, wed], "demand": dem, "stores": [1, 2], "base": {}, "days": [30, 60, 90, 180]}
    colli = {mon["path"]: S.base_colli(mon, dem, [1, 2]), wed["path"]: S.base_colli(wed, dem, [1, 2])}
    res = S.allocate(calc, colli, FILL, str(tmp_path), log=lambda *_: None, date_tag="1810")
    wed_files = [w[0] for w in res["written"] if S.written_source(w, res) == wed["path"]]
    assert wed_files and all(os.path.exists(f) for f in wed_files)
    res2 = S.reallocate_one(calc, colli, FILL, res, 0, "all_bms", log=lambda *_: None)
    assert all(os.path.exists(f) for f in wed_files)                     # 周三的文件还在
    assert [w[0] for w in res2["written"] if S.written_source(w, res2) == wed["path"]] == wed_files
    mon_now = [w for w in res2["written"] if S.written_source(w, res2) == mon["path"]]
    assert [w[2] for w in mon_now] == [S.BMS]                             # 周一这家按「全部并入 BMS」重新生成


def test_written_source_old_results():
    """以前的结果只有 6 项：按「星期文件夹 + 供应商名」找回 SCHEDA"""
    sc_a = {"name": "ACME", "path": "A"}
    sc_b = {"name": "ACME", "path": "B"}
    res = {"results": [("周一", sc_a, {}), ("周三", sc_b, {})]}
    w = (os.path.join("out", "周三", "Ordine_ACME_MARLIA_1810.xlsx"), "ACME", "MARLIA", 1, 10.0, "direct")
    assert S.written_source(w, res) == "B"
