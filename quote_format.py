"""
批发商报价：直接认供应商发来的原文件（不用操作员改表头）。

以前的程序只认固定表头（CODICE EAN / PREZZO NETTO / ORDINE），所以每周操作员都要把收到的报价改一遍：
  MELONI     barcode_pezzo → CODICE EAN、Prezzo → PREZZO NETTO、Ordine → ORDINE
  PETRILLO   .xls → .xlsx、Ord. → ORDINE、「Vista 3%」那列换成 PREZZO × (1 − 6.83%)
  NEW GRIECO .xls → .xlsx、ORDINE CARTONI → ORDINE、CODICI EAN → CODICE EAN、
             插一列 PREZZO NETTO = PREZZO LISTINO × (1 − SCONTO %) × 1.01
现在程序自己认这些写法、按固定规则算件价（用户 11-20 定：PETRILLO 一律按 6.83% 折扣，NEW GRIECO 一律加 1%），
结果和操作员改好的文件一样；改好的文件照样能读（价格从原来的列算，不会重复打折 / 加价）。
"""
import os
import re

from utils import parse_price

EAN_HEADS = ("CODICE EAN", "CODICI EAN", "BARCODE_PEZZO", "BARCODE PEZZO", "BARCODE")
ORD_HEADS = ("ORDINE", "ORD.", "ORD", "ORDINE CARTONI")
NET_HEADS = ("PREZZO NETTO", "PREZZO")          # 一般的报价：先 PREZZO NETTO，没有用 PREZZO（MELONI 原文件）

PETRILLO_DISCOUNT = 0.0683                      # 用户 11-20：PETRILLO 每次都按 6.83% 算
GRIECO_MARKUP = 1.01                            # 用户 11-20：NEW GRIECO 每次都要加 1%

SCAN_ROWS = 10


def _norm(v):
    return re.sub(r"\s+", " ", str(v if v is not None else "").strip().upper())


def _heads(ws):
    """前 10 行的 [(列号, 行号, 大写表头)]"""
    out = []
    try:
        last = min(SCAN_ROWS, ws.max_row or 0)
    except Exception:
        last = SCAN_ROWS
    for r in range(1, last + 1):
        try:
            cells = ws[r]
        except Exception:
            continue
        for cell in cells:
            v = cell.value
            if v not in (None, "") and not (isinstance(v, str) and v.lstrip().startswith("=")):
                out.append((cell.column, cell.row, _norm(v)))
    return out


def _filled(ws, col, row, max_scan=80):
    n = 0
    try:
        top = min(row + max_scan, ws.max_row or row)
    except Exception:
        top = row + max_scan
    for r in range(row + 1, top + 1):
        v = ws.cell(r, col).value
        if v is not None and str(v).strip() and any(ch.isdigit() for ch in str(v)):
            n += 1
    return n


def find_head(ws, names, heads=None, best=False):
    """按 names 的先后找表头（不分大小写、多余空格不算）；best=True 时同名几列选下面有数字最多的。返回 (列, 行)"""
    heads = _heads(ws) if heads is None else heads
    for name in names:
        hits = [(c, r) for c, r, h in heads if h == name]
        if hits:
            if best and len(hits) > 1:
                return max(hits, key=lambda h: (_filled(ws, *h), -h[0]))
            return hits[0]
    return None, None


def supplier_kind(ws, path="", heads=None):
    """petrillo / grieco / ""：看前几行的字（Petrillo D1「F.lli Petrillo & Co.」、Grieco H1「CANVASS NEW GRIECO」），再看文件名"""
    heads = _heads(ws) if heads is None else heads
    text = " ".join(h for _c, _r, h in heads) + " " + os.path.basename(path or "").upper()
    if "PETRILLO" in text:
        return "petrillo"
    if "GRIECO" in text:
        return "grieco"
    return ""


def _num(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return parse_price(v)


def _r(x):
    return round(x, 8) if x is not None else None


def layout(ws, path=""):
    """
    一份报价的结构：{kind, ean, ean_row, ord, ord_row, price_col, header_row, price(ws, 行) → 件价 / None}；
    条码或价格找不到返回 None。price_col = 用来排除「每箱件数」的价格列。
    """
    heads = _heads(ws)
    kind = supplier_kind(ws, path, heads)
    c_ean, r_ean = find_head(ws, EAN_HEADS, heads, best=True)
    if not c_ean:
        return None
    c_ord, r_ord = find_head(ws, ORD_HEADS, heads)
    price = None
    c_price = r_price = None
    if kind == "petrillo":
        c_price, r_price = find_head(ws, ("PREZZO",), heads)
        if c_price:
            cp = c_price

            def price(ws_, i):
                p = _num(ws_.cell(i, cp).value)
                return _r(p * (1 - PETRILLO_DISCOUNT)) if p is not None else None
    elif kind == "grieco":
        c_price, r_price = find_head(ws, ("PREZZO LISTINO",), heads)
        c_sc, _ = find_head(ws, ("SCONTO %", "SCONTO%", "SCONTO"), heads)
        if c_price:
            cp, cs = c_price, c_sc

            def price(ws_, i):
                p = _num(ws_.cell(i, cp).value)
                if p is None:
                    return None
                s = _num(ws_.cell(i, cs).value) if cs else None
                s = (s or 0) / 100 if (s or 0) > 1 else (s or 0)
                return _r(p * (1 - s) * GRIECO_MARKUP)
    if price is None:                                   # 一般的报价（MELONI、MEGA CASH…）或者认不出的
        c_price, r_price = find_head(ws, NET_HEADS, heads)
        if not c_price:
            return None
        cp = c_price

        def price(ws_, i):
            return parse_price(ws_.cell(i, cp).value)
    return {"kind": kind, "ean": c_ean, "ean_row": r_ean, "ord": c_ord, "ord_row": r_ord,
            "price_col": c_price, "header_row": max(r_ean, r_price), "price": price}


def ean_ord(ws):
    """回写供应商文件用：(条码列, 条码表头行, 订货列, 订货表头行)，找不到的是 None"""
    heads = _heads(ws)
    c_ean, r_ean = find_head(ws, EAN_HEADS, heads, best=True)
    c_ord, r_ord = find_head(ws, ORD_HEADS, heads)
    return c_ean, r_ean, c_ord, r_ord


def price_col(ws, path=""):
    """回写时找每箱件数要排除的价格列"""
    lay = layout(ws, path)
    return lay["price_col"] if lay else None
