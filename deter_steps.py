"""
DETERSIVO 四步里的第 ① ③ ④ 步（2026-11-16 照设计稿「收集文件 / 比价分配 / 下单发送」重做；第 ② 步审核清单在 order_check）。
  ① 收集文件：工作文件夹一行 + 三张能拖文件进来的卡（门店清单 · 批发商报价 · 扣减）+ 右边「准备好了吗」
  ③ 比价分配：进度大卡 + 每家门店一行（比完 / 正在比 / 排队）+ 右边「正在做」（处理过程）
  ④ 下单发送：这一批程序生成的订单按供应商分组（门店 · 订单文件 · 金额 · 状态）+ 右边订单邮件预览
     「发送这一封」= 原来的发送订单窗口（照旧先确认）；「全部发送」先列出发给谁、几封再发
老的 DropZone / ws_status / feed / stage / bar 照旧在（main 里的老代码照用），这里只是换了摆法。
纯函数（price_state / batch_orders / ready_items）不依赖界面，有测试。
"""
import os
import threading
from datetime import datetime, date, timedelta

from i18n import tr

STORES = {1: "FOIANO", 2: "MARLIA", 3: "S.FILIPPO", 4: "S.MARCO", 5: "S.CONCORDIO"}
SUP_TONES = ("accent", "cyan", "violet", "ok", "warn", "err")


# ===================================================================== 纯函数
def week_start(today=None):
    d = today or date.today()
    return d - timedelta(days=d.weekday())


def price_state(mtime, today=None, mail_newer=False):
    """报价文件状态：("本周" / "上周的" / "dd/mm 的", 颜色)；邮件里有更新的 → "邮件里有新的" 橙色"""
    if mail_newer:
        return tr("邮件里有新的"), "warn"
    if mtime is None:
        return "—", "muted"
    d = mtime.date() if isinstance(mtime, datetime) else mtime
    ws = week_start(today)
    if d >= ws:
        return tr("本周"), "ok"
    if d >= ws - timedelta(days=7):
        return tr("上周的"), "warn"
    return tr("{0:%d/%m} 的", d), "warn"


def when_text(t, today=None):
    """今天 08:12 / 昨天 / 13/11"""
    if not t:
        return ""
    d = today or date.today()
    if t.date() == d:
        return tr("今天 {0:%H:%M}", t)
    if t.date() == d - timedelta(days=1):
        return tr("昨天")
    return f"{t:%d/%m}"


def ready_items(lists, prices, elim, grid, today=None):
    """
    「准备好了吗」几行：[(状态 1 好 / 0 要看 / -1 没关系, 标题, 小字)]
    lists = [(门店编号或 None, 修改时间)]；prices = [(名称, 修改时间, 邮件里有新的)]；elim = {门店编号: [文件]}；
    grid = (文件名, 修改时间) 或 None
    """
    out = []
    known = [s for s, _t in lists if s]
    unknown = len(lists) - len(known)
    if not lists:
        out.append((0, tr("还没有门店清单"), tr("把门店发来的清单拖到左边")))
    else:
        miss = [STORES[k] for k in STORES if k not in known]
        out.append((1, tr("{0} 家门店清单", len(set(known))) if known else tr("{0} 份门店清单", len(lists)),
                    tr("{0} 还没有", "、".join(miss)) if miss and known else ""))
        if unknown:
            out.append((0, tr("{0} 份认不出门店", unknown), tr("开始时会问你是哪家")))
    if not prices:
        out.append((0, tr("还没有批发商报价"), tr("把这周的报价拖到中间")))
    else:
        old = [(n, t, m) for n, t, m in prices if m or price_state(t, today)[1] != "ok"]
        good = len(prices) - len(old)
        for n, t, m in old:
            out.append((0, tr("{0} 报价不是最新的", n),
                        tr("邮件里有新的 → 点「✉ 邮件」放进来") if m else price_state(t, today)[0]))
        if good:
            out.append((1, tr("{0} 份报价是本周的", good), ""))
    if grid:
        out.append((1, tr("Griglia {0:%d/%m}", grid[1]) if grid[1] else "Griglia", grid[0]))
    else:
        out.append((-1, tr("没有 Griglia"), tr("不判断是不是 Griglia 商品")))
    n_el = len(elim)
    out.append((-1, tr("扣减文件 {0} 家", n_el) if n_el else tr("没有扣减文件"),
                tr("{0} 家没有，可以不放", 5 - n_el) if n_el < 5 else ""))
    return out


def batch_orders(orders, source="wholesale", today=None, days=10):
    """
    最近一次比价生成的订单（程序的单号 = draft_id(来源, 生成那天, 门店, 供应商)，确认后单号不变）：
    返回 (生成那天, [(单号, 单)])；没有返回 (None, [])
    """
    import order_tracking as OT
    d0 = today or date.today()
    days_list = [(d0 - timedelta(days=i)).isoformat() for i in range(days)]
    by_day = {}
    for oid, o in orders.items():
        if o.get("origin") != source or o.get("state") == "cancel" or not o.get("store") or not o.get("supplier"):
            continue
        for d in days_list:
            if OT.draft_id(source, d, o["store"], o["supplier"]) == oid:
                by_day.setdefault(d, []).append((oid, o))
                break
    if not by_day:
        return None, []
    day = max(by_day)
    rows = sorted(by_day[day], key=lambda x: (str(x[1].get("supplier")), str(x[1].get("store"))))
    return day, rows


def send_state(o):
    """(文字, 颜色)：待发送 / 已发送 / 已确认 / 已发货 / 已到货"""
    import order_tracking as OT
    s = OT.status(o)
    return {"draft": (tr("待发送"), "accent"), "ordered": (tr("已发送") if o.get("sent") else tr("已下单"), "ok"),
            "confirmed": (tr("已确认"), "cyan"), "shipped": (tr("已发货"), "violet"),
            "arrived": (tr("已到货"), "ok"), "partial": (tr("部分到货"), "warn"),
            "stuck": (tr("卡住了"), "err")}.get(s, (s, "muted"))


# ===================================================================== 界面零件
from PySide6.QtCore import Qt, Signal, QObject                                   # noqa: E402
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QVBoxLayout, QGridLayout, QLabel, QPushButton,   # noqa: E402
                               QWidget, QScrollArea, QMessageBox, QPlainTextEdit, QSizePolicy)
from theme import T                                                              # noqa: E402
import result_page as RP                                                         # noqa: E402


QSS = """
QFrame#DeterCard {{ background:{surface}; border:1px solid {line}; border-radius:14px; }}
QFrame#DeterCard[drag="true"] {{ border-color:{accent}; background:{sel}; }}
QFrame#DeterRow {{ background:{canvas}; border-radius:10px; }}
QWidget#MissInner {{ background:transparent; }}
QScrollArea#MissScroll {{ background:transparent; border:none; }}
QLabel#DeterDrop {{ border:1.5px dashed {line2}; border-radius:12px; color:{muted}; padding:12px; }}
QPushButton#DeterX {{ border:none; background:transparent; color:{muted}; font-size:12px; padding:0; min-height:0; min-width:0; }}
QPushButton#DeterX:hover {{ color:{err}; }}
QPushButton#DeterLink {{ border:none; background:transparent; color:{accent}; padding:0 2px; min-height:0; }}
QPushButton#DeterNote {{ text-align:left; background:{sel}; border:1px solid {ring}; border-radius:12px; padding:10px 12px; }}
QPushButton#DeterNote:hover {{ border-color:{accent}; }}
QFrame#StoreLine {{ background:{surface}; border:1px solid {line}; border-radius:14px; }}
QFrame#StoreLine[tone="warn"] {{ border-color:{warn}; }}
QPushButton#SendRow {{ background:transparent; border:none; border-top:1px solid {line}; text-align:left; }}
QPushButton#SendRow:hover {{ background:{hover}; }}
QPushButton#SendRow:checked {{ background:{sel}; border-left:3px solid {accent}; }}
QFrame#MailSide {{ background:{surface}; border:1px solid {line}; border-radius:14px; }}
QFrame#MailField {{ background:{canvas}; border:1px solid {line}; border-radius:10px; }}
QPlainTextEdit#MailBody {{ background:{canvas}; border:1px solid {line}; border-radius:12px; padding:8px; }}
QFrame#MailAtt {{ background:{ok_soft}; border:1px solid {ok}; border-radius:10px; }}
"""


def qss():
    return RP.qss() + QSS.format(surface=T("surface"), line=T("line"), line2=T("line"), accent=T("accent"), sel=T("accent_soft"),
                                 canvas=T("canvas"), muted=T("muted"), err=T("err"), ring=T("accent_ring"), warn=T("warn"),
                                 hover=T("hover"), ok_soft=T("ok_soft"), ok=T("ok"))


def _title(text, tip=None):
    lb = QLabel(text + ("  ⓘ" if tip else ""))
    lb.setStyleSheet("font-size:15px; font-weight:600;")
    if tip:
        lb.setToolTip(tip)
    return lb


def _tag(text, tone="accent"):
    lb = QLabel(text)
    lb.setStyleSheet(f"padding:2px 6px; border-radius:5px; background:{T(tone + '_soft')}; color:{T(tone)};"
                     f"font-size:11px; font-weight:600;")
    return lb


def _icon(state):
    """准备好了吗前面的小圆：✓ 绿 / ! 黄 / – 灰"""
    m, tone = {1: ("✓", "ok"), 0: ("!", "warn")}.get(state, ("–", "muted"))
    lb = QLabel(m)
    lb.setFixedSize(22, 22)
    lb.setAlignment(Qt.AlignCenter)
    bg = T(tone + "_soft") if tone != "muted" else T("track")
    lb.setStyleSheet(f"background:{bg}; color:{T(tone)}; border-radius:11px; font-weight:700; font-size:12px;")
    return lb


class DropCard(QFrame):
    """能把 Excel 拖进来的卡（标题 · 右上角小东西 · 内容 · 虚线「拖到这里 · 选择文件…」）"""
    files_dropped = Signal(list)

    def __init__(self, title, tip, hint, pick_title):
        super().__init__()
        self.setObjectName("DeterCard")
        self.setAcceptDrops(True)
        self.pick_title = pick_title
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(10)
        h = QHBoxLayout()
        h.addWidget(_title(title, tip))
        h.addStretch()
        self.right = QHBoxLayout()
        self.right.setSpacing(8)
        h.addLayout(self.right)
        v.addLayout(h)
        self.head = QVBoxLayout()
        self.head.setSpacing(8)
        v.addLayout(self.head)
        self.body = QVBoxLayout()
        self.body.setSpacing(6)
        v.addLayout(self.body)
        self.hint_text = hint
        self.drop = QLabel()
        self.drop.setObjectName("DeterDrop")
        self.drop.setAlignment(Qt.AlignCenter)
        self.drop.setTextFormat(Qt.RichText)
        self.drop.linkActivated.connect(lambda _l: self._pick())
        self.restyle()
        v.addWidget(self.drop)
        self.foot = QVBoxLayout()
        self.foot.setSpacing(8)
        v.addLayout(self.foot)
        v.addStretch()

    def restyle(self):
        self.drop.setText(self.hint_text + f" · <a href='#' style='color:{T('accent')}; text-decoration:none'>"
                          + tr("选择文件…") + "</a>")

    def _pick(self):
        from PySide6.QtWidgets import QFileDialog
        files, _ = QFileDialog.getOpenFileNames(self, self.pick_title, "", tr("Excel 文件 ({0})", "*.xlsx *.xls *.xlsm"))
        if files:
            self.files_dropped.emit([os.path.normpath(f) for f in files])

    def _drag(self, on):
        self.setProperty("drag", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e):
        from erp_widgets import _local_files
        if _local_files(e.mimeData(), (".xlsx", ".xls", ".xlsm")):
            e.acceptProposedAction()
            self._drag(True)
        else:
            e.ignore()

    def dragLeaveEvent(self, _e):
        self._drag(False)

    def dropEvent(self, e):
        from erp_widgets import _local_files
        self._drag(False)
        files = _local_files(e.mimeData(), (".xlsx", ".xls", ".xlsm"))
        if files:
            e.acceptProposedAction()
            self.files_dropped.emit(files)


def _file_row(tag, name, sub, on_remove=None, tone="accent", bad=False):
    f = QFrame()
    f.setObjectName("DeterRow")
    h = QHBoxLayout(f)
    h.setContentsMargins(10, 7, 6, 7)
    h.setSpacing(10)
    if tag:
        h.addWidget(_tag(tag, "warn" if bad else tone))
    v = QVBoxLayout()
    v.setSpacing(1)
    n = QLabel(name)
    n.setToolTip(name)
    n.setMinimumWidth(10)
    n.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    v.addWidget(n)
    if sub:
        s = RP.muted(sub, 11)
        s.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        v.addWidget(s)
    h.addLayout(v, 1)
    if on_remove:
        x = QPushButton("✕")
        x.setObjectName("DeterX")
        x.setFixedSize(24, 24)
        x.setCursor(Qt.PointingHandCursor)
        x.setToolTip(tr("从这里拿走（挪进旧文件夹，不删除）"))
        x.clicked.connect(on_remove)
        h.addWidget(x)
    return f


# ===================================================================== ① 收集文件
def build_collect(win, lay):
    """lay = 第 1 步的竖排版面（main._page_order 里，在老的工作文件夹 Panel 位置）"""
    page = lay.parentWidget()
    win._dc_page = page
    path = QHBoxLayout()
    path.setSpacing(10)
    ic = QLabel("▤")
    ic.setStyleSheet(f"color:{T('muted')};")
    path.addWidget(ic)
    win.ws_label.setParent(None)
    win.ws_label.setObjectName("")
    win.ws_label.setStyleSheet(f"color:{T('muted')}; font-size:12px;")
    path.addWidget(win.ws_label)
    for text, fn, tip in ((tr("打开"), win.btn_folder.click, tr("打开工作文件夹")),
                          (tr("换位置…"), win.change_ws_root, None)):
        b = QPushButton(text)
        b.setObjectName("DeterLink")
        b.setCursor(Qt.PointingHandCursor)
        if tip:
            b.setToolTip(tip)
        b.clicked.connect(fn)
        path.addWidget(b)
    path.addStretch()
    lay.addLayout(path)
    win.banner_folder.setParent(None)
    lay.addWidget(win.banner_folder)

    row = QHBoxLayout()
    row.setSpacing(16)
    lay.addLayout(row, 1)
    grid = QHBoxLayout()
    grid.setSpacing(14)
    row.addLayout(grid, 1)
    win._dc_lists = DropCard(tr("门店清单"), tr("门店发来的订货清单，一家店一份，新的替换旧的；清单会一直留着，想重新比价直接再点「开始检查」"),
                             tr("把清单拖到这里"), tr("门店清单"))
    win._dc_prices = DropCard(tr("批发商报价"), tr("每周新的报价替换同一家的旧报价，五家店共用；放进来时选是哪家"),
                              tr("把报价拖到这里"), tr("批发商报价"))
    win._dc_elim = DropCard(tr("扣减 · 已下单的"), tr("每家店已处理 / 已下单的文件（可以没有），比价时从清单里扣掉"),
                            tr("把扣减文件拖到这里"), tr("扣减文件"))
    win._dc_lists.files_dropped.connect(win._drop_lists)
    win._dc_prices.files_dropped.connect(win._drop_suppliers)
    win._dc_elim.files_dropped.connect(win._drop_eliminare)
    for c in (win._dc_lists, win._dc_prices, win._dc_elim):
        c.setMinimumWidth(230)
        grid.addWidget(c, 1, Qt.AlignTop)
    win._dc_count = RP.mono("", 12, 600, "ok")
    win._dc_lists.right.addWidget(win._dc_count)
    win._dc_cells = QHBoxLayout()
    win._dc_cells.setSpacing(6)
    win._dc_lists.head.addLayout(win._dc_cells)
    # 报价卡右上角：邮件里的新报价 · 供应商名称
    for b in (win.btn_price_mail, win.btn_sup_names):
        b.setParent(None)
        win._dc_prices.right.addWidget(b)
    win._dc_elim_n = RP.mono("", 12, 600, "muted")
    win._dc_elim.right.addWidget(win._dc_elim_n)
    note = QPushButton()
    note.setObjectName("DeterNote")
    note.setCursor(Qt.PointingHandCursor)
    note.setToolTip(tr("列出每家店上一次的 DETERSIVO 订单，勾上还没到货的 → 自动变成这家店的扣减文件（不用再手动放）"))
    nl = QVBoxLayout(note)
    nl.setContentsMargins(12, 10, 12, 10)
    nl.setSpacing(2)
    a = QLabel(tr("上周的单还没到？"))
    a.setStyleSheet("font-weight:500;")
    nl.addWidget(a)
    nl.addWidget(RP.muted(tr("勾上没到的 → 自动扣掉，不用再放文件") + "  ›", 12))
    for w in note.findChildren(QWidget):
        w.setAttribute(Qt.WA_TransparentForMouseEvents)
    note.setMinimumHeight(58)
    note.clicked.connect(win.open_deter_arrival)
    win._dc_elim.foot.addWidget(note)
    chk = QPushButton(tr("报价表和数据库对比…"))
    chk.setObjectName("DeterLink")
    chk.setCursor(Qt.PointingHandCursor)
    chk.setToolTip(tr("只读检查：报价表里的商品数据库有没有、登记的供应商对不对、进价和箱规一样不一样"))
    chk.clicked.connect(win.check_prices_vs_db)
    win._dc_prices.foot.addWidget(chk, 0, Qt.AlignLeft)

    side = QFrame()
    side.setObjectName("DeterCard")
    side.setFixedWidth(270)
    sv = QVBoxLayout(side)
    sv.setContentsMargins(18, 16, 18, 16)
    sv.setSpacing(12)
    sv.addWidget(_title(tr("准备好了吗")))
    win._dc_ready = QVBoxLayout()
    win._dc_ready.setSpacing(12)
    sv.addLayout(win._dc_ready)
    sv.addStretch()
    row.addWidget(side, 0, Qt.AlignTop)
    page.setStyleSheet(qss())


def _clear(lay):
    RP._clear(lay)


def render_collect(win, st, root):
    """refresh_ws 读好的 workspace.scan 结果 → 三张卡 + 准备好了吗 + 底部一句话"""
    if not hasattr(win, "_dc_lists"):
        return
    import workspace as W
    import supplier_names as SN
    import griglia as G
    S = win._S
    win._dc_page.setStyleSheet(qss())
    for c in (win._dc_lists, win._dc_prices, win._dc_elim):
        c.restyle()
    today = date.today()

    def mtime(folder, f):
        try:
            return datetime.fromtimestamp(os.path.getmtime(os.path.join(folder, f)))
        except OSError:
            return None

    # 门店清单
    _clear(win._dc_cells)
    have = {sid for sid, _f in st["lists"] if sid}
    for k, name in STORES.items():
        c = QLabel(name.replace("S.", "S")[:3])
        c.setAlignment(Qt.AlignCenter)
        c.setToolTip(name + " · " + (tr("清单已放") if k in have else tr("还没有清单")))
        on = k in have
        c.setStyleSheet(f"padding:6px 0; border-radius:8px; font-size:11px; font-weight:600;"
                        f"background:{T('ok_soft') if on else T('track')}; color:{T('ok') if on else T('muted')};")
        win._dc_cells.addWidget(c, 1)
    win._dc_count.setText(f"{len(have)} / 5")
    RP.set_tone(win._dc_count, "ok" if len(have) == 5 else "warn" if have else "muted")
    _clear(win._dc_lists.body)
    lists_t = []
    for sid, f in sorted(st["lists"], key=lambda x: (x[0] is None, x[0] or 0, x[1])):
        t = mtime(W.lists_dir(root), f)
        lists_t.append((sid, t))
        name = W.STORES.get(sid) if sid else None
        label = (name or tr("⚠ 哪家店？")) + " · " + f
        win._dc_lists.body.addWidget(_file_row(
            (name or "?").replace("S.", "S")[:3], f, ((name or tr("认不出门店")) + " · " + when_text(t, today)).strip(" ·"),
            on_remove=lambda _=False, lb=label: win._remove_ws_file("lists", lb), bad=not name))
    win._dc_lists.drop.setVisible(True)

    # 批发商报价
    _clear(win._dc_prices.body)
    found = {b["label"]: b for b in ((getattr(win.price_mail, "res", None) or {}).get("found") or []) if b.get("newer")}
    prices_t = []
    for f in st["prices"]:
        t = mtime(W.price_dir(root), f)
        ok_name = SN.recognized(f, S)
        lb = SN.label_of(f, S) if ok_name else tr("⚠ 哪家？")
        newer = lb in found
        prices_t.append((lb, t, newer))
        state, tone = price_state(t, today, newer)
        fr = QFrame()
        fr.setObjectName("DeterRow")
        fr.setStyleSheet(f"QFrame#DeterRow {{ border:1px solid {T('warn') if tone == 'warn' or not ok_name else T('line')}; }}")
        v = QVBoxLayout(fr)
        v.setContentsMargins(12, 9, 6, 9)
        v.setSpacing(3)
        h = QHBoxLayout()
        h.setSpacing(8)
        n = QLabel(lb)
        n.setStyleSheet("font-weight:600;" + (f"color:{T('warn')};" if not ok_name else ""))
        h.addWidget(n, 1)
        h.addWidget(RP.chip(state, tone))
        x = QPushButton("✕")
        x.setObjectName("DeterX")
        x.setFixedSize(22, 22)
        x.setToolTip(tr("从这里拿走（挪进旧文件夹，不删除）"))
        x.clicked.connect(lambda _=False, lab=f"{lb} · {f}": win._remove_ws_file("prices", lab))
        h.addWidget(x)
        v.addLayout(h)
        fl = RP.muted(f, 12)
        fl.setToolTip(f)
        fl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        v.addWidget(fl)
        if t:
            v.addWidget(RP.mono(tr("更新于 {0:%d/%m %H:%M}", t), 11, 500, "muted"))
        if not ok_name:
            fr.setToolTip(tr("认不出是哪家批发商：点右上角「供应商名称」选一下"))
        win._dc_prices.body.addWidget(fr)

    # 扣减
    _clear(win._dc_elim.body)
    for k, name in STORES.items():
        fs = st["elim"].get(k) or []
        if fs:
            for f in fs:
                win._dc_elim.body.addWidget(_file_row(None, name, f, on_remove=lambda _=False, lb=f"{name} · {f}":
                                                      win._remove_ws_file("elim", lb)))
        else:
            r = QFrame()
            r.setObjectName("DeterRow")
            h = QHBoxLayout(r)
            h.setContentsMargins(10, 7, 10, 7)
            n = QLabel(name)
            n.setFixedWidth(96)
            h.addWidget(n)
            h.addWidget(RP.muted(tr("没有（可以没有）"), 12), 1)
            win._dc_elim.body.addWidget(r)
    for f in st["elim_loose"]:
        win._dc_elim.body.addWidget(_file_row("?", f, tr("不知道是哪家店的：重新拖进来一次"), bad=True,
                                              on_remove=lambda _=False, lb=tr("⚠ 哪家店？") + " · " + f:
                                              win._remove_ws_file("elim", lb)))
    win._dc_elim_n.setText(tr("{0} 家", len(st["elim"])))

    # 准备好了吗
    g = G.find_file(root)
    grid = (os.path.basename(g), datetime.fromtimestamp(os.path.getmtime(g))) if g else None
    _clear(win._dc_ready)
    items = ready_items(lists_t, prices_t, st["elim"], grid, today)
    for state, title, sub in items:
        h = QHBoxLayout()
        h.setSpacing(10)
        h.addWidget(_icon(state), 0, Qt.AlignTop)
        v = QVBoxLayout()
        v.setSpacing(2)
        t1 = QLabel(title)
        t1.setWordWrap(True)
        v.addWidget(t1)
        if sub:
            s = RP.muted(sub, 12)
            s.setWordWrap(True)
            v.addWidget(s)
        h.addLayout(v, 1)
        w = QWidget()
        w.setLayout(h)
        win._dc_ready.addWidget(w)
    # 底部一句话
    n_old = sum(1 for _n, t, m in prices_t if m or price_state(t, today)[1] != "ok")
    parts = [tr("{0} 份清单", len(st["lists"])), tr("{0} 份报价", len(st["prices"]))]
    if n_old:
        parts.append(tr("{0} 份报价不是最新的", n_old))
    unknown = sum(1 for sid, _f in st["lists"] if not sid)
    if unknown:
        parts.append(tr("{0} 份认不出门店", unknown))
    win.ws_status.setText(" · ".join(parts))
    win.ws_status.setStyleSheet(f"color:{T('head')};")


# ===================================================================== ③ 比价分配
def build_compare(win, lay):
    page = lay.parentWidget()
    win._cp_page = page
    row = QHBoxLayout()
    row.setSpacing(16)
    lay.addLayout(row, 1)
    mid = QVBoxLayout()
    mid.setSpacing(12)
    row.addLayout(mid, 1)
    top = QFrame()
    top.setObjectName("DeterCard")
    th = QHBoxLayout(top)
    th.setContentsMargins(20, 16, 20, 16)
    th.setSpacing(18)
    tv = QVBoxLayout()
    tv.setSpacing(4)
    win._cp_cap = RP.muted(tr("比价 · 一家一家来"), 12)
    tv.addWidget(win._cp_cap)
    win._cp_big = QLabel(tr("还没开始"))
    win._cp_big.setStyleSheet("font-size:22px; font-weight:700;")
    tv.addWidget(win._cp_big)
    th.addLayout(tv, 1)
    pv = QVBoxLayout()
    pv.setSpacing(6)
    win.bar.setParent(None)
    win.bar.setFixedHeight(10)
    pv.addWidget(win.bar)
    sl = QHBoxLayout()
    for w in (win.stage, win.elapsed):
        w.setParent(None)
    win.stage.setObjectName("Faint")
    sl.addWidget(win.stage, 1)
    sl.addWidget(win.elapsed)
    pv.addLayout(sl)
    th.addLayout(pv, 1)
    win.btn_run.setParent(None)
    th.addWidget(win.btn_run)
    mid.addWidget(top)
    win._cp_rows = QVBoxLayout()
    win._cp_rows.setSpacing(10)
    mid.addLayout(win._cp_rows)
    mid.addStretch()

    side = QFrame()
    side.setObjectName("DeterCard")
    side.setFixedWidth(360)
    sv = QVBoxLayout(side)
    sv.setContentsMargins(0, 0, 0, 0)
    sv.setSpacing(0)
    hh = QHBoxLayout()
    hh.setContentsMargins(18, 14, 18, 12)
    hh.addWidget(_title(tr("正在做")))
    hh.addStretch()
    win._cp_store = RP.muted("", 12)
    hh.addWidget(win._cp_store)
    sv.addLayout(hh)
    win.feed.setParent(None)
    win.feed.setObjectName("LogRows")
    win.feed.setMinimumHeight(0)
    win.feed.setStyleSheet("QTextEdit { background:transparent; border:none; border-top:1px solid %s; padding:6px 10px; }"
                           % T("line"))
    sv.addWidget(win.feed, 1)
    row.addWidget(side)
    page.setStyleSheet(qss())


def compare_rows(batch, queue, results, current, running, info):
    """
    每家门店一行的状态：[(门店名, 状态 done / now / fail / wait, 小字, 进度 0–1, 金额, 用时)]
    batch = {门店编号: 文件夹}；queue = 还没比的文件夹；results = [(文件夹名, 成功, 原因)]；current = 正在比的文件夹；
    info = {文件夹名: (金额, 商品行, 有报价, 供应商数, 缺几个, 用时秒)}
    """
    out = []
    res = {n: (ok, why) for n, ok, why in results or []}
    qn = {os.path.basename(os.path.normpath(f)) for f in queue or []}
    cur = os.path.basename(os.path.normpath(current)) if current else ""
    for sid, folder in sorted((batch or {}).items(), key=lambda kv: int(kv[0])):
        n = os.path.basename(os.path.normpath(folder))
        name = STORES.get(int(sid), n)
        inf = (info or {}).get(n)
        if n in res and not res[n][0]:
            out.append((name, "fail", res[n][1] or tr("没有完成"), 0, "—", ""))
        elif n in res or (inf and n not in qn and n != cur):
            amt, rows, priced, sups, miss, secs = inf or (None, 0, 0, 0, 0, None)
            sub = tr("{0} / {1} 分给 {2} 家", priced, rows, sups) + (" · " + tr("缺 {0}", miss) if miss else "")
            out.append((name, "done", sub, 1, RP.eur(amt) if amt is not None else "—",
                        tr("{0:.0f} 秒", secs) if secs else ""))
        elif n == cur and running:
            out.append((name, "now", "", None, "—", ""))
        elif n in qn:
            out.append((name, "wait", tr("排队"), 0, "—", ""))
        else:
            out.append((name, "wait", tr("还没比价"), 0, "—", ""))
    return out


def render_compare(win):
    if not hasattr(win, "_cp_rows"):
        return
    S = win._S
    win._cp_page.setStyleSheet(qss())
    batch = (S.get("wholesale_batch") or {}).get("stores") or {}
    cur = win.folder_edit.text().strip()
    rows = compare_rows(batch, getattr(win, "_batch_queue", None) or [], getattr(win, "_batch_results", None) or [],
                        cur, win.running, getattr(win, "_batch_info", {}))
    _clear(win._cp_rows)
    for name, state, sub, p, amt, secs in rows:
        f = QFrame()
        f.setObjectName("StoreLine")
        f.setProperty("tone", "warn" if state == "fail" else "")
        h = QHBoxLayout(f)
        h.setContentsMargins(18, 12, 18, 12)
        h.setSpacing(14)
        m, tone = {"done": ("✓", "ok"), "now": ("…", "accent"), "fail": ("!", "err")}.get(state, ("·", "muted"))
        ic = QLabel(m)
        ic.setFixedSize(28, 28)
        ic.setAlignment(Qt.AlignCenter)
        if state == "now":
            ic.setStyleSheet(f"background:{T('accent')}; color:{T('canvas')}; border-radius:14px; font-weight:700;")
        else:
            ic.setStyleSheet(f"background:{T(tone + '_soft') if tone != 'muted' else 'transparent'}; color:{T(tone)};"
                             f"border-radius:14px; font-weight:700;" + (f"border:1px solid {T('line')};" if tone == "muted" else ""))
        h.addWidget(ic)
        n = QLabel(name)
        n.setFixedWidth(110)
        n.setStyleSheet("font-weight:600;")
        h.addWidget(n)
        v = QVBoxLayout()
        v.setSpacing(6)
        s = QLabel(win.stage.text() if state == "now" else sub)
        s.setStyleSheet(f"color:{T('err') if state == 'fail' else T('muted') if state == 'wait' else T('head')};")
        v.addWidget(s)
        bar = RP.Progress()
        bar.set((win.bar.value() / 100) if state == "now" else (p or 0),
                {"done": "ok", "now": "accent", "fail": "err"}.get(state, "muted"))
        v.addWidget(bar)
        h.addLayout(v, 1)
        a = RP.mono(amt, 13, 600)
        a.setFixedWidth(90)
        a.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        h.addWidget(a)
        t = RP.mono(secs, 12, 500, "muted")
        t.setFixedWidth(56)
        t.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        h.addWidget(t)
        if state == "wait":
            from PySide6.QtWidgets import QGraphicsOpacityEffect
            eff = QGraphicsOpacityEffect(f)
            eff.setOpacity(0.6)
            f.setGraphicsEffect(eff)
        win._cp_rows.addWidget(f)
    if not rows:
        win._cp_rows.addWidget(RP.muted(tr("检查完门店清单、点「确认，开始比价」后，这里一家一家显示"), 13))
    done = sum(1 for r in rows if r[1] == "done")
    if win.running:
        left = len(getattr(win, "_batch_queue", None) or [])
        win._cp_cap.setText(tr("正在比价 · 一家一家来"))
        win._cp_big.setText(tr("{0} / {1} 家比完", done, len(rows)) if rows else tr("正在比价"))
        win._cp_store.setText(os.path.basename(os.path.normpath(cur)) if cur else "")
        if left:
            win._cp_cap.setText(tr("正在比价 · 后面还有 {0} 家", left))
    else:
        win._cp_cap.setText(tr("比价 · 一家一家来"))
        win._cp_big.setText(tr("{0} / {1} 家比完", done, len(rows)) if rows else tr("还没开始"))
        win._cp_store.setText("")


# ===================================================================== ④ 下单发送
class _Sig(QObject):
    loaded = Signal(object)
    draft = Signal(object)


def build_send(win, lay):
    page = lay.parentWidget()
    win._sd_page = page
    win._sd_sel = None
    win._sd_st = None
    win._sd_sig = _Sig(page)
    win._sd_sig.loaded.connect(lambda r: _loaded(win, r))
    win._sd_sig.draft.connect(lambda r: _draft_ready(win, r))
    row = QHBoxLayout()
    row.setSpacing(16)
    lay.addLayout(row, 1)
    mid = QVBoxLayout()
    mid.setSpacing(12)
    row.addLayout(mid, 1)
    head = QHBoxLayout()
    hv = QVBoxLayout()
    hv.setSpacing(4)
    win._sd_big = QLabel(tr("还没有订单"))
    win._sd_big.setStyleSheet("font-size:22px; font-weight:700;")
    hv.addWidget(win._sd_big)
    win._sd_line = QLabel("")
    win._sd_line.setTextFormat(Qt.RichText)
    win._sd_line.setStyleSheet(f"color:{T('head')};")
    hv.addWidget(win._sd_line)
    head.addLayout(hv, 1)
    from page_kit import Seg
    win._sd_view = Seg([("sup", tr("按供应商")), ("store", tr("按门店"))])
    win._sd_by = "sup"
    win._sd_view.changed.connect(lambda k: (setattr(win, "_sd_by", k), _render_groups(win)))
    head.addWidget(win._sd_view, 0, Qt.AlignBottom)
    mid.addLayout(head)
    sc = QScrollArea()
    sc.setWidgetResizable(True)
    sc.setFrameShape(QFrame.NoFrame)
    sc.setObjectName("MissScroll")
    inner = QWidget()
    inner.setObjectName("MissInner")
    sc.viewport().setObjectName("MissInner")
    win._sd_groups = QVBoxLayout(inner)
    win._sd_groups.setContentsMargins(0, 0, 0, 0)
    win._sd_groups.setSpacing(12)
    sc.setWidget(inner)
    mid.addWidget(sc, 1)

    side = QFrame()
    side.setObjectName("MailSide")
    side.setFixedWidth(380)
    sv = QVBoxLayout(side)
    sv.setContentsMargins(18, 16, 18, 16)
    sv.setSpacing(10)
    sv.addWidget(_title(tr("订单邮件"), tr("收件人从以前发过的订单信里学来的 · 正文照上次写的")))
    win._sd_fields = QVBoxLayout()
    win._sd_fields.setSpacing(6)
    sv.addLayout(win._sd_fields)
    win._sd_body = QPlainTextEdit()
    win._sd_body.setObjectName("MailBody")
    win._sd_body.setReadOnly(True)
    sv.addWidget(win._sd_body, 1)
    win._sd_att = QVBoxLayout()
    sv.addLayout(win._sd_att)
    win._sd_btn = QPushButton(tr("发送这一封…"))
    win._sd_btn.setObjectName("Primary")
    win._sd_btn.setMinimumHeight(40)
    win._sd_btn.setToolTip(tr("打开发送窗口：发件 / 收件 / 正文都还能改，发之前会再确认"))
    win._sd_btn.clicked.connect(lambda: _send_one(win))
    win._sd_btn.setEnabled(False)
    sv.addWidget(win._sd_btn)
    row.addWidget(side)
    page.setStyleSheet(qss())


def refresh_send(win):
    """切到第 4 步时：后台读一次订单跟踪（共享文件夹），读好了画列表"""
    if not hasattr(win, "_sd_groups"):
        return
    win._sd_page.setStyleSheet(qss())
    S = win._S

    def work():
        try:
            import commands as CMD
            st = CMD.tracking_store(S)
            day, rows = batch_orders(st.orders)
            win._sd_sig.loaded.emit((st, day, rows, None))
        except Exception as e:
            win._sd_sig.loaded.emit((None, None, [], e))
    if not getattr(win, "_sd_loading", False):
        win._sd_loading = True
        threading.Thread(target=work, daemon=True).start()


def _loaded(win, r):
    win._sd_loading = False
    st, day, rows, err = r
    win._sd_st, win._sd_day, win._sd_rows = st, day, rows
    win._sd_err = err
    _render_groups(win)


def _render_groups(win):
    _clear(win._sd_groups)
    rows = getattr(win, "_sd_rows", None) or []
    err = getattr(win, "_sd_err", None)
    if err or not rows:
        win._sd_big.setText(tr("还没有订单"))
        win._sd_line.setText(html_escape(str(err)) if err else
                             tr("比价完成后，程序生成的订单在这里，一封封发给批发商"))
        win._sd_groups.addStretch()
        _fill_mail(win, None)
        if hasattr(win, "_sd_all"):
            win._sd_all.setEnabled(False)
            win._sd_all.setText(tr("全部发送") + "  →")
        return
    total = sum(float(o.get("amount") or 0) for _i, o in rows)
    todo = [i for i, o in rows if o.get("draft")]
    sent = len(rows) - len(todo)
    day = getattr(win, "_sd_day", None)
    win._sd_line.setStyleSheet(f"color:{T('head')};")
    win._sd_big.setText(tr("{0} 张订单 · {1}", len(rows), RP.eur(total)))
    win._sd_line.setText(tr("已发 <b style='color:{2}'>{0}</b> · 待发 <b>{1}</b> · 发完自动变成「已下单」，订单跟踪里看进展",
                            sent, len(todo), T("ok")) + (f" · {day[8:10]}/{day[5:7]}" if day else ""))
    if hasattr(win, "_sd_all"):
        win._sd_all.setEnabled(bool(todo))
        win._sd_all.setText(tr("全部发送（{0}）", len(todo)) + "  →")
    by_store = getattr(win, "_sd_by", "sup") == "store"
    groups = {}
    for oid, o in rows:
        k = str(o.get("store")) if by_store else str(o.get("supplier"))
        groups.setdefault(k, []).append((oid, o))
    tot_of = {k: sum(float(o.get("amount") or 0) for _i, o in v) for k, v in groups.items()}
    import order_tracking as OT
    sups = sorted({str(o.get("supplier")) for _i, o in rows})
    tone_of = {s: SUP_TONES[i % len(SUP_TONES)] for i, s in enumerate(sups)}
    if win._sd_sel not in {i for i, _o in rows}:
        first = max(groups, key=lambda x: tot_of[x]) if not by_store else min(groups)
        win._sd_sel = next((i for i, o in groups[first] if o.get("draft")), (todo or [rows[0][0]])[0])
    for k in sorted(groups, key=lambda x: (str(x) if by_store else -tot_of[x])):
        items = groups[k]
        card = QFrame()
        card.setObjectName("DeterCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        h = QHBoxLayout()
        h.setContentsMargins(16, 12, 16, 12)
        h.setSpacing(10)
        h.addWidget(RP.Dot(tone_of.get(k, "accent") if not by_store else "accent", 10))
        t = QLabel(OT.STORE_NAMES.get(k, k) if by_store else k)
        t.setStyleSheet("font-weight:600;")
        h.addWidget(t)
        h.addWidget(RP.muted(tr("{0} 家店", len(items)) if not by_store else tr("{0} 家批发商", len(items)), 12))
        h.addStretch()
        h.addWidget(RP.mono(RP.eur(sum(float(o.get("amount") or 0) for _i, o in items)), 13, 600))
        v.addLayout(h)
        for oid, o in items:
            b = QPushButton()
            b.setObjectName("SendRow")
            b.setCheckable(True)
            b.setChecked(oid == win._sd_sel)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(40)
            rh = QHBoxLayout(b)
            rh.setContentsMargins(16, 6, 16, 6)
            rh.setSpacing(12)
            n = QLabel(str(o.get("supplier")) if by_store else OT.STORE_NAMES.get(str(o.get("store")), o.get("store")))
            n.setFixedWidth(110)
            n.setStyleSheet("font-weight:500;")
            rh.addWidget(n)
            fl = RP.muted(os.path.basename(str(o.get("file") or "")) or tr("没有订单文件"), 12)
            fl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            rh.addWidget(fl, 1)
            a = RP.mono(RP.eur(o.get("amount")) if o.get("amount") is not None else "—", 13, 500)
            a.setFixedWidth(84)
            a.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            rh.addWidget(a)
            stt, tone = send_state(o)
            c = RP.chip(stt, tone)
            rh.addWidget(c)
            for w in b.findChildren(QWidget):
                w.setAttribute(Qt.WA_TransparentForMouseEvents)
            b.clicked.connect(lambda _=False, k2=oid: _pick(win, k2))
            v.addWidget(b)
        win._sd_groups.addWidget(card)
    win._sd_groups.addStretch()
    _pick(win, win._sd_sel, redraw=False)


def html_escape(s):
    import html
    return html.escape(s)


def _pick(win, oid, redraw=True):
    win._sd_sel = oid
    if redraw:
        for b in win._sd_page.findChildren(QPushButton, "SendRow"):
            b.setChecked(False)
        _render_groups(win)
        return
    st = win._sd_st
    if st is None or oid not in st.orders:
        _fill_mail(win, None)
        return
    _fill_mail(win, "loading")
    S = win._S
    tok = object()
    win._sd_tok = tok

    def work():
        import commands as CMD
        try:
            d = CMD.order_mail_draft(S, st, oid, fetch_body=False)
            win._sd_sig.draft.emit((tok, oid, d, None))
        except Exception as e:
            win._sd_sig.draft.emit((tok, oid, None, e))
    threading.Thread(target=work, daemon=True).start()


def _draft_ready(win, r):
    tok, oid, d, err = r
    if tok is not getattr(win, "_sd_tok", None):
        return
    _fill_mail(win, d if d is not None else err)


def _fill_mail(win, d):
    _clear(win._sd_fields)
    _clear(win._sd_att)
    win._sd_body.setPlainText("")
    win._sd_draft = None
    o = (win._sd_st.orders.get(win._sd_sel) if win._sd_st is not None and win._sd_sel else None) or {}
    win._sd_btn.setEnabled(False)
    if d is None or d == "loading":
        win._sd_fields.addWidget(RP.muted(tr("正在准备邮件…") if d == "loading" else tr("选左边一张订单"), 12))
        return
    if isinstance(d, Exception):
        e = RP.muted(str(d), 12)
        e.setWordWrap(True)
        RP.set_tone(e, "warn")
        win._sd_fields.addWidget(e)
        return
    win._sd_draft = d
    for label, val in ((tr("发件"), d.get("from")), (tr("收件"), ", ".join(d.get("to") or []) or tr("没有收件人 ⚠")),
                       (tr("抄送"), ", ".join(d.get("cc") or []) or "—"), (tr("主题"), d.get("subject"))):
        f = QFrame()
        f.setObjectName("MailField")
        h = QHBoxLayout(f)
        h.setContentsMargins(12, 7, 12, 7)
        h.setSpacing(10)
        lb = RP.muted(label, 12)
        lb.setFixedWidth(34)
        h.addWidget(lb)
        v = QLabel(str(val or ""))
        v.setToolTip(str(val or ""))
        v.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        if label == tr("收件") and not d.get("to"):
            v.setStyleSheet(f"color:{T('warn')};")
        h.addWidget(v, 1)
        win._sd_fields.addWidget(f)
    win._sd_body.setPlainText(d.get("body") or "")
    for p, name, ok in d.get("files") or []:
        f = QFrame()
        f.setObjectName("MailAtt")
        h = QHBoxLayout(f)
        h.setContentsMargins(10, 7, 10, 7)
        h.setSpacing(10)
        h.addWidget(_tag("XLS", "ok" if ok else "warn"))
        n = QLabel(name if ok else tr("{0}（这台电脑上没有）", name))
        n.setToolTip(p)
        n.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        h.addWidget(n, 1)
        win._sd_att.addWidget(f)
    win._sd_btn.setEnabled(bool(o.get("draft")))
    win._sd_btn.setText(tr("发送这一封…") if o.get("draft") else send_state(o)[0])


def _send_one(win):
    if win._sd_st is None or not win._sd_sel:
        return
    from send_order_dialog import open_send
    S = win._S
    res = open_send(win, win._sd_st, S, win._sd_sel, on_sent=None)
    if res:
        refresh_send(win)
        if getattr(win, "tracking", None) is not None:
            win.tracking.check_badge()


def send_all(win):
    """全部发送：先列出发给谁、几封（没有收件人 / 附件不在的跳过），确认后一封封发"""
    st = win._sd_st
    if st is None:
        return
    S = win._S
    import commands as CMD
    from erp_widgets import BusyDialog
    todo = [oid for oid, o in (getattr(win, "_sd_rows", None) or []) if o.get("draft")]
    if not todo:
        return

    def prep():
        seen, out = set(), []
        for oid in todo:
            if oid in seen:
                continue
            d = CMD.order_mail_draft(S, st, oid, fetch_body=True)
            seen.update(d["ids"])
            out.append(d)
        return out
    drafts, err = BusyDialog.run(win, tr("全部发送"), tr("正在准备邮件…"), prep)
    if err:
        QMessageBox.warning(win, tr("全部发送"), str(err))
        return
    import order_tracking as OT
    go, skip = [], []
    for d in drafts:
        stores = "、".join(OT.STORE_NAMES.get(str(s), str(s)) for s in d.get("stores") or [])
        if not d.get("to"):
            skip.append(tr("{0} · {1}：没有收件人", d["supplier"], stores))
        elif d.get("files") and not all(ok for _p, _n, ok in d["files"]):
            skip.append(tr("{0} · {1}：附件不在这台电脑上", d["supplier"], stores))
        else:
            go.append(d)
    if not go:
        QMessageBox.information(win, tr("全部发送"), "\n".join(skip) or tr("没有能发的"))
        return
    lines = [tr("{0} · {1} → {2}", d["supplier"], "、".join(OT.STORE_NAMES.get(str(s), str(s)) for s in d["stores"]),
                ", ".join(d["to"])) for d in go]
    msg = tr("发 {0} 封订单邮件？发出去撤不回。\n\n{1}", len(go), "\n".join(lines))
    if skip:
        msg += "\n\n" + tr("这些先不发（要单独处理）：\n{0}", "\n".join(skip))
    msg += "\n\n" + tr("发完这些单变成「已下单」（下单日期 = 今天），订单跟踪里大家都看得到。")
    if QMessageBox.question(win, tr("全部发送"), msg) != QMessageBox.Yes:
        return

    def work():
        done, bad = 0, []
        for d in go:
            try:
                CMD.send_order(S, st, d["ids"], d["from"], d["to"], d.get("cc") or [], d["subject"], d["body"],
                               [(p, n) for p, n, _ok in d.get("files") or []])
                done += 1
            except Exception as e:
                bad.append(f"{d['supplier']}: {e}")
        return done, bad
    r, err = BusyDialog.run(win, tr("全部发送"), tr("正在发送订单邮件…"), work)
    if err:
        QMessageBox.warning(win, tr("全部发送"), str(err))
    else:
        done, bad = r
        if bad:
            QMessageBox.warning(win, tr("全部发送"), tr("发了 {0} 封，没发出去：\n{1}", done, "\n".join(bad)))
        win.show_toast(tr("已发送 {0} 封订单邮件", done))
    refresh_send(win)
    if getattr(win, "tracking", None) is not None:
        win.tracking.check_badge()
