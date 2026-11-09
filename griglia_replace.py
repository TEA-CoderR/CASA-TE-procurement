"""
Griglia「都不做了」的商品在这周批发商报价里找替代品（2026-11-09 用户：很多不做了是换了规格，40 洗 → 50 洗、
950 ml → 1000 ml，帮我从新报价里找出来核对，再加进 Griglia）。不依赖界面。

  suggest(grid, quotes, fresh=None, skip=()) → [{"old": Griglia 那一行, "cands": [候选, …（最多 TOP 个，好的在前）]}]
  候选 = {"ean", "name", "score"(0–1), "size_from", "size_to", "n"(几家在做), "best", "best_sup", "prices", "pack",
          "fresh"(新上架), "why"(短说明)}

怎么算像：名字里的词（品牌、香型 / 款式这些少见的词分量重，idf）+ 字母三连（DELICATOeSOFF ≈ DELICATO&SOFF）
+ 条码前 7 位一样（同一个厂家）+ 规格是同一种单位（ml / lav / pz…）且相差不太大；第一个词（品牌）对不上的大打折扣。
规格不同不扣分——就是要找换了规格的。候选只从这周报价里、Griglia 里还没有的商品里找。
"""
import json
import math
import os
import re
from collections import Counter

TOP = 5
MIN_SCORE = 0.35
SURE = 0.8                 # 这个分以上默认勾上（还是要人核对）

UNITS = {"ML": "ml", "LT": "l", "LITRI": "l", "L": "l", "CL": "cl", "GR": "g", "G": "g", "KG": "kg",
         "LAVAGGI": "lav", "LAVAG": "lav", "LAV": "lav", "PZ": "pz", "PEZZI": "pz", "FOGLI": "fogli",
         "FOGXXL": "fogli", "FOG": "fogli", "PANNI": "panni", "TABS": "tab", "TAB": "tab", "CAPS": "caps",
         "BUSTE": "bus", "BUS": "bus", "MT": "m", "MTR": "m", "ROTOLI": "rot", "ROT": "rot", "SALV": "pz"}
_U = "|".join(sorted(UNITS, key=len, reverse=True))
SIZE = re.compile(r"(?<![A-Z0-9])(\d+(?:\.\d+)?)\s*(" + _U + r")(?!(?!X\d)[A-Z])")         # 1100 MLx2 也认
SIZE2 = re.compile(r"(?<![A-Z0-9])(ML|LT|GR|KG|PZ)\.?\s*(\d+(?:\.\d+)?)(?![0-9])")
TO_BASE = {"l": ("ml", 1000), "cl": ("ml", 10), "kg": ("g", 1000)}
STOP = {"X", "DA", "DI", "CON", "E", "PER", "IL", "LA", "DEL", "NEW", "NUOVO", "CF", "CONF", "IN", "AL", "ALLA"}


def _norm(s):
    s = re.sub(r"([A-Z]{3,})(e|di|da|x)(?=[A-Z]{3,})", r"\1 \2 ", str(s or ""))   # DELICATOeSOFF → DELICATO e SOFF
    s = s.upper().replace("&", " ").replace("''", "'")
    return re.sub(r"(\d),(\d)", r"\1.\2", s)


def sizes(name):
    """名字里的规格：[(数, 单位)]，L / CL / KG 换成 ml / g（1,7 LT → 1700 ml）"""
    s, out = _norm(name), []
    for m in SIZE.finditer(s):
        v, u = float(m.group(1)), UNITS[m.group(2)]
        if m.group(2) == "L" and v > 10:
            u = "lav"                       # 「54L.」「50L.」是 54 / 50 洗（没有 54 升的洗衣液）
        out.append((v, u))
    for m in SIZE2.finditer(s):
        out.append((float(m.group(2)), UNITS[m.group(1)]))
    res = []
    for v, u in out:
        if u in TO_BASE:
            u, k = TO_BASE[u]
            v *= k
        res.append((v, u))
    return res


def words(name):
    s = SIZE2.sub(" ", SIZE.sub(" ", _norm(name)))
    return [w for w in re.findall(r"[A-Z]{2,}", s) if w not in STOP]


def _grams(name):
    s = "".join(words(name))
    return Counter(s[i:i + 3] for i in range(len(s) - 2))


def _cos(a, b):
    if not a or not b:
        return 0.0
    num = sum(v * b.get(k, 0) for k, v in a.items())
    return num / math.sqrt(sum(v * v for v in a.values()) * sum(v * v for v in b.values()))


def size_text(sz):
    return " ".join((f"{v / 1000:g} l" if u == "ml" and v >= 1000 else f"{v:g} {u}") for v, u in sz)


def _one(v, u):
    return (f"{v / 1000:g}", "l") if u == "ml" and v >= 1000 else (f"{v:g}", u)


_MAIN = ("lav", "ml", "g", "fogli", "panni", "tab", "caps", "bus", "rot", "m", "pz")


def change(old, new):
    """规格怎么变：(文字, 变没变)。「40 → 50 lav」「980 ml → 1.03 l」「750 ml」（没变）；名字里都没写规格 → ("", False)"""
    za, zb = sizes(old), sizes(new)
    if not za and not zb:
        return "", False
    common = {u for _v, u in za} & {u for _v, u in zb}
    u = next((x for x in _MAIN if x in common), None)
    if u is None:
        return f"{size_text(za) or '?'} → {size_text(zb) or '?'}", True
    va = max(v for v, x in za if x == u)
    vb = max(v for v, x in zb if x == u)
    (a, ua), (b, ub) = _one(va, u), _one(vb, u)
    if va == vb:
        return f"{a} {ua}", False
    return (f"{a} → {b} {ub}" if ua == ub else f"{a} {ua} → {b} {ub}"), True


# 大写一串后面跟小写（DELICATOeSOFF、MLx2、SAPOREdiALOE）在大小写交界拆开；Trig 这种首字母大写的不拆
_TOK = re.compile(r"[A-Z]{2,}(?=[a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+(?:[.,]\d+)?|[^A-Za-z\d]+")
_SAME_UNIT = {"ML": "~V", "L": "~V", "LT": "~V", "CL": "~V", "LITRI": "~V", "GR": "~W", "G": "~W", "KG": "~W"}


def _tok_key(t):
    if t[0].isdigit():
        try:
            return float(t.replace(",", "."))
        except ValueError:
            return t
    u = t.upper()
    return _SAME_UNIT.get(u, u)


def diff_tokens(name, other):
    """
    名字拆成一段段，标出另一个名字里有没有（用户 11-09：相同的一个颜色、不同的另一个颜色，一眼看出差别）。
    返回 [(文字, 0 = 一样 / 1 = 不一样 / None = 标点空格)]。
    「一样」：同一个词或数；缩写也算（DELIC = DELICATO、AMMORB. = AMMORBIDENTE，前 3 个字母以上是开头）；
    粘在一起的（DELICATOeSOFF）按大小写拆开再比；ML / L / LT 算同一种写法（数不一样才算不一样）
    """
    toks = _TOK.findall(str(name or ""))
    ot = [_tok_key(t) for t in _TOK.findall(str(other or "")) if t[0].isalnum()]
    nums = {k for k in ot if isinstance(k, float)}
    wset = {k for k in ot if isinstance(k, str)}

    def same(t):
        k = _tok_key(t)
        if isinstance(k, float):
            return k in nums
        if len(k) == 1 or k in wset:
            return True
        if len(k) >= 3 and any(len(w) >= 3 and (w.startswith(k) or k.startswith(w)) for w in wset):
            return True
        return False
    return [(t, (0 if same(t) else 1) if t[0].isalnum() else None) for t in toks]


class _Idf:
    def __init__(self, names):
        self.n = len(names) + 1
        self.df = Counter()
        for n in names:
            self.df.update(set(words(n)))

    def __call__(self, w):
        return math.log((self.n + 1) / (self.df.get(w, 0) + 1)) + 1


def score(old, new, e_old, e_new, idf):
    """(分, 规格前, 规格后)"""
    wa, wb = words(old), words(new)
    if not wa or not wb:
        return 0.0, "", ""
    sa, sb = set(wa), set(wb)
    tok = sum(idf(w) for w in sa & sb) / sum(idf(w) for w in sa | sb)
    s = 0.65 * tok + 0.35 * _cos(_grams(old), _grams(new))
    joined = "".join(wb)

    def hit(w):
        return w in sb or (len(w) >= 4 and (any(x[:5] == w[:5] for x in sb) or w in joined))
    if not any(hit(w) for w in wa[:2]):
        s *= 0.4                                   # 品牌（前两个词）对不上：多半不是同一个东西
    if e_old[:7] == e_new[:7]:
        s += 0.12                                  # 同一个厂家（条码前缀）
    elif e_old[:5] == e_new[:5]:
        s += 0.05
    za, zb = sizes(old), sizes(new)
    ua, ub = {u for _v, u in za}, {u for _v, u in zb}
    if za and zb:
        common = ua & ub
        if not common:
            s -= 0.12                              # 一个是 ml 一个是 lav：不是同一类规格
        else:
            u = sorted(common)[0]
            va = max(v for v, x in za if x == u)
            vb = max(v for v, x in zb if x == u)
            r = vb / va if va else 1
            if 0.7 <= r <= 1.45:
                s += 0.06                          # 规格差不多（40 → 50 洗、950 → 1000 ml）
            elif r > 2.2 or r < 0.45:
                s -= 0.08                          # 差太多（342 ml → 828 ml 是另一个包装）
        pa = [v for v, u in za if u == "pz"]
        pb = [v for v, u in zb if u == "pz"]
        if pa != pb and (pa or pb) and ("ml" in ua or "g" in ua):
            s -= 0.05                              # 980 ml x 3 → 1000 ml x 3 比 → 1000 ml 单瓶更像
    return max(0.0, s), size_text(za), size_text(zb)          # 可以超过 1（排先后用），界面上按 100% 封顶


def _cand(e, n, o, sc=0.0, za="", zb="", fresh=None, names=None):
    lab = min(o, key=lambda k: o[k]["price"])
    return {"ean": e, "name": n, "names": names or [n], "score": round(sc, 3), "size_from": za, "size_to": zb,
            "n": len(o), "best": o[lab]["price"], "best_sup": lab, "pack": o[lab].get("pack"),
            "prices": {k: v["price"] for k, v in o.items()},
            "fresh": bool(fresh) and all(e in fresh.get(k, ()) for k in o)}


def pool(grid, quotes):
    """这周报价里、Griglia 里没有的全部商品（手动搜替代品用）：[候选（score 0）]"""
    items = grid["items"]
    offer = {}
    for label, qs in quotes.items():
        for e, q in qs.items():
            if q.get("price"):
                offer.setdefault(e, {})[label] = q
    out = []
    for e, o in offer.items():
        if e in items:
            continue
        ns = _descs(o)
        if ns:
            out.append(_cand(e, ns[0], o, names=ns))
    return out


def _best(old, e_old, names, e_new, idf):
    """几家批发商对同一个条码写法不一样（MELONI「COCCOLINO DILUITO 50 LAV.DELICATO&SOFF.」、
    NEW GRIECO「COCCOLINO AMM. 3L 50L. DELIC. & SOFFICE」）：每种写法都算，取最像的那个（名字也用它）"""
    best = None
    for n in names:
        sc, za, zb = score(old, n, e_old, e_new, idf)
        if best is None or sc > best[0]:
            best = (sc, za, zb, n)
    return best


def rescore(old, c):
    """手动挑的候选：按旧商品算一遍像不像 / 规格变化"""
    sc, za, zb, n = _best(old.get("name") or "", old["ean"], c.get("names") or [c["name"]], c["ean"], lambda w: 1.0)
    return dict(c, name=n, score=round(sc, 3), size_from=za, size_to=zb)


def _descs(o):
    out = []
    for q in o.values():
        d = str(q.get("desc") or "").strip()
        if d and not d.startswith("=") and d not in out:
            out.append(d)
    return out


def suggest(grid, quotes, fresh=None, skip=()):
    """Griglia 里这周报价都没有的商品 → 每个最多 TOP 个替代候选（分高的在前）。skip = 已经替代过的旧条码"""
    items = grid["items"]
    offer = {}
    for label, qs in quotes.items():
        for e, q in qs.items():
            if q.get("price"):
                offer.setdefault(e, {})[label] = q
    gone = [it for e, it in items.items() if e not in offer and e not in skip]
    pool = [(e, _descs(o), o) for e, o in offer.items() if e not in items]
    pool = [(e, ns, o) for e, ns, o in pool if ns]
    idf = _Idf([n for _e, ns, _o in pool for n in ns] + [it.get("name") or "" for it in items.values()])
    # 先按词粗筛（品牌那个词要有，几家的写法都算），再细算
    by_word, by_pre = {}, {}
    for i, (e, ns, _o) in enumerate(pool):
        for w in {w for n in ns for w in words(n)}:
            by_word.setdefault(w, []).append(i)
        by_pre.setdefault(e[:7], []).append(i)
    out = []
    for it in gone:
        name = it.get("name") or ""
        ws = words(name)
        cand_ix = set()
        for w in ws[:3]:
            cand_ix.update(by_word.get(w, ()))
        cand_ix.update(by_pre.get(it["ean"][:7], ()))      # 同一个厂家的（MASTROLINDO ↔ MASTRO LINDO 这种连写）
        res = []
        for i in cand_ix:
            e, ns, o = pool[i]
            sc, za, zb, n = _best(name, it["ean"], ns, e, idf)
            if fresh and all(e in fresh.get(k, ()) for k in o):
                sc += 0.05                          # 这周新上架的：更像是换下来的那个
            if sc < MIN_SCORE:
                continue
            res.append(_cand(e, n, o, sc, za, zb, fresh, names=ns))
        res.sort(key=lambda c: (-c["score"], -c["n"]))
        out.append({"old": it, "cands": res[:TOP]})
    out.sort(key=lambda r: (-(r["cands"][0]["score"] if r["cands"] else 0), r["old"].get("name") or ""))
    return out


# ===================================================================== 已经替代过的（不再算「都不做了」）
FILE = "_替代记录.json"


def load_done(grid_dir):
    """{旧条码: {"new", "name", "at", "by"}}，放在工作文件夹「0 Griglia」里（大家共用）"""
    try:
        with open(os.path.join(grid_dir, FILE), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_done(grid_dir, done):
    p = os.path.join(grid_dir, FILE)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(done, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def sup_full_names(grid):
    """报价名称（MELONI）→ Griglia「Fornitore」列里那家的写法（MELONI WALTER SPA），按出现最多的"""
    cnt = {}
    for it in grid["items"].values():
        s = (it.get("supplier") or "").strip()
        if s:
            cnt[s] = cnt.get(s, 0) + 1
    return cnt


def full_name(label, names):
    k = re.sub(r"[^A-Z0-9]", "", str(label or "").upper())
    best = None
    for s, n in names.items():
        ks = re.sub(r"[^A-Z0-9]", "", s.upper())
        if k and (k in ks or ks in k) and (best is None or n > names[best]):
            best = s
    return best or label


# ===================================================================== 写进 Griglia（用 Excel 本身改：图片 / 格式都保留）
GREY_FONT = 0x9E9E9E               # 不做了的那一行：字变灰（Excel 颜色是 BGR，灰色一样）
NEW_FILL = 0xF7EBDD                # 新加的那一行：浅蓝底（DDEBF7，和订单跟踪写进 Excel 的标记同一个颜色）


def _key(s):
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def price_cols(grid, labels):
    """报价名称 → Griglia 里那家批发商价格列（0 开始）：MELONI → MELONI，petrillo → F.LLI PETRILLO"""
    out = {}
    for lab in labels:
        k = _key(lab)
        for h, i in (grid.get("sup_cols") or {}).items():
            kh = _key(h)
            if k and (k in kh or kh in k):
                out[lab] = i
                break
    return out


def plan_rows(grid, picks, today=None):
    """
    要写的：[(旧条码, 旧行号, {列(0 开始): 值})]，按行号从下往上（插行不影响上面的行号）。
    picks = [(旧商品, 候选)]。新行照抄旧行（分类 / 品牌 / 门店勾选 / 公式），这里只给要换掉的格子：
    条码、名称、箱规、最低价（PREZZO NETTO、PA MIGLIORE）、Fornitore（最便宜那家，Griglia 里的写法）、各批发商价格；
    编号（COD.ART.）清空——新商品在我们系统里还没有编号
    """
    col = grid.get("cols") or {}
    names = sup_full_names(grid)
    out = []
    for old, c in picks:
        vals = {}

        def put(f, v):
            if f in col:
                vals[col[f]] = v
        put("ean", c["ean"])
        put("code", None)
        put("name", c["name"])
        if c.get("pack"):
            put("pack", c["pack"])
        if c.get("best"):
            put("price", round(c["best"], 4))
            put("best", round(c["best"], 4))
        if c.get("best_sup"):
            put("supplier", full_name(c["best_sup"], names))
        pc = price_cols(grid, list(c.get("prices") or {}))
        for lab, i in pc.items():
            vals[i] = round(c["prices"][lab], 4)
        for h, i in (grid.get("sup_cols") or {}).items():          # 这家这周没报价：清掉照抄过来的旧价格
            if i not in pc.values():
                vals[i] = None
        out.append((old["ean"], old.get("row"), vals))
    out.sort(key=lambda x: -(x[1] or 0))
    return out


def write_rows(path, grid, plan, excel=None, log=None):
    """
    用 Excel 打开 Griglia，每个不做了的商品下面插一行替代品（照抄旧行再改格子），旧行字变灰、旧条码格加批注。
    excel = 测试用的假 Excel。返回 [(旧条码, 新行号)]。找不到旧行（条码对不上）的跳过，记在日志里
    """
    log = log or (lambda *_a: None)
    own = excel is None
    if own:
        import pythoncom
        import win32com.client as win32
        pythoncom.CoInitialize()
        excel = win32.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.ScreenUpdating = False
    done = []
    wb = None
    try:
        wb = excel.Workbooks.Open(os.path.abspath(path))
        ws = wb.Worksheets(grid.get("sheet")) if grid.get("sheet") else wb.ActiveSheet
        cols = grid.get("cols") or {}
        ec = cols.get("ean", 0) + 1
        last = max([c for c in cols.values()] + [i for i in (grid.get("sup_cols") or {}).values()] + [0]) + 1
        last = max(last, ws.UsedRange.Columns.Count + ws.UsedRange.Column - 1)
        from utils import clean_ean
        for old_ean, row, vals in plan:
            if not row or clean_ean(ws.Cells(row, ec).Value) != old_ean:
                hit = ws.Columns(ec).Find(What=old_ean, LookIn=-4163, LookAt=1)   # 行号变了（有人插过行）：按条码整格再找
                row = hit.Row if hit is not None else None
                if not row or clean_ean(ws.Cells(row, ec).Value) != old_ean:
                    log(f"Griglia 里找不到条码 {old_ean}，跳过")
                    continue
            # 下面插一行（格式照上面那行），内容按 R1C1 照抄（公式里的相对引用跟着下移一行）。
            # 不用 Rows.Copy：会把这一行上的商品图片也抄一份（Griglia 几千张图，再一张张删很慢）
            ws.Rows(row + 1).Insert(Shift=-4121, CopyOrigin=0)
            src, dst = ws.Range(ws.Cells(row, 1), ws.Cells(row, last)), ws.Range(ws.Cells(row + 1, 1), ws.Cells(row + 1, last))
            dst.FormulaR1C1 = src.FormulaR1C1
            try:
                ws.Rows(row + 1).RowHeight = ws.Rows(row).RowHeight
            except Exception:
                pass
            for c0, v in vals.items():
                cell = ws.Cells(row + 1, c0 + 1)
                if v is not None and cell.HasFormula and c0 != cols.get("ean"):
                    continue                                          # 公式格（PREZZO NETTO = …）照抄的公式会自己算
                if c0 == cols.get("ean"):
                    cell.NumberFormat = "@"                           # 条码当文字（不然变成 8.72E+12）
                    cell.Value = str(v)
                else:
                    cell.Value = v
            ws.Range(ws.Cells(row + 1, 1), ws.Cells(row + 1, last)).Interior.Color = NEW_FILL
            ws.Range(ws.Cells(row, 1), ws.Cells(row, last)).Font.Color = GREY_FONT
            try:
                c = ws.Cells(row, ec)
                if c.Comment is not None:
                    c.Comment.Delete()
                c.AddComment(f"Non più disponibile dai grossisti → sostituito da {vals.get(cols.get('ean'))}")
            except Exception:
                pass
            done.append((old_ean, row + 1))
        wb.Save()
        return done
    finally:
        if wb is not None:
            try:
                wb.Close(SaveChanges=False)
            except Exception:
                pass
        if own:
            try:
                excel.Quit()
            except Exception:
                pass
