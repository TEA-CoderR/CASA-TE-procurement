"""
邮件里的批发商报价（2026-11-07 用户同意下一步：Meloni / Petrillo / New Grieco 的价目表一来就提示放进「2 供应商报价」）。不依赖界面。

摸底看到的（10 – 11 月）：
  MELONI    alessandro.gianchecchi78@gmail.com「Fwd: Listino 1 con offerta」/「Meloni listino」→ Listino_01 - Offerta.xlsx（每周四）
  NEW GRIECO andrea.comegna2@gmail.com「AGGIORNAMENTO LISTINO NEW GRIECO …」→ Canvass 46 New Grieco.xls（每周四晚）
  PETRILLO  francesco.fratellirana@gmail.com「*** Listini F.lli Petrillo Dal #/# - Al #/# ***」→ .xls
  MEGA CASH 邮件里没有（自己下载）
怎么认：批发商 = 「2 供应商报价」里现在那几个文件的供应商名称（supplier_names.label_of）。
一封收到的信算某家的报价：有 Excel 附件，并且 ① 主题 / 附件名 / 发件人名字里有这家的名字，或 ② 发件人是这家的人
（我们发这家的订单时发给过他，或者他发过主题里写这家名字的信），再加上主题 / 附件名像价目表（listino / canvass / offert / prezzi）。
每家只看最新的一封；比现在文件夹里那份新（发信时间晚于文件放进来的时间）、也没放过（cache 记着 Message-ID）才提示。
"""
import os
import re
from datetime import datetime

import mail_match as MM

_PRICE_WORDS = re.compile(r"listin|canvass|offert|prezz|price ?list|quotazion", re.I)
_EXCEL = (".xlsx", ".xls", ".xlsm")
_GENERIC = {"NEW", "CASH", "SRL", "SPA", "FLLI", "FRATELLI", "GROUP", "ITALIA", "SNC", "SAS"}


def label_words(label):
    """供应商名称 → 认它的词：MEGA CASH → {MEGA}，NEW GRIECO → {GRIECO}，F.LLI PETRILLO → {PETRILLO}"""
    ws = {w for w in MM.words(label) if len(w) >= 4 and w not in _GENERIC}
    return ws or set(MM.words(label))


def current_files(settings, price_dir):
    """「2 供应商报价」里现在的报价：{名称: (文件名, 放进来的时间)}"""
    import supplier_names as SN
    import workspace as W
    out = {}
    try:
        names = [f for f in os.listdir(price_dir) if f.lower().endswith(_EXCEL) and W.is_price_name(f)
                 and not f.startswith("~$")]
    except OSError:
        return out
    for f in names:
        try:
            t = datetime.fromtimestamp(os.path.getmtime(os.path.join(price_dir, f)))
        except OSError:
            t = None
        lab = SN.label_of(f, settings)
        if lab not in out or (t and (out[lab][1] is None or t > out[lab][1])):
            out[lab] = (f, t)
    return out


def _hits(text, labels):
    ws = set(MM.words(text))
    j = "".join(MM.words(text))
    return [lab for lab, lw in labels.items() if any(w in ws or (len(w) >= 6 and w in j) for w in lw)]


def find(mails, labels, own, done=(), current=None):
    """
    mails = mail_client.collect 的行；labels = 供应商名称列表（现在报价文件的名称）；own = 自己的邮箱；
    done = 已经放过的 Message-ID；current = current_files 的结果（判断是不是比现在的新）。
    返回 [{"label", "mail", "part", "current", "current_time", "newer"}]（每家最新一封；没找到的不在里面）
    """
    own = {a.lower() for a in own}
    lw = {lab: label_words(lab) for lab in labels}
    current = current or {}
    # 发件人是谁家的：我们发订单给他（收件人），或者他的信主题里写着某一家
    who = {}
    for m in mails:
        frm = (m.get("from") or "").lower()
        text = " ".join([m.get("subject") or ""] + list(m.get("attachments") or []))
        hit = _hits(text, lw)
        if len(hit) != 1:
            continue
        if frm in own:
            for a in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", m.get("to") or ""):
                if a.lower() not in own:
                    who.setdefault(MM.sender_key(a.lower()), set()).add(hit[0])
        else:
            who.setdefault(MM.sender_key(frm), set()).add(hit[0])
    best = {}
    for m in mails:
        frm = (m.get("from") or "").lower()
        if frm in own or not m.get("date"):
            continue
        parts = [p for p in (m.get("parts") or []) if str(p[1]).lower().endswith(_EXCEL)]
        if not parts:
            continue
        subj = MM.clean_subject(m.get("subject") or "")
        names = " ".join(p[1] for p in parts)
        if not _PRICE_WORDS.search(subj + " " + names):
            continue
        hit = _hits(" ".join([subj, names, m.get("from_name") or ""]), lw)
        if len(hit) != 1:
            mine = who.get(MM.sender_key(frm)) or set()
            hit = list(mine) if len(mine) == 1 else []
        if len(hit) != 1:
            continue
        lab = hit[0]
        # 几个 Excel 附件：名字像价目表的优先，再按大小
        part = max(parts, key=lambda p: (bool(_PRICE_WORDS.search(p[1])) or any(w in MM.words(p[1]) for w in lw[lab]),
                                         int(p[3] or 0)))
        if lab not in best or m["date"] > best[lab]["mail"]["date"]:
            best[lab] = {"label": lab, "mail": m, "part": part}
    out = []
    for lab, b in sorted(best.items()):
        cur = current.get(lab) or (None, None)
        b["current"], b["current_time"] = cur
        mid = (b["mail"].get("message_id") or "").strip()
        # 比现在那份新：信比文件放进来晚（文件是之后手动放的就算已经有了）；放过的不再提
        b["newer"] = mid not in set(done) and (cur[1] is None or b["mail"]["date"] > cur[1])
        out.append(b)
    return out


def file_name(b):
    """存成什么名字：附件原名（不带路径、去掉不能用的字符）"""
    n = re.sub(r'[\\/:*?"<>|\r\n]+', "_", str(b["part"][1])).strip()
    return n or f"{b['label']}.xlsx"
