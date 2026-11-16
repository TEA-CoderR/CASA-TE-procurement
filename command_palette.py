"""
Ctrl K 指令面板（2026-11-16 照设计稿「指令面板和助手」做）：
  一个框搜到 操作 · 供应商 · 页面；↑ ↓ 选，Enter 执行，Tab 交给采购助手去做，Esc 关掉。
  · 操作：发送 X 订单邮件（有待登记时）、查看 X 在途订单、邮件核对、同步 Excel、新订单、换深浅色…
  · 供应商：订单跟踪里出现过的供应商（几张没到货、最近哪天下单）
  · 页面：所有页面
  · 最后一行永远是「交给助手：…」
不会改数据：会改数据的操作只是打开对应的窗口（那里照旧先确认）。
挑选 / 排序的逻辑写成不依赖界面的 build_groups()，有测试。
"""
import re
from datetime import date

from PySide6.QtCore import Qt, QRectF, QSize, QEvent
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QFontMetrics
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QStyledItemDelegate, QStyle, QFrame, QAbstractItemView, QWidget)

from i18n import tr


def _norm(s):
    return re.sub(r"\s+", " ", str(s or "").lower()).strip()


def matches(q, *texts):
    """q 里每个词都在 texts 里出现就算对上（不分大小写）；空 q 全对上"""
    q = _norm(q)
    if not q:
        return True
    hay = " ".join(_norm(t) for t in texts)
    return all(w in hay for w in q.split())


def _score(q, text):
    """排序：开头就对上 > 整词对上 > 包含"""
    q, t = _norm(q), _norm(text)
    if not q:
        return 0
    if t.startswith(q):
        return 0
    if f" {q}" in f" {t}":
        return 1
    return 2


def supplier_index(rows, today=None):
    """订单跟踪的行 → 每家供应商：{name, open, drafts, first_open, last}"""
    today = today or date.today()
    out = {}
    for r in rows or []:
        name = r.get("supplier") or ""
        if not name or r.get("status") in ("cancel",):
            continue
        k = r.get("sup_key") or name.upper()
        s = out.setdefault(k, {"name": name, "open": [], "drafts": [], "last": None})
        if r.get("status") == "draft":
            s["drafts"].append(r)
        elif r.get("open"):
            s["open"].append(r)
        d = r.get("date")
        if d and (s["last"] is None or d > s["last"]):
            s["last"] = d
    return out


def build_groups(q, pages, rows, actions, limit=4):
    """
    → [(组名, [项])]；项 = dict(key, title, sub, tok, abbr, data)
      pages   = [(页面键, 页面名, 入口名)]
      rows    = 订单跟踪的行（order_tracking.rows）
      actions = [(键, 名称, 小字, 搜索用的词)]
    """
    groups = []
    sups = supplier_index(rows)
    ops = []
    if q.strip():
        for k, s in sorted(sups.items(), key=lambda kv: _score(q, kv[1]["name"])):
            if not matches(q, s["name"]):
                continue
            if s["drafts"]:
                stores = sorted({r.get("store_name") or "" for r in s["drafts"]})
                ops.append({"key": "send", "title": tr("发送 {0} 订单邮件", s["name"]),
                            "sub": tr("{0} 张待登记 · 先预览再发", len(s["drafts"])) + (" · " + "、".join(stores[:3]) if stores else ""),
                            "tok": "accent", "abbr": "✉", "data": s["drafts"][0]})
            if s["open"]:
                first = min((r["date"] for r in s["open"] if r.get("date")), default=None)
                ops.append({"key": "transit", "title": tr("查看 {0} 在途订单", s["name"]),
                            "sub": tr("{0} 张", len(s["open"])) + (tr(" · 最早 {0:%d/%m} 下单", first) if first else ""),
                            "tok": "accent", "abbr": "⇢", "data": s["name"]})
            if len(ops) >= limit:
                break
    for key, title, sub, words in actions:
        if len(ops) >= limit + 2:
            break
        if q.strip() and matches(q, title, words):
            ops.append({"key": key, "title": title, "sub": sub, "tok": "accent", "abbr": "▶", "data": None})
        elif not q.strip() and len(ops) < 4:
            ops.append({"key": key, "title": title, "sub": sub, "tok": "accent", "abbr": "▶", "data": None})
    if ops:
        groups.append((tr("操作"), ops))
    if q.strip():
        sl = []
        for k, s in sorted(sups.items(), key=lambda kv: (_score(q, kv[1]["name"]), kv[1]["name"])):
            if matches(q, s["name"]):
                bits = []
                if s["open"]:
                    bits.append(tr("{0} 张没到货", len(s["open"])))
                if s["drafts"]:
                    bits.append(tr("{0} 张待登记", len(s["drafts"])))
                if s["last"]:
                    bits.append(tr("最近下单 {0:%d/%m}", s["last"]))
                sl.append({"key": "supplier", "title": s["name"], "sub": " · ".join(bits) or tr("订单跟踪里的供应商"),
                           "tok": "cyan", "abbr": (s["name"][:1] or "?").upper(), "data": s["name"]})
            if len(sl) >= limit:
                break
        if sl:
            groups.append((tr("供应商"), sl))
    pl = [{"key": "page", "title": name, "sub": entry, "tok": "violet", "abbr": "#", "data": key}
          for key, name, entry in pages if matches(q, name, entry, key)]
    pl.sort(key=lambda x: _score(q, x["title"]))
    if pl:
        groups.append((tr("页面"), pl[:limit if q.strip() else 3]))
    if q.strip():
        groups.append((tr("助手"), [{"key": "agent", "title": tr("交给助手：{0}", q.strip()),
                                    "sub": tr("会改数据的每一步都先问你"), "tok": "accent", "abbr": "✦", "data": q.strip()}]))
    return groups


# =====================================================================
def _T(k):
    from theme import T
    return T(k)


class _Delegate(QStyledItemDelegate):
    def sizeHint(self, opt, ix):
        return QSize(opt.rect.width(), 30 if ix.data(Qt.UserRole + 1) == "head" else 52)

    def paint(self, p, opt, ix):
        kind = ix.data(Qt.UserRole + 1)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(opt.rect)
        base = QFont(opt.font)
        if kind == "head":
            f = QFont(base)
            f.setPixelSize(11)
            f.setWeight(QFont.DemiBold)
            f.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
            p.setFont(f)
            p.setPen(QColor(_T("faint")))
            p.drawText(r.adjusted(12, 8, -12, 0), Qt.AlignLeft | Qt.AlignVCenter, ix.data(Qt.DisplayRole))
            p.restore()
            return
        it = ix.data(Qt.UserRole)
        sel = bool(opt.state & QStyle.State_Selected)
        hov = bool(opt.state & QStyle.State_MouseOver)
        box = r.adjusted(0, 1, 0, -1)
        if sel or hov:
            p.setPen(QPen(QColor(_T("accent_ring")), 1) if sel else Qt.NoPen)
            p.setBrush(QColor(_T("accent_soft") if sel else _T("hover")))
            p.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        c = QColor(_T(it["tok"]))
        ic = QRectF(box.left() + 12, box.center().y() - 16, 32, 32)
        bg, edge = QColor(c), QColor(c)
        bg.setAlpha(31)
        edge.setAlpha(68)
        p.setPen(QPen(edge, 1))
        p.setBrush(bg)
        p.drawRoundedRect(ic, 9, 9)
        f = QFont(base)
        f.setPixelSize(13)
        f.setWeight(QFont.Bold)
        p.setFont(f)
        p.setPen(c)
        p.drawText(ic, Qt.AlignCenter, it["abbr"])
        x = ic.right() + 12
        kb_w = 0
        if sel:
            from theme import NUM_FONT
            fk = QFont(base)
            fk.setFamilies([NUM_FONT] + list(base.families()))
            fk.setPixelSize(11)
            p.setFont(fk)
            kb_w = QFontMetrics(fk).horizontalAdvance("Enter") + 16
            kb = QRectF(box.right() - 12 - kb_w, box.center().y() - 10, kb_w, 20)
            p.setPen(QPen(QColor(_T("accent_ring")), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(kb, 6, 6)
            p.setPen(QColor(_T("accent")))
            p.drawText(kb, Qt.AlignCenter, "Enter")
        w = box.right() - x - 16 - kb_w
        f1 = QFont(base)
        f1.setPixelSize(13)
        f1.setWeight(QFont.Medium)
        p.setFont(f1)
        p.setPen(QColor(_T("text")))
        p.drawText(QRectF(x, box.top() + 7, w, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(f1).elidedText(it["title"], Qt.ElideRight, int(w)))
        f2 = QFont(base)
        f2.setPixelSize(12)
        p.setFont(f2)
        p.setPen(QColor(_T("muted")))
        p.drawText(QRectF(x, box.top() + 27, w, 17), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(f2).elidedText(it["sub"] or "", Qt.ElideRight, int(w)))
        p.restore()


class CommandPalette(QDialog):
    """run(项) 执行一项；ask(文字) 交给助手"""

    def __init__(self, win, pages, rows_fn, actions, run, ask, text=""):
        super().__init__(win)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.pages, self.rows_fn, self.actions, self.run_fn, self.ask_fn = pages, rows_fn, actions, run, ask
        self._rows = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        self.box = QFrame()
        self.box.setObjectName("Palette")
        outer.addWidget(self.box)
        v = QVBoxLayout(self.box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        top = QHBoxLayout()
        top.setContentsMargins(20, 16, 20, 16)
        top.setSpacing(12)
        self.icon = QLabel()
        self.icon.setFixedSize(20, 20)
        top.addWidget(self.icon)
        self.edit = QLineEdit(text)
        self.edit.setObjectName("PaletteEdit")
        self.edit.setPlaceholderText(tr("搜操作、供应商、页面，或者直接说要做什么…"))
        self.edit.textChanged.connect(self._fill)
        self.edit.installEventFilter(self)
        top.addWidget(self.edit, 1)
        esc = QLabel("Esc")
        esc.setObjectName("PaletteKey")
        top.addWidget(esc)
        v.addLayout(top)
        sep = QFrame()
        sep.setObjectName("PaletteSep")
        sep.setFixedHeight(1)
        v.addWidget(sep)
        self.list = QListWidget()
        self.list.setObjectName("PaletteList")
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setItemDelegate(_Delegate(self.list))
        self.list.setMouseTracking(True)
        self.list.viewport().setAttribute(Qt.WA_Hover, True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.itemClicked.connect(lambda it: self._run(it))
        lw = QWidget()
        lv = QVBoxLayout(lw)
        lv.setContentsMargins(10, 8, 10, 10)
        lv.addWidget(self.list)
        v.addWidget(lw, 1)
        sep2 = QFrame()
        sep2.setObjectName("PaletteSep")
        sep2.setFixedHeight(1)
        v.addWidget(sep2)
        foot = QLabel()
        foot.setObjectName("PaletteFoot")
        foot.setTextFormat(Qt.RichText)
        self.foot = foot
        v.addWidget(foot)
        self.setFixedWidth(700)
        self.restyle()
        self._fill()

    def restyle(self):
        from theme import NUM_FONT, icon
        dark = True
        try:
            from theme import current
            dark = current() == "dark"
        except Exception:
            pass
        self.icon.setPixmap(icon("search", _T("accent"), 20).pixmap(20, 20))
        k = f"font-family:'{NUM_FONT}'; color:{_T('head')};"
        self.foot.setText(f"<span style=\"{k}\">↑ ↓</span> {tr('选择')} &nbsp;&nbsp;&nbsp; "
                          f"<span style=\"{k}\">Enter</span> {tr('执行')} &nbsp;&nbsp;&nbsp; "
                          f"<span style=\"{k}\">Tab</span> {tr('交给助手去做')}")
        self.setStyleSheet(
            f"#Palette {{ background:{_T('surface') if dark else _T('surface')}; border:1px solid {_T('accent_ring')};"
            f" border-radius:18px; }}"
            f"#PaletteEdit {{ border:0; background:transparent; font-size:18px; color:{_T('text')}; padding:0; min-height:28px; }}"
            f"#PaletteEdit:focus {{ border:0; }}"
            f"#PaletteKey {{ font-family:'{NUM_FONT}'; font-size:11px; color:{_T('head')}; border:1px solid {_T('border')};"
            f" border-radius:6px; padding:2px 8px; }}"
            f"#PaletteSep {{ background:{_T('line')}; border:0; }}"
            f"#PaletteList {{ background:transparent; outline:0; }}"
            f"#PaletteList::item {{ background:transparent; border:0; }}"
            f"#PaletteFoot {{ padding:12px 20px; font-size:12px; color:{_T('muted')}; background:{_T('side') if dark else _T('surface2')};"
            f" border-bottom-left-radius:18px; border-bottom-right-radius:18px; }}")

    def rows(self):
        if self._rows is None:
            try:
                self._rows = self.rows_fn() or []
            except Exception:
                self._rows = []
        return self._rows

    def _fill(self, *_):
        q = self.edit.text()
        groups = build_groups(q, self.pages, self.rows(), self.actions)
        self.list.clear()
        first = None
        for name, items in groups:
            h = QListWidgetItem(name)
            h.setData(Qt.UserRole + 1, "head")
            h.setFlags(Qt.NoItemFlags)
            self.list.addItem(h)
            for it in items:
                li = QListWidgetItem(it["title"])
                li.setData(Qt.UserRole, it)
                li.setData(Qt.UserRole + 1, "item")
                self.list.addItem(li)
                if first is None:
                    first = li
        if first is not None:
            self.list.setCurrentItem(first)
        h = sum(30 if self.list.item(i).data(Qt.UserRole + 1) == "head" else 52 for i in range(self.list.count()))
        self.list.setFixedHeight(max(110, min(h + 6, 400)))       # 结果少时面板也矮（设计稿）
        self.adjustSize()

    def _move(self, d):
        n = self.list.count()
        i = self.list.currentRow()
        for _ in range(n):
            i = (i + d) % n
            it = self.list.item(i)
            if it.data(Qt.UserRole + 1) == "item":
                self.list.setCurrentItem(it)
                self.list.scrollToItem(it)
                return

    def eventFilter(self, obj, e):
        if obj is self.edit and e.type() == QEvent.KeyPress:
            k = e.key()
            if k in (Qt.Key_Down, Qt.Key_Up):
                self._move(1 if k == Qt.Key_Down else -1)
                return True
            if k in (Qt.Key_Return, Qt.Key_Enter):
                self._run(self.list.currentItem())
                return True
            if k == Qt.Key_Tab:
                t = self.edit.text().strip()
                if t:
                    self.accept()
                    self.ask_fn(t)
                return True
            if k == Qt.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(obj, e)

    def _run(self, li):
        if li is None or li.data(Qt.UserRole + 1) != "item":
            return
        it = li.data(Qt.UserRole)
        self.accept()
        self.run_fn(it)

    def showEvent(self, e):
        super().showEvent(e)
        p = self.parentWidget()
        if p is not None:
            g = p.geometry()
            self.move(g.left() + (g.width() - self.width()) // 2, g.top() + 70)
        self.edit.setFocus()
        self.edit.deselect()
        self.edit.end(False)
