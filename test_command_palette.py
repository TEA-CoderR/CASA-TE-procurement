from datetime import date

import command_palette as CP


def _r(sup, status, d, store="FOIANO", open_=None):
    return {"supplier": sup, "sup_key": sup.upper(), "status": status, "date": d, "store_name": store,
            "open": status in ("ordered", "confirmed", "shipped") if open_ is None else open_}


ROWS = [_r("MELONI", "draft", date(2026, 11, 16)), _r("MELONI", "draft", date(2026, 11, 16), "MARLIA"),
        _r("MELONI", "ordered", date(2026, 11, 6)), _r("MELONI", "confirmed", date(2026, 11, 9)),
        _r("MEGA CASH", "arrived", date(2026, 11, 1)), _r("SAIS", "ordered", date(2026, 11, 2))]
PAGES = [("order", "DETERSIVO采购", "采购"), ("result", "比价结果", "采购"), ("tracking", "采购订单跟踪", "订单")]
ACTS = [("mail_check", "邮件核对", "", "mail 邮件"), ("sync", "同步 Excel", "", "excel")]


def test_matches_all_words_any_order():
    assert CP.matches("mel", "MELONI")
    assert CP.matches("cash mega", "MEGA CASH")
    assert not CP.matches("melx", "MELONI")
    assert CP.matches("", "x")


def test_supplier_query_gives_send_and_transit_then_supplier_and_agent():
    g = dict(CP.build_groups("meloni", PAGES, ROWS, ACTS))
    ops = g["操作"]
    assert ops[0]["key"] == "send" and "2" in ops[0]["sub"]
    assert ops[1]["key"] == "transit" and ops[1]["sub"].startswith("2") and "06/11" in ops[1]["sub"]
    assert g["供应商"][0]["title"] == "MELONI"
    assert g["助手"][0]["key"] == "agent" and g["助手"][0]["data"] == "meloni"


def test_page_and_action_search_and_empty_query():
    g = dict(CP.build_groups("比价", PAGES, ROWS, ACTS))
    assert [x["data"] for x in g["页面"]] == ["result"]
    g = dict(CP.build_groups("excel", PAGES, ROWS, ACTS))
    assert g["操作"][0]["key"] == "sync"
    g = dict(CP.build_groups("", PAGES, ROWS, ACTS))
    assert "助手" not in g and "供应商" not in g and len(g["页面"]) == 3
