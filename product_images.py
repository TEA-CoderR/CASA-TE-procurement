"""
商品图片（2026-10-25）：按条码到公司图片服务器取主图
  https://img.casaete.top/prodotti/<条码>/01.webp   （有这个条码的文件夹就有图，只取第一张）

做法（不卡界面、不重复下载）：
  · 表格只在画到那一行时才要图（看得见的才下载），后台 4 个线程下载，下载完通知表格重画那一格；
  · 下载下来的图缩成最长边 256 的 PNG 存在程序目录 cache/images/ 里，下次打开直接读本地文件，不再联网；
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
from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOptionViewItem, QApplication

DEFAULT_URL = "https://img.casaete.top/prodotti/{ean}/01.webp"
MISS_DAYS = 7          # 服务器上没有图：几天以后再问一次
MAX_SIDE = 256         # 存到本地的图缩到多大（详情栏最大显示 160）


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
        self.url = url or DEFAULT_URL
        self.enabled = enabled
        os.makedirs(self.dir, exist_ok=True)
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="img")
        self._pending, self._failed = set(), set()
        self._lock = threading.Lock()
        self._mem = {}             # (条码, 尺寸) → QPixmap
        self._session = None

    # ---------- 本地缓存 ----------
    def _png(self, ean):
        return os.path.join(self.dir, f"{ean}.png")

    def _miss(self, ean):
        return os.path.join(self.dir, f"{ean}.none")

    def has_cached(self, ean):
        return os.path.exists(self._png(ean))

    def known_missing(self, ean):
        p = self._miss(ean)
        try:
            return os.path.exists(p) and time.time() - os.path.getmtime(p) < MISS_DAYS * 86400
        except OSError:
            return False

    # ---------- 给界面用 ----------
    def pixmap(self, ean, size):
        """有图返回缩好的 QPixmap；还没有返回 None（同时在后台去取，取到后发 ready）"""
        e = norm_ean(ean)
        if not e or not self.enabled:
            return None
        key = (e, size)
        pm = self._mem.get(key)
        if pm is not None:
            return pm
        if self.has_cached(e):
            pm = QPixmap(self._png(e))
            if pm.isNull():
                return None
            pm = pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            if len(self._mem) > 3000:
                self._mem.clear()
            self._mem[key] = pm
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
            s.headers["User-Agent"] = "CasaTe-Procurement"
            self._session = s
        return self._session

    def _fetch(self, ean):
        ok, missing = False, False
        try:
            for name in candidates(ean):
                r = self._http().get(self.url.format(ean=name), timeout=8)
                if r.status_code == 200 and r.content:
                    img = _decode(r.content)
                    if img is not None:
                        if max(img.width(), img.height()) > MAX_SIDE:
                            img = img.scaled(MAX_SIDE, MAX_SIDE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        tmp = self._png(ean) + ".tmp"
                        img.save(tmp, "PNG")
                        os.replace(tmp, self._png(ean))
                        ok = True
                        break
                elif r.status_code in (403, 404):
                    missing = True
                    continue
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
    """

    def __init__(self, view, ean_col, size=26, settings=None):
        super().__init__(view)
        self.view, self.ean_col, self.size = view, ean_col, size
        self.store = store(settings)
        self.store.ready.connect(self._ready)

    def _ready(self, _ean):
        try:
            self.view.viewport().update()
        except RuntimeError:            # 表格已经关掉
            pass

    def paint(self, p, opt, idx):
        ean = idx.sibling(idx.row(), self.ean_col).data() if self.ean_col is not None else idx.data()
        pm = self.store.pixmap(ean, self.size) if self.store.enabled else None
        if not self.store.enabled or not norm_ean(ean):
            return super().paint(p, opt, idx)
        r = opt.rect
        box = QRect(r.left() + 4, r.top() + (r.height() - self.size) // 2, self.size, self.size)
        bg = QStyleOptionViewItem(opt)
        self.initStyleOption(bg, idx)
        w = opt.widget
        (w.style() if w else QApplication.style()).drawPrimitive(QStyle.PE_PanelItemViewItem, bg, p, w)   # 整格背景
        if pm is not None:
            x = box.left() + (self.size - pm.width()) // 2
            y = box.top() + (self.size - pm.height()) // 2
            p.drawPixmap(x, y, pm)
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

    def sizeHint(self, opt, idx):
        s = super().sizeHint(opt, idx)
        return QSize(s.width() + self.size + 10, max(s.height(), self.size + 4))


def set_label_image(label, ean, size=150, settings=None):
    """详情栏的大图：有图显示，没图藏起来；图还在下载时，下载完自动补上（只要还是同一个商品）"""
    st = store(settings)
    e = norm_ean(ean)
    label._img_ean = e
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
