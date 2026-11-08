"""
本机密码保管（2026-11-06 第 0 阶段）：数据库密码、以后的邮箱「第三方客户端安全密码」都放这里。

· 放在每个 Windows 用户自己的文件夹（%APPDATA%\\CASATE\\credentials.json），不在共享文件夹、不进 git、不会被同步盘
  传给别人，也不会被别的电脑的设置覆盖。
· 每个值用 Windows DPAPI 加密（secure_store.protect）：文件拷到别的电脑 / 别的用户下解不开。
· settings.json 里不再写密码：settings.load_settings 把旧的搬过来，save_settings 写文件前拿掉（SECRET_PATHS）。
· 测试 / 开发可以用环境变量 CASATE_CRED_DIR 指定别的文件夹。
"""
import json
import os
import sys
import threading

import secure_store

_LOCK = threading.Lock()


def cred_dir():
    d = os.environ.get("CASATE_CRED_DIR")
    if d:
        return d
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "CASATE")
    return os.path.join(os.path.expanduser("~"), ".config", "casate")


def cred_path():
    return os.path.join(cred_dir(), "credentials.json")


def _read():
    try:
        with open(cred_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(d):
    os.makedirs(cred_dir(), exist_ok=True)
    tmp = cred_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, cred_path())


def get_protected(name):
    """加密后的样子（交给要自己解密的旧代码，例如 fenpei.config.configure）；没有返回 ''"""
    return _read().get(name) or ""


def get(name):
    """明文；没有或解不开（别的电脑加密的）返回 ''"""
    return secure_store.unprotect(get_protected(name))


def set(name, value):
    """保存（明文或已经加密的都行）；空值 = 删掉"""
    with _LOCK:
        d = _read()
        if value:
            v = secure_store.protect(value)
            if d.get(name) == v:
                return
            d[name] = v
        elif name in d:
            d.pop(name)
        else:
            return
        _write(d)


def delete(name):
    set(name, "")


def names():
    """保存了哪些（只列名字，不给值）"""
    return sorted(_read())


# settings.json 里的密码：(设置路径, 保管处名字)
SECRET_PATHS = [(("db", "password"), "db.password")]


def _dig(data, path):
    for k in path[:-1]:
        data = data.get(k) if isinstance(data, dict) else None
    return data if isinstance(data, dict) else None


def load_into(data):
    """读设置之后：文件里还有密码（旧版本）→ 搬进保管处；内存里的设置照旧带着加密后的密码（老代码照常用）。
    返回 True = 文件里有密码要拿掉（调用方存一次设置）"""
    moved = False
    for path, name in SECRET_PATHS:
        box = _dig(data, path)
        if box is None:
            continue
        v = box.get(path[-1])
        if v:
            set(name, v)
            moved = True
        stored = get_protected(name)
        if stored:
            box[path[-1]] = stored
    return moved


def strip_for_file(data):
    """写 settings.json 之前：密码存进保管处，返回拿掉密码的设置（不改 data 本身）"""
    import copy
    out = copy.deepcopy(data)
    for path, name in SECRET_PATHS:
        box = _dig(out, path)
        if box is None or path[-1] not in box:
            continue
        v = box.pop(path[-1])
        if v:
            set(name, v)
    return out
