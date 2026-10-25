"""右键表头按列筛选（HeaderFilter）：和页面自己的筛选一起用"""
import erp_widgets as W


class _Item:
    def __init__(self, v):
        self.v = v

    def text(self):
        return self.v


class _Table:
    """只做 HeaderFilter 用到的那几样（不用开界面）"""
    def __init__(self, rows):
        self.rows, self.hidden = rows, set()

    def rowCount(self):
        return len(self.rows)

    def columnCount(self):
        return len(self.rows[0])

    def item(self, r, c):
        return _Item(self.rows[r][c])

    def cellWidget(self, r, c):
        return None

    def horizontalHeaderItem(self, c):
        return None

    def setRowHidden(self, r, h):
        (self.hidden.add if h else self.hidden.discard)(r)

    def isRowHidden(self, r):
        return r in self.hidden


def _table(rows):
    return _Table(rows)


class _HF(W.HeaderFilter):
    def __init__(self, t, **k):           # 不建 Qt 对象，只测筛选逻辑
        self.t, self.apply_cb, self.base_hidden = t, k.get("apply"), k.get("base_hidden")
        self.pin_last, self.skip, self.sorter, self.extra, self.sort_cb = k.get("pin_last", False), set(), None, [], None
        self.filters = {}

    def _changed(self):
        if self.apply_cb:
            self.apply_cb()
        else:
            self.reapply()


def test_self_hiding_and_values():
    t = _table([["MEGA", "A"], ["GRIECO", "B"], ["MEGA", "B"], ["合计", ""]])
    hf = _HF(t, pin_last=True)
    assert hf.values_of(0) == {"MEGA": 2, "GRIECO": 1}
    hf.set_filter(0, {"MEGA"})
    assert [t.isRowHidden(r) for r in range(4)] == [False, True, False, False]   # 合计行一直在
    assert hf.values_of(1) == {"A": 1, "B": 1}              # 只列其他筛选之后还看得到的
    hf.set_filter(1, {"B"})
    assert [t.isRowHidden(r) for r in range(4)] == [True, True, False, False]
    hf.clear()
    assert not any(t.isRowHidden(r) for r in range(4))


def test_with_page_filter():
    t = _table([["MEGA", "A"], ["GRIECO", "B"], ["MEGA", "B"]])
    page_hide = {1}
    calls = []

    def apply():
        calls.append(1)
        for r in range(t.rowCount()):
            t.setRowHidden(r, r in page_hide or not hf.accepts(r))
    hf = _HF(t, apply=apply, base_hidden=lambda r: r in page_hide)
    assert hf.values_of(0) == {"MEGA": 2}                   # 页面筛选藏掉的 GRIECO 不列出来
    hf.set_filter(1, {"A"})
    assert calls and [t.isRowHidden(r) for r in range(3)] == [False, True, True]
