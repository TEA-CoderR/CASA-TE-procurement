import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings as S


def _load_from(content):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "settings.json")
    if content is not None:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(content, f, ensure_ascii=False)
    old = S.SETTINGS_PATH
    S.SETTINGS_PATH = p
    try:
        return S.load_settings(), p
    finally:
        S.SETTINGS_PATH = old


def test_new_install_gets_company_defaults():
    data, p = _load_from(None)
    assert data["algo"]["engine"] == "v1" and [r["keyword"] for r in data["rules"]] == ["mega", "grieco", "petrillo", "meloni"]
    assert data["company_defaults_ver"] == S.COMPANY_DEFAULTS_VER and os.path.isfile(p)


def test_update_overrides_business_keeps_personal():
    old = {"language": "it", "db": {"host": "x"}, "algo": {"engine": "v2"}, "rules": [{"name": "OLD", "keyword": "old"}],
           "store_orders": {"folder": "D:\\\\CasaTe", "fill": {"borrow_min_days": 7, "max_stock_days": 90}},
           "reorder": {"last_dir": "D:\\\\x", "use_supplier_cycles": True}}
    data, _ = _load_from(old)
    assert data["algo"]["engine"] == "v1" and data["rules"][0]["name"] == "MEGA CASH"
    assert data["store_orders"]["folder"] == "D:\\\\CasaTe" and data["store_orders"]["fill"]["borrow_min_days"] == 15
    assert data["reorder"]["last_dir"] == "D:\\\\x" and data["reorder"]["use_supplier_cycles"] is False
    assert data["language"] == "it" and data["db"] == {"host": "x"}


def test_applied_only_once():
    mine = {"company_defaults_ver": S.COMPANY_DEFAULTS_VER, "algo": {"engine": "v2"}, "rules": []}
    data, _ = _load_from(mine)
    assert data["algo"]["engine"] == "v2" and data["rules"] == []         # 套用过以后自己改的保留
