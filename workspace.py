"""
批发商比价的工作文件夹：一个固定的根目录，程序自动建好、文件放哪一看就懂（不依赖界面，可以单独测试）。

  根目录\\
    0 Griglia\\                 我们自己的 DETERSIVI 订货总表（放最新的一份，见 griglia.py）
    1 门店清单\\                门店发来的订货清单（文件名里带门店名；认不出的放进来时加上门店名）
        _已处理\\2026-10-20\\    确认生成主订单后挪到这里
    2 供应商报价\\              几家门店共用的批发商报价，每周替换（旧的进 _旧文件）
    3 扣减文件\\FOIANO\\ …       每家门店自己的扣减文件（eliminare）
        FOIANO\\_已用\\2026-10-20\\  确认后挪到这里（下次不会重复扣减）
    4 比价结果\\2026-10-20\\      每次确认自动建：
        调货单_全部门店_….xlsx
        MARLIA\\ORDINE  eliminare  distribuire   ← 这家店这次的主订单、扣减文件、报价副本（比价写在这里）

以前的根目录\\distribuire（共用报价）第一次打开时改名成「2 供应商报价」；以前的「批发商比价\\日期」结果保留不动。
"""
import os
import re
import shutil
from datetime import datetime

GRID = "0 Griglia"           # 我们的 DETERSIVI 总表（griglia.py）
LISTS = "1 门店清单"
PRICES = "2 供应商报价"
ELIM = "3 扣减文件"
RESULTS = "4 比价结果"
DONE = "_已处理"
USED = "_已用"
OLD = "_旧文件"
STORES = {1: "FOIANO", 2: "MARLIA", 3: "S.FILIPPO", 4: "S.MARCO", 5: "S.CONCORDIO"}
EXCEL = (".xlsx", ".xls", ".xlsm")


# 2026-11-21 用户：DETERSIVO 工作文件夹搬进同步盘，几台电脑看同一份（一周一个人做，另一个人能查看）
SHARED_NAME = os.path.join("SynologyDrive", "SynologyDrive", "Ordine DETERSIVI")
DATA = "_程序数据"           # 程序自己记的东西（每台电脑一个文件，读的时候合起来）
BUSY_MIN = 30                # 别的电脑最后一次操作在这么多分钟内 = 他正在做


def find_shared_root(home=None, drives=None):
    """同步盘里的「Ordine DETERSIVI」：D:\\SynologyDrive\\SynologyDrive\\Ordine DETERSIVI（盘符不一定是 D，也可能在用户文件夹下）"""
    home = home or os.path.expanduser("~")
    if drives is None:
        drives = [f"{c}:\\" for c in "DEFGHCIJKLMNOPQRSTUVWXYZ"] if os.name == "nt" else []
    for base in list(drives) + [home]:
        p = os.path.join(base, SHARED_NAME)
        if os.path.isdir(p):
            return os.path.normpath(p)
    return None


def data_dir(root):
    return os.path.join(root, DATA)


def me():
    """这台电脑（文件名里用）"""
    import platform
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", os.environ.get("COMPUTERNAME") or platform.node() or "pc") or "pc"


def mark_working(root, user, what, now=None):
    """这台电脑在做 DETERSIVO（检查清单 / 比价 / 改分配时调）：写 _程序数据/working_<电脑>.json"""
    import json
    now = now or datetime.now()
    d = data_dir(root)
    try:
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "working_%s.json" % me())
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"pc": me(), "user": user or "", "what": what, "at": now.strftime("%Y-%m-%d %H:%M:%S")}, f,
                      ensure_ascii=False)
        os.replace(p + ".tmp", p)
    except OSError:
        pass


def who_works(root, now=None, minutes=BUSY_MIN, mine=None):
    """
    别的电脑谁在做：[(用户, 电脑, 做什么, 最后操作时间)]，最近的在前。
    minutes=None 时不管多久以前（「这周谁做的」用）
    """
    import json
    now = now or datetime.now()
    mine = mine or me()
    out = []
    d = data_dir(root)
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for n in names:
        if not (n.startswith("working_") and n.endswith(".json")) or "conflict" in n.lower():
            continue
        try:
            with open(os.path.join(d, n), encoding="utf-8") as f:
                r = json.load(f)
            at = datetime.strptime(r.get("at") or "", "%Y-%m-%d %H:%M:%S")
        except (OSError, ValueError, TypeError):
            continue
        if r.get("pc") == mine:
            continue
        if minutes is not None and (now - at).total_seconds() > minutes * 60:
            continue
        out.append((r.get("user") or r.get("pc") or "", r.get("pc") or "", r.get("what") or "", at))
    return sorted(out, key=lambda x: x[3], reverse=True)


def latest_result_folder(root):
    """「4 比价结果」里最新一天、有比价结果（_备份/last_allocation.json）的门店文件夹；没有 None"""
    base = os.path.join(root, RESULTS)
    try:
        days = sorted((d for d in os.listdir(base) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)), reverse=True)
    except OSError:
        return None
    for d in days:
        dd = os.path.join(base, d)
        try:
            subs = sorted(os.listdir(dd))
        except OSError:
            continue
        done = [os.path.join(dd, s) for s in subs
                if os.path.isfile(os.path.join(dd, s, "_备份", "last_allocation.json"))]
        if done:
            return max(done, key=lambda x: os.path.getmtime(os.path.join(x, "_备份", "last_allocation.json")))
    return None


def default_root(home=None):
    """第一次用时的根目录：我的文档\\批发商比价"""
    home = home or os.path.expanduser("~")
    docs = os.path.join(home, "Documents")
    return os.path.join(docs if os.path.isdir(docs) else home, "批发商比价")


def lists_dir(root):
    return os.path.join(root, LISTS)


def price_dir(root):
    return os.path.join(root, PRICES)


def elim_dir(root, sid):
    return os.path.join(root, ELIM, STORES.get(int(sid), str(sid)))


def ensure(root):
    """建好所有文件夹；以前的 根目录\\distribuire 改名成「2 供应商报价」。返回新建 / 改名的说明列表"""
    notes = []
    os.makedirs(root, exist_ok=True)
    old = os.path.join(root, "distribuire")
    if os.path.isdir(old) and not os.path.exists(price_dir(root)):
        try:
            os.rename(old, price_dir(root))
            notes.append(f"distribuire → {PRICES}")
        except OSError as e:                      # 文件开着等：先建新的空文件夹，旧的留着
            notes.append(f"distribuire 没能改名（{e}）")
    for d in [os.path.join(root, GRID), lists_dir(root), price_dir(root), os.path.join(root, RESULTS)] + \
            [elim_dir(root, s) for s in STORES]:
        if not os.path.isdir(d):
            os.makedirs(d)
            notes.append(os.path.relpath(d, root))
    return notes


def _excel(folder):
    if not os.path.isdir(folder):
        return []
    return sorted(f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))
                  and f.lower().endswith(EXCEL) and not f.startswith(("~", "$")))


def guess_store(name):
    """文件名里有门店名 → 门店编号（S.MARCO / S MARCO / SMARCO 都认）；没有门店名就看文件名最后的门店编号
    （DETER1 … DETER5，2026-11-15 用户：清单文件名后面都带 1–5）；都没有 → None"""
    low = re.sub(r"[\s._\-]", "", os.path.basename(name or "").upper())
    for sid, n in STORES.items():
        if re.sub(r"[\s._\-]", "", n) in low:
            return sid
    return number_store(name)


def number_store(name):
    """文件名最后的门店编号：「DETER3.xlsx」「Ordine - 3.xlsx」→ 3。前面紧挨着数字的不算（「1810」「05」是日期），
    Windows 复制出来的「 (1)」后缀先去掉；不是 1–5 → None"""
    stem = os.path.splitext(os.path.basename(name or ""))[0]
    stem = re.sub(r"\s*\(\d+\)\s*$", "", stem)
    m = re.search(r"(?<!\d)([1-5])\s*$", stem)
    return int(m.group(1)) if m else None


def is_price_name(name):
    low = name.lower()
    return ".calc." not in low and not low.endswith(".xls.xlsx") and "缺失报告" not in name and "missing" not in low


def scan(root):
    """
    现在放了什么：{"lists": [(门店编号或 None, 文件名)], "prices": [文件名], "elim": {门店编号: [文件名]},
                   "elim_loose": [直接放在「3 扣减文件」下面、不知道是哪家店的]}
    """
    out = {"lists": [], "prices": [], "elim": {}, "elim_loose": []}
    if not root or not os.path.isdir(root):
        return out
    out["lists"] = [(guess_store(f), f) for f in _excel(lists_dir(root))]
    out["prices"] = [f for f in _excel(price_dir(root)) if is_price_name(f)]
    for sid in STORES:
        fs = _excel(elim_dir(root, sid))
        if fs:
            out["elim"][sid] = fs
    out["elim_loose"] = _excel(os.path.join(root, ELIM))
    return out


def archive(path, old_dir):
    """挪进 old_dir（重名时加时间；旧文件夹里已经有一模一样的就不多存一份），返回新路径"""
    import file_inbox as FI
    return FI.archive(path, old_dir)


OLD_NAMES = ("_旧文件", "_旧订单")


def dedupe_old(root, max_depth=4):
    """
    工作文件夹里所有 _旧文件 / _旧订单 文件夹：一模一样的文件只留一份（以前没防重复时留下的）。
    不进 _备份 / .backup（那是每次比价前的完整备份，按时间点留着）。返回 [(文件夹, [删掉的文件名])]
    """
    import file_inbox as FI
    out = []
    if not root or not os.path.isdir(root):
        return out
    base = os.path.abspath(root).rstrip(os.sep).count(os.sep)
    for d, dirs, _files in os.walk(root):
        depth = os.path.abspath(d).count(os.sep) - base
        dirs[:] = [x for x in dirs if x not in ("_备份", ".backup") and depth < max_depth]
        if os.path.basename(d) in OLD_NAMES:
            gone = FI.dedupe(d)
            if gone:
                out.append((d, gone))
    return out


def place_list(root, src, sid):
    """
    门店清单放进「1 门店清单」：文件名里没有门店名的，前面加上（以后一看就知道是哪家）；
    同一家店原来的清单挪进 _旧文件（一家店只留一份）。返回 (新路径, 挪走的旧文件列表)
    """
    folder = lists_dir(root)
    os.makedirs(folder, exist_ok=True)
    name = os.path.basename(src)
    if guess_store(name) != sid:
        name = f"{STORES[int(sid)]} - {name}"
    dst = os.path.join(folder, name)
    moved = []
    for f in _excel(folder):
        p = os.path.join(folder, f)
        if guess_store(f) == sid and os.path.abspath(p) != os.path.abspath(src):
            moved.append(archive(p, os.path.join(folder, OLD)))
    if os.path.abspath(src) != os.path.abspath(dst):
        shutil.copy2(src, dst)
    return dst, moved


def place_elim(root, src, sid):
    """扣减文件放进「3 扣减文件\\门店」：同名的旧文件挪进 _旧文件。返回新路径"""
    folder = elim_dir(root, sid)
    os.makedirs(folder, exist_ok=True)
    dst = os.path.join(folder, os.path.basename(src))
    if os.path.abspath(src) == os.path.abspath(dst):
        return dst
    import file_inbox as FI
    if os.path.exists(dst) and FI.same_content(src, dst):
        return dst                                     # 一模一样的已经在了
    if os.path.exists(dst):
        archive(dst, os.path.join(folder, OLD))
    shutil.copy2(src, dst)
    return dst


def store_list_files(root):
    """{门店编号: 清单路径}（文件名认不出门店的不在里面）"""
    out = {}
    for sid, f in scan(root)["lists"]:
        if sid:
            out[sid] = os.path.join(lists_dir(root), f)
    return out


def copy_elim(root, sid, store_folder):
    """这家店的扣减文件复制进它这次的比价文件夹（eliminare）。返回复制了几个"""
    dst = os.path.join(store_folder, "eliminare")
    os.makedirs(dst, exist_ok=True)
    n = 0
    for f in _excel(elim_dir(root, sid)):
        shutil.copy2(os.path.join(elim_dir(root, sid), f), os.path.join(dst, f))
        n += 1
    return n


def archive_used(root, sids, list_paths=(), day=None):
    """
    确认生成主订单以后：用过的门店清单挪进「1 门店清单\\_已处理\\日期」，这几家店的扣减文件挪进
    「3 扣减文件\\门店\\_已用\\日期」（下次不会再扣一遍）。返回挪了几个文件
    """
    tag = f"{(day or datetime.now()):%Y-%m-%d}"
    n = 0
    for p in list_paths:
        if p and os.path.isfile(p) and os.path.dirname(os.path.abspath(p)) == os.path.abspath(lists_dir(root)):
            archive(p, os.path.join(lists_dir(root), DONE, tag))
            n += 1
    for sid in sids:
        folder = elim_dir(root, sid)
        for f in _excel(folder):
            archive(os.path.join(folder, f), os.path.join(folder, USED, tag))
            n += 1
    return n
