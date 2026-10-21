"""
界面设置的保存与读取（settings.json，放在程序同目录）
"""
import os
import sys
import json
import copy

DEFAULT_SETTINGS = {
    "last_folder": "",
    "theme": "light",           # light / dark
    "language": "zh",           # zh / en / it
    "auto_backup": True,          # 处理前自动备份 ORDINE / distribuire
    "confirm_before_run": True,   # 开始处理前弹出确认
    "min_order_enabled": True,    # 是否启用起订量凑单
    "match_threshold": 0.6,
    "use_web": False,
    "review_before_write": False,  # 写入前总是打开手动调整窗口
    "allow_partial_purchase": False,  # 放弃供应商时允许只有它有货的商品不采购（默认不允许）
    "history_keep_days": 180,     # 历史分配记录保留天数：180 = 半年，365 = 一年，0 = 永久保留
    "db": {},                     # 货物分配数据库连接（空 = 用原程序的默认值）
    "algo": {},                   # 订货算法（新算法参数，见 forecast.DEFAULT_PARAMS）
    "store_orders": {},           # 门店配货页的文件夹、凑单设置、不再合作的供应商
    "reorder": {},                # 订货建议页的门店、周期等
    # 起订量规则：每个供应商一条
    "rules": [
        {
            "enabled": True,
            "name": "MEGA CASH",
            "keyword": "mega",        # 供应商文件名包含该关键字（不区分大小写）
            "min_colli": 200,         # 起订箱数，0 = 不限
            "min_amount": 700,        # 起订金额（欧元），0 = 不限
            "mode": "all",            # all = 都要达到；any = 达到一个即可
            "allow_drop": True,       # 无人值守时：凑单太贵允许不下单
            "manual_extra": 5,        # 手动调整线：凑单多花超过它 → 打开手动调整窗口（0 = 不用）
            "drop_extra": 30,         # 放弃供应商线：凑单多花超过它 → 问是否放弃该供应商（0 = 不用）
        }
    ],
}


# ---------------------------------------------------------------------
# 公司统一的默认设置（2026-10-20 按采购主管现在用的这套定下来）：
# 新装的、或者更新到这个版本的电脑，第一次打开时都改成这套（只改一次，之后自己改的会保留）。
# 只管业务规则和表格的列（顺序、显示哪些、列宽）；文件夹、数据库、语言这些个人的东西不动。
# 以后要再统一推一次：改下面的值，把 COMPANY_DEFAULTS_VER 加 1。
# ---------------------------------------------------------------------
COMPANY_DEFAULTS_VER = 2          # 2026-10-21：按采购部（XH 电脑）现在的设置统一一次
COMPANY_DEFAULTS = {
    "auto_backup": True,
    "min_order_enabled": True,
    "review_before_write": False,
    "allow_partial_purchase": False,
    "history_keep_days": 180,
    # 订货算法：新算法（订货周期 30 天、到货 0 天、服务水平 95%、平均压货最多 14 天、零头 ≥ 50% 才多订一箱）
    "algo": {"engine": "v2", "review_days": 30, "lead_days": 0, "service": 0.95, "abc_service": True, "max_stock_days": 14, "season": True, "pack_threshold": 0.5, "min_shelf": 1},
    # 门店配货：订货周期、凑单 / 借货、门店间调货；订货明细的列顺序、显示哪些列、列宽
    "store_orders": {
        "cycle": 30, "days": [30, 60, 90, 180], "use_supplier_cycles": True,
        "fill": {"max_amount_gap": 30.0, "max_colli_gap": 3, "max_stock_days": 60, "fill_bms": True, "merge_colocated": True, "borrow": True, "borrow_min_days": 30, "borrow_urgent": True, "merge_direct": True, "far_stores": [1], "colocated_store": 3},
        "transfer": {"enabled": True, "mode": "waste", "waste_ratio": 0.5, "item_min_value": 10.0, "min_value": 0.0, "keep_cycles": 1.0, "exclude_stores": [1]},
        "detail_columns": [4, 36, 5, 18, 19, 6, 20, 22, 11, 7, 8, 9, 10, 23, 12, 15, 2, 16, 33, 32, 13, 17, 35, 1, 0, 3, 34, 14, 21, 24, 25, 26, 27, 28, 29, 30, 31], "detail_columns_ver": 5,
        "detail_cols_hidden": [0, 1, 3, 14, 21, 24, 25, 26, 27, 28, 29, 30, 31, 34], "detail_cols_hidden_ver": 4,
        "detail_widths": {"4": 126, "36": 76, "18": 50, "19": 56, "6": 71, "20": 60, "11": 106, "23": 87, "12": 78, "15": 97, "2": 85, "16": 99, "33": 134, "32": 119, "13": 62, "17": 80, "35": 380, "1": 0, "0": 0, "5": 70, "3": 0, "34": 0, "14": 0, "21": 0, "7": 50, "8": 50, "9": 50, "10": 50, "22": 56, "24": 0, "25": 0, "26": 0, "27": 0, "28": 0, "29": 0, "30": 0, "31": 0},
    },
    # 检查门店订货清单：订货周期、销量周期、不按配置文件周期；列顺序、显示哪些列、列宽
    "reorder": {
        "cycle": 30, "days": [30, 60, 90, 180], "use_supplier_cycles": False,
        "check_columns": [22, 2, 27, 26, 3, 16, 17, 4, 18, 28, 9, 5, 6, 7, 8, 10, 11, 0, 15, 14, 24, 20, 25, 12, 13, 19, 23, 21, 1], "check_columns_ver": 8,
        "check_cols_hidden": [1, 12, 13, 19, 21, 23, 25], "check_cols_hidden_ver": 6,
        "check_widths": {"22": 0, "2": 129, "26": 77, "3": 79, "27": 80, "16": 50, "17": 50, "4": 70, "18": 60, "28": 56, "9": 102, "10": 80, "11": 70, "0": 92, "15": 89, "24": 68, "20": 134, "25": 0, "12": 0, "14": 76, "13": 0, "19": 0, "23": 0, "21": 0, "5": 68, "6": 52, "7": 52, "8": 52, "1": 0},
    },
    # 批发商报价文件 → 供应商名称（文件名每周变，认不出的再在程序里改）
    "supplier_names": {"(Prezzi)FlliPetrillo_Base_17_10.xlsx": "PETRILLO", "Canvass 43 New Grieco(1).xlsx": "NEW GRIECO", "Listino_01 - Offerte(1).xlsx": "MELONI", "formattato_059603(2).xlsx": "MEGA CASH", "Ordine Mega Cash Foiano 1810.xlsx": "MEGA CASH", "Ordine Meloni Foiano 1810.xlsx": "MELONI"},
    # 起订规则：关键词用供应商名字里固定的词（文件名每周变，这样下周也认得出）
    "rules": [
        {"enabled": True, "name": "MEGA CASH", "keyword": "mega", "min_colli": 200.0, "min_amount": 700.0,
         "mode": "colli", "allow_drop": True, "manual_extra": 0.0, "drop_extra": 0.0},
        {"enabled": True, "name": "NEW GRIECO", "keyword": "grieco", "min_colli": 0.0, "min_amount": 700.0,
         "mode": "amount", "allow_drop": True, "manual_extra": 0.0, "drop_extra": 0.0},
        {"enabled": True, "name": "PETRILLO", "keyword": "petrillo", "min_colli": 0.0, "min_amount": 700.0,
         "mode": "all", "allow_drop": True, "manual_extra": 0.0, "drop_extra": 0.0},
        {"enabled": True, "name": "MELONI", "keyword": "meloni", "min_colli": 0.0, "min_amount": 500.0,
         "mode": "all", "allow_drop": True, "manual_extra": 0.0, "drop_extra": 0.0},
    ],
}


def apply_company_defaults(data):
    """把公司统一的默认设置套到 data 上（字典一层一层合并：store_orders 里的文件夹、列宽等个人设置保留）。返回 data"""
    def merge(dst, src):
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                merge(dst[k], v)
            else:
                dst[k] = copy.deepcopy(v)
    merge(data, COMPANY_DEFAULTS)
    data["company_defaults_ver"] = COMPANY_DEFAULTS_VER
    return data


def app_dir():
    """程序所在目录（打包后为 exe 所在目录）"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


SETTINGS_PATH = os.path.join(app_dir(), "settings.json")


def load_settings():
    data = copy.deepcopy(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            data.update(saved)
    except (OSError, ValueError):
        pass
    new_defaults = (data.get("company_defaults_ver") or 0) < COMPANY_DEFAULTS_VER
    if new_defaults:                      # 新装 / 刚更新到新的统一设置：套用一次
        apply_company_defaults(data)
    # 补齐规则字段
    rules = []
    for r in data.get("rules") or []:
        base = copy.deepcopy(DEFAULT_SETTINGS["rules"][0])
        r = dict(r)
        # 旧版只有一个「凑单最多多花」，当作放弃供应商线
        if "max_extra" in r and "drop_extra" not in r:
            r["drop_extra"] = r.get("max_extra") or 0
            r.setdefault("manual_extra", 0)
        r.pop("max_extra", None)
        base.update(r)
        rules.append(base)
    data["rules"] = rules
    if new_defaults:
        save_settings(data)
    return data


def save_settings(data):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def match_rules(rules, supplier_headers, labels=None):
    """
    把启用的规则对应到本次的供应商文件（列头）。
    labels：可选 {列头: 供应商名称}（supplier_names.labels）。文件名每周会变，
            所以关键词在文件名里找不到时，再看名称（关键词在名称里，或名称和规则的供应商名称一样）。
    返回 (matched, unmatched)：
      matched = [(rule, 列头)]，unmatched = [rule]
    一个文件只对应第一条匹配的规则。
    """
    used, matched, unmatched = set(), [], []
    for r in rules:
        if not r.get("enabled", True) or not str(r.get("keyword", "")).strip():
            continue
        kw = str(r["keyword"]).strip().lower()
        rname = str(r.get("name") or "").strip().lower()
        hit = None
        for h in supplier_headers:
            name = str(h or "").lower()
            if h and h not in used and kw in name and ".calc" not in name:
                hit = h
                break
        if not hit and labels:
            for h in supplier_headers:
                name = str(h or "").lower()
                lb = str(labels.get(h) or "").strip().lower()
                if h and h not in used and ".calc" not in name and lb and (kw in lb or (rname and rname == lb)):
                    hit = h
                    break
        if hit:
            used.add(hit)
            matched.append((r, hit))
        else:
            unmatched.append(r)
    return matched, unmatched
