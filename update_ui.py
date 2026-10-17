"""
自动更新的界面部分：启动后静默检查、「发现新版本」对话框、下载安装进度窗口
"""
import os
import shutil
import threading

from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit, QMessageBox,
                               QApplication, QFrame)

from i18n import tr
from settings import save_settings
from theme import APP_VERSION
from erp_widgets import TaskDialog
import app_update as U


class _Sig(QObject):
    checked = Signal(object)        # (rel, err, silent)
    installed = Signal(object)      # (ok, msg)


class NewVersionDialog(QDialog):
    def __init__(self, parent, rel):
        super().__init__(parent)
        self.setObjectName("Dialog")
        self.setWindowTitle(tr("发现新版本"))
        self.setMinimumWidth(480)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 16)
        v.setSpacing(10)
        t = QLabel(tr("发现新版本 {0}", rel["version"]))
        t.setStyleSheet("font-size:17px; font-weight:700;")
        s = QLabel(tr("现在用的是 {0}。更新只替换程序文件，设置和历史记录都会保留。", APP_VERSION))
        s.setObjectName("Faint")
        s.setWordWrap(True)
        v.addWidget(t)
        v.addWidget(s)
        lab = QLabel(tr("更新内容"))
        lab.setStyleSheet("font-weight:600;")
        v.addWidget(lab)
        notes = QTextEdit()
        notes.setReadOnly(True)
        notes.setPlainText(rel.get("notes") or tr("改进和修复"))
        notes.setMaximumHeight(180)
        v.addWidget(notes)
        foot = QFrame()
        foot.setObjectName("DialogFoot")
        h = QHBoxLayout(foot)
        h.setContentsMargins(0, 8, 0, 0)
        self.skip = QPushButton(tr("跳过这个版本"))
        self.skip.setObjectName("Link")
        h.addWidget(self.skip)
        h.addStretch()
        later = QPushButton(tr("稍后"))
        later.clicked.connect(self.reject)
        now = QPushButton(tr("现在更新"))
        now.setObjectName("Primary")
        now.clicked.connect(self.accept)
        now.setDefault(True)
        h.addWidget(later)
        h.addWidget(now)
        v.addWidget(foot)
        self.skipped = False
        self.skip.clicked.connect(self._skip)

    def _skip(self):
        self.skipped = True
        self.reject()


class Updater:
    def __init__(self, win, settings):
        self.win, self.settings = win, settings
        self.sig = _Sig()
        self.sig.checked.connect(self._checked)
        self.sig.installed.connect(self._installed)
        self.busy = False
        self.latest = None

    def config(self):
        return U.load_config(self.settings)

    def auto_enabled(self):
        return self.settings.get("update_auto", True)

    def start_auto(self):
        """启动后 8 秒静默检查一次，之后每 4 小时一次"""
        if not self.auto_enabled() or not U.is_frozen() or not self.config():
            return
        QTimer.singleShot(8000, lambda: self.check(silent=True))
        self.timer = QTimer(self.win, interval=4 * 3600 * 1000, timeout=lambda: self.check(silent=True))
        self.timer.start()

    # -----------------------------------------------------------------
    def check(self, silent=False):
        if self.busy:
            return
        cfg = self.config()
        if not cfg:
            if not silent:
                QMessageBox.information(self.win, tr("检查更新"),
                                        tr("还没有设置更新源。由发布人运行 publish_update 发布一次后，程序里会自带更新源。"))
            return
        self.busy = True

        def work():
            try:
                rel = U.latest_release(cfg)
                self.sig.checked.emit((rel, None, silent))
            except Exception as e:
                self.sig.checked.emit((None, str(e), silent))

        threading.Thread(target=work, daemon=True).start()

    def _checked(self, payload):
        rel, err, silent = payload
        self.busy = False
        if err:
            if not silent:
                QMessageBox.warning(self.win, tr("检查更新"), tr("检查更新失败：{0}", err))
            return
        self.latest = rel
        if hasattr(self.win, "settings_page"):
            self.win.settings_page.refresh_update()
        if not U.is_newer(rel["version"], APP_VERSION):
            if not silent:
                QMessageBox.information(self.win, tr("检查更新"), tr("已经是最新版本（{0}）。", APP_VERSION))
            return
        if silent and self.settings.get("update_skip") == rel["version"]:
            return
        self.win.notify(tr("发现新版本 {0}", rel["version"]))
        dlg = NewVersionDialog(self.win, rel)
        ok = dlg.exec()
        if dlg.skipped:
            self.settings["update_skip"] = rel["version"]
            save_settings(self.settings)
            return
        if ok:
            self.install(rel)

    # -----------------------------------------------------------------
    def install(self, rel):
        if not U.is_frozen():
            QMessageBox.information(self.win, tr("更新"), tr("现在是用源代码（main.py）运行的，不能自动更新；打包后的程序才可以。"))
            return
        if getattr(self.win, "running", False) or getattr(getattr(self.win, "store_orders", None), "running", False):
            QMessageBox.information(self.win, tr("更新"), tr("正在处理，等处理完成后再更新。"))
            return
        cfg = self.config()
        self.busy = True
        dlg = TaskDialog(self.win, tr("正在更新到 {0}", rel["version"]), tr("设置和历史记录会保留"),
                         [tr("对比本机文件"), tr("下载更新包"), tr("校验文件"), tr("安装并重新打开")], cancellable=True)
        dlg.show()

        def work():
            stage_root = os.path.join(U.work_dir(), "stage")
            try:
                dlg.post_step(0, "running", "", "")
                man = U.fetch_manifest(cfg, rel)
                changed = U.changed_files(man, progress=lambda i, n: dlg.post_progress(2 + 13 * i / max(1, n)))
                dlg.post_step(0, "done", tr("{0} 个文件要换", len(changed)), "")
                if not changed:
                    dlg.post_finished(True, tr("程序文件已经是最新的"))
                    self.sig.installed.emit((False, "nothing"))
                    return
                asset = U.choose_package(man, changed, rel)
                zpath = os.path.join(U.work_dir(), asset["name"])
                size = asset.get("size") or 0
                dlg.post_step(1, "running", f"{size / 1e6:.1f} MB", asset["name"])
                U.download_asset(cfg, asset, zpath,
                                 progress=lambda d, t: (dlg.post_progress(15 + 70 * d / max(1, t)),
                                                        dlg.post_step(1, "running", f"{d / 1e6:.1f} / {t / 1e6:.1f} MB", "")),
                                 cancel=dlg.cancel_event.is_set)
                dlg.post_step(1, "done", f"{size / 1e6:.1f} MB", "")
                dlg.post_step(2, "running", "", "")
                if os.path.isdir(stage_root):
                    shutil.rmtree(stage_root, ignore_errors=True)
                staging = os.path.join(stage_root, "files")
                U.extract_needed(zpath, man, changed, staging)
                os.remove(zpath)
                dlg.post_step(2, "done", tr("{0} 个文件", len(changed)), "")
                dlg.post_step(3, "running", "", "")
                dlg.post_progress(95)
                U.launch_installer(staging)
                dlg.post_finished(True, tr("马上关闭程序并安装，几秒后自动重新打开"))
                self.sig.installed.emit((True, rel["version"]))
            except U.Cancelled:
                dlg.post_finished(False, tr("已取消"))
                self.sig.installed.emit((False, "cancel"))
            except Exception as e:
                dlg.post_finished(False, tr("更新失败：{0}", e))
                self.sig.installed.emit((False, str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _installed(self, payload):
        ok, msg = payload
        self.busy = False
        if ok:
            self.win.log(tr("开始安装更新 {0}，程序将重新打开", msg))
            QTimer.singleShot(1500, QApplication.instance().quit)
