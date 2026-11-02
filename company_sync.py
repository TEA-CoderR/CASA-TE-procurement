"""
发布更新时同步「公司统一设置」（用户 2026-10-28：每次发布都把我的配置同步给大家，包括列宽这些）。

publish_update.py 打包前调用 sync()：读发布人这台电脑的 settings.json，挑出要统一的部分
（业务规则、算法参数、起订规则、供应商名称、各个表格的列顺序 / 显示哪些列 / 列宽…），
写进 company_defaults.py（会提交、打包）。内容变了就把版本号加 1 → 同事更新后第一次打开时套用一次。

不同步（每台电脑自己的）：文件夹路径、数据库、语言、深浅色、用户名、上次打开的位置、
这次配货选的日子 / 跳过的供应商；任何像路径的文字、名字里带 password / token / secret 的都不要。
不依赖界面，可以单独测试。
"""
import os
import re
import json
import copy

MODULE = "company_defaults.py"

# 顶层不同步的（个人的 / 这台电脑的）
SKIP_TOP = {"last_folder", "theme", "language", "db", "wholesale_root", "wholesale_batch", "user_name", "user_site",
            "company_defaults_ver", "use_web", "window", "geometry", "last_dir"}
# 某一块里面不同步的
SKIP_IN = {"store_orders": {"folder", "day", "skip", "moq_override", "stores", "last_dir"},
           "reorder": {"store", "last_dir", "last_file"},
           "distribution": {"folder", "last_dir", "last_file"},
           "import_page": {"folder", "last_dir", "last_file"},
           # 采购订单跟踪：共享文件夹、Excel、我的名字、上次同步是每台电脑自己的（列顺序 / 宽度照常统一）
           "tracking": {"folder", "excel", "me", "excel_mtime", "synced_at", "last_buyer"}}
_SECRET = re.compile(r"pass|pwd|token|secret|api_?key|credential", re.I)
_PATHLIKE = re.compile(r"^[A-Za-z]:[\\/]|^\\\\|^/(home|Users|mnt|tmp)/")


def _clean(v):
    """去掉像路径的文字、带密码字样的键；空的块不要"""
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if _SECRET.search(str(k)):
                continue
            x = _clean(x)
            if x is _DROP:
                continue
            out[k] = x
        return out
    if isinstance(v, list):
        return [x for x in (_clean(y) for y in v) if x is not _DROP]
    if isinstance(v, str) and _PATHLIKE.search(v):
        return _DROP
    return copy.deepcopy(v)


_DROP = object()


def extract(settings):
    """从一份 settings 里挑出要统一给大家的部分"""
    out = {}
    for k, v in (settings or {}).items():
        if k in SKIP_TOP or _SECRET.search(str(k)):
            continue
        if isinstance(v, dict):
            v = {kk: vv for kk, vv in v.items() if kk not in SKIP_IN.get(k, ())}
            v = _clean(v)
            if not v:
                continue
        else:
            v = _clean(v)
            if v is _DROP:
                continue
        out[k] = v
    return out


def render(defaults, ver, note=""):
    """company_defaults.py 的内容（JSON 写进 Python 字符串，字典顺序固定，方便看改了什么）"""
    body = json.dumps(defaults, ensure_ascii=False, indent=1, sort_keys=True)
    return ('"""\n公司统一设置：由 publish_update.py 发布时从发布人的 settings.json 自动生成（company_sync.py），不要手改。\n'
            '同事更新到新版本后第一次打开时套用一次（settings.apply_company_defaults）。\n"""\n'
            f"import json\n\nCOMPANY_DEFAULTS_VER = {int(ver)}{('          # ' + note) if note else ''}\n\n"
            f"COMPANY_DEFAULTS = json.loads(r'''\n{body}\n''')\n")


def current(path):
    """现在 company_defaults.py 里的 (版本, 内容)；没有返回 (None, None)"""
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, encoding="utf-8") as f:
            src = f.read()
        ver = int(re.search(r"COMPANY_DEFAULTS_VER\s*=\s*(\d+)", src).group(1))
        body = re.search(r"json\.loads\(r'''(.*)'''\)", src, re.S).group(1)
        return ver, json.loads(body)
    except Exception:
        return None, None


def sync(settings_path, module_path, base_ver=0, note=""):
    """
    读 settings_path，内容和 module_path 里的不一样就重写、版本 + 1。
    返回 (改了没有, 新版本号)。base_ver = settings.py 里写死的版本（module 还没有时从它往上加）
    """
    with open(settings_path, encoding="utf-8") as f:
        data = json.load(f)
    new = extract(data)
    ver, old = current(module_path)
    if old == new:
        return False, ver
    ver = max(ver or 0, int(base_ver or 0)) + 1
    with open(module_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(render(new, ver, note))
    return True, ver
