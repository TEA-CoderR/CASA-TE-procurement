"""
分货管理：从旧程序 SistemaIntegrato 移植过来的两个模块的逻辑部分（界面另见 distribution_page.py / import_page.py）

① 分货数据处理（原 ui_distribuzione.py）
   - 补齐 EAN：按 codbarre 表，在商品编号列后面加 Codice EAN / 2 / 3（排除 66、55 开头的内部码）
   - 双表格提取：首页按 商品编号↔Cod.Art.（找不到再用 商品条码↔Codice EAN）匹配 Sheet1，
     取 PZ*COLLI 和 5 家门店数量（固定 Excel 列 K/N/Q/T/W）附加到首页，最后一列 差值 = 数量 − 五店合计
   - 一体化：上面两个一起做，Sheet1 里没匹配上的行放到导出文件第二张表

② 导入 / 导出（原 ui_importa.py）
   - 导入商品清单 → 查最近 N 天各门店销量（vendite_list_2026）+ 当前库存（giacenza G1~G5）
   - 调货判断：要补货的门店 + 供货门店优先顺序；供货店库存 > 补货店销量 + 供货店销量 → 从这家调
   - 导出报告：条码、供应商 + 各店销量 / 库存 + 调货结果

查询语句和判断规则和旧程序一致。
"""
import os
import re
from datetime import datetime, timedelta

import pandas as pd

STORE_NAMES = {1: "FOIANO", 2: "MARLIA", 3: "S.FILIPPO", 4: "S.MARCO", 5: "S.CONCORDIO"}

# ---------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------
EXCLUDE_PREFIXES = ("66", "55")                       # 内部码
PRODUCT_COLUMN_NAMES = ["Cod.Art.", "Cod.Prod.", "Codice prodotto", "CodArticolo", "商品编号"]
HOME_PRODUCT_COLUMN_NAMES = ["商品编号", "Codice prodotto", "Cod.Art.", "CodArticolo"]
HOME_EAN_COLUMN_NAMES = ["商品条码", "Codice Ean", "Codice EAN", "CodBarre"]
SHEET1_ART_COLUMN_NAMES = ["Cod.Art.", "CodArticolo", "商品编号", "Codice prodotto"]
SHEET1_EAN_COLUMN_NAMES = ["Codice EAN", "Codice Ean", "商品条码", "CodBarre"]
EXTRACTION_TARGETS = ["PZ*COLLI", 1, 2, 3, 4, 5]
STORE_SOURCE_COLUMN_INDEX = {1: 10, 2: 13, 3: 16, 4: 19, 5: 22}     # K N Q T W
TEXT_BARCODE_COLUMNS = ("商品条码", "Codice EAN", "CodBarre", "Codice EAN 2", "Codice EAN 3", "Codice a Barre")
QUANTITY_COLUMN_NAMES = ["数量", "Quantità", "Qtà", "Qty", "Quantita"]


def normalize_ean(raw):
    """条码转成纯数字字符串：去空格、科学计数法、小数尾巴"""
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none"):
        return ""
    s = s.upper()
    if re.fullmatch(r"\d+(\.\d+)?E[+-]?\d+", s):
        try:
            s = str(int(float(s)))
        except (ValueError, TypeError):
            pass
    elif re.fullmatch(r"\d+\.\d+", s):
        s = s.split(".")[0]
    return s


def to_int(val):
    """单元格 → 整数编号；无效返回 None"""
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    s = str(val).strip()
    if not s:
        return None
    if re.fullmatch(r"\d+(\.\d+)?[Ee][+-]?\d+", s):
        try:
            return int(float(s))
        except (ValueError, TypeError):
            return None
    if re.fullmatch(r"\d+\.\d+", s):
        s = s.split(".")[0]
    if re.fullmatch(r"\d+", s):
        return int(s)
    return None


def find_column(df, candidates):
    for name in candidates:
        if name in df.columns:
            return name
    norm = {str(c).strip().lower().replace("\n", " "): c for c in df.columns}
    for name in candidates:
        key = str(name).strip().lower()
        if key in norm:
            return norm[key]
    return None


def detect_numeric_code_column(df):
    """表头找不到时：取 5~15 位数字最多的那一列"""
    best_col, best_count = None, 0
    for col in df.columns:
        count = sum(1 for v in df[col].dropna().head(200) if re.fullmatch(r"\d{5,15}", str(v).strip()))
        if count > best_count:
            best_count, best_col = count, col
    return best_col if (best_col is not None and best_count > 5) else None


def detect_product_column(df):
    return find_column(df, PRODUCT_COLUMN_NAMES) or detect_numeric_code_column(df)


def read_workbook(path):
    """读全部工作表 {名字: DataFrame}"""
    return pd.read_excel(path, sheet_name=None)


def _in(n):
    return ", ".join(["%s"] * n)


def _clean_codes(codes):
    out, seen = [], set()
    for v in codes:
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        if iv > 0 and iv not in seen:
            seen.add(iv)
            out.append(iv)
    return out


# ---------------------------------------------------------------------
# 数据库查询（和旧程序 database.py 相同）
# ---------------------------------------------------------------------
def query_ean_rows(engine, codes):
    """codbarre：每个商品的全部条码（按 l 排序）"""
    clean = _clean_codes(codes)
    frames = []
    for i in range(0, len(clean), 2000):
        chunk = clean[i:i + 2000]
        frames.append(pd.read_sql(f"SELECT CodArticolo, CodBarre, l FROM codbarre WHERE CodArticolo IN ({_in(len(chunk))}) "
                                  f"ORDER BY CodArticolo, l", engine, params=tuple(chunk)))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["CodArticolo", "CodBarre", "l"])


def build_ean_map(ean_df):
    """{商品编号: [非 66/55 开头的条码, 按 l 顺序, 去重]}"""
    if ean_df is None or ean_df.empty:
        return {}
    df = ean_df.copy()
    df["CodBarre"] = df["CodBarre"].apply(normalize_ean)
    df = df[df["CodBarre"] != ""]
    df = df[~df["CodBarre"].astype(str).str[:2].isin(EXCLUDE_PREFIXES)]
    df = df.sort_values("l")
    out = {}
    for code, grp in df.groupby("CodArticolo"):
        lst = []
        for b in grp["CodBarre"]:
            if b not in lst:
                lst.append(b)
        out[int(code)] = lst
    return out


def query_sales_inventory(engine, codes, days=7):
    """最近 days 天各门店销量（vendite_list_2026）+ 当前库存（giacenza）。列：CodArticolo, Descrizione, NegN_Sales, NegN_Inv, Total_*"""
    clean = _clean_codes(codes)
    cols = ["CodArticolo", "Descrizione"] + [f"Neg{i}_{k}" for i in range(1, 6) for k in ("Sales", "Inv")] + \
        ["Total_Sales", "Total_Inv"]
    if not clean:
        return pd.DataFrame(columns=cols)
    since = datetime.now().date() - timedelta(days=days)
    ph = _in(len(clean))
    frames = []
    for year in range(since.year, datetime.now().year + 1):      # 旧程序固定查 vendite_list_2026；跨年时两张表都查
        try:
            frames.append(pd.read_sql(f"SELECT v.CodArticolo, v.Negozio, SUM(v.Qta) AS SalesQty FROM vendite_list_{year} v "
                                      f"WHERE v.Data >= %s AND v.CodArticolo IN ({ph}) AND v.Qta > 0 "
                                      f"GROUP BY v.CodArticolo, v.Negozio", engine, params=tuple([since] + clean)))
        except Exception:
            if year == datetime.now().year:
                raise
    sales = pd.concat(frames, ignore_index=True) if frames else None
    inv = pd.read_sql(f"SELECT CodArticolo, COALESCE(G1,0) AS Neg1_Inv, COALESCE(G2,0) AS Neg2_Inv, COALESCE(G3,0) AS Neg3_Inv, "
                      f"COALESCE(G4,0) AS Neg4_Inv, COALESCE(G5,0) AS Neg5_Inv, "
                      f"COALESCE(G1+G2+G3+G4+G5,0) AS Total_Inv FROM giacenza WHERE CodArticolo IN ({ph})",
                      engine, params=tuple(clean))
    desc = pd.read_sql(f"SELECT CodArticolo, MAX(Descrizione) AS Descrizione FROM prodotti WHERE CodArticolo IN ({ph}) "
                       f"GROUP BY CodArticolo", engine, params=tuple(clean))
    return combine_sales_inventory(clean, sales, inv, desc)


def combine_sales_inventory(codes, sales, inv, desc):
    out = pd.DataFrame({"CodArticolo": codes})
    if sales is not None and not sales.empty:
        pv = sales.pivot_table(index="CodArticolo", columns="Negozio", values="SalesQty", aggfunc="sum",
                               fill_value=0).reset_index()
        pv.columns = ["CodArticolo"] + [f"Neg{int(c)}_Sales" for c in pv.columns[1:]]
        out = out.merge(pv, on="CodArticolo", how="left")
    if inv is not None and not inv.empty:
        out = out.merge(inv, on="CodArticolo", how="left")
    if desc is not None and not desc.empty:
        out = out.merge(desc, on="CodArticolo", how="left")
    for i in range(1, 6):
        for k in ("Sales", "Inv"):
            c = f"Neg{i}_{k}"
            if c not in out.columns:
                out[c] = 0
    out["Total_Sales"] = out[[f"Neg{i}_Sales" for i in range(1, 6)]].fillna(0).sum(axis=1)
    if "Total_Inv" not in out.columns:
        out["Total_Inv"] = out[[f"Neg{i}_Inv" for i in range(1, 6)]].fillna(0).sum(axis=1)
    if "Descrizione" not in out.columns:
        out["Descrizione"] = ""
    num = [c for c in out.columns if c.startswith("Neg") or c.startswith("Total")]
    out[num] = out[num].fillna(0)
    out["Descrizione"] = out["Descrizione"].fillna("")
    cols = ["CodArticolo", "Descrizione"] + [f"Neg{i}_{k}" for i in range(1, 6) for k in ("Sales", "Inv")] + \
        ["Total_Sales", "Total_Inv"]
    return out[cols]


def query_product_info(engine, codes):
    """条码、名称、供应商"""
    clean = _clean_codes(codes)
    if not clean:
        return pd.DataFrame(columns=["CodArticolo", "CodBarre", "Descrizione", "IDFornitore", "NomeFornitore"])
    return pd.read_sql(f"SELECT p.CodArticolo, COALESCE(p.CodBarre,'') AS CodBarre, COALESCE(p.Descrizione,'') AS Descrizione, "
                       f"COALESCE(p.IDFornitore,0) AS IDFornitore, COALESCE(f.Nome,'') AS NomeFornitore "
                       f"FROM prodotti p LEFT JOIN fornitore f ON p.IDFornitore = f.IDFornitore "
                       f"WHERE p.CodArticolo IN ({_in(len(clean))}) ORDER BY p.CodArticolo", engine, params=tuple(clean))


# ---------------------------------------------------------------------
# ① 分货数据处理
# ---------------------------------------------------------------------
def add_ean_columns(df, product_col, ean_map):
    """在 product_col 后面插入 Codice EAN / 2 / 3"""
    out = df.copy()
    vals = [[], [], []]
    for v in out[product_col]:
        code = to_int(v)
        lst = ean_map.get(code, []) if code is not None else []
        for k in range(3):
            vals[k].append(lst[k] if len(lst) > k else "")
    for k, name in enumerate(("Codice EAN", "Codice EAN 2", "Codice EAN 3")):
        if name in out.columns:
            out = out.drop(columns=[name])
    idx = list(out.columns).index(product_col) + 1
    for k, name in enumerate(("Codice EAN", "Codice EAN 2", "Codice EAN 3")):
        out.insert(idx + k, name, vals[k])
    return out


class ExtractError(Exception):
    pass


def find_sheet1(sheets):
    for name, df in sheets.items():
        if str(name).strip().lower() == "sheet1":
            return name, df
    if len(sheets) > 1:
        name = list(sheets.keys())[1]
        return name, sheets[name]
    return None, None


def dual_extract(sheets):
    """首页 + Sheet1 双表格提取。返回 dict(out, match_art, match_ean, miss, present, absent, unmatched)"""
    if not sheets:
        raise ExtractError("文件里没有工作表")
    home_name = list(sheets.keys())[0]
    home = sheets[home_name].copy()
    if home.empty:
        raise ExtractError("第一张表是空的")
    s1_name, sheet1 = find_sheet1(sheets)
    if sheet1 is None:
        raise ExtractError("找不到 Sheet1（文件只有一张表）")
    sheet1 = sheet1.copy()
    hp, he = find_column(home, HOME_PRODUCT_COLUMN_NAMES), find_column(home, HOME_EAN_COLUMN_NAMES)
    sa, se = find_column(sheet1, SHEET1_ART_COLUMN_NAMES), find_column(sheet1, SHEET1_EAN_COLUMN_NAMES)
    missing = [x for x, c in (("首页 商品编号列", hp), ("首页 商品条码列", he), ("Sheet1 Cod.Art. 列", sa),
                              ("Sheet1 Codice EAN 列", se)) if c is None]
    if missing:
        raise ExtractError("缺少列：" + "、".join(missing))
    sources = {}
    pz = find_column(sheet1, ["PZ*COLLI"])
    if pz is not None:
        sources["PZ*COLLI"] = pz
    for store, ci in STORE_SOURCE_COLUMN_INDEX.items():
        if ci < len(sheet1.columns):
            sources[store] = sheet1.columns[ci]
    present = [t for t in EXTRACTION_TARGETS if t in sources]
    absent = [t for t in EXTRACTION_TARGETS if t not in sources]
    if not present:
        raise ExtractError("Sheet1 里找不到 PZ*COLLI，也没有门店固定列 K/N/Q/T/W")
    art_index, ean_index = {}, {}
    for idx, v in sheet1[sa].items():
        iv = to_int(v)
        if iv is not None:
            art_index[iv] = idx
    for idx, v in sheet1[se].items():
        iv = to_int(v)
        if iv is not None:
            ean_index[iv] = idx
    extracted = {t: [] for t in present}
    match_art = match_ean = miss = 0
    matched, status = set(), []
    for _, row in home.iterrows():
        code, ean = to_int(row[hp]), to_int(row[he])
        sidx = None
        if code is not None and code in art_index:
            sidx = art_index[code]
            match_art += 1
            status.append("art")
        elif ean is not None and ean in ean_index:
            sidx = ean_index[ean]
            match_ean += 1
            status.append("ean")
        else:
            miss += 1
            status.append("miss")
        for t in present:
            if sidx is None:
                extracted[t].append("")
            else:
                v = sheet1.at[sidx, sources[t]]
                extracted[t].append("" if v is None or (isinstance(v, float) and pd.isna(v)) else v)
        if sidx is not None:
            matched.add(sidx)
    out = home
    name = lambda t: STORE_NAMES.get(t, str(t)) if isinstance(t, int) else t     # noqa: E731
    for t in present:
        cn = name(t)
        if cn in out.columns:
            out = out.drop(columns=[cn])
        out[cn] = extracted[t]
    store_cols = [name(t) for t in present if isinstance(t, int)]
    qty = find_column(out, QUANTITY_COLUMN_NAMES)
    if qty and store_cols:
        if "差值" in out.columns:
            out = out.drop(columns=["差值"])

        def diff(r):
            q = to_int(r[qty])
            if q is None:
                return ""
            return q - sum(to_int(r[c]) or 0 for c in store_cols)
        out["差值"] = [diff(r) for _, r in out.iterrows()]
    um_idx = [i for i in sheet1.index if i not in matched]
    unmatched = sheet1.loc[um_idx].copy() if um_idx else pd.DataFrame()
    if not unmatched.empty:
        unmatched = unmatched.rename(columns={sources[t]: name(t) for t in present if isinstance(t, int)})
    return {"out": out, "match_art": match_art, "match_ean": match_ean, "miss": miss, "present": present,
            "absent": absent, "unmatched": unmatched, "status": status, "home_name": home_name, "sheet1_name": s1_name}


def process(sheets, mode, engine_factory=None, step=None):
    """
    mode: "ean" 补齐 EAN / "dual" 双表格提取 / "unified" 一体化
    engine_factory: 需要查 codbarre 时调用，返回 (engine, close)
    step(i, state, count, detail, pct): 进度回调
    返回 dict(out, unmatched, stats)
    """
    step = step or (lambda *a, **k: None)
    stats, unmatched = {}, None
    home = sheets[list(sheets.keys())[0]]
    if mode in ("dual", "unified"):
        step(0, "running", "", "", 10)
        res = dual_extract(sheets)
        out = res["out"]
        unmatched = res["unmatched"] if mode == "unified" else None
        stats.update(rows=len(out), match_art=res["match_art"], match_ean=res["match_ean"], miss=res["miss"],
                     unmatched=0 if res["unmatched"] is None else len(res["unmatched"]), absent=res["absent"],
                     row_status=res["status"])
        step(0, "done", f"{res['match_art'] + res['match_ean']} / {len(out)}", "", 40)
    else:
        out = home.copy()
        stats.update(rows=len(out))
        step(0, "done", str(len(out)), "", 40)
    if mode in ("ean", "unified"):
        pc = find_column(out, HOME_PRODUCT_COLUMN_NAMES) if mode == "unified" else None
        pc = pc or detect_product_column(out)
        if pc is None:
            raise ExtractError("找不到商品编号列（Cod.Art. / Codice prodotto / CodArticolo / 商品编号）")
        codes = [c for c in (to_int(v) for v in out[pc]) if c is not None]
        step(1, "running", f"{len(codes)}", "codbarre", 50)
        ean_map = {}
        if codes and engine_factory:
            engine, close = engine_factory()
            try:
                ean_map = build_ean_map(query_ean_rows(engine, codes))
            finally:
                close()
        out = add_ean_columns(out, pc, ean_map)
        for k, name in enumerate(("Codice EAN", "Codice EAN 2", "Codice EAN 3")):
            stats[f"ean{k + 1}"] = int((out[name] != "").sum())
        stats["product_col"] = pc
        step(1, "done", f"{stats['ean1']} / {len(out)}", "", 85)
    return {"out": out, "unmatched": unmatched, "stats": stats}


# ---------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------
def _text_cols(df):
    df = df.copy()
    for c in TEXT_BARCODE_COLUMNS:
        if c in df.columns:
            df[c] = df[c].apply(lambda v: "" if v is None or (isinstance(v, float) and pd.isna(v)) else normalize_ean(v))
    return df


def _format_sheet(ws):
    from openpyxl.styles import Alignment, Font, PatternFill
    header = [c.value for c in ws[1]]
    for i, name in enumerate(header, start=1):
        letter = ws.cell(1, i).column_letter
        if name in TEXT_BARCODE_COLUMNS:
            ws.column_dimensions[letter].width = 18
            for r in range(2, ws.max_row + 1):
                ws.cell(r, i).number_format = "@"
        elif name in ("Descrizione", "Nome Prodotto", "名称", "Nome Fornitore"):
            ws.column_dimensions[letter].width = 36
        else:
            ws.column_dimensions[letter].width = max(10, min(22, len(str(name or "")) + 4))
        c = ws.cell(1, i)
        c.font = Font(bold=True)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.fill = PatternFill("solid", fgColor="EEF2F8")
    ws.freeze_panes = "A2"


def export_distribution(path, out, unmatched=None):
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        _text_cols(out).to_excel(w, sheet_name="Distribuzione", index=False)
        _format_sheet(w.sheets["Distribuzione"])
        if unmatched is not None and not unmatched.empty:
            _text_cols(unmatched).to_excel(w, sheet_name="Sheet1 未匹配", index=False)
            _format_sheet(w.sheets["Sheet1 未匹配"])
    return path


def default_export_path(src, tag):
    base = os.path.splitext(os.path.basename(src))[0] if src else tag
    folder = os.path.dirname(src) if src else os.path.expanduser("~")
    return os.path.join(folder, f"{base}_{tag}_{datetime.now():%Y%m%d_%H%M}.xlsx")


# ---------------------------------------------------------------------
# ② 导入 / 导出：调货判断
# ---------------------------------------------------------------------
def detect_codes(df):
    """导入清单里的商品编号（旧程序的列名规则 + 新的通用规则）"""
    col_lower = {str(c).lower().strip(): c for c in df.columns}
    for name in ("codarticolo", "codart", "cod.art.", "codice", "codice_prodotto", "codice prodotto", "cod_prodotto",
                 "codice articolo", "cod.articolo", "商品编号", "产品代码", "编码", "product_code", "productcode",
                 "cod", "art", "articolo"):
        if name in col_lower:
            col = col_lower[name]
            codes = [c for c in (to_int(v) for v in df[col]) if c and c > 0]
            if codes:
                return list(dict.fromkeys(codes)), col
    col = detect_numeric_code_column(df) or (df.columns[0] if len(df.columns) else None)
    if col is None:
        return [], None
    codes = [c for c in (to_int(v) for v in df[col]) if c and c > 0]
    return list(dict.fromkeys(codes)), col


def restock(df, need_store, order):
    """
    每个商品：按供货顺序找第一家「库存 > 补货店销量 + 这家自己的销量」的门店。
    返回 [dict(code, desc, need, supplier, supplier_inv, available, ok)]
    """
    out = []
    for _, r in df.iterrows():
        need = int(r.get(f"Neg{need_store}_Sales", 0) or 0)
        res = {"code": int(r["CodArticolo"]), "desc": str(r.get("Descrizione") or ""), "need": need,
               "need_store": need_store, "need_store_name": STORE_NAMES.get(need_store, str(need_store)),
               "need_inv": int(r.get(f"Neg{need_store}_Inv", 0) or 0),
               "supplier": None, "supplier_inv": 0, "available": 0, "ok": False}
        for s in order:
            if s == need_store:
                continue
            inv = int(r.get(f"Neg{s}_Inv", 0) or 0)
            own = int(r.get(f"Neg{s}_Sales", 0) or 0)
            avail = inv - (need + own)
            if inv > need + own:
                res.update(supplier=s, supplier_inv=inv, available=avail, ok=True)
                break
            if res["supplier"] is None:
                res.update(supplier=s, supplier_inv=inv, available=avail)
        out.append(res)
    return out


def export_report(path, codes, sales_df, info_df, restock_rows=None, days=7):
    """完整报告：条码 + 供应商 + 各店销量 / 库存 + 调货结果（一张表）"""
    base = info_df if info_df is not None and not info_df.empty else pd.DataFrame({"CodArticolo": codes})
    sales_cols = [c for c in sales_df.columns if c != "Descrizione"]
    df = base.merge(sales_df[sales_cols], on="CodArticolo", how="left")
    if "Descrizione" not in df.columns:
        df = df.merge(sales_df[["CodArticolo", "Descrizione"]], on="CodArticolo", how="left")
    num = df.select_dtypes(include=["number"]).columns
    df[num] = df[num].fillna(0)
    rs = {r["code"]: r for r in (restock_rows or [])}
    if rs:
        df["Negozio da rifornire"] = df["CodArticolo"].map(lambda c: rs[c]["need_store_name"] if c in rs else "")
        df["Fornitore Selezionato"] = df["CodArticolo"].map(
            lambda c: STORE_NAMES.get(rs[c]["supplier"], "") if c in rs and rs[c]["supplier"] else "")
        df["Giacenza Fornitore"] = df["CodArticolo"].map(lambda c: rs[c]["supplier_inv"] if c in rs else 0)
        df["Q.tà Disponibile"] = df["CodArticolo"].map(lambda c: rs[c]["available"] if c in rs else 0)
        df["Stato Rifornimento"] = df["CodArticolo"].map(
            lambda c: ("✓ Disponibile" if rs[c]["ok"] else "✗ Non disponibile") if c in rs else "")
    ren = {"CodArticolo": "Codice Prodotto", "CodBarre": "Codice a Barre", "Descrizione": "Nome Prodotto",
           "IDFornitore": "ID Fornitore", "NomeFornitore": "Nome Fornitore", "Total_Sales": "Totale Vendite",
           "Total_Inv": "Totale Giacenza"}
    for i in range(1, 6):
        ren[f"Neg{i}_Sales"] = f"{STORE_NAMES[i]} Vendite {days}gg"
        ren[f"Neg{i}_Inv"] = f"{STORE_NAMES[i]} Giacenza"
    order = ["CodArticolo", "CodBarre", "Descrizione", "IDFornitore", "NomeFornitore"] + \
        [f"Neg{i}_{k}" for i in range(1, 6) for k in ("Sales", "Inv")] + ["Total_Sales", "Total_Inv"]
    order = [c for c in order if c in df.columns] + [c for c in df.columns if c not in order]
    df = df[order].rename(columns=ren)
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        _text_cols(df).to_excel(w, sheet_name="Report", index=False)
        _format_sheet(w.sheets["Report"])
    return path
