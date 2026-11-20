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
        q_pool = _quotes_pool()          # 一直留着的两条线程（读报价可能用 Excel：线程退出时拆 COM 会和界面互相卡死，2026-11-16）
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
            mine = R.check_rows(x["rows"], x["result"], x["unit"])
            for r in mine:
                r["store"] = x["store"]
                rows.append(r)
            # 清单几行 → 检查里几个商品：差的写清楚（合并的、数据库里没有的），免得以为没读到（2026-11-15）
            rep = R.list_report(x["rows"], mine)
            line = tr("[检查清单] {0}：清单 {1} 行 → {2} 个商品", R.STORES.get(x["store"], x["store"]), rep["lines"], rep["items"])
            if rep["nodb"]:
                line += tr("；数据库里没有（新商品？照清单订，在表格里判断写「数据库没有」）{0} 个：{1}", len(rep["nodb"]),
                           "、".join(n[:30] for n in rep["nodb"][:20]))
            if rep["merged"]:
                line += tr("；同一个商品写了几行、合成一行：{0}", "、".join(
                    f"{a[:30]} ← {'、'.join(n[:30] for n in b)}" for a, b in rep["merged"][:10]))
            log(line)
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
            # 停售的旧商品找过替代品的（找替代商品 → 替换进 Griglia）：门店清单里还写着旧条码 → 换成新商品去采购
            try:
                import griglia_replace as GR
                from utils import clean_ean
                subs = GR.substitutes(GR.load_done(os.path.dirname(grid["path"])), grid)
                n_s = GR.apply_substitutes(rows, subs, clean_ean)
                if n_s:
                    log(tr("[检查清单] {0} 个停售的商品换成了替代商品：{1}", n_s, "、".join(
                        f"{x['repl']['old_ean']} → {x['ean']}" for x in rows if x.get("repl"))[:400]))
            except Exception as e:
                log(tr("⚠ 替代商品没换上：{0}", e))
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
                ovr = dict((settings.get("store_pdv") or {}).get("moq_override") or {})
                ovr.update((settings.get("store_orders") or {}).get("moq_override") or {})
                for x in rows:
                    if x.get("in_grid") is False:
                        bi = SO.brand_item(idx, x.get("ean"), x.get("code"))
                        x["brand"] = bi[0] if bi else None
                        if bi:            # 门店报告右边的统计：直厂这几个商品多少箱、多少钱、起订量
                            x["brand_pack"], x["brand_price"] = bi[1], bi[2]
                            x["brand_moq"] = SO.apply_override(idx["moq"].get(bi[0]) or {}, ovr.get(bi[0]))
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
        pass                             # 读报价的线程留着下次用，不关


_QPOOL = []


def _quotes_pool():
    """读报价 / 查供应商编号用的两条线程：建一次一直用（不退出）"""
    if not _QPOOL:
        from concurrent.futures import ThreadPoolExecutor
        _QPOOL.append(ThreadPoolExecutor(max_workers=2, thread_name_prefix="quotes"))
    return _QPOOL[0]


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
    diff = sum(1 for x in rows if x.get("quote_sup") and x.get("db_pack") and abs(x.get("quote_pack", x["pack"]) - x["db_pack"]) > 1e-9)
    log(tr("[检查清单] {0} 份报价：{1} 个商品改用最便宜那家的件价（箱数照数据库箱规），其中 {2} 个报价箱规和数据库不一样",
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


def mail_save_account(settings, address, password=None, host=None, port=None, store=False):
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
    if store is not False:                       # 门店：没写门店的信算哪家（None = 不设，几家店都管）
        acc["store"] = store or ""
    if password is not None:
        credentials.set(MC.cred_name(address), password)
    return acc


def mail_remove_account(settings, address):
    import credentials
    import mail_client as MC
    box = (settings.get("mail") or {}).get("accounts") or []
    settings.setdefault("mail", {})["accounts"] = [a for a in box if (a.get("address") or "").lower() != address.lower()]
    credentials.delete(MC.cred_name(address))


def mail_test(settings, address, smtp_factory=None):
    """测试连接：收信（IMAP）+ 发信（SMTP 只登录、不发信）。返回给人看的两行；有一样不行抛 mail_client.MailError"""
    import mail_client as MC
    recv = _imap_test(settings, address)
    acc = next(a for a in MC.accounts(settings) if a["address"].lower() == address.lower())
    try:
        send = _smtp_test(settings, acc, mail_password(address), smtp_factory)
    except MC.MailError as e:
        raise MC.MailError(recv + "\n" + str(e))
    return recv + "\n" + send


def _smtp_test(settings, acc, pw, smtp_factory=None):
    """发信服务器登录一下：密码被拒先换另一个阿里地址试，能登录就记进设置（以后发信用它）"""
    import smtplib
    import mail_client as MC
    import mail_send as MS
    host, port = MS.smtp_of(acc)
    try:
        MS.smtp_check(acc, pw, smtp_factory)
        return tr("✔ 发信：能登录 {0}（只测登录，没发信）", host)
    except smtplib.SMTPAuthenticationError:
        alt = MS.SMTP_ALT.get(host.lower())
        if alt:
            try:
                MS.smtp_check(acc, pw, smtp_factory, host=alt)
            except (smtplib.SMTPException, OSError):
                pass
            else:
                box = (settings.get("mail") or {}).get("accounts") or []
                for a in box:
                    if (a.get("address") or "").lower() == acc["address"].lower():
                        a["smtp"] = alt
                return tr("✔ 发信：能登录 {0}（发信服务器已改成它，只测登录，没发信）", alt)
        raise MC.MailError(tr("✘ 发信：{0} 拒绝了密码。收信能登录说明密码是对的，可能是这个邮箱没开 SMTP："
                              "请在阿里邮箱网页版「设置 → 客户端设置」里打开 SMTP，或者用网页版生成的安全密码再试", host))
    except (smtplib.SMTPException, OSError) as e:
        raise MC.MailError(tr("✘ 发信：连不上 {0}:{1}（{2}）。可能是网络 / 防火墙挡了 {1} 端口", host, port, e))


def _imap_test(settings, address):
    """收信测试：返回给人看的一句话；失败抛 mail_client.MailError"""
    import mail_client as MC
    acc = next(a for a in MC.accounts(settings) if a["address"].lower() == address.lower())
    pw = mail_password(address)
    try:
        nf, n = MC.test_account(acc, pw)
        return tr("✔ 收信：{0} 个文件夹，收件箱 {1} 封", nf, n)
    except MC.LoginFailed:
        why, host = MC.diagnose_login(acc, pw)               # 登录被拒：分清是密码不对还是没开 IMAP
    if why == "other_host":                                  # 换个服务器地址就行：直接改好
        mail_save_account(settings, address, None, host, acc["port"])
        nf, n = MC.test_account(dict(acc, host=host), pw)
        return tr("✔ 收信：{0} 个文件夹，收件箱 {1} 封（服务器已改成 {2}）", nf, n, host)
    if why == "no_imap":
        raise MC.MailError(tr("{0}：密码是对的（用 POP3 能登录，就是大家邮件软件现在收信的方式），但这个邮箱还没开 IMAP。"
                              "请在阿里邮箱网页版「设置」里打开 IMAP（客户端设置 / POP3·IMAP 那一项）；找不到的话请管理员在后台"
                              "给这个账号开通 IMAP。打开 IMAP 不影响大家现在用 POP3 收信。", address))
    if why == "bad_password":
        raise MC.MailError(tr("{0}：密码不对（用 POP3 也登录不了）。请重新填一次登录密码，或者用网页版生成的安全密码。", address))
    raise MC.MailError(tr("{0}：登录被拒，原因看不出来（POP3 服务器也连不上）。请检查网络，或者把这个提示发给管理员。", address))


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


# ===================================================================== 邮件 → 订单跟踪（2026-11-07）
MAIL_TRACK_DAYS = 21


def _mail_ignored_path():
    from settings import app_dir
    return os.path.join(app_dir(), "cache", "mail_ignored.json")


def mail_ignored():
    """这台电脑点过「忽略」的建议（键的集合）"""
    import json
    try:
        with open(_mail_ignored_path(), encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def mail_ignore(keys):
    import json
    keys = mail_ignored() | set(keys)
    p = _mail_ignored_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        json.dump(sorted(keys), f, ensure_ascii=False)
    return len(keys)


def mail_tracking(settings, st=None, days=None, log=print, progress=None, cancel=None, actor=None, via="desktop",
                  factory=None, today=None):
    """
    读所有邮箱（收件箱 + 已发送）最近 days 天的信头（只读，读过的有缓存），对到订单跟踪 → 建议（还没改任何东西）。
    返回 {"suggest", "missing", "dups", "mails", "errors", "store"}
    """
    import mail_client as MC
    import mail_match as MM
    accs = MC.accounts(settings)
    if not accs:
        raise RuntimeError(tr("还没设置邮箱（系统设置 → 邮箱）"))
    st = st or tracking_store(settings)
    days = int(days or (settings.get("mail") or {}).get("track_days") or MAIL_TRACK_DAYS)
    from settings import app_dir
    mails, errors = MC.collect(accs, mail_password, days=days, cache_dir=os.path.join(app_dir(), "cache", "mail"),
                               log=log, progress=progress, cancel=cancel, factory=factory, today=today)
    own = {a["address"].lower() for a in accs}
    ign = mail_ignored()
    stores = {a["address"].lower(): a.get("store") for a in accs}
    bodies = _bodies_load()
    for m in mails:
        b = bodies.get(_body_key(m))
        if b is not None:
            m["body"] = b
    res = MM.analyze(mails, st, own, stores, today=today, ignored=ign)
    # 发货 / 确认信没写门店、却对上了几家店的单 → 读这几封信的正文（只读）看是哪家店，再对一次
    tried = set()
    for _round in range(3):          # 读了一封的正文、它对上了自己那家店，同一批的下一封信可能又要读
        need = [e["row"] for e in res["events"] if e.get("need_body") and _body_key(e["row"]) not in tried]
        if not need:
            break
        tried.update(_body_key(m) for m in need)
        got = 0
        for acc in accs:
            rows = [m for m in need if (m.get("mailbox") or "").lower() == acc["address"].lower()]
            if not rows:
                continue
            try:
                txt = MC.fetch_bodies(acc, mail_password(acc["address"]), rows, factory=factory)
            except Exception as e:
                log(f"  ⚠ {acc['address']}：读正文失败（{e}）")
                continue
            for m in rows:
                t = txt.get((m.get("folder"), str(m.get("uid"))))
                if t is not None:
                    m["body"] = bodies[_body_key(m)] = t
                    got += 1
        if not got:
            break
        _bodies_save(bodies)
        log(tr("  读了 {0} 封发货 / 确认信的正文，看是哪家门店", got))
        res = MM.analyze(mails, st, own, stores, today=today, ignored=ign)
    res["dups"] = MM.dup_suggestions(st, ign)
    res["mails"], res["errors"], res["store"] = len(mails), errors, st
    audit(actor or st.me, via, "mail_tracking", {"mailboxes": [a["address"] for a in accs], "days": days},
          {"mails": len(mails), "suggest": len(res["suggest"]), "missing": len(res["missing"]), "dups": len(res["dups"]),
           "errors": len(errors)})
    return res


def _bodies_path():
    from settings import app_dir
    return os.path.join(app_dir(), "cache", "mail_bodies.json")


def _body_key(m):
    return f"{(m.get('mailbox') or '').lower()}|{m.get('folder')}|{m.get('uid')}"


def _bodies_load():
    import json
    try:
        with open(_bodies_path(), encoding="utf-8") as f:
            return dict(json.load(f))
    except (OSError, ValueError):
        return {}


def _bodies_save(d):
    import json
    p = _bodies_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if len(d) > 3000:                                   # 只留最近的
        d = dict(list(d.items())[-3000:])
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)


# ===================================================================== 订单窗口「邮件」页（2026-11-08）
VIEW_TEXT = 30000                                         # 看信时正文最多读这么多字


def order_mails(st, oid):
    """这张单的邮件（不连网，用本机缓存的信头）：[{"kind", "date", "subject", "from", "mail"}]，见 mail_match.related"""
    import mail_match as MM
    o = st.orders.get(oid) or {}
    return MM.related(o, _cached_mails())


def _acc_of(settings, mailbox):
    import mail_client as MC
    acc = next((a for a in MC.accounts(settings) if a["address"].lower() == (mailbox or "").lower()), None)
    if acc is None:
        raise RuntimeError(tr("这封信在 {0} 里，这个邮箱没在设置里", mailbox))
    pw = mail_password(acc["address"])
    if not pw:
        raise RuntimeError(tr("{0} 还没填密码（系统设置 → 邮箱）", acc["address"]))
    return acc, pw


def mail_view_body(settings, m, factory=None):
    """一封信的正文（只读：EXAMINE + BODY.PEEK，不会在 Foxmail 里变成已读）。读过的存在 cache/mail_bodies.json"""
    import mail_client as MC
    key = "view|" + _body_key(m)
    cache = _bodies_load()
    if key in cache:
        return cache[key]
    acc, pw = _acc_of(settings, m.get("mailbox"))
    got = MC.fetch_bodies(acc, pw, [m], factory=factory, max_bytes=400000, text_limit=VIEW_TEXT)
    text = got.get((m.get("folder"), str(m.get("uid"))))
    if text is None:
        raise RuntimeError(tr("服务器上找不到这封信了（可能被删了或挪了文件夹）"))
    cache[key] = text
    _bodies_save(cache)
    return text


def mail_attachment(settings, m, part, factory=None):
    """下载一封信的一个附件到 cache/mail_att/（只读），已经下过的直接用。part = [段号, 文件名, 编码, 大小]。返回路径"""
    import hashlib
    import mail_client as MC
    from settings import app_dir
    d = os.path.join(app_dir(), "cache", "mail_att", hashlib.sha1(_body_key(m).encode("utf-8")).hexdigest()[:12])
    import re as _re
    name = _re.sub(r'[\\/:*?"<>|\r\n]+', "_", part[1] or "allegato").strip() or "allegato"
    p = os.path.join(d, name)
    if os.path.exists(p) and os.path.getsize(p) > 0:
        return p
    acc, pw = _acc_of(settings, m.get("mailbox"))
    out = MC.download(acc, pw, [{"folder_raw": m.get("folder_raw") or "INBOX", "uid": m["uid"], "section": part[0],
                                 "encoding": part[2], "name": name}], d, factory=factory)
    return out[0]


# ===================================================================== 发订单邮件（2026-11-07，第 3 阶段）
_MAIL_CACHE = {"sig": None, "rows": []}
_HIST_CACHE = {"key": None, "hist": None}


def _cached_mails():
    """各邮箱读过的信头缓存（cache/mail/*.json，邮件核对 / 报价检查时存的），不连网。
    文件没变（路径 + 大小 + 修改时间）就用内存里读好的（几 MB 的 JSON，2026-11-08 发订单窗口要快）"""
    import glob
    import json
    from settings import app_dir
    paths = sorted(glob.glob(os.path.join(app_dir(), "cache", "mail", "*.json")))
    sig = []
    for p in paths:
        try:
            stt = os.stat(p)
            sig.append((p, stt.st_size, stt.st_mtime_ns))
        except OSError:
            pass
    sig = tuple(sig)
    if sig == _MAIL_CACHE["sig"]:
        return _MAIL_CACHE["rows"]
    rows = []
    for p, _sz, _mt in sig:
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        for _label, fo in (d or {}).items():
            rows.extend((fo or {}).get("rows", {}).values())
    _MAIL_CACHE.update(sig=sig, rows=rows)
    return rows


def _sent_history(st, own):
    """以前发过的订单信（mail_send.sent_history），信头缓存和订单跟踪都没变就不重算"""
    import mail_send as MS
    mails = _cached_mails()
    key = (_MAIL_CACHE["sig"], id(st), len(st.orders), sum(len(h) for h in st.hist.values()), len(st.aliases), frozenset(own))
    if _HIST_CACHE["key"] != key:
        _HIST_CACHE.update(key=key, hist=MS.sent_history(mails, st, own))
    return _HIST_CACHE["hist"]


def _cached_body(m):
    """读过的正文（看信 / 发订单时存的），没有返回 None"""
    if not m or not m.get("uid"):
        return None
    t = _bodies_load().get("view|" + _body_key(m))
    return t if t and t.strip() else None


def order_mail_draft(settings, st, oid, fetch_body=True, factory=None, today=None):
    """
    要发的那封订单信：一起发的单（同一个订单文件）、发件邮箱、收件人、抄送、主题、正文、附件。
    正文：以前发给这家的最近一封订单信的正文，没有就用默认模板。读过的正文直接用（不连网）；
    还没读过的：fetch_body=True 时现在连邮箱读（只读），False 时先用模板、d["need_last"] = True，
    窗口打开后再在后台读（order_mail_last_body），窗口不用等
    """
    import mail_client as MC
    import mail_send as MS
    o = st.orders.get(oid)
    if not o:
        raise RuntimeError(tr("找不到这张单"))
    ids = MS.group_of(st, oid)
    stores = MS.stores_of(st, ids)
    accs = MC.accounts(settings)
    if not accs:
        raise RuntimeError(tr("还没设置邮箱（系统设置 → 邮箱）"))
    own = {a["address"].lower() for a in accs}
    hist = _sent_history(st, own)
    acc = MS.sender_for(stores, accs)
    supplier = str(o.get("supplier") or "")
    to, to_src = MS.recipients(st, supplier, hist)
    subject = MS.subject_for(supplier, stores, today)
    body, body_src = MS.default_body(supplier, stores), "template"
    last = MS.last_sent(hist, st, supplier, acc["address"])
    need_last = False
    if last and last.get("uid"):
        t = _cached_body(last)
        if t is None and fetch_body:
            try:
                t = mail_view_body(settings, last, factory=factory)
            except Exception:
                t = None
        if t and t.strip():
            body, body_src = MS.body_from_last(t, last["subject"], subject, supplier), "last"
        elif not fetch_body:
            need_last = True
    files = []
    f = str(o.get("file") or "").strip()
    if f:
        files.append((f, MS.attachment_name(subject, f), os.path.exists(f)))
    return {"ids": ids, "stores": stores, "supplier": supplier, "from": acc["address"], "accounts": accs,
            "to": to, "to_src": to_src, "cc": MS.usual_cc(hist), "subject": subject, "body": body, "body_src": body_src,
            "files": files, "last": last, "need_last": need_last,
            "amount": sum(float((st.orders.get(k) or {}).get("amount") or 0) for k in ids),
            "sent_before": [k for k in ids if (st.orders.get(k) or {}).get("sent")]}


def order_mail_last_body(settings, d, subject=None, factory=None):
    """窗口打开后在后台读上次那封订单信的正文（只读），换成这次的主题 / 日期。读不到返回 None"""
    import mail_send as MS
    last = d.get("last")
    if not last:
        return None
    t = mail_view_body(settings, last, factory=factory)
    if not t or not t.strip():
        return None
    return MS.body_from_last(t, last["subject"], subject or d["subject"], d["supplier"])


def send_order(settings, st, ids, frm, to, cc, subject, body, files, via="desktop", force=False,
               smtp_factory=None, imap_factory=None, today=None):
    """
    发订单邮件 → 这几张单变「已下单」（下单日期 = 今天）、收件人存进共享通讯录、记审计日志。
    files = [(路径, 附件名)]。已经发过的单不再发（force=True 才发）。发信失败抛错，什么都不改
    """
    import mail_client as MC
    import mail_send as MS
    ids = [k for k in ids if k in st.orders]
    if not ids:
        raise RuntimeError(tr("找不到要发的单"))
    to = [a.strip() for a in to if a.strip()]
    cc = [a.strip() for a in cc if a.strip()]
    bad = [a for a in to + cc if not MS.valid_address(a)]
    if not to or bad:
        raise RuntimeError(tr("收件人不对：{0}", ", ".join(bad) or tr("没填")))
    sent_before = [k for k in ids if st.orders[k].get("sent")]
    if sent_before and not force:
        raise RuntimeError(tr("这几张单已经发过邮件了，不再重发"))
    accs = MC.accounts(settings)
    acc = next((a for a in accs if a["address"].lower() == (frm or "").lower()), None)
    if acc is None:
        raise RuntimeError(tr("发件邮箱不在设置里：{0}", frm))
    pw = mail_password(acc["address"])
    if not pw:
        raise RuntimeError(tr("{0} 还没填密码（系统设置 → 邮箱）", acc["address"]))
    for p, _n in files:
        if not os.path.exists(p):
            raise RuntimeError(tr("附件找不到：{0}", p))
    msg = MS.build(acc["address"], to, cc, subject, body, files)
    mid, saved = MS.send(acc, pw, msg, smtp_factory=smtp_factory, imap_factory=imap_factory)
    supplier = str(st.orders[ids[0]].get("supplier") or "")
    n = MS.mark_sent(st, ids, {"to": to, "from": acc["address"], "subject": subject, "mid": mid}, today=today)
    try:
        MS.save_contact(st, supplier, to)
    except OSError:
        pass
    audit(st.me, via, "send_order", {"ids": ids, "from": acc["address"], "to": to, "cc": cc, "subject": subject,
                                     "files": [n_ for _p, n_ in files], "force": force},
          {"message_id": mid, "saved_in_sent": saved, "orders": n})
    return {"mid": mid, "saved": saved, "n": n}


def mail_apply(st, sugs, via="desktop"):
    """按选中的建议改订单跟踪：邮件进展（确认 / 发货 / 已下单）、合并重复的待登记、补登记。返回 (改了几张, 撤销用)"""
    import mail_match as MM
    upd = [s for s in sugs if s["kind"] in ("order", "confirm", "ship")]
    dup = [s for s in sugs if s["kind"] == "dup"]
    miss = [s for s in sugs if s["kind"] == "missing"]
    n1, u1 = MM.apply(st, upd) if upd else (0, [])
    n2, u2 = MM.merge_duplicates(st, dup) if dup else (0, [])
    n3, u3 = MM.add_missing(st, miss) if miss else (0, [])
    audit(st.me, via, "mail_apply", {"keys": [s["key"] for s in sugs]}, {"updated": n1, "merged": n2, "added": n3})
    return n1 + n2 + n3, u1 + u2 + u3


def mail_undo(st, items, via="desktop"):
    import mail_match as MM
    MM.undo(st, items)
    audit(st.me, via, "mail_undo", {"items": len(items)}, None)


# ===================================================================== 邮件里的批发商报价 → 2 供应商报价（2026-11-07）
def _prices_done_path():
    from settings import app_dir
    return os.path.join(app_dir(), "cache", "mail_prices_done.json")


def _prices_done():
    import json
    try:
        with open(_prices_done_path(), encoding="utf-8") as f:
            return list(json.load(f))
    except (OSError, ValueError):
        return []


def mail_price_lists(settings, root, days=None, log=print, progress=None, cancel=None, via="desktop", factory=None,
                     today=None):
    """
    读邮件（只读信头，有缓存）→ 每家批发商最新一封价目表信。root = DETERSIVO采购的工作文件夹。
    返回 {"found": [...], "mails": 信数, "errors": [...], "labels": [现在的报价名称]}；found 里 newer = 比现在那份新
    """
    import mail_client as MC
    import mail_prices as MP
    import workspace as W
    from settings import app_dir
    accs = [a for a in MC.accounts(settings) if mail_password(a["address"])]
    if not accs:
        raise RuntimeError(tr("还没设置邮箱或没填密码（系统设置 → 邮箱）"))
    cur = MP.current_files(settings, W.price_dir(root))
    days = int(days or (settings.get("mail") or {}).get("track_days") or MAIL_TRACK_DAYS)
    mails, errors = MC.collect(accs, mail_password, days=days, cache_dir=os.path.join(app_dir(), "cache", "mail"),
                               log=log, progress=progress, cancel=cancel, factory=factory, today=today)
    found = MP.find(mails, list(cur), {a["address"] for a in accs}, done=_prices_done(), current=cur)
    audit(settings.get("user_name") or "", via, "mail_price_lists", {"days": days},
          {"mails": len(mails), "new": [b["label"] for b in found if b["newer"]], "errors": len(errors)})
    return {"found": found, "mails": len(mails), "errors": errors, "labels": sorted(cur)}


def _count_price_items(path):
    """比价能从这份报价读出几个商品（读不了抛 ValueError，带缺哪几列）"""
    try:
        import pythoncom                     # 后台线程里可能要开 Excel（.xls 先转 .xlsx）
        pythoncom.CoInitialize()
    except Exception:
        pass
    import service
    return len(service.read_price_file(path, log=lambda *a: None))     # 读过的（现在那份）直接用缓存


def mail_import_prices(settings, root, picks, via="desktop", factory=None, check=None):
    """
    把选中的价目表附件放进「2 供应商报价」：下载（只读）→ 替换同一家的旧报价（旧的进 _旧文件，不删）→ 名称带到新文件。
    picks = mail_price_lists 的 found 里选中的。返回 (done = [(新文件名, 替换掉的旧文件名)], undo = 给 mail_undo_prices,
    skipped = [(名称, 原因)])。
    2026-11-07 用户第一次用：邮件里的原文件表头和比价要的不一样（MELONI barcode_pezzo / Prezzo、NEW GRIECO CODICI EAN、
    PETRILLO Vista 3%），比价读不了 → 只剩一家报价。所以放进去之前先用比价的读法读一遍（check），读不了 / 商品少一大半的不放、旧的留着。
    check(路径) → 能读出的商品数（读不了抛错），默认用 service._read_price_file；测试可以换
    """
    import json
    import shutil
    import tempfile
    import file_inbox as FI
    import mail_client as MC
    import mail_prices as MP
    import supplier_names as SN
    import workspace as W
    from settings import app_dir
    folder = W.price_dir(root)
    accs = {a["address"].lower(): a for a in MC.accounts(settings)}
    tmp = tempfile.mkdtemp(prefix="casate_listini_")
    bak = os.path.join(app_dir(), "cache", "mail_prices_undo", datetime_now_tag())
    try:
        plan = []
        for b in picks:
            m = b["mail"]
            acc = accs.get((m.get("mailbox") or "").lower())
            if acc is None:
                raise RuntimeError(tr("找不到邮箱 {0}", m.get("mailbox")))
            sub = os.path.join(tmp, str(len(plan)))
            path = MC.download(acc, mail_password(acc["address"]),
                               [{"folder_raw": m.get("folder_raw") or m.get("folder"), "uid": m["uid"], "section": b["part"][0],
                                 "encoding": b["part"][2], "name": MP.file_name(b)}], sub, factory=factory)[0]
            if os.path.getsize(path) < 200:
                raise RuntimeError(tr("{0} 的附件下载下来是空的", b["label"]))
            try:                              # 改成操作员以前手动整理好的格式（用户 11-20：他们会在表格里操作）
                import quote_prepare as QP
                path, _what = QP.prepare(path, os.path.join(sub, "ok"))
            except Exception:
                pass                          # 整理不了就放原文件（程序照样读得了）
            plan.append({"src": path, "replace": b.get("current"), "auto": True, "_b": b})
        # 比价读得了才放：读不了的、商品比现在那份少一大半的不放（旧的留着）
        check = check or _count_price_items
        good, skipped = [], []
        for p in plan:
            b = p.pop("_b")
            try:
                n = check(p["src"])
            except Exception as e:
                skipped.append((b["label"], tr("比价读不了这份文件（{0}），要先手动整理表头", e)))
                try:                          # 留一份给维护的人看到底是什么格式（cache/mail_prices_failed）
                    fd = os.path.join(app_dir(), "cache", "mail_prices_failed")
                    os.makedirs(fd, exist_ok=True)
                    shutil.copy2(p["src"], os.path.join(fd, datetime_now_tag() + "_" + os.path.basename(p["src"])))
                except Exception:
                    pass
                continue
            old_n = None
            if b.get("current") and os.path.exists(os.path.join(folder, b["current"])):
                try:
                    old_n = check(os.path.join(folder, b["current"]))
                except Exception:
                    old_n = None
            if n < 20 or (old_n and n < old_n * 0.5):
                skipped.append((b["label"], tr("只读出 {0} 个商品（现在那份 {1} 个），看起来格式不对", n, old_n or "?")))
                continue
            good.append((p, b))
        if not good:
            raise RuntimeError(tr("没有放进去，原来的报价都留着：") + "\n" + "\n".join(f"· {l}：{w}" for l, w in skipped))
        plan, picks = [p for p, _b in good], [b for _p, b in good]
        old_labels = SN.labels(W.scan(root)["prices"], settings)
        undo = {"folder": folder, "new": [], "restore": [], "names": dict(settings.get("supplier_names") or {}),
                "ids": [(b["mail"].get("message_id") or "").strip() for b in picks]}
        os.makedirs(bak, exist_ok=True)
        for b in picks:                       # 撤销用：替换前的那份先复制一份
            cur = b.get("current")
            if cur and os.path.exists(os.path.join(folder, cur)):
                dst = os.path.join(bak, cur)
                shutil.copy2(os.path.join(folder, cur), dst)
                undo["restore"].append((cur, dst))
        done = FI.apply_suppliers(root, plan, folder=folder)
        undo["new"] = [n for n, _o in done]
        # 名称：替换的旧文件的名称带过来；新的一家（以前没有）直接用邮件认出的名称
        SN.carry_over(settings, done, old_labels)
        names = settings.setdefault("supplier_names", {})
        for (new, _old), b in zip(done, picks):
            if SN.label_of(new, settings) != b["label"]:
                names[new] = b["label"]
        ids = _prices_done() + [(b["mail"].get("message_id") or "").strip() for b in picks]
        p = _prices_done_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            json.dump([i for i in ids if i][-200:], f)
        audit(settings.get("user_name") or "", via, "mail_import_prices",
              {"labels": [b["label"] for b in picks]}, {"files": [n for n, _o in done]})
        return done, undo, skipped
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def datetime_now_tag():
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def mail_undo_prices(settings, undo, via="desktop"):
    """撤销 mail_import_prices：新放进来的挪进 _旧文件，原来那份放回去，供应商名称改回去，这几封信下次照样提示"""
    import json
    import shutil
    import workspace as W
    folder = undo["folder"]
    for n in undo["new"]:
        p = os.path.join(folder, n)
        if os.path.exists(p):
            W.archive(p, os.path.join(folder, W.OLD))
    for name, src in undo["restore"]:
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(folder, name))
    settings["supplier_names"] = dict(undo["names"])
    ids = [i for i in _prices_done() if i not in set(undo["ids"])]
    with open(_prices_done_path(), "w", encoding="utf-8", newline="") as f:
        json.dump(ids, f)
    audit(settings.get("user_name") or "", via, "mail_undo_prices", {"files": undo["new"]}, None)


# ===================================================================== Griglia：不做了的商品找替代品（2026-11-09）
def griglia_replacements(grid, quotes, fresh=None, struck=False):
    """
    Griglia「都不做了」的 → 这周报价里像的商品（griglia_replace.suggest），已经替代过（新商品在 Griglia 里）的不算。
    struck=True：只要 Griglia 里划了线的商品；"both"：批发商都没货的 + 划线的一起（窗口用这个，索引只建一次）
    """
    import griglia_replace as GR
    done = GR.load_done(os.path.dirname(grid["path"])) if grid.get("path") else {}
    return GR.suggest(grid, quotes, fresh=fresh, skip=GR.done_eans(done, grid), struck=struck)


GRID_BACKUPS = 5          # 本地备份（不带图片，几 MB）留最近 5 份


def _backup_griglia(target, backup_dir=None, url=None, log=None):
    """改之前：在程序文件夹 backup/griglia 存一份不带图片的备份（留最近 GRID_BACKUPS 份），返回备份路径"""
    import griglia_replace as GR
    from datetime import datetime
    bdir = backup_dir or GR.backup_dir()
    stem, ext = os.path.splitext(os.path.basename(target))
    bk = os.path.join(bdir, f"{stem}_{datetime.now():%Y%m%d_%H%M%S}{ext}")
    GR.light_copy(target, bk, url=url, log=log)
    olds = sorted(f for f in os.listdir(bdir) if f.startswith(stem + "_") and f.endswith(ext))
    for f in olds[:-GRID_BACKUPS]:
        try:
            os.remove(os.path.join(bdir, f))
        except OSError:
            pass
    return bk


def _edit_griglia_safely(target, sheet, edit, log=None):
    """
    用户 11-11：改完别的内容必须原封不动 → 先在本机临时副本上改（edit(副本路径, ops)），和原文件逐格比（内容 / 公式 / 删除线），
    只有 ops 里记的那几行变了才把副本放回去；核对不过原文件一个字都不动。返回 (edit 的结果, 比了几个格子)
    """
    import shutil
    import tempfile
    import griglia_replace as GR
    from datetime import datetime
    d, name = os.path.dirname(target), os.path.basename(target)
    st0 = os.stat(target)
    tmp_dir = tempfile.mkdtemp(prefix="casate_griglia_")
    work = os.path.join(tmp_dir, name)
    try:
        shutil.copy2(target, work)
        ops = []
        result = edit(work, ops)
        diffs, n_cells = GR.diff_cells(target, work, sheet, ops, limit=5000)
        if diffs:
            sample = "；".join(f"{c}：{GR._show(b)} → {GR._show(a)}" for c, b, a in diffs[:3])
            try:                                          # 全部不一样的格子写进 logs，方便查原因
                from settings import app_dir
                ld = os.path.join(app_dir(), "logs")
                os.makedirs(ld, exist_ok=True)
                with open(os.path.join(ld, f"griglia_check_{datetime.now():%Y%m%d_%H%M%S}.txt"), "w",
                          encoding="utf-8", newline="") as f:
                    f.write(f"{target}\nops={ops}\n")
                    for c, b_, a_ in diffs:
                        f.write(f"{c}\t{b_!r}\t{a_!r}\n")
            except Exception:
                pass
            if log:
                for c, b_, a_ in diffs[:200]:
                    log(f"⚠ Griglia 核对：{c} {b_!r} → {a_!r}")
            more = f"{len(diffs)}+" if len(diffs) >= 5000 else str(len(diffs))
            raise RuntimeError(tr("核对没通过：除了替换的那几行，还有 {0} 个格子变了（{1}），原文件没有改", more, sample))
        st1 = os.stat(target)
        if (st1.st_mtime, st1.st_size) != (st0.st_mtime, st0.st_size) or \
                os.path.exists(os.path.join(d, "~$" + name)):
            raise RuntimeError(tr("改的时候 Griglia 被别人打开或保存过（{0}），没有放回，请再试一次", name))
        tmp = target + ".casate_tmp"
        shutil.copy2(work, tmp)
        os.replace(tmp, target)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return result, n_cells


def griglia_add_replacements(grid, picks, by=None, via="desktop", excel=None, log=None, source=None, url=None,
                             backup_dir=None):
    """
    选好的替代品写进 Griglia（用户 11-09：Griglia 是从同步文件夹拿来的，就直接改同步文件夹那份，本地留备份）：
      改哪份：source（「换 Griglia…」时记下的原件，在同步文件夹）还在就改它，不然改工作文件夹「0 Griglia」那份；
      改之前：在程序文件夹 backup/griglia 存一份不带图片的备份（图片换成 =IMAGE 公式，几百 MB → 几 MB）；
      用 Excel 每个旧商品下面插一行新的、旧的字变灰；改完原件再复制回「0 Griglia」（程序读的那份跟着变）；
      记进「0 Griglia/_替代记录.json」（时间、谁、旧 → 新、哪个文件、第几行），界面「替换记录」看得到。
    picks = [(旧商品, 候选)]。返回 {"written": [(旧条码, 新商品所在行 或 None, 旧行挪到的行)], "backup": 备份路径, "file": 改的文件}
    """
    import shutil
    import griglia_replace as GR
    from datetime import datetime
    path = grid["path"]
    target = source if source and os.path.isfile(source) else path
    by = by or os.environ.get("USERNAME") or os.environ.get("USER") or ""
    d, name = os.path.dirname(target), os.path.basename(target)
    if os.path.exists(os.path.join(d, "~$" + name)):
        raise RuntimeError(tr("Griglia 正被 Excel 打开着（{0}），请先关掉再加", name))
    bk = _backup_griglia(target, backup_dir, url, log)
    plan = GR.plan_rows(grid, picks)
    written, n_cells = _edit_griglia_safely(
        target, grid.get("sheet"), lambda work, ops: GR.write_rows(work, grid, plan, excel=excel, log=log, url=url, ops=ops),
        log)
    if os.path.normcase(os.path.abspath(target)) != os.path.normcase(os.path.abspath(path)):
        shutil.copy2(target, path)                       # 工作文件夹那份跟着更新（程序读的是它）
    gdir = os.path.dirname(path)
    done = GR.load_done(gdir)
    by_old = {o["ean"]: (o, c) for o, c in picks}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for old_ean, row, moved in written:
        o, c = by_old[old_ean]
        done[old_ean] = {"new": c["ean"], "name": c["name"], "old_name": o.get("name") or "", "at": now, "by": by,
                         "file": target, "row": row, "moved_to": moved, "in_grid": bool(c.get("in_grid")),
                         "struck": bool(o.get("struck")), "size": GR.change(o.get("name") or "", c["name"])[0],
                         "best": c.get("best"), "best_sup": c.get("best_sup"), "score": c.get("score"),
                         # 新旧价格对比（用户 11-18）：旧 = Griglia 里记的（最低价 PA MIGLIORE，没有用 PREZZO NETTO），新 = 这周报价最低
                         "old_price": GR.old_price_sup(o)[0], "old_sup": GR.old_price_sup(o)[1],
                         "old_pack": o.get("pack"), "pack": c.get("pack")}
    GR.save_done(gdir, done)
    audit(by, via, "griglia_add_replacements", {"file": target, "n": len(written), "backup": bk,
                                                "pairs": [[o, by_old[o][1]["ean"]] for o, _r, _m in written]}, None)
    return {"written": written, "backup": bk, "file": target, "checked": n_cells}


def griglia_restore_old(grid, olds, by=None, via="desktop", excel=None, log=None, source=None, url=None,
                        backup_dir=None):
    """
    用户 11-18：以前 Griglia 已停用的商品替换时是原地换掉的，原商品没留在最下面 → 从程序文件夹的备份里找到那一行，
    抄回 Griglia 最下面划线（和现在替换时一样）。改法和替换一样：先备份 → 临时副本上改 → 逐格核对 → 放回。
    olds = 旧条码。返回 {"restored": [(旧条码, 行号)], "missing": [备份里也找不到的旧条码], "backup", "file", "checked"}
    """
    import shutil
    import griglia_replace as GR
    path = grid["path"]
    target = source if source and os.path.isfile(source) else path
    by = by or os.environ.get("USERNAME") or os.environ.get("USER") or ""
    d, name = os.path.dirname(target), os.path.basename(target)
    if os.path.exists(os.path.join(d, "~$" + name)):
        raise RuntimeError(tr("Griglia 正被 Excel 打开着（{0}），请先关掉再加", name))
    gdir = os.path.dirname(path)
    done = GR.load_done(gdir)
    backups = GR.list_backups(name, backup_dir)
    found = GR.find_in_backups(backups, grid, olds, excel=excel, log=log)
    missing = [o for o in olds if o not in found]
    if not found:
        return {"restored": [], "missing": missing, "backup": None, "file": target, "checked": 0}
    bk = _backup_griglia(target, backup_dir, url, log)
    items = [(o, (done.get(o) or {}).get("new"), found[o][0], found[o][1]) for o in olds if o in found]
    info = {}
    restored, n_cells = _edit_griglia_safely(
        target, grid.get("sheet"),
        lambda work, ops: GR.restore_rows(work, grid, items, excel=excel, log=log, url=url, ops=ops, info=info), log)
    if restored and os.path.normcase(os.path.abspath(target)) != os.path.normcase(os.path.abspath(path)):
        shutil.copy2(target, path)
    for o, row in restored:
        if o in done:
            done[o]["restored_to"] = row
            done[o]["moved_to"] = row
            price, sup = info.get(o, (None, ""))
            if done[o].get("old_price") is None and price is not None:
                done[o]["old_price"] = price
            if not done[o].get("old_sup") and sup:
                done[o]["old_sup"] = sup
    GR.save_done(gdir, done)
    audit(by, via, "griglia_restore_old", {"file": target, "rows": restored, "missing": missing, "backup": bk}, None)
    return {"restored": restored, "missing": missing, "backup": bk, "file": target, "checked": n_cells}


# ===================================================================== LUCAS定制采购：总仓采购 / 门店采购（2026-11-13，智能体用）
# 和页面（store_order_page）同一套计算：读 SCHEDA → 查数据库算建议箱数 → 调货 → 每家供应商模拟配货 → 生成订单文件 + 待登记。
# 页面照旧自己跑；这里是不开窗口的版本（智能体、命令行）。设置读同一份 settings（总仓采购 store_orders / 门店采购 store_pdv）
STORE_KINDS = {"central": "store_orders", "pdv": "store_pdv"}
STORE_TITLES = {"central": "总仓采购", "pdv": "门店采购"}


def _preview_key(sc, calc, lines, fill, pk):
    """起订量检查的模拟配货缓存键（界面线程和计算线程用同一个，计算线程算好的直接能用）"""
    import json
    import store_orders as S
    return (sc["path"], id(calc["demand"]), tuple(calc["stores"]),
            json.dumps(fill, sort_keys=True, default=str), json.dumps(pk, sort_keys=True),
            tuple(sorted((sid, tuple(sorted(ln.items()))) for sid, ln in lines.items())), json.dumps(S.sc_moq(sc), default=str))


def _plan_one(sc, calc, lines, fill, pk):
    import store_orders as S
    return S.plan_supplier(sc, calc["demand"], fill, calc["stores"], lines, "manual" if pk is not None else "auto", picks=pk)


def finish_compute(calc, tcfg, tfar, sup_cfg, fill, picks, log=None):
    """
    计算线程里做完原来在界面线程做的收尾（2026-11-07 用户：后台运行完跳回去会卡）：调货、复制建议箱数、
    每家供应商模拟配货一次（起订量检查的颜色）。结果放进 calc["_ui"]，界面线程只填表
    """
    import copy
    import datetime as _dt
    import store_orders as S
    calc.setdefault("date", _dt.date.today())    # 周销量按计算当天往回数
    note = None
    try:
        S.apply_transfers(calc, tcfg, tfar, sup_cfg)
        if calc.get("transfers"):
            note = tr("[总仓采购] 门店间调货：{0} 个商品", len(calc["transfers"]))
    except Exception as e:
        note = tr("[总仓采购] ⚠ 调货计算失败（按原来的建议订）：{0}", e)
    if note and log:
        log(note)
    colli = copy.deepcopy(calc["base"])
    cache = {}
    for sc in calc["schede"]:
        lines = colli.get(sc["path"], {})
        pk = picks.get(sc["path"])
        try:
            cache[_preview_key(sc, calc, lines, fill, pk)] = _plan_one(sc, calc, lines, fill, pk)
        except Exception:
            pass                                   # 界面线程到时候自己再算（会报出原来的错）
    calc["_ui"] = {"colli": colli, "pv": cache}
    return calc


def store_setup(settings, kind):
    """页面上那些设置（不开窗口）：文件夹、门店、凑单 / 调货 / 起订量手动修改、算法、销量周期"""
    import store_orders as S
    import transfers as TR
    if kind not in STORE_KINDS:
        raise ValueError(tr("不认识的采购类型：{0}", kind))
    key = STORE_KINDS[kind]
    cfg = settings.get(key) or {}
    central = settings.get("store_orders") or {}
    rcfg = settings.get("reorder") or {}
    pdv = kind == "pdv"
    fill = dict(S.DEFAULT_FILL, **((central if pdv else cfg).get("fill") or {}))
    fill["supplier_cfg"] = cfg.get("supplier_cfg") or {}
    if pdv:
        tcfg = dict(TR.config(None), enabled=False)
    else:
        tcfg = TR.config(cfg.get("transfer"))
    tfar = TR.excluded_stores(cfg.get("transfer"), fill.get("far_stores"))
    stores = [int(s) for s in (cfg.get("stores") or list(S.STORE_SHEETS))]
    return {"kind": kind, "key": key, "title": STORE_TITLES[kind], "pdv": pdv, "cfg": cfg,
            "folder": cfg.get("folder") or (S.PDV_DEFAULT_FOLDER if pdv else r"D:\CasaTe\采购配单"),
            "stores": stores, "fill": fill, "tcfg": tcfg, "tfar": tfar, "sup_cfg": cfg.get("supplier_cfg"),
            "moq_over": dict(cfg.get("moq_override") or {}), "skip": set(cfg.get("skip") or []),
            "days": [int(d) for d in (cfg.get("days") or rcfg.get("days") or R.DEFAULT_SALES_DAYS)],
            "cycle": int(cfg.get("cycle") or rcfg.get("cycle") or 30),
            "use_cfg": bool(cfg.get("use_supplier_cycles", True)), "algo": algo_params(settings),
            "source": "store_pdv" if pdv else "store_orders"}


def today_day(today=None):
    """今天是采购配单文件夹里的哪一天（周一…周五；周末返回 None）"""
    import datetime as _dt
    import store_orders as S
    wd = (today or _dt.date.today()).weekday()
    return S.DAY_FOLDERS[wd] if wd < len(S.DAY_FOLDERS) else None


def store_suppliers(settings, kind, day=None):
    """这一天（None = 全部）文件夹里的供应商：[{"name", "day", "path", "skipped"（页面上没勾）}]"""
    import store_orders as S
    su = store_setup(settings, kind)
    if not os.path.isdir(su["folder"]):
        raise RuntimeError(tr("{0}的文件夹不存在：{1}", tr(su["title"]), su["folder"]))
    out = []
    for d, p in S.list_schede(su["folder"], day):
        n = S.supplier_short_name(p)
        out.append({"name": n, "day": d, "path": p, "skipped": n in su["skip"]})
    return out


def _match_suppliers(rows, names):
    """按名字挑供应商（不分大小写，名字里包含也算）；names 为空 = 页面上勾着的那些"""
    if not names:
        return [r for r in rows if not r["skipped"]]
    want = [str(n).strip().upper() for n in names if str(n).strip()]
    got = [r for r in rows if any(w == r["name"].upper() for w in want)]
    rest = [w for w in want if not any(w == r["name"].upper() for r in got)]
    for w in rest:
        got += [r for r in rows if w in r["name"].upper() and r not in got]
    return got


def store_compute(settings, kind, day=None, suppliers=None, stores=None, log=print, progress=None, cancel=None,
                  actor=None, via="agent", db=None):
    """
    算建议箱数（只读：读 SCHEDA、查数据库，不写任何文件）。suppliers = 供应商名字（空 = 页面上勾着的），
    stores = 门店号（空 = 页面上选的）。返回 compute() 的 calc（带 _ui：建议箱数 + 每家模拟配货）
    """
    import store_orders as S
    su = store_setup(settings, kind)
    rows = _match_suppliers(store_suppliers(settings, kind, day), suppliers)
    if not rows:
        raise RuntimeError(tr("没有要算的供应商（{0} · {1}）", day or tr("全部"), ", ".join(suppliers or []) or tr("页面上勾着的")))
    stores = [int(s) for s in (stores or su["stores"])]
    transit = None
    try:
        import order_record as REC
        if REC.tracking_folder(settings) and REC.deduct_on(settings):
            transit = REC.cached(settings)
    except Exception as e:
        log(tr("⚠ 在途（RECORD ORDINE）读不了：{0}", e))
    own = db is None
    db = db or R.make_db(settings.get("db") or None)
    try:
        calc = S.compute(db, [(r["day"], r["path"]) for r in rows], stores, su["days"], su["cycle"], log=log,
                         transit=transit, use_supplier_cycles=su["use_cfg"], algo=su["algo"],
                         moq_overrides=su["moq_over"], progress=progress, cancel=cancel)
    finally:
        if own:
            try:
                db.engine.dispose()
            except Exception:
                pass
    finish_compute(calc, su["tcfg"], su["tfar"], su["sup_cfg"], su["fill"], {}, log=log)
    calc["_kind"] = kind
    audit(actor or "", via, "store_compute", {"kind": kind, "day": day, "suppliers": [r["name"] for r in rows],
                                              "stores": stores}, {"schede": len(calc["schede"]),
                                                                  "errors": len(calc.get("errors") or [])})
    return calc


def store_plans(settings, calc, colli=None, picks=None):
    """每家供应商现在的模拟配货（用 calc["_ui"]["colli"] 或给的 colli）：{路径: plan}"""
    su = store_setup(settings, calc.get("_kind") or "central")
    colli = colli if colli is not None else calc["_ui"]["colli"]
    out = {}
    for sc in calc["schede"]:
        pk = (picks or {}).get(sc["path"])
        out[sc["path"]] = _plan_one(sc, calc, colli.get(sc["path"], {}), su["fill"], pk)
    return out


def orderable(plan):
    """模拟配货里有门店会下单（直送 / 凑单够了），或 BMS 够起订量"""
    import store_orders as S
    return any(st["status"] in ("direct", "filled") for st in plan["stores"].values()) or S.bms_ordered(plan.get("bms"))


def store_summary(settings, calc, colli=None, picks=None):
    """
    给人 / 智能体看的结果：每家供应商一行 {name, day, moq, orderable, amount, colli, stores: {门店: {status, amount, colli}}, bms}
    （金额不含税）
    """
    import store_orders as S
    plans = store_plans(settings, calc, colli, picks)
    out = []
    for sc in calc["schede"]:
        pl = plans[sc["path"]]
        sts = {}
        amt = col = 0.0
        for sid, st in pl["stores"].items():
            if st["status"] == "none":
                continue
            sts[S.STORE_SHEETS[sid]] = {"status": S.status_text(S.STORE_SHEETS[sid], st["status"]),
                                        "amount": round(float(st.get("amount") or 0), 2), "colli": st.get("colli")}
            if st["status"] in ("direct", "filled"):
                amt += float(st.get("amount") or 0)
                col += float(st.get("colli") or 0)
        b = pl.get("bms")
        bms = None
        if b:
            bms = {"status": S.status_text(S.BMS, b["status"]), "amount": round(float(b.get("amount") or 0), 2),
                   "colli": b.get("colli")}
            if S.bms_ordered(b):
                amt += float(b.get("amount") or 0)
                col += float(b.get("colli") or 0)
        out.append({"name": sc["name"], "day": sc.get("day"), "path": sc["path"],
                    "moq": S.moq_label(S.sc_moq(sc), short=True), "orderable": orderable(pl),
                    "amount": round(amt, 2), "colli": col, "stores": sts, "bms": bms})
    return out


def store_lines(calc, supplier, store=None, colli=None, limit=200):
    """一家供应商的订货明细（建议 / 现在的箱数）：[{store, row, code, ean, name, pack, price, sugg, boxes}]"""
    import store_orders as S
    sc = _find_scheda(calc, supplier)
    colli = colli if colli is not None else calc["_ui"]["colli"]
    base = calc.get("base_sugg") or calc.get("base") or {}
    items = {it["row"]: it for it in sc.get("items") or []}
    out = []
    for sid, lines in (colli.get(sc["path"]) or {}).items():
        if store and int(store) != int(sid):
            continue
        for row, n in lines.items():
            sug = ((base.get(sc["path"]) or {}).get(sid) or {}).get(row)
            if not n and not sug:
                continue
            it = items.get(row) or {}
            out.append({"store": S.STORE_SHEETS.get(sid, sid), "store_id": sid, "row": row,
                        "code": S.item_code(it) if it else None,
                        "ean": it.get("ean"), "name": it.get("desc"), "pack": it.get("pack"), "price": it.get("price"),
                        "sugg": sug, "boxes": n})
    out.sort(key=lambda x: (str(x["store"]), -(x["boxes"] or 0)))
    return out[:limit]


def _find_scheda(calc, supplier):
    want = str(supplier or "").strip().upper()
    hits = [sc for sc in calc["schede"] if sc["name"].upper() == want] or \
        [sc for sc in calc["schede"] if want and want in sc["name"].upper()]
    if len(hits) != 1:
        raise RuntimeError(tr("供应商「{0}」对不上（这次算了：{1}）", supplier, "、".join(sc["name"] for sc in calc["schede"])))
    return hits[0]


def store_set_boxes(calc, supplier, store, row, boxes):
    """改一行的订货箱数（只改这次计算里的，生成订单时才写文件）。返回原来的箱数"""
    sc = _find_scheda(calc, supplier)
    colli = calc["_ui"]["colli"].setdefault(sc["path"], {}).setdefault(int(store), {})
    old = colli.get(row, 0)
    colli[row] = max(0, int(boxes))
    calc.setdefault("_edited", set()).add((sc["path"], int(store), row))
    return old


def store_allocate(settings, calc, suppliers=None, log=print, progress=None, actor=None, via="agent"):
    """
    生成订单文件（会写文件）：只生成订得起来的供应商（suppliers 给了就只这些），一家供应商一个订单文件、配货汇总、调货单，
    采购订单跟踪里每家供应商 × 每个去向一张「待登记」。返回 S.allocate 的结果 + tracked
    """
    import copy
    import store_orders as S
    import order_tracking as OT
    su = store_setup(settings, calc.get("_kind") or "central")
    plans = store_plans(settings, calc)
    if suppliers:
        chosen = [_find_scheda(calc, n) for n in suppliers]
    else:
        chosen = [sc for sc in calc["schede"] if orderable(plans[sc["path"]])]
    if not chosen:
        raise RuntimeError(tr("没有订得起来的供应商（都不够起订量）"))
    out_root = S.new_run_folder(S.output_root(su["folder"]))
    keep = {sc["path"] for sc in chosen}
    sub = dict(calc, schede=chosen)
    colli = {p: v for p, v in copy.deepcopy(calc["_ui"]["colli"]).items() if p in keep}
    edited = {k for k in calc.get("_edited") or set() if k[0] in keep}
    res = S.allocate(sub, colli, su["fill"], out_root, log=log, edited=edited, progress=progress)
    try:
        res["tracked"] = OT.register_store_orders(settings, res["written"], source=su["source"], log=log)
    except Exception as e:
        log(tr("⚠ 订单跟踪没登记上：{0}", e))
        res["tracked"] = None
    tot = S.plan_totals(res["results"])
    audit(actor or "", via, "store_allocate", {"kind": su["kind"], "suppliers": [sc["name"] for sc in chosen],
                                               "out_root": out_root},
          {"files": len(S.written_files(res["written"])), "total": round(tot["total"], 2)})
    return res
