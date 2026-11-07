import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workspace as W


def _touch(p):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(b"x")
    return p


def test_ensure_and_migrate():
    root = tempfile.mkdtemp()
    _touch(os.path.join(root, "distribuire", "MEGA.xlsx"))
    notes = W.ensure(root)
    assert "distribuire → 2 供应商报价" in notes
    assert os.path.isfile(os.path.join(root, W.PRICES, "MEGA.xlsx"))
    for d in (W.LISTS, W.RESULTS, os.path.join(W.ELIM, "S.MARCO")):
        assert os.path.isdir(os.path.join(root, d))
    assert W.ensure(root) == []                      # 第二次什么都不用做


def test_guess_store():
    assert W.guess_store("Ordine S MARCO 20-10.xlsx") == 4
    assert W.guess_store("marlia.xlsx") == 2
    assert W.guess_store("lista.xlsx") is None


def test_place_and_scan_and_archive():
    root = tempfile.mkdtemp()
    W.ensure(root)
    src = tempfile.mkdtemp()
    a = _touch(os.path.join(src, "lista settimana.xlsx"))
    p, moved = W.place_list(root, a, 2)
    assert os.path.basename(p) == "MARLIA - lista settimana.xlsx" and moved == []
    b = _touch(os.path.join(src, "MARLIA nuova.xlsx"))
    p2, moved = W.place_list(root, b, 2)            # 同一家店再放一份：旧的挪走
    assert len(moved) == 1 and os.path.basename(p2) == "MARLIA nuova.xlsx"
    e = _touch(os.path.join(src, "elim.xlsx"))
    W.place_elim(root, e, 4)
    _touch(os.path.join(root, W.PRICES, "MEGA.xlsx"))
    _touch(os.path.join(root, W.PRICES, "MEGA.calc.xlsx"))
    s = W.scan(root)
    assert s["lists"] == [(2, "MARLIA nuova.xlsx")] and s["prices"] == ["MEGA.xlsx"] and s["elim"] == {4: ["elim.xlsx"]}
    assert W.store_list_files(root) == {2: p2}
    store = os.path.join(root, W.RESULTS, "2026-10-20", "S.MARCO")
    assert W.copy_elim(root, 4, store) == 1 and os.path.isfile(os.path.join(store, "eliminare", "elim.xlsx"))
    assert W.archive_used(root, [4], [p2]) == 2
    s = W.scan(root)
    assert s["lists"] == [] and s["elim"] == {}


def test_dedupe_old_skips_backups():
    import file_inbox as FI  # noqa: F401
    root = tempfile.mkdtemp()
    for sub in ("2 供应商报价/_旧文件", "_备份/x", "ORDINE/_旧订单"):
        os.makedirs(os.path.join(root, sub))
    for sub in ("2 供应商报价/_旧文件", "_备份/x", "ORDINE/_旧订单"):
        for n in ("a.xlsx", "a_20261101_101010.xlsx"):
            with open(os.path.join(root, sub, n), "w") as f:
                f.write("same")
    res = W.dedupe_old(root)
    assert sorted(len(g) for _, g in res) == [1, 1]
    assert len(os.listdir(os.path.join(root, "_备份", "x"))) == 2      # 备份不动
