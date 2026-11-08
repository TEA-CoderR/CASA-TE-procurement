"""
统一样式的 Excel 报表（2026-11-07 用户：导出的表都要美观、专业，参考门店报告，可以带颜色）。
不依赖界面。一个工作表 = 深蓝标题条 + 灰色说明行 + 彩色表头 + 斑马纹数据 + 细框 + Excel 表格（自带筛选）+ 冻结表头，
可选第一列商品照片（=IMAGE(网址)，Excel 365 打开时自己读，文件里不放图，所以文件小）。

用法：
    wb = new_book()
    add_sheet(wb, "都不做了", "Griglia · 都不做了", "CASA&TE · 07/11/2026 · 18 articoli",
              [Col("EAN", 16, "ean"), Col("Descrizione", 40, bold=True), Col("Prezzo", 11, "price")],
              rows, marks={(行, 列): "GREEN"}, photo_eans=[...])
    wb.save(path)
"""
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

# 字体（2026-11-07 用户：原来的字体看着眼睛难受、不清晰）：微软雅黑，Windows 都有（意大利文 Windows 也有），
# 中文和拉丁字母都清楚；没有这个字体的电脑 Excel 会自己换一个
XL_FONT = "Microsoft YaHei"
INK, MUTED, LINE, ZEBRA = "1F2A37", "6B7280", "E5E7EB", "FAFBFC"
BAND = "1F3A5F"                              # 标题条深蓝（和门店报告一样）
HEAD = "F3F4F6"                              # 表头默认浅灰
# 格子标记：(底色, 字色)
TONES = {"GREEN": ("E7F5EC", "1E7A3C"), "RED": ("FDECEC", "B42318"), "AMBER": ("FFF4E0", "A15C07"),
         "BLUE": ("E8F0FE", "1A56DB"), "GREY": ("F3F4F6", "6B7280")}
# 表头分组颜色
HEAD_TONES = {"id": "F3F4F6", "cat": "EEF2FF", "price": "E7F5EC", "sup": "E8F0FE", "warn": "FFF4E0"}
IMG = 48                                      # 照片 48 × 48 像素
FMT = {"price": '€ #,##0.00', "num": '#,##0', "pct": '+0.0%;-0.0%;0.0%', "ean": "@", "txt": "@"}


class Col:
    """一列：表头、列宽、类型（txt / ean / num / price / pct）、表头分组颜色、加粗、靠左"""

    def __init__(self, head, width=12, kind="txt", group="id", bold=False, left=None):
        self.head, self.width, self.kind, self.group, self.bold = head, width, kind, group, bold
        self.left = (kind == "txt") if left is None else left


def new_book():
    wb = Workbook()
    wb.remove(wb.active)
    return wb


def _safe_title(name, wb):
    t = "".join("_" if ch in '[]:*?/\\' else ch for ch in str(name))[:31] or "Sheet"
    base, n = t, 2
    while t in wb.sheetnames:
        t = f"{base[:28]}_{n}"
        n += 1
    return t


def add_sheet(wb, name, title, subtitle, cols, rows, marks=None, photo_eans=None, url=None, note=None, tone=None):
    """
    加一个工作表。rows = [[值…]]（和 cols 一一对应）；marks = {(行号, 列号): "GREEN" / "RED" / "AMBER" / "BLUE" / "GREY"}
    （行号、列号都从 0 数，不算照片列）；photo_eans = 每行的条码（给了就在最前面加一列照片）；
    note = 标题下面一行斜体说明（可选）；tone = 说明行的颜色（TONES 的键）
    """
    marks = marks or {}
    photo = photo_eans is not None
    ws = wb.create_sheet(_safe_title(name, wb))
    ws.sheet_view.showGridLines = False
    allc = ([Col("Foto", 9, "photo")] if photo else []) + list(cols)
    ncol = len(allc)
    last = get_column_letter(ncol)
    for i, c in enumerate(allc, 1):
        ws.column_dimensions[get_column_letter(i)].width = c.width
    thin = Side(style="thin", color=LINE)
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="center", wrap_text=True, indent=1)
    right = Alignment(horizontal="right", vertical="center", indent=1)
    # 标题条 + 说明行
    ws.merge_cells(f"A1:{last}1")
    ws["A1"] = title
    ws["A1"].font = Font(name=XL_FONT, size=16, bold=True, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", fgColor=BAND)
    ws["A1"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 32
    ws.merge_cells(f"A2:{last}2")
    ws["A2"] = subtitle
    ws["A2"].font = Font(name=XL_FONT, size=10, color=MUTED)
    ws["A2"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[2].height = 20
    hr = 4
    if note:
        ws.merge_cells(f"A3:{last}3")
        bg, ink = TONES.get(tone or "GREY")
        ws["A3"] = note
        ws["A3"].font = Font(name=XL_FONT, size=9, color=ink)
        ws["A3"].fill = PatternFill("solid", fgColor=bg)
        ws["A3"].alignment = Alignment(horizontal="left", vertical="center", wrap_text=True, indent=1)
        ws.row_dimensions[3].height = 26
        hr = 5
    # 表头
    for j, c in enumerate(allc, 1):
        hc = ws.cell(hr, j, c.head)
        hc.font = Font(name=XL_FONT, size=10, bold=True, color=INK)
        hc.fill = PatternFill("solid", fgColor=HEAD_TONES.get(c.group, HEAD))
        hc.alignment = center
        hc.border = box
    ws.row_dimensions[hr].height = 30
    # 数据
    off = 1 if photo else 0
    import product_images as PI
    url = url or PI.DEFAULT_URL
    for k, vals in enumerate(rows):
        r = hr + 1 + k
        zebra = PatternFill("solid", fgColor=ZEBRA) if k % 2 else None
        if photo:
            e = PI.norm_ean((photo_eans[k] if k < len(photo_eans) else None) or "")
            pc = ws.cell(r, 1, f'=IFERROR(_xlfn.IMAGE("{url.format(ean=e)}","",3,{IMG},{IMG}),"")' if e else None)
            pc.border = box
            pc.alignment = center
            if zebra:
                pc.fill = zebra
        for j, c in enumerate(cols):
            v = vals[j] if j < len(vals) else None
            if c.kind == "pct" and isinstance(v, (int, float)):
                v = v / 100.0                                  # 传进来的是 12.3（%），Excel 里存 0.123 用百分比格式
            if c.kind == "ean" and v not in (None, ""):
                v = str(v)
            dc = ws.cell(r, j + 1 + off, v)
            dc.border = box
            dc.alignment = left if c.left else right if c.kind in ("num", "price", "pct") else center
            dc.number_format = FMT.get(c.kind, "General")
            t = marks.get((k, j))
            if t:
                bg, ink = TONES[t]
                dc.fill = PatternFill("solid", fgColor=bg)
                dc.font = Font(name=XL_FONT, size=10, bold=True, color=ink)
            else:
                dc.font = Font(name=XL_FONT, size=10, color=INK, bold=c.bold)
                if zebra:
                    dc.fill = zebra
        ws.row_dimensions[r].height = 42 if photo else 20
    end = hr + max(1, len(rows))
    if not rows:                                                   # 空表也放一行说明，Excel 表格不能是空的
        ws.cell(hr + 1, 1 + off, "—").font = Font(name=XL_FONT, size=10, color=MUTED)
    # Excel 表格：表头带筛选按钮（照片列不放进去）
    first = get_column_letter(1 + off)
    t = Table(displayName=f"Tabella{len(wb.sheetnames)}", ref=f"{first}{hr}:{last}{end}")
    t.tableStyleInfo = TableStyleInfo(name="TableStyleLight1", showRowStripes=False)
    ws.add_table(t)
    ws.freeze_panes = ws.cell(hr + 1, 1 + off + (1 if photo else 0))   # 表头 + 照片 / 条码列一直看得到
    foot = end + 2
    if photo:
        ws.cell(foot, 1, "Foto: si caricano da internet in Excel 365 · 照片要用 Excel 365 打开才显示").font = \
            Font(name=XL_FONT, size=9, color=MUTED)
    # 打印：横向、一页宽
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"{hr}:{hr}"
    ws.page_margins.left = ws.page_margins.right = 0.4
    return ws
