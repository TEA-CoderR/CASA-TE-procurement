"""
采购系统配置文件
集中管理所有硬编码值，方便维护和调整
"""

import os

# =====================
# 路径配置
# =====================
ORDINE_FOLDER = "ORDINE"
ELIMINARE_FOLDER = "eliminare"
DISTRIBUTUIRE_FOLDER = "distribuire"

# =====================
# Excel 列名常量
# =====================
COL_CODICE_EAN = "CODICE EAN"
COL_COLLI = "COLLI"
COL_PREZZO_NETTO = "PREZZO NETTO"
COL_ORDINE = "ORDINE"

# =====================
# 列位置常量
# =====================
# EAN 出现次数统计列（AB列，第28列）
EAN_COUNT_COL = 28
# 供应商选择结果写入第 30 列
SUPPLIER_RESULT_COL = 30
# 供应商价格矩阵从第 31 列开始
SUPPLIER_MATRIX_START_COL = 31

# =====================
# 起订量凑单配置（MEGA CASH）
# =====================
# 是否启用凑单优化
MIN_ORDER_ENABLED = True
# 供应商文件名中包含此关键字（不区分大小写）即视为该供应商
MIN_ORDER_SUPPLIER_KEYWORD = "mega"
# 起订箱数（COLLI 合计），0 表示不限制
MIN_ORDER_COLLI = 200
# 起订金额（欧元，= PREZZO NETTO × COLLI 合计），0 表示不限制
MIN_ORDER_AMOUNT = 700
# 起订规则的条件：「colli」= 达到起订箱数；「amount」= 达到起订金额；「all」= 都要达到（旧的「any」= 达到其中一个，仍兼容）
MIN_ORDER_MODE = "all"
# 凑单比"不在该供应商下单"更贵时，是否允许改为不在该供应商下单
MIN_ORDER_ALLOW_DROP = True
# 改动说明写入的列（AC 列，第 29 列）
MIN_ORDER_NOTE_COL = 29

# =====================
# 性能配置
# =====================
# Excel 最大行数限制（用于遍历保护）
MAX_ROWS = 10000
# 报告输出条数限制（UI 显示）
REPORT_DISPLAY_LIMIT = 30

# =====================
# 日志配置
# =====================
LOG_DIR = "logs"
LOG_FILE = "procurement.log"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# =====================
# Excel COM 配置
# =====================
EXCEL_FILE_FORMAT_XLSX = 51  # xlOpenXMLWorkbook
