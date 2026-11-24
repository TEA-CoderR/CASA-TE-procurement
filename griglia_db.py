# -*- coding: utf-8 -*-
"""
程序里的 Griglia（2026-11-24 第 1 步，设计见 claude.ai「Griglia 模块设计方案」）。不依赖界面。

以后 Griglia 只在程序里改；第 1 步先做：从现在的 Excel 导入 · 商品目录（只看）· 价格自动更新 · 导出和原表一样的 Excel ·
和 Excel 对比（过渡期 Excel 还是正本，对得上再往下做）。

放在同步文件夹（用户 11-24 定：方案 A，和订单跟踪一样）：工作文件夹「0 Griglia\\Griglia programma」
    base\\base_<时间>_<电脑>.json.gz        导入的那一份（GRIGLIA 页 67 列一列不少：值 + 公式；其他几页的值）
    base\\template_<时间>_<电脑>.xlsx       导出用的模板（原表不带图片的副本，格式 / 表头 / 其他几页都在）
    log_<电脑>_<用户>.jsonl                 每台电脑只写自己的改动记录（只加不改，读的时候合起来；第 1 步只有「导入」）
    auto.json                               程序算的列（每家批发商价格 / PA MIGLIORE / Fornitore / 卖价），谁算都一样，后算的盖掉
    history\\prezzi_<年>-W<周>.json         每周的批发商价格（保留历史）

一个商品的「状态」（设计稿 5 种）：在售 active · 新品 new · 暂停 paused（这周批发商都没货，程序自动）·
停用 off（以前划了删除线的）· 被替换 replaced（替换记录里的原商品）。
"""
import gzip
import json
import os
import re
from collections import Counter
from datetime import date, datetime

import griglia as G
from utils import clean_ean

DIR = "Griglia programma"
FORMAT = 1
STATUS = ("active", "new", "paused", "off", "replaced")
# 程序自动的列（Excel 的表头，大写比较）：以后不让人填
AUTO_HEADS = ("MELONI", "MEGA CASH", "F.LLI PETRILLO", "NEW GRIECO", "PA MIGLIORE", "FORNITORE", "PVP CASA & TE")
ORDER_HEADS = ("COLLI",)           # 订单那几列：不搬（导出时空着，和原表一样）
IMG_HEADS = ("IMMAGINE", "FOTO")
MARGIN_LOW = 0.15                  # 毛利率低于这个：提醒（导入检查 / 以后保存检查）
ISSUES = (("cats", "两套分类不一样"), ("shifted", "列错位"), ("nocode", "没有 Cod.Art."),
          ("below_cost", "卖价低于最低进价"), ("dup", "条码重复"),
          ("off", "划了删除线 → 停用"), ("replaced", "替换记录里的原商品 → 被替换"))


# ------------------------------------------------------------------ 位置 / 记录
def store_dir(root):
    return os.path.join(G.grid_dir(root), DIR)


def me():
    import workspace as W
    return W.me()


def user():
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", os.environ.get("USERNAME") or os.environ.get("USER") or "user") or "user"


def log_path(store, pc=None, usr=None):
    return os.path.join(store, "log_%s_%s.jsonl" % (pc or me(), usr or user()))


def read_events(store):
    """所有电脑的记录合起来，按时间排"""
    out = []
    if not os.path.isdir(store):
        return out
    for f in sorted(os.listdir(store)):
        if not (f.startswith("log_") and f.endswith(".jsonl")) or "Conflict" in f:
            continue
        try:
            with open(os.path.join(store, f), encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            out.append(json.loads(line))
                        except ValueError:
                            pass                    # 同步盘传了一半的最后一行
        except OSError:
            pass
    out.sort(key=lambda e: e.get("t") or "")
    return out


def add_event(store, ev, pc=None, usr=None):
    os.makedirs(store, exist_ok=True)
    ev = dict(ev)
    ev.setdefault("t", datetime.now().isoformat(timespec="seconds"))
    ev.setdefault("pc", pc or me())
    ev.setdefault("user", usr or user())
    with open(log_path(store, pc, usr), "a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev


def current_import(store):
    """最近一次导入的记录（base / template 文件还在的）；没导入过返回 None"""
    for ev in reversed(read_events(store)):
        if ev.get("op") == "import" and os.path.isfile(os.path.join(store, "base", ev.get("base") or "")):
            return ev
    return None


# ------------------------------------------------------------------ 读 Excel
def _plain(v):
    """格子的值 → 能存进 JSON 的：日期写成文字，#REF! 这类错误当空"""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d") if not (v.hour or v.minute or v.second) else v.isoformat(timespec="seconds")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("#") and s.upper().rstrip("!?") in ("#REF", "#VALUE", "#N/A", "#DIV/0", "#NAME", "#NUM", "#NULL"):
            return None
        return v
    if isinstance(v, bool) or v is None or isinstance(v, (int, float)):
        return v
    return str(v)


def _canon(f, col, row, base_row):
    """公式 → 换成「写在第 base_row 行」的样子（同一列每行的公式就能比较、导出时再换回每一行）"""
    from openpyxl.formula.translate import Translator
    from openpyxl.utils import get_column_letter as L
    try:
        return Translator(f, origin=f"{L(col + 1)}{row}").translate_formula(f"{L(col + 1)}{base_row}")
    except Exception:
        return None


def move_formula(f, col, base_row, row):
    from openpyxl.formula.translate import Translator
    from openpyxl.utils import get_column_letter as L
    if row == base_row:
        return f
    return Translator(f, origin=f"{L(col + 1)}{base_row}").translate_formula(f"{L(col + 1)}{row}")


def _find_sheet(wb):
    sheets = sorted(wb.worksheets, key=lambda ws: 0 if G._norm(ws.title) == "GRIGLIA" else 1)
    for ws in sheets:
        head = list(ws.iter_rows(max_row=10, values_only=True))
        if any(G._norm(v) == "CODICE EAN" for r in head for v in r):
            return ws.title
    raise ValueError("Griglia 里找不到「Codice EAN」表头（所有工作表都看过了；表头要在前 10 行）")


def read_excel(path, progress=None):
    """
    现在的 SCHEDA DETERSIVI → 导入用的快照（还没存）。GRIGLIA 页每一列都留（值 + 公式），其他有内容的页留值。
    progress(文字)：进度
    """
    from openpyxl import load_workbook
    say = progress or (lambda *_: None)
    say("读公式")
    wbf = load_workbook(path, read_only=True, data_only=False)
    try:
        title = _find_sheet(wbf)
        fr = list(wbf[title].iter_rows(values_only=True))
        head = fr[:10]
        struck = G.struck_rows(wbf[title], head)
    finally:
        wbf.close()
    say("读值")
    wbv = load_workbook(path, read_only=True, data_only=True)
    try:
        vr = list(wbv[title].iter_rows(values_only=True))
        others = {}
        for ws in wbv.worksheets:
            if ws.title == title:
                continue
            rows = [list(map(_plain, r)) for r in ws.iter_rows(max_row=6000, values_only=True)]
            while rows and not any(v not in (None, "") for v in rows[-1]):
                rows.pop()
            if rows:
                width = max(max((i + 1 for i, v in enumerate(r) if v not in (None, "")), default=0) for r in rows)
                others[ws.title] = [r[:width] for r in rows]
    finally:
        wbv.close()
    say("整理")
    hr = next(i for i, r in enumerate(fr[:10]) if any(G._norm(v) == "CODICE EAN" for v in r))
    heads = [str(v).strip() if v is not None else "" for v in fr[hr]]
    width = max(i + 1 for i, h in enumerate(heads) if h)
    groups, cur = [], ""
    grow = fr[hr - 1] if hr > 0 else ()
    for i in range(width):
        g = grow[i] if i < len(grow) else None
        if g not in (None, ""):
            cur = re.sub(r"\s+", " ", str(g)).strip()
        groups.append(cur)
    base = hr + 2                                   # 第一行数据（Excel 行号）
    norm = [G._norm(h) for h in heads[:width]]
    img_col = next((i for i, h in enumerate(norm) if h in IMG_HEADS), None)
    # 每一列最常见的公式（换到第一行数据的样子）= 这一列的公式模板；和模板不一样的行单独记
    cnt = [Counter() for _ in range(width)]
    canon_rows = {}
    for n, r in enumerate(fr[hr + 1:], base):
        for c in range(min(width, len(r))):
            v = r[c]
            if isinstance(v, str) and v.startswith("=") and c != img_col:
                k = _canon(v, c, n, base) if "#REF!" not in v.upper() else None
                canon_rows[(n, c)] = k
                if k:
                    cnt[c][k] += 1
    nonempty = Counter()
    for r in fr[hr + 1:]:
        for c in range(min(width, len(r))):
            if r[c] not in (None, ""):
                nonempty[c] += 1
    tpl = [None] * width
    for c in range(width):
        if cnt[c]:
            k, n = cnt[c].most_common(1)[0]
            if n * 2 >= nonempty[c]:
                tpl[c] = k
    rows, seen = [], set()
    for n, (f_r, v_r) in enumerate(zip(fr[hr + 1:], vr[hr + 1:]), base):
        vals = [(_plain(v_r[c]) if c < len(v_r) else None) for c in range(width)]
        e = clean_ean(vals[norm.index("CODICE EAN")])
        if not e:
            if any(v not in (None, "") for v in vals):
                rows.append({"n": n, "ean": "", "v": vals, "f": {}, "struck": n in struck, "blank": True})
            continue
        fx = {}
        for c in range(width):
            if c == img_col:
                continue
            raw = f_r[c] if c < len(f_r) else None
            k = canon_rows.get((n, c))
            if isinstance(raw, str) and raw.startswith("="):
                if k != tpl[c]:
                    fx[str(c)] = k                    # None = 这一格的公式有 #REF! 等：导出成值
            elif tpl[c] and raw not in (None, ""):
                fx[str(c)] = None                     # 这一列一般是公式，这一行人手写了值
            elif tpl[c]:
                fx[str(c)] = ""                       # 这一列一般是公式，这一行空着
        refs = [c for c in range(width) if isinstance(f_r[c] if c < len(f_r) else None, str)
                and str(f_r[c]).strip().upper().startswith("#REF")
                or (canon_rows.get((n, c), 1) is None)]
        rows.append({"n": n, "ean": e, "v": vals, "f": fx, "struck": n in struck, "dup": e in seen, "ref": refs})
        seen.add(e)
    return {"format": FORMAT, "sheet": title, "header_row": hr + 1, "first_row": base,
            "head": [[_plain(v) for v in r[:width]] for r in fr[:hr - 1]] if hr > 1 else [],
            "columns": [{"name": heads[c], "group": groups[c], "tpl": tpl[c], "img": c == img_col} for c in range(width)],
            "rows": rows, "others": others, "source": os.path.abspath(path),
            "source_mtime": os.path.getmtime(path), "source_name": os.path.basename(path)}


# ------------------------------------------------------------------ 目录（导入的 + 程序算的）
class Catalog:
    """
    snap = 导入的快照，auto = auto.json，replaced = {原条码: 新条码}（替换记录）。
    items = [商品]（每个商品一个 dict，界面直接用）；col = 字段 → 列号
    """

    def __init__(self, snap, auto=None, replaced=None, imp=None):
        self.snap, self.auto, self.imp = snap, auto or {}, imp or {}
        self.columns = snap["columns"]
        self.heads = [G._norm(c["name"]) for c in self.columns]
        self.col = self._fields()
        rows = [r for r in snap["rows"] if r.get("ean")]
        vals = [r["v"] for r in rows]
        parsed = G.parse_rows([[c["name"] for c in self.columns]] + vals,
                              struck={i + 2 for i, r in enumerate(rows) if r.get("struck")})
        by_row = {}
        for src in (parsed["items"], parsed["struck"]):
            for it in src.values():
                by_row[it["row"]] = it
        self.bad_rows = {rows[n - 2]["n"] for n in parsed.get("bad_rows") or ()}
        self.labels = sorted((self.auto.get("files") or {}).keys())
        self.items = []
        replaced = replaced or {}
        for i, r in enumerate(rows):
            p = by_row.get(i + 2) or {}
            self.items.append(self._item(i, r, p, replaced))
        self.by_ean = {}
        for x in self.items:
            self.by_ean.setdefault(x["ean"], x)

    def _fields(self):
        col = {}
        for i, h in enumerate(self.heads):
            f = G._FIELDS.get(h)
            if f and f not in col:
                col[f] = i
            if h.startswith("FORNITOR") and "DIRETT" in h:
                col.setdefault("direct", i)
        for f, h in (("iva", "IVA%"), ("pvi", "PVI"), ("stato", "STATO PRODOTTO"), ("ruolo_promo", "RUOLO PROMOZIONE"),
                     ("reparto", "DESCR.REPARTO"), ("gruppo", "DESCR.GRUPPO"), ("sgruppo", "DESCR.SOTTOGRUPPO"),
                     ("cod_forn", "CODICE FORNITORE"), ("strato", "STRATO"), ("pallet", "QTA_X_PALLET")):
            if h in self.heads:
                col[f] = self.heads.index(h)
        col["sup"] = {h: i for i, h in enumerate(self.heads) if h in G._SUP_COLS}
        col["store"] = {h: i for i, h in enumerate(self.heads) if h in G.STORES}
        left = [col.get(f) for f in ("cat1", "cat2", "cat3")]
        right = []
        for f, h in (("cat1", "CAT.1"), ("cat2", "CAT.2"), ("cat3", "CAT.3")):
            idx = [i for i, x in enumerate(self.heads) if x == h and i != col.get(f)]
            right.append(idx[-1] if idx else None)
        col["left"], col["right"] = left, right
        return col

    def cell(self, x, field):
        c = self.col.get(field)
        return self.value(x, c) if isinstance(c, int) else None

    def value(self, x, c):
        """格子的值：程序自动的列用 auto 算的，别的用导入的"""
        a = x.get("auto") or {}
        if c in a:
            return a[c]
        v = x["v"]
        return v[c] if c < len(v) else None

    def _item(self, i, r, p, replaced):
        v, col = r["v"], self.col
        ean = r["ean"]
        g = lambda c: (v[c] if c is not None and c < len(v) else None)

        def txt(c):
            s = g(c)
            return re.sub(r"\s+", " ", str(s)).strip() if s not in (None, "") else ""

        x = {"i": i, "n": r["n"], "ean": ean, "v": v, "f": r.get("f") or {}, "struck": bool(r.get("struck")),
             "code": p.get("code") or "", "name": p.get("name") or txt(col.get("name")),
             "brand": p.get("brand") or "", "direct": p.get("direct") or "",
             "pack": p.get("pack"), "cats": [re.sub(r"\s+", " ", c).strip() for c in (p.get("cats") or ["", "", ""])],
             "cats_left": [txt(c) for c in col["left"]], "cats_right": [txt(c) for c in col["right"]],
             "stores": p.get("stores") or [], "stato": txt(col.get("stato")).upper(),
             "iva": G._num(g(col.get("iva"))), "issues": []}
        a = (self.auto.get("items") or {}).get(ean) or {}
        x["prices"] = dict(a.get("p") or {}) if self.auto else dict(p.get("prices") or {})
        x["best"] = a.get("best") if self.auto else (p.get("best") or p.get("price"))
        x["best_sup"] = a.get("sup") if self.auto else (p.get("supplier") or "")
        x["pvp"] = a.get("pvp") if a.get("pvp") else G._num(g(col.get("pvp")))
        x["in_quotes"] = bool(a.get("p")) if self.auto else None
        pr = {h: v for h, v in x["prices"].items() if v}
        x["best_short"] = short_wholesaler(min(pr, key=lambda h: (pr[h], h))) if pr else ""
        x["direct_short"] = short_company(x["direct"])
        auto = {}
        if self.auto:
            for h, c in col["sup"].items():
                auto[c] = (a.get("p") or {}).get(h)
            if col.get("best") is not None:
                auto[col["best"]] = x["best"] if x["best"] is not None else G._num(g(col["best"]))
            if col.get("supplier") is not None and a.get("sup"):
                auto[col["supplier"]] = a["sup"]
            if col.get("pvp") is not None and a.get("pvp"):
                auto[col["pvp"]] = a["pvp"]
        x["auto"] = auto
        iva = x["iva"]
        iva = (iva / 100.0 if iva and iva > 1 else iva) if iva else 0.22
        x["pvi"] = x["pvp"] / (1 + iva) if x["pvp"] else None
        x["margin"] = (x["pvi"] - x["best"]) if x["pvi"] and x["best"] else None
        x["mrg"] = x["margin"] / x["pvi"] if x["margin"] is not None and x["pvi"] else None
        # 状态
        if ean in replaced:
            x["status"], x["new_ean"] = "replaced", replaced[ean]
        elif x["struck"]:
            x["status"] = "off"
        elif x["stato"] == "NUOVO":
            x["status"] = "new"
        else:
            x["status"] = "active"
        if x["status"] in ("active", "new") and x["in_quotes"] is False:
            x["status"] = "paused"
        # 导入时要人看的
        iss = x["issues"]
        if cats_differ(x["cats_left"], x["cats_right"]):
            iss.append("cats")
        if r["n"] in self.bad_rows:
            iss.append("shifted")
        if not x["code"]:
            iss.append("nocode")
        if x["pvi"] and x["best"] and x["pvi"] < x["best"]:
            iss.append("below_cost")
        if r.get("dup"):
            iss.append("dup")
        if x["status"] in ("off", "replaced"):
            iss.append(x["status"])
        return x

    # ---- 给界面 ----
    def ref_columns(self):
        """原表里写着 #REF! 的列（导出时空着）：[(列名, 几格)]"""
        c = Counter(k for x in self.snap["rows"] for k in (x.get("ref") or ()))
        return [(self.columns[k]["name"].replace("\n", " ") or f"#{k + 1}", n) for k, n in sorted(c.items())]

    def counts(self):
        c = Counter(x["status"] for x in self.items)
        return {s: c.get(s, 0) for s in STATUS}

    def issue_counts(self):
        c = Counter(k for x in self.items for k in x["issues"])
        return {k: c.get(k, 0) for k, _t in ISSUES}

    def tree(self, items=None):
        """{大类: {中类: {小类: 个数}}}（空的写成 ""）"""
        out = {}
        for x in (self.items if items is None else items):
            c1, c2, c3 = (x["cats"] + ["", "", ""])[:3]
            out.setdefault(c1, {}).setdefault(c2, Counter())[c3] += 1
        return out

    def suppliers(self, kind):
        """kind = "direct"（直厂）/ "best"（这周最便宜的批发商）→ [(名称, 个数)] 多的在前"""
        c = Counter((x["direct"] if kind == "direct" else x["best_short"]) for x in self.items)
        return sorted(c.items(), key=lambda kv: (kv[0] == "", -kv[1], kv[0]))


def short_wholesaler(col):
    """Griglia 的价格列名 → 短名（F.LLI PETRILLO → PETRILLO）"""
    return re.sub(r"^F\.?\s*LLI\s+", "", str(col or "").strip(), flags=re.I)


_LEGAL = re.compile(r"[\s,.]+(S\.?\s?R\.?\s?L\.?S?|S\.?\s?P\.?\s?A\.?|S\.?\s?N\.?\s?C\.?|S\.?\s?A\.?\s?S\.?|"
                    r"UNIPERSONALE|ITALIA|ITALY|& C\.?)\.?$", re.I)


def short_company(name):
    """表格里显示的短名：HENKEL ITALIA SRL - DIV.DETERSIVI E BEAUTY CARE → HENKEL；全名在鼠标提示里"""
    s = re.split(r"\s+[-–]\s+", str(name or "").strip())[0]
    s = re.split(r"\s(?:S\.?R\.?L\.?S?|S\.?P\.?A\.?|S\.?N\.?C\.?|S\.?A\.?S\.?)(?=\s|$|\.)", s, flags=re.I)[0]
    prev = None
    while s and s != prev:
        prev = s
        s = _LEGAL.sub("", s).strip(" ,.-")
    return s or str(name or "").strip()


def cats_differ(left, right):
    """两套分类对不上：两边都写了的那一级写的不一样（只差空格 / 大小写不算）"""
    a = [re.sub(r"\s+", " ", s or "").strip().upper() for s in left]
    b = [re.sub(r"\s+", " ", s or "").strip().upper() for s in right]
    return any(x and y and x != y for x, y in zip(a, b))


def load_snapshot(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def load_auto(store):
    p = os.path.join(store, "auto.json")
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def load(root, replaced=None, source=None):
    """同步文件夹里程序的 Griglia → Catalog；还没导入过返回 None"""
    store = store_dir(root)
    ev = current_import(store)
    if not ev:
        return None
    snap = load_snapshot(os.path.join(store, "base", ev["base"]))
    if replaced is None:
        replaced = replaced_from_log(root, source)
    return Catalog(snap, load_auto(store), replaced, ev)


def replaced_from_log(root, source=None):
    """找替代商品的替换记录（griglia_replace，本机 + 同步文件夹里每台电脑的）：{原条码: 新条码}"""
    try:
        import griglia_replace as GR
        if source:
            GR.set_shared(source)
        done = GR.load_done(G.grid_dir(root))
    except Exception:
        return {}
    out = {}
    for old, d in (done or {}).items():
        new = (d or {}).get("new") if isinstance(d, dict) else None
        if old and new:
            out[old] = new
    return out


# ------------------------------------------------------------------ 导入
def save_import(root, snap, source_path, progress=None, url=None, pc=None, usr=None, now=None):
    """
    导入：快照存成 base\\base_<时间>_<电脑>.json.gz，原表另存一份不带图片的当导出模板，记一条「导入」。
    返回导入记录
    """
    import griglia_replace as GR
    say = progress or (lambda *_: None)
    store = store_dir(root)
    bdir = os.path.join(store, "base")
    os.makedirs(bdir, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    pc = pc or me()
    name = f"base_{stamp}_{pc}.json.gz"
    tname = f"template_{stamp}_{pc}.xlsx"
    say("存导出模板")
    GR.light_copy(source_path, os.path.join(bdir, tname), url=url)
    say("存数据")
    tmp = os.path.join(bdir, name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(bdir, name))
    n = sum(1 for r in snap["rows"] if r.get("ean"))
    return add_event(store, {"op": "import", "base": name, "template": tname, "source": snap.get("source"),
                             "source_name": snap.get("source_name"), "source_mtime": snap.get("source_mtime"),
                             "n": n}, pc=pc, usr=usr)


# ------------------------------------------------------------------ 程序算的列
def _clean_prices(row):
    """一个商品各家件价里明显写错的去掉（和 Griglia 统计「价格可疑」同一个规则）"""
    ok = {k: p for k, p in row.items() if p and p >= G.MIN_PRICE}
    if len(ok) >= 2:
        vals = sorted(ok.values())
        mid = vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
        ok = {k: p for k, p in ok.items() if p >= mid * G.ODD_RATIO}
    return ok


def compute_auto(cat, quotes, files=None, pvp=None, now=None):
    """
    quotes = {报价批发商名: {条码: {"price": 件价…}}}（commands.read_quote_files），pvp = {条码: 数据库卖价 PrezzoVedi1}。
    → auto.json 的内容：每个商品每家批发商的件价（列名照 Griglia：MELONI / MEGA CASH / F.LLI PETRILLO / NEW GRIECO）、
    PA MIGLIORE = 最低、Fornitore = 最便宜那家（写成 Griglia 里这家最常见的写法）、PVP = 数据库卖价
    """
    labels = sorted(quotes)
    colmap = {}                                   # Griglia 价格列 → 报价批发商名
    for h in cat.col["sup"]:
        lab = G.match_label(h, labels)
        if lab:
            colmap[h] = lab
    spell = G.full_names({x["ean"]: {"supplier": cat.cell(x, "supplier") or ""} for x in cat.items}, labels)
    items = {}
    for x in cat.items:
        e = x["ean"]
        raw = {h: (quotes.get(lab) or {}).get(e, {}).get("price") for h, lab in colmap.items()}
        p = _clean_prices({h: float(v) for h, v in raw.items() if v})
        d = {"p": {h: round(v, 4) for h, v in p.items()}}
        if p:
            h = min(p, key=lambda k: (p[k], k))
            d["best"] = round(p[h], 4)
            d["sup"] = spell.get(colmap[h]) or colmap[h]
        if pvp and pvp.get(e):
            d["pvp"] = round(float(pvp[e]), 2)
        items[e] = d
    t = (now or datetime.now())
    return {"at": t.isoformat(timespec="seconds"), "pc": me(), "files": {lab: (files or {}).get(lab, "") for lab in labels},
            "colmap": colmap, "pvp_at": t.isoformat(timespec="seconds") if pvp else None, "items": items}


def save_auto(root, auto):
    store = store_dir(root)
    os.makedirs(os.path.join(store, "history"), exist_ok=True)
    p = os.path.join(store, "auto.json")
    with open(p + ".tmp", "w", encoding="utf-8", newline="") as f:
        json.dump(auto, f, ensure_ascii=False)
    os.replace(p + ".tmp", p)
    y, w, _d = datetime.fromisoformat(auto["at"]).isocalendar()
    h = os.path.join(store, "history", f"prezzi_{y}-W{w:02d}.json")
    hist = {"at": auto["at"], "files": auto.get("files"),
            "items": {e: d["p"] for e, d in auto["items"].items() if d.get("p")}}
    with open(h + ".tmp", "w", encoding="utf-8", newline="") as f:
        json.dump(hist, f, ensure_ascii=False)
    os.replace(h + ".tmp", h)
    return p


def db_prices(db, cat):
    """数据库卖价（PrezzoVedi1，只读）：{条码: 卖价}。Griglia 里有 Cod.Art. 的按它查，没有的按条码查编号"""
    import pandas as pd
    import reorder_service as R
    code_of = {x["ean"]: x["code"] for x in cat.items if x["code"]}
    miss = [x["ean"] for x in cat.items if not x["code"]]
    if miss:
        for e, c in R.codes_from_eans(db, miss).items():
            code_of[e] = c
    by_code = {}
    for e, c in code_of.items():
        if str(c).isdigit():
            by_code.setdefault(str(int(c)), []).append(e)
    out, codes = {}, sorted(by_code)
    for i in range(0, len(codes), 2000):
        chunk = [int(c) for c in codes[i:i + 2000]]
        ph = ", ".join(["%s"] * len(chunk))
        df = pd.read_sql(f"SELECT CodArticolo, PrezzoVedi1 FROM prodotti WHERE CodArticolo IN ({ph})",
                         db.engine, params=tuple(chunk))
        for r in R.records(df):
            v = r.get("PrezzoVedi1")
            if v and float(v) > 0:
                for e in by_code.get(str(int(r["CodArticolo"])), ()):
                    out[e] = float(v)
    return out


# ------------------------------------------------------------------ 导出（和原表一样）
def export(root, cat, path, url=None, progress=None):
    """
    程序里的 Griglia → 和原表一样的 Excel：用导入时存的模板（原表不带图片的副本：表头、格式、其他几页、外部链接都在），
    GRIGLIA 页的商品行重新写：每个商品原来的值 / 公式（公式换到新的行号）、程序算的列写这周的、图片列写 IMAGE 公式、
    订单那几列空着、停用 / 被替换的划删除线。返回写了几个商品
    """
    from copy import copy
    from openpyxl import load_workbook
    from openpyxl.styles import Font
    import griglia_replace as GR
    say = progress or (lambda *_: None)
    store = store_dir(root)
    tpl = os.path.join(store, "base", cat.imp.get("template") or "")
    if not os.path.isfile(tpl):
        raise FileNotFoundError(f"导出模板不见了：{tpl}（重新导入一次就有了）")
    say("读模板")
    wb = load_workbook(tpl)
    ws = wb[cat.snap["sheet"]]
    first = cat.snap["first_row"]
    width = len(cat.columns)
    last = ws.max_row
    # 每一列用最常见的格式（原表每一行格式基本一样；个别人手标颜色的格子不带过来）
    styles = []
    for c in range(1, width + 1):
        cnt = Counter()
        for r in range(first, min(last, first + 400) + 1):
            cnt[ws.cell(r, c)._style] += 0 if ws.cell(r, c).value is None else 1
        styles.append(copy(cnt.most_common(1)[0][0]) if cnt else None)
    height = ws.row_dimensions[first].height
    order = [x for x in cat.items if x["status"] not in ("off", "replaced")] + \
            [x for x in cat.items if x["status"] in ("off", "replaced")]
    blanks = [r for r in cat.snap["rows"] if not r.get("ean")]
    say("写商品")
    colli = {i for i, h in enumerate(cat.heads) if h in ORDER_HEADS}
    for k, x in enumerate(order):
        n = first + k
        for c in range(width):
            cell = ws.cell(n, c + 1)
            if styles[c] is not None:
                cell._style = copy(styles[c])
            col = cat.columns[c]
            if col.get("img"):
                cell.value = GR.image_formula(x["ean"], url)
                continue
            if c in colli:
                cell.value = None
                continue
            if c in x["auto"]:
                cell.value = x["auto"][c]
                continue
            fx = x["f"].get(str(c), False)
            if fx is False and col.get("tpl"):
                cell.value = move_formula(col["tpl"], c, first, n)
            elif fx:
                cell.value = move_formula(fx, c, first, n)
            elif fx == "":
                cell.value = None
            else:
                cell.value = x["v"][c] if c < len(x["v"]) else None
            if isinstance(cell.value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cell.value) and \
                    G._norm(col["name"]).startswith("DATA"):
                cell.value = datetime.strptime(cell.value, "%Y-%m-%d")
        if x["status"] in ("off", "replaced"):
            for c in range(width):
                cell = ws.cell(n, c + 1)
                f = copy(cell.font) if cell.font else Font()
                cell.font = Font(name=f.name, size=f.size, bold=f.bold, italic=f.italic, strike=True, color="FF808080")
        if height:
            ws.row_dimensions[n].height = height
    end = first + len(order)
    for b in blanks:                                     # 没有条码的行（分隔 / 备注）照原样放最后
        for c in range(width):
            ws.cell(end, c + 1).value = b["v"][c] if c < len(b["v"]) else None
        end += 1
    if last >= end:
        ws.delete_rows(end, last - end + 1)
    if ws.auto_filter and ws.auto_filter.ref:
        m = re.match(r"([A-Z]+)(\d+):([A-Z]+)\d+", ws.auto_filter.ref)
        if m:
            ws.auto_filter.ref = f"{m.group(1)}{m.group(2)}:{m.group(3)}{max(end - 1, first)}"
    say("存文件")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = path + ".tmp.xlsx"
    wb.save(tmp)
    os.replace(tmp, path)
    return len(order)


# ------------------------------------------------------------------ 和 Excel 对比（过渡期）
COMPARE_FIELDS = (("name", "名称"), ("code", "Cod.Art."), ("pack", "PZ*COLLI"), ("brand", "Marca"),
                  ("cats", "分类"), ("direct", "FORNITORI DIRETTO"), ("off", "停用"))


def compare(cat, grid):
    """
    程序里的 vs 现在 Excel 里的（griglia.load 的结果）。程序自动的列（价格、Fornitore、卖价）不比。
    → [{"ean", "name", "kind": only_excel / only_prog / changed, "diff": [(字段名, 程序, Excel)]}]
    """
    ex = dict(grid.get("items") or {})
    ex_off = dict(grid.get("struck") or {})
    out = []
    mine = {x["ean"]: x for x in cat.items}
    for e, x in mine.items():
        y = ex.get(e) or ex_off.get(e)
        if y is None:
            out.append({"ean": e, "name": x["name"], "kind": "only_prog", "diff": []})
            continue
        diff = []
        for f, lab in COMPARE_FIELDS:
            if f == "off":
                a, b = x["status"] in ("off", "replaced"), e in ex_off
                if x["status"] == "replaced" and e not in ex:
                    b = True
            elif f == "cats":
                a, b = " › ".join(c for c in x["cats"] if c), " › ".join(re.sub(r"\s+", " ", c).strip()
                                                                         for c in (y.get("cats") or []) if c)
            elif f == "pack":
                a, b = x.get("pack"), y.get("pack")
            else:
                a, b = (x.get(f) or ""), (y.get(f) or "")
            if (a or None) != (b or None) and str(a).upper() != str(b).upper():
                diff.append((lab, a, b))
        if diff:
            out.append({"ean": e, "name": x["name"], "kind": "changed", "diff": diff})
    for e, y in list(ex.items()) + list(ex_off.items()):
        if e not in mine:
            out.append({"ean": e, "name": y.get("name") or "", "kind": "only_excel", "diff": []})
    return out
