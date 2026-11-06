"""
邮箱设置窗口 + 邮件摸底（第 1 阶段，2026-11-06）。业务都在 commands.mail_* / mail_client，这里只有界面。
"""
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit, QPushButton,
                               QListWidget, QListWidgetItem, QMessageBox, QSpinBox)

from i18n import tr
from settings import save_settings
from erp_widgets import tip_label, BusyDialog, fit_to_screen
import commands as CMD
import mail_client as MC


def survey_dir():
    """摸底结果放在自己的「文档」里（里面有供应商、门店的往来，不放共享文件夹）"""
    home = os.path.expanduser("~")
    docs = os.path.join(home, "Documents")
    return os.path.join(docs if os.path.isdir(docs) else home, "CASATE 邮件摸底")


class MailAccountsDialog(QDialog):
    """左边邮箱列表，右边地址 / 安全密码 / 服务器；按钮：新邮箱 · 保存 · 测试连接 · 删除"""

    def __init__(self, win, settings):
        super().__init__(win)
        self.win, self.settings = win, settings
        self.setObjectName("Dialog")
        self.setWindowTitle(tr("邮箱"))
        self.resize(680, 360)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(12)
        body = QHBoxLayout()
        body.setSpacing(16)
        self.lst = QListWidget()
        self.lst.setFixedWidth(250)
        self.lst.currentItemChanged.connect(self._picked)
        body.addWidget(self.lst)
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(10)
        g.addWidget(QLabel(tr("地址")), 0, 0)
        self.addr = QLineEdit()
        self.addr.setPlaceholderText("ordine.foiano@casa-te.com")
        g.addWidget(self.addr, 0, 1, 1, 3)
        g.addWidget(tip_label(tr("安全密码"), tr("阿里邮箱网页版：设置 → 账户与安全 → 第三方客户端安全密码。"
                                                "不是登录密码；只保存在这台电脑（Windows 加密），不进共享文件夹，同事看不到。")), 1, 0)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        g.addWidget(self.pw, 1, 1, 1, 3)
        g.addWidget(tip_label(tr("服务器"), tr("阿里云企业邮箱：imap.qiye.aliyun.com（老地址 imap.mxhichina.com 也行），端口 993")), 2, 0)
        self.host = QLineEdit(MC.DEFAULT_HOST)
        g.addWidget(self.host, 2, 1)
        g.addWidget(QLabel(tr("端口")), 2, 2)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(MC.DEFAULT_PORT)
        g.addWidget(self.port, 2, 3)
        btns = QHBoxLayout()
        self.b_new = QPushButton(tr("新邮箱"))
        self.b_new.clicked.connect(self._new)
        self.b_save = QPushButton(tr("保存"))
        self.b_save.setObjectName("Primary")
        self.b_save.clicked.connect(self._save)
        self.b_test = QPushButton(tr("测试连接"))
        self.b_test.setToolTip(tr("只登录、数一下收件箱有几封信，不读信、不改任何东西"))
        self.b_test.clicked.connect(self._test)
        self.b_del = QPushButton(tr("删除"))
        self.b_del.clicked.connect(self._delete)
        for b in (self.b_new, self.b_save, self.b_test, self.b_del):
            btns.addWidget(b)
        btns.addStretch()
        g.addLayout(btns, 3, 0, 1, 4)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setObjectName("Faint")
        g.addWidget(self.msg, 4, 0, 1, 4)
        g.setRowStretch(5, 1)
        g.setColumnStretch(1, 1)
        body.addLayout(g, 1)
        v.addLayout(body, 1)
        foot = QHBoxLayout()
        foot.addStretch()
        close = QPushButton(tr("关闭"))
        close.clicked.connect(self.accept)
        foot.addWidget(close)
        v.addLayout(foot)
        fit_to_screen(self)
        self._fill()

    def _fill(self, select=None):
        self.lst.blockSignals(True)
        self.lst.clear()
        for a in MC.accounts(self.settings):
            has = bool(CMD.mail_password(a["address"]))
            it = QListWidgetItem(("✓ " if has else "⚠ ") + a["address"])
            it.setData(Qt.UserRole, a["address"])
            it.setToolTip(tr("安全密码已保存在这台电脑") if has else tr("还没填安全密码"))
            self.lst.addItem(it)
        self.lst.blockSignals(False)
        rows = [self.lst.item(i).data(Qt.UserRole) for i in range(self.lst.count())]
        if select in rows:
            self.lst.setCurrentRow(rows.index(select))
        elif rows:
            self.lst.setCurrentRow(0)
        else:
            self._new()

    def _current(self):
        it = self.lst.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _picked(self, *_):
        a = next((x for x in MC.accounts(self.settings) if x["address"] == self._current()), None)
        if not a:
            return
        self.addr.setText(a["address"])
        self.host.setText(a["host"])
        self.port.setValue(a["port"])
        self.pw.clear()
        has = bool(CMD.mail_password(a["address"]))
        self.pw.setPlaceholderText(tr("已保存（要改才填）") if has else tr("填网页版生成的安全密码"))
        self.msg.setText("")

    def _new(self):
        self.lst.clearSelection()
        self.lst.setCurrentItem(None)
        for w in (self.addr, self.pw):
            w.clear()
        self.host.setText(MC.DEFAULT_HOST)
        self.port.setValue(MC.DEFAULT_PORT)
        self.pw.setPlaceholderText(tr("填网页版生成的安全密码"))
        self.msg.setText("")
        self.addr.setFocus()

    def _save(self):
        addr = self.addr.text().strip()
        try:
            old = self._current()
            if old and old.lower() != addr.lower():          # 改了地址：旧的连密码一起删掉
                pw = self.pw.text() or CMD.mail_password(old)
                CMD.mail_remove_account(self.settings, old)
            else:
                pw = self.pw.text() or None
            CMD.mail_save_account(self.settings, addr, pw, self.host.text(), self.port.value())
        except ValueError as e:
            QMessageBox.warning(self, tr("邮箱"), str(e))
            return False
        save_settings(self.settings)
        self._fill(select=addr)
        self.msg.setText(tr("已保存"))
        return True

    def _test(self):
        if self.pw.text() or not self._current() or self._current().lower() != self.addr.text().strip().lower():
            if not self._save():
                return
        addr = self._current()
        r, e = BusyDialog.run(self, tr("测试连接"), tr("正在连接 {0}…", addr), lambda: CMD.mail_test(self.settings, addr))
        self.msg.setText(str(e) if e else r)
        from theme import T
        self.msg.setStyleSheet("" if not e else f"color:{T('err')};")

    def _delete(self):
        addr = self._current()
        if not addr:
            return
        if QMessageBox.question(self, tr("删除邮箱"),
                                tr("从程序里删掉 {0}（连同这台电脑保存的安全密码）？\n邮箱本身和里面的信不受影响。", addr)) \
                != QMessageBox.Yes:
            return
        CMD.mail_remove_account(self.settings, addr)
        save_settings(self.settings)
        self._fill()


def run_survey(win, settings):
    """邮件摸底：进度窗口（可后台运行），做完问要不要打开结果"""
    from erp_widgets import TaskDialog, ask_return, open_file
    import threading
    accs = MC.accounts(settings)
    if not accs:
        QMessageBox.information(win, tr("邮件摸底"), tr("先在「邮箱」里加至少一个邮箱。"))
        return
    missing = [a["address"] for a in accs if not CMD.mail_password(a["address"])]
    if missing:
        QMessageBox.information(win, tr("邮件摸底"), tr("这些邮箱还没填安全密码，会跳过：\n{0}", "\n".join(missing)))
    days = int((settings.get("mail") or {}).get("survey_days") or 28)
    dlg = TaskDialog(win, tr("邮件摸底"), tr("{0} 个邮箱 · 最近 {1} 天 · 只读信头", len(accs), days),
                     [a["address"] for a in accs], cancellable=True)
    dlg.went_background.connect(lambda: win.show_toast(tr("邮件摸底在后台进行，完成后会提醒")))
    dlg.show()
    from PySide6.QtCore import QObject, Signal

    class _Sig(QObject):
        done = Signal(object)
    sig = _Sig(dlg)
    log = getattr(win, "log", print)

    def work():
        try:
            path, n, errors = CMD.mail_survey(settings, survey_dir(), days=days, log=log, progress=dlg,
                                              cancel=dlg.cancel_event.is_set)
            dlg.post_finished(True, tr("完成：{0} 封信", n) + (tr("，{0} 个邮箱没连上", len(errors)) if errors else ""))
            sig.done.emit((path, n, errors, None))
        except Exception as e:
            dlg.post_finished(False, str(e))
            sig.done.emit((None, 0, [], e))

    def done(res):
        path, n, errors, err = res
        if err:
            QMessageBox.warning(win, tr("邮件摸底"), str(err))
            return
        text = tr("邮件摸底完成：{0} 封信", n) + ("\n" + "\n".join(errors) if errors else "")
        ask_return(win, dlg, text, lambda: QTimer.singleShot(0, lambda: open_file(win, path, tr("邮件摸底"))))
    sig.done.connect(done)
    threading.Thread(target=work, daemon=True).start()
