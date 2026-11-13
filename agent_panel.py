# -*- coding: utf-8 -*-
"""
采购助手面板（主窗口右边，点顶栏「✦ 采购助手」打开 / 收起）。

只管显示和确认：对话在后台线程跑（agent.Agent），会改数据的步骤弹窗问人（每一步都确认，用户 2026-11-13 定），
计算 / 生成订单的结果同步到对应页面（总仓采购 / 门店采购），订单跟踪改了就刷新。
"""
import copy
import threading

from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtWidgets import (QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QScrollArea,
                               QPlainTextEdit, QDialog, QLineEdit, QComboBox, QDialogButtonBox, QGridLayout,
                               QMessageBox, QSizePolicy)

from i18n import tr
from theme import T
from settings import save_settings
import agent as A

PANEL_W = 360
QUICK = ["今天要订哪些供应商？", "算一下今天总仓采购的建议量", "哪些订单超期还没到货？", "看看邮件里有没有订单新进展"]


class _Sig(QObject):
    event = Signal(str, object)          # 后台线程 → 界面：kind, data
    done = Signal(object, object)        # 回答, 错误
    ask = Signal(str, str)               # 确认：标题, 说明
    hook = Signal(str, object)           # 页面同步


class KeyDialog(QDialog):
    """服务商 + API 密钥 + 模型（密钥只存本机，不进 settings.json；每家服务商一个密钥）"""

    TIPS = {"claude": "在 console.anthropic.com 生成的 Claude API 密钥（sk-ant-…）。",
            "deepseek": "在 platform.deepseek.com 生成的 DeepSeek API 密钥（sk-…）。\n"
                        "注意：用 DeepSeek 时，问的问题和查到的订单 / 价格 / 销量 / 邮件内容会发到 DeepSeek（中国）的服务器。"}

    def __init__(self, parent, settings):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle(tr("采购助手设置"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        g = QGridLayout()
        g.setSpacing(10)
        from erp_widgets import tip_label
        g.addWidget(tip_label(tr("服务商"), tr("用哪家的大模型。可以随时换，两家的密钥分开保存。\n"
                                              "Claude 更稳、业务理解更好；DeepSeek 便宜，但数据发到中国的服务器。")), 0, 0)
        self.prov = QComboBox()
        for k, p in A.PROVIDERS.items():
            self.prov.addItem(p["label"], k)
        self.prov.setCurrentIndex(self.prov.findData(A.provider_of(settings)))
        g.addWidget(self.prov, 0, 1)
        self.key_lab = tip_label(tr("API 密钥"), " ")
        g.addWidget(self.key_lab, 1, 0)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setMinimumWidth(300)
        g.addWidget(self.key, 1, 1)
        g.addWidget(tip_label(tr("模型"), tr("聪明的模型推理更强、贵几倍；日常采购用便宜的那个一般就够。")), 2, 0)
        self.model = QComboBox()
        g.addWidget(self.model, 2, 1)
        lay.addLayout(g)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.keys = {}                              # 这次在窗口里填的密钥（换服务商时先记着）
        self._shown = None
        self.prov.currentIndexChanged.connect(self._switch)
        self._switch()

    def _switch(self, *_):
        if self._shown and self.key.text().strip():
            self.keys[self._shown] = self.key.text().strip()
        p = self.prov.currentData()
        info = A.PROVIDERS[p]
        self._shown = p
        self.key.setText(self.keys.get(p, ""))
        self.key.setPlaceholderText(tr("已保存") if A.api_key(p) else info["hint"])
        tip = tr(self.TIPS[p]) + "\n" + tr("只存在这台电脑的本机密码保管里，不放共享文件夹、不随发布同步。")
        self.key_lab.setToolTip(tip)
        self.model.clear()
        for k, label in info["models"]:
            self.model.addItem(tr(label), k)
        cur = A.model_of(self.settings, p)
        if self.model.findData(cur) < 0:
            self.model.addItem(cur, cur)
        self.model.setCurrentIndex(self.model.findData(cur))

    def _save(self):
        p = self.prov.currentData()
        if self.key.text().strip():
            self.keys[p] = self.key.text().strip()
        for k, v in self.keys.items():
            A.set_api_key(v, k)
        cfg = self.settings.setdefault("agent", {})
        models = cfg.setdefault("models", {})
        if cfg.get("model"):                        # 老设置（只有 Claude 时）挪进 models
            models.setdefault("claude", cfg.pop("model"))
        cfg["provider"] = p
        models[p] = self.model.currentData()
        save_settings(self.settings)
        self.accept()


class _Bubble(QFrame):
    def __init__(self, text, who):
        super().__init__()
        self.setObjectName("AgentUser" if who == "user" else "AgentBot" if who == "bot" else "AgentStep")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8) if who != "step" else lay.setContentsMargins(4, 0, 4, 0)
        self.lab = QLabel()
        self.lab.setWordWrap(True)
        self.lab.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        self.lab.setTextFormat(Qt.MarkdownText if who == "bot" else Qt.PlainText)
        self.lab.setText(text)
        lay.addWidget(self.lab)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

    def set_text(self, text):
        self.lab.setText(text)


class AgentPanel(QFrame):
    def __init__(self, win, settings):
        super().__init__()
        self.win, self.settings = win, settings
        self.setObjectName("AgentPanel")
        self.setFixedWidth(PANEL_W)
        self.sig = _Sig()
        self.sig.event.connect(self._event)
        self.sig.done.connect(self._done)
        self.sig.ask.connect(self._ask)
        self.sig.hook.connect(self._hook)
        self._answer = None
        self._answer_ev = threading.Event()
        self.busy = False
        self.stop = threading.Event()
        self.ctx = {"settings": settings, "calc": {}}
        self.agent = None
        self._build()
        self.restyle()

    # ------------------------------------------------------------------ 界面
    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QFrame()
        head.setObjectName("AgentHead")
        head.setFixedHeight(56)
        h = QHBoxLayout(head)
        h.setContentsMargins(16, 0, 10, 0)
        title = QLabel(tr("✦ 采购助手") + "  ⓘ")
        title.setObjectName("AgentTitle")
        title.setToolTip(tr("用平常说话的方式让它做事：查今天的供应商、算建议量、生成采购订单、查订单、核对邮件、发订单邮件。\n"
                            "会改数据的每一步（生成订单、改订单状态、发邮件）都会先弹窗让你确认。"))
        h.addWidget(title)
        self.prov_lab = QLabel()
        self.prov_lab.setObjectName("AgentProv")
        h.addWidget(self.prov_lab)
        self._show_provider()
        h.addStretch()
        self.btn_new = QPushButton(tr("新对话"))
        self.btn_new.setObjectName("TopBtnText")
        self.btn_new.setToolTip(tr("清空对话（已经生成的订单、改过的数据不受影响）"))
        self.btn_new.clicked.connect(self.new_chat)
        h.addWidget(self.btn_new)
        self.btn_set = QPushButton("⚙")
        self.btn_set.setObjectName("TopBtnText")
        self.btn_set.setToolTip(tr("服务商、API 密钥、模型"))
        self.btn_set.clicked.connect(self.open_settings)
        h.addWidget(self.btn_set)
        close = QPushButton("✕")
        close.setObjectName("TopBtnText")
        close.setToolTip(tr("收起"))
        close.clicked.connect(lambda: self.win.toggle_agent(False))
        h.addWidget(close)
        v.addWidget(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.body = QWidget()
        self.body.setObjectName("AgentBody")
        self.list = QVBoxLayout(self.body)
        self.list.setContentsMargins(14, 14, 14, 14)
        self.list.setSpacing(10)
        self.list.addStretch()
        self.scroll.setWidget(self.body)
        v.addWidget(self.scroll, 1)

        self.quick = QWidget()
        q = QVBoxLayout(self.quick)
        q.setContentsMargins(14, 0, 14, 8)
        q.setSpacing(6)
        for text in QUICK:
            b = QPushButton(tr(text))
            b.setObjectName("AgentChip")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, t=text: self.send(tr(t)))
            q.addWidget(b)
        v.addWidget(self.quick)

        self.status = QLabel("")
        self.status.setObjectName("Faint")
        self.status.setContentsMargins(16, 0, 16, 4)
        self.status.hide()
        v.addWidget(self.status)

        foot = QFrame()
        foot.setObjectName("AgentFoot")
        f = QHBoxLayout(foot)
        f.setContentsMargins(12, 10, 12, 12)
        f.setSpacing(8)
        self.input = QPlainTextEdit()
        self.input.setObjectName("AgentInput")
        self.input.setPlaceholderText(tr("想让助手做什么？Enter 发送，Shift+Enter 换行"))
        self.input.setFixedHeight(64)
        self.input.installEventFilter(self)
        f.addWidget(self.input, 1)
        self.btn = QPushButton(tr("发送"))
        self.btn.setObjectName("Primary")
        self.btn.setFixedWidth(64)
        self.btn.clicked.connect(self._btn_clicked)
        f.addWidget(self.btn, 0, Qt.AlignBottom)
        v.addWidget(foot)
        if not self._has_key():
            self._add(tr("还没填 {0} API 密钥。点上面的 ⚙ 填一次就行（只存在这台电脑上）。").format(
                A.provider_label(A.provider_of(self.settings))), "bot")

    def restyle(self):
        self.setStyleSheet(f"""
            #AgentPanel {{ background: {T('surface')}; border-left: 1px solid {T('line')}; }}
            #AgentHead {{ border-bottom: 1px solid {T('line')}; }}
            #AgentTitle {{ font-size: 15px; font-weight: 600; color: {T('text')}; }}
            #AgentProv {{ font-size: 11px; color: {T('muted')}; border: 1px solid {T('line')}; border-radius: 8px;
                          padding: 1px 7px; margin-left: 6px; }}
            #AgentBody {{ background: {T('surface')}; }}
            #AgentUser {{ background: {T('accent_soft')}; border-radius: 12px; margin-left: 40px; }}
            #AgentBot {{ background: {T('surface2')}; border: 1px solid {T('line')}; border-radius: 12px; margin-right: 16px; }}
            #AgentStep QLabel {{ color: {T('faint')}; font-size: 12px; }}
            #AgentChip {{ text-align: left; padding: 7px 12px; border: 1px solid {T('line')}; border-radius: 14px;
                          background: {T('surface')}; color: {T('muted')}; }}
            #AgentChip:hover {{ border-color: {T('accent')}; color: {T('accent')}; }}
            #AgentFoot {{ border-top: 1px solid {T('line')}; }}
            #AgentInput {{ border: 1px solid {T('line')}; border-radius: 8px; padding: 6px; background: {T('surface')}; }}
            #TopBtnText {{ border: none; padding: 4px 8px; color: {T('muted')}; background: transparent; }}
            #TopBtnText:hover {{ color: {T('accent')}; }}
        """)

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if obj is self.input and ev.type() == QEvent.KeyPress and ev.key() in (Qt.Key_Return, Qt.Key_Enter) \
                and not (ev.modifiers() & Qt.ShiftModifier):
            self._btn_clicked()
            return True
        return False

    def _add(self, text, who):
        b = _Bubble(text, who)
        self.list.insertWidget(self.list.count() - 1, b)
        QTimer.singleShot(30, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))
        return b

    def _show_provider(self):
        p = A.provider_of(self.settings)
        self.prov_lab.setText(A.provider_label(p))
        self.prov_lab.setToolTip(tr("现在用的：{0} · {1}（点 ⚙ 换）").format(A.provider_label(p), A.model_of(self.settings, p)))

    def _has_key(self):
        return bool(A.api_key(A.provider_of(self.settings)))

    def open_settings(self):
        before = (A.provider_of(self.settings), A.model_of(self.settings))
        if KeyDialog(self, self.settings).exec():
            if before != (A.provider_of(self.settings), A.model_of(self.settings)) and not self.busy:
                self.new_chat()                     # 换了模型：对话从头开始，界面上也清掉，免得以为它还记得
            self.agent = None                       # 下次用新的服务商 / 密钥 / 模型（对话重新开始）
            self._show_provider()

    def new_chat(self):
        if self.busy:
            return
        self.agent = None
        self.ctx = {"settings": self.settings, "calc": {}}
        while self.list.count() > 1:
            w = self.list.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.quick.show()

    # ------------------------------------------------------------------ 对话
    def _btn_clicked(self):
        if self.busy:
            self.stop.set()
            if self.agent:
                self.agent.cancelled = True
            self.status.setText(tr("正在停止…（这一步做完就停）"))
            return
        text = self.input.toPlainText().strip()
        if text:
            self.input.clear()
            self.send(text)

    def _make_agent(self):
        import agent_tools as AT
        self.ctx.update(log=lambda m: self.sig.event.emit("log", m),
                        progress=lambda i, st, cnt, det, pct: self.sig.event.emit("progress", (det or cnt, pct)),
                        cancel=lambda: self.stop.is_set(),
                        hooks={"computed": lambda kind, calc: self.sig.hook.emit("computed", (kind, calc)),
                               "allocated": lambda kind, calc, res: self.sig.hook.emit("allocated", (kind, res)),
                               "tracking_changed": lambda: self.sig.hook.emit("tracking", None)})
        return A.Agent(self.settings, AT.build_tools(), self._confirm, on_event=lambda k, d: self.sig.event.emit(k, d),
                       ctx=self.ctx)

    def send(self, text):
        if self.busy:
            return
        if not self._has_key():
            self.open_settings()
            if not self._has_key():
                return
        self.quick.hide()
        self._add(text, "user")
        if self.agent is None:
            self.agent = self._make_agent()
        self.busy = True
        self.stop.clear()
        self.btn.setText(tr("停止"))
        self.status.setText(tr("正在想…"))
        self.status.show()
        ag = self.agent

        def work():
            try:
                ans = ag.send(text)
                self.sig.done.emit(ans, None)
            except Exception as e:
                self.sig.done.emit(None, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _event(self, kind, data):
        if kind == "text":
            self._add(data, "bot")
        elif kind == "tool":
            self.status.setText(tr("正在：{0}…", tr(data["label"])))
            self._step = self._add("⋯ " + tr(data["label"]), "step")
        elif kind == "tool_done":                # 同一行改成做完了（不再多一行）
            text = ("✗ " if data.get("error") else "✓ ") + tr(data["label"]) + tr("（{0} 秒）", data["seconds"])
            b = getattr(self, "_step", None)
            if b is not None:
                b.set_text(text)
                self._step = None
            else:
                self._add(text, "step")
        elif kind == "progress":
            det, pct = data
            self.status.setText(tr("{0} · {1}%", det or tr("处理中"), int(pct or 0)))
        elif kind == "log":
            try:
                self.win.log(tr("[采购助手] ") + str(data))
            except Exception:
                pass

    def _done(self, ans, err):
        self.busy = False
        self.btn.setText(tr("发送"))
        self.status.hide()
        if err:
            self._add(tr("出错了：{0}", err), "bot")

    # ------------------------------------------------------------------ 确认（后台线程等界面回答）
    def _confirm(self, title, text):
        self._answer_ev.clear()
        self._answer = False
        self.sig.ask.emit(title, text)
        self._answer_ev.wait()
        return self._answer

    def _ask(self, title, text):
        box = QMessageBox(self.win)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("采购助手 · {0}", tr(title)))
        box.setText(tr("采购助手要「{0}」，确认吗？", tr(title)))
        box.setInformativeText(text)
        ok = box.addButton(tr("确认"), QMessageBox.AcceptRole)
        box.addButton(tr("取消"), QMessageBox.RejectRole)
        box.setDefaultButton(ok)
        box.exec()
        self._answer = box.clickedButton() is ok
        self._add((tr("✓ 你确认了：{0}", tr(title)) if self._answer else tr("✗ 你取消了：{0}", tr(title))), "step")
        self._answer_ev.set()

    # ------------------------------------------------------------------ 结果同步到页面
    def _page(self, kind):
        return getattr(self.win, "store_pdv" if kind == "pdv" else "store_orders", None)

    def _hook(self, what, data):
        try:
            if what == "computed":
                kind, calc = data
                page = self._page(kind)
                if page is None or page.running:
                    return
                c2 = dict(calc)
                c2["_ui"] = {"colli": copy.deepcopy(calc["_ui"]["colli"]), "pv": {}}
                page._computed((c2, None))
            elif what == "allocated":
                kind, res = data
                page = self._page(kind)
                if page is None:
                    return
                page._task = None
                page._done((res, None))
            elif what == "tracking":
                t = getattr(self.win, "tracking", None)
                if t is not None and getattr(t, "st", None) is not None:
                    t.refresh()
        except Exception as e:
            try:
                self.win.log(tr("[采购助手] 页面没同步上：{0}", e))
            except Exception:
                pass
