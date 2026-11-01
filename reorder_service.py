"""
订货建议（从货物分配系统 SistemaIntegrato 的 Suggerimenti Ordini 搬过来）

两种来源：
  A. 导入门店发来的订货清单（每个文件对应一家门店），自动识别表头
  B. 按星期几选供应商组（suppliers_config.json），提取这家门店有销量的商品
然后用原程序的算法（fenpei.models.ProductDataProcessor.process_reorder_data）按
这家门店的销量、数据库库存、订货周期算建议量（件），0 = 不订。

最后可以生成采购系统的主订单（ORDINE 文件夹）：CODICE EAN + 件数(PEZZI)，
采购系统再按各批发商自己的每箱件数换算成箱数。
"""
import os
import re
import math
import shutil
from datetime import datetime, timedelta

# 门店编号 → 名字（和原程序「分货」页一致；0 = 仓库）
STORES = {0: "仓库", 1: "FOIANO", 2: "MARLIA", 3: "S.FILIPPO", 4: "S.MARCO", 5: "S.CONCORDIO"}
DAYS = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì"]
DEFAULT_SALES_DAYS = [30, 60, 90, 180]       # 原程序 4 个销量周期的默认值
QUERY_DAYS_OPTIONS = [90, 120, 150, 180, 210, 240]
REORDER_CYCLE_OPTIONS = [15, 30, 45, 60, 90]

# 订货清单里各字段可能的表头（比较时忽略大小写和空格）
FIELD_ALIASES = {
    "code": ["商品编号", "Cod.Art.", "Cod.Art", "Cod.Prod.", "Codice prodotto", "CodArticolo", "Codice articolo"],
    "ean": ["商品条码", "Codice EAN", "CODICE EAN", "Codice Ean", "CodBarre", "EAN", "Barcode", "Codice a barre"],
    "name": ["商品名称", "Descrizione", "DESCRIZIONE", "ARTICOLO", "Articolo", "Descrizione articolo"],
    "qty": ["数量", "Quantità ordine", "Quantità", "Qtà ordine", "Qta ordine", "Qta", "Qtà", "Colli", "Ord.Colli"],
    "stock": ["库存", "Giacenza", "Inventario", "Stock"],
    "supplier": ["商家编号", "IDFornitore", "Fornitore"],
}


def _norm(v):
    return re.sub(r"\s+", "", str(v or "")).lower()


_ALIAS = {f: {_norm(a) for a in names} for f, names in FIELD_ALIASES.items()}


def _clean_code(v):
    """商品编号/条码：'30450'、30450.0、' 30450 ' → '30450'；空 → ''"""
    if v is None:
        return ""
    if isinstance(v, float):
        if v != v:  # NaN
            return ""
        if v == int(v):
            v = int(v)
    s = str(v).strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    if re.fullmatch(r"\d+(\.\d+)?[eE][+]?\d+", s):
        try:
            s = str(int(float(s)))
        except ValueError:
            pass
    return s


def _num(v):
    try:
        if v in (None, ""):
            return None
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def guess_store(filename):
    """文件名里有门店名就返回门店编号，否则 None"""
    low = re.sub(r"[\s._\-]", "", os.path.basename(filename or "").upper())
    for sid, name in STORES.items():
        if sid and re.sub(r"[\s._\-]", "", name.upper()) in low:
            return sid
    return None


BATCH_DIR = "4 比价结果"          # workspace.RESULTS
OLD_BATCH_DIRS = ("批发商比价",)  # 以前的结果文件夹名（还认得，不再新建）


def batch_folder(root, day=None):
    """
    这一批（检查清单确认时生成主订单）的文件夹，结构固定、一看就懂：
      根目录\\4 比价结果\\2026-10-20\\            ← 这一天的一批（这里放整批的调货单）
      根目录\\4 比价结果\\2026-10-20\\MARLIA\\     ← 每家门店一个，里面 ORDINE / eliminare / distribuire
    同一天再确认一次就用同一个文件夹（主订单替换，原来的挪进 ORDINE\\_旧订单）
    """
    day = day or datetime.now()
    return os.path.join(root, BATCH_DIR, f"{day:%Y-%m-%d}")


def batch_store_folder(root, store_id, day=None):
    return os.path.join(batch_folder(root, day), STORES.get(store_id, str(store_id)))


def batch_root_of(path):
    """path 是 根目录\\4 比价结果\\日期\\门店（或日期；以前的「批发商比价」也认）时返回根目录，否则 None"""
    if not path:
        return None
    parts = os.path.normpath(path).split(os.sep)
    for k in (3, 2):
        if len(parts) > k and parts[-k] in (BATCH_DIR,) + OLD_BATCH_DIRS and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[-k + 1] or ""):
            return os.sep.join(parts[:-k]) or os.sep
    return None


def make_store_folder(path):
    """建好门店的比价文件夹和三个子文件夹"""
    for sub in ("ORDINE", "eliminare", "distribuire"):
        os.makedirs(os.path.join(path, sub), exist_ok=True)
    return path


# =====================================================================
# 订货清单解析
# =====================================================================
def parse_order_file(path):
    """
    读取门店订货清单，按表头自动识别字段。
    返回 {"rows": [{code, ean, name, qty, stock, supplier}], "fields": {字段: 表头}, "sheet", "header_row", "warnings"}
    找不到商品编号也找不到条码时抛 ValueError。
    """
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        sheets = list(wb.worksheets)
        # 原程序优先找名字里有 GRIGLIA / GRILIA 的表
        sheets.sort(key=lambda ws: 0 if ("GRIGLIA" in ws.title.upper() or "GRILIA" in ws.title.upper()) else 1)
        best = None
        for ws in sheets:
            grid = [list(r) for r in ws.iter_rows(min_row=1, max_row=50, values_only=True)]
            for i, row in enumerate(grid):
                found = {}
                for c, v in enumerate(row):
                    n = _norm(v)
                    if not n:
                        continue
                    for f, al in _ALIAS.items():
                        if f not in found and n in al:
                            found[f] = (c, str(v).strip())
                if ("code" in found or "ean" in found):
                    score = len(found)
                    if best is None or score > best[0]:
                        best = (score, ws, i, found)
            if best and best[1] is ws:
                break
        if not best:
            raise ValueError("没有找到表头：需要有「商品编号 / Cod.Art. / CodArticolo」或「商品条码 / Codice EAN」这样的列")
        _, ws, hdr, found = best
        warnings = []
        rows = []
        for r in ws.iter_rows(min_row=hdr + 2, values_only=True):
            r = list(r)

            def get(f):
                if f not in found:
                    return None
                c = found[f][0]
                return r[c] if c < len(r) else None
            code, ean = _clean_code(get("code")), _clean_code(get("ean"))
            if not code and not ean:
                continue
            rows.append({"code": code, "ean": ean, "name": str(get("name") or "").strip(),
                         "qty": _num(get("qty")), "stock": _num(get("stock")),
                         "supplier": _clean_code(get("supplier"))})
        # 商品编号列里大多不是数字（比如表头对错了列）→ 改用条码
        if "code" in found and rows:
            ok = sum(1 for x in rows if re.fullmatch(r"\d{1,9}", x["code"] or ""))
            if ok < len(rows) * 0.5:
                warnings.append(f"「{found['code'][1]}」列里大多不是数字商品编号，改用条码查商品")
                for x in rows:
                    x["code"] = ""
        if "code" not in found:
            warnings.append("文件里没有商品编号列，用条码到数据库里查商品")
        return {"rows": rows, "fields": {f: v[1] for f, v in found.items()}, "sheet": ws.title,
                "header_row": hdr + 1, "warnings": warnings}
    finally:
        wb.close()


# =====================================================================
# 数据库
# =====================================================================
def make_db(db_settings=None):
    """按界面里的数据库设置建 DatabaseManager（原程序的那个类，查询语句不变）"""
    from fenpei.config import DatabaseConfig
    from fenpei.database import DatabaseManager
    if db_settings:
        DatabaseConfig.configure(**{k: db_settings.get(k) for k in ("host", "port", "user", "password", "database")})
    return DatabaseManager()


_WRITE_PRIVS = ("ALL PRIVILEGES", "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "GRANT OPTION",
                "SUPER", "FILE", "TRUNCATE", "INDEX", "EVENT", "TRIGGER", "SHUTDOWN")


def account_info(db):
    """当前连接用的账号和权限：{"user": "名字@主机", "readonly": True/False, "grants": [...]}"""
    import pandas as pd
    from sqlalchemy import text
    user = pd.read_sql(text("SELECT CURRENT_USER() AS u"), db.engine)["u"].iloc[0]
    grants = [str(r[0]) for r in pd.read_sql(text("SHOW GRANTS"), db.engine).itertuples(index=False)]
    readonly = not any(p in g.upper().split(" ON ")[0] for g in grants for p in _WRITE_PRIVS)
    return {"user": str(user), "readonly": readonly, "grants": grants}


def test_connection(db):
    import pandas as pd
    from sqlalchemy import text
    pd.read_sql(text("SELECT 1 AS ok"), db.engine)
    return True


def codes_from_eans(db, eans):
    """
    条码 → 商品编号：先查 prodotti.CodBarre，再查 codbarre 表（一个商品多个条码）。
    商品表查不了（数据库断了等）直接报错——以前悄悄跳过，结果整张清单都变成「数据库没有」
    """
    import pandas as pd
    eans = sorted({e for e in eans if e})
    out = {}
    for i in range(0, len(eans), 2000):
        chunk = eans[i:i + 2000]
        ph = ", ".join(["%s"] * len(chunk))
        for k, q in enumerate((f"SELECT CodArticolo, CodBarre FROM prodotti WHERE CodBarre IN ({ph})",
                               f"SELECT CodArticolo, CodBarre FROM codbarre WHERE CodBarre IN ({ph})")):
            try:
                df = pd.read_sql(q, db.engine, params=tuple(chunk))
            except Exception as e:
                if k == 0:
                    raise RuntimeError(f"用条码查商品编号失败（数据库）：{e}") from e
                continue              # codbarre（多条码表）没有也不要紧
            for _, x in df.iterrows():
                e = _clean_code(x["CodBarre"])
                if e and e not in out and x["CodArticolo"]:
                    out[e] = str(int(x["CodArticolo"]))
    return out


def supplier_info(db, codes):
    """
    商品编号 → (Codice Fornitore 供应商自己的货号, 供应商名称)，来自数据库商品表（只读）。
    只是显示用：查不到 / 查询失败都返回空的，不影响检查和比价
    """
    import pandas as pd
    codes = sorted({int(c) for c in codes if str(c).strip().isdigit()})
    out = {}
    for i in range(0, len(codes), 2000):
        chunk = codes[i:i + 2000]
        ph = ", ".join(["%s"] * len(chunk))
        try:
            df = pd.read_sql(f"SELECT p.CodArticolo, COALESCE(p.ArticoloForn,'') AS ArticoloForn, "
                             f"COALESCE(f.Nome,'') AS Nome FROM prodotti p "
                             f"LEFT JOIN fornitore f ON p.IDFornitore = f.IDFornitore "
                             f"WHERE p.CodArticolo IN ({ph})", db.engine, params=tuple(chunk))
        except Exception:
            return out
        for _, x in df.iterrows():
            out.setdefault(str(int(x["CodArticolo"])), (str(x["ArticoloForn"] or "").strip(), str(x["Nome"] or "").strip()))
    return out


def add_supplier_info(rows, info):
    """检查清单的行加上 sup_code（Codice Fornitore）、sup_name（数据库登记的供应商）。返回加了几行"""
    n = 0
    for x in rows:
        v = info.get(str(x.get("code") or ""))
        if v:
            x["sup_code"], x["sup_name"] = v
            n += 1
    return n


def extract_products(db, store_id, day, query_days, supplier_groups=None):
    """
    不导入文件时（原程序的 Estrazione Prodotti）：这家门店最近 query_days 天里，
    这一天对应的供应商组有销量的商品。返回 (商品编号列表, 供应商 ID 列表)
    """
    from fenpei.config import AppConfig
    groups = supplier_groups or AppConfig.get_supplier_groups()
    supplier_ids = groups.get(day, [])
    if not supplier_ids:
        raise ValueError(f"{day} 没有配置供应商（suppliers_config.json）")
    end_date = datetime.now()
    start_date = end_date - timedelta(days=int(query_days))
    codes = db.get_product_codes(store_id, supplier_ids, start_date, end_date)
    return codes, supplier_ids


DB_WORKERS = 3          # 几家门店同时查数据库（每家店里面还会同时发几条查询，3 家 × 7 条以内，不超过连接池）


def _timed(label, store_id, fn, *a, **k):
    """跑 fn 并在日志里记一笔用了几秒（看慢在哪一条查询；不改结果）"""
    import time
    import logging
    t = time.time()
    try:
        return fn(*a, **k)
    finally:
        dt = time.time() - t
        if dt >= 1:
            logging.getLogger("procurement").info("[数据库] %s%s：%.1f 秒",
                                                   (str(STORES.get(store_id, store_id)) + " ") if store_id is not None else "", label, dt)


def ping(db, n=3):
    """数据库来回一次要多少毫秒（取 n 次里最快的）：网络 / VPN 慢的话这个数就大；查不了返回 None"""
    import time
    best = None
    try:
        from sqlalchemy import text
        with db.engine.connect() as c:
            for _ in range(n):
                t = time.time()
                c.execute(text("SELECT 1"))
                ms = (time.time() - t) * 1000
                best = ms if best is None else min(best, ms)
    except Exception:
        return None
    return best


def per_store(stores, fn, on_store=None, workers=DB_WORKERS):
    """
    几家门店同时跑 fn(门店号)（查数据库大部分时间在等服务器，同时查快很多）。返回 {门店号: 结果}，和一家家查结果一样。
    on_store(k, 门店号)：进度（k = 已经查完几家；一开始先报 0）；它抛异常（例如取消）就停下。
    """
    stores = list(stores)
    if workers <= 1 or len(stores) <= 1:
        out = {}
        for k, sid in enumerate(stores):
            if on_store:
                on_store(k, sid)
            out[sid] = fn(sid)
        return out
    from concurrent.futures import ThreadPoolExecutor, as_completed
    if on_store:
        on_store(0, stores[0])
    ex = ThreadPoolExecutor(max_workers=min(workers, len(stores)), thread_name_prefix="db")
    try:
        futs = {ex.submit(fn, sid): sid for sid in stores}
        out, done = {}, 0
        for f in as_completed(futs):
            out[futs[f]] = f.result()
            done += 1
            if on_store and done < len(stores):
                on_store(done, futs[f])
        return {sid: out[sid] for sid in stores}
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def compute_suggestions(db, store_id, product_codes, days_list=None, reorder_cycle=30, supplier_ids=None,
                        use_supplier_cycles=True, round_to_db_pack=True, algo=None, groups=None, want_raw=False,
                        cycle_factor=1.0):
    """
    原程序 Suggerimenti Ordini 的 query_reorder()：查数据 → process_reorder_data。
    返回每个商品一行的 DataFrame（suggested_order_qty = 建议件数）。
    use_supplier_cycles: True = suppliers_config.json 里单独设了周期的供应商用它自己的周期（原程序做法），
                         False = 全部用界面上选的订货周期。
    round_to_db_pack:    True = 建议件数按数据库每箱件数（qtasmallbox）向上取整（原程序做法）；
                         False = 不取整，交给调用方按自己的箱规换算（门店配货按 SCHEDA 每箱件数）。
    algo: 算法参数（forecast.DEFAULT_PARAMS）；engine = "v2" 时用新算法（服务水平法）重算
          建议件数 / 判断 / 等级 / 库存天数，建议件数不取整（下游按各自箱规智能取整）。
    groups: 原程序算法时，把商品分成几组分别算（门店配货：每个 SCHEDA 一组）。原程序一次只算一个导入的 SCHEDA，
            A/B/C 等级是和「这一批商品的 30 天平均销量」比的，所以要按供应商分开算才和原程序一样。
            数据库只查一次，查回来再按组拆开，速度不变。
    want_raw: 原程序算法按数据库箱规取整时，另外给一列 raw_order_qty = 取整之前的件数（门店间调货要知道真正要几件）。
              用同一份查回来的数据再跑一遍原程序（不取整），不多查数据库；原程序的建议件数不受影响。
    cycle_factor: 门店周期系数（门店配货按门店设，algo["store_cycle"]）：这家店所有商品的订货周期 × 系数，
              配置文件里供应商自己的周期也一起乘（销量差的店 > 1 一次订够更久，销量好的店 < 1 少压货）。
    """
    import pandas as pd
    from fenpei.config import AppConfig
    from fenpei.models import ProductDataProcessor
    days_list = list(days_list or DEFAULT_SALES_DAYS)
    import forecast as F
    factor = float(cycle_factor or 1.0)
    rc_base = reorder_cycle
    if factor != 1.0:
        reorder_cycle = max(1, int(round(float(reorder_cycle) * factor)))
    algo = dict(F.DEFAULT_PARAMS, **(algo or {})) if algo is not None else None
    use_v2 = bool(algo) and algo.get("engine") == "v2"
    if use_v2:
        round_to_db_pack = False
    seen, codes = set(), []
    for c in product_codes:       # 去重（原程序 drop_duplicates）
        k = str(c).strip()
        if k and k not in seen:
            seen.add(k)
            codes.append(c)
    product_codes = codes
    pre = None
    if use_v2:      # 新算法要的周销量、全店销售额：和下面的查询同时查（结果一样，只是不用排队等）
        from concurrent.futures import ThreadPoolExecutor
        pre = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dbv2")
        f_week = pre.submit(_timed, "周销量", store_id, query_weekly_sales, db, store_id, product_codes)
        f_rev = pre.submit(_timed, "全店销售额", store_id, query_store_revenue, db, store_id)
    try:
        data = _timed("销量 / 库存", store_id, db.query_reorder_data, store_id, product_codes, days_list, supplier_ids)
    except Exception:
        if pre is not None:
            pre.shutdown(wait=False, cancel_futures=True)
        raise
    # 新算法：负库存（盘点误差等）事先当 0，不然库存天数是负数，建议量也会把负数补上。
    # 原程序算法：库存原样交给原程序 —— 原程序自己在算订货量时把负库存当 0
    #   （fenpei/models.py process_reorder_data：store_inv_for_calc = np.maximum(0, store_inv)），
    #   库存天数、转移提示也按原程序用原值，这里不再另外处理。
    inv = data.get("inventory")
    raw_inv = None
    if inv is not None and len(inv):
        inv = inv.copy()
        if "store_inventory" in inv.columns:
            raw_inv = inv[["CodArticolo", "store_inventory"]].rename(columns={"store_inventory": "store_inventory_raw"})
            raw_inv = raw_inv.drop_duplicates("CodArticolo")
        if use_v2:
            for col in ("store_inventory", "total_inventory"):
                if col in inv.columns:
                    inv[col] = pd.to_numeric(inv[col], errors="coerce").fillna(0).clip(lower=0)
        if not round_to_db_pack and "qtasmallbox" in inv.columns:
            inv["qtasmallbox_db"] = inv["qtasmallbox"]
            inv["qtasmallbox"] = 0          # 原算法只在 qtasmallbox > 1 时取整
        data["inventory"] = inv
    supplier_reorder_cycles = {}

    def run(part):
        """原程序 query_reorder() 里查完数据之后的那一段（avg_sales_30、供应商周期、process_reorder_data、两个合并）"""
        if not part["sales_1"].empty:
            avg_sales_30 = part["sales_1"]["sales_qty"].mean()
        else:
            avg_sales_30 = 1
        if use_supplier_cycles and "suppliers" in part and not part["suppliers"].empty:
            for sid in part["suppliers"]["IDFornitore"].unique():
                if pd.notna(sid):
                    sid = int(sid)
                    c0 = AppConfig.get_supplier_reorder_cycle(sid, fallback=rc_base)
                    supplier_reorder_cycles[sid] = max(1, int(round(float(c0) * factor))) if factor != 1.0 else c0
        cycles = {int(s): supplier_reorder_cycles[int(s)] for s in part["suppliers"]["IDFornitore"].unique()
                  if pd.notna(s)} if use_supplier_cycles and "suppliers" in part and not part["suppliers"].empty else {}
        df = ProductDataProcessor.process_reorder_data(
            part["inventory"], part["suppliers"],
            {"df_sales_1": part["sales_1"], "df_sales_2": part["sales_2"],
             "df_sales_3": part["sales_3"], "df_sales_4": part["sales_4"]},
            {"df_margin_1": part["margin_1"], "df_margin_2": part["margin_2"], "df_margin_3": part["margin_3"]},
            avg_sales_30, days_list, reorder_cycle=reorder_cycle,
            supplier_reorder_cycles=cycles or None)
        if "sales_after_last_in" in part and len(part["sales_after_last_in"]) > 0:
            df = df.merge(part["sales_after_last_in"], on="CodArticolo", how="left")
            df["SalesAfterLastIn"] = df["SalesAfterLastIn"].fillna(0).astype(int)
        if "last_sale_date" in part and len(part["last_sale_date"]) > 0:
            df = df.merge(part["last_sale_date"], on="CodArticolo", how="left")
        return df

    def process(dd):
        if groups and not use_v2:
            frames = []
            for g in groups:
                keep = {int(c) for c in g if str(c).strip().isdigit()}
                part = {k: (v[v["CodArticolo"].isin(keep)] if isinstance(v, pd.DataFrame) and "CodArticolo" in v.columns
                            else v) for k, v in dd.items()}
                if part.get("inventory") is None or part["inventory"].empty:
                    continue
                frames.append(run(part))
            return pd.concat(frames, ignore_index=True) if frames else run(dd)
        return run(dd)

    result_df = process(data)
    if want_raw and round_to_db_pack and not use_v2 and data.get("inventory") is not None \
            and "qtasmallbox" in data["inventory"].columns and len(result_df):
        d2 = dict(data)
        inv2 = data["inventory"].copy()
        inv2["qtasmallbox"] = 0                     # 原程序只在 qtasmallbox > 1 时取整
        d2["inventory"] = inv2
        try:
            raw = process(d2)[["CodArticolo", "suggested_order_qty"]].drop_duplicates("CodArticolo")
            result_df = result_df.merge(raw.rename(columns={"suggested_order_qty": "raw_order_qty"}),
                                        on="CodArticolo", how="left")
        except Exception:
            pass
    # 每个商品实际用的订货周期（界面显示用）
    if "IDFornitore" in result_df.columns:
        result_df["reorder_cycle_used"] = result_df["IDFornitore"].apply(
            lambda sid: supplier_reorder_cycles.get(int(sid), reorder_cycle) if pd.notna(sid) else reorder_cycle)
    if "qtasmallbox_db" in result_df.columns:
        result_df["qtasmallbox"] = result_df["qtasmallbox_db"]
    # 表里显示数据库原来的库存（负数照样显示），计算用的是当 0 处理过的
    if raw_inv is not None and "CodArticolo" in result_df.columns:
        result_df = result_df.merge(raw_inv, on="CodArticolo", how="left")
    if use_v2 and len(result_df):
        cycles = None
        if use_supplier_cycles and "IDFornitore" in result_df.columns:
            # 新算法也按配置文件：配置里单独设了周期的供应商用它自己的周期，其余用「算法设置」里的订货周期
            base = algo.get("review_days", 7)
            memo = {}

            def cyc(sid):
                if not pd.notna(sid):
                    return base
                sid = int(sid)
                if sid not in memo:
                    memo[sid] = AppConfig.get_supplier_reorder_cycle(sid, fallback=base)
                return memo[sid]
            cycles = [cyc(s) for s in result_df["IDFornitore"].tolist()]
        if factor != 1.0:                        # 门店周期系数：供应商自己的周期 / 默认周期都乘上
            base = float(algo.get("review_days", 7))
            cycles = [max(1.0, round(float(c if c is not None else base) * factor))
                      for c in (cycles or [base] * len(result_df))]
        def got(f):         # 提前查的没成功：交给 apply_v2 自己再查（和以前一样）
            try:
                return f.result()
            except Exception:
                return None
        result_df = apply_v2(db, store_id, result_df, algo, weekly=got(f_week), revenue=got(f_rev), cycles=cycles)
    if pre is not None:
        pre.shutdown(wait=False)
    return result_df


# =====================================================================
# 新算法（forecast.py）：周销量 + 服务水平
# =====================================================================
def query_weekly_sales(db, store_id, codes, weeks=None):
    """{商品编号(int): [最近 7 天销量, 再往前 7 天, …]}，共 weeks 周"""
    import pandas as pd
    import forecast as F
    weeks = weeks or F.WEEKS
    since = datetime.now().date() - timedelta(days=7 * weeks)
    codes = sorted({int(c) for c in codes if str(c).strip().isdigit()})
    out = {c: [0.0] * weeks for c in codes}
    for i in range(0, len(codes), 2000):
        chunk = codes[i:i + 2000]
        ph = ", ".join(["%s"] * len(chunk))
        sql = (f"SELECT CodArticolo, FLOOR(DATEDIFF(CURDATE(), Data) / 7) AS wk, SUM(Qta) AS qty "
               f"FROM vendite_list_{int(store_id)} WHERE CodArticolo IN ({ph}) AND Data >= %s "
               f"GROUP BY CodArticolo, wk")
        df = pd.read_sql(sql, db.engine, params=tuple(chunk) + (since,))
        for _, r in df.iterrows():
            try:
                c, wk = int(r["CodArticolo"]), int(r["wk"])
            except (TypeError, ValueError):
                continue
            if c in out and 0 <= wk < weeks:
                out[c][wk] += float(r["qty"] or 0)
    return out


def month_keys(n=24, today=None):
    """最近 n 个月（含本月）：[(年, 月), …]，从旧到新"""
    from datetime import date
    today = today or date.today()
    out = []
    for k in range(n - 1, -1, -1):
        y, m = today.year, today.month - k
        while m <= 0:
            m += 12
            y -= 1
        out.append((y, m))
    return out


def monthly_series(by_month, keys):
    """{(年, 月): 销量} → 按 keys 排好的销量列表（没卖的月份 = 0）"""
    return [float(by_month.get(k, 0) or 0) for k in keys]


def query_monthly_sales(db, store_id, codes, months=24, today=None):
    """本店这些商品最近 months 个月每个月的销量：{商品编号(str): {(年, 月): 件数}}（只读查询）"""
    import pandas as pd
    from datetime import date
    keys = month_keys(months, today)
    since = date(keys[0][0], keys[0][1], 1)
    codes = sorted({int(c) for c in codes if str(c).strip().isdigit()})
    out = {}
    for i in range(0, len(codes), 2000):
        chunk = codes[i:i + 2000]
        ph = ", ".join(["%s"] * len(chunk))
        sql = (f"SELECT CodArticolo, YEAR(Data) AS y, MONTH(Data) AS m, SUM(Qta) AS qty "
               f"FROM vendite_list_{int(store_id)} WHERE CodArticolo IN ({ph}) AND Data >= %s "
               f"GROUP BY CodArticolo, YEAR(Data), MONTH(Data)")
        df = pd.read_sql(sql, db.engine, params=tuple(chunk) + (since,))
        for _, r in df.iterrows():
            try:
                c, y, m = str(int(r["CodArticolo"])), int(r["y"]), int(r["m"])
            except (TypeError, ValueError):
                continue
            out.setdefault(c, {})[(y, m)] = float(r["qty"] or 0)
    return out


# ---------- 门店在做的促销（2026-11-01 用户：检查清单里促销的商品条码标红；促销在会员价里）----------
# 数据库里四种（用户以前诊断过，D:\CasaTeCopy\erp-agent\query）：
#   会员价      prodotti.PrezzoVedi3（> 0 且比标价 PrezzoVedi1 便宜；一直有效）
#   单品特价    prodotti.PrezzoVediOfferta + PrezzoVediOfferta_inizio / _fine（文字格式的日期）
#   会员限时价  prodotti_vip.PrezzoVedi + Prezzofine
#   促销单      promozioni（l, Data_a, Data_f, negozio = "0,1,2,3" 空 = 全部门店）+ promozioni_list（promozioni_l, CodArticolo,
#               Prezzo_p 促销价 / sconto 折扣 %, invalid）
# 只算有时间限制的（单品特价、会员限时价、促销单；用户 2026-11-01 定），会员价不算。
# 今天有效、比标价便宜的都算；同一个商品几种都有，取最便宜的。查不到的表跳过（不影响别的）
PROMO_KINDS = {"vip": "会员价", "offer": "单品特价", "vip_t": "会员限时价", "promo": "促销单"}
PROMO_DAYS = 7        # 详情窗口每天销量：实际卖价比标价便宜 PROMO_MIN 以上标红
PROMO_MIN = 0.05


def _num(v):
    try:
        f = float(str(v).replace(",", ".")) if v is not None else 0.0
        return f if f == f else 0.0
    except (TypeError, ValueError):
        return 0.0


def _day(v):
    """数据库里的日期（date / datetime / 'YYYY-MM-DD' 文字）→ date；空的、0000-00-00 → None"""
    from datetime import date as _date
    if v is None or (isinstance(v, float) and v != v):
        return None
    if hasattr(v, "date") and callable(v.date):
        try:
            return v.date()
        except (TypeError, ValueError):
            return None
    if isinstance(v, _date):
        return v
    s = str(v).strip()[:10]
    try:
        return _date.fromisoformat(s) if s and not s.startswith("0000") else None
    except ValueError:
        return None


def _active(start, end, today):
    a, b = _day(start), _day(end)
    return (a is None or a <= today) and (b is None or b >= today)


def _in_store(negozio, store_id):
    s = str(negozio if negozio is not None else "").strip()
    if not s or s.lower() in ("nan", "none"):
        return True
    return str(int(store_id)) in [x.strip() for x in s.replace(";", ",").split(",")]


def promo_pick(list_price, offers):
    """offers = [(种类, 价格, 结束日期)] → 最便宜、比标价便宜的那个 {"kind", "label", "list", "price", "pct", "end"}；没有 None"""
    lp = _num(list_price)
    best = None
    for kind, price, end in offers:
        p = _num(price)
        if lp <= 0 or not (0 < p < lp * 0.995):
            continue
        if best is None or p < best["price"]:
            best = {"kind": kind, "label": PROMO_KINDS.get(kind, kind), "list": lp, "price": p, "pct": 1 - p / lp,
                    "end": _day(end)}
    return best


def query_promos(db, store_id, codes, today=None):
    """本店这些商品里今天在促销的：{商品编号(str): promo_pick}（只读查询）"""
    import pandas as pd
    from datetime import date as _date
    today = today or _date.today()
    codes = sorted({int(c) for c in codes if str(c).strip().isdigit()})
    offers, lists = {}, {}

    def add(c, kind, price, end=None):
        offers.setdefault(str(int(c)), []).append((kind, price, end))
    for i in range(0, len(codes), 2000):
        chunk = codes[i:i + 2000]
        ph = ", ".join(["%s"] * len(chunk))
        df = pd.read_sql(f"SELECT CodArticolo, PrezzoVedi1, PrezzoVedi3, PrezzoVediOfferta, PrezzoVediOfferta_inizio, "
                         f"PrezzoVediOfferta_fine FROM prodotti WHERE CodArticolo IN ({ph})", db.engine, params=tuple(chunk))
        for r in df.to_dict("records"):
            c = r["CodArticolo"]
            lists[str(int(c))] = r["PrezzoVedi1"]
            # 会员价 PrezzoVedi3 没有时间限制：不算促销（用户 2026-11-01：只要有时间限制的），只在详情窗口显示
            if _active(r.get("PrezzoVediOfferta_inizio"), r.get("PrezzoVediOfferta_fine"), today):
                add(c, "offer", r.get("PrezzoVediOfferta"), r.get("PrezzoVediOfferta_fine"))
        try:
            df = pd.read_sql(f"SELECT CodArticolo, PrezzoVedi, Prezzofine FROM prodotti_vip WHERE CodArticolo IN ({ph})",
                             db.engine, params=tuple(chunk))
            for r in df.to_dict("records"):
                if _active(None, r.get("Prezzofine"), today):
                    add(r["CodArticolo"], "vip_t", r.get("PrezzoVedi"), r.get("Prezzofine"))
        except Exception:
            pass
        try:
            df = pd.read_sql(f"SELECT pl.CodArticolo, pl.Prezzo_p, pl.sconto, pl.invalid, pr.Data_a, pr.Data_f, pr.negozio "
                             f"FROM promozioni_list pl JOIN promozioni pr ON pr.l = pl.promozioni_l "
                             f"WHERE pl.CodArticolo IN ({ph})", db.engine, params=tuple(chunk))
            for r in df.to_dict("records"):
                if _num(r.get("invalid")) or not _active(r.get("Data_a"), r.get("Data_f"), today) or \
                        not _in_store(r.get("negozio"), store_id):
                    continue
                c = str(int(r["CodArticolo"]))
                p = _num(r.get("Prezzo_p"))
                if p <= 0 and _num(r.get("sconto")) > 0:
                    p = _num(lists.get(c)) * (1 - _num(r.get("sconto")) / 100)
                add(c, "promo", p, r.get("Data_f"))
        except Exception:
            pass
    out = {}
    for c, of in offers.items():
        p = promo_pick(lists.get(c), of)
        if p:
            out[c] = p
    return out


# ---------- 商品详情窗口（检查清单里双击商品）：一个商品的资料、各店库存、入库记录、每天销量（只读）----------
# 入库记录表 caricolistalist 的列：认识的换成中文表头排在前面，其余照原名放后面（右键表头可以藏）
RECEIPT_COLS = (("date", "日期", ("cdata", "data", "datacarico", "datadoc")),
                ("qty", "数量", ("quantita", "qta", "quantità")),
                ("type", "类型", ("tipo", "tipodoc", "tipocarico", "causale", "tipodocumento")),
                ("sup", "商家", ("idfornitore", "fornitore", "codfornitore", "ragionesociale")),
                ("doc", "发票", ("fattura", "numfattura", "nfattura", "numerofattura", "documento", "numdoc", "ndoc",
                                 "numerodoc", "bolla", "ddt", "numero")),
                ("price", "进价", ("prezzo", "prezzoforn", "costo", "prezzoacquisto", "prezzounitario")),
                ("store", "门店", ("negozio", "idnegozio", "magazzino", "idmagazzino")))
_RECEIPT_SKIP = ("codarticolo", "codbarre", "descrizione")


def receipt_columns(cols):
    """入库记录的列 → [(原列名, 表头, 记号)]：认识的在前面（按 RECEIPT_COLS 的顺序），其余照原名；商品编号 / 条码 / 名称不重复显示"""
    low = {}
    for c in cols:
        low.setdefault(str(c).lower().replace("_", ""), c)
    used, out = set(), []
    for tag, head, names in RECEIPT_COLS:
        for n in names:
            c = low.get(n)
            if c is not None and c not in used:
                out.append((c, head, tag))
                used.add(c)
                break
    for c in cols:
        if c in used or str(c).lower() in _RECEIPT_SKIP:
            continue
        out.append((c, str(c), None))
    return out


def _find(d, *pats, skip=()):
    """在商品表的一行里按列名找（不分大小写）：返回 (列名, 值)，找不到 (None, None)"""
    import re
    for pat in pats:
        rx = re.compile(pat, re.I)
        for k, v in d.items():
            if k not in skip and rx.search(str(k)):
                return k, v
    return None, None


def _blank(v):
    return v is None or (isinstance(v, float) and v != v) or str(v).strip() in ("", "nan", "NaT", "None")


def card_fields(prod):
    """商品表一行（dict）→ 详情窗口要显示的 [(标签, 文字)]（数据库里没有的就不显示）"""
    def num(v, fmt="{0:g}"):
        if _blank(v):
            return ""
        try:
            return fmt.format(float(v))
        except (TypeError, ValueError):
            return str(v)

    def day(v):
        if _blank(v):
            return ""
        return v.strftime("%d/%m/%Y") if hasattr(v, "strftime") else str(v)[:10]

    def txt(v):
        return "" if _blank(v) else str(v).strip()
    p = prod or {}
    out = [("编号", txt(p.get("CodArticolo"))), ("条码", txt(p.get("CodBarre"))),
           ("商家", " · ".join(x for x in (txt(p.get("IDFornitore")), txt(p.get("FornitoreNome"))) if x)),
           ("商家编码", txt(p.get("ArticoloForn")))]
    _k, brand = _find(p, r"^marca", r"brand", r"^marchio")
    out.append(("品牌", txt(brand)))
    out += [("Iva", num(p.get("CodIva"), "{0:g}%")), ("每箱件数", num(p.get("qtasmallbox")))]
    k2, box = _find(p, r"^qta.*(big|box|collo|cartone|imballo)", skip=("qtasmallbox",))
    if k2:
        out.append(("箱装数", num(box)))
    out += [("进价", num(p.get("PrezzoForn"), "€{0:.3f}")), ("售价", num(p.get("PrezzoVedi1"), "€{0:.2f}"))]
    if _num(p.get("PrezzoVedi3")) > 0:
        out.append(("会员价", num(p.get("PrezzoVedi3"), "€{0:.2f}")))
    if _num(p.get("PrezzoVediOfferta")) > 0:
        span = " – ".join(x for x in (day(_day(p.get("PrezzoVediOfferta_inizio"))), day(_day(p.get("PrezzoVediOfferta_fine")))) if x)
        out.append(("单品特价", "€{0:.2f}".format(_num(p.get("PrezzoVediOfferta"))) + (f"（{span}）" if span else "")))
    out.append(("类别", " / ".join(x for x in (txt(p.get("CategoriaNome")), txt(p.get("SottocategoriaNome"))) if x)))
    _k, cr = _find(p, r"crea", r"inserim", r"datains")
    _k, md = _find(p, r"modific", r"aggiorn", r"update", r"datamod")
    out.append(("创建 / 修改", " · ".join(x for x in (day(cr), day(md)) if x)))
    return [(a, b) for a, b in out if b]


def daily_sales_table(per_store, days=60, today=None):
    """
    每家门店每天的销量 {门店: {日期: (件数, 金额)}} → 每天一行 (日期, {门店: 件数}, {门店: 平均卖价})，新的在前；
    一家都没卖的日子不放
    """
    from datetime import date as _date
    end = today or _date.today()
    rows = []
    for k in range(int(days) + 1):
        d = end - timedelta(days=k)
        q, pr = {}, {}
        for sid, m in (per_store or {}).items():
            v = (m or {}).get(d)
            if v and v[0]:
                q[sid] = v[0]
                if v[0] > 0 and v[1]:
                    pr[sid] = v[1] / v[0]
        if q:
            rows.append((d, q, pr))
    return rows


def product_card(db, code, days=60, stores=None, today=None):
    """
    双击商品看的详情（只读查询，几条同时查）：
      prod     商品表这一行（+ 供应商名、大类 / 中类名）
      stock    {门店: 库存}（giacenza G0..G5）
      receipts (列 [(原列名, 表头, 记号)], 行 [dict])：caricolistalist 最近 300 条，新的在前；商家编号换成名字
      daily    {门店: {日期: (件数, 金额)}}：近 days 天
    查不到的部分是空的，errors 里写原因
    """
    import pandas as pd
    from datetime import date as _date
    from concurrent.futures import ThreadPoolExecutor
    code = int(code)
    stores = [s for s in (stores or STORES) if s != 0]
    since = (today or _date.today()) - timedelta(days=int(days))
    out = {"prod": {}, "stock": {}, "receipts": ([], []), "daily": {}, "errors": []}

    def q_prod():
        df = pd.read_sql("SELECT p.*, f.Nome AS FornitoreNome FROM prodotti p "
                         "LEFT JOIN fornitore f ON f.IDFornitore = p.IDFornitore WHERE p.CodArticolo = %s",
                         db.engine, params=(code,))
        prod = df.iloc[0].to_dict() if len(df) else {}
        if prod:
            try:
                cat = pd.read_sql("SELECT c1.Descrizione AS a, c2.Descrizione AS b FROM prodotti p "
                                  "LEFT JOIN categorie c1 ON p.Categoria = c1.Categorie AND c1.SottoCategorie IS NULL "
                                  "LEFT JOIN categorie c2 ON p.Sottocategoria = c2.SottoCategorie AND p.Categoria = c2.Categorie "
                                  "WHERE p.CodArticolo = %s", db.engine, params=(code,))
                if len(cat):
                    prod["CategoriaNome"], prod["SottocategoriaNome"] = cat.iloc[0]["a"], cat.iloc[0]["b"]
            except Exception:
                pass
        return prod

    def q_stock():
        df = pd.read_sql("SELECT * FROM giacenza WHERE CodArticolo = %s", db.engine, params=(code,))
        if not len(df):
            return {}
        r = df.iloc[0].to_dict()
        st = {}
        for s in STORES:
            try:
                st[s] = float(r.get(f"G{s}") or 0)
            except (TypeError, ValueError):
                pass
        return st

    def q_receipts():
        df = pd.read_sql("SELECT * FROM caricolistalist WHERE CodArticolo = %s ORDER BY Cdata DESC LIMIT 300",
                         db.engine, params=(code,))
        cols = receipt_columns(list(df.columns))
        rows = df.to_dict("records")
        sup = next((c for c, _h, tag in cols if tag == "sup"), None)
        if sup is not None and rows:
            ids = sorted({int(float(r[sup])) for r in rows if not _blank(r.get(sup)) and
                          str(r[sup]).replace(".0", "").strip().isdigit()})
            if ids:
                try:
                    names = pd.read_sql(f"SELECT IDFornitore, Nome FROM fornitore WHERE IDFornitore IN "
                                        f"({', '.join(['%s'] * len(ids))})", db.engine, params=tuple(ids))
                    nm = {int(a): str(b or "").strip() for a, b in zip(names["IDFornitore"], names["Nome"])}
                    for r in rows:
                        try:
                            n = nm.get(int(float(r[sup])))
                        except (TypeError, ValueError):
                            n = None
                        if n:
                            r[sup] = f"{n} ({int(float(r[sup]))})"
                except Exception:
                    pass
        return cols, rows

    def q_daily(sid):
        df = pd.read_sql(f"SELECT DATE(Data) AS d, SUM(Qta) AS q, SUM(TotRighe) AS t FROM vendite_list_{int(sid)} "
                         f"WHERE CodArticolo = %s AND Data >= %s GROUP BY DATE(Data)", db.engine, params=(code, since))
        m = {}
        for d, q, t in zip(df["d"], df["q"], df["t"]):
            try:
                dd = d.date() if hasattr(d, "date") else _date.fromisoformat(str(d)[:10])
                m[dd] = (float(q or 0), float(t or 0))
            except (TypeError, ValueError):
                continue
        return m
    jobs = {"prod": q_prod, "stock": q_stock, "receipts": q_receipts}
    jobs.update({("daily", s): (lambda s=s: q_daily(s)) for s in stores})
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="card") as ex:
        futs = {k: ex.submit(f) for k, f in jobs.items()}
        for k, f in futs.items():
            try:
                v = f.result()
            except Exception as e:
                out["errors"].append(f"{k if isinstance(k, str) else STORES.get(k[1], k[1])}: {e}")
                continue
            if isinstance(k, tuple):
                out["daily"][k[1]] = v
            else:
                out[k] = v
    return out


_REV_CACHE = {}         # (数据库, 门店, 天数, 日期) → (查的时间, 结果)：全店 91 天销售额，同一批检查 / 配货里各处都要用
REV_TTL = 600           # 10 分钟内再要直接用（ABC 按 91 天销售额排，几分钟里的新销售不会改变排名）


def query_store_revenue(db, store_id, days=91):
    """本店近 days 天每个商品的销售额（不含税），用于 ABC；查不了返回 None"""
    import time
    key = (str(getattr(getattr(db, "engine", None), "url", "")), int(store_id), int(days), datetime.now().date())
    hit = _REV_CACHE.get(key)
    if hit and time.time() - hit[0] < REV_TTL:
        return hit[1]
    res = _query_store_revenue(db, store_id, days)
    if res is not None:
        _REV_CACHE[key] = (time.time(), res)
    return res


def _query_store_revenue(db, store_id, days=91):
    import pandas as pd
    since = datetime.now().date() - timedelta(days=days)
    try:
        df = pd.read_sql(f"SELECT CodArticolo, SUM(TotRighe - TotCodIva) AS rev FROM vendite_list_{int(store_id)} "
                         f"WHERE Data >= %s GROUP BY CodArticolo", db.engine, params=(since,))
    except Exception:
        return None
    return {int(r["CodArticolo"]): float(r["rev"] or 0) for _, r in df.iterrows() if r["CodArticolo"] is not None}


def apply_v2(db, store_id, result_df, algo, weekly=None, revenue=None, on_order=None, cycles=None):
    """用新算法覆盖 suggested_order_qty / decision_text / priority / sales_tier / inventory_days 等列
    cycles：每行的订货周期（按配置文件周期时）；None = 全部用 algo 的 review_days"""
    import forecast as F
    result_df = result_df.copy()
    codes = [int(c) for c in result_df["CodArticolo"].tolist()]
    if weekly is None:
        weekly = query_weekly_sales(db, store_id, codes)
    if revenue is None:
        revenue = query_store_revenue(db, store_id)
    if not revenue:      # 查不到全店销售额：用这批商品近 13 周销量 × 售价
        price = dict(zip(codes, result_df.get("PrezzoVedi1", result_df["CodArticolo"] * 0).fillna(0).tolist()))
        revenue = {c: sum(weekly.get(c, [])[:13]) * float(price.get(c) or 0) for c in codes}
    tiers = F.abc_by_revenue(revenue)
    on_order = on_order or {}
    cols = {k: [] for k in ("suggested_order_qty", "decision_text", "priority", "sales_tier", "inventory_days",
                            "daily_rate", "safety_stock", "target_stock", "service_level", "season_factor",
                            "age_weeks", "weekly_26", "long_daily", "zero_weeks")}
    used = []
    for i, (_, r) in enumerate(result_df.iterrows()):
        c = int(r["CodArticolo"])
        inv = float(r.get("store_inventory", 0) or 0)           # 已经把负数当 0
        tier, _why = F.adjust_tier(tiers.get(c, "C"), weekly.get(c, []), algo)      # 金额定等级，动销兜底
        p = algo
        if cycles is not None and i < len(cycles) and cycles[i] is not None \
                and float(cycles[i]) != float(algo.get("review_days", 7)):
            p = dict(algo, review_days=float(cycles[i]))
        used.append(int(round(float(p.get("review_days", 7)))))
        a = F.analyze(weekly.get(c, [0.0] * F.WEEKS), inv, tier, p,
                      total_inv=float(r.get("total_inventory", 0) or 0), on_order=float(on_order.get(c, 0) or 0))
        cols["suggested_order_qty"].append(a["qty"])
        cols["decision_text"].append(a["decision"])
        cols["priority"].append(a["priority"])
        cols["sales_tier"].append(tier)
        cols["inventory_days"].append(a["cover_days"])
        cols["daily_rate"].append(a["daily"])
        cols["safety_stock"].append(a["safety"])
        cols["target_stock"].append(a["target"])
        cols["service_level"].append(a["service"])
        cols["season_factor"].append(a["season"])
        cols["age_weeks"].append(a["age_weeks"])
        cols["weekly_26"].append(list(reversed(weekly.get(c, [0.0] * F.WEEKS)[:26])))   # 旧 → 新，画图用
        ld, zw = F.long_rate(weekly.get(c, []))
        cols["long_daily"].append(ld)          # 过去一年（上架以来，最多 52 周）的平均日销：断货时用
        cols["zero_weeks"].append(zw)          # 最近连续几周一件没卖
    for k, v in cols.items():
        result_df[k] = v
    result_df["reorder_cycle_used"] = used if used else int(algo.get("review_days", 7))
    result_df["algo"] = "v2"
    return result_df


def _val(row, key, default=None):
    v = row.get(key, default) if hasattr(row, "get") else default
    try:
        if v != v:  # NaN
            return default
    except Exception:
        pass
    return v


def _date_text(v):
    """数据库日期 → 'YYYY-MM-DD'（存快照用文字）；没有返回 None"""
    if v is None:
        return None
    try:
        if v != v:                      # NaN / NaT
            return None
    except Exception:
        pass
    try:
        import pandas as pd
        return pd.Timestamp(v).strftime("%Y-%m-%d")
    except Exception:
        return None


def to_rows(result_df, file_rows=None, days_list=None):
    """
    把计算结果整理成界面 / 导出 / 主订单用的行；file_rows 是订货清单（用来带上原数量）。
    inv0 / daily0：算「库存够卖」「订后够卖」用的库存（负数当 0）和日均（新算法的加权日均；
    原程序算法没有日均，用最近一个周期的销量 ÷ 天数），和门店配货的够卖天数同一个算法。
    """
    days_list = list(days_list or DEFAULT_SALES_DAYS)
    by_code = {}
    for x in file_rows or []:
        if x.get("code"):
            by_code.setdefault(str(x["code"]), x)
    out = []
    for _, r in result_df.iterrows():
        code = str(int(r["CodArticolo"])) if _val(r, "CodArticolo") is not None else ""
        fx = by_code.get(code, {})
        pack = _val(r, "qtasmallbox", 0) or 0
        try:
            pack = float(pack)
        except (TypeError, ValueError):
            pack = 0
        sugg = int(_val(r, "suggested_order_qty", 0) or 0)
        ean = _clean_code(_val(r, "CodBarre", "")) or fx.get("ean", "")
        out.append({
            "code": code, "ean": ean,
            "name": str(_val(r, "Descrizione", "") or fx.get("name", "")).strip(),
            "supplier_id": _val(r, "IDFornitore"),
            "store_inv": _val(r, "store_inventory_raw", _val(r, "store_inventory", 0)),
            "total_inv": _val(r, "total_inventory", 0),
            "sales": [_val(r, f"sales_{i}", 0) or 0 for i in range(1, 5)],
            "inv_days": _val(r, "inventory_days"),
            "tier": _val(r, "sales_tier", ""),
            "decision": _val(r, "decision_text", ""),
            "priority": _val(r, "priority", 0),
            "pack": pack,
            "suggested": sugg,
            # 原程序的 Ord.Colli：建议件数 ÷ 数据库箱规，向上取整；没有箱规时为 0
            "ord_colli": math.ceil(sugg / pack) if pack > 0 and sugg > 0 else 0,
            "file_qty": fx.get("qty"),
            "file_stock": fx.get("stock"),
            "price": _val(r, "PrezzoForn"),
            "cycle": _val(r, "reorder_cycle_used"),
            "weekly": _val(r, "weekly_26"),
            "long_daily": _val(r, "long_daily"),
            "zero_weeks": _val(r, "zero_weeks"),
            "season": _val(r, "season_factor"),
            "service": _val(r, "service_level"),
            "safety": _val(r, "safety_stock"),
            "daily": _val(r, "daily_rate"),
            "target": _val(r, "target_stock"),
            "inv0": max(0.0, float(_val(r, "store_inventory", 0) or 0)),
            "daily0": daily_of(r, days_list),
            "last_sale": _date_text(_val(r, "LastSaleDate")),     # 最后一次卖出的日期（判断库存是不是虚的）
        })
    out.sort(key=lambda x: (-(x["priority"] or 0), x["code"]))
    return out


# =====================================================================
# 导出 / 生成采购主订单
# =====================================================================
REPORT_HEADERS = ["商品编号", "条码", "名称", "供应商ID", "本店库存", "总库存",
                  "销量1", "销量2", "销量3", "销量4", "库存天数", "等级", "判断", "优先级",
                  "箱规(数据库)", "建议件数", "建议箱数", "清单原数量", "清单库存"]


def export_report(rows, path, days_list=None, store_id=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    d = list(days_list or DEFAULT_SALES_DAYS)
    heads = list(REPORT_HEADERS)
    for i in range(4):
        heads[6 + i] = f"销量{d[i]}天"
    wb = Workbook()
    ws = wb.active
    ws.title = f"订货建议_{STORES.get(store_id, store_id)}" if store_id is not None else "订货建议"
    ws.append(heads)
    for c in ws[1]:
        c.font = Font(bold=True)
    for x in rows:
        inv_days = x["inv_days"]
        if isinstance(inv_days, float) and math.isinf(inv_days):
            inv_days = "∞"
        elif isinstance(inv_days, float):
            inv_days = round(inv_days, 1)
        ws.append([x["code"], x["ean"], x["name"], x["supplier_id"], x["store_inv"], x["total_inv"],
                   *x["sales"], inv_days, x["tier"], x["decision"], x["priority"],
                   x["pack"] or None, x["suggested"], x["ord_colli"] or None, x["file_qty"], x["file_stock"]])
        ws.cell(ws.max_row, 2).number_format = "@"
    ws.freeze_panes = "A2"
    for col in ws.columns:
        w = max((len(str(c.value)) for c in col if c.value is not None), default=6)
        ws.column_dimensions[col[0].column_letter].width = min(w + 2, 50)
    wb.save(path)
    return path


ORDER_HEADERS = ["CODICE EAN", "ARTICOLO", "COLLI", "PEZZI", "CodArticolo", "CASSA_DB", "NEGOZIO"]


def write_main_order(rows, root_folder, store_id, from_file=None, prefix="订货建议", pieces=True):
    """
    生成采购系统主订单：总文件夹/ORDINE/订货建议_门店_时间.xlsx
    只放建议件数 > 0 且有条码的商品；ORDINE 里原来的文件挪进 ORDINE/_旧订单/。
    PEZZI = 建议件数（采购系统按这个和各家每箱件数算箱数），COLLI = 按数据库箱规算的参考箱数。
    pieces=False：不写 PEZZI 列，比价按 COLLI（箱数）分配供应商（检查门店清单确认的主订单）。
    返回 (路径, 写了几行, 没条码跳过的商品列表)
    """
    from openpyxl import Workbook
    ordine = os.path.join(root_folder, "ORDINE")
    os.makedirs(ordine, exist_ok=True)
    old_dir = os.path.join(ordine, "_旧订单")
    for f in os.listdir(ordine):
        p = os.path.join(ordine, f)
        if os.path.isfile(p) and f.lower().endswith((".xlsx", ".xls")) and not f.startswith("~$"):
            os.makedirs(old_dir, exist_ok=True)
            dst = os.path.join(old_dir, f)
            if os.path.exists(dst):
                base, ext = os.path.splitext(f)
                dst = os.path.join(old_dir, f"{base}_{datetime.now():%Y%m%d_%H%M%S}{ext}")
            shutil.move(p, dst)
    name = f"{prefix}_{STORES.get(store_id, store_id)}_{datetime.now():%Y%m%d_%H%M}.xlsx"
    path = os.path.join(ordine, name)
    wb = Workbook()
    ws = wb.active
    ws.title = "ORDINE"
    heads = ORDER_HEADERS if pieces else [h for h in ORDER_HEADERS if h != "PEZZI"]
    ws.append(heads)
    n, skipped = 0, []
    for x in rows:
        if x["suggested"] <= 0:
            continue
        if not x["ean"]:
            skipped.append(x)
            continue
        colli = x["ord_colli"] or 1
        vals = {"CODICE EAN": x["ean"], "ARTICOLO": x["name"], "COLLI": colli, "PEZZI": x["suggested"],
                "CodArticolo": x["code"], "CASSA_DB": x["pack"] or None, "NEGOZIO": STORES.get(store_id, store_id)}
        ws.append([vals[h] for h in heads])
        ws.cell(ws.max_row, 1).number_format = "@"
        n += 1
    ws.freeze_panes = "A2"
    for col, w in zip("ABCDEFG", (16, 44, 8, 8, 12, 10, 12)):
        ws.column_dimensions[col].width = w
    wb.save(path)
    return path, n, skipped


# =====================================================================
# 订单处理前：检查门店订货清单的数量（清单数量 vs 系统建议量），人工确认后生成主订单
# =====================================================================
VERDICTS = ("增加", "减少", "不订", "不变", "数据库没有")


def qty_unit(field_header=None):
    """清单数量列的单位：2026-10-25 起门店清单一律按箱（不再按表头猜件 / 箱）"""
    return "colli"


def check_rows(file_rows, result_rows, unit="colli"):
    """
    把清单和计算结果按商品对上，返回界面用的行（保持清单顺序，同一商品多行合并）：
      file_qty 清单数量、file_pcs 折合件数（单位是箱时 × 数据库箱规）、suggested 建议件数、
      final 确定件数（默认 = 建议件数；数据库里没有的商品 = 清单件数）、verdict 判断
    """
    by_code = {x["code"]: x for x in result_rows if x.get("code")}
    by_ean = {x["ean"]: x for x in result_rows if x.get("ean")}
    merged, order = {}, []
    for f in file_rows:
        key = f.get("code") or f.get("ean")
        if not key:
            continue
        if key in merged:
            merged[key]["qty"] = (merged[key]["qty"] or 0) + (f.get("qty") or 0)
            continue
        merged[key] = dict(f)
        order.append(key)
    out = []
    for key in order:
        f = merged[key]
        r = by_code.get(f.get("code")) if f.get("code") else None
        r = r or (by_ean.get(f.get("ean")) if f.get("ean") else None)
        qty = float(f.get("qty") or 0)
        if r:
            pack = r["pack"] if r["pack"] and r["pack"] > 0 else 1
            file_pcs = qty * pack if unit == "colli" else qty
            sugg = r["suggested"]
            if sugg <= 0 and file_pcs > 0:
                verdict = "不订"
            elif sugg > file_pcs:
                verdict = "增加"
            elif sugg < file_pcs:
                verdict = "减少"
            else:
                verdict = "不变"
            row = dict(r)
            row.update(file_qty=qty, file_pcs=file_pcs, final=sugg, verdict=verdict, in_db=True)
            if not row.get("ean"):
                row["ean"] = f.get("ean", "")
        else:
            row = {"code": f.get("code", ""), "ean": f.get("ean", ""), "name": f.get("name", ""),
                   "store_inv": None, "sales": [None] * 4, "inv_days": None, "tier": "", "decision": "",
                   "pack": 0, "suggested": 0, "ord_colli": 0, "file_qty": qty, "file_pcs": qty,
                   "final": int(math.ceil(qty)), "verdict": "数据库没有", "in_db": False}
        out.append(row)
    return out


# =====================================================================
# 各门店库存快照（检查门店清单时一起查，批发商比价用来算门店间调货、够卖天数）
# =====================================================================
def daily_of(r, days_list):
    """一行计算结果的日均：新算法有加权日均；原程序算法用最近一个周期（没有就用第 3 个周期）的销量"""
    v = _val(r, "daily_rate")
    if v is not None:
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
    s1 = float(_val(r, "sales_1", 0) or 0)
    s3 = float(_val(r, "sales_3", 0) or 0)
    if s1 > 0:
        return s1 / days_list[0]
    if s3 > 0:
        return s3 / days_list[2]
    return 0.0


def stock_of(df, days_list):
    """一家门店的计算结果 → {商品编号: {"inv", "daily", "cycle", "ean"}}（负库存当 0）"""
    out = {}
    if df is None or not len(df):
        return out
    for _, r in df.iterrows():
        if _val(r, "CodArticolo") is None:
            continue
        code = str(int(r["CodArticolo"]))
        out[code] = {"inv": max(0.0, float(_val(r, "store_inventory", 0) or 0)),
                     "daily": daily_of(r, days_list),
                     "cycle": float(_val(r, "reorder_cycle_used", 30) or 30),
                     "ean": _clean_code(_val(r, "CodBarre", "")) or ""}
    return out


def stock_snapshot(db, codes, days_list, reorder_cycle, stores, use_supplier_cycles=True, algo=None,
                   own=None, own_df=None, on_store=None):
    """
    几家门店同一批商品的库存和日均：{门店号: {商品编号: {inv, daily, cycle, ean}}}。
    own / own_df：本店已经算过的结果，直接用，不再查。
    """
    def one(sid):
        if sid == own and own_df is not None:
            df = own_df
        else:
            df = compute_suggestions(db, sid, codes, days_list, reorder_cycle, None,
                                     use_supplier_cycles=use_supplier_cycles, algo=algo)
        return stock_of(df, days_list)
    return per_store(stores, one, on_store)


def snapshot_path(main_order_path):
    """主订单旁边的库存快照文件：总文件夹/_备份/门店库存/<主订单文件名>.json"""
    root = os.path.dirname(os.path.dirname(main_order_path))
    return os.path.join(root, "_备份", "门店库存", os.path.basename(main_order_path) + ".json")


def save_snapshot(main_order_path, store_id, snap, rows=None, transfers=None, transfers_file=None):
    """
    存快照：按条码（主订单按条码找商品）→ {"code", "stores": {门店号: {inv, daily, cycle}}}
    rows：检查清单的行（补上快照里没有条码的商品的条码）
    transfers：检查清单时（几家门店一起）已经定好的门店间调货（这家店调进 / 调出的）；
               存了就说明主订单的件数已经减掉了调货，比价时不再重算调货（None = 旧做法，比价时再算）
    """
    import json
    ean_of, sup_of = {}, {}
    for x in rows or []:
        if x.get("code") and x.get("ean"):
            ean_of[str(x["code"])] = _clean_code(x["ean"])
        if x.get("code") and (x.get("sup_code") or x.get("sup_name")):
            sup_of[str(x["code"])] = (x.get("sup_code") or "", x.get("sup_name") or "")
    by_ean = {}
    for sid, d in snap.items():
        for code, v in d.items():
            ean = ean_of.get(code) or v.get("ean")
            if not ean:
                continue
            e = by_ean.setdefault(ean, {"code": code, "stores": {}})
            if code in sup_of:
                e["sup_code"], e["sup_name"] = sup_of[code]
            e["stores"][str(sid)] = {"inv": v["inv"], "daily": v["daily"], "cycle": v["cycle"]}
    path = snapshot_path(main_order_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        data = {"store": store_id, "made": datetime.now().isoformat(timespec="seconds"),
                "main_order": os.path.basename(main_order_path), "by_ean": by_ean}
        if transfers is not None:
            data["planned_transfers"] = transfers
            data["transfers_file"] = transfers_file
        json.dump(data, f, ensure_ascii=False)
    return path


def load_snapshot(main_order_path):
    """读快照；没有 / 读不了 / 不是这份主订单的 → None。门店号转回 int"""
    import json
    try:
        with open(snapshot_path(main_order_path), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if data.get("main_order") != os.path.basename(main_order_path):
        return None
    for e in data.get("by_ean", {}).values():
        e["stores"] = {int(k): v for k, v in e.get("stores", {}).items()}
    return data


def stock_for_eans(db, store_id, eans, days_list=None, reorder_cycle=30, algo=None):
    """
    一批条码在这家门店的库存和日均（批发商比价「加商品凑单」列报价里没订的商品用）：
    {条码: {"code", "name", "inv"（负数当 0）, "daily", "cycle"}}；数据库里没有的条码不返回
    """
    days_list = list(days_list or DEFAULT_SALES_DAYS)
    by_ean = codes_from_eans(db, eans)
    if not by_ean:
        return {}
    codes = sorted(set(by_ean.values()))
    df = compute_suggestions(db, store_id, codes, days_list, reorder_cycle, None, algo=algo)
    st = stock_of(df, days_list)
    names = {}
    if df is not None and len(df):
        for _, r in df.iterrows():
            if _val(r, "CodArticolo") is not None:
                names[str(int(r["CodArticolo"]))] = str(_val(r, "Descrizione", "") or "").strip()
    out = {}
    for e, code in by_ean.items():
        d = st.get(code)
        if d:
            out[e] = {"code": code, "name": names.get(code, ""), "inv": d["inv"], "daily": d["daily"], "cycle": d["cycle"]}
    return out


def plan_batch_transfers(store_rows, snap, cfg=None, far=(), overrides=None, auto=True):
    """
    几家门店的清单一起检查以后，先算门店间调货、再订剩下的（和门店配货同一个规则，见 transfers.py）：
      store_rows: {门店号: 检查清单的行}（用 final 确定件数、pack 数据库箱规、price 进价、code、ean、name）
      snap:       {门店号: {商品编号: {inv, daily, cycle}}}：五家店这批商品的库存和日均
    调出门店留够自己一个订货周期；这次自己也要订这个商品的门店不调出；离得远的门店（far）不调出也不调进。
    overrides {(门店, 商品编号): 人工定的调货}（件数，或 {"from", "pieces", "auto"}）：0 = 不调；>0 = 就调这么多
    auto=False：审核时人工改过调货 → 只按 overrides 调，别的行不跟着变
    返回 (moves, new_final)：moves 是调货单的行；new_final {(门店, 商品编号): 调货以后还要订的件数}
    """
    import transfers as TR
    import store_orders as SO
    cfg = TR.config(cfg)
    if not cfg.get("enabled"):
        return [], {}
    ordering = {(sid, str(x["code"])) for sid, rows in store_rows.items() for x in rows
                if x.get("code") and int(x.get("final") or 0) > 0}
    needs, info = [], {}
    for sid, rows in store_rows.items():
        for x in rows:
            final = int(x.get("final") or 0)
            pack = float(x.get("pack") or 0)
            if not x.get("in_db", True) or not x.get("code") or final <= 0 or pack <= 0:
                continue
            k = len(needs)
            needs.append({"id": k, "store": sid, "key": str(x["code"]), "need": final, "pack": pack,
                          "colli": int(math.ceil(final / pack)), "price": float(x.get("price") or 0)})
            info[k] = (sid, x)
    avail, stock = {}, {}
    for sid, d in (snap or {}).items():
        for code, v in d.items():
            stock[(sid, str(code))] = max(0.0, float((v or {}).get("inv") or 0))
            if (sid, str(code)) in ordering:
                continue
            sp = TR.surplus(v, cfg.get("keep_cycles"))
            if sp > 0:
                avail[(sid, str(code))] = sp
    ov = {}
    for k, (sid, x) in info.items():
        key = (sid, str(x["code"]))
        if overrides and key in overrides:
            ov[k] = overrides[key]
    moves, _ = TR.plan(needs, avail, cfg, far, ov, stock=stock, auto=auto)
    new_final, out = {}, []
    for m in moves:
        sid, x = info[m["need_id"]]
        code = str(x["code"])
        left = max(0, int(x["final"]) - int(m["pieces"]))
        new_final[(sid, code)] = left
        d_from = (snap.get(m["from"]) or {}).get(code)
        d_to = (snap.get(sid) or {}).get(code)
        out.append({"from": m["from"], "to": sid, "code": code, "ean": x.get("ean") or "", "desc": x.get("name") or "",
                    "supplier": "", "pieces": int(m["pieces"]), "value": float(m["value"]),
                    "price": float(x.get("price") or 0), "colli_before": m["colli_before"], "colli_after": m["colli_after"],
                    "need": int(x["final"]), "pack": m["pack"], "manual": bool(m.get("manual")),
                    "from_days": SO.cover_days(d_from, -m["pieces"]) if d_from else None,
                    "to_days": SO.cover_days(d_to, left + m["pieces"]) if d_to else None})
    out.sort(key=lambda t: (t["from"], t["to"], t["desc"]))
    return out, new_final


def reason_text(inv, daily, cycle, sugg, target=None, in_db=True):
    """
    一句话说清楚为什么建议订 / 不订、订多少（检查清单、订货明细的「原因」列）：
      库存够卖几天 和 订货周期比；不够 → 周期里大约卖多少 → 建议订几件
    """
    from i18n import tr
    if not in_db:
        return tr("数据库里没有这个商品，按清单数量")
    try:
        inv = float(inv or 0)
        daily = float(daily or 0)
        cycle = float(cycle or 0)
        sugg = float(sugg or 0)
    except (TypeError, ValueError):
        return ""
    inv0 = max(0.0, inv)
    neg = ""                                     # 负库存一律当 0，不另外说明
    if daily <= 0:
        if sugg > 0:
            return tr("没有销量{0}，原程序仍建议订 {1:g} 件", neg, sugg)
        return tr("没有销量，库存 {0:g} 件{1}，不订", inv0, neg) if inv0 else tr("没有销量也没有库存{0}，不订", neg)
    days = inv0 / daily
    if sugg <= 0:
        if cycle > 0:
            return tr("库存 {0:g} 件够卖 {1:.0f} 天（周期 {2:g} 天）{3}，不用订", inv0, days, cycle, neg)
        return tr("库存 {0:g} 件够卖 {1:.0f} 天{2}，不用订", inv0, days, neg)
    if isinstance(target, (int, float)) and target == target and target > 0:
        return tr("库存够卖 {0:.0f} 天{1}；目标库存 {2:.0f} − 库存 {3:g} → 订 {4:g} 件", days, neg, target, inv0, sugg)
    if cycle > 0:
        return tr("库存够卖 {0:.0f} 天{1}，周期 {2:g} 天约卖 {3:.0f} 件 → 订 {4:g} 件", days, neg, cycle, daily * cycle, sugg)
    return tr("库存够卖 {0:.0f} 天{1} → 订 {2:g} 件", days, neg, sugg)

def plain_reason(inv, daily, cycle, sugg, boxes=None, target=None, in_db=True, transfer=None, pack=None):
    """
    给采购员看的「为什么」：用直白的话，一行一句（订货明细、检查门店订货清单的「原因」列和右边详情栏共用）。
      inv 库存（可以是负数）、daily 每天卖几件、cycle 订货周期（天）、sugg 系统建议件数、boxes 折成几箱（None = 只说件数）
      transfer：从别的门店调进来 {"name", "inv", "days", "pieces", "saved"(少订几箱，可以没有)}
    """
    from i18n import tr
    if not in_db:
        return [tr("数据库里找不到这个商品，没有销量和库存可以参考")]
    try:
        inv, daily, cycle, sugg = float(inv or 0), float(daily or 0), float(cycle or 0), float(sugg or 0)
    except (TypeError, ValueError):
        return []
    inv0 = max(0.0, inv)
    lines = []
    if daily <= 0:
        lines.append(tr("最近没有卖出去过，还有 {0:g} 件库存", inv0) if inv0 else tr("最近没有卖出去过，也没有库存"))
    else:
        days = inv0 / daily
        if days < 7:
            lines.append(tr("现在库存 {0:g} 件，每天卖 {1:.1f} 件左右，只够卖 {2:.0f} 天，快断货了", inv0, daily, days))
        else:
            lines.append(tr("现在库存 {0:g} 件，每天卖 {1:.1f} 件左右，够卖 {2:.0f} 天", inv0, daily, days))
    if boxes and pack and pack > 1:
        q = tr("{0:g} 箱（要 {1:g} 件，一箱 {2:g} 件）", boxes, sugg, pack)
    elif boxes:
        q = tr("{0:g} 箱（{1:g} 件）", boxes, sugg)
    else:
        q = tr("{0:g} 件", sugg)
    if sugg <= 0:
        lines.append(tr("够卖到下次订货，不用订") if daily > 0 else tr("不用订"))
    elif daily <= 0:
        lines.append(tr("虽然没有销量，按原来的规则还是建议订 {0}", q))
    elif isinstance(target, (int, float)) and target == target and target > 0:
        lines.append(tr("要备 {0:.0f} 件才够卖到下一批货到，现在不够 → 建议订 {1}", target, q))
    elif cycle > 0:
        lines.append(tr("接下来 {0:g} 天大约要卖 {1:.0f} 件，库存不够 → 建议订 {2}", cycle, daily * cycle, q))
    else:
        lines.append(tr("库存不够 → 建议订 {0}", q))
    if transfer:
        d = transfer.get("days")
        if d is None or d == float("inf"):
            have = tr("{0} 有 {1:g} 件卖不动", transfer["name"], transfer.get("inv") or 0)
        else:
            have = tr("{0} 库存多：有 {1:g} 件，够卖 {2:.0f} 天", transfer["name"], transfer.get("inv") or 0, d)
        line = have + tr("，卖不完 → 调 {0:g} 件过来", transfer["pieces"])
        if transfer.get("saved"):
            line += tr("，这边少订 {0:g} 箱", transfer["saved"])
        lines.append(line)
    return lines


SLOW_DAYS = 180          # 新算法：一箱到货后要卖超过这么多天 → 标「慢销」（照样订，只提醒）


def review_marks(new_algo, ordered, after_days, inv_raw, slow_days=SLOW_DAYS):
    """
    审核表格「判断」后面的提醒（只提醒，不改订多少）：[(种类, 短字, 鼠标提示)]
      slow 慢销：新算法订了、到货后要卖超过 slow_days 天
      neg  库存不准：系统里库存是负数（多半是货到了没入库），按 0 算
    """
    from i18n import tr
    out = []
    if new_algo and ordered and after_days is not None and after_days != float("inf") and after_days > slow_days:
        out.append(("slow", tr("慢销"), tr("到货后要卖 {0:.0f} 天才卖完，卖得很慢：要不要订你决定（订货数量改成 0 就不订）", after_days)))
    # 库存是负数：先一律当 0（不提醒；库存准不准以后再处理），inv_raw 参数留着不用
    return out


VERDICT_RANK = {"transfer": 0, "more": 1, "less": 2, "none": 3, "same": 4, "nodb": 5}


def review_verdict(before, after, moved=False, in_db=True, pack=None, same_text=None):
    """
    审核表格的「判断」：一眼看出这一行怎么处理（订货明细、检查门店订货清单共用）。
      before 改之前的数量（系统原建议 / 门店清单），after 现在要订的；
      pack=None 时两个数都是箱数，给了 pack 就是件数（差额正好是整箱写「箱」，否则写「件」）。
      moved 有门店间调货（调进或调出）。
    返回 (类别, 文字)：transfer 建议调货 / more 多订 N 箱 / less 少订 N 箱 / none 不订 / same 照建议订 / nodb 数据库没有
    """
    from i18n import tr
    if not in_db:
        return "nodb", tr("数据库没有")
    if moved:
        return "transfer", tr("建议调货")
    before, after = float(before or 0), float(after or 0)
    if after <= 0:
        return "none", tr("不订")
    d = after - before
    if not d:
        return "same", same_text or tr("照建议订")
    n = abs(d)
    if pack and pack > 0:
        q = tr("{0:g} 箱", n / pack) if abs(n / pack - round(n / pack)) < 1e-9 else tr("{0:g} 件", n)
    else:
        q = tr("{0:g} 箱", n)
    return ("more", tr("多订 {0}", q)) if d > 0 else ("less", tr("少订 {0}", q))



def checked_main_orders(ordine_folder):
    """
    ORDINE 文件夹里的主订单：(能用的, 旧系统的)。
    主订单只能由「检查门店订货清单」生成（_备份/门店库存 里有它的库存快照）；没有快照的是旧系统的文件，不能再用来比价。
    能用的按时间从新到旧排。
    """
    if not os.path.isdir(ordine_folder):
        return [], []
    names = sorted(f for f in os.listdir(ordine_folder)
                   if os.path.isfile(os.path.join(ordine_folder, f)) and f.lower().endswith((".xlsx", ".xls", ".xlsm"))
                   and not f.startswith("~$"))
    good = [n for n in names if load_snapshot(os.path.join(ordine_folder, n))]
    good.sort(key=lambda n: os.path.getmtime(os.path.join(ordine_folder, n)), reverse=True)
    return good, [n for n in names if n not in good]


def write_checked_order(rows, root_folder, store_id, source_name=""):
    """
    确认后的订货量写成采购主订单（ORDINE 文件夹）：COLLI = 确定箱数（final = 箱数 × 每箱件数；没有箱规的 final 就是箱数）。
    不写 PEZZI：比价按箱数分配供应商（2026-10-21 用户：先按箱数，件数分配以后有问题再说）。
    确定为 0 的不写；没有条码的跳过。原来的文件挪进 ORDINE/_旧订单。返回 (路径, 行数, 跳过列表)
    """
    conv = []
    for x in rows:
        final = int(x.get("final") or 0)
        pack = x.get("pack") or 0
        y = dict(x)
        y["suggested"] = final
        y["ord_colli"] = math.ceil(final / pack - 1e-9) if pack > 0 and final > 0 else final
        if x.get("fbox") is not None:                # 检查窗口里定的箱数（调货以后的）
            y["ord_colli"] = int(x["fbox"])
            y["suggested"] = y["ord_colli"]
        conv.append(y)
    return write_main_order(conv, root_folder, store_id, prefix="确认订单", pieces=False)
