"""
数据库设置窗口 + 订货建议用到的小工具。
（原来的「订货建议」页已拆开：门店配货页里算品牌供应商的订货量；订单处理页里检查门店订货清单。）
"""
from PySide6.QtWidgets import (QLabel, QPushButton, QLineEdit, QHBoxLayout, QVBoxLayout, QGridLayout, QDialog,
                               QDialogButtonBox)

from i18n import tr
from settings import save_settings
import reorder_service as R


_DECISION_SHORT = [("Rifornimento urgente", "紧急补货"), ("Ordine necessario", "必须订货"),
                   ("Ordine suggerito", "建议订货"), ("Non ordinare", "不订"), ("Osserva", "观察"),
                   # 新算法
                   ("【紧急】", "紧急补货"), ("【建议订货】", "建议订货"), ("【可订】", "可订"),
                   ("【不动销】", "不动销"), ("【库存多】", "库存多"), ("【不用订】", "不用订"), ("【无销量】", "无销量")]


def short_decision(text):
    for key, zh in _DECISION_SHORT:
        if key in (text or ""):
            return tr(zh)
    return text or ""


def store_label(sid):
    return f"{sid} · {tr(R.STORES[sid])}" if sid == 0 else f"{sid} · {R.STORES[sid]}"


class DbDialog(QDialog):
    """数据库连接设置（存到 settings.json 的 db 部分）"""

    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setWindowTitle(tr("数据库设置"))
        self.settings = settings
        from fenpei.config import DatabaseConfig as D
        cur = dict(host=D.HOST, port=D.PORT, user=D.USER, password="", database=D.DATABASE)
        cur.update({k: v for k, v in (settings.get("db") or {}).items() if v not in (None, "") and k != "password"})
        lay = QVBoxLayout(self)
        g = QGridLayout()
        self.ed = {}
        for i, (k, label) in enumerate([("host", tr("地址")), ("port", tr("端口")), ("user", tr("用户")),
                                        ("password", tr("密码")), ("database", tr("数据库名"))]):
            g.addWidget(QLabel(label), i, 0)
            e = QLineEdit(str(cur.get(k) or ""))
            if k == "password":
                e.setEchoMode(QLineEdit.Password)
                e.setPlaceholderText(tr("不改就留空"))
            g.addWidget(e, i, 1)
            self.ed[k] = e
        lay.addLayout(g)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        row = QHBoxLayout()
        t = QPushButton(tr("测试连接"))
        t.clicked.connect(self.test)
        row.addWidget(t)
        row.addStretch()
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)
        self.resize(420, 0)

    def values(self):
        v = {k: e.text().strip() for k, e in self.ed.items()}
        if not v["password"]:
            v["password"] = (self.settings.get("db") or {}).get("password", "")
        return v

    def test(self):
        self.msg.setText(tr("正在连接…"))
        self.repaint()
        try:
            db = R.make_db(self.values())
            R.test_connection(db)
            db.engine.dispose()
            self.msg.setText(tr("✔ 连接成功"))
        except Exception as e:
            self.msg.setText(tr("✘ 连接失败：{0}", e))

    def save(self):
        import secure_store
        v = self.values()
        v["password"] = secure_store.protect(v["password"])      # 加密保存，settings.json 里没有明文
        self.settings["db"] = v
        save_settings(self.settings)
        self.accept()


ENGINES = [("v2", "新算法（服务水平）"), ("v1", "原程序算法")]


def algo_params(settings):
    import forecast as F
    return dict(F.DEFAULT_PARAMS, **(settings.get("algo") or {}))


class AlgoDialog(QDialog):
    """新算法参数（存到 settings.json 的 algo 部分；门店配货和订单处理共用）
    排版（用户 2026-10-29：看着乱）：三块 —— 基本 / 按 ABC（A B C 三列对齐）/ 门店周期（五家店五列）；
    所有输入框一样宽、单位写在框里，说明都在 ⓘ 鼠标提示"""
    W = 92                      # 输入框统一宽度

    def __init__(self, parent, settings):
        from PySide6.QtWidgets import QSpinBox, QDoubleSpinBox, QCheckBox, QFrame
        from PySide6.QtCore import Qt
        super().__init__(parent)
        self.setWindowTitle(tr("订货算法设置"))
        self.settings = settings
        p = algo_params(settings)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(6)
        from erp_widgets import tip_label
        self.setToolTip(tr("新算法：目标库存 = 下次到货前要卖的量 + 安全库存（按销量波动和服务水平算），建议量 = 目标库存 − 现有库存。日均 = 近 4 周 50% + 13 周 30% + 26 周 20%，可按去年同期做季节调整。"))
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(8)
        g.setColumnMinimumWidth(0, 120)
        self.w = {}
        row = [0]

        def section(text, tip=None, top=14):
            if row[0]:
                g.setRowMinimumHeight(row[0], top)
                row[0] += 1
            t = QLabel(text + ("  ⓘ" if tip else ""))
            t.setObjectName("Section")
            if tip:
                t.setToolTip(tip)
            g.addWidget(t, row[0], 0, 1, 6)
            row[0] += 1

        def mk(key, lo, hi, dec=None, scale=1, prefix="", suffix="", step=None, default=None):
            v = p.get(key, default)
            if dec is None:
                sb = QSpinBox()
                sb.setRange(lo, hi)
                sb.setValue(int(round(float(v) * scale)))
            else:
                sb = QDoubleSpinBox()
                sb.setDecimals(dec)
                sb.setRange(lo, hi)
                sb.setValue(float(v) * scale)
            if step:
                sb.setSingleStep(step)
            sb.setPrefix(prefix)
            sb.setSuffix(suffix)
            sb.setFixedWidth(self.W)
            sb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.w[key] = (sb, scale)
            return sb

        def line(label, tip, widgets):
            g.addWidget(tip_label(label, tip), row[0], 0)
            for i, wdg in enumerate(widgets):
                g.addWidget(wdg, row[0], 1 + i)
            row[0] += 1

        def head(names):
            for i, n in enumerate(names):
                lb = QLabel(n)
                lb.setObjectName("Faint")
                lb.setAlignment(Qt.AlignCenter)
                lb.setFixedWidth(self.W)
                g.addWidget(lb, row[0], 1 + i)
            row[0] += 1

        # ① 基本
        section(tr("基本"))
        line(tr("订货周期"), tr("多久下一次单。每周订 = 7；这次订的要卖到下次订的货到为止"),
             [mk("review_days", 1, 120, suffix=tr(" 天"))])
        line(tr("到货"), tr("下单后几天到货（一般 2–7 天）"), [mk("lead_days", 0, 60, suffix=tr(" 天"))])
        line(tr("服务水平"), tr("不缺货的概率。95 = 100 次里约 5 次可能卖断"),
             [mk("service", 50, 99.5, dec=1, scale=100, suffix=" %")])
        line(tr("安全库存最多"), tr("按服务水平多备的货最多够卖几天（只限制卖得快的商品；卖得慢的按服务水平保证不断货）。A/B/C 的差别由服务水平区分"),
             [mk("max_safety_days", 0, 90, suffix=tr(" 天"))])
        line(tr("最少陈列"), tr("近 13 周有销量的商品至少保留几件"), [mk("min_shelf", 0, 50, suffix=tr(" 件"))])
        self.season = QCheckBox(tr("季节调整"))
        self.season.setToolTip(tr("用去年同期做季节调整（数据不够一年或量太小时不调整）"))
        self.season.setChecked(bool(p["season"]))
        g.addWidget(self.season, row[0], 1, 1, 3)
        row[0] += 1

        # ② 按 ABC：A B C 三列对齐
        section(tr("按 ABC"))
        head(["A", "B", "C"])
        line(tr("周期系数"), tr("按 ABC 乘订货周期：A 类多备（1.25 = 订够 1.25 个周期），C 类少备（0.75 = 订够 3/4 个周期），少压货。ABC 按本店近 13 周销售额：前 80% A，到 95% B，其余 C"),
             [mk(k, 0.1, 3.0, dec=2, prefix="×", step=0.05, default=1.0) for k in ("cycle_a", "cycle_b", "cycle_c")])
        line(tr("最低保障线"), tr("件数凑不成整箱时：少订（往下取整）后的库存够卖一个订货周期的这个比例，就少订，不为了一两件硬凑一箱；不够才多订一箱。（和「安全库存」不是一回事：安全库存是按服务水平多备的货）"),
             [mk(k, 0, 200, scale=100, suffix=" %", step=5, default=d) for k, d in (("safety_a", 0.9), ("safety_b", 0.7), ("safety_c", 0.5))])
        line(tr("动销周数"), tr("近 13 周有几周卖过：A 列 = A 类至少要这么多周有卖，不然最多 B；B 列 = 卖过的周数 ≥ 它 → 最低算 B（天天卖的便宜货不当 C 少订）；C 列 = ≤ 它 → 最高算 C（偶尔才卖的不多备）。新品按上架以来的周数折算，上架不到 4 周的最多 B。B 列填 14 = 不升级，C 列填 0 = 不降级"),
             [mk("abc_freq_a", 0, 13, prefix="≥ ", suffix=tr(" 周"), default=6),
              mk("abc_freq_hi", 1, 14, prefix="≥ ", suffix=tr(" 周"), default=10),
              mk("abc_freq_lo", 0, 13, prefix="≤ ", suffix=tr(" 周"), default=3)])
        self.abc = QCheckBox(tr("按 ABC 调整服务水平"))
        self.abc.setToolTip(tr("按 ABC 调整服务水平（A +2.5%，C −5%；ABC 按本店近 13 周销售额帕累托）"))
        self.abc.setChecked(bool(p["abc_service"]))
        g.addWidget(self.abc, row[0], 1, 1, 3)
        row[0] += 1

        # ③ 门店周期（只用在门店配货）：五家店五列
        section(tr("门店周期"), tr("只用在门店配货：这家店的订货周期 × 系数（配置文件里供应商自己的周期也一起乘）。销量差、凑不够起订量的店填大于 1，一次订够更久、下单次数少（例如 × 1.5：30 天 → 45 天）；销量好、补货勤的店填小于 1，少压货。1 = 和现在一样"))
        stores = [(k, v) for k, v in R.STORES.items() if k]
        head([v for _, v in stores])
        sc = p.get("store_cycle") or {}
        self.store_cycle = {}
        boxes = []
        for sid, _name in stores:
            sb = QDoubleSpinBox()
            sb.setDecimals(2)
            sb.setRange(0.25, 4.0)
            sb.setSingleStep(0.25)
            sb.setPrefix("×")
            sb.setValue(float(sc.get(str(sid), 1.0) or 1.0))
            sb.setFixedWidth(self.W)
            sb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            sb.valueChanged.connect(lambda _v, b=sb: self._tip_store(b))
            self.store_cycle[sid] = sb
            boxes.append(sb)
        line(tr("系数"), tr("× 订货周期；鼠标停在框上看换算成几天"), boxes)
        for b in boxes:
            self._tip_store(b)
        self.w["review_days"][0].valueChanged.connect(lambda _v: [self._tip_store(b) for b in self.store_cycle.values()])
        g.setColumnStretch(6, 1)
        lay.addLayout(g)
        lay.addSpacing(10)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self.defaults)
        lay.addWidget(bb)
        self.adjustSize()

    def _tip_store(self, sb):
        """鼠标停在系数上：按现在的订货周期换算成天数"""
        try:
            base = float(self.w["review_days"][0].value()) if "review_days" in self.w else 30.0
        except Exception:
            base = 30.0
        sb.setToolTip(tr("订货周期 {0:g} 天 × {1:g} = {2:g} 天（供应商自己的周期也乘 {1:g}）", base, sb.value(),
                         round(base * sb.value())))

    def defaults(self):
        import forecast as F
        for sb in getattr(self, "store_cycle", {}).values():
            sb.setValue(1.0)
        from PySide6.QtWidgets import QSpinBox
        for k, (sb, scale) in self.w.items():
            v = F.DEFAULT_PARAMS[k] * scale
            sb.setValue(int(round(v)) if isinstance(sb, QSpinBox) else float(v))
        self.abc.setChecked(F.DEFAULT_PARAMS["abc_service"])
        self.season.setChecked(F.DEFAULT_PARAMS["season"])

    def save(self):
        p = algo_params(self.settings)
        for k, (sb, scale) in self.w.items():
            v = sb.value() / scale
            p[k] = int(v) if isinstance(p.get(k), int) and float(v).is_integer() else v
        p["abc_service"] = self.abc.isChecked()
        p["season"] = self.season.isChecked()
        p["store_cycle"] = {str(sid): round(float(sb.value()), 2) for sid, sb in self.store_cycle.items()}
        self.settings["algo"] = p
        save_settings(self.settings)
        self.accept()


def algo_summary(p):
    if p.get("engine") != "v2":
        return tr("原程序算法：日均 × 订货周期 × 系数，按数据库箱规向上取整")
    return tr("订货周期 {0} 天 + 到货 {1} 天，服务水平 {2:.0%}{3}，安全库存最多 {4} 天",
              p["review_days"], p["lead_days"], p["service"], tr("（按 ABC）") if p.get("abc_service") else "",
              p.get("max_safety_days", 7)) + \
        tr("，最低保障线 A {0:.0%} · B {1:.0%} · C {2:.0%}", p.get("safety_a", 0.9), p.get("safety_b", 0.7), p.get("safety_c", 0.5)) + \
        tr("，周期系数 A ×{0:g} · B ×{1:g} · C ×{2:g}", p.get("cycle_a", 1), p.get("cycle_b", 1), p.get("cycle_c", 1)) + \
        _store_cycle_text(p)


def _store_cycle_text(p):
    """「，门店周期 FOIANO ×1.5、…」：只列不是 1 的店"""
    parts = []
    for k, v in sorted((p.get("store_cycle") or {}).items()):
        try:
            if float(v) != 1.0:
                parts.append(f"{R.STORES.get(int(k), k)} ×{float(v):g}")
        except (TypeError, ValueError):
            continue
    return tr("，门店周期 {0}", "、".join(parts)) if parts else ""


_ALGO_UIS = []          # 各页面的算法选择（改了一个，其他跟着变）


def algo_widgets(page, ui):
    """算法选择 + 设置按钮 + 一行说明；挂到 page 上：page.current_algo()、切换时禁用订货周期"""
    from PySide6.QtWidgets import QComboBox
    row = QHBoxLayout()
    engine = QComboBox()
    for k, label in ENGINES:
        engine.addItem(tr(label), k)
    p = algo_params(page.settings)
    engine.setCurrentIndex(max(0, engine.findData(p.get("engine", "v2"))))
    row.addWidget(engine)
    btn = QPushButton(tr("算法设置…"))
    row.addWidget(btn)
    info = QLabel("")
    info.setObjectName("Faint")
    info.setWordWrap(True)
    row.addWidget(info, 1)

    def apply_ui(cur):
        """按设置更新这一页的算法下拉、说明和可用状态（不保存）"""
        i = engine.findData(cur.get("engine", "v2"))
        if i >= 0 and i != engine.currentIndex():
            engine.blockSignals(True)
            engine.setCurrentIndex(i)
            engine.blockSignals(False)
        info.setText("ⓘ")
        info.setToolTip(algo_summary(cur))
        engine.setToolTip(algo_summary(cur))
        v1 = cur["engine"] == "v1"
        for w in (getattr(page, "cycle", None),):   # 「按配置文件周期」两种算法都能用
            if w is not None:
                if not hasattr(w, "_tip0"):
                    w._tip0 = w.toolTip()
                w.setEnabled(v1)
                w.setToolTip(w._tip0 if v1 else tr("新算法按「算法设置」里的订货周期和到货天数算，这里的不用"))
        # 原程序算法严格按原程序算，「算法设置」里的参数（服务水平、到货天数 …）都用不到
        btn.setEnabled(not v1)
        btn.setToolTip(tr("原程序算法严格按原程序计算，不用填算法设置") if v1 else "")

    def refresh(*_):
        cur = algo_params(page.settings)
        cur["engine"] = engine.currentData()
        page.settings["algo"] = cur
        save_settings(page.settings)
        # 门店配货和检查门店订货清单用同一个算法设置：一页改了，另一页跟着变（两边算出来才一样）
        for other in list(_ALGO_UIS):
            if other is apply_ui:
                continue
            try:
                other(cur)
            except RuntimeError:              # 那一页已经关掉了
                _ALGO_UIS.remove(other)
        apply_ui(cur)

    def open_dialog():
        if AlgoDialog(page.win, page.settings).exec():
            refresh()

    engine.currentIndexChanged.connect(refresh)
    btn.clicked.connect(open_dialog)
    _ALGO_UIS.append(apply_ui)
    page.current_algo = lambda: dict(algo_params(page.settings), engine=engine.currentData())
    apply_ui(dict(algo_params(page.settings), engine=engine.currentData()))
    return row, engine, info
