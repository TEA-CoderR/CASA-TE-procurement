import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import company_sync as C


def _settings():
    return {"theme": "dark", "language": "zh", "last_folder": "D:\\x", "db": {"host": "h", "password": "p"},
            "wholesale_root": "D:\\w", "user_name": "采购部", "company_defaults_ver": 2,
            "algo": {"engine": "v2", "review_days": 30},
            "store_orders": {"folder": "D:\\CasaTe", "day": "周五", "skip": ["a"], "cycle": 30,
                             "detail_widths": {"4": 126}, "fill": {"bms_order": [3, 4]}},
            "reorder": {"store": 2, "last_dir": "D:/x", "check_widths": {"3": 297}, "max_cut": 0.5},
            "griglia_cols": {"new_widths": {"2": 427}, "new_columns": [1, 2]},
            "rules": [{"name": "MEGA", "keyword": "mega", "min_colli": 200}],
            "api_token": "x", "image_size": "m", "export_dir": "C:\\Users\\XH\\Desktop"}


def test_extract_keeps_rules_and_layout_drops_personal():
    out = C.extract(_settings())
    assert out["algo"] == {"engine": "v2", "review_days": 30}
    assert out["store_orders"] == {"cycle": 30, "detail_widths": {"4": 126}, "fill": {"bms_order": [3, 4]}}
    assert out["reorder"] == {"check_widths": {"3": 297}, "max_cut": 0.5}
    assert out["griglia_cols"]["new_widths"] == {"2": 427}
    assert out["rules"][0]["keyword"] == "mega" and out["image_size"] == "m"
    for k in ("theme", "language", "last_folder", "db", "wholesale_root", "user_name", "company_defaults_ver",
              "api_token", "export_dir"):
        assert k not in out


def test_sync_bumps_only_when_changed_and_settings_uses_it():
    d = tempfile.mkdtemp()
    sp, mp = os.path.join(d, "settings.json"), os.path.join(d, C.MODULE)
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(_settings(), f, ensure_ascii=False)
    assert C.sync(sp, mp, base_ver=2) == (True, 3)
    assert C.sync(sp, mp, base_ver=2) == (False, 3)              # 没变化不加版本
    s = _settings()
    s["store_orders"]["detail_widths"]["4"] = 200                 # 拖了列宽
    s["theme"] = "light"
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)
    assert C.sync(sp, mp, base_ver=2) == (True, 4)
    ver, data = C.current(mp)
    assert ver == 4 and data["store_orders"]["detail_widths"]["4"] == 200
    ns = {}
    exec(open(mp, encoding="utf-8").read(), ns)                   # 生成的文件能直接导入
    assert ns["COMPANY_DEFAULTS_VER"] == 4 and ns["COMPANY_DEFAULTS"] == data
