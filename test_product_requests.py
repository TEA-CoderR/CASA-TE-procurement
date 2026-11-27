# -*- coding: utf-8 -*-
"""门店申请（扫码枪上问办公室的商品）：整理、程序先查、合理性检查、发给门店的话、扫码枪目录跟着回复变"""
from datetime import datetime, timezone

import product_requests as PR
import scanner_catalog as SC

NOW = datetime(2026, 11, 27, 11, 0, tzinfo=timezone.utc)      # 星期五


def _r(id_, store, ean, status="open", created="2026-11-27T08:00:00.000Z", **kw):
    d = {"id": id_, "entityCode": store, "ean": ean, "name": "", "kind": "unknown", "reason": "customers", "boxes": None,
         "note": None, "hasPhoto": False, "status": status, "answerKind": None, "answer": None, "answerEan": None,
         "answerName": None, "createdAt": created, "updatedAt": created, "handledAt": None}
    d.update(kw)
    return d


def test_group_by_barcode_waiting_first_and_late_after_two_workdays():
    reqs = [_r(1, 1, "0800123", boxes=2, created="2026-11-23T09:00:00Z"),          # 星期一：等了 4 个工作日
            _r(2, 3, "800123", boxes=1, note="nuovo formato"),
            _r(3, 2, "555", status="answered", answerKind="no", handledAt="2026-11-26T10:00:00Z"),
            _r(4, 4, "777", status="info", answerKind="info"),
            _r(5, 5, "999", status="cancelled")]
    gs = PR.group(reqs, NOW)
    assert [g["ean"] for g in gs] == ["800123", "777", "555"]
    g = gs[0]
    assert g["stores"] == [1, 3] and g["boxes"] == 3 and g["status"] == "open"
    assert g["waited"] == 4 and g["late"]
    assert PR.waiting_count(reqs) == 1


def test_aliases_only_from_use_answers():
    reqs = [_r(1, 1, "800123", status="answered", answerKind="use", answerEan="08001090000001", answerName="SVELTO",
               handledAt="2026-11-26T10:00:00Z"),
            _r(2, 2, "555", status="answered", answerKind="no")]
    assert PR.aliases(reqs) == {"800123": ("8001090000001", "SVELTO")}


def test_checks_suggest_new_barcode_of_a_griglia_item():
    grid = {"items": {"8001090213465": {"name": "SVELTO PIATTI LIMONE 1000 ML"},
                      "8000000000001": {"name": "DIXAN LAVATRICE 40 MISURINI"}}, "struck": {}}
    g = PR.group([_r(1, 1, "8001090999999", name="SVELTO PIATTI LIMONE 1000 ML")], NOW)[0]
    ch = PR.checks(g, grid, {"_offers": {}}, {}, {})
    assert ch["similar"][0]["ean"] == "8001090213465" and ch["suggest"] == "use"
    # 报价里有、不在 Griglia、不像 Griglia 里的：加进 Griglia
    g2 = PR.group([_r(2, 1, "123456789", name="CANDEGGINA ACE 2 L")], NOW)[0]
    ch2 = PR.checks(g2, grid, {"_offers": PR.offers_index({"MELONI": {"123456789": {"price": 1.2, "pack": 12}}})}, {}, {})
    assert ch2["suggest"] == "griglia" and ch2["offers"] == [("MELONI", 1.2, 12.0)]
    # 哪里都查不到：要更多信息
    g3 = PR.group([_r(3, 1, "42")], NOW)[0]
    assert PR.checks(g3, grid, {}, {}, {})["suggest"] == "info"


def test_box_check_blocks_pieces_written_as_boxes():
    assert PR.box_check(2, 6, 1.5)[0] is None
    assert PR.box_check(8, 6, 1.5)[0] == "warn"
    assert PR.box_check(25, 6, 1.5)[0] == "block"
    assert PR.box_check(5, 56, 4.25)[0] == "block"          # 5 箱 × 56 件 × €4.25 = €1,190
    assert PR.box_check(0)[0] is None


def test_message_in_italian_with_boxes():
    assert PR.message("use", name="SVELTO", boxes=2) == "Ordina al suo posto: SVELTO. Messo in lista: 2 colli."
    assert PR.message("no", reason="Il prezzo è troppo alto") == "Non lo trattiamo: Il prezzo è troppo alto."
    assert "HENKEL" in PR.message("direct", supplier="HENKEL")
    assert PR.message("griglia", boxes=1).endswith("Messo in lista: 1 collo.")


def test_catalog_follows_answers_new_barcode_and_added_to_griglia():
    grid = {"items": {"8001090213465": {"name": "SVELTO PIATTI", "pack": 12}}, "struck": {}}
    meta, rows = SC.build(grid, {}, aliases={"8001090999999": ("8001090213465", "SVELTO PIATTI"),
                                             "8001090213465": ("1", "x")},
                          added={"123456789": {"name": "CANDEGGINA ACE 2 L", "pack": 6}})
    by = {r[0]: r for r in rows}
    assert by["8001090999999"][2] == 2 and by["8001090999999"][4] == "8001090213465"     # SOSTITUITO → SVELTO
    assert by["8001090213465"][2] == 1 and by["8001090213465"][4] == ""                  # Griglia 商品本身不动
    assert by["123456789"][2] == 1 and by["123456789"][7] == 6                           # 程序里加进 Griglia 的


def test_db_error_shows_reason_not_the_sql_and_retries_lost_connection():
    import reorder_service as R
    ph = ", ".join(["%s"] * 2000)
    e = Exception(f"Execution failed on sql 'SELECT CodArticolo, CodBarre FROM prodotti WHERE CodBarre IN ({ph})': "
                  "(2013, 'Lost connection to MySQL server during query')")
    assert R.db_error_text(e) == "(2013, 'Lost connection to MySQL server during query')"
    calls = []

    class Db:
        engine = None
    import pandas as pd
    real = pd.read_sql

    def flaky(q, eng, params=None):
        calls.append(1)
        if len(calls) == 1:
            raise e
        return pd.DataFrame({"CodArticolo": [7], "CodBarre": ["800"]})
    pd.read_sql = flaky
    try:
        assert R.read_sql_retry("q", Db(), (), wait=0).iloc[0]["CodArticolo"] == 7
        assert len(calls) == 2
        calls.clear()
        pd.read_sql = lambda *a, **k: (_ for _ in ()).throw(Exception("(1054, 'Unknown column')"))
        try:
            R.read_sql_retry("q", Db(), (), wait=0)
            raise AssertionError("should raise")
        except Exception as x:
            assert "1054" in str(x)
    finally:
        pd.read_sql = real


def test_two_publishes_do_not_query_the_database_twice(tmp_path, monkeypatch):
    import threading
    import time
    n = []

    def slow(settings, rows, quotes, log, with_ctx=False):
        n.append(1)
        time.sleep(0.3)
        return {1: [["8001", 1]]}, {"horizon": 12}
    monkeypatch.setattr(SC, "store_data", slow)
    path = str(tmp_path / "s.json")
    out = []
    ts = [threading.Thread(target=lambda: out.append(SC.cached_store_data({}, [], {}, log=lambda *_: None, force=True,
                                                                         path=path, with_ctx=True))) for _ in range(2)]
    for t in ts:
        t.start()
        time.sleep(0.05)
    for t in ts:
        t.join()
    assert len(n) == 1 and out[0] == out[1]
