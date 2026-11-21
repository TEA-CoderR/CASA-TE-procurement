"""
一个同步文件夹管全部（2026-11-21 用户：文件位置是不是只要一个同步文件夹就够了）。

大家用的都是 Synology Drive 同步盘：D:\\SynologyDrive\\SynologyDrive（盘符不一定是 D）。程序要用的文件夹都在它下面、
名字是固定的，所以每台电脑只要知道同步文件夹在哪（自动找，找不到才让人选一次），其余的按固定的相对位置算出来：
  总仓采购   2 Griglia per gli ordini\\04 Griglia per gli ordine Centrale Aquisti   settings store_orders.folder
  门店采购   2 Griglia per gli ordini\\03 Griglia per gli ordini PDV                settings store_pdv.folder
  DETERSIVO  Ordine DETERSIVI                                                       settings wholesale_root
  订单跟踪   2 Griglia per gli ordini\\02 Calendario ordine e Piano acquisti mesile  settings tracking.folder
  订单明细   2 Griglia per gli ordini\\01 RECORD ORDINE                             settings tracking.record
原来的设置键照用（别的代码不用改）：程序每次打开时把「空着 / 文件夹不在了」的那几项按同步文件夹填上；
用户单独选过、而且文件夹在的不动（= 单独设置）。不依赖界面。
"""
import os

SYNC_NAME = os.path.join("SynologyDrive", "SynologyDrive")

# (键, 叫法, 相对同步文件夹的位置, 设置里存在哪)
FOLDERS = (
    ("store_orders", "总仓采购", os.path.join("2 Griglia per gli ordini", "04 Griglia per gli ordine Centrale Aquisti"),
     ("store_orders", "folder")),
    ("store_pdv", "门店采购", os.path.join("2 Griglia per gli ordini", "03 Griglia per gli ordini PDV"), ("store_pdv", "folder")),
    ("deter", "DETERSIVO", "Ordine DETERSIVI", ("wholesale_root",)),
    ("tracking", "订单跟踪", os.path.join("2 Griglia per gli ordini", "02 Calendario ordine e Piano acquisti mesile"),
     ("tracking", "folder")),
    ("record", "订单明细", os.path.join("2 Griglia per gli ordini", "01 RECORD ORDINE"), ("tracking", "record")),
)


def _isdir(p):
    return bool(p) and os.path.isdir(p)


def looks_like_root(p):
    """是同步文件夹：里面至少有一个程序要用的文件夹"""
    return _isdir(p) and any(_isdir(os.path.join(p, rel)) for _k, _l, rel, _s in FOLDERS)


def find_root(home=None, drives=None):
    """自动找同步文件夹：各个盘符下 / 用户文件夹下的 SynologyDrive\\SynologyDrive；找不到 None"""
    home = home or os.path.expanduser("~")
    if drives is None:
        drives = [f"{c}:\\" for c in "DEFGHCIJKLMNOPQRSTUVWXYZ"] if os.name == "nt" else []
    for base in list(drives) + [home]:
        p = os.path.join(base, SYNC_NAME)
        if looks_like_root(p):
            return os.path.normpath(p)
    return None


def _get(settings, path):
    d = settings
    for k in path[:-1]:
        d = (d or {}).get(k) or {}
    return (d or {}).get(path[-1]) or ""


def _set(settings, path, value):
    d = settings
    for k in path[:-1]:
        d = d.setdefault(k, {})
    d[path[-1]] = value


def root(settings):
    r = (settings or {}).get("sync_root") or ""
    return r if _isdir(r) else ""


def derived(root_dir, rel):
    return os.path.normpath(os.path.join(root_dir, rel)) if root_dir else ""


def status(settings):
    """每个文件夹现在用的是哪里：[{key, label, path, derived, own（单独设置的）, ok（文件夹在）}]"""
    r = root(settings)
    out = []
    for key, label, rel, spath in FOLDERS:
        cur = _get(settings, spath)
        d = derived(r, rel)
        own = bool(cur) and (not d or os.path.normcase(os.path.normpath(cur)) != os.path.normcase(d))
        out.append({"key": key, "label": label, "path": cur or d, "derived": d, "own": own, "ok": _isdir(cur or d)})
    return out


def apply(settings, new_root=None, force=False, home=None, drives=None):
    """
    记下同步文件夹（new_root；没给就用设置里的，再没有就自动找），把空着 / 文件夹不在了的几项按它填上。
    force = 换了同步文件夹：原来在旧同步文件夹下面算出来的也换成新的（单独设置在别处的不动）。
    → 改了哪几项 [键]；同步文件夹找不到返回 []（什么都不改）
    """
    old = root(settings)
    r = new_root or old or find_root(home, drives)
    if not _isdir(r):
        return []
    r = os.path.normpath(r)
    changed = []
    if (settings.get("sync_root") or "") != r:
        settings["sync_root"] = r
        changed.append("sync_root")
    for key, _label, rel, spath in FOLDERS:
        cur = _get(settings, spath)
        d = derived(r, rel)
        if not _isdir(d):
            continue
        under_old = bool(cur) and bool(old) and os.path.normcase(os.path.normpath(cur)).startswith(os.path.normcase(old))
        if not _isdir(cur) or (force and under_old):
            if os.path.normcase(os.path.normpath(cur or "")) != os.path.normcase(d):
                _set(settings, spath, d)
                changed.append(key)
    return changed


def set_own(settings, key, path):
    """单独设置一个文件夹（path 空 = 恢复按同步文件夹算）"""
    for k, _label, rel, spath in FOLDERS:
        if k == key:
            _set(settings, spath, os.path.normpath(path) if path else derived(root(settings), rel))
            return True
    return False
