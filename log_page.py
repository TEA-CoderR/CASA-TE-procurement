"""
运行日志页（2026-11-16 照设计稿「⑬ 运行日志」重做）：
  左：级别（全部 / 提醒和错误 / 只看错误）· 哪一块（比价 / 总仓采购 / 订单跟踪 / 数据库 / 邮件 …，按日志内容认）·
      现在的状态（数据库 / Excel 同步 / 邮件 / 销售数据 四个小圆点）
  右：页头（运行日志 + 几条 · 提醒 n · 错误 n）+ 复制诊断信息 / 打开日志文件 / ⋯ 清空；搜索；
      一行一条（时间 · 级别小圆点 · 哪一块 · 内容），新的在上面，错误红底
  module_of / matches 不依赖界面，有测试
"""
import html
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QWidget, QLineEdit, QButtonGroup)

from theme import T
from i18n import tr
import result_page as RP

# 哪一块：按日志里的字认（先出现的规则先用）
MODULES = [
    ("SELL OUT", ("SELL OUT", "sellout")),
    ("数据库", ("数据库", "MySQL", "销售数据", "连不上")),
    ("比价", ("比价", "报价", "凑单", "分配", "供应商文件", "主订单", "缺失", "DETERSIVO", "检查清单", "门店清单", "起订")),
    ("总仓采购", ("总仓", "门店采购", "配货", "SCHEDA", "订货量", "调货")),
    ("订单跟踪", ("订单跟踪", "Excel", "PIANO", "待登记", "在途", "同步")),
    ("邮件", ("邮件", "邮箱", "IMAP", "SMTP")),
    ("Griglia", ("Griglia",)),
    ("促销", ("促销", "VOLANTINO", "圣诞")),
]
OTHER = "系统"
LEVELS = [("all", "全部", "muted"), ("warn", "提醒和错误", "warn"), ("err", "只看错误", "err")]
SHOW_MAX = 1500


def module_of(line):
    t = line or ""
    low = t.lower()
    for name, keys in MODULES:
        if any(k.lower() in low for k in keys):
            return name
    return OTHER


def matches(entry, level, module, query):
    """entry = (级别, 文字, 时间)"""
    lvl, text = entry[0], entry[1]
    if level == "warn" and lvl not in ("warn", "err") or level == "err" and lvl != "err":
        return False
    if module and module != "all" and module_of(text) != module:
        return False
    return not query or query.lower() in (text or "").lower()


class NavBtn(QPushButton):
    def __init__(self, text, tone):
        super().__init__()
        self.setObjectName("LogNav")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(34)
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 0, 10, 0)
        h.setSpacing(8)
        if tone:
            h.addWidget(RP.Dot(tone, 8))
        self.lb = QLabel(text)
        h.addWidget(self.lb, 1)
        self.cnt = RP.mono("", 11, 500, "muted")
        h.addWidget(self.cnt)
        for w in self.findChildren(QWidget):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)


QSS = """
QPushButton#LogNav {{ background:transparent; border:none; border-radius:9px; text-align:left; }}
QPushButton#LogNav:hover {{ background:{hover}; }}
QPushButton#LogNav:checked {{ background:{sel}; }}
QFrame#LogSide {{ background:{panel}; border:1px solid {border}; border-radius:14px; }}
QTextEdit#LogRows {{ background:transparent; border:none; }}
"""


def qss():
    return RP.qss() + QSS.format(hover=T("hover"), sel=T("accent_soft"), panel=T("surface"), border=T("line"))


def build(win, lay):
    page = lay.parentWidget()
    win._log_page = page
    win._log_mod = "all"
    row = QHBoxLayout()
    row.setSpacing(16)
    lay.addLayout(row, 1)

    side = QFrame()
    side.setObjectName("LogSide")
    side.setFixedWidth(230)
    sv = QVBoxLayout(side)
    sv.setContentsMargins(12, 16, 12, 16)
    sv.setSpacing(2)

    def sec(text):
        lb = RP.muted(text, 11)
        lb.setContentsMargins(10, 10, 0, 4)
        sv.addWidget(lb)
    sec(tr("级别"))
    win._log_lv_btns = {}
    g1 = QButtonGroup(side)
    for k, text, tone in LEVELS:
        b = NavBtn(tr(text), tone)
        b.setChecked(k == win.log_filter)
        b.clicked.connect(lambda _=False, kk=k: win._set_log_filter(kk))
        g1.addButton(b)
        sv.addWidget(b)
        win._log_lv_btns[k] = b
    sec(tr("哪一块"))
    win._log_mod_btns = {}
    g2 = QButtonGroup(side)
    for name in ["all"] + [m for m, _k in MODULES] + [OTHER]:
        b = NavBtn(tr("全部") if name == "all" else tr(name), None)
        b.setChecked(name == "all")
        b.clicked.connect(lambda _=False, n=name: _set_mod(win, n))
        g2.addButton(b)
        sv.addWidget(b)
        win._log_mod_btns[name] = b
    sec(tr("现在的状态"))
    win._log_health = QVBoxLayout()
    win._log_health.setSpacing(8)
    win._log_health.setContentsMargins(10, 4, 4, 0)
    sv.addLayout(win._log_health)
    sv.addStretch()
    row.addWidget(side)

    mid = QVBoxLayout()
    mid.setSpacing(14)
    row.addLayout(mid, 1)
    head = QHBoxLayout()
    hv = QVBoxLayout()
    hv.setSpacing(6)
    t = QLabel(tr("运行日志"))
    t.setObjectName("HeroTitle")
    hv.addWidget(t)
    win._log_line = QLabel("")
    win._log_line.setObjectName("HeroSub")
    win._log_line.setTextFormat(Qt.RichText)
    hv.addWidget(win._log_line)
    head.addLayout(hv, 1)
    win._log_search = QLineEdit()
    win._log_search.setPlaceholderText(tr("搜索日志"))
    win._log_search.setClearButtonEnabled(True)
    win._log_search.setFixedWidth(200)
    win._log_search.textChanged.connect(lambda _t: win._render_log())
    head.addWidget(win._log_search, 0, Qt.AlignBottom)
    cp = QPushButton(tr("复制诊断信息"))
    cp.setToolTip(tr("把版本、数据库状态和最近的提醒 / 错误复制下来，发给负责的同事"))
    cp.clicked.connect(lambda: _copy_diag(win))
    head.addWidget(cp, 0, Qt.AlignBottom)
    of = QPushButton(tr("打开日志文件"))
    of.clicked.connect(win._open_log_file)
    head.addWidget(of, 0, Qt.AlignBottom)
    from page_kit import more_menu
    more, _a = more_menu([(tr("清空"), win._clear_log, tr("只清空这里显示的，日志文件不动"))])
    head.addWidget(more, 0, Qt.AlignBottom)
    mid.addLayout(head)

    card, cv = RP.card(None, margins=(6, 6, 6, 6))
    win.log_view.setParent(None)
    win.log_view.setObjectName("LogRows")
    win.log_view.setMinimumHeight(0)
    cv.addWidget(win.log_view, 1)
    mid.addWidget(card, 1)
    page.setStyleSheet(qss())


def _set_mod(win, n):
    win._log_mod = n
    win._render_log()


def _row_html(e):
    lvl, text, ts = e[0], e[1], (e[2] if len(e) > 2 else None)
    color = {"err": T("err"), "warn": T("warn"), "ok": T("ok"), "info": T("accent")}.get(lvl, T("muted"))
    txt = {"err": T("err"), "warn": T("warn")}.get(lvl, T("text"))
    bg = f" background:{T('err_soft')};" if lvl == "err" else ""
    mod = module_of(text)
    t = ts.strftime("%H:%M:%S") if ts else ""
    from theme import NUM_FONT
    return (f"<tr><td width='78' style='padding:6px 10px; color:{T('muted')}; font-family:\"{NUM_FONT}\"; font-size:12px;{bg}'>{t}</td>"
            f"<td width='22' style='padding:6px 4px;{bg}'><span style='color:{color}; font-size:14px'>●</span></td>"
            f"<td width='86' style='padding:6px 8px; color:{T('muted')}; font-size:12px; white-space:nowrap;{bg}'>{html.escape(tr(mod))}</td>"
            f"<td style='padding:6px 10px; color:{txt}; font-size:13px;{bg}'>{html.escape(text.strip())}</td></tr>")


def render(win):
    """重画列表（新的在上面，最多 SHOW_MAX 条）+ 左边个数 + 页头一句话"""
    if not hasattr(win, "_log_line"):
        return False
    ents = [e for e in win.log_entries if (e[1] or "").strip() and not set(e[1].strip()) <= {"=", "-"}]
    q = win._log_search.text().strip()
    shown = [e for e in ents if matches(e, win.log_filter, win._log_mod, q)]
    rows = "".join(_row_html(e) for e in reversed(shown[-SHOW_MAX:]))
    win.log_view.setHtml(f"<table width='100%' cellspacing='0' cellpadding='0'>{rows}</table>" if rows else
                         f"<p style='color:{T('muted')}; padding:16px'>" + html.escape(tr("没有符合条件的记录")) + "</p>")
    n_w = sum(1 for e in ents if e[0] == "warn")
    n_e = sum(1 for e in ents if e[0] == "err")
    for k, b in win._log_lv_btns.items():
        b.cnt.setText(str(len(ents) if k == "all" else n_w + n_e if k == "warn" else n_e))
        b.setChecked(k == win.log_filter)
    cnt = {}
    for e in ents:
        if matches(e, win.log_filter, None, ""):
            m = module_of(e[1])
            cnt[m] = cnt.get(m, 0) + 1
    for name, b in win._log_mod_btns.items():
        c = sum(cnt.values()) if name == "all" else cnt.get(name, 0)
        b.cnt.setText(str(c))
        b.setVisible(name == "all" or c > 0 or name == win._log_mod)
    line = tr("本次打开以来 {0} 条", len(ents))
    if n_w:
        line += f" · <span style='color:{T('warn')}'>" + tr("提醒 {0}", n_w) + "</span>"
    if n_e:
        line += f" · <span style='color:{T('err')}'>" + tr("错误 {0}", n_e) + "</span>"
    line += " · " + tr("新的在上面")
    win._log_line.setText(line)
    _health(win)
    return True


def health(win):
    """[(名字, 一句话, 颜色)]"""
    out = []
    ok = getattr(win, "_db_ok", None)
    out.append((tr("数据库"), tr("已连接") if ok else tr("正在检查") if ok is None else tr("连不上"),
                "ok" if ok else "warn" if ok is None else "err"))
    try:
        import reorder_service as R
        upto = getattr(win, "_db_sales_upto", None)
        late = R.stale_days(upto) if ok else None
        if upto is not None and ok:
            out.append((tr("销售数据"), tr("到 {0}", upto.strftime("%d/%m")),
                        "warn" if late is not None and late > R.STALE_DAYS else "ok"))
    except Exception:
        pass
    try:
        tp = getattr(win, "tracking_page", None) or getattr(win, "tracking", None)
        ts = (tp.cfg.get("synced_ts") if tp is not None and hasattr(tp, "cfg") else None)
        if ts:
            mins = int((datetime.now() - datetime.fromisoformat(ts)).total_seconds() // 60)
            out.append((tr("Excel 同步"), tr("{0} 分钟前", mins) if mins < 120 else tr("{0} 小时前", mins // 60),
                        "ok" if mins < 60 else "warn"))
    except Exception:
        pass
    return out


def _health(win):
    RP._clear(win._log_health)
    for name, sub, tone in health(win):
        h = QHBoxLayout()
        h.setSpacing(10)
        h.addWidget(RP.Dot(tone, 10), 0, Qt.AlignTop)
        v = QVBoxLayout()
        v.setSpacing(0)
        n = QLabel(name)
        n.setStyleSheet("font-weight:500; font-size:12px;")
        v.addWidget(n)
        s = RP.muted(sub, 11)
        if tone in ("err", "warn"):
            RP.set_tone(s, tone)
        v.addWidget(s)
        h.addLayout(v, 1)
        w = QWidget()
        w.setLayout(h)
        win._log_health.addWidget(w)


def _copy_diag(win):
    from PySide6.QtWidgets import QApplication
    import theme
    lines = [f"CASA&TE {getattr(theme, 'APP_VERSION', '')} · {datetime.now():%d/%m/%Y %H:%M}"]
    lines += [f"{n}: {s}" for n, s, _t in health(win)]
    bad = [e for e in win.log_entries if e[0] in ("warn", "err")][-30:]
    lines += [""] + [(f"{e[2]:%H:%M:%S} " if len(e) > 2 and e[2] else "") + e[1].strip() for e in bad]
    QApplication.clipboard().setText("\n".join(lines))
    try:
        win.show_toast(tr("已复制诊断信息"))
    except Exception:
        pass
