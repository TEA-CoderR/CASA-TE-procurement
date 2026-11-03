"""
已经订了、还没到货的商品（在途）——2026-11-03 用户：用共享盘「01 RECORD ORDINE」里的历史订单明细，结合采购订单跟踪，
看哪些商品已经订过但还没到货。

文件夹（settings tracking.record，默认 RECORD_DEFAULT）：
  01 FOIANO … 05 S.CONCORDIO   门店自己订的（门店采购），里面按月份分子文件夹，文件名大致是「Ordine 供应商 门店 ddmm.xlsx」
  06 RECORD CENTRALE ACQUISTI  总仓订的（总仓采购），各门店页 / GRIGLIA PER TUTTI PDV
  07 RECORD DETERSIVI …        DETERSIVO 每周比价的结果（日期 \\ 门店 \\ ORDINE \\ 确认订单_*.xlsx）

规则（用户定）：
  · 在途箱数从建议量里扣（一开始说只显示，同一天改成要扣）：总仓 / 门店采购 store_orders.apply_transit + colli_for，
    检查清单 order_check.init_boxes（整箱扣）
  · 订单跟踪里超过 MAX_DAYS（60）天还没标到货的单不算（多半是忘了点到货）
  · DETERSIVO 订单跟踪不记到货 → 不算在途；改成检查清单前把上一周的订单列出来，人勾哪些没到 → 生成扣减文件
    （deter_last_order / write_elim），原来的上传扣减文件留着

一张没到货的单对哪个文件：手动指定的（订单字段 record：相对 RECORD 文件夹的路径，"-" = 没有文件）> 程序生成的订单文件
（字段 file）> 自动找（门店文件夹或 06、供应商名对得上、文件日期在下单日 −7 … +10 天，取最近的）。
不依赖界面，测试 test_order_record.py。
"""
import os
import re
import json
import difflib
import threading
import time
from datetime import date, datetime, timedelta

RECORD_DEFAULT = r"D:\SynologyDrive\SynologyDrive\2 Griglia per gli ordini\01 RECORD ORDINE"
MAX_DAYS = 60            # 下单超过这么多天还没标到货：不算在途
SCAN_DAYS = 90           # 只看最近这么多天的订单文件（旧文件不读，省时间）
DATE_BEFORE, DATE_AFTER = 7, 10      # 文件日期在下单日前 7 天 … 后 10 天
CACHE_VER = 1
OPEN_STATES = ("ordered", "confirmed", "partial", "stuck")
STORE_DIRS = {"01": "1", "02": "2", "03": "3", "04": "4", "05": "5", "06": "C", "07": "D"}
STORE_NAMES = {"1": "FOIANO", "2": "MARLIA", "3": "S.FILIPPO", "4": "S.MARCO", "5": "S.CONCORDIO"}
NO_FILE = "-"
# 文件名里不是供应商名的词
_STOP = set("""ORDINE ORDINI ORD SCHEDA COPY COPIA FOIANO CAPANNORI MARLIA MARLYA PDV BMS BATCH SAN S SANFILIPPO FILIPPO
MARCO CONCORDIO SMARCO SFILIPPO SCONCORDIO URGENTE STAGIONALE PROMO CONFERMA NON ARRIVA MINIMO INVERNALE ESTIVO
SPA SRL SAS SNC XLSX XLS DI E C A SOCIO UNICO SOC COOPERATIVA""".split())


# ===================================================================== 小工具
def ean_key(v):
    s = str(v or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s.lstrip("0") if s.isdigit() else ""


def code_key(v):
    if isinstance(v, float) and v == int(v):
        v = int(v)
    return str(v or "").strip()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        try:
            return float(str(v).replace(",", "."))
        except (TypeError, ValueError):
            return 0.0


def folder_store(rel):
    """相对 RECORD 文件夹的路径 → "1".."5"（门店文件夹）/ "C"（06 总仓）/ "D"（07 DETERSIVO）/ None"""
    top = re.split(r"[\\/]", str(rel or ""), 1)[0].strip()
    return STORE_DIRS.get(top[:2]) if top[:2].isdigit() else None


def file_date(name, mtime):
    """
    文件名里的日期（「… 2610.xlsx」= 26/10，「07.08」，「201026」= 20/10/26，「10626」= 10/6/26）；
    只认修改日期前 30 天 … 后 3 天之内的（「0726_batch」「2308_batch」这种是模板的版本，不是下单日）。认不出 → 修改日期
    """
    mt = mtime if isinstance(mtime, date) else datetime.fromtimestamp(mtime).date()
    base = re.sub(r"\.(xlsx|xlsm|xls|et)$", "", os.path.basename(str(name)), flags=re.I)
    best = None
    cands = []
    for m in re.finditer(r"(?<!\d)(\d{1,2})[._\-/](\d{1,2})(?:[._\-/](\d{2,4}))?(?!\d)", base):
        cands.append((int(m.group(1)), int(m.group(2))))
    for m in re.finditer(r"(?<!\d)(\d{3,6})(?!\d)", base):
        s = m.group(1)
        if len(s) == 4:
            cands.append((int(s[:2]), int(s[2:])))
        elif len(s) == 3:
            cands.append((int(s[:1]), int(s[1:])))
        elif len(s) == 6:
            cands.append((int(s[:2]), int(s[2:4])))
        elif len(s) == 5:
            cands.append((int(s[:1]), int(s[1:3])))
    for dd, mm in cands:
        for y in (mt.year, mt.year - 1):
            try:
                d = date(y, mm, dd)
            except ValueError:
                continue
            diff = (mt - d).days
            if -3 <= diff <= 30 and (best is None or abs(diff) < abs((mt - best).days)):
                best = d
    return best or mt


def name_words(name):
    """文件名 → 可能是供应商名的词（大写）"""
    base = re.sub(r"\.(xlsx|xlsm|xls|et)$", "", os.path.basename(str(name)), flags=re.I)
    base = re.sub(r"\(\d+\)", " ", base)
    out = []
    for w in re.split(r"[^A-Za-z0-9'À-ÿ]+", base):
        u = w.upper().strip("'")
        if len(u) < 2 or u in _STOP or u.isdigit() or re.fullmatch(r"\d+PDV|\d+[A-Z]?", u):
            continue
        out.append(u)
    return out


def _key(s):
    from order_tracking import sup_key
    return sup_key(s)


def supplier_score(supplier, words):
    """订单跟踪里的供应商名 和 文件名的词 有多像（0–1）"""
    k = _key(str(supplier or "").split("(")[0]) or _key(supplier)
    if not k or not words:
        return 0.0
    joined = "".join(_key(w) for w in words)
    if len(k) >= 3 and (k in joined or (len(k) >= 5 and k[:5] in joined)):
        return 1.0
    for w in words:
        wk = _key(w)
        if len(wk) >= 4 and (k.startswith(wk) or wk.startswith(k[:max(4, min(len(k), 6))])):
            return 0.95
    return difflib.SequenceMatcher(None, k, joined[:len(k) + 4]).ratio()


# ===================================================================== 列出文件、读明细
def list_files(root, today=None, days=SCAN_DAYS):
    """01–06 文件夹里最近 days 天的订单文件 → [{rel, path, store, date, mtime, size, words}]（rel 用 / 分隔）"""
    today = today or date.today()
    out = []
    if not root or not os.path.isdir(root):
        return out
    for dp, dns, fns in os.walk(root):
        rel_dir = os.path.relpath(dp, root)
        st_ = folder_store(rel_dir) if rel_dir != "." else None
        if rel_dir == ".":
            dns[:] = [d for d in dns if STORE_DIRS.get(d[:2]) in ("1", "2", "3", "4", "5", "C")]
            continue
        dns[:] = [d for d in dns if not d.startswith(("_", "."))]
        for fn in fns:
            if fn.startswith(("~$", ".")) or not fn.lower().endswith((".xlsx", ".xlsm")):
                continue
            p = os.path.join(dp, fn)
            try:
                s = os.stat(p)
            except OSError:
                continue
            d = file_date(fn, s.st_mtime)
            if (today - d).days > days:
                continue
            out.append({"rel": os.path.join(rel_dir, fn).replace("\\", "/"), "path": p, "store": st_, "date": d,
                        "mtime": s.st_mtime, "size": s.st_size, "words": name_words(fn)})
    return out


def _dest_store(wb, grid):
    """GRIGLIA 页表头「Luogo di destinazione」那一格写的是哪家店"""
    import store_orders as SO
    ws = wb[grid]
    for row in ws.iter_rows(min_row=1, max_row=4, values_only=True):
        cells = list(row)
        for c, v in enumerate(cells):
            if SO._norm(v).replace(" ", "").rstrip(":") == "LUOGODIDESTINAZIONE":
                for x in cells[c + 1:c + 4]:
                    sid = SO._store_of_text(x)
                    if sid:
                        return str(sid)
    return None


def read_lines(path, folder_sid=None):
    """
    一个订单文件 → {门店号 "1".."5" / "BMS" / "?": [{ean, code, desc, colli, pack}]}（只要 Colli > 0 的行）
    有门店页（FOIANO / MARLIA …）按门店页；只有 GRIGLIA 页的（门店自己订）算在文件夹那家店，06 里的看「送货地址」；
    只有 GRIGLIA PER TUTTI PDV 的算 BMS
    """
    import store_orders as SO
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        titles = {ws.title.strip().upper(): ws.title for ws in wb.worksheets}
        sheets = [(str(sid), titles[n]) for sid, n in SO.STORE_SHEETS.items() if n in titles]
        if not sheets:
            grids = [t for k, t in titles.items() if k.startswith("GRIGLIA") and "TUTTI" not in k]
            if grids:
                sid = folder_sid if folder_sid in STORE_NAMES else (_dest_store(wb, grids[0]) or "?")
                sheets = [(sid, g) for g in grids]
            else:
                sheets = [("BMS", t) for k, t in titles.items() if k.startswith("GRIGLIA")]
        out = {}
        for sid, t in sheets:
            ws = wb[t]
            cols, hdr = SO._find_header(ws)
            if not hdr:
                continue
            for row in ws.iter_rows(min_row=hdr + 1, values_only=True):
                def g(k):
                    c = cols.get(k)
                    return row[c - 1] if c and c - 1 < len(row) else None
                n = _num(g("colli"))
                if n <= 0:
                    continue
                e, c = ean_key(g("ean")), code_key(g("code"))
                if not e and not c:
                    continue
                out.setdefault(sid, []).append({"ean": e, "code": c, "desc": str(g("desc") or "").strip(),
                                                "colli": n, "pack": _num(g("pack")) or 1})
        return out
    finally:
        wb.close()


class Index:
    """读过的订单文件明细（cache/record_index.json：相对路径 + 大小 + 修改时间一样就不再打开）"""

    def __init__(self, cache_path=None):
        self.cache_path = cache_path
        self.data = {}
        if cache_path and os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as f:
                    d = json.load(f)
                if d.get("ver") == CACHE_VER:
                    self.data = d.get("files") or {}
            except Exception:
                self.data = {}
        self._dirty = False

    @staticmethod
    def _sig(f):
        return f"{f['size']}|{int(f['mtime'])}"

    def get(self, f):
        e = self.data.get(f["rel"])
        return e if e and e.get("sig") == self._sig(f) else None

    def lines(self, f, log=None):
        """这个文件的明细（读过、没改过就用缓存）；读不了 → {}，ent["error"] 记原因"""
        e = self.get(f)
        if e is None:
            try:
                e = {"sig": self._sig(f), "lines": read_lines(f["path"], f.get("store"))}
            except Exception as ex:
                e = {"sig": self._sig(f), "lines": {}, "error": str(ex)[:200]}
                if log:
                    log(f"⚠ {f['rel']}：{ex}")
            self.data[f["rel"]] = e
            self._dirty = True
        return e.get("lines") or {}

    def save(self, keep=None):
        if keep is not None:
            for k in list(self.data):
                if k not in keep:
                    del self.data[k]
                    self._dirty = True
        if not (self.cache_path and self._dirty):
            return
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as fh:
                json.dump({"ver": CACHE_VER, "files": self.data}, fh, ensure_ascii=False)
            os.replace(tmp, self.cache_path)
            self._dirty = False
        except OSError:
            pass


# ===================================================================== 对单
def open_orders(rows, today=None, max_days=MAX_DAYS):
    """订单跟踪里算在途的单：没到货、没取消、不是 DETERSIVO、不是待登记、下单不超过 max_days 天"""
    today = today or date.today()
    out = []
    for r in rows:
        if r.get("status") not in OPEN_STATES or not r.get("date"):
            continue
        if (today - r["date"]).days > max_days:
            continue
        out.append(r)
    return out


def match(order, files):
    """一张单 → 最像的文件（files 是 list_files 的结果）；没有 None"""
    sid = str(order.get("store") or "")
    best, bkey = None, None
    for f in files:
        if f["store"] != "C" and f["store"] != sid:      # BMS 单只看 06；门店单看那家店的文件夹 + 06
            continue
        diff = (f["date"] - order["date"]).days
        if diff < -DATE_BEFORE or diff > DATE_AFTER:
            continue
        sc = supplier_score(order.get("supplier"), f["words"])
        if sc < 0.8:
            continue
        k = (-sc, abs(diff), f["store"] == "C")
        if bkey is None or k < bkey:
            best, bkey = f, k
    return best


def _abs_file(root, path):
    if not path:
        return None
    p = path if os.path.isabs(path) else os.path.join(root or "", path)
    return p if os.path.exists(p) else None


def resolve(orders, files, root=None):
    """每张单 → (文件 dict 或 None, 怎么来的 "manual" / "program" / "auto" / "none")"""
    by_rel = {f["rel"]: f for f in files}
    out = {}
    for o in orders:
        rec = str(o.get("record") or "")
        if rec == NO_FILE:
            out[o["id"]] = (None, "none")
            continue
        if rec:
            f = by_rel.get(rec.replace("\\", "/"))
            if f is None:
                p = _abs_file(root, rec)
                if p:
                    s = os.stat(p)
                    f = {"rel": rec.replace("\\", "/"), "path": p, "store": folder_store(rec), "date": o["date"],
                         "mtime": s.st_mtime, "size": s.st_size, "words": name_words(p)}
            out[o["id"]] = (f, "manual") if f else (None, "none")
            continue
        p = _abs_file(None, o.get("file"))
        if p and p.lower().endswith((".xlsx", ".xlsm")):
            s = os.stat(p)
            sid = str(o.get("store") or "")
            out[o["id"]] = ({"rel": p, "path": p, "store": sid if sid in STORE_NAMES else "C", "date": o["date"],
                             "mtime": s.st_mtime, "size": s.st_size, "words": name_words(p)}, "program")
            continue
        f = match(o, files)
        out[o["id"]] = (f, "auto") if f else (None, "none")
    return out


def order_lines(order, f, lines):
    """这张单在这个文件里是哪些行：门店单 → 那家店的；BMS 单 → 所有门店的（[(门店号, 行)]）"""
    sid = str(order.get("store") or "")
    if sid in STORE_NAMES:
        if sid in lines:
            return [(sid, x) for x in lines[sid]]
        if f and f.get("store") == sid and "?" in lines:
            return [(sid, x) for x in lines["?"]]
        return []
    return [(s, x) for s, xs in lines.items() for x in xs]


def in_transit(orders, resolved, index, log=None):
    """
    → {"by_ean": {(门店号, 条码): 信息}, "by_code": {(门店号, 编号): 信息}, "orders": {单号: {file, how, n, colli}}}
    信息 = {"colli", "pcs", "orders": [(下单日 iso, 供应商, 箱数)]}。同一个文件里同一家店只算一次（门店单 + BMS 单对到同一个
    文件时不重复）
    """
    by_ean, by_code, info = {}, {}, {}
    used = set()
    for o in sorted(orders, key=lambda o: (str(o.get("store")) == "BMS", str(o["date"]))):
        f, how = resolved.get(o["id"], (None, "none"))
        if f is None:
            info[o["id"]] = {"file": None, "how": how, "n": 0, "colli": 0}
            continue
        lines = index.lines(f, log)
        ls = order_lines(o, f, lines)
        n, tot = 0, 0.0
        for sid, x in ls:
            if (f["rel"], sid) in used:
                continue
            n += 1
            tot += x["colli"]
            ent_o = (o["date"].isoformat(), o.get("supplier") or "", x["colli"])
            for key, d in (((sid, x["ean"]), by_ean), ((sid, x["code"]), by_code)):
                if not key[1]:
                    continue
                e = d.setdefault(key, {"colli": 0.0, "pcs": 0.0, "orders": [], "desc": x["desc"]})
                e["colli"] += x["colli"]
                e["pcs"] += x["colli"] * (x["pack"] or 1)
                e["orders"].append(ent_o)
        for sid in {s for s, _ in ls}:
            used.add((f["rel"], sid))
        info[o["id"]] = {"file": f["rel"], "path": f["path"], "how": how, "n": n, "colli": tot,
                         "error": (index.data.get(f["rel"]) or {}).get("error")}
    return {"by_ean": by_ean, "by_code": by_code, "orders": info}


def lookup(transit, sid, ean=None, code=None):
    """这家店这个商品在途（先条码再编号）；没有 None"""
    if not transit:
        return None
    sid = str(sid)
    e = ean_key(ean)
    if e and (sid, e) in transit["by_ean"]:
        return transit["by_ean"][(sid, e)]
    c = code_key(code)
    if c and (sid, c) in transit["by_code"]:
        return transit["by_code"][(sid, c)]
    return None


def transit_text(t):
    """鼠标提示：哪几张单"""
    if not t:
        return ""
    return "\n".join(f"{d[8:10]}/{d[5:7]} · {s} · {c:g}" for d, s, c in t["orders"])


# ===================================================================== 一步到位（页面 / 线程里用）
def record_root(settings):
    cfg = (settings or {}).get("tracking") or {}
    return cfg.get("record") or RECORD_DEFAULT


def tracking_folder(settings):
    f = ((settings or {}).get("tracking") or {}).get("folder") or ""
    return f if f and os.path.isdir(f) else ""


def cache_path():
    from settings import app_dir
    return os.path.join(app_dir(), "cache", "record_index.json")


def compute(settings, rows=None, today=None, log=None, cache=True):
    """
    读订单跟踪 + RECORD 文件夹 → in_transit 的结果（另加 "root"、"files"、"ok"）。
    订单跟踪文件夹没设 / RECORD 文件夹不在 → ok False，空结果。在后台线程里调用（第一次要打开很多大文件）
    """
    import order_tracking as OT
    today = today or date.today()
    root = record_root(settings)
    empty = {"by_ean": {}, "by_code": {}, "orders": {}, "root": root, "files": 0, "ok": False}
    if rows is None:
        folder = tracking_folder(settings)
        if not folder:
            return empty
        rows = OT.rows(OT.Store(folder).reload(), today)
    orders = open_orders(rows, today)
    files = list_files(root, today)
    idx = Index(cache_path() if cache else None)
    res = in_transit(orders, resolve(orders, files, root), idx, log)
    idx.save(keep={f["rel"] for f in files} | {v["file"] for v in res["orders"].values() if v.get("file")})
    res.update(root=root, files=len(files), ok=os.path.isdir(root))
    return res


_MEMO = {"t": 0.0, "key": None, "res": None}
_LOCK = threading.Lock()


def cached(settings, rows=None, max_age=300, force=False, log=None):
    """
    compute 的结果记 max_age 秒（订单跟踪页、检查清单、总仓 / 门店采购都要用，免得每次都去扫文件夹）。
    rows 给了（订单跟踪页刚读完）或 force → 重算。几个线程同时要时只算一次
    """
    key = (record_root(settings), tracking_folder(settings))
    with _LOCK:
        m = _MEMO
        if not force and rows is None and m["key"] == key and m["res"] is not None and time.time() - m["t"] < max_age:
            return m["res"]
        res = compute(settings, rows=rows, log=log)
        m.update(t=time.time(), key=key, res=res)
        return res


def forget():
    """订单跟踪改过（到货、指定了文件）：下次重算"""
    with _LOCK:
        _MEMO.update(t=0.0, res=None)


# ===================================================================== DETERSIVO：上一周的订单 → 勾选没到货 → 扣减文件
def _date_of_folder(name):
    n = str(name).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(n, fmt).date()
        except ValueError:
            pass
    return None


def deter_candidates(roots, store_name, today=None, days=14):
    """
    DETERSIVO 最近 days 天（今天以前）的比价结果文件夹里这家店的确认订单：[(日期, 路径)]，新的在前。
    roots：几个「日期 \\ 门店」文件夹的上一层（程序的「4 比价结果」、RECORD 的 07 文件夹）；同一天两边都有取新的那份
    """
    today = today or date.today()
    found = {}
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dn in os.listdir(root):
            d = _date_of_folder(dn)
            if not d or not (0 < (today - d).days <= days):
                continue
            od = os.path.join(root, dn, store_name, "ORDINE")
            if not os.path.isdir(od):
                continue
            fs = [os.path.join(od, f) for f in os.listdir(od)
                  if f.lower().endswith((".xlsx", ".xlsm")) and not f.startswith("~$")]
            if not fs:
                continue
            p = max(fs, key=os.path.getmtime)
            if d not in found or os.path.getmtime(p) > os.path.getmtime(found[d]):
                found[d] = p
    return sorted(found.items(), reverse=True)


def deter_roots(settings, ws_root=None):
    """DETERSIVO 比价结果在哪：程序的「4 比价结果」（ws_root = DETERSIVO 工作文件夹）+ RECORD 文件夹里 07 开头的那个"""
    import workspace as W
    out = []
    wr = ws_root or (settings or {}).get("wholesale_root") or W.default_root()
    out.append(os.path.join(wr, W.RESULTS))
    rr = record_root(settings)
    if os.path.isdir(rr):
        out += [os.path.join(rr, d) for d in os.listdir(rr) if d[:2] == "07" and os.path.isdir(os.path.join(rr, d))]
    return out


def read_deter_order(path):
    """
    DETERSIVO 确认订单（ORDINE 页，第 1 行表头 CODICE EAN / ARTICOLO / COLLI，「供应商调整」后一列是分给哪家的报价文件）
    → [{ean, desc, colli, supplier}]；没分到供应商的（哪家都没有、没订）不要
    """
    from openpyxl import load_workbook
    labels = {}
    alloc = os.path.join(os.path.dirname(os.path.dirname(path)), "_备份", "last_allocation.json")
    try:
        with open(alloc, encoding="utf-8") as f:
            labels = json.load(f).get("supplier_labels") or {}
    except Exception:
        labels = {}
    norm = lambda s: re.sub(r"\s+", " ", str(s or "").replace("\xa0", " ")).strip()
    labels = {norm(k).lower(): v for k, v in labels.items()}
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb["ORDINE"] if "ORDINE" in wb.sheetnames else wb.worksheets[0]
        rows = ws.iter_rows(values_only=True)
        hdr = [norm(v).upper() for v in next(rows, ())]
        def col(*names):
            return next((i for i, h in enumerate(hdr) if h in names), None)
        ce, cd, cc = col("CODICE EAN", "EAN"), col("ARTICOLO", "DESCRIZIONE"), col("COLLI")
        adj = col("供应商调整")
        # 分给哪家：「供应商调整」后一列（表头空着）；老文件没有这一列 → 报价列表头前面那一列
        prices = [i for i, h in enumerate(hdr) if h.lower().endswith((".xlsx", ".xls"))]
        cs = adj + 1 if adj is not None else ((prices[0] - 1) if prices else None)
        out = []
        if ce is None or cc is None:
            return out
        for r in rows:
            e = ean_key(r[ce] if ce < len(r) else None)
            n = _num(r[cc] if cc < len(r) else None)
            if not e or n <= 0:
                continue
            sup = norm(r[cs]) if cs is not None and cs < len(r) else ""
            if not sup or not sup.lower().endswith((".xlsx", ".xls")):
                continue
            label = labels.get(sup.lower())
            if not label:
                try:
                    import supplier_names as SN
                    label = SN.label_of(sup)
                except Exception:
                    label = re.sub(r"\.(xlsx|xls)$", "", sup, flags=re.I)
            out.append({"ean": e, "desc": norm(r[cd]) if cd is not None and cd < len(r) else "", "colli": n,
                        "supplier": label})
        return out
    finally:
        wb.close()


ELIM_PREFIX = "Non arrivati"


def elim_name(day):
    """勾选生成的扣减文件名（一家店一个，上周那张单的日期）"""
    return f"{ELIM_PREFIX} {day:%d%m}.xlsx"


def read_elim_eans(folder):
    """这家店扣减文件夹里以前勾选生成的文件 → 勾过的条码（再打开窗口时还是勾着的）"""
    out = set()
    if not folder or not os.path.isdir(folder):
        return out
    from openpyxl import load_workbook
    for f in os.listdir(folder):
        if not f.startswith(ELIM_PREFIX) or not f.lower().endswith(".xlsx"):
            continue
        try:
            wb = load_workbook(os.path.join(folder, f), read_only=True, data_only=True)
            for r in wb.active.iter_rows(min_row=2, values_only=True):
                e = ean_key(r[0] if r else None)
                if e:
                    out.add(e)
            wb.close()
        except Exception:
            continue
    return out


def write_elim(lines, path):
    """勾了「没到货」的行 → 扣减文件（和比价的 eliminare 一样：A 列 CODICE EAN、COLLI 列）"""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "NON ARRIVATI"
    ws.append(["CODICE EAN", "ARTICOLO", "COLLI", "FORNITORE"])
    for x in lines:
        ws.append([x["ean"], x.get("desc") or "", x["colli"], x.get("supplier") or ""])
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 44
    ws.column_dimensions["D"].width = 16
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb.save(path)
    return path
