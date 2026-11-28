# -*- coding: utf-8 -*-
"""
给 MCP 服务（mcp_server.py）用的工具：让 Claude 直接看程序里的数据（2026-11-26 用户：「给程序做一个 MCP 协议，
这样你就能直接使用程序并且看到程序数据」）。不依赖界面。

第 1 期只读，第 2 期（2026-11-26 用户：「可以添加改的权限，我不想动手」）加了**会改订单跟踪的操作**，都是两步：
先 *_prepare 列出要改哪几张、改完会怎样 → 返回一个确认号（ticket）→ 用户在对话里说可以，再 apply_change(ticket)；
改完能 undo_change(ticket) 撤销。改的人记成「<本机用户> · Claude」（单独一个日志文件，不和程序抢同一个文件）。
不给的：确认收货 / 部分收货（一定要门店的人点、要选卸货人）、发邮件、生成订单、改 Griglia / 门店清单。

只读的：
- 采购助手里不改数据的工具（agent_tools 里 write=False 的：总览、供应商、算建议箱数、订单列表、邮件核对、订单邮件草稿…）；
- 订单跟踪：统计、一张单的全部字段和修改记录、旧的待登记 / 历史未结订单预览；
- 数据库：只读 SQL（只许 SELECT / SHOW / DESCRIBE / EXPLAIN，一条语句，最多 MAX_ROWS 行；数据库账号本来就是只读的）；
- 日志：procurement / stall / hang / crash / audit 最后几行（能按文字筛）；
- 设置：去掉密码 / 令牌 / 密钥；
- 文件：只在程序文件夹、同步文件夹、订单跟踪 / DETERSIVO 工作文件夹里列目录、看 Excel 前几行。
每次调用写一条审计（logs/audit.jsonl，via = "mcp"）。
"""
import copy
import json
import os
import re
import secrets
import time
from datetime import date, datetime

MAX_ROWS = 500
MAX_TEXT = 60000
LOGS = {"procurement": "procurement.log", "stall": "stall.log", "hang": "hang.log", "crash": "crash.log",
        "audit": "audit.jsonl"}
SECRET = re.compile(r"pass|token|secret|api_?key|credential|pwd", re.I)
_SQL_OK = re.compile(r"^\s*(select|show|describe|desc|explain|with)\b", re.I)
_SQL_BAD = re.compile(r"\b(insert|update|delete|drop|alter|create|replace(?!\s*\()|truncate|grant|revoke|lock|unlock|call|"
                      r"load|handler|set|rename|optimize|repair|analyze|flush|kill|into\s+outfile|into\s+dumpfile)\b", re.I)


class Tool:
    def __init__(self, name, description, schema, run):
        self.name, self.description, self.schema, self.run = name, description, schema, run

    def spec(self):
        return {"name": self.name, "description": self.description,
                "inputSchema": self.schema or {"type": "object", "properties": {}}}


def _obj(props=None, req=()):
    return {"type": "object", "properties": props or {}, "required": list(req)}


def _app_dir():
    from settings import app_dir
    return app_dir()


# ----------------------------------------------------------------------------- 设置
def redact(v, key=""):
    """去掉像密码 / 令牌 / 密钥的值（键名里有 pass / token / secret / key…）"""
    if key and SECRET.search(str(key)):
        return "***" if v not in (None, "") else v
    if isinstance(v, dict):
        return {k: redact(x, k) for k, x in v.items()}
    if isinstance(v, list):
        return [redact(x) for x in v]
    return v


def settings_view(ctx, args):
    s = redact(ctx["settings"])
    part = args.get("section")
    if part:
        return {part: s.get(part)}
    return {k: (v if not isinstance(v, (dict, list)) or len(json.dumps(v, default=str)) < 4000 else "…（太长，用 section 单独看）")
            for k, v in s.items()}


# ----------------------------------------------------------------------------- 数据库（只读）
def check_sql(sql):
    """只读的一条语句：SELECT / SHOW / DESCRIBE / EXPLAIN / WITH；不许分号接第二条、不许写库的关键字"""
    s = str(sql or "").strip().rstrip(";").strip()
    if not s:
        raise ValueError("SQL 是空的")
    body = re.sub(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`[^`]*`", "''", s)     # 引号里的字不算
    body = re.sub(r"--[^\n]*|#[^\n]*|/\*.*?\*/", " ", body, flags=re.S)
    if ";" in body:
        raise ValueError("一次只能一条语句")
    if not _SQL_OK.match(body):
        raise ValueError("只能查（SELECT / SHOW / DESCRIBE / EXPLAIN）")
    if _SQL_BAD.search(body):
        raise ValueError("只能查，不能改数据库")
    return s


def db_query(ctx, args):
    import pandas as pd
    from sqlalchemy import text
    sql = check_sql(args.get("sql"))
    n = max(1, min(int(args.get("max_rows") or 200), MAX_ROWS))
    db = ctx.get("db")
    if db is None:
        import reorder_service as R
        db = ctx["db"] = R.make_db(ctx["settings"].get("db") or None)
    if re.match(r"^\s*(select|with)\b", sql, re.I) and not re.search(r"\blimit\s+\d+", sql, re.I):
        sql = f"{sql} LIMIT {n + 1}"
    df = pd.read_sql(text(sql), db.engine)
    more = len(df) > n
    df = df.head(n)
    rows = [[_cell(v) for v in r] for r in df.itertuples(index=False)]
    return {"列": list(df.columns), "行数": len(rows), "还有更多": more, "行": rows}


def _cell(v):
    try:
        if v != v:                       # NaN
            return None
    except Exception:
        pass
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v if isinstance(v, (int, float, str, bool)) or v is None else str(v)


# ----------------------------------------------------------------------------- 日志
def log_tail(ctx, args):
    name = str(args.get("name") or "procurement")
    if name not in LOGS:
        raise ValueError(f"日志只有：{', '.join(LOGS)}")
    p = os.path.join(_app_dir(), "logs", LOGS[name])
    if not os.path.exists(p):
        return {"文件": p, "内容": "（没有这个日志）"}
    lines = max(1, min(int(args.get("lines") or 200), 2000))
    grep = str(args.get("grep") or "").strip().lower()
    with open(p, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 4_000_000))
        txt = f.read().decode("utf-8", "replace")
    out = txt.splitlines()
    if grep:
        out = [l for l in out if grep in l.lower()]
    return {"文件": p, "大小": size, "内容": "\n".join(out[-lines:])}


# ----------------------------------------------------------------------------- 文件（只读、只在允许的文件夹里）
def allowed_roots(settings):
    """能看的文件夹：程序文件夹、同步文件夹、订单跟踪 / 订单明细 / DETERSIVO / 总仓 / 门店采购文件夹"""
    roots = [_app_dir(), settings.get("sync_root"), settings.get("wholesale_root"),
             (settings.get("tracking") or {}).get("folder"), (settings.get("tracking") or {}).get("record"),
             (settings.get("store_orders") or {}).get("folder"), (settings.get("store_pdv") or {}).get("folder")]
    out = []
    for r in roots:
        if r and os.path.isdir(r):
            r = os.path.normcase(os.path.realpath(r))
            if r not in out:
                out.append(r)
    return out


def _inside(path, roots):
    p = os.path.normcase(os.path.realpath(path))
    return any(p == r or p.startswith(r.rstrip("\\/") + os.sep) for r in roots)


def _check_path(ctx, path):
    path = str(path or "").strip()
    if not path:
        raise ValueError("要给一个路径")
    roots = allowed_roots(ctx["settings"])
    if not _inside(path, roots):
        raise PermissionError("只能看这些文件夹里的：" + " / ".join(roots))
    base = os.path.basename(path).lower()
    if base in ("settings.json", "publish_config.json", "update_config.json", "credentials.json") or SECRET.search(base):
        raise PermissionError("这个文件里有密码 / 令牌，不能看（设置用 settings_view，去掉了密码）")
    return path


def files_list(ctx, args):
    path = args.get("path")
    if not path:
        return {"能看的文件夹": allowed_roots(ctx["settings"])}
    path = _check_path(ctx, path)
    pat = str(args.get("contains") or "").lower()
    out = []
    for n in sorted(os.listdir(path)):
        if pat and pat not in n.lower():
            continue
        p = os.path.join(path, n)
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({"名字": n, "类型": "文件夹" if os.path.isdir(p) else "文件", "大小": st.st_size,
                    "修改时间": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="minutes")})
        if len(out) >= 500:
            break
    return {"文件夹": path, "个数": len(out), "内容": out}


def excel_peek(ctx, args):
    path = _check_path(ctx, args.get("path"))
    rows = max(1, min(int(args.get("rows") or 40), 400))
    start = max(1, int(args.get("start_row") or 1))
    from utils import read_sheets
    out = {}
    want = args.get("sheet")
    for name, it in read_sheets(path):
        if want and str(name).strip().lower() != str(want).strip().lower():
            continue
        data = [[_cell(v) for v in (r or ())] for r in it.iter_rows(min_row=start, max_row=start + rows - 1, values_only=True)]
        out[name] = {"一共几行": it.max_row, "行": data}
        if not want and len(out) >= 6:
            break
    return {"文件": path, "从第几行": start, "工作表": out}


# ----------------------------------------------------------------------------- 订单跟踪
def tracking_summary(ctx, args):
    import collections
    import order_tracking as OT
    import tracking_board as TB
    import commands as C
    st = C.tracking_store(ctx["settings"])
    today = date.today()
    rs = OT.units(OT.rows(st, today=today))          # BMS 凑单算一张
    lanes = collections.Counter(TB.lane_of(r) or "其他" for r in rs)
    status = collections.Counter(r["status"] for r in rs)
    origin = collections.Counter((r["status"], r["origin"] or "excel") for r in rs if r["status"] in ("draft", "void"))
    stale = OT.stale_drafts(st, today=today)
    import old_orders as OO
    old = OO.candidates(st, OO.default_before(today), rs)
    return {"单数": len(rs), "看板": dict(lanes), "状态": dict(status),
            "待登记 / 作废 按来源": {f"{a} · {b}": n for (a, b), n in origin.items()},
            "现在该作废的待登记": {"共": len(stale), "被取代": sum(1 for x in stale if x["why"] == "superseded"),
                            "超过天数": sum(1 for x in stale if x["why"] == "expired")},
            "这个月以前还开着的单": len(old), "日志文件": [os.path.basename(p) for p in st._files()]}


def order_get(ctx, args):
    import order_tracking as OT
    import commands as C
    st = C.tracking_store(ctx["settings"])
    oid = str(args.get("id") or "").strip()
    o = st.orders.get(oid)
    if o is None:
        return {"找不到": oid, "删掉过": st.deleted.get(oid)}
    row = next((r for r in OT.rows(st) if r["id"] == oid), {})
    hist = [{"时间": e.get("t"), "谁": e.get("by"), "来源": e.get("src"), "操作": e.get("op"), "改了": e.get("f")}
            for e in (st.hist.get(oid) or [])[-int(args.get("history") or 30):]]
    return {"单号": oid, "状态": OT.status(o), "字段": o, "界面上的一行": {k: _cell(v) if not isinstance(v, (list, dict)) else v
                                                                   for k, v in row.items()},
            "第一次生成": str(OT.gen_day(st, oid) or ""), "修改记录": hist}


# ----------------------------------------------------------------------------- 会改订单跟踪的（两步：准备 → 用户同意 → 执行）
TICKET_MIN = 30                                 # 确认号多久有效（分钟）
ACTIONS = {"placed": "确认已下单（待登记 → 已下单，下单日期 = 今天）", "confirmed": "标记供应商已确认（今天）",
           "stuck": "标记卡住了", "normal": "恢复正常（去掉卡住 / 部分到货标记）", "cancel": "取消订单",
           "note": "只加一句备注"}
NO_ARRIVAL = "确认收货 / 部分收货一定要门店的人在程序里点、选卸货人，Claude 不能代点。"


def _store(ctx):
    """订单跟踪（改的人 = 本机用户 · Claude，写自己的日志文件）"""
    import commands as C
    cfg = ctx["settings"].get("tracking") or {}
    import order_tracking as OT
    base = (cfg.get("me") or ctx["settings"].get("user_name") or OT.default_me()).strip()
    return C.tracking_store(ctx["settings"], me=f"{base} · Claude")


def _brief(st, ids, limit=60):
    import order_tracking as OT
    rows = {r["id"]: r for r in OT.rows(st)}
    out = []
    for i in ids[:limit]:
        r = rows.get(i)
        out.append({"单号": i, "下单日期": r["date"], "门店": r["store_name"], "供应商": r["supplier"],
                    "金额": r["amount"], "现在": r["status"]} if r else {"单号": i, "现在": "找不到"})
    return out


def _ticket(ctx, kind, args, preview):
    tk = secrets.token_hex(4)
    ctx.setdefault("tickets", {})[tk] = {"kind": kind, "args": args, "exp": time.time() + TICKET_MIN * 60}
    preview = dict(preview)
    preview["确认号"] = tk
    preview["下一步"] = (f"把上面的内容给用户看，用户在对话里明确说可以，再调用 apply_change(ticket=\"{tk}\")。"
                      f"{TICKET_MIN} 分钟内有效。改的是共享的采购订单跟踪，同事都看得到；PIANO Excel 程序下次同步时写进去。")
    return preview


def drafts_void_prepare(ctx, args):
    import order_tracking as OT
    st = _store(ctx)
    cfg = ctx["settings"].get("tracking") or {}
    items = OT.stale_drafts(st, keep_days=cfg.get("draft_days"))
    if not items:
        return {"要作废的": 0, "说明": "现在没有该作废的旧待登记"}
    ids = [x["id"] for x in items]
    why = {x["id"]: ("后来 %s 又生成了这家的订单" % x.get("by") if x["why"] == "superseded"
                     else f"生成超过 {OT.DRAFT_KEEP_DAYS} 天没发出") for x in items}
    rows = _brief(st, ids, limit=400)
    for r in rows:
        r["为什么作废"] = why.get(r["单号"])
    return _ticket(ctx, "void", {"ids": ids}, {
        "操作": "作废旧的待登记", "张数": len(ids),
        "被新的配货取代": sum(1 for x in items if x["why"] == "superseded"),
        "超过天数没发出": sum(1 for x in items if x["why"] == "expired"),
        "只作废": "程序自动生成、没人改过、没发过邮件、没关联过邮件的待登记；作废不是删除，能恢复",
        "订单": rows})


def drafts_restore_prepare(ctx, args):
    st = _store(ctx)
    ids = [i for i in (args.get("ids") or []) if (st.orders.get(i) or {}).get("void")]
    if not ids:
        return {"能恢复的": 0, "说明": "这些单都不是已作废的待登记"}
    return _ticket(ctx, "restore", {"ids": ids}, {"操作": "把已作废的待登记恢复成待登记", "张数": len(ids),
                                                   "订单": _brief(st, ids)})


def orders_update_prepare(ctx, args):
    act = args.get("action")
    if act in ("arrived", "partial"):
        raise RuntimeError(NO_ARRIVAL)
    if act not in ACTIONS:
        raise RuntimeError(f"不认识的操作：{act}（能用：{', '.join(ACTIONS)}）")
    note = (args.get("note") or "").strip()
    if act == "note" and not note:
        raise RuntimeError("「只加一句备注」要写 note")
    st = _store(ctx)
    ids = [i for i in (args.get("ids") or []) if i in st.orders]
    miss = [i for i in (args.get("ids") or []) if i not in st.orders]
    if not ids:
        raise RuntimeError("这些单号都找不到")
    out = {"操作": ACTIONS[act], "张数": len(ids), "订单": _brief(st, ids)}
    if note:
        out["备注"] = note
    if miss:
        out["找不到的单号"] = miss
    return _ticket(ctx, "update", {"ids": ids, "action": act, "note": note}, out)


def mail_apply_prepare(ctx, args):
    import agent_tools as AT
    sugs = AT._mail_sugs(ctx, args)
    kinds = {"order": "邮件说已下单", "confirm": "供应商已确认", "ship": "已发货", "dup": "合并重复的待登记",
             "missing": "补登记跟踪里没有的单"}
    rows = [{"编号": i, "类型": kinds.get(s["kind"], s["kind"]), "门店": s.get("store"), "供应商": s.get("supplier"),
             "订单日期": s.get("order_date"), "邮件": ((s.get("mail") or {}).get("subject") or "")[:120]}
            for i, s in zip(args.get("indexes") or [], sugs)]
    return _ticket(ctx, "mail", {"indexes": [int(i) for i in args.get("indexes") or []]},
                   {"操作": "按邮件更新订单跟踪", "条数": len(rows), "建议": rows})


def _diff_undo(before, st, ids):
    """改之前的字段 → 撤销时要写回的 [(单号, {字段: 旧值})]"""
    out = []
    for oid in ids:
        a, b = before.get(oid) or {}, st.orders.get(oid) or {}
        f = {k: a.get(k) for k in set(a) | set(b) if a.get(k) != b.get(k)}
        if f:
            out.append((oid, f))
    return out


def apply_change(ctx, args):
    import order_tracking as OT
    import commands as C
    tk = str(args.get("ticket") or "").strip()
    t = (ctx.get("tickets") or {}).get(tk)
    if not t:
        raise RuntimeError("没有这个确认号（可能已经用过了）：先调用对应的 *_prepare")
    if time.time() > t["exp"]:
        ctx["tickets"].pop(tk, None)
        raise RuntimeError("确认号过期了，重新 prepare 一次")
    a = t["args"]
    st = _store(ctx)
    ids = list(a.get("ids") or [])
    before = {i: copy.deepcopy(st.orders.get(i) or {}) for i in ids}
    if t["kind"] == "void":
        items = [x for x in OT.stale_drafts(st, keep_days=(ctx["settings"].get("tracking") or {}).get("draft_days"))
                 if x["id"] in set(ids)]
        n = OT.void_drafts(st, items)
        res = f"作废了 {n} 张旧的待登记（「看哪些 → 已作废的待登记」里能看到、能恢复）"
    elif t["kind"] == "restore":
        n = OT.restore_drafts(st, ids)
        res = f"恢复了 {n} 张待登记"
    elif t["kind"] == "update":
        act, note = a["action"], a.get("note") or ""
        if act == "placed":
            n = OT.confirm_drafts(st, ids)
        else:
            today = date.today().isoformat()
            ch = []
            for oid in ids:
                o = st.orders.get(oid) or {}
                f = {}
                if act == "confirmed" and not o.get("confirmed"):
                    f = {"confirmed": today}
                elif act == "stuck":
                    f = {"state": "stuck"}
                elif act == "normal" and o.get("state") in ("stuck", "partial"):
                    f = {"state": None}
                elif act == "cancel":
                    f = {"state": "cancel"}
                if note:
                    f["note"] = (str(o.get("note") or "") + " · " if o.get("note") else "") + note
                if f:
                    ch.append((oid, f))
            st.set_many(ch)
            n = len(ch)
        res = f"改了 {n} 张订单（{ACTIONS[act]}）"
    elif t["kind"] == "mail":
        import agent_tools as AT
        sugs = AT._mail_sugs(ctx, a)
        n, undo = C.mail_apply(st, sugs, via="mcp")
        ctx.setdefault("undo", {})[tk] = ("mail", undo)
        ctx["tickets"].pop(tk, None)
        return {"结果": f"按邮件改了 {n} 张订单", "撤销": f"undo_change(ticket=\"{tk}\")"}
    else:
        raise RuntimeError(t["kind"])
    st.reload()
    ctx.setdefault("undo", {})[tk] = ("fields", _diff_undo(before, st, ids))
    ctx["tickets"].pop(tk, None)
    C.audit(st.me, "mcp", "apply_" + t["kind"], a, {"result": res})
    return {"结果": res, "撤销": f"undo_change(ticket=\"{tk}\")"}


def undo_change(ctx, args):
    import commands as C
    tk = str(args.get("ticket") or "").strip()
    u = (ctx.get("undo") or {}).get(tk)
    if not u:
        raise RuntimeError("这个确认号没有能撤销的修改（只有这次连接里做的能撤销；以前的请在程序里改回去）")
    st = _store(ctx)
    kind, data = u
    if kind == "mail":
        C.mail_undo(st, data, via="mcp")
        n = len(data)
    else:
        st.set_many(data)
        n = len(data)
    ctx["undo"].pop(tk, None)
    C.audit(st.me, "mcp", "undo", {"ticket": tk}, {"orders": n})
    return {"结果": f"撤销了，{n} 张单改回去了"}


def write_tools():
    ids = {"type": "array", "items": {"type": "string"}, "description": "单号（orders_list / order_get 里的）"}
    first = "第一步，只是准备，不会改任何东西：返回要改哪几张和一个确认号；给用户看、用户同意后再 apply_change。"
    return [
        Tool("drafts_void_prepare", "作废旧的待登记（被新的配货取代 / 生成超过几天没发出）。" + first, _obj(), drafts_void_prepare),
        Tool("drafts_restore_prepare", "把已作废的待登记恢复成待登记。" + first, _obj({"ids": ids}, ["ids"]), drafts_restore_prepare),
        Tool("orders_update_prepare", "改订单状态：placed = 待登记确认为已下单；confirmed = 供应商已确认；stuck = 卡住了；"
             "normal = 恢复正常；cancel = 取消订单；note = 只加备注（note 字段也可以跟着别的操作一起写）。" + NO_ARRIVAL + first,
             _obj({"ids": ids, "action": {"type": "string", "enum": list(ACTIONS)}, "note": {"type": "string"}}, ["ids", "action"]),
             orders_update_prepare),
        Tool("mail_apply_prepare", "按 mail_check 的建议改订单跟踪（供应商确认了 / 发货了 / 合并重复的待登记 / 补登记）。"
             "indexes = mail_check 返回的编号（同一次连接里先调 mail_check）。" + first,
             _obj({"indexes": {"type": "array", "items": {"type": "integer"}}}, ["indexes"]), mail_apply_prepare),
        Tool("apply_change", "第二步：执行 *_prepare 准备好的修改。**只有用户在对话里明确同意了才能调用**。",
             _obj({"ticket": {"type": "string"}}, ["ticket"]), apply_change),
        Tool("undo_change", "撤销这次连接里 apply_change 做过的修改。", _obj({"ticket": {"type": "string"}}, ["ticket"]), undo_change),
    ]


# ----------------------------------------------------------------------------- 汇总
def build_tools():
    tools = [
        Tool("tracking_summary", "采购订单跟踪的统计：看板每列几张、每种状态几张、待登记按来源、现在该作废的旧待登记、这个月以前还开着的单。",
             _obj(), tracking_summary),
        Tool("order_get", "一张订单的全部字段、界面上显示的那一行（状态、预计到货、超期…）和修改记录（谁、什么时候、从哪改的）。",
             _obj({"id": {"type": "string", "description": "单号（orders_list 返回的）"},
                   "history": {"type": "integer", "description": "看最近几条修改记录，默认 30"}}, ["id"]), order_get),
        Tool("db_query", "只读查销售数据库（MySQL，ERP 的 ysoft 库）。只许一条 SELECT / SHOW / DESCRIBE / EXPLAIN；"
             "没写 LIMIT 自动加。常用表：vendite_list_1..5（各店销售）、giacenza（库存 G0..G5）、prodotti（商品）、"
             "caricolistalist（入库明细：Cdata、Quantita、Tipo CA/CI/UI、Fornitore、Prezzo_A、Negozio）、fornitore、categorie、promozioni。",
             _obj({"sql": {"type": "string"}, "max_rows": {"type": "integer", "description": f"最多几行（≤ {MAX_ROWS}）"}}, ["sql"]),
             db_query),
        Tool("log_tail", "程序日志最后几行：procurement（运行日志）、stall（小卡顿）、hang（卡住 5 秒以上）、crash（闪退）、audit（谁做了什么）。可按文字筛。",
             _obj({"name": {"type": "string", "enum": list(LOGS)}, "lines": {"type": "integer"},
                   "grep": {"type": "string", "description": "只看含这段文字的行"}}), log_tail),
        Tool("settings_view", "程序设置（密码 / 令牌 / 密钥都去掉了）。section = 只看一块，例如 algo、tracking、mail、order_mail、store_orders。",
             _obj({"section": {"type": "string"}}), settings_view),
        Tool("files_list", "列出文件夹里的文件（只能看程序文件夹、同步文件夹、订单跟踪 / DETERSIVO / 总仓 / 门店采购的文件夹）。不给 path = 列出能看哪些文件夹。",
             _obj({"path": {"type": "string"}, "contains": {"type": "string", "description": "文件名里有这段字"}}), files_list),
        Tool("excel_peek", "看一个 Excel（.xlsx / .xls）每页前几行的值（只读，同样只能看允许的文件夹）。",
             _obj({"path": {"type": "string"}, "sheet": {"type": "string"}, "rows": {"type": "integer"},
                   "start_row": {"type": "integer"}}, ["path"]), excel_peek),
    ]
    try:
        import agent_tools as AT
        for t in AT.build_tools():
            if not t.write and t.name not in {x.name for x in tools}:
                tools.append(Tool(t.name, t.description + "（采购助手的同一个工具，只读）", t.schema, t.run))
    except Exception:
        pass
    return tools + write_tools()


def call(tools, ctx, name, args):
    """跑一个工具 → 文字（JSON）；写一条审计"""
    t = next((x for x in tools if x.name == name), None)
    if t is None:
        raise KeyError(f"没有这个工具：{name}")
    res = t.run(ctx, dict(args or {}))
    txt = res if isinstance(res, str) else json.dumps(res, ensure_ascii=False, default=str, indent=1)
    try:
        import commands as C
        C.audit(ctx.get("actor") or "Claude", "mcp", name, {k: (str(v)[:300]) for k, v in (args or {}).items()},
                {"chars": len(txt)})
    except Exception:
        pass
    if len(txt) > MAX_TEXT:
        txt = txt[:MAX_TEXT] + f"\n…（太长，后面 {len(txt) - MAX_TEXT} 字省略；加筛选条件或 limit 再查）"
    return txt
