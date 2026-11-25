"""
商品图片（2026-10-25）：按条码到公司图片服务器取主图
  https://img.casaete.top/prodotti/<条码>/01.webp   （有这个条码的文件夹就有图，只取第一张）

做法（不卡界面、不重复下载）：
  · 表格只在画到那一行时才要图（看得见的才下载），后台 4 个线程下载，下载完通知表格重画那一格；
  · 下载下来的图**按原图大小**存在程序目录 cache/images/ 里（原样存 webp；Qt 读不了 webp 才转成 PNG），
    下次打开直接读本地文件，不再联网；显示时按屏幕缩放（高分屏 ×1.5 / ×2）缩到正好的像素，不发糊；
  · 服务器上没有的（404）记一个标记，7 天内不再问；网络出错只在这次运行里不再试。
  · Qt 读不了 webp 时用 Pillow 转（打包时要带上 Pillow）。
设置：settings["show_images"]（默认开），settings["image_url"]（图片地址模板，默认上面那个）。
"""
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal, Qt, QRect, QSize
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor
from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOptionViewItem, QApplication, QToolTip

DEFAULT_URL = "https://img.casaete.top/prodotti/{ean}/01.webp"
MEM_MB = 60               # 内存里缓存的小图最多多少 MB
MISS_DAYS = 1          # 服务器上没有图（404）：一天以后再问一次（图会陆续传上去）
MISS_VER = 2           # 2026-10-31：以前把 403（服务器拒绝 / 限流）也记成「没有图」7 天，作废一次重新问
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 CasaTe-Procurement")
PREVIEW = 600          # 点详情栏的图放大看：最大多少
HOVER = 320            # 表格里鼠标停在小图上：弹出多大的图
SIZES = {"s": 40, "m": 64, "l": 96}     # 表格小图：小 / 中 / 大（settings["image_size"]，默认中）


def thumb_size(settings=None):
    return SIZES.get((settings or {}).get("image_size") or "m", SIZES["m"])


def row_height(settings=None, base=34):
    """表格行高：开了图片按小图大小加高，没开照旧"""
    s = settings or {}
    return max(base, thumb_size(s) + 8) if s.get("show_images", True) else base


def norm_ean(ean):
    s = str(ean or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s if s.isdigit() and 6 <= len(s) <= 14 else ""


def candidates(ean):
    """服务器文件夹名可能是 13 位（数据库里去掉了前面的 0）：先原样，再补成 13 位"""
    e = norm_ean(ean)
    if not e:
        return []
    out = [e]
    if len(e) < 13:
        out.append(e.zfill(13))
    return out


def _decode(data):
    """图片字节 → QImage（先用 Qt，读不了 webp 再用 Pillow）"""
    img = QImage.fromData(data)
    if not img.isNull():
        return img
    try:
        from io import BytesIO
        from PIL import Image
        im = Image.open(BytesIO(data)).convert("RGBA")
        buf = BytesIO()
        im.save(buf, "PNG")
        img = QImage.fromData(buf.getvalue())
        return None if img.isNull() else img
    except Exception:
        return None


class ImageStore(QObject):
    ready = Signal(str)        # 某个条码的图准备好了（主线程收到）

    def __init__(self, cache_dir, url=DEFAULT_URL, enabled=True):
        super().__init__()
        self.dir = cache_dir
        self._used = set()
        self.url = url or DEFAULT_URL
        self.enabled = enabled
        os.makedirs(self.dir, exist_ok=True)
        self._clear_old_misses()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="img")
        self._pending, self._failed = set(), set()
        self._lock = threading.Lock()
        self._mem = {}             # (条码, 尺寸) → QPixmap
        self._mem_bytes = 0
        self._session = None

    def _clear_old_misses(self):
        """旧版本记下的「没有图」作废一次（有的其实是服务器临时拒绝，不是真没有）"""
        mark = os.path.join(self.dir, ".miss_ver")
        try:
            with open(mark, encoding="utf-8") as f:
                if f.read().strip() == str(MISS_VER):
                    return
        except OSError:
            pass
        try:
            for f in os.listdir(self.dir):
                if f.endswith(".none"):
                    try:
                        os.remove(os.path.join(self.dir, f))
                    except OSError:
                        pass
            with open(mark, "w", encoding="utf-8") as f:
                f.write(str(MISS_VER))
        except OSError:
            pass

    # ---------- 本地缓存 ----------
    def _png(self, ean):
        return os.path.join(self.dir, f"{ean}.png")

    def _raw(self, ean):
        return os.path.join(self.dir, f"{ean}.webp")

    def path(self, ean):
        """本地缓存的图（原图），没有返回 None"""
        for p in (self._raw(ean), self._png(ean)):
            if os.path.exists(p):
                if p not in self._used:            # 这次打开程序第一次用：摸一下，自动清理从最久没用的图删起
                    self._used.add(p)
                    import housekeeping
                    housekeeping.touch(p)
                return p
        return None

    def _miss(self, ean):
        return os.path.join(self.dir, f"{ean}.none")

    def has_cached(self, ean):
        return self.path(ean) is not None

    def known_missing(self, ean):
        p = self._miss(ean)
        try:
            return os.path.exists(p) and time.time() - os.path.getmtime(p) < MISS_DAYS * 86400
        except OSError:
            return False

    # ---------- 给界面用 ----------
    def pixmap(self, ean, size, dpr=None):
        """
        有图返回缩好的 QPixmap（按屏幕缩放 dpr 缩到实际像素，高分屏也清楚）；
        还没有返回 None（同时在后台去取，取到后发 ready）
        """
        e = norm_ean(ean)
        if not e or not self.enabled:
            return None
        if dpr is None:
            try:
                dpr = QApplication.primaryScreen().devicePixelRatio()
            except Exception:
                dpr = 1.0
        dpr = max(1.0, float(dpr or 1.0))
        key = (e, size, dpr)
        pm = self._mem.get(key)
        if pm is not None:
            return pm
        p = self.path(e)
        if p:
            img = QImage(p)
            if img.isNull():
                return None
            px = int(round(size * dpr))
            if max(img.width(), img.height()) > px:
                img = img.scaled(px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pm = QPixmap.fromImage(img)
            pm.setDevicePixelRatio(dpr)
            # 内存里最多留约 MEM_MB 的小图（以前按张数 3000 张，大图 / 高分屏时能到几百 MB，2026-11-25 内存紧）
            nb = pm.width() * pm.height() * 4
            if self._mem_bytes + nb > MEM_MB * 1048576:
                self._mem.clear()
                self._mem_bytes = 0
            self._mem[key] = pm
            self._mem_bytes += nb
            return pm
        self.request(e)
        return None

    def request(self, ean):
        e = norm_ean(ean)
        if not e or not self.enabled or self.has_cached(e) or self.known_missing(e):
            return
        with self._lock:
            if e in self._pending or e in self._failed:
                return
            self._pending.add(e)
        self._pool.submit(self._fetch, e)

    # ---------- 后台下载 ----------
    def _http(self):
        if self._session is None:
            import requests
            s = requests.Session()
            s.headers["User-Agent"] = UA                  # 像浏览器一样（有的服务器 / 防火墙拒绝不认识的程序）
            s.headers["Accept"] = "image/webp,image/*,*/*;q=0.8"
            self._session = s
        return self._session

    def _fetch(self, ean):
        ok, missing = False, False
        try:
            for name in candidates(ean):
                r = self._http().get(self.url.format(ean=name), timeout=8)
                if r.status_code == 200 and r.content:
                    if not QImage.fromData(r.content).isNull():
                        dst, data = self._raw(ean), r.content          # Qt 能直接读：原样存（原图、最小）
                    else:
                        img = _decode(r.content)                       # 只能用 Pillow 读：转成原尺寸 PNG
                        if img is None:
                            break
                        dst, data = self._png(ean), None
                    tmp = dst + ".tmp"
                    if data is not None:
                        with open(tmp, "wb") as f:
                            f.write(data)
                    else:
                        img.save(tmp, "PNG")
                    os.replace(tmp, dst)
                    ok = True
                    break
                elif r.status_code == 404:                    # 真没有：记下来，一天内不再问
                    missing = True
                    continue
                elif r.status_code in (403, 429) or r.status_code >= 500:
                    missing = False                            # 服务器拒绝 / 限流 / 出错：不记，下次打开再试
                    break
                else:
                    break
            if not ok and missing:
                with open(self._miss(ean), "w") as f:
                    f.write("")
        except Exception:
            pass
        finally:
            with self._lock:
                self._pending.discard(ean)
                if not ok:
                    self._failed.add(ean)
        if ok:
            self.ready.emit(ean)


_STORE = None


def store(settings=None):
    """全程序共用一个（第一次用的时候按设置建）"""
    global _STORE
    if _STORE is None:
        from settings import app_dir
        s = settings or {}
        _STORE = ImageStore(os.path.join(app_dir(), "cache", "images"), s.get("image_url") or DEFAULT_URL,
                            bool(s.get("show_images", True)))
    elif settings is not None:
        _STORE.enabled = bool(settings.get("show_images", True))
    return _STORE


class ThumbDelegate(QStyledItemDelegate):
    """
    表格里在格子左边画一张小图（条码取自 ean_col 那一列）。只画看得见的行，所以只下载看得见的商品的图。
    鼠标停在小图上弹出大图（HOVER）。size 不给 = 按设置的图片大小。
    """

    def __init__(self, view, ean_col, size=None, settings=None):
        super().__init__(view)
        self.view, self.ean_col, self.size = view, ean_col, size or thumb_size(settings)
        self.store = store(settings)
        self.store.ready.connect(self._ready)

    def _ready(self, _ean):
        try:
            self.view.viewport().update()
        except RuntimeError:            # 表格已经关掉
            pass

    def paint(self, p, opt, idx):
        ean = self._ean(idx)
        w = opt.widget
        dpr = w.devicePixelRatioF() if w is not None and hasattr(w, "devicePixelRatioF") else None
        pm = self.store.pixmap(ean, self.size, dpr) if self.store.enabled else None
        if not self.store.enabled or not norm_ean(ean):
            return super().paint(p, opt, idx)
        r = opt.rect
        box = QRect(r.left() + 4, r.top() + (r.height() - self.size) // 2, self.size, self.size)
        bg = QStyleOptionViewItem(opt)
        self.initStyleOption(bg, idx)
        w = opt.widget
        (w.style() if w else QApplication.style()).drawPrimitive(QStyle.PE_PanelItemViewItem, bg, p, w)   # 整格背景
        if pm is not None:
            pw, ph = pm.width() / pm.devicePixelRatio(), pm.height() / pm.devicePixelRatio()
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawPixmap(int(box.left() + (self.size - pw) / 2), int(box.top() + (self.size - ph) / 2), pm)
        else:
            p.save()
            p.setPen(Qt.NoPen)
            c = QColor(opt.palette.mid().color())
            c.setAlpha(40)
            p.setBrush(c)
            p.drawRoundedRect(box, 4, 4)
            p.restore()
        o = QStyleOptionViewItem(opt)
        o.rect = r.adjusted(self.size + 10, 0, 0, 0)
        super().paint(p, o, idx)

    def _ean(self, idx):
        return idx.sibling(idx.row(), self.ean_col).data() if self.ean_col is not None else idx.data()

    def helpEvent(self, ev, view, opt, idx):
        """鼠标停在小图上：弹出大图（只用本机已经下载好的原图）"""
        try:
            e = norm_ean(self._ean(idx))
            p = self.store.path(e) if (self.store.enabled and e) else None
            if p and ev.pos().x() <= opt.rect.left() + self.size + 8:
                img = QImage(p)
                if not img.isNull():
                    k = min(1.0, HOVER / max(img.width(), img.height(), 1))
                    url = "file:///" + p.replace("\\", "/").lstrip("/")
                    html = f'<img src="{url}" width="{int(img.width() * k)}" height="{int(img.height() * k)}">'
                    QToolTip.showText(ev.globalPos(), html, view)
                    return True
        except Exception:
            pass
        return super().helpEvent(ev, view, opt, idx)

    def sizeHint(self, opt, idx):
        s = super().sizeHint(opt, idx)
        return QSize(s.width() + self.size + 10, max(s.height(), self.size + 4))


def add_thumbs(table, name_col, ean_col, settings=None):
    """
    商品列表加小图（用户 11-18「这种列表都放缩略图」）：名称那一格左边画图（条码取 ean_col 那一列），行高跟着图片大小。
    设置里关了商品图片就不加。手动定宽的名称列加宽一张图的宽度
    """
    from PySide6.QtWidgets import QHeaderView
    s = settings or {}
    if not s.get("show_images", True):
        return None
    d = ThumbDelegate(table, ean_col, None, s)
    table.setItemDelegateForColumn(name_col, d)
    table.verticalHeader().setDefaultSectionSize(max(table.verticalHeader().defaultSectionSize(), row_height(s)))
    hh = table.horizontalHeader()
    if hh.sectionResizeMode(name_col) == QHeaderView.Interactive:
        table.setColumnWidth(name_col, table.columnWidth(name_col) + d.size + 10)
    return d


def set_label_image(label, ean, size=150, settings=None):
    """详情栏的大图：有图显示，没图藏起来；图还在下载时，下载完自动补上（只要还是同一个商品）"""
    st = store(settings)
    e = norm_ean(ean)
    label._img_ean = e
    label.clear()                 # 先清掉上一个商品的图（没图的商品不能还显示别人的图）
    if not st.enabled or not e:
        label.hide()
        return

    def show():
        if getattr(label, "_img_ean", None) != e:
            return
        pm = st.pixmap(e, size)
        if pm is not None:
            label.setPixmap(pm)
            label.show()
        else:
            label.clear()
            label.hide()
    pm = st.pixmap(e, size)
    if pm is not None:
        label.setPixmap(pm)
        label.show()
        return
    label.hide()
    if not getattr(label, "_img_hooked", False):
        label._img_hooked = True
        def relay(got, lb=label):
            try:
                getattr(lb, "_img_cb", lambda g: None)(got)
            except RuntimeError:          # 窗口已经关掉
                pass
        st.ready.connect(relay)
    label._img_cb = lambda got: show() if got == e else None
    if not getattr(label, "_img_click", False):        # 点一下放大看（原图，最大 600）
        label._img_click = True
        from i18n import tr
        label.setCursor(Qt.PointingHandCursor)
        label.setToolTip(tr("点一下放大"))
        label.mousePressEvent = lambda ev, lb=label: preview(lb, getattr(lb, "_img_ean", None), settings)


def preview(parent, ean, settings=None):
    """放大看商品图（按原图，最大 PREVIEW 像素，高分屏也清楚）"""
    from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel
    st = store(settings)
    p = st.path(norm_ean(ean)) if ean else None
    if not p:
        return
    img = QImage(p)
    if img.isNull():
        return
    side = min(PREVIEW, max(img.width(), img.height()))
    pm = st.pixmap(ean, side, parent.devicePixelRatioF() if hasattr(parent, "devicePixelRatioF") else None)
    dlg = QDialog(parent.window() if hasattr(parent, "window") else None)
    dlg.setWindowTitle(str(ean))
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(12, 12, 12, 12)
    lb = QLabel()
    lb.setAlignment(Qt.AlignCenter)
    lb.setPixmap(pm)
    lay.addWidget(lb)
    lb.mousePressEvent = lambda ev: dlg.accept()
    dlg.exec()
