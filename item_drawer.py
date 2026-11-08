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
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QScrollArea, QWidget, QFrame, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
                               QPushButton)

from i18n import tr
from theme import T
from erp_widgets import MonthGrid

SOFT = {"ok": "ok_soft", "accent": "accent_soft", "warn": "warn_soft", "err": "err_soft", "info": "info_soft"}


def _clear(lay):
    while lay.count():
        w = lay.takeAt(0).widget()
        if w:
            w.deleteLater()


class ItemDrawer(QScrollArea):
    def __init__(self, width=360, on_more=None):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFixedWidth(width)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        d = QWidget()
        d.setObjectName("Drawer")
        v = QVBoxLayout(d)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)
        # 头
        head = QHBoxLayout()
        head.setSpacing(10)
        self.img = QLabel()
        self.img.setFixedSize(84, 84)
        self.img.setAlignment(Qt.AlignCenter)
        self.img.hide()
        head.addWidget(self.img, 0, Qt.AlignTop)
        hv = QVBoxLayout()
        hv.setSpacing(3)
        self.title = QLabel(tr("选中一行查看详情"))
        self.title.setStyleSheet("font-size:14px; font-weight:700;")
        self.title.setWordWrap(True)
        self.sub = QLabel("")
        self.sub.setObjectName("Faint")
        self.sub.setWordWrap(True)
        hv.addWidget(self.title)
        hv.addWidget(self.sub)
        self.more = None
        if on_more:
            self.more = QPushButton(tr("商品详情 ›"))
            self.more.setObjectName("CrumbLink")
            self.more.setCursor(Qt.PointingHandCursor)
            self.more.setToolTip(tr("入库记录、每天销量、各店库存（双击表格里的商品也能打开）"))
            self.more.clicked.connect(on_more)
            hv.addWidget(self.more, 0, Qt.AlignLeft)
        head.addLayout(hv, 1)
        v.addLayout(head)
        # 结论卡
        dec = QFrame()
        dec.setObjectName("MiniCard")
        dv = QVBoxLayout(dec)
        dv.setContentsMargins(14, 12, 14, 12)
        dv.setSpacing(8)
        top = QHBoxLayout()
        self.verdict = QLabel("")
        self.amount = QLabel("")
        self.amount.setObjectName("MiniLabel")
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
        self.sep.setFixedHeight(1)
        dv.addWidget(self.sep)
        g = QGridLayout()
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(0)
        self.stats = []
        for i, lab in enumerate((tr("库存"), tr("在途"), tr("月销"), tr("够卖"))):
            a = QLabel(lab)
            a.setObjectName("MiniLabel")
            b = QLabel("—")
            b.setObjectName("MiniValue")
            b.setTextFormat(Qt.RichText)
            g.addWidget(a, 0, i)
            g.addWidget(b, 1, i)
            g.setColumnStretch(i, 1)
            self.stats.append((a, b))
        dv.addLayout(g)
        v.addWidget(dec)
        # 每月销量
        sp = QFrame()
        sp.setObjectName("MiniCard")
        spv = QVBoxLayout(sp)
        spv.setContentsMargins(12, 10, 12, 10)
        spv.setSpacing(6)
        hl = QHBoxLayout()
        t = QLabel(tr("每月销量") + " ⓘ")
        t.setStyleSheet("font-weight:600; font-size:12px;")
        t.setToolTip(tr("近两年每个月卖了几件（上面今年、下面去年），颜色越深卖得越多；* = 本月到今天"))
        self.months_note = QLabel("")
        self.months_note.setObjectName("MiniLabel")
        hl.addWidget(t)
        hl.addStretch()
        hl.addWidget(self.months_note)
        spv.addLayout(hl)
        self.months = MonthGrid()
        spv.addWidget(self.months)
        v.addWidget(sp)
        # 各店库存
        oth = QFrame()
        oth.setObjectName("MiniCard")
        ov = QVBoxLayout(oth)
        ov.setContentsMargins(12, 10, 12, 10)
        ov.setSpacing(6)
        ot = QLabel(tr("各店库存") + " ⓘ")
        ot.setStyleSheet("font-weight:600; font-size:12px;")
        ot.setToolTip(tr("五家门店的库存、够卖几天、这次订几箱；蓝底是这一行的门店"))
        ov.addWidget(ot)
        sw = QWidget()
        self.stores = QVBoxLayout(sw)
        self.stores.setContentsMargins(0, 0, 0, 0)
        self.stores.setSpacing(2)
        ov.addWidget(sw)
        v.addWidget(oth)
        v.addStretch()
        self.setWidget(d)

    # ---------- 头 ----------
    def show_head(self, name, short, tip="", ean=None, settings=None, red=False):
        import product_images as PI
        self.title.setText(name or "")
        self.title.setStyleSheet("font-size:14px; font-weight:700;" + (f" color:{T('err')};" if red else ""))
        self.sub.setText(short or "")
        self.title.setToolTip(tip)
        self.sub.setToolTip(tip)
        PI.set_label_image(self.img, ean, 84, settings)

    # ---------- 结论 ----------
    def show_decision(self, verdict, tok, amount, left, right, right_tok=None, extra=""):
        """left / right = (标签, 箱数)：例如 (清单, 1) → (确定, 0)；extra = 第二行小字（调货等）"""
        self.verdict.setText(verdict or "")
        self.verdict.setVisible(bool(verdict))
        self.verdict.setStyleSheet(f"font-weight:700; font-size:12px; color:{T(tok)}; background:{T(SOFT.get(tok, 'surface2'))};"
                                   " border-radius:9px; padding:2px 10px;")
        self.amount.setText(f"€{amount:,.2f}" if amount and amount > 0 else "")
        self.sep.setStyleSheet(f"background:{T('line')}; border:none;")
        big = f"font-size:22px; font-weight:700; color:{T('text')};"
        dim = f"font-size:13px; color:{T('muted')};"

        def n(v):
            return f"{v:,.0f}" if isinstance(v, (int, float)) else str(v)
        rc = T(right_tok) if right_tok else T("text")
        html = (f"<span style='{dim}'>{left[0]}</span> <span style='{big}'>{n(left[1])}</span>"
                f"<span style='font-size:18px; color:{T('faint')};'>&nbsp;&nbsp;→&nbsp;&nbsp;</span>"
                f"<span style='{dim}'>{right[0]}</span> <span style='{big} color:{rc};'>{n(right[1])}</span>"
                f" <span style='{dim}'>{tr('箱')}</span>")
        if extra:
            html += f"<br><span style='{dim}'>{extra}</span>"
        self.boxes.setText(html)

    def show_tags(self, tags, reasons):
        """最多 3 个短标签 [(文字, 颜色 token)]；整句原因放鼠标提示"""
        _clear(self.tags)
        for text, tok in tags[:3]:
            c = QLabel(text)
            c.setStyleSheet(f"color:{T(tok)}; background:{T('surface2')}; border:1px solid {T('line')};"
                            " border-radius:9px; padding:1px 8px; font-size:11px;")
            c.setToolTip(reasons)
            self.tags.addWidget(c)
        more = QLabel((f"+{len(tags) - 3} " if len(tags) > 3 else "") + tr("为什么 ⓘ"))
        more.setObjectName("MiniLabel")
        more.setToolTip(reasons)
        more.setCursor(Qt.WhatsThisCursor)
        self.tags.addStretch()
        self.tags.addWidget(more)

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
        unit = f"<span style='font-size:11px; font-weight:400; color:{T('muted')};'>&nbsp;%s</span>"
        va.setText(f"{inv:g}")
        va.setStyleSheet(f"color:{T('warn')};" if inv < 0 else "")
        va.setToolTip(tr("账面负数，计算时当 0") if inv < 0 else tr("件"))
        if transit_boxes:
            vb.setText(f"{transit_boxes:g}" + unit % tr("箱"))
        elif transit_pcs:
            vb.setText(f"{transit_pcs:g}" + unit % tr("件"))
        else:
            vb.setText("—")
        vb.setStyleSheet(f"color:{T('info')};" if transit_boxes or transit_pcs else f"color:{T('faint')};")
        vb.setToolTip(tr("已经订了还没到货：{0:g} 件", transit_pcs) if transit_pcs else tr("没有在途"))
        vc.setText(monthly or "0")
        vc.setToolTip(tr("件 / 月"))
        ld.setText(cover_label or tr("够卖"))
        vd.setText((cover or "0") + unit % tr("天"))
        vd.setStyleSheet(f"color:{T(cover_tok)};" if cover_tok in ("err", "warn") else "")
        vd.setToolTip(cover_tip)

    # ---------- 每月销量 ----------
    def months_message(self, text, note=""):
        self.months.set_message(text)
        self.months_note.setText(note)
        self.months_note.setToolTip("")

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
            row.setStyleSheet(f"QFrame {{ background:{T('accent_soft')}; border-radius:6px; }}" if me else
                              "QFrame { background:transparent; }")
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 4, 8, 4)
            h.setSpacing(8)
            col = T("accent") if me else (T("faint") if empty else T("text"))
            wt = "font-weight:700;" if me else ""
            inv_t = tr("数据库没有") if inv is None else f"{inv:g} " + tr("件")
            days_t = (days or "—") + " " + tr("天") if inv and inv > 0 and days else "—"
            cells = ((name, 0), (inv_t, 56), (days_t if inv is not None else "", 56),
                     (tr("订 {0:g} 箱", on) if on else "", 62))
            for c, (txt, wdt) in enumerate(cells):
                lab = QLabel(txt)
                lab.setStyleSheet(f"background:transparent; color:{T('accent') if (c == 3 and on) else col}; {wt}")
                if c:
                    lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    lab.setFixedWidth(wdt)
                h.addWidget(lab, 0 if c else 1)
            self.stores.addWidget(row)
        if not rows:
            lab = QLabel(tr("没有门店库存数据"))
            lab.setObjectName("Faint")
            self.stores.addWidget(lab)
