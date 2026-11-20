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
from functools import lru_cache
from collections import Counter

TOP = 5
PRE_TOP = 120              # 每个原商品最多细算这么多个候选（先按共同的词粗排）
MIN_SCORE = 0.35
SURE = 0.8                 # 这个分以上默认勾上（还是要人核对）

UNITS = {"ML": "ml", "LT": "l", "LITRI": "l", "L": "l", "CL": "cl", "GR": "g", "G": "g", "KG": "kg",
         "LAVAGGI": "lav", "LAVAG": "lav", "LAV": "lav", "PZ": "pz", "PEZZI": "pz", "FOGLI": "fogli",
         "FOGXXL": "fogli", "FOG": "fogli", "PANNI": "panni", "TABS": "tab", "TAB": "tab", "CAPS": "caps",
         "BUSTE": "bus", "BUS": "bus", "MT": "m", "MTR": "m", "ROTOLI": "rot", "ROT": "rot", "SALV": "pz",
         # 洗衣粉的「量勺」= 能洗几次（用户 11-20：40 MISURINI 和 130 MISURINI 是两种包装）
         "MISURINI": "lav", "MISURIN": "lav", "MISUR": "lav", "MIS": "lav", "DOSI": "lav", "DOSE": "lav", "SCOOP": "lav",
         "PAN": "panni"}
_U = "|".join(sorted(UNITS, key=len, reverse=True))
SIZE = re.compile(r"(?<![A-WYZ0-9])(\d+(?:\.\d+)?)\s*(" + _U + r")(?!(?!X\d)[A-Z])")      # 1100 MLx2、28 LAVx3 PZ 也认
SIZE2 = re.compile(r"(?<![A-Z0-9])(ML|LT|GR|KG|PZ)\.?\s*(\d+(?:\.\d+)?)(?![0-9])")
TO_BASE = {"l": ("ml", 1000), "cl": ("ml", 10), "kg": ("g", 1000)}
STOP = {"X", "DA", "DI", "CON", "E", "PER", "IL", "LA", "DEL", "NEW", "NUOVO", "CF", "CONF", "IN", "AL", "ALLA"}


def _fp(m):
    """防晒指数 SPF30 / PF30 / FP 30 → 一个词 FPDA（数字换成字母，这样能按词比：FP30 ≠ FP50）"""
    return " FP" + "".join("ABCDEFGHIJ"[int(c)] for c in m.group(1)) + " "


def _norm(s):
    s = re.sub(r"([A-Z]{3,})(e|di|da|x)(?=[A-Z]{3,})", r"\1 \2 ", str(s or ""))   # DELICATOeSOFF → DELICATO e SOFF
    s = s.upper().replace("&", " ").replace("''", "'")
    s = re.sub(r"(\d),(\d)", r"\1.\2", s)
    s = re.sub(r"(?<![A-Z])(?:SPF|PF|FP)\s*\.?\s*(\d+)\+?", _fp, s)
    # 「52+8L.」「12+4PZ」= 送的也算：60 洗 / 16 件
    return re.sub(r"(?<![\d.])(\d+)\s*\+\s*(\d+)\s*(?=(L|LAV|PZ)\b|(L|LAV|PZ)[.\s])",
                  lambda m: str(int(m.group(1)) + int(m.group(2))) + " ", s)


def sizes(name):
    """名字里的规格：[(数, 单位)]，L / CL / KG 换成 ml / g（1,7 LT → 1700 ml）"""
    return list(_sizes(str(name or "")))


@lru_cache(maxsize=200000)
def _sizes(name):
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
    return tuple(res)


def words(name):
    return list(_words(str(name or "")))


# 同一个名字要算几千次（每个原商品 × 候选都算一遍）：记住结果（用户 11-11：点「找替代商品」卡一下才出进度条）
@lru_cache(maxsize=200000)
def _words(name):
    # 规格（数 + 单位）不算词；只有「量勺」留一个词 MISURINI（洗衣粉 ≠ 洗衣液，这个词本身有意思）
    s = SIZE.sub(lambda m: " MISURINI " if m.group(2).startswith("MIS") else " ", _norm(name))
    s = SIZE2.sub(" ", s)
    return tuple(w for w in re.findall(r"[A-Z]{2,}", s) if w not in STOP)


@lru_cache(maxsize=200000)
def _grams(name):
    """（只读，别改返回的 Counter）"""
    s = "".join(_words(str(name or "")))
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
            self.df.update(set(_words(str(n or ""))))

    def __call__(self, w):
        return math.log((self.n + 1) / (self.df.get(w, 0) + 1)) + 1


# ---------------------------------------------------------------- 相似度（2026-11-20 第二版）
# 用户 11-20：「LAV. DIXAN CLASSICO 40 MISURINI」应该配 40 MISURINI，程序却把 130 MISURINI 排第一 → 重写：
#   ① 名字：按词比（少见的词分量重），缩写算对上（DETER. = DETERSIVO），连写算对上（MASTRO LINDO = MASTROLINDO）
#   ② 规格：同一种规格（洗几次 / ml / g / 张…）一样多加分最多，差得越多扣得越多；一个是 3 瓶装一个是单瓶要扣
#   ③ 没写单位的数（SVELTO 1030、ESSENZA 750）和另一边的规格对上也算
#   ④ 同一个厂家（条码前缀）加分；这周新上架只当一点点加分（不再盖过规格）

@lru_cache(maxsize=400000)
def _wsim(a, b):
    """两个词像不像：一样 1；一个是另一个的开头（缩写，DETER = DETERSIVO）0.85，三个字母的 0.6"""
    if a == b:
        return 1.0
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 3 and long_.startswith(short):
        return 0.85 if len(short) >= 4 else 0.6
    return 0.0


def _glue(wa, wb):
    """MASTRO LINDO ↔ MASTROLINDO：一边挨着的两个词连起来正好是另一边的一个词 → 当成一个词"""
    sb = set(wb)
    out, i = [], 0
    while i < len(wa):
        if i + 1 < len(wa) and wa[i] + wa[i + 1] in sb:
            out.append(wa[i] + wa[i + 1])
            i += 2
        else:
            out.append(wa[i])
            i += 1
    return out


def _side(ws, other, idf):
    tot = got = 0.0
    for w in ws:
        k = idf(w)
        tot += k
        got += k * max((_wsim(w, o) for o in other), default=0.0)
    return got, tot


def _text(wa, wb, idf):
    wa = _glue(list(wa), wb)
    wb = _glue(list(wb), wa)
    g1, t1 = _side(wa, wb, idf)
    g2, t2 = _side(wb, wa, idf)
    return (g1 + g2) / (t1 + t2) if t1 + t2 else 0.0


_DIMS = ("lav", "ml", "g", "fogli", "panni", "tab", "caps", "bus", "rot", "m")
_COUNTS = ("panni", "fogli", "tab", "caps", "bus", "rot", "pz")
_BARE = re.compile(r"(?<![0-9.,])(\d{2,4})(?![0-9]|[.,]\d)")


@lru_cache(maxsize=200000)
def _bare(name):
    """名字里没写单位的数（规格已经认出来的不算）"""
    s = SIZE2.sub(" ", SIZE.sub(" ", _norm(name)))
    return frozenset(float(x) for x in _BARE.findall(s))


def _ratio_adj(va, vb):
    r = min(va, vb) / max(va, vb) if va and vb else 0.0
    if r >= 0.99:
        return 0.15                     # 一模一样（40 → 40 MISURINI）
    if r >= 0.8:
        return 0.08                     # 差一点（40 → 45 洗、950 → 1000 ml）
    if r >= 0.6:
        return 0.02
    if r >= 0.45:
        return -0.04
    return -0.12                        # 差太多（40 → 130 MISURINI、342 → 828 ml）：另一个包装


def size_adj(old, new):
    """规格对不对得上：加减分（-0.25 … +0.25）"""
    za, zb = sizes(old), sizes(new)
    da = {}
    db = {}
    for v, u in za:
        da.setdefault(u, v)
    for v, u in zb:
        db.setdefault(u, v)
    adj = 0.0
    main_a = [u for u in _DIMS if u in da]
    main_b = [u for u in _DIMS if u in db]
    common = [u for u in main_a if u in db]
    if common:
        adj += sum(_ratio_adj(da[u], db[u]) for u in common) / max(1, len(common)) \
            + (0.03 if len(common) > 1 and all(_ratio_adj(da[u], db[u]) > 0 for u in common) else 0)
    elif main_a and main_b:
        adj -= 0.10                     # 一个按 ml 一个按洗几次：不是同一类规格
    else:
        # 张数 / 片数写法不一样：22 PANNI ↔ PZ.20
        ca = next((da[u] for u in _COUNTS if u in da), None)
        cb = next((db[u] for u in _COUNTS if u in db), None)
        if ca and cb:
            adj += _ratio_adj(ca, cb) / 2
        elif main_a:
            adj -= 0.04                 # 旧的写了规格，候选没写可比的规格（多半是别的东西：手套、配件）
    # 几瓶装：980 ml x 3 → 1000 ml x 3 比单瓶 1000 ml 像得多（PZ.1 = 单个）
    pa, pb = da.get("pz"), db.get("pz")
    pa, pb = (None if pa == 1 else pa), (None if pb == 1 else pb)
    if main_a and main_b:
        if pa and pb:
            adj += 0.06 if pa == pb else -0.06
        elif pa or pb:
            adj -= 0.12
    # 没写单位的数（SVELTO PIATTI 1030 / ESSENZA 750）：和另一边的数对上加一点
    na, nb = _bare(str(old or "")), _bare(str(new or ""))
    va = {v for v, _u in za}
    vb = {v for v, _u in zb}
    if (na & (vb | nb)) or (nb & va):
        adj += 0.06
    return max(-0.25, min(0.25, adj))


def score(old, new, e_old, e_new, idf):
    """(分, 规格前, 规格后)"""
    wa, wb = _words(str(old or "")), _words(str(new or ""))
    if not wa or not wb:
        return 0.0, "", ""
    s = 0.7 * _text(wa, wb, idf) + 0.3 * _cos(_grams(old), _grams(new))
    gb = set(_glue(list(wb), wa))
    jb = "".join(wb)
    if not any(w in gb or (len(w) >= 5 and (any(x[:5] == w[:5] for x in gb) or w in jb))
               for w in _glue(list(wa), wb)[:2]):
        s *= 0.4                                   # 品牌（前两个词）对不上：多半不是同一个东西
    if e_old[:7] == e_new[:7]:
        s += 0.06                                  # 同一个厂家（条码前缀；厂家换了前缀也常见，只加一点）
    elif e_old[:5] == e_new[:5]:
        s += 0.03
    s += size_adj(old, new)
    return max(0.0, s), size_text(sizes(old)), size_text(sizes(new))          # 可以超过 1（排先后用），界面上按 100% 封顶


def _cand(e, n, o, sc=0.0, za="", zb="", fresh=None, names=None):
    lab = min(o, key=lambda k: o[k]["price"])
    return {"ean": e, "name": n, "names": names or [n], "score": round(sc, 3), "size_from": za, "size_to": zb,
            "n": len(o), "best": o[lab]["price"], "best_sup": lab, "pack": o[lab].get("pack"),
            "prices": {k: v["price"] for k, v in o.items()},
            "fresh": bool(fresh) and all(e in fresh.get(k, ()) for k in o)}


def pool(grid, quotes):
    """
    这周报价里的全部商品（手动搜替代品用）：[候选（score 0）]。
    Griglia 里已经有的也在（用户 11-09：搜 FINISH GEL 只出来 3 个，另外 4 个是 Griglia 里已经有的被藏了），标 in_grid：
    选它 = 新商品已经在 Griglia 里了，只把旧的那行移到最下面、划线
    """
    items = grid["items"]
    offer = {}
    for label, qs in quotes.items():
        for e, q in qs.items():
            if q.get("price"):
                offer.setdefault(e, {})[label] = q
    out = []
    for e, o in offer.items():
        ns = _descs(o)
        if ns:
            c = _cand(e, ns[0], o, names=ns)
            if e in items:
                c["in_grid"] = True
            out.append(c)
    return out


def _flat(s):
    return re.sub(r"[^A-Z0-9]", "", s)


def brand_of(old):
    """
    搜索时锁定的品牌（用户 11-20：搜候选想先按品牌筛）：Griglia 的 Marca 在名称里出现就用它，
    不然用名称第一个词（ANTICA ERB.SH… → ANTICA）。返回 (显示的字, 比较用的字) 或 None
    """
    name = _flat(_norm((old or {}).get("name") or ""))
    b = str((old or {}).get("brand") or "").strip()
    if len(_flat(_norm(b))) >= 3 and _flat(_norm(b)) in name:
        return b.upper(), _flat(_norm(b))
    for w in re.split(r"[^A-Z0-9]+", _norm((old or {}).get("name") or "")):
        if len(w) >= 3 and not w.isdigit():
            return w, w
    return None


def search(pool_, q, must=()):
    """
    手动搜（用户 11-09：要模糊一点）：条码开头对上，或者输入的每个词都在某家的写法里出现
    （不分大小写、标点；粘在一起的也算：「540ml」= 「540 ML」，「ULTIM」= ULTIMATE）。
    「-词」= 不要带这个词的（-RICARICA）；must = 一定要有的词（锁定的品牌）
    """
    q = str(q or "").strip()
    if not q and not must:
        return []
    parts = [t for t in re.split(r"[\s,;/]+", q) if t]
    toks = [_flat(_norm(t)) for t in parts if not t.startswith("-")]
    toks = [t for t in toks if t] + [t for t in must if t]
    nots = [_flat(_norm(t[1:])) for t in parts if t.startswith("-")]
    nots = [t for t in nots if t]
    out = []
    for c in pool_:
        if q.isdigit() and len(q) >= 6:          # 条码（短的数字当规格：14、130）
            if c["ean"].startswith(q):
                out.append(c)
            continue
        hay = " ".join(_norm(n) for n in (c.get("names") or [c["name"]]))
        flat = _flat(hay)
        if nots:                                  # 不要的词按词开头比（-50 不排除 750 ML）
            ws = [w for w in re.split(r"[^A-Z0-9]+", hay) if w]
            if any(w.startswith(t) for t in nots for w in ws):
                continue
        if toks and all(t in flat for t in toks):
            out.append(c)
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


def suggest(grid, quotes, fresh=None, skip=(), struck=False):
    """
    Griglia 里这周报价都没有的商品 → 每个最多 TOP 个替代候选（分高的在前）。skip = 已经替代过的旧条码。
    struck=True（用户 11-11）：给 Griglia 里划了线的商品找，旧商品带 "struck": True。
    2026-11-11 第二次（用户：供应商有货的为什么也进来？分两种增加歧义）：划线的也只要**停售**的（几家批发商都不卖了）；
    还有人卖的是你们自己决定不订，不用找替代
    """
    items = grid["items"]
    struck_items = grid.get("struck") or {}
    offer = {}
    for label, qs in quotes.items():
        for e, q in qs.items():
            if q.get("price"):
                offer.setdefault(e, {})[label] = q
    gone = []
    if struck is not True:                  # False = 只要批发商都没货的；"both" = 两种一起（只建一次索引）
        gone += [it for e, it in items.items() if e not in offer and e not in skip]
    if struck:
        gone += [dict(it, struck=True) for e, it in struck_items.items() if e not in skip and e not in offer]
    # Griglia 里已经有的也算候选（用户 11-20：SVELTO 980 ML x 3 → 1000 ML x 3、COCCOLINO DILUITO 40 → 50 LAV 已经在 Griglia 里，
    # 选它 = 旧的那行只移到最下面划线）；Griglia 已停用（划线）的不算
    pool = [(e, _descs(o), o) for e, o in offer.items() if e not in struck_items]
    pool = [(e, ns, o) for e, ns, o in pool if ns]
    idf = _Idf([n for _e, ns, _o in pool for n in ns] + [it.get("name") or "" for it in items.values()])
    # 先按词粗筛（品牌那个词要有，几家的写法都算），再细算
    by_word, by_pre, wsets, psets = {}, {}, [], []
    for i, (e, ns, _o) in enumerate(pool):
        ws_ = {w for n in ns for w in _words(str(n or ""))}
        wsets.append(ws_)
        psets.append({w[:4] for w in ws_ if len(w) >= 4})
        for w in ws_:
            by_word.setdefault(w, []).append(i)
        by_pre.setdefault(e[:7], []).append(i)
    out = []
    for it in gone:
        name = it.get("name") or ""
        ws = words(name)
        cand_ix = set()
        for w in ws[:3]:
            cand_ix.update(by_word.get(w, ()))
        for a, b in zip(ws[:3], ws[1:4]):                   # MASTRO LINDO → 也找写成 MASTROLINDO 的
            cand_ix.update(by_word.get(a + b, ()))
        cand_ix.update(by_pre.get(it["ean"][:7], ()))      # 同一个厂家的（MASTROLINDO ↔ MASTRO LINDO 这种连写）
        if len(cand_ix) > PRE_TOP:          # 太多：先按共同的词（少见的词分量重）粗排，只细算前 PRE_TOP 个
            own = set(_glue(list(ws), ()))
            own |= {a + b for a, b in zip(ws, ws[1:])}
            brand = set(ws[:2]) | {a + b for a, b in zip(ws[:2], ws[1:3])}       # 品牌对上的一定细算

            own4 = {w[:4]: w for w in own if len(w) >= 4}

            def rough(i):           # 词一样，或者前 4 个字母一样（BAGNODOCCIA ↔ BAGNO、DETER ↔ DETERSIVO）
                return (bool(brand & wsets[i]), sum(idf(w) for w in own & wsets[i])
                        + sum(0.8 * idf(w) for p4, w in own4.items() if p4 in psets[i] and w not in wsets[i]))
            cand_ix = sorted(cand_ix, key=rough, reverse=True)[:PRE_TOP]
        res = []
        for i in cand_ix:
            e, ns, o = pool[i]
            if e == it["ean"]:
                continue
            sc, za, zb, n = _best(name, it["ean"], ns, e, idf)
            if fresh and all(e in fresh.get(k, ()) for k in o):
                sc += 0.03                          # 这周新上架的：更像是换下来的那个（只当一点点加分，不盖过规格）
            if sc < MIN_SCORE:
                continue
            c = _cand(e, n, o, sc, za, zb, fresh, names=ns)
            if e in items:
                c["in_grid"] = True
            res.append(c)
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


def done_eans(done, grid):
    """替代过、而且新商品已经在这份 Griglia 里的旧条码（换了一份没加过的 Griglia，旧的又会算「都不做了」）"""
    items = (grid or {}).get("items") or {}
    return {old for old, r in (done or {}).items() if (r or {}).get("new") in items}


def substitutes(done, grid):
    """
    门店清单里的旧条码 → 换成哪个新商品（2026-11-11 用户：订 DETERSIVI 之前先找好替代商品，门店清单里还有停售的旧条码，
    就自动换成替代商品去采购，门店报告里写明）。{旧条码: {"ean", "name", "code"}}；
    A → B、B → C 换过两次的跟到最后一个；最后那个新商品要在现在这份 Griglia 里才算（没替换进这份 Griglia 的不换）
    """
    items = (grid or {}).get("items") or {}
    out = {}
    for old in done or {}:
        cur, seen = old, {old}
        while (done.get(cur) or {}).get("new") and done[cur]["new"] not in seen:
            cur = done[cur]["new"]
            seen.add(cur)
        if cur != old and cur in items:
            it = items[cur]
            out[old] = {"ean": cur, "name": it.get("name") or (done.get(old) or {}).get("name") or "",
                        "code": str(it.get("code") or "").strip()}
    return out


def apply_substitutes(rows, subs, clean=None):
    """
    检查清单的行：条码是停售的旧商品 → 换成新商品（条码 / 名称），原来的记在 x["repl"]（old_ean / old_name / old_code / new_code）。
    销量、库存、建议箱数照旧用旧商品的（同一个商品换了规格，卖得一样）；箱规、件价后面按新条码的报价补（apply_quotes）。
    返回换了几行
    """
    clean = clean or (lambda v: str(v or "").strip())
    n = 0
    for x in rows:
        if x.get("repl"):
            continue
        e = clean(x.get("ean"))
        s = subs.get(e) if e else None
        if not s:
            continue
        x["repl"] = {"old_ean": e, "old_name": x.get("name") or "", "old_code": str(x.get("code") or ""),
                     "new_code": s.get("code") or ""}
        x["ean"], x["name"] = s["ean"], s.get("name") or x.get("name")
        x["in_grid"] = True
        n += 1
    return n


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
    要写的：[(旧条码, 旧行号, {列(0 开始): 值}, 怎么写)]，按行号从下往上（删行不影响上面的行号）。
    怎么写：move = 原地换成新商品、旧行抄到最下面划线（Griglia 已停用的商品也这样，用户 11-18）；
    inplace = 老的写法（划线那一行直接改成新商品，旧商品不留），现在不再生成，write_rows 还认；
    noop = 划线的商品、新商品 Griglia 里已经有：Excel 不用改，只记替换记录
    picks = [(旧商品, 候选)]。旧行原地换成新商品（分类 / 品牌 / 门店勾选 / 公式照旧），这里只给要换掉的格子：
    条码、名称、箱规、最低价（PREZZO NETTO、PA MIGLIORE）、Fornitore（最便宜那家，Griglia 里的写法）、各批发商价格；
    编号（COD.ART.）清空——新商品在我们系统里还没有编号
    """
    col = grid.get("cols") or {}
    names = sup_full_names(grid)
    out = []
    for old, c in picks:
        vals = {}
        if c.get("in_grid"):                        # 新商品 Griglia 里已经有：旧行只移到最下面划线
            out.append((old["ean"], old.get("row"), vals, "noop" if old.get("struck") else "move"))
            continue

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
        # 用户 11-18：Griglia 已停用的商品也一样——这一行换成新商品，旧商品抄到最下面划线（以前原地改、旧商品就没了）
        out.append((old["ean"], old.get("row"), vals, "move"))
    out.sort(key=lambda x: -(x[1] or 0))
    return out


def _write_new(ws, row, vals, cols, img_col, new_ean, url, row_range, ec, replaced):
    """这一行写成新商品：改过的格子浅蓝、图片格 =IMAGE、去掉删除线；replaced = 原来是划线的哪个旧条码（写进批注）"""
    for c0, v in vals.items():
        cell = ws.Cells(row, c0 + 1)
        if v is not None and cell.HasFormula and c0 != cols.get("ean"):
            continue                                          # 公式格（PREZZO NETTO = …）会自己算
        if c0 == cols.get("ean"):
            cell.NumberFormat = "@"                           # 条码当文字（不然变成 8.72E+12）
            cell.Value = str(v)
        else:
            cell.Value = v
        try:
            cell.Interior.Color = NEW_FILL                    # 改过的格子浅蓝
        except Exception:
            pass
    if img_col and new_ean:
        cell = ws.Cells(row, img_col)
        f = image_formula(new_ean, url)
        try:
            cell.Formula2 = f                                 # Excel 365（动态数组公式）
        except Exception:
            cell.Formula = f
    try:
        row_range(row).Font.Strikethrough = False
    except Exception:
        pass
    try:
        c = ws.Cells(row, ec)
        if c.Comment is not None:
            c.Comment.Delete()
        if replaced:
            c.AddComment(f"Sostituisce {replaced} (non più disponibile)")
    except Exception:
        pass


IMG_HEADS = ("IMMAGINE", "IMMAGINI", "FOTO", "IMAGE", "IMG", "图片")


def _set_formula(cell, f):
    try:
        cell.Formula2 = f
    except Exception:
        cell.Formula = f


class _Pictures:
    """图片列里浮着的商品图片（Shapes）：第一次要用时扫一遍，记下每张在哪一行"""

    def __init__(self, ws, col):
        self.ws, self.col, self.rows = ws, col, None

    def _scan(self):
        if self.rows is None:
            self.rows = {}
            try:
                shapes = self.ws.Shapes
                for i in range(1, int(shapes.Count) + 1):
                    sh = shapes.Item(i)
                    try:
                        tl = sh.TopLeftCell
                        if int(tl.Column) == self.col:
                            self.rows.setdefault(int(tl.Row), []).append(sh)
                    except Exception:
                        continue
            except Exception:
                pass
        return self.rows

    def move(self, src, dst):
        """src 行的图片挪到 dst 行（位置照原来在格子里的位置）；挪到了返回 True"""
        shs = self._scan().pop(src, [])
        if not shs:
            return False
        try:
            top_src, top_dst = self.ws.Cells(src, self.col).Top, self.ws.Cells(dst, self.col).Top
            for sh in shs:
                sh.Top = top_dst + (sh.Top - top_src)
            self.rows.setdefault(dst, []).extend(shs)
            return True
        except Exception:
            return False

    def remove(self, row):
        for sh in self._scan().pop(row, []):
            try:
                sh.Delete()
            except Exception:
                pass


def image_formula(ean, url=None):
    """=IMAGE(网址)：图片不放进文件，Excel 365 打开时自己读（和导出的报表一样）"""
    import product_images as PI
    u = (url or PI.DEFAULT_URL).format(ean=ean)
    return f'=IFERROR(_xlfn.IMAGE("{u}","",3,48,48),"")'


def _is_img_head(v):
    t = re.sub(r"\s+", " ", str(v or "")).strip().upper()
    return bool(t) and any(t == h or t.startswith(h) for h in IMG_HEADS)


def write_rows(path, grid, plan, excel=None, log=None, url=None, ops=None):
    """
    用 Excel 打开 Griglia（用户 11-09：替换原来的产品行，原来的行移到文件最底部、用线划掉，和 Griglia 里现在的做法一样）：
      1. 旧行照抄一份到最下面（EAN 列最后一个有内容的行下面）：格式 PasteSpecial 只贴格式（不抄商品图片），内容按 R1C1 照抄，
         整行划线、字变灰，旧条码格加批注「Non più disponibile dai grossisti → sostituito da …」；
      2. 原来那一行原地改成新商品：条码 / 名称 / 箱规 / 价格 / 供应商（改过的格子浅蓝底），编号清空，去掉划线，图片格写新条码的 =IMAGE；
         分类 / 品牌 / 门店勾选 / 公式照旧；
      3. 新商品 Griglia 里已经有（vals 空）：原来那一行删掉（已经抄到最下面了）。
    excel = 测试用的假 Excel。返回 [(旧条码, 新商品所在行号 或 None, 旧行挪到的行号)]。
    找不到旧行（条码对不上）的跳过，记在日志里。
    ops（列表）：按顺序记下改了哪几行 ("write" / "append" / "delete", 行号)，`diff_cells` 用它核对别的格子都没动
    """
    ops = ops if ops is not None else []
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
        hr = grid.get("header_row")
        if hr:                                       # 只管有表头的那些列（UsedRange 可能是整张表 16384 列）
            head_vals = _values(ws, hr, 1, hr, min(ws.UsedRange.Columns.Count + ws.UsedRange.Column - 1, 1000))[0]
            last = max([last] + [i for i, x in enumerate(head_vals, 1) if x not in (None, "")])
        else:
            last = max(last, ws.UsedRange.Columns.Count + ws.UsedRange.Column - 1)
        img_col = next((c for c in range(1, min(last, 40) + 1) if hr and _is_img_head(ws.Cells(hr, c).Value)), None)
        pics = _Pictures(ws, img_col)
        from utils import clean_ean

        def row_range(r):
            return ws.Range(ws.Cells(r, 1), ws.Cells(r, last))

        # 用户 11-11：用 WPS 时 Find(SearchDirection=往上找) 不管方向，找到的是第 2 行 → 旧行被抄到了表头第 3 行。
        # 现在不用 Find：整列一次读出来，在程序里找最后一行 / 找条码；最下面一定在 Griglia 已知的所有商品行下面
        known = max([(it.get("row") or 0) for it in grid.get("items", {}).values()] +
                    [(it.get("row") or 0) for it in (grid.get("struck") or {}).values()] + [hr or 0])

        def used_end():
            try:
                u = ws.UsedRange
                return int(u.Row) + int(u.Rows.Count) - 1
            except Exception:
                return 0

        def ean_col():
            n = max(used_end(), known) + 5
            return n, [row[0] for row in _values(ws, 1, ec, n, ec)]

        def bottom():
            _n, col = ean_col()
            filled = [i for i, x in enumerate(col, 1) if x not in (None, "")]
            return max(filled[-1] if filled else 0, known) + 1

        def find_row(ean):
            _n, col = ean_col()
            return next((i for i, x in enumerate(col, 1) if i > (hr or 0) and clean_ean(x) == ean), None)

        head_n = max(hr or 0, 1)
        head_w = min(last, 200)
        def head():
            # 表头（订单号 / 日期 / 合计…）：文字照比，公式只看还是不是公式（合计的范围会跟着删行变）
            return [["=" if isinstance(x, str) and x.startswith("=") else x for x in r]
                    for r in _values(ws, 1, 1, head_n, head_w, "Formula")]
        head_before = head()

        for item in plan:
            old_ean, row, vals = item[:3]
            mode = item[3] if len(item) > 3 else "move"
            if mode == "noop":
                done.append((old_ean, None, row))
                continue
            if not row or row <= (hr or 0) or clean_ean(ws.Cells(row, ec).Value) != old_ean:
                row = find_row(old_ean)                              # 行号变了（有人插过行）：按条码再找
                if not row or clean_ean(ws.Cells(row, ec).Value) != old_ean:
                    log(f"Griglia 里找不到条码 {old_ean}，跳过")
                    continue
            new_ean = vals.get(cols.get("ean"))
            if mode == "inplace":
                if img_col:
                    pics.remove(row)                  # 旧商品的图片（浮在上面）拿掉，新商品用 IMAGE 公式显示
                _write_new(ws, row, vals, cols, img_col, new_ean, url, row_range, ec, old_ean)
                ops.append(("write", row))
                try:
                    f = row_range(row).Font
                    if f.Color == GREY_FONT:
                        f.ColorIndex = -4105                          # 程序挪下去时变的灰字：改回自动颜色
                except Exception:
                    pass
                done.append((old_ean, row, None))
                continue
            # 1. 旧行抄到最下面、划线
            dst = bottom()
            if dst <= max(hr or 0, row):
                raise RuntimeError(f"找不到 Griglia 最下面一行（算出来第 {dst} 行），没有修改")
            ops.append(("append", dst))
            row_range(row).Copy()
            row_range(dst).PasteSpecial(Paste=-4122)                    # 只贴格式（不抄商品图片）
            try:
                excel.CutCopyMode = False
            except Exception:
                pass
            row_range(dst).FormulaR1C1 = row_range(row).FormulaR1C1
            try:
                ws.Rows(dst).RowHeight = ws.Rows(row).RowHeight
            except Exception:
                pass
            # 商品图片是浮在格子上的图片，不跟着抄：把原来那一行的图片挪到最下面那一行；
            # 原来那一行没有图片（或挪不了）→ 最下面那一行的图片列写 IMAGE 公式（用户 11-11：划线的旧商品也要有图片）
            if img_col and not pics.move(row, dst):
                _set_formula(ws.Cells(dst, img_col), image_formula(old_ean, url))
            moved = row_range(dst)
            moved.Font.Strikethrough = True
            moved.Font.Color = GREY_FONT
            try:
                c = ws.Cells(dst, ec)
                if c.Comment is not None:
                    c.Comment.Delete()
                c.AddComment("Non più disponibile dai grossisti" + (f" → sostituito da {new_ean}" if new_ean else ""))
            except Exception:
                pass
            if not vals:
                # 3. 新商品已经在 Griglia 里：原来那一行删掉
                ws.Rows(row).Delete()
                ops.append(("delete", row))
                done.append((old_ean, None, dst - 1 if dst > row else dst))
                continue
            # 2. 原来那一行原地换成新商品
            _write_new(ws, row, vals, cols, img_col, new_ean, url, row_range, ec, None)
            try:
                f = row_range(row).Font
                if f.Color == GREY_FONT:
                    f.ColorIndex = -4105                          # 原来是 Griglia 已停用的灰字：改回自动颜色
            except Exception:
                pass
            ops.append(("write", row))
            done.append((old_ean, row, dst))
        if head() != head_before:           # 表头被改了：一个字都不存
            raise RuntimeError("改动碰到了 Griglia 的表头（前几行），已放弃，文件没有保存")
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


# ===================================================================== 把原商品从备份抄回最下面（用户 11-18）
def find_in_backups(backups, grid, eans, excel=None, log=None):
    """
    在备份里找原商品那一行：backups = 备份文件路径（新的在前），eans = 要找的旧条码。
    返回 {条码: (备份路径, 行号)}（每个条码用最新一份里有的）。只读，不改任何文件。
    """
    from utils import clean_ean
    found = {}
    want = set(eans)
    cols = grid.get("cols") or {}
    ec = cols.get("ean", 0) + 1
    hr = grid.get("header_row") or 0
    own = excel is None
    if own:
        import pythoncom
        import win32com.client as win32
        pythoncom.CoInitialize()
        excel = win32.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
    try:
        for bk in backups:
            if not want - set(found):
                break
            wb = None
            try:
                wb = excel.Workbooks.Open(os.path.abspath(bk), ReadOnly=True)
                ws = wb.Worksheets(grid.get("sheet")) if grid.get("sheet") else wb.ActiveSheet
                u = ws.UsedRange
                n = int(u.Row) + int(u.Rows.Count) + 5
                col = [r[0] for r in _values(ws, 1, ec, n, ec)]
                for i, x in enumerate(col, 1):
                    e = clean_ean(x)
                    if i > hr and e in want and e not in found:
                        found[e] = (bk, i)
            except Exception as e:
                if log:
                    log(f"读不了备份 {os.path.basename(bk)}：{e}")
            finally:
                if wb is not None:
                    try:
                        wb.Close(SaveChanges=False)
                    except Exception:
                        pass
    finally:
        if own:
            try:
                excel.Quit()
            except Exception:
                pass
    return found


def old_price_sup(item, sup_cols=None, row=None):
    """
    原商品的价格和供应商（替换记录用，用户 11-18「为什么有些没有原价」）：
    价格 = PA MIGLIORE，没有用 PREZZO NETTO；供应商 = Fornitore 列，空的（Griglia 已停用的商品常常空着）用几家批发商里最便宜的那家。
    item = griglia 解析出来的商品；也可以给 Excel 一整行 row（0 开始）+ grid 的 cols / sup_cols
    """
    def num(v):
        try:
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None
    if row is not None:
        cols = item or {}
        g = (lambda k: row[cols[k]] if cols.get(k) is not None and cols[k] < len(row) else None)
        price = num(g("best")) or num(g("price"))
        sup = str(g("supplier") or "").strip()
        prices = {h: num(row[i]) for h, i in (sup_cols or {}).items() if i < len(row) and num(row[i])}
    else:
        item = item or {}
        price = item.get("best") or item.get("price")
        sup = (item.get("supplier") or "").strip()
        prices = item.get("prices") or {}
    if not sup and prices:
        sup = min(prices, key=prices.get)
    return price, sup


def restore_rows(path, grid, items, excel=None, log=None, url=None, ops=None, info=None):
    """
    items = [(旧条码, 新条码, 备份路径, 备份里的行号)]：把备份里那一行（格式 + 内容，按 R1C1 照抄）抄到 Griglia 最下面，
    划线、字变灰、旧条码格加批注、图片列写 IMAGE 公式——和替换时挪下去的旧行一样。
    Griglia 里已经有这个旧条码的跳过（不重复加）。返回 [(旧条码, 抄到的行号)]；ops 记 ("append", 行号)
    """
    from utils import clean_ean
    ops = ops if ops is not None else []
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
    wb, srcs = None, {}
    try:
        wb = excel.Workbooks.Open(os.path.abspath(path))
        ws = wb.Worksheets(grid.get("sheet")) if grid.get("sheet") else wb.ActiveSheet
        cols = grid.get("cols") or {}
        ec = cols.get("ean", 0) + 1
        hr = grid.get("header_row")
        last = max([c for c in cols.values()] + [i for i in (grid.get("sup_cols") or {}).values()] + [0]) + 1
        if hr:
            head_vals = _values(ws, hr, 1, hr, min(ws.UsedRange.Columns.Count + ws.UsedRange.Column - 1, 1000))[0]
            last = max([last] + [i for i, x in enumerate(head_vals, 1) if x not in (None, "")])
        img_col = next((c for c in range(1, min(last, 40) + 1) if hr and _is_img_head(ws.Cells(hr, c).Value)), None)
        known = max([(it.get("row") or 0) for it in grid.get("items", {}).values()] +
                    [(it.get("row") or 0) for it in (grid.get("struck") or {}).values()] + [hr or 0])

        def col_vals():
            u = ws.UsedRange
            n = max(int(u.Row) + int(u.Rows.Count) - 1, known) + 5
            return [r[0] for r in _values(ws, 1, ec, n, ec)]

        def rng(sh, r):
            return sh.Range(sh.Cells(r, 1), sh.Cells(r, last))

        head_n, head_w = max(hr or 0, 1), min(last, 200)

        def head():
            return [["=" if isinstance(x, str) and x.startswith("=") else x for x in r]
                    for r in _values(ws, 1, 1, head_n, head_w, "Formula")]
        head_before = head()
        for old_ean, new_ean, bk, src_row in items:
            col = col_vals()
            if any(i > (hr or 0) and clean_ean(x) == old_ean for i, x in enumerate(col, 1)):
                log(f"Griglia 里已经有 {old_ean}，不再加")
                continue
            if bk not in srcs:
                swb = excel.Workbooks.Open(os.path.abspath(bk), ReadOnly=True)
                srcs[bk] = (swb, swb.Worksheets(grid.get("sheet")) if grid.get("sheet") else swb.ActiveSheet)
            sws = srcs[bk][1]
            if clean_ean(sws.Cells(src_row, ec).Value) != old_ean:
                log(f"备份 {os.path.basename(bk)} 第 {src_row} 行不是 {old_ean}，跳过")
                continue
            filled = [i for i, x in enumerate(col, 1) if x not in (None, "")]
            dst = max(filled[-1] if filled else 0, known) + 1
            if dst <= (hr or 0):
                raise RuntimeError(f"找不到 Griglia 最下面一行（算出来第 {dst} 行），没有修改")
            ops.append(("append", dst))
            rng(sws, src_row).Copy()
            rng(ws, dst).PasteSpecial(Paste=-4122)                      # 只贴格式
            try:
                excel.CutCopyMode = False
            except Exception:
                pass
            rng(ws, dst).FormulaR1C1 = rng(sws, src_row).FormulaR1C1
            if info is not None:
                try:
                    vals = _values(sws, src_row, 1, src_row, last)[0]
                    info[old_ean] = old_price_sup(grid.get("cols") or {}, grid.get("sup_cols") or {}, vals)
                except Exception:
                    pass
            try:
                ws.Rows(dst).RowHeight = sws.Rows(src_row).RowHeight
            except Exception:
                pass
            if img_col:
                _set_formula(ws.Cells(dst, img_col), image_formula(old_ean, url))
            moved = rng(ws, dst)
            moved.Font.Strikethrough = True
            moved.Font.Color = GREY_FONT
            try:
                c = ws.Cells(dst, ec)
                if c.Comment is not None:
                    c.Comment.Delete()
                c.AddComment("Non più disponibile dai grossisti" + (f" → sostituito da {new_ean}" if new_ean else ""))
            except Exception:
                pass
            done.append((old_ean, dst))
        if head() != head_before:
            raise RuntimeError("改动碰到了 Griglia 的表头（前几行），已放弃，文件没有保存")
        if done:
            wb.Save()
        return done
    finally:
        for swb, _sws in srcs.values():
            try:
                swb.Close(SaveChanges=False)
            except Exception:
                pass
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


def missing_old_rows(done, grid):
    """替换记录里旧商品没有留在 Griglia 最下面的（11-18 以前 Griglia 已停用的商品是原地换掉的）：[旧条码]"""
    have = set((grid or {}).get("items") or {}) | set((grid or {}).get("struck") or {})
    return [old for old, r in done.items()
            if not r.get("moved_to") and not r.get("restored_to") and old not in have]


def list_backups(name, bdir=None):
    """这个 Griglia 文件的备份（新的在前）"""
    bdir = bdir or backup_dir()
    stem, ext = os.path.splitext(name)
    try:
        fs = sorted((f for f in os.listdir(bdir) if f.startswith(stem + "_") and f.endswith(ext)), reverse=True)
    except OSError:
        return []
    return [os.path.join(bdir, f) for f in fs]


# ===================================================================== 改完核对：别的格子一个都不能变（用户 11-11）
# 用户 11-20：以前把「/」后面的引用漏掉了（=W704/V705 只改了 W，V 没改）→ 核对误报 2058 个格子。
# 现在「/」后面照样认；引号里的文字（IMAGE 的网址等）不动
_REF = re.compile(r"(?<![A-Za-z0-9_!'.\"])(\$?)([A-Z]{1,3})(\$?)(\d+)(?![0-9A-Za-z_(])")
_QUOTED = re.compile(r'("(?:[^"]|"")*")')


def _shift_refs(f, deleted):
    """删掉第 deleted 行以后，公式里同一张表的行号跟着变（和 Excel 一样：下面的行号 −1）"""
    def sub(m):
        r = int(m.group(4))
        return f"{m.group(1)}{m.group(2)}{m.group(3)}{r - 1 if r > deleted else r}"
    parts = _QUOTED.split(f)
    return "".join(p if i % 2 else _REF.sub(sub, p) for i, p in enumerate(parts))


_EXT = re.compile(r"\[(\d+)\]")


def _link_names(wb):
    """外部链接编号 → 链接到的文件名（用户 11-11：WPS 保存时把用不到的外部链接删掉、其余重新编号，
    公式里的 [2]Sheet1!… 变成 [1]Sheet1!…，指的还是同一个文件）"""
    from urllib.parse import unquote
    out = {}
    for i, link in enumerate(getattr(wb, "_external_links", None) or [], 1):
        t = getattr(getattr(link, "file_link", None), "Target", None) or ""
        t = unquote(str(t)).replace("\\", "/")
        out[str(i)] = t.rsplit("/", 1)[-1].lower() or str(i)
    return out


def _cmp_value(v):
    """格子内容换成能比较的样子：只比「写的是什么」，不比 Excel / WPS 存文件的写法
    （用户 11-11：动态数组公式读出来是 ArrayFormula 对象，每次读都是新对象 → 永远「不一样」）"""
    if isinstance(v, (str, int, bool)):
        pass
    elif isinstance(v, float):
        return round(v, 9)                       # 15 位小数存法不同（2.4900000000000002 = 2.49）
    elif hasattr(v, "text") and isinstance(getattr(v, "text", None), str):
        v = v.text                               # 数组公式 / 动态公式：比公式本身
    elif hasattr(v, "__dict__"):
        return type(v).__name__ + repr(sorted((k, str(x)) for k, x in vars(v).items()))
    else:
        return v                                 # 日期等照原样比
    if isinstance(v, str) and v.startswith("="):
        v = v.replace("_xlfn._xlws.", "").replace("_xlfn.", "").replace("_xlws.", "")   # 新函数的前缀有的软件写、有的不写
    return v


def _show(v):
    t = str(v) if v is not None else "（空）"
    return t if len(t) <= 60 else t[:57] + "…"


def _sheet_cells(path, sheet, max_col):
    """{(行, 列): (内容 / 公式, 有没有删除线)}，只读（不读图片）；公式里的外部链接编号换成链接到的文件名"""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=False)
    try:
        names = _link_names(wb)

        def ext(m):
            return "[" + names.get(m.group(1), m.group(1)) + "]"
        ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.worksheets[0]
        out = {}
        for row in ws.iter_rows(max_col=max_col):
            for c in row:
                v = getattr(c, "value", None)
                try:
                    st = bool(c.font.strike) if v not in (None, "") else False
                except Exception:
                    st = False
                if v not in (None, ""):
                    v = _cmp_value(v)
                    if isinstance(v, str) and v.startswith("=") and "[" in v:
                        v = _EXT.sub(ext, v)
                    out[(c.row, c.column)] = (v, st)
        return out
    finally:
        wb.close()


def _data_width(path, sheet, scan_rows=12, cap=1000):
    """最右边一个有表头 / 内容的列（前几行里找），+ 5 列余量"""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=False)
    try:
        ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.worksheets[0]
        w = 1
        for row in ws.iter_rows(max_row=scan_rows, max_col=cap):
            for c in row:
                if getattr(c, "value", None) not in (None, ""):
                    w = max(w, c.column)
        return min(w + 5, cap)
    finally:
        wb.close()


def diff_cells(before, after, sheet, ops, max_col=None, limit=50):
    """
    改之前 / 改之后两份文件比每个格子（内容、公式、删除线）：除了 ops 里改过的行，别的必须一模一样
    （删掉一行时下面的行往上挪、公式行号跟着 −1，照 Excel 的规矩算）。返回不一样的 [(格子, 改之前, 改之后)]，最多 limit 个
    """
    from openpyxl.utils import get_column_letter
    max_col = max_col or _data_width(before, sheet)
    b = _sheet_cells(before, sheet, max_col)
    a = _sheet_cells(after, sheet, max_col)
    dels, allowed = [], set()
    for op, r in ops:
        if op == "delete":
            allowed = {x - 1 if x > r else x for x in allowed if x != r}
            dels.append(r)
        else:
            allowed.add(r)

    def final(r):
        for d in dels:
            if r == d:
                return None
            if r > d:
                r -= 1
        return r

    out, seen = [], set()
    for (r, c), (v, st) in b.items():
        fr = final(r)
        if fr is None or fr in allowed:
            continue
        if isinstance(v, str) and v.startswith("="):
            for d in dels:
                v = _shift_refs(v, d)
        seen.add((fr, c))
        got = a.get((fr, c))
        if got != (v, st):
            out.append((f"{get_column_letter(c)}{fr}", v,
                        got[0] if got and got[0] != v else ("（删除线变了）" if got else None)))
    for (r, c), (v, _st) in a.items():
        if r not in allowed and (r, c) not in seen:
            out.append((f"{get_column_letter(c)}{r}", None, v))
    out.sort(key=lambda x: (int(re.sub(r"\D", "", x[0])), x[0]))
    return out[:limit], len(b)


def _values(ws, r1, c1, r2, c2, attr="Value"):
    """一块格子的值（attr="Formula" 读公式），二维列表（Excel / WPS 一次读完，比一格一格读快很多）"""
    v = getattr(ws.Range(ws.Cells(r1, c1), ws.Cells(r2, c2)), attr)
    if not isinstance(v, (tuple, list)):
        v = ((v,),)
    return [list(r) if isinstance(r, (tuple, list)) else [r] for r in v]


# ===================================================================== 不带图片的备份（用户 11-09：文件几百 MB，备份换成 IMAGE 函数）
def light_copy(src, dst, url=None, log=None):
    """
    把 Griglia 另存一份不带图片的：嵌进去的商品图片（几百 MB）不读，图片那一列写成 =IMAGE(网址)。
    用 openpyxl（不开 Excel），公式 / 格式 / 筛选照旧。返回 dst
    """
    import openpyxl
    import openpyxl.reader.excel as RX
    from utils import clean_ean
    orig = RX.find_images
    RX.find_images = lambda *a, **k: ([], [])                  # 不读图片（不然几百 MB 全读进内存）
    try:
        wb = openpyxl.load_workbook(src)
    finally:
        RX.find_images = orig
    n = 0
    for ws in wb.worksheets:
        head = None
        for r, row in enumerate(ws.iter_rows(min_row=1, max_row=10, values_only=True), 1):
            heads = [re.sub(r"\s+", " ", str(v or "")).strip().upper() for v in row]
            if "CODICE EAN" in heads:
                ec = heads.index("CODICE EAN") + 1
                ic = next((i + 1 for i, v in enumerate(row) if _is_img_head(v)), None)
                head = (r, ec, ic)
                break
        if not head or not head[2]:
            continue
        r0, ec, ic = head
        for r in range(r0 + 1, ws.max_row + 1):
            e = clean_ean(ws.cell(r, ec).value)
            cell = ws.cell(r, ic)
            if e:
                cell.value = image_formula(e, url)
                n += 1
            elif isinstance(cell.value, str) and cell.value.startswith("="):
                cell.value = None                                  # 原来的嵌入图片公式（WPS DISPIMG）没用了
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    tmp = dst + ".tmp.xlsx"
    wb.save(tmp)
    os.replace(tmp, dst)
    if log:
        log(f"Griglia 备份（图片换成 IMAGE 公式 {n} 个）：{dst}")
    return dst


def backup_dir():
    from settings import app_dir
    return os.path.join(app_dir(), "backup", "griglia")
