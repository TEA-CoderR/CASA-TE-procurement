from datetime import date, timedelta

import tracking_board as B

T = date(2026, 11, 16)


def _r(**k):
    r = {"id": "o1", "status": "ordered", "overdue": False, "pending": False, "date": T - timedelta(days=3),
         "confirmed": None, "shipped": None, "arrived": None, "expected": None, "mails": [], "origin": "app",
         "note": "", "late_by": 0, "supplier": "X", "store_name": "FOIANO", "amount": 10.0}
    r.update(k)
    return r


def test_lane_of_each_status():
    assert B.lane_of(_r(status="draft")) == "draft"
    assert B.lane_of(_r(status="ordered")) == "ordered"
    assert B.lane_of(_r(status="confirmed")) == "confirmed"
    assert B.lane_of(_r(status="shipped")) == "shipped"
    assert B.lane_of(_r(status="partial")) == "shipped"
    assert B.lane_of(_r(status="stuck")) == "late"
    assert B.lane_of(_r(status="confirmed", overdue=True)) == "late"      # 超期优先
    for s in ("arrived", "cancel", "deter"):
        assert B.lane_of(_r(status=s)) is None


def test_card_hint_and_action():
    assert B.card_hint(_r(status="draft", origin="wholesale"))[0].startswith("DETERSIVO")
    text, tone = B.card_hint(_r(status="ordered", pending=True, wait_days=3, wait_level=1), T)
    assert "3" in text and tone == "warn"
    assert B.card_hint(_r(status="ordered", pending=True, wait_days=4, wait_level=2), T)[1] == "err"   # 4 个工作日红
    text, tone = B.card_hint(_r(status="ordered", overdue=True, late_by=9), T)
    assert "9" in text and tone == "err"
    assert B.action_of(_r(status="draft"))[0] == "send"
    assert B.action_of(_r(status="ordered"))[0] == "confirm"
    assert B.action_of(_r(status="confirmed"))[0] == "ship"
    assert B.action_of(_r(status="shipped"))[0] == "arrive"
    assert B.action_of(_r(status="ordered", overdue=True))[0] == "arrive"
    assert B.action_of(_r(status="arrived"))[0] is None


def test_changes_for_clears_partial_state():
    assert B.changes_for("confirm", _r(), T) == {"confirmed": "2026-11-16"}
    assert B.changes_for("ship", _r(), T) == {"shipped": "2026-11-16"}
    assert B.changes_for("arrive", _r(status="partial"), T) == {"arrived": "2026-11-16", "state": None}
    assert B.changes_for("arrive", _r(status="shipped"), T) == {"arrived": "2026-11-16"}


def test_timeline_marks_current_step_and_late():
    tl = B.timeline(_r(status="confirmed", confirmed=T - timedelta(days=1)), T)
    assert [s for _n, _s, s in tl] == ["done", "done", "done", "now", "todo"]
    tl = B.timeline(_r(status="ordered", overdue=True, late_by=4), T)
    assert [s for _n, _s, s in tl] == ["done", "done", "late", "todo", "todo"]
    assert "4" in tl[2][1]
    tl = B.timeline(_r(status="draft"), T)
    assert [s for _n, _s, s in tl] == ["done", "now", "todo", "todo", "todo"]
    tl = B.timeline(_r(status="arrived", arrived=T), T)
    assert all(s == "done" for _n, _s, s in tl)


def test_lane_sort_late_first_stuck_then_longest():
    rs = [_r(id="a", status="ordered", overdue=True, late_by=2), _r(id="b", status="stuck"),
          _r(id="c", status="ordered", overdue=True, late_by=9)]
    assert [r["id"] for r in B.lane_sort("late", rs)] == ["b", "c", "a"]
