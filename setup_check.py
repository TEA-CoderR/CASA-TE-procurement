"""
设置检查（2026-11-21 用户：帮我找一下还有什么是一定要设置的、不设置用不了这个程序的地方）。不依赖界面。

一定要设置（每台电脑一次，不设置程序算不了 / 打不开文件）：
  ① 销售数据库：地址 + 只读账号 + 密码（密码只存在这台电脑，不随发布同步）
  ② 同步文件夹：Synology Drive 同步盘（一般自动找到；总仓采购、门店采购、DETERSIVO、订单跟踪的文件都在里面）
用到某个功能才要（不设置只是那个功能用不了）：
  ③ 公司邮箱密码：收报价、核对供应商回信、发订单邮件
  ④ 预约平台密钥：约卸货、门店扫码枪
  ⑤ 采购助手密钥
其余（算法、起订规则、供应商名称、列宽、订单发件邮箱、卸货人……）发布时随公司统一设置同步过来，不用每台电脑设。
"""
import os

import sync_paths as SP
from i18n import tr


def items(settings, db_ok=None):
    """→ [{key, label, need（必须）, ok, detail, cat（系统设置里哪一类）}]；db_ok = 刚才连数据库的结果（None = 还不知道）"""
    s = settings or {}
    out = []
    d = dict(s.get("db") or {})
    try:
        from fenpei.config import DatabaseConfig as DC          # 空着的用原程序的默认值
        for k, a in (("host", "HOST"), ("user", "USER"), ("password", "PASSWORD")):
            d.setdefault(k, getattr(DC, a, "") or "")
            d[k] = d[k] or getattr(DC, a, "") or ""
    except Exception:
        pass
    try:
        import credentials as CR
        has_pw = bool(CR.get("db.password")) or bool(d.get("password"))
    except Exception:
        has_pw = bool(d.get("password"))
    if db_ok is True:
        db = (True, tr("已连接"))
    elif db_ok is False:
        db = (False, tr("连不上"))
    elif not (d.get("host") and d.get("user")):
        db = (False, tr("还没填地址 / 账号"))
    elif not has_pw:
        db = (False, tr("还没填密码"))
    else:
        db = (True, tr("已填"))
    out.append({"key": "db", "label": tr("销售数据库"), "need": True, "ok": db[0], "detail": db[1], "cat": "conn"})

    st = SP.status(s)
    miss = [tr(x["label"]) for x in st if not x["ok"]]
    if not SP.root(s):
        sync = (False, tr("没找到同步文件夹"))
    elif miss:
        sync = (False, tr("找不到：{0}", "、".join(miss)))
    else:
        sync = (True, SP.root(s))
    out.append({"key": "sync", "label": tr("同步文件夹"), "need": True, "ok": sync[0], "detail": sync[1], "cat": "conn"})

    try:
        import mail_client as MC
        import credentials as CR
        accs = MC.accounts(s)
        n_pw = sum(1 for a in accs if CR.get(MC.cred_name(a["address"])))
        mail = (bool(accs) and n_pw == len(accs), tr("{0} / {1} 个邮箱填了密码", n_pw, len(accs)) if accs else tr("还没加邮箱"))
    except Exception:
        mail = (False, tr("还没加邮箱"))
    out.append({"key": "mail", "label": tr("公司邮箱"), "need": False, "ok": mail[0], "detail": mail[1], "cat": "mail"})
    try:
        import booking_api as BA
        bk = (BA.ready(s), tr("已填密钥") if BA.ready(s) else tr("还没填密钥"))
    except Exception:
        bk = (False, tr("还没填密钥"))
    out.append({"key": "booking", "label": tr("预约平台"), "need": False, "ok": bk[0], "detail": bk[1], "cat": "conn"})
    try:
        import agent as A
        ag = bool(A.api_key(A.provider_of(s)))
    except Exception:
        ag = False
    out.append({"key": "agent", "label": tr("采购助手"), "need": False, "ok": ag, "detail": tr("已填密钥") if ag else tr("还没填密钥"),
                "cat": "agent"})
    return out


def missing_required(settings, db_ok=None):
    return [x for x in items(settings, db_ok) if x["need"] and not x["ok"]]
