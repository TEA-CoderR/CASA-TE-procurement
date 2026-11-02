"""
采购订单跟踪（2026-11-02 用户：先用共享文件夹；先从 PIANO ACQUISTO MESILE 导入；程序里要有可改、可排序、清楚的表格）。
不依赖界面，可以单独测试。

数据放在共享文件夹（Synology Drive 同步）里的「Tracking ordini (programma)」：
  每台电脑 / 每个人只写自己的一个文件 log_<电脑>_<用户>.jsonl，一行一条修改（谁、什么时候、改了哪张单的哪些字段），只加不改。
  读的时候把所有人的文件合起来按时间排好、一条条套上去 → 每个字段以最后一次修改为准。
  为什么这样：同步盘上两个人同时改同一个文件会出「冲突副本」、丢数据；每人只写自己的文件就不会冲突，
  而且每张单的修改记录都留着（谁在什么时候确认、到货、改金额）。
订单字段（都是可选的）：
  date 下单日期 · store 门店（"1".."5" / "BMS"）· supplier 供应商（照写的）· buyer 谁下的 · amount 金额（不含税）
  confirmed 确认日期 · arrived 到货日期 · expected 手动填的预计到货（空 = 按这家供应商以往的到货天数推算）
  state 特殊情况（partial 部分到货 / stuck 卡住了 / cancel 已取消）· kind（deter = 洗涤用品批发商单，不跟踪确认 / 到货）
  note 备注 · ref 从 Excel 导入的位置（OTT!B12）· origin（excel / app）
从 Excel 同步（sync_excel）：程序里改过的字段不会被 Excel 盖掉；Excel 里新加的单、程序没动过的字段跟着 Excel 变。
"""
import os
import re
import json
import uuid
import socket
import getpass
import hashlib
import unicodedata
from datetime import datetime, date, timedelta, timezone

DATA_DIR = "Tracking ordini (programma)"
STORES = [("1", "FOIANO"), ("2", "MARLIA"), ("3", "S.FILIPPO"), ("4", "S.MARCO"), ("5", "S.CONCORDIO"), ("BMS", "BMS")]
STORE_NAMES = dict(STORES)
# Excel 每一块的标题「Gli ordini per Capannori」→ 门店（Capannori = 程序里的 MARLIA）
EXCEL_STORE = {"FOIANO": "1", "CAPANNORI": "2", "MARLIA": "2", "SANFILIPPO": "3", "SFILIPPO": "3", "SANMARCO": "4",
               "SMARCO": "4", "SANCONCORDIO": "5", "SCONCORDIO": "5", "BMS": "BMS"}
IVA = 0.22
PENDING_DAYS = 2          # 下单超过 2 天还没确认 → 「待确认」
LEAD_DEFAULT = 10         # 没有历史时按 10 天到货
LEAD_MIN_SAMPLES = 2      # 一家供应商至少 2 张到货的单才用它自己的天数
EXCEL_FIELDS = ("date", "store", "supplier", "buyer", "amount", "confirmed", "arrived", "state", "kind", "note", "ref",
                "origin")

# 状态（显示顺序）
# draft = 待登记：程序生成了订单文件、还没人确认真的发给供应商（2026-11-02 用户：以后都由程序自动登记，不手填）
STATUS = ["draft", "ordered", "confirmed", "partial", "arrived", "stuck", "cancel", "deter"]
NOT_ORDERED = {"cancel", "draft"}      # 不算进花费、预算、供应商统计
OPEN = {"ordered", "confirmed", "partial", "stuck"}


# ===================================================================== 小工具
def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _safe(s):
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(s or "")).strip("-") or "x"


def default_me():
    try:
        return getpass.getuser()
    except Exception:
        return "?"


def to_date(v):
    """ISO 文字 / date / datetime → date；其他 None"""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def iso(d):
    d = to_date(d)
    return d.isoformat() if d else None


def sup_key(name):
    """供应商名字 → 比较用的键：大写、去掉重音 / 标点 / 空格 / 公司后缀（ZAMbetti → ZAMBETTI，S.N Commerciale = S.N commerciale）"""
    raw = str(name or "").strip()
    s = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode().upper()
    s = s.replace("F.LLI", "FLLI").replace("F.LI", "FLLI")
    s = re.sub(r"\b(S\.?\s?R\.?\s?L|S\.?\s?P\.?\s?A|S\.?\s?A\.?\s?S|S\.?\s?N\.?\s?C|SOC\.?\s*COOP)\b\.?", " ", s)
    k = re.sub(r"[^A-Z0-9]", "", s)
    return k or raw.upper()


# ===================================================================== 数据（共享文件夹里的修改记录）
class Store:
    """
    orders  {单号: {字段: 值}}（已删除的不在里面）
    meta    {单号: {字段: (时间, 谁, 来源)}}：每个字段最后一次是谁改的（来源 excel / app），从 Excel 同步时用来判断能不能覆盖
    hist    {单号: [修改记录]}；budgets {"2026-10|1": 预算}；aliases {供应商键: 合并到的键}
    """

    def __init__(self, folder, me=None, host=None):
        self.folder = folder
        self.dir = os.path.join(folder, DATA_DIR)
        self.me = me or default_me()
        host = host or os.environ.get("COMPUTERNAME") or socket.gethostname()
        self.file = os.path.join(self.dir, f"log_{_safe(host)}_{_safe(self.me)}.jsonl")
        self._sig = None
        self._reset()

    def _reset(self):
        self.orders, self.meta, self.hist = {}, {}, {}
        self.budgets, self.budget_meta, self.aliases = {}, {}, {}
        self.deleted = {}          # {单号: 来源}：程序里删掉的单，从 Excel 同步时不再加回来
        self._seen = set()

    # ---- 读 ----
    def _files(self):
        try:
            return sorted(os.path.join(self.dir, f) for f in os.listdir(self.dir)
                          if f.lower().endswith(".jsonl") and f.startswith("log_"))
        except OSError:
            return []

    def signature(self):
        sig = []
        for p in self._files():
            try:
                st = os.stat(p)
                sig.append((os.path.basename(p), st.st_size, st.st_mtime_ns))
            except OSError:
                pass
        return tuple(sig)

    def changed(self):
        """别的电脑同步过来新的修改了吗"""
        return self.signature() != self._sig

    def reload(self):
        evs = []
        sig = self.signature()
        for p in self._files():
            try:
                with open(p, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            e = json.loads(line)
                        except ValueError:
                            continue                # 同步到一半的最后一行：下次再读
                        if isinstance(e, dict) and e.get("e") and e.get("t"):
                            evs.append(e)
            except OSError:
                continue
        self._reset()
        for e in sorted(evs, key=lambda e: (str(e["t"]), str(e["e"]))):
            self._apply(e)
        self._sig = sig
        return self

    def _apply(self, e):
        if e["e"] in self._seen:                # 同步盘的「冲突副本」里会有重复的
            return
        self._seen.add(e["e"])
        op, oid = e.get("op"), e.get("id")
        who = (e["t"], e.get("by") or "?", e.get("src") or "app")
        if op == "set" and oid:
            o = self.orders.setdefault(oid, {})
            m = self.meta.setdefault(oid, {})
            for k, v in (e.get("f") or {}).items():
                o[k] = v
                m[k] = who
            self.deleted.pop(oid, None)
            self.hist.setdefault(oid, []).append(e)
        elif op == "del" and oid:
            self.orders.pop(oid, None)
            self.meta.pop(oid, None)
            self.deleted[oid] = who[2]
            self.hist.setdefault(oid, []).append(e)
        elif op == "budget" and oid:
            if e.get("v") is None:
                self.budgets.pop(oid, None)
            else:
                self.budgets[oid] = float(e["v"])
            self.budget_meta[oid] = who
        elif op == "alias" and oid:
            if e.get("to") and e["to"] != oid:
                self.aliases[oid] = e["to"]
            else:
                self.aliases.pop(oid, None)

    # ---- 写（追加到自己的文件，马上生效）----
    def _write(self, evs):
        if not evs:
            return
        os.makedirs(self.dir, exist_ok=True)
        with open(self.file, "a", encoding="utf-8", newline="") as f:
            for e in evs:
                f.write(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        for e in evs:
            self._apply(e)
        self._sig = self.signature() if self._sig is not None and not self._others_changed() else self._sig

    def _others_changed(self):
        """除了自己刚写的文件，别人的有没有变（变了就别把签名更新掉，好让界面知道要重读）"""
        mine = os.path.basename(self.file)
        old = {n: (s, m) for n, s, m in (self._sig or ())}
        for n, s, m in self.signature():
            if n != mine and old.get(n) != (s, m):
                return True
        return False

    def _ev(self, op, oid, src="app", **kw):
        e = {"e": uuid.uuid4().hex, "t": now_iso(), "by": self.me, "op": op, "id": oid, "src": src}
        e.update(kw)
        return e

    def set(self, oid, fields, src="app"):
        fields = {k: v for k, v in fields.items()}
        if fields:
            self._write([self._ev("set", oid, src, f=fields)])

    def set_many(self, changes, src="app"):
        """[(单号, {字段: 值})] 一次写完"""
        self._write([self._ev("set", oid, src, f=f) for oid, f in changes if f])

    def add(self, fields):
        oid = "o" + uuid.uuid4().hex[:12]
        f = {"origin": "app"}
        f.update(fields)
        self.set(oid, f)
        return oid

    def delete(self, oids, src="app"):
        self._write([self._ev("del", oid, src) for oid in oids if oid in self.orders])

    def set_budget(self, month, store, value, src="app"):
        self._write([self._ev("budget", f"{month}|{store}", src, v=None if value in (None, "") else float(value))])

    def merge_suppliers(self, keys, target):
        """几个供应商键算成同一家（target）；target 自己不用写"""
        self._write([self._ev("alias", k, to=target) for k in keys if k != target])

    def unmerge(self, key):
        self._write([self._ev("alias", key, to="")])

    def canon(self, key):
        seen = set()
        while key in self.aliases and key not in seen:
            seen.add(key)
            key = self.aliases[key]
        return key


# ===================================================================== 状态、预计到货
def status(o):
    if o.get("state") == "cancel":
        return "cancel"
    if o.get("draft"):
        return "draft"
    if o.get("kind") == "deter":
        return "deter"
    if o.get("arrived"):
        return "partial" if o.get("state") == "partial" else "arrived"
    if o.get("state") == "partial":
        return "partial"
    if o.get("state") == "stuck":
        return "stuck"
    if o.get("confirmed"):
        return "confirmed"
    return "ordered"


def lead_times(st):
    """每家供应商（合并后的键）到货用了几天：{键: [天数…]}（下单 → 到货，0–120 天才算，其他多半是填错了）"""
    out = {}
    for o in st.orders.values():
        d0, d1 = to_date(o.get("date")), to_date(o.get("arrived"))
        if not d0 or not d1 or o.get("kind") == "deter" or o.get("state") == "cancel":
            continue
        n = (d1 - d0).days
        if 0 <= n <= 120:
            out.setdefault(st.canon(sup_key(o.get("supplier"))), []).append(n)
    return out


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def lead_of(leads, key, default=None):
    xs = leads.get(key) or []
    if len(xs) >= LEAD_MIN_SAMPLES:
        return _median(xs)
    return default


def default_lead(leads):
    allx = [n for xs in leads.values() for n in xs]
    m = _median(allx)
    return m if m is not None else LEAD_DEFAULT


def rows(st, today=None):
    """界面用的一行一张单（算好状态、预计到货、超期、用了几天、供应商显示名）"""
    today = today or date.today()
    leads = lead_times(st)
    dflt = default_lead(leads)
    # 供应商显示名：同一家（合并后的键）最常见的写法
    spell = {}
    for o in st.orders.values():
        k = st.canon(sup_key(o.get("supplier")))
        n = str(o.get("supplier") or "").strip()
        if n:
            spell.setdefault(k, {}).setdefault(n, 0)
            spell[k][n] += 1
    # 合并过的用合并到的那家自己的写法；一样多时：有小写的（Mega Cash 比 MEGACASH 好看）、长一点的
    disp = {}
    for k, c in spell.items():
        own = {n: x for n, x in c.items() if sup_key(n) == k} or c
        disp[k] = max(own.items(), key=lambda kv: (kv[1], kv[0] != kv[0].upper(), " " in kv[0], len(kv[0])))[0]
    out = []
    for oid, o in st.orders.items():
        s = status(o)
        k = st.canon(sup_key(o.get("supplier")))
        d0 = to_date(o.get("date"))
        conf, arr = to_date(o.get("confirmed")), to_date(o.get("arrived"))
        exp_manual = to_date(o.get("expected"))
        lead = lead_of(leads, k)
        exp = exp_manual or ((d0 + timedelta(days=round(lead if lead is not None else dflt))) if d0 else None)
        open_ = s in OPEN
        overdue = bool(open_ and exp and today > exp)
        pending = bool(s == "ordered" and d0 and (today - d0).days >= PENDING_DAYS)
        days = (arr - d0).days if (arr and d0) else ((today - d0).days if (d0 and open_) else None)
        m = st.meta.get(oid, {})
        last = max(m.values(), key=lambda w: w[0]) if m else None
        out.append({"id": oid, "date": d0, "store": str(o.get("store") or ""),
                    "store_name": STORE_NAMES.get(str(o.get("store") or ""), str(o.get("store") or "")),
                    "supplier": disp.get(k) or str(o.get("supplier") or ""), "raw_supplier": o.get("supplier") or "",
                    "sup_key": k, "buyer": str(o.get("buyer") or ""), "amount": o.get("amount"),
                    "status": s, "confirmed": conf, "arrived": arr, "expected": exp, "expected_manual": bool(exp_manual),
                    "lead": lead, "overdue": overdue, "pending": pending, "days": days, "open": open_,
                    "note": str(o.get("note") or ""), "ref": str(o.get("ref") or ""),
                    "origin": o.get("origin") or "", "last": last, "file": str(o.get("file") or ""),
                    "colli": o.get("colli"),
                    "late_by": (today - exp).days if overdue else 0})
    return out


def month_of(r):
    return r["date"].strftime("%Y-%m") if r.get("date") else ""


def summary(rs, st, month, today=None):
    """顶上几张卡：待登记 / 待确认 / 已超期 / 卡住了 / 本月下单和预算（都不含税）"""
    spent = sum(float(r["amount"] or 0) for r in rs if month_of(r) == month and r["status"] not in NOT_ORDERED)
    budget = sum(v for k, v in st.budgets.items() if k.startswith(month + "|"))
    return {"pending": sum(1 for r in rs if r["pending"]), "overdue": sum(1 for r in rs if r["overdue"]),
            "stuck": sum(1 for r in rs if r["status"] == "stuck"),
            "draft": sum(1 for r in rs if r["status"] == "draft"),
            "open": sum(1 for r in rs if r["open"]),
            "spent": spent, "spent_iva": spent * (1 + IVA), "budget": budget}


def supplier_stats(rs):
    """按供应商：单数、金额、到货天数（平均 / 中位 / 最长）、还没到的、超期的、最近一次下单"""
    out = {}
    for r in rs:
        if r["status"] in NOT_ORDERED:
            continue
        s = out.setdefault(r["sup_key"], {"key": r["sup_key"], "supplier": r["supplier"], "n": 0, "amount": 0.0,
                                         "leads": [], "open": 0, "overdue": 0, "last": None, "deter": 0,
                                         "names": set()})
        s["n"] += 1
        s["amount"] += float(r["amount"] or 0)
        s["names"].add(str(r["raw_supplier"]).strip())
        if r["status"] == "deter":
            s["deter"] += 1
        if r["arrived"] and r["date"] and 0 <= (r["arrived"] - r["date"]).days <= 120:
            s["leads"].append((r["arrived"] - r["date"]).days)
        s["open"] += int(r["open"])
        s["overdue"] += int(r["overdue"])
        if r["date"] and (s["last"] is None or r["date"] > s["last"]):
            s["last"] = r["date"]
    for s in out.values():
        xs = s["leads"]
        s["avg"] = sum(xs) / len(xs) if xs else None
        s["med"] = _median(xs)
        s["max"] = max(xs) if xs else None
    return sorted(out.values(), key=lambda s: -s["amount"])


def budget_rows(rs, st):
    """预算表：每个月 × 门店：预算、已下单、占预算、单数（都不含税；含税的旧预算导入时已经 ÷ 1.22）"""
    agg = {}
    for r in rs:
        if r["status"] in NOT_ORDERED or not r["date"]:
            continue
        k = (month_of(r), r["store"])
        a = agg.setdefault(k, [0.0, 0])
        a[0] += float(r["amount"] or 0)
        a[1] += 1
    months = {m for m, _s in agg} | {k.split("|", 1)[0] for k in st.budgets}
    keys = {(m, s) for m in months for s, _n in STORES}             # 每个月每家店都有一行（没预算也能双击填）
    out = []
    for m, s in keys:
        amt, n = agg.get((m, s), (0.0, 0))
        b = st.budgets.get(f"{m}|{s}")
        out.append({"month": m, "store": s, "store_name": STORE_NAMES.get(s, s), "budget": b, "amount": amt,
                    "amount_iva": amt * (1 + IVA), "n": n, "pct": (amt / b) if b else None})
    order = {k: i for i, (k, _n) in enumerate(STORES)}
    out.sort(key=lambda x: order.get(x["store"], 99))
    out.sort(key=lambda x: x["month"], reverse=True)                # 新的月份在上
    return out


def suggest_merges(st, rs=None):
    """名字很像的供应商（一个是另一个的开头，例如 ZAMBETTI / ZAMBETTIINVERNALE）：[(键, [像它的键…])]，让人确认要不要合并"""
    keys = sorted({st.canon(sup_key(o.get("supplier"))) for o in st.orders.values()} - {""})
    out = []
    for k in keys:
        if len(k) < 4:
            continue
        sim = [x for x in keys if x != k and x.startswith(k)]
        if sim:
            out.append((k, sim))
    return out


# ===================================================================== 从 PIANO ACQUISTO MESILE 读
def _norm(v):
    return re.sub(r"[^A-Z0-9]", "", unicodedata.normalize("NFKD", str(v or "")).encode("ascii", "ignore").decode().upper())


_DMY = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?\s*$")


def parse_date(v, year=None):
    """Excel 里的日期：真日期 / 「10/06/2026」/「07/05」/ Excel 序号 → (date 或 None, 不是日期的文字 或 None)"""
    if v is None or v == "":
        return None, None
    if isinstance(v, datetime):
        return v.date(), None
    if isinstance(v, date):
        return v, None
    if isinstance(v, (int, float)):
        if 30000 < v < 60000:
            return date(1899, 12, 30) + timedelta(days=int(v)), None
        return None, str(v)
    s = str(v).strip()
    m = _DMY.match(s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        y = (int(y) + (2000 if len(y) == 2 else 0)) if y else (year or date.today().year)
        try:
            return date(y, mo, d), None
        except ValueError:
            return None, s
    try:
        return date.fromisoformat(s[:10]), None
    except ValueError:
        return None, s


def parse_amount(v):
    """金额：数字 / 「311.10 €」/「1.234,56」→ (float 或 None, 读不懂的文字 或 None)"""
    if v is None or v == "":
        return None, None
    if isinstance(v, (int, float)):
        return float(v), None
    s = re.sub(r"[€\s]", "", str(v))
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s), None
    except ValueError:
        return None, str(v).strip()


def _flag_text(text, out):
    """确认 / 到货格里写的字：DETER → 洗涤批发商单；卡住了 → stuck；到了一部分 → partial；其他进备注"""
    t = str(text or "").strip()
    if not t:
        return
    u = t.upper()
    if u.startswith("DETER"):
        out["kind"] = "deter"
    elif "卡住" in t or "BLOCC" in u:
        out["state"] = "stuck"
    elif "部分" in t or "PARZIAL" in u:
        out["state"] = "partial"
    else:
        out.setdefault("_notes", []).append(t)


def _layout(ws):
    """月份表的格式：表头行（「Data Ordine」那一行）和每一块 [(起始列, 门店, 备注列)]；不是月份表 (None, [])"""
    hr, starts = None, []
    for r in range(1, 13):
        cs = [c for c in range(1, min(ws.max_column, 80) + 1) if _norm(ws.cell(r, c).value) == "DATAORDINE"]
        if cs:
            hr, starts = r, cs
            break
    if not hr:
        return None, []
    blocks = []
    for c in starts:
        if _norm(ws.cell(hr, c + 1).value) != "FORNITORE":
            continue
        # 块标题「Gli ordini per …」：不一定正好在「Data Ordine」那一列上面（OTT / SET / Agosto / NOV 的 Capannori
        # 标题在 H 列、表头从 I 列开始）→ 在左右两列里找，取最近的一个
        store = None
        for dc in (0, -1, 1, -2, 2):
            if c + dc < 1:
                continue
            for r in range(1, hr):
                n = _norm(ws.cell(r, c + dc).value)
                if n.startswith("GLIORDINI"):
                    store = EXCEL_STORE.get(n.replace("GLIORDINIPER", "").replace("GLIORDINI", ""))
                    break
            if store:
                break
        if not store:
            continue
        note_col = c + 7 if _norm(ws.cell(hr, c + 7).value).startswith("NOT") else None
        blocks.append((c, store, note_col))
    return hr, blocks


def _block_iva(ws, hr, c):
    """
    月份表里这一块（一家门店）是不是按含税算的：上面几行的每周合计公式一半以上 ×1.22 就算含税（按块看：10 月表其他门店
    不含税了，BMS 那块还是 ×1.22、预算 109 800 = 90 000 × 1.22）。
    用户的 PIANO（2026-11-02 分析）：6–9 月几乎都 ×1.22、预算写「IVATO」；10 月起公式不乘了，预算也换成不含税
    （S.Filippo 9 月 39 204.70 ÷ 1.22 = 10 月 32 135.00），11 月表头把「IVATO」去掉了
    """
    n = k = 0
    for row in ws.iter_rows(min_row=1, max_row=max(1, hr - 1), min_col=c, max_col=c + 7, values_only=True):
        for v in row:
            if isinstance(v, str) and v.startswith("=") and "SUM(" in v.upper():
                refs = [int(x) for x in re.findall(r"[A-Z]+(\d+)", v.upper())]
                if not refs or min(refs) <= hr:                  # 本月合计（加的是上面几周的格子）不算
                    continue
                n += 1
                k += "*1.22" in v.replace(" ", "")
    return k, n


def _is_iva(k, n, sk, sn):
    """这一块 ×1.22 的过半 → 含税；一半一半看整张表"""
    if n and k * 2 != n:
        return k * 2 > n
    return sn > 0 and sk * 2 > sn


def read_excel(path):
    """
    读 PIANO ACQUISTO MESILE：每个有「Data Ordine」表头的工作表（GIUGNO_26 以后的新格式），每块一家门店。
    → (rows, budgets, sheets)：rows = [{id, date, store, supplier, buyer, amount, confirmed, arrived, state, kind, note,
    ref, origin}]，budgets = [(月份, 门店, 预算)]，sheets = 读了的工作表名
    """
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter
    wb = load_workbook(path, data_only=True)
    try:
        wf = load_workbook(path, read_only=True)               # 看公式（每周合计有没有 ×1.22）
    except Exception:
        wf = None
    rows, budgets, sheets = [], [], []
    for ws in wb.worksheets:
        hr, blocks = _layout(ws)
        if not blocks:
            continue
        # 这张表是哪个月：下单日期最多的那个月（预算记在这个月）
        cnt = {}
        for c, _s, _n in blocks:
            for r in range(hr + 1, ws.max_row + 1):
                if ws.cell(r, c + 1).value in (None, ""):
                    continue
                d, _ = parse_date(ws.cell(r, c).value)
                if d:
                    cnt[(d.year, d.month)] = cnt.get((d.year, d.month), 0) + 1
        if not cnt:
            continue
        ym = max(cnt.items(), key=lambda kv: kv[1])[0]
        month = f"{ym[0]:04d}-{ym[1]:02d}"
        sheets.append(ws.title)
        cnts = {c: (_block_iva(wf[ws.title], hr, c) if wf is not None and ws.title in wf.sheetnames else (0, 0))
                for c, _s, _n in blocks}
        sk, sn = sum(k for k, _n in cnts.values()), sum(n for _k, n in cnts.values())
        for c, store, note_col in blocks:
            iva = _is_iva(*cnts[c], sk, sn)
            for r in range(1, hr):                     # 预算：标题那一行「BADGET ACQUISTO」后面的数字
                labels = [cc for cc in range(c, c + 8) if "BADGET" in _norm(ws.cell(r, cc).value) or
                          "BUDGET" in _norm(ws.cell(r, cc).value)]
                if labels:
                    for cc in range(labels[0] + 1, c + 8):
                        v = ws.cell(r, cc).value
                        if isinstance(v, (int, float)) and v > 0:
                            # 6–9 月的表是含税的（每周合计 ×1.22、预算含税）→ 换成不含税，和订单金额一样（10 月起原表就不含税）
                            budgets.append((month, store, round(float(v) / (1 + IVA), 2) if iva else float(v)))
                            break
                    break
            occ = {}
            for r in range(hr + 1, ws.max_row + 1):
                sup = ws.cell(r, c + 1).value
                if sup in (None, "") or not str(sup).strip():
                    continue
                sup = str(int(sup)) if isinstance(sup, float) and sup == int(sup) else str(sup).strip()
                d, dtext = parse_date(ws.cell(r, c).value, ym[0])
                amt, atext = parse_amount(ws.cell(r, c + 3).value)
                out = {"_notes": []}
                conf, ctext = parse_date(ws.cell(r, c + 4).value, ym[0])
                arr, rtext = parse_date(ws.cell(r, c + 5).value, ym[0])
                _flag_text(ctext, out)
                _flag_text(rtext, out)
                for cc in (c, c + 1, c + 4, c + 5):    # 批注：「到了一部分」
                    cm = ws.cell(r, cc).comment
                    if cm and cm.text and cm.text.strip():
                        _flag_text(cm.text.strip(), out)
                if note_col:
                    _flag_text(ws.cell(r, note_col).value, out)
                if dtext:
                    out["_notes"].append(dtext)
                if atext:
                    out["_notes"].append(tr_amount(atext))
                buyer = ws.cell(r, c + 2).value
                k = (iso(d), sup_key(sup))
                occ[k] = occ.get(k, 0) + 1
                key = f"{ws.title}|{store}|{k[0]}|{k[1]}|{occ[k]}"
                notes = [x for x in dict.fromkeys(out.pop("_notes")) if x]
                row = {"id": "x" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12], "date": iso(d), "store": store,
                       "supplier": sup, "buyer": str(buyer).strip() if buyer not in (None, "") else None,
                       "amount": round(amt, 2) if amt is not None else None, "confirmed": iso(conf),
                       "arrived": iso(arr), "state": out.get("state"), "kind": out.get("kind"),
                       "note": " · ".join(notes) or None, "ref": f"{ws.title}!{get_column_letter(c + 1)}{r}",
                       "origin": "excel"}
                rows.append(row)
    return rows, budgets, sheets


def tr_amount(text):
    return f"金额：{text}"


def sync_excel(st, rows, budgets, sheets):
    """
    把 Excel 读到的单写进跟踪记录：新的单加进来；已有的单只更新「最后一次是 Excel 写的」字段（程序里改过的不动）；
    程序里删掉的单不再加回来；Excel 里已经没有的单（而且程序里没改过）删掉。返回 {"new", "updated", "removed", "kept"}
    """
    res = {"new": 0, "updated": 0, "removed": 0, "kept": 0}
    changes, seen = [], set()
    for r in rows:
        oid = r["id"]
        seen.add(oid)
        if st.deleted.get(oid) == "app":
            continue
        cur, meta = st.orders.get(oid), st.meta.get(oid, {})
        ch = {}
        for f in EXCEL_FIELDS:
            v = r.get(f)
            if cur is not None and cur.get(f) == v:
                continue
            if cur is None and v is None:
                continue
            w = meta.get(f)
            if w and w[2] != "excel":
                continue                                    # 程序里改过：以程序为准
            if cur is not None and v is None and f not in cur:
                continue
            ch[f] = v
        if ch:
            changes.append((oid, ch))
            res["new" if cur is None else "updated"] += 1
    st.set_many(changes, src="excel")
    gone = []
    read = set(sheets)
    for oid, o in st.orders.items():
        if o.get("origin") != "excel" or oid in seen or str(o.get("ref") or "").split("!")[0] not in read:
            continue
        if all(w[2] == "excel" for w in st.meta.get(oid, {}).values()):
            gone.append(oid)
        else:
            res["kept"] += 1
    if gone:
        st.delete(gone, src="excel")
        res["removed"] = len(gone)
    bud = []
    for month, store, v in budgets:
        k = f"{month}|{store}"
        w = st.budget_meta.get(k)
        if st.budgets.get(k) != v and (not w or w[2] == "excel"):
            bud.append(st._ev("budget", k, "excel", v=v))
    st._write(bud)
    res["budgets"] = len(bud)
    return res


def import_file(st, path):
    """读 Excel（先复制一份再读：原文件可能正被 Excel 打开 / 正在同步）→ sync_excel"""
    import shutil
    import tempfile
    tmp = os.path.join(tempfile.mkdtemp(prefix="piano_"), "piano.xlsx")
    shutil.copy2(path, tmp)
    try:
        rows_, budgets, sheets = read_excel(tmp)
    finally:
        try:
            shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)
        except Exception:
            pass
    st.reload()
    res = sync_excel(st, rows_, budgets, sheets)
    res["rows"], res["sheets"] = len(rows_), sheets
    return res


def find_excel(folder):
    """共享文件夹里的 PIANO ACQUISTO MESILE（有 _COPIA 的先用 COPIA：用户 2026-11-02 说先用副本试）"""
    try:
        fs = [f for f in os.listdir(folder) if f.lower().endswith((".xlsx", ".xlsm")) and not f.startswith("~$")
              and "PIANO" in f.upper()]
    except OSError:
        return ""
    fs.sort(key=lambda f: ("COPIA" not in f.upper(), "NUOVO" not in f.upper(), f))
    return os.path.join(folder, fs[0]) if fs else ""


def export_excel(rs, path):
    """把（筛选后的）订单导出成一张普通的表"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = Workbook()
    ws = wb.active
    ws.title = "Ordini"
    heads = ["Data ordine", "Negozio", "Fornitore", "Ordine da", "Importo imponibile", "Stato", "Data confermato",
             "Arrivo previsto", "Data arrivato", "Giorni", "Note"]
    ws.append(heads)
    lab = {"ordered": "Ordinato", "confirmed": "Confermato", "partial": "Arrivato in parte", "arrived": "Arrivato",
           "stuck": "Bloccato", "cancel": "Annullato", "deter": "DETER"}
    for r in rs:
        ws.append([r["date"], r["store_name"], r["supplier"], r["buyer"], r["amount"],
                   lab.get(r["status"], r["status"]) + (" (in ritardo)" if r["overdue"] else ""),
                   r["confirmed"], r["expected"], r["arrived"], r["days"], r["note"]])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for col, w in zip("ABCDEFGHIJK", (12, 13, 26, 12, 14, 18, 13, 13, 13, 8, 40)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if isinstance(c.value, date):
                c.number_format = "DD/MM/YYYY"
        row[4].number_format = "#,##0.00 €"
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:K{ws.max_row}"
    wb.save(path)
    return path


# ===================================================================== 程序生成的订单自动登记（2026-11-02 用户：以后都由程序登记，不手填 Excel）
SOURCES = {"store_orders": "门店配货", "wholesale": "批发商比价"}


def store_code(name):
    """门店名（FOIANO / MARLIA / Capannori / S.FILIPPO / BMS…）→ "1".."5" / "BMS"；认不出 None"""
    n = _norm(name)
    if n in EXCEL_STORE:
        return EXCEL_STORE[n]
    for k, v in STORE_NAMES.items():
        if _norm(v) == n:
            return k
    return None


def draft_id(source, day, store, supplier):
    return "a" + hashlib.sha1(f"{source}|{day}|{store}|{sup_key(supplier)}".encode("utf-8")).hexdigest()[:12]


def register_drafts(st, source, items, in_scope=None, today=None):
    """
    门店配货 / 批发商比价生成订单文件后调用：每家供应商 × 每个去向（门店 / BMS）一张「待登记」的单（draft）。
    items = [{"store", "supplier", "amount", "colli", "file"}]。同一天同一来源再生成（重新配货、修改分配）：
    同一张单更新金额 / 文件（只改程序自动写的字段，人改过的不动）；这次没有了、还是待登记、没人动过的删掉
    （只删 in_scope(单) 为真的，例如同一家门店 / 同一批供应商）。已经确认下单的不会被删，金额照样跟着更新（有修改记录）。
    返回 {"new", "updated", "removed", "drafts"}
    """
    day = (today or date.today()).isoformat()
    res = {"new": 0, "updated": 0, "removed": 0}
    changes, ids = [], set()
    for it in items:
        store, sup = it.get("store"), str(it.get("supplier") or "").strip()
        if not store or not sup:
            continue
        oid = draft_id(source, day, store, sup)
        if oid in ids:
            continue
        ids.add(oid)
        f = {"date": day, "store": store, "supplier": sup,
             "amount": round(float(it["amount"]), 2) if it.get("amount") is not None else None,
             "colli": it.get("colli"), "file": it.get("file") or None, "origin": source}
        cur = st.orders.get(oid)
        if cur is None:
            f["draft"] = True
            changes.append((oid, f))
            res["new"] += 1
            continue
        meta = st.meta.get(oid, {})
        ch = {k: v for k, v in f.items() if cur.get(k) != v and (not meta.get(k) or meta[k][2] == "auto")}
        if ch:
            changes.append((oid, ch))
            res["updated"] += 1
    st.set_many(changes, src="auto")
    gone = [oid for oid, o in st.orders.items()
            if o.get("origin") == source and o.get("draft") and o.get("date") == day and oid not in ids
            and (in_scope is None or in_scope(o)) and all(w[2] == "auto" for w in st.meta.get(oid, {}).values())]
    if gone:
        st.delete(gone, src="auto")
    res["removed"] = len(gone)
    res["drafts"] = sum(1 for o in st.orders.values() if o.get("draft") and o.get("state") != "cancel")
    return res


def store_for(settings):
    """设置里有共享文件夹、没关掉自动登记 → 读好的 Store；不然 None"""
    cfg = (settings or {}).get("tracking") or {}
    folder = cfg.get("folder") or ""
    if not folder or not os.path.isdir(folder) or cfg.get("auto_register") is False:
        return None
    me = (cfg.get("me") or (settings or {}).get("user_name") or default_me()).strip()
    return Store(folder, me=me).reload()


def register_store_orders(settings, written, log=None):
    """门店配货：write_plans 的结果 [(文件, 供应商, 去向, 箱数, 金额, 状态, SCHEDA)] → 待登记"""
    st = store_for(settings)
    if st is None:
        return None
    items = [{"store": store_code(dest), "supplier": sup, "colli": colli, "amount": amt, "file": p}
             for p, sup, dest, colli, amt, _status, _sc in written or []]
    sups = {sup_key(x["supplier"]) for x in items}
    res = register_drafts(st, "store_orders", items, in_scope=lambda o: sup_key(o.get("supplier")) in sups)
    if log:
        from i18n import tr
        log(tr("[订单跟踪] 门店配货：待登记 新的 {0} 张，更新 {1} 张，删掉 {2} 张", res["new"], res["updated"], res["removed"]))
    return res


def register_wholesale(settings, store_name, items, log=None):
    """批发商比价：一家门店这次采购的供应商 [{"supplier", "amount", "colli", "file"}] → 待登记"""
    st = store_for(settings)
    code = store_code(store_name)
    if st is None or not code:
        if st is not None and log:
            from i18n import tr
            log(tr("[订单跟踪] 认不出门店「{0}」，没有登记", store_name))
        return None
    for x in items:
        x["store"] = code
    res = register_drafts(st, "wholesale", items, in_scope=lambda o: str(o.get("store")) == code)
    if log:
        from i18n import tr
        log(tr("[订单跟踪] {0}：待登记 新的 {1} 张，更新 {2} 张，删掉 {3} 张", store_name, res["new"], res["updated"], res["removed"]))
    return res


def confirm_drafts(st, ids, today=None):
    """确认已下单：待登记 → 已下单，下单日期 = 今天（发给供应商的那天）；谁下的没填就写自己"""
    day = (today or date.today()).isoformat()
    ch = []
    for oid in ids:
        o = st.orders.get(oid)
        if not o or not o.get("draft"):
            continue
        f = {"draft": None, "date": day}
        if not o.get("buyer"):
            f["buyer"] = st.me
        ch.append((oid, f))
    st.set_many(ch)
    return len(ch)


# ===================================================================== 导出成和 PIANO ACQUISTO MESILE 一样的表
# 2026-11-02 用户：导出的表格要和原表格一致。拿原来的 Excel 当模板：复制最新那张月份表的格式（合并格、颜色、列宽、数字格式），
# 每个月一张，六块门店并排；每周单数、每周合计（=SUM 那几行）、本月合计、预算都按原表的位置写。原文件里其他工作表原样保留，
# 同一个月的旧表换成新的。写到一个新文件，不改原来的 Excel。
ITA_MONTHS = ["GEN", "FEB", "MAR", "APR", "MAG", "GIU", "LUG", "AGO", "SET", "OTT", "NOV", "DIC"]


def _week_of(d):
    """这个月的第几周（0 起）：第 1 周 = 1 号到第一个星期天，之后每周一到周日；最多 5 周（第 6 周算进第 5 周）"""
    first_sun = 7 - date(d.year, d.month, 1).weekday()          # 1 号是周日 → 1
    return 0 if d.day <= first_sun else min(4, 1 + (d.day - first_sun - 1) // 7)


def _sheet_month(ws, hr, blocks):
    """这张月份表是哪个月 (年, 月)：先看下单日期，空表看表名（OTT、GIUGNO_26…）"""
    cnt = {}
    for c, _s, _n in blocks:
        for r in range(hr + 1, ws.max_row + 1):
            d, _ = parse_date(ws.cell(r, c).value)
            if d and ws.cell(r, c + 1).value not in (None, ""):
                cnt[(d.year, d.month)] = cnt.get((d.year, d.month), 0) + 1
    if cnt:
        return max(cnt.items(), key=lambda kv: kv[1])[0]
    n = _norm(ws.title)
    m = next((i + 1 for i, a in enumerate(ITA_MONTHS) if n.startswith(a)), None)
    if not m:
        return None
    y = re.search(r"(\d{2,4})$", n)
    year = (2000 + int(y.group(1)) if len(y.group(1)) == 2 else int(y.group(1))) if y else date.today().year
    return (year, m)


def export_piano(rs, st, template, out):
    """
    rs = rows(st)（已经是要导出的单：不含待登记、已取消）；template = 原来的 PIANO ACQUISTO MESILE。
    返回 [(工作表名, 单数)]
    """
    import copy
    from openpyxl import load_workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import PatternFill
    from openpyxl.utils import get_column_letter as L
    wb = load_workbook(template)
    layouts = {}
    for ws in wb.worksheets:
        hr, blocks = _layout(ws)
        if hr and blocks:
            layouts[ws.title] = (hr, blocks, _sheet_month(ws, hr, blocks))
    if not layouts:
        raise ValueError("模板里没有月份表（表头「Data Ordine」）")
    # 模板 = 门店块最多的那张（一样多取排在前面的，一般是最新的月份）
    tpl_name = max(layouts, key=lambda k: (len(layouts[k][1]), -wb.sheetnames.index(k)))
    tpl = wb[tpl_name]
    hr, blocks, _ = layouts[tpl_name]
    by_month = {}
    for r in rs:
        if r["status"] in NOT_ORDERED or not r["date"]:
            continue
        by_month.setdefault((r["date"].year, r["date"].month), {}).setdefault(r["store"], []).append(r)
    years = {y for y, _m in by_month}
    done = []
    for ym in sorted(by_month, reverse=True):
        old = next((k for k, (_h, _b, m) in layouts.items() if m == ym), None)
        name = old or (ITA_MONTHS[ym[1] - 1] + (f"_{ym[0] % 100:02d}" if len(years) > 1 or ym[0] != date.today().year else ""))
        ws = wb.copy_worksheet(tpl)
        ws.sheet_view.zoomScale = tpl.sheet_view.zoomScale
        ws.freeze_panes = tpl.freeze_panes
        if tpl.sheet_properties.tabColor is not None:
            ws.sheet_properties.tabColor = copy.copy(tpl.sheet_properties.tabColor)
        idx = wb.sheetnames.index(old) if old else 0
        if old:
            wb.remove(wb[old])
        wb._sheets.remove(ws)
        wb._sheets.insert(idx, ws)
        ws.title = name
        # 清空数据区和批注；格式统一成每列最常见的那种（模板里有人手工涂的红格子不跟过来）
        last = max(ws.max_row, hr + 1)
        for c, _s, _n in blocks:
            for cc in range(c, c + 8):
                cnt = {}
                for r in range(hr + 1, last + 1):
                    k = ws.cell(r, cc).style_id
                    cnt[k] = cnt.get(k, 0) + 1
                base = copy.copy(ws.cell(next(r for r in range(hr + 1, last + 1)
                                             if ws.cell(r, cc).style_id == max(cnt, key=cnt.get)), cc)._style)
                for r in range(hr + 1, last + 1):
                    cell = ws.cell(r, cc)
                    cell.value = None
                    cell.comment = None
                    cell._style = copy.copy(base)
        week_rows = [r for r in range(1, hr) if "SETTIMANA" in _norm(ws.cell(r, blocks[0][0] + 2).value)]
        total_row = next((r for r in range(1, hr) if "TOTALE" in _norm(ws.cell(r, blocks[0][0] + 2).value)), None)
        budget_row = next((r for r in range(1, hr) if "BADGET" in _norm(ws.cell(r, blocks[0][0] + 2).value)
                           or "BUDGET" in _norm(ws.cell(r, blocks[0][0] + 2).value)), None)
        n_all = 0
        for c, store, _n in blocks:
            orders = sorted(by_month[ym].get(store, []), key=lambda r: (r["date"], r["supplier"].lower()))
            need = hr + len(orders)
            for r in range(ws.max_row + 1, need + 1):                # 比模板行多：照第一行数据行的格式
                for cc in range(c, c + 8):
                    ws.cell(r, cc)._style = copy.copy(ws.cell(hr + 1, cc)._style)
            weeks = {}
            for i, o in enumerate(orders):
                r = hr + 1 + i
                weeks.setdefault(_week_of(o["date"]), []).append(r)
                deter = o["status"] == "deter"
                arr = o["arrived"] or ("DETER" if deter else ("卡住了" if o["status"] == "stuck" else None))
                vals = [o["date"], o["raw_supplier"] or o["supplier"], o["buyer"] or None, o["amount"],
                        o["confirmed"] or ("DETER" if deter else None), arr,
                        (o["arrived"] - o["date"]).days if (o["arrived"] and o["date"]) else None]
                for k, v in enumerate(vals):
                    cell = ws.cell(r, c + k)
                    cell.value = v
                    if isinstance(v, date):
                        cell.number_format = "dd/mm/yyyy;@"
                    elif k == 3 and v is not None:
                        cell.number_format = "[$€-410]\\ #,##0.00;\\-[$€-410]\\ #,##0.00"
                    elif k == 6 and v is not None:
                        cell.number_format = "0"
                if o["overdue"]:                                       # 超期没到：到货格涂红（和原表一样的红）
                    ws.cell(r, c + 5).fill = PatternFill("solid", fgColor="FFFF0000")
                if o["status"] == "partial":
                    ws.cell(r, c + 5).comment = Comment("到了一部分", "Programma")
                if o["note"]:
                    ws.cell(r, c + 1).comment = Comment(o["note"], "Programma")
            n_all += len(orders)
            amt = L(c + 3)
            for w, row in enumerate(week_rows):
                rr = weeks.get(w) or []
                if w == len(week_rows) - 1:                          # 最后一行包括之后所有的周
                    rr = [x for k, v in weeks.items() if k >= w for x in v]
                ws.cell(row, c + 4).value = len(rr)
                ws.cell(row, c + 5).value = f"=SUM({amt}{min(rr)}:{amt}{max(rr)})" if rr else None
            if total_row and week_rows:
                ws.cell(total_row, c + 5).value = f"=SUM({L(c + 5)}{week_rows[0]}:{L(c + 5)}{week_rows[-1]})"
            if budget_row:
                ws.cell(budget_row, c + 5).value = st.budgets.get(f"{ym[0]:04d}-{ym[1]:02d}|{store}")
        done.append((name, n_all))
    wb.save(out)
    return done


# ===================================================================== 页面顶上的「本月」：预算、合计、每周（和 PIANO 表头一样）
def week_ranges(year, month):
    """这个月每一周的 (第一天, 最后一天)：第 1 周 = 1 号到第一个星期天，之后周一到周日；最多 5 周（多出来的并进第 5 周，和 _week_of 一样）"""
    import calendar
    last = calendar.monthrange(year, month)[1]
    end = min(7 - date(year, month, 1).weekday(), last)
    out = [(1, end)]
    while end < last:
        start, end = end + 1, min(end + 7, last)
        if len(out) == 5:
            out[-1] = (out[-1][0], end)
        else:
            out.append((start, end))
    return [(date(year, month, a), date(year, month, b)) for a, b in out]


def month_summary(rs, st, year, month, store=None):
    """
    某个月（某家门店 / 全部）：预算、已下单金额和单数、每周的单数和金额（不含待登记、已取消；都不含税）。
    → {"budget", "amount", "n", "weeks": [{"start", "end", "n", "amount"}]}
    """
    ym = f"{year:04d}-{month:02d}"
    weeks = [{"start": a, "end": b, "n": 0, "amount": 0.0} for a, b in week_ranges(year, month)]
    n, amt = 0, 0.0
    for r in rs:
        d = r.get("date")
        if not d or d.year != year or d.month != month or r["status"] in NOT_ORDERED:
            continue
        if store and r["store"] != store:
            continue
        w = weeks[min(_week_of(d), len(weeks) - 1)]
        v = float(r["amount"] or 0)
        w["n"] += 1
        w["amount"] += v
        n += 1
        amt += v
    vals = [v for k, v in st.budgets.items() if k.startswith(ym + "|") and (not store or k.split("|", 1)[1] == store)]
    return {"budget": sum(vals) if vals else None, "amount": amt, "n": n, "weeks": weeks}
