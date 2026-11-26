# -*- coding: utf-8 -*-
"""
采购助手的工具（给 agent.Agent 用）。不依赖界面；业务步骤都调 commands.py（审计来源 via="agent"）。

第 1 批（2026-11-13）：
- 总览：今天的供应商、订单跟踪的待登记 / 待确认 / 超期 / 卡住
- 总仓采购 / 门店采购：列供应商、算建议箱数、看一家供应商的明细、改箱数、生成订单（确认）
- 采购订单跟踪：查订单、改状态（确认）
- 邮件：邮件核对（只读）、按建议更新订单（确认）、看订单邮件草稿、发订单邮件（确认）
还没有的（之后接）：DETERSIVO 检查门店清单 + 比价、Griglia 停售 / 替代商品、门店报告、在途
ctx：{"settings", "calc": {kind: 最近一次计算}, "mail": 最近一次邮件核对结果, "log", "hooks": 界面钩子}
"""
from datetime import date

import commands as C
from agent import Tool

KIND = {"type": "string", "enum": ["central", "pdv"],
        "description": "central = 总仓采购（Centrale Acquisti 的 SCHEDA）；pdv = 门店采购（门店自己采购的供应商）"}
DAY = {"type": "string", "enum": ["周一", "周二", "周三", "周四", "周五", "全部"],
       "description": "采购配单文件夹里的哪一天；不填 = 今天（周末不填时按全部）"}
STORE_IDS = {"FOIANO": 1, "MARLIA": 2, "S.FILIPPO": 3, "SFILIPPO": 3, "S.MARCO": 4, "SMARCO": 4,
             "S.CONCORDIO": 5, "SCONCORDIO": 5}
STATUS_ZH = {"draft": "待登记", "void": "待登记已作废", "ordered": "已下单", "confirmed": "已确认", "shipped": "已发货", "partial": "部分到货",
             "arrived": "已到货", "stuck": "卡住了", "cancel": "已取消", "deter": "DETERSIVO"}


def _log(ctx):
    return ctx.get("log") or (lambda m: None)


def _hook(ctx, name, *a):
    fn = (ctx.get("hooks") or {}).get(name)
    if fn:
        try:
            fn(*a)
        except Exception:
            pass


def _day(args):
    d = args.get("day")
    if d == "全部":
        return None
    return d or C.today_day()


def _store_id(v):
    if v is None or v == "":
        return None
    s = str(v).strip().upper().replace(" ", "")
    if s.isdigit():
        return int(s)
    if s in STORE_IDS:
        return STORE_IDS[s]
    for k, n in STORE_IDS.items():
        if s in k or k in s:
            return n
    raise ValueError(f"不认识的门店：{v}")


def _calc(ctx, kind):
    c = (ctx.get("calc") or {}).get(kind)
    if not c:
        raise RuntimeError("还没算过建议箱数，先调用 store_compute")
    return c


# ----------------------------------------------------------------------------- 总览
def overview(ctx, args):
    s = ctx["settings"]
    out = {"today": date.today().isoformat(), "day": C.today_day() or "周末"}
    for kind in ("central", "pdv"):
        try:
            rows = C.store_suppliers(s, kind, C.today_day()) if C.today_day() else []
            out[C.STORE_TITLES[kind]] = {"今天的供应商": len(rows), "页面上没勾的": sum(1 for r in rows if r["skipped"]),
                                         "名单": [r["name"] for r in rows][:40]}
        except Exception as e:
            out[C.STORE_TITLES[kind]] = f"读不了：{e}"
    try:
        import order_tracking as OT
        st = C.tracking_store(s)
        rs = OT.rows(st)
        sm = OT.summary(rs, st, date.today().strftime("%Y-%m"))
        out["采购订单跟踪"] = {"待登记": sm["draft"], "下单超过几天还没确认": sm["pending"], "已超期没到货": sm["overdue"],
                           "卡住了": sm["stuck"], "还没到货的": sm["open"], "本月已下单（不含税）": round(sm["spent"], 2),
                           "本月预算": sm["budget"]}
    except Exception as e:
        out["采购订单跟踪"] = f"读不了：{e}"
    return out


# ----------------------------------------------------------------------------- 总仓采购 / 门店采购
def store_list(ctx, args):
    rows = C.store_suppliers(ctx["settings"], args["kind"], _day(args))
    return [{"供应商": r["name"], "星期": r["day"], "页面上勾着": not r["skipped"]} for r in rows]


def store_compute(ctx, args):
    kind = args["kind"]
    stores = [_store_id(x) for x in args.get("stores") or []] or None
    calc = C.store_compute(ctx["settings"], kind, _day(args), args.get("suppliers") or None, stores,
                           log=_log(ctx), progress=ctx.get("progress"), cancel=ctx.get("cancel"))
    ctx.setdefault("calc", {})[kind] = calc
    _hook(ctx, "computed", kind, calc)
    return _summary(ctx, kind)


def _summary(ctx, kind):
    calc = _calc(ctx, kind)
    rows = C.store_summary(ctx["settings"], calc)
    ok = [r for r in rows if r["orderable"]]
    out = {"供应商": len(rows), "订得起来的": len(ok), "合计金额（订得起来的）": round(sum(r["amount"] for r in ok), 2),
           "明细": [{"供应商": r["name"], "星期": r["day"], "起订量": r["moq"], "订得起来": r["orderable"],
                    "金额": r["amount"], "箱数": r["colli"],
                    "各店": {k: f"{v['status']} · {v['colli']} 箱 · €{v['amount']}" for k, v in r["stores"].items()},
                    "BMS": (f"{r['bms']['status']} · {r['bms']['colli']} 箱 · €{r['bms']['amount']}" if r["bms"] else None)}
                   for r in rows]}
    if calc.get("errors"):
        out["读不出来的 SCHEDA"] = [f"{p}: {e}" for p, e in calc["errors"]]
    if calc.get("missing"):
        out["数据库里找不到的商品数"] = len(calc["missing"])
    if calc.get("transfers"):
        out["门店间调货"] = f"{len(calc['transfers'])} 个商品"
    return out


def store_result(ctx, args):
    return _summary(ctx, args["kind"])


def store_lines(ctx, args):
    calc = _calc(ctx, args["kind"])
    rows = C.store_lines(calc, args["supplier"], _store_id(args.get("store")), limit=int(args.get("limit") or 80))
    return [{"门店": r["store"], "行": r["row"], "编号": r["code"], "条码": r["ean"], "商品": r["name"],
             "每箱": r["pack"], "件价": r["price"], "建议箱数": r["sugg"], "现在订": r["boxes"]} for r in rows]


def store_set_boxes(ctx, args):
    calc = _calc(ctx, args["kind"])
    sid = _store_id(args["store"])
    old = C.store_set_boxes(calc, args["supplier"], sid, int(args["row"]), int(args["boxes"]))
    _hook(ctx, "computed", args["kind"], calc)
    return f"已改：{args['supplier']} · 门店 {sid} · 第 {args['row']} 行 {old} → {int(args['boxes'])} 箱（生成订单时才写文件）"


def _alloc_preview(ctx, args):
    kind = args["kind"]
    calc = _calc(ctx, kind)
    rows = C.store_summary(ctx["settings"], calc)
    want = [str(n).upper() for n in args.get("suppliers") or []]
    if want:
        rows = [r for r in rows if any(w == r["name"].upper() or w in r["name"].upper() for w in want)]
    else:
        rows = [r for r in rows if r["orderable"]]
    return kind, rows


def store_generate_text(ctx, args):
    kind, rows = _alloc_preview(ctx, args)
    su = C.store_setup(ctx["settings"], kind)
    lines = [f"· {r['name']}：€{r['amount']:,.2f} · {r['colli']:g} 箱" for r in rows[:30]]
    if len(rows) > 30:
        lines.append(f"…还有 {len(rows) - 30} 家")
    total = sum(r["amount"] for r in rows)
    return (f"{su['title']}：生成 {len(rows)} 家供应商的订单文件（合计 €{total:,.2f}，不含税）\n\n" + "\n".join(lines) +
            f"\n\n改完会：\n· 在「{su['folder']}\\生成订单」里新建这次的文件夹，一家供应商一个订单文件 + 配货汇总；\n"
            "· 采购订单跟踪里登记成「待登记」（同事都能看到），发给供应商后才变「已下单」。")


def store_generate(ctx, args):
    kind = args["kind"]
    calc = _calc(ctx, kind)
    import store_orders as S
    res = C.store_allocate(ctx["settings"], calc, args.get("suppliers") or None, log=_log(ctx),
                           progress=ctx.get("progress"))
    _hook(ctx, "allocated", kind, calc, res)
    tot = S.plan_totals(res["results"])
    tk = res.get("tracked") or {}
    return {"订单文件": len(S.written_files(res["written"])), "文件夹": res["out_root"],
            "直接送店（€）": round(tot["direct"], 2), "BMS（€）": round(tot["bms"], 2), "合计（€）": round(tot["total"], 2),
            "采购订单跟踪待登记": tk.get("drafts") if isinstance(tk, dict) else None}


# ----------------------------------------------------------------------------- 采购订单跟踪
def orders_list(ctx, args):
    import order_tracking as OT
    st = C.tracking_store(ctx["settings"])
    rs = OT.rows(st)
    status = args.get("status")
    if status:
        want = {k for k, v in STATUS_ZH.items() if v == status or k == status}
        rs = [r for r in rs if r["status"] in want]
    if args.get("only_open"):
        rs = [r for r in rs if r["open"]]
    if args.get("only_overdue"):
        rs = [r for r in rs if r["overdue"]]
    if args.get("store"):
        sid = str(_store_id(args["store"])) if str(args["store"]).upper() != "BMS" else "BMS"
        rs = [r for r in rs if r["store"] == sid]
    if args.get("supplier"):
        w = str(args["supplier"]).upper()
        rs = [r for r in rs if w in r["supplier"].upper() or w in str(r["raw_supplier"]).upper()]
    if args.get("since"):
        d0 = OT.to_date(args["since"])
        rs = [r for r in rs if r["date"] and d0 and r["date"] >= d0]
    rs.sort(key=lambda r: (r["date"] or date.min), reverse=True)
    n = len(rs)
    rs = rs[: int(args.get("limit") or 40)]
    return {"一共": n, "订单": [{"单号": r["id"], "下单日期": r["date"], "门店": r["store_name"], "供应商": r["supplier"],
                               "金额": r["amount"], "箱数": r["colli"], "状态": STATUS_ZH.get(r["status"], r["status"]),
                               "预计到货": r["expected"], "超期天数": r["late_by"] or None, "备注": r["note"] or None,
                               "订单文件": bool(r["file"])} for r in rs]}


ACTIONS = {"placed": "确认已下单（待登记 → 已下单，下单日期 = 今天）", "confirmed": "供应商已确认",
           "arrived": "确认收货（已到货，今天）", "partial": "部分收货", "stuck": "标记卡住了", "normal": "恢复正常",
           "cancel": "取消订单"}


def _orders_brief(ctx, ids):
    import order_tracking as OT
    st = C.tracking_store(ctx["settings"])
    rows = {r["id"]: r for r in OT.rows(st)}
    out = []
    for i in ids:
        r = rows.get(i)
        out.append(f"· {r['date'] or ''} {r['store_name']} · {r['supplier']} · €{r['amount'] or 0} · "
                   f"{STATUS_ZH.get(r['status'], r['status'])}" if r else f"· {i}（找不到）")
    return out


def orders_update_text(ctx, args):
    ids = list(args.get("ids") or [])
    act = ACTIONS.get(args.get("action"), args.get("action"))
    return (f"{act}：{len(ids)} 张订单\n\n" + "\n".join(_orders_brief(ctx, ids)[:30]) +
            ("\n\n备注：" + args["note"] if args.get("note") else "") +
            "\n\n改的是共享的采购订单跟踪，同事都能看到；PIANO Excel 会在下次同步时写进去。")


def orders_update(ctx, args):
    ids = list(args.get("ids") or [])
    act = args.get("action")
    st = C.tracking_store(ctx["settings"])
    today = date.today().isoformat()
    if act == "placed":
        n = C.confirm_orders(st, ids, via="agent")
        _hook(ctx, "tracking_changed")
        return f"{n} 张待登记已确认为已下单"
    ch = []
    note = (args.get("note") or "").strip()
    for oid in ids:
        o = st.orders.get(oid)
        if o is None:
            continue
        f = {}
        if act == "confirmed" and not o.get("confirmed"):
            f = {"confirmed": today}
        elif act == "arrived":
            f = {"arrived": today}
            if o.get("state") in ("partial", "stuck"):
                f["state"] = None
        elif act in ("partial", "stuck"):
            f = {"state": act}
            if act == "partial" and not o.get("arrived"):
                f["arrived"] = today
        elif act == "normal" and o.get("state"):
            f = {"state": None}
        elif act == "cancel":
            f = {"state": "cancel"}
        if note and f:
            f["note"] = (str(o.get("note") or "") + " · " if o.get("note") else "") + note
        if f:
            ch.append((oid, f))
    if ch:
        st.set_many(ch)
    C.audit(st.me, "agent", "orders_update", {"ids": ids, "action": act, "note": note}, {"changed": len(ch)})
    _hook(ctx, "tracking_changed")
    return f"改了 {len(ch)} 张订单（{ACTIONS.get(act, act)}）"


# ----------------------------------------------------------------------------- 邮件
def mail_check(ctx, args):
    res = C.mail_tracking(ctx["settings"], days=args.get("days"), log=_log(ctx), via="agent",
                          progress=ctx.get("progress"), cancel=ctx.get("cancel"))
    sugs = list(res.get("suggest") or []) + list(res.get("dups") or []) + list(res.get("missing") or [])
    ctx["mail"] = {"sugs": sugs, "store": res.get("store")}
    out = []
    for i, s in enumerate(sugs):
        m = s.get("mail") or {}
        out.append({"编号": i, "类型": {"order": "邮件说已下单", "confirm": "供应商已确认", "ship": "已发货",
                                    "dup": "重复的待登记（Excel 已手填）", "missing": "跟踪里没有这张单（补登记）"}.get(s["kind"], s["kind"]),
                    "门店": s.get("store"), "供应商": s.get("supplier"), "订单日期": s.get("order_date"),
                    "邮件": f"{m.get('date') or ''} {m.get('from') or ''} · {m.get('subject') or ''}"[:160]})
    return {"读了几封信": res.get("mails"), "出错的邮箱": len(res.get("errors") or []), "建议": out}


def _mail_sugs(ctx, args):
    m = ctx.get("mail")
    if not m:
        raise RuntimeError("先调用 mail_check")
    idx = [int(i) for i in args.get("indexes") or []]
    bad = [i for i in idx if i < 0 or i >= len(m["sugs"])]
    if bad:
        raise RuntimeError(f"没有这些编号：{bad}")
    return [m["sugs"][i] for i in idx]


def mail_apply_text(ctx, args):
    sugs = _mail_sugs(ctx, args)
    lines = [f"· {s['kind']} · {s.get('store') or ''} · {s.get('supplier') or ''} · {(s.get('mail') or {}).get('subject') or ''}"[:150]
             for s in sugs[:30]]
    return (f"按邮件更新 {len(sugs)} 条订单记录：\n\n" + "\n".join(lines) +
            "\n\n改的是共享的采购订单跟踪，同事都能看到；可以在订单跟踪「邮件核对」里撤销。")


def mail_apply(ctx, args):
    sugs = _mail_sugs(ctx, args)
    st = ctx["mail"].get("store") or C.tracking_store(ctx["settings"])
    n, _undo = C.mail_apply(st, sugs, via="agent")
    _hook(ctx, "tracking_changed")
    return f"改了 {n} 张订单"


def order_mail_preview(ctx, args):
    st = C.tracking_store(ctx["settings"])
    d = C.order_mail_draft(ctx["settings"], st, args["order_id"], fetch_body=True)
    ctx.setdefault("drafts", {})[args["order_id"]] = d
    return {"一起发的单": d.get("ids"), "发件": d.get("from"), "收件": d.get("to"), "收件人从哪来": d.get("to_src"),
            "抄送": d.get("cc"), "主题": d.get("subject"), "正文": d.get("body"),
            "附件": [f[1] for f in d.get("files") or []],
            "这台电脑上找不到的附件": [f[1] for f in d.get("files") or [] if len(f) > 2 and not f[2]] or None,
            "已经发过的单": d.get("sent_before") or None}


def _mail_final(ctx, args):
    d = (ctx.get("drafts") or {}).get(args["order_id"])
    if d is None:
        order_mail_preview(ctx, args)
        d = ctx["drafts"][args["order_id"]]
    pick = lambda k, dflt: args[k] if args.get(k) not in (None, "", []) else dflt
    to = pick("to", d.get("to") or [])
    cc = pick("cc", d.get("cc") or [])
    to = [to] if isinstance(to, str) else list(to)
    cc = [cc] if isinstance(cc, str) else list(cc)
    return d, to, cc, pick("subject", d.get("subject")), pick("body", d.get("body"))


def order_mail_text(ctx, args):
    d, to, cc, subj, body = _mail_final(ctx, args)
    files = ", ".join(f[1] for f in d.get("files") or []) or "（没有附件）"
    return (f"从 {d.get('from')} 发订单邮件\n收件：{', '.join(to)}\n抄送：{', '.join(cc) or '—'}\n主题：{subj}\n附件：{files}\n\n"
            f"{body[:800]}\n\n发出后这几张单变「已下单」（下单日期 = 今天），供应商会收到邮件，不能撤回。")


def order_mail_send(ctx, args):
    d, to, cc, subj, body = _mail_final(ctx, args)
    st = C.tracking_store(ctx["settings"])
    res = C.send_order(ctx["settings"], st, d["ids"], d["from"], to, cc, subj, body,
                       [(f[0], f[1]) for f in d.get("files") or []], via="agent")
    _hook(ctx, "tracking_changed")
    return {"已发送": True, "邮件编号": res.get("mid"), "存进已发送": res.get("saved"), "变成已下单": res.get("n")}


# ----------------------------------------------------------------------------- 工具清单
def build_tools():
    obj = lambda props, req=(): {"type": "object", "properties": props, "required": list(req)}
    sup_list = {"type": "array", "items": {"type": "string"}, "description": "供应商名字（SCHEDA 文件名里的，例如 DOPLA）"}
    return [
        Tool("overview", "今天的总览：今天是星期几、总仓采购 / 门店采购今天有哪些供应商、采购订单跟踪的待登记 / 待确认 / 超期 / 卡住数量、本月花费和预算。",
             obj({}), overview, label="查看总览"),
        Tool("store_list_suppliers", "列出总仓采购或门店采购某一天文件夹里的供应商（SCHEDA），以及页面上有没有勾选。",
             obj({"kind": KIND, "day": DAY}, ["kind"]), store_list, label="列出供应商"),
        Tool("store_compute", "算建议订货箱数（读 SCHEDA、查数据库，只读，可能要一两分钟）。返回每家供应商：够不够起订量、各店直送 / 凑单 / 不订、BMS、金额。"
             "算完结果会留着，后面的 store_supplier_lines / store_set_boxes / store_generate_orders 都用它。",
             obj({"kind": KIND, "day": DAY, "suppliers": sup_list,
                  "stores": {"type": "array", "items": {"type": "string"}, "description": "门店（名字或编号 1–5）；不填 = 页面上选的"}},
                 ["kind"]), store_compute, label="计算建议箱数"),
        Tool("store_result", "再看一次最近一次计算的结果（改过箱数后用它看新的金额 / 起订量情况）。",
             obj({"kind": KIND}, ["kind"]), store_result, label="查看计算结果"),
        Tool("store_supplier_lines", "看一家供应商的订货明细（每家店每个商品：建议箱数、现在订几箱、每箱件数、件价）。",
             obj({"kind": KIND, "supplier": {"type": "string"}, "store": {"type": "string", "description": "只看这家店（可不填）"},
                  "limit": {"type": "integer"}}, ["kind", "supplier"]), store_lines, label="查看订货明细"),
        Tool("store_set_boxes", "改一行的订货箱数（只改这次计算里的，生成订单时才写文件）。row 是 store_supplier_lines 返回的「行」。",
             obj({"kind": KIND, "supplier": {"type": "string"}, "store": {"type": "string"}, "row": {"type": "integer"},
                  "boxes": {"type": "integer", "minimum": 0}}, ["kind", "supplier", "store", "row", "boxes"]),
             store_set_boxes, label="改订货箱数"),
        Tool("store_generate_orders", "生成采购订单（会写文件、登记待登记，程序会先弹窗让用户确认）。suppliers 不填 = 所有订得起来的供应商。",
             obj({"kind": KIND, "suppliers": sup_list}, ["kind"]), store_generate, write=True,
             confirm_text=store_generate_text, label="生成采购订单"),
        Tool("orders_list", "查采购订单跟踪里的订单（可按状态 / 门店 / 供应商 / 日期筛选）。",
             obj({"status": {"type": "string", "enum": list(STATUS_ZH.values())},
                  "only_open": {"type": "boolean", "description": "只看还没到货的"},
                  "only_overdue": {"type": "boolean", "description": "只看已超期的"},
                  "store": {"type": "string"}, "supplier": {"type": "string"},
                  "since": {"type": "string", "description": "下单日期从这天起（YYYY-MM-DD）"},
                  "limit": {"type": "integer"}}), orders_list, label="查订单"),
        Tool("orders_update", "改订单状态（程序会先弹窗让用户确认）：placed = 待登记确认为已下单；confirmed = 供应商已确认；"
             "arrived = 确认收货；partial = 部分收货；stuck = 卡住了；normal = 恢复正常；cancel = 取消订单。",
             obj({"ids": {"type": "array", "items": {"type": "string"}, "description": "单号（orders_list 返回的）"},
                  "action": {"type": "string", "enum": list(ACTIONS)}, "note": {"type": "string"}}, ["ids", "action"]),
             orders_update, write=True, confirm_text=orders_update_text, label="改订单状态"),
        Tool("mail_check", "邮件核对：读所有邮箱最近几周的信头（只读），对到采购订单跟踪，给出建议（供应商确认了、发货了、跟踪里没有的单、重复的待登记）。",
             obj({"days": {"type": "integer"}}), mail_check, label="邮件核对"),
        Tool("mail_apply", "按 mail_check 的建议更新订单（程序会先弹窗让用户确认）。indexes = 建议的编号。",
             obj({"indexes": {"type": "array", "items": {"type": "integer"}}}, ["indexes"]), mail_apply, write=True,
             confirm_text=mail_apply_text, label="按邮件更新订单"),
        Tool("order_mail_preview", "准备一张待登记订单的订单邮件（发件邮箱、收件人、主题、正文、附件），只看不发。",
             obj({"order_id": {"type": "string"}}, ["order_id"]), order_mail_preview, label="准备订单邮件"),
        Tool("order_mail_send", "发订单邮件给供应商（程序会先弹窗让用户确认）。不填的项用 order_mail_preview 准备好的。",
             obj({"order_id": {"type": "string"}, "to": {"type": "array", "items": {"type": "string"}},
                  "cc": {"type": "array", "items": {"type": "string"}}, "subject": {"type": "string"},
                  "body": {"type": "string"}}, ["order_id"]), order_mail_send, write=True,
             confirm_text=order_mail_text, label="发订单邮件"),
    ]
