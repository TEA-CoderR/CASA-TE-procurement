from i18n import tr
import os
import math
import gc
import time
import win32com.client as win32
from openpyxl import load_workbook

from utils import (
    convert_to_xlsx, calculate_formulas, cleanup_calculated_files,
    find_col, get_last_row, clean_ean, normalize, parse_price,
    is_valid_xlsx
)
from config import (
    COL_CODICE_EAN, COL_COLLI, COL_PREZZO_NETTO, COL_ORDINE,
    SUPPLIER_MATRIX_START_COL, SUPPLIER_RESULT_COL, EAN_COUNT_COL,
    MAX_ROWS, REPORT_DISPLAY_LIMIT, MIN_ORDER_NOTE_COL
)
from min_order_optimizer import optimize_min_order, optimize_min_orders
from settings import load_settings, match_rules
from supplier_layout import find_pack_col, find_order_input, order_formula, parse_pack
from openpyxl.utils import get_column_letter


# =====================
# 供应商选择：最低价 + 起订量凑单（可由操作员决定 / 手动调整）
# =====================
def _price_row(row, qty, unit, packs, ean="", articolo=""):
    """
    一个商品的报价数据。
      unit : {供应商: 单件价}
      packs: {供应商: 每箱件数}
      prices: 比价用的「一箱价」= 单件价 × 参考件数（参考件数 = 单件价最低那家的每箱件数），
              所以选供应商就是比单件价；同一商品各家箱规一样时，就等于真实的一箱价。
      amt  : 真实的一箱金额 = 单件价 × 这家的每箱件数（算起订金额、各家采购金额用）
    """
    best, ref = None, 1
    for s, p in unit.items():
        if p is not None and (best is None or p < best):
            best, ref = p, packs.get(s) or 1
    return {"row": row, "qty": qty, "ean": ean, "articolo": articolo,
            "unit": dict(unit), "packs": dict(packs), "ref_pack": ref,
            "prices": {s: (p * ref if p is not None else None) for s, p in unit.items()},
            "amt": {s: (p * (packs.get(s) or 1) if p is not None else None) for s, p in unit.items()}}


def _price_row_pieces(row, pieces, unit, packs, ean="", articolo=""):
    """
    主订单按件数（PEZZI 列，来自订货建议）时的一行：
      每家要订的箱数 = ceil(件数 ÷ 这家每箱件数)；金额 = 箱数 × 每箱件数 × 件价（含凑整多买的件）。
      比价就按这个真实金额比。
    """
    colli, total = {}, {}
    for s, p in unit.items():
        if p is None:
            continue
        pk = packs.get(s) or 1
        c = math.ceil(pieces / pk) if pieces > 0 else 0
        colli[s] = c
        total[s] = c * pk * p
    per = {s: (total[s] / pieces if (s in total and pieces > 0) else None) for s in unit}
    return {"row": row, "qty": pieces, "pieces": pieces, "ean": ean, "articolo": articolo,
            "unit": dict(unit), "packs": dict(packs), "ref_pack": 1,
            "prices": dict(per), "amt": dict(per), "colli": colli}


def set_row_qty(r, q):
    """
    修改分配里改了一个商品的数量 → 新的这一行（不改原来的）。
      普通主订单：q 是箱数（COLLI）
      按件数订货（PEZZI）：q 是件数，各家箱数重新按 件数 ÷ 每箱件数 向上取整
    qty_orig 记住处理时的原数量（界面上标出改过的）。
    """
    q = max(0.0, float(q or 0))
    orig = r.get("qty_orig", r.get("qty"))
    if r.get("pieces") is not None:
        r2 = _price_row_pieces(r["row"], q, r.get("unit") or {}, r.get("packs") or {}, r.get("ean", ""),
                               r.get("articolo", ""))
        for k, v in r.items():
            r2.setdefault(k, v)
    else:
        r2 = dict(r, qty=q)
    if q != orig:
        r2["qty_orig"] = orig
    else:
        r2.pop("qty_orig", None)
    return r2


def _order_qty(r, s):
    """写进供应商 s 订单的箱数：按件数订货时用这家的箱数，否则就是主订单的 COLLI"""
    c = (r or {}).get("colli")
    if c and s in c:
        return c[s]
    return (r or {}).get("qty")


def _select_suppliers(rows_data, supplier_headers, ws_main, log, settings, summary, decide=None, choose=None):
    """
    返回 {行号: 供应商}；返回 None 表示操作员取消了处理。
    decide(plan, rows_data, rules) 由界面提供：弹窗让操作员决定、手动调整，
    返回 {"assignment": {...}, "sources": {行号: 来源}} 或 None（取消）。
    choose(results) 由界面提供：几种方案（全部供应商 / 不含某家 …）的对比，返回选中的下标或 None（取消）。
    """
    from allocation_plan import build_plan, auto_assignment, build_report, baseline_assignment

    summary["min_order"] = {"enabled": False, "rules": [], "unmatched": [], "extra_cost": 0.0,
                            "changes": 0, "warning": None}
    rules = []
    import supplier_names as SN
    labels = SN.labels(supplier_headers, settings)
    summary["supplier_labels"] = dict(labels)
    if settings.get("min_order_enabled", True):
        matched, unmatched = match_rules(settings.get("rules", []), supplier_headers, labels)
        for r in unmatched:
            log(tr("⚠ 起订规则「{0}」：本次没有文件名包含 '{1}' 的供应商文件", r.get('name') or r.get('keyword'), r.get('keyword')))
        summary["min_order"]["unmatched"] = [r.get("name") or r.get("keyword") for r in unmatched]
        from min_order_optimizer import effective_limits
        rules = []
        for r, h in matched:
            mc, ma, md = effective_limits(r.get("min_colli"), r.get("min_amount"), r.get("mode", "all"))
            rules.append(dict(target=h, name=r.get("name") or normalize(h), min_colli=mc, min_amount=ma,
                              mode=md, allow_drop=bool(r.get("allow_drop", True)),
                              manual_extra=float(r.get("manual_extra") or 0),
                              drop_extra=float(r.get("drop_extra") or 0)))
    else:
        log(tr("起订量凑单：未启用（只按最低价选供应商）"))

    baseline = baseline_assignment(rows_data)
    sources = {}
    rules_all, scen_assigns = list(rules), []
    # ---- 几种方案对比（全部供应商 / 排除 MEGA / 排除 NEW GRIECO …），由用户选一种 ----
    import scenarios as SC
    defs = SC.scenario_defs(settings)
    if choose and settings.get("scenarios_enabled", True) and len(defs) > 1:
        while True:
            log(tr("\n[方案对比] 同时计算 {0} 种方案…", len(defs)))
            results = SC.compute_scenarios(rows_data, supplier_headers, rules, defs, labels)
            for sc in results:
                log(tr("  {0}：采购总额 €{1:,.2f}，有报价 {2} 个商品，缺 {3} 个", tr(sc["name"]), sc["total"],
                       sc["priced"], len(sc["missing"])))
                if sc.get("unmatched"):
                    log(tr("  ⚠ {0}：没有找到要排除的供应商「{1}」", tr(sc["name"]), "、".join(sc["unmatched"])))
            idx = choose(results)
            if idx != "again":
                break
            # 对比窗口里改了供应商名称 / 方案：按新的设置重新算（不用重新读文件）
            fresh = load_settings()
            settings = dict(settings, supplier_names=fresh.get("supplier_names") or {},
                            scenarios=fresh.get("scenarios") or settings.get("scenarios"))
            labels = SN.labels(supplier_headers, settings)
            summary["supplier_labels"] = dict(labels)
            defs = SC.scenario_defs(settings)
        if idx is None:
            return None
        scen_assigns = [dict(x["assignment"]) for x in results]
        chosen = results[idx]
        summary["scenarios"] = SC.compact(results, idx)
        summary["scenario"] = chosen["name"]
        log(tr("  ✔ 选用方案：{0}", tr(chosen["name"])))
        excl = set(chosen["excluded"])
        final = dict(chosen["assignment"])
        for r in rows_data:
            b, f = baseline.get(r["row"]), final.get(r["row"])
            if b != f:
                sources[r["row"]] = "方案排除" if b in excl else "自动凑单"
        rules_eff = [ru for ru in rules if ru["target"] not in excl]
        if decide and settings.get("review_before_write"):
            rows_eff = SC.restrict_rows(rows_data, excl)
            result = decide({"fixed": final, "fixed_sources": sources, "baseline": baseline_assignment(rows_eff),
                             "rules": []}, rows_eff, rules_eff)
            if result is None:
                return None
            final, sources = result["assignment"], result.get("sources", sources)
            for r in rows_data:                       # 「方案排除」的来源说明保留
                b, f = baseline.get(r["row"]), final.get(r["row"])
                if b != f and b in excl and sources.get(r["row"]) != "手动":
                    sources[r["row"]] = "方案排除"
        assignment = final
        rules = rules_eff
    elif rules or (decide and settings.get("review_before_write")):
        log(tr("\n[起订量检查] {0} 个供应商有起订条件，正在计算方案...", len(rules)))
        plan = build_plan(rows_data, rules, allow_partial=bool(settings.get("allow_partial_purchase", False)))
        for info in plan["rules"]:
            log(tr("  {0}: 最低价分到 {1:.0f} 箱 / €{2:.2f}，未达起订条件", info['name'], info['before_colli'], info['before_amount']))
            log(tr("    继续凑单: 采购总额 €{0:,.2f}（因凑单多花 €{1:,.2f}）", info['fill_total'], info['fill_extra'])
                if info["fill_ok"] else tr("    继续凑单: 凑不够"))
            if info["drop_allowed"]:
                log(tr("    放弃该供应商: 采购总额 €{0:,.2f}", info['drop_total'])
                    + (tr("，有 {0} 个商品只有它有、将不采购", info['lost_count']) if info["lost_count"] else ""))
            else:
                log(tr("    放弃该供应商: 不可选（{0} 个商品只有它有货，设置为所有商品都必须采购）", info['lost_count']))
        if decide:
            result = decide(plan, rows_data, rules)
            if result is None:
                return None
            assignment, sources = result["assignment"], result.get("sources", {})
        else:
            assignment = auto_assignment(rows_data, rules)
    else:
        assignment = baseline

    rep = build_report(rows_data, rules, baseline, assignment, sources)
    summary.setdefault("scenario", None)
    for ru in rep["rules"]:
        log(tr("  {0}: {1:.0f} 箱 / €{2:.2f} → {3:.0f} 箱 / €{4:.2f}  【{5}】", ru['name'], ru['before_colli'], ru['before_amount'], ru['after_colli'], ru['after_amount'], tr(ru['status'])))
    if rep["changes"]:
        m0 = sum(x["before_amount"] for x in rep["suppliers"])
        m1 = sum(x["after_amount"] for x in rep["suppliers"])
        log(tr("  ✔ 共改动 {0} 个商品，采购金额 €{1:,.2f} → €{2:,.2f}，按件价比多花 €{3:+,.2f}", len(rep['changes']), m0, m1, rep['extra_cost']))
        ws_main.cell(1, MIN_ORDER_NOTE_COL).value = tr("供应商调整")
        for ch in rep["changes"]:
            old = normalize(ch["old"]) if ch["old"] else tr("无")
            new = normalize(ch["new"]) if ch["new"] else tr("不采购")
            d = f" ({ch['delta']:+.2f}€)" if ch["delta"] is not None else ""
            ws_main.cell(ch["row"], MIN_ORDER_NOTE_COL).value = f"{tr(ch['source'])}: {old} → {new}{d}"
    elif rules:
        log(tr("  ✔ 无需调整"))

    summary["min_order"].update(enabled=bool(rules), rules=rep["rules"], extra_cost=rep["extra_cost"],
                                changes=len(rep["changes"]),
                                warning=None if all(r["status"] != "未达标" for r in rep["rules"])
                                else tr("有起订供应商没有达标，请检查"))
    summary["changes"] = rep["changes"]
    summary["supplier_delta"] = {x["name"]: x for x in rep["suppliers"]}
    summary["baseline_cost"], summary["final_cost"] = rep["baseline_cost"], rep["final_cost"]
    summary["_plan_state"] = {"rules": rules, "baseline": baseline, "sources": sources,
                              "scenario_assigns": scen_assigns, "rules_all": rules_all}
    return assignment

# 导入性能监控和影子测试
try:
    from perf_monitor import perf_monitor, start_session, end_session, print_report
    from shadow_test import shadow_test
    USE_MONITORING = True
except ImportError:
    USE_MONITORING = False


# =====================
# 安全打开 Excel 文件
# =====================
def safe_open(path, log):
    """打开 Excel 文件，用 Excel COM 计算公式后读取纯值"""
    try:
        # 先转换 .xls → .xlsx
        xlsx_path = convert_to_xlsx(path)
        # 用 Excel 计算公式，保存纯值副本
        calc_path = calculate_formulas(xlsx_path)
        # 读取纯值副本（openpyxl 直接读值，不需要 data_only）
        wb = load_workbook(calc_path)
        return wb, path, calc_path  # 返回原路径和临时路径
    except Exception as e:
        log(tr("❌ 打不开文件: {0} | 错误: {1}", path, e))
        return None, None, None


def safe_save(wb, original_path, calc_path, log):
    """
    保存修改：直接使用 openpyxl 保存，避免 Excel COM 遍历所有单元格的性能问题。
    打包后 Excel COM 调用极慢，直接使用 shutil.copy2 同步数据。
    """
    import shutil
    try:
        # 先保存修改后的 workbook 到临时文件
        wb.save(calc_path)
        
        # 直接同步到原始文件（避免 Excel COM 的性能问题）
        shutil.copy2(calc_path, original_path)
        log(tr("  ✔ 已保存到 {0}", os.path.basename(original_path)))
        
    except Exception as e:
        log(tr("❌ 保存失败: {0} | 错误: {1}", original_path, e))


def safe_cleanup_calculated_files(log):
    """
    安全清理临时计算公式生成的 .calc.xlsx 文件。
    增加错误处理，防止文件被锁定时卡住。
    """
    from utils import _calculated_cache
    cleaned = 0
    failed = 0
    for calc_path in _calculated_cache.values():
        try:
            if os.path.exists(calc_path):
                # 先尝试解锁文件
                try:
                    # 尝试打开并关闭文件以释放锁
                    f = open(calc_path, 'r')
                    f.close()
                except:
                    pass
                os.remove(calc_path)
                cleaned += 1
        except PermissionError:
            log(tr("⚠ 无法删除临时文件（被占用）: {0}", os.path.basename(calc_path)))
            failed += 1
        except Exception as e:
            log(tr("⚠ 删除临时文件失败: {0} | {1}", os.path.basename(calc_path), e))
            failed += 1
    _calculated_cache.clear()
    if cleaned > 0:
        log(tr("✔ 已清理 {0} 个临时文件", cleaned))
    if failed > 0:
        log(tr("⚠ {0} 个临时文件清理失败（可稍后手动删除）", failed))


# =====================
# 构建主表 EAN 索引（O(n²) → O(n) 优化）
# =====================
def build_ean_index(ws_main, col_ean, start_row=2, end_row=None):
    """
    构建 {clean_ean: row_number} 字典，避免嵌套循环。
    返回字典，key 是清理后的 EAN，value 是行号。
    """
    if end_row is None:
        end_row = get_last_row(ws_main, col_ean) + 1
    index = {}
    for i in range(start_row, end_row):
        ean = clean_ean(ws_main.cell(i, col_ean).value)
        if ean:
            index[ean] = i
    return index


# =====================
# eliminare 处理（逐行扣减 + 报告）
# =====================
def run_eliminare(ws_main, eliminare_path, col_ean_main, log):
    """
    处理 eliminare 文件夹中的所有文件，从主表扣减 COLLI 数量。
    数量为 0 或负数的行会被删除。
    """
    log(tr("\n执行 eliminare（逐行扣减 + 报告）"))

    col_colli_main, _ = find_col(ws_main, COL_COLLI)
    if not col_colli_main:
        log(tr("❌ 主表缺少 {0} 列", COL_COLLI))
        return

    delete_count = 0
    update_count = 0
    report = []

    # 收集需要删除的行号（延迟删除，避免索引偏移）
    rows_to_delete = []

    # 只构建一次索引（延迟删除前主表不变）
    log(tr("  构建 EAN 索引..."))
    ean_index = build_ean_index(ws_main, col_ean_main)
    log(tr("  索引包含 {0} 个 EAN", len(ean_index)))

    for file in sorted(os.listdir(eliminare_path)):
        if not file.lower().endswith((".xls", ".xlsx")):
            continue

        path = os.path.join(eliminare_path, file)
        log(tr("\n处理文件: {0}", file))

        wb, _, calc_path_elim = safe_open(path, log)
        if not wb:
            continue

        ws = wb.active
        col_colli, _ = find_col(ws, COL_COLLI)

        if not col_colli:
            log(tr("⚠ {0} 无 {1} 列", file, COL_COLLI))
            wb.close()
            continue

        last_row = get_last_row(ws, 1)
        for i in range(2, last_row + 1):
            e = clean_ean(ws.cell(i, 1).value)

            try:
                sub_val = float(ws.cell(i, col_colli).value)
            except (ValueError, TypeError):
                sub_val = 0

            if not e or sub_val == 0:
                continue

            # 通过索引快速查找主表行
            if e not in ean_index:
                continue

            j = ean_index[e]

            try:
                main_val = float(ws_main.cell(j, col_colli_main).value)
            except (ValueError, TypeError):
                main_val = 0

            new_val = main_val - sub_val

            if new_val <= 0:
                report.append(f"EAN {e}: {main_val} - {sub_val} = 删除")
                rows_to_delete.append(j)
                delete_count += 1
            else:
                ws_main.cell(j, col_colli_main).value = new_val
                report.append(f"EAN {e}: {main_val} - {sub_val} = {new_val}")
                update_count += 1

        wb.close()

    # 延迟删除：从后往前删，避免索引偏移 Bug
    for row in sorted(set(rows_to_delete), reverse=True):
        ws_main.delete_rows(row)

    # 输出报告
    log(tr("\n扣减报告："))
    for line in report[:REPORT_DISPLAY_LIMIT]:
        log(line)
    if len(report) > REPORT_DISPLAY_LIMIT:
        log(tr("... 还有 {0} 条", len(report) - REPORT_DISPLAY_LIMIT))

    log(tr("\n✔ 删除 {0} 行", delete_count))
    log(tr("✔ 更新 {0} 行", update_count))
    return delete_count, update_count


# =====================
# 主流程
# =====================
def _backup_inputs(ordine_file, distribuire_path, log):
    """处理前把主订单和供应商文件复制到 总文件夹/_备份/时间戳/"""
    import shutil
    from datetime import datetime
    root = os.path.dirname(os.path.dirname(ordine_file))
    dest = os.path.join(root, "_备份", datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(os.path.join(dest, "ORDINE"), exist_ok=True)
    os.makedirs(os.path.join(dest, "distribuire"), exist_ok=True)
    shutil.copy2(ordine_file, os.path.join(dest, "ORDINE", os.path.basename(ordine_file)))
    n = 1
    for f in os.listdir(distribuire_path):
        fp = os.path.join(distribuire_path, f)
        if os.path.isfile(fp) and f.lower().endswith((".xls", ".xlsx")) and not f.startswith("~"):
            shutil.copy2(fp, os.path.join(dest, "distribuire", f))
            n += 1
    log(tr("✔ 已备份 {0} 个文件到: {1}", n, dest))
    return dest


def run_process(ordine_file, eliminare_path, distribuire_path, log, progress=None, settings=None, decide=None, choose=None):
    """
    采购系统主流程：
    1. 打开 ORDINE 主文件
    2. 执行 eliminare 扣减
    3. 构建供应商价格矩阵
    4. 选择最优供应商（最低价）
    5. 回写订单到供应商文件
    
    progress(百分比, 文字): 可选，界面进度条回调
    settings: 界面设置（起订量规则等），None 时从 settings.json 读取
    返回 summary 字典，供界面显示结果汇总
    """
    t_start = time.time()
    settings = settings or load_settings()

    def P(pct, text):
        if progress:
            try:
                progress(pct, text)
            except Exception:
                pass

    summary = {"ok": False, "error": None, "main_file": os.path.basename(ordine_file), "changes": [],
               "backup_dir": None, "eliminare": (0, 0), "suppliers": [], "total_rows": 0,
               "priced_rows": 0, "missing": 0, "missing_report": None, "elapsed": 0.0,
               "min_order": {"enabled": False, "rules": [], "unmatched": [], "extra_cost": 0.0,
                             "changes": 0, "warning": None}}

    # 每次都从原文件开始算（重复处理结果一样）：文件还是上次程序写出来的样子 → 先恢复成原文件
    import originals as OG
    root_dir = os.path.dirname(os.path.dirname(ordine_file))
    prev_state = (load_state(root_dir) or {}).get("state") or {}
    prev_fp = prev_state.get("written") or {}
    try:
        how = OG.prepare(ordine_file, root_dir, "ORDINE", prev_fp.get("ORDINE"))
        if how == "restored":
            log(tr("✔ 主订单还是上次处理后的样子，已恢复成原文件再算（eliminare 不会重复扣减）"))
        for f in _price_files(distribuire_path):
            if OG.prepare(os.path.join(distribuire_path, f), root_dir, "distribuire",
                          prev_fp.get("distribuire")) == "restored":
                    log(tr("  {0}：恢复成原报价文件再算", f))
    except Exception as e:
        log(tr("⚠ 恢复原文件失败（按现在的文件算）：{0}", e))
    # 上次（例如中途取消）留下的公式计算副本不能再用：文件可能刚恢复 / 换过
    try:
        safe_cleanup_calculated_files(lambda *a: None)
    except Exception:
        pass

    if settings.get("auto_backup", True):
        P(2, tr("备份原始文件..."))
        try:
            summary["backup_dir"] = _backup_inputs(ordine_file, distribuire_path, log)
        except Exception as e:
            log(tr("⚠ 自动备份失败: {0}", e))
    # 开始性能监控会话
    session = None
    if USE_MONITORING:
        session = start_session("procurement_process")
        perf_monitor.start_timer("total_process")
    
    log("=" * 50)
    log(tr("启动"))
    log("=" * 50)
    
    if USE_MONITORING:
        log(tr("[监控] 性能监控已启用"))

    P(5, tr("打开主订单（Excel 计算公式中）..."))
    wb_main, ordine_file, calc_main = safe_open(ordine_file, log)
    if not wb_main:
        summary["error"] = tr("主订单文件打不开")
        return summary

    ws_main = wb_main.active

    col_ean_main, row_ean_main = find_col(ws_main, COL_CODICE_EAN)
    if not col_ean_main:
        log(tr("❌ 主表缺少 {0} 列", COL_CODICE_EAN))
        wb_main.close()
        summary["error"] = f"主表缺少 {COL_CODICE_EAN} 列"
        return summary

    # 显示主文件名
    main_filename = os.path.basename(ordine_file)
    log(tr("主表: {0}", main_filename))

    # ① eliminare 扣减
    log("\n" + "=" * 10)
    log("eliminare")
    log("=" * 10)
    
    if USE_MONITORING:
        perf_monitor.start_timer("eliminare")
    P(10, tr("扣减已处理数量（eliminare）..."))
    summary["eliminare"] = run_eliminare(ws_main, eliminare_path, col_ean_main, log) or (0, 0)
    if USE_MONITORING:
        eliminare_time = perf_monitor.stop_timer("eliminare")
        log(tr("[监控] eliminare耗时: {0:.2f}秒", eliminare_time))

    # ② 构建价格矩阵
    log("\n" + "=" * 10)
    log(tr("构建价格"))
    log("=" * 10)
    
    if USE_MONITORING:
        perf_monitor.start_timer("build_price_matrix")

    # 备份供应商文件（防止第一次运行修改后影响第二次运行结果）
    import shutil
    backup_dir = os.path.join(distribuire_path, ".backup")
    if os.path.exists(backup_dir):
        shutil.rmtree(backup_dir)
    os.makedirs(backup_dir, exist_ok=True)
    for file in os.listdir(distribuire_path):
        if file.lower().endswith((".xls", ".xlsx")) and not file.startswith("~") and "缺失报告" not in file:
            src = os.path.join(distribuire_path, file)
            dst = os.path.join(backup_dir, file)
            shutil.copy2(src, dst)
    log(tr("✔ 供应商文件已备份"))

    # 从备份恢复原始供应商文件（确保每次运行都使用原始文件）
    for file in os.listdir(backup_dir):
        if file.lower().endswith((".xls", ".xlsx")):
            src = os.path.join(backup_dir, file)
            dst = os.path.join(distribuire_path, file)
            shutil.copy2(src, dst)
    log(tr("✔ 已从备份恢复原始供应商文件"))

    # 清理旧的供应商列和选择结果（防止重复运行导致列累积）
    log(tr("清理旧的供应商列..."))
    main_last_row = get_last_row(ws_main, col_ean_main)
    
    # 清空 EAN 出现次数列
    ws_main.cell(1, EAN_COUNT_COL).value = None
    for i in range(2, main_last_row + 1):
        ws_main.cell(i, EAN_COUNT_COL).value = None
    
    # 清空供应商选择结果列和凑单说明列
    for i in range(2, main_last_row + 1):
        ws_main.cell(i, SUPPLIER_RESULT_COL).value = None
    for i in range(1, main_last_row + 1):
        ws_main.cell(i, MIN_ORDER_NOTE_COL).value = None
    
    # 清空旧的供应商矩阵列
    for col in range(SUPPLIER_MATRIX_START_COL, ws_main.max_column + 1):
        ws_main.cell(1, col).value = None
        for i in range(2, main_last_row + 1):
            ws_main.cell(i, col).value = None
    
    log(tr("[完成] 已清理"))

    q = SUPPLIER_MATRIX_START_COL
    supplier_files = []
    # EAN出现次数统计: {clean_ean: 在多少个供应商文件中出现}
    ean_supplier_count = {}

    price_files = [f for f in sorted(os.listdir(distribuire_path))
                   if f.lower().endswith((".xls", ".xlsx"))
                   and "缺失报告" not in f and "missing" not in f.lower()
                   and ".calc." not in f.lower() and not f.lower().endswith(".xls.xlsx")
                   and not f.startswith("~$")]
    supplier_hits = {}
    pack_maps = {}   # {供应商文件: {EAN: 每箱件数}}
    for n_file, file in enumerate(price_files):
        P(20 + int(40 * n_file / max(1, len(price_files))),
          tr("读取供应商报价 {0}/{1}: {2}", n_file + 1, len(price_files), file))

        path = os.path.join(distribuire_path, file)

        wb, orig_path, calc_path_supplier = safe_open(path, log)
        if not wb:
            continue

        ws = wb.active

        col_ean, row_ean = find_col(ws, COL_CODICE_EAN)
        col_price, row_price = find_col(ws, COL_PREZZO_NETTO)

        if not col_ean or not col_price:
            log(tr("⚠ {0} 缺少必要列", file))
            wb.close()
            continue

        # 数据从表头行之后开始
        header_row = max(row_ean, row_price)

        # 每箱件数（PREZZO NETTO 是单件价，一箱金额 = 件价 × 每箱件数）
        col_ord_p, _ = find_col(ws, COL_ORDINE)
        pack_col, pack_how = _detect_pack_col(ws, path, header_row, col_ord_p, col_price)
        if pack_col:
            head = ws.cell(header_row, pack_col).value
            log(tr("  {0} 每箱件数：{1} 列{2}", file, get_column_letter(pack_col),
                   tr("（表头「{0}」）", str(head).strip()) if head not in (None, "") else tr("（从公式推断）")))
        else:
            log(tr("⚠ {0} 找不到每箱件数列，金额按每箱 1 件计算", file))
        pack_map = {}

        # 构建价格字典，同时统计 EAN 出现次数
        price_map = {}
        last_row = get_last_row(ws, col_ean)
        for i in range(header_row + 1, last_row + 1):
            e = clean_ean(ws.cell(i, col_ean).value)
            p = parse_price(ws.cell(i, col_price).value)
            if e and p is not None:
                price_map[e] = p
                pack_map[e] = parse_pack(ws.cell(i, pack_col).value) if pack_col else 1
                # 统计 EAN 在多少个供应商文件中出现
                if e not in ean_supplier_count:
                    ean_supplier_count[e] = 0
                ean_supplier_count[e] += 1
        
        log(tr("  {0} 表头在第 {1} 行，数据从第 {2} 行开始", file, header_row, header_row + 1))

        # 写入主表，统计命中数
        ws_main.cell(1, q).value = file
        supplier_files.append(file)
        pack_maps[file] = pack_map

        main_last = get_last_row(ws_main, col_ean_main)
        hit_count = 0
        for i in range(2, main_last + 1):
            ean = clean_ean(ws_main.cell(i, col_ean_main).value)
            if ean in price_map:
                ws_main.cell(i, q).value = price_map[ean]
                hit_count += 1

        # 短文件名显示
        short_name = file.replace(".xlsx", "").replace(".xls", "")
        log(tr("{0} ✔ 价格:{1} 命中:{2}", short_name, len(price_map), hit_count))
        supplier_hits[file] = (len(price_map), hit_count)

        wb.close()
        q += 1

    # ②.5 写入 EAN 出现次数统计
    log("\n" + "=" * 10)
    log(tr("EAN 出现次数统计"))
    log("=" * 10)
    log(tr("统计每个 EAN 在 {0} 个供应商文件中出现的次数", len(supplier_files)))
    
    # 写入列头
    ws_main.cell(1, EAN_COUNT_COL).value = tr("EAN出现次数")
    
    # 写入每个 EAN 的出现次数
    main_last = get_last_row(ws_main, col_ean_main)
    ean_count_written = 0
    for i in range(2, main_last + 1):
        ean = clean_ean(ws_main.cell(i, col_ean_main).value)
        if ean:
            count = ean_supplier_count.get(ean, 0)
            ws_main.cell(i, EAN_COUNT_COL).value = count
            if count > 0:
                ean_count_written += 1
    
    log(tr("✔ EAN 出现次数已写入 AB 列（第 {0} 列）", EAN_COUNT_COL))
    log(tr("  有供应商报价的 EAN: {0}/{1}", ean_count_written, main_last - 1))
    log(tr("  无供应商报价的 EAN: {0}", (main_last - 1) - ean_count_written))

    # ③ 选择最优供应商（最低价）
    log("\n" + "=" * 10)
    log(tr("选择供应商"))
    log("=" * 10)
    
    if USE_MONITORING:
        perf_monitor.start_timer("select_supplier")

    # 查找 ARTICOLO 列
    col_articolo, _ = find_col(ws_main, "ARTICOLO")

    main_last = get_last_row(ws_main, col_ean_main)
    col_colli_sel, _ = find_col(ws_main, COL_COLLI)

    # 读取每行的价格（按供应商列顺序）和箱数
    supplier_headers = [ws_main.cell(1, col).value for col in range(SUPPLIER_MATRIX_START_COL, q)]
    rows_data = []
    col_pezzi, _ = find_col(ws_main, "PEZZI")
    if col_pezzi:
        log(tr("主订单有 PEZZI 列：按件数订货，各家箱数 = 件数 ÷ 这家每箱件数（向上取整）"))
    for i in range(2, main_last + 1):
        unit = {}
        for k, col in enumerate(range(SUPPLIER_MATRIX_START_COL, q)):
            unit[supplier_headers[k]] = parse_price(ws_main.cell(i, col).value)
        qty = ws_main.cell(i, col_colli_sel).value if col_colli_sel else 0
        ean = clean_ean(ws_main.cell(i, col_ean_main).value)
        packs_i = {h: pack_maps.get(h, {}).get(ean, 1) for h in supplier_headers}
        art = str(ws_main.cell(i, col_articolo).value or "") if col_articolo else ""
        if col_pezzi:
            try:
                pieces = max(0.0, float(ws_main.cell(i, col_pezzi).value or 0))
            except (TypeError, ValueError):
                pieces = 0.0
            rows_data.append(_price_row_pieces(i, pieces, unit, packs_i, ean, art))
        else:
            rows_data.append(_price_row(i, qty, unit, packs_i, ean, art))

    # 最低价分配 + 起订量凑单优化
    P(62, tr("选择供应商（最低价 + 起订量凑单）..."))
    assignment = _select_suppliers(rows_data, supplier_headers, ws_main, log, settings, summary, decide, choose)
    if assignment is None:
        log(tr("⚠ 操作员取消了处理，主订单和供应商文件都没有改动"))
        wb_main.close()
        safe_cleanup_calculated_files(log)
        summary["error"] = tr("已取消，文件没有改动")
        summary["cancelled"] = True
        return summary

    # 各供应商分配汇总
    for h in supplier_headers:
        rows_n = colli_n = amount_n = 0
        for r in rows_data:
            if assignment.get(r["row"]) == h:
                rows_n += 1
                try:
                    qv = float(r["qty"] or 0)
                except (TypeError, ValueError):
                    qv = 0
                colli_n += float(_order_qty(r, h) or 0) if r.get("colli") else qv
                amount_n += qv * ((r.get("amt") or r["prices"]).get(h) or 0)
        n_price, n_hit = supplier_hits.get(h, (0, 0))
        dl = summary.get("supplier_delta", {}).get(h, {})
        summary["suppliers"].append({"name": h, "prices": n_price, "hits": n_hit,
                                     "rows": rows_n, "colli": colli_n, "amount": amount_n,
                                     "before_colli": dl.get("before_colli", colli_n),
                                     "before_amount": dl.get("before_amount", amount_n),
                                     "written": 0})
    summary["total_rows"] = len(rows_data)
    summary["priced_rows"] = len(assignment)

    missing_count = 0
    missing_items = []  # [(ean, articolo), ...]
    row_by_i = {r["row"]: r for r in rows_data}
    for i in range(2, main_last + 1):
        best_supplier = assignment.get(i)

        if best_supplier:
            ws_main.cell(i, SUPPLIER_RESULT_COL).value = best_supplier
            if col_pezzi and col_colli_sel:
                # 按件数订货：COLLI 改成选中那家的箱数（回写供应商订单用的就是这一列）
                ws_main.cell(i, col_colli_sel).value = _order_qty(row_by_i.get(i), best_supplier)
        else:
            missing_count += 1
            ean = clean_ean(ws_main.cell(i, col_ean_main).value)
            articolo = ws_main.cell(i, col_articolo).value if col_articolo else ""
            if ean:
                missing_items.append((ean, articolo))
    
    if USE_MONITORING:
        select_time = perf_monitor.stop_timer("select_supplier")
        log(tr("[监控] 选择供应商耗时: {0:.2f}秒", select_time))

    log(tr("缺失: {0}", missing_count))
    summary["missing"] = missing_count
    try:
        import scenarios as SC
        excl = next((x.get("excluded") or [] for x in summary.get("scenarios") or [] if x.get("chosen")), [])
        summary["missing_items"] = SC.missing_items(rows_data, assignment, excl)
    except Exception as e:
        log(tr("⚠ 缺失商品明细整理失败：{0}", e))

    # 生成缺失报告（Excel）
    if missing_items:
        from openpyxl import Workbook
        from datetime import datetime

        wb_report = Workbook()
        ws_report = wb_report.active
        ws_report.title = "缺失报告"

        # 标题行
        ws_report.append(["缺失供应商报价的产品"])
        ws_report.append(["生成时间: %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        ws_report.append(["总缺失数: %d" % len(missing_items)])
        ws_report.append([])
        ws_report.append(["序号", "EAN", "ARTICOLO", "数量"])

        # 统计每个 EAN 出现的数量和对应的 ARTICOLO
        ean_info = {}  # {ean: (articolo, count)}
        for ean, articolo in missing_items:
            if ean in ean_info:
                old_articolo, old_count = ean_info[ean]
                ean_info[ean] = (old_articolo, old_count + 1)
            else:
                ean_info[ean] = (articolo, 1)

        for idx, (ean, (articolo, count)) in enumerate(ean_info.items(), 1):
            ws_report.append([idx, ean, articolo, count])

        # 自动调整列宽
        for col in ws_report.columns:
            max_length = 0
            column_letter = col[0].column_letter
            for cell in col:
                try:
                    if cell.value:
                        max_length = max(max_length, len(str(cell.value)))
                except:
                    pass
            ws_report.column_dimensions[column_letter].width = min(max_length + 2, 50)

        # 保存
        report_path = os.path.join(distribuire_path, "缺失报告.xlsx")
        wb_report.save(report_path)
        wb_report.close()
        summary["missing_report"] = report_path
        log(tr("✔ 缺失报告生成（稳定版）"))

    # ④ 回写订单到供应商文件（只修改 ORDINE 列，保留原文件公式）
    log("\n" + "=" * 10)
    log(tr("回写"))
    log("=" * 10)
    
    if USE_MONITORING:
        perf_monitor.start_timer("write_back")

    # 找到主表 COLLI 列
    col_colli_main, _ = find_col(ws_main, COL_COLLI)

    # 同一个文件夹上次处理时写进供应商表的数量：这次先清掉，再写新的
    prev_written = _previous_written(os.path.dirname(os.path.dirname(ordine_file)))

    # 从备份恢复原始供应商文件（确保回写时使用的是原始文件）
    import shutil
    backup_dir = os.path.join(distribuire_path, ".backup")
    if os.path.exists(backup_dir):
        for file in os.listdir(backup_dir):
            if file.lower().endswith((".xls", ".xlsx")):
                src = os.path.join(backup_dir, file)
                dst = os.path.join(distribuire_path, file)
                shutil.copy2(src, dst)
        log(tr("✔ 已从备份恢复原始供应商文件用于回写"))
    else:
        log(tr("⚠ 未找到备份目录，使用当前文件"))

    for n_file, file in enumerate(supplier_files):
        P(70 + int(25 * n_file / max(1, len(supplier_files))),
          tr("回写订单 {0}/{1}: {2}", n_file + 1, len(supplier_files), file))
        path = os.path.join(distribuire_path, file)

        # 直接打开原文件，不经过任何转换
        # 先检查是否是有效的 xlsx
        try:
            if path.lower().endswith('.xlsx') and is_valid_xlsx(path):
                work_path = path
            elif path.lower().endswith('.xls'):
                # .xls 文件无法用 openpyxl 直接操作，需要 Excel COM
                log(tr("⚠ {0} 是 .xls 格式，需要使用 Excel COM（可能影响公式）", file))
                work_path = convert_to_xlsx(path)
            else:
                # 伪 xlsx（实际是旧格式），用 Excel COM 转换一次
                log(tr("⚠ {0} 不是标准 xlsx，尝试转换...", file))
                work_path = convert_to_xlsx(path)
        except Exception as e:
            log(tr("⚠ {0} 打开失败: {1}", file, e))
            continue

        try:
            # 用默认模式打开（data_only=False），保留公式
            # 对于 EAN 列，通常是值；对于价格列，可能有公式
            # 我们需要读取 EAN 的值来匹配，所以用 Excel COM 计算一次
            # 但只针对 EAN 列，不修改其他列
            
            # 先尝试直接用 openpyxl 读取（假设 EAN 是值）
            wb_supplier = load_workbook(work_path)  # 默认 data_only=False
        except Exception as e:
            log(tr("⚠ {0} 读取失败: {1}", file, e))
            continue

        ws = wb_supplier.active

        col_ean, row_ean_supplier = find_col(ws, COL_CODICE_EAN)
        col_ord, row_ord = find_col(ws, COL_ORDINE)

        if not col_ean or not col_ord:
            log(tr("⚠ {0} 缺少必要列 ({1}/{2})", file, COL_CODICE_EAN, COL_ORDINE))
            wb_supplier.close()
            continue

        # 数据从表头行之后开始
        supplier_header_row = max(row_ean_supplier, row_ord)
        # ORDINE 是公式（比如 Meloni 的 =L*E：L 填箱数）时，箱数写进箱数列，ORDINE 公式保留
        col_price_w, _ = find_col(ws, COL_PREZZO_NETTO)
        pack_col_w, _ = find_pack_col(ws, supplier_header_row, col_ord, col_price_w or 0)
        order_input = find_order_input(ws, supplier_header_row, col_ord, pack_col_w)
        if order_input:
            log(tr("  {0}：ORDINE 列是公式（箱数 × 每箱件数），箱数写进 {1} 列", file, get_column_letter(order_input[0])))

        # 构建供应商文件 EAN 索引
        # 如果 EAN 列有公式，需要用 Excel COM 计算
        supplier_last = get_last_row(ws, col_ean)
        supplier_ean_index = {}
        
        # 检查 EAN 列是否有公式
        has_formula = False
        for j in range(supplier_header_row + 1, min(supplier_header_row + 11, supplier_last + 1)):
            val = ws.cell(j, col_ean).value
            if isinstance(val, str) and val.startswith('='):
                has_formula = True
                break
        
        if has_formula:
            # EAN 列有公式，用 Excel COM 计算一次
            log(tr("  {0} EAN 列有公式，使用 Excel COM 计算...", file))
            calc_path = calculate_formulas(work_path)
            wb_calc = load_workbook(calc_path, data_only=True)
            ws_calc = wb_calc.active
            
            for j in range(supplier_header_row + 1, supplier_last + 1):
                e = clean_ean(ws_calc.cell(j, col_ean).value)
                if e:
                    supplier_ean_index[e] = j
            
            wb_calc.close()
        else:
            # EAN 列是值，直接读取
            for j in range(supplier_header_row + 1, supplier_last + 1):
                e = clean_ean(ws.cell(j, col_ean).value)
                if e:
                    supplier_ean_index[e] = j

        cleared = _clear_previous(ws, col_ord, order_input, supplier_ean_index, prev_written.get(file, {}))
        if cleared:
            log(tr("  {0}：清掉上次写入的 {1} 个数量", file, cleared))

        count = 0
        main_last = get_last_row(ws_main, col_ean_main)
        for i in range(2, main_last + 1):
            # 检查该行是否分配给当前供应商
            assigned_supplier = ws_main.cell(i, SUPPLIER_RESULT_COL).value
            if normalize(assigned_supplier) != normalize(file):
                continue

            ean = clean_ean(ws_main.cell(i, col_ean_main).value)

            if ean in supplier_ean_index:
                j = supplier_ean_index[ean]
                _write_order(ws, j, col_ord, order_input, ws_main.cell(i, col_colli_main).value)
                count += 1

        for sup in summary["suppliers"]:
            if sup["name"] == file:
                sup["written"] = count

        # 保存文件
        try:
            wb_supplier.save(work_path)
            wb_supplier.close()
            if work_path == path:
                log(tr("  {0} ✔ 写入 {1} (仅修改 ORDINE 列，公式保留)", file, count))
            else:
                log(tr("  {0} ✔ 写入 {1} (转换后保存)", file, count))
        except Exception as e:
            log(tr("❌ 保存失败: {0} | 错误: {1}", file, e))
            wb_supplier.close()

    # 保存主文件到原路径
    P(96, tr("保存主订单..."))
    safe_save(wb_main, ordine_file, calc_main, log)
    wb_main.close()

    # 清理临时文件
    safe_cleanup_calculated_files(log)
    
    if USE_MONITORING:
        write_back_time = perf_monitor.stop_timer("write_back")
        log(tr("[监控] 回写耗时: {0:.2f}秒", write_back_time))
    
    # 结束性能监控会话
    if USE_MONITORING:
        total_time = perf_monitor.stop_timer("total_process")
        
        try:
            session_data = end_session()
            
            log("\n" + "=" * 50)
            log(tr("[性能监控报告]"))
            log("=" * 50)
            log(tr("总耗时: {0:.2f}秒", total_time))
            
            # 打印各阶段耗时
            for metric_name in ['eliminare', 'build_price_matrix', 'select_supplier', 'write_back']:
                stats = perf_monitor.get_metric_stats(metric_name)
                if stats:
                    log(tr("{0}: {1:.2f}秒", metric_name, stats['avg']))
            
            # 系统资源（可选，不阻塞主流程）
            try:
                system = perf_monitor.get_system_metrics()
                log(tr("CPU使用率: {0}%", system.get('cpu_percent', 'N/A')))
                log(tr("内存使用: {0:.1f}MB", system.get('process_memory_mb', 0)))
            except Exception as e:
                log(tr("⚠ 无法获取系统资源信息: {0}", e))
            log("=" * 50)
        except Exception as e:
            log(tr("⚠ 性能监控报告生成失败: {0}", e))
            log(tr("总耗时: {0:.2f}秒", total_time))

    log("\n" + "=" * 50)
    log(tr("[完成] 处理完成"))
    log("=" * 50)
    P(100, tr("处理完成"))
    summary["ok"] = True
    summary["elapsed"] = time.time() - t_start
    ps = summary.pop("_plan_state", {})
    summary["state"] = {
        "ordine_file": ordine_file, "distribuire_path": distribuire_path,
        "rows": rows_data, "rules": ps.get("rules", []), "baseline": ps.get("baseline", {}),
        "assignment": assignment, "sources": ps.get("sources", {}),
        # 换方案用：每个方案的分配和完整的起订规则
        "scenario_assigns": ps.get("scenario_assigns") or [], "rules_all": ps.get("rules_all") or [],
        "labels": summary.get("supplier_labels") or {},
        # 写完以后的文件指纹：下次处理时认出「还是程序写的」→ 恢复原文件再算
        "written": _written_fingerprints(ordine_file, distribuire_path),
    }
    _save_history(summary, log, settings.get("history_keep_days", 180))
    save_state(summary)
    return summary


def _price_files(folder):
    """distribuire 里的报价文件（不含临时文件、缺失报告、.xls 转换出来的 .xls.xlsx）"""
    out = []
    for f in sorted(os.listdir(folder)):
        lf = f.lower()
        if lf.endswith((".xls", ".xlsx")) and not f.startswith("~") and "缺失报告" not in f \
                and ".calc." not in lf and not lf.endswith(".xls.xlsx"):
            out.append(f)
    return out


def _written_fingerprints(ordine_file, distribuire_path):
    import originals as OG
    return {"ORDINE": OG.record([ordine_file]),
            "distribuire": OG.record([os.path.join(distribuire_path, f) for f in _price_files(distribuire_path)])}


def _save_history(summary, log, keep_days=None, update=False):
    """存一份到历史记录（失败只提醒，不影响处理结果）"""
    try:
        import history
        if update:
            history.update_run(summary)
        else:
            history.save_run(summary, keep_days=keep_days)
    except Exception as e:
        log(tr("⚠ 历史记录保存失败：{0}", e))


# =====================
# 处理完以后修改分配：只改动变了的商品，重新写回文件
# =====================
def _state_path(ordine_file):
    root = os.path.dirname(os.path.dirname(ordine_file))
    return os.path.join(root, "_备份", "last_allocation.json")


def save_state(summary):
    """把最近一次的分配存到 总文件夹/_备份/last_allocation.json，重启程序后也能修改"""
    import json
    try:
        st = summary["state"]
        path = _state_path(st["ordine_file"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {k: v for k, v in summary.items() if k != "state"}
        data["state"] = dict(st, assignment={str(k): v for k, v in st["assignment"].items()},
                             baseline={str(k): v for k, v in st["baseline"].items()},
                             sources={str(k): v for k, v in st["sources"].items()},
                             scenario_assigns=[{str(k): v for k, v in a.items()}
                                               for a in st.get("scenario_assigns") or []])
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
    except Exception:
        pass


def load_state(root_path):
    """读取上次保存的分配；没有或读不了返回 None"""
    import json
    path = os.path.join(root_path, "_备份", "last_allocation.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        st = data["state"]
        for k in ("assignment", "baseline", "sources"):
            st[k] = {int(r): v for r, v in st[k].items()}
        st["scenario_assigns"] = [{int(r): v for r, v in a.items()} for a in st.get("scenario_assigns") or []]
        from history import _new_names
        _new_names(data)
        if not os.path.exists(st["ordine_file"]):
            return None
        return data
    except Exception:
        return None


def _previous_written(root):
    """上次处理（_备份/last_allocation.json）写进各供应商表的数量：{供应商: {EAN: 箱数}}"""
    prev = load_state(root) if root else None
    out = {}
    if not prev:
        return out
    st = prev.get("state") or {}
    assign = st.get("assignment") or {}
    for r in st.get("rows") or []:
        s_ = assign.get(r.get("row"))
        if s_ and r.get("ean"):
            out.setdefault(s_, {})[r["ean"]] = _order_qty(r, s_)   # 同一 EAN 多行时取最后一行，和回写一致
    return out


def _same_qty(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return False


def _clear_previous(ws, col_ord, order_input, index, prev):
    """
    清掉上次程序写进这个供应商表的数量。
    只清「现在的值 == 上次写的值」的格子，别人手动改过的不动。
    """
    n = 0
    for e, q_old in prev.items():
        j = index.get(e)
        if not j or q_old in (None, ""):
            continue
        hit = False
        if order_input:
            colli_col, pair = order_input
            if _same_qty(ws.cell(j, colli_col).value, q_old):
                ws.cell(j, colli_col).value = None
                hit = True
            cur = ws.cell(j, col_ord).value
            if not (isinstance(cur, str) and cur.startswith("=")):
                # 旧版本把箱数直接写进了 ORDINE、盖掉了公式：恢复公式
                if cur in (None, "") or _same_qty(cur, q_old):
                    ws.cell(j, col_ord).value = order_formula(pair, j)
                    hit = hit or cur not in (None, "")
        elif _same_qty(ws.cell(j, col_ord).value, q_old):
            ws.cell(j, col_ord).value = None
            hit = True
        n += hit
    return n


def _write_order(ws, j, col_ord, order_input, colli):
    """把箱数写进供应商表第 j 行：一般写 ORDINE；ORDINE 是公式时写箱数列，并保证 ORDINE 公式还在"""
    if order_input:
        colli_col, pair = order_input
        ws.cell(j, colli_col).value = colli
        cur = ws.cell(j, col_ord).value
        if not (isinstance(cur, str) and cur.startswith("=")):
            ws.cell(j, col_ord).value = order_formula(pair, j)
    else:
        ws.cell(j, col_ord).value = colli


def _detect_pack_col(ws_values, path, header_row, col_ord, col_price):
    """每箱件数列：先在值副本里按表头找，找不到再打开原文件看公式"""
    pc, how = find_pack_col(ws_values, header_row, col_ord or 0, col_price)
    if pc:
        return pc, how
    try:
        src = convert_to_xlsx(path)
        wb_f = load_workbook(src, read_only=True)
        try:
            return find_pack_col(wb_f.active, header_row, col_ord or 0, col_price)
        finally:
            wb_f.close()
    except Exception:
        return None, None


def _supplier_work_path(path):
    """回写时实际写入的文件（.xls 会写进转换出来的 .xls.xlsx）"""
    if path.lower().endswith(".xlsx") and is_valid_xlsx(path):
        return path
    alt = path + ".xlsx"
    if os.path.exists(alt) and is_valid_xlsx(alt):
        return alt
    return convert_to_xlsx(path)


def _supplier_index(ws, work_path):
    """返回 (ORDINE 列号, {EAN: 行号}, 箱数写法)，找不到列返回 (None, None, None)"""
    col_ean, row_ean = find_col(ws, COL_CODICE_EAN)
    col_ord, row_ord = find_col(ws, COL_ORDINE)
    if not col_ean or not col_ord:
        return None, None, None
    header = max(row_ean, row_ord)
    last = get_last_row(ws, col_ean)
    src = ws
    wb_calc = None
    for j in range(header + 1, min(header + 11, last + 1)):
        v = ws.cell(j, col_ean).value
        if isinstance(v, str) and v.startswith("="):
            wb_calc = load_workbook(calculate_formulas(work_path), data_only=True)
            src = wb_calc.active
            break
    index = {}
    for j in range(header + 1, last + 1):
        e = clean_ean(src.cell(j, col_ean).value)
        if e:
            index[e] = j
    if wb_calc:
        wb_calc.close()
    col_price, _ = find_col(ws, COL_PREZZO_NETTO)
    pack_col, _ = find_pack_col(ws, header, col_ord, col_price or 0)
    return col_ord, index, find_order_input(ws, header, col_ord, pack_col)


def _write_missing_report(distribuire_path, rows, assignment):
    from openpyxl import Workbook
    from datetime import datetime
    info = {}
    for r in rows:
        if assignment.get(r["row"]) is None and r.get("ean"):
            a, c = info.get(r["ean"], (r.get("articolo", ""), 0))
            info[r["ean"]] = (a, c + 1)
    path = os.path.join(distribuire_path, "缺失报告.xlsx")
    if not info:
        return None
    wb = Workbook()
    ws = wb.active
    ws.title = "缺失报告"
    ws.append(["缺失供应商报价的产品"])
    ws.append(["生成时间: %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    ws.append(["总缺失数: %d" % sum(c for _, c in info.values())])
    ws.append([])
    ws.append(["序号", "EAN", "ARTICOLO", "数量"])
    for i, (e, (a, c)) in enumerate(info.items(), 1):
        ws.append([i, e, a, c])
    wb.save(path)
    wb.close()
    return path


def can_switch_scenario(data):
    """结果里存了每个方案的分配，才能不重算直接换方案"""
    st = (data or {}).get("state") or {}
    scs = (data or {}).get("scenarios") or []
    return len(scs) > 1 and len(st.get("scenario_assigns") or []) == len(scs)


def switch_scenario(data, idx, log, progress=None):
    """
    结果页换方案：不重算，直接用当时算好的第 idx 个方案的分配，只把换了供应商的商品重新写入文件，
    历史记录同一条跟着改（选用的方案、金额、缺失商品）。之前手动修改的分配会被这个方案替换掉。
    返回 (新 summary, 换了供应商的商品数)
    """
    import copy as _copy
    st = data["state"]
    scs = data["scenarios"]
    if not can_switch_scenario(data) or not 0 <= idx < len(scs):
        raise ValueError(tr("这次结果没有存各方案的分配，不能直接换方案，请重新处理"))
    excl = set(scs[idx].get("excluded") or [])
    new = dict(st["scenario_assigns"][idx])
    baseline = st["baseline"]
    sources = {}
    for r in st["rows"]:
        b, f = baseline.get(r["row"]), new.get(r["row"])
        if b != f:
            sources[r["row"]] = "方案排除" if b in excl else "自动凑单"
    d2 = _copy.deepcopy(data)
    d2["state"]["rules"] = [ru for ru in (st.get("rules_all") or st["rules"]) if ru["target"] not in excl]
    for i, x in enumerate(d2["scenarios"]):
        x["chosen"] = (i == idx)
    d2["scenario"] = scs[idx]["name"]
    log("\n" + "=" * 50)
    log(tr("[换方案] 改用：{0}", tr(scs[idx]["name"])))
    return rewrite_assignment(d2, new, sources, log, progress, force=True)


def rewrite_assignment(data, new_assign, new_sources, log, progress=None, force=False, qty=None):
    """
    data: 上次处理的 summary（含 state）
    new_assign: {行号: 供应商}，new_sources: {行号: 来源}
    只处理供应商变了的商品：从原供应商文件清掉、写进新供应商文件，更新主订单 AD / AC 列。
    返回新的 summary。
    """
    import io
    import copy as _copy
    from collections import defaultdict
    from allocation_plan import build_report

    def P(pct, text):
        if progress:
            try:
                progress(pct, text)
            except Exception:
                pass

    st = data["state"]
    rows, rules, baseline = st["rows"], st["rules"], st["baseline"]
    old = st["assignment"]
    ordine_file, dist = st["ordine_file"], st["distribuire_path"]
    # 改了数量的商品（qty = {行号: 新数量}）
    old_q = {r["row"]: r.get("qty") for r in rows}
    if qty:
        rows = [set_row_qty(r, qty[r["row"]]) if r["row"] in qty else r for r in rows]
    q_changed = {r["row"] for r in rows if r.get("qty") != old_q.get(r["row"])}
    changed = [r for r in rows if old.get(r["row"]) != new_assign.get(r["row"]) or r["row"] in q_changed]
    out = _copy.deepcopy(data)
    if not changed and not force:
        return out, 0

    log("\n" + "=" * 50)
    n_sup = sum(1 for r in changed if old.get(r["row"]) != new_assign.get(r["row"]))
    log(tr("[修改分配] 有 {0} 个商品换了供应商，重新写入文件", n_sup))
    if q_changed:
        log(tr("[修改分配] 有 {0} 个商品改了数量", len(q_changed)))
    P(5, tr("备份原始文件..."))
    try:
        out["backup_dir"] = _backup_inputs(ordine_file, dist, log)
    except Exception as e:
        log(tr("⚠ 自动备份失败: {0}", e))

    # 主订单：AD 列（供应商）和 AC 列（改动说明）
    P(20, tr("更新主订单..."))
    rep = build_report(rows, rules, baseline, new_assign, new_sources)
    with open(ordine_file, "rb") as f:
        wb = load_workbook(io.BytesIO(f.read()))
    ws = wb.active
    col_colli_m, _ = find_col(ws, COL_COLLI)
    col_pezzi_m, _ = find_col(ws, "PEZZI")
    for r in changed:
        ws.cell(r["row"], SUPPLIER_RESULT_COL).value = new_assign.get(r["row"])
        if r.get("colli") and col_colli_m and new_assign.get(r["row"]):
            ws.cell(r["row"], col_colli_m).value = _order_qty(r, new_assign.get(r["row"]))
        if r["row"] in q_changed:
            v = r.get("qty") or 0
            v = int(v) if float(v).is_integer() else v
            if r.get("pieces") is not None:
                if col_pezzi_m:
                    ws.cell(r["row"], col_pezzi_m).value = v
                if col_colli_m and not new_assign.get(r["row"]):
                    ws.cell(r["row"], col_colli_m).value = 0
            elif col_colli_m:
                ws.cell(r["row"], col_colli_m).value = v
    last = max([r["row"] for r in rows] + [2])
    for i in range(1, last + 1):
        ws.cell(i, MIN_ORDER_NOTE_COL).value = None
    if rep["changes"]:
        ws.cell(1, MIN_ORDER_NOTE_COL).value = tr("供应商调整")
        for ch in rep["changes"]:
            o = normalize(ch["old"]) if ch["old"] else tr("无")
            n = normalize(ch["new"]) if ch["new"] else tr("不采购")
            d = f" ({ch['delta']:+.2f}€)" if ch["delta"] is not None else ""
            ws.cell(ch["row"], MIN_ORDER_NOTE_COL).value = f"{tr(ch['source'])}: {o} → {n}{d}"
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    with open(ordine_file, "wb") as f:
        f.write(buf.getvalue())
    log(tr("  ✔ 已保存到 {0}", os.path.basename(ordine_file)))

    # 供应商文件：受影响的 EAN 重新计算应写的数量（同一 EAN 多行时取最后一行，和第一次回写一致）
    affected = defaultdict(set)
    for r in changed:
        for s_ in (old.get(r["row"]), new_assign.get(r["row"])):
            if s_ and r.get("ean"):
                affected[s_].add(r["ean"])
    want = {}
    for r in rows:
        s_ = new_assign.get(r["row"])
        if s_ and r.get("ean"):
            v = _order_qty(r, s_)
            if r["row"] in q_changed and not float(v or 0) > 0:
                want.pop((s_, r["ean"]), None)          # 数量改成 0：从供应商订单里清掉
                continue
            want[(s_, r["ean"])] = v
    written_delta = defaultdict(int)
    files = sorted(affected)
    for n, file in enumerate(files):
        P(30 + int(60 * n / max(1, len(files))), tr("回写订单 {0}/{1}: {2}", n + 1, len(files), file))
        try:
            work = _supplier_work_path(os.path.join(dist, file))
            wb_s = load_workbook(work)
            ws_s = wb_s.active
            col_ord, index, order_input = _supplier_index(ws_s, work)
            if not col_ord:
                log(tr("⚠ {0} 缺少必要列 ({1}/{2})", file, COL_CODICE_EAN, COL_ORDINE))
                wb_s.close()
                continue
            set_n = clear_n = 0
            for e in affected[file]:
                j = index.get(e)
                if not j:
                    continue
                v = want.get((file, e))
                _write_order(ws_s, j, col_ord, order_input, v)
                if v is None:
                    clear_n += 1
                else:
                    set_n += 1
            wb_s.save(work)
            wb_s.close()
            written_delta[file] = sum(1 for r in changed if new_assign.get(r["row"]) == file) - \
                sum(1 for r in changed if old.get(r["row"]) == file)
            log(tr("  {0} ✔ 写入 {1} 个，清除 {2} 个", file, set_n, clear_n))
        except Exception as e:
            log(tr("❌ 保存失败: {0} | 错误: {1}", file, e))

    P(92, tr("更新缺失报告..."))
    try:
        _write_missing_report(dist, rows, new_assign)
    except Exception as e:
        log(tr("⚠ {0}", e))

    # 更新结果
    sup_by = {x["name"]: x for x in rep["suppliers"]}
    for sp in out.get("suppliers", []):
        x = sup_by.get(sp["name"])
        if not x:
            continue
        sp["colli"], sp["amount"] = x["after_colli"], x["after_amount"]
        sp["before_colli"], sp["before_amount"] = x["before_colli"], x["before_amount"]
        sp["rows"] = sum(1 for r in rows if new_assign.get(r["row"]) == sp["name"])
        sp["written"] = sp.get("written", 0) + written_delta.get(sp["name"], 0)
    out["changes"] = rep["changes"]
    out["priced_rows"] = sum(1 for r in rows if new_assign.get(r["row"]))
    out["missing"] = len(rows) - out["priced_rows"]
    try:
        import scenarios as SC
        excl = next((x.get("excluded") or [] for x in out.get("scenarios") or [] if x.get("chosen")), [])
        out["missing_items"] = SC.missing_items(rows, new_assign, excl)
    except Exception as e:
        log(tr("⚠ 缺失商品明细整理失败：{0}", e))
    out["baseline_cost"], out["final_cost"] = rep["baseline_cost"], rep["final_cost"]
    mo = out.setdefault("min_order", {})
    mo.update(rules=rep["rules"], extra_cost=rep["extra_cost"], changes=len(rep["changes"]),
              warning=None if all(r["status"] != "未达标" for r in rep["rules"])
              else tr("有起订供应商没有达标，请检查"))
    out["state"] = dict(st, assignment=dict(new_assign), sources=dict(new_sources), rows=rows)
    try:
        out["state"]["written"] = _written_fingerprints(ordine_file, dist)
    except Exception as e:
        log(tr("⚠ {0}", e))
    _save_history(out, log, update=True)
    save_state(out)
    log(tr("[完成] 修改分配已写入"))
    P(100, tr("修改分配已写入"))
    return out, len(changed)
