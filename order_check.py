"""
批发商比价的 ②：检查门店订货清单（比价用的主订单只能从这里生成）

一次可以放一家或几家门店的清单（和门店配货一样，几家一起才好调货）：
门店发来的订货清单 → 按这家门店的销量、数据库库存和订货周期算建议量
→ 逐条对比清单数量：增加 / 减少 / 不订 / 不变 → 可以手动改「确定件数」
→ 确认时先算门店间调货（别的门店多出来的货先用掉），每家门店剩下要订的写成它自己的主订单
  （每家店一个总文件夹，PEZZI = 确定件数 − 调货），原文件挪进 ORDINE\\_旧订单；调货单写进相关门店的文件夹；
→ 再一家一家点「开始处理」做供应商比价分配（每家店各自下单、各自达到起订量）。
"""
import os
import math
import threading
import time
from datetime import datetime

from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (QWidget, QLabel, QPushButton, QLineEdit, QComboBox, QHBoxLayout, QVBoxLayout,
                               QFileDialog, QMessageBox, QSpinBox, QAbstractItemView, QHeaderView, QDialog,
                               QGridLayout, QFrame, QStyledItemDelegate, QAbstractSpinBox, QButtonGroup, QMenu,
                               QSizePolicy)

from i18n import tr
from settings import save_settings
import reorder_service as R
import transfers as TR
from reorder_page import short_decision, store_label, algo_widgets

# 表格列（逻辑顺序）→ 显示顺序：按审核时看一行的顺序
#   哪个商品：门店 判断 等级 名称 | 现在：本店库存 库存够卖 日均 | 门店要多少：清单箱数 清单件数 清单订后够卖 |
#   系统建议：建议件数 | 最后订多少：确定件数 变化（比清单多 / 少几件）确定箱数 确定订后够卖 | 调货 | 原因 |
#   （默认藏起来，「显示哪些列」里可以打开）目标库存 各周期销量 周期 箱规 编号 条码
# 23 = 变化，24 = 调货，25 = 原因（列可以拖动换位置，会记住）
# 2026-10-20 第二版（用户：要简洁、改前改后一眼看出）：
#   「判断」只写结论：不订 / 照清单订 / 多订 N 箱 / 少订 N 箱 / 建议调货（和门店清单比；为什么建议放在鼠标提示里）
#   紧接着「清单 → 确定 → 调货」对比改前改后和调货件数：
#     清单按箱：清单箱数 | 确定箱数 | 确定件数 ✎；清单按件：清单件数 | 确定件数 ✎（箱数列自动藏起来）
# 2026-10-20 第三版：和门店配货的订货明细同一个样子（调货只显示从哪家调进来）：
#   门店 条码 名称 等级 周期 本店库存 日均 库存够卖 每箱件数 | 清单 判断 确定箱数 确定件数 ✎ 调货 | 到货后够卖 原因
# 2026-10-21 加了 26 Codice Fornitore、27 供应商（数据库登记的），默认放在条码后面
# 2026-10-21 第五版：按箱审核（用户：件数看着乱，少订几件不改变订的箱数没意义）：清单箱数 → 判断 → 确定箱数 ✎ → 调货；
#   件数列（清单件数、建议件数、确定件数）默认收起来；数据库没有 / 没有箱规的商品放在单独的标签页
# 2026-10-21 加了 28 月均（最长周期销量 ÷ 月数，和订货明细一样），放在日均后面
# 2026-10-21 第六版：用户自己排好的顺序做默认：条码 供应商 Codice 名称 等级 周期 本店库存 日均 月均 够卖 30/60/90/180 天
#   每箱件数 清单箱数 判断 确定箱数 ✎ 确定件数 调货 到货后够卖（其余收起来）
# 2026-10-25 第七版（用户：审核太累，很多列从来不看）：一行只放判断要用的 ——
#   商品（图 + 名称）供应商 | 现在：库存 月销 现在够卖 | 订多少：每箱件数 清单 → 确定 ✎ | 结果：订后够卖 金额 | 判断 调货
#   订后够卖一个数就能看出订得合不合理：红 = 下次到货前就卖完，黄 = 压货。30/60/90/180 天、日均、周期、等级、编号、条码收起来（详情栏里有）
#   29 = 金额（确定箱数 × 每箱件数 × 件价，有调货的按调货以后的箱数）
#   2026-11-01 加 30 = 情况（Griglia 商品 · 批发商都没有 / 非 Griglia 商品 · 批发商都没有 / 非 Griglia 商品 · 批发商有），放在判断后面，
#   待核对的商品也在主表格里显示，右键表头按「情况」筛选、直接改（用户：在列表里直接判断和修改）
_ORDER = [22, 3, 27, 4, 28, 9, 10, 11, 15, 20, 29, 0, 30, 24, 2, 26, 16, 17, 18, 5, 6, 7, 8, 14, 12, 13, 19, 21, 23, 25, 1]
SITU = 30
# 2026-10-21 第四版：窗口和订货明细一个样子（门店按钮、一行工具条、状态栏），名称显示、供应商和原因收起来（详情栏里有）：
#   门店 条码 Codice 名称 等级 周期 本店库存 日均 库存够卖 每箱件数 | 清单 判断 确定箱数 确定件数 ✎ 调货 ✎ | 到货后够卖
_LAYOUT_VER = 9           # 改了默认顺序就加 1（第 9 版起列宽也一起恢复默认）
_DEFAULT_HIDDEN = [2, 26, 16, 17, 18, 5, 6, 7, 8, 14, 12, 13, 19, 21, 23, 25, 1]
_HIDDEN_VER = 7           # 改了默认藏哪些列就加 1（settings.COMPANY_DEFAULTS 里的 check_cols_hidden 一起改）
CHANGE_COL, TRANSFER_COL, REASON_COL = 23, 24, 25
AMOUNT = 29
# 「判断」排序：要看的排前面（不订、少订、调货、多订），照清单订的放最后
VERDICT_RANK = {"none": 0, "less": 1, "transfer": 2, "more": 3, "nodb": 4, "same": 5}


def _col(k):
    return _ORDER.index(k)


def _perm(vals):
    return [vals[k] for k in _ORDER]


EDIT_COL = _col(15)           # 确定箱数 ✎（按箱审核）
AMOUNT_COL = _col(AMOUNT)     # 金额
PCS_COL = _col(14)            # 确定件数（= 箱数 × 每箱件数，默认收起来）


def _pk(x):
    try:
        v = float(x.get("pack") or 0)
    except (TypeError, ValueError):
        return 0.0
    return v if v == v and v > 0 else 0.0


def PROB_KINDS_TIPS():
    """待核对窗口的标签页（顺序 = 显示顺序）和鼠标提示"""
    return (("noquote", tr("在我们的 Griglia 里，但这周几家批发商的报价里都没有：比价时会缺失。可能批发商不做了、断货，或者要向别处订")),
            ("nogrid_noq", tr("非 Griglia 商品（多半是门店发来的别的区域的商品），批发商报价里也没有：比价时会缺失")),
            ("nogrid", tr("非 Griglia 商品（多半是门店发来的别的区域的商品），但批发商有报价：比价能分出去，要不要订你决定")))


def _prob_title(kind, n, has_grid=True):
    """标签页名字：短、一眼看懂"""
    if kind == "noquote":
        return tr("Griglia 商品 · 批发商都没有（{0}）", n) if has_grid else tr("批发商都没有（{0}）", n)
    return tr("非 Griglia 商品（{0}）", n)


def _issue(x):
    """系统算不了的原因（待核对窗口「备注」列）：nodb 数据库里没有 / nopack 不知道每箱几件；能算返回 None。
    这两种都说明哪家批发商报价里都没有（报价里有就会补上箱规），所以归在「批发商都没有」的页里"""
    if not x.get("in_db", True):
        if not (x.get("quote_sup") and _pk(x) > 0):
            return "nodb"
    elif _pk(x) <= 0:
        return "nopack"
    return None


def _situation(x):
    """「情况」列：待核对的商品属于哪一类（和待核对窗口的页一样）；正常的空着"""
    k = _problem(x)
    if k == "noquote":
        return tr("Griglia 商品 · 批发商都没有") if x.get("in_grid") else tr("批发商都没有")
    return {"nogrid_noq": tr("非 Griglia 商品 · 批发商都没有"), "nogrid": tr("非 Griglia 商品 · 批发商有")}.get(k, "")


def _edited(x):
    """手动改过确定箱数：和系统建议比（系统算不了的和门店清单比）"""
    base = x.get("sbox") if x.get("sbox") is not None else x.get("lbox")
    return base is not None and x.get("fbox") is not None and x["fbox"] != base


def _issue_text(x):
    return {"nodb": tr("数据库没有"), "nopack": tr("没有箱规")}.get(_issue(x), "")


def _problem(x):
    """
    要单独核对的商品（不在主表格，在「⚠ 待核对」窗口）：按「在不在 Griglia × 批发商有没有」分三页，每个商品只在一页。
    数据库没有 / 没有箱规的（_issue）一定是批发商都没有的，放进对应的「批发商都没有」页，备注列写原因（用户 2026-11-01）。
    数据库没有但报价里找得到的（补上了箱规、件价）留在主表格。其他返回 None
    """
    no_q = x.get("in_quote") is False or _issue(x) is not None
    if x.get("in_grid") is False:                     # 不在 Griglia：多半是门店没注意发来的别的区域的商品
        return "nogrid_noq" if no_q else "nogrid"
    if no_q:
        return "noquote"                              # 在 Griglia（或没放 Griglia）、几家批发商报价里都没有：比价时会成为缺失
    return None


# 待核对窗口的标签页（2026-11-01 用户：两个「非 Griglia」页合成一页，单独一列「批发商」体现区别）
def PROB_TABS():
    return (("noquote", tr("在我们的 Griglia 里，但这周几家批发商的报价里都没有：比价时会缺失。可能批发商不做了、断货，或者要向别处订")),
            ("nogrid", tr("非 Griglia 商品（多半是门店发来的别的区域的商品）：「批发商」列 = 这周报价里有没有。有的比价能分出去，要不要订你决定；都没有的比价时会缺失")))


def _tab_of(kind):
    """_problem 的细分 → 待核对窗口的哪一页"""
    return "nogrid" if kind in ("nogrid", "nogrid_noq") else kind


def _report_rows(rows, store):
    """发给门店的报告里这家店的两段：① 非 Griglia 商品（下次别加）② Griglia 商品这周批发商都没有"""
    rs = [x for x in rows if x.get("store") == store and _problem(x)]
    key = lambda y: str(y.get("name") or "").lower()
    return (sorted([x for x in rs if _tab_of(_problem(x)) == "nogrid"], key=key),
            sorted([x for x in rs if _problem(x) == "noquote"], key=key))


def report_name(store, when=None):
    """发给门店的报告文件名（意大利文）：Articoli_da_verificare_FOIANO_0111.xlsx"""
    return f"Articoli_da_verificare_{R.STORES.get(store, store)}_{(when or datetime.now()):%d%m}.xlsx"


def write_problem_report(rows, path, store=None, url=None, when=None):
    """
    发给门店的意大利文报告（2026-11-01 用户：格式漂亮、不要无关的数据、用 IMAGE 函数读我们网站的图片、文件要小）。
    一家店一个工作表（store = None：清单里每家有待核对商品的店各一页），每页：
      标题（门店 · 日期）→ ① Articoli non in Griglia – da non inserire nei prossimi ordini（照片 EAN 编号 名称 要的箱数 订的箱数 批发商有没有）
                         → ② Articoli Griglia non disponibili dai grossisti questa settimana（照片 EAN 编号 名称 要的箱数）
    照片 = Excel 公式 =IMAGE("https://img.casaete.top/prodotti/<EAN>/01.webp")（Excel 365 打开时自己从网上读，文件里不放图片，很小）。
    这家店没有待核对的商品返回 None
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    import product_images as PI
    url = url or PI.DEFAULT_URL
    when = when or datetime.now()
    stores = [store] if store is not None else sorted({x.get("store") for x in rows if _problem(x)}, key=lambda v: v or 0)
    wb = Workbook()
    wb.remove(wb.active)
    INK, MUTED, LINE = "1F2A37", "6B7280", "E5E7EB"
    thin = Side(style="thin", color=LINE)
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="center", wrap_text=True)
    heads = ["Foto", "EAN", "Cod. Art.", "Descrizione", "Colli richiesti", "Colli ordinati", "Grossisti"]
    widths = (13, 16, 10, 46, 11, 11, 12)
    ncol = len(heads)
    sections = (("① Articoli non in Griglia – da non inserire nei prossimi ordini", "FDECE2", "B4471A", True),
                ("② Articoli Griglia non disponibili dai grossisti questa settimana", "E3EEFB", "1F5FA8", False))
    for sid in stores:
        parts = _report_rows(rows, sid)
        if not any(parts):
            continue
        ws = wb.create_sheet(str(R.STORES.get(sid, sid))[:31])
        ws.sheet_view.showGridLines = False
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        last = get_column_letter(ncol)
        ws.merge_cells(f"A1:{last}1")
        ws["A1"] = f"Articoli da verificare – {R.STORES.get(sid, sid)}"
        ws["A1"].font = Font(size=16, bold=True, color="FFFFFF")
        ws["A1"].fill = PatternFill("solid", fgColor="1F3A5F")
        ws["A1"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[1].height = 32
        ws.merge_cells(f"A2:{last}2")
        ws["A2"] = f"CASA&TE · {when:%d/%m/%Y} · {len(parts[0]) + len(parts[1])} articoli"
        ws["A2"].font = Font(size=10, italic=True, color=MUTED)
        ws["A2"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[2].height = 20
        r = 4
        for (title, fill, ink, full), xs in zip(sections, parts):
            if not xs:
                continue
            cols = ncol if full else ncol - 2          # ② 不放「订的箱数」「批发商」（都是 0 / 都没有）
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncol)
            c = ws.cell(r, 1, f"{title}  ({len(xs)})")
            c.font = Font(size=12, bold=True, color=ink)
            c.fill = PatternFill("solid", fgColor=fill)
            c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
            ws.row_dimensions[r].height = 24
            r += 1
            for j, h in enumerate(heads[:cols], 1):
                hc = ws.cell(r, j, h)
                hc.font = Font(size=10, bold=True, color=INK)
                hc.fill = PatternFill("solid", fgColor="F3F4F6")
                hc.alignment = center
                hc.border = box
            ws.row_dimensions[r].height = 20
            r += 1
            for k, x in enumerate(xs):
                ean = PI.norm_ean(x.get("ean"))
                vals = [f'=_xlfn.IMAGE("{url.format(ean=ean)}")' if ean else "", ean or str(x.get("ean") or ""),
                        str(x.get("code") or ""), str(x.get("name") or ""), int(x.get("lbox") or 0), int(x.get("fbox") or 0),
                        "No" if _problem(x) == "nogrid_noq" else "Sì"]
                zebra = PatternFill("solid", fgColor="FAFBFC") if k % 2 else None
                for j, v in enumerate(vals[:cols], 1):
                    dc = ws.cell(r, j, v)
                    dc.border = box
                    dc.alignment = left if j == 4 else center
                    dc.font = Font(size=10, color=INK, bold=(j == 4))
                    if zebra is not None:
                        dc.fill = zebra
                if full:
                    g = ws.cell(r, 7)
                    g.font = Font(size=10, bold=True, color="B42318" if g.value == "No" else "2E7D32")
                ws.row_dimensions[r].height = 60          # 照片那一格的高度（IMAGE 按格子大小缩放）
                r += 1
            r += 1
        foot = ws.cell(r, 1, "Foto: si caricano da internet in Excel 365. Ordinare questi articoli solo se indicati in Griglia.")
        foot.font = Font(size=9, italic=True, color=MUTED)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncol)
        ws.page_setup.orientation = "portrait"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.print_options.horizontalCentered = True
        ws.page_margins.left = ws.page_margins.right = 0.4
    if not wb.sheetnames:
        return None
    wb.save(path)
    return path


def _can_transfer(x):
    """能算调货的：数据库里有（有其他门店的库存、销量）而且知道每箱几件"""
    return bool(x.get("in_db", True)) and _pk(x) > 0


def _set_box(x, boxes):
    """确定箱数 → fbox；final = 件数（箱数 × 每箱件数；没有箱规的就是箱数）"""
    b = max(0, int(boxes or 0))
    x["fbox"] = b
    pk = _pk(x)
    x["final"] = int(round(b * pk)) if pk > 0 else b


def is_new_item(x):
    """
    新品保护：180 天里从没卖过，或者 180 天的销量全是最近 60 天卖的（两个月前还没开始卖）→ 新品 / 刚补上货。
    这种商品销量数据还说明不了什么，按门店清单订
    """
    s = x.get("sales") or []
    if len(s) < 4 or not all(isinstance(v, (int, float)) for v in s[:4]):
        return False
    return float(s[3]) <= 0 or float(s[3]) - float(s[1]) <= 0


REVIEW_DEFAULTS = {"max_cut": 0.5, "free_cut_cycles": 2.0}


def review_rules(cfg=None):
    """检查清单的两条限制（批发商比价「设置」里改）：少订最多砍掉清单的几成；库存够卖几个订货周期以上才可以砍得更多 / 不订"""
    c = dict(REVIEW_DEFAULTS)
    for k in c:
        try:
            if cfg and cfg.get(k) is not None:
                c[k] = float(cfg[k])
        except (TypeError, ValueError):
            pass
    return c


def _cover_now(x):
    """现在的库存够卖几天（负库存当 0；没销量但有库存 = 无限；都没有 = 0）"""
    d = x.get("daily0") if x.get("daily0") is not None else x.get("daily")
    d = float(d or 0)
    inv = max(0.0, float(x.get("inv0") or 0))
    if d <= 0:
        return float("inf") if inv > 0 else 0.0
    return inv / d


PHANTOM_MIN_DAYS = 7          # 至少这么多天没卖才算（周末、关门几天很正常）
PHANTOM_UNITS = 3.0          # 按平时的速度，这段时间本该卖出 3 件以上（真有货却一件不卖的概率 ≈ e^-3 ≈ 5%）


def censored_info(x):
    """
    断货补算：门店点名要、库存只剩一点（≤ 2 件或不到一个月的量）、最近 ≥ 2 周一件没卖，
    而过去一年明明在卖 —— 最近的 0 多半是因为货架上没货（卖不了），不是卖不动了。
    这时加权日均被这几周的 0 拉得很低（月销 0.2、够卖 182 天），会被错判成「不用订」→ 按过去一年的平均日销算。
    返回 {"long": 一年平均日销, "weeks": 连续几周没卖, "orig": 原来的日均} 或 None
    """
    if not x.get("in_db", True) or float(x.get("file_qty") or 0) <= 0:
        return None
    long_ = float(x.get("long_daily") or 0)
    weeks = int(x.get("zero_weeks") or 0)
    orig = float((x.get("daily0") if x.get("daily0") is not None else x.get("daily")) or 0)
    inv = max(0.0, float(x.get("inv0") or 0))
    if long_ <= 0 or weeks < 2 or orig >= long_ or inv > max(2.0, long_ * 30):
        return None
    return {"long": long_, "weeks": weeks, "orig": orig}


def phantom_info(x, today=None):
    """
    虚库存（账面有货、货架上没货）：门店点名要、账面还有货，但按平时的卖法早该卖出几件了，却一件都没卖。
    返回 {"days": 几天没卖, "expect": 这段时间本该卖几件} 或 None。
    标准：没卖的天数 ≥ max(7 天, 3 件 ÷ 日均) —— 真有货的话这么久一件不卖的可能只有 5% 左右；
      快销品几天不卖就可疑，慢销品要很久不卖才算，不会误判。
    """
    from datetime import date, datetime as _dt
    if not x.get("in_db", True) or float(x.get("file_qty") or 0) <= 0:
        return None
    inv = float(x.get("inv0") or 0)
    d = x.get("daily0") if x.get("daily0") is not None else x.get("daily")
    d = max(float(d or 0), float(x.get("long_daily") or 0))     # 用一年平均：断货时加权日均被拉低
    ls = x.get("last_sale")
    if inv <= 0 or d <= 0 or not ls:
        return None
    try:
        last = ls if isinstance(ls, date) else _dt.strptime(str(ls)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    days = ((today or date.today()) - last).days
    if days >= max(PHANTOM_MIN_DAYS, PHANTOM_UNITS / d):
        return {"days": days, "expect": d * days}
    return None


def init_boxes(rows, units=None, algo=None, rules=None):
    """
    检查结果按箱算（2026-10-25 用户定）：门店清单的数量一律是箱（lbox）；
      sys_box 系统算出来的箱数；sbox 建议箱数 = 系统算的，但**不多于门店清单**（先不做多订）；
      系统算出来要订（哪怕不到一箱）就至少 1 箱，不因为凑不成整箱砍成 0；
      新品（is_new_item）按门店清单订（protect = "new"）；fbox 确定箱数默认 = 建议；数据库没有 / 没有箱规的 = 清单。
    units 不再用（以前分箱 / 件）；algo：新算法参数（凑整箱用 forecast.to_packs）
    2026-10-25 第二次（用户定）：
      A 类保护：A 类商品、库存不够卖一个订货周期、门店要了 → 至少 1 箱（protect = "a_low"）；
      少订有上限：清单要的数量有它的原因（促销、陈列、门店知道的情况），系统最多砍掉清单的 max_cut（默认一半），
        除非现在的库存已经够卖 free_cut_cycles 个订货周期以上（库存明显多）才可以砍得更多 / 不订（limited = True 表示被这条拦住了）
    """
    import forecast as F
    v2 = bool(algo) and algo.get("engine") == "v2"
    rr = review_rules(rules)
    for x in rows:
        pk = _pk(x)
        for k in ("daily0", "daily"):                  # 断货补算改过日均的：重新整理前先还原
            if k + "_orig" in x:
                x[k] = x.pop(k + "_orig")
        x["lbox"] = int(math.ceil(float(x.get("file_qty") or 0) - 1e-9))
        x.pop("protect", None)
        if not (x.get("in_db", True) and pk > 0):
            x["sbox"] = x["sys_box"] = None
            _set_box(x, x["lbox"])
            continue
        sugg = float(x.get("suggested") or 0)
        if v2 and x.get("daily") is not None:
            sys_ = F.to_packs(sugg, pk, float(x.get("inv0") or 0), float(x.get("daily") or 0), algo,
                              tier=x.get("tier") or "C", cycle=x.get("cycle"))
        else:
            sys_ = int(math.ceil(sugg / pk - 1e-9))
        if sugg > 0 and sys_ <= 0:
            sys_ = 1                                   # 系统也说要订：至少 1 箱
        if sys_ <= 0 and float(x.get("inv0") or 0) <= 0 and x["lbox"] > 0:
            sys_ = 1                                   # 店里已经没货、门店又点名要：至少 1 箱（缺货保护）
            x["protect"] = "empty"
        cyc = float(x.get("cycle") or 30) or 30.0
        # 断货补算：日均按过去一年平均（原来的记在 *_orig，循环开头已经还原）
        cz = censored_info(x)
        x["censored"] = cz
        if cz:
            for k in ("daily0", "daily"):
                x[k + "_orig"] = x.get(k)
                x[k] = cz["long"]
            need = cz["long"] * cyc - max(0.0, float(x.get("inv0") or 0))
            if need > 0:
                sys_ = max(sys_, int(math.ceil(need / pk - 1e-9)), 1)
        cover = _cover_now(x)
        ph = phantom_info(x)
        x["phantom"] = ph
        tier = (x.get("tier") or "")
        if ph and tier in ("A", "B"):
            # 虚库存：账面的货多半已经没了 → 库存当 0 重新算这一个周期要几箱（A 类直接相信门店清单）
            d = float((x.get("daily0") if x.get("daily0") is not None else x.get("daily")) or 0)
            need = max(1, int(math.ceil(d * cyc / pk - 1e-9)))
            sys_ = max(sys_, x["lbox"] if tier == "A" else need)
            x["protect"] = "phantom"
            cover = 0.0                                # 少订上限也按没货算
        if sys_ <= 0 and x["lbox"] > 0 and (x.get("tier") or "") == "A" and cover < cyc:
            sys_ = 1                                   # A 类、库存不够卖一个周期、门店要：至少 1 箱
            x["protect"] = "a_low"
        x["sys_box"] = sys_
        x.pop("limited", None)
        if is_new_item(x):
            x["protect"] = "new"
            x["sbox"] = x["lbox"]
        else:
            sb = min(sys_, x["lbox"])                  # 先不多订：最多订到门店清单的箱数
            floor = int(math.ceil(x["lbox"] * (1 - rr["max_cut"]) - 1e-9))
            if sb < floor and cover < rr["free_cut_cycles"] * cyc:
                sb = floor                             # 少订不超过上限（库存不是明显多）
                x["limited"] = True
            x["sbox"] = sb
        _set_box(x, x["sbox"])
AFTER_COL = _col(20)          # 确定订后够卖（跟着确定件数变）
LIST_AFTER_COL = _col(21)     # 清单订后够卖（按门店清单原来的数量订）


def _cover(x, pieces=0):
    """
    够卖几天（和门店配货同一个算法 store_orders.cover_days）：(库存 + pieces) ÷ 日均。
    账面库存是负数时按 0 算（负库存不代表欠了多少天的货）；数据库里没有的商品返回 None
    """
    if not x.get("in_db") or x.get("daily0") is None:
        return None
    import store_orders as SO
    return SO.cover_days({"inv": x.get("inv0") or 0, "daily": x["daily0"]}, pieces)


def _days(v):
    import store_orders as SO
    return SO.days_text(v)
VERDICT_TOKEN = {"transfer": "ok", "more": "accent", "less": "warn", "none": "err", "same": "muted", "nodb": "err"}
# 「看哪些」下拉框
VIEWS = (("all", "全部商品"), ("changed", "只看有变化的"), ("transfer", "只看调货"), ("more", "只看多订"),
         ("less", "只看少订"), ("none", "只看不订"), ("phantom", "只看库存可能不准"),
         ("promo", "只看促销"))


def promo_text(p):
    """「会员价 €4.99（标价 €6.49，−23%）· 到 31/12/2026」"""
    s = tr("{0} €{1:.2f}（标价 €{2:.2f}，−{3:.0%}）", tr(p.get("label") or "促销"), p["price"], p["list"], p["pct"])
    if p.get("end"):
        s += " · " + tr("到 {0}", p["end"].strftime("%d/%m/%Y"))
    return s


class _Sig(QObject):
    done = Signal(object)
    months = Signal(object)          # 近两年每月销量读好了（后台线程 → 界面）
    promos = Signal(object)          # 本店在促销的商品读好了


class OrderCheck:
    def __init__(self, win, settings, ui):
        self.win, self.settings, self.ui = win, settings, ui
        self.cfg = settings.setdefault("reorder", {})
        self.rows = []
        self.meta = {}
        self._moves = []
        self.lists = []           # 放进来的清单：[{"path", "info", "store", "unit"}]，一家门店一份
        self.running = False
        self._filling = False
        self.sig = _Sig()
        self.sig.done.connect(self._done)
        self.sig.months.connect(self._months_arrived)
        self.sig.promos.connect(self._promos_arrived)
        self._card = None                # 商品详情窗口（双击商品打开，一个窗口反复用）
        self._m24, self._m24_keys, self._m24_done = {}, [], set()

    def build(self):
        ui = self.ui
        p = ui.Panel(tr("② 检查门店订货清单"))
        p.setToolTip(tr("比价用的主订单只从这里生成：把一家或几家门店的订货清单一起拖进来，按销量和数据库库存检查数量（增加 / 减少 / 不订），同时查五家门店的库存。确认时先用别的门店多出来的货（门店间调货），每家店剩下要订的各自生成主订单；再一家一家点下面的「开始处理」做供应商比价。"))

        r1 = QHBoxLayout()
        r1.addWidget(QLabel(tr("清单")))
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        self.file_edit.setPlaceholderText(tr("拖入门店清单"))
        self.file_edit.setToolTip(tr("几家门店的清单一起检查，才能先在门店之间调货、再订剩下的（和门店配货一样）"))
        from erp_widgets import accept_file_drops
        accept_file_drops(self.file_edit, self._dropped, (".xlsx", ".xlsm"), multi=True)
        r1.addWidget(self.file_edit, 1)
        b2 = QPushButton(tr("选择文件…"))
        b2.clicked.connect(self.choose_file)
        r1.addWidget(b2)
        b_clr = QPushButton(tr("清空"))
        b_clr.clicked.connect(self.clear_lists)
        r1.addWidget(b_clr)
        p.lay.addLayout(r1)
        # 放进来的清单：每份一行，门店按文件名认，认不出来的自己选
        self.list_table = ui.make_table([tr("门店"), tr("清单文件"), tr("商品数"), ""], stretch_col=1,
                                        numeric_cols=(2,))
        self.list_table.horizontalHeaderItem(2).setToolTip(tr("门店清单的数量一律按箱"))
        self.list_table.verticalHeader().setDefaultSectionSize(36)
        self.list_table.setColumnWidth(0, 170)
        self.list_table.setColumnWidth(2, 70)
        self.list_table.setColumnWidth(3, 70)
        self.list_table.setMaximumHeight(36 * 5 + 34)
        self.list_table.hide()
        p.lay.addWidget(self.list_table)
        # 现在的主订单（上一次在这里确认生成的）
        r0 = QHBoxLayout()
        self.order_lbl = QLabel("")
        self.order_lbl.setWordWrap(True)
        r0.addWidget(self.order_lbl, 1)
        self.btn_open_order = QPushButton(tr("打开主订单"))
        self.btn_open_order.setObjectName("Link")
        self.btn_open_order.clicked.connect(lambda: self.win.open_main_order())
        r0.addWidget(self.btn_open_order)
        p.lay.addLayout(r0)

        # 检查用的参数（订货周期、销量周期、算法、数据库）：批发商比价页的「设置」窗口里改
        self.params_box = QWidget()
        pv = QVBoxLayout(self.params_box)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(8)
        r2 = QHBoxLayout()
        r2.setSpacing(10)
        r2.addWidget(QLabel(tr("订货周期")))
        self.cycle = QComboBox()
        for d in R.REORDER_CYCLE_OPTIONS:
            self.cycle.addItem(tr("{0} 天", d), d)
        self.cycle.setCurrentIndex(max(0, self.cycle.findData(int(self.cfg.get("cycle", 30)))))
        r2.addWidget(self.cycle)
        self.use_cfg = ui.Toggle(tr("按配置文件周期"), bool(self.cfg.get("use_supplier_cycles", True)))
        self.use_cfg.setToolTip(tr("打开：配置文件里单独设了订货周期的供应商用它自己的周期，其余用默认周期（新算法 = 算法设置里的）。\n关闭：全部用默认周期。"))
        r2.addWidget(self.use_cfg)
        r2.addSpacing(12)
        r2.addWidget(QLabel(tr("销量周期（天）")))
        self.days = []
        for d in self.cfg.get("days") or R.DEFAULT_SALES_DAYS:
            sb = QSpinBox()
            sb.setRange(7, 365)
            sb.setValue(int(d))
            sb.setFixedWidth(76)
            r2.addWidget(sb)
            self.days.append(sb)
        r2.addStretch()
        pv.addLayout(r2)
        r3 = QHBoxLayout()
        r3.setSpacing(10)
        r3.addWidget(QLabel(tr("算法")))
        algo_row, self.engine, self.algo_info = algo_widgets(self, ui)
        self.algo_info.setWordWrap(True)
        r3.addLayout(algo_row, 1)
        dbb = QPushButton(tr("数据库设置…"))
        dbb.clicked.connect(self.db_settings)
        r3.addWidget(dbb)
        self.btn_run = QPushButton(tr("检查数量"))
        self.btn_run.setObjectName("Primary")
        self.btn_run.clicked.connect(self.start)
        r3.addWidget(self.btn_run)
        self.btn_run.hide()                  # 页面上的「开始」按钮代替它
        pv.addLayout(r3)
        # 少订的上限（2026-10-25 用户定：清单数量有它的原因，系统不要改得太狠）
        from erp_widgets import tip_label
        from PySide6.QtWidgets import QDoubleSpinBox
        rr = review_rules(self.cfg)
        r4 = QHBoxLayout()
        r4.setSpacing(10)
        r4.addWidget(tip_label(tr("少订最多"), tr("系统建议比门店清单少订时，最多砍掉清单的几成（默认 50%：清单 4 箱最少订 2 箱）。门店报的数量有它的原因（促销、陈列等），改得太狠反而不好。下次检查生效")))
        self.max_cut = QSpinBox()
        self.max_cut.setRange(0, 100)
        self.max_cut.setSingleStep(10)
        self.max_cut.setSuffix(" %")
        self.max_cut.setValue(int(round(rr["max_cut"] * 100)))
        self.max_cut.valueChanged.connect(lambda v: (self.cfg.__setitem__("max_cut", v / 100.0), save_settings(self.settings)))
        r4.addWidget(self.max_cut)
        r4.addSpacing(12)
        r4.addWidget(tip_label(tr("库存多可不订"), tr("现在的库存够卖这么多个订货周期以上，才可以不受上面的限制（少订更多或不订）。默认 2 个周期。下次检查生效")))
        self.free_cut = QDoubleSpinBox()
        self.free_cut.setRange(0.5, 12)
        self.free_cut.setSingleStep(0.5)
        self.free_cut.setDecimals(1)
        self.free_cut.setSuffix(" " + tr("个周期"))
        self.free_cut.setValue(rr["free_cut_cycles"])
        self.free_cut.valueChanged.connect(lambda v: (self.cfg.__setitem__("free_cut_cycles", float(v)), save_settings(self.settings)))
        r4.addWidget(self.free_cut)
        r4.addStretch()
        pv.addLayout(r4)
        p.lay.addWidget(self.params_box)
        self.msg = QLabel("")
        self.msg.setObjectName("Muted")
        self.msg.setWordWrap(True)
        p.lay.addWidget(self.msg)

        # 检查结果在独立窗口里（表格大，不挤在页面上）
        from erp_widgets import ToolWindow
        self.window = ToolWindow(self.win, tr("检查门店订货清单"), (1360, 820))
        self.box = QWidget()
        self.box.setObjectName("Dialog")
        bl = QVBoxLayout(self.box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        # ---- 工具条（一行，和门店配货的订货明细一样）----
        # 门店（按钮）| 搜索 | 看哪些（下拉，带个数）……  恢复建议值 | 更多 ▾（用清单数量、显示哪些列、列顺序、导出）
        bar = QFrame()
        bar.setObjectName("TopBar")
        tb = QHBoxLayout(bar)
        tb.setContentsMargins(16, 8, 16, 8)
        tb.setSpacing(8)
        self.chip_lay = QHBoxLayout()          # 门店按钮：检查完按这次的门店建
        self.chip_lay.setSpacing(8)
        tb.addLayout(self.chip_lay)
        self.store_group = QButtonGroup(bar)
        self.store_group.setExclusive(True)
        self.store_group.buttonClicked.connect(lambda _b: self._store_changed())
        tb.addSpacing(6)
        # 供应商（数据库登记的）：和订货明细的供应商下拉框一样
        self.f_sup = QComboBox()
        self.f_sup.setMinimumWidth(130)
        self.f_sup.setMaximumWidth(240)
        self.f_sup.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.f_sup.setMinimumContentsLength(10)
        self.f_sup.addItem(tr("全部供应商"), None)
        self.f_sup.currentIndexChanged.connect(self._filter)
        tb.addWidget(self.f_sup)
        self.flt = QLineEdit()
        self.flt.setPlaceholderText(tr("编号、条码或名称"))
        self.flt.setMinimumWidth(90)
        self.flt.setMaximumWidth(200)
        self.flt.textChanged.connect(self._filter)
        tb.addWidget(self.flt)
        self.view = QComboBox()
        for key, text in VIEWS:
            self.view.addItem(tr(text), key)
        self.view.setToolTip(tr("有变化 = 判断是多订、少订、不订或建议调货"))
        self.view.currentIndexChanged.connect(self._filter)
        tb.addWidget(self.view)
        # 「看哪些」右边：一个「⚠ 待核对」按钮（有才显示），点开是一个窗口，按问题分标签页（用户：按钮别排一长串、名字要好懂）
        from PySide6.QtWidgets import QTabWidget
        self.prob = {}
        self._prob_filling = False
        self.prob_btn = QPushButton()
        self.prob_btn.setStyleSheet(f"color:{ui.T('warn')};")
        self.prob_btn.clicked.connect(lambda: self._open_prob(None))
        self.prob_btn.hide()
        tb.addWidget(self.prob_btn)
        dlg = QDialog(self.window)
        dlg.setWindowTitle(tr("待核对的商品"))
        dlg.resize(980, 560)
        dv = QVBoxLayout(dlg)
        dv.setContentsMargins(16, 14, 16, 12)
        dv.setSpacing(10)
        self.prob_tabs = QTabWidget()
        dv.addWidget(self.prob_tabs, 1)
        close = QPushButton(tr("关闭"))
        close.clicked.connect(dlg.accept)
        exp = QPushButton(tr("导出门店报告") + " ▾")
        exp.setToolTip(tr("意大利文报告，可以直接发给门店（带商品照片，Excel 365 打开时从网上读）。确认生成主订单时，每家店的比价文件夹里也会自动生成一份"))
        em = QMenu(exp)                               # 两种导出放在一个按钮的下拉里（用户：每店一个 / 合成一个都要）
        em.addAction(tr("每家店一个文件"), self._export_prob)
        em.addAction(tr("合成一个文件（每店一页）"), self._export_prob_one)
        exp.setMenu(em)
        cb = QHBoxLayout()
        cb.addWidget(exp)
        cb.addStretch()
        cb.addWidget(close)
        dv.addLayout(cb)
        self.prob_dlg = dlg
        self._sum_timer = QTimer(dlg)                 # 改了箱数：合计等停手 0.3 秒再算（连着改不卡）
        self._sum_timer.setSingleShot(True)
        self._sum_timer.setInterval(300)
        self._sum_timer.timeout.connect(self._summary)
        for kind, tip in PROB_TABS():
            page = QWidget()
            pv = QVBoxLayout(page)
            pv.setContentsMargins(0, 8, 0, 0)
            pv.setSpacing(8)
            t = ui.make_table([tr("门店"), tr("条码"), tr("名称"), tr("供应商"), tr("批发商"), tr("清单箱数"), tr("确定箱数") + " ✎"],
                              stretch_col=2, numeric_cols=(5, 6))
            t.verticalHeader().setDefaultSectionSize(30)
            t.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                              QAbstractItemView.AnyKeyPressed)
            for c, wd in ((0, 90), (1, 130), (3, 170), (4, 86), (5, 76), (6, 90)):
                t.setColumnWidth(c, wd)
            t.horizontalHeaderItem(4).setToolTip(tr("这周几家批发商的报价里有没有这个商品：都没有 = 比价时会缺失"))
            t.setColumnHidden(4, kind == "noquote")       # Griglia 那页全是「都没有」，不用这一列
            t.itemChanged.connect(lambda it, k=kind: self._prob_edited(k, it))
            from erp_widgets import HeaderFilter      # 右键表头按列筛选
            t._hf = HeaderFilter(t)
            pv.addWidget(t, 1)
            if True:
                bb = QHBoxLayout()
                b_none = QPushButton(tr("全部不订"))
                b_none.setToolTip(tr("这一页的商品确定箱数都改成 0"))
                b_none.clicked.connect(lambda _=False, k=kind: self._nogrid_none(k))
                bb.addWidget(b_none)
                bb.addStretch()
                pv.addLayout(bb)
            idx = self.prob_tabs.addTab(page, "")
            self.prob_tabs.setTabToolTip(idx, tip)
            self.prob[kind] = (idx, page, t)
        tb.addStretch()
        b_s = QPushButton(tr("恢复建议值"))
        b_s.setToolTip(tr("全部商品的确定件数恢复成系统建议的（人工改的调货也恢复）"))
        b_s.clicked.connect(lambda: self._set_all("sugg"))
        tb.addWidget(b_s)
        more = QPushButton(tr("更多") + " ▾")
        more.setToolTip(tr("全部用清单数量、显示哪些列、导出 Excel"))
        more.clicked.connect(lambda: self._more_menu(more))
        tb.addWidget(more)
        bl.addWidget(bar)
        self.table = ui.make_table(_perm([tr("判断"), tr("商品编号"), tr("条码"), tr("商品"), tr("库存"),
                                          "30", "60", "90", "180", tr("现在够卖"), tr("每箱"), tr("清单数量"),
                                          tr("清单件数"), tr("建议件数"), tr("确定件数"), tr("确定箱数"), tr("等级"), tr("周期"), tr("日均"),
                                          tr("目标库存"), tr("订后够卖"), tr("清单订后够卖（天）"), tr("门店"),
                                          tr("变化（箱）"), tr("调货"), tr("原因"), "Codice Fornitore", tr("供应商"), tr("月销"),
                                          tr("金额 €"), tr("情况")]),
                                   numeric_cols=[_col(k) for k in list(range(4, 16)) + [17, 18, 19, 20, 21, 23, 28, 29]])
        self.table.setMinimumHeight(380)
        import product_images as PI
        self.table.verticalHeader().setDefaultSectionSize(PI.row_height(self.settings))
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                                   QAbstractItemView.AnyKeyPressed)
        self.table.setFocusPolicy(Qt.StrongFocus)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(False)
        import product_images as PI          # 「商品」那一格：左边商品图（条码在藏起来的条码列），右边名称
        self.table.setItemDelegateForColumn(_col(3), PI.ThumbDelegate(self.table, _col(2), None, self.settings))
        for c, wd in enumerate(_perm((96, 80, 130, PI.thumb_size(self.settings) + 300, 60, 52, 52, 52, 52, 76, 52, 56, 70, 70, 76,
                                      70, 50, 50, 60, 70, 76, 120, 80, 76, 110, 380, 110, 120, 60, 76, 180))):
            self.table.setColumnWidth(c, wd)
        self.table.horizontalHeaderItem(_col(28)).setToolTip(tr("月销：一个月大约卖几件（新算法 = 加权日均 × 30；原程序算法 = 最长周期销量 ÷ 月数）"))
        self.table.horizontalHeaderItem(_col(4)).setToolTip(tr("本店现在的库存（件）"))
        self.table.horizontalHeaderItem(_col(10)).setText(tr("每箱"))
        self.table.horizontalHeaderItem(AMOUNT_COL).setToolTip(tr("金额 = 确定箱数 × 每箱件数 × 件价（有调货的按调货以后的箱数）"))
        self.table.horizontalHeaderItem(EDIT_COL).setToolTip(tr("确定订几箱：双击修改，0 = 不订"))
        self.table.horizontalHeaderItem(EDIT_COL).setText(tr("确定") + " ✎")
        self.table.horizontalHeaderItem(_col(10)).setToolTip(
            tr("每箱件数：按 distribuire 里最便宜那家报价的箱规（比价默认挑它）；报价里没有的用数据库箱规。蓝色 = 和数据库不一样"))
        self.table.horizontalHeaderItem(_col(9)).setToolTip(
            tr("现在够卖（天）= 本店库存 ÷ 日均（账面库存是负数时按 0 算）"))
        self.table.horizontalHeaderItem(AFTER_COL).setToolTip(
            tr("订后够卖（天）=（库存 + 订的 + 调进来的）÷ 日均，看订得合不合理就看它：红 = 不到一个订货周期，下次到货前会卖断；黄 = 超过凑单天数，可能压货。不订的显示现在的库存够卖几天"))
        self.table.horizontalHeaderItem(LIST_AFTER_COL).setToolTip(
            tr("清单订后够卖 =（本店库存 + 门店清单原来的件数）÷ 日均：门店自己报的量订完能卖几天，和「确定订后够卖」对比"))
        self.table.horizontalHeaderItem(_col(18)).setToolTip(
            tr("新算法：加权日均（含季节）；原程序算法：最近一个周期的销量 ÷ 天数"))
        self.table.horizontalHeaderItem(_col(CHANGE_COL)).setToolTip(
            tr("确定件数 − 门店清单的件数：+ 是比门店要的多订，− 是少订"))
        self.table.horizontalHeaderItem(_col(TRANSFER_COL)).setToolTip(
            tr("门店间调货（按现在的确定件数实时算）：从哪家门店调进几件，调进来的就少订。双击改件数，0 = 不调（比如过季品）"))
        self.table.horizontalHeaderItem(_col(REASON_COL)).setToolTip(
            tr("系统为什么这么建议：库存够卖几天、订货周期里大约卖多少"))
        self.table.itemChanged.connect(self._edited)
        # 点表头排序、右键表头按列筛选；上面「判断」「等级」两个下拉框快速筛选
        from erp_widgets import TableSortFilter, ColumnFilterBox
        self.ctl = TableSortFilter(self.table, lambda: len(self.rows), self._text, self._fill_row, key=self._key,
                                   hide=self._hidden, numeric=tuple(_col(k) for k in list(range(4, 16)) + [17, 18, 19, 20, 21, 23, 28, 29]))
        # 列可以拖动换位置、拖宽度，下次打开照旧（右键表头是筛选，所以「恢复默认列顺序」放在下面）
        from erp_widgets import remember_columns
        saved = self.cfg.get("check_columns")
        if isinstance(saved, list) and saved and SITU not in saved:     # 新加的「情况」列：插在判断后面，原来排好的不动
            saved.insert(saved.index(0) + 1 if 0 in saved else len(saved), SITU)
        self.table.horizontalHeaderItem(_col(SITU)).setToolTip(
            tr("待核对的商品属于哪一类：Griglia 商品批发商都没有 / 非 Griglia 商品批发商都没有 / 非 Griglia 商品批发商有；正常的空着。右键表头可以只看某一类"))
        if self.cfg.get("check_columns_ver") != _LAYOUT_VER:
            self.cfg.pop("check_widths", None)        # 第 9 版重新设计了：旧的列宽（名称列太窄等）也不用了
        self._reset_cols = remember_columns(self.table, self.cfg, "check", _ORDER, lambda: save_settings(self.settings),
                                            _LAYOUT_VER)
        # 「显示哪些列」：默认只放审核要看的，其他的勾选了才显示
        from erp_widgets import ColumnChooser
        self._forced = set()
        self.cols = ColumnChooser(self.table, self.cfg, "check_cols", _ORDER, lambda: save_settings(self.settings),
                                  _DEFAULT_HIDDEN, ver=_HIDDEN_VER, forced=lambda: self._forced)
        self._moves_in, self._moves_out = {}, {}
        self.transfer_ov = {}
        self.transfer_frozen = False
        self.table.cellDoubleClicked.connect(self._edit_transfer)     # 双击「调货」：选调出门店、填件数
        self.table.cellDoubleClicked.connect(self._open_card)         # 双击商品：商品详情窗口
        self.table.horizontalHeaderItem(_col(3)).setToolTip(tr("双击打开商品详情（入库记录、每天销量、各店库存）；名称红色 = 门店在促销"))
        self.table.horizontalHeaderItem(_col(TRANSFER_COL)).setText(tr("调货") + " ✎")
        self._plan_timer = QTimer()
        self._plan_timer.setSingleShot(True)
        self._plan_timer.setInterval(250)
        self._plan_timer.timeout.connect(self._replan_in_place)
        self.ctl.rendered.connect(lambda n, tot: self._status(n, tot))
        # 表格 + 右边详情栏（和门店配货的订货明细一样：选中一行看为什么）
        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        mid.addWidget(self.table, 1)
        mid.addWidget(self._build_drawer())
        bl.addLayout(mid, 1)
        self._cur_i = None
        self._by_key = {}
        self.table.currentCellChanged.connect(lambda r, c, pr, pc: self._select(r))
        # ---- 状态栏：显示几个 · 改了几个 · 金额 · 调货 ……  确认，开始比价 ----
        sb = QFrame()
        sb.setObjectName("StatusBar")
        sh = QHBoxLayout(sb)
        sh.setContentsMargins(16, 7, 16, 7)
        sh.setSpacing(12)
        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("StatusText")
        sh.addWidget(self.count_lbl, 1)
        self.btn_confirm = QPushButton(tr("确认，开始比价"))
        self.btn_confirm.setObjectName("Primary")
        self.btn_confirm.setToolTip(tr("按「确定件数」给每家门店生成主订单，然后自动一家一家比价"))
        self.btn_confirm.clicked.connect(self.confirm)
        sh.addWidget(self.btn_confirm)
        bl.addWidget(sb)
        self._shown = (0, 0)
        self.window.setCentralWidget(self.box)
        # 页面上只放一行摘要 + 打开窗口
        self.summary_row = QWidget()
        sr = QHBoxLayout(self.summary_row)
        sr.setContentsMargins(0, 0, 0, 0)
        self.page_sum = QLabel("")
        self.page_sum.setObjectName("Faint")
        self.page_sum.setWordWrap(True)
        sr.addWidget(self.page_sum, 1)
        ob = QPushButton(tr("打开检查窗口"))
        ob.setObjectName("Outline")
        ob.clicked.connect(self.window.bring_up)
        sr.addWidget(ob)
        cb = QPushButton(tr("确认，开始比价"))
        cb.setObjectName("Primary")
        cb.clicked.connect(self.confirm)
        sr.addWidget(cb)
        self.summary_row.hide()
        p.lay.addWidget(self.summary_row)
        return p

    # ---------- 选文件（一家或几家门店） ----------
    def choose_file(self):
        paths, _ = QFileDialog.getOpenFileNames(self.win, tr("选择订货清单（可以选好几个）"), self.cfg.get("last_dir") or "",
                                                tr("Excel 文件 (*.xlsx *.xlsm)"))
        if paths:
            self.cfg["last_dir"] = os.path.dirname(paths[0])
            self.add_files(paths)

    def _dropped(self, paths):
        """门店发来的清单直接拖进来（文件留在原来的地方，不复制）"""
        paths = paths if isinstance(paths, (list, tuple)) else [paths]
        self.cfg["last_dir"] = os.path.dirname(paths[0])
        n = self.add_files(paths)
        if n:
            self.win.show_toast(tr("已载入 {0} 份清单", n))

    def add_files(self, paths):
        """读清单、按文件名认门店；同一家门店再放一份就替换原来那份。返回放进来几份"""
        notes, n = [], 0
        for path in paths:
            name = os.path.basename(path)
            if any(os.path.normcase(os.path.abspath(x["path"])) == os.path.normcase(os.path.abspath(path)) for x in self.lists):
                continue
            try:
                info = R.parse_order_file(path)
            except Exception as e:
                notes.append(tr("✘ {0} 读不出来：{1}", name, e))
                continue
            f = info["fields"]
            unit = "colli"                      # 门店清单的数量一律是箱（2026-10-25 用户定）
            sid = R.guess_store(path)
            if sid:
                self.lists = [x for x in self.lists if x["store"] != sid]
            self.lists.append({"path": path, "info": info, "store": sid, "unit": unit})
            n += 1
            names = {"code": tr("商品编号"), "ean": tr("条码"), "name": tr("名称"), "qty": tr("数量")}
            line = tr("{0}：{1} 个商品，识别到 {2}", name, len(info["rows"]),
                      "　".join(f"{names[k]}=「{v}」" for k, v in f.items() if k in names))
            if not sid:
                line += "　" + tr("文件名里看不出是哪家门店，请在上面选")
            for w in info["warnings"]:
                line += "\n⚠ " + w
            notes.append(line)
        self._refresh_lists()
        self.msg.setText("\n".join(notes))
        return n

    def run_lists(self, items):
        """工作文件夹里的门店清单 [(门店编号, 路径)] 放进来，直接开始检查"""
        self.lists = []
        self.add_files([p for _, p in items])
        by_path = {os.path.normcase(os.path.abspath(p)): sid for sid, p in items}
        for x in self.lists:
            x["store"] = by_path.get(os.path.normcase(os.path.abspath(x["path"])), x["store"])
        self._refresh_lists()
        if not self.lists:
            return False
        self.start()
        return True

    def clear_lists(self):
        self.lists = []
        self._refresh_lists()
        self.msg.setText("")

    def _refresh_lists(self):
        t = self.list_table
        t.setRowCount(len(self.lists))
        stores = [k for k in R.STORES if k]
        for i, x in enumerate(self.lists):
            self.ui.table_set_row(t, i, ["", os.path.basename(x["path"]), str(len(x["info"]["rows"])), ""])
            t.item(i, 1).setToolTip(x["path"])
            cb = QComboBox()
            cb.addItem(tr("选择门店…"), None)
            for k in stores:
                cb.addItem(store_label(k), k)
            cb.setCurrentIndex(max(0, cb.findData(x["store"])))
            cb.currentIndexChanged.connect(lambda _, i=i, cb=cb: self._set_store(i, cb.currentData()))
            t.setCellWidget(i, 0, cb)
            rb = QPushButton(tr("移除"))
            rb.setObjectName("Link")
            rb.clicked.connect(lambda _=False, i=i: self._remove(i))
            t.setCellWidget(i, 3, rb)
        t.setVisible(bool(self.lists))
        names = [R.STORES.get(x["store"]) or "?" for x in self.lists]
        self.file_edit.setText(tr("{0} 份清单：{1}", len(self.lists), "、".join(names)) if self.lists else "")

    def _set_store(self, i, sid):
        if 0 <= i < len(self.lists):
            if sid and any(k != i and x["store"] == sid for k, x in enumerate(self.lists)):
                QMessageBox.information(self.win, tr("检查门店订货清单"),
                                        tr("{0} 已经有一份清单了。同一家门店只能放一份，请先移除另一份。", R.STORES.get(sid)))
                self._refresh_lists()
                return
            self.lists[i]["store"] = sid
            self._refresh_lists()

    def _remove(self, i):
        if 0 <= i < len(self.lists):
            self.lists.pop(i)
            self._refresh_lists()

    def folder_changed(self):
        """总文件夹变了：显示现在的主订单是哪家门店、什么时候生成的"""
        if not hasattr(self, "order_lbl"):
            return
        info = self.win.info or {}
        T = self.ui.T
        name = info.get("ordine")
        if name:
            root = self.win.folder_edit.text().strip()
            path = os.path.join(root, "ORDINE", name)
            snap = R.load_snapshot(path) or {}
            try:
                when = datetime.fromtimestamp(os.path.getmtime(path))
                when = f"{when:%d/%m %H:%M}"
            except OSError:
                when = ""
            self.order_lbl.setText(tr("✔ 现在的主订单：{0}（{1}，{2} 生成）。门店清单变了就重新检查、再确认一次，会替换它。",
                                      name, store_label(snap.get("store")) if snap.get("store") else "—", when))
            self.order_lbl.setStyleSheet(f"color:{T('ok')};")
        else:
            self.order_lbl.setText(info.get("need_order") or tr("还没有主订单。"))
            self.order_lbl.setStyleSheet(f"color:{T('warn') if info.get('ordine_old') else T('muted')};")
        self.btn_open_order.setVisible(bool(name))

    def db_settings(self):
        from reorder_page import DbDialog
        DbDialog(self.win, self.settings).exec()

    # ---------- 计算 ----------
    def start(self):
        if self.running:
            return
        if not self.lists:
            QMessageBox.information(self.win, tr("检查门店订货清单"), tr("先把门店发来的订货清单拖进来，或点「选择文件…」。"))
            return
        if any(not x["store"] for x in self.lists):
            QMessageBox.information(self.win, tr("检查门店订货清单"), tr("有清单还没选是哪家门店，请在清单列表里选。"))
            return
        cycle = self.cycle.currentData()
        use_cfg = self.use_cfg.isChecked()
        algo = self.current_algo()
        days = [sb.value() for sb in self.days]
        self.cfg.update(cycle=cycle, days=days, use_supplier_cycles=use_cfg)
        save_settings(self.settings)
        lists = [{"store": x["store"], "unit": x["unit"], "path": x["path"],
                  "rows": [dict(r) for r in x["info"]["rows"]]} for x in self.lists]
        dbs = self.settings.get("db") or None
        log = self.win.log
        price_dir = self._price_dir()
        self.running = True
        self.btn_run.setEnabled(False)
        self.msg.setText(tr("正在连接数据库并计算，商品多时要几十秒…"))
        names = "、".join(R.STORES.get(x["store"], "") for x in lists)
        from erp_widgets import TaskDialog
        dlg = TaskDialog(self.win, tr("正在检查订货清单"),
                         tr("{0} · {1} 个商品", names, sum(len(x["rows"]) for x in lists)),
                         [tr("读取清单、对上商品编号"), tr("查询门店销量与库存"), tr("查询五家门店库存（调货用）"),
                          tr("读取供应商报价（箱规、件价）"), tr("对比清单数量")], cancellable=False)
        dlg.went_background.connect(lambda: self.win.show_toast(tr("清单在后台检查，完成后会提醒")))
        dlg.show()

        def work():
            q_pool = None
            try:
                dlg.post_step(0, "running", "", "")
                dlg.post_progress(5)
                # 报价（Excel）和数据库同时读：读报价主要用电脑，查数据库主要在等服务器，一起做省时间
                from concurrent.futures import ThreadPoolExecutor
                q_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="quotes")
                t_q = time.time()
                q_fut = q_pool.submit(lambda: (self._read_quotes(price_dir, log), time.time()))   # 记下读完的时间
                db = R.make_db(dbs)
                need = [r["ean"] for x in lists for r in x["rows"] if not r["code"] and r["ean"]]
                if need:
                    m = R.codes_from_eans(db, need)
                    for x in lists:
                        for r in x["rows"]:
                            if not r["code"] and r["ean"] in m:
                                r["code"] = m[r["ean"]]
                union = []
                for x in lists:
                    x["codes"] = [r["code"] for r in x["rows"] if r["code"]]
                    union += [c for c in x["codes"] if c not in union]
                dlg.post_step(0, "done", f"{len(union)}", "")
                dlg.post_step(1, "running", "", "")
                t0 = time.time()
                ms = R.ping(db)
                if ms is not None:
                    log(tr("[检查清单] 数据库响应 {0:.0f} 毫秒", ms))
                try:
                    upto = R.sales_latest(db)
                    late = R.stale_days(upto)
                    if late is not None and late > R.STALE_DAYS:
                        log(tr("⚠ 数据库里最新的销售是 {0}，已经 {1} 天没同步：销量和库存都是那天的", upto.strftime("%d/%m/%Y %H:%M"), late))
                except Exception:
                    pass
                pre_snap = None
                # 供应商编号（只显示用）和下面的门店查询同时查，不用排在最后等（结果一样）
                sup_fut = q_pool.submit(R._timed, "供应商编号", None, R.supplier_info, db, union) if union else None
                others = [k for k in R.STORES if k != 0]
                if len(lists) == 1 and lists[0]["codes"] and union:
                    # 只有一份清单（平常都是）：这家店和其他门店（调货用）同时查，结果和一家家查一样
                    x0 = lists[0]
                    log(tr("[检查清单] 门店 {0}，{1} 个商品", R.STORES.get(x0["store"], x0["store"]), len(x0["codes"])))
                    every = [x0["store"]] + [k for k in others if k != x0["store"]]

                    def one(sid):
                        return R.compute_suggestions(db, sid, x0["codes"] if sid == x0["store"] else union, days, cycle,
                                                     None, use_supplier_cycles=use_cfg, algo=algo)
                    try:
                        dfs = R.per_store(every, one, lambda k, sid: (
                            dlg.post_step(1, "running", f"{k + 1} / {len(every)}", R.STORES.get(sid, "")),
                            dlg.post_progress(15 + 70 * k / max(1, len(every)))))
                        x0["df"] = dfs[x0["store"]]
                        x0["result"] = R.to_rows(x0["df"], x0["rows"], days)
                        pre_snap = {sid: R.stock_of(dfs[sid], days) for sid in others if sid in dfs}
                    except Exception as e:
                        x0.pop("df", None)
                        log(tr("⚠ 几家门店一起查没成功，改成一家家查：{0}", e))
                for k, x in enumerate(lists):
                    if x.get("df") is not None:
                        continue
                    sid = x["store"]
                    log(tr("[检查清单] 门店 {0}，{1} 个商品", R.STORES.get(sid, sid), len(x["codes"])))
                    dlg.post_step(1, "running", f"{k + 1} / {len(lists)}", R.STORES.get(sid, ""))
                    dlg.post_progress(15 + 40 * k / max(1, len(lists)))
                    x["df"], x["result"] = None, []
                    if x["codes"]:
                        x["df"] = R.compute_suggestions(db, sid, x["codes"], days, cycle, None, use_supplier_cycles=use_cfg,
                                                        algo=algo)
                        x["result"] = R.to_rows(x["df"], x["rows"], days)
                dlg.post_step(1, "done", f"{len(lists)} / {len(lists)}", "")
                # 五家门店这批商品（所有清单合起来）的库存和日均：先算门店间调货，比价时算够卖天数（查不到不影响检查）
                dlg.post_step(2, "running", "", "")
                dlg.post_progress(55)
                snap = pre_snap
                if union and snap is None:
                    try:
                        others = [k for k in R.STORES if k != 0]
                        one = lists[0] if len(lists) == 1 else None          # 只有一份清单：它自己那家不用再查
                        snap = R.stock_snapshot(
                            db, union, days, cycle, others, use_cfg, algo, own=one["store"] if one else None,
                            own_df=one["df"] if one else None,
                            on_store=lambda k, sid: (dlg.post_step(2, "running", f"{k + 1} / {len(others)}", R.STORES.get(sid, "")),
                                                     dlg.post_progress(55 + 30 * k / max(1, len(others)))))
                        dlg.post_step(2, "done", f"{len(others)} / {len(others)}", "")
                    except Exception as e:
                        log(tr("⚠ 其他门店库存没查到（不能算调货）：{0}", e))
                        dlg.post_step(2, "error", "", str(e))
                else:
                    dlg.post_step(2, "done", "", "")
                sup_info = sup_fut.result() if sup_fut is not None else {}     # Codice Fornitore + 供应商名（只显示用）
                log(tr("[检查清单] 数据库查询用了 {0:.0f} 秒", time.time() - t0))
                db.engine.dispose()
                rows = []
                for x in lists:
                    for r in R.check_rows(x["rows"], x["result"], x["unit"]):
                        r["store"] = x["store"]
                        rows.append(r)
                R.add_supplier_info(rows, sup_info)
                if rows and not any(r.get("in_db") for r in rows):
                    # 一个都没在数据库里找到：几乎一定是数据库刚才没连上或者清单不对，不能拿这个结果去生成主订单
                    raise RuntimeError(tr("清单里的 {0} 个商品在数据库里一个都没找到：可能是数据库刚才没连上，或者清单里的条码 / 商品编号不对。请检查数据库连接后再点一次「开始」。",
                                          len(rows)))
                # 箱规、件价用 distribuire 里最便宜那家的报价（比价默认挑它）；报价里没有的才用数据库的
                dlg.post_step(3, "running", "", "")
                dlg.post_progress(86)
                # Griglia：不在我们 DETERSIVI 总表里的商品单独列出来（工作文件夹「0 Griglia」里没有就不检查）
                try:
                    import griglia as G
                    grid = G.load_for(os.path.dirname(price_dir)) if price_dir else None
                except Exception as e:
                    grid = None
                    log(tr("⚠ Griglia 读不了：{0}", e))
                if grid:
                    for x in rows:
                        x["in_grid"] = G.contains(grid, x.get("ean"), x.get("code"))
                    log(tr("[检查清单] Griglia {0}：{1} 个商品不在里面", grid["name"],
                           sum(1 for x in rows if x.get("in_grid") is False)))
                q_files, q_end = q_fut.result()
                n_q, n_files = self._apply_quotes(rows, price_dir, log, files=q_files)
                if n_files:
                    log(tr("[检查清单] 读报价用了 {0:.0f} 秒（和数据库同时读）", q_end - t_q))
                dlg.post_step(3, "done", tr("{0} 份报价", n_files) if n_files else tr("没有报价，用数据库"), "")
                dlg.post_step(4, "running", "", "")
                dlg.post_progress(95)
                dlg.post_finished(True, tr("完成：{0} 个商品", len(rows)))
                self.sig.done.emit((rows, None, dict(stores=[x["store"] for x in lists], days=days, cycle=cycle,
                                                     units={x["store"]: x["unit"] for x in lists},
                                                     files={x["store"]: x["path"] for x in lists}, snap=snap,
                                                     quoted=n_q, quote_files=n_files,
                                                     store=lists[0]["store"])))
            except Exception as e:
                import traceback
                log(tr("❌ 检查清单失败：{0}", e))
                log(traceback.format_exc())
                dlg.post_finished(False, str(e))
                self.sig.done.emit((None, str(e), None))
            finally:
                if q_pool is not None:
                    q_pool.shutdown(wait=False)

        threading.Thread(target=work, daemon=True).start()

    def _algo(self):
        from reorder_page import algo_params
        return algo_params(self.settings)

    def _done(self, payload):
        rows, err, meta = payload
        self.running = False
        self.btn_run.setEnabled(True)
        if err:
            self.msg.setText(tr("计算失败"))
            self.win.notify(tr("检查清单失败：{0}", err))
            return
        init_boxes(rows, None, self._algo(), self.cfg)
        self.rows, self.meta = rows, meta
        # 人工改的调货 {(门店, 商品编号): {"from", "pieces", "auto"} 或 0 = 不调}；改过一次就冻结（别的行不再跟着变）
        self.transfer_ov = {}
        self.transfer_frozen = False
        self._by_key = {(x.get("store"), str(x.get("code"))): x for x in rows}
        self._cur_i = None
        d = meta["days"]
        for i in range(4):
            self.ctl.set_title(_col(5 + i), tr("{0}天", d[i]), tr("最近 {0} 天销量", d[i]))
        # 一律按箱看：清单箱数（清单按件的折成箱）→ 确定箱数
        self.ctl.set_title(_col(11), tr("清单"), tr("门店清单要的箱数"))
        self.ctl.sort_col, self.ctl.sort_desc = _col(0), False     # 要看的排前面，照清单订的在最后
        self._fill_prob()
        self._build_chips(meta["stores"])
        self._fill_suppliers()
        self._apply_forced()
        self.ctl.filters = {}
        self._replan(render=False)
        self._fill()
        self._load_months()                             # 详情栏的近两年每月销量：后台读
        try:                                            # 商品详情窗口：先在后台连好数据库，第一次双击就快
            from product_window import ProductWindow
            if self._card is None:
                self._card = ProductWindow(self.window, self.ui, self.settings)
            self._card._cache.clear()
            self._card.warm_up()
        except Exception:
            pass
        self.summary_row.show()
        self.window.setWindowTitle(tr("检查门店订货清单 — {0} · {1} 个商品",
                                      "、".join(store_label(k) for k in meta["stores"]), len(rows)))
        self.window.bring_up()
        self.msg.setText(tr("✔ {0}：{1} 个商品", "、".join(store_label(k) for k in meta["stores"]), len(rows)))

    def _vals(self, i):
        """数据行 i 每一列显示的文字（按屏幕上的列顺序）"""
        x = self.rows[i]
        sales = [f"{v:g}" if isinstance(v, (int, float)) else "" for v in x["sales"]]
        pack = x["pack"] or 0
        daily = x.get("daily") if x.get("daily") is not None else x.get("daily0")
        return _perm([" · ".join([self._verdict(x)[1]] + [m[1] for m in self._marks(x)]), x["code"], x["ean"], x["name"],
                      f"{x['store_inv']:g}" if isinstance(x["store_inv"], (int, float)) else "",
                      *sales, (_days(_cover(x)) or "0") if x.get("in_db", True) else "",    # 没销量也没库存 → 0
                      f"{x['pack']:g}" if x["pack"] else "",
                      f"{x['lbox']:,}", f"{x['file_pcs']:g}",
                      f"{x['suggested']:,}" if x["in_db"] else "", f"{x['final']:,}" if pack > 0 else "",
                      f"{x['fbox']:,}",
                      x.get("tier") or "",
                      f"{x['cycle']:g}" if isinstance(x.get("cycle"), (int, float)) else "",
                      (f"{daily:.2f}" if daily else "0") if x.get("in_db", True) else "",
                      f"{x['target']:.0f}" if isinstance(x.get("target"), (int, float)) else "",
                      self._after_info(x)[0],
                      _days(_cover(x, x["file_pcs"])) if (x.get("file_pcs") or 0) > 0 else "",
                      R.STORES.get(x.get("store"), ""), self._change_text(x), self._transfer_text(x),
                      "；".join(self._reason_lines(x)), x.get("sup_code") or "", x.get("sup_name") or "", self._monthly(x),
                      self._amount_text(x), _situation(x)])

    def _amount(self, x):
        """这一行要花多少钱：确定箱数（有调货的按调货以后的箱数）× 每箱件数 × 件价"""
        m = self._moves_in.get((x.get("store"), str(x.get("code"))))
        b = m["colli_after"] if m else x.get("fbox") or 0
        try:
            p = float(x.get("price") or 0)
        except (TypeError, ValueError):
            return 0.0
        return b * _pk(x) * (p if p == p else 0.0)

    def _amount_text(self, x):
        a = self._amount(x)
        return f"{a:,.2f}" if a > 0 else ""

    def _key(self, i, c):
        """排序：「判断」按要看的先后（不订、少订、调货、多订 … 照清单订最后），同一类里金额大的在前"""
        x = self.rows[i]
        if c == _col(0):
            rank = VERDICT_RANK.get(self._verdict(x)[0], 9)
            if x.get("phantom"):
                rank = min(rank, 2.5)                  # 库存可能不准：排在前面要看的里
            return rank * 1e7 - min(self._amount(x), 9e6)
        if c == AMOUNT_COL:
            return self._amount(x)
        return None

    def _monthly(self, x):
        """月均：新算法 = 加权日均 × 30；原程序算法 = 最长周期销量 ÷ 月数（和订货明细同一个算法，store_orders.monthly_and_need）"""
        import store_orders as SO
        days = (self.meta or {}).get("days") or [30, 60, 90, 180]
        sales = x.get("sales") or []
        if not x.get("in_db", True) or len(sales) < 4 or not isinstance(sales[3], (int, float)):
            return ""
        return f"{SO.monthly_and_need(x, days)[0]:g}"

    def _verdict(self, x):
        """(类别, 文字)：确定件数和门店清单比，不订 / 照清单订 / 多订 / 少订 / 建议调货"""
        key = (x.get("store"), str(x.get("code")))
        moved = key in self._moves_in                  # 只算调进来的
        return R.review_verdict(x["lbox"], x["fbox"], moved, x.get("in_db", True), None, tr("照清单订"))

    def _reason_lines(self, x):
        """为什么：直白的几句话（「原因」列、鼠标提示、右边详情栏共用，和订货明细同一个写法）"""
        code = str(x.get("code"))
        tin = None
        m = self._moves_in.get((x.get("store"), code))
        if m:
            d = ((self.meta.get("snap") or {}).get(m["from"]) or {}).get(code) or {}
            tin = {"name": R.STORES.get(m["from"], m["from"]), "pieces": m["pieces"],
                   "inv": max(0.0, float(d.get("inv") or 0)), "days": _cover({"in_db": True, "inv0": d.get("inv"),
                                                                              "daily0": d.get("daily")}) if d else None,
                   "saved": (m.get("colli_before") or 0) - (m.get("colli_after") or 0)}
        pack = x.get("pack") or 0
        sugg = x.get("suggested") or 0
        lines = R.plain_reason(x.get("inv0"), x.get("daily0"), x.get("cycle"), sugg,
                               boxes=math.ceil(sugg / pack) if pack > 0 and sugg > 0 else None, target=x.get("target"),
                               in_db=x.get("in_db", True), transfer=tin, pack=pack)
        if x.get("protect") == "new":
            lines.insert(0, tr("新品保护：近两个月才开始卖（或还没卖过），销量还说明不了什么，按门店清单订"))
        elif x.get("protect") == "empty":
            lines.insert(0, tr("缺货保护：店里已经没货，门店又点名要，至少订 1 箱"))
        elif x.get("protect") == "a_low":
            lines.insert(0, tr("A 类保护：卖得好的商品，库存不够卖一个订货周期，至少订 1 箱"))
        cz = x.get("censored")
        if cz:
            lines.insert(0, tr("断货补算：最近 {0} 周一件没卖、库存只剩 {1:g} 件，多半是货架上没货（不是卖不动了）；按过去一年的平均算，月销 {2:.1f} 件（不是 {3:.1f}）",
                               cz["weeks"], float(x.get("inv0") or 0), cz["long"] * 30, cz["orig"] * 30))
        ph = x.get("phantom")
        if ph:
            line = tr("库存可能不准：账面有 {0:g} 件，但已经 {1} 天一件没卖（平时这段时间能卖 {2:.0f} 件），门店又点名要，货架上多半没货了",
                      float(x.get("inv0") or 0), ph["days"], ph["expect"])
            if x.get("protect") == "phantom":
                line += "；" + (tr("A 类：按门店清单订") if (x.get("tier") or "") == "A" else tr("库存按 0 重新算"))
            else:
                line += "；" + tr("C 类卖得慢，只提醒，订多少你决定")
            lines.insert(0, line)
        if x.get("limited"):
            rr = review_rules(self.cfg)
            lines.append(tr("系统算出来只要 {0:,} 箱；门店清单要 {1:,} 箱有它的原因，少订最多砍掉 {2:.0%}，按 {3:,} 箱订（库存够卖 {4:g} 个订货周期以上才会砍得更多）",
                            x.get("sys_box") or 0, x["lbox"], rr["max_cut"], x["sbox"], rr["free_cut_cycles"]))
        elif x.get("sys_box") is not None and x["sys_box"] > x["lbox"]:
            lines.append(tr("系统算出来要 {0:,} 箱，多于门店清单，按清单 {1:,} 箱订（先不多订）", x["sys_box"], x["lbox"]))
        if x.get("in_db", True) and x.get("sbox") is not None and x["lbox"] != x["sbox"]:
            lines.append(tr("门店清单报了 {0:,} 箱", x["lbox"]))
        if _edited(x):
            lines.append(tr("现在确定 {0:,} 箱（手动改过）", x["fbox"]))
        return lines

    def _marks(self, x):
        """判断后面的提醒：慢销（订了要卖很久）；库存可能不准（虚库存）"""
        if not x.get("in_db", True):
            return []
        out = R.review_marks(x.get("target") is not None, x["final"] > 0, self._arrive_days(x), x.get("store_inv"))
        cz = x.get("censored")
        if cz:
            out.append(("censored", tr("断货补算"),
                        tr("最近 {0} 周没卖、库存只剩 {1:g} 件：按过去一年平均（月销 {2:.1f}）算", cz["weeks"],
                           float(x.get("inv0") or 0), cz["long"] * 30)))
        ph = x.get("phantom")
        if ph:
            out.append(("phantom", tr("库存可能不准"),
                        tr("账面有 {0:g} 件，但已经 {1} 天一件没卖（平时能卖 {2:.0f} 件），货架上多半没货了", float(x.get("inv0") or 0),
                           ph["days"], ph["expect"])))
        return out

    def _arrive_days(self, x):
        """
        到货后够卖：(库存 + 订的箱数 × 每箱件数 + 调进来的 − 调给别的店的) ÷ 日均；不订也不调出 → None
        （有调进来的：订的箱数是调货以后的）
        """
        key = (x.get("store"), str(x.get("code")))
        out = sum(t["pieces"] for t in self._moves_out.get(key, []))
        m = self._moves_in.get(key)
        have = (m["colli_after"] * _pk(x) + m["pieces"]) if m else x["final"]
        if have <= 0 and not out:
            return None
        return _cover(x, have - out)

    def _change_text(self, x):
        """确定箱数比门店清单多 / 少几箱（一样就空着，表格干净）"""
        d = int(x["fbox"]) - int(x["lbox"])
        return f"{d:+,}" if d else ""

    def _need_pcs(self, x):
        """调货用的「真正要的件数」：确定箱数没改（= 系统建议）就用系统算的件数（零头才能调），改过就按箱数 × 每箱件数"""
        if x.get("sbox") is not None and x["fbox"] == x["sbox"]:
            pk = _pk(x)
            return int(min(float(x.get("suggested") or 0), x["fbox"] * pk) if pk > 0 else (x.get("suggested") or 0))
        return int(x["final"])

    def _transfer_text(self, x):
        key = (x.get("store"), str(x.get("code")))
        m = self._moves_in.get(key)
        if m:                                          # 短：← 从哪家 几件（详细的在鼠标提示里）
            return f"← {R.STORES.get(m['from'], m['from'])} {m['pieces']}" + tr("件") + ("  ✎" if m.get("manual") else "")
        ov = self.transfer_ov.get(key)
        if ov is not None and not TR.norm_override(ov)["auto"]:     # 人工改过：不调 / 调出门店不够
            p = TR.norm_override(ov)["pieces"]
            return tr("不调") + "  ✎" if not p else tr("调不了 {0} 件", p)
        # 要订、没调货的写「不调」（不订的行空着；调出去的不显示，审核只要知道从哪调进来）
        return tr("不调")                              # 没调货的一律写「不调」

    def freeze_transfers(self):
        """第一次人工改（调货或确定件数）：把现在系统建议的调货冻结下来，以后改一行别的行不跟着变"""
        if self.transfer_frozen:
            return
        self.transfer_ov = TR.freeze(self._moves, self.transfer_ov, lambda m: (m["to"], str(m["code"])))
        self.transfer_frozen = True

    def _edit_transfer(self, k, c):
        """双击「调货」格：选从哪家门店调、调几件（或者不调）。只改这一行"""
        if c != _col(TRANSFER_COL):
            return
        i = self.ctl.row_at(k)
        if i is None:
            return
        x = self.rows[i]
        snap = self.meta.get("snap") or {}
        if not snap:
            self.win.show_toast(tr("没有其他门店的库存数据，不能调货"))
            return
        if not _can_transfer(x) or int(x.get("fbox") or 0) <= 0:
            self.win.show_toast(tr("这一行不订货，不用调货"))
            return
        import store_orders as SO
        so = self.settings.get("store_orders") or {}
        wt = TR.wholesale_raw(self.settings)          # 批发商比价自己的调货设置
        cfg = TR.config(wt)
        if not cfg.get("enabled"):
            self.win.show_toast(tr("批发商比价没有启用门店间调货（设置 → 门店间调货…）"))
            return
        far = set(TR.excluded_stores(wt, (dict(SO.DEFAULT_FILL, **(so.get("fill") or {}))).get("far_stores")))
        sid, code = x.get("store"), str(x.get("code"))
        ordering = {(r.get("store"), str(r.get("code"))) for r in self.rows if int(r.get("final") or 0) > 0}
        stores = []
        for s_, d in snap.items():
            v = (d or {}).get(code)
            if s_ == sid or not v:
                continue
            notes = []
            if s_ in far:
                notes.append(tr("不参与调货"))
            if (s_, code) in ordering:
                notes.append(tr("自己也要订"))
            stores.append({"sid": s_, "name": R.STORES.get(s_, str(s_)), "inv": v.get("inv"), "daily": v.get("daily"),
                           "surplus": TR.surplus(v, cfg.get("keep_cycles")), "note": "、".join(notes)})
        if not stores:
            self.win.show_toast(tr("其他门店没有这个商品的库存数据"))
            return
        key = (sid, code)
        m = self._moves_in.get(key)
        pack = float(x.get("pack") or 1) or 1.0
        need = self._need_pcs(x)
        colli = int(x["fbox"])
        from erp_widgets import TransferEditDialog
        dlg = TransferEditDialog(self.window, x.get("name") or code,
                                 tr("{0} 这次要 {1:g} 件，一箱 {2:g} 件，现在订 {3:g} 箱",
                                    R.STORES.get(sid, sid), need, pack, colli),
                                 stores, (m["from"], m["pieces"]) if m else None, need, pack, colli)
        if not dlg.exec() or dlg.value is None:
            return
        frm, pcs = dlg.value
        from PySide6.QtWidgets import QApplication
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.freeze_transfers()
            self.transfer_ov[key] = {"from": frm, "pieces": pcs} if pcs else 0
            self._replan(render=False)
            self._refresh_rows([j for j, r in enumerate(self.rows) if str(r.get("code")) == code])   # 只刷新这个商品的几行
            self._summary()
        finally:
            QApplication.restoreOverrideCursor()
        if pcs and key not in self._moves_in:
            self.win.show_toast(tr("{0} 库存不够，这次调不了", R.STORES.get(frm, frm)))

    def _transfer_tip(self, x):
        key = (x.get("store"), str(x.get("code")))
        m = self._moves_in.get(key)
        if m:
            return tr("调进 {0} 件 ← {1}，还订 {2} 箱", m["pieces"], R.STORES.get(m["from"], m["from"]), m["colli_after"])
        return ""

    # ---------- 右边详情栏 ----------
    # 2026-10-25 重新设计（用户：柱状图、服务水平这些从来不看）：只放判断这一行要用的 ——
    #   商品图和名称 → 这一行怎么订（清单 → 确定、订后够卖、金额）→ 库存 / 月销 / 现在够卖
    #   → 近两年每月销量（今年和去年同月对比，看季节和趋势）→ 为什么 → 其他门店
    def _build_drawer(self):
        from PySide6.QtWidgets import QScrollArea
        from erp_widgets import MonthGrid
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFixedWidth(360)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setFrameShape(QFrame.NoFrame)
        d = QWidget()
        d.setObjectName("Drawer")
        v = QVBoxLayout(d)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)
        self.d_img = QLabel()                     # 商品图（公司图片服务器上有这个条码的图才显示）
        self.d_img.setAlignment(Qt.AlignCenter)
        self.d_img.setMinimumHeight(160)
        self.d_img.hide()
        self.d_title = QLabel(tr("选中一行查看详情"))
        self.d_title.setStyleSheet("font-size:15px; font-weight:700;")
        self.d_title.setWordWrap(True)
        self.d_codes = QLabel("")
        self.d_codes.setObjectName("Faint")
        self.d_codes.setWordWrap(True)
        self.d_codes.setTextInteractionFlags(Qt.TextSelectableByMouse)
        for w in (self.d_img, self.d_title, self.d_codes):
            v.addWidget(w)
        self.d_more = QPushButton(tr("商品详情 ›"))       # 和双击商品一样：入库记录、每天销量、各店库存
        self.d_more.setObjectName("CrumbLink")
        self.d_more.setCursor(Qt.PointingHandCursor)
        self.d_more.setToolTip(tr("入库记录、每天销量、各店库存（双击表格里的商品也能打开）"))
        self.d_more.clicked.connect(lambda: self.open_card(self._cur_i) if self._cur_i is not None else None)
        v.addWidget(self.d_more, 0, Qt.AlignLeft)
        # 这一行怎么订：清单 3 箱 → 确定 1 箱（大字），下面一行订后够卖、金额
        dec = QFrame()
        dec.setObjectName("MiniCard")
        dv = QVBoxLayout(dec)
        dv.setContentsMargins(12, 10, 12, 10)
        dv.setSpacing(2)
        self.d_verdict = QLabel("")
        self.d_verdict.setStyleSheet("font-weight:700; font-size:12px;")
        self.d_boxes = QLabel("—")
        self.d_boxes.setStyleSheet("font-size:20px; font-weight:700;")
        self.d_after = QLabel("")
        self.d_after.setWordWrap(True)
        for w in (self.d_verdict, self.d_boxes, self.d_after):
            dv.addWidget(w)
        v.addWidget(dec)
        g = QGridLayout()
        g.setSpacing(8)
        self.minis = []
        for i, lab in enumerate((tr("库存"), tr("月销"), tr("现在够卖"))):
            f = QFrame()
            f.setObjectName("MiniCard")
            fv = QVBoxLayout(f)
            fv.setContentsMargins(10, 8, 10, 8)
            fv.setSpacing(1)
            a_ = QLabel(lab)
            a_.setObjectName("MiniLabel")
            b_ = QLabel("—")
            b_.setObjectName("MiniValue")
            c_ = QLabel("")
            c_.setObjectName("MiniLabel")
            for w in (a_, b_, c_):
                fv.addWidget(w)
            g.addWidget(f, 0, i)
            self.minis.append((b_, c_))
        v.addLayout(g)
        sp = QFrame()
        sp.setObjectName("MiniCard")
        spv = QVBoxLayout(sp)
        spv.setContentsMargins(12, 10, 12, 10)
        hl = QHBoxLayout()
        t = QLabel(tr("近两年每月销量"))
        t.setStyleSheet("font-weight:600; font-size:12px;")
        u = QLabel(tr("件 · 颜色越深卖得越多"))
        u.setObjectName("MiniLabel")
        hl.addWidget(t, 1)
        hl.addWidget(u)
        spv.addLayout(hl)
        self.months_w = MonthGrid()
        spv.addWidget(self.months_w)
        self.months_note = QLabel("")
        self.months_note.setObjectName("MiniLabel")
        self.months_note.setWordWrap(True)
        spv.addWidget(self.months_note)
        v.addWidget(sp)
        why = QFrame()
        why.setObjectName("MiniCard")
        wv = QVBoxLayout(why)
        wv.setContentsMargins(12, 10, 12, 10)
        self.why_t = QLabel(tr("为什么"))
        self.why_t.setStyleSheet("font-weight:600; font-size:12px;")
        self.why = QLabel("")
        self.why.setWordWrap(True)
        self.why.setObjectName("Faint")
        wv.addWidget(self.why_t)
        wv.addWidget(self.why)
        v.addWidget(why)
        oth = QFrame()
        oth.setObjectName("MiniCard")
        ov = QVBoxLayout(oth)
        ov.setContentsMargins(12, 10, 12, 10)
        ot = QLabel(tr("其他门店"))
        ot.setStyleSheet("font-weight:600; font-size:12px;")
        ov.addWidget(ot)
        self.others = QVBoxLayout()
        self.others.setSpacing(4)
        ov.addLayout(self.others)
        v.addWidget(oth)
        v.addStretch()
        sa.setWidget(d)
        return sa

    def _select(self, r):
        i = self.ctl.row_at(r) if r is not None and r >= 0 else None
        if i is None:
            return
        self._cur_i = i
        self._show_drawer(i)

    # ---------- 近两年每月销量（检查完以后后台读，不拖慢检查）----------
    def _load_months(self):
        """每家门店这次清单里的商品，最近 24 个月每月销量 → self._m24[(门店, 编号)]（只读查询，后台线程）"""
        self._m24, self._m24_keys = {}, R.month_keys(24)
        self._m24_token = getattr(self, "_m24_token", 0) + 1
        self._m24_done = set()
        token = self._m24_token
        by_store = {}
        for x in self.rows:
            if x.get("in_db", True) and str(x.get("code") or "").isdigit():
                by_store.setdefault(x.get("store"), set()).add(str(x["code"]))
        if not by_store:
            return
        dbs = self.settings.get("db") or None

        def work():
            try:
                db = R.make_db(dbs)
                for sid, codes in by_store.items():       # 促销先查（主表格要标红），每月销量后查
                    try:
                        self.sig.promos.emit((token, sid, R.query_promos(db, sid, codes)))
                    except Exception as e:
                        self.sig.promos.emit((token, sid, {"_error": str(e)}))
                for sid, codes in by_store.items():
                    try:
                        data = R.query_monthly_sales(db, sid, codes)
                    except Exception as e:
                        data = {"_error": str(e)}
                    self.sig.months.emit((token, sid, codes, data))
                db.engine.dispose()
            except Exception as e:
                for sid, codes in by_store.items():
                    self.sig.months.emit((token, sid, codes, {"_error": str(e)}))
        threading.Thread(target=work, daemon=True).start()

    def _promos_arrived(self, payload):
        """本店在促销的商品：x["promo"]，商品 / 条码格标红、「只看促销」"""
        token, sid, data = payload
        if token != getattr(self, "_m24_token", None):
            return
        err = data.pop("_error", None)
        if err:
            self.win.log(tr("⚠ 促销没查到：{0}", err))
            return
        for w in data.pop("_warn", None) or ():
            self.win.log("⚠ " + w)
        self.win.log(tr("[检查清单] {0}：{1} 个商品在促销", R.STORES.get(sid, sid), len(data)))
        idx = []
        for i, x in enumerate(self.rows):
            if x.get("store") == sid:
                p = data.get(str(x.get("code")))
                if p or x.get("promo"):
                    x["promo"] = p
                    idx.append(i)
        if idx:
            self._refresh_rows(idx)
            self._summary()
        if self._card is not None and self._card.isVisible() and self._card.x is not None and \
                self._card.x.get("store") == sid:
            self._card._show_promo(self._card.x.get("promo"))

    def _promo_tip(self, x):
        p = x.get("promo")
        if not p:
            return ""
        return promo_text(p) + "\n" + tr("促销期间销量会偏高")

    def _open_card(self, k, c):
        """双击商品 / 条码 / 编号：打开商品详情窗口（入库记录、每天销量、各店库存…）"""
        if c not in (_col(1), _col(2), _col(3)):
            return
        i = self.ctl.row_at(k)
        if i is not None:
            self.open_card(i)

    def open_card(self, i):
        if not (0 <= i < len(self.rows)):
            return
        x = self.rows[i]
        from product_window import ProductWindow
        if self._card is None:
            self._card = ProductWindow(self.window, self.ui, self.settings)
        key = (x.get("store"), str(x.get("code")))
        vals = self._m24.get(key) if x.get("store") in getattr(self, "_m24_done", set()) else None
        self._card.show_item(x, (vals, self._m24_keys) if vals is not None else None)

    def _months_arrived(self, payload):
        token, sid, codes, data = payload
        if token != getattr(self, "_m24_token", None):
            return
        err = data.pop("_error", None) if isinstance(data, dict) else None
        for c in codes:
            self._m24[(sid, c)] = None if err else R.monthly_series(data.get(c, {}), self._m24_keys)
        self._m24_done.add(sid)
        if err:
            self._m24_err = err
        if self._cur_i is not None and 0 <= self._cur_i < len(self.rows) and self.rows[self._cur_i].get("store") == sid:
            self._show_months(self.rows[self._cur_i])

    def _show_months(self, x):
        key = (x.get("store"), str(x.get("code")))
        if not x.get("in_db", True):
            self.months_w.set_message(tr("数据库里没有这个商品"))
            self.months_note.setText("")
            return
        if x.get("store") not in getattr(self, "_m24_done", set()):
            self.months_w.set_message(tr("正在读取…"))
            self.months_note.setText("")
            return
        vals = self._m24.get(key)
        if vals is None:
            self.months_w.set_message(tr("没读到（数据库）"))
            self.months_note.setText(getattr(self, "_m24_err", ""))
            return
        self.months_w.set_data(vals, self._m24_keys)
        cur, prev, full = self.months_w.ytd()
        m = self._m24_keys[-1][1]
        note = tr("今年 1–{0} 月 {1:g} 件", m, round(cur, 1))
        if prev:
            note += " · " + tr("去年同期 {0:g} 件（{1:+.0%}）", round(prev, 1), cur / prev - 1)
        else:
            note += " · " + tr("去年同期没卖")
        note += "\n" + tr("去年全年 {0:g} 件 · * 本月到今天", round(full, 1))
        self.months_note.setText(note)

    def _show_drawer(self, i):
        """详情栏：这一行怎么订 + 判断要用的几个数 + 近两年每月销量 + 为什么 + 其他门店"""
        if not (0 <= i < len(self.rows)):
            return
        T = self.ui.T
        x = self.rows[i]
        code = str(x.get("code"))
        sid = x.get("store")
        self.d_title.setText(x.get("name") or code)
        import product_images as PI
        PI.set_label_image(self.d_img, x.get("ean"), 160, self.settings)
        pack = x.get("pack") or 0
        try:
            price = float(x.get("price") or 0)
            price = price if price == price else 0
        except (TypeError, ValueError):
            price = 0
        parts1 = [p for p in (R.STORES.get(sid, ""), x.get("sup_name") or "", (x.get("tier") or "") and tr("{0} 类", x["tier"])) if p]
        parts2 = [p for p in (code, x.get("ean") or "", f"Cod.Forn. {x['sup_code']}" if x.get("sup_code") else "") if p]
        parts3 = [p for p in (f"{pack:g} " + tr("件/箱") if pack else "", f"€{price:.3f}/" + tr("件") if price else "",
                              tr("{0} 报价", x["quote_sup"]) if x.get("quote_sup") else "") if p]
        self.d_codes.setText("\n".join(" · ".join(ps) for ps in (parts1, parts2, parts3) if ps))
        # 这一行怎么订
        kind, vt = self._verdict(x)
        self.d_verdict.setText(vt)
        self.d_verdict.setStyleSheet(f"font-weight:700; font-size:12px; color:{T(VERDICT_TOKEN.get(kind, 'text'))};")
        m_in = self._moves_in.get((sid, code))
        boxes = tr("清单 {0:,} 箱  →  确定 {1:,} 箱", x["lbox"], x["fbox"])
        if m_in:
            boxes += "\n" + tr("调进 {0} 件 ← {1}，还订 {2} 箱", m_in["pieces"], R.STORES.get(m_in["from"], m_in["from"]),
                                m_in["colli_after"])
        self.d_boxes.setText(boxes)
        text, tok, tip = self._after_info(x)
        amt = self._amount(x)
        bits = []
        if text:
            bits.append((tr("订后够卖 {0} 天", text) if self._arrive_days(x) is not None else tr("现在的库存够卖 {0} 天", text)))
        if amt > 0:
            bits.append(f"€{amt:,.2f}")
        self.d_after.setText(" · ".join(bits) + (f"\n{tip}" if tip and tok in ("err", "warn") else ""))
        self.d_after.setStyleSheet(f"color:{T(tok) if tok in ('err', 'warn') else T('text')};")
        (a1, a2), (b1, b2), (c1, c2) = self.minis
        if x.get("in_db", True):
            inv = x.get("store_inv")
            inv = float(inv) if isinstance(inv, (int, float)) else 0.0     # 显示原值（计算时负数当 0）
            a1.setText(f"{inv:g}")
            a2.setText(tr("件") + ("  " + tr("（账面负数）") if inv < 0 else ""))
            mo = self._monthly(x)
            b1.setText(mo or "0")
            b2.setText(tr("件 / 月"))
            cv = _cover(x)
            c1.setText(_days(cv) or "0")
            cyc = x.get("cycle")
            c2.setText(tr("天 · 周期 {0:g} 天", cyc) if isinstance(cyc, (int, float)) else tr("天"))
        else:
            for m1, m2 in self.minis:
                m1.setText("—")
                m2.setText("")
        self._show_months(x)
        if not x.get("in_db", True):
            self.why_t.setText(tr("为什么"))
        elif x.get("sbox") is not None:
            self.why_t.setText(tr("为什么建议 {0:g} 箱", x["sbox"]))
        else:
            self.why_t.setText(tr("为什么"))
        self.why.setText("\n".join(["· " + l for l in self._reason_lines(x)] + ["⚠ " + m[2] for m in self._marks(x)] +
                                   (["🏷 " + self._promo_tip(x)] if x.get("promo") else [])))
        self.d_title.setStyleSheet("font-size:15px; font-weight:700;" + (f" color:{T('err')};" if x.get("promo") else ""))
        # 其他门店：库存、够卖几天、这次定了几箱
        while self.others.count():
            w = self.others.takeAt(0).widget()
            if w:
                w.deleteLater()
        snap = self.meta.get("snap") or {}
        for osid in sorted(k for k in snap if k != sid):
            d = (snap.get(osid) or {}).get(code)
            if not d:
                continue
            ox = self._by_key.get((osid, code))
            on = int(ox.get("fbox") or 0) if ox else 0
            cv = _cover({"in_db": True, "inv0": d.get("inv"), "daily0": d.get("daily")})
            l = QLabel(f"{R.STORES.get(osid, osid)}    " +
                       tr("库存 {0:g} · 够卖 {1} 天 · 订 {2:g} 箱", float(d.get("inv") or 0), _days(cv) or "—", on))
            l.setObjectName("Faint" if not on else "")
            self.others.addWidget(l)
        if not self.others.count():
            l = QLabel(tr("没有其他门店的数据"))
            l.setObjectName("Faint")
            self.others.addWidget(l)

    def _replan_in_place(self):
        """改了确定箱数以后重算调货，但表格不重新排序 / 筛选：改过的行留在原地（点表头或换筛选时才重排）"""
        self._replan(render=False)
        self._refresh_rows(list(self.ctl.visible))
        self._fill_prob()
        self._summary()

    def _replan(self, render=True):
        """按现在的确定箱数重新算门店间调货（改了以后稍等一下再算，不卡）"""
        missing = [x for x in self.rows if "fbox" not in x]
        if missing:                                    # 还没按箱整理过的行（保险）
            init_boxes(missing, None, self._algo(), self.cfg)
        self._moves_in, self._moves_out = {}, {}
        self._moves = []
        if self.rows and self.meta.get("snap"):
            try:
                self._moves, _ = self.transfer_plan()
            except Exception as e:
                self.win.log(tr("⚠ 调货计算失败：{0}", e))
        for m in self._moves:
            self._moves_in[(m["to"], str(m["code"]))] = m
            self._moves_out.setdefault((m["from"], str(m["code"])), []).append(m)
        if render:
            self.ctl.render()
            self._fill_prob()
            self._summary()
            if self._cur_i is not None:
                self._show_drawer(self._cur_i)

    def _text(self, i, c):
        return self._vals(i)[c]

    # ---------- 工具条：门店按钮、看哪些、更多 ----------
    def _build_chips(self, stores):
        """门店按钮：「全部门店」+ 五家店（和订货明细一样）；这次没检查的门店按钮灰掉"""
        import store_orders as SO
        for b in list(self.store_group.buttons()):
            self.store_group.removeButton(b)
            b.deleteLater()
        stores = {int(k) for k in stores or []}
        for sid, text in [(None, tr("全部门店"))] + list(SO.STORE_SHEETS.items()):
            b = QPushButton(text)
            b.setEnabled(sid is None or sid in stores)
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setChecked(sid is None)
            b.setProperty("sid", -1 if sid is None else int(sid))
            b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            bf = b.font()
            bf.setBold(True)
            b.setMinimumWidth(QFontMetrics(bf).horizontalAdvance(text) + 28)
            self.store_group.addButton(b)
            self.chip_lay.addWidget(b)

    def _fill_suppliers(self):
        """供应商下拉框：这次清单里商品的供应商（数据库登记的），后面带商品数"""
        cnt = {}
        for x in self.rows:
            n = x.get("sup_name") or ""
            if n:
                cnt[n] = cnt.get(n, 0) + 1
        self.f_sup.blockSignals(True)
        self.f_sup.clear()
        self.f_sup.addItem(tr("全部供应商"), None)
        for n in sorted(cnt):
            self.f_sup.addItem(f"{n}（{cnt[n]}）", n)
        self.f_sup.blockSignals(False)

    def _fill_prob(self):
        """「数据库没有 / 没有箱规」两个按钮（带个数）和点开的列表：列表跟着门店按钮、搜索框筛选；没有这类商品就不显示按钮"""
        if not hasattr(self, "prob"):
            return
        sid = self._store_sel()
        text = self.flt.text().strip().lower()
        self._prob_filling = True
        has_grid = any(x.get("in_grid") is not None for x in self.rows)
        total, tips = 0, []
        for kind, (ti, page, t) in self.prob.items():
            allk = [i for i, x in enumerate(self.rows) if _tab_of(_problem(x)) == kind]
            idx = [i for i in allk if (sid is None or self.rows[i].get("store") == sid) and
                   (not text or text in f"{self.rows[i].get('code')} {self.rows[i].get('ean')} {self.rows[i].get('name')}".lower())]
            title = _prob_title(kind, len(allk), has_grid)
            self.prob_tabs.setTabText(ti, title)
            self.prob_tabs.setTabVisible(ti, bool(allk))
            if allk:
                total += len(allk)
                tips.append(title)
            t.blockSignals(True)
            t.setRowCount(len(idx))
            for r, i in enumerate(idx):
                x = self.rows[i]
                none = _problem(x) in ("noquote", "nogrid_noq")
                self.ui.table_set_row(t, r, [R.STORES.get(x.get("store"), ""), x.get("ean") or "", x.get("name") or "",
                                             x.get("sup_name") or "", tr("都没有") if none else tr("有"),
                                             f"{x['lbox']:,}", f"{x['fbox']:,}"])
                t.item(r, 0).setData(Qt.UserRole, i)
                t.item(r, 2).setToolTip(f"{x.get('name') or ''}\n{x.get('code') or ''} · {x.get('ean') or ''}" +
                                        ("\n" + _issue_text(x) if _issue(x) else ""))
                t.item(r, 4).setForeground(QColor(self.ui.T("err") if none else self.ui.T("ok")))
                for c in range(t.columnCount()):
                    it = t.item(r, c)
                    if c == 6:
                        it.setFlags(it.flags() | Qt.ItemIsEditable)
                        it.setToolTip(tr("双击修改箱数，0 = 不订"))
                        self._style_prob_cell(it, x)
                    else:
                        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            t.blockSignals(False)
            if getattr(t, "_hf", None) is not None and t._hf.active():
                t._hf.reapply()
            t.setColumnHidden(0, len((self.meta or {}).get("stores") or []) < 2)
        self.prob_btn.setText(tr("⚠ 待核对（{0}）", total))
        self.prob_btn.setToolTip("\n".join(tips) + "\n" + tr("点一下打开"))
        self.prob_btn.setVisible(bool(total))
        self._prob_filling = False

    def _nogrid_none(self, kind="nogrid"):
        """这个问题列表（不在 Griglia / 没报价）里的商品全部不订"""
        n, idx = 0, []
        for i, x in enumerate(self.rows):
            if _tab_of(_problem(x)) == kind and x.get("fbox"):
                _set_box(x, 0)
                n += 1
                idx.append(i)
        self._fill_prob()
        self._refresh_rows(idx)                       # 主表格里这些行也标黄
        self._summary()
        self.win.show_toast(tr("{0} 个商品改成不订", n))

    def _open_prob(self, kind=None):
        """点「⚠ 待核对」：打开窗口（不挡住检查窗口，可以一边对照一边改），停在第一个有商品的标签页"""
        self._fill_prob()
        tabs = self.prob_tabs
        if kind is not None and kind in self.prob:
            tabs.setCurrentIndex(self.prob[kind][0])
        elif not tabs.isTabVisible(tabs.currentIndex()):
            for k in range(tabs.count()):
                if tabs.isTabVisible(k):
                    tabs.setCurrentIndex(k)
                    break
        dlg = self.prob_dlg
        from erp_widgets import fit_to_screen
        fit_to_screen(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _prob_edited(self, kind, item):
        """待核对窗口里改了确定箱数：只更新这一格（不重画整张表，改得快），合计停手 0.3 秒再算。
        等编辑框关掉再改格子（编辑中途重画会卡住）"""
        if self._prob_filling or item.column() != 6:
            return
        t = self.prob[kind][2]
        first = t.item(item.row(), 0)
        i = first.data(Qt.UserRole) if first is not None else None
        if i is None:
            return
        try:
            n = float(item.text().strip().replace(",", "") or 0)
            if n < 0 or n != int(n):
                raise ValueError
            _set_box(self.rows[i], int(n))
        except ValueError:
            self.win.show_toast(tr("箱数要填 0 或正整数"))
        row, x = item.row(), self.rows[i]

        def later():
            cell = t.item(row, 6)
            if cell is None:
                return
            self._prob_filling = True
            t.blockSignals(True)
            cell.setText(f"{x['fbox']:,}")
            self._style_prob_cell(cell, x)
            t.blockSignals(False)
            self._prob_filling = False
            self._refresh_rows([i])                     # 主表格里这一行也跟着变（改过的标黄）
            self._sum_timer.start()
        QTimer.singleShot(0, later)

    def _style_prob_cell(self, it, x):
        """待核对窗口的确定箱数格：改过的标黄（和主表格一样）"""
        T = self.ui.T
        ch = _edited(x)
        it.setForeground(QColor(T("warn") if ch else T("accent") if x["fbox"] > 0 else T("faint")))
        it.setBackground(QColor(T("warn_soft")) if ch else QColor(0, 0, 0, 0))

    def _export_prob_one(self):
        """导出门店报告：五家店合成一个文件，每家店一个工作表（发到大家都在的群里用）"""
        start = self.win.ws_root() if hasattr(self.win, "ws_root") else ""
        path, _ = QFileDialog.getSaveFileName(self.prob_dlg, tr("导出门店报告"), os.path.join(
            start or "", f"Articoli_da_verificare_{datetime.now():%d%m}.xlsx"), tr("Excel 文件 (*.xlsx)"))
        if not path:
            return
        try:
            p = write_problem_report(self.rows, path, store=None, url=self.settings.get("image_url") or None)
        except Exception as e:
            QMessageBox.warning(self.prob_dlg, tr("导出门店报告"), tr("导出失败：{0}", e))
            return
        if p:
            self.win.log(tr("✔ 门店报告：{0}", p))
            self.win.show_toast(tr("已导出：{0}", os.path.basename(p)))
            try:
                os.startfile(os.path.dirname(p))
            except Exception:
                pass

    def _export_prob(self):
        """待核对窗口「导出门店报告」：选一个文件夹，每家店一个意大利文报告（可以直接发给门店）"""
        start = self.win.ws_root() if hasattr(self.win, "ws_root") else ""
        folder = QFileDialog.getExistingDirectory(self.prob_dlg, tr("导出门店报告"), start or "")
        if not folder:
            return
        url = self.settings.get("image_url") or None
        done, errs = [], []
        for sid in sorted({x.get("store") for x in self.rows if _problem(x)}, key=lambda v: v or 0):
            try:
                p = write_problem_report(self.rows, os.path.join(folder, report_name(sid)), store=sid, url=url)
                if p:
                    done.append(p)
                    self.win.log(tr("✔ 门店报告：{0}", p))
            except Exception as e:
                errs.append(f"{R.STORES.get(sid, sid)}：{e}")
        if errs:
            QMessageBox.warning(self.prob_dlg, tr("导出门店报告"), tr("导出失败：{0}", "\n".join(errs)))
        if done:
            self.win.show_toast(tr("已导出 {0} 家门店的报告", len(done)))
            try:
                os.startfile(folder)                    # Windows：打开文件夹，方便直接发
            except Exception:
                pass

    def _store_sel(self):
        b = self.store_group.checkedButton() if hasattr(self, "store_group") else None
        v = b.property("sid") if b is not None else -1
        return None if v is None or int(v) < 0 else int(v)

    def _apply_forced(self):
        """程序自己藏的列：清单按件时的箱数列；只有一家门店 / 只看一家门店时的「门店」列"""
        one = len((self.meta or {}).get("stores") or []) < 2 or self._store_sel() is not None
        off = not TR.config(TR.wholesale_raw(self.settings)).get("enabled")    # 没开调货：调货列全是「不调」，藏起来
        self._forced = set(getattr(self, "_unit_forced", set())) | ({22} if one else set()) | ({TRANSFER_COL} if off else set())
        self.cols.apply()

    def _store_changed(self):
        self._apply_forced()
        self._filter()

    def _more_menu(self, btn):
        m = QMenu(self.window)
        a_file = m.addAction(tr("全部用清单数量"))
        m.addSeparator()
        a_cols = m.addAction(tr("显示哪些列…"))
        a_order = m.addAction(tr("恢复默认列顺序"))
        a_exp = m.addAction(tr("导出 Excel"))
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        got = m.exec(pos)
        if got == a_file:
            self._set_all("file")
        elif got == a_cols:
            self.cols.menu(pos)
        elif got == a_order:
            self._reset_cols()
        elif got == a_exp:
            self.export()

    def _update_view_counts(self, cnt):
        """「看哪些」下拉框里带上个数：只看多订（12）"""
        n = {"all": len(self.rows), "changed": sum(cnt.get(k, 0) for k in ("transfer", "more", "less", "none"))}
        n.update(cnt)
        n["phantom"] = sum(1 for x in self.rows if x.get("phantom") and not _problem(x))
        n["promo"] = sum(1 for x in self.rows if x.get("promo"))
        for k in range(self.view.count()):
            key = self.view.itemData(k)
            text = tr(dict(VIEWS)[key])
            self.view.setItemText(k, f"{text}（{n.get(key, 0)}）" if key in n else text)

    def _status(self, shown, total):
        """状态栏一行（和订货明细一样）：显示几行 · 改了几处 · 当前筛选合计几箱、多少钱"""
        self._shown = (shown, total)
        if not self.rows:
            self.count_lbl.setText("")
            return
        n_edit = sum(1 for x in self.rows if _edited(x))
        boxes = amt = 0.0
        for i in getattr(self.ctl, "visible", None) or []:
            x = self.rows[i]
            m = self._moves_in.get((x.get("store"), str(x.get("code"))))
            b = m["colli_after"] if m else x["fbox"]          # 有调货的按调货以后的箱数
            boxes += b
            try:
                p = float(x.get("price") or 0)
                amt += b * _pk(x) * (p if p == p else 0)
            except (TypeError, ValueError):
                pass
        self.count_lbl.setText(tr("显示 {0} / {1} 行 · 已改 {2} 处 · 当前筛选合计 {3:g} 箱 · €{4:,.2f}",
                                  shown, total, n_edit, boxes, amt))
        self.count_lbl.setToolTip(getattr(self, "page_sum", None).text() if hasattr(self, "page_sum") else "")

    def _hidden(self, i):
        x = self.rows[i]
        sid = self._store_sel()
        if sid is not None and x.get("store") != sid:
            return True
        sup = self.f_sup.currentData()
        if sup and (x.get("sup_name") or "") != sup:
            return True
        text = self.flt.text().strip().lower()
        view = self.view.currentData()
        kind = self._verdict(x)[0]
        hide = (view == "changed" and kind not in ("transfer", "more", "less", "none")) or \
            (view in ("transfer", "more", "less", "none") and kind != view) or \
            (view == "phantom" and not x.get("phantom")) or (view == "promo" and not x.get("promo"))
        return hide or \
               (bool(text) and text not in f"{x['code']} {x['ean']} {x['name']} {x.get('sup_code') or ''} {x.get('sup_name') or ''}".lower())

    def _fill(self):
        missing = [x for x in self.rows if "fbox" not in x]
        if missing:
            init_boxes(missing, None, self._algo(), self.cfg)
        self.ctl.render()
        self._summary()

    def _fill_row(self, i, k):
        T = self.ui.T
        t = self.table
        x = self.rows[i]
        self.ui.table_set_row(t, k, self._vals(i))
        for c in range(t.columnCount()):
            it = t.item(k, c)
            if c == EDIT_COL:
                it.setFlags(it.flags() | Qt.ItemIsEditable)
            else:
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        tip = short_decision(x["decision"]) if x["in_db"] else tr("数据库里找不到这个商品，箱规未知；确定件数默认 = 清单数量，请核对")
        t.item(k, 0).setToolTip(tip)
        t.item(k, 0).setData(Qt.UserRole, i)
        t.item(k, _col(3)).setToolTip(f"{x['name']}\nEAN {x['ean'] or '—'}")
        if x.get("promo"):                       # 门店在促销：商品名 / 条码标红
            ptip = self._promo_tip(x)
            for pc in (_col(2), _col(3)):
                it = t.item(k, pc)
                it.setForeground(QColor(T("err")))
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            t.item(k, _col(3)).setToolTip(f"{x['name']}\nEAN {x['ean'] or '—'}\n🏷 {ptip}")
            t.item(k, _col(2)).setToolTip("🏷 " + ptip)
        pk = t.item(k, _col(10))
        if x.get("quote_sup"):
            same = not x.get("db_pack") or abs(x["pack"] - x["db_pack"]) < 1e-9
            pk.setToolTip(tr("{0} 的报价：每箱 {1:g} 件", x["quote_sup"], x["pack"]) +
                          ("" if same else "\n" + tr("数据库箱规是每箱 {0:g} 件", x["db_pack"])))
            if not same:
                pk.setForeground(QColor(T("accent")))
        elif x.get("pack"):
            pk.setToolTip(tr("报价里没有这个商品，用数据库箱规"))
        ch = t.item(k, _col(CHANGE_COL))
        d = int(x["fbox"]) - int(x["lbox"])
        ch.setForeground(QColor(T("accent") if d > 0 else T("warn") if d < 0 else T("faint")))
        if d:
            f = ch.font()
            f.setBold(True)
            ch.setFont(f)
        tri = t.item(k, _col(TRANSFER_COL))
        tkey = (x.get("store"), str(x.get("code")))
        m_in = self._moves_in.get(tkey)
        if m_in:
            tri.setForeground(QColor(T("warn") if m_in.get("manual") else T("ok")))
            tri.setToolTip(self._transfer_tip(x) + "\n" + tr("双击修改：换调出门店、改件数或者不调"))
        elif tkey in self.transfer_ov:
            tri.setForeground(QColor(T("faint")))
            tri.setToolTip(tr("人工改过：双击重新选调出门店和件数；「全部用建议量」恢复系统算的调货"))
        else:
            tri.setForeground(QColor(T("faint")))
            tri.setToolTip(tr("双击：这个商品改成从别的门店调货") if (x["fbox"] > 0 and _can_transfer(x)) else "")
        reason = "\n".join(self._reason_lines(x) + ["⚠ " + m[2] for m in self._marks(x)])
        t.item(k, _col(REASON_COL)).setToolTip(reason)
        t.item(k, _col(REASON_COL)).setForeground(QColor(T("muted")))
        t.item(k, 0).setToolTip(reason)          # 鼠标停在「判断」上也能看为什么
        v = _cover(x, x["file_pcs"]) if (x.get("file_pcs") or 0) > 0 else None
        if v is not None and v > self._limit():
            t.item(k, LIST_AFTER_COL).setForeground(QColor(T("warn")))
            t.item(k, LIST_AFTER_COL).setToolTip(tr("订完要很久才卖得完（超过 {0} 天），可能压货", int(self._limit())))
        st = t.item(k, _col(SITU))
        if st is not None and st.text():
            st.setForeground(QColor(T("err") if _problem(x) in ("noquote", "nogrid_noq") else T("warn")))
            st.setToolTip(dict(PROB_KINDS_TIPS()).get(_problem(x), "") + ("\n" + _issue_text(x) if _issue(x) else ""))
        self._style_edit(i, k)

    def _after_info(self, x):
        """订后够卖那一格：(文字, 颜色, 鼠标提示)。不订的显示现在的库存够卖几天"""
        if not x.get("in_db", True):
            return "", "text", ""
        cyc = float(x.get("cycle") or 0)
        limit = self._limit()
        v = self._arrive_days(x)
        if v is None:                                  # 不订、也不调：到下次订货就靠现在的库存
            v = _cover(x)
            if v is None or not float(x.get("daily0") or 0):
                return "", "text", ""
            if cyc and v < cyc:
                return _days(v), "err", tr("不订的话，现在的库存只够卖 {0} 天，不到一个订货周期（{1:g} 天）", _days(v), cyc)
            return _days(v), "faint", tr("不订：现在的库存够卖 {0} 天", _days(v))
        if cyc and v < cyc:
            return _days(v), "err", tr("订完只够卖 {0} 天，不到一个订货周期（{1:g} 天），下次到货前可能卖断", _days(v), cyc)
        if v > limit:
            return _days(v), "warn", tr("订完要很久才卖得完（超过 {0} 天），可能压货", int(limit))
        return _days(v), "text", ""

    def _limit(self):
        """凑单天数（配货设置）：订后够卖超过它标黄"""
        return float((self.settings.get("store_orders") or {}).get("fill", {}).get("max_stock_days") or 60)

    def _style_edit(self, i, k):
        x = self.rows[i]
        T = self.ui.T
        # 判断：跟着确定件数变
        kind, vt = self._verdict(x)
        marks = self._marks(x)
        vc = self.table.item(k, 0)
        vc.setText(" · ".join([vt] + [m[1] for m in marks]))
        vc.setForeground(QColor(T("warn") if (marks and kind in ("same", "more")) else T(VERDICT_TOKEN.get(kind, "text"))))
        f = vc.font()
        f.setBold(kind in ("transfer", "more", "less", "none"))
        vc.setFont(f)
        c = self.table.item(k, EDIT_COL)
        c.setText(f"{x['fbox']:,}")
        f = c.font()
        f.setBold(x["fbox"] > 0)
        c.setFont(f)
        changed = _edited(x)
        c.setForeground(QColor(T("warn") if changed else T("accent") if x["fbox"] > 0 else T("faint")))
        c.setBackground(QColor(T("warn_soft")) if changed else QColor(0, 0, 0, 0))
        c.setToolTip((tr("手动改过：系统建议 {0:,} 箱", x["sbox"]) if x.get("sbox") is not None else
                      tr("手动改过：门店清单 {0:,} 箱", x["lbox"])) if changed else tr("双击修改箱数，0 = 不订"))
        self.table.item(k, PCS_COL).setText(f"{x['final']:,}" if _pk(x) > 0 else "")
        # 订后够卖：跟着确定箱数变。红 = 不到一个订货周期（下次到货前卖断），黄 = 超过凑单天数（压货）
        a = self.table.item(k, AFTER_COL)
        text, tok, tip = self._after_info(x)
        a.setText(text)
        a.setForeground(QColor(T(tok)))
        f = a.font()
        f.setBold(tok in ("err", "warn"))
        a.setFont(f)
        a.setToolTip(tip)
        am = self.table.item(k, AMOUNT_COL)
        if am is not None:
            am.setText(self._amount_text(x))
        ch = self.table.item(k, _col(CHANGE_COL))
        if ch is not None:
            d = int(x["fbox"]) - int(x["lbox"])
            ch.setText(self._change_text(x))
            ch.setForeground(QColor(T("accent") if d > 0 else T("warn") if d < 0 else T("faint")))

    def _refresh_rows(self, idx):
        """只重画这几行（改一个调货不用整张表重画）"""
        was = self._filling
        self._filling = True
        self.table.blockSignals(True)
        for i in idx:
            k = self.ctl.pos.get(i)
            if k is not None:
                self._fill_row(i, k)
        self.table.blockSignals(False)
        self._filling = was
        if self._cur_i in idx:
            self._show_drawer(self._cur_i)

    def _show(self, i):
        k = self.ctl.pos.get(i)
        if k is None:
            return
        was = self._filling
        self._filling = True
        self.table.blockSignals(True)
        self._style_edit(i, k)
        self.table.blockSignals(False)
        self._filling = was
        if i == self._cur_i:
            self._show_drawer(i)

    def _edited(self, item):
        if self._filling or item.column() != EDIT_COL:
            return
        i = self.ctl.row_at(item.row())
        if i is None:
            return
        try:
            n = float(item.text().strip().replace(",", "") or 0)
            if n < 0 or n != int(n):
                raise ValueError
        except ValueError:
            self.win.show_toast(tr("箱数要填 0 或正整数"))
            n = None
        if n is not None and int(n) != self.rows[i]["fbox"]:
            self.freeze_transfers()                   # 改了确定箱数：别的行的调货不跟着变
            _set_box(self.rows[i], int(n))
        # 等编辑框关掉再更新这一行（编辑中途改格子会让程序卡住）
        QTimer.singleShot(0, lambda: (self._show(i), self._summary(), self._plan_timer.start()))

    def _set_all(self, which):
        if which == "sugg":
            self.transfer_ov = {}                     # 人工改的调货也恢复成系统算的（解冻，按设置重新建议）
            self.transfer_frozen = False
        for x in self.rows:
            if which == "sugg" and x.get("sbox") is not None:
                _set_box(x, x["sbox"])
            else:
                _set_box(x, x["lbox"])
        self._replan()

    def _filter(self, *_):
        if hasattr(self, "ctl"):
            self.ctl.render()
        self._fill_prob()
        self._summary()

    def _summary(self):
        cnt = {v: 0 for v in R.VERDICT_RANK}
        for x in self.rows:
            k_ = self._verdict(x)[0]
            cnt[k_] = cnt.get(k_, 0) + 1
        n_edit = sum(1 for x in self.rows if _edited(x))
        txt = tr("{0} 个商品：照清单订 {1}，多订 {2}，少订 {3}，不订 {4}，建议调货 {5}，数据库没有 {6}。手动改了 {7} 个。",
                 len(self.rows), cnt["same"], cnt["more"], cnt["less"], cnt["none"], cnt["transfer"], cnt["nodb"], n_edit)
        # 采购金额估算：最便宜那家报价的件价（没有报价的用数据库进价 PrezzoForn）× 件数；实际金额以「开始处理」后的比价为准
        def price(x):
            try:
                v = float(x.get("price") or 0)
                return v if v == v else 0.0
            except (TypeError, ValueError):
                return 0.0
        amt_file = sum(price(x) * x["lbox"] * _pk(x) for x in self.rows)
        amt_final = sum(price(x) * x["fbox"] * _pk(x) for x in self.rows)
        no_price = sum(1 for x in self.rows if x["fbox"] > 0 and not price(x))
        if amt_file or amt_final:
            txt += "\n" + tr("估算采购金额（有报价的按最便宜那家，没有的按数据库进价）：清单 €{0:,.2f} → 确定 €{1:,.2f}",
                             amt_file, amt_final)
            if no_price:
                txt += tr("（{0} 个商品没有进价，没算进去）", no_price)
        mv = getattr(self, "_moves", None) or []
        if mv:
            txt += "\n" + tr("门店间调货（按现在的确定件数）：{0} 个商品，少订 {1} 箱，货值 €{2:,.2f}", len(mv),
                             sum(m["colli_before"] - m["colli_after"] for m in mv), sum(m["value"] for m in mv))
        self._money = (amt_file, amt_final)
        self._update_view_counts(cnt)
        self._status(*self._shown)
        if hasattr(self, "page_sum"):
            self.page_sum.setText(txt)

    # ---------- 导出 / 确认 ----------
    def export(self):
        if not self.rows:
            return
        from openpyxl import Workbook
        from openpyxl.styles import Font
        tag = "_".join(str(R.STORES.get(k, k)) for k in self.meta["stores"])
        name = f"清单检查_{tag}_{datetime.now():%Y%m%d_%H%M}.xlsx"
        path, _ = QFileDialog.getSaveFileName(self.win, tr("导出 Excel"), os.path.join(self.cfg.get("last_dir") or "", name),
                                              tr("Excel 文件 (*.xlsx)"))
        if not path:
            return
        d = self.meta["days"]
        wb = Workbook()
        ws = wb.active
        ws.title = "清单检查"
        ws.append(["门店", "判断", "商品编号", "条码", "名称", "本店库存", *[f"{x}天销量" for x in d], "库存够卖（天）", "箱规",
                   "清单箱数", "清单件数", "清单订后够卖（天）", "建议件数", "确定箱数", "变化（箱）", "确定订后够卖（天）",
                   "调货", "原因", "系统说明"])
        for c in ws[1]:
            c.font = Font(bold=True)
        def num(v):
            return None if v is None else ("∞" if math.isinf(v) else round(v))

        for x in self.rows:
            ws.append([R.STORES.get(x.get("store"), ""), self._verdict(x)[1], x["code"], x["ean"], x["name"], x["store_inv"],
                       *x["sales"], num(_cover(x)),
                       x["pack"] or None, x["lbox"], x["file_pcs"],
                       num(_cover(x, x["file_pcs"])) if (x.get("file_pcs") or 0) > 0 else None,
                       x["suggested"] if x["in_db"] else None, x["fbox"], self._change_text(x),
                       num(self._arrive_days(x)), self._transfer_tip(x),
                       R.reason_text(x.get("inv0"), x.get("daily0"), x.get("cycle"), x.get("suggested"), x.get("target"),
                                     x.get("in_db", True)), x["decision"]])
            ws.cell(ws.max_row, 4).number_format = "@"
        ws.freeze_panes = "A2"
        try:
            wb.save(path)
            self.win.show_toast(tr("已导出"))
        except Exception as e:
            QMessageBox.critical(self.win, tr("导出"), tr("导出失败：{0}", e))

    def _price_dir(self):
        """共用的供应商报价文件夹：比价根目录（或现在的总文件夹）下面的 distribuire"""
        import workspace as W
        root = self.win.ws_root() if hasattr(self.win, "ws_root") else self.batch_root()
        return W.price_dir(root) if root else ""

    def _read_quotes(self, price_dir, log):
        """读 price_dir 里的全部报价：{供应商名: {条码: 报价}}；没有文件夹返回 None。可以在后台线程里跑"""
        if not price_dir or not os.path.isdir(price_dir):
            return None
        com = False
        try:                        # 个别报价还要用 Excel 算公式：后台线程里先准备好 Excel 调用
            import pythoncom
            pythoncom.CoInitialize()
            com = True
        except Exception:
            pass
        try:
            return self._read_quote_files(price_dir, log)
        finally:
            if com:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    def _apply_quotes(self, rows, price_dir, log, files=False):
        """读 price_dir 里的报价，检查清单的行改用最便宜那家的每箱件数和件价：(改了几行, 读了几份报价)
        files：已经提前读好的报价（_read_quotes 的结果）；不给就现在读"""
        if files is False:
            files = self._read_quotes(price_dir, log)
        if files is None:
            log(tr("[检查清单] 没找到供应商报价文件夹，箱规和件价用数据库的"))
            return 0, 0
        import price_check as PC
        if not files:
            log(tr("[检查清单] 供应商报价文件夹里没有报价，箱规和件价用数据库的"))
            return 0, 0
        n = PC.apply_quotes(rows, PC.best_quotes(files))
        for x in rows:                                  # 这几家报价里都没有（比价时会成为缺失）
            x["in_quote"] = bool(x.get("quote_sup"))
        diff = sum(1 for x in rows if x.get("quote_sup") and x.get("db_pack") and abs(x["pack"] - x["db_pack"]) > 1e-9)
        log(tr("[检查清单] {0} 份报价：{1} 个商品改用最便宜那家的箱规和件价，其中 {2} 个箱规和数据库不一样",
               len(files), n, diff))
        return n, len(files)

    def _read_quote_files(self, price_dir, log):
        import service as SV
        import price_check as PC
        import supplier_names as SN
        import workspace as W
        files = {}
        for f in sorted(os.listdir(price_dir)):
            low = f.lower()
            if not low.endswith((".xlsx", ".xls")) or f.startswith(("~", "$")) or not W.is_price_name(f):
                continue
            try:
                name = SN.label_of(f, self.settings)
                files[name if name not in files else f"{name} ({SN.stem(f)})"] = SV.read_price_file(os.path.join(price_dir, f))
            except Exception as e:
                log(tr("⚠ 报价文件读不了，跳过：{0}（{1}）", f, e))
        return files

    def batch_root(self):
        """
        比价根目录（下面自动建 批发商比价\\日期\\门店）：现在的总文件夹已经是某一批的门店文件夹 → 它的根目录；
        否则上次用的根目录；再否则现在的总文件夹
        """
        cur = self.win.folder_edit.text().strip()
        r = R.batch_root_of(cur)
        if r:
            return r
        saved = self.settings.get("wholesale_root")
        if saved and os.path.isdir(saved):
            return saved
        return cur if cur and os.path.isdir(cur) else ""

    def _to_order(self, sid):
        return sum(1 for x in self.rows if x.get("store") == sid and x["fbox"] > 0)

    def transfer_plan(self):
        """按现在的确定件数算门店间调货：(调货单, {(门店, 商品编号): 调货后还要订的件数})"""
        import store_orders as SO
        import transfers as TR
        so = self.settings.get("store_orders") or {}
        wt = TR.wholesale_raw(self.settings)          # 批发商比价自己的调货设置
        far = TR.excluded_stores(wt, (dict(SO.DEFAULT_FILL, **(so.get("fill") or {}))).get("far_stores"))
        by_store = {}
        for x in self.rows:                       # 调货按「真正要的件数」算（确定箱数没改就是系统算的件数，零头才能调）
            by_store.setdefault(x.get("store"), []).append(dict(x, final=self._need_pcs(x)) if _can_transfer(x) else
                                                          dict(x, final=0))
        snap = self.meta.get("snap")
        if not snap:
            return [], {}
        return R.plan_batch_transfers(by_store, snap, wt, far, getattr(self, "transfer_ov", None),
                                      auto=not getattr(self, "transfer_frozen", False))

    def confirm(self):
        if not self.rows:
            return
        import workspace as W
        moves, new_final = self.transfer_plan()
        # 文件夹不用选：工作文件夹\4 比价结果\今天\门店（没有要订的门店不建）
        root_dir = self.win.ws_root() if hasattr(self.win, "ws_root") else self.batch_root()
        if not root_dir:
            QMessageBox.warning(self.win, tr("确认，开始比价"), tr("还没有工作文件夹。"))
            return
        folders = {sid: R.batch_store_folder(root_dir, sid) for sid in self.meta["stores"] if self._to_order(sid)}
        made, problems = [], []
        tag = datetime.now().strftime("%Y%m%d_%H%M")
        day_dir = R.batch_folder(root_dir)
        os.makedirs(day_dir, exist_ok=True)
        all_tf = None
        if moves:                                  # 整批的调货单放在这一天的文件夹里（每家店的也放一份在它自己的文件夹）
            try:
                import store_orders as SO
                all_tf = SO.export_transfers(moves, os.path.join(day_dir, f"调货单_全部门店_{tag}.xlsx"))
            except Exception as e:
                problems.append(tr("调货单没写上：{0}", e))
        for sid in self.meta["stores"]:
            if sid not in folders:                 # 没有要订的：不建文件夹、不生成主订单
                continue
            root = R.make_store_folder(folders[sid])
            if hasattr(self.win, "sync_prices"):
                self.win.sync_prices(root)             # 共用的供应商报价（工作文件夹\\2 供应商报价）复制一份过来
            try:
                n_el = W.copy_elim(root_dir, sid, root)  # 这家店的扣减文件复制进它的 eliminare
                if n_el:
                    self.win.log(tr("[文件] {0}：扣减文件 {1} 个", R.STORES.get(sid), n_el))
            except Exception as e:
                problems.append(tr("{0}：扣减文件没复制过去：{1}", R.STORES.get(sid), e))
            rows = []
            for x in self.rows:
                if x.get("store") != sid:
                    continue
                y = dict(x)
                left = new_final.get((sid, str(x.get("code"))))
                if left is not None:                       # 调货以后还要订的箱数
                    _set_box(y, int(math.ceil(left / _pk(x) - 1e-9)) if _pk(x) > 0 else 0)
                rows.append(y)
            mine = [m for m in moves if sid in (m["from"], m["to"])]
            try:
                path, written, skipped = R.write_checked_order(rows, root, sid)
            except Exception as e:
                problems.append(tr("{0}：写入失败：{1}", R.STORES.get(sid), e))
                continue
            tf = None
            if mine:
                try:
                    import store_orders as SO
                    tf = SO.export_transfers(mine, os.path.join(root, f"调货单_{R.STORES.get(sid)}_{tag}.xlsx"))
                except Exception as e:
                    problems.append(tr("{0}：调货单没写上：{1}", R.STORES.get(sid), e))
            if True:                                   # 一定要存（没查到其他门店库存也存空的）：没有快照的主订单会被当成旧系统文件
                try:
                    R.save_snapshot(path, sid, self.meta.get("snap") or {}, rows, transfers=mine, transfers_file=tf)
                except Exception as e:
                    problems.append(tr("{0}：门店库存快照没存上：{1}", R.STORES.get(sid), e))
            try:                                       # 发给门店的提醒：非 Griglia 商品下次别加、这次批发商没货的
                rep = write_problem_report(self.rows, os.path.join(root, report_name(sid)), store=sid,
                                           url=self.settings.get("image_url") or None)
                if rep:
                    self.win.log(tr("✔ 门店报告：{0}", rep))
            except Exception as e:
                problems.append(tr("{0}：门店提醒没写上：{1}", R.STORES.get(sid), e))
            self.win.log(tr("✔ 采购主订单：{0}（{1} 个商品）", path, written))
            if skipped:
                self.win.log(tr("⚠ {0} 个商品没有条码，没写进主订单：{1}", len(skipped),
                                "、".join(f"{x['code']} {x['name'][:20]}" for x in skipped[:10])))
            made.append((sid, root, written, len(skipped)))
        if not made:
            if problems:
                QMessageBox.critical(self.win, tr("确认，开始比价"), "\n".join(problems))
            else:
                QMessageBox.information(self.win, tr("确认，开始比价"), tr("这几家门店这次都不用订货，不用比价。"))
            return
        if moves:
            self.win.log(tr("✔ 门店间调货：{0} 个商品，少订 {1} 箱", len(moves),
                            sum(m["colli_before"] - m["colli_after"] for m in moves)))
            if all_tf:
                self.win.log(tr("✔ 调货单（全部门店）：{0}", all_tf))
        # 这一批是哪几家门店（批发商比价页上能一家一家看结果）
        self.settings["wholesale_batch"] = {"time": datetime.now().isoformat(timespec="minutes"),
                                            "stores": {str(sid): root for sid, root, _, _ in made}}
        save_settings(self.settings)
        # 用过的扣减文件挪进 _已用（下次不会再扣一遍；今天再比价用的是比价文件夹里那份）
        try:
            W.archive_used(root_dir, [sid for sid, _, _, _ in made])     # 门店清单留着：想重新比价直接再点「开始」
        except Exception as e:
            problems.append(tr("用过的清单 / 扣减文件没挪走：{0}", e))
        msg = tr("已生成 {0} 家门店的主订单（{1}）", len(made), "、".join(
            f"{R.STORES.get(sid)} {n}" for sid, _, n, _ in made))
        if moves:
            msg += "；" + tr("门店间调货 {0} 个商品，少订 {1} 箱，调货单在 {2}", len(moves),
                            sum(m["colli_before"] - m["colli_after"] for m in moves), day_dir)
        if problems:
            msg += "\n⚠ " + "；".join(problems)
        self.win.log(msg)
        self.lists = []
        self._refresh_lists()
        self.summary_row.hide()
        # 检查窗口关掉，回到批发商比价，自动一家一家比价
        self.window.close()
        if self.win.isMinimized():
            self.win.showNormal()
        self.win.raise_()
        self.win.activateWindow()
        if hasattr(self.win, "go_page"):
            self.win.go_page("order")
        self.win.run_batch([root for _, root, _, _ in made], msg, bool(problems))
