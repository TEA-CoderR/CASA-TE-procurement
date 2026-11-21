"""确认收货登记：谁卸的货、到了什么、拒收原因（2026-11-21）"""
from datetime import datetime

import receipt as RC


def test_people_default_and_settings():
    assert RC.people({}) == ["Ronaldo", "Lucas"]
    assert RC.people({"receiving": {"people": [" Mario ", ""]}}) == ["Mario"]


def test_check_requires_unloader_and_reason():
    assert RC.check({"by": "", "result": "full"})
    assert RC.check({"by": "Lucas", "result": "refused"}) == "请选拒收原因"
    assert RC.check({"by": "Lucas", "result": "refused", "reason": "其他"})
    assert RC.check({"by": "Lucas", "result": "refused", "reason": "其他", "note": "x"}) is None
    assert RC.check({"by": "Lucas", "result": "partial"})
    assert RC.check({"by": "Lucas", "result": "full"}) is None


def test_result_from_lines():
    assert RC.result_of([{"ordered": 2, "got": 2}]) == "full"
    assert RC.result_of([{"ordered": 2, "got": 1}]) == "partial"
    assert RC.result_of([{"ordered": 2, "got": 2}], refused=True) == "refused"


def test_changes_full_partial_refused_keep_history():
    at = datetime(2026, 11, 21, 10, 30)
    o = {"note": "vecchio", "state": "partial"}
    f = RC.changes(o, {"by": "Ronaldo", "at": at, "result": "full", "ddt": " 123 "}, pc="PC1")
    assert f["arrived"] == "2026-11-21" and f["state"] is None and "note" not in f
    e = f["receipts"][0]
    assert e["by"] == "Ronaldo" and e["pc"] == "PC1" and e["ddt"] == "123" and e["at"] == "2026-11-21 10:30"
    o2 = dict(o, receipts=f["receipts"])
    f2 = RC.changes(o2, {"by": "Lucas", "at": at, "result": "partial",
                         "lines": [{"ean": "1", "ordered": 3, "got": 1}, {"ean": "2", "ordered": 1, "got": 1}]})
    assert f2["state"] == "partial" and "缺 1 个商品" in f2["note"] and f2["note"].startswith("vecchio · ")
    assert len(f2["receipts"]) == 2
    f3 = RC.changes({}, {"by": "Lucas", "at": "2026-11-21 11:00", "result": "refused", "reason": "包装破损", "note": "bagnato"})
    assert f3["state"] == "stuck" and f3["arrived"] is None and f3["note"] == "拒收 · 包装破损 · bagnato"
    assert RC.summary(f3["receipts"][-1]) == "21/11 11:00 · Lucas · 拒收 · 包装破损"


def test_receiver_name_not_synced_but_list_is():
    import company_sync as CS
    out = CS.extract({"receiving": {"people": ["Ronaldo"], "last_by": "Lucas"}})
    assert out == {"receiving": {"people": ["Ronaldo"]}}
