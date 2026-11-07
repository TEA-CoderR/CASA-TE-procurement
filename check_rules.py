"""
检查门店订货清单的业务规则（不依赖界面）：建议箱数（init_boxes）、断货补算、虚库存、少订上限、
问题分类（_problem / _situation）、门店报告（write_problem_report）。
2026-11-06 第 0 阶段从 order_check.py 原样搬过来（order_check 照旧能用这些名字），命令行 / 以后的服务器也用。
"""
import math
from datetime import datetime

from i18n import tr
import reorder_service as R


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
    """发给门店的报告里这家店的商品：(① Griglia 商品这周批发商都没有, ② 非 Griglia 商品)。
    ① 用户 2026-11-01 又要回来、放在最上面（一般很少；有的是供应商不做了，有的是换了规格 / 条码，留一列手填新条码）；
    ② 批发商有货的排前面，再按品牌供应商有没有、名称"""
    mine = [x for x in rows if x.get("store") == store]
    a = sorted((x for x in mine if _problem(x) == "noquote"), key=lambda y: str(y.get("name") or "").lower())
    b = sorted((x for x in mine if _tab_of(_problem(x)) == "nogrid"),
               key=lambda y: (_problem(y) == "nogrid_noq", not y.get("brand"), str(y.get("name") or "").lower()))
    return a, b


def report_name(store, when=None):
    """发给门店的报告文件名（意大利文）：Articoli_da_verificare_FOIANO_0111.xlsx"""
    return f"Articoli_da_verificare_{R.STORES.get(store, store)}_{(when or datetime.now()):%d%m}.xlsx"


def _advice(x):
    """门店报告「Consiglio」列：(文字, 颜色)。品牌供应商有 → 建议直接向它订（用户 2026-11-01）"""
    if x.get("brand"):
        return f"Ordinare direttamente da {x['brand']}", "GREEN"
    if _problem(x) != "nogrid_noq":
        return "Sconsigliato: articolo fuori Griglia", "AMBER"
    return "Non disponibile: non ordinare", "RED"


def write_problem_report(rows, path, store=None, url=None, when=None, sources=None):
    """
    发给门店的意大利文报告（2026-11-01 用户：格式漂亮、颜色、不要无关的数据、IMAGE 函数读我们网站的图片、文件要小）。
    一家店一个工作表（store = None：每家有待核对商品的店各一页），每页：
      标题（门店 · 日期）
      ① Articoli Griglia non disponibili dai grossisti questa settimana：Foto EAN Cod.Art. Descrizione Fornitore
         Colli richiesti + EAN alternativo（空着、浅黄，用户自己填新条码：换了规格 / 条码的）
      ② Articoli non in Griglia – si consiglia di non ordinarli：… Colli ordinati、Disp. grossisti（这周几家批发商报价里有没有）、
         Disp. fornitore diretto（门店配货的品牌供应商 SCHEDA 里有没有，x["brand"]）、Consiglio（品牌供应商有 → 直接向它订）
    照片 = =IMAGE("https://img.casaete.top/prodotti/<EAN>/01.webp","",3,48,48)（Excel 365 打开时自己读，文件里不放图）。
    每段是一个 Excel 表格（自带筛选按钮，一页里两段都能筛）。这家店没有待核对的商品返回 None
    """
    from openpyxl import Workbook
    from xlsx_report import XL_FONT
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableStyleInfo
    import product_images as PI
    url = url or PI.DEFAULT_URL
    when = when or datetime.now()
    stores = [store] if store is not None else \
        sorted({x.get("store") for x in rows if _tab_of(_problem(x)) in ("nogrid", "noquote")}, key=lambda v: v or 0)
    wb = Workbook()
    wb.remove(wb.active)
    INK, MUTED, LINE = "1F2A37", "6B7280", "E5E7EB"
    COLORS = {"GREEN": ("E7F5EC", "1E7A3C"), "AMBER": ("FFF4E0", "A15C07"), "RED": ("FDECEC", "B42318")}
    thin = Side(style="thin", color=LINE)
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="center", wrap_text=True, indent=1)
    widths = (9, 16, 10, 36, 20, 10, 16, 12, 14, 32)
    ncol = len(widths)
    IMG = 48                      # 照片显示 48 × 48 像素
    names = " · ".join(str(s) for s in (sources or []) if str(s).strip())
    who = names or "i nostri grossisti"
    base = ["Foto", "EAN", "Cod. Art.", "Descrizione", "Fornitore", "Colli richiesti"]
    sec_a = dict(title="Articoli Griglia non disponibili dai grossisti questa settimana", fill="FFF4E0", ink="A15C07",
                 note=f"Non presenti nei listini di questa settimana di {who}. Se l'articolo ha un nuovo codice, "
                      f"scrivere l'EAN nella colonna «EAN alternativo».",
                 heads=base + ["EAN alternativo"])
    sec_b = dict(title="Articoli non in Griglia – si consiglia di non ordinarli", fill="FDECE2", ink="B4471A",
                 note=f"Disp. grossisti = presente nei listini di questa settimana di {who}. "
                      f"Disp. fornitore diretto = presente nelle schede d'ordine dei fornitori diretti.",
                 heads=base + ["Colli ordinati", "Disp. grossisti", "Disp. fornitore diretto", "Consiglio"])
    head_fill = {7: "FEF9C3"}                       # 表头颜色：要手填的黄、两列「有没有」青、建议绿
    n_tab = 0
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
        ws["A1"].font = Font(name=XL_FONT, size=16, bold=True, color="FFFFFF")
        ws["A1"].fill = PatternFill("solid", fgColor="1F3A5F")
        ws["A1"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[1].height = 32
        ws.merge_cells(f"A2:{last}2")
        ws["A2"] = f"CASA&TE · {when:%d/%m/%Y} · {sum(len(p) for p in parts)} articoli"
        ws["A2"].font = Font(name=XL_FONT, size=10, color=MUTED)
        ws["A2"].alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.row_dimensions[2].height = 20
        ws.freeze_panes = "A3"                       # 标题一直看得到；每段的表头往下翻时 Excel 显示在列号上
        r = 4
        for sec, xs in ((sec_a, parts[0]), (sec_b, parts[1])):
            if not xs:
                continue
            heads = sec["heads"]
            cols = len(heads)
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncol)
            c = ws.cell(r, 1, f"{sec['title']}  ({len(xs)})")
            c.font = Font(name=XL_FONT, size=12, bold=True, color=sec["ink"])
            c.fill = PatternFill("solid", fgColor=sec["fill"])
            c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
            ws.row_dimensions[r].height = 24
            r += 1
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncol)
            nc = ws.cell(r, 1, sec["note"])
            nc.font = Font(name=XL_FONT, size=9, color=sec["ink"])
            nc.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True, indent=1)
            ws.row_dimensions[r].height = 28
            r += 1
            hr = r
            for j, h in enumerate(heads, 1):
                hc = ws.cell(r, j, h)
                fill = head_fill.get(j) if sec is sec_a else \
                    ("DDF3F1" if j in (8, 9) else "E7F5EC" if j == 10 else None)
                hc.font = Font(name=XL_FONT, size=10, bold=True, color=INK)
                hc.fill = PatternFill("solid", fgColor=fill or "F3F4F6")
                hc.alignment = center
                hc.border = box
            ws.row_dimensions[r].height = 30
            r += 1
            for k, x in enumerate(xs):
                ean = PI.norm_ean(x.get("ean"))
                vals = [f'=_xlfn.IMAGE("{url.format(ean=ean)}","",3,{IMG},{IMG})' if ean else "", ean or str(x.get("ean") or ""),
                        str(x.get("code") or ""), str(x.get("name") or ""),
                        str(x.get("sup_name") or x.get("quote_sup") or ""), int(x.get("lbox") or 0)]
                tags = {}
                if sec is sec_a:
                    vals.append(None)                   # EAN alternativo：用户手填
                else:
                    # 批发商都订不到的：比价时哪家都分不出去（进缺失报告），门店一件也收不到 → 订的箱数写 0
                    can = _problem(x) != "nogrid_noq"
                    adv, tone = _advice(x)
                    vals += [int(x.get("fbox") or 0) if can else 0, "Sì" if can else "No",
                             "Sì" if x.get("brand") else "No", adv]
                    tags = {8: "GREEN" if can else "RED", 9: "GREEN" if x.get("brand") else "RED", 10: tone}
                zebra = "FAFBFC" if k % 2 else None
                for j, v in enumerate(vals, 1):
                    dc = ws.cell(r, j, v)
                    dc.border = box
                    dc.alignment = left if j in (4, 5, 10) else center
                    dc.font = Font(name=XL_FONT, size=10, color=INK, bold=(j == 4))
                    bg = zebra
                    if j in tags:
                        bg, ink = COLORS[tags[j]]
                        dc.font = Font(name=XL_FONT, size=10, bold=True, color=ink)
                    elif sec is sec_a and j == 7:
                        bg = "FFFBEB"                   # 要手填的格：浅黄
                    if bg:
                        dc.fill = PatternFill("solid", fgColor=bg)
                ws.row_dimensions[r].height = 42          # 放得下 48 像素的照片
                r += 1
            # 每段一个 Excel 表格：自带筛选按钮（一页两段都能筛；工作表的「筛选」一页只能有一个），照片列不放进去
            n_tab += 1
            t = Table(displayName=f"Articoli{n_tab}", ref=f"B{hr}:{get_column_letter(cols)}{r - 1}")
            t.tableStyleInfo = TableStyleInfo(name="TableStyleLight1", showRowStripes=False)
            ws.add_table(t)
            r += 1
        foot = ws.cell(r, 1, "Foto: si caricano da internet in Excel 365.")
        foot.font = Font(name=XL_FONT, size=9, color=MUTED)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncol)
        ws.page_setup.orientation = "landscape"
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


# 2026-11-07 用户定：尽可能少砍门店清单（采购部常常又加回去），只有库存真的多才砍：
#   现在库存够卖 < keep_days 天 → 照清单订，一箱不砍；keep_days ~ free_days 天 → 最多砍掉清单的 max_cut；
#   ≥ free_days 天 → 才按系统建议（可以少很多 / 不订）。以前按「订货周期」算，周期变成 14 天检查间隔后会砍得更多
REVIEW_DEFAULTS = {"max_cut": 0.5, "keep_days": 30.0, "free_days": 60.0}


def review_rules(cfg=None):
    """检查清单少砍的规则（DETERSIVO采购「设置」里改）：库存够卖几天以下照清单；几天以上才可以不订；中间最多砍几成"""
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
    2026-11-07（用户：尽可能少砍门店清单，采购部常常又加回去；只有库存真的多才砍，少量库存保留）改成按「现在库存够卖几天」：
      < keep_days（30）天 → 照清单（kept = True）；keep_days ~ free_days（60）天 → 最多砍掉 max_cut（limited = True）；
      ≥ free_days 天 → 按系统建议。没销量、库存不到一箱的也算库存少。cover_now = 用来判断的够卖天数
    2026-11-03：在途（x["transit_pcs"]，已经订了还没到货）最后从建议箱数里扣（在途件数 ÷ 每箱件数，整箱扣；transit_cut = 扣了几箱）
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
        if math.isinf(cover) and max(0.0, float(x.get("inv0") or 0)) < pk:
            cover = 0.0                                # 没销量、库存还不到一箱：算「库存少」，照清单
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
        x.pop("kept", None)
        x["cover_now"] = cover
        if is_new_item(x):
            x["protect"] = "new"
            x["sbox"] = x["lbox"]
        else:
            sb = min(sys_, x["lbox"])                  # 先不多订：最多订到门店清单的箱数
            if sb < x["lbox"] and cover < rr["keep_days"]:
                sb = x["lbox"]                         # 库存不多：照门店清单，一箱不砍
                x["kept"] = True
            elif sb < x["lbox"] and cover < rr["free_days"]:
                floor = int(math.ceil(x["lbox"] * (1 - rr["max_cut"]) - 1e-9))
                if sb < floor:
                    sb = floor                         # 库存够一阵子但不算多：最多砍掉 max_cut
                    x["limited"] = True
            x["sbox"] = sb
        # 在途（已经订了还没到货，order_record；2026-11-03 用户：从建议量里扣）：整箱扣，零头不扣
        x.pop("transit_cut", None)
        tp = float(x.get("transit_pcs") or 0)
        if tp > 0 and x["sbox"]:
            cut = min(x["sbox"], int(tp / pk + 1e-9))
            if cut > 0:
                x["sbox"] -= cut
                x["transit_cut"] = cut
        _set_box(x, x["sbox"])
