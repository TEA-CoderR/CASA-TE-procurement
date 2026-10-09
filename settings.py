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
    return data


def save_settings(data):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def match_rules(rules, supplier_headers):
    """
    把启用的规则对应到本次的供应商文件（列头）。
    返回 (matched, unmatched)：
      matched = [(rule, 列头)]，unmatched = [rule]
    一个文件只对应第一条匹配的规则。
    """
    used, matched, unmatched = set(), [], []
    for r in rules:
        if not r.get("enabled", True) or not str(r.get("keyword", "")).strip():
            continue
        kw = str(r["keyword"]).strip().lower()
        hit = None
        for h in supplier_headers:
            name = str(h or "").lower()
            if h and h not in used and kw in name and ".calc" not in name:
                hit = h
                break
        if hit:
            used.add(hit)
            matched.append((r, hit))
        else:
            unmatched.append(r)
    return matched, unmatched
