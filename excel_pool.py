"""
Excel COM 实例复用（每条线程一个）

2026-11-18 重写（用户：比价分配点一下就闪退）：以前是几条线程共用的「连接池」，一条线程建的 Excel 对象
会被另一条线程拿去用 —— Excel COM 对象只能在建它的那条线程里用，跨线程用会让程序直接崩（不报错就闪退）；
池满时还会拿着锁等别人还，而还的时候也要这把锁 → 互相等。
现在：每条线程第一次要 Excel 时建一个、以后这条线程一直用它；别的线程拿不到。会用 Excel 的后台活
本来就都排在同一条线程里（erp_widgets.EXCEL_WORKER），所以全程序通常只有一个 Excel。
接口照旧：get_excel() → (excel, 是不是新建的)，release_excel(excel) 不再做事。
"""
import threading
import time

_local = threading.local()
_stats = {"created": 0, "reused": 0}


def _alive(excel):
    try:
        _ = excel.Visible          # Excel 被关掉 / 崩了：这里会出错
        return True
    except Exception:
        return False


def _new():
    import win32com.client as win32
    excel = win32.Dispatch("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    try:
        excel.ScreenUpdating = False
    except Exception:
        pass
    return excel


def get_excel():
    """这条线程的 Excel（没有或已经坏了就新建）→ (excel, is_new)"""
    excel = getattr(_local, "excel", None)
    if excel is not None and _alive(excel):
        _local.used = time.time()
        _stats["reused"] += 1
        return excel, False
    excel = _new()
    _local.excel, _local.used = excel, time.time()
    _stats["created"] += 1
    return excel, True


def release_excel(excel):
    """留给这条线程下次用（以前放回共用的池；现在什么都不用做）"""
    return None


class _Compat:
    """老代码写 excel_pool.acquire() / excel_pool.release(x)"""

    @staticmethod
    def acquire():
        return get_excel()

    @staticmethod
    def release(excel):
        return release_excel(excel)

    @staticmethod
    def get_stats():
        return get_pool_stats()

    @staticmethod
    def close_all():
        close_pool()


excel_pool = _Compat()


def get_pool_stats():
    n = _stats["created"] + _stats["reused"]
    return {"total_created": _stats["created"], "total_reused": _stats["reused"],
            "reuse_rate": _stats["reused"] / max(1, n)}


def close_pool():
    """关掉这条线程的 Excel（只能关自己线程建的）"""
    excel = getattr(_local, "excel", None)
    _local.excel = None
    if excel is not None:
        try:
            excel.Quit()
        except Exception:
            pass
