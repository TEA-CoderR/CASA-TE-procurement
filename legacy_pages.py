"""
第二期要从旧程序（SistemaIntegrato，Tkinter）移植过来的模块（分货管理）；商品提取、订货建议、库存与分货已由门店配货取代，不再移植：先占位，并可直接打开旧程序。
"""
import os
import subprocess
import sys

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QFileDialog, QFrame

from i18n import tr
from settings import save_settings
from erp_widgets import EmptyState

MODULES = {
    "distribution": ("distribution", "分货数据处理", "分货数据处理",
                     "处理分货表格：从 Sheet1 双表格提取、把结果写回同一张表。",
                     ["双表格提取", "写回结果（从 O 列开始）", "导出单表"]),
    "import": ("import", "导入 / 导出", "Importa / Esporta Excel",
               "Excel 导入导出：读表格、单表导出、把结果写回原表。",
               ["导入 Excel 数据表", "导出单张表格", "写回结果"]),
    "tracking": ("tracking", "采购订单跟踪", "",
                 "每次生成的采购单自动登记，跟踪到货和 BMS 分货。",
                 ["总仓采购、DETERSIVO采购生成的订单自动记成采购单", "用入库记录对账：已下单 → 部分到货 → 已到货，超期提醒",
                  "在途数量自动从订货建议里扣掉", "BMS 到货后的分货进度"]),
}


class LegacyPage:
    def __init__(self, win, settings, key):
        self.win, self.settings, self.key = win, settings, key

    def build(self):
        ic, name, orig, desc, feats = MODULES[self.key]
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(14)
        t = QLabel(tr(name) + (f"  ·  {orig}" if orig and orig != name else ""))
        t.setObjectName("PageTitle")
        t.setText(t.text() + "  ⓘ")
        t.setToolTip(tr(desc))
        lay.addWidget(t)
        card = QFrame()
        card.setObjectName("Panel")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(0, 0, 0, 0)
        if self.key == "tracking":
            title = tr("采购订单跟踪将在第二期上线")
            text = tr("需要先确认数据库里的入库 / 出库记录，接上后会自动对账，并把在途数量从订货建议里扣掉。")
        else:
            title = tr("「{0}」将在第二期移入新系统", tr(name))
            text = tr("现在可以先用旧程序完成这项工作；移植后功能保持一致，界面换成新的样式。")
        es = EmptyState(ic, title, text)
        feat_box = QVBoxLayout()
        feat_box.setSpacing(6)
        for f in feats:
            l = QLabel("•  " + tr(f))
            l.setObjectName("Muted")
            feat_box.addWidget(l)
        fw = QWidget()
        fw.setLayout(feat_box)
        es.layout().insertWidget(3, fw)
        if self.key != "tracking":
            self.btn = QPushButton()
            self.btn.setObjectName("Primary")
            self.btn.clicked.connect(self.open_legacy)
            es.buttons.addWidget(self.btn)
            pick = QPushButton(tr("设置旧程序位置"))
            pick.clicked.connect(self.pick_legacy)
            es.buttons.addWidget(pick)
            self._update_btn()
        else:
            go = QPushButton(tr("去总仓采购"))
            go.setObjectName("Primary")
            go.clicked.connect(lambda: self.win.go_page("store_orders"))
            es.buttons.addWidget(go)
        cv.addWidget(es)
        lay.addWidget(card)
        lay.addStretch()
        return w

    def _exe(self):
        p = self.settings.get("legacy_exe") or ""
        return p if p and os.path.exists(p) else ""

    def _update_btn(self):
        self.btn.setText(tr("打开旧程序") if self._exe() else tr("找不到旧程序"))
        self.btn.setEnabled(bool(self._exe()))

    def pick_legacy(self):
        path, _ = QFileDialog.getOpenFileName(self.win, tr("选择旧程序 SistemaIntegrato"), os.path.dirname(self._exe()),
                                              tr("程序 (*.exe *.py)"))
        if path:
            self.settings["legacy_exe"] = path
            save_settings(self.settings)
            self._update_btn()
            for pg in getattr(self.win, "legacy_pages", []):
                pg._update_btn()

    def open_legacy(self):
        p = self._exe()
        if not p:
            return
        try:
            if p.lower().endswith(".py"):
                subprocess.Popen([sys.executable, p], cwd=os.path.dirname(p))
            else:
                subprocess.Popen([p], cwd=os.path.dirname(p))
            self.win.show_toast(tr("已打开旧程序"))
        except Exception as e:
            self.win.show_toast(tr("打开失败：{0}", e))
