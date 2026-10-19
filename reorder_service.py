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
    """条码 → 商品编号：先查 prodotti.CodBarre，再查 codbarre 表（一个商品多个条码）"""
    import pandas as pd
    eans = sorted({e for e in eans if e})
    out = {}
    for i in range(0, len(eans), 2000):
        chunk = eans[i:i + 2000]
        ph = ", ".join(["%s"] * len(chunk))
        for q in (f"SELECT CodArticolo, CodBarre FROM prodotti WHERE CodBarre IN ({ph})",
                  f"SELECT CodArticolo, CodBarre FROM codbarre WHERE CodBarre IN ({ph})"):
            try:
                df = pd.read_sql(q, db.engine, params=tuple(chunk))
            except Exception:
                continue
            for _, x in df.iterrows():
                e = _clean_code(x["CodBarre"])
                if e and e not in out and x["CodArticolo"]:
                    out[e] = str(int(x["CodArticolo"]))
    return out


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


def compute_suggestions(db, store_id, product_codes, days_list=None, reorder_cycle=30, supplier_ids=None,
                        use_supplier_cycles=True, round_to_db_pack=True, algo=None, groups=None, want_raw=False):
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
    """
    import pandas as pd
    from fenpei.config import AppConfig
    from fenpei.models import ProductDataProcessor
    days_list = list(days_list or DEFAULT_SALES_DAYS)
    import forecast as F
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
    data = db.query_reorder_data(store_id, product_codes, days_list, supplier_ids)
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
                    supplier_reorder_cycles[sid] = AppConfig.get_supplier_reorder_cycle(sid, fallback=reorder_cycle)
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
        result_df = apply_v2(db, store_id, result_df, algo)
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


def query_store_revenue(db, store_id, days=91):
    """本店近 days 天每个商品的销售额（不含税），用于 ABC；查不了返回 None"""
    import pandas as pd
    since = datetime.now().date() - timedelta(days=days)
    try:
        df = pd.read_sql(f"SELECT CodArticolo, SUM(TotRighe - TotCodIva) AS rev FROM vendite_list_{int(store_id)} "
                         f"WHERE Data >= %s GROUP BY CodArticolo", db.engine, params=(since,))
    except Exception:
        return None
    return {int(r["CodArticolo"]): float(r["rev"] or 0) for _, r in df.iterrows() if r["CodArticolo"] is not None}


def apply_v2(db, store_id, result_df, algo, weekly=None, revenue=None, on_order=None):
    """用新算法覆盖 suggested_order_qty / decision_text / priority / sales_tier / inventory_days 等列"""
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
                            "age_weeks", "weekly_26")}
    for _, r in result_df.iterrows():
        c = int(r["CodArticolo"])
        inv = float(r.get("store_inventory", 0) or 0)           # 已经把负数当 0
        tier = tiers.get(c, "C")
        a = F.analyze(weekly.get(c, [0.0] * F.WEEKS), inv, tier, algo,
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
    for k, v in cols.items():
        result_df[k] = v
    result_df["reorder_cycle_used"] = int(algo.get("review_days", 7))
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


def to_rows(result_df, file_rows=None):
    """把计算结果整理成界面 / 导出 / 主订单用的行；file_rows 是订货清单（用来带上原数量）"""
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
            "safety": _val(r, "safety_stock"),
            "daily": _val(r, "daily_rate"),
            "target": _val(r, "target_stock"),
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


def write_main_order(rows, root_folder, store_id, from_file=None, prefix="订货建议"):
    """
    生成采购系统主订单：总文件夹/ORDINE/订货建议_门店_时间.xlsx
    只放建议件数 > 0 且有条码的商品；ORDINE 里原来的文件挪进 ORDINE/_旧订单/。
    PEZZI = 建议件数（采购系统按这个和各家每箱件数算箱数），COLLI = 按数据库箱规算的参考箱数。
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
    ws.append(ORDER_HEADERS)
    n, skipped = 0, []
    for x in rows:
        if x["suggested"] <= 0:
            continue
        if not x["ean"]:
            skipped.append(x)
            continue
        colli = x["ord_colli"] or 1
        ws.append([x["ean"], x["name"], colli, x["suggested"], x["code"], x["pack"] or None, STORES.get(store_id, store_id)])
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


def qty_unit(field_header):
    """清单数量列的单位：表头里有 COLLI / 箱 → 箱，否则当件"""
    h = _norm(field_header)
    return "colli" if ("colli" in h or "箱" in h) else "pcs"


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
    snap = {}
    for k, sid in enumerate(stores):
        if on_store:
            on_store(k, sid)
        if sid == own and own_df is not None:
            df = own_df
        else:
            df = compute_suggestions(db, sid, codes, days_list, reorder_cycle, None,
                                     use_supplier_cycles=use_supplier_cycles, algo=algo)
        snap[sid] = stock_of(df, days_list)
    return snap


def snapshot_path(main_order_path):
    """主订单旁边的库存快照文件：总文件夹/_备份/门店库存/<主订单文件名>.json"""
    root = os.path.dirname(os.path.dirname(main_order_path))
    return os.path.join(root, "_备份", "门店库存", os.path.basename(main_order_path) + ".json")


def save_snapshot(main_order_path, store_id, snap, rows=None):
    """
    存快照：按条码（主订单按条码找商品）→ {"code", "stores": {门店号: {inv, daily, cycle}}}
    rows：检查清单的行（补上快照里没有条码的商品的条码）
    """
    import json
    ean_of = {}
    for x in rows or []:
        if x.get("code") and x.get("ean"):
            ean_of[str(x["code"])] = _clean_code(x["ean"])
    by_ean = {}
    for sid, d in snap.items():
        for code, v in d.items():
            ean = ean_of.get(code) or v.get("ean")
            if not ean:
                continue
            e = by_ean.setdefault(ean, {"code": code, "stores": {}})
            e["stores"][str(sid)] = {"inv": v["inv"], "daily": v["daily"], "cycle": v["cycle"]}
    path = snapshot_path(main_order_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"store": store_id, "made": datetime.now().isoformat(timespec="seconds"),
                   "main_order": os.path.basename(main_order_path), "by_ean": by_ean}, f, ensure_ascii=False)
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


def write_checked_order(rows, root_folder, store_id, source_name=""):
    """
    确认后的订货量写成采购主订单（ORDINE 文件夹）：PEZZI = 确定件数，COLLI = 按数据库箱规折合的箱数。
    确定件数为 0 的不写；没有条码的跳过。原来的文件挪进 ORDINE/_旧订单。返回 (路径, 行数, 跳过列表)
    """
    conv = []
    for x in rows:
        final = int(x.get("final") or 0)
        pack = x.get("pack") or 0
        y = dict(x)
        y["suggested"] = final
        y["ord_colli"] = math.ceil(final / pack) if pack > 0 and final > 0 else final
        conv.append(y)
    return write_main_order(conv, root_folder, store_id, prefix="确认订单")
