"""
系统设置：数据库、订货算法、文件位置、门店与仓库、外观与用户、关于
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QFrame,
                               QLineEdit, QFileDialog, QComboBox, QCheckBox)

from i18n import tr
from settings import save_settings, SETTINGS_PATH
from theme import APP_VERSION
from erp_widgets import chip, set_chip
import store_orders as S


def _card(title, sub=""):
    f = QFrame()
    f.setObjectName("Panel")
    v = QVBoxLayout(f)
    v.setContentsMargins(18, 14, 18, 16)
    v.setSpacing(10)
    t = QLabel(title)
    t.setObjectName("Section")
    v.addWidget(t)
    if sub:
        s = QLabel(sub)
        s.setObjectName("Faint")
        s.setWordWrap(True)
        v.addWidget(s)
    return f, v


class SettingsPage:
    def __init__(self, win, settings):
        self.win, self.settings = win, settings

    def build(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(14)
        t = QLabel(tr("系统设置"))
        t.setObjectName("PageTitle")
        lay.addWidget(t)
        grid = QGridLayout()
        grid.setSpacing(14)

        # 数据库
        c, v = _card(tr("数据库连接"), tr("销量、库存、商品资料都从这里读（ysoft 数据库）"))
        self.db_info = QLabel("")
        self.db_info.setFont(self.db_info.font())
        v.addWidget(self.db_info)
        self.db_chip = chip(tr("未检查"), "gray")
        row = QHBoxLayout()
        row.addWidget(self.db_chip)
        self.acc_chip = chip("", "ok")
        self.acc_chip.hide()
        row.addWidget(self.acc_chip)
        row.addStretch()
        b = QPushButton(tr("修改连接…"))
        b.clicked.connect(self.edit_db)
        b2 = QPushButton(tr("测试连接"))
        b2.clicked.connect(self.win.check_db)
        row.addWidget(b)
        row.addWidget(b2)
        v.addLayout(row)
        grid.addWidget(c, 0, 0)

        # 算法
        c, v = _card(tr("订货算法"), tr("门店配货和批发商比价 · 检查清单共用这套设置"))
        self.algo_info = QLabel("")
        self.algo_info.setWordWrap(True)
        v.addWidget(self.algo_info)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("算法")))
        self.engine = QComboBox()
        from reorder_page import ENGINES
        for k, label in ENGINES:
            self.engine.addItem(tr(label), k)
        self.engine.currentIndexChanged.connect(self._engine_changed)
        row.addWidget(self.engine)
        row.addStretch()
        b = QPushButton(tr("算法设置…"))
        b.clicked.connect(self.edit_algo)
        row.addWidget(b)
        v.addLayout(row)
        grid.addWidget(c, 0, 1)

        # 文件位置
        c, v = _card(tr("文件位置"))
        so = self.settings.setdefault("store_orders", {})
        self.paths = {}
        for key, label, getter, setter, is_file in (
                ("so_folder", tr("采购配单文件夹（门店配货）"), lambda: so.get("folder", ""),
                 lambda p: so.__setitem__("folder", p), False),
                ("cmp_folder", tr("比价总文件夹（批发商比价）"), lambda: self.settings.get("last_folder", ""),
                 lambda p: self.settings.__setitem__("last_folder", p), False),
                ("legacy", tr("旧程序 SistemaIntegrato（第二期之前用）"), lambda: self.settings.get("legacy_exe", ""),
                 lambda p: self.settings.__setitem__("legacy_exe", p), True)):
            l = QLabel(label)
            l.setObjectName("Faint")
            v.addWidget(l)
            r = QHBoxLayout()
            e = QLineEdit(getter())
            e.setReadOnly(True)
            pb = QPushButton(tr("选择…"))
            pb.clicked.connect(lambda _=False, e=e, setter=setter, is_file=is_file, label=label: self._pick(e, setter, is_file, label))
            r.addWidget(e, 1)
            r.addWidget(pb)
            v.addLayout(r)
            self.paths[key] = (e, getter)
        grid.addWidget(c, 1, 0)

        # 门店与仓库
        c, v = _card(tr("门店与仓库"), tr("配货时用：远店不借货；BMS 不够起订量时，先把同仓门店整单并入，再向同城门店借不紧急的货"))
        fill = dict(S.DEFAULT_FILL, **(so.get("fill") or {}))
        far = set(fill.get("far_stores") or [])
        colo = fill.get("colocated_store")
        self.store_chips = []
        for sid, name in S.STORE_SHEETS.items():
            r = QHBoxLayout()
            n = QLabel(f"{sid} · {name}")
            r.addWidget(n, 1)
            if sid in far:
                tag, kind = tr("远店 · 不借货"), "warn"
            elif sid == colo:
                tag, kind = tr("和 BMS 同一个仓库"), "ok"
            else:
                tag, kind = tr("同城"), "accent"
            ch = chip(tag, kind)
            self.store_chips.append((ch, tag, kind))
            r.addWidget(ch)
            v.addLayout(r)
        r = QHBoxLayout()
        r.addWidget(QLabel(tr("0 · BMS 总仓")), 1)
        ch = chip(tr("独立公司 · 合并订货后分货"), "gray")
        self.store_chips.append((ch, tr("独立公司 · 合并订货后分货"), "gray"))
        r.addWidget(ch)
        v.addLayout(r)
        grid.addWidget(c, 1, 1)

        # 外观与用户
        c, v = _card(tr("外观与用户"))
        r = QHBoxLayout()
        r.addWidget(QLabel(tr("主题")))
        self.theme_box = QComboBox()
        self.theme_box.addItem(tr("浅色"), "light")
        self.theme_box.addItem(tr("深色"), "dark")
        self.theme_box.setCurrentIndex(1 if self.settings.get("theme") == "dark" else 0)
        self.theme_box.currentIndexChanged.connect(self._theme_changed)
        r.addWidget(self.theme_box)
        r.addStretch()
        v.addLayout(r)
        g2 = QGridLayout()
        g2.addWidget(QLabel(tr("用户名称")), 0, 0)
        self.user_name = QLineEdit(self.settings.get("user_name") or tr("采购部"))
        g2.addWidget(self.user_name, 0, 1)
        g2.addWidget(QLabel(tr("所在位置")), 1, 0)
        self.user_site = QLineEdit(self.settings.get("user_site") or tr("BMS 总仓"))
        g2.addWidget(self.user_site, 1, 1)
        v.addLayout(g2)
        sv = QPushButton(tr("保存"))
        sv.clicked.connect(self._save_user)
        r = QHBoxLayout()
        hint = QLabel(tr("语言在右上角切换；用户名称重启后显示在右上角"))
        hint.setObjectName("Faint")
        r.addWidget(hint, 1)
        r.addWidget(sv)
        v.addLayout(r)
        grid.addWidget(c, 2, 0)

        # 关于
        c, v = _card(tr("关于"))
        ur = QHBoxLayout()
        la = QLabel(tr("更新"))
        la.setObjectName("Faint")
        la.setFixedWidth(80)
        self.upd_info = QLabel("")
        self.upd_info.setWordWrap(True)
        ur.addWidget(la)
        ur.addWidget(self.upd_info, 1)
        v.addLayout(ur)
        ub = QHBoxLayout()
        ub.addSpacing(86)
        self.upd_auto = QCheckBox(tr("启动时自动检查更新"))
        self.upd_auto.setChecked(self.settings.get("update_auto", True))
        self.upd_auto.toggled.connect(self._upd_auto)
        ub.addWidget(self.upd_auto)
        ub.addStretch()
        cb = QPushButton(tr("检查更新"))
        cb.setObjectName("Primary")
        cb.clicked.connect(lambda: self.win.updater.check(silent=False))
        ub.addWidget(cb)
        v.addLayout(ub)
        for a, b in ((tr("版本"), f"{tr('CASA&TE采购系统')} {APP_VERSION}"),
                     (tr("设置文件"), SETTINGS_PATH),
                     (tr("模块"), tr("采购管理（门店配货、批发商比价、历史记录、起订规则）；分货管理（分货数据处理、导入 / 导出）"))):
            r = QHBoxLayout()
            la = QLabel(a)
            la.setObjectName("Faint")
            la.setFixedWidth(80)
            lb = QLabel(b)
            lb.setWordWrap(True)
            lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
            r.addWidget(la)
            r.addWidget(lb, 1)
            v.addLayout(r)
        v.addStretch()
        grid.addWidget(c, 2, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        lay.addStretch()
        self.refresh()
        return w

    # ---------- ----------
    def refresh(self):
        from fenpei.config import DatabaseConfig as D
        d = self.settings.get("db") or {}
        self.db_info.setText(tr("{0}@{1}:{2} · 数据库 {3}", d.get("user") or D.USER, d.get("host") or D.HOST,
                                d.get("port") or D.PORT, d.get("database") or D.DATABASE))
        ok = getattr(self.win, "_db_ok", None)
        set_chip(self.db_chip, tr("已连接") if ok else (tr("检查中…") if ok is None else tr("连不上")),
                 "ok" if ok else ("gray" if ok is None else "err"))
        acc = getattr(self.win, "_db_account", None) if ok else None
        if acc:
            set_chip(self.acc_chip, tr("只读账号") if acc["readonly"] else tr("有写入权限"),
                     "ok" if acc["readonly"] else "warn")
            self.acc_chip.setToolTip(acc["user"] + "\n" + "\n".join(acc["grants"]))
            self.acc_chip.show()
        else:
            self.acc_chip.hide()
        from reorder_page import algo_params, algo_summary
        p = algo_params(self.settings)
        self.engine.blockSignals(True)
        self.engine.setCurrentIndex(max(0, self.engine.findData(p.get("engine", "v2"))))
        self.engine.blockSignals(False)
        self.algo_info.setText(algo_summary(p))
        for e, getter in self.paths.values():
            e.setText(getter())
        self.refresh_update()

    def refresh_update(self):
        if not hasattr(self, "upd_info"):
            return
        import app_update as U
        cfg = U.load_config(self.settings)
        up = getattr(self.win, "updater", None)
        latest = up.latest if up else None
        if not cfg:
            txt = tr("还没有更新源（发布人发布一次后，程序里会自带）")
        else:
            txt = tr("更新源：GitHub {0}", cfg["repo"])
            if latest:
                txt += " · " + (tr("最新版本 {0}", latest["version"]) if U.is_newer(latest["version"], APP_VERSION)
                                else tr("已经是最新版本"))
        self.upd_info.setText(txt)

    def _upd_auto(self, on):
        self.settings["update_auto"] = bool(on)
        save_settings(self.settings)

    def restyle(self):
        for ch, tag, kind in getattr(self, "store_chips", []):
            set_chip(ch, tag, kind)
        if hasattr(self, "db_chip"):
            self.refresh()

    def edit_db(self):
        from reorder_page import DbDialog
        if DbDialog(self.win, self.settings).exec():
            self.refresh()
            self.win.check_db()

    def edit_algo(self):
        from reorder_page import AlgoDialog
        if AlgoDialog(self.win, self.settings).exec():
            self.refresh()

    def _engine_changed(self, *_):
        from reorder_page import algo_params
        p = algo_params(self.settings)
        p["engine"] = self.engine.currentData()
        self.settings["algo"] = p
        save_settings(self.settings)
        self.refresh()

    def _pick(self, edit, setter, is_file, label):
        start = edit.text() or ""
        if is_file:
            path, _ = QFileDialog.getOpenFileName(self.win, label, os.path.dirname(start), tr("程序 (*.exe *.py)"))
        else:
            path = QFileDialog.getExistingDirectory(self.win, label, start)
        if path:
            setter(os.path.normpath(path))
            save_settings(self.settings)
            self.refresh()
            self.win.show_toast(tr("已保存"))

    def _theme_changed(self, *_):
        want = self.theme_box.currentData()
        if want != (self.settings.get("theme") or "light"):
            self.win.toggle_theme()

    def _save_user(self):
        self.settings["user_name"] = self.user_name.text().strip() or tr("采购部")
        self.settings["user_site"] = self.user_site.text().strip() or tr("BMS 总仓")
        save_settings(self.settings)
        self.win.show_toast(tr("已保存"))
