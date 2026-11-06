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
# 星期文件夹的其他写法（门店采购的 PDV 文件夹是「01 LUNEDI」…「05 VENERDI」，2026-11-03）
DAY_ALIASES = {"周一": ("LUNEDI", "LUNEDÌ", "MONDAY"), "周二": ("MARTEDI", "MARTEDÌ", "TUESDAY"),
               "周三": ("MERCOLEDI", "MERCOLEDÌ", "WEDNESDAY"), "周四": ("GIOVEDI", "GIOVEDÌ", "THURSDAY"),
               "周五": ("VENERDI", "VENERDÌ", "FRIDAY")}
# 门店采购（PDV）文件夹的默认位置（共享盘；设置里可以改）
PDV_DEFAULT_FOLDER = r"D:\SynologyDrive\SynologyDrive\2 Griglia per gli ordini\03 Griglia per gli ordini PDV"
# 门店采购（PDV）订单号里的门店简写：NUMERO ORDINE = 日月年-简写-01（SCHEDA 模板里的样子是 01032026-SF-01）
STORE_ABBR = {1: "FO", 2: "MA", 3: "SF", 4: "SM", 5: "SC"}
# Dati Filiali 页里认门店用的关键词（地址文字里有就算）
STORE_WORDS = {1: ("FOIANO",), 2: ("CAPANNORI", "MARLIA", "GROUP"), 3: ("FILIPPO",), 4: ("MARCO",), 5: ("CONCORDIO",)}

DEFAULT_FILL = {
    "max_amount_gap": 30.0,   # 离起订金额差多少欧以内才凑单
    "max_colli_gap": 3,       # 离起订箱数差几箱以内才凑单
    "max_stock_days": 60,     # 凑单加的货：加完后库存最多够卖几天
    "fill_bms": True,         # BMS 合并后不够起订量时也尝试凑单
    # BMS 不够起订量时的补救（按顺序试，能让 BMS 订得起来才用，否则保持原样）
    "merge_colocated": True,  # ① 和 BMS 同一个仓库的门店整单并进 BMS（不多跑一趟）
    "borrow": True,           # ② 从同城直接送店的门店借「不紧急」的货给 BMS（到货后由 BMS 分回去）
    "borrow_min_days": 7,     # 借货条件：这家店现有库存至少还够卖几天
    "borrow_urgent": True,    # ③ 不紧急的借完还不够：紧急的也借（只借这家店超过自己起订量的部分）
    "merge_direct": True,     # ④ 还不够：把够起订量的门店整单并进 BMS，让其他门店也订得起来
    "far_stores": [1],        # 不参与 BMS 凑单的门店（供应商设置里可以按供应商单独选）
    "colocated_store": 3,     # 和 BMS 仓库在一起的门店
    "bms_order": [3, 4, 5, 2, 1],   # 订不起来的门店按这个先后并进 BMS，够起订量就停
}
BMS_ORDER = [3, 4, 5, 2, 1]


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
    """'SCHEDA DOPLA 0110 .xlsx' → 'DOPLA'；'SCHEDA FHP(VILEDA) 2308.xlsx' → 'FHP(VILEDA)'；'SCHEDA SAIS 0726..xlsx' → 'SAIS'"""
    n = os.path.splitext(os.path.basename(path))[0]
    n = re.sub(r"^\s*SCHEDA\s+", "", n, flags=re.I)
    n = re.sub(r"\(\d{4}-\d{2}-\d{2}.*?\)", "", n)
    n = re.sub(r"\s+\d{4,6}[\s.]*$", "", n.strip())         # 「SCHEDA SAIS 0726..xlsx」后面多一个点也去掉
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


def day_dirs(root):
    """{星期（周一…周五）: 文件夹路径}：文件夹叫「周一」或者「01 LUNEDI」这种都认"""
    out = {}
    try:
        names = [n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n))]
    except OSError:
        return out
    for d in DAY_FOLDERS:
        if d in names:
            out[d] = os.path.join(root, d)
            continue
        for n in sorted(names):
            u = _norm(n).replace(" ", "")
            if any(a in u for a in DAY_ALIASES[d]):
                out[d] = os.path.join(root, n)
                break
    return out


def list_schede(root, day=None):
    """采购配单文件夹里的 SCHEDA 文件：[(星期, 路径)]"""
    out = []
    days = [day] if day else DAY_FOLDERS
    dirs = day_dirs(root)
    for d in days:
        folder = dirs.get(d)
        if not folder:
            continue
        for f in sorted(os.listdir(folder)):
            if f.lower().endswith((".xlsx", ".xlsm")) and not f.startswith("~$") and "SCHEDA" in f.upper():
                out.append((d, os.path.join(folder, f)))
    return out


def _ean_key(v):
    s = str(v or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s.lstrip("0") if s.isdigit() else ""


def scheda_index(root, cache_path=None, log=None):
    """
    品牌供应商（直接下单的供应商）的采购配单文件 SCHEDA 里有哪些商品（2026-11-01 用户：门店报告里写明非 Griglia 商品
    能不能直接向品牌供应商订）→ {"ean": {条码: 供应商名}, "code": {我们的编号: 供应商名}, "files": n}。
    读「采购配单文件夹」周一 – 周五所有 SCHEDA（root 可以是几个文件夹：总仓采购 + 门店采购）；每个文件按 路径 + 大小 + 修改时间
    缓存（cache_path，JSON），没改过不再打开
    """
    import json
    out = {"ean": {}, "code": {}, "files": 0}
    roots = [r for r in (root if isinstance(root, (list, tuple)) else [root]) if r and os.path.isdir(r)]
    if not roots:
        return out
    old = {}
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                old = json.load(f)
        except Exception:
            old = {}
    new = {}
    for _d, p in [x for r in roots for x in list_schede(r)]:
        try:
            st = os.stat(p)
        except OSError:
            continue
        key = f"{os.path.normcase(os.path.abspath(p))}|{st.st_size}|{st.st_mtime_ns}"
        ent = old.get(key)
        if ent is None:
            try:
                info = parse_scheda(p)
                ent = {"name": info["name"], "eans": sorted({_ean_key(i["ean"]) for i in info["items"]} - {""}),
                       "codes": sorted({str(i["code"]).strip() for i in info["items"]} - {""})}
            except Exception as e:
                if log:
                    log(f"⚠ {os.path.basename(p)}：{e}")
                continue
        new[key] = ent
        out["files"] += 1
        for e in ent["eans"]:
            out["ean"].setdefault(e, ent["name"])
        for c in ent["codes"]:
            out["code"].setdefault(c, ent["name"])
    if cache_path and new != old:
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            tmp = cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                json.dump(new, f, ensure_ascii=False)
            os.replace(tmp, cache_path)
        except OSError:
            pass
    return out


def brand_supplier(index, ean, code):
    """这个商品在哪家品牌供应商的 SCHEDA 里（先按条码，再按我们的编号）；没有 None"""
    if not index:
        return None
    k = _ean_key(ean)
    if k and k in index["ean"]:
        return index["ean"][k]
    c = str(code or "").strip()
    return index["code"].get(c) if c else None


# =====================================================================
# 读取 SCHEDA
# =====================================================================
PDV_ROW_BLOCK = 100000      # 门店采购 SCHEDA 有几页 GRIGLIA 时，第 k 页的商品行号 = 行 + k × 这个数


def _find_header(ws):
    """商品表头（Cod.Art. / Codice EAN / Colli …那一行）：({键: 列}, 行号)；找不到 ({}, None)"""
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
            elif n.startswith("CODICEFORN") or n in ("COD.FORN.", "CODFORN", "COD.FORNITORE"):
                found["sup_code"] = c          # 供应商自己的货号（Codice Fornitore）
        if "colli" in found and ("ean" in found or "code" in found):
            return found, i
    return {}, None


def _read_items(info, ws, cols, hdr, offset=0, sheet=None):
    """一页的商品 → info["items"]（行号 + offset；sheet：门店采购几页 GRIGLIA 时记是哪一页）"""
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
        sc_ = g("sup_code")
        sc_ = str(int(sc_)) if isinstance(sc_, float) and sc_ == int(sc_) else str(sc_ or "").strip()
        it = {"row": r_idx + offset, "code": code, "ean": ean, "desc": str(g("desc") or "").strip(),
              "pack": pack if pack > 0 else 1, "price": _num(g("price")) or 0.0, "sup_code": sc_}
        if sheet is not None:
            it["sheet"] = sheet
        info["items"].append(it)


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
        grids = [t for k, t in titles.items() if k.startswith("GRIGLIA") and "TUTTI" not in k]
        if not info["sheets"] and grids:
            # 门店采购（PDV 文件夹）：一页 GRIGLIA（也有分好几页的，例如 SAIS：BAZZAR & DRUG / ESTIVI / INVERNO），
            # 一张单送一家店（表头「Luogo di destinazione」选门店）→ 五家店都按这几页算，生成订单时每家店一个文件
            # （write_pdv_file）。第 k 页（从 0 数）的商品行号 = 行 + k × PDV_ROW_BLOCK，几页的行号不会重
            info["pdv"] = read_pdv_header(wb, grids[0])
            info["pdv"]["sheets"] = grids
            info["pdv"]["cells_by_sheet"] = {t: read_pdv_header(wb, t)["cells"] for t in grids}
            info["sheets"] = {sid: grids[0] for sid in STORE_SHEETS}
        if not info["sheets"]:
            raise ValueError("没有门店页（FOIANO / MARLIA / S.FILIPPO / S.MARCO / S.CONCORDIO），也没有 GRIGLIA 页")
        ws = wb[next(iter(info["sheets"].values()))]
        cols, hdr = _find_header(ws)
        if not hdr:
            raise ValueError("门店页没有找到表头（Cod.Art. / Codice EAN / Colli）")
        info["header_row"], info["cols"] = hdr, cols
        # 订单文件里附加数据列（GIA、ROT.30d …）的起始列：表头里已经有 GIA 就用那一列（以前写过的文件），
        # 否则接在表头最后一列（一般是 IMPORTO）后面
        hrow = next(ws.iter_rows(min_row=hdr, max_row=hdr, values_only=True), ())
        filled = [c for c, v in enumerate(hrow, 1) if v not in (None, "") and str(v).strip()]
        gia = next((c for c, v in enumerate(hrow, 1) if _norm(v) == "GIA"), None)
        info["extra_col"] = gia or ((max(filled) + 1) if filled else len(hrow) + 1)
        pages = [(0, ws, cols, hdr)]
        if info.get("pdv"):
            info["pdv"]["cols"] = {ws.title: cols}
            for k, t in enumerate(info["pdv"]["sheets"][1:], 1):
                c2, h2 = _find_header(wb[t])
                if not h2:
                    info["warnings"].append(f"{t} 页没有找到表头，这一页没读")
                    continue
                info["pdv"]["cols"][t] = c2
                pages.append((k, wb[t], c2, h2))
        for k, ws_, cols_, hdr_ in pages:
            _read_items(info, ws_, cols_, hdr_, k * PDV_ROW_BLOCK, ws_.title if info.get("pdv") else None)
        # 各门店页一般行完全一样；万一不一样，按商品编号 / EAN 对应到各自的行
        info["rowmap"] = {}
        first = next(iter(info["sheets"].values()))
        for sid, sheet in info["sheets"].items():
            if sheet == first:
                continue
            # 同一个编号可能出现好几行（例如 ASS. 整箱一行 + 各个颜色一行，编号、EAN 都一样）：
            # 同一行号上编号对得上就用同一行；对不上才按编号找，第 n 次出现对第 n 次出现（不能都对到第一行）
            row_keys, key_rows = {}, {}
            for r_idx, row in enumerate(wb[sheet].iter_rows(min_row=hdr + 1, values_only=True), hdr + 1):
                keys = {}
                for k in ("code", "ean"):
                    c = cols.get(k)
                    v = row[c - 1] if c and c - 1 < len(row) else None
                    v = str(int(v)) if isinstance(v, float) and v == int(v) else str(v or "").strip()
                    if v:
                        keys[k] = v
                        key_rows.setdefault((k, v), []).append(r_idx)
                row_keys[r_idx] = keys
            m, used = {}, set()

            def same(r, it):
                k = row_keys.get(r) or {}
                return (it["code"] and k.get("code") == it["code"]) or (not it["code"] and it["ean"] and k.get("ean") == it["ean"])
            for it in info["items"]:
                if same(it["row"], it):
                    used.add(it["row"])
            for it in info["items"]:
                if same(it["row"], it):
                    continue
                r = None
                for key in ((("code", it["code"]),) if it["code"] else ()) + ((("ean", it["ean"]),) if it["ean"] else ()):
                    r = next((x for x in key_rows.get(key, []) if x not in used), None)
                    if r:
                        break
                if r:
                    used.add(r)
                    m[it["row"]] = r
                else:
                    m[it["row"]] = None
            if m:
                info["rowmap"][sid] = m
                if any(v is None for v in m.values()):
                    info["warnings"].append(f"{sheet} 页少了一些商品，那些商品不会写进这家店")
        return info
    finally:
        wb.close()


def _store_of_text(text):
    """
    地址 / 公司名文字 → 门店号（Dati Filiali 页一行一家店）；BMS 那一行、认不出 → None。
    先看第一行（公司名「CASA & TE S.CONCORDIO SRL」），没有门店名再看整段：用户的文件里 S.CONCORDIO 那一行的 PEC
    写成了 casaete.smarco@pec.it，整段一起看会认成 S.MARCO
    """
    raw = str(text or "")
    for part in (raw.split("\n", 1)[0], raw):
        u = _norm(part).replace(".", " ")
        if not u:
            continue
        for sid, words in STORE_WORDS.items():
            if any(w in u for w in words):
                return sid
    return None


def read_pdv_header(wb, grid_title):
    """
    门店采购 SCHEDA 的表头（GRIGLIA 页第 1–3 行）和「Dati Filiali」页：
      {"sheet", "cells": {"fatt": "F1", "dest": "F2", "number": "H1", "date": "H2"}, "dest": {门店号: 送货地址},
       "fatt": {门店号: 开票抬头}, "fatt_all": 所有门店都一样的开票抬头（发票开给 BMS 的那种）}
    两种写法（2026-11-03 看用户的文件）：「Dati Filiali 1」A 列只有一行 BMS 抬头 + B 列各店地址（发票开给 BMS、货送门店）；
    「Dati Filiale 2」A / B 两列一家店一行（每家店自己开票）
    """
    ws = wb[grid_title]
    cells = {}
    for r, row in enumerate(ws.iter_rows(min_row=1, max_row=4, values_only=True), 1):
        for c, v in enumerate(row, 1):
            n = _norm(v).replace(" ", "").rstrip(":")
            key = {"FATTURAZIONE": "fatt", "LUOGODIDESTINAZIONE": "dest", "NUMEROORDINE": "number",
                   "DATAORDINE": "date", "DATACONSEGNAENTRO": "deliver"}.get(n)
            if key and key not in cells:
                cells[key] = f"{_col_letter(c + 1)}{r}"
    out = {"sheet": grid_title, "cells": cells, "dest": {}, "fatt": {}, "fatt_all": None, "data_sheet": None}
    ds = next((w for w in wb.worksheets if _norm(w.title).startswith("DATI FILIAL")), None)
    if ds is None:
        return out
    out["data_sheet"] = ds.title
    fatts = []
    for row in ds.iter_rows(min_row=2, max_row=20, values_only=True):
        a = row[0] if len(row) > 0 else None
        b = row[1] if len(row) > 1 else None
        if b not in (None, "") and str(b).strip():
            sid = _store_of_text(b)
            if sid and sid not in out["dest"]:
                out["dest"][sid] = str(b).strip()
        if a not in (None, "") and str(a).strip():
            fatts.append(str(a).strip())
            sid = _store_of_text(a)
            if sid and sid not in out["fatt"]:
                out["fatt"][sid] = str(a).strip()
    if len(fatts) == 1:
        out["fatt_all"] = fatts[0]
    return out


def pdv_header_values(pdv, sid, when=None):
    """门店采购：这家店的订单文件表头要填的格子 {格子: 文字}（开票、送货地址、订单号、下单日期）"""
    when = when or datetime.now()
    cells = pdv.get("cells") or {}
    out = {}
    if cells.get("fatt"):
        f = pdv["fatt"].get(sid) or pdv.get("fatt_all")
        if f:
            out[cells["fatt"]] = f
    if cells.get("dest") and pdv["dest"].get(sid):
        out[cells["dest"]] = pdv["dest"][sid]
    if cells.get("number"):
        out[cells["number"]] = f"{when:%d%m%Y}-{STORE_ABBR.get(sid, sid)}-01"
    if cells.get("date"):
        out[cells["date"]] = f"{when:%d/%m/%Y}"
    return out


_MOQ_GUARD = None
_MOQ_LOCKS = {}


def load_moq_into(moq, p):
    """
    读一个 SCHEDA 的起订量放进 moq（{路径: 起订量}）。几个线程同时要同一个文件时只读一次，后到的等它读完直接用
    （2026-11-06：打开程序时工作台和总仓采购两个后台线程同时把今天的 SCHEDA 各读一遍）
    """
    global _MOQ_GUARD
    import threading
    if _MOQ_GUARD is None:
        _MOQ_GUARD = threading.Lock()
    with _MOQ_GUARD:
        lk = _MOQ_LOCKS.setdefault(p, threading.Lock())
    with lk:
        if p in moq:
            return moq[p]
        try:
            moq[p] = parse_moq(p)
        except Exception as e:
            from i18n import tr
            moq[p] = dict(read_moq(None), raw=tr("读不出来：{0}", e))
        return moq[p]


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
DUP_PREFIX = "dup:"


def mark_duplicates(schede):
    """
    同一个 SCHEDA 里同一个商品编号出现好几行（例如「ASS.」整箱一行 + 各个颜色各一行，编号、EAN 一样，颜色行每箱 1 件）：
    数据库里只有一个商品、一份销量，建议量只算在第一次出现的那一行；后面的行 dup_of = 第一行，
    db_code 换成查不到的 "dup:编号"，不再各算一遍（不然一份需求会订好几遍，颜色行还会按件当箱）。要订颜色行自己手动填。
    """
    for s in schede:
        first = {}
        for it in s["items"]:
            code = it.get("db_code")
            if not code or str(code).startswith(DUP_PREFIX):
                continue
            if code in first:
                it["dup_of"] = first[code]
                it["db_code"] = DUP_PREFIX + str(code)
            else:
                first[code] = it["row"]


def item_code(it):
    """表格里显示的商品编号（重复行也显示原来的编号）"""
    c = it.get("db_code") or it["code"]
    return it["code"] if str(c).startswith(DUP_PREFIX) else c


def store_cycle_factor(algo, sid):
    """门店周期系数（算法设置 → 门店周期，只用在门店配货）：algo["store_cycle"] = {"门店号": 系数}，没设 = 1"""
    try:
        v = float(((algo or {}).get("store_cycle") or {}).get(str(sid), 1.0))
        return v if v > 0 else 1.0
    except (TypeError, ValueError):
        return 1.0


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
    mark_duplicates(schede)
    codes = sorted({it["db_code"] for s in schede for it in s["items"] if it.get("db_code") and not it.get("dup_of")})
    out = {"_missing": missing, "_algo": dict(algo) if algo else None}
    if not codes:
        return out

    def one(sid):
        # 新算法：不按数据库箱规取整，箱数按 SCHEDA 的每箱件数智能取整（colli_for）
        # 原程序算法：建议件数完全按原程序算（含按数据库箱规取整），再按 SCHEDA 每箱件数换成箱
        v2 = bool(algo) and algo.get("engine") == "v2"
        # 原程序算法：每个 SCHEDA（供应商）单独算，和原程序一次导入一个 SCHEDA 一样（A/B/C 等级在同一家供应商的商品里比）
        groups = None if v2 else [sorted({it["db_code"] for it in s["items"] if it.get("db_code")}) for s in schede]
        df = R.compute_suggestions(db, sid, codes, days_list, reorder_cycle, None,
                                   use_supplier_cycles=use_supplier_cycles, round_to_db_pack=not v2, algo=algo,
                                   groups=groups, want_raw=True, cycle_factor=store_cycle_factor(algo, sid))
        d = {}
        for _, r in df.iterrows():
            code = str(int(r["CodArticolo"]))
            s1 = float(r.get("sales_1", 0) or 0)
            s3 = float(r.get("sales_3", 0) or 0)
            daily = s1 / days_list[0] if s1 > 0 else (s3 / days_list[2] if s3 > 0 else 0.0)
            if R._val(r, "daily_rate") is not None:                       # 新算法的日均（加权 + 季节）
                daily = float(R._val(r, "daily_rate") or 0)
            inv_days = R._val(r, "inventory_days")
            sugg = int(R._val(r, "suggested_order_qty", 0) or 0)
            raw = R._val(r, "raw_order_qty")
            d[code] = {"sugg": sugg, "daily": daily,
                       # 真正要的件数（没按箱规取整）：门店间调货用。新算法本来就不取整
                       "need": float(raw) if (raw is not None and raw == raw) else float(sugg),
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
        return d
    out.update(R.per_store(stores, one, on_store))      # 几家门店同时查（结果和一家家查一样）
    return out


# =====================================================================
# 门店之间调货（transfers.py）：要的件数远小于一箱时，从别的门店调多出来的货，少订一箱
# =====================================================================
def apply_transfers(calc, cfg=None, far_stores=None, supplier_cfg=None):
    # calc["transfer_overrides"] = {(SCHEDA 路径, 调入门店, 行号): 人工定的调货}（订货明细里改的，见 transfers.norm_override；
    #   件数 0 = 不调）；calc["transfer_frozen"] = True 时只按它调
    """
    在系统建议的箱数上算调货：calc["base_orig"] = 调货前的建议；calc["base"] = 调货后的建议；
    calc["transfers"] = [{from, to, path, supplier, row, code, ean, desc, pieces, value, price, pack,
                          colli_before, colli_after}]
    cfg 关掉（enabled False）时 base 恢复成调货前的建议。
    """
    import copy
    import transfers as TR
    cfg = TR.config(cfg)
    if "base_orig" not in calc:
        calc["base_orig"] = copy.deepcopy(calc["base"])
    base = copy.deepcopy(calc["base_orig"])
    calc["transfers"] = []
    if not cfg.get("enabled"):
        calc["base"] = base
        return []
    demand, stores = calc["demand"], calc.get("stores") or []
    needs, info, ordering = [], {}, set()
    for sc in calc["schede"]:
        items = {it["row"]: it for it in sc["items"]}
        # 供应商设置里单独选了参与调货的门店：这家供应商的商品只在这几家店之间调
        allowed = supplier_stores({"supplier_cfg": supplier_cfg}, sc, "transfer_stores")
        for sid, lines in base.get(sc["path"], {}).items():
            for row, n in lines.items():
                it = items.get(row)
                if not it or n <= 0:
                    continue
                code = it.get("db_code") or it["code"]
                ordering.add((sid, code))
                d = demand.get(sid, {}).get(code)
                if not d:
                    continue
                nid = (sc["path"], sid, row)
                needs.append({"id": nid, "store": sid, "key": code, "need": d.get("need", d.get("sugg", 0)),
                              "pack": it["pack"], "colli": n, "price": it.get("price") or 0.0, "allowed": allowed})
                info[nid] = (sc, it)
    avail, stock = {}, {}
    for sid in stores:
        for code, d in (demand.get(sid) or {}).items():
            if not isinstance(d, dict):
                continue
            stock[(sid, code)] = max(0.0, float(d.get("inv") or 0))
            if (sid, code) in ordering:
                continue                       # 自己这次也要订的，不调出
            s = TR.surplus(d, cfg.get("keep_cycles"))
            if s > 0:
                avail[(sid, code)] = s
    far = far_stores if far_stores is not None else DEFAULT_FILL.get("far_stores")
    # transfer_frozen：审核时人工改过调货 → 只按 transfer_overrides 调（改一行不影响别的行）
    moves, new_colli = TR.plan(needs, avail, cfg, far, calc.get("transfer_overrides"), stock=stock,
                               auto=not calc.get("transfer_frozen"))
    for nid, c2 in new_colli.items():
        path, sid, row = nid
        lines = base.setdefault(path, {}).setdefault(sid, {})
        if c2 > 0:
            lines[row] = c2
        else:
            lines.pop(row, None)
    out = []
    for m in moves:
        sc, it = info[m["need_id"]]
        code = it.get("db_code") or it["code"]
        d_from = demand.get(m["from"], {}).get(code)
        d_to = demand.get(m["to"], {}).get(code)
        out.append(dict(m, path=sc["path"], supplier=sc["name"], row=it["row"], code=code,
                        ean=it.get("ean", ""), desc=it.get("desc", ""), price=it.get("price") or 0.0,
                        # 调完以后：调出店还够卖几天、调入店（加上这次订的）够卖几天
                        from_days=cover_days(d_from, -m["pieces"]),
                        to_days=cover_days(d_to, m["pieces"] + m["colli_after"] * it["pack"])))
    out.sort(key=lambda x: (x["from"], x["to"], x["supplier"], x["desc"]))
    calc["base"] = base
    calc["transfers"] = out
    return out


def export_transfers(transfers, path, date_tag=""):
    """调货单：每家调出门店一页（拣货用），第一页是汇总"""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    import transfers as TR
    wb = Workbook()
    ws = wb.active
    ws.title = "汇总"
    ws.append(["调出门店", "商品数", "件数", "货值 €", "少订箱数"])
    for sid, x in sorted(TR.by_donor(transfers).items()):
        ws.append([STORE_SHEETS.get(sid, str(sid)), x["items"], x["pieces"], round(x["value"], 2), x["boxes_saved"]])
    for c in ws[1]:
        c.font = Font(bold=True)
    for sid in sorted({t["from"] for t in transfers}):
        w = wb.create_sheet(f"从 {STORE_SHEETS.get(sid, sid)}"[:31])
        w.append(["调入门店", "供应商", "Cod.Art.", "EAN", "品名", "调货件数", "件价 €", "货值 €", "原来订（箱）", "现在订（箱）",
                  "调出后本店够卖（天）", "调入店够卖（天）"])
        for t in transfers:
            if t["from"] != sid:
                continue
            w.append([STORE_SHEETS.get(t["to"], str(t["to"])), t["supplier"], t["code"], t["ean"], t["desc"],
                      t["pieces"], round(t["price"], 2), round(t["value"], 2), t["colli_before"], t["colli_after"],
                      _fmt_days(t.get("from_days")), _fmt_days(t.get("to_days"))])
        for c in w[1]:
            c.font = Font(bold=True)
        w.column_dimensions["E"].width = 40
    wb.save(path)
    return path


def colli_for(d, pack, algo=None):
    """
    建议件数 → SCHEDA 箱数。新算法：凑不成整箱时少订后够安全库存就少订（forecast.to_packs）；原程序算法：件数（已按原程序取整）÷ 每箱件数，向上取整。
    不管在途：在途在 base_colli 里按箱扣（订货箱数 = 建议箱数 − 在途箱数，2026-11-03 用户：三列放一起一眼看懂）
    """
    if not d or d["sugg"] <= 0:
        return 0
    if algo and algo.get("engine") == "v2":
        import forecast as F
        return F.to_packs(d["sugg"], pack, d.get("inv", 0), d.get("daily", 0), algo,
                          tier=d.get("tier") or "C", cycle=d.get("cycle"))
    return math.ceil(d["sugg"] / pack)


def transit_boxes(d, pack):
    """在途折成这个 SCHEDA 的箱数（在途件数 ÷ 每箱件数，整箱；零头不算）"""
    tp = float((d or {}).get("transit") or 0)
    if tp <= 0:
        return 0
    pack = pack if pack and pack > 0 else 1
    return int(tp / pack + 1e-9)


def apply_transit(demand, schede, transit):
    """
    在途（order_record.compute 的结果）→ demand[门店][编号]["transit"]（件）/ ["transit_info"]；调货用的 need 也扣掉。
    建议箱数（colli_for）会扣掉在途。返回有在途的 (门店, 编号) 个数
    """
    import order_record as REC
    for sid, dem in demand.items():                 # 再算一次（例如标了到货）：先还原上次扣的
        if isinstance(sid, int) and isinstance(dem, dict):
            for d in dem.values():
                if isinstance(d, dict) and "transit" in d:
                    d.pop("transit", None)
                    d.pop("transit_info", None)
                    if "need0" in d:
                        d["need"] = d["need0"]
    if not transit:
        return 0
    n = 0
    for sid, dem in demand.items():
        if not isinstance(sid, int) or not isinstance(dem, dict):
            continue
        done = set()
        for sc in schede:
            for it in sc["items"]:
                code = it.get("db_code") or it["code"]
                d = dem.get(code)
                if not d or code in done:
                    continue
                t = REC.lookup(transit, sid, it.get("ean"), code)
                if not t:
                    continue
                done.add(code)
                d["transit"] = float(t["pcs"])
                d["transit_info"] = t
                d.setdefault("need0", float(d.get("need", d["sugg"]) or 0))
                d["need"] = max(0.0, d["need0"] - float(t["pcs"]))
                n += 1
    return n


def base_colli(scheda, demand, stores=None, transit=True):
    """
    系统建议的订货箱数：{门店号: {行号: 箱数}}。transit=True：订货箱数 = 建议箱数 − 在途箱数（不到 0）；
    False：建议箱数（没扣在途，订货明细「建议（箱）」列显示用，calc["base_sugg"]）
    """
    out = {}
    for sid in (stores or scheda["sheets"].keys()):
        if sid not in scheda["sheets"]:
            continue
        dem = demand.get(sid, {})
        lines = {}
        for it in scheda["items"]:
            d = dem.get(it.get("db_code") or it["code"])
            n = colli_for(d, it["pack"], demand.get("_algo"))
            if transit:
                n = max(0, n - transit_boxes(d, it["pack"]))
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


def cover_days(d, pieces=0):
    """
    够卖几天 =（现有库存 + pieces 件）÷ 日均。负库存当 0；没有销量 → 无穷大（∞）；没有数据 → None。
    「库存够卖」pieces = 0；「订后够卖」pieces = 订货箱数 × 每箱件数。
    """
    if not d:
        return None
    daily = float(d.get("daily") or 0)
    have = max(0.0, float(d.get("inv") or 0)) + float(pieces or 0)
    if daily <= 0:
        return float("inf") if have > 0 else None
    return have / daily


def days_text(v):
    """够卖天数的显示：∞ / 整数天 / 空"""
    if v is None:
        return ""
    return "∞" if math.isinf(v) else f"{v:,.0f}"


def add_candidates(scheda, sid, demand, lines):
    """
    订货明细「加商品凑单」：这家店在这个供应商能加的所有商品（包括没被选进来的），按卖得快排在前面。
    返回 [{row, item, daily, inv, days_now, ordered, box, tier, decision}]
    """
    dem = demand.get(sid, {}) if demand else {}
    out = []
    for it in scheda["items"]:
        d = dem.get(it.get("db_code") or it["code"]) or {}
        daily = float(d.get("daily") or 0)
        inv = max(0.0, float(d.get("inv") or 0))
        ordered = int((lines or {}).get(it["row"], 0))
        have = inv + ordered * it["pack"]
        out.append({"row": it["row"], "item": it, "daily": daily, "inv": inv, "ordered": ordered,
                    "days_stock": cover_days(d) if d else None,
                    "days_now": (have / daily) if daily > 0 else float("inf"),
                    "box": it["pack"] * it["price"], "tier": d.get("tier") or "", "decision": d.get("decision") or "",
                    "known": bool(d)})
    out.sort(key=lambda x: (-x["daily"], x["days_now"]))
    return out


def days_after(c, add):
    """加 add 箱以后够卖几天"""
    if c["daily"] <= 0:
        return float("inf")
    return (c["inv"] + (c["ordered"] + add) * c["item"]["pack"]) / c["daily"]


def suggest_fill(scheda, sid, demand, lines, max_days=60):
    """
    按销量推荐加哪些商品补齐起订量（不管差多少都试，和配货时自动凑单一样：
    先加卖得最快的；只差金额时找一箱就够、多出来最少的；加完库存最多够卖 max_days 天）。
    返回 {行号: 加几箱}；已经够起订量返回 {}；在天数限制内凑不齐返回 None
    """
    dem_rows = {}
    for it in scheda["items"]:
        d = (demand.get(sid, {}) if demand else {}).get(it.get("db_code") or it["code"])
        if d:
            dem_rows[it["row"]] = {"daily": float(d.get("daily") or 0), "inv": float(d.get("inv") or 0)}
    mc, ma = scheda.get("moq_colli") or 0, scheda.get("moq_amount") or 0
    if math.isinf(mc) or math.isinf(ma):
        return None
    r = _try_fill(dict(lines or {}), scheda, dem_rows,
                  {"max_colli_gap": float("inf"), "max_amount_gap": float("inf"), "max_stock_days": max_days})
    if r is None:
        return None
    return {row: n for row, n, _ in r[1]}


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


def supplier_stores(fill, scheda, key):
    """
    这家供应商单独设的门店名单（配货设置里的总名单之外，「供应商设置」里可以按供应商单独选）：
      key = "bms_stores"（BMS 不够时可以整单并进 BMS 的门店）/ "transfer_stores"（参与门店间调货的门店）
    没单独设 → None（按总设置）
    """
    c = ((fill or {}).get("supplier_cfg") or {}).get(scheda.get("name")) or {}
    v = c.get(key)
    return None if v is None else {int(x) for x in v}


def bms_far(fill, scheda):
    """不能被并进 / 借给 BMS 的门店：供应商单独设了就按它，否则按配货设置的 far_stores"""
    own = supplier_stores(fill, scheda, "bms_stores")
    if own is not None:
        return set(STORE_SHEETS) - own
    return set((fill or {}).get("far_stores") or [])


def _borrow(scheda, res, members, lent, fill, items, relaxed=False):
    """
    从同城、直接送店的门店一箱一箱借不紧急的货给 BMS，直到 BMS 够起订量；借完那家店自己还要够起订量。
    relaxed=True：紧急的、库存天数少的也可以借（还是先借最不紧急的），只要借完那家店仍够起订量
                  —— 就是把这家店超过起订量的部分挪给 BMS。
    返回 True = 借够了
    """
    mc, ma = scheda["moq_colli"], scheda["moq_amount"]
    far = bms_far(fill, scheda)
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
                if not relaxed and _URGENT in ((d or {}).get("decision") or ""):
                    continue
                days = _stock_days(d)
                if not relaxed and days < min_days:
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


PLAN_MODES = ("auto", "all_bms", "direct_only", "pdv")


def bms_ordered(b):
    """这张 BMS 单会不会真的下（不够起订量的不下，除非手动强制并入）"""
    return bool(b) and b.get("status") in ("direct", "filled", "forced")


def plan_supplier(scheda, demand, fill=None, stores=None, colli=None, mode="auto", picks=None):
    """
    一个供应商的配货方案。
    返回 {"stores": {门店号: {status, colli, amount, lines, added, lent}}, "bms": {...} 或 None}
      status: "direct" 直接送店 / "filled" 凑单后直接送店 / "bms" 并入 BMS / "wait" 这次不订（下次再看）/
              "pulled" 自己够起订量、手动并进 BMS / "dropped" 手动选「只订达标门店」后不订 / "none" 不用订
    规则（2026-10-22 用户定，从简单开始）：
      能单独送店（够起订量，或差一点凑单后够）就单独送店——不拆单借货，也不把它拉进 BMS；
      订不起来的门店按 fill["bms_order"]（默认 3-4-5-2-1，只算参与 BMS 凑单的门店）一家一家并进 BMS，
      每加一家试一次（含 BMS 凑单），够起订量就停；剩下的、不参与的、全并完还不够的 → "wait" 这次不订，下次再看。
    mode（配货结果页手动选）：
      "auto"        系统建议（上面的规则）
      "manual"      picks = {"stores": [进 BMS 的门店], "force": 不够起订量也下单}：自己够起订量的店也可以并进来
      "all_bms"     所有要订的门店整单并进 BMS；BMS 不够起订量也照样下单（status "forced"）
      "direct_only" 只订自己够起订量（含凑单后够）的门店，其他门店这次不订（status "dropped"）
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
    if mode == "pdv" or scheda.get("pdv"):
        # 门店采购（2026-11-03 用户定）：每家店的单都要各自达到起订量（差一点可以凑单），不够的这次不订；没有 BMS
        for sid in to_bms:
            res["stores"][sid]["status"] = "wait"
        res["mode"] = "pdv"
        return res

    def evaluate(members):
        lines, dem_rows, per_store = _bms_state(scheda, res, members, {}, items)
        c, a = _totals(lines, items)
        if _satisfied(c, a, mc, ma):
            return "direct", lines, [], per_store
        r = _try_fill(lines, scheda, dem_rows, fill) if fill.get("fill_bms", True) else None
        if r is not None:
            return "filled", r[0], r[1], per_store
        return "short", lines, [], per_store

    def to_base(sid, status):
        st = res["stores"][sid]
        st["status"] = status
        st["lines"], st["added"] = dict(st["base_lines"]), []
        st["colli"], st["amount"] = _totals(st["lines"], items)

    how = []
    if mode == "all_bms":                                   # 手动：所有要订的门店整单并进 BMS
        members = [sid for sid, st in res["stores"].items() if st["status"] != "none"]
        for sid in members:
            to_base(sid, "bms")
        how = ["manual"]
    elif mode == "manual":                                  # 手动选哪几家进 BMS（自己够起订量的也可以并进来）
        pick = picks or {}
        members = [sid for sid in (pick.get("stores") or []) if sid in res["stores"]
                   and res["stores"][sid]["status"] != "none"]
        for sid in members:
            to_base(sid, "bms" if sid in to_bms else "pulled")
        for sid in to_bms:
            if sid not in members:
                res["stores"][sid]["status"] = "wait"
        how = ["picked"]
    else:
        # 系统建议（2026-10-22 用户定的规则）：能单独送店的都单独送店，从不拆单借货、也不把它们拉进 BMS；
        # 订不起来的门店按优先顺序（默认 3-4-5-2-1，只算参与 BMS 凑单的门店）一家一家并进 BMS，
        # 够起订量就停（BMS 分货的门店越少越省事）；剩下的、不参与的、并完还不够的 → 这次不订，下次再看
        if not to_bms:
            return res
        far = bms_far(fill, scheda)
        order = [int(x) for x in (fill.get("bms_order") or BMS_ORDER)]
        eligible = [sid for sid in order if sid in to_bms and sid not in far] + \
                   [sid for sid in to_bms if sid not in order and sid not in far]
        members, ok = [], False
        for sid in eligible:
            members.append(sid)
            if evaluate(members)[0] != "short":
                ok = True
                break
        for sid in to_bms:
            if not ok or sid not in members:
                res["stores"][sid]["status"] = "wait"
        if not members:
            return res
        how = ["priority"]
    if not members:
        return res
    status, lines, added, per_store = evaluate(members)
    if status == "short" and (mode == "all_bms" or (mode == "manual" and (picks or {}).get("force"))):
        status = "forced"
    c, a = _totals(lines, items)
    b0c, b0a = _totals(_bms_state(scheda, res, members, {}, items)[0], items)
    b = {"stores": members, "lenders": {}, "colli": c, "amount": a, "lines": lines, "added": added,
         "status": status, "how": how, "before_colli": b0c, "before_amount": b0a}
    # 凑单加的箱放在 BMS 里这个商品卖得最快的那家店
    involved = list(per_store.keys())
    for row, n, _ in added:
        best = max(involved, key=lambda s_: res["stores"][s_]["dem_rows"].get(row, {}).get("daily", 0))
        per_store.setdefault(best, {})[row] = per_store.get(best, {}).get(row, 0) + n
    b["per_store"] = per_store
    res["bms"] = b
    return res


def urgent_share(scheda, lines, dem_rows, days=7, with_total=False):
    """
    一张单里「紧急缺货」商品占多少：库存够卖不到 days 天（默认一周）的商品个数 ÷ 这张单的商品个数。
    返回 (占比 0~1, 紧急商品数)；with_total=True 时多返回商品总数。给起订量检查 / 配货结果标颜色用
    """
    items = {it["row"]: it for it in scheda.get("items") or []}
    tot = n = 0
    for row, c in (lines or {}).items():
        if row not in items or c <= 0:
            continue
        tot += 1
        d = (dem_rows or {}).get(row)
        cd = cover_days(d) if d else None
        if cd is not None and cd < days:
            n += 1
    share = n / tot if tot else 0.0
    return (share, n, tot) if with_total else (share, n)


def plan_urgent(scheda, plan, sid):
    """某一张单的紧急占比（按商品个数）：门店（sid）用这家店的单；sid = None 是 BMS 单（各店并进来的部分合起来算）"""
    if sid is not None:
        st = (plan.get("stores") or {}).get(sid) or {}
        return urgent_share(scheda, st.get("lines") or st.get("base_lines"), st.get("dem_rows"))
    b = plan.get("bms") or {}
    n = tot = 0
    for s_, lines in (b.get("per_store") or {}).items():
        _, k, t = urgent_share(scheda, lines, ((plan.get("stores") or {}).get(s_) or {}).get("dem_rows"), with_total=True)
        n += k
        tot += t
    return (n / tot if tot else 0.0), n


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


def _set_cells(xml, col_letter, values, plain_col=None):
    """
    values: {行号: 数字或 None}；None = 清空（保留格式）。
    plain_col：不订的行（空）改用同一行这一列的格式——模板里 Colli 整列都是黄底，只让订了的那几行留黄色
    """
    for row, val in values.items():
        ref = f"{col_letter}{row}"
        cell_re = re.compile(r'<c\b[^>]*\br="' + ref + r'"[^>]*?(?:/>|>.*?</c>)', re.S)
        m = cell_re.search(xml)
        style = ""
        if m:
            s = re.search(r'\bs="(\d+)"', m.group(0).split(">")[0])
            style = f' s="{s.group(1)}"' if s else ""
        if plain_col and val in (None, 0):
            pm = re.search(r'<c\b[^>]*\br="' + plain_col + str(row) + r'"[^>]*?(?:/>|>)', xml)
            ps = re.search(r'\bs="(\d+)"', pm.group(0)) if pm else None
            if ps:
                style = f' s="{ps.group(1)}"'
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


def monthly_and_need(x, days=None, sales=None):
    """
    月均、需求（件）：
      新算法（有目标库存 target）：月均 = 加权日均（含季节）× 30；需求 = 日均 × 要备的天数（订货周期 × 等级系数 + 到货天数），
        就是目标库存里不含安全库存的那部分（target − safety）
      原程序算法：月均 = 最长周期销量 ÷ 月数；需求 = 第 3 个周期（默认 90 天）的日均 × 订货周期
    """
    d = list(days or [30, 60, 90, 180])
    if sales is None:
        sales = [float(v or 0) for v in (x.get("sales") or [0, 0, 0, 0])][:4]
        sales += [0.0] * (4 - len(sales))
    if x.get("target") is not None and x.get("daily") is not None:
        need = float(x["target"]) - float(x.get("safety") or 0)
        return round(float(x["daily"]) * 30, 1), float(round(max(0.0, need)))
    cyc = x.get("cycle") if isinstance(x.get("cycle"), (int, float)) else 30
    months = max(1.0, d[3] / 30.0)
    return round(sales[3] / months, 1), float(round(sales[2] / d[2] * float(cyc)) if d[2] else 0)


def extra_fields(x, sugg_colli, days=None):
    """
    一个商品在一家店的附加数据（顺序同 extra_headers）；数据库里没有这个商品 → None（那一行留空）
      GIA 本店库存（原值）· ROT 各周期销量 · 月均 / 需求 见 monthly_and_need（新算法按新算法算）
      采购量 = 系统建议件数 · 箱数 = 系统建议箱数 · Livello = 等级 · GIA.0~5 各门店库存（0 = BMS 总仓）
      period_sales = 第 3 个周期的销量 · GIA-period = 本店库存（负数按 0）− period_sales
    """
    if not x:
        return None
    d = list(days or [30, 60, 90, 180])
    sales = [float(v or 0) for v in (x.get("sales") or [0, 0, 0, 0])][:4] + [0.0] * max(0, 4 - len(x.get("sales") or []))
    inv = float(x.get("inv_raw", x.get("inv", 0)) or 0)
    period = sales[2]
    monthly, demand = monthly_and_need(x, d, sales)
    gia = [float(v or 0) for v in (x.get("gia_all") or [0] * 6)][:6]
    gia += [0.0] * (6 - len(gia))
    return ([inv] + sales + [monthly, float(x.get("sugg") or 0), float(sugg_colli or 0),
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


FILE_SKIP_HEADS = ("月均", "采购量", "箱数", "需求")     # 程序内部用，不写进给供应商的订单文件


def file_extra(extra, heads):
    """写进订单文件的附加列：去掉中文名的内部列（月均 / 采购量 / 箱数 / 需求）。返回 (extra, heads)"""
    if not extra or not heads:
        return extra, heads
    keep = [i for i, h in enumerate(heads) if h not in FILE_SKIP_HEADS]
    out = {sid: {r: ([v[i] for i in keep] if v else v) for r, v in rows.items()} for sid, rows in extra.items()}
    return out, [heads[i] for i in keep]


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


def write_order_file(scheda, per_store_lines, out_path, extra=None, headers=None, marks=None):
    """
    复制 SCHEDA → out_path，各门店页 Colli 列：per_store_lines 里有的门店写箱数，其余门店清空。
    extra：{门店号: {行号: [附加列的值]}}（extra_data），有的话在各门店页 IMPORTO 后面加 GIA、ROT.30d … 这些列。
    marks：{门店页名: 去向文字}，写在汇总页（GRIGLIA PER TUTTI PDV）门店名上面。
    打开时让 Excel / WPS 重新计算合计。
    """
    colli_col = _col_letter(scheda["cols"]["colli"])
    pc = scheda["cols"].get("pack") or (scheda["cols"]["colli"] - 1)
    plain_col = _col_letter(pc) if pc and pc >= 1 and pc != scheda["cols"]["colli"] else None
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
        grid = None
        if marks:
            grid = next((p for n, p in paths.items() if "GRIGLIA" in n.upper()), None)
            sst = _shared_strings(zin) if grid else []
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if grid and item.filename == grid and item.filename not in targets:
                    data = _mark_griglia(data.decode("utf-8"), sst, marks).encode("utf-8")
                elif item.filename in targets:
                    x = _set_cells(data.decode("utf-8"), colli_col, targets[item.filename], plain_col)
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


def _inline_str(ref, text, style):
    import html
    t = html.escape(str(text), quote=False).replace("\n", "&#10;")
    return f'<c r="{ref}"{style} t="inlineStr"><is><t xml:space="preserve">{t}</t></is></c>'


def _set_text_cells(xml, values):
    """values: {格子（如 F2）: 文字}：写成文字格（保留原来的格式）；没有这一格就加上"""
    for ref, text in values.items():
        m = re.match(r"([A-Z]+)(\d+)$", ref)
        if not m:
            continue
        col, row = m.group(1), int(m.group(2))
        cell_re = re.compile(r'<c\b[^>]*\br="' + ref + r'"[^>]*?(?:/>|>.*?</c>)', re.S)
        cm = cell_re.search(xml)
        style = ""
        if cm:
            st = re.search(r'\bs="(\d+)"', cm.group(0).split(">")[0])
            style = f' s="{st.group(1)}"' if st else ""
            xml = xml[:cm.start()] + _inline_str(ref, text, style) + xml[cm.end():]
            continue
        row_re = re.compile(r'<row\b[^>]*\br="' + str(row) + r'"[^>]*?(/>|>(.*?)</row>)', re.S)
        rm = row_re.search(xml)
        if not rm:
            continue
        new = _inline_str(ref, text, "")
        if rm.group(1) == "/>":
            xml = xml[:rm.start()] + rm.group(0)[:-2] + ">" + new + "</row>" + xml[rm.end():]
            continue
        inner = rm.group(2)
        pos = len(inner)
        for c2 in re.finditer(r'<c\b[^>]*\br="([A-Z]+)\d+"', inner):
            if _col_num(c2.group(1)) > _col_num(col):
                pos = c2.start()
                break
        xml = xml[:rm.start(2) + pos] + new + xml[rm.start(2) + pos:]
    return xml


def write_pdv_file(scheda, sid, lines, out_path, when=None):
    """
    门店采购：一家店一个订单文件。复制 SCHEDA（图片、格式都保留），GRIGLIA 页 Colli 列写这家店的箱数，
    表头填开票抬头（Fatturazione）、送货地址（Luogo di destinazione）、订单号、下单日期（从「Dati Filiali」页取这家店那一行）
    """
    pdv = scheda["pdv"]
    sheets = pdv.get("sheets") or [pdv["sheet"]]
    jobs = {}                                   # {工作表名: (Colli 列, 不订的行用的格式列, {行: 箱数}, 表头文字)}
    for k, t in enumerate(sheets):
        cols = (pdv.get("cols") or {}).get(t) or scheda["cols"]
        if "colli" not in cols:
            continue
        pc = cols.get("pack") or (cols["colli"] - 1)
        plain = _col_letter(pc) if pc and pc >= 1 and pc != cols["colli"] else None
        cells = {it["row"] - k * PDV_ROW_BLOCK: lines.get(it["row"]) for it in scheda["items"]
                 if it.get("sheet", sheets[0]) == t}
        head = pdv_header_values(dict(pdv, cells=(pdv.get("cells_by_sheet") or {}).get(t) or pdv["cells"]), sid, when)
        jobs[t] = (_col_letter(cols["colli"]), plain, cells, head)
    tmp = out_path + ".tmp"
    with zipfile.ZipFile(scheda["path"]) as zin:
        paths = _sheet_paths(zin)
        targets = {paths[t]: v for t, v in jobs.items() if t in paths}
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename in targets:
                    colli_col, plain_col, cells, head = targets[item.filename]
                    x = _set_cells(data.decode("utf-8"), colli_col, cells, plain_col)
                    x = _set_text_cells(x, head)
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


GRIGLIA_MARK = {"direct": "DIRETTO", "bms": "BMS"}     # 汇总页门店名上面写的去向


def _shared_strings(z):
    import html
    try:
        x = z.read("xl/sharedStrings.xml").decode("utf-8")
    except KeyError:
        return []
    out = []
    for si in re.finditer(r"<si\b[^>]*>(.*?)</si>", x, re.S):
        out.append(html.unescape("".join(re.findall(r"<t\b[^>]*>(.*?)</t>", si.group(1), re.S))))
    return out


def _mark_griglia(xml, sst, marks):
    """
    汇总页（GRIGLIA PER TUTTI PDV）：在每个门店名的上一行写去向（marks: {门店页名: 文字}），
    用门店名那一格的格式；门店名是合并格时上面也合并同样宽度。
    """
    import html
    found = {}
    for cm in _CELL_RE.finditer(xml):
        r = int(cm.group(2))
        if r < 2 or r > 15:
            continue
        cell = cm.group(0)
        tag = cell.split(">")[0]
        txt = None
        v = re.search(r"<v>(.*?)</v>", cell, re.S)
        if 't="s"' in tag and v:
            i = int(v.group(1))
            txt = sst[i] if 0 <= i < len(sst) else None
        elif 't="inlineStr"' in tag or 't="str"' in tag:
            t = re.search(r"<t\b[^>]*>(.*?)</t>", cell, re.S) or v
            txt = html.unescape(t.group(1)) if t else None
        name = _norm(txt) if txt else ""
        for sheet in marks:
            if name and name == _norm(sheet) and sheet not in found:
                found[sheet] = (r, _col_num(cm.group(1)), _cell_style(cell, f"{cm.group(1)}{r}"))
    if not found:
        return xml
    values, styles = {}, {}
    merges = re.findall(r'<mergeCell ref="([A-Z]+)(\d+):([A-Z]+)(\d+)"\s*/>', xml)
    new_merges = []
    for sheet, (r, c, st) in found.items():
        values.setdefault(r - 1, {})[c] = marks[sheet] or None
        styles[(r - 1, c)] = st
        for c1, r1, c2, r2 in merges:
            if _col_num(c1) == c and int(r1) == r == int(r2) and _col_num(c2) > c:
                rng = (c, r - 1, _col_num(c2))
                clash = any(int(a2) <= r - 1 <= int(b2) and _col_num(a1) <= rng[2] and _col_num(b1) >= c
                            for a1, a2, b1, b2 in merges)
                if not clash:
                    new_merges.append(f'<mergeCell ref="{c1}{r - 1}:{c2}{r - 1}"/>')
                    for cc in range(c + 1, rng[2] + 1):
                        values[r - 1].setdefault(cc, None)
                        styles[(r - 1, cc)] = st
    xml = _set_row_cells(xml, values, styles)
    if new_merges:
        m = re.search(r'<mergeCells\b[^>]*count="(\d+)"[^>]*>', xml)
        if m:
            tag = m.group(0).replace(f'count="{m.group(1)}"', f'count="{int(m.group(1)) + len(new_merges)}"')
            xml = xml[:m.start()] + tag + "".join(new_merges) + xml[m.end():]
        elif "</sheetData>" in xml:
            xml = xml.replace("</sheetData>", f'</sheetData><mergeCells count="{len(new_merges)}">'
                              + "".join(new_merges) + "</mergeCells>", 1)
    return xml


def written_source(w, result=None):
    """生成的文件是哪个 SCHEDA 的：新版本记在第 7 项；老结果（只有 6 项）按「星期文件夹 + 供应商名」找回来"""
    if len(w) > 6:
        return w[6]
    day = os.path.basename(os.path.dirname(w[0]))
    for d, sc, _ in (result or {}).get("results", []):
        if sc["name"] == w[1] and d == day:
            return sc["path"]
    return None


def written_files(written):
    """生成的文件（去重，按顺序）：一家供应商一个文件，written 里每个去向一行，几行可能是同一个文件"""
    seen, out = set(), []
    for w in written:
        if w[0] not in seen:
            seen.add(w[0])
            out.append(w[0])
    return out


def plan_marks(plan):
    """{门店页名: 去向文字}：直接送店 / 并进 BMS 的；其余门店（这次不订）留空"""
    marks = {name: "" for name in STORE_SHEETS.values()}
    for sid, st in plan["stores"].items():
        if st["status"] in ("direct", "filled") and sid in STORE_SHEETS:
            marks[STORE_SHEETS[sid]] = GRIGLIA_MARK["direct"]
    b = plan.get("bms")
    if bms_ordered(b):
        for sid in (b.get("per_store") or {}):
            if sid in STORE_SHEETS:
                marks[STORE_SHEETS[sid]] = GRIGLIA_MARK["bms"]
    return marks


def write_plans(results, out_root, date_tag=None, on_file=None, calc=None):
    """
    results: [(星期, scheda, plan)]。一家供应商一个文件：直接送店的门店和并进 BMS 的门店都写在同一个文件里，
    汇总页门店名上面写明去向（DIRETTO / BMS）。
    calc: compute() 的结果；有的话各门店页加上 GIA、ROT.30d … 附加数据列
    返回 [(文件路径, 供应商, 去向, 箱数, 金额, 状态, SCHEDA 路径)]：每个去向一行（同一家供应商的路径相同）
    """
    date_tag = date_tag or datetime.now().strftime("%d%m")
    written = []
    todo = []
    for day, sc, plan in results:
        direct = [(sid, st) for sid, st in plan["stores"].items() if st["status"] in ("direct", "filled")]
        b = plan.get("bms")
        if direct or bms_ordered(b):          # BMS 不够起订量的不算（这次不订）
            todo.append((day, sc, plan, direct, b if bms_ordered(b) else None))
    on_file = on_file or _noop
    for n, (day, sc, plan, direct, b) in enumerate(todo):
        folder = os.path.join(out_root, day)
        os.makedirs(folder, exist_ok=True)
        safe = re.sub(r'[\\/:*?"<>|]', "_", sc["name"])
        if sc.get("pdv"):
            # 门店采购：一家店一个文件（送货地址、开票抬头各不一样）
            for sid, st in direct:
                p = os.path.join(folder, f"Ordine_{safe}_{STORE_SHEETS[sid]}_{date_tag}.xlsx")
                on_file(n, len(todo), os.path.basename(p))
                write_pdv_file(sc, sid, st["lines"], p)
                written.append((p, sc["name"], STORE_SHEETS[sid], st["colli"], st["amount"], st["status"], sc["path"]))
            continue
        extra = extra_data(sc, calc.get("demand"), calc.get("base"), calc.get("days")) if calc else None
        heads = extra_headers(calc.get("days")) if calc else None
        extra, heads = file_extra(extra, heads)
        lines = {sid: st["lines"] for sid, st in direct}
        if b:
            for sid, ln in (b.get("per_store") or {}).items():
                lines[sid] = ln
        p = os.path.join(folder, f"Ordine_{safe}_{date_tag}.xlsx")
        on_file(n, len(todo), os.path.basename(p))
        write_order_file(sc, lines, p, extra, heads, marks=plan_marks(plan))
        for sid, st in direct:
            written.append((p, sc["name"], STORE_SHEETS[sid], st["colli"], st["amount"], st["status"], sc["path"]))
        if b:
            written.append((p, sc["name"], BMS, b["colli"], b["amount"], b["status"], sc["path"]))
    return written


STATUS_TEXT = {"direct": "直接送店", "filled": "凑单后送店", "bms": "并入 BMS", "none": "不用订",
               "wait": "这次不订（下次再看）",
               "short": "BMS 仍不够起订量", "merged": "并入 BMS（同一仓库）", "dropped": "不订（手动）",
               "pulled": "并入 BMS（帮其他店凑起订量）"}
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
    if "pulled" in b.get("how", []):
        parts.append("够起订量的门店整单并入，让其他店也订得起来")
    if "manual" in b.get("how", []):
        parts.append("手动选择全部并入 BMS")
    if "picked" in b.get("how", []):
        parts.append("手动选的门店")
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
                *[f"{x}天销量" for x in d], "库存够卖（天）", "订后够卖（天）", "判断", "建议件数", "建议箱数", "订货箱数",
                "金额 €", "备注"])
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
                got = (direct.get(it["row"], 0) + via_bms.get(it["row"], 0)) * it["pack"]   # 这家店这次一共到货的件数
                after = _fmt_days(cover_days(x, got)) if x else None
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
                    if st["status"] == "pulled" and dest == BMS:
                        note.append("并入帮其他店凑起订量")
                    ws2.append([day, sc["name"], STORE_SHEETS[sid], dest, item_code(it), it["ean"],
                                it["desc"], it["pack"], it["price"], x.get("inv_raw", x["inv"]) if x else None,
                                *((x["sales"] if x else [None] * 4)), _fmt_days(cover_days(x)) if x else None, after,
                                x["decision"] if x else "数据库没有", x["sugg"] if x else None, sg or None, n or None,
                                round(n * it["pack"] * it["price"], 2) if n else None, "、".join(note)])
                    ws2.cell(ws2.max_row, 6).number_format = "@"
    ws2.freeze_panes = "A2"
    for col, w in zip("ABCDEFGHIJKLMNOPQRSTUVW", (6, 22, 12, 12, 10, 15, 40, 8, 8, 8, 8, 8, 8, 8, 10, 10, 24, 8, 8, 8, 10, 16)):
        ws2.column_dimensions[col].width = w
    wb.save(path)
    return path


# =====================================================================
# 界面后台线程调用：① 计算（读 SCHEDA + 查数据库） ② 配货（判断起订量 / 凑单 / BMS + 写文件）
# =====================================================================
COMPUTE_STEPS = ["读取供应商清单", "查询门店销量与库存", "计算建议箱数"]
ALLOCATE_STEPS = ["判断起订量、凑单和 BMS", "生成订单文件", "生成汇总表"]


def compute(db, schede_paths, stores, days_list, reorder_cycle, log=print, use_supplier_cycles=True, algo=None,
            progress=None, cancel=None, moq_overrides=None, transit=None):
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

    import time
    t0 = time.time()
    demand = demand_by_store(db, schede, stores, days_list, reorder_cycle, use_supplier_cycles, algo, on_store)
    log(f"数据库查询用了 {time.time() - t0:.0f} 秒")
    progress(1, "done", f"{len(stores)} / {len(stores)}", "", 90.0)
    progress(2, "running", "", "", 92.0)
    missing = demand.pop("_missing", [])
    if missing:
        log(f"⚠ {len(missing)} 个商品在数据库里找不到编号，跳过")
    n_tr = apply_transit(demand, schede, transit)
    if n_tr:
        log(f"在途：{n_tr} 个商品已经订了还没到货，建议量里扣掉了")
    base = {sc["path"]: base_colli(sc, demand, stores) for sc in schede}
    base_sugg = {sc["path"]: base_colli(sc, demand, stores, transit=False) for sc in schede} if n_tr else base
    progress(2, "done", "", "", 100.0)
    return {"schede": schede, "demand": demand, "base": base, "missing": missing, "errors": errors,
            "stores": list(stores), "days": list(days_list), "transit": transit, "base_sugg": base_sugg}


def allocate(calc, colli, fill, out_root, log=print, date_tag=None, edited=None, only=None, progress=None, modes=None,
             picks=None):
    """
    calc: compute() 的结果；colli: {路径: {门店号: {行号: 箱数}}}（建议值或手动改过的值）。
    picks: {路径: {"stores": [...], "force": bool}}：配货前「BMS 调整」手动定的进 BMS 的门店（按 manual 配）
    only: 只处理这些路径（None = 全部）。返回 {"results", "written", "summary", "out_root"}
    """
    progress = progress or _noop
    stores = calc["stores"]
    results = []
    todo = [sc for sc in calc["schede"] if only is None or sc["path"] in only]
    for i, sc in enumerate(todo):
        progress(0, "running", f"{i + 1} / {len(todo)}", sc["name"], 40.0 * i / max(1, len(todo)))
        pk = (picks or {}).get(sc["path"])
        mode = "pdv" if sc.get("pdv") else "manual" if pk is not None else (modes or {}).get(sc["path"], "auto")
        plan = plan_supplier(sc, calc["demand"], fill, stores, colli.get(sc["path"], {}), mode, picks=pk)
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
    progress(1, "done", f"{len(written_files(written))}", "", 90.0)
    progress(2, "running", "", "", 92.0)
    summary = os.path.join(out_root, f"配货汇总_{date_tag or datetime.now().strftime('%d%m')}.xlsx")
    export_summary(results, summary, calc["demand"], calc["base"], edited, calc.get("days"))
    tfile = None
    if calc.get("transfers"):
        tfile = os.path.join(out_root, f"调货单_{date_tag or datetime.now().strftime('%d%m')}.xlsx")
        try:
            export_transfers(calc["transfers"], tfile)
            log(f"调货单：{len(calc['transfers'])} 个商品 → {os.path.basename(tfile)}")
        except Exception as e:
            log(f"⚠ 调货单生成失败：{e}")
            tfile = None
    progress(2, "done", "", "", 100.0)
    log(f"完成：生成 {len(written_files(written))} 个订单文件 → {out_root}")
    return {"results": results, "written": written, "summary": summary, "out_root": out_root,
            "transfers_file": tfile}


def reallocate_one(calc, colli, fill, result, index, mode, edited=None, log=print, picks=None):
    """
    配货结果页手动改一个供应商的去向：重新配这一家，删掉它在这次文件夹里的旧文件、写新文件，重做汇总表。
    只删这一个 SCHEDA（这个星期的这家供应商）的文件：同一家供应商在别的星期也有 SCHEDA 时不能动它的文件。
    """
    day, sc, _old = result["results"][index]
    plan = plan_supplier(sc, calc["demand"], fill, calc["stores"], colli.get(sc["path"], {}), mode, picks=picks)
    keep = []
    for w in result["written"]:
        if written_source(w, result) == sc["path"]:
            try:
                if os.path.exists(w[0]):
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
    log(f"手动调整 {sc['name']}：{mode}，生成 {len(written_files(new))} 个文件")
    return dict(result, results=results, written=written)


def run(db, schede_paths, stores, days_list, reorder_cycle, fill, out_root, log=print, date_tag=None):
    """计算 + 配货一次做完（不手动改）"""
    calc = compute(db, schede_paths, stores, days_list, reorder_cycle, log)
    res = allocate(calc, calc["base"], fill, out_root, log, date_tag)
    res.update(missing=calc["missing"], errors=calc["errors"])
    return res
