"""
业务命令（不依赖界面）：桌面程序、命令行、以后的采购服务器 / MCP 都调用这里（2026-11-06 第 0 阶段）。

· run_check  检查门店订货清单（原来写在 order_check.OrderCheck.start 的后台线程里，逐行搬过来，结果一样）
· read_quotes / apply_quotes  读供应商报价、改用最便宜那家的箱规和件价
· load_lists  读门店清单文件（和界面「拖进清单」同一套识别）

规则：这里不 import PySide6；进度用 progress.post_step / post_progress（TaskDialog 有这两个方法，
命令行用 PrintProgress），日志用 log(文字)。
"""
import os
import time

from i18n import tr
import reorder_service as R


class NullProgress:
    """不显示进度"""
    def post_step(self, i, state="running", count="", detail=""):
        pass

    def post_progress(self, pct):
        pass


class PrintProgress(NullProgress):
    """命令行：每一步开始 / 结束打一行"""
    def __init__(self, steps, out=print):
        self.steps, self.out, self._last = steps, out, {}

    def post_step(self, i, state="running", count="", detail=""):
        if self._last.get(i) == state or not (0 <= i < len(self.steps)):
            return
        self._last[i] = state
        mark = {"running": "…", "done": "✔", "error": "✘"}.get(state, "·")
        self.out(f"{mark} {tr(self.steps[i])} {count}".rstrip())


CHECK_STEPS = ["读取清单、对上商品编号", "查询门店销量与库存", "查询五家门店库存（调货用）",
               "读取供应商报价（箱规、件价）", "对比清单数量"]


def algo_params(settings):
    """算法参数（和 reorder_page.algo_params 一样；这里不能 import 界面模块）"""
    import forecast as F
    return dict(F.DEFAULT_PARAMS, **(settings.get("algo") or {}))


def check_params(settings):
    """检查清单用的参数（和界面上的选项同一份设置：reorder.cycle / days / use_supplier_cycles + 算法设置）"""
    cfg = settings.get("reorder") or {}
    return dict(days=[int(d) for d in (cfg.get("days") or R.DEFAULT_SALES_DAYS)],
                cycle=int(cfg.get("cycle", 30)),
                use_cfg=bool(cfg.get("use_supplier_cycles", True)),
                algo=algo_params(settings))


def load_lists(items):
    """[(门店编号或 None, 清单文件路径)] → run_check 要的 lists；门店没给就按文件名认（认不出抛错）。
    同一家门店给了两份，用后面那份（和界面一样）"""
    lists = []
    for sid, path in items:
        info = R.parse_order_file(path)
        sid = sid or R.guess_store(path)
        if not sid:
            raise ValueError(tr("文件名里看不出是哪家门店：{0}", os.path.basename(path)))
        lists = [x for x in lists if x["store"] != sid]
        lists.append({"store": sid, "unit": "colli", "path": path, "rows": [dict(r) for r in info["rows"]]})
    return lists


def run_check(settings, lists, *, price_dir, log=print, progress=None, days=None, cycle=None, use_cfg=None, algo=None):
    """
    检查门店订货清单：查数据库（本店 + 其他门店）、Griglia、品牌供应商、在途、报价 → (rows, meta)。
    lists = [{"store", "unit", "path", "rows"}]（load_lists 或界面给的）；参数没给就用设置里的。
    出错抛异常（界面 / 命令行自己显示）。只读：不写任何文件、不改数据库（只写缓存）。
    """
    progress = progress or NullProgress()
    p = check_params(settings)
    days = p["days"] if days is None else days
    cycle = p["cycle"] if cycle is None else cycle
    use_cfg = p["use_cfg"] if use_cfg is None else use_cfg
    algo = p["algo"] if algo is None else algo
    dbs = settings.get("db") or None
    q_pool = None
    try:
        progress.post_step(0, "running", "", "")
        progress.post_progress(5)
        # 报价（Excel）和数据库同时读：读报价主要用电脑，查数据库主要在等服务器，一起做省时间
        from concurrent.futures import ThreadPoolExecutor
        q_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="quotes")
        t_q = time.time()
        q_fut = q_pool.submit(lambda: (read_quotes(settings, price_dir, log), time.time()))   # 记下读完的时间
        import order_record as REC
        tr_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="transit")
        settings_ = settings
        tr_fut = tr_pool.submit(lambda: REC.cached(settings_) if REC.tracking_folder(settings_) else None)
        tr_pool.shutdown(wait=False)
        db = R.make_db(dbs)
        need = [r["ean"] for x in lists for r in x["rows"] if not r["code"] and r["ean"]]
        if need:
            m = R.codes_from_eans(db, need)
            for x in lists:
                for r in x["rows"]:
                    if not r["code"] and r["ean"] in m:
                        r["code"] = m[r["ean"]]
        union = []
        for x in lists:
            x["codes"] = [r["code"] for r in x["rows"] if r["code"]]
            union += [c for c in x["codes"] if c not in union]
        progress.post_step(0, "done", f"{len(union)}", "")
        progress.post_step(1, "running", "", "")
        t0 = time.time()
        ms = R.ping(db)
        if ms is not None:
            log(tr("[检查清单] 数据库响应 {0:.0f} 毫秒", ms))
        try:
            upto = R.sales_latest(db)
            late = R.stale_days(upto)
            if late is not None and late > R.STALE_DAYS:
                log(tr("⚠ 数据库里最新的销售是 {0}，已经 {1} 天没同步：销量和库存都是那天的", upto.strftime("%d/%m/%Y %H:%M"), late))
        except Exception:
            pass
        pre_snap = None
        # 供应商编号（只显示用）和下面的门店查询同时查，不用排在最后等（结果一样）
        sup_fut = q_pool.submit(R._timed, "供应商编号", None, R.supplier_info, db, union) if union else None
        others = [k for k in R.STORES if k != 0]
        if len(lists) == 1 and lists[0]["codes"] and union:
            # 只有一份清单（平常都是）：这家店和其他门店（调货用）同时查，结果和一家家查一样
            x0 = lists[0]
            log(tr("[检查清单] 门店 {0}，{1} 个商品", R.STORES.get(x0["store"], x0["store"]), len(x0["codes"])))
            every = [x0["store"]] + [k for k in others if k != x0["store"]]

            def one(sid):
                return R.compute_suggestions(db, sid, x0["codes"] if sid == x0["store"] else union, days, cycle,
                                             None, use_supplier_cycles=use_cfg, algo=algo)
            try:
                dfs = R.per_store(every, one, lambda k, sid: (
                    progress.post_step(1, "running", f"{k + 1} / {len(every)}", R.STORES.get(sid, "")),
                    progress.post_progress(15 + 70 * k / max(1, len(every)))))
                x0["df"] = dfs[x0["store"]]
                x0["result"] = R.to_rows(x0["df"], x0["rows"], days)
                pre_snap = {sid: R.stock_of(dfs[sid], days) for sid in others if sid in dfs}
            except Exception as e:
                x0.pop("df", None)
                log(tr("⚠ 几家门店一起查没成功，改成一家家查：{0}", e))
        for k, x in enumerate(lists):
            if x.get("df") is not None:
                continue
            sid = x["store"]
            log(tr("[检查清单] 门店 {0}，{1} 个商品", R.STORES.get(sid, sid), len(x["codes"])))
            progress.post_step(1, "running", f"{k + 1} / {len(lists)}", R.STORES.get(sid, ""))
            progress.post_progress(15 + 40 * k / max(1, len(lists)))
            x["df"], x["result"] = None, []
            if x["codes"]:
                x["df"] = R.compute_suggestions(db, sid, x["codes"], days, cycle, None, use_supplier_cycles=use_cfg,
                                                algo=algo)
                x["result"] = R.to_rows(x["df"], x["rows"], days)
        progress.post_step(1, "done", f"{len(lists)} / {len(lists)}", "")
        # 五家门店这批商品（所有清单合起来）的库存和日均：先算门店间调货，比价时算够卖天数（查不到不影响检查）
        progress.post_step(2, "running", "", "")
        progress.post_progress(55)
        snap = pre_snap
        if union and snap is None:
            try:
                others = [k for k in R.STORES if k != 0]
                one = lists[0] if len(lists) == 1 else None          # 只有一份清单：它自己那家不用再查
                snap = R.stock_snapshot(
                    db, union, days, cycle, others, use_cfg, algo, own=one["store"] if one else None,
                    own_df=one["df"] if one else None,
                    on_store=lambda k, sid: (progress.post_step(2, "running", f"{k + 1} / {len(others)}", R.STORES.get(sid, "")),
                                             progress.post_progress(55 + 30 * k / max(1, len(others)))))
                progress.post_step(2, "done", f"{len(others)} / {len(others)}", "")
            except Exception as e:
                log(tr("⚠ 其他门店库存没查到（不能算调货）：{0}", e))
                progress.post_step(2, "error", "", str(e))
        else:
            progress.post_step(2, "done", "", "")
        sup_info = sup_fut.result() if sup_fut is not None else {}     # Codice Fornitore + 供应商名（只显示用）
        log(tr("[检查清单] 数据库查询用了 {0:.0f} 秒", time.time() - t0))
        db.engine.dispose()
        rows = []
        for x in lists:
            for r in R.check_rows(x["rows"], x["result"], x["unit"]):
                r["store"] = x["store"]
                rows.append(r)
        R.add_supplier_info(rows, sup_info)
        if rows and not any(r.get("in_db") for r in rows):
            # 一个都没在数据库里找到：几乎一定是数据库刚才没连上或者清单不对，不能拿这个结果去生成主订单
            raise RuntimeError(tr("清单里的 {0} 个商品在数据库里一个都没找到：可能是数据库刚才没连上，或者清单里的条码 / 商品编号不对。请检查数据库连接后再点一次「开始」。",
                                  len(rows)))
        # 箱规、件价用 distribuire 里最便宜那家的报价（比价默认挑它）；报价里没有的才用数据库的
        progress.post_step(3, "running", "", "")
        progress.post_progress(86)
        # Griglia：不在我们 DETERSIVI 总表里的商品单独列出来（工作文件夹「0 Griglia」里没有就不检查）
        try:
            import griglia as G
            grid = G.load_for(os.path.dirname(price_dir)) if price_dir else None
        except Exception as e:
            grid = None
            log(tr("⚠ Griglia 读不了：{0}", e))
        if grid:
            for x in rows:
                x["in_grid"] = G.contains(grid, x.get("ean"), x.get("code"))
            log(tr("[检查清单] Griglia {0}：{1} 个商品不在里面", grid["name"],
                   sum(1 for x in rows if x.get("in_grid") is False)))
        # 非 Griglia 商品能不能直接向品牌供应商订：到 LUCAS定制采购的 SCHEDA 里找（门店报告用；读不了不影响检查）
        try:
            import store_orders as SO
            from settings import app_dir
            # 总仓采购 + 门店采购两个文件夹的 SCHEDA 都算（门店采购的就是门店自己能直接订的供应商）
            sroot = [r for r in ((settings.get("store_orders") or {}).get("folder") or "",
                                 (settings.get("store_pdv") or {}).get("folder") or SO.PDV_DEFAULT_FOLDER)
                     if r and os.path.isdir(r)]
            if sroot and any(x.get("in_grid") is False for x in rows):
                idx = SO.scheda_index(sroot, os.path.join(app_dir(), "cache", "schede_index.json"), log)
                for x in rows:
                    if x.get("in_grid") is False:
                        x["brand"] = SO.brand_supplier(idx, x.get("ean"), x.get("code"))
                log(tr("[检查清单] 品牌供应商配单文件 {0} 份：{1} 个非 Griglia 商品可以直接向品牌供应商订", idx["files"],
                       sum(1 for x in rows if x.get("brand"))))
        except Exception as e:
            log(tr("⚠ 品牌供应商配单文件读不了：{0}", e))
        # 在途（已经订了还没到货）：确定建议箱数时扣掉（init_boxes）
        transit = None
        try:
            transit = tr_fut.result()
        except Exception as e:
            log(tr("⚠ 在途（RECORD ORDINE）读不了：{0}", e))
        n_tr = 0
        deduct = REC.deduct_on(settings)          # 开关关了：在途只显示，不扣
        for x in rows:
            t_ = REC.lookup(transit, x.get("store"), x.get("ean"), x.get("code")) if (transit and deduct) else None
            x.pop("transit_pcs", None)
            if t_:
                x["transit_pcs"] = float(t_["pcs"])
                n_tr += 1
        if n_tr:
            log(tr("在途：{0} 个商品已经订了还没到货，建议箱数里扣掉了", n_tr))
        q_files, q_end = q_fut.result()
        n_q, n_files = apply_quotes(settings, rows, price_dir, log, files=q_files)
        if n_files:
            log(tr("[检查清单] 读报价用了 {0:.0f} 秒（和数据库同时读）", q_end - t_q))
        progress.post_step(3, "done", tr("{0} 份报价", n_files) if n_files else tr("没有报价，用数据库"), "")
        progress.post_step(4, "running", "", "")
        progress.post_progress(95)
        return rows, dict(stores=[x["store"] for x in lists], days=days, cycle=cycle,
                          units={x["store"]: x["unit"] for x in lists},
                          files={x["store"]: x["path"] for x in lists}, snap=snap,
                          quoted=n_q, quote_files=n_files,
                          quote_labels=list(q_files.keys()) if q_files else [],
                          store=lists[0]["store"], transit=transit)
    finally:
        if q_pool is not None:
            q_pool.shutdown(wait=False)


def read_quotes(settings, price_dir, log):
    """读 price_dir 里的全部报价：{供应商名: {条码: 报价}}；没有文件夹返回 None。可以在后台线程里跑"""
    if not price_dir or not os.path.isdir(price_dir):
        return None
    com = False
    try:                        # 个别报价还要用 Excel 算公式：后台线程里先准备好 Excel 调用
        import pythoncom
        pythoncom.CoInitialize()
        com = True
    except Exception:
        pass
    try:
        return read_quote_files(settings, price_dir, log)
    finally:
        if com:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


def apply_quotes(settings, rows, price_dir, log, files=False):
    """读 price_dir 里的报价，检查清单的行改用最便宜那家的每箱件数和件价：(改了几行, 读了几份报价)
    files：已经提前读好的报价（read_quotes 的结果）；不给就现在读"""
    if files is False:
        files = read_quotes(settings, price_dir, log)
    if files is None:
        log(tr("[检查清单] 没找到供应商报价文件夹，箱规和件价用数据库的"))
        return 0, 0
    import price_check as PC
    if not files:
        log(tr("[检查清单] 供应商报价文件夹里没有报价，箱规和件价用数据库的"))
        return 0, 0
    n = PC.apply_quotes(rows, PC.best_quotes(files))
    for x in rows:                                  # 这几家报价里都没有（比价时会成为缺失）
        x["in_quote"] = bool(x.get("quote_sup"))
    diff = sum(1 for x in rows if x.get("quote_sup") and x.get("db_pack") and abs(x["pack"] - x["db_pack"]) > 1e-9)
    log(tr("[检查清单] {0} 份报价：{1} 个商品改用最便宜那家的箱规和件价，其中 {2} 个箱规和数据库不一样",
           len(files), n, diff))
    return n, len(files)


def read_quote_files(settings, price_dir, log):
    import service as SV
    import supplier_names as SN
    import workspace as W
    files = {}
    for f in sorted(os.listdir(price_dir)):
        low = f.lower()
        if not low.endswith((".xlsx", ".xls")) or f.startswith(("~", "$")) or not W.is_price_name(f):
            continue
        try:
            name = SN.label_of(f, settings)
            files[name if name not in files else f"{name} ({SN.stem(f)})"] = SV.read_price_file(os.path.join(price_dir, f))
        except Exception as e:
            log(tr("⚠ 报价文件读不了，跳过：{0}（{1}）", f, e))
    return files


# ===================================================================== 审核规则（建议箱数）
def review(settings, rows, meta=None):
    """检查结果 → 每行的清单箱数 / 建议箱数 / 确定箱数 + 判断（和界面打开检查窗口时一样：init_boxes）"""
    import check_rules as CR
    CR.init_boxes(rows, None, algo_params(settings), settings.get("reorder") or {})
    for x in rows:
        x["verdict"] = R.review_verdict(x["lbox"], x["fbox"], False, x.get("in_db", True), None, tr("照清单订"))[0]
    return rows


# ===================================================================== 审计日志
def audit(actor, via, command, detail=None, result=None):
    """谁（actor）、从哪（via：desktop / cli / agent）、做了什么（command + detail）、结果（result）。
    一行一条 JSON，只加不改：logs/audit.jsonl（以后搬到采购服务器统一记）。写不了不影响业务"""
    import json
    from datetime import datetime
    try:
        from settings import app_dir
        d = os.path.join(app_dir(), "logs")
        os.makedirs(d, exist_ok=True)
        line = json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "actor": actor, "via": via,
                           "command": command, "detail": detail, "result": result}, ensure_ascii=False, default=str)
        with open(os.path.join(d, "audit.jsonl"), "a", encoding="utf-8", newline="") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ===================================================================== 订单跟踪
def tracking_store(settings, me=None):
    """打开共享的订单跟踪（没设置文件夹抛错）"""
    import order_record as REC
    import order_tracking as OT
    folder = REC.tracking_folder(settings)
    if not folder:
        raise RuntimeError(tr("还没设置订单跟踪文件夹（采购订单跟踪 → 设置）"))
    cfg = settings.get("tracking") or {}
    me = (me or cfg.get("me") or settings.get("user_name") or OT.default_me()).strip()
    return OT.Store(folder, me=me).reload()


def list_drafts(st):
    """待登记（程序生成、还没确认已下单）的订单"""
    return [dict(o, id=oid) for oid, o in st.orders.items() if o.get("draft")]


def confirm_orders(st, ids, via="desktop", today=None):
    """确认已下单：待登记 → 已下单（下单日期 = 今天）。返回确认了几张；记审计日志"""
    import order_tracking as OT
    ids = list(ids)
    n = OT.confirm_drafts(st, ids, today=today)
    audit(st.me, via, "confirm_orders", {"ids": ids}, {"confirmed": n})
    return n


# ===================================================================== 邮件（第 1 阶段：只读摸底）
def mail_password(address):
    import credentials
    import mail_client as MC
    return credentials.get(MC.cred_name(address))


def mail_save_account(settings, address, password=None, host=None, port=None):
    """加 / 改一个邮箱：地址、服务器存设置，安全密码存本机保管处（password=None 不改密码）"""
    import credentials
    import mail_client as MC
    address = (address or "").strip()
    if not address or "@" not in address:
        raise ValueError(tr("邮箱地址不对：{0}", address))
    box = settings.setdefault("mail", {}).setdefault("accounts", [])
    acc = next((a for a in box if (a.get("address") or "").lower() == address.lower()), None)
    if acc is None:
        acc = {"address": address}
        box.append(acc)
    acc["host"] = (host or acc.get("host") or MC.DEFAULT_HOST).strip()
    acc["port"] = int(port or acc.get("port") or MC.DEFAULT_PORT)
    if password is not None:
        credentials.set(MC.cred_name(address), password)
    return acc


def mail_remove_account(settings, address):
    import credentials
    import mail_client as MC
    box = (settings.get("mail") or {}).get("accounts") or []
    settings.setdefault("mail", {})["accounts"] = [a for a in box if (a.get("address") or "").lower() != address.lower()]
    credentials.delete(MC.cred_name(address))


def mail_test(settings, address):
    """测试连接：返回给人看的一句话；失败抛 mail_client.MailError"""
    import mail_client as MC
    acc = next(a for a in MC.accounts(settings) if a["address"].lower() == address.lower())
    nf, n = MC.test_account(acc, mail_password(address))
    return tr("✔ 连上了：{0} 个文件夹，收件箱 {1} 封", nf, n)


def mail_survey(settings, out_dir, days=28, log=print, progress=None, cancel=None, actor=None, via="desktop"):
    """邮件摸底（只读）：所有设置了的邮箱，收件箱 + 已发送，最近 days 天的信头 → Excel。返回 (路径, 信数, 错误)"""
    import mail_client as MC
    from datetime import datetime
    accs = MC.accounts(settings)
    if not accs:
        raise RuntimeError(tr("还没设置邮箱（系统设置 → 邮箱）"))
    rows, summary, errors = MC.survey(accs, mail_password, days=days, log=log, progress=progress, cancel=cancel)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"邮件摸底_{datetime.now():%Y%m%d_%H%M}.xlsx")
    MC.write_survey(path, rows, summary, days)
    audit(actor or settings.get("user_name") or "", via, "mail_survey",
          {"mailboxes": [a["address"] for a in accs], "days": days}, {"mails": len(rows), "errors": len(errors)})
    return path, len(rows), errors
