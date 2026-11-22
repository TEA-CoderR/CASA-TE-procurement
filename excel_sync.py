"""
采购订单跟踪 ↔ PIANO ACQUISTO MESILE（同步文件夹里的 Excel）双向同步：程序 → Excel 这一半。
（2026-11-04 用户定：直接写回原表；同一格两边都改了谁最后改的听谁；程序里新增的单「确认已下单」以后才写进 Excel）

Excel → 程序：order_tracking.import_file / sync_excel（三方比较，xbase = 上次从 Excel 读到的值）。
程序 → Excel：pending() 算出要写的：
  update  已经在 Excel 里的单（Excel 读进来的 / 程序写进去的），程序里的值和 Excel 里的（xbase）不一样的字段
  append  程序里新增的单（手动新建、总仓 / 门店 / DETERSIVO 自动登记后确认的），不是待登记 / 已取消，还没写进 Excel
  clear   程序里删掉的、Excel 里还有的单 → 清空那一行
  budget  程序里改的预算
  writeback() 一次写完：Excel / WPS 正开着（有 ~$ 锁文件或文件被锁）就不写，下次再试；先把 Excel 最新的读进来，
  在临时副本上改，读回来核对，原文件备份到「Tracking ordini (programma)\\Backup Excel」（留最近 30 份），
  原文件这段时间没被别人存过才替换；写完记 xbase / xid / xstate["write"]。
同步盘冲突副本（2026-11-04 真实发生：14:43 / 14:48 程序写进去的版本被同步盘改名成
「…_LAPTOP-xxx_…_Conflict.xlsx」，原表换回了 14:27 的旧版本，程序跟着把这 10 处当成「Excel 里改回去了」）：
  merge_conflicts() 每次写之前看有没有冲突副本，有就合并回程序（程序写的被换掉的 → 恢复并重新写；副本里有人手填、
  原表没有的 → 补回来），然后把副本挪到「Backup Excel\冲突副本」，文件夹里不留。
谁来写：改这张单的那台电脑写；别的电脑等 10 分钟还没人写才接手（几台电脑同时开着，免得一起写出冲突副本）。
不依赖界面，测试 test_excel_sync.py。
"""
import os
import copy
import shutil
import tempfile
from datetime import date, datetime, timedelta, timezone

import order_tracking as OT

WRITE_FIELDS = ("date", "supplier", "buyer", "amount", "confirmed", "arrived", "state", "note")
BACKUP_DIR = "Backup Excel"
KEEP_BACKUPS = 30
# 「写不进去的不再重试」的记录换代号：读 PIANO 的规矩改了（2026-11-22 表头 Fornitore 空着的块也认）→ 以前写不进去的再试一次
FAILED_VER = 2


def _failed(st):
    f = st.xstate.get("failed") or {}
    return f if f.get("_ver") == FAILED_VER else {"_ver": FAILED_VER}


TAKEOVER_MIN = 10               # 别人改的单，10 分钟还没写进 Excel 就由这台电脑写
STUCK_TEXT = "卡住了"
PARTIAL_TEXT = "到了一部分"
CANCEL_TEXT = "ANNULLATO"
_MISS = object()
REWRITE = "\x01"              # xbase["_w_字段"] 是这个 = 要重新写一次（冲突副本里恢复回来的）
CONFLICT_DIR = "冲突副本"       # Backup Excel 下面：合并过的同步盘冲突副本挪到这里
# 程序写进 Excel 的格子标浅蓝底（2026-11-04 用户要标记，颜色让我自己挑）：原表数据区用了 灰（主题色）、红 FF0000（超期）、
# 橙 FFC000，表头用 绿 92D050 / ADD88D / 主题绿；字体有紫 7030A0、红、蓝 2D529F / 0070C0 → 浅蓝 DDEBF7 原表没用过，字都看得清
MARK_RGB = "FFDDEBF7"
UNMARKED = "\x00"             # xbase["_m_字段"] 是这个 = 标记已经去掉了
_FIELD_COLS = {"date": (0,), "supplier": (1,), "buyer": (2,), "amount": (3,), "confirmed": (4,), "arrived": (5, 6),
               "state": (5,)}


# ===================================================================== 程序里的单 → Excel 里该是什么样
def excel_view(o):
    """程序里的单写进 Excel 再读回来应该是什么（和 read_excel 读出来的字段一样）"""
    st = o.get("state")
    amt = o.get("amount")
    return {"date": OT.iso(o.get("date")), "supplier": str(o.get("supplier") or "").strip() or None,
            "buyer": str(o.get("buyer") or "").strip() or None,
            "amount": round(float(amt), 2) if isinstance(amt, (int, float)) else None,
            "confirmed": OT.iso(o.get("confirmed")), "arrived": OT.iso(o.get("arrived")),
            "state": st if st in ("partial", "stuck", "cancel") else None,
            "note": str(o.get("note") or "").strip() or None}


def _owner(st, oid, fields):
    """这几个字段最后是谁、什么时候改的 (谁, 时间)"""
    m = st.meta.get(oid, {})
    ws = [m[f] for f in fields if f in m and m[f][2] != "excel"]
    if not ws:
        return None, None
    w = max(ws, key=lambda x: x[0])
    return w[1], w[0]


def _mine(owner, t, me, now):
    if not owner or owner == me:
        return True
    try:
        age = now - datetime.strptime(t[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    return age > timedelta(minutes=TAKEOVER_MIN)


def pending(st, me=None, now=None, everyone=False):
    """
    要写进 Excel 的修改：[(类型, 单号, 字段 / 值)]。everyone=False：只要这台电脑该写的（自己改的、或者别人改了
    10 分钟还没写的）；True：全部（界面上显示「几处等待写入 Excel」用）
    """
    now = now or datetime.now(timezone.utc)
    ops = []
    bad = _failed(st)                                 # 上次写不进去的（同样的值不再每次重试）

    def skip(kind, oid, val):
        return bad.get(f"{kind}|{oid}") == repr(val)
    for oid, o in st.orders.items():
        base = st.xbase.get(oid)
        gone = bool(base and base.get("_gone"))          # 写进去过、后来被同步盘换掉了（冲突副本里恢复的）→ 重新加
        linked = base is not None and not gone and (o.get("origin") == "excel" or o.get("xid"))
        if linked:
            want = excel_view(o)
            if o.get("kind") == "deter":                 # DETERSIVO 单：确认 / 到货格写的是 DETER，不动
                want.pop("confirmed")
                want.pop("arrived")
            diff = {f: v for f, v in want.items()
                    if v != base.get(f) and v != base.get("_w_" + f, _MISS)
                    and not (v is None and f not in base)}
            if diff and not skip("update", oid, diff):
                who, t = _owner(st, oid, diff)
                if everyone or _mine(who, t, me, now):
                    ops.append(("update", oid, diff))
            # 程序写的格子后来在 Excel 里被人改了 → 去掉浅蓝标记（不然以为还是程序写的）
            un = [f for f in WRITE_FIELDS if base.get("_m_" + f, UNMARKED) != UNMARKED
                  and base.get(f) != base["_m_" + f] and f not in diff]
            if un:
                ops.append(("unmark", oid, un))
        elif gone or (base is None and o.get("origin") != "excel" and not o.get("xid")):
            s = OT.status(o)
            if s in ("draft", "cancel") or not o.get("date") or not o.get("store") or not o.get("supplier"):
                continue
            who, t = _owner(st, oid, ["origin", "draft", "date"])
            v = excel_view(o)
            if (everyone or _mine(who, t, me, now)) and not skip("append", oid, v):
                ops.append(("append", oid, v))
    for oid, src in st.deleted.items():
        b = st.xbase.get(oid)
        if src == "app" and b and not b.get("_cleared") and not skip("clear", oid, None):
            ops.append(("clear", oid, None))
    bb = st.xbase.get("_budget", {})
    for k, v in st.budgets.items():
        w = st.budget_meta.get(k)
        if k in bb and v != bb[k] and v != bb.get("_w_" + k, _MISS) and w and w[2] != "excel" \
                and not skip("budget", k, v):
            ops.append(("budget", k, v))
    return ops


# ===================================================================== 改工作簿
def _layouts(wb):
    out = {}
    for ws in wb.worksheets:
        hr, blocks = OT._layout(ws)
        if hr and blocks:
            out[ws.title] = (hr, blocks)
    return out


def _cell_key(ws, r, c):
    d, _ = OT.parse_date(ws.cell(r, c).value)
    sup = ws.cell(r, c + 1).value
    sup = str(int(sup)) if isinstance(sup, float) and sup == int(sup) else str(sup or "").strip()
    return OT.iso(d), OT.sup_key(sup)


def _find_row(wb, lay, o, base):
    """这张单在 Excel 的哪一行：先按记着的位置（ref = 表!供应商格），对不上（有人插了行）就在那家店那一块里按日期 + 供应商找"""
    ref = str(o.get("ref") or "")
    if "!" not in ref:
        return None
    sh, cell = ref.split("!", 1)
    if sh not in lay:
        return None
    ws = wb[sh]
    hr, blocks = lay[sh]
    from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
    try:
        col, row = coordinate_from_string(cell)
        sc = column_index_from_string(col) - 1
    except Exception:
        return None
    want = (base.get("date"), OT.sup_key(base.get("supplier")))
    blk = next((b for b in blocks if b[0] == sc), None)
    if blk and _cell_key(ws, row, sc) == want:
        return ws, blk, row
    blk = next((b for b in blocks if b[1] == o.get("store")), blk)
    if not blk:
        return None
    for r in range(hr + 1, ws.max_row + 1):
        if _cell_key(ws, r, blk[0]) == want:
            return ws, blk, r
    return None


def _comment(cell, text):
    from openpyxl.comments import Comment
    if text:
        cell.comment = Comment(text, "Programma")
    else:
        cell.comment = None


def _cols(blk, f):
    c, _s, note_col = blk
    if f == "note":
        return (note_col,) if note_col else (c + 1,)
    return tuple(c + k for k in _FIELD_COLS.get(f, ()))


def _mark(ws, blk, r, fields, on=True):
    """程序写的格子标浅蓝底 / 去掉标记"""
    from openpyxl.styles import PatternFill
    fill = PatternFill("solid", fgColor=MARK_RGB) if on else PatternFill(fill_type=None)
    for f in fields:
        for cc in _cols(blk, f):
            cell = ws.cell(r, cc)
            if on or (cell.fill is not None and cell.fill.fill_type and
                      str(getattr(cell.fill.fgColor, "rgb", "")).upper() == MARK_RGB):
                cell.fill = fill


def _write(ws, blk, r, fields):
    """把字段写进这一行（只写给的字段），写的格子标浅蓝"""
    _mark(ws, blk, r, fields)
    c, _store, note_col = blk
    if "date" in fields:
        d = OT.to_date(fields["date"])
        ws.cell(r, c).value = d
        if d:
            ws.cell(r, c).number_format = "dd/mm/yyyy;@"
    if "supplier" in fields:
        ws.cell(r, c + 1).value = fields["supplier"]
    if "buyer" in fields:
        ws.cell(r, c + 2).value = fields["buyer"]
    if "amount" in fields:
        ws.cell(r, c + 3).value = fields["amount"]
        if fields["amount"] is not None:
            ws.cell(r, c + 3).number_format = "[$€-410]\\ #,##0.00;\\-[$€-410]\\ #,##0.00"
    if "confirmed" in fields:
        d = OT.to_date(fields["confirmed"])
        ws.cell(r, c + 4).value = d
        if d:
            ws.cell(r, c + 4).number_format = "dd/mm/yyyy;@"
    if "arrived" in fields or "state" in fields:
        cur_state = fields.get("state", _MISS)
        arr = OT.to_date(fields["arrived"]) if "arrived" in fields else OT.parse_date(ws.cell(r, c + 5).value)[0]
        cell = ws.cell(r, c + 5)
        if cur_state == "stuck" and not arr:
            cell.value = STUCK_TEXT
        elif cur_state == "cancel":
            cell.value = CANCEL_TEXT
        else:
            cell.value = arr
            if arr:
                cell.number_format = "dd/mm/yyyy;@"
        if cur_state is not _MISS:
            _comment(cell, PARTIAL_TEXT if cur_state == "partial" else None)
        d0 = OT.parse_date(ws.cell(r, c).value)[0]
        days = (arr - d0).days if (arr and d0) else None
        ws.cell(r, c + 6).value = days
    if "note" in fields:
        if note_col:
            ws.cell(r, note_col).value = fields["note"]
        else:
            _comment(ws.cell(r, c + 1), fields["note"])


def _month_sheet(wb, lay, ym):
    """这个月的月份表；没有就照最新那张复制一张空的（和导出 PIANO 一样）"""
    for name, (hr, blocks) in lay.items():
        if OT._sheet_month(wb[name], hr, blocks) == ym:
            return wb[name]
    tpl_name = max(lay, key=lambda k: (len(lay[k][1]), -wb.sheetnames.index(k)))
    tpl = wb[tpl_name]
    hr, blocks = lay[tpl_name]
    ws = wb.copy_worksheet(tpl)
    ws.freeze_panes = tpl.freeze_panes
    wb._sheets.remove(ws)
    wb._sheets.insert(0, ws)
    name = OT.ITA_MONTHS[ym[1] - 1] + f"_{ym[0] % 100:02d}"
    ws.title = name if name not in wb.sheetnames else name + "_P"
    for c, _s, _n in blocks:
        for r in range(hr + 1, ws.max_row + 1):
            for cc in range(c, c + 8):
                ws.cell(r, cc).value = None
                ws.cell(r, cc).comment = None
        for r in range(1, hr):
            for cc in (c + 4, c + 5):
                v = ws.cell(r, cc).value
                if isinstance(v, (int, float)) or (isinstance(v, str) and v.startswith("=")):
                    ws.cell(r, cc).value = None
    lay[ws.title] = (hr, blocks)
    return ws


def _weeks(ws, hr, blk, iva):
    """这一块上面的每周单数 / 每周合计（和原表一样：第 1 周 = 1 号到第一个周日，之后周一到周日，第 6 周并进第 5 周）"""
    from openpyxl.utils import get_column_letter as L
    c = blk[0]
    week_rows = [r for r in range(1, hr) if "SETTIMANA" in OT._norm(ws.cell(r, c + 2).value)]
    if not week_rows:
        return
    weeks = {}
    for r in range(hr + 1, ws.max_row + 1):
        if ws.cell(r, c + 1).value in (None, ""):
            continue
        d, _ = OT.parse_date(ws.cell(r, c).value)
        if d:
            weeks.setdefault(OT._week_of(d), []).append(r)
    amt = L(c + 3)
    for w, row in enumerate(week_rows):
        rr = weeks.get(w) or []
        if w == len(week_rows) - 1:
            rr = [x for k, v in weeks.items() if k >= w for x in v]
        ws.cell(row, c + 4).value = len(rr) or None
        ws.cell(row, c + 5).value = (f"=SUM({amt}{min(rr)}:{amt}{max(rr)})" + ("*1.22" if iva else "")) if rr else None


def _append(wb, lay, o, fields):
    d = OT.to_date(fields["date"])
    ws = _month_sheet(wb, lay, (d.year, d.month))
    hr, blocks = lay[ws.title]
    blk = next((b for b in blocks if b[1] == str(o.get("store"))), None)
    if blk is None:
        raise ValueError(OT.STORE_NAMES.get(str(o.get("store")), str(o.get("store"))) + " ?")
    c = blk[0]
    last = hr
    for r in range(hr + 1, ws.max_row + 1):
        if ws.cell(r, c + 1).value not in (None, "") or ws.cell(r, c).value not in (None, ""):
            last = r
    r = last + 1
    src = last if last > hr else hr + 1
    for cc in range(c, c + 8):
        if r != src:
            ws.cell(r, cc)._style = copy.copy(ws.cell(src, cc)._style)
    _write(ws, blk, r, fields)
    _mark(ws, blk, r, WRITE_FIELDS)                   # 程序新增的单：整行浅蓝
    # 上面的每周单数 / 每周合计不动：同事自己维护（每周合计的范围原表已经预留好了行）
    return ws.title, r, c


def _budget(wb, lay, key, v):
    month, store = key.split("|")
    ym = (int(month[:4]), int(month[5:7]))
    for name, (hr, blocks) in lay.items():
        ws = wb[name]
        if OT._sheet_month(ws, hr, blocks) != ym:
            continue
        for c, s, _n in blocks:
            if s != store:
                continue
            k, n = OT._block_iva(ws, hr, c)
            iva = OT._is_iva(k, n, 0, 0)
            for r in range(1, hr):
                labels = [cc for cc in range(c, c + 8) if "BADGET" in OT._norm(ws.cell(r, cc).value)
                          or "BUDGET" in OT._norm(ws.cell(r, cc).value)]
                if not labels:
                    continue
                from openpyxl.cell.cell import MergedCell
                cells = [ws.cell(r, cc) for cc in range(labels[0] + 1, c + 8)]
                cells = [x for x in cells if not isinstance(x, MergedCell)]          # 合并格只有左上角能写
                target = next((x for x in cells if isinstance(x.value, (int, float))), None) \
                    or next((x for x in cells if x.value in (None, "")), None)
                if target is None:
                    return False
                target.value = round(v * (1 + OT.IVA), 2) if (iva and v is not None) else v
                return True
    return False


def apply_ops(src, dst, st, ops):
    """在 src 上做这些修改存成 dst。返回 {单号: (表, 行, 列)}（写了的单在哪一行）、[(类型, 单号, 原因)] 没写成的"""
    from openpyxl import load_workbook
    wb = load_workbook(src)
    lay = _layouts(wb)
    if not lay:
        raise ValueError("Excel 里没有月份表（表头「Data Ordine」）")
    where, failed = {}, []
    for kind, oid, val in ops:
        try:
            if kind == "budget":
                if not _budget(wb, lay, oid, val):
                    failed.append((kind, oid, "找不到这个月 / 门店的预算格"))
                continue
            o = st.orders.get(oid) or {}
            if kind == "append":
                where[oid] = _append(wb, lay, o, val)
                continue
            base = st.xbase.get(oid, {})
            hit = _find_row(wb, lay, o if kind != "clear" else {"ref": base.get("ref"), "store": base.get("store")}, base)
            if hit is None:
                failed.append((kind, oid, "Excel 里找不到这一行"))
                continue
            ws, blk, r = hit
            if kind == "clear":
                for cc in range(blk[0], blk[0] + 8):
                    ws.cell(r, cc).value = None
                    ws.cell(r, cc).comment = None
                _mark(ws, blk, r, WRITE_FIELDS, on=False)
            elif kind == "unmark":
                _mark(ws, blk, r, val, on=False)
            else:
                _write(ws, blk, r, val)
            where[oid] = (ws.title, r, blk[0])
        except Exception as e:
            failed.append((kind, oid, str(e)))
    try:
        wb.calculation.fullCalcOnLoad = True          # 每周合计等公式：Excel / WPS 打开时重新算
    except Exception:
        pass
    wb.save(dst)
    return where, failed


def _adopt_existing(st, src, ops, log):
    """
    要新加进 Excel 的单，Excel 里已经有一样的一行（门店 · 日期 · 供应商 · 金额都一样）：不再加一行，直接认这一行
    （2026-11-09：同一张单程序里登记了、Excel 里也手填了 / 两台电脑同步慢各写了一次 → Excel 里出现两行）。
    那一行已经是程序里另一张单的：也不加（程序里有两张，留给人合并），日志写一句。返回剩下要写的 ops
    """
    apps = [x for x in ops if x[0] == "append"]
    if not apps:
        return ops
    rows, _b, _s = OT.read_excel(src)
    owner = {o["xid"]: oid for oid, o in st.orders.items() if o.get("xid")}
    owner.update({oid: oid for oid, o in st.orders.items() if o.get("origin") == "excel"})
    free, taken = {}, {}
    for r in rows:
        k = dup_key(st, r)
        if r["id"] in owner:
            taken.setdefault(k, owner[r["id"]])
        else:
            free.setdefault(k, []).append(r)
    keep, sets, bases = [], [], []
    for op in ops:
        kind, oid, _v = op
        if kind != "append":
            keep.append(op)
            continue
        k = dup_key(st, st.orders.get(oid) or {})
        if free.get(k):
            r = free[k].pop(0)
            f = {"ref": r["ref"], "xid": r["id"]}
            o = st.orders.get(oid) or {}
            if r.get("supplier") and r["supplier"] != o.get("supplier"):
                f["supplier"] = r["supplier"]           # 同一家（写法不同）：照 Excel 里的写法，不去改表
            sets.append((oid, f))
            nb = {f: r.get(f) for f in OT.EXCEL_FIELDS if f != "origin"}
            nb["_gone"] = False
            bases.append((oid, nb))
            log(f"Excel 里已经有这张单（{r['ref']}），不再加一行")
        elif k in taken and taken[k] != oid:
            log(f"⚠ 没写进 Excel：{k[2]} {k[1]} 和程序里另一张单一样（Excel 已经有了），请在订单跟踪里合并")
            fails = dict(_failed(st))
            fails[f"append|{oid}"] = repr(_v)                # 同样的值不再每次重试
            st.set_xstate("failed", fails)
        else:
            keep.append(op)
    if sets:
        st.set_many(sets, src="excel")
        st.set_xbase(bases)
    return keep


# ===================================================================== 一次写回（后台线程里调用）
def locked(path):
    """Excel / WPS 正开着这个文件：有 ~$ 锁文件，或者文件打不开写"""
    d, n = os.path.split(path)
    for lock in ("~$" + n, ".~lock." + n + "#"):
        if os.path.exists(os.path.join(d, lock)):
            return True
    try:
        with open(path, "r+b"):
            pass
    except OSError:
        return True
    return False


def backup(path, folder):
    os.makedirs(folder, exist_ok=True)
    base, ext = os.path.splitext(os.path.basename(path))
    dst = os.path.join(folder, f"{base}_{datetime.now():%Y%m%d_%H%M%S}{ext}")
    shutil.copy2(path, dst)
    olds = sorted(f for f in os.listdir(folder) if f.startswith(base + "_") and f.endswith(ext))
    for f in olds[:-KEEP_BACKUPS]:
        try:
            os.remove(os.path.join(folder, f))
        except OSError:
            pass
    return dst


def writeback(st, path, me=None, log=None, imported=None):
    """
    程序里的修改写进 Excel。返回 {"status": "ok" / "nothing" / "busy" / "stale" / "changed", "written", "failed", "import", "backup"}。
    busy = Excel 正被人打开；stale = 这台电脑的同步盘还没收到别人刚写的 Excel；changed = 写的时候有人存了 Excel
    """
    log = log or (lambda *_a: None)
    out = {"status": "nothing", "written": 0, "failed": [], "import": None, "backup": None, "conflicts": None}
    if not path or not os.path.isfile(path):
        out["status"] = "missing"
        return out
    if locked(path):
        out["status"] = "busy"
        return out
    # 先把 Excel 最新的读进来（谁最后改听谁）；imported = 刚读过、文件没再变就不重读
    imp = imported if (imported and imported.get("mtime") == os.path.getmtime(path)) else OT.import_file(st, path)
    out["import"] = imp
    if imp.get("stale"):
        out["status"] = "stale"
        return out
    out["conflicts"] = merge_conflicts(st, path, log)
    ops = pending(st, me)
    if not ops:
        return out
    mt0, sz0 = os.path.getmtime(path), os.path.getsize(path)
    tmpd = tempfile.mkdtemp(prefix="piano_w_")
    try:
        src = os.path.join(tmpd, "src.xlsx")
        dst = os.path.join(tmpd, "dst.xlsx")
        shutil.copy2(path, src)
        ops = _adopt_existing(st, src, ops, log)
        if not ops:
            out["status"] = "ok"
            return out
        where, failed = apply_ops(src, dst, st, ops)
        rows, _b, _s = OT.read_excel(dst)                # 读回来核对
        by_ref = {r["ref"]: r for r in rows}
        from openpyxl.utils import get_column_letter as L
        bases, sets = [], []
        for kind, oid, val in ops:
            if kind in ("budget", "clear") or oid not in where:
                continue
            if kind == "unmark":
                bases.append((oid, {"_m_" + f: UNMARKED for f in val}))
                continue
            sh, r, c = where[oid]
            row = by_ref.get(f"{sh}!{L(c + 1)}{r}")
            if row is None:
                failed.append((kind, oid, "写进去以后读不回来"))
                continue
            nb = {f: row.get(f) for f in OT.EXCEL_FIELDS}
            nb.update({"_w_" + f: v for f, v in val.items()})
            # 标了浅蓝的格子写完读回来是什么：以后 Excel 里这格变了 = 有人手改了 → 去掉标记
            nb.update({"_m_" + f: row.get(f) for f in (WRITE_FIELDS if kind == "append" else val)})
            nb.pop("origin", None)
            if kind == "append":
                nb["_gone"] = False
            bases.append((oid, nb))
            o = st.orders.get(oid, {})
            f = {"ref": row["ref"], "xid": row["id"]}
            if o.get("origin") == "excel" and row["id"] != oid:
                f["xid"] = row["id"]                     # 改了日期 / 供应商：Excel 那一行算出来的单号变了
            sets.append((oid, {k: v for k, v in f.items() if o.get(k) != v}))
        for kind, oid, val in ops:
            if kind == "clear" and not any(x[1] == oid for x in failed):
                bases.append((oid, {"_cleared": True}))
            if kind == "budget" and not any(x[1] == oid for x in failed):
                bases.append(("_budget", {oid: val, "_w_" + oid: val}))
        vals = {(k, o): v for k, o, v in ops}
        fails = dict(_failed(st))
        for kind, oid, _why in failed:
            fails[f"{kind}|{oid}"] = repr(vals.get((kind, oid)))
        if len(failed) >= len(ops):                    # 一处都没写成：原文件不动
            st.set_xstate("failed", fails)
            out.update(status="failed", failed=failed)
            return out
        # 原文件这段时间被人存过（或者又被打开了）：不覆盖，下次再写
        if locked(path) or os.path.getmtime(path) != mt0 or os.path.getsize(path) != sz0:
            out["status"] = "changed"
            return out
        bk = backup(path, os.path.join(st.dir, BACKUP_DIR))
        shutil.copyfile(dst, path)
        st.set_many([x for x in sets if x[1]], src="excel")
        st.set_xbase(bases)
        if failed:
            st.set_xstate("failed", fails)
        st.set_xstate("write", {"mtime": os.path.getmtime(path), "size": os.path.getsize(path), "by": st.me,
                                "t": OT.now_iso(), "n": len(ops) - len(failed)})
        out.update(status="ok", written=len(ops) - len(failed), failed=failed, backup=bk)
        log(f"写入 Excel：{len(ops) - len(failed)} 处" + (f"，{len(failed)} 处没写成" if failed else ""))
        return out
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


# ===================================================================== 同步盘冲突副本
def conflict_copies(path):
    """原表旁边同步盘生成的冲突副本（Synology：原名_电脑名_日期_Conflict.xlsx；也认「conflicted copy」「冲突」）"""
    d, n = os.path.split(path)
    stem, ext = os.path.splitext(n)
    try:
        names = os.listdir(d or ".")
    except OSError:
        return []
    out = []
    for f in names:
        s_, e_ = os.path.splitext(f)
        if f == n or f.startswith("~$") or e_.lower() != ext.lower() or not s_.startswith(stem):
            continue
        if "conflict" in f.lower() or "冲突" in f:
            out.append(os.path.join(d, f))
    return sorted(out)


def is_conflict_name(name):
    return "conflict" in name.lower() or "冲突" in name


def _hist_vals(st, oid, f):
    """这张单这一格按时间先后的 [(值, 来源)]"""
    return [(e["f"][f], e.get("src") or "app") for e in st.hist.get(oid, [])
            if e.get("op") == "set" and f in (e.get("f") or {})]


def _lost_write(st, oid, f, cv, cur):
    """
    冲突副本里的 cv 是程序里改的值（谁改的都行），现在的 cur 是从 Excel 读进来的、正好是改之前的旧值
    → 同步盘把程序写进去的换掉了，不是有人手改
    """
    m = st.meta.get(oid, {}).get(f)
    if m and m[2] != "excel":
        return False                                    # 现在的值本来就是程序里改的
    hv = _hist_vals(st, oid, f)
    for i in range(len(hv) - 1, -1, -1):
        v, src = hv[i]
        if src != "excel" and v == cv:
            before = hv[i - 1][0] if i > 0 else None
            return cur == before
    return False


def _last_known(st, oid):
    """删掉的单删之前的样子（按修改记录重放）"""
    o = {}
    for e in st.hist.get(oid, []):
        if e.get("op") == "set":
            o.update(e.get("f") or {})
        elif e.get("op") == "del":
            pass
    return o


RESTORE_MIN = 60        # 冲突副本里补回「被同步盘换掉」的程序新增单：只认写进去之后这么多分钟内就不见了的


def dup_key(st, o):
    """判断两张单是不是同一张：门店 · 下单日期 · 供应商 · 金额"""
    try:
        amt = round(float(o.get("amount")), 2)
    except (TypeError, ValueError):
        amt = None
    d = OT.to_date(o.get("date"))
    return (str(o.get("store") or ""), d.isoformat() if d else str(o.get("date") or "")[:10],
            st.canon(OT.sup_key(o.get("supplier") or "")), amt)


def _t(s):
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _swapped_away(st, oid):
    """
    程序写进 Excel 的单后来从 Excel 里不见了：是同步盘换掉的（可以从冲突副本补回），还是有人故意删的？
    2026-11-09 用户：S.Concordio 的 SAIS / CUKI 删了又被程序加回来 → 只在「这张单第一次不见、而且是写进去以后
    RESTORE_MIN 分钟内就不见了」时才算同步盘换掉的；删过一次又补回、再删的 = 人删的，不再补
    """
    h = st.hist.get(oid, [])
    dels = [e for e in h if e.get("op") == "del"]
    if len(dels) != 1:
        return False
    wrote = [_t(e.get("t")) for e in h if e.get("op") == "set" and e.get("src") == "excel"
             and "ref" in (e.get("f") or {}) and (e.get("f") or {}).get("origin") != "excel"]
    wrote = [w for w in wrote if w]
    gone = _t(dels[0].get("t"))
    if not wrote or gone is None:
        return False
    return timedelta(0) <= gone - max(wrote) <= timedelta(minutes=RESTORE_MIN)


def merge_conflicts(st, path, log=None):
    """
    合并原表旁边的冲突副本，合并完挪到 Backup Excel\冲突副本。返回 {"files", "restored", "added"}。
      · 副本里是程序改的值、原表（程序现在）是改之前的旧值 → 恢复（同步盘换掉的），重新写进原表
      · 副本里有、程序里从来没见过的值（有人在那个版本里手填的）→ 比程序里这一格改得晚就用副本的
      · 程序新增写进去、后来跟着原表被「删掉」的单 → 恢复，重新加进原表
      · 程序完全不认识的行（有人在那个版本里加的）→ 补成新单，写进原表
    """
    log = log or (lambda *_a: None)
    out = {"files": 0, "restored": 0, "added": 0}
    cps = conflict_copies(path)
    if not cps:
        return out
    import hashlib
    xid = {o["xid"]: oid for oid, o in st.orders.items() if o.get("xid")}
    dead = {}
    for oid, src in st.deleted.items():
        k = _last_known(st, oid)
        dead[k.get("xid") or oid] = (oid, k, src)
    ever_xid = {(e.get("f") or {}).get("xid") for h in st.hist.values() for e in h
                if e.get("op") == "set" and (e.get("f") or {}).get("xid")}
    live = {dup_key(st, o) for o in st.orders.values()}
    for cp in cps:
        tmpd = tempfile.mkdtemp(prefix="piano_c_")
        try:
            tmp = os.path.join(tmpd, "c.xlsx")
            shutil.copy2(cp, tmp)
            rows, _b, _s = OT.read_excel(tmp)
        except Exception as ex:                       # 打不开（还在同步 / 被占用）：下次再试
            log(f"⚠ 冲突副本读不了：{os.path.basename(cp)}（{ex}）")
            continue
        finally:
            shutil.rmtree(tmpd, ignore_errors=True)
        tc = OT._utc_iso(os.path.getmtime(cp))
        sets, xb, n_rest, n_add = [], [], 0, 0
        for r in rows:
            rid = r["id"]
            oid = xid.get(rid, rid)
            o = st.orders.get(oid)
            if o is not None:
                ch = {}
                for f in WRITE_FIELDS:
                    cv, cur = r.get(f), o.get(f)
                    if cv == cur or (f in ("confirmed", "arrived") and o.get("kind") == "deter"):
                        continue
                    if _lost_write(st, oid, f, cv, cur):
                        ch[f] = cv
                    elif cv is not None and not any(v == cv for v, _src in _hist_vals(st, oid, f)):
                        m = st.meta.get(oid, {}).get(f)
                        if m is None or m[0] < tc:
                            ch[f] = cv
                if ch:
                    sets.append((oid, ch))
                    xb.append((oid, {"_w_" + f: REWRITE for f in ch}))
                    n_rest += len(ch)
            elif rid in dead:
                doid, k, src = dead[rid]
                if src == "excel" and k.get("origin") != "excel" and doid not in st.orders \
                        and _swapped_away(st, doid) and dup_key(st, k) not in live:
                    live.add(dup_key(st, k))
                    sets.append((doid, k))
                    xb.append((doid, {"_gone": True}))
                    n_add += 1
            else:
                if rid in ever_xid or dup_key(st, r) in live:
                    continue                            # 程序以前写进去的行 / 程序里已经有一样的单：不是有人新加的
                live.add(dup_key(st, r))
                nid = "c" + hashlib.md5((os.path.basename(cp) + "|" + str(r.get("ref"))).encode("utf-8")).hexdigest()[:12]
                if nid in st.orders or nid in st.deleted:
                    continue
                f = {k: r.get(k) for k in OT.EXCEL_FIELDS if k not in ("ref", "origin") and r.get(k) is not None}
                f["origin"] = "app"
                f["note"] = (f.get("note") + " · " if f.get("note") else "") + "冲突副本补回"
                sets.append((nid, f))
                n_add += 1
        st.set_many(sets, src="app")
        st.set_xbase(xb)
        dst_dir = os.path.join(st.dir, BACKUP_DIR, CONFLICT_DIR)
        try:
            os.makedirs(dst_dir, exist_ok=True)
            shutil.move(cp, os.path.join(dst_dir, os.path.basename(cp)))
        except OSError as ex:
            log(f"⚠ 冲突副本挪不走：{os.path.basename(cp)}（{ex}）")
        out["files"] += 1
        out["restored"] += n_rest
        out["added"] += n_add
        log(f"合并同步盘冲突副本 {os.path.basename(cp)}：恢复 {n_rest} 处、补回 {n_add} 张单，副本挪到 "
            f"{BACKUP_DIR}\\{CONFLICT_DIR}")
    return out
