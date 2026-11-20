"""
供应商报价表的结构识别：每箱件数在哪一列、箱数应该写在哪一列。

报价表里的 PREZZO NETTO 是单件价格，一箱的金额 = 件价 × 每箱件数。
各家的表格式不一样：
  F.lli Petrillo：表头「PZ」，小计公式 =H*E*B（价格 × PZ × ORDINE）
  Meloni        ：表头「UDV」，ORDINE 本身是公式 =L*E（L 填箱数，ORDINE 算出件数）
  New Grieco    ：每箱件数那一列没有表头，总价公式 =(B*E*T)（ORDINE × 件数 × 价格）
所以先按表头找，找不到再从公式里推断。
"""
import re
from collections import Counter

from openpyxl.utils import get_column_letter, column_index_from_string

# 表示「每箱件数」的表头（大写、去掉多余空格后比较）
PACK_HEADERS = {
    "PZ", "UDV", "PZ*COLLI", "PZ * COLLI", "PZ X COLLI", "PZ X COLLO", "PZ/COLLO", "PZ COLLO",
    "PZ/CT", "PZ X CT", "PZ.CONF", "PZ CONF", "PZ/CONF", "PEZZI", "PEZZI X CARTONE",
    "PEZZI PER COLLO", "PEZZI X COLLO", "PEZZI_X_CARTONE", "CONF", "CONFEZIONE", "CONFEZIONI PER CARTONE",
    "QTA CONF", "Q.TA CONF", "QTA X CONF", "IMBALLO", "PZ IMBALLO",
}
# 表头里有这些词的列是价格/金额，不会是件数
PRICE_WORDS = ("PREZZO", "PRICE", "IVA", "IVATO", "NETTO", "LISTINO", "SCONTO", "TOT", "IMPORTO", "SUB")

_REF = re.compile(r"\$?([A-Z]{1,3})\$?(\d+)")
_PRODUCT2 = re.compile(r"^=\(?\s*\$?([A-Z]{1,3})\$?(\d+)\s*\*\s*\$?([A-Z]{1,3})\$?(\d+)\s*\)?$")


def _norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip().upper())


def _headers(ws, header_row):
    """{列号: 表头文字}"""
    out = {}
    for row in ws.iter_rows(min_row=header_row, max_row=header_row, values_only=True):
        for c_idx, v in enumerate(row, 1):
            if v not in (None, ""):
                out[c_idx] = _norm(v)
    return out


def _data_rows(ws, header_row, max_scan):
    for r_idx, row in enumerate(ws.iter_rows(min_row=header_row + 1, max_row=header_row + max_scan,
                                             values_only=True), header_row + 1):
        yield r_idx, row


def find_pack_col(ws, header_row, col_ord, col_price, max_scan=80):
    """
    返回 (每箱件数的列号, 怎么找到的) ；找不到返回 (None, None)。
    ws 需要能看到公式（不是只有值的副本），否则只能按表头找。
    """
    heads = _headers(ws, header_row)
    for c, h in sorted(heads.items()):
        if h in PACK_HEADERS:
            return c, "header"

    def is_price(c):
        return c == col_price or any(w in heads.get(c, "") for w in PRICE_WORDS)

    cnt = Counter()
    for r_idx, row in _data_rows(ws, header_row, max_scan):
        for v in row:
            if not (isinstance(v, str) and v.startswith("=") and "*" in v):
                continue
            refs = _REF.findall(v.upper())
            if not refs or any(int(n) != r_idx for _, n in refs):
                continue
            cols = {column_index_from_string(c) for c, _ in refs}
            if col_ord not in cols:
                continue
            rest = {c for c in cols if c != col_ord and not is_price(c)}
            if len(rest) == 1:
                cnt[rest.pop()] += 1
    if cnt:
        c, n = cnt.most_common(1)[0]
        if n >= 3:
            return c, "formula"
    return None, None


def find_order_input(ws, header_row, col_ord, pack_col, max_scan=80):
    """
    ORDINE 列本身是公式 =箱数列 × 件数列 时（比如 Meloni 的 =L*E），
    返回 (箱数列号, (公式第一列, 公式第二列))；否则返回 None（箱数直接写 ORDINE）。
    """
    if not pack_col:
        return None
    cnt = Counter()
    for r_idx, row in _data_rows(ws, header_row, max_scan):
        if col_ord - 1 >= len(row):
            continue
        v = row[col_ord - 1]
        if not isinstance(v, str):
            continue
        m = _PRODUCT2.match(v.replace(" ", "").upper())
        if not m or int(m.group(2)) != r_idx or int(m.group(4)) != r_idx:
            continue
        a, b = column_index_from_string(m.group(1)), column_index_from_string(m.group(3))
        if pack_col not in (a, b) or a == b or col_ord in (a, b):
            continue
        other = b if a == pack_col else a
        cnt[(other, a, b)] += 1
    if cnt:
        (other, a, b), n = cnt.most_common(1)[0]
        if n >= 3:
            return other, (a, b)
    return None


def order_formula(pair, row):
    a, b = pair
    return f"={get_column_letter(a)}{row}*{get_column_letter(b)}{row}"


def parse_pack(v):
    """每箱件数：正数取整数，读不出来当 1"""
    try:
        f = float(str(v).replace(",", ".").strip())
    except (TypeError, ValueError):
        return 1
    if f <= 0:
        return 1
    return int(f) if abs(f - round(f)) < 1e-9 else f
