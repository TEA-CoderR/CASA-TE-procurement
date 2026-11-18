"""
缺失查找页（2026-11-16 照设计稿「⑫ 缺失查找」重做）：
  页头：大标题 + 结果一句话 + 右边 开始查找 / ⋯（导出 Excel）
  左：没有报价的商品列表（筛选胶囊 全部 / 有相似商品 / 没找到 + 搜索），每行 小图 · 名称 · 条码 · 箱数 · 相似度标签
  右：选中的商品（大图 · 名称 · 条码 · 门店要几箱）+「这周报价里相似的商品」卡片（名称里不一样的地方上色、
      相似度、批发商、品名相似度），网络搜索结果；底部 相似度至少 n% · 网络搜索开关 · 网络搜索这个商品
  原来的表格（miss_table）还在、藏着：导出 Excel 照旧从它读
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QWidget, QLineEdit,
                               QScrollArea, QButtonGroup)

from theme import T
from i18n import tr
import result_page as RP

FILTERS = [("all", "全部", "head"), ("found", "有相似商品", "accent"), ("none", "没找到", "muted")]


def _tone(score):
    return "ok" if score >= 0.85 else "accent" if score >= 0.7 else "warn"


def _letters(name, tone, size):
    lb = QLabel("".join(w[:1] for w in (name or "?").split()[:2]).upper() or "?")
    lb.setFixedSize(size, size)
    lb.setAlignment(Qt.AlignCenter)
    lb.setStyleSheet(f"background:{T(tone + '_soft') if tone != 'muted' else T('track')}; color:{T(tone)};"
                     f"border-radius:{14 if size > 40 else 9}px; font-weight:700; font-size:{18 if size > 40 else 12}px;")
    return lb


def _pic(name, ean, tone, size):
    """商品小图（用户 11-18：列表都放缩略图）：有图显示图，没图显示名称头两个字母"""
    box = QWidget()
    box.setFixedSize(size, size)
    lt = _letters(name, tone, size)
    lt.setParent(box)
    img = QLabel(box)
    img.setFixedSize(size, size)
    img.setAlignment(Qt.AlignCenter)
    img.setStyleSheet(f"background:{T('canvas')}; border-radius:{14 if size > 40 else 9}px;")
    try:
        import product_images as PI
        PI.set_label_image(img, ean, size)
    except Exception:
        img.hide()
    return box


class ItemRow(QPushButton):
    def __init__(self, it, win):
        super().__init__()
        self.setCheckable(True)
        self.setObjectName("MissRow")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(58)
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(12)
        best = it["best"]
        tone = _tone(best) if best else "muted"
        h.addWidget(_pic(it["art"], it["ean"], tone, 36))
        v = QVBoxLayout()
        v.setSpacing(2)
        n = QLabel(it["art"] or "—")
        n.setStyleSheet("font-weight:500;")
        n.setToolTip(it["art"] or "")
        v.addWidget(n)
        sub = it["ean"] or ""
        if it["count"]:
            sub += " · " + tr("{0} 箱", it["count"])
        v.addWidget(RP.mono(sub, 11, 500, "muted"))
        h.addLayout(v, 1)
        h.addWidget(RP.chip(f"{best:.0%}" if best else tr("没找到"), tone), 0, Qt.AlignVCenter)
        for w in self.findChildren(QWidget):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)


class CandCard(QFrame):
    def __init__(self, m, base, first):
        super().__init__()
        self.setObjectName("MissCand")
        self.setProperty("best", first)
        tot, ean, art, sup, art_s, web_art = (list(m) + [None] * 6)[:6]
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 12, 14, 12)
        h.setSpacing(14)
        h.addWidget(_pic(art, ean, "accent", 40), 0, Qt.AlignTop)
        v = QVBoxLayout()
        v.setSpacing(4)
        name = QLabel()
        name.setTextFormat(Qt.RichText)
        name.setWordWrap(True)
        try:
            from griglia_replace import diff_tokens
            from griglia_replace_dialog import segs_html
            name.setText(segs_html(diff_tokens(art or "", base or ""), "new"))
        except Exception:
            name.setText(art or "")
        name.setStyleSheet("font-weight:500; font-size:13px;")
        v.addWidget(name)
        v.addWidget(RP.mono(f"EAN {ean or '—'}", 11, 500, "muted"))
        tags = QHBoxLayout()
        tags.setSpacing(6)
        if sup:
            tags.addWidget(RP.chip(str(sup), "violet"))
        if art_s is not None:
            c = RP.chip(tr("品名 {0:.0%}", float(art_s)), "muted")
            c.setToolTip(tr("只看品名的相似度"))
            tags.addWidget(c)
        if first:
            tags.addWidget(RP.chip(tr("最像"), "ok"))
        tags.addStretch()
        v.addLayout(tags)
        if web_art:
            v.addWidget(RP.muted(tr("网络：{0}", web_art), 11))
        h.addLayout(v, 1)
        sc = RP.chip(f"{float(tot or 0):.0%}", _tone(float(tot or 0)))
        sc.setToolTip(tr("综合相似度（条码前缀 + 品名）"))
        h.addWidget(sc, 0, Qt.AlignTop)


QSS = """
QPushButton#MissRow {{ background:transparent; border:1px solid transparent; border-radius:12px; text-align:left; }}
QPushButton#MissRow:hover {{ background:{hover}; }}
QPushButton#MissRow:checked {{ background:{sel}; border-color:{ring}; }}
QPushButton#MissChip {{ height:30px; padding:0 12px; border-radius:15px; border:1px solid {border}; background:transparent; color:{muted}; font-size:12px; }}
QPushButton#MissChip:checked {{ border-color:{accent}; background:{sel}; color:{text}; }}
QWidget#MissInner {{ background:transparent; }}
QScrollArea#MissScroll {{ background:transparent; border:none; }}
QFrame#MissCand {{ background:{canvas}; border:1px solid {border}; border-radius:12px; }}
QFrame#MissCand[best="true"] {{ border-color:{accent}; }}
"""


def qss():
    return RP.qss() + QSS.format(hover=T("hover"), sel=T("accent_soft"), ring=T("accent_ring"), border=T("line"),
                                 muted=T("muted"), accent=T("accent"), text=T("text"), canvas=T("canvas"))


def build(win, lay, hold):
    page = lay.parentWidget()
    win._miss_page = page
    win._miss_items = []
    win._miss_sel = 0
    win._miss_f = "all"

    head = QHBoxLayout()
    hv = QVBoxLayout()
    hv.setSpacing(6)
    t = QLabel(tr("缺失查找"))
    t.setObjectName("HeroTitle")
    hv.addWidget(t)
    win.miss_status.setParent(None)
    win.miss_status.setObjectName("HeroSub")
    hv.addWidget(win.miss_status)
    head.addLayout(hv, 1)
    from page_kit import more_menu
    more, _a = more_menu([(tr("导出 Excel"), win.export_missing, None)])
    win.btn_find.setParent(None)
    head.addWidget(more, 0, Qt.AlignBottom)
    head.addWidget(win.btn_find, 0, Qt.AlignBottom)
    lay.addLayout(head)

    win.miss_empty.setParent(None)
    lay.addWidget(win.miss_empty, 1)
    body = QWidget()
    win._miss_body = body
    row = QHBoxLayout(body)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(16)
    lay.addWidget(body, 1)
    body.hide()

    # 左：商品列表
    left, lv = RP.card(None, margins=(12, 14, 12, 12))
    left.setFixedWidth(400)
    chips = QHBoxLayout()
    chips.setSpacing(6)
    win._miss_chips = {}
    grp = QButtonGroup(left)
    for key, text, _tone_ in FILTERS:
        b = QPushButton(tr(text))
        b.setObjectName("MissChip")
        b.setCheckable(True)
        b.setChecked(key == "all")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda _=False, k=key: _set_filter(win, k))
        grp.addButton(b)
        chips.addWidget(b)
        win._miss_chips[key] = (b, text)
    chips.addStretch()
    lv.addLayout(chips)
    win.miss_filter.setParent(None)
    win.miss_filter.setFixedWidth(16777215)
    win.miss_filter.setPlaceholderText(tr("搜索名称 / 条码"))
    win.miss_filter.textChanged.connect(lambda _t: _render_list(win))
    lv.addWidget(win.miss_filter)
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setObjectName("MissScroll")
    sc.viewport().setObjectName("MissInner")
    sc.setFrameShape(QFrame.NoFrame)
    inner = QWidget()
    inner.setObjectName("MissInner")
    win._miss_list = QVBoxLayout(inner)
    win._miss_list.setContentsMargins(0, 0, 0, 0)
    win._miss_list.setSpacing(2)
    win._miss_list.addStretch()
    sc.setWidget(inner)
    lv.addWidget(sc, 1)
    row.addWidget(left)

    # 右：选中的商品 + 相似商品
    right, rv = RP.card(None, margins=(20, 18, 20, 16))
    win._miss_head = QHBoxLayout()
    win._miss_head.setSpacing(16)
    rv.addLayout(win._miss_head)
    sec = QHBoxLayout()
    st = QLabel(tr("这周报价里相似的商品"))
    st.setObjectName("Section")
    sec.addWidget(st)
    sec.addStretch()
    win._miss_cnt = RP.muted("", 12)
    sec.addWidget(win._miss_cnt)
    rv.addLayout(sec)
    sc2 = QScrollArea()
    sc2.setWidgetResizable(True)
    sc2.setObjectName("MissScroll")
    sc2.viewport().setObjectName("MissInner")
    sc2.setFrameShape(QFrame.NoFrame)
    inner2 = QWidget()
    inner2.setObjectName("MissInner")
    win._miss_cands = QVBoxLayout(inner2)
    win._miss_cands.setContentsMargins(0, 0, 0, 0)
    win._miss_cands.setSpacing(10)
    sc2.setWidget(inner2)
    rv.addWidget(sc2, 1)
    foot = QHBoxLayout()
    foot.setSpacing(12)
    lt = RP.muted(tr("相似度至少") + " ⓘ", 12)
    lt.setToolTip(tr("0 到 1，越大越严格"))
    foot.addWidget(lt)
    win.thr.setParent(None)
    foot.addWidget(win.thr)
    win.t_web.setParent(None)
    foot.addWidget(win.t_web)
    foot.addStretch()
    win.btn_web.setParent(None)
    win.btn_web.setText(tr("网络搜索这个商品"))
    foot.addWidget(win.btn_web)
    rv.addLayout(foot)
    row.addWidget(right, 1)
    page.setStyleSheet(qss())


def _set_filter(win, k):
    win._miss_f = k
    _render_list(win)


def render(win, results):
    """results = find_missing_items 的结果"""
    items = []
    for ean, art, count, matches, web_source, web_art, st in results or []:
        ms = list(matches or [])
        items.append(dict(ean=str(ean or ""), art=str(art or ""), count=count, matches=ms,
                          best=float(ms[0][0]) if ms else 0.0, web=web_art, web_src=web_source))
    items.sort(key=lambda x: -x["best"])
    win._miss_items = items
    win._miss_sel = 0
    win._miss_page.setStyleSheet(qss())
    win._miss_body.setVisible(bool(items))
    win.miss_empty.setVisible(not items)
    _render_list(win)


def _visible(win):
    q = win.miss_filter.text().strip().lower()
    f = win._miss_f
    out = []
    for i, it in enumerate(win._miss_items):
        if f == "found" and not it["matches"] or f == "none" and it["matches"]:
            continue
        if q and q not in (it["art"] + " " + it["ean"]).lower():
            continue
        out.append(i)
    return out


def _render_list(win):
    if not hasattr(win, "_miss_list"):
        return
    items = win._miss_items
    n = {"all": len(items), "found": sum(1 for x in items if x["matches"]),
         "none": sum(1 for x in items if not x["matches"])}
    for k, (b, text) in win._miss_chips.items():
        b.setText(f"{tr(text)}  {n[k]}")
    lst = win._miss_list
    while lst.count() > 1:
        w = lst.takeAt(0).widget()
        if w:
            w.hide()
            w.setParent(None)
            w.deleteLater()
    vis = _visible(win)
    if vis and win._miss_sel not in vis:
        win._miss_sel = vis[0]
    for i in vis[:400]:
        r = ItemRow(items[i], win)
        r.setChecked(i == win._miss_sel)
        r.clicked.connect(lambda _=False, k=i: _select(win, k))
        lst.insertWidget(lst.count() - 1, r)
    _detail(win)


def _select(win, k):
    win._miss_sel = k
    for i in range(win._miss_list.count() - 1):
        w = win._miss_list.itemAt(i).widget()
        if w is not None:
            w.setChecked(False)
    _render_list(win)


def selected(win):
    """右边正在看的那个商品 (ean, 名称)，没有就 None"""
    items = getattr(win, "_miss_items", None)
    if not items or not (0 <= win._miss_sel < len(items)):
        return None
    it = items[win._miss_sel]
    return it["ean"], it["art"]


def _detail(win):
    RP._clear(win._miss_head)
    RP._clear(win._miss_cands)
    items = win._miss_items
    if not items or not (0 <= win._miss_sel < len(items)) or win._miss_sel not in _visible(win):
        win._miss_cnt.setText("")
        win._miss_cands.addWidget(RP.muted(tr("没有符合条件的商品"), 13))
        return
    it = items[win._miss_sel]
    win._miss_head.addWidget(_letters(it["art"], "warn", 64))
    v = QVBoxLayout()
    v.setSpacing(4)
    v.addWidget(RP.muted(tr("门店要的"), 11))
    n = QLabel(it["art"] or "—")
    n.setStyleSheet("font-size:17px; font-weight:600;")
    n.setWordWrap(True)
    v.addWidget(n)
    sub = it["ean"]
    if it["count"]:
        sub += " · " + tr("清单 {0} 箱", it["count"])
    v.addWidget(RP.mono(sub, 12, 500, "muted"))
    if it["web"]:
        w = RP.muted(tr("网络搜索：{0}", it["web"]), 12)
        w.setToolTip(str(it["web_src"] or ""))
        v.addWidget(w)
    win._miss_head.addLayout(v, 1)
    ms = it["matches"]
    win._miss_cnt.setText(tr("{0} 个", len(ms)) if ms else "")
    if not ms:
        e = RP.muted(tr("这周的报价里没有相似的商品。可以降低相似度再查一次，或网络搜索这个条码。"), 13)
        e.setWordWrap(True)
        win._miss_cands.addWidget(e)
    for k, m in enumerate(ms[:12]):
        win._miss_cands.addWidget(CandCard(m, it["art"], k == 0))
    win._miss_cands.addStretch()
