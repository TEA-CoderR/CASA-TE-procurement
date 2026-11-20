"""
从邮件收到的批发商原报价 → 改成操作员以前手动整理好的格式（用户 11-20：操作员会在供应商表格里操作，格式要和以前一样）。

以前操作员每周手动做的（照 11-07 / 11-14 的文件逐格对过）：
  MELONI     表头 barcode_pezzo → CODICE EAN、Prezzo → PREZZO NETTO、Ordine → ORDINE（价格不变）
  PETRILLO   .xls → .xlsx；Ord. → ORDINE；折扣格（Sc.1 下面，H3）3% → 6.83%；「Vista 3%」那列表头改成 PREZZO NETTO，
             有条码的行写数值 PREZZO × (1 − 6.83%)；前面那列空的「CODICE EAN」表头清掉（真正的条码在后面那列）
  NEW GRIECO .xls → .xlsx；ORDINE CARTONI → ORDINE、CODICI EAN → CODICE EAN；原来的 PREZZO NETTO 表头清掉，
             它右边插一列 PREZZO NETTO = 那一列 × 1.01（= PREZZO LISTINO × (1 − SCONTO %) × 1.01，数值）
已经整理好的文件不动。.xls 先用 Excel 转（公式、格式都留着），Excel 转不了才只按值转。
"""
import os
import re
import shutil

import quote_format as QF
from utils import clean_ean

DISCOUNT_CELL_LABEL = "SC.1"


def _num(v):
    return QF._num(v)


def _heads(ws, row):
    out = {}
    for cell in ws[row]:
        if cell.value not in (None, "") and not (isinstance(cell.value, str) and cell.value.lstrip().startswith("=")):
            out[cell.column] = QF._norm(cell.value)
    return out


def make_plan(ws, path=""):
    """
    ws（只有值也行）→ 要改的格子：{kind, set: {(行, 列): 值}, insert: (列, {行: 值}) 或 None}；
    已经整理好 / 认不出返回 None。只看、不改 ws。
    """
    lay = QF.layout(ws, path)
    if not lay:
        return None
    kind, hr = lay["kind"], lay["header_row"]
    heads = _heads(ws, hr)
    col_of = {}
    for c, h in sorted(heads.items()):
        col_of.setdefault(h, c)
    sets, insert = {}, None
    ean_rows = [i for i in range(hr + 1, (ws.max_row or hr) + 1) if clean_ean(ws.cell(i, lay["ean"]).value)]

    def rename(old, new):
        c = col_of.get(old)
        if c and new not in col_of:
            sets[(hr, c)] = new

    if kind == "petrillo":
        if "VISTA 3%" not in col_of and "ORD." not in col_of and "PREZZO NETTO" in col_of:
            return None                                     # 已经整理好了
        rename("ORD.", "ORDINE")
        net = col_of.get("PREZZO NETTO") or next((c for h, c in col_of.items() if h.startswith("VISTA")), None)
        price = col_of.get("PREZZO")
        if net and price:
            sets[(hr, net)] = "PREZZO NETTO"
            for i in ean_rows:
                p = _num(ws.cell(i, price).value)
                if p is not None:
                    sets[(i, net)] = round(p * (1 - QF.PETRILLO_DISCOUNT), 8)
            for r in range(1, hr):                          # 折扣格：「Sc.1」下面那格
                if QF._norm(ws.cell(r, net).value) == DISCOUNT_CELL_LABEL:
                    sets[(r + 1, net)] = QF.PETRILLO_DISCOUNT
                    break
        eans = [c for c, h in heads.items() if h == "CODICE EAN" and c != lay["ean"]]
        for c in eans:                                      # 空的那列「CODICE EAN」表头清掉，免得认错
            sets[(hr, c)] = None
    elif kind == "grieco":
        if "ORDINE CARTONI" not in col_of and "CODICI EAN" not in col_of:
            return None
        rename("ORDINE CARTONI", "ORDINE")
        rename("CODICI EAN", "CODICE EAN")
        net = col_of.get("PREZZO NETTO")
        lst, sc = col_of.get("PREZZO LISTINO"), col_of.get("SCONTO %") or col_of.get("SCONTO%")
        if net and lst:
            sets[(hr, net)] = None
            vals = {hr: "PREZZO NETTO"}
            for i in range(hr + 1, (ws.max_row or hr) + 1):
                o = _num(ws.cell(i, lst).value)
                old = ws.cell(i, net).value
                if o is None and old in (None, ""):
                    continue
                s = _num(ws.cell(i, sc).value) if sc else None
                s = (s or 0) / 100 if (s or 0) > 1 else (s or 0)
                vals[i] = round((o or 0) * (1 - s) * QF.GRIECO_MARKUP, 8)
            insert = (net + 1, vals)
    else:                                                   # MELONI 和别的：只改表头
        rename("BARCODE_PEZZO", "CODICE EAN")
        rename("BARCODE PEZZO", "CODICE EAN")
        rename("CODICE_A_BARRE", "CODICE EAN")             # MEGA CASH 原文件（11-20 用户手动放进来的）
        rename("CODICE A BARRE", "CODICE EAN")
        if "PREZZO NETTO" not in col_of:
            rename("PREZZO", "PREZZO NETTO")
        for o in ("ORD.", "ORD", "ORDINE CARTONI", "QUANTITA"):
            if "ORDINE" not in col_of:
                rename(o, "ORDINE")
        if not sets:
            return None
        # 「Ordine」只是大小写不一样：改成 ORDINE
        c = col_of.get("ORDINE")
        if c and ws.cell(hr, c).value != "ORDINE":
            sets[(hr, c)] = "ORDINE"
    if not sets and not insert:
        return None
    return {"kind": kind, "set": sets, "insert": insert}


# ---------------- 插一列：公式 / 合并格 / 列宽 / 筛选跟着往右挪（像 Excel 插列一样） ----------------
_CELL = re.compile(r"^(\$?)([A-Z]{1,3})(\$?)(\d+)$")
_COLS = re.compile(r"^(\$?)([A-Z]{1,3}):(\$?)([A-Z]{1,3})$")


def _shift_ref(ref, at):
    from openpyxl.utils import column_index_from_string as ci, get_column_letter as gl
    if "!" in ref:
        return ref
    parts = ref.split(":")
    if len(parts) <= 2 and all(_CELL.match(p) for p in parts):
        out = []
        for p in parts:
            d1, col, d2, row = _CELL.match(p).groups()
            n = ci(col)
            out.append(f"{d1}{gl(n + 1) if n >= at else col}{d2}{row}")
        return ":".join(out)
    m = _COLS.match(ref)
    if m:
        d1, a, d2, b = m.groups()
        a2 = gl(ci(a) + 1) if ci(a) >= at else a
        b2 = gl(ci(b) + 1) if ci(b) >= at else b
        return f"{d1}{a2}:{d2}{b2}"
    return ref


def shift_formula(f, at):
    """公式里指向第 at 列及右边的格子都往右挪一列（同一页的引用）"""
    from openpyxl.formula.tokenizer import Tokenizer, Token
    if not (isinstance(f, str) and f.startswith("=")):
        return f
    try:
        tok = Tokenizer(f)
    except Exception:
        return f
    for t in tok.items:
        if t.type == Token.OPERAND and t.subtype == Token.RANGE:
            t.value = _shift_ref(t.value, at)
    return "=" + "".join(t.value for t in tok.items)


def _shift_sqref(sq, at):
    return " ".join(_shift_ref(r, at) for r in str(sq).split())


def insert_col(ws, at):
    """openpyxl 的 insert_cols 只挪格子：这里把公式、合并格、列宽、筛选、条件格式也一起挪"""
    from openpyxl.utils import get_column_letter as gl
    from openpyxl.worksheet.dimensions import ColumnDimension
    from openpyxl.formatting.formatting import ConditionalFormattingList
    for row in ws.iter_rows():
        for cell in row:
            v = cell.value
            if isinstance(v, str) and v.startswith("="):
                cell.value = shift_formula(v, at)
    merged = [str(r) for r in ws.merged_cells.ranges]
    for r in merged:
        ws.unmerge_cells(r)
    widths = {}
    for k, d in list(ws.column_dimensions.items()):
        widths[k] = (d.width, d.hidden, d.customWidth)
    ws.insert_cols(at)
    for r in merged:
        ws.merge_cells(_shift_ref(r, at))
    from openpyxl.utils import column_index_from_string as ci
    ws.column_dimensions.clear()
    for k, (w, hid, cw) in widths.items():
        try:
            n = ci(k)
        except ValueError:
            continue
        key = gl(n + 1) if n >= at else k
        dim = ColumnDimension(ws, index=key, width=w, hidden=hid)
        ws.column_dimensions[key] = dim
    left = widths.get(gl(at - 1))
    if left and left[0]:
        ws.column_dimensions[gl(at)] = ColumnDimension(ws, index=gl(at), width=left[0])
    if ws.auto_filter.ref:
        ws.auto_filter.ref = _shift_ref(ws.auto_filter.ref, at)
    try:
        old = ws.conditional_formatting
        new = ConditionalFormattingList()
        for cf in old:
            for rule in cf.rules:
                new.add(_shift_sqref(cf.sqref, at), rule)
        ws.conditional_formatting = new
    except Exception:
        pass
    try:
        for dv in ws.data_validations.dataValidation:
            dv.sqref = type(dv.sqref)(_shift_sqref(dv.sqref, at))
    except Exception:
        pass


def apply_plan(ws, plan):
    """在 openpyxl 的工作表上照 plan 改（先改格子，再插列）"""
    from copy import copy
    for (r, c), v in plan["set"].items():
        ws.cell(r, c).value = v
    if plan.get("insert"):
        at, vals = plan["insert"]
        insert_col(ws, at)
        for r, v in vals.items():
            cell = ws.cell(r, at)
            cell.value = v
            src = ws.cell(r, at - 1)                       # 格式照左边那列
            if src.has_style:
                cell._style = copy(src._style)


def prepare(src, out_dir, log=None):
    """
    原报价 → out_dir 里整理好的 .xlsx（文件名 = 原名换成 .xlsx）；不用改的原样返回 src。
    返回 (路径, 说明)。说明 = 改了什么（写日志 / 提示用）。
    """
    from openpyxl import load_workbook
    from utils import read_sheets
    log = log or (lambda *a: None)
    sheets = read_sheets(src)
    ws_v, _lay = QF.pick_sheet(sheets, src)
    if ws_v is None:
        return src, ""
    name = next(n for n, w in sheets if w is ws_v)
    plan = make_plan(ws_v, src)
    if not plan:
        return src, ""
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src))[0]
    dst = os.path.join(out_dir, stem + ".xlsx")
    how = "copy"
    if src.lower().endswith(".xls"):
        conv_src = src
        try:                                                # WPS 存的 .xls 范围写错了 → 先修好，Excel 才肯打开
            fixed = os.path.join(out_dir, "_fixed_" + os.path.basename(src))
            if repair_xls(src, fixed):
                conv_src = fixed
        except Exception as e:
            log(f"  修 .xls 没成功：{e}")
        tmp = _excel_convert(conv_src, log)
        if tmp:
            shutil.copyfile(tmp, dst)
            how = "excel"
        else:
            import service
            service.xls_to_xlsx_values(src, dst)
            how = "values"
    elif os.path.abspath(src) != os.path.abspath(dst):
        shutil.copyfile(src, dst)
    wb = load_workbook(dst)
    try:
        ws = wb[name] if name in wb.sheetnames else wb.active
        apply_plan(ws, plan)
        wb.save(dst)
    finally:
        wb.close()
    what = {"petrillo": "Ord. → ORDINE、PREZZO NETTO = PREZZO × (1 − 6.83%)",
            "grieco": "ORDINE / CODICE EAN 表头，插一列 PREZZO NETTO = 原净价 × 1.01"}.get(
        plan["kind"], "表头改成 CODICE EAN / PREZZO NETTO / ORDINE")
    if how == "values":
        what += "（Excel 转不了 .xls，只按值转，原来的格式和公式没有了）"
    return dst, what


def repair_xls(src, dst):
    """
    修好 Excel 不肯打开的 .xls（11-20 NEW GRIECO Canvass：「Office 检测到此文件存在一个问题…不能打开此文件」）：
    这些 .xls 是用 WPS 存的，工作表的 DIMENSIONS（表格范围）写成 1,048,576 行——那是 .xlsx 的上限，
    老 .xls 最多 65,536 行，Excel 的文件检查就当成坏文件拒绝打开。这里只把范围改成真正的行数 / 列数（同样长度、原地改），
    其他一个字节都不动。返回改了什么 [(表名, 原来的行数, 改成的行数)]；没问题返回 []（dst 不写）。
    """
    import struct
    import olefile
    import xlrd
    book = xlrd.open_workbook(src, on_demand=True)
    try:
        sizes = [(book.sheet_by_index(i).name, book.sheet_by_index(i).nrows, book.sheet_by_index(i).ncols)
                 for i in range(book.nsheets)]
    finally:
        book.release_resources()
    ole = olefile.OleFileIO(src)
    try:
        name = "Workbook" if ole.exists("Workbook") else "Book"
        data = bytearray(ole.openstream(name).read())
    finally:
        ole.close()

    fixes = fix_dimensions(data, sizes)
    if not fixes:
        return []
    import shutil
    shutil.copyfile(src, dst)
    ole = olefile.OleFileIO(dst, write_mode=True)
    try:
        ole.write_stream(name, bytes(data))
    finally:
        ole.close()
    return fixes


def fix_dimensions(data, sizes):
    """BIFF8 工作簿流（bytearray，原地改）里每个工作表的 DIMENSIONS 超过 .xls 上限的改成 sizes[i] = (表名, 行数, 列数)"""
    import struct

    def records(pos):
        while pos + 4 <= len(data):
            rid, ln = struct.unpack_from("<HH", data, pos)
            yield pos, rid, ln
            pos += 4 + ln

    sheets = []                                            # BOUNDSHEET：每个工作表从流里哪个位置开始
    for pos, rid, ln in records(0):
        if rid == 0x0085:
            off, _vis, kind = struct.unpack_from("<IBB", data, pos + 4)
            if kind == 0:
                sheets.append(off)
        elif rid == 0x000A:                                # 工作簿那一段结束
            break
    fixes = []
    for i, off in enumerate(sheets):
        if i >= len(sizes):
            break
        sname, nrows, ncols = sizes[i]
        for pos, rid, ln in records(off):
            if rid == 0x0200 and ln >= 14:                 # DIMENSIONS
                r0, r1, c0, c1 = struct.unpack_from("<IIHH", data, pos + 4)
                if r1 > 65536 or c1 > 256:
                    nr, nc = max(nrows, 1), max(min(c1, 256), ncols, 1)
                    struct.pack_into("<IIHH", data, pos + 4, min(r0, nr - 1), nr, min(c0, nc - 1), nc)
                    fixes.append((sname, r1, nr))
                break
            if rid == 0x000A:
                break
    return fixes


def _excel_convert(src, log):
    """
    .xls 用 Excel / WPS 另存为 .xlsx（公式、格式都留着）；转不了返回 None。
    Excel 有时不肯直接打开邮件里来的 .xls（「Office 检测到此文件存在一个问题…不能打开此文件」= 文件验证没过，
    11-20 NEW GRIECO Canvass 48）：人手打开是先进「受保护的视图」再点「启用编辑」——程序照这样做一次。
    """
    try:
        from utils import convert_to_xlsx, is_valid_xlsx
        out = convert_to_xlsx(src)
        if out and out.lower().endswith(".xlsx") and os.path.exists(out) and is_valid_xlsx(out):
            return out
    except Exception as e:
        log(f"  Excel 转换失败：{e}")
    return _protected_view_convert(src, log)


def _protected_view_convert(src, log):
    try:
        import win32com.client as win32
    except Exception:
        return None
    xl = None
    dst = src + ".xlsx"
    try:
        xl = win32.DispatchEx("Excel.Application")          # 单独开一个，不动别的 Excel
        xl.Visible = False
        xl.DisplayAlerts = False
        pv = xl.ProtectedViewWindows.Open(os.path.abspath(src))
        wb = pv.Edit()                                        # =「启用编辑」
        wb.SaveAs(os.path.abspath(dst), FileFormat=51)
        wb.Close(False)
        from utils import is_valid_xlsx
        return dst if os.path.exists(dst) and is_valid_xlsx(dst) else None
    except Exception as e:
        log(f"  受保护视图打开也不行：{e}")
        return None
    finally:
        if xl is not None:
            try:
                xl.Quit()
            except Exception:
                pass
