import os
import re
import gc
import zipfile
import win32com.client as win32

from config import EXCEL_FILE_FORMAT_XLSX

# 导入Excel连接池
try:
    from excel_pool import excel_pool, get_excel, release_excel
    USE_POOL = True
except ImportError:
    USE_POOL = False

# =====================
# 判断是否真实xlsx
# =====================
def is_valid_xlsx(path):
    """检查 .xlsx 文件是否为有效的 ZIP 格式"""
    try:
        with zipfile.ZipFile(path, 'r') as z:
            return True
    except (zipfile.BadZipFile, Exception):
        return False


# =====================
# 安全关闭 Excel COM 对象
# =====================
def _safe_quit_excel(excel):
    """安全关闭 Excel COM 对象，防止进程泄漏"""
    try:
        excel.DisplayAlerts = False
        excel.Quit()
    except Exception:
        pass
    finally:
        del excel
        gc.collect()


# =====================
# 用 Excel 计算公式并保存纯值副本
# =====================
_calculated_cache = {}  # 缓存已计算的文件，避免重复打开 Excel

def calculate_formulas(path):
    """
    用 Excel COM 打开文件，保存为纯值副本。
    Excel 在 SaveAs 时自动计算公式，Value=Value 覆盖为纯值。
    返回纯值副本的路径（原文件不变）。
    同一文件只计算一次（内存缓存）。
    
    优化版本：使用连接池复用Excel实例
    """
    if path in _calculated_cache:
        return _calculated_cache[path]

    # 创建纯值副本路径
    calc_path = path + ".calc.xlsx"
    
    # 清理旧的 .calc 文件（防止累积）
    if os.path.exists(calc_path):
        try:
            os.remove(calc_path)
        except Exception:
            pass

    excel = None
    try:
        # 使用连接池获取Excel实例
        if USE_POOL:
            excel, is_new = get_excel()
        else:
            excel = win32.Dispatch("Excel.Application")
            excel.Visible = False
            excel.DisplayAlerts = False
            excel.ScreenUpdating = False

        wb = excel.Workbooks.Open(path)

        # 遍历所有 sheet，将公式替换为纯值
        for sheet in wb.Sheets:
            try:
                used = sheet.UsedRange
                used.Value = used.Value
            except Exception:
                pass

        # 保存为纯值副本
        wb.SaveAs(calc_path, FileFormat=EXCEL_FILE_FORMAT_XLSX)
        wb.Close(SaveChanges=False)

        _calculated_cache[path] = calc_path
        
        # 释放连接回池
        if USE_POOL:
            release_excel(excel)
        
        return calc_path

    except Exception as e:
        print(f"[错误] 公式计算失败: {path} | 错误: {e}")
        # 发生错误时也要释放连接
        if USE_POOL and excel is not None:
            try:
                release_excel(excel)
            except:
                pass
        return path  # 返回原路径，避免程序崩溃

    finally:
        # 不使用连接池时关闭Excel
        if not USE_POOL and excel is not None:
            _safe_quit_excel(excel)


_CELL_F = re.compile(rb"<f[\s>/]")
_VOLATILE = (b"TODAY(", b"NOW(", b"RAND(", b"RANDBETWEEN(", b"INDIRECT(", b"OFFSET(", b"CELL(", b"INFO(")


def formulas_cached(path):
    """
    .xlsx 里每个公式格子都存了计算结果（Excel / WPS 保存时都会存），而且结果不会因为「今天」或外部文件变
    → True：直接读存好的值，和用 Excel 打开算一遍得到的一样，不用开 Excel（快很多）。
    拿不准（没存结果、要求打开时重算、有外部链接、有 TODAY / INDIRECT 这类公式）→ False，照旧用 Excel。
    """
    try:
        if not path.lower().endswith(".xlsx"):
            return False
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if any(n.startswith("xl/externalLinks/") for n in names):
                return False
            wbx = z.read("xl/workbook.xml")
            if re.search(rb'fullCalcOnLoad="(1|true)"', wbx):
                return False
            for n in names:
                if not (n.startswith("xl/worksheets/") and n.endswith(".xml")):
                    continue
                data = z.read(n)
                a, b = data.find(b"<sheetData"), data.find(b"</sheetData>")
                if a == -1:
                    continue
                cells = data[a:b if b != -1 else len(data)]       # 只看格子（条件格式里的 <formula> 不算）
                for m in _CELL_F.finditer(cells):
                    end = cells.find(b"</c>", m.start())
                    seg = cells[m.start():end if end != -1 else len(cells)]
                    if b"<v>" not in seg or b"<v></v>" in seg:
                        return False
                    up = seg.upper()
                    if any(v in up for v in _VOLATILE):
                        return False
        return True
    except Exception:
        return False


class _VCell:
    __slots__ = ("value", "row", "column")

    def __init__(self, value, row, column):
        self.value, self.row, self.column = value, row, column


class ValueSheet:
    """
    工作表的「只有值」版本：一次把所有格子的值读进内存（openpyxl 只读模式，比普通打开快一倍），
    提供找列 / 读格子用到的那几个用法（max_row、ws[行]、ws.cell(行, 列).value、iter_rows(values_only=True)）。
    """

    def __init__(self, rows):
        self._rows = [tuple(r) for r in rows]
        self.max_row = len(self._rows)
        self.max_column = max((len(r) for r in self._rows), default=0)

    def _v(self, r, c):
        if 1 <= r <= self.max_row:
            row = self._rows[r - 1]
            if 1 <= c <= len(row):
                return row[c - 1]
        return None

    def cell(self, row, column):
        return _VCell(self._v(row, column), row, column)

    def __getitem__(self, r):
        row = self._rows[r - 1] if 1 <= r <= self.max_row else ()
        return tuple(_VCell(v, r, c) for c, v in enumerate(row, 1))

    def iter_rows(self, min_row=1, max_row=None, values_only=False):
        max_row = self.max_row if max_row is None else min(max_row, self.max_row)
        for r in range(min_row, max_row + 1):
            row = self._rows[r - 1]
            yield row if values_only else tuple(_VCell(v, r, c) for c, v in enumerate(row, 1))


def read_values(path):
    """只读模式读第一个（活动）工作表的值 → ValueSheet"""
    from openpyxl import load_workbook as _lw
    wb = _lw(path, data_only=True, read_only=True)
    try:
        return ValueSheet(wb.active.iter_rows(values_only=True))
    finally:
        wb.close()


def cleanup_calculated_files():
    """清理临时计算公式生成的 .calc.xlsx 文件"""
    for calc_path in _calculated_cache.values():
        try:
            if os.path.exists(calc_path):
                os.remove(calc_path)
        except Exception:
            pass
    _calculated_cache.clear()


# =====================
# 强制转换（支持 .xls → .xlsx）
# =====================
def convert_to_xlsx(path):
    """
    将 .xls 文件转换为 .xlsx 格式。
    如果是有效的 .xlsx，直接返回原路径。
    
    优化版本：使用连接池复用Excel实例
    """
    if path.lower().endswith(".xlsx") and is_valid_xlsx(path):
        return path

    new_path = path + ".xlsx"

    excel = None
    is_new = True
    try:
        # 使用连接池获取Excel实例
        if USE_POOL:
            excel, is_new = excel_pool.acquire()
        else:
            excel = win32.Dispatch("Excel.Application")
            excel.Visible = False
            excel.DisplayAlerts = False

        wb = excel.Workbooks.Open(path)
        wb.SaveAs(new_path, FileFormat=EXCEL_FILE_FORMAT_XLSX)
        wb.Close()

        return new_path

    except Exception as e:
        print(f"[错误] Excel转换失败: {path} | 错误: {e}")
        return path  # 返回原路径，避免程序崩溃

    finally:
        # 释放连接回池
        if USE_POOL and not is_new and excel is not None:
            try:
                excel_pool.release(excel)
            except:
                pass
        elif not USE_POOL and excel is not None:
            _safe_quit_excel(excel)


# =====================
# 查找列
# =====================
def find_col(ws, name):
    """
    在工作表中查找指定列名的列号和行号。
    搜索前 10 行（兼容表头在第 8 行等较后位置的情况）。
    """
    for row_idx in range(1, min(11, ws.max_row + 1)):
        for cell in ws[row_idx]:
            if cell.value and str(cell.value).strip() == name:
                return cell.column, cell.row
    return None, None


# =====================
# 获取最后一行
# =====================
def get_last_row(ws, col):
    """
    获取指定列的最后一行有数据的行号。
    使用 ws.max_row 作为起点，最小 100 行以防 openpyxl 计算不准确。
    """
    max_row = max(ws.max_row, 100)
    for i in range(max_row, 0, -1):
        if ws.cell(i, col).value:
            return i
    return 1


# =====================
# 清理 EAN 编码
# =====================
def find_col_best(ws, name, max_scan=80):
    """
    和 find_col 一样找表头，但表头里同一个名字出现好几次时（例如 Petrillo 的报价有两列「CODICE EAN」，
    前一列是空的），选表头下面有内容最多的那一列；都一样就用第一列。返回 (列号, 行号)
    """
    hits = []
    for row_idx in range(1, min(11, ws.max_row + 1)):
        for cell in ws[row_idx]:
            if cell.value and str(cell.value).strip() == name:
                hits.append((cell.column, cell.row))
    if len(hits) <= 1:
        return hits[0] if hits else (None, None)

    def filled(col, row):
        n = 0
        for r in range(row + 1, min(row + 1 + max_scan, ws.max_row + 1)):
            v = ws.cell(r, col).value
            if v is not None and str(v).strip() and any(ch.isdigit() for ch in str(v)):
                n += 1
        return n
    return max(hits, key=lambda h: (filled(*h), -h[0]))


def clean_ean(val):
    """
    清理 EAN 编码：去除前后空格和前导零。
    处理 None、数字、字符串等多种输入类型。
    """
    if val is None:
        return ""
    # 处理数字类型（如 12345.0）
    if isinstance(val, (int, float)):
        # 去掉小数部分
        val = int(val) if val == int(val) else val
    s = str(val).strip()
    if not s:
        return ""
    return s.lstrip("0")


# =====================
# 标准化文件名
# =====================
def normalize(name):
    """
    去除文件扩展名，转为小写。
    同时处理特殊空格字符和 .calc 后缀累积问题。
    """
    s = str(name).strip().lower()
    # 将各种特殊空格替换为普通空格
    s = s.replace("\xa0", " ").replace("\u3000", " ")
    # 清理 .calc 后缀累积（如 .calc.xlsx.calc.xlsx → .xlsx）
    # 修复：先去除扩展名，再清理 .calc
    while ".calc" in s:
        s = s.replace(".calc", "")
    # 去除扩展名（可能在 .calc 之后还有 .xlsx/.xls）
    s = s.replace(".xlsx", "").replace(".xls", "")
    return s


# =====================
# 解析价格
# =====================
def parse_price(val):
    """
    从各种格式的价格字符串中提取数值。
    支持 "€12.34", "12,34 EUR", "12.34" 等格式。
    """
    if val is None:
        return None

    s = str(val).replace("€", "").replace("EUR", "").strip()
    m = re.findall(r"\d+[.,]?\d*", s)

    if not m:
        return None

    try:
        return float(m[0].replace(",", "."))
    except (ValueError, IndexError) as e:
        return None