"""
批发商比价：把拖进来（或选进来）的文件放进总文件夹的 ORDINE / eliminare / distribuire。
原来的文件不删，挪进子文件夹（ORDINE/_旧订单、eliminare/_旧文件、distribuire/_旧文件），随时能找回。

  · ORDINE（主订单）：只能有一个文件 → 新的放进去，原来的挪走
  · eliminare（扣减文件）：可以有好几个 → 新的加进去；同名的先把旧的挪走
  · distribuire（供应商报价）：每家一个文件，每周更新 → 按文件名认出是哪家供应商，替换那一家的旧报价；
    认不出（新供应商、名字对不上、对上好几家）的交给界面问用户
"""
import os
import re
import shutil
from datetime import datetime

EXCEL_EXT = (".xlsx", ".xls", ".xlsm")
OLD_DIRS = {"ORDINE": "_旧订单", "eliminare": "_旧文件", "distribuire": "_旧文件"}
# 文件名里常见、但不代表是哪家供应商的词（不参与对应）
_STOP = {"NEW", "SRL", "SRLS", "SPA", "SAS", "SNC", "ITALIA", "ITALY", "GROUP", "LISTINO", "PREZZI", "PREZZO", "OFFERTA",
         "OFFERTE", "LIST", "PRICE", "ORDINE", "ORDER", "CATALOGO", "FILE", "COPIA", "COPY", "NUOVO", "AGGIORNATO",
         "XLSX", "XLS", "CALC", "THE", "AND", "DEL", "DELLA", "PER", "CON"}


def is_excel(path):
    n = os.path.basename(path)
    return n.lower().endswith(EXCEL_EXT) and not n.startswith("~$")


def excel_files(folder):
    """文件夹里的 Excel 文件名（不含子文件夹、临时文件），没有文件夹返回 []"""
    if not os.path.isdir(folder):
        return []
    return sorted(f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f)) and is_excel(f))


def archive(path, old_dir):
    """把文件挪进 old_dir（重名时文件名后加时间），返回新路径"""
    os.makedirs(old_dir, exist_ok=True)
    name = os.path.basename(path)
    dst = os.path.join(old_dir, name)
    if os.path.exists(dst):
        base, ext = os.path.splitext(name)
        dst = os.path.join(old_dir, f"{base}_{datetime.now():%Y%m%d_%H%M%S}{ext}")
    shutil.move(path, dst)
    return dst


def _copy_in(src, folder):
    """复制进 folder；同名的旧文件先挪进旧文件夹。返回 (新路径, 被挪走的旧文件或 None)"""
    sub = os.path.basename(os.path.normpath(folder))
    old_dir = os.path.join(folder, OLD_DIRS.get(sub, "_旧文件"))
    dst = os.path.join(folder, os.path.basename(src))
    if os.path.abspath(src) == os.path.abspath(dst):
        return dst, None                       # 本来就在这里
    moved = archive(dst, old_dir) if os.path.exists(dst) else None
    shutil.copy2(src, dst)
    return dst, moved


def _check_sources(files):
    bad = [f for f in files if not is_excel(f)]
    if bad:
        raise ValueError("只能放 Excel 文件（.xlsx / .xls）：" + "、".join(os.path.basename(f) for f in bad))
    missing = [f for f in files if not os.path.isfile(f)]
    if missing:
        raise ValueError("找不到文件：" + "、".join(missing))


# ---------------------------------------------------------------------
# ORDINE：只留一个主订单
# ---------------------------------------------------------------------
def import_ordine(root, files):
    """返回 {"file": 新主订单路径, "archived": [挪走的旧文件]}"""
    _check_sources(files)
    if len(files) != 1:
        raise ValueError("主订单一次只能放一个文件")
    folder = os.path.join(root, "ORDINE")
    os.makedirs(folder, exist_ok=True)
    src = files[0]
    archived = []
    for f in excel_files(folder):
        p = os.path.join(folder, f)
        if os.path.abspath(p) != os.path.abspath(src):
            archived.append(archive(p, os.path.join(folder, OLD_DIRS["ORDINE"])))
    dst, moved = _copy_in(src, folder)
    if moved:
        archived.append(moved)
    return {"file": dst, "archived": archived}


# ---------------------------------------------------------------------
# eliminare：加进去（同名替换）
# ---------------------------------------------------------------------
def import_eliminare(root, files, replace_all=False):
    """replace_all=True：先把原来的扣减文件都挪走（换成这一批）。返回 {"files": [...], "archived": [...]}"""
    _check_sources(files)
    folder = os.path.join(root, "eliminare")
    os.makedirs(folder, exist_ok=True)
    archived = []
    if replace_all:
        keep = {os.path.abspath(f) for f in files}
        for f in excel_files(folder):
            p = os.path.join(folder, f)
            if os.path.abspath(p) not in keep:
                archived.append(archive(p, os.path.join(folder, OLD_DIRS["eliminare"])))
    out = []
    for src in files:
        dst, moved = _copy_in(src, folder)
        out.append(dst)
        if moved:
            archived.append(moved)
    return {"files": out, "archived": archived}


def remove_file(root, sub, name):
    """界面上点 ×：把文件挪进旧文件夹（不删除）"""
    folder = os.path.join(root, sub)
    return archive(os.path.join(folder, name), os.path.join(folder, OLD_DIRS.get(sub, "_旧文件")))


# ---------------------------------------------------------------------
# distribuire：按供应商替换
# ---------------------------------------------------------------------
def name_tokens(name):
    """文件名里代表供应商的词：MEGA CASH 12-10.xlsx → {MEGA, CASH}"""
    base = os.path.splitext(os.path.basename(name))[0].upper()
    return {t for t in re.findall(r"[A-Z]{3,}", base) if t not in _STOP}


def match_supplier(new_name, existing, keywords=()):
    """
    新报价文件对应 distribuire 里哪个旧文件：返回旧文件名，或 None（新供应商 / 认不出 / 对上好几家）。
    先看起订规则的关键词（mega …）：新文件名里有某个关键词，旧文件里只有一个也有 → 就是它；
    再看文件名里的词：和哪个旧文件共同的词最多（而且只有它最多）就是哪个。
    """
    low = os.path.basename(new_name).lower()
    for kw in keywords or ():
        kw = str(kw or "").strip().lower()
        if kw and kw in low:
            hits = [e for e in existing if kw in e.lower()]
            if len(hits) == 1:
                return hits[0]
    mine = name_tokens(new_name)
    if not mine:
        return None
    scored = sorted(((len(mine & name_tokens(e)), e) for e in existing), reverse=True)
    if not scored or scored[0][0] == 0:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None                                  # 对上好几家，拿不准
    return scored[0][1]


def plan_suppliers(root, files, keywords=()):
    """
    先算好每个新文件要替换哪个旧文件（不动文件），给界面确认：
    返回 [{"src": 新文件, "replace": 旧文件名或 None, "auto": 是否自动认出}]
    同一个旧文件不会被两个新文件替换。
    """
    _check_sources(files)
    existing = [e for e in excel_files(os.path.join(root, "distribuire"))
                if ".calc." not in e.lower() and "缺失报告" not in e and "missing" not in e.lower()]
    used, out = set(), []
    for src in files:
        cand = [e for e in existing if e not in used]
        if os.path.basename(src) in existing:          # 同名：就是替换它
            hit = os.path.basename(src)
        else:
            hit = match_supplier(src, cand, keywords)
        if hit:
            used.add(hit)
        out.append({"src": src, "replace": hit, "auto": hit is not None})
    return out


def apply_suppliers(root, plan):
    """按 plan_suppliers（界面上可能改过 replace）放文件。返回 [(新文件名, 替换掉的旧文件名或 None)]"""
    folder = os.path.join(root, "distribuire")
    os.makedirs(folder, exist_ok=True)
    old_dir = os.path.join(folder, OLD_DIRS["distribuire"])
    done = []
    for p in plan:
        rep = p.get("replace")
        if rep and os.path.basename(p["src"]) != rep and os.path.exists(os.path.join(folder, rep)):
            archive(os.path.join(folder, rep), old_dir)
        dst, _ = _copy_in(p["src"], folder)
        done.append((os.path.basename(dst), rep))
    return done
