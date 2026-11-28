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
  note 备注 · ref 从 Excel 导入的位置（OTT!B12）· origin（excel / app / mail）
  shipped 发货日期 · mails 从邮件认出来的进展 [{k 类别, d 时间, s 主题, f 发件人}]（mail_match，2026-11-07）
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
from collections import Counter

DATA_DIR = "Tracking ordini (programma)"
STORES = [("1", "FOIANO"), ("2", "MARLIA"), ("3", "S.FILIPPO"), ("4", "S.MARCO"), ("5", "S.CONCORDIO"), ("BMS", "BMS")]
STORE_NAMES = dict(STORES)
# Excel 每一块的标题「Gli ordini per Capannori」→ 门店（Capannori = 程序里的 MARLIA）
EXCEL_STORE = {"FOIANO": "1", "CAPANNORI": "2", "MARLIA": "2", "SANFILIPPO": "3", "SFILIPPO": "3", "SANMARCO": "4",
               "SMARCO": "4", "SANCONCORDIO": "5", "SCONCORDIO": "5", "BMS": "BMS"}
# 块标题里的门店名写错 / 写法不同时按关键词认（2026-11-02：11 月表写成「Gli ordini per San S.Conrdio」→ 整块没读）
_STORE_WORDS = (("FOIANO", "1"), ("CAPANNORI", "2"), ("MARLIA", "2"), ("FILIPPO", "3"), ("MARCO", "4"),
                ("CONCORDIO", "5"), ("BMS", "BMS"))
IVA = 0.22
PENDING_DAYS = 2          # 发出 2 个工作日还没收到确认 → 「待确认」黄色
PENDING_RED_DAYS = 4      # 4 个工作日（差不多一个星期）还没确认 → 红色（2026-11-21 用户：两天黄、再久红色，按最优解）
BMS_ABBR = {"1": "FO", "2": "MA", "3": "FI", "4": "SM", "5": "SC"}   # BMS 单后面写凑单门店：BMS (FO, MA, SC)
ORDER_NO_RE = re.compile(r"\b(?:FO|MA|FI|SM|SC|BMS|CT)-\d{4}-[0-9A-F]{4}\b")   # 程序发的订单号（mail_send.order_no）


def find_order_nos(text):
    """文字里的订单号（FO-2011-3F9A…），不分大小写"""
    return ORDER_NO_RE.findall(str(text or "").upper())


def workdays_between(d0, d1):
    """d0 之后到 d1（含）有几个工作日（周一到周五）；d1 不比 d0 晚 = 0"""
    if not d0 or not d1 or d1 <= d0:
        return 0
    n, d = 0, d0
    while d < d1:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def store_label(o):
    """门店显示：BMS 单后面写是哪几家店凑的（bms_of），例如「BMS (FO, MA, SC)」"""
    sc = str(o.get("store") or "")
    name = STORE_NAMES.get(sc, sc)
    if sc == "BMS" and o.get("bms_of"):
        ab = [BMS_ABBR.get(str(x), str(x)) for x in sorted(o["bms_of"], key=str)]
        return f"{name} ({', '.join(ab)})"
    return name


def sent_day(o):
    """订单邮件发出那天（程序发的有 sent.at），没有就用下单日期"""
    at = ((o.get("sent") or {}).get("at") or "")[:10]
    return to_date(at) if at else to_date(o.get("date"))
LEAD_DEFAULT = 10         # 没有历史时按 10 天到货
LEAD_MIN_SAMPLES = 2      # 一家供应商至少 2 张到货的单才用它自己的天数
EXCEL_FIELDS = ("date", "store", "supplier", "buyer", "amount", "confirmed", "arrived", "state", "kind", "note", "ref",
                "origin", "cen")
# BMS 凑单（2026-11-28 用户定 B）：几家门店凑成的一张 BMS 单（送 BMS 仓库分货）= 一张单、一个状态；
# 每家店那一份写在那家店那一块、Gg.cons. 写 CEN（cen = True），算进那家店的预算。
#   · 以前同事手填的：同一张表、同一家供应商、同一天、标了 CEN 的几行 = 一张 BMS 单（groups）；
#     BMS 那一块有同一家同一天的一行就是这张单，没有就由程序建一张（origin "group"，不写进 Excel）
#   · 程序生成的 BMS 单：shares = {门店: 金额}；发出去以后（不再是待登记）每家店一行 part_of = 这张单（cen = True），
#     写进 PIANO：BMS 那一块一行（金额空着，和同事的写法一样，免得重复算）+ 每家店那一块一行标 CEN
GROUP_SYNC = ("confirmed", "shipped", "arrived", "state")   # 在 BMS 单上改的这几项，每家店那一份跟着改
GROUP_SRC = "group"

# 状态（显示顺序）
# draft = 待登记：程序生成了订单文件、还没人确认真的发给供应商（2026-11-02 用户：以后都由程序自动登记，不手填）
# shipped = 已发货：供应商发了 DDT / 发货通知（2026-11-07 从邮件认出来，shipped 字段 = 发货日期）
# void = 待登记已作废（2026-11-26）：程序生成了、一直没发出去，后来有了更新的配货 / 超过 7 天 → 不再算待登记。
#        存法：state = "cancel" + void = {why, at, by}（所以所有「取消的不算」的地方自动不算它），status() 分开显示
STATUS = ["draft", "ordered", "confirmed", "shipped", "partial", "arrived", "stuck", "cancel", "void", "deter"]
NOT_ORDERED = {"cancel", "draft", "void"}      # 不算进花费、预算、供应商统计
OPEN = {"ordered", "confirmed", "shipped", "partial", "stuck"}


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

    def adopt(self, other):
        """把另一个 Store 刚读好的内容换进来（后台线程读、界面线程换；这个对象别处还拿着）"""
        for k in ("orders", "meta", "hist", "budgets", "budget_meta", "aliases", "xbase", "xstate", "deleted", "deleted_at", "_seen", "_sig"):
            setattr(self, k, getattr(other, k))
        return self

    def _reset(self):
        self.orders, self.meta, self.hist = {}, {}, {}
        self.budgets, self.budget_meta, self.aliases = {}, {}, {}
        # 和 Excel 双向同步（excel_sync.py）：xbase {单号: {字段: 上次从 Excel 读到的值, "_w_字段": 上次写进 Excel 的值}}
        # （"_budget" 键下是预算）；xstate {键: 值}（"write" = 最后一次写 Excel：文件修改时间 / 大小 / 谁）
        self.xbase, self.xstate = {}, {}
        self.deleted = {}          # {单号: 来源}：程序里删掉的单，从 Excel 同步时不再加回来
        self.deleted_at = {}       # {单号: 删掉的时间}（从 Excel 同步时：本地 Excel 比删掉的时候还旧 → 不加回来）
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
        self._heal_orphans()
        self._sig = sig
        return self

    # 只有这些字段的单不算一张单（没有门店 / 供应商 / 日期）
    _ID_FIELDS = ("store", "supplier", "date")
    _CONTENT = ("confirmed", "arrived", "amount", "ref", "buyer", "note", "kind", "state", "shipped", "colli")

    def _heal_orphans(self):
        """
        2026-11-28 查到的 bug（看板上没有供应商、没有门店的空白卡片）：两台电脑同步 Excel 时，
        一台电脑的同步盘还没收到新的 PIANO（本地 Excel 旧），把别的电脑刚读进来的单当成「Excel 里删了」删掉；
        另一台电脑之后只写了改动的那几格（确认日期 / 到货日期…）→ 单又出现了，但只剩这几格。
        这里按修改记录修回来：
          · 这个单号是程序写进 Excel 那一行的 xid → 是那张单的重复，不要
          · 删掉以后从 Excel 又写过内容 → 这一行 Excel 里还在：用全部修改记录拼回完整的单
          · 拼不出门店 / 供应商 → 不要
        """
        xid = {o.get("xid"): k for k, o in self.orders.items() if o.get("xid")}
        for oid in [k for k, o in self.orders.items() if not any(o.get(f) for f in self._ID_FIELDS)]:
            hist = self.hist.get(oid, [])
            dels = [i for i, e in enumerate(hist) if e.get("op") == "del"]
            after = [e for e in hist[(dels[-1] + 1 if dels else 0):] if e.get("op") == "set"]
            redo = any(k in self._CONTENT for e in after if (e.get("src") or "app") == "excel"
                       for k in (e.get("f") or {}))
            full, meta = {}, {}
            for e in hist:
                if e.get("op") == "set":
                    who = (e["t"], e.get("by") or "?", e.get("src") or "app")
                    for k, v in (e.get("f") or {}).items():
                        full[k] = v
                        meta[k] = who
            if xid.get(oid) or not dels or not redo or not (full.get("store") and full.get("supplier")):
                self.orders.pop(oid, None)
                self.meta.pop(oid, None)
                self.deleted.setdefault(oid, "excel")
                continue
            self.orders[oid] = full
            self.meta[oid] = meta

    def _apply(self, e):
        if e["e"] in self._seen:                # 同步盘的「冲突副本」里会有重复的
            return
        self._seen.add(e["e"])
        op, oid = e.get("op"), e.get("id")
        who = (e["t"], e.get("by") or "?", e.get("src") or "app")
        if op == "set" and oid:
            o = self.orders.setdefault(oid, {})
            m = self.meta.setdefault(oid, {})
            f = e.get("f") or {}
            if who[2] == VOID_SRC and f.get("void") and not _voidable(o, m):
                # 作废的时候这台电脑还不知道：别的电脑已经发出 / 有人动过这张单 → 不作废（两台电脑同步慢）
                self.hist.setdefault(oid, []).append(e)
                return
            if o.get("void") and who[2] not in ("auto", "file", VOID_SRC) and "void" not in f:
                # 已作废的单有人动了（发出订单邮件、确认、改了什么）→ 回到正常的单，不再算作废
                f = dict(f)
                f["void"] = None
                if "state" not in f:
                    f["state"] = None
            for k, v in f.items():
                o[k] = v
                m[k] = who
            self.deleted.pop(oid, None)
            self.deleted_at.pop(oid, None)
            self.hist.setdefault(oid, []).append(e)
        elif op == "del" and oid:
            self.orders.pop(oid, None)
            self.meta.pop(oid, None)
            self.deleted[oid] = who[2]
            self.deleted_at[oid] = who[0]
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
        elif op == "xbase" and oid:
            self.xbase.setdefault(oid, {}).update(e.get("f") or {})
        elif op == "xs" and oid:
            self.xstate[oid] = e.get("v")

    # ---- 写（追加到自己的文件，马上生效）----
    def _write(self, evs):
        if not evs:
            return
        self.writes = getattr(self, "writes", 0) + 1
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
            self.set_many([(oid, fields)], src)

    def set_many(self, changes, src="app"):
        """[(单号, {字段: 值})] 一次写完（BMS 凑单：在 BMS 单上改确认 / 发货 / 到货，每家店那一份跟着改，见 group_expand）"""
        changes = [(oid, f) for oid, f in changes if f]
        if src not in ("excel", "file", GROUP_SRC):
            changes = group_expand(self, changes)
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

    def set_xbase(self, changes):
        """[(单号, {字段: Excel 里的值})]（excel_sync 用；src 记 excel）"""
        self._write([self._ev("xbase", oid, "excel", f=f) for oid, f in changes if f])

    def set_xstate(self, key, value):
        self._write([self._ev("xs", key, "excel", v=value)])

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
# ===================================================================== BMS 凑单
def _ref_sheet(o):
    return str(o.get("ref") or "").split("!")[0]


def group_key(o):
    """标了 CEN 的门店那一行 → 「表|供应商|下单日期」（同一张 BMS 单的几家店一样）；不是 → None"""
    if not o.get("cen") or str(o.get("store")) not in BMS_ABBR:
        return None
    return f"{_ref_sheet(o)}|{sup_key(o.get('supplier'))}|{iso(o.get('date')) or ''}"


def group_id(key):
    return "g" + hashlib.sha1(str(key).encode("utf-8")).hexdigest()[:12]


def share_id(parent, store):
    return "s" + hashlib.sha1(f"{parent}|{store}".encode("utf-8")).hexdigest()[:12]


def groups(st):
    """
    BMS 凑单：{BMS 单号: {"parent": 单号 或 None（还没建）, "members": [每家店那一份的单号], "key": 表|供应商|日期}}。
    ① 程序建的：门店那一份 part_of = BMS 单号；② 同事手填的：标 CEN、同一张表同一家同一天的几行一组；
    BMS 那一块有同一家同一天的一行（Excel 里的，不是程序拆过的）就是这张单，没有 = group_id(key)（程序建，不写 Excel）
    """
    out = {}
    bms_rows = {}
    for oid, o in st.orders.items():
        if str(o.get("store")) == "BMS" and o.get("ref") and not o.get("shares"):
            bms_rows.setdefault(f"{_ref_sheet(o)}|{sup_key(o.get('supplier'))}|{iso(o.get('date')) or ''}", oid)
    for oid, o in st.orders.items():
        p = o.get("part_of")
        if p:
            g = out.setdefault(p, {"parent": p if p in st.orders else None, "members": [], "key": None})
            g["members"].append(oid)
            continue
        k = group_key(o)
        if not k:
            continue
        gid = bms_rows.get(k) or group_id(k)
        g = out.setdefault(gid, {"parent": gid if gid in st.orders else None, "members": [], "key": k})
        g["members"].append(oid)
        g["key"] = g["key"] or k
    for g in out.values():
        g["members"].sort(key=lambda m: (str(st.orders[m].get("store")), m))
    return out


def _parent_base(st, g):
    """还没建的 BMS 单（同事手填的 CEN）：照第一家店那一份写 门店 BMS · 供应商 · 日期"""
    m0 = st.orders[g["members"][0]]
    return {"store": "BMS", "supplier": m0.get("supplier"), "date": m0.get("date"), "origin": GROUP_SRC,
            "group_key": g["key"]}


def _share_fields(p, sid, amount):
    f = {"store": str(sid), "supplier": p.get("supplier"), "date": p.get("date"), "amount": amount, "cen": True,
         "origin": p.get("origin") or "app"}
    for k in ("buyer", "file", "kind"):
        if p.get(k):
            f[k] = p[k]
    return f


def group_expand(st, changes):
    """
    写之前：① 写到还没建的 BMS 单（CEN 那一组）→ 先把这张单建出来；② 在 BMS 单上改确认 / 发货 / 到货 / 状态 →
    每家店那一份也改；③ 程序生成的 BMS 单（shares）发出去了（不是待登记、没取消）→ 每家店建一份（part_of，标 CEN），
    金额 / 日期跟着 BMS 单。门店那一份自己改的不往回传（BMS 单的状态是几家店合起来算的，见 merge_group）。
    """
    gs = None
    out, extra = [], []
    for oid, f in changes:
        o = st.orders.get(oid)
        if o is not None and o.get("part_of"):
            out.append((oid, f))
            continue
        if gs is None:
            gs = groups(st)
        g = gs.get(oid)
        f = dict(f)
        if g is not None and oid not in st.orders:
            base = _parent_base(st, g)
            base.update(f)
            f = base
        out.append((oid, f))
        if g is not None:
            sync = {k: v for k, v in f.items() if k in GROUP_SYNC}
            if sync:
                for m in g["members"]:
                    extra.append((m, dict(sync)))
        p = dict(o or {})
        p.update(f)
        shares = p.get("shares")
        if shares and not p.get("draft") and p.get("state") != "cancel" and str(p.get("store")) == "BMS":
            for sid, amt in sorted(shares.items(), key=lambda kv: str(kv[0])):
                cid = share_id(oid, sid)
                want = _share_fields(p, sid, round(float(amt or 0), 2))
                want["part_of"] = oid
                for k in GROUP_SYNC:
                    if p.get(k) is not None:
                        want[k] = p[k]
                cur = st.orders.get(cid) or {}
                diff = {k: v for k, v in want.items() if cur.get(k) != v}
                if diff:
                    extra.append((cid, diff))
    # 同一张单的几次改合在一起（后面的盖前面的）
    merged, order = {}, []
    for oid, f in out + extra:
        if oid not in merged:
            merged[oid] = {}
            order.append(oid)
        merged[oid].update(f)
    return [(oid, merged[oid]) for oid in order]


def merge_group(p, members):
    """BMS 单显示的样子：确认 / 发货 = 几家店里最早的；几家店都到了才算到货（有的到了 = 部分到货）；金额 = 几家店加起来"""
    o = dict(p)
    active = [m for m in members if m.get("state") != "cancel"]
    shares = {}
    for m in active:
        sid = str(m.get("store"))
        shares[sid] = round(shares.get(sid, 0.0) + float(m.get("amount") or 0), 2)
    o["_shares"] = shares
    o["bms_of"] = sorted(shares) or list(p.get("bms_of") or [])
    if members and not active:
        o.setdefault("state", None)
        if not o.get("state"):
            o["state"] = "cancel"
        return o
    # BMS 那一块自己那一行有金额的（同事有时写总额、有时只写没分到门店的那部分）：多出来的那部分算 BMS 自己的
    own = p.get("amount") if isinstance(p.get("amount"), (int, float)) and not p.get("shares") else None
    tot = round(sum(shares.values()), 2)
    o["_own"] = round(max(0.0, float(own) - tot), 2) if own is not None else 0.0
    if any(m.get("amount") is not None for m in active):
        o["amount"] = round(max(tot, float(own or 0)), 2)
    for k in ("confirmed", "shipped"):
        if not o.get(k):
            vals = sorted(str(m[k]) for m in active if m.get(k))
            if vals:
                o[k] = vals[0]
    if not o.get("arrived") and active:
        arr = [m.get("arrived") for m in active]
        if all(arr):
            o["arrived"] = max(str(x) for x in arr)
            if any(m.get("state") == "partial" for m in active):
                o["state"] = "partial"
        elif any(arr) and not o.get("state"):
            o["state"] = "partial"
    if not o.get("state") and any(m.get("state") == "stuck" for m in active):
        o["state"] = "stuck"
    return o


def materialize_groups(st):
    """同事手填的 CEN 那一组还没有 BMS 单的：建出来（origin "group"，不写进 Excel）。返回建了几张"""
    ch = [(gid, _parent_base(st, g)) for gid, g in groups(st).items() if g["parent"] is None and g["key"]]
    if ch:
        st.set_many(ch, src=GROUP_SRC)
    return len(ch)


def counted_amount(r):
    """
    算进花费 / 预算的金额：几家店凑的 BMS 单，每家店那一份算进那家店；BMS 单本身只算没分到门店的那部分
    （BMS 那一块同事写的金额比几家店加起来多的部分，例如 OTT HD 289.14 − S.MARCO 203.64）
    """
    if r.get("group"):
        return float(r.get("own") or 0)
    return float(r.get("amount") or 0)


def in_store(r, store):
    """这张单算不算这家店的（看板 / 列表按门店看：这家店的单 + 这家店参与的 BMS 单）"""
    return not store or r.get("store") == store or (r.get("group") and store in (r.get("bms_of") or []))


def units(rs, store=None):
    """要跟进的单（每家店的那一份不单独算，跟着 BMS 单）"""
    return [r for r in rs if not r.get("share") and in_store(r, store)]


def status(o):
    if o.get("state") == "cancel":
        return "void" if o.get("void") else "cancel"
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
    if o.get("shipped"):
        return "shipped"
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
    # BMS 凑单：BMS 单按几家店合起来算状态 / 金额；每家店那一份跟着 BMS 单的状态（share = True，看板 / 列表里不单独出现）
    gs = groups(st)
    member_of = {m: gid for gid, g in gs.items() for m in g["members"]}
    view = {}
    for oid, o in st.orders.items():
        if o.get("origin") == GROUP_SRC and oid not in gs:
            continue                                    # 那一组的 CEN 都去掉了：这张 BMS 单不要了
        view[oid] = o
    for gid, g in gs.items():
        base = st.orders.get(gid) or _parent_base(st, g)
        view[gid] = merge_group(base, [st.orders[m] for m in g["members"]])
    out = []
    parent_row = {}
    order = sorted(view, key=lambda x: x in member_of)          # BMS 单先算，每家店那一份照抄它的状态
    for oid in order:
        o = view[oid]
        s = status(o)
        k = st.canon(sup_key(o.get("supplier")))
        d0 = to_date(o.get("date"))
        conf, arr = to_date(o.get("confirmed")), to_date(o.get("arrived"))
        exp_manual = to_date(o.get("expected"))
        lead = lead_of(leads, k)
        exp = exp_manual or ((d0 + timedelta(days=round(lead if lead is not None else dflt))) if d0 else None)
        open_ = s in OPEN
        overdue = bool(open_ and exp and today > exp)
        wd = workdays_between(sent_day(o), today) if s == "ordered" else 0
        wait_level = 2 if wd >= PENDING_RED_DAYS else 1 if wd >= PENDING_DAYS else 0
        pending = bool(s == "ordered" and wait_level)
        days = (arr - d0).days if (arr and d0) else ((today - d0).days if (d0 and open_) else None)
        m = st.meta.get(oid, {})
        last = max(m.values(), key=lambda w: w[0]) if m else None
        out.append({"id": oid, "date": d0, "store": str(o.get("store") or ""),
                    "store_name": store_label(o), "bms_of": list(o.get("bms_of") or []),
                    "wait_days": wd, "wait_level": wait_level,
                    "supplier": disp.get(k) or str(o.get("supplier") or ""), "raw_supplier": o.get("supplier") or "",
                    "sup_key": k, "buyer": str(o.get("buyer") or ""), "amount": o.get("amount"),
                    "status": s, "confirmed": conf, "arrived": arr, "expected": exp, "expected_manual": bool(exp_manual),
                    "lead": lead, "overdue": overdue, "pending": pending, "days": days, "open": open_,
                    "note": str(o.get("note") or ""), "ref": str(o.get("ref") or ""),
                    "origin": o.get("origin") or "", "last": last, "file": str(o.get("file") or ""),
                    "colli": o.get("colli"), "record": str(o.get("record") or ""),
                    "shipped": to_date(o.get("shipped")), "mails": list(o.get("mails") or []),
                    "receipts": list(o.get("receipts") or []), "booking": dict(o.get("booking") or {}),
                    "from_file": any((m.get(k) or ("", "", ""))[2] == "file" for k in ("amount", "colli")),
                    "late_by": (today - exp).days if overdue else 0,
                    "cen": bool(o.get("cen")), "share": False, "part_of": None,
                    "group": bool(gs.get(oid) and gs[oid]["members"]), "virtual": oid not in st.orders,
                    "shares": dict(o.get("_shares") or o.get("shares") or {}), "own": o.get("_own") or 0.0,
                    "members": list((gs.get(oid) or {}).get("members") or [])})
        r = out[-1]
        if oid in gs:
            parent_row[oid] = r
        pid = member_of.get(oid)
        if pid and pid in parent_row:
            pr = parent_row[pid]
            r.update({"share": True, "part_of": pid, "status": pr["status"], "expected": pr["expected"],
                      "expected_manual": pr["expected_manual"], "lead": pr["lead"], "overdue": pr["overdue"],
                      "late_by": pr["late_by"], "open": pr["open"], "pending": False, "wait_days": 0,
                      "wait_level": 0})
    return out


def month_of(r):
    return r["date"].strftime("%Y-%m") if r.get("date") else ""


def summary(rs, st, month, today=None, store=None):
    """
    顶上几张卡：待登记 / 待确认 / 已超期 / 卡住了 / 本月下单和预算（都不含税）。
    rs = 全部的行（rows）；store = 只看这家店。张数按要跟进的单数（BMS 凑单算一张）；金额按每家店那一份算
    """
    us = units(rs, store)
    spent = sum(counted_amount(r) for r in rs if month_of(r) == month and r["status"] not in NOT_ORDERED
                and (not store or r["store"] == store))
    budget = sum(v for k, v in st.budgets.items() if k.startswith(month + "|") and (not store or k.endswith("|" + store)))
    return {"pending": sum(1 for r in us if r["pending"]), "overdue": sum(1 for r in us if r["overdue"]),
            "stuck": sum(1 for r in us if r["status"] == "stuck"),
            "draft": sum(1 for r in us if r["status"] == "draft"),
            "open": sum(1 for r in us if r["open"]),
            "spent": spent, "spent_iva": spent * (1 + IVA), "budget": budget}


def supplier_stats(rs):
    """按供应商：单数、金额、到货天数（平均 / 中位 / 最长）、还没到的、超期的、最近一次下单"""
    out = {}
    for r in rs:
        if r["status"] in NOT_ORDERED or r.get("share"):     # BMS 凑单算一张（金额 = 几家店加起来）
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
        if r.get("group") and not r.get("own"):  # BMS 凑单：算在每家店那一份上（BMS 自己的那部分才算 BMS）
            continue
        k = (month_of(r), r["store"])
        a = agg.setdefault(k, [0.0, 0])
        a[0] += counted_amount(r)
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
    elif u.startswith("ANNULL") or "已取消" in t:          # 程序写回 Excel 的取消（excel_sync）
        out["state"] = "cancel"
    else:
        out.setdefault("_notes", []).append(t)


def title_store(text):
    """
    块标题「Gli ordini per …」→ 门店号（认不出 None）。先照写法对（EXCEL_STORE），再找关键词（FILIPPO / CONCORDIO…），
    最后按相似度对（拼错一两个字母：「S.Conrdio」→ CONCORDIO）
    """
    import difflib
    n = _norm(text).replace("GLIORDINIPER", "").replace("GLIORDINI", "")
    if not n:
        return None
    if n in EXCEL_STORE:
        return EXCEL_STORE[n]
    for w, code in _STORE_WORDS:
        if w in n:
            return code
    core = n
    for _ in range(3):                                  # 去掉前面的 SAN / S（「SANSCONRDIO」→「CONRDIO」）
        core = core[3:] if core.startswith("SAN") else core[1:] if core.startswith("S") and len(core) > 4 else core
    words = [w for w, _c in _STORE_WORDS if w != "BMS"]
    hit = difflib.get_close_matches(core, words, n=1, cutoff=0.75) or difflib.get_close_matches(n, words, n=1, cutoff=0.75)
    return dict(_STORE_WORDS)[hit[0]] if hit else None


def _layout(ws, unknown=None):
    """
    月份表的格式：表头行（「Data Ordine」那一行）和每一块 [(起始列, 门店, 备注列)]；不是月份表 (None, [])。
    unknown（列表）：认不出是哪家门店的块记进去 (工作表, 列字母, 标题)，同步时提醒（不然整块悄悄漏掉）
    """
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
        # 「Fornitore」表头：2026-11-22 NOV 表 S.Marco 那块的 Z8 被人清空了 → 整块（47 张单）读不到、程序新单也写不进去。
        # 表头空着、但后面几列还是 Ordine Da / Importo 的，照样当一块（读不出供应商的块才跳过）
        h1 = _norm(ws.cell(hr, c + 1).value)
        if h1 != "FORNITORE" and not (h1 == "" and _norm(ws.cell(hr, c + 2).value).startswith("ORDINEDA")
                                      and _norm(ws.cell(hr, c + 3).value).startswith("IMPORTO")):
            continue
        # 块标题「Gli ordini per …」：不一定正好在「Data Ordine」那一列上面（OTT / SET / Agosto / NOV 的 Capannori
        # 标题在 H 列、表头从 I 列开始）→ 在左右两列里找，取最近的一个
        store, title = None, None
        for dc in (0, -1, 1, -2, 2):
            if c + dc < 1:
                continue
            for r in range(1, hr):
                v = ws.cell(r, c + dc).value
                if _norm(v).startswith("GLIORDINI"):
                    title = title or " ".join(str(v).split())
                    store = title_store(v)
                    break
            if store:
                break
        if not store:
            if unknown is not None:
                from openpyxl.utils import get_column_letter
                unknown.append((ws.title, get_column_letter(c), title or ""))
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


def read_excel(path, info=None):
    """
    info（dict，可选）：读完写进 info["blocks"] = {(工作表, 门店)}（真读到的块）、info["unknown"] = 认不出门店的块。
    读 PIANO ACQUISTO MESILE：每个有「Data Ordine」表头的工作表（GIUGNO_26 以后的新格式），每块一家门店。
    → (rows, budgets, sheets)：rows = [{id, date, store, supplier, buyer, amount, confirmed, arrived, state, kind, note,
    ref, origin}]，budgets = [(月份, 门店, 预算)]，sheets = 读了的工作表名
    """
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter
    wb = load_workbook(path, data_only=True)
    try:
        wf = load_workbook(path)                               # 看公式（每周合计有没有 ×1.22；程序写回过的文件没有缓存值）
    except Exception:
        wf = None
    rows, budgets, sheets = [], [], []
    unknown, read_blocks = [], set()
    if info is not None:
        info["unknown"], info["blocks"] = unknown, read_blocks
    for ws in wb.worksheets:
        hr, blocks = _layout(ws, unknown)
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
            read_blocks.add((ws.title, store))
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
                av = ws.cell(r, c + 3).value
                if av is None and wf is not None:          # 公式（「=876.91/1.22」）文件里没存结果（程序写回过）：自己算
                    av = _calc(wf[ws.title].cell(r, c + 3).value)
                amt, atext = parse_amount(av)
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
                cen = str(ws.cell(r, c + 6).value or "").strip().upper().startswith("CEN") and store != "BMS"
                k = (iso(d), sup_key(sup))
                occ[k] = occ.get(k, 0) + 1
                key = f"{ws.title}|{store}|{k[0]}|{k[1]}|{occ[k]}"
                notes = [x for x in dict.fromkeys(out.pop("_notes")) if x]
                row = {"id": "x" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12], "date": iso(d), "store": store,
                       "supplier": sup, "buyer": str(buyer).strip() if buyer not in (None, "") else None,
                       "amount": round(amt, 2) if amt is not None else None, "confirmed": iso(conf),
                       "arrived": iso(arr), "state": out.get("state"), "kind": out.get("kind"),
                       "note": " · ".join(notes) or None, "ref": f"{ws.title}!{get_column_letter(c + 1)}{r}",
                       "origin": "excel", "cen": True if cen else None}
                rows.append(row)
    return rows, budgets, sheets


def _calc(f):
    """只认数字和 + − × ÷ 括号的简单公式（「=876.91/1.22」）；别的返回 None"""
    if not isinstance(f, str) or not f.startswith("="):
        return f
    e = f[1:].replace(",", ".").replace(" ", "")
    if not e or not re.fullmatch(r"[0-9.+\-*/()]+", e):
        return None
    try:
        return float(eval(e, {"__builtins__": {}}, {}))
    except Exception:
        return None


def tr_amount(text):
    return f"金额：{text}"


_MISS = object()


def _utc_iso(ts):
    """文件修改时间 → 和修改记录一样的时间格式（UTC），好比先后"""
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ") if ts else None


def sync_excel(st, rows, budgets, sheets, blocks=None, mtime=None):
    """
    Excel → 程序（2026-11-04 用户定：双向同步，同一格两边都改了「谁最后改的听谁」）。
    st.xbase 记着每张单每个字段上次从 Excel 读到的值（三方比较）：
      · Excel 这一格和上次读到的一样 → Excel 没改，程序里的为准（程序改过的由 excel_sync 写回 Excel）
      · Excel 改了、程序这一格在 Excel 存盘（mtime）之后又改过 → 程序为准；不然 → 用 Excel 的
      · 以前没记过 xbase 的（老数据）：程序改过的以程序为准（照旧），然后记下 xbase
    程序生成写进 Excel 的单有 xid（Excel 那一行算出来的单号）→ 对回程序里的单，不会变成两张。
    blocks = 这次真读到的 {(工作表, 门店)}：只删这些块里 Excel 已经没有的单。返回 {"new", "updated", "removed", "kept", "budgets"}
    """
    xt = _utc_iso(mtime)
    res = {"new": 0, "updated": 0, "removed": 0, "kept": 0}
    changes, bases, seen = [], [], set()
    xid = {o["xid"]: oid for oid, o in st.orders.items() if o.get("xid")}
    for r in rows:
        oid = xid.get(r["id"], r["id"])
        seen.add(oid)
        if st.deleted.get(oid) == "app":
            continue                                    # 程序里删了：excel_sync 会把 Excel 那一行清掉
        cur, meta = st.orders.get(oid), st.meta.get(oid, {})
        if cur is None and oid in st.deleted and xt is not None and str(st.deleted_at.get(oid) or "") > xt:
            continue                                    # 删掉的时候已经是比这份本地 Excel 新的了（同步盘还没传过来）→ 不加回来
        base = st.xbase.get(oid, {}) if cur is not None else {}   # 没有这张单：整行都写（以前只写改了的格 → 空白的单）
        ch, nb = {}, {}
        for f in EXCEL_FIELDS:
            v = r.get(f)
            if f == "origin" and cur is not None:
                continue
            if base.get(f, _MISS) != v:
                nb[f] = v                               # 包括空的：以后才知道 Excel 是不是改了这一格
            if cur is not None and cur.get(f) == v:
                continue
            if cur is None and v is None:
                continue
            w = meta.get(f)
            if f in base:
                if base[f] == v:
                    continue                            # Excel 没改这一格 → 程序的为准（等写回）
                if w and w[2] != "excel" and (xt is None or w[0] > xt):
                    continue                            # 两边都改了，程序改得更晚
            else:
                if cur is not None and v is None and f not in cur:
                    continue
                if w and w[2] != "excel":
                    continue                            # 老数据：程序里改过的以程序为准（会写回 Excel）
            ch[f] = v
        if ch:
            changes.append((oid, ch))
            res["new" if cur is None else "updated"] += 1
        if nb:
            bases.append((oid, nb))
    st.set_many(changes, src="excel")
    st.set_xbase(bases)
    gone = []
    read = set(sheets)
    for oid, o in st.orders.items():
        sh = str(o.get("ref") or "").split("!")[0]
        if oid in seen or sh not in read or not (o.get("origin") == "excel" or o.get("xid")):
            continue
        if blocks is not None and (sh, o.get("store")) not in blocks:
            continue
        m = st.meta.get(oid, {})
        last_app = max((w[0] for w in m.values() if w[2] != "excel"), default=None)
        last_x = max((w[0] for w in m.values() if w[2] == "excel"), default=None)
        if xt is not None and last_x and last_x > xt:
            res["kept"] += 1                            # 别的电脑从更新的 Excel 读到的：这台电脑的 Excel 还旧，不算删了
            continue
        if last_app is None or (xt is not None and xt > last_app):
            gone.append(oid)                            # Excel 里删了这一行（比程序里最后一次改得晚）
        else:
            res["kept"] += 1
    if gone:
        st.delete(gone, src="excel")
        res["removed"] = len(gone)
    bud, bb = [], {}
    bbase = st.xbase.get("_budget", {})
    for month, store, v in budgets:
        k = f"{month}|{store}"
        if bbase.get(k, _MISS) != v:
            bb[k] = v
        w = st.budget_meta.get(k)
        if st.budgets.get(k) == v:
            continue
        if k in bbase:
            if bbase[k] == v or (w and w[2] != "excel" and (xt is None or w[0] > xt)):
                continue
        elif w and w[2] != "excel":
            continue
        bud.append(st._ev("budget", k, "excel", v=v))
    st._write(bud)
    if bb:
        st.set_xbase([("_budget", bb)])
    res["budgets"] = len(bud)
    return res


STALE_MAX_MIN = 10       # 本地 Excel 比程序最后写的旧：最多等同步盘这么多分钟


def is_stale(st, path, mtime=None, now=None):
    """
    本地 Excel 比程序最后一次写进去的旧 = 同步盘还没把新文件传过来，先不读。
    只等 STALE_MAX_MIN 分钟；旁边有同步盘冲突副本也不等（2026-11-04 测试发现：同步盘换回一个旧版本、修改时间也是旧的，
    以前会一直「等同步盘」、再也不读 Excel，冲突副本也合并不了）
    """
    w = st.xstate.get("write") or {}
    mtime = os.path.getmtime(path) if mtime is None else mtime
    if not w.get("mtime") or mtime + 2 >= float(w["mtime"]):
        return False
    try:
        t = datetime.fromisoformat(str(w.get("t")).replace("Z", "+00:00"))
        now = now or datetime.now(t.tzinfo)
        if (now - t).total_seconds() > STALE_MAX_MIN * 60:
            return False
    except (TypeError, ValueError):
        return False
    import excel_sync as XS
    return not XS.conflict_copies(path)


def import_file(st, path):
    """读 Excel（先复制一份再读：原文件可能正被 Excel 打开 / 正在同步）→ sync_excel"""
    import shutil
    import tempfile
    mtime = os.path.getmtime(path)
    if is_stale(st, path, mtime):
        # 别的电脑刚写过 Excel，这台电脑的同步盘还没收到新文件：先不读（不然会把旧文件当成「Excel 里改回去了」）
        return {"stale": True, "new": 0, "updated": 0, "removed": 0, "kept": 0, "rows": 0, "sheets": [],
                "unknown": [], "mtime": mtime}
    tmp = os.path.join(tempfile.mkdtemp(prefix="piano_"), "piano.xlsx")
    shutil.copy2(path, tmp)
    try:
        info = {}
        rows_, budgets, sheets = read_excel(tmp, info)
    finally:
        try:
            shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)
        except Exception:
            pass
    st.reload()
    res = sync_excel(st, rows_, budgets, sheets, info.get("blocks"), mtime=mtime)
    res["mtime"] = mtime
    # Excel 里手填了的单，程序还挂着同一张「待登记」→ 合并掉（2026-11-09），以 Excel 为准
    import mail_match as MM
    res["absorbed"], res["absorb_undo"], res["absorb_pairs"] = MM.absorb_drafts(st)
    res["groups"] = materialize_groups(st)          # 同事手填的 BMS 凑单（CEN）：建 BMS 单
    res["hand_dups"] = hand_duplicates(st)
    res["rows"], res["sheets"], res["unknown"] = len(rows_), sheets, info.get("unknown") or []
    return res


DUP_DAYS = 3            # 手填的一行和程序登记的单：下单日期前后几天内
DUP_EUR = 0.05          # 金额差多少以内算一样


def hand_duplicates(st):
    """
    程序已经把这张单写进 PIANO 了，同事又手填了一行同样的（同一家门店、同一家供应商、日期前后 3 天、金额一样）
    → [(手填那行的单号, 程序那张单号)]。只列出来让人删掉一行，程序不自己删 Excel 的行
    （2026-11-28 用户：以后统一用程序发订单、自动登记 PIANO，免得乱——过渡期同事可能还会手填）
    """
    prog = {}
    for oid, o in st.orders.items():
        if o.get("origin") in ("excel", GROUP_SRC) or not o.get("xid") or o.get("state") == "cancel":
            continue
        prog.setdefault((str(o.get("store")), sup_key(o.get("supplier"))), []).append((oid, o))
    out = []
    for oid, o in st.orders.items():
        if o.get("origin") != "excel" or o.get("state") == "cancel" or o.get("part_of"):
            continue
        cands = prog.get((str(o.get("store")), sup_key(o.get("supplier"))))
        d = to_date(o.get("date"))
        if not cands or not d:
            continue
        for pid, p in cands:
            pd = to_date(p.get("date"))
            if pid == oid or not pd or abs((pd - d).days) > DUP_DAYS or p.get("xid") == o.get("xid"):
                continue
            a, b = o.get("amount"), p.get("amount")
            if a is None or b is None or abs(float(a) - float(b)) > DUP_EUR:
                continue
            out.append((oid, pid))
            break
    return out


def find_excel(folder):
    """
    共享文件夹里的 PIANO ACQUISTO MESILE：先用 NUOVO 正本（2026-11-02 用户：换成 NUOVO 那份——同事在正本里填，
    副本 _COPIA 不会跟着变；程序只是复制一份再读，不改原表），没有正本才用 COPIA / 别的
    """
    try:
        fs = [f for f in os.listdir(folder) if f.lower().endswith((".xlsx", ".xlsm")) and not f.startswith("~$")
              and "PIANO" in f.upper() and "CONFLICT" not in f.upper() and "冲突" not in f]
    except OSError:
        return ""
    fs.sort(key=lambda f: ("NUOVO" not in f.upper(), "COPIA" in f.upper(), f))
    return os.path.join(folder, fs[0]) if fs else ""


def prefer_original(path):
    """设置里存的是副本（…_COPIA.xlsx）、同一个文件夹里有正本（去掉 _COPIA）→ 返回正本路径，否则 None"""
    if not path:
        return None
    d, f = os.path.split(path)
    stem, ext = os.path.splitext(f)
    m = re.match(r"^(.*?)[ _-]*COPIA$", stem, re.I)
    if not m:
        return None
    orig = os.path.join(d, m.group(1) + ext)
    return orig if os.path.isfile(orig) else None


def export_excel(rs, path, subtitle=None):
    """
    把（筛选后的）订单导出成统一样式的报表（xlsx_report，2026-11-25 用户：导出都用新格式）：
    意大利文表头，状态上色（超期红 · 到货绿 · 待登记黄 · 取消灰），最后一行合计
    """
    import xlsx_report as XR
    Col = XR.Col
    lab = {"draft": "Da registrare", "ordered": "Ordinato", "confirmed": "Confermato", "shipped": "Spedito",
           "partial": "Arrivato in parte", "arrived": "Arrivato", "stuck": "Bloccato", "cancel": "Annullato",
           "void": "Non inviato · annullato", "deter": "DETER"}
    tone = {"arrived": "GREEN", "partial": "AMBER", "stuck": "RED", "cancel": "GREY", "draft": "AMBER",
            "confirmed": "BLUE", "shipped": "BLUE"}

    def d(v):
        if isinstance(v, (date, datetime)):
            return v if isinstance(v, date) else v.date()
        try:
            return date.fromisoformat(str(v)[:10]) if v else None
        except ValueError:
            return v
    cols = [Col("Data ordine", 13, "date"), Col("Negozio", 16), Col("Fornitore", 30, bold=True, group="sup"),
            Col("Ordine da", 13), Col("Importo imponibile", 16, "price", group="price"), Col("Stato", 20),
            Col("Confermato", 13, "date"), Col("Arrivo previsto", 14, "date"), Col("Arrivato", 13, "date"),
            Col("Giorni", 9, "num"), Col("Note", 44)]
    rows, marks = [], {}
    total = 0.0
    for k, r in enumerate(rs):
        st = r["status"]
        rows.append([d(r["date"]), r["store_name"], r["supplier"], r["buyer"], r["amount"],
                     lab.get(st, st) + (" (in ritardo)" if r["overdue"] else ""),
                     d(r["confirmed"]), d(r["expected"]), d(r["arrived"]), r["days"], r["note"]])
        t = "RED" if r["overdue"] else tone.get(st)
        if t:
            marks[(k, 5)] = t
        if r["overdue"]:
            marks[(k, 7)] = "RED"
        if st not in NOT_ORDERED:
            total += float(r["amount"] or 0)
    wb = XR.new_book()
    sub = subtitle or f"CASA&TE · {date.today():%d/%m/%Y} · {len(rows)} ordini · " \
                      f"totale € {total:,.2f} (imponibile, senza annullati / da registrare)"
    XR.add_sheet(wb, "Ordini", "Ordini fornitori", sub, cols, rows, marks=marks,
                 note="Rosso = in ritardo / bloccato · Verde = arrivato · Giallo = da registrare / in parte · "
                      "Blu = confermato / spedito · Grigio = annullato", tone="GREY")
    wb.save(path)
    return path


# ===================================================================== 程序生成的订单自动登记（2026-11-02 用户：以后都由程序登记，不手填 Excel）
SOURCES = {"store_orders": "总仓采购", "store_pdv": "门店采购", "wholesale": "DETERSIVO采购"}


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


# ===================================================================== 旧的待登记作废（2026-11-26 用户：「一定要谨慎再谨慎」）
# 总仓采购 / 门店采购 / DETERSIVO 每算一次就生成一批待登记；以前没发出去的旧单一直留着（METALTEX BMS 5 张、303 张里 202 张是重复的）。
# 规则：
#   ① 被取代：同一个来源、同一个范围（总仓 / 门店采购 = 同一家供应商；DETERSIVO = 同一家门店）后来又生成过一次
#      （按那张单第一次生成的日期比，确认下单会改下单日期，不能用它）→ 更早那次生成、还没发出的待登记作废：新的配货才是现在的计划；
#   ② 过期：生成超过 DRAFT_KEEP_DAYS 天还没发出 → 作废（库存、销量早就变了，要发也该重新算）。
# 只作废「没人动过」的：每个字段都是程序自动写的（auto / file）、没发过邮件（sent）、没关联过邮件（mails）、没订单号（no）。
# 人改过的、发过的一律不动。作废 = state cancel + void，不删：「看哪些 → 已作废的待登记」看得到，能恢复；
# 有人对它发订单邮件 / 确认 / 改字段 → 自动回到正常的单（Store._apply）。
DRAFT_KEEP_DAYS = 7
VOID_SRC = "void"
GEN_SOURCES = ("store_orders", "store_pdv", "wholesale")


def is_draft(o):
    """还在等发送的待登记（作废的、取消的不算）"""
    return bool(o.get("draft")) and o.get("state") != "cancel"


def _voidable(o, meta):
    if not o.get("draft") or o.get("sent") or o.get("mails") or o.get("no"):
        return False
    if o.get("state") == "cancel" and not o.get("void"):
        return False
    return all((w[2] if isinstance(w, (list, tuple)) and len(w) > 2 else "app") in ("auto", "file", VOID_SRC)
               for k, w in (meta or {}).items())


def gen_day(st, oid):
    """程序第一次生成这张单的日期（确认下单会把下单日期改成发出那天，所以看第一条记录）"""
    for e in st.hist.get(oid) or []:
        d = (e.get("f") or {}).get("date")
        if d:
            return to_date(d)
    return to_date((st.orders.get(oid) or {}).get("date"))


def _scope(st, o):
    src = o.get("origin")
    if src not in GEN_SOURCES:
        return None
    if src == "wholesale":
        return (src, str(o.get("store") or ""))
    return (src, st.canon(sup_key(o.get("supplier"))))


def stale_drafts(st, today=None, keep_days=None, scope=None):
    """
    哪些待登记该作废 → [{"id", "why": "superseded" / "expired", "by": 取代它的那次生成日期（ISO）或 None, "day": 生成日期}]
    scope(o) 为真才看（register_drafts 只看这次生成的范围）；keep_days = None / 0 → 不按天数过期
    """
    today = today or date.today()
    keep = DRAFT_KEEP_DAYS if keep_days is None else int(keep_days or 0)
    latest = {}
    for oid, o in st.orders.items():          # 每个范围最近一次生成（发出去的、确认的也算：那次配货是真的）
        if o.get("state") == "cancel" or o.get("kind") == "deter":
            continue
        k = _scope(st, o)
        d = gen_day(st, oid)
        if k and d and (k not in latest or d > latest[k]):
            latest[k] = d
    out = []
    for oid, o in st.orders.items():
        if not is_draft(o) or (scope is not None and not scope(o)):
            continue
        if not _voidable(o, st.meta.get(oid)):
            continue
        k, d = _scope(st, o), gen_day(st, oid)
        if not k or not d:
            continue
        if latest.get(k) and latest[k] > d:
            out.append({"id": oid, "why": "superseded", "by": latest[k].isoformat(), "day": d.isoformat()})
        elif keep > 0 and (today - d).days > keep:
            out.append({"id": oid, "why": "expired", "by": None, "day": d.isoformat()})
    return out


def void_drafts(st, items, today=None):
    """把 stale_drafts 的结果作废（state cancel + void）；返回作废了几张"""
    at = (today or date.today()).isoformat()
    ch = [(x["id"], {"state": "cancel", "void": {"why": x["why"], "at": at, "by": x.get("by")}})
          for x in items if x["id"] in st.orders and is_draft(st.orders[x["id"]])]
    st.set_many(ch, src=VOID_SRC)
    return len(ch)


def restore_drafts(st, ids):
    """撤销作废：回到待登记（人点的，之后不会再被自动作废）"""
    ch = [(oid, {"state": None, "void": None}) for oid in ids if (st.orders.get(oid) or {}).get("void")]
    st.set_many(ch)
    return len(ch)


def void_text(o):
    """作废原因（界面用，文字没翻译）"""
    v = o.get("void") or {}
    if v.get("why") == "superseded":
        return f"后来 {v.get('by') or ''} 又生成了这家的订单，这张没发出，已作废"
    if v.get("why") == "expired":
        return f"生成超过 {DRAFT_KEEP_DAYS} 天一直没发出，已作废"
    return ""


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
        if it.get("bms_of"):                 # BMS 单是哪几家店凑的（"1".."5"）
            f["bms_of"] = sorted(str(x) for x in it["bms_of"])
        if it.get("shares"):                 # 每家店那一份的金额（发出去以后拆成每家店一行，写进 PIANO 标 CEN）
            f["shares"] = {str(k): round(float(v or 0), 2) for k, v in it["shares"].items()}
        cur = st.orders.get(oid)
        if cur is None and st.deleted.get(oid) in ("mail", "merged"):
            continue                    # 已经和 Excel 手填的那张合并了（mail_match.merge_duplicates），重新配货不再生成
        if cur is None:
            f["draft"] = True
            changes.append((oid, f))
            res["new"] += 1
            continue
        meta = st.meta.get(oid, {})
        # 程序自动写的（auto）和从订单文件读回来的（file，order_record.sync_files）重新生成时照样更新
        ch = {k: v for k, v in f.items() if cur.get(k) != v and (not meta.get(k) or meta[k][2] in ("auto", "file"))}
        if cur.get("void") and cur.get("draft"):          # 作废了又重新生成（同一天又配了一次）→ 回到待登记
            ch.update({"state": None, "void": None})
        if ch:
            changes.append((oid, ch))
            res["updated"] += 1
    st.set_many(changes, src="auto")
    gone = [oid for oid, o in st.orders.items()
            if o.get("origin") == source and o.get("draft") and o.get("date") == day and oid not in ids
            and (in_scope is None or in_scope(o)) and all(w[2] in ("auto", "file") for w in st.meta.get(oid, {}).values())]
    if gone:
        st.delete(gone, src="auto")
    res["removed"] = len(gone)
    # 这次生成的范围里，以前几天生成、还没发出的待登记 → 作废（被这次取代）；只按「被取代」，不按天数
    old = [x for x in stale_drafts(st, today=today, keep_days=0,
                                   scope=lambda o: o.get("origin") == source and (in_scope is None or in_scope(o)))
           if x["why"] == "superseded"]
    res["voided"] = void_drafts(st, old, today=today) if old else 0
    res["drafts"] = sum(1 for o in st.orders.values() if is_draft(o))
    return res


def store_for(settings):
    """设置里有共享文件夹、没关掉自动登记 → 读好的 Store；不然 None"""
    cfg = (settings or {}).get("tracking") or {}
    folder = cfg.get("folder") or ""
    if not folder or not os.path.isdir(folder) or cfg.get("auto_register") is False:
        return None
    me = (cfg.get("me") or (settings or {}).get("user_name") or default_me()).strip()
    return Store(folder, me=me).reload()


def register_store_orders(settings, written, log=None, source="store_orders"):
    """
    LUCAS定制采购：write_plans 的结果 [(文件, 供应商, 去向, 箱数, 金额, 状态, SCHEDA)] → 待登记。
    source：总仓采购 "store_orders" / 门店采购 "store_pdv"（两边的单分开：同一家供应商两边都有也不会互相删）
    """
    st = store_for(settings)
    if st is None:
        return None
    items = [{"store": store_code(w[2]), "supplier": w[1], "colli": w[3], "amount": w[4], "file": w[0],
              "bms_of": list(w[7]) if len(w) > 7 and w[7] else None,
              "shares": dict(w[8]) if len(w) > 8 and w[8] else None}
             for w in written or []]
    sups = {sup_key(x["supplier"]) for x in items}
    res = register_drafts(st, source, items, in_scope=lambda o: sup_key(o.get("supplier")) in sups)
    if log:
        from i18n import tr
        log(tr("[订单跟踪] {0}：待登记 新的 {1} 张，更新 {2} 张，删掉 {3} 张", tr(SOURCES.get(source, source)),
               res["new"], res["updated"], res["removed"]))
        if res.get("voided"):
            log(tr("[订单跟踪] 以前生成、还没发出的 {0} 张待登记已作废（这次重新配货取代了；「看哪些 → 已作废的待登记」能恢复）",
                   res["voided"]))
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
        if res.get("voided"):
            log(tr("[订单跟踪] 以前生成、还没发出的 {0} 张待登记已作废（这次重新配货取代了；「看哪些 → 已作废的待登记」能恢复）",
                   res["voided"]))
    return res


def _norm_buyer(b):
    return re.sub(r"[\s.]+", "", str(b or "")).lower()


def buyers(st, days=180, today=None):
    """
    PIANO 里「Ordine Da」写过的人（近 days 天，用得多的在前）；同一个人几种写法（Pres. / Pres / pres.）只留最常见的写法。
    发订单时选「下单人」用（2026-11-28 用户：以后统一用程序发、自动登记 PIANO——写进去的要和同事手填的一样）
    """
    since = (today or date.today()) - timedelta(days=days)
    cnt, spell = Counter(), {}
    for o in st.orders.values():
        b = str(o.get("buyer") or "").strip()
        d = to_date(o.get("date"))
        if not b or o.get("origin") != "excel" or (d and d < since):
            continue
        k = _norm_buyer(b)
        cnt[k] += 1
        spell.setdefault(k, Counter())[b] += 1
    return [spell[k].most_common(1)[0][0] for k, _n in cnt.most_common()]


def excel_spelling(st, supplier, days=365, today=None):
    """
    这家供应商同事在 PIANO 里最常用的写法（只认 sup_key 一样的：PETRILLO → Petrillo）；没写过就照原样。
    程序登记进 PIANO 时用它，免得同事还要手改名字、表里一家供应商两种写法
    """
    k = sup_key(supplier)
    since = (today or date.today()) - timedelta(days=days)
    c = Counter()
    for o in st.orders.values():
        if o.get("origin") != "excel" or sup_key(o.get("supplier")) != k:
            continue
        d = to_date(o.get("date"))
        if d and d < since:
            continue
        c[str(o.get("supplier") or "").strip()] += 1
    c.pop("", None)
    return c.most_common(1)[0][0] if c else supplier


def ordered_fields(st, o, buyer=None):
    """
    待登记 → 已下单时一起改的字段（发订单邮件 / 确认已下单都用）：下单人、供应商照 PIANO 的写法、
    DETERSIVO 的单标 DETER（同事手填 PIANO 时确认 / 到货格写 DETER、不跟踪确认到货；程序登记时照写，不用人再改）
    """
    f = {}
    b = str(buyer or "").strip()
    if b:
        f["buyer"] = b
    elif not o.get("buyer"):
        f["buyer"] = st.me
    sp = excel_spelling(st, o.get("supplier"))
    if sp and sp != o.get("supplier"):
        f["supplier"] = sp
    if str(o.get("origin") or "") == "wholesale" and o.get("kind") != "deter":
        f["kind"] = "deter"
    return f


def confirm_drafts(st, ids, today=None, buyer=None):
    """确认已下单：待登记 → 已下单，下单日期 = 今天（发给供应商的那天）；下单人 = buyer（没给、单上也没有就写自己）"""
    day = (today or date.today()).isoformat()
    ch = []
    for oid in ids:
        o = st.orders.get(oid)
        if not o or not o.get("draft") or (o.get("state") == "cancel" and not o.get("void")):
            continue
        f = {"draft": None, "date": day}
        f.update(ordered_fields(st, o, buyer))
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
        v = counted_amount(r)                      # BMS 凑单的金额算在每家店那一份上
        c = 1 if (store or not r.get("share")) else 0   # 全部门店：BMS 凑单算一张
        w["n"] += c
        w["amount"] += v
        n += c
        amt += v
    vals = [v for k, v in st.budgets.items() if k.startswith(ym + "|") and (not store or k.split("|", 1)[1] == store)]
    return {"budget": sum(vals) if vals else None, "amount": amt, "n": n, "weeks": weeks}
