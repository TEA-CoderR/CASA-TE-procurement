"""门店促销判断、商品详情窗口的查询（用 sqlite 模拟数据库）"""
import os
import sys
import sqlite3
import threading
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reorder_service as R


def test_promo_info_rules():
    assert R.promo_info(10, 8.0, 10, 1.0)["pct"] > 0.19          # 标价 1 元，实卖 0.8 → 促销 −20%
    assert R.promo_info(10, 9.7, 10, 1.0) is None                 # 只便宜 3%：不算
    assert R.promo_info(10, 8.0, 3, 1.0) is None                  # 只有 3 / 10 件是打折价（会员卡、个别折扣）：不算
    assert R.promo_info(0, 0, 0, 1.0) is None and R.promo_info(5, 4, 5, 0) is None
    assert R.promo_info(5, 0, 5, 1.0) is None                     # 白送的（金额 0）不算
    assert R.promo_info(float("nan"), 1, 1, 1) is None


def _db():
    con = sqlite3.connect(":memory:", check_same_thread=False)
    lock = threading.Lock()
    con.execute("CREATE TABLE vendite_list_1 (CodArticolo INT, Data TEXT, Qta REAL, TotRighe REAL, TotCodIva REAL)")
    con.execute("CREATE TABLE vendite_list_2 (CodArticolo INT, Data TEXT, Qta REAL, TotRighe REAL, TotCodIva REAL)")
    for s in (3, 4, 5):
        con.execute(f"CREATE TABLE vendite_list_{s} (CodArticolo INT, Data TEXT, Qta REAL, TotRighe REAL, TotCodIva REAL)")
    con.execute("CREATE TABLE prodotti (CodArticolo INT, CodBarre TEXT, Descrizione TEXT, ArticoloForn TEXT, PrezzoForn REAL,"
                " PrezzoVedi1 REAL, CodIva INT, qtasmallbox INT, Categoria INT, Sottocategoria INT, IDFornitore INT,"
                " Marca TEXT, DataCreazione TEXT)")
    con.execute("CREATE TABLE fornitore (IDFornitore INT, Nome TEXT)")
    con.execute("CREATE TABLE categorie (Categorie INT, SottoCategorie INT, Descrizione TEXT)")
    con.execute("CREATE TABLE giacenza (CodArticolo INT, G0 REAL, G1 REAL, G2 REAL, G3 REAL, G4 REAL, G5 REAL)")
    con.execute("CREATE TABLE caricolistalist (IDCarico INT, CodArticolo INT, Cdata TEXT, Quantita REAL, Tipo TEXT,"
                " IDFornitore INT, NumFattura TEXT)")
    con.execute("INSERT INTO prodotti VALUES (1,'8001','Detersivo','F-1',0.5,2.0,22,6,2,5,7,'ACE','2025-03-01')")
    con.execute("INSERT INTO prodotti VALUES (2,'8002','Sapone','F-2',0.3,1.0,22,12,2,5,7,NULL,NULL)")
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
    con.execute("INSERT INTO caricolistalist VALUES (11,1,?,24,'CA',7,'FT 12')", (d(3),))
    con.execute("INSERT INTO caricolistalist VALUES (12,1,?,-2,'RE',7,'NC 3')", (d(1),))
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
        pr = R.query_promos(db, 1, ["1", "2", "x"])
        assert set(pr) == {"1"}
        assert abs(pr["1"]["sold"] - 1.6) < 1e-9 and abs(pr["1"]["pct"] - 0.2) < 1e-9 and pr["1"]["qty"] == 5
        card = R.product_card(db, 1, days=60)
        assert not card["errors"], card["errors"]
        assert card["prod"]["FornitoreNome"] == "MEGA CASH" and card["prod"]["CategoriaNome"] == "DETERSIVI"
        assert card["stock"][1] == 3 and card["stock"][2] == -1 and card["stock"][0] == 10
        cols, rows = card["receipts"]
        assert [h for _c, h, _t in cols][:5] == ["日期", "数量", "类型", "商家", "发票"]
        assert rows[0]["Quantita"] == -2 and rows[1]["IDFornitore"] == "MEGA CASH (7)"     # 新的在前，商家换成名字
        assert sum(q for q, _t in card["daily"][1].values()) == 8 and sum(q for q, _t in card["daily"][2].values()) == 4
        table = R.daily_sales_table(card["daily"], 60)
        assert table[0][0] == date.today() and table[0][1] == {1: 1}
        assert any(row[1].get(2) == 4 and abs(row[2][2] - 2.0) < 1e-9 for row in table)
        fields = dict(R.card_fields(card["prod"]))
        assert fields["商家"] == "7 · MEGA CASH" and fields["品牌"] == "ACE" and fields["售价"] == "€2.00"
        assert fields["类别"] == "DETERSIVI / BUCATO" and fields["每箱件数"] == "6" and "2025-03-01" in fields["创建 / 修改"]
    finally:
        pd.read_sql = orig


def test_receipt_columns_unknown_kept_after_known():
    cols = R.receipt_columns(["ID", "CodArticolo", "Utente", "Quantita", "Cdata", "Prezzo"])
    assert [(c, h) for c, h, _t in cols] == [("Cdata", "日期"), ("Quantita", "数量"), ("Prezzo", "进价"),
                                             ("ID", "ID"), ("Utente", "Utente")]
    assert R.card_fields({"CodArticolo": 5, "Marca": float("nan"), "CodIva": None}) == [("编号", "5")]
