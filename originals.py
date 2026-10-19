"""
批发商比价：每次处理都从「原文件」开始算，重复点「开始处理」结果一样。

处理会改写文件：主订单按 eliminare 扣减（扣到 0 的行删掉）、按件数订货时 COLLI 改成选中那家的箱数；
供应商报价写进订购数量。以前再点一次处理，就是在已经改过的文件上再扣一次、再算一次，结果每次不同。

做法：
  · 处理前：文件如果还是程序上次写出来的样子（指纹和上次写完记下的一样）→ 用存好的原文件恢复，再从头算；
    如果不一样（换了新文件、自己在 WPS 里改过、第一次处理）→ 把现在这个当作原文件存起来。
  · 处理（或修改分配、换方案）写完后：记下每个文件写完的指纹（存在 last_allocation.json 的 state 里）。
原文件存在 总文件夹/_备份/原文件/ORDINE、…/distribuire 里，同名覆盖（只留最新的原文件）。
"""
import hashlib
import os
import shutil


def fingerprint(path):
    """文件内容的指纹（sha1）；文件不存在返回 None"""
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def store_dir(root, sub):
    return os.path.join(root, "_备份", "原文件", sub)


def prepare(path, root, sub, written):
    """
    处理前调用。written：上次写完记下的 {文件名: 指纹}。
    返回 "restored"（恢复成原文件）/ "saved"（现在这个存成原文件）/ "missing"（文件不存在）
    """
    if not os.path.exists(path):
        return "missing"
    name = os.path.basename(path)
    keep = os.path.join(store_dir(root, sub), name)
    fp = fingerprint(path)
    if written and written.get(name) and written.get(name) == fp and os.path.exists(keep):
        shutil.copy2(keep, path)
        return "restored"
    os.makedirs(os.path.dirname(keep), exist_ok=True)
    shutil.copy2(path, keep)
    return "saved"


def record(paths):
    """写完后调用：{文件名: 指纹}"""
    out = {}
    for p in paths:
        fp = fingerprint(p)
        if fp:
            out[os.path.basename(p)] = fp
    return out
