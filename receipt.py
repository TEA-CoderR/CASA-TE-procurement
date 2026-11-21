"""
确认收货登记（2026-11-21 用户：确认收货要弹出内容编辑窗口，写明是谁卸的货、到了什么；拒收要选原因。
出问题找不到货时，能查到是谁、哪天、收了什么）。不依赖界面。

一次收货 = 订单上 receipts 列表里的一条：
  {"at": "2026-11-21 10:30", "by": 卸货人, "pc": 哪台电脑点的, "result": full / partial / refused,
   "ddt": 送货单号, "reason": 拒收原因, "note": 说明, "lines": [{ean, code, desc, ordered, got}]}
订单状态跟着改：全部收到 → 到货（arrived = 那天）；部分收到 → 部分到货（还算在途）；
拒收 → 卡住了（备注写「拒收 · 原因」，还算在途、等供应商重新送）。
登记了就不能改，只能再登记一次「更正」（同样记一条，旧的留着）。
"""
from datetime import datetime

RESULTS = ("full", "partial", "refused")
REFUSE_REASONS = ("没有预约", "超过预约时间", "包装破损", "货不对", "数量不对", "没有送货单（DDT）", "其他")
DEFAULT_PEOPLE = ("Ronaldo", "Lucas")


def people(settings):
    """卸货人名单（设置里 receiving.people，随发布同步给大家）"""
    xs = [str(x).strip() for x in ((settings or {}).get("receiving") or {}).get("people") or [] if str(x).strip()]
    return xs or list(DEFAULT_PEOPLE)


def result_of(lines, refused=False):
    """按商品明细判断：拒收 / 每行都收齐 = 全部收到 / 有少的 = 部分收到"""
    if refused:
        return "refused"
    for x in lines or []:
        if float(x.get("got") or 0) < float(x.get("ordered") or 0):
            return "partial"
    return "full"


def check(rec):
    """登记前检查：返回要补的那一句，没问题返回 None"""
    if not str(rec.get("by") or "").strip():
        return "请选卸货人"
    if rec.get("result") not in RESULTS:
        return "请选收货结果"
    if rec["result"] == "refused":
        if not rec.get("reason"):
            return "请选拒收原因"
        if rec["reason"] == "其他" and not str(rec.get("note") or "").strip():
            return "拒收原因选了「其他」，请写说明"
    if rec["result"] == "partial" and not rec.get("lines") and not str(rec.get("note") or "").strip():
        return "部分收货：请在明细里改收到的箱数，或写说明缺了什么"
    return None


def changes(order, rec, pc=""):
    """这次收货要改订单的哪些字段（写进订单跟踪）"""
    at = rec.get("at") or datetime.now()
    if isinstance(at, str):
        at = datetime.strptime(at[:16], "%Y-%m-%d %H:%M")
    entry = {"at": at.strftime("%Y-%m-%d %H:%M"), "by": str(rec["by"]).strip(), "pc": pc,
             "result": rec["result"], "ddt": str(rec.get("ddt") or "").strip(),
             "reason": rec.get("reason") or "", "note": str(rec.get("note") or "").strip(),
             "lines": [{k: x.get(k) for k in ("ean", "code", "desc", "ordered", "got")} for x in rec.get("lines") or []]}
    f = {"receipts": list(order.get("receipts") or []) + [entry]}
    day = at.date().isoformat()
    old_note = str(order.get("note") or "")
    if rec["result"] == "full":
        f.update(arrived=day, state=None)
    elif rec["result"] == "partial":
        f.update(arrived=day, state="partial")
        miss = [x for x in entry["lines"] if float(x.get("got") or 0) < float(x.get("ordered") or 0)]
        txt = "部分收货" + (f"：缺 {len(miss)} 个商品" if miss else "") + (f" · {entry['note']}" if entry["note"] else "")
        f["note"] = (old_note + " · " if old_note else "") + txt
    else:
        f.update(state="stuck", arrived=None)
        txt = "拒收 · " + entry["reason"] + (f" · {entry['note']}" if entry["note"] else "")
        f["note"] = (old_note + " · " if old_note else "") + txt
    return f


def last(order):
    """最近一次收货登记（没有 None）"""
    xs = order.get("receipts") or []
    return xs[-1] if xs else None


def summary(entry):
    """一行给人看：21/11 10:30 · Ronaldo · 全部收到 · DDT 1234"""
    if not entry:
        return ""
    res = {"full": "全部收到", "partial": "部分收到", "refused": "拒收"}.get(entry.get("result"), "")
    parts = [entry.get("at", "")[8:10] + "/" + entry.get("at", "")[5:7] + " " + entry.get("at", "")[11:16],
             entry.get("by") or "", res]
    if entry.get("reason"):
        parts.append(entry["reason"])
    if entry.get("ddt"):
        parts.append("DDT " + entry["ddt"])
    return " · ".join(p for p in parts if p.strip())
