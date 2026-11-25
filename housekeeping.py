"""
自动清理多余的缓存和备份（2026-11-25 用户：C 盘满了，查哪些是多余的、可以清理的）。不依赖界面。

只清理程序自己生成、以后用不到或者能重新生成的东西，**不碰门店清单 / 报价 / 订单文件 / 比价结果本身**：
· cache/quotes：读过的报价结果。报价每周换、每家门店文件夹一份，旧版本越积越多 → 30 天没用过的删掉
  （用到时 service 会把文件「摸一下」更新修改时间，所以还在用的不会被删）；
· cache/images：商品图片原图。超过 MAX_IMAGES_MB 就从最久没用过的删起（要用时会重新下载）；
· 比价结果里每家门店的 _备份/时间戳：每次比价 / 修改分配都整套复制一份供应商文件（一周四五十份），
  只留最近 KEEP_RUNS 份；_备份/门店库存 的快照、ORDINE/_旧订单 也只留最近几份；
· 临时文件夹里程序留下的 casate_* / piano_* / scanner_lists_*（超过一天的）；
· cache/mail_att（下载过的邮件附件）、cache/mail_prices_failed 60 天以前的。

每天最多做一次（cache/housekeeping.json 记日期），在后台线程里做；删了多少写进日志。
"""
import json
import os
import shutil
import tempfile
import time
from datetime import date

DAY = 86400
QUOTE_DAYS = 30
MAX_IMAGES_MB = 600
KEEP_RUNS = 3              # 每家门店的 _备份/时间戳 留几份
KEEP_SNAPSHOTS = 3         # _备份/门店库存 留几份
KEEP_OLD_ORDERS = 5        # ORDINE/_旧订单 留几份
MAIL_DAYS = 60
TEMP_PREFIXES = ("casate_", "piano_", "scanner_lists_")


def touch(path):
    """缓存用到了：更新修改时间，清理时就知道它还在用"""
    try:
        os.utime(path, None)
    except OSError:
        pass


def _size(p):
    if os.path.isfile(p):
        try:
            return os.path.getsize(p)
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _remove(p, stats, what):
    n = _size(p)
    try:
        if os.path.isdir(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
    except OSError:
        return
    stats[what] = stats.get(what, 0) + n
    stats["_n"] = stats.get("_n", 0) + 1


def prune_old_files(folder, days, stats, what, now=None):
    """文件夹里修改时间早于 days 天的文件删掉"""
    now = now or time.time()
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        p = os.path.join(folder, name)
        try:
            old = now - os.path.getmtime(p) > days * DAY
        except OSError:
            continue
        if old:
            _remove(p, stats, what)


def prune_to_size(folder, max_bytes, stats, what):
    """文件夹超过 max_bytes：从最久没动过的删起，删到 90% 以下（.none 标记不算、不删）"""
    files = []
    try:
        with os.scandir(folder) as it:
            for e in it:
                if e.is_file() and not e.name.endswith(".none") and not e.name.startswith("."):
                    st = e.stat()
                    files.append((st.st_mtime, st.st_size, e.path))
    except OSError:
        return
    total = sum(s for _m, s, _p in files)
    if total <= max_bytes:
        return
    for _m, s, p in sorted(files):
        if total <= max_bytes * 0.9:
            break
        _remove(p, stats, what)
        total -= s


def keep_newest(folder, keep, stats, what, dirs=None, suffix=None):
    """只留最新的 keep 个（按名字里的时间排，名字一样按修改时间）；dirs=True 只看文件夹，False 只看文件"""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    items = []
    for name in names:
        p = os.path.join(folder, name)
        if dirs is True and not os.path.isdir(p) or dirs is False and not os.path.isfile(p):
            continue
        if suffix and not name.lower().endswith(suffix):
            continue
        try:
            items.append((os.path.getmtime(p), name, p))
        except OSError:
            continue
    items.sort(key=lambda x: (x[0], x[1]), reverse=True)
    for _m, _n, p in items[keep:]:
        _remove(p, stats, what)


def _is_stamp(name):
    """_备份 里每次比价那份：20261122_172316"""
    return len(name) == 15 and name[8] == "_" and name.replace("_", "").isdigit()


def prune_result_backups(results_root, stats):
    """4 比价结果/日期/门店/_备份：时间戳文件夹只留最近 KEEP_RUNS 份，门店库存快照 / 旧订单只留最近几份"""
    if not results_root or not os.path.isdir(results_root):
        return
    for root, dirs, _files in os.walk(results_root):
        if os.path.basename(root) != "_备份":
            continue
        stamps = sorted((d for d in dirs if _is_stamp(d)), reverse=True)
        for d in stamps[KEEP_RUNS:]:
            _remove(os.path.join(root, d), stats, "比价备份")
        keep_newest(os.path.join(root, "门店库存"), KEEP_SNAPSHOTS, stats, "比价备份", dirs=False, suffix=".json")
        keep_newest(os.path.join(os.path.dirname(root), "ORDINE", "_旧订单"), KEEP_OLD_ORDERS, stats, "比价备份", dirs=False)
        dirs[:] = []                  # _备份 里面不用再往下找


def prune_temp(stats, now=None, tmp=None):
    now = now or time.time()
    tmp = tmp or tempfile.gettempdir()
    try:
        names = os.listdir(tmp)
    except OSError:
        return
    for name in names:
        if not name.startswith(TEMP_PREFIXES):
            continue
        p = os.path.join(tmp, name)
        try:
            if now - os.path.getmtime(p) > DAY:
                _remove(p, stats, "临时文件")
        except OSError:
            pass


def due(state_path, today=None):
    today = (today or date.today()).isoformat()
    try:
        with open(state_path, encoding="utf-8") as f:
            return json.load(f).get("day") != today
    except (OSError, ValueError):
        return True


def mark(state_path, stats, today=None):
    try:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"day": (today or date.today()).isoformat(), "freed": {k: v for k, v in stats.items()}}, f)
    except OSError:
        pass


def run(app_dir, results_root=None, force=False, log=None):
    """
    清理一次 → {种类: 删掉的字节数, "_n": 删了几个}。force=False 时一天只做一次。
    results_root = DETERSIVO 工作文件夹的「4 比价结果」（没有就不清理比价备份）
    """
    cache = os.path.join(app_dir, "cache")
    state = os.path.join(cache, "housekeeping.json")
    if not force and not due(state):
        return None
    stats = {}
    prune_old_files(os.path.join(cache, "quotes"), QUOTE_DAYS, stats, "报价缓存")
    prune_to_size(os.path.join(cache, "images"), MAX_IMAGES_MB * 1024 * 1024, stats, "商品图片")
    for sub in ("mail_att", "mail_prices_failed", "mail_prices_undo"):
        prune_old_files(os.path.join(cache, sub), MAIL_DAYS, stats, "邮件附件")
    prune_result_backups(results_root, stats)
    prune_temp(stats)
    mark(state, stats)
    if log and stats.get("_n"):
        log(text(stats))
    return stats


def text(stats):
    parts = [f"{k} {v / 1048576:,.0f} MB" for k, v in sorted(stats.items(), key=lambda kv: -kv[1] if kv[0] != "_n" else 0)
             if k != "_n" and v >= 1048576]
    total = sum(v for k, v in stats.items() if k != "_n")
    return f"自动清理：删了 {stats.get('_n', 0)} 个多余的缓存 / 备份，空出 {total / 1048576:,.0f} MB" + \
        (f"（{'、'.join(parts)}）" if parts else "")


def rotate_log(path, max_bytes=10 * 1024 * 1024):
    """日志文件超过 max_bytes：改名成 .old（旧的 .old 盖掉），新开一个。要在打开日志之前调用"""
    try:
        if os.path.getsize(path) > max_bytes:
            os.replace(path, path + ".old")
    except OSError:
        pass
