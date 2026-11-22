"""一个同步文件夹管全部（2026-11-21）+ 设置检查"""
import os

import sync_paths as SP


def _tree(tmp_path, *rels):
    root = tmp_path / "SynologyDrive" / "SynologyDrive"
    for rel in rels:
        (root / rel).mkdir(parents=True)
    return str(root)


ALL = [rel for _k, _l, rel, _s in SP.FOLDERS]


def test_found_and_empty_or_missing_folders_filled(tmp_path):
    root = _tree(tmp_path, *ALL)
    assert SP.find_root(home=str(tmp_path), drives=[]) == os.path.normpath(root)
    own = tmp_path / "altrove"
    own.mkdir()
    s = {"store_orders": {"folder": str(own)}, "tracking": {"folder": str(tmp_path / "non esiste")}}
    changed = SP.apply(s, home=str(tmp_path), drives=[])
    assert s["sync_root"] == os.path.normpath(root)
    assert s["store_orders"]["folder"] == str(own)                       # 单独设置、文件夹在 → 不动
    assert s["tracking"]["folder"].endswith("02 Calendario ordine e Piano acquisti mesile")   # 不在了 → 换成同步文件夹里的
    assert s["wholesale_root"].endswith("Ordine DETERSIVI") and s["tracking"]["record"].endswith("01 RECORD ORDINE")
    assert "store_orders" not in changed and "tracking" in changed
    st = {x["key"]: x for x in SP.status(s)}
    assert st["store_orders"]["own"] and not st["deter"]["own"] and all(x["ok"] for x in st.values())
    assert SP.apply(s, home=str(tmp_path), drives=[]) == []              # 第二次什么都不改


def test_change_root_moves_derived_but_not_own(tmp_path):
    a = _tree(tmp_path / "a", *ALL)
    b = _tree(tmp_path / "b", *ALL)
    own = tmp_path / "mio"
    own.mkdir()
    s = {}
    SP.apply(s, new_root=a)
    SP.set_own(s, "store_pdv", str(own))
    SP.apply(s, new_root=b, force=True)
    assert s["wholesale_root"].startswith(os.path.normpath(b)) and s["store_pdv"]["folder"] == os.path.normpath(str(own))
    SP.set_own(s, "store_pdv", "")                                       # 恢复
    assert s["store_pdv"]["folder"].startswith(os.path.normpath(b))


def test_not_a_sync_folder_and_nothing_found(tmp_path):
    assert not SP.looks_like_root(str(tmp_path))
    s = {}
    assert SP.apply(s, home=str(tmp_path), drives=[]) == [] and "sync_root" not in s


def test_setup_check_required_items(tmp_path, monkeypatch):
    monkeypatch.setenv("CASATE_CRED_DIR", str(tmp_path / "cred"))
    import setup_check as SC
    root = _tree(tmp_path, *ALL)
    s = {}
    miss = {x["key"] for x in SC.missing_required(s, db_ok=False)}
    assert miss == {"db", "sync"}
    SP.apply(s, new_root=root)
    assert {x["key"] for x in SC.missing_required(s, db_ok=True)} == set()
    opt = {x["key"]: x for x in SC.items(s, True) if not x["need"]}
    assert set(opt) == {"mail", "booking", "agent"} and not opt["booking"]["ok"]
