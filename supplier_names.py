"""
批发商比价：给每个供应商报价文件一个固定的「供应商名称」。

供应商文件名每周都在变（比如 MEGA 的文件叫 formattato_059603(2).xlsx，里面根本没有 mega 这几个字），
所以「排除 MEGA」这类方案、起订规则只按文件名找就会找不到。现在每个文件都有一个名称：
  1. settings.json → supplier_names 里用户起的名字（{文件名: 名称}）
  2. 起订规则：规则的关键词出现在文件名里 → 用规则的供应商名称
  3. 文件名里有常见供应商的名字（mega / meloni / grieco / petrillo）→ 用这个名字
  4. 都没有 → 文件名（去掉扩展名）
拖入新文件替换旧文件时，名字跟着带到新文件上（carry_over）。
方案排除、起订规则都同时按「文件名」和「名称」找。
"""
import os

# 文件名里出现这些字 → 猜是这家
KNOWN = [("mega", "MEGA CASH"), ("meloni", "MELONI"), ("grieco", "NEW GRIECO"), ("petrillo", "PETRILLO")]


def stem(name):
    s = os.path.basename(str(name or ""))
    for ext in (".xlsx", ".xls"):
        if s.lower().endswith(ext):
            s = s[: -len(ext)]
    return s


def label_of(header, settings):
    """一个供应商文件的名称"""
    names = (settings or {}).get("supplier_names") or {}
    if names.get(header):
        return str(names[header]).strip()
    low = str(header or "").lower()
    for r in (settings or {}).get("rules") or []:
        kw = str(r.get("keyword") or "").strip().lower()
        if kw and kw in low and str(r.get("name") or "").strip():
            return str(r["name"]).strip()
    for kw, nm in KNOWN:
        if kw in low:
            return nm
    return stem(header)


def labels(headers, settings):
    return {h: label_of(h, settings) for h in headers if h}


def guessed(header, settings):
    """名称是不是用户自己起的（False = 程序猜的）"""
    return not ((settings or {}).get("supplier_names") or {}).get(header)


def hits(keyword, header, label=None):
    """关键词出现在文件名或名称里（不分大小写）"""
    kw = str(keyword or "").strip().lower()
    if not kw:
        return False
    return kw in str(header or "").lower() or (label is not None and kw in str(label).lower())


def carry_over(settings, done, old_labels):
    """
    拖入新文件替换旧文件以后：旧文件的名称带到新文件上。
    done = [(新文件名, 被替换的旧文件名或 None)]，old_labels = 替换前 {旧文件名: 名称}
    返回 settings 有没有改动
    """
    names = settings.setdefault("supplier_names", {})
    changed = False
    for new, old in done:
        if old and old != new:
            nm = names.get(old) or old_labels.get(old)
            if nm and stem(nm) != stem(old) and names.get(new) != nm:
                names[new] = nm
                changed = True
            if old in names:
                names.pop(old, None)
                changed = True
    return changed


# ---------------------------------------------------------------------
# 拖入报价时先选好供应商（2026-11-07 用户：程序常常认不出是哪家，起订规则也对不上文件 → 放进去的时候就选好）
# ---------------------------------------------------------------------
def recognized(header, settings):
    """程序认得出这个文件是哪家（用户起过名字 / 起订规则关键词 / 常见批发商名字），不是只拿文件名凑数"""
    return label_of(header, settings) != stem(header)


def choices(settings, existing=()):
    """下拉框里的供应商名称：现在报价文件的名称 + 起订规则的供应商名称 + 常见批发商，去重、按字母排"""
    seen, out = set(), []
    names = [label_of(h, settings) for h in existing if recognized(h, settings)]
    names += [str(r.get("name") or "").strip() for r in (settings or {}).get("rules") or []]
    names += [nm for _, nm in KNOWN]
    for nm in names:
        k = nm.casefold()
        if nm and k not in seen:
            seen.add(k)
            out.append(nm)
    return sorted(out, key=str.casefold)


def file_for(name, labels, skip=()):
    """现在的报价里名称是 name 的文件（替换它）；没有返回 None"""
    k = str(name or "").strip().casefold()
    if not k:
        return None
    for h, lb in (labels or {}).items():
        if h not in skip and str(lb or "").strip().casefold() == k:
            return h
    return None


def apply_names(settings, done, picked):
    """
    放好以后记名称：done = [(新文件名, 被替换的旧文件名或 None)]，picked = {新文件名: 选的供应商名称}。
    名称和文件名（去掉扩展名）一样的不用记。返回 settings 有没有改动
    """
    names = settings.setdefault("supplier_names", {})
    changed = False
    for new, old in done:
        if old and old != new and old in names:
            names.pop(old, None)
            changed = True
        nm = str(picked.get(new) or "").strip()
        if nm and nm != stem(new) and names.get(new) != nm:
            names[new] = nm
            changed = True
    return changed
