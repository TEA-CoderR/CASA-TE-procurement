"""
订单跟踪「看板」（2026-11-16 照设计稿 Orders 画板做）：
  · 五列：待登记 / 已下单 / 已确认 / 已发货 / 已超期，每张单一张卡片（供应商 · 金额 / 门店 · 下单日期 / 一行提示）
  · 右边详情：状态胶囊、供应商、门店 · 下单日期、金额 / 箱数 / 预计到货、按状态的主按钮、进展时间线、订单明细 / 邮件
  · 顶上的本月小卡：已下单 / 预算、每周一根柱子、用了预算的百分比
业务判断（哪张单放哪一列、提示写什么、时间线走到哪）写成不依赖界面的函数（lane_of / card_hint / timeline / action_of），有测试。
"""
from datetime import date

from PySide6.QtCore import Qt, Signal, QRectF, QSize, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QFontMetrics
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
                               QListWidgetItem, QStyledItemDelegate, QStyle, QAbstractItemView, QSizePolicy,
                               QGridLayout, QToolButton, QToolTip, QScrollArea)

from i18n import tr

# 列：键、名字、颜色 token
LANES = (("draft", "待登记", "accent"), ("ordered", "已下单", "violet"), ("confirmed", "已确认", "cyan"),
         ("shipped", "已发货", "ok"), ("late", "已超期", "err"))
LANE_TIPS = {
    "draft": "总仓采购 / 门店采购 / DETERSIVO采购生成的订单，还没发给供应商。点一张 →「发送订单邮件」",
    "ordered": "订单发出去了，还没收到供应商确认",
    "confirmed": "供应商确认了，还没发货",
    "shipped": "供应商发货了（收到 DDT）或部分到货，还没全部到",
    "late": "过了预计到货日还没到，或者卡住了",
}


def lane_of(r):
    """这张单放哪一列；到货了 / 取消了 / DETERSIVO 手填的不放"""
    s = r.get("status")
    if s in ("arrived", "cancel", "deter") or not s:
        return None
    if s == "draft":
        return "draft"
    if r.get("overdue") or s == "stuck":
        return "late"
    if s in ("shipped", "partial"):
        return "shipped"
    if s in ("confirmed", "ordered"):
        return s
    return None


def _dm(d):
    return f"{d:%d/%m}" if d else ""


def card_hint(r, today=None):
    """卡片最下面一行：(文字, 颜色 token)"""
    today = today or date.today()
    lane, s = lane_of(r), r.get("status")
    if lane == "draft":
        src = {"store_orders": tr("总仓采购"), "store_pdv": tr("门店采购"), "wholesale": tr("DETERSIVO采购")}.get(r.get("origin"))
        return (tr("{0}生成", src) if src else tr("还没发给供应商")), "muted"
    if lane == "late":
        if s == "stuck":
            return tr("卡住了") + (f" · {r['note']}" if r.get("note") else ""), "err"
        return tr("超期 {0} 天", r.get("late_by") or 0), "err"
    if lane == "shipped":
        if s == "partial":
            return tr("部分到货") + (f" · {_dm(r.get('arrived'))}" if r.get("arrived") else ""), "warn"
        return (tr("发货 {0}", _dm(r["shipped"])) if r.get("shipped") else tr("已发货")) + \
            (tr(" · 预计 {0}", _dm(r["expected"])) if r.get("expected") else ""), "muted"
    if lane == "confirmed":
        return (tr("确认信 {0}", _dm(r["confirmed"])) if r.get("confirmed") else tr("已确认")) + \
            (tr(" · 预计 {0}", _dm(r["expected"])) if r.get("expected") else ""), "muted"
    if lane == "ordered":
        d0 = r.get("date")
        n = (today - d0).days if d0 else 0
        text = tr("邮件已发 · 等确认") if r.get("mails") or r.get("origin") == "mail" else tr("等确认")
        if n >= 1:
            text += tr(" {0} 天", n)
        return text, ("warn" if r.get("pending") else "muted")
    return "", "muted"


def lane_sort(lane, rs):
    """每一列里的顺序：要先处理的在上面"""
    far = date(9999, 1, 1)
    if lane == "late":
        return sorted(rs, key=lambda r: (r.get("status") != "stuck", -(r.get("late_by") or 0)))
    if lane == "draft":
        return sorted(rs, key=lambda r: (r.get("date") or far, r.get("supplier") or ""), reverse=True)
    if lane == "ordered":
        return sorted(rs, key=lambda r: (r.get("date") or far, r.get("supplier") or ""))
    return sorted(rs, key=lambda r: (r.get("expected") or far, r.get("supplier") or ""))


def action_of(r):
    """详情里的主按钮：(动作键, 按钮文字)"""
    lane = lane_of(r)
    if lane == "draft":
        return "send", tr("发送订单邮件")
    if lane == "ordered":
        return "confirm", tr("标记供应商已确认")
    if lane == "confirmed":
        return "ship", tr("登记发货")
    if lane in ("shipped", "late"):
        return "arrive", tr("确认收货")
    return None, ""


def changes_for(action, r, today=None):
    """主按钮要改的字段"""
    today = (today or date.today()).isoformat()
    if action == "confirm":
        return {"confirmed": today}
    if action == "ship":
        return {"shipped": today}
    if action == "arrive":
        f = {"arrived": today}
        if r.get("status") in ("partial", "stuck"):
            f["state"] = None
        return f
    return {}


def timeline(r, today=None):
    """进展：[(名字, 小字, 状态 done / now / late / todo)]，五步：生成 → 发出 → 确认 → 发货 → 到货"""
    today = today or date.today()
    s = r.get("status")
    origin = r.get("origin") or ""
    gen = {"store_orders": tr("总仓采购生成"), "store_pdv": tr("门店采购生成"), "wholesale": tr("DETERSIVO采购生成"),
           "excel": tr("Excel 手填"), "mail": tr("按邮件登记"), "app": tr("程序里登记")}.get(origin, tr("已登记"))
    sent = s != "draft"
    conf = bool(r.get("confirmed")) or s in ("shipped", "partial", "arrived")
    ship = bool(r.get("shipped")) or s in ("partial", "arrived")
    arr = s == "arrived"
    done = [True, sent, conf, ship, arr]
    subs = [gen, tr("下单 {0}", _dm(r.get("date"))) if sent else "",
            _dm(r.get("confirmed")) if r.get("confirmed") else (tr("已确认") if conf else ""),
            _dm(r.get("shipped")) if r.get("shipped") else (tr("已发货") if ship else ""),
            _dm(r.get("arrived")) if arr else ""]
    names = [tr("程序生成订单") if origin in ("store_orders", "store_pdv", "wholesale") else tr("登记订单"),
             tr("发给供应商"), tr("供应商确认"), tr("发货"), tr("到货")]
    out = []
    cur = next((i for i, d in enumerate(done) if not d), None)
    for i, n in enumerate(names):
        if done[i]:
            out.append((n, subs[i], "done"))
        elif i == cur:
            if s == "stuck":
                out.append((n, tr("卡住了") + (f" · {r['note']}" if r.get("note") else ""), "late"))
            elif r.get("overdue"):
                out.append((n, tr("已超期 {0} 天 · 还没收到", r.get("late_by") or 0), "late"))
            elif s == "partial" and i == 4:
                out.append((n, tr("部分到货"), "now"))
            elif i == 1:
                out.append((n, tr("等你发送"), "now"))
            else:
                out.append((n, tr("等待中") + (tr(" · 预计 {0}", _dm(r["expected"])) if i == 4 and r.get("expected") else ""),
                            "now"))
        else:
            out.append((n, "—", "todo"))
    return out


# =====================================================================
def _T(key):
    from theme import T
    return T(key)


def _dark():
    from theme import current
    return current() == "dark"


def _lane_bg():
    return _T("side") if _dark() else _T("track")


def _drawer_bg():
    return _T("side") if _dark() else _T("surface")


def _num_font(base, size=None, weight=QFont.DemiBold):
    from theme import NUM_FONT
    f = QFont(base)
    f.setFamilies([NUM_FONT] + list(base.families()))
    if size:
        f.setPixelSize(size)
    f.setWeight(weight)
    return f


def _money0(v):
    return f"€{float(v or 0):,.0f}"


class _CardDelegate(QStyledItemDelegate):
    """一张订单卡片：供应商 · 金额 / 门店 · 下单日期 / 提示"""
    H = 92

    def __init__(self, board, tok):
        super().__init__(board)
        self.board, self.tok = board, tok

    def sizeHint(self, opt, ix):
        return QSize(opt.rect.width(), self.H)

    def paint(self, p, opt, ix):
        r = self.board.row(ix.data(Qt.UserRole))
        if r is None:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(opt.rect).adjusted(1.5, 1.5, -1.5, -9.5)
        sel = bool(opt.state & QStyle.State_Selected)
        hov = bool(opt.state & QStyle.State_MouseOver)
        lane = QColor(_T(self.tok))
        if sel:
            glow = QColor(lane)
            glow.setAlpha(40)
            p.setPen(QPen(glow, 4))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 12, 12)
        p.setPen(QPen(lane if sel else QColor(_T("accent_ring") if hov else _T("border")), 1))
        p.setBrush(QColor(_T("hover") if (sel or hov) else (_T("surface2") if _dark() else _T("surface"))))
        p.drawRoundedRect(rect, 11, 11)
        inner = rect.adjusted(12, 10, -12, -10)
        base = QFont(opt.font)
        f1 = QFont(base)
        f1.setPixelSize(13)
        f1.setWeight(QFont.DemiBold)
        fn = _num_font(base, 12, QFont.Medium)
        f2 = QFont(base)
        f2.setPixelSize(12)
        f3 = QFont(base)
        f3.setPixelSize(11)
        amt = _money0(r.get("amount")) if r.get("amount") is not None else "—"
        p.setFont(fn)
        aw = QFontMetrics(fn).horizontalAdvance(amt)
        p.setPen(QColor(_T("head")))
        p.drawText(QRectF(inner.right() - aw, inner.top(), aw, 18), Qt.AlignRight | Qt.AlignVCenter, amt)
        p.setFont(f1)
        p.setPen(QColor(_T("text")))
        sup = QFontMetrics(f1).elidedText(r.get("supplier") or "", Qt.ElideRight, int(inner.width() - aw - 8))
        p.drawText(QRectF(inner.left(), inner.top(), inner.width() - aw - 8, 18), Qt.AlignLeft | Qt.AlignVCenter, sup)
        y2 = inner.top() + 22
        p.setPen(QColor(_T("muted")))
        p.setFont(_num_font(base, 11, QFont.Medium))
        dt = _dm(r.get("date"))
        dw = QFontMetrics(p.font()).horizontalAdvance(dt)
        p.drawText(QRectF(inner.right() - dw, y2, dw, 16), Qt.AlignRight | Qt.AlignVCenter, dt)
        p.setFont(f2)
        p.drawText(QRectF(inner.left(), y2, inner.width() - dw - 8, 16), Qt.AlignLeft | Qt.AlignVCenter, r.get("store_name") or "")
        text, tone = card_hint(r)
        p.setFont(f3)
        p.setPen(QColor(_T(tone) if tone != "muted" else _T("muted")))
        p.drawText(QRectF(inner.left(), y2 + 20, inner.width(), 16), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(f3).elidedText(text, Qt.ElideRight, int(inner.width())))
        p.restore()


class _Lane(QFrame):
    def __init__(self, board, key, name, tok):
        super().__init__()
        self.key, self.tok = key, tok
        self.setObjectName("Lane")
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 12, 6, 6)
        v.setSpacing(8)
        h = QHBoxLayout()
        h.setContentsMargins(4, 0, 6, 0)
        h.setSpacing(8)
        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        self.title = QLabel(tr(name))
        self.title.setObjectName("LaneTitle")
        self.title.setToolTip(tr(LANE_TIPS[key]))
        self.count = QLabel("0")
        self.count.setObjectName("LaneCount")
        h.addWidget(self.dot)
        h.addWidget(self.title)
        h.addStretch()
        h.addWidget(self.count)
        v.addLayout(h)
        self.list = QListWidget()
        self.list.setObjectName("LaneList")
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setMouseTracking(True)
        self.list.setUniformItemSizes(True)
        self.list.setItemDelegate(_CardDelegate(board, tok))
        self.list.viewport().setAttribute(Qt.WA_Hover, True)
        v.addWidget(self.list, 1)
        self.empty = QLabel(tr("没有"))
        self.empty.setObjectName("Faint")
        self.empty.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        v.addWidget(self.empty)
        self.empty.hide()

    def restyle(self):
        c = _T(self.tok)
        self.dot.setStyleSheet(f"background:{c}; border-radius:4px;")


class OrderBoard(QWidget):
    """五列看板。selected(row) 选了一张单；opened(row) 双击一张单"""
    selected = Signal(object)
    opened = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows, self.by_id, self.sel_id = [], {}, None
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)
        self.lanes = {}
        for key, name, tok in LANES:
            ln = _Lane(self, key, name, tok)
            ln.setMinimumWidth(160)
            ln.list.itemSelectionChanged.connect(lambda ln=ln: self._picked(ln))
            ln.list.itemDoubleClicked.connect(lambda it: self.opened.emit(self.row(it.data(Qt.UserRole))))
            self.lanes[key] = ln
            h.addWidget(ln, 1)
        self.restyle()

    def row(self, oid):
        return self.by_id.get(oid)

    def set_rows(self, rows):
        self.rows = list(rows)
        self.by_id = {r["id"]: r for r in self.rows}
        groups = {k: [] for k, _n, _t in LANES}
        for r in self.rows:
            k = lane_of(r)
            if k:
                groups[k].append(r)
        keep = self.sel_id
        for k, ln in self.lanes.items():
            rs = lane_sort(k, groups[k])
            ln.list.blockSignals(True)
            ln.list.clear()
            for r in rs:
                it = QListWidgetItem()
                it.setData(Qt.UserRole, r["id"])
                it.setSizeHint(QSize(10, _CardDelegate.H))
                ln.list.addItem(it)
                if r["id"] == keep:
                    it.setSelected(True)
                    ln.list.setCurrentItem(it)
            ln.list.blockSignals(False)
            ln.count.setText(str(len(rs)))
            amt = sum(float(r.get("amount") or 0) for r in rs)
            ln.count.setToolTip(tr("{0} 张单 · {1}", len(rs), _money0(amt)))
            ln.empty.setVisible(not rs)
        if keep not in self.by_id or not lane_of(self.by_id[keep]):
            self.sel_id = None
            first = next((ln.list.item(0) for k, ln in self.lanes.items() if ln.list.count()), None)
            if first is not None:
                first.listWidget().setCurrentItem(first)     # 默认选最左边一列第一张
                return
            self.selected.emit(None)
        else:
            self.selected.emit(self.by_id[keep])

    def counts(self):
        return {k: ln.list.count() for k, ln in self.lanes.items()}

    def _picked(self, lane):
        its = lane.list.selectedItems()
        if not its:
            return
        oid = its[0].data(Qt.UserRole)
        for ln in self.lanes.values():
            if ln is not lane and ln.list.selectedItems():
                ln.list.blockSignals(True)
                ln.list.clearSelection()
                ln.list.blockSignals(False)
        self.sel_id = oid
        self.selected.emit(self.by_id.get(oid))

    def restyle(self):
        self.setStyleSheet(
            f"#Lane {{ background:{_lane_bg()}; border:1px solid {_T('line')}; border-radius:14px; }}"
            f"#LaneTitle {{ font-weight:600; color:{_T('text')}; }}"
            f"#LaneCount {{ color:{_T('muted')}; }}"
            f"#LaneList {{ background:transparent; outline:0; }}"
            f"#LaneList::item {{ background:transparent; border:0; }}")
        from theme import NUM_FONT
        for ln in self.lanes.values():
            ln.restyle()
            ln.count.setStyleSheet(f"font-family:\"{NUM_FONT}\"; color:{_T('muted')};")
            ln.list.viewport().update()


# =====================================================================
class _Timeline(QWidget):
    ROW = 50

    def __init__(self):
        super().__init__()
        self.steps = []
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_steps(self, steps):
        self.steps = steps
        self.setFixedHeight(self.ROW * len(steps))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        base = QFont(self.font())
        f1 = QFont(base)
        f1.setPixelSize(13)
        f1.setWeight(QFont.Medium)
        f2 = QFont(base)
        f2.setPixelSize(12)
        n = len(self.steps)
        for i, (name, sub, state) in enumerate(self.steps):
            y = i * self.ROW
            cx, cy = 7.0, y + 9.0
            col = QColor(_T("ok") if state == "done" else _T("err") if state == "late" else
                         _T("accent") if state == "now" else _T("border"))
            if i < n - 1:
                nxt_done = self.steps[i + 1][2] == "done"
                p.setPen(QPen(QColor(_T("ok") if (state == "done" and nxt_done) else _T("border")), 2))
                p.drawLine(QPointF(cx, cy + 10), QPointF(cx, y + self.ROW + 1))
            if state in ("now", "late"):
                halo = QColor(col)
                halo.setAlpha(55)
                p.setPen(Qt.NoPen)
                p.setBrush(halo)
                p.drawEllipse(QPointF(cx, cy), 10, 10)
            if state == "done":
                p.setPen(Qt.NoPen)
                p.setBrush(col)
                p.drawEllipse(QPointF(cx, cy), 6, 6)
            else:
                p.setPen(QPen(col, 2))
                p.setBrush(QColor(_drawer_bg()))
                p.drawEllipse(QPointF(cx, cy), 5, 5)
            p.setFont(f1)
            p.setPen(QColor(_T("text") if state != "todo" else _T("faint")))
            p.drawText(QRectF(26, y, self.width() - 26, 18), Qt.AlignLeft | Qt.AlignVCenter, name)
            p.setFont(f2)
            p.setPen(QColor(_T("err") if state == "late" else _T("muted")))
            p.drawText(QRectF(26, y + 19, self.width() - 26, 16), Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetrics(f2).elidedText(sub, Qt.ElideRight, self.width() - 28))


class OrderDrawer(QFrame):
    """右边订单详情。act(行, 动作键) 点了主按钮；lines(行) 订单明细；mails(行) 邮件；edit(行) 编辑"""
    act = Signal(object, str)
    lines = Signal(object)
    mails = Signal(object)
    edit = Signal(object)

    def __init__(self):
        super().__init__()
        self.setObjectName("OrderDrawer")
        self.setFixedWidth(340)
        self.r = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(sa)
        body = QWidget()
        body.setObjectName("OrderDrawerBody")
        sa.setWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(22, 22, 22, 22)
        v.setSpacing(18)
        self.none = QLabel(tr("点左边一张单看详情"))
        self.none.setObjectName("Faint")
        self.none.setAlignment(Qt.AlignCenter)
        v.addWidget(self.none)
        self.box = QWidget()
        bv = QVBoxLayout(self.box)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(18)
        head = QVBoxLayout()
        head.setSpacing(8)
        top = QHBoxLayout()
        self.chip = QLabel("")
        top.addWidget(self.chip)
        top.addStretch()
        self.b_edit = QPushButton(tr("编辑…"))
        self.b_edit.setObjectName("Link")
        self.b_edit.setCursor(Qt.PointingHandCursor)
        self.b_edit.setToolTip(tr("手动改日期、金额、状态、备注（特殊情况用；双击卡片也可以）"))
        self.b_edit.clicked.connect(lambda: self.r and self.edit.emit(self.r))
        top.addWidget(self.b_edit)
        head.addLayout(top)
        self.sup = QLabel("")
        self.sup.setObjectName("DrawerSup")
        self.sup.setWordWrap(True)
        head.addWidget(self.sup)
        self.sub = QLabel("")
        self.sub.setObjectName("DrawerSub")
        head.addWidget(self.sub)
        bv.addLayout(head)
        g = QGridLayout()
        g.setSpacing(8)
        self.tiles = []
        for i, name in enumerate((tr("金额"), tr("箱数"), tr("预计到货"))):
            f = QFrame()
            f.setObjectName("DrawerTile")
            fv = QVBoxLayout(f)
            fv.setContentsMargins(10, 9, 10, 9)
            fv.setSpacing(3)
            a = QLabel(name)
            a.setObjectName("TileName")
            b = QLabel("")
            b.setObjectName("TileVal")
            fv.addWidget(a)
            fv.addWidget(b)
            g.addWidget(f, 0, i)
            self.tiles.append(b)
        bv.addLayout(g)
        self.btn = QPushButton("")
        self.btn.setObjectName("Primary")
        self.btn.setMinimumHeight(42)
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(self._act)
        bv.addWidget(self.btn)
        tl = QVBoxLayout()
        tl.setSpacing(10)
        self.tl_title = QLabel(tr("进展 · 邮件自动更新"))
        self.tl_title.setObjectName("TileName")
        self.tl_title.setToolTip(tr("「邮件核对」读到订单信、确认信、DDT 会自动往下走；也可以点上面的按钮手动登记"))
        tl.addWidget(self.tl_title)
        self.tl = _Timeline()
        tl.addWidget(self.tl)
        bv.addLayout(tl)
        self.note = QLabel("")
        self.note.setObjectName("Muted")
        self.note.setWordWrap(True)
        bv.addWidget(self.note)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.b_lines = QPushButton(tr("订单明细"))
        self.b_lines.clicked.connect(lambda: self.r and self.lines.emit(self.r))
        self.b_mail = QPushButton(tr("邮件"))
        self.b_mail.clicked.connect(lambda: self.r and self.mails.emit(self.r))
        for b in (self.b_lines, self.b_mail):
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b, 1)
        bv.addLayout(row)
        v.addWidget(self.box)
        v.addStretch()
        self.box.hide()
        self.action = None
        self.restyle()

    def _act(self):
        if self.r and self.action:
            self.act.emit(self.r, self.action)

    def show_row(self, r, lines_ok=None):
        self.r = r
        self.none.setVisible(r is None)
        self.box.setVisible(r is not None)
        if r is None:
            return
        lane = lane_of(r) or ("arrived" if r.get("status") == "arrived" else None)
        name, tok = next(((n, t) for k, n, t in LANES if k == lane), (tr("已到货"), "ok"))
        self._tok = tok
        self.chip.setText(tr(name) if lane in dict((k, 1) for k, _n, _t in LANES) else name)
        self.sup.setText(r.get("supplier") or "")
        self.sup.setToolTip(r.get("raw_supplier") or "")
        self.sub.setText(f"{r.get('store_name') or ''} · " + tr("下单") + f" {r['date']:%d/%m/%Y}" if r.get("date")
                         else (r.get("store_name") or ""))
        amt = r.get("amount")
        self.tiles[0].setText(f"€{float(amt):,.2f}" if amt is not None else "—")
        c = r.get("colli")
        self.tiles[1].setText(f"{float(c):g}" if c not in (None, "") else "—")
        exp = r.get("expected")
        late = bool(r.get("overdue"))
        self.tiles[2].setText((_dm(exp) + (" · " + tr("已过") if late else "")) if exp else "—")
        self.tiles[2].setProperty("late", late)
        self.tiles[2].setToolTip(tr("下单日期 + 这家供应商以往到货的天数（中位数）") if exp and not r.get("expected_manual")
                                 else tr("手动填的预计到货") if exp else "")
        self.action, label = action_of(r)
        self.btn.setText(label)
        self.btn.setVisible(bool(self.action))
        self.tl.set_steps(timeline(r))
        self.note.setText(r.get("note") or "")
        self.note.setVisible(bool(r.get("note")))
        n = len(r.get("mails") or [])
        self.b_mail.setText(tr("邮件（{0}）", n) if n else tr("邮件"))
        self.b_lines.setEnabled(lines_ok is not False)
        self.b_lines.setToolTip("" if lines_ok is not False else tr("没找到这张单的订单文件"))
        self.restyle()

    def restyle(self):
        from theme import NUM_FONT
        tok = getattr(self, "_tok", "accent")
        c = QColor(_T(tok))
        soft = QColor(c)
        soft.setAlpha(36)
        edge = QColor(c)
        edge.setAlpha(110)
        rgba = lambda q: f"rgba({q.red()},{q.green()},{q.blue()},{q.alpha()})"
        self.setStyleSheet(
            f"#OrderDrawer {{ background:{_drawer_bg()}; border:0; border-left:1px solid {_T('line')}; }}"
            f"#OrderDrawerBody {{ background:{_drawer_bg()}; }}"
            f"#DrawerSup {{ font-size:20px; font-weight:700; color:{_T('text')}; }}"
            f"#DrawerSub {{ color:{_T('head')}; }}"
            f"#DrawerTile {{ background:{_T('surface2')}; border:1px solid {_T('line')}; border-radius:10px; }}"
            f"#TileName {{ font-size:11px; color:{_T('muted')}; }}"
            f"#TileVal {{ font-family:\"{NUM_FONT}\"; font-weight:600; color:{_T('text')}; }}"
            f"#TileVal[late=\"true\"] {{ color:{_T('err')}; }}")
        self.chip.setStyleSheet(f"padding:3px 10px; border-radius:11px; font-size:12px; font-weight:600; color:{c.name()};"
                                f" background:{rgba(soft)}; border:1px solid {rgba(edge)};")
        self.tl.update()


# =====================================================================
class _WeekBars(QWidget):
    clicked = Signal(int)

    def __init__(self):
        super().__init__()
        self.weeks, self.today, self.sel, self.hover = [], date.today(), None, None
        self.setMinimumHeight(40)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def set(self, weeks, today, sel):
        self.weeks, self.today, self.sel = weeks, today, sel
        self.update()

    def _rects(self):
        n = max(1, len(self.weeks))
        gap = 6.0
        w = (self.width() - gap * (n - 1)) / n
        return [QRectF(i * (w + gap), 0, w, self.height()) for i in range(n)]

    def _at(self, pos):
        return next((i for i, r in enumerate(self._rects()) if r.contains(pos)), None)

    def mouseMoveEvent(self, e):
        i = self._at(e.position())
        if i != self.hover:
            self.hover = i
            self.update()
        if i is None or i >= len(self.weeks):
            QToolTip.hideText()
            return
        w = self.weeks[i]
        QToolTip.showText(e.globalPosition().toPoint(),
                          tr("第 {0} 周 {1:%d/%m}–{2:%d/%m}", i + 1, w["start"], w["end"]) + "\n" +
                          tr("{0} 张单 · €{1:,.0f}", w["n"], w["amount"]) + "\n" + tr("点一下：列表只看这一周"), self)

    def leaveEvent(self, _):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        i = self._at(e.position())
        if i is not None:
            self.clicked.emit(i)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        top = max([w["amount"] for w in self.weeks] + [0])
        for i, (r, w) in enumerate(zip(self._rects(), self.weeks)):
            future = w["start"] > self.today
            now = w["start"] <= self.today <= w["end"]
            sel = self.sel == (w["start"], w["end"])
            frac = (w["amount"] / top) if top else 0
            h = max(4.0, r.height() * (0.18 + 0.82 * frac)) if not future else r.height() * 0.24
            bar = QRectF(r.left(), r.bottom() - h, r.width(), h)
            if future and not w["n"]:
                p.setPen(QPen(QColor(_T("border")), 1, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(bar.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
                continue
            col = QColor(_T("accent") if (now or sel) else _T("accent_ring"))
            if self.hover == i and not (now or sel):
                col = col.lighter(130)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(bar, 4, 4)


class MonthMini(QFrame):
    """顶上的本月小卡：「11 月已下单 / 预算」+ 金额、每周一根柱子、用了预算的百分比（接口和 MonthPanel 一样）"""
    week_clicked = Signal(object)
    month_step = Signal(int)
    budget_clicked = Signal()

    def __init__(self, ui=None):
        super().__init__()
        self.setObjectName("MonthMini")
        self.data, self.sel, self.tone = None, None, "ok"
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 10, 16, 10)
        h.setSpacing(16)
        lv = QVBoxLayout()
        lv.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(2)
        self.prev, self.next = QToolButton(), QToolButton()
        for b, d, tip in ((self.prev, -1, tr("上个月")), (self.next, 1, tr("下个月"))):
            b.setText("‹" if d < 0 else "›")
            b.setAutoRaise(True)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedSize(16, 16)
            b.clicked.connect(lambda _=False, d=d: self.month_step.emit(d))
        self.lbl = QLabel("")
        self.lbl.setObjectName("MmLabel")
        top.addWidget(self.prev)
        top.addWidget(self.lbl)
        top.addWidget(self.next)
        top.addStretch()
        lv.addLayout(top)
        self.amt = QLabel("")
        self.amt.setObjectName("MmAmount")
        self.amt.setTextFormat(Qt.RichText)
        self.amt.setCursor(Qt.PointingHandCursor)
        self.amt.mousePressEvent = lambda _e: self.budget_clicked.emit()
        lv.addWidget(self.amt)
        lw = QWidget()
        lw.setLayout(lv)
        lw.setMinimumWidth(150)
        h.addWidget(lw)
        self.bars = _WeekBars()
        self.bars.clicked.connect(self._week)
        h.addWidget(self.bars, 1)
        self.pct = QLabel("")
        self.pct.setObjectName("MmPct")
        h.addWidget(self.pct)
        self.restyle()

    def _week(self, i):
        if not self.data or i >= len(self.data["weeks"]):
            return
        w = self.data["weeks"][i]
        key = (w["start"], w["end"])
        self.week_clicked.emit(None if self.sel == key else key)

    def show_month(self, data, year, month, scope, can_edit, sel=None, today=None):
        today = today or date.today()
        self.data, self.sel = data, sel
        self.lbl.setText(tr("{0} 月已下单 / 预算", month) if data["budget"] else tr("{0} 月已下单", month))
        self.lbl.setToolTip(tr("{0} 年 {1} 月 · {2}；不含待登记、已取消，不含税", year, month, scope))
        b = data["budget"]
        muted = _T("muted")
        txt = f"€{data['amount']:,.0f}"
        if b:
            txt += f" <span style='color:{muted}; font-size:12px; font-weight:500;'>/ {b:,.0f}</span>"
        else:
            txt += f" <span style='color:{muted}; font-size:12px; font-weight:500;'>· " + tr("{0} 张单", data["n"]) + "</span>"
        self.amt.setText(txt)
        self.amt.setToolTip((tr("点一下改这家店这个月的预算（不含税）") if can_edit else tr("各门店预算加起来；选一家门店可以改它的预算"))
                            + "\n" + tr("{0} 张单", data["n"]))
        if b:
            p = data["amount"] / b
            self.tone = "err" if p > 1 else "warn" if p >= 0.9 else "ok"
            self.pct.setText(f"{p:.0%}")
            self.pct.setToolTip(tr("超出 € {0:,.0f}", data["amount"] - b) if p > 1 else tr("还剩 € {0:,.0f}", b - data["amount"]))
        else:
            self.tone = "muted"
            self.pct.setText("—")
            self.pct.setToolTip(tr("没有预算"))
        self.bars.set(data["weeks"], today, sel)
        self.restyle()

    def restyle(self):
        from theme import NUM_FONT
        tone = _T(self.tone)
        self.setStyleSheet(
            f"#MonthMini {{ background:{_T('surface')}; border:1px solid {_T('line')}; border-radius:14px; }}"
            f"#MmLabel {{ font-size:11px; color:{_T('muted')}; }}"
            f"#MmAmount {{ font-family:\"{NUM_FONT}\"; font-size:16px; font-weight:600; color:{_T('text')}; }}"
            f"#MmPct {{ font-family:\"{NUM_FONT}\"; font-size:18px; font-weight:600; color:{tone}; }}"
            f"QToolButton {{ color:{_T('faint')}; font-size:13px; border:0; border-radius:8px; padding:0; }}"
            f"QToolButton:hover {{ color:{_T('accent')}; background:{_T('surface2')}; }}")
        self.bars.update()
