"""页面零件（page_kit）：指标卡 / 页头 / 流程条的接口（2026-11-16 设计稿外的页面重排）"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_stat_hero_flowcard_seg():
    _app()
    from page_kit import Stat, Hero, FlowCard, Seg, more_menu
    s = Stat("商品行", "table", "accent", "说明", clickable=True)
    hit = []
    s.clicked.connect(lambda: hit.append(1))
    s.set("624", "#ff0000", sub="占 96%")
    assert s.value.text() == "624" and "96" in s.subl.text()
    s.set("1")                                    # 和旧 Kpi 一样：只给值，小字不变
    assert "96" in s.subl.text()
    s.set_tone("warn")
    h = Hero("FOIANO", "sub", tip="t")
    h.set_title("MARLIA · 16/11")
    assert "MARLIA" in h.title.text() and "ⓘ" in h.title.text()
    f = FlowCard([("a", ""), ("b", ""), ("c", "")])
    f.set_state(1, "now", "x")
    assert f.bar.shown == 1 and f.bar.subs[1] == "x"
    g = Seg([("all", "全部"), ("err", "错误")], "all")
    got = []
    g.changed.connect(got.append)
    g.btns["err"].click()
    assert got == ["err"]
    b, acts = more_menu([("打开", lambda: None, "tip"), None, ("删除", lambda: None)])
    assert set(acts) == {"打开", "删除"}
