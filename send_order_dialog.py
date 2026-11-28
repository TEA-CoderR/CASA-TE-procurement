"""
发送订单邮件的窗口（2026-11-07 用户：点「待登记」→ 邮件模板 → 审核 → 发送 → 订单自动变已下单）。
业务在 commands.order_mail_draft / send_order（mail_send.py），这里只是界面：
  上：供应商 · 这封信一起下几张单（门店 · 金额）
  中：发件 · 收件 · 抄送 · 主题（短标签 + 输入框，说明在 ⓘ）· 正文 · 附件
  下：状态 · 取消 · 发送（先确认，发出去撤不回）
"""
from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QLineEdit, QComboBox,
                               QPlainTextEdit, QCheckBox, QMessageBox, QWidget)

from i18n import tr
from theme import T


class _BodySig(QObject):
    done = Signal(object)


def open_send(parent, st, settings, oid, on_sent=None):
    """草稿不连网（信头 / 读过的正文都在本机），窗口马上打开；上次那封信的正文还没读过的，窗口打开后在后台读"""
    import commands as CMD
    from erp_widgets import BusyDialog
    d, err = BusyDialog.run(parent, tr("发送订单"), tr("正在准备邮件…"),
                            lambda: CMD.order_mail_draft(settings, st, oid, fetch_body=False))
    if err:
        QMessageBox.warning(parent, tr("发送订单"), str(err))
        return None
    dlg = SendOrderDialog(parent, st, settings, d, on_sent=on_sent)
    dlg.exec()
    return dlg.result_info


def buyer_box(st, settings):
    """「下单人」下拉：PIANO 里写过的人（用得多的在前），能输入；默认这台电脑上次选的"""
    import order_tracking as OT
    box = QComboBox()
    box.setEditable(True)
    names = OT.buyers(st) if st is not None else []
    last = str(((settings or {}).get("tracking") or {}).get("last_buyer") or "").strip()
    if not OT.is_buyer_name(last):
        last = ""                                    # 以前存的「采购部」这种不是人名的不用
    if last and last not in names:
        names.insert(0, last)
    box.addItems(names)
    box.setCurrentText(last or (names[0] if names else ""))
    return box


def checked_buyer(st, text):
    """
    下单人照同事在 PIANO 的写法（pamela → Pamela）；不是人名的（「采购部」、空）→ None
    （2026-11-28 用户：PIANO 表里不要用「采购部」，学同事的写法）
    """
    import order_tracking as OT
    t = str(text or "").strip()
    if not t:
        return None
    if st is not None:
        for n in OT.buyers(st, days=365):
            if OT._norm_buyer(n) == OT._norm_buyer(t):
                return n
    return t if OT.is_buyer_name(t) else None


BUYER_HINT = "下单人要写人名，和同事在 PIANO「Ordine Da」里写的一样（例如 LUCAS、Pamela），不要写「采购部」"


def remember_buyer(settings, buyer):
    """记住这台电脑上次选的下单人（个人设置，不随发布同步）"""
    if not buyer or settings is None:
        return
    settings.setdefault("tracking", {})["last_buyer"] = buyer
    try:
        import settings as ST
        ST.save_settings(settings)
    except Exception:
        pass


def pick_buyer(parent, st, settings, title):
    """一次发好几封（DETERSIVO 全部发送）之前问一次下单人；取消 → None"""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    v = QVBoxLayout(dlg)
    v.setContentsMargins(20, 16, 20, 14)
    row = QHBoxLayout()
    from erp_widgets import tip_label
    row.addWidget(tip_label(tr("下单人"), tr("发完这些单自动写进 PIANO ACQUISTO MESILE，这一格就是「Ordine Da」（谁下的单）")))
    box = buyer_box(st, settings)
    box.setMinimumWidth(220)
    row.addWidget(box, 1)
    v.addLayout(row)
    foot = QHBoxLayout()
    foot.addStretch()
    cancel = QPushButton(tr("取消"))
    cancel.clicked.connect(dlg.reject)
    ok = QPushButton(tr("下一步"))
    ok.setObjectName("Primary")
    ok.setDefault(True)
    hint = QLabel("")
    hint.setObjectName("Muted")
    v.insertWidget(1, hint)

    def _ok():
        if checked_buyer(st, box.currentText()):
            dlg.accept()
        else:
            hint.setText(tr(BUYER_HINT))
    ok.clicked.connect(_ok)
    foot.addWidget(cancel)
    foot.addWidget(ok)
    v.addLayout(foot)
    if dlg.exec() != QDialog.Accepted:
        return None
    b = checked_buyer(st, box.currentText())
    remember_buyer(settings, b)
    return b


class SendOrderDialog(QDialog):
    def __init__(self, parent, st, settings, d, on_sent=None):
        super().__init__(parent)
        import mail_send as MS
        self.st, self.settings, self.d, self.on_sent = st, settings, d, on_sent
        self.result_info = None
        self.setWindowTitle(tr("发送订单"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(12)
        # 头：供应商 + 一起下的单
        title = QLabel(tr("发送订单 · {0}", d["supplier"]))
        title.setStyleSheet("font-size:15px; font-weight:700;")
        lay.addWidget(title)
        names = [MS.STORE_LABEL.get(s, s) for s in d["stores"]]
        sub = QLabel(tr("{0} 张单：{1} · €{2:,.2f}（不含税）· 发完自动变「已下单」", len(d["ids"]), " · ".join(names), d["amount"]))
        sub.setObjectName("Faint")
        lay.addWidget(sub)
        if d["sent_before"]:
            warn = QLabel(tr("⚠ 这几张单已经发过邮件了，再发一次请先确认对方没收到"))
            warn.setStyleSheet(f"color:{T('err')}; font-weight:600;")
            lay.addWidget(warn)
        # 表单
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(8)

        def lab(text, tip, r):
            from erp_widgets import tip_label
            w = tip_label(text, tip) if tip else QLabel(text)
            g.addWidget(w, r, 0, Qt.AlignRight | Qt.AlignVCenter)
        lab(tr("发件"), tr("一家店的单从这家店的订单发件邮箱发，BMS / 几家店一起的（总仓采购一个文件）从 BMS 的邮箱发"
                         "（系统设置 → 邮箱 → 订单发件邮箱）"), 0)
        self.e_from = QComboBox()
        for a in d["accounts"]:
            self.e_from.addItem(a["address"], a["address"])
        self.e_from.setCurrentIndex(max(0, self.e_from.findData(d["from"])))
        if d.get("from_locked"):                 # 固定邮箱：不让选别的，免得用错门店的邮箱发
            self.e_from.setEnabled(False)
        g.addWidget(self.e_from, 0, 1)
        lab(tr("收件"), tr("以前发给这家的地址（共享通讯录 / 「已发送」里学的）。改了的话，发完存进通讯录，下次照用。"
                         "几个地址用逗号隔开"), 1)
        self.e_to = QLineEdit(", ".join(d["to"]))
        self.e_to.setPlaceholderText(tr("第一次给这家发：填供应商的邮箱"))
        g.addWidget(self.e_to, 1, 1)
        lab(tr("抄送"), tr("公司里平时一起收订单信的同事"), 2)
        self.e_cc = QLineEdit(", ".join(d["cc"]))
        g.addWidget(self.e_cc, 2, 1)
        lab(tr("主题"), "", 3)
        self.e_subj = QLineEdit(d["subject"])
        g.addWidget(self.e_subj, 3, 1)
        lab(tr("下单人"), tr("发完这张单自动写进 PIANO ACQUISTO MESILE，这一格就是「Ordine Da」（谁下的单）"), 4)
        self.e_buyer = buyer_box(st, settings)
        g.addWidget(self.e_buyer, 4, 1)
        g.setColumnStretch(1, 1)
        lay.addLayout(g)
        nos = " · ".join(dict.fromkeys((d.get("nos") or {}).values()))
        src = {"last": tr("正文照上次发给这家的那封信（看一下日期、门店对不对）"),
               "template": tr("正文按订单数据自动写好（订单号 {0}、送货地址、箱数、预约卸货）。供应商回信、送货单、"
                              "预约时都会写这个订单号，程序靠它对到这张单", nos)}[d["body_src"]]
        self.hint = hint = QLabel(src)
        hint.setObjectName("Faint")
        lay.addWidget(hint)
        self.e_body = QPlainTextEdit(d["body"])
        self.e_body.setMinimumHeight(180)
        lay.addWidget(self.e_body, 1)
        if d.get("need_last"):
            self._load_last()
        # 附件
        self.att = []
        att_box = QWidget()
        av = QVBoxLayout(att_box)
        av.setContentsMargins(0, 0, 0, 0)
        av.setSpacing(4)
        if not d["files"]:
            n = QLabel(tr("⚠ 这张单没有订单文件，信里不会带附件"))
            n.setStyleSheet(f"color:{T('warn')};")
            av.addWidget(n)
        for path, name, ok in d["files"]:
            row = QHBoxLayout()
            cb = QCheckBox("📎 " + name)
            cb.setChecked(ok)
            cb.setEnabled(ok)
            cb.setToolTip(path if ok else tr("这台电脑上找不到订单文件（在生成它的那台电脑上发）：\n{0}", path))
            row.addWidget(cb)
            if ok:
                b = QPushButton(tr("打开"))
                b.setObjectName("Link")
                b.clicked.connect(lambda _=False, p=path: self._open(p))
                row.addWidget(b)
            else:
                miss = QLabel(tr("这台电脑上找不到"))
                miss.setStyleSheet(f"color:{T('err')};")
                row.addWidget(miss)
            row.addStretch()
            av.addLayout(row)
            self.att.append((cb, path, name))
        lay.addWidget(att_box)
        # 底部
        foot = QHBoxLayout()
        self.status = QLabel("")
        self.status.setStyleSheet(f"color:{T('err')};")
        foot.addWidget(self.status, 1)
        cancel = QPushButton(tr("取消"))
        cancel.clicked.connect(self.reject)
        self.b_send = QPushButton(tr("✉ 发送"))
        self.b_send.setObjectName("Primary")
        self.b_send.setDefault(True)
        self.b_send.clicked.connect(self._send)
        foot.addWidget(cancel)
        foot.addWidget(self.b_send)
        if d["files"] and not any(ok for _p, _n, ok in d["files"]):
            # 订单文件只在生成它的电脑上：不带附件的订单信没有用，不让发
            self.b_send.setEnabled(False)
            self.status.setText(tr("请在生成订单文件的那台电脑上发"))
        lay.addLayout(foot)
        self.resize(760, 640)
        from erp_widgets import fit_to_screen
        fit_to_screen(self)

    def _load_last(self):
        """后台读上次发给这家的订单信正文（只读），读到了、正文还没人改过就换上"""
        import threading
        import commands as CMD
        self.hint.setText(tr("正在读上次发给这家的信…（先用默认模板，读到了自动换上）"))
        self._tpl = self.e_body.toPlainText()
        self._sig = _BodySig()
        self._sig.done.connect(self._last_arrived)
        sig, settings, d, subj = self._sig, self.settings, self.d, self.e_subj.text().strip()

        def work():
            try:
                res = (CMD.order_mail_last_body(settings, d, subject=subj), None)
            except Exception as e:
                res = (None, e)
            try:
                sig.done.emit(res)
            except RuntimeError:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _last_arrived(self, res):
        text, err = res
        if not text:
            self.hint.setText(tr("正文用的是默认模板（以前没给这家发过，或者读不到上次的信）"))
            return
        if self.e_body.toPlainText() != self._tpl:          # 已经开始改了：不覆盖
            self.hint.setText(tr("上次那封信读到了，正文你已经改过，没有替换"))
            return
        self.e_body.setPlainText(text)
        self.hint.setText(tr("正文照上次发给这家的那封信（看一下日期、门店对不对）"))

    def _open(self, p):
        from erp_widgets import open_file
        open_file(self, p)

    def _send(self):
        import mail_send as MS
        import commands as CMD
        from erp_widgets import BusyDialog
        to, cc = MS.split_addrs(self.e_to.text()), MS.split_addrs(self.e_cc.text())
        bad = [a for a in to + cc if not MS.valid_address(a)]
        if not to:
            self.status.setText(tr("请填收件人"))
            return
        if bad:
            self.status.setText(tr("这几个地址不对：{0}", ", ".join(bad)))
            return
        buyer = checked_buyer(self.st, self.e_buyer.currentText())
        if not buyer:
            self.status.setText(tr(BUYER_HINT))
            return
        files = [(p, n) for cb, p, n in self.att if cb.isChecked()]
        frm = self.e_from.currentData()
        names = ", ".join(n for _p, n in files) or tr("（没有附件）")
        msg = tr("用 {0} 发给：\n{1}\n抄送：{2}\n附件：{3}\n\n发完 {4} 张单变「已下单」（下单日期 = 今天），收件人存进共享通讯录。\n"
                 "邮件发出去撤不回，确定发送？", frm, ", ".join(to), ", ".join(cc) or "—", names, len(self.d["ids"]))
        msg = msg.replace("\n\n", "\n" + tr("下单人：{0}（发完自动写进 PIANO）", buyer) + "\n\n", 1)
        if QMessageBox.question(self, tr("发送订单"), msg) != QMessageBox.Yes:
            return
        force = bool(self.d["sent_before"])
        res, err = BusyDialog.run(self, tr("发送订单"), tr("正在发送…"), lambda: CMD.send_order(
            self.settings, self.st, self.d["ids"], frm, to, cc, self.e_subj.text().strip(),
            self.e_body.toPlainText(), files, force=force, buyer=buyer))
        if err:
            self.status.setText(tr("没发出去：{0}", err))
            return
        remember_buyer(self.settings, buyer)
        self.result_info = dict(res, to=to, ids=self.d["ids"])
        if self.on_sent:
            self.on_sent(self.result_info)
        self.accept()
