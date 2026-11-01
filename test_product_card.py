"""门店促销判断、商品详情窗口的查询（用 sqlite 模拟数据库）"""
import os
import sys
import sqlite3
import threading
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reorder_service as R


def test_promo_pick_and_dates():
    from datetime import date
    t = date(2026, 11, 1)
    assert R._active("2026-10-01", "2026-12-31", t) and R._active(None, "", t) and R._active("0000-00-00", None, t)
    assert not R._active("2026-11-02", None, t) and not R._active(None, "2026-10-31", t)
    assert R._in_store("0,1,2,3", 2) and not R._in_store("1", 2) and R._in_store("", 5) and R._in_store(None, 5)
    p = R.promo_pick(6.49, [("vip", 4.99, None), ("offer", 5.5, "2026-12-31"), ("promo", 0, None)])
    assert p["kind"] == "vip" and p["label"] == "会员价" and abs(p["pct"] - (1 - 4.99 / 6.49)) < 1e-9
    assert R.promo_pick(2.0, [("vip", 2.0, None), ("vip", 0, None)]) is None          # 不比标价便宜：不算
    assert R.promo_pick(0, [("vip", 1, None)]) is None


def _db():
    con = sqlite3.connect(":memory:", check_same_thread=False)
    lock = threading.Lock()
    con.execute("CREATE TABLE vendite_list_1 (CodArticolo INT, Data TEXT, Qta REAL, TotRighe REAL, TotCodIva REAL)")
    con.execute("CREATE TABLE vendite_list_2 (CodArticolo INT, Data TEXT, Qta REAL, TotRighe REAL, TotCodIva REAL)")
    for s in (3, 4, 5):
        con.execute(f"CREATE TABLE vendite_list_{s} (CodArticolo INT, Data TEXT, Qta REAL, TotRighe REAL, TotCodIva REAL)")
    con.execute("CREATE TABLE prodotti (CodArticolo INT, CodBarre TEXT, Descrizione TEXT, ArticoloForn TEXT, PrezzoForn REAL,"
                " PrezzoVedi1 REAL, CodIva INT, qtasmallbox INT, Categoria INT, Sottocategoria INT, IDFornitore INT,"
                " Marca TEXT, DataCreazione TEXT, PrezzoVedi3 REAL, PrezzoVediOfferta TEXT, PrezzoVediOfferta_inizio TEXT,"
                " PrezzoVediOfferta_fine TEXT)")
    con.execute("CREATE TABLE prodotti_vip (CodArticolo INT, PrezzoVedi REAL, Prezzofine TEXT)")
    con.execute("CREATE TABLE promozioni (l INT, Data_a TEXT, Data_f TEXT, negozio TEXT)")
    con.execute("CREATE TABLE promozioni_list (promozioni_l INT, CodArticolo INT, Prezzo_p REAL, sconto INT, invalid INT)")
    con.execute("CREATE TABLE fornitore (IDFornitore INT, Nome TEXT)")
    con.execute("CREATE TABLE categorie (Categorie INT, SottoCategorie INT, Descrizione TEXT)")
    con.execute("CREATE TABLE giacenza (CodArticolo INT, G0 REAL, G1 REAL, G2 REAL, G3 REAL, G4 REAL, G5 REAL)")
    con.execute("CREATE TABLE caricolistalist (l INT, CodArticolo INT, Cdata TEXT, Quantita REAL, Tipo TEXT,"
                " Fornitore INT, Ddt TEXT, Negozio INT, ListaId INT, Prezzo_A REAL)")
    con.execute("CREATE TABLE caricolista (ListaId INT, Fnumero TEXT)")
    con.execute("INSERT INTO caricolista VALUES (5, 'FT 12'), (6, 'NC 3')")
    con.execute("INSERT INTO prodotti VALUES (1,'8001','Detersivo','F-1',0.5,2.0,22,6,2,5,7,'ACE','2025-03-01',1.6,'',NULL,NULL)")
    con.execute("INSERT INTO prodotti VALUES (2,'8002','Sapone','F-2',0.3,1.0,22,12,2,5,7,NULL,NULL,0,'0.79','2026-01-01','2099-12-31')")
    for c in range(3, 8):
        con.execute("INSERT INTO prodotti (CodArticolo, PrezzoVedi1, PrezzoVedi3, PrezzoVediOfferta, PrezzoVediOfferta_fine)"
                    " VALUES (?, 10, 0, ?, ?)", (c, "5" if c == 3 else "", "2020-01-01" if c == 3 else None))
    con.execute("INSERT INTO prodotti_vip VALUES (4, 7.5, '2099-01-01')")       # 会员限时价，还有效
    con.execute("INSERT INTO prodotti_vip VALUES (5, 6.0, '2020-01-01')")       # 过期
    con.execute("INSERT INTO promozioni VALUES (9, '2020-01-01', '2099-12-31', '0,1,2,3')")
    con.execute("INSERT INTO promozioni VALUES (10, '2020-01-01', '2099-12-31', '2')")    # 只有门店 2
    con.execute("INSERT INTO promozioni_list VALUES (9, 6, 0, 30, 0)")        # 打 7 折 → 7.0
    con.execute("INSERT INTO promozioni_list VALUES (10, 7, 5, 0, 0)")
    con.execute("INSERT INTO fornitore VALUES (7,'MEGA CASH')")
    con.execute("INSERT INTO categorie VALUES (2,NULL,'DETERSIVI')")
    con.execute("INSERT INTO categorie VALUES (2,5,'BUCATO')")
    con.execute("INSERT INTO giacenza VALUES (1,10,3,-1,0,2,5)")
    today = date.today()
    d = lambda k: (datetime.combine(today, datetime.min.time()) - timedelta(days=k) + timedelta(hours=10)).strftime("%Y-%m-%d %H:%M:%S")
    for k in range(5):                                    # 商品 1：最近 5 天一件 1.6 元（标价 2）→ 促销
        con.execute("INSERT INTO vendite_list_1 VALUES (1,?,1,1.6,0.29)", (d(k),))
        con.execute("INSERT INTO vendite_list_1 VALUES (2,?,2,2.0,0.36)", (d(k),))      # 商品 2：照标价卖
    con.execute("INSERT INTO vendite_list_1 VALUES (1,?,3,6.0,1)", (d(20),))            # 20 天前：不在 7 天里
    con.execute("INSERT INTO vendite_list_2 VALUES (1,?,4,8.0,1)", (d(1),))
    con.execute("INSERT INTO caricolistalist VALUES (11,1,?,24,'CA',7,'',1,5,0.5)", (d(3),))
    con.execute("INSERT INTO caricolistalist VALUES (12,1,?,-2,'RE',7,'',1,6,0.5)", (d(1),))
    con.execute("INSERT INTO caricolistalist VALUES (13,1,?,48,'CA',7,'',2,9,0.5)", (d(2),))    # 别的门店的入库
    old = R.__dict__.get("_pd_read_sql")
    import pandas as pd
    orig = pd.read_sql

    def fake(q, engine, params=None):
        p = tuple(str(x) if hasattr(x, "strftime") else x for x in (params or ()))
        with lock:
            return orig(q.replace("%s", "?"), con, params=p)
    db = type("DB", (), {"engine": None})()
    return db, fake, orig, old


def test_query_promos_and_product_card():
    import pandas as pd
    db, fake, orig, _ = _db()
    pd.read_sql = fake
    try:
        pr = R.query_promos(db, 1, ["1", "2", "3", "4", "5", "6", "7", "x"])
        assert set(pr) == {"1", "2", "4", "6"}, pr      # 1 会员价、3 特价过期、5 限时价过期、7 不是这家店
        assert pr["1"]["kind"] == "vip" and abs(pr["1"]["price"] - 1.6) < 1e-9 and abs(pr["1"]["pct"] - 0.2) < 1e-9
        assert pr["2"]["kind"] == "offer" and pr["2"]["end"] == date(2099, 12, 31)
        assert pr["4"]["kind"] == "vip_t" and pr["6"]["kind"] == "promo" and abs(pr["6"]["price"] - 7.0) < 1e-9
        assert set(R.query_promos(db, 2, ["7"])) == {"7"}
        card = R.product_card(db, 1, days=60, store=1)
        assert not card["errors"], card["errors"]
        assert card["prod"]["FornitoreNome"] == "MEGA CASH" and card["prod"]["CategoriaNome"] == "DETERSIVI"
        assert card["stock"][1] == 3 and card["stock"][2] == -1 and card["stock"][0] == 10
        cols, rows = card["receipts"]
        assert [h for _c, h, _t in cols][:5] == ["日期", "数量", "类型", "商家", "发票"]
        assert len(rows) == 2 and not card["receipts_all"]                # 只有门店 1 的入库
        assert card["receipts_upto"] == date.today() - timedelta(days=1)   # 入库表最新的日期（数据库停更时窗口里提醒）
        assert len(R.product_card(db, 1, days=60)["receipts"][1]) == 3    # 不给门店：全部
        assert rows[0]["Quantita"] == -2 and rows[1]["Fornitore"] == "MEGA CASH" and rows[1]["Fattura"] == "FT 12"   # 新的在前，商家换成名字，发票号来自入库单
        assert sum(q for q, _t in card["daily"][1].values()) == 8 and sum(q for q, _t in card["daily"][2].values()) == 4
        table = R.daily_sales_table(card["daily"], 60)
        assert table[0][0] == date.today() and table[0][1] == {1: 1}
        assert any(row[1].get(2) == 4 and abs(row[2][2] - 2.0) < 1e-9 for row in table)
        fields = dict(R.card_fields(card["prod"]))
        assert fields["商家"] == "7 · MEGA CASH" and fields["品牌"] == "ACE" and fields["售价"] == "€2.00" and fields["会员价"] == "€1.60"
        assert fields["类别"] == "DETERSIVI / BUCATO" and fields["每箱件数"] == "6" and "2025-03-01" in fields["创建 / 修改"]
    finally:
        pd.read_sql = orig


def test_receipt_columns_unknown_kept_after_known():
    cols = R.receipt_columns(["ID", "CodArticolo", "Utente", "Quantita", "Cdata", "Prezzo"])
    assert [(c, h) for c, h, _t in cols] == [("Cdata", "日期"), ("Quantita", "数量"), ("Prezzo", "进价"),
                                             ("ID", "ID"), ("Utente", "Utente")]
    assert R.card_fields({"CodArticolo": 5, "Marca": float("nan"), "CodIva": None}) == [("编号", "5")]
