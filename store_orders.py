"""
门店配货（品牌供应商 SCHEDA 表）

采购配单文件夹按 周一…周五 分子文件夹，每个供应商一个 SCHEDA 表：
  - 「SCHEDA ANAGRAFICA FORNITORI」页第 35/36 行：起订箱数 MINIMO ORDINE VOLUME / 起订金额 MINIMO ORDINE IMPORTO
  - 门店页 FOIANO / MARLIA / S.FILIPPO / S.MARCO / S.CONCORDIO：商品清单，H 列「Colli」填箱数

流程：
  1. 用货物分配系统的算法按每家门店算建议件数 → 按 SCHEDA 里的每箱件数换成箱数
  2. 每家店分别看是否达到这家供应商的起订量（每家店单独满足才能直接送店）
     差得不多（在设置的范围内）就凑单；差太多就并入 BMS（0 号总仓，独立公司实体）一起订
  3. BMS 合并后再看起订量，同样可以凑单
  4. 每家直接送店的门店出一个文件；BMS 一个文件（复制 SCHEDA，只改 Colli 列，图片和格式保留）
"""
import os
import re
import math
import zipfile
from datetime import datetime

STORE_SHEETS = {1: "FOIANO", 2: "MARLIA", 3: "S.FILIPPO", 4: "S.MARCO", 5: "S.CONCORDIO"}
BMS = "BMS"
DAY_FOLDERS = ["周一", "周二", "周三", "周四", "周五"]

DEFAULT_FILL = {
    "max_amount_gap": 30.0,   # 离起订金额差多少欧以内才凑单
    "max_colli_gap": 3,       # 离起订箱数差几箱以内才凑单
    "max_stock_days": 60,     # 凑单加的货：加完后库存最多够卖几天
    "fill_bms": True,         # BMS 合并后不够起订量时也尝试凑单
    # BMS 不够起订量时的补救（按顺序试，能让 BMS 订得起来才用，否则保持原样）
    "merge_colocated": True,  # ① 和 BMS 同一个仓库的门店整单并进 BMS（不多跑一趟）
    "borrow": True,           # ② 从同城直接送店的门店借「不紧急」的货给 BMS（到货后由 BMS 分回去）
    "borrow_min_days": 7,     # 借货条件：这家店现有库存至少还够卖几天
    "far_stores": [1],        # 离得远的门店：不借它的货
    "colocated_store": 3,     # 和 BMS 仓库在一起的门店
}


def _norm(v):
    return re.sub(r"\s+", " ", str(v or "")).strip().upper()


def _num(v):
    try:
        if v in (None, ""):
            return None
        return float(str(v).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def _first_number(text):
    m = re.search(r"\d+(?:[.,]\d+)?", str(text or ""))
    return float(m.group(0).replace(",", ".")) if m else 0.0


def supplier_short_name(path):
    """'SCHEDA DOPLA 0110 .xlsx' → 'DOPLA'；'SCHEDA FHP(VILEDA) 2308.xlsx' → 'FHP(VILEDA)'"""
    n = os.path.splitext(os.path.basename(path))[0]
    n = re.sub(r"^\s*SCHEDA\s+", "", n, flags=re.I)
    n = re.sub(r"\(\d{4}-\d{2}-\d{2}.*?\)", "", n)
    n = re.sub(r"\s+\d{4,6}\s*$", "", n.strip())
    return n.strip() or os.path.basename(path)


# =====================================================================
# 起订量（SCHEDA ANAGRAFICA 页「MINIMO ORDINE VOLUME / IMPORTO」两行）
# 文件里经常填错行：金额写在「箱数」那行（LAMPA "100€"）、板数写在「金额」那行（"7 PANCALI"），
# 所以不看行，先看文字里的单位：€ / EURO → 金额；BANCALE / PEDANE / PALLET → 板；COLLI → 箱；
# 没写单位的纯数字才按行算。拿不准的（有 %、几个不同的数、倍数规则）标 uncertain，界面上提醒去确认。
# 用户手动改过的（settings.json → store_orders.moq_override[供应商名]）优先。
# =====================================================================
_NUM_RE = r"\d+(?:[.,]\d+)?"
_PALLET_RE = re.compile(r"BANCAL|PEDAN|PALLET|PALET|PANCAL|BILIC", re.I)
_EURO_RE = re.compile(r"€|\bEUR|\bERUO|EURO", re.I)
_COLLI_RE = re.compile(r"COLL[IO]|CARTON|SCATOL|\bCT\b", re.I)
_MIN_AMOUNT = 20.0      # 低于 20 欧的金额当作运费 / 杂费，不是起订金额（"sotto 100€ c'è 8€ di trasp."）
_NAME_STOP = {"SPA", "SRL", "SRLS", "SAS", "SNC", "ITALIA", "ITALY", "GROUP", "SOCIETA", "THE", "AND"}


def _f(x):
    return float(str(x).replace(",", "."))


def _name_tokens(name):
    return {t for t in re.findall(r"[A-Z]{3,}", _norm(name)) if t not in _NAME_STOP}


def _euro_amounts(s):
    """文字里紧挨着 € / EURO 的金额：[(金额, 起始位置, 结束位置)]"""
    out = []
    for m in re.finditer(r"(" + _NUM_RE + r")\s*(?:€|EURO|ERUO|EUR)|(?:€|EURO|EUR)\s*(" + _NUM_RE + r")", s, re.I):
        out.append((_f(m.group(1) or m.group(2)), m.start(), m.end()))
    return out


def parse_moq_cell(val, row_kind, supplier=""):
    """
    一格的起订量。row_kind: "colli"（MINIMO ORDINE VOLUME 那行）/ "amount"（MINIMO ORDINE IMPORTO 那行）
    返回 {"colli", "amount", "pallets", "multiple", "uncertain"}（没有就是空 dict）
    """
    if val in (None, ""):
        return {}
    if isinstance(val, (int, float)):
        return {row_kind: float(val)} if val > 0 else {}
    s = str(val).strip()
    nums = [_f(x) for x in re.findall(_NUM_RE, s)]
    if not nums:
        return {}                       # 「口 SI : 口 NO」之类的空表格
    U = s.upper()
    unsure = "%" in s or bool(re.search(r"\d\s*-\s*\d", s))
    if "MULTIPL" in U:                  # "Quantità ordinabile in multipli di 10 colli" → 不是起订量，是倍数
        return {"multiple": nums[0], "uncertain": True}
    if _PALLET_RE.search(U):
        m = re.search(r"(" + _NUM_RE + r")\s*(?:" + _PALLET_RE.pattern + ")", s, re.I)
        return {"pallets": _f(m.group(1)) if m else nums[0], "uncertain": unsure}
    if _EURO_RE.search(U):
        amts = [a for a in _euro_amounts(s) if a[0] >= _MIN_AMOUNT] or \
               [(n, 0, 0) for n in nums if n >= _MIN_AMOUNT][:1]
        if not amts:
            return {"uncertain": True}
        distinct = sorted({a[0] for a in amts})
        pick = amts[0][0]
        if len(distinct) > 1:
            # 一格里几个金额（"500€ GIMI  600€ VILEDA"）：找后面 / 前面紧挨着的名字和供应商名对得上的那个
            # 名字只看两个金额之间那一段：先看金额后面（"500€ GIMI"），都对不上再看前面（"TOYS:400€"）
            mine = _name_tokens(supplier)
            found = None
            for side in ("after", "before"):
                for k, (amt, a, b) in enumerate(amts):
                    seg = s[b:amts[k + 1][1]] if side == "after" and k + 1 < len(amts) else \
                        s[b:] if side == "after" else s[(amts[k - 1][2] if k else 0):a]
                    if mine & _name_tokens(seg):
                        found = amt
                        break
                if found is not None:
                    break
            if found is None:
                unsure = True
            else:
                pick = found
        return {"amount": pick, "uncertain": unsure}
    if _COLLI_RE.search(U):
        return {"colli": nums[0], "uncertain": unsure or len(set(nums)) > 1}
    return {row_kind: nums[0], "uncertain": unsure or len(set(nums)) > 1}


def read_moq(ana, supplier=""):
    """从 ANAGRAFICA 工作表读起订量 → moq dict（见 moq_effective）；ana 为 None 时返回空的"""
    m = {"colli": 0.0, "amount": 0.0, "pallets": 0.0, "per_pallet": 0.0, "multiple": 0.0, "uncertain": False,
         "raw": "", "company": "", "source": "file"}
    if ana is None:
        return m
    raws = []
    for r in ana.iter_rows(min_row=1, max_row=60, max_col=6, values_only=True):
        label = _norm(r[1] if len(r) > 1 else "")
        cells = [v for v in r[2:6] if v not in (None, "") and str(v).strip()]
        val = cells[0] if cells else None
        if label.startswith("RAGIONE SOCIALE"):
            m["company"] = str(val or "").strip()
            continue
        if label.startswith("MINIMO ORDINE VOLUME"):
            kind = "colli"
        elif label.startswith("MINIMO ORDINE IMPORTO"):
            kind = "amount"
        else:
            continue
        if val is not None:
            raws.append(re.sub(r"\s+", " ", str(val)).strip())
        got = parse_moq_cell(val, kind, supplier)
        for k in ("colli", "amount", "pallets", "multiple"):
            if got.get(k) and not m[k]:
                m[k] = got[k]
        m["uncertain"] = m["uncertain"] or bool(got.get("uncertain"))
    m["raw"] = " | ".join(x for x in raws if x)
    return m


def apply_override(m, override):
    """手动改过的起订量盖掉文件里读到的"""
    if not override:
        return dict(m)
    out = dict(m)
    for k in ("colli", "amount", "pallets", "per_pallet", "multiple"):
        out[k] = float(override.get(k) or 0)
    out["uncertain"] = False
    out["source"] = "manual"
    out["note"] = override.get("note", "")
    return out


def moq_effective(m):
    """判断起订量用的 (箱数, 金额)。按板起订但没填每板几箱 → 箱数 = 无穷大（单独永远不够，等手动填）"""
    colli = float(m.get("colli") or 0)
    if m.get("pallets"):
        if m.get("per_pallet"):
            colli = max(colli, float(m["pallets"]) * float(m["per_pallet"]))
        else:
            colli = math.inf
    return colli, float(m.get("amount") or 0)


def set_moq(info, m):
    """把 moq dict 放进 SCHEDA 信息里（moq_colli / moq_amount 是配货判断用的有效值）"""
    info["moq"] = m
    info["moq_colli"], info["moq_amount"] = moq_effective(m)
    return info


def sc_moq(sc):
    """SCHEDA 信息里的 moq dict（老数据 / 测试里只有 moq_colli、moq_amount 时现拼一个）"""
    return sc.get("moq") or {"colli": sc.get("moq_colli") or 0, "amount": sc.get("moq_amount") or 0}


def pallet_unknown(sc):
    return math.isinf(sc.get("moq_colli") or 0)


def moq_parts(m, t=None, short=False):
    """起订量的几段文字；t = 翻译函数（界面用 tr，文件里用中文原文）；short = 窄的列表里用的简短写法"""
    t = t or (lambda s, *a: s.format(*a))
    parts = []
    if m.get("colli"):
        parts.append(t("{0:g} 箱", m["colli"]))
    if m.get("amount"):
        parts.append(f"€{m['amount']:g}")
    if m.get("pallets"):
        if short:
            parts.append(t("{0:g} 板", m["pallets"]))
        else:
            parts.append(t("{0:g} 板（{1:g} 箱/板）", m["pallets"], m["per_pallet"]) if m.get("per_pallet")
                         else t("{0:g} 板（每板箱数没填）", m["pallets"]))
    if m.get("multiple"):
        parts.append(t("×{0:g} 箱", m["multiple"]) if short else t("{0:g} 箱的倍数", m["multiple"]))
    return parts


def moq_needs_check(m):
    """要提醒去确认：读的时候拿不准（没手动改过），或者按板起订但还没填每板几箱"""
    return bool(m) and ((m.get("uncertain") and m.get("source") != "manual") or
                        (bool(m.get("pallets")) and not m.get("per_pallet")))


# ---------------------------------------------------------------------
# 和 suppliers_config.json（supplier_details：数据库供应商 ID → 名字、起订金额 …）对应
# ---------------------------------------------------------------------
def _letters(s):
    return re.sub(r"[^A-Z]", "", _norm(s))


def match_config_supplier(name, company, details):
    """
    SCHEDA 的供应商（文件名里的短名 + 公司名）→ suppliers_config.json 里的供应商 ID（字符串）；对不上返回 None。
    短名里的每个词都要在配置的名字里找到（允许拼错一两个字母，例如 INDUSTRIAUMBRA / INDUSTRAUMBRA），
    只对上一部分（FHP(VILEDA) 只对上 FHP）不算，交给用户自己选。
    """
    import difflib
    mine = _name_tokens(name) or ({_letters(name)} if _letters(name) else set())
    if not mine:
        return None
    best, best_score, tie = None, 0.0, False
    for sid, d in (details or {}).items():
        cname = d.get("name", "")
        ctoks = _name_tokens(cname) | {w for w in re.findall(r"[A-Z]+", _norm(cname))}
        joined = _letters(cname)
        hit = 0
        for tkn in mine:
            if tkn in ctoks or (len(tkn) >= 4 and tkn in joined) or \
                    any(len(c) >= 4 and difflib.SequenceMatcher(None, tkn, c).ratio() >= 0.85 for c in ctoks):
                hit += 1
        score = hit / len(mine)
        if company and _letters(company) and _letters(company) == joined:
            score = 2.0
        if score > best_score + 1e-9:
            best, best_score, tie = sid, score, False
        elif score > 0 and abs(score - best_score) < 1e-9:
            tie = True
    if best is None or tie or best_score < 0.6 or (best_score < 0.99 and len(mine) < 3):
        return None
    return best


def load_suppliers_config(path):
    import json
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_config_moq(path, supplier_id, m, scheda_name=""):
    """把手动改的起订量写进 suppliers_config.json 的 supplier_details[ID]（min_order_amount 等），保持原来的格式"""
    import json
    cfg = load_suppliers_config(path)
    d = cfg.setdefault("supplier_details", {}).get(str(supplier_id))
    if d is None:
        raise KeyError(f"suppliers_config.json 里没有供应商 {supplier_id}")
    amt = float(m.get("amount") or 0)
    d["min_order_amount"] = int(amt) if amt == int(amt) else amt
    for key, src in (("min_order_colli", "colli"), ("min_order_pallets", "pallets"), ("colli_per_pallet", "per_pallet")):
        v = float(m.get(src) or 0)
        if v or key in d:
            d[key] = int(v) if v == int(v) else v
    if scheda_name:
        d["scheda_name"] = scheda_name
    cfg["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)
    os.replace(tmp, path)
    return d


def list_schede(root, day=None):
    """采购配单文件夹里的 SCHEDA 文件：[(星期, 路径)]"""
    out = []
    days = [day] if day else DAY_FOLDERS
    for d in days:
        folder = os.path.join(root, d)
        if not os.path.isdir(folder):
            continue
        for f in sorted(os.listdir(folder)):
            if f.lower().endswith((".xlsx", ".xlsm")) and not f.startswith("~$") and "SCHEDA" in f.upper():
                out.append((d, os.path.join(folder, f)))
    return out


# =====================================================================
# 读取 SCHEDA
# =====================================================================
def parse_scheda(path, overrides=None):
    """
    返回 {"path", "name", "company", "moq": {...}, "moq_colli", "moq_amount",
          "items": [{row, code, ean, desc, pack, price}], "sheets": {门店号: 工作表名}, "header_row", "cols"}
    overrides: {供应商名: 手动改的起订量}（settings.json → store_orders.moq_override）
    """
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        name = supplier_short_name(path)
        info = {"path": path, "name": name, "company": "", "items": [], "sheets": {}, "warnings": []}
        ana = next((ws for ws in wb.worksheets if "ANAGRAFICA" in ws.title.upper()), None)
        m = read_moq(ana, name)
        info["company"] = m["company"]
        info["moq_file"] = m
        set_moq(info, apply_override(m, (overrides or {}).get(name)))
        if ana is None:
            info["warnings"].append("没有 SCHEDA ANAGRAFICA FORNITORI 页，读不到起订量")
        elif m["uncertain"] and info["moq"]["source"] == "file":
            info["warnings"].append(f"起订量拿不准（文件里写的是：{m['raw']}），请在起订量那一格点开确认")
        titles = {ws.title.strip().upper(): ws.title for ws in wb.worksheets}
        for sid, name in STORE_SHEETS.items():
            if name in titles:
                info["sheets"][sid] = titles[name]
        if not info["sheets"]:
            raise ValueError("没有门店页（FOIANO / MARLIA / S.FILIPPO / S.MARCO / S.CONCORDIO）")
        ws = wb[next(iter(info["sheets"].values()))]
        cols, hdr = {}, None
        for i, row in enumerate(ws.iter_rows(min_row=1, max_row=15, values_only=True), 1):
            found = {}
            for c, v in enumerate(row, 1):
                n = _norm(v).replace(" ", "")
                if n == "COD.ART.":
                    found["code"] = c
                elif n == "CODICEEAN":
                    found["ean"] = c
                elif n.startswith("DESCRIZIONE"):
                    found["desc"] = c
                elif n == "PZ*COLLI":
                    found["pack"] = c
                elif n == "COLLI":
                    found["colli"] = c
                elif n == "PREZZONETTO":
                    found["price"] = c
            if "colli" in found and ("ean" in found or "code" in found):
                cols, hdr = found, i
                break
        if not hdr:
            raise ValueError("门店页没有找到表头（Cod.Art. / Codice EAN / Colli）")
        info["header_row"], info["cols"] = hdr, cols
        # 订单文件里附加数据列（GIA、ROT.30d …）的起始列：表头里已经有 GIA 就用那一列（以前写过的文件），
        # 否则接在表头最后一列（一般是 IMPORTO）后面
        hrow = next(ws.iter_rows(min_row=hdr, max_row=hdr, values_only=True), ())
        filled = [c for c, v in enumerate(hrow, 1) if v not in (None, "") and str(v).strip()]
        gia = next((c for c, v in enumerate(hrow, 1) if _norm(v) == "GIA"), None)
        info["extra_col"] = gia or ((max(filled) + 1) if filled else len(hrow) + 1)
        for r_idx, row in enumerate(ws.iter_rows(min_row=hdr + 1, values_only=True), hdr + 1):
            def g(k):
                c = cols.get(k)
                return row[c - 1] if c and c - 1 < len(row) else None
            code, ean = g("code"), g("ean")
            code = str(int(code)) if isinstance(code, float) and code == int(code) else str(code or "").strip()
            ean = str(int(ean)) if isinstance(ean, float) and ean == int(ean) else str(ean or "").strip()
            if not code and not ean:
                continue
            pack = _num(g("pack")) or 1
            info["items"].append({"row": r_idx, "code": code, "ean": ean, "desc": str(g("desc") or "").strip(),
                                  "pack": pack if pack > 0 else 1, "price": _num(g("price")) or 0.0})
        # 各门店页一般行完全一样；万一不一样，按商品编号 / EAN 对应到各自的行
        info["rowmap"] = {}
        first = next(iter(info["sheets"].values()))
        for sid, sheet in info["sheets"].items():
            if sheet == first:
                continue
            key2row = {}
            for r_idx, row in enumerate(wb[sheet].iter_rows(min_row=hdr + 1, values_only=True), hdr + 1):
                for k in ("code", "ean"):
                    c = cols.get(k)
                    v = row[c - 1] if c and c - 1 < len(row) else None
                    v = str(int(v)) if isinstance(v, float) and v == int(v) else str(v or "").strip()
                    if v:
                        key2row.setdefault((k, v), r_idx)
            m = {}
            for it in info["items"]:
                r = key2row.get(("code", it["code"])) if it["code"] else None
                r = r or (key2row.get(("ean", it["ean"])) if it["ean"] else None)
                if r and r != it["row"]:
                    m[it["row"]] = r
                elif not r:
                    m[it["row"]] = None
            if m:
                info["rowmap"][sid] = m
                if any(v is None for v in m.values()):
                    info["warnings"].append(f"{sheet} 页少了一些商品，那些商品不会写进这家店")
        return info
    finally:
        wb.close()


def parse_moq(path):
    """只读起订量（列表里显示用，比 parse_scheda 快）：返回文件里读到的 moq dict（还没套手动修改）"""
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ana = next((ws for ws in wb.worksheets if "ANAGRAFICA" in ws.title.upper()), None)
        return read_moq(ana, supplier_short_name(path))
    finally:
        wb.close()


def moq_text(m):
    """汇总表 / 日志用（中文）"""
    return " / ".join(moq_parts(m)) or "无"


def moq_label(m, mark=True, short=False):
    """
    界面显示用（翻译后）；手动改过的前面加 ✎，要确认的后面加 ⚠。
    short=True：供应商列表这种窄列用简短写法（"1 板 ⚠"），完整说明放在鼠标提示里，免得把供应商名挤掉。
    """
    from i18n import tr
    if m is None:
        return "…"
    s = " / ".join(moq_parts(m, tr, short)) or tr("无")
    if mark and m.get("source") == "manual":
        s = "✎ " + s
    if mark and moq_needs_check(m):
        s += " ⚠"
    return s


# =====================================================================
# 计算
# =====================================================================
def demand_by_store(db, schede, stores, days_list, reorder_cycle, use_supplier_cycles=True, algo=None,
                    on_store=None):
    """
    每家门店跑一次原程序的订货建议算法（所有 SCHEDA 的商品一起查）。
    返回 {门店号: {商品编号: {"sugg": 建议件数, "daily": 日均销量, "inv": 本店库存}}, "_missing": [查不到编号的商品]}
    """
    import reorder_service as R
    # Cod.Art. 就是我们的商品编号；没有编号的用 EAN 查
    need_ean = [it["ean"] for s in schede for it in s["items"]
                if not re.fullmatch(r"\d{1,9}", it["code"] or "") and it["ean"]]
    ean_map = R.codes_from_eans(db, need_ean) if need_ean else {}
    missing = []
    for s in schede:
        for it in s["items"]:
            if re.fullmatch(r"\d{1,9}", it["code"] or ""):
                it["db_code"] = it["code"]
            else:
                it["db_code"] = ean_map.get(it["ean"])
                if not it["db_code"]:
                    missing.append((s["name"], it["code"] or it["ean"], it["desc"]))
    codes = sorted({it["db_code"] for s in schede for it in s["items"] if it.get("db_code")})
    out = {"_missing": missing, "_algo": dict(algo) if algo else None}
    if not codes:
        return out
    for k, sid in enumerate(stores):
        if on_store:
            on_store(k, sid)      # 进度：第 k 家门店开始查
        # 新算法：不按数据库箱规取整，箱数按 SCHEDA 的每箱件数智能取整（colli_for）
        # 原程序算法：建议件数完全按原程序算（含按数据库箱规取整），再按 SCHEDA 每箱件数换成箱
        v2 = bool(algo) and algo.get("engine") == "v2"
        # 原程序算法：每个 SCHEDA（供应商）单独算，和原程序一次导入一个 SCHEDA 一样（A/B/C 等级在同一家供应商的商品里比）
        groups = None if v2 else [sorted({it["db_code"] for it in s["items"] if it.get("db_code")}) for s in schede]
        df = R.compute_suggestions(db, sid, codes, days_list, reorder_cycle, None,
                                   use_supplier_cycles=use_supplier_cycles, round_to_db_pack=not v2, algo=algo,
                                   groups=groups)
        d = {}
        for _, r in df.iterrows():
            code = str(int(r["CodArticolo"]))
            s1 = float(r.get("sales_1", 0) or 0)
            s3 = float(r.get("sales_3", 0) or 0)
            daily = s1 / days_list[0] if s1 > 0 else (s3 / days_list[2] if s3 > 0 else 0.0)
            if R._val(r, "daily_rate") is not None:                       # 新算法的日均（加权 + 季节）
                daily = float(R._val(r, "daily_rate") or 0)
            inv_days = R._val(r, "inventory_days")
            d[code] = {"sugg": int(R._val(r, "suggested_order_qty", 0) or 0), "daily": daily,
                       "inv": float(R._val(r, "store_inventory", 0) or 0),        # 计算用（负数已当 0）
                       "inv_raw": float(R._val(r, "store_inventory_raw", R._val(r, "store_inventory", 0)) or 0),
                       "sales": [R._val(r, f"sales_{i}", 0) or 0 for i in range(1, 5)],
                       "inv_days": inv_days, "decision": R._val(r, "decision_text", "") or "",
                       "tier": R._val(r, "sales_tier", "") or "",
                       "cycle": R._val(r, "reorder_cycle_used", reorder_cycle),
                       "safety": R._val(r, "safety_stock"), "target": R._val(r, "target_stock"),
                       "season": R._val(r, "season_factor"), "weekly": R._val(r, "weekly_26"),
                       "service": R._val(r, "service_level"),
                       "db_pack": R._val(r, "qtasmallbox"),          # 数据库箱规（和 SCHEDA 每箱件数对照用）
                       "gia_all": [R._val(r, f"Negozio{i}_Giacenza", 0) or 0 for i in range(6)]}   # GIA.0（BMS）~ GIA.5
        out[sid] = d
    return out


def colli_for(d, pack, algo=None):
    """建议件数 → SCHEDA 箱数。新算法：智能取整（零头够多才多订一箱）；原程序算法：件数（已按原程序取整）÷ 每箱件数，向上取整"""
    if not d or d["sugg"] <= 0:
        return 0
    if algo and algo.get("engine") == "v2":
        import forecast as F
        return F.to_packs(d["sugg"], pack, d.get("inv", 0), d.get("daily", 0), algo)
    return math.ceil(d["sugg"] / pack)


def base_colli(scheda, demand, stores=None):
    """系统建议的箱数：{门店号: {行号: 箱数}}"""
    out = {}
    for sid in (stores or scheda["sheets"].keys()):
        if sid not in scheda["sheets"]:
            continue
        dem = demand.get(sid, {})
        lines = {}
        for it in scheda["items"]:
            n = colli_for(dem.get(it.get("db_code") or it["code"]), it["pack"], demand.get("_algo"))
            if n > 0:
                lines[it["row"]] = n
        out[sid] = lines
    return out


def _noop(*a, **k):
    pass


def _satisfied(colli, amount, moq_colli, moq_amount):
    ok = True
    if moq_colli:
        ok = ok and colli >= moq_colli - 1e-9
    if moq_amount:
        ok = ok and amount >= moq_amount - 1e-9
    return ok


def _totals(lines, items_by_row):
    c = a = 0.0
    for row, n in lines.items():
        it = items_by_row[row]
        c += n
        a += n * it["pack"] * it["price"]
    return c, a


def _try_fill(lines, scheda, dem_rows, fill):
    """
    在差得不多时凑单：优先给卖得快的商品多加箱，加完后库存不超过 max_stock_days 天。
    dem_rows: {行号: {"daily", "inv"}}（BMS 时为几家店合计）
    返回 (新的 lines, 加了哪些 [(行号, 箱数, 金额)]) 或 None（差太多 / 凑不到）
    """
    items = {it["row"]: it for it in scheda["items"]}
    mc, ma = scheda["moq_colli"], scheda["moq_amount"]
    c, a = _totals(lines, items)
    gap_c = max(0.0, mc - c) if mc else 0.0
    gap_a = max(0.0, ma - a) if ma else 0.0
    if gap_c == 0 and gap_a == 0:
        return lines, []
    if (mc and gap_c > fill["max_colli_gap"]) or (ma and gap_a > fill["max_amount_gap"]):
        return None
    lines = dict(lines)
    added = []
    max_days = float(fill["max_stock_days"])
    for _ in range(500):
        c, a = _totals(lines, items)
        gap_c = max(0.0, mc - c) if mc else 0.0
        gap_a = max(0.0, ma - a) if ma else 0.0
        if gap_c <= 1e-9 and gap_a <= 1e-9:
            break
        cands = []
        for row, it in items.items():
            d = dem_rows.get(row)
            if not d or d["daily"] <= 0 or it["price"] <= 0:
                continue
            have = max(0.0, d["inv"]) + (lines.get(row, 0) + 1) * it["pack"]
            days_after = have / d["daily"]
            if days_after > max_days:
                continue
            box = it["pack"] * it["price"]
            cands.append((days_after, box, row))
        if not cands:
            return None
        if gap_a > 1e-9 and gap_c <= 1e-9:
            # 只差金额：先找一箱就够、而且多出来最少的；没有就加卖得最快的
            enough = [x for x in cands if x[1] >= gap_a - 1e-9]
            pick = min(enough, key=lambda x: (x[1], x[0])) if enough else min(cands)
        else:
            pick = min(cands)   # 差箱数：加卖得最快的
        _, box, row = pick
        lines[row] = lines.get(row, 0) + 1
        added.append((row, 1, box))
    else:
        return None
    merged = {}
    for row, n, box in added:
        m = merged.setdefault(row, [0, 0.0])
        m[0] += n
        m[1] += box
    return lines, [(r, v[0], v[1]) for r, v in merged.items()]


_URGENT = "🔴"


def _stock_days(d):
    """现有库存够卖几天（负库存当 0；没销量 = 无穷大）"""
    if not d:
        return float("inf")
    if d.get("daily", 0) <= 0:
        return float("inf")
    return max(0.0, d.get("inv", 0)) / d["daily"]


def _urgency_rank(d):
    """越大越不紧急：没判断/观察 3，🟢 2，🟡 1，🔴 0"""
    dec = (d or {}).get("decision") or ""
    for mark, r in (("🔴", 0), ("🟡", 1), ("🟢", 2)):
        if mark in dec:
            return r
    return 3


def _bms_state(scheda, res, members, lent, items):
    """members 整单并进 BMS 的门店；lent {门店: {行: 箱}} 从直接送店门店借来的货"""
    lines, dem_rows, per_store = {}, {}, {}
    for sid in members:
        st = res["stores"][sid]
        src = st.get("base_lines", st["lines"])
        per_store[sid] = dict(src)
        for row, n in src.items():
            lines[row] = lines.get(row, 0) + n
    for sid, rows in lent.items():
        ps = per_store.setdefault(sid, {})
        for row, n in rows.items():
            if n > 0:
                lines[row] = lines.get(row, 0) + n
                ps[row] = ps.get(row, 0) + n
    for sid in set(members) | set(lent):
        for row, d in res["stores"][sid]["dem_rows"].items():
            x = dem_rows.setdefault(row, {"daily": 0.0, "inv": 0.0})
            x["daily"] += d["daily"]
            x["inv"] += max(0.0, d["inv"])
    return lines, dem_rows, per_store


def _borrow(scheda, res, members, lent, fill, items):
    """
    从同城、直接送店的门店一箱一箱借不紧急的货给 BMS，直到 BMS 够起订量；借完那家店自己还要够起订量。
    返回 True = 借够了
    """
    mc, ma = scheda["moq_colli"], scheda["moq_amount"]
    far = set(fill.get("far_stores") or [])
    donors = [sid for sid, st in res["stores"].items()
              if st["status"] in ("direct", "filled") and sid not in far and sid not in members]
    remain = {sid: dict(res["stores"][sid]["lines"]) for sid in donors}
    min_days = float(fill.get("borrow_min_days", 0) or 0)
    for _ in range(2000):
        lines, _, _ = _bms_state(scheda, res, members, lent, items)
        c, a = _totals(lines, items)
        if _satisfied(c, a, mc, ma):
            return True
        gap_a = max(0.0, ma - a) if ma else 0.0
        gap_c = max(0.0, mc - c) if mc else 0.0
        cands = []
        for sid in donors:
            dc, da = _totals(remain[sid], items)
            for row, n in remain[sid].items():
                if n <= 0:
                    continue
                it = items[row]
                d = res["stores"][sid]["dem_rows"].get(row)
                if _URGENT in ((d or {}).get("decision") or ""):
                    continue
                days = _stock_days(d)
                if days < min_days:
                    continue
                box = it["pack"] * it["price"]
                if not _satisfied(dc - 1, da - box, mc, ma):
                    continue     # 借了这家店自己就不够起订量了
                cands.append((_urgency_rank(d), days, box, sid, row))
        if not cands:
            return False
        if gap_c <= 1e-9 and gap_a > 1e-9:
            enough = [x for x in cands if x[2] >= gap_a - 1e-9]
            if enough:
                # 一箱就够：在最不紧急的那一档里挑多出来最少的
                top = max(x[0] for x in enough)
                pick = min((x for x in enough if x[0] == top), key=lambda x: (x[2], -x[1]))
            else:
                pick = max(cands, key=lambda x: (x[0], x[1], x[2]))
        else:
            pick = max(cands, key=lambda x: (x[0], x[1], -x[2]))
        _, _, _, sid, row = pick
        remain[sid][row] -= 1
        lent.setdefault(sid, {})[row] = lent.get(sid, {}).get(row, 0) + 1
    return False


PLAN_MODES = ("auto", "all_bms", "direct_only")


def bms_ordered(b):
    """这张 BMS 单会不会真的下（不够起订量的不下，除非手动强制并入）"""
    return bool(b) and b.get("status") in ("direct", "filled", "forced")


def plan_supplier(scheda, demand, fill=None, stores=None, colli=None, mode="auto"):
    """
    一个供应商的配货方案。
    返回 {"stores": {门店号: {status, colli, amount, lines, added, lent}}, "bms": {...} 或 None}
      status: "direct" 直接送店 / "filled" 凑单后直接送店 / "bms" 并入 BMS /
              "merged" 和 BMS 同仓、整单并进 BMS / "none" 不用订
      lent: 借给 BMS 的货 [(行号, 箱数, 金额)]（这家店仍直接送店，这部分由 BMS 订、到货后分回来）
    BMS 不够起订量时依次尝试：① 同仓门店整单并入 ② 从同城门店借不紧急的货 ③ 凑单；
    试完能订得起来才采用，否则维持「BMS 仍不够起订量」（这张 BMS 单不下）。
    mode（配货结果页手动选）：
      "auto"        系统建议（上面的规则）
      "all_bms"     所有要订的门店整单并进 BMS（就算有的店自己够起订量）；BMS 不够起订量也照样下单（status "forced"）
      "direct_only" 只订自己够起订量（含凑单后够）的门店，其他门店这次不订（status "dropped"），不借货、不出 BMS 单
    """
    fill = dict(DEFAULT_FILL, **(fill or {}))
    stores = stores or list(scheda["sheets"].keys())
    items = {it["row"]: it for it in scheda["items"]}
    mc, ma = scheda["moq_colli"], scheda["moq_amount"]
    res = {"stores": {}, "bms": None}
    to_bms = []
    for sid in stores:
        if sid not in scheda["sheets"]:
            continue
        dem = demand.get(sid, {})
        lines, dem_rows = {}, {}
        for it in scheda["items"]:
            d = dem.get(it.get("db_code") or it["code"])
            if d:
                dem_rows[it["row"]] = d
            if colli is None:
                n = colli_for(d, it["pack"], demand.get("_algo"))
                if n > 0:
                    lines[it["row"]] = n
            else:
                n = (colli.get(sid) or {}).get(it["row"], 0)
                if n > 0:
                    lines[it["row"]] = n
        c, a = _totals(lines, items)
        st = {"colli": c, "amount": a, "lines": lines, "base_lines": dict(lines), "added": [], "lent": [],
              "dem_rows": dem_rows, "before_colli": c, "before_amount": a}
        if c == 0:
            st["status"] = "none"
        elif _satisfied(c, a, mc, ma):
            st["status"] = "direct"
        else:
            r = _try_fill(lines, scheda, dem_rows, fill)
            if r is not None:
                st["lines"], st["added"] = r
                st["colli"], st["amount"] = _totals(st["lines"], items)
                st["status"] = "filled"
            else:
                st["status"] = "bms"
                to_bms.append(sid)
        res["stores"][sid] = st
    res["mode"] = mode
    if mode == "direct_only":
        for sid in to_bms:
            res["stores"][sid]["status"] = "dropped"
        return res
    if mode == "all_bms":
        to_bms = [sid for sid, st in res["stores"].items() if st["status"] != "none"]
        for sid in to_bms:
            st = res["stores"][sid]
            st["status"] = "bms"
            st["lines"], st["added"] = dict(st["base_lines"]), []
            st["colli"], st["amount"] = _totals(st["lines"], items)
    if not to_bms:
        return res

    def evaluate(members, lent):
        lines, dem_rows, per_store = _bms_state(scheda, res, members, lent, items)
        c, a = _totals(lines, items)
        if _satisfied(c, a, mc, ma):
            return "direct", lines, [], per_store
        r = _try_fill(lines, scheda, dem_rows, fill) if fill.get("fill_bms", True) else None
        if r is not None:
            return "filled", r[0], r[1], per_store
        return "short", lines, [], per_store

    members, lent, how = list(to_bms), {}, []
    status, lines, added, per_store = evaluate(members, lent)
    if status == "short" and mode != "all_bms":
        # ① 同仓门店整单并进 BMS
        colo = fill.get("colocated_store")
        trial_members = list(members)
        if fill.get("merge_colocated", True) and colo in res["stores"] and colo not in members and \
                res["stores"][colo]["status"] in ("direct", "filled"):
            trial_members.append(colo)
        trial_lent = {}
        ok = False
        if trial_members != members:
            s2 = evaluate(trial_members, trial_lent)
            ok = s2[0] != "short"
        # ② 从同城门店借不紧急的货
        if not ok and fill.get("borrow", True):
            got = _borrow(scheda, res, trial_members, trial_lent, fill, items)
            s2 = evaluate(trial_members, trial_lent) if (got or trial_lent) else None
            ok = bool(s2) and s2[0] != "short"
        if ok:
            if colo in trial_members and colo not in members:
                how.append("merged")
                st = res["stores"][colo]
                st["status"] = "merged"
                st["lines"], st["added"] = dict(st["base_lines"]), []
                st["colli"], st["amount"] = _totals(st["lines"], items)
            if trial_lent:
                how.append("borrow")
            members, lent = trial_members, trial_lent
            status, lines, added, per_store = s2
    # 借出的门店：直接送店的箱数减掉借出去的
    for sid, rows in lent.items():
        st = res["stores"][sid]
        for row, n in rows.items():
            st["lines"][row] = st["lines"].get(row, 0) - n
            if st["lines"][row] <= 0:
                st["lines"].pop(row)
        st["lent"] = [(row, n, n * items[row]["pack"] * items[row]["price"]) for row, n in rows.items() if n > 0]
        st["colli"], st["amount"] = _totals(st["lines"], items)
    c, a = _totals(lines, items)
    b0c, b0a = _totals(_bms_state(scheda, res, to_bms, {}, items)[0], items)
    if mode == "all_bms":
        how = ["manual"]
        if status == "short":
            status = "forced"
    b = {"stores": members, "lenders": {k: v for k, v in lent.items() if v}, "colli": c, "amount": a,
         "lines": lines, "added": added, "status": status, "how": how, "before_colli": b0c, "before_amount": b0a}
    # 凑单加的箱放在 BMS 里这个商品卖得最快的那家店
    involved = list(per_store.keys())
    for row, n, _ in added:
        best = max(involved, key=lambda s_: res["stores"][s_]["dem_rows"].get(row, {}).get("daily", 0))
        per_store.setdefault(best, {})[row] = per_store.get(best, {}).get(row, 0) + n
    b["per_store"] = per_store
    res["bms"] = b
    return res


# =====================================================================
# 写订单文件（只改各门店页 Colli 列的单元格，其余原样保留：图片、格式、公式）
# =====================================================================
def _col_letter(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _col_num(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def _sheet_paths(z):
    wb = z.read("xl/workbook.xml").decode("utf-8")
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    rid2t = {}
    for m in re.finditer(r"<Relationship\b[^>]*>", rels):
        tag = m.group(0)
        i = re.search(r'\bId="([^"]+)"', tag)
        t = re.search(r'\bTarget="([^"]+)"', tag)
        if i and t:
            tgt = t.group(1)
            tgt = tgt[1:] if tgt.startswith("/") else "xl/" + tgt
            rid2t[i.group(1)] = tgt
    out = {}
    for m in re.finditer(r"<sheet\b[^>]*>", wb):
        tag = m.group(0)
        name = re.search(r'\bname="([^"]*)"', tag)
        rid = re.search(r'\br:id="([^"]+)"', tag)
        if name and rid and rid.group(1) in rid2t:
            import html
            out[html.unescape(name.group(1))] = rid2t[rid.group(1)]
    return out


def _set_cells(xml, col_letter, values):
    """values: {行号: 数字或 None}；None = 清空（保留格式）"""
    for row, val in values.items():
        ref = f"{col_letter}{row}"
        cell_re = re.compile(r'<c\b[^>]*\br="' + ref + r'"[^>]*?(?:/>|>.*?</c>)', re.S)
        m = cell_re.search(xml)
        style = ""
        if m:
            s = re.search(r'\bs="(\d+)"', m.group(0).split(">")[0])
            style = f' s="{s.group(1)}"' if s else ""
        new = f'<c r="{ref}"{style}><v>{val:g}</v></c>' if val not in (None, 0) else f'<c r="{ref}"{style}/>'
        if m:
            xml = xml[:m.start()] + new + xml[m.end():]
            continue
        if val in (None, 0):
            continue
        row_re = re.compile(r'<row\b[^>]*\br="' + str(row) + r'"[^>]*?(/>|>(.*?)</row>)', re.S)
        rm = row_re.search(xml)
        if not rm:
            continue
        if rm.group(1) == "/>":
            open_tag = rm.group(0)[:-2] + ">"
            xml = xml[:rm.start()] + open_tag + new + "</row>" + xml[rm.end():]
            continue
        inner = rm.group(2)
        tcol = _col_num(col_letter)
        pos = len(inner)
        for cm in re.finditer(r'<c\b[^>]*\br="([A-Z]+)\d+"', inner):
            if _col_num(cm.group(1)) > tcol:
                pos = cm.start()
                break
        start_inner = rm.start(2)
        xml = xml[:start_inner + pos] + new + xml[start_inner + pos:]
    return xml


# =====================================================================
# 订单文件里每家店页附加的数据列（和原程序「中央采购」批量写回 SCHEDA 的列一样：L~AC）
# =====================================================================
def extra_headers(days=None):
    d = list(days or [30, 60, 90, 180])
    return (["GIA"] + [f"ROT.{x}d" for x in d[:4]] + ["月均", "采购量", "箱数", "需求", "Livello"] +
            [f"GIA.{i}" for i in range(6)] + ["GIA-period", "period_sales"])


def extra_fields(x, sugg_colli, days=None):
    """
    一个商品在一家店的附加数据（顺序同 extra_headers）；数据库里没有这个商品 → None（那一行留空）
      GIA 本店库存（原值）· ROT 各周期销量 · 月均 = 最长周期销量 ÷ 月数 · 采购量 = 系统建议件数 · 箱数 = 系统建议箱数
      需求 = 第 3 个周期（默认 90 天）的日均 × 订货周期 · Livello = 等级 · GIA.0~5 各门店库存（0 = BMS 总仓）
      period_sales = 第 3 个周期的销量 · GIA-period = 本店库存（负数按 0）− period_sales
    """
    if not x:
        return None
    d = list(days or [30, 60, 90, 180])
    sales = [float(v or 0) for v in (x.get("sales") or [0, 0, 0, 0])][:4] + [0.0] * max(0, 4 - len(x.get("sales") or []))
    inv = float(x.get("inv_raw", x.get("inv", 0)) or 0)
    months = max(1.0, d[3] / 30.0)
    cyc = x.get("cycle") if isinstance(x.get("cycle"), (int, float)) else 30
    period = sales[2]
    demand = round(period / d[2] * float(cyc)) if d[2] else 0
    gia = [float(v or 0) for v in (x.get("gia_all") or [0] * 6)][:6]
    gia += [0.0] * (6 - len(gia))
    return ([inv] + sales + [round(sales[3] / months, 1), float(x.get("sugg") or 0), float(sugg_colli or 0),
                             float(demand), x.get("tier") or ""] + gia + [max(0.0, inv) - period, period])


def extra_data(sc, demand, base, days=None):
    """{门店号: {SCHEDA 行号: [附加列的值]}}，写订单文件用"""
    out = {}
    for sid in sc["sheets"]:
        dem = (demand or {}).get(sid)
        if not dem:
            continue
        sug = ((base or {}).get(sc["path"]) or {}).get(sid) or {}
        rows = {}
        for it in sc["items"]:
            v = extra_fields(dem.get(it.get("db_code") or it["code"]), sug.get(it["row"], 0), days)
            if v is not None:
                rows[it["row"]] = v
        out[sid] = rows
    return out


def _cell_style(xml, ref):
    m = re.search(r'<c\b[^>]*\br="' + ref + r'"[^>]*?(?:/>|>)', xml)
    if not m:
        return ""
    s_ = re.search(r'\bs="(\d+)"', m.group(0))
    return f' s="{s_.group(1)}"' if s_ else ""


def _cell_xml(ref, val, style):
    import html
    if val is None or val == "":
        return f'<c r="{ref}"{style}/>'
    if isinstance(val, str):
        return f'<c r="{ref}"{style} t="inlineStr"><is><t>{html.escape(val)}</t></is></c>'
    v = float(val)
    return f'<c r="{ref}"{style}><v>{int(v) if v == int(v) else round(v, 4)}</v></c>'


_ROW_RE = re.compile(r'<row\b([^>]*?)(/>|>(.*?)</row>)', re.S)
_CELL_RE = re.compile(r'<c\b[^>]*?\br="([A-Z]+)(\d+)"[^>]*?(?:/>|>.*?</c>)', re.S)


def _set_row_cells(xml, values, styles=None):
    """
    一次过把很多格写进工作表 XML（比一格一格搜快得多）。
    values: {行号: {列号: 值}}（值：数字 / 文字 / None = 清空）；styles: {(行号, 列号): ' s="n"'}
    行不存在时不写（数据行一定存在）。
    """
    styles = styles or {}

    def fix(m):
        attrs, inner = m.group(1), m.group(3) or ""
        rn = re.search(r'\br="(\d+)"', attrs)
        if not rn or int(rn.group(1)) not in values:
            return m.group(0)
        r = int(rn.group(1))
        cells = {}
        order = []
        for cm in _CELL_RE.finditer(inner):
            c = _col_num(cm.group(1))
            cells[c] = cm.group(0)
            order.append(c)
        for c, val in values[r].items():
            ref = f"{_col_letter(c)}{r}"
            st = styles.get((r, c))
            if st is None:
                st = _cell_style(cells[c], ref) if c in cells else ""
            cells[c] = _cell_xml(ref, val, st)
        body = "".join(cells[c] for c in sorted(cells))
        attrs = re.sub(r'\s*\bspans="[^"]*"', "", attrs)     # 列范围变了，spans 去掉让 Excel 自己算
        return f"<row{attrs}>{body}</row>"

    xml = _ROW_RE.sub(fix, xml)
    # 表格范围（dimension）放大到新写的列
    last = max((c for r in values.values() for c in r), default=0)
    dm = re.search(r'<dimension ref="([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?"', xml)
    if dm and dm.group(3) and _col_num(dm.group(3)) < last:
        xml = xml[:dm.start()] + f'<dimension ref="{dm.group(1)}{dm.group(2)}:{_col_letter(last)}{dm.group(4)}"' + xml[dm.end():]
    return xml


def _write_extra(xml, scheda, rows_vals, headers, row_map):
    """一家店页：表头 + 每个商品行的附加数据。row_map：SCHEDA 第一页的行号 → 这一页的行号（None = 这一页没有）"""
    hdr = scheda["header_row"]
    start = scheda.get("extra_col") or 12
    cols = scheda["cols"]
    hstyle = _cell_style(xml, f"{_col_letter(cols['colli'])}{hdr}")
    first = min((it["row"] for it in scheda["items"]), default=hdr + 1)
    vcol = cols.get("code") or cols.get("ean") or cols["colli"]
    vstyle = _cell_style(xml, f"{_col_letter(vcol)}{first}")
    values = {hdr: {start + i: h for i, h in enumerate(headers)}}
    styles = {(hdr, start + i): hstyle for i in range(len(headers))}
    for it in scheda["items"]:
        r = row_map.get(it["row"], it["row"])
        if r is None:
            continue
        vals = rows_vals.get(it["row"])
        values[r] = {start + i: (vals[i] if vals else None) for i in range(len(headers))}
        for i in range(len(headers)):
            styles[(r, start + i)] = vstyle
    return _set_row_cells(xml, values, styles)


def write_order_file(scheda, per_store_lines, out_path, extra=None, headers=None):
    """
    复制 SCHEDA → out_path，各门店页 Colli 列：per_store_lines 里有的门店写箱数，其余门店清空。
    extra：{门店号: {行号: [附加列的值]}}（extra_data），有的话在各门店页 IMPORTO 后面加 GIA、ROT.30d … 这些列。
    打开时让 Excel / WPS 重新计算合计。
    """
    colli_col = _col_letter(scheda["cols"]["colli"])
    rows = [it["row"] for it in scheda["items"]]
    tmp = out_path + ".tmp"
    with zipfile.ZipFile(scheda["path"]) as zin:
        paths = _sheet_paths(zin)
        targets, extra_by_path = {}, {}
        for sid, sheet in scheda["sheets"].items():
            p = paths.get(sheet)
            if p:
                lines = per_store_lines.get(sid, {})
                rm = scheda.get("rowmap", {}).get(sid, {})
                cells = {}
                for r in rows:
                    tr = rm.get(r, r)
                    if tr is not None:
                        cells[tr] = lines.get(r)
                targets[p] = cells
                if extra and sid in extra:
                    extra_by_path[p] = (extra[sid], rm)
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename in targets:
                    x = _set_cells(data.decode("utf-8"), colli_col, targets[item.filename])
                    if item.filename in extra_by_path:
                        vals, rm = extra_by_path[item.filename]
                        x = _write_extra(x, scheda, vals, headers or extra_headers(), rm)
                    data = x.encode("utf-8")
                elif item.filename == "xl/workbook.xml":
                    wbx = data.decode("utf-8")
                    if "<calcPr" in wbx:
                        if "fullCalcOnLoad" not in wbx:
                            wbx = re.sub(r"<calcPr\b", '<calcPr fullCalcOnLoad="1"', wbx, count=1)
                    else:
                        wbx = wbx.replace("</workbook>", '<calcPr fullCalcOnLoad="1"/></workbook>')
                    data = wbx.encode("utf-8")
                elif item.filename == "xl/calcChain.xml":
                    pass
                zout.writestr(item, data)
    os.replace(tmp, out_path)
    return out_path


def written_source(w, result=None):
    """生成的文件是哪个 SCHEDA 的：新版本记在第 7 项；老结果（只有 6 项）按「星期文件夹 + 供应商名」找回来"""
    if len(w) > 6:
        return w[6]
    day = os.path.basename(os.path.dirname(w[0]))
    for d, sc, _ in (result or {}).get("results", []):
        if sc["name"] == w[1] and d == day:
            return sc["path"]
    return None


def write_plans(results, out_root, date_tag=None, on_file=None, calc=None):
    """
    results: [(星期, scheda, plan)]。每家直接送店的门店一个文件，BMS 一个文件。
    calc: compute() 的结果；有的话各门店页加上 GIA、ROT.30d … 附加数据列
    返回 [(文件路径, 供应商, 去向, 箱数, 金额, 状态)]
    """
    date_tag = date_tag or datetime.now().strftime("%d%m")
    written = []
    total = sum(sum(1 for st in plan["stores"].values() if st["status"] in ("direct", "filled")) +
                (1 if bms_ordered(plan.get("bms")) else 0) for _, _, plan in results)
    on_file = on_file or _noop
    for day, sc, plan in results:
        folder = os.path.join(out_root, day)
        os.makedirs(folder, exist_ok=True)
        safe = re.sub(r'[\\/:*?"<>|]', "_", sc["name"])
        extra = extra_data(sc, calc.get("demand"), calc.get("base"), calc.get("days")) if calc else None
        heads = extra_headers(calc.get("days")) if calc else None
        for sid, st in plan["stores"].items():
            if st["status"] in ("direct", "filled"):
                p = os.path.join(folder, f"Ordine_{safe}_{STORE_SHEETS[sid]}_{date_tag}.xlsx")
                on_file(len(written), total, os.path.basename(p))
                write_order_file(sc, {sid: st["lines"]}, p, extra, heads)
                written.append((p, sc["name"], STORE_SHEETS[sid], st["colli"], st["amount"], st["status"], sc["path"]))
        b = plan.get("bms")
        if bms_ordered(b):        # BMS 不够起订量的不生成文件（这次不订）
            p = os.path.join(folder, f"Ordine_{safe}_{BMS}_{date_tag}.xlsx")
            on_file(len(written), total, os.path.basename(p))
            write_order_file(sc, b["per_store"], p, extra, heads)
            written.append((p, sc["name"], BMS, b["colli"], b["amount"], b["status"], sc["path"]))
    return written


STATUS_TEXT = {"direct": "直接送店", "filled": "凑单后送店", "bms": "并入 BMS", "none": "不用订",
               "short": "BMS 仍不够起订量", "merged": "并入 BMS（同一仓库）", "dropped": "不订（手动）"}
# 去向是 BMS 的那一行：由 BMS 订货，到货后仓库分给各门店
BMS_STATUS_TEXT = {"direct": "BMS 分货", "filled": "凑单后 BMS 分货", "short": "BMS 不够起订量 · 不订",
                   "forced": "手动并入 BMS（未达起订量）"}
OUTPUT_FOLDER = "生成订单"


def status_text(dest, status):
    return BMS_STATUS_TEXT.get(status, STATUS_TEXT.get(status, status)) if dest == BMS else STATUS_TEXT.get(status, status)


def output_root(root, when=None):
    """当天的日期文件夹：采购配单/生成订单/YYYYMMDD/（每次配货在里面再建 第NN次_HHMM/星期/）"""
    return os.path.join(root, OUTPUT_FOLDER, (when or datetime.now()).strftime("%Y%m%d"))


RUN_RE = re.compile(r"^第(\d+)次_")


def new_run_folder(date_dir, when=None):
    """在日期文件夹里新建这一次配货的子文件夹「第NN次_HHMM」，编号接着已有的往下排，不覆盖以前的文件"""
    when = when or datetime.now()
    n = 0
    if os.path.isdir(date_dir):
        for name in os.listdir(date_dir):
            m = RUN_RE.match(name)
            if m and os.path.isdir(os.path.join(date_dir, name)):
                n = max(n, int(m.group(1)))
    while True:
        n += 1
        path = os.path.join(date_dir, f"第{n:02d}次_{when:%H%M}")
        if not os.path.exists(path):
            os.makedirs(path)
            return path


def _fmt_days(v):
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return v
    return "∞" if math.isinf(v) else round(v, 1)


def plan_totals(results):
    """采购金额：{"direct": 直接送店合计, "bms": BMS 合计, "total", "by_day": {星期: 金额}, "by_store": {去向: 金额}}"""
    out = {"direct": 0.0, "bms": 0.0, "total": 0.0, "by_day": {}, "by_store": {}, "direct_colli": 0.0, "bms_colli": 0.0}
    for day, sc, plan in results:
        for sid, st in plan["stores"].items():
            if st["status"] in ("direct", "filled"):
                out["direct"] += st["amount"]
                out["direct_colli"] += st["colli"]
                out["by_day"][day] = out["by_day"].get(day, 0.0) + st["amount"]
                k = STORE_SHEETS[sid]
                out["by_store"][k] = out["by_store"].get(k, 0.0) + st["amount"]
        b = plan.get("bms")
        if bms_ordered(b):
            out["bms"] += b["amount"]
            out["bms_colli"] += b["colli"]
            out["by_day"][day] = out["by_day"].get(day, 0.0) + b["amount"]
            out["by_store"][BMS] = out["by_store"].get(BMS, 0.0) + b["amount"]
    out["total"] = out["direct"] + out["bms"]
    return out


def bms_note(b):
    """BMS 这一单是怎么凑起来的（给界面和汇总表用）"""
    parts = ["合并：" + "、".join(STORE_SHEETS[s] for s in b["stores"])]
    if "merged" in b.get("how", []):
        parts.append("同仓门店整单并入")
    if "manual" in b.get("how", []):
        parts.append("手动选择全部并入 BMS")
    if b.get("lenders"):
        parts.append("借货：" + "、".join(f"{STORE_SHEETS[s]} {sum(r.values()):g} 箱" for s, r in b["lenders"].items()))
    return "；".join(parts)


def export_summary(results, path, demand=None, base=None, edited=None, days_list=None):
    """
    配货汇总：第一页每个供应商 × 去向（含采购金额合计）；第二页订货明细（每个商品 × 门店 × 去向）。
    base: {路径: {门店号: {行号: 建议箱数}}}；edited: {(路径, 门店号, 行号)}
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "配货汇总"
    ws.append(["星期", "供应商", "起订条件", "去向", "箱数", "金额 €", "状态", "凑单加了", "凑单金额 €",
               "借给 BMS 箱数", "借给 BMS 金额 €", "说明"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for day, sc, plan in results:
        cond = moq_text(sc_moq(sc))
        for sid, st in plan["stores"].items():
            ws.append([day, sc["name"], cond, STORE_SHEETS[sid], st["colli"], round(st["amount"], 2),
                       status_text(STORE_SHEETS[sid], st["status"]), sum(n for _, n, _ in st["added"]) or None,
                       round(sum(v for _, _, v in st["added"]), 2) or None,
                       sum(n for _, n, _ in st["lent"]) or None, round(sum(v for _, _, v in st["lent"]), 2) or None, None])
        b = plan.get("bms")
        if b:
            ws.append([day, sc["name"], cond, BMS, b["colli"] if bms_ordered(b) else 0,
                       round(b["amount"], 2) if bms_ordered(b) else 0, status_text(BMS, b["status"]),
                       sum(n for _, n, _ in b["added"]) or None, round(sum(v for _, _, v in b["added"]), 2) or None,
                       None, None, bms_note(b)])
    t = plan_totals(results)
    ws.append([])
    bold = Font(bold=True)
    for label, colli, amt in (("直接送店合计", t["direct_colli"], t["direct"]), ("BMS 合计", t["bms_colli"], t["bms"]),
                              ("采购总额", t["direct_colli"] + t["bms_colli"], t["total"])):
        ws.append(["", "", "", label, colli, round(amt, 2)])
        for c in ws[ws.max_row]:
            c.font = bold
    if len(t["by_day"]) > 1:
        for day, amt in t["by_day"].items():
            ws.append(["", "", "", f"{day} 合计", None, round(amt, 2)])
    ws.freeze_panes = "A2"
    for col, w in zip("ABCDEFGHIJKL", (6, 26, 14, 14, 8, 11, 18, 9, 10, 11, 12, 40)):
        ws.column_dimensions[col].width = w

    d = list(days_list or [30, 60, 90, 180])
    ws2 = wb.create_sheet("订货明细")
    ws2.append(["星期", "供应商", "门店", "去向", "商品编号", "条码", "名称", "每箱件数", "件价", "本店库存",
                *[f"{x}天销量" for x in d], "库存天数", "判断", "建议件数", "建议箱数", "订货箱数", "金额 €", "备注"])
    for c in ws2[1]:
        c.font = bold
    edited = edited or set()
    for day, sc, plan in results:
        b = plan.get("bms") if bms_ordered(plan.get("bms")) else None
        bms_lines = (b or {}).get("per_store", {})
        bms_added = {r for r, _, _ in (b or {}).get("added", [])}
        for sid, st in plan["stores"].items():
            direct = st["lines"] if st["status"] in ("direct", "filled") else {}
            via_bms = bms_lines.get(sid, {})
            lent = {r for r, _, _ in st["lent"]}
            sug = (base or {}).get(sc["path"], {}).get(sid) or {}
            dem = (demand or {}).get(sid, {})
            added = {r for r, _, _ in (st["added"] or [])}
            for it in sc["items"]:
                x = dem.get(it.get("db_code") or it["code"])
                sg = sug.get(it["row"], 0)
                parts = []
                if direct.get(it["row"]):
                    parts.append((STORE_SHEETS[sid], direct[it["row"]], it["row"] in added, False))
                if via_bms.get(it["row"]):
                    parts.append((BMS, via_bms[it["row"]], it["row"] in bms_added, it["row"] in lent))
                if not parts and sg:
                    parts.append((STORE_SHEETS[sid], 0, False, False))
                for dest, n, is_fill, is_lent in parts:
                    note = []
                    if (sc["path"], sid, it["row"]) in edited:
                        note.append("手动改")
                    if is_fill:
                        note.append("凑单")
                    if is_lent:
                        note.append("借给 BMS")
                    if st["status"] == "merged" and dest == BMS:
                        note.append("同仓并入")
                    ws2.append([day, sc["name"], STORE_SHEETS[sid], dest, it.get("db_code") or it["code"], it["ean"],
                                it["desc"], it["pack"], it["price"], x.get("inv_raw", x["inv"]) if x else None,
                                *((x["sales"] if x else [None] * 4)), _fmt_days(x["inv_days"]) if x else None,
                                x["decision"] if x else "数据库没有", x["sugg"] if x else None, sg or None, n or None,
                                round(n * it["pack"] * it["price"], 2) if n else None, "、".join(note)])
                    ws2.cell(ws2.max_row, 6).number_format = "@"
    ws2.freeze_panes = "A2"
    for col, w in zip("ABCDEFGHIJKLMNOPQRSTUV", (6, 22, 12, 12, 10, 15, 40, 8, 8, 8, 8, 8, 8, 8, 8, 24, 8, 8, 8, 10, 16)):
        ws2.column_dimensions[col].width = w
    wb.save(path)
    return path


# =====================================================================
# 界面后台线程调用：① 计算（读 SCHEDA + 查数据库） ② 配货（判断起订量 / 凑单 / BMS + 写文件）
# =====================================================================
COMPUTE_STEPS = ["读取供应商清单", "查询门店销量与库存", "计算建议箱数"]
ALLOCATE_STEPS = ["判断起订量、凑单和 BMS", "生成订单文件", "生成汇总表"]


def compute(db, schede_paths, stores, days_list, reorder_cycle, log=print, use_supplier_cycles=True, algo=None,
            progress=None, cancel=None, moq_overrides=None):
    """
    返回 {"schede", "demand", "base", "missing", "errors"}；base = {路径: {门店号: {行号: 建议箱数}}}
    progress(步骤下标, 状态, 计数, 说明, 总百分比)；cancel() 返回 True 时抛出取消
    """
    progress = progress or _noop
    check = cancel or (lambda: False)
    schede, errors = [], []
    n = max(1, len(schede_paths))
    for i, (day, p) in enumerate(schede_paths):
        if check():
            raise InterruptedError("cancelled")
        progress(0, "running", f"{i + 1} / {len(schede_paths)}", os.path.basename(p), 30.0 * i / n)
        try:
            sc = parse_scheda(p, moq_overrides)
            sc["day"] = day
            schede.append(sc)
            log(f"读取 {os.path.basename(p)}：{len(sc['items'])} 个商品，起订 {moq_text(sc_moq(sc))}")
            for w in sc["warnings"]:
                log(f"  ⚠ {w}")
        except Exception as e:
            errors.append((p, str(e)))
            log(f"✗ {os.path.basename(p)}：{e}")
    if not schede:
        raise ValueError("没有可用的 SCHEDA 文件")
    progress(0, "done", f"{len(schede)} / {len(schede_paths)}", "", 30.0)
    log(f"查询数据库：{len(stores)} 家门店 …")

    def on_store(k, sid):
        if check():
            raise InterruptedError("cancelled")
        progress(1, "running", f"{k + 1} / {len(stores)}", STORE_SHEETS.get(sid, str(sid)), 30.0 + 60.0 * k / max(1, len(stores)))

    demand = demand_by_store(db, schede, stores, days_list, reorder_cycle, use_supplier_cycles, algo, on_store)
    progress(1, "done", f"{len(stores)} / {len(stores)}", "", 90.0)
    progress(2, "running", "", "", 92.0)
    missing = demand.pop("_missing", [])
    if missing:
        log(f"⚠ {len(missing)} 个商品在数据库里找不到编号，跳过")
    base = {sc["path"]: base_colli(sc, demand, stores) for sc in schede}
    progress(2, "done", "", "", 100.0)
    return {"schede": schede, "demand": demand, "base": base, "missing": missing, "errors": errors,
            "stores": list(stores), "days": list(days_list)}


def allocate(calc, colli, fill, out_root, log=print, date_tag=None, edited=None, only=None, progress=None, modes=None):
    """
    calc: compute() 的结果；colli: {路径: {门店号: {行号: 箱数}}}（建议值或手动改过的值）。
    only: 只处理这些路径（None = 全部）。返回 {"results", "written", "summary", "out_root"}
    """
    progress = progress or _noop
    stores = calc["stores"]
    results = []
    todo = [sc for sc in calc["schede"] if only is None or sc["path"] in only]
    for i, sc in enumerate(todo):
        progress(0, "running", f"{i + 1} / {len(todo)}", sc["name"], 40.0 * i / max(1, len(todo)))
        plan = plan_supplier(sc, calc["demand"], fill, stores, colli.get(sc["path"], {}),
                             (modes or {}).get(sc["path"], "auto"))
        results.append((sc["day"], sc, plan))
        parts = [f"{STORE_SHEETS[s]}:{status_text(STORE_SHEETS[s], st['status'])}" for s, st in plan["stores"].items()
                 if st["status"] != "none"]
        if plan["bms"]:
            parts.append(f"BMS:{status_text(BMS, plan['bms']['status'])}")
        log(f"{sc['name']}：" + ("，".join(parts) if parts else "都不用订"))
    progress(0, "done", f"{len(todo)} / {len(todo)}", "", 40.0)
    os.makedirs(out_root, exist_ok=True)
    written = write_plans(results, out_root, date_tag,
                          lambda k, n, name: progress(1, "running", f"{k + 1} / {n}", name, 40.0 + 50.0 * k / max(1, n)),
                          calc=calc)
    progress(1, "done", f"{len(written)}", "", 90.0)
    progress(2, "running", "", "", 92.0)
    summary = os.path.join(out_root, f"配货汇总_{date_tag or datetime.now().strftime('%d%m')}.xlsx")
    export_summary(results, summary, calc["demand"], calc["base"], edited, calc.get("days"))
    progress(2, "done", "", "", 100.0)
    log(f"完成：生成 {len(written)} 个订单文件 → {out_root}")
    return {"results": results, "written": written, "summary": summary, "out_root": out_root}


def reallocate_one(calc, colli, fill, result, index, mode, edited=None, log=print):
    """
    配货结果页手动改一个供应商的去向：重新配这一家，删掉它在这次文件夹里的旧文件、写新文件，重做汇总表。
    只删这一个 SCHEDA（这个星期的这家供应商）的文件：同一家供应商在别的星期也有 SCHEDA 时不能动它的文件。
    """
    day, sc, _old = result["results"][index]
    plan = plan_supplier(sc, calc["demand"], fill, calc["stores"], colli.get(sc["path"], {}), mode)
    keep = []
    for w in result["written"]:
        if written_source(w, result) == sc["path"]:
            try:
                os.remove(w[0])
            except OSError:
                pass
        else:
            keep.append(w)
    date_tag = os.path.basename(result["summary"]).rsplit("_", 1)[-1].split(".")[0]
    new = write_plans([(day, sc, plan)], result["out_root"], date_tag, calc=calc)
    results = list(result["results"])
    results[index] = (day, sc, plan)
    order = {x[1]["path"]: k for k, x in enumerate(results)}
    written = sorted(keep + new, key=lambda w: order.get(written_source(w, result), 0))
    export_summary(results, result["summary"], calc["demand"], calc["base"], edited, calc.get("days"))
    log(f"手动调整 {sc['name']}：{mode}，生成 {len(new)} 个文件")
    return dict(result, results=results, written=written)


def run(db, schede_paths, stores, days_list, reorder_cycle, fill, out_root, log=print, date_tag=None):
    """计算 + 配货一次做完（不手动改）"""
    calc = compute(db, schede_paths, stores, days_list, reorder_cycle, log)
    res = allocate(calc, calc["base"], fill, out_root, log, date_tag)
    res.update(missing=calc["missing"], errors=calc["errors"])
    return res
