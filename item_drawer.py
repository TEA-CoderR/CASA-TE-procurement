"""
商品详情栏（右边那一栏）：检查门店订货清单、总仓 / 门店采购的订货明细共用。
2026-11-07 用户：审核时看不了那么多字，只留精华、看一眼就懂；各店库存五家都列、本店高亮。

从上往下：
  头    小图 + 名称 + 一行资料（等级 · 箱规 · 件价；门店 / 供应商 / 编号 / 条码在鼠标提示）+ 商品详情 ›
  结论  判断彩色小标签 + 金额；「清单 1 → 确定 0 箱」大字；最多 3 个短标签（整句原因在鼠标提示）；
        一排四个数 库存 · 在途 · 月销 · 够卖
  每月销量  近两年 1–12 月方框，标题旁一行「今年 n 件 · 同比」
  各店库存  五家店一家一行，本店浅蓝底加粗，没货的字变淡
颜色都在 show_* 里按主题现取，换深浅色后下次选中一行就对了。
"""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QScrollArea, QWidget, QFrame, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
                               QPushButton, QToolTip, QSizePolicy)

from i18n import tr
from theme import T, NUM_FONT

SOFT = {"ok": "ok_soft", "accent": "accent_soft", "warn": "warn_soft", "err": "err_soft", "info": "info_soft",
        "violet": "violet_soft", "cyan": "cyan_soft"}


def _clear(lay):
    while lay.count():
        w = lay.takeAt(0).widget()
        if w:
            w.hide()              # 马上藏起来（deleteLater 要等下一轮才删，快速上下翻时旧标签会叠在新标签下面）
            w.deleteLater()


class MonthStrip(QWidget):
    """每月销量 · 今年（设计稿）：一排 12 个小方块，卖得越多蓝色越深，还没到的月份虚线框；下面 1月 / 6月 / 12月。
    鼠标停在一格上：这个月今年几件、去年同月几件。set_data / set_message / ytd 和 erp_widgets.MonthGrid 一样"""

    def __init__(self):
        super().__init__()
        self.data, self.year, self.cur_month, self.message = {}, None, None, ""
        self.setFixedHeight(46)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_data(self, values, keys):
        self.data = {k: float(v or 0) for k, v in zip(keys or [], values or [])}
        self.year, self.cur_month = (keys[-1] if keys else (None, None))
        self.message = ""
        self.update()

    def set_message(self, text):
        self.data, self.year, self.message = {}, None, text
        self.update()

    def ytd(self):
        if self.year is None:
            return None
        y, m = self.year, self.cur_month
        cur = sum(self.data.get((y, k), 0) for k in range(1, m + 1))
        prev = sum(self.data.get((y - 1, k), 0) for k in range(1, m + 1))
        full = sum(self.data.get((y - 1, k), 0) for k in range(1, 13))
        return cur, prev, full

    def _cell(self, k):
        gap = 4
        w = (self.width() - 11 * gap) / 12.0
        return QRectF(k * (w + gap), 0, w, 26)

    def mouseMoveEvent(self, e):
        if self.year is None:
            return
        for k in range(12):
            if self._cell(k).contains(e.position()):
                m = k + 1
                if m > self.cur_month:
                    break
                a, b = self.data.get((self.year, m), 0), self.data.get((self.year - 1, m), 0)
                QToolTip.showText(e.globalPosition().toPoint(),
                                  tr("{0} 月：今年 {1:g} 件 · 去年 {2:g} 件", m, a, b) +
                                  (tr("（到今天）") if m == self.cur_month else ""), self)
                return
        QToolTip.hideText()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = self.font()
        f.setPixelSize(10)
        p.setFont(f)
        if self.year is None:
            p.setPen(QColor(T("muted")))
            f.setPixelSize(12)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, self.message or "")
            p.end()
            return
        mx = max([self.data.get((self.year, m), 0) for m in range(1, 13)] + [1])
        acc = QColor(T("accent"))
        for k in range(12):
            r = self._cell(k)
            m = k + 1
            if m > self.cur_month:
                pen = QPen(QColor(T("border")), 1, Qt.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
                continue
            v = self.data.get((self.year, m), 0)
            c = QColor(acc)
            c.setAlphaF(0.12 + 0.88 * (v / mx))
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(r, 5, 5)
        p.setPen(QColor(T("faint")))
        y = QRectF(0, 30, self.width(), 14)
        p.drawText(y, Qt.AlignLeft | Qt.AlignVCenter, tr("1月"))
        p.drawText(y, Qt.AlignHCenter | Qt.AlignVCenter, tr("6月"))
        p.drawText(y, Qt.AlignRight | Qt.AlignVCenter, tr("12月"))
        p.end()


class ItemDrawer(QScrollArea):
    """照设计稿（2026-11-15）：头（64 图 / 两个字母的色块 + 名称 + 一行资料）· 结论卡 · 四个小方块 · 每月销量一排 · 各店库存"""

    def __init__(self, width=340, on_more=None):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFixedWidth(width)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        d = QWidget()
        d.setObjectName("Drawer")
        v = QVBoxLayout(d)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(16)
        # 头
        head = QHBoxLayout()
        head.setSpacing(14)
        box = QFrame()
        box.setFixedSize(64, 64)
        self.ph = QLabel("", box)                 # 没有图：名字头两个字母
        self.ph.setGeometry(0, 0, 64, 64)
        self.ph.setAlignment(Qt.AlignCenter)
        self.img = QLabel(box)
        self.img.setGeometry(0, 0, 64, 64)
        self.img.setAlignment(Qt.AlignCenter)
        self.img.hide()
        head.addWidget(box, 0, Qt.AlignTop)
        hv = QVBoxLayout()
        hv.setSpacing(4)
        self.title = QLabel(tr("选中一行查看详情"))
        self.title.setWordWrap(True)
        self.sub = QLabel("")
        self.sub.setWordWrap(True)
        hv.addWidget(self.title)
        hv.addWidget(self.sub)
        self.more = None
        if on_more:
            self.more = QPushButton(tr("商品详情 ›"))
            self.more.setObjectName("Link")
            self.more.setStyleSheet("padding:0; font-size:12px;")
            self.more.setCursor(Qt.PointingHandCursor)
            self.more.setToolTip(tr("入库记录、每天销量、各店库存（双击表格里的商品也能打开）"))
            self.more.clicked.connect(on_more)
            hv.addWidget(self.more, 0, Qt.AlignLeft)
        hv.addStretch()
        head.addLayout(hv, 1)
        v.addLayout(head)
        # 结论卡
        self.dec = QFrame()
        self.dec.setObjectName("DrawerCard")
        dv = QVBoxLayout(self.dec)
        dv.setContentsMargins(16, 16, 16, 16)
        dv.setSpacing(12)
        top = QHBoxLayout()
        self.verdict = QLabel("")
        self.amount = QLabel("")
        top.addWidget(self.verdict)
        top.addStretch()
        top.addWidget(self.amount)
        dv.addLayout(top)
        self.boxes = QLabel("—")
        self.boxes.setTextFormat(Qt.RichText)
        dv.addWidget(self.boxes)
        tw = QWidget()
        self.tags = QHBoxLayout(tw)
        self.tags.setContentsMargins(0, 0, 0, 0)
        self.tags.setSpacing(6)
        dv.addWidget(tw)
        self.sep = QFrame()
        self.sep.hide()
        v.addWidget(self.dec)
        # 四个小方块
        g = QGridLayout()
        g.setHorizontalSpacing(8)
        self.stats = []
        self.tiles = []
        for i, lab in enumerate((tr("库存"), tr("在途"), tr("月销"), tr("够卖"))):
            tile = QFrame()
            tile.setObjectName("DrawerTile")
            tv = QVBoxLayout(tile)
            tv.setContentsMargins(10, 10, 6, 10)
            tv.setSpacing(3)
            a = QLabel(lab)
            b = QLabel("—")
            b.setTextFormat(Qt.RichText)
            tv.addWidget(a)
            tv.addWidget(b)
            g.addWidget(tile, 0, i)
            g.setColumnStretch(i, 1)
            self.stats.append((a, b))
            self.tiles.append(tile)
        v.addLayout(g)
        # 每月销量
        mv = QVBoxLayout()
        mv.setSpacing(8)
        hl = QHBoxLayout()
        self.months_title = QLabel(tr("每月销量 · 近两年"))
        self.months_title.setToolTip(tr("今年在上、去年在下，方框里是这个月卖了几件，卖得越多底色越深"))
        self.months_note = QLabel("")
        self.months_note.setMaximumWidth(width - 150)
        hl.addWidget(self.months_title)
        hl.addStretch()
        hl.addWidget(self.months_note)
        mv.addLayout(hl)
        from erp_widgets import MonthGrid       # 2026-11-16 用户：要近两年方框、数量写在方框里（MonthStrip 留着不用）
        self.months = MonthGrid()
        mv.addWidget(self.months)
        v.addLayout(mv)
        # 各店库存
        ov = QVBoxLayout()
        ov.setSpacing(6)
        self.stores_title = QLabel(tr("各店库存"))
        self.stores_title.setToolTip(tr("五家门店的库存、够卖几天、这次订几箱；蓝底是这一行的门店"))
        ov.addWidget(self.stores_title)
        sw = QWidget()
        self.stores = QVBoxLayout(sw)
        self.stores.setContentsMargins(0, 0, 0, 0)
        self.stores.setSpacing(2)
        ov.addWidget(sw)
        v.addLayout(ov)
        v.addStretch()
        d.setMaximumWidth(width)
        self.setWidget(d)
        self._paint_static()

    def _paint_static(self):
        """跟主题走的固定样式（每次选中一行都重套一次：换深浅色后马上对）"""
        num = f'font-family:"{NUM_FONT}";'
        self.widget().setStyleSheet(
            f"#Drawer {{ background:{T('side') if T('bg') == '#070B14' else T('surface2')}; border:none; border-left:1px solid {T('line')}; }} "
            f"#DrawerCard {{ background:{T('surface2')}; border:1px solid {T('border')}; border-radius:14px; }} "
            f"#DrawerTile {{ background:{T('surface2')}; border:1px solid {T('line')}; border-radius:10px; }}")
        self.title.setProperty("base", f"font-size:15px; font-weight:600; background:transparent;")
        self.sub.setStyleSheet(f"font-size:12px; color:{T('muted')}; background:transparent;")
        self.amount.setStyleSheet(f"{num} color:{T('head')}; background:transparent;")
        for a, b in self.stats:
            a.setStyleSheet(f"font-size:11px; color:{T('muted')}; background:transparent;")
        for lb in (self.months_title, self.stores_title):
            lb.setStyleSheet(f"font-size:12px; color:{T('muted')}; background:transparent;")
        self.months_note.setStyleSheet(f"{num} font-size:12px; color:{T('head')}; background:transparent;")

    # ---------- 头 ----------
    def show_head(self, name, short, tip="", ean=None, settings=None, red=False):
        import product_images as PI
        self._paint_static()
        self.title.setText(name or "")
        self.title.setStyleSheet("font-size:15px; font-weight:600; background:transparent;" + (f" color:{T('err')};" if red else ""))
        self.sub.setText(short or "")
        self.title.setToolTip(tip)
        self.sub.setToolTip(tip)
        letters = "".join(ch for ch in (name or "") if ch.isalnum())[:2].upper()
        self.ph.setText(letters)
        self.ph.setStyleSheet(f"background:{T('accent_soft')}; color:{T('accent')}; border-radius:14px; "
                              f"font-size:18px; font-weight:700;")
        PI.set_label_image(self.img, ean, 64, settings)

    # ---------- 结论 ----------
    def show_decision(self, verdict, tok, amount, left, right, right_tok=None, extra=""):
        """left / right = (标签, 箱数)：例如 (清单, 1) → (确定, 0)；extra = 第二行小字（调货等）"""
        self.verdict.setText(verdict or "")
        self.verdict.setVisible(bool(verdict))
        self.verdict.setStyleSheet(f"font-weight:500; font-size:12px; color:{T(tok)}; background:{T(SOFT.get(tok, 'track'))};"
                                   " border-radius:11px; padding:3px 10px;")
        self.amount.setText(f"€{amount:,.2f}" if amount and amount > 0 else "")
        big = f"font-family:'{NUM_FONT}'; font-size:26px; font-weight:600; color:{T('text')};"
        dim = f"font-size:14px; color:{T('muted')};"

        def n(v):
            return f"{v:,.0f}" if isinstance(v, (int, float)) else str(v)
        rc = T(right_tok) if right_tok else T("text")
        html = (f"<span style='{dim}'>{left[0]}</span> <span style='{big}'>{n(left[1])}</span>"
                f"<span style='font-size:22px; color:{T('accent')};'>&nbsp;→&nbsp;</span>"
                f"<span style='{dim}'>{right[0]}</span> <span style='{big} color:{rc};'>{n(right[1])}</span>"
                f" <span style='{dim}'>{tr('箱')}</span>")
        if extra:
            html += f"<br><span style='{dim}'>{extra}</span>"
        self.boxes.setText(html)

    def show_tags(self, tags, reasons):
        """最多 3 个短标签 [(文字, 颜色 token)]；整句原因放鼠标提示"""
        _clear(self.tags)
        shown = [(t_, k_) for t_, k_ in tags[:3]] + ([(f"+{len(tags) - 3}", "muted")] if len(tags) > 3 else [])
        for text, tok in shown:
            c = QLabel(text)
            col = T(tok) if tok in ("err", "warn") else T("head")
            c.setStyleSheet(f"color:{col}; background:{T('side_active') if T('bg') == '#070B14' else T('track')};"
                            " border-radius:6px; padding:3px 9px; font-size:12px;")
            c.setFixedSize(c.fontMetrics().horizontalAdvance(text) + 22, 24)
            c.setToolTip(reasons)
            c.setCursor(Qt.WhatsThisCursor)
            self.tags.addWidget(c, 0, Qt.AlignLeft)
        self.tags.addStretch(1)
        self.dec.setToolTip(reasons)

    def show_stats(self, inv=None, transit_boxes=0, transit_pcs=0, monthly="", cover="", cover_tok=None,
                   cover_label=None, cover_tip=""):
        """一排四个数；inv 是 None = 数据库没有 → 全写 —"""
        (la, va), (lb, vb), (lc, vc), (ld, vd) = self.stats
        if inv is None:
            for _, b in self.stats:
                b.setText("—")
                b.setStyleSheet("")
            ld.setText(tr("够卖"))
            return
        unit = f"<span style='font-size:11px; font-weight:400; color:{T('muted')};'>%s</span>"
        base = f"font-family:'{NUM_FONT}'; font-size:15px; font-weight:600; background:transparent;"
        for _a, b_ in self.stats:
            b_.setStyleSheet(base)
        va.setText(f"{inv:g}")
        va.setStyleSheet(base + (f"color:{T('warn')};" if inv < 0 else ""))
        va.setToolTip(tr("账面负数，计算时当 0") if inv < 0 else tr("件"))
        if transit_boxes:
            vb.setText(f"{transit_boxes:g}" + unit % tr("箱"))
        elif transit_pcs:
            vb.setText(f"{transit_pcs:g}" + unit % tr("件"))
        else:
            vb.setText("—")
        vb.setStyleSheet(base + (f"color:{T('info')};" if transit_boxes or transit_pcs else f"color:{T('faint')};"))
        vb.setToolTip(tr("已经订了还没到货：{0:g} 件", transit_pcs) if transit_pcs else tr("没有在途"))
        vc.setText(monthly or "0")
        vc.setToolTip(tr("件 / 月"))
        ld.setText(cover_label or tr("够卖"))
        vd.setText((cover or "0") + unit % tr("天"))
        vd.setStyleSheet(base + (f"color:{T(cover_tok)};" if cover_tok in ("err", "warn") else ""))
        vd.setToolTip(cover_tip)

    # ---------- 每月销量 ----------
    def months_message(self, text, note=""):
        self.months.set_message(text)
        self.months.setToolTip(note or "")
        self.months_note.setText("ⓘ" if note else "")      # 原因（常是数据库报错的原文）只放鼠标提示，不把英文报错摆在界面上
        self.months_note.setToolTip(note or "")

    def show_months(self, vals, keys):
        self.months.set_data(vals, keys)
        cur, prev, full = self.months.ytd()
        m = keys[-1][1]
        note = tr("今年 {0:g} 件", round(cur, 1)) + " · " + (tr("同比 {0:+.0%}", cur / prev - 1) if prev else tr("去年同期 0"))
        self.months_note.setText(note)
        self.months_note.setToolTip(tr("今年 1–{0} 月 {1:g} 件，去年同期 {2:g} 件，去年全年 {3:g} 件",
                                       m, round(cur, 1), round(prev, 1), round(full, 1)))

    # ---------- 各店库存 ----------
    def show_stores(self, rows):
        """rows = [(店名, 库存件数 或 None（数据库没有）, 够卖天数文字, 这次订几箱, 是不是本店)]"""
        _clear(self.stores)
        for name, inv, days, on, me in rows:
            empty = (inv is None or inv <= 0) and not on
            row = QFrame()
            row.setStyleSheet(f"QFrame {{ background:{T('accent_soft')}; border-radius:8px; }}" if me else
                              "QFrame { background:transparent; }")
            h = QHBoxLayout(row)
            h.setContentsMargins(10, 7, 10, 7)
            h.setSpacing(8)
            dark = T('bg') == '#070B14'
            col = ("#CFE0FF" if dark else T("accent")) if me else (T("faint") if empty else T("text"))
            wt = "font-weight:600;" if me else ""
            parts = [tr("数据库没有") if inv is None else f"{inv:g} " + tr("件")]
            if me:
                parts.append(tr("本店"))
            if on:
                parts.append(tr("订 {0:g} 箱", on))
            n_ = QLabel(name)
            n_.setStyleSheet(f"background:transparent; font-size:12px; color:{col}; {wt}")
            r_ = QLabel(" · ".join(parts))
            r_.setStyleSheet(f"background:transparent; font-family:'{NUM_FONT}'; font-size:12px; "
                             f"color:{col if me else (T('faint') if empty else T('head'))}; {wt}")
            row.setToolTip(tr("够卖 {0} 天", days) if inv and inv > 0 and days else "")
            h.addWidget(n_, 1)
            h.addWidget(r_)
            self.stores.addWidget(row)
        if not rows:
            lab = QLabel(tr("没有门店库存数据"))
            lab.setObjectName("Faint")
            self.stores.addWidget(lab)
