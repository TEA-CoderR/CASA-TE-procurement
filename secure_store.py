"""
密码 / 令牌加密

· protect / unprotect：Windows DPAPI（CryptProtectData），用当前 Windows 用户的密钥加密。
  加密后的 settings.json 拷到别的电脑或别的用户下解不开 → 需要在那台电脑上重新输一次密码。
  非 Windows（开发、测试）退回到 obf（只是混淆）。
· obf / deobf：固定密钥的混淆，用在必须随程序分发的内容（内置的默认连接、更新只读令牌）。
  只能防止在文件里直接看到明文，懂行的人仍然能还原 —— 真正的保护要靠只读数据库账号 + VPN。
"""
import base64
import os
import sys

_K = b"CASA&TE\x07procurement\x13v3"


def obf(text):
    if not text:
        return ""
    raw = text.encode("utf-8")
    x = bytes(b ^ _K[i % len(_K)] ^ (i * 31 & 0xFF) for i, b in enumerate(raw))
    return "obf:" + base64.urlsafe_b64encode(x[::-1]).decode("ascii")


def deobf(s):
    if not s:
        return ""
    if s.startswith("obf:"):
        x = base64.urlsafe_b64decode(s[4:].encode("ascii"))[::-1]
        return bytes(b ^ _K[i % len(_K)] ^ (i * 31 & 0xFF) for i, b in enumerate(x)).decode("utf-8")
    return s


def _dpapi(data, encrypt):
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    inp = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    ent_raw = b"CASATE-db"
    ebuf = ctypes.create_string_buffer(ent_raw, len(ent_raw))
    ent = BLOB(len(ent_raw), ctypes.cast(ebuf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    if encrypt:
        ok = fn(ctypes.byref(inp), "CASATE", ctypes.byref(ent), None, None, 0x01, ctypes.byref(out))
    else:
        ok = fn(ctypes.byref(inp), None, ctypes.byref(ent), None, None, 0x01, ctypes.byref(out))
    if not ok:
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def protect(text):
    """加密（存到 settings.json 用）"""
    if not text:
        return ""
    if is_protected(text):
        return text
    if sys.platform == "win32":
        try:
            return "dpapi:" + base64.b64encode(_dpapi(text.encode("utf-8"), True)).decode("ascii")
        except Exception:
            pass
    return obf(text)


def unprotect(s):
    """解密；解不开（别的电脑 / 别的用户加密的）返回空字符串"""
    if not s:
        return ""
    if s.startswith("dpapi:"):
        if sys.platform != "win32":
            return ""
        try:
            return _dpapi(base64.b64decode(s[6:]), False).decode("utf-8")
        except Exception:
            return ""
    if s.startswith("obf:"):
        try:
            return deobf(s)
        except Exception:
            return ""
    return s           # 旧版本存的明文


def is_protected(s):
    return isinstance(s, str) and (s.startswith("dpapi:") or s.startswith("obf:"))


def env_or(name, default=""):
    return os.getenv(name) or default
