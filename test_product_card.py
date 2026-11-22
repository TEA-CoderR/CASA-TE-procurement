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
    con.execute("CREATE TABLE prodotti_vip (CodArticolo INT, PrezzoVedi REAL, Prezzofine TEXT, Udata TEXT)")
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
    con.execute("INSERT INTO prodotti_vip VALUES (4, 7.5, '2099-01-01', ?)", (str(date.today()),))   # 会员价，还没到结束日期
    con.execute("INSERT INTO prodotti_vip VALUES (5, 6.0, '2020-01-01', '2020-01-01')")             # 过期
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
    con.commit()                 # 查询出错时 pandas 会回滚：先提交，测试数据不会丢
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
        assert set(pr) == {"2", "4", "6"}, pr      # 1 只有 PrezzoVedi3（没有结束日期，不算）、3 特价过期、5 会员价过期、7 不是这家店
        assert pr["2"]["kind"] == "offer" and pr["2"]["end"] == date(2099, 12, 31)
        assert pr["4"]["kind"] == "vip_t" and pr["6"]["kind"] == "promo" and abs(pr["6"]["price"] - 7.0) < 1e-9
        assert set(R.query_promos(db, 2, ["7"])) == {"7"}
        con_old = R.query_promos(db, 1, ["4"], today=date(2099, 6, 1))           # 会员价表很久没更新：日志里提醒
        assert any("prodotti_vip" in w for w in con_old.get("_warn", []))
        card = R.product_card(db, 1, days=60, store=1)
        assert not card["errors"], card["errors"]
        assert card["prod"]["FornitoreNome"] == "MEGA CASH" and card["prod"]["CategoriaNome"] == "DETERSIVI"
        assert card["stock"][1] == 3 and card["stock"][2] == -1 and card["stock"][0] == 10
        cols, rows = card["receipts"]
        assert [h for _c, h, _t in cols][:6] == ["日期", "数量", "类型", "商家", "供应商", "发票"]
        assert len(rows) == 2 and not card["receipts_all"]                # 只有门店 1 的入库
        assert card["receipts_upto"] == date.today() - timedelta(days=1)   # 入库表最新的日期（数据库停更时窗口里提醒）
        assert len(R.product_card(db, 1, days=60)["receipts"][1]) == 3    # 不给门店：全部
        assert rows[0]["Quantita"] == -2 and rows[1]["Fornitore"] == 7 and rows[1]["_sup_name"] == "MEGA CASH" and rows[1]["Fattura"] == "FT 12"   # 新的在前，商家换成名字，发票号来自入库单
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


def test_sales_latest_and_stale_days():
    """数据库停止同步时（诊断 36：销售只到 10-24、今天 11-01）能看出来"""
    import pandas as pd
    db, fake, orig, _ = _db()
    pd.read_sql = fake
    try:
        up = R.sales_latest(db)
        assert up is not None and up.date() == date.today()          # 测试数据里今天有销售
    finally:
        pd.read_sql = orig
    assert R.stale_days(datetime(2026, 10, 24, 20, 10), datetime(2026, 11, 1, 13, 3)) == 8
    assert R.stale_days(None) is None



def test_old_receipts_before_2025_01_08_flagged_per_store():
    """2026-11-22 用户：2025-01-08 以前的入库记录是错的 → 列表里标出有这种记录的商品（按门店）"""
    import sqlite3
    import pandas as pd
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE caricolistalist (CodArticolo INT, Cdata TEXT, Negozio INT, Quantita REAL)")
    con.executemany("INSERT INTO caricolistalist VALUES (?,?,?,?)", [
        (1, "2025-01-03 00:00:00", 2, 24), (1, "2024-11-20 00:00:00", 2, 29), (1, "2026-11-10 00:00:00", 2, 6),
        (2, "2025-01-08 00:00:00", 2, 12),            # 当天开始的不算
        (3, "2025-01-02 00:00:00", 1, 12)])           # 别的门店
    orig = pd.read_sql
    pd.read_sql = lambda q, engine, params=None: orig(q.replace("%s", "?"), con, params=params)
    try:
        db = type("DB", (), {"engine": None})()
        got = R.old_receipts(db, 2, ["1", "2", "3", "x"])
    finally:
        pd.read_sql = orig
    assert got == {"1": (date(2024, 11, 20), 2, 53.0)}
