"""门店扫码枪目录（2026-11-21）：Griglia + 这周报价 + 替代商品 + 直厂 → 扫码枪离线查的目录"""
import json

import pytest

import booking_api as BA
import scanner_catalog as SC
from test_booking_api import FakeSession


@pytest.fixture(autouse=True)
def _cred(monkeypatch, tmp_path):
    monkeypatch.setenv("CASATE_CRED_DIR", str(tmp_path / "cred"))


GRID = {"path": "/x/ELENCO_DETERSIVI.xlsx",
        "items": {"8001": {"name": "SVELTO 1000"}, "8002": {"name": "DIXAN 40"}, "8004": {"name": "NUOVO"}},
        "struck": {"8003": {"name": "SVELTO 980"}}}
QUOTES = {"MELONI": {"8001": {"price": 1.2, "desc": "SVELTO PIATTI 1000"}, "08005": {"price": 2, "desc": "FUORI GRIGLIA"},
                     "8002": {"price": 0, "desc": "omaggio"}},
          "PETRILLO": {"8001": {"price": 1.1, "desc": "SVELTO"}}}


def test_catalog_rows_griglia_state_wholesalers_substitute_direct():
    meta, rows = SC.build(GRID, QUOTES, {"8003": {"ean": "8004", "name": "NUOVO"}},
                          {"ean": {"5005": "BAMA", "8005": "BAMA"}}, week="2026-W47")
    by = {r[0]: r for r in rows}
    assert meta["sources"] == ["MELONI", "PETRILLO"] and meta["directs"] == ["BAMA"]
    assert by["8001"] == ["8001", "SVELTO 1000", 1, [0, 1], "", "", -1]
    assert by["8002"][3] == []                      # 价格 0 的行不算有货
    assert by["8003"][2] == 2 and by["8003"][4:6] == ["8004", "NUOVO"]
    assert by["8005"] == ["8005", "FUORI GRIGLIA", 0, [0], "", "", 0]   # 前面的 0 去掉
    assert by["5005"][2] == 0 and by["5005"][6] == 0
    assert meta["counts"] == {"griglia": 3, "stopped": 1, "items": 6}
    assert all(len(r) == 7 for r in rows)


def test_publish_in_parts_and_only_when_changed(tmp_path, monkeypatch):
    BA.set_token("k" * 40)
    monkeypatch.setattr(SC, "PART", 2)
    calls = []

    def answer(params, body):
        calls.append(body)
        return {"complete": body["part"] == body["parts"], "id": 9, "items": 6}
    s = FakeSession({("POST", "/api/erp/catalog"): (200, answer)})
    meta, rows = SC.build(GRID, QUOTES, {}, None)
    state = str(tmp_path / "state.json")
    res = SC.publish_if_changed({}, meta, rows, "Marco", session=s, log=lambda *_: None, state_path=state)
    assert res["id"] == 9 and [c["part"] for c in calls] == [1, 2, 3] and calls[0]["parts"] == 3
    assert "meta" in calls[0] and "meta" not in calls[1] and calls[0]["by"] == "Marco"
    assert sum(len(c["items"]) for c in calls) == len(rows)
    assert json.load(open(state))["id"] == 9
    assert SC.publish_if_changed({}, meta, rows, "Marco", session=s, log=lambda *_: None, state_path=state) is None
    assert len(calls) == 3                          # 没变不再发


def test_device_accounts_go_through_platform():
    BA.set_token("k" * 40)
    s = FakeSession({("POST", "/api/erp/scanners"): (201, {"device": {"id": 4, "name": "TC22 FOIANO"}, "pairCode": "ABCD2345"}),
                     ("POST", "/api/erp/scanners/4"): (200, {"device": {"id": 4, "active": False}})})
    d, code = SC.add_device({}, "TC22 FOIANO", "1", session=s)
    assert code == "ABCD2345" and s.calls[0][3] == {"name": "TC22 FOIANO", "entityCode": 1}
    assert SC.pretty_code(code) == "ABCD-2345"
    assert SC.set_active({}, 4, False, session=s)["active"] is False
    assert s.calls[1][3] == {"action": "deactivate"}
