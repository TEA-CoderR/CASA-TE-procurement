"""自动清理（2026-11-25 C 盘满了）：只删多余的缓存 / 备份，别的不碰"""
import os
import time

import housekeeping as HK


def _f(p, size=10, age_days=0):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(b"x" * size)
    t = time.time() - age_days * 86400
    os.utime(p, (t, t))
    return p


def test_old_quote_cache_images_and_result_backups_pruned(tmp_path, monkeypatch):
    app = tmp_path / "app"
    old_q = _f(str(app / "cache/quotes/a.json"), age_days=40)
    new_q = _f(str(app / "cache/quotes/b.json"), age_days=3)
    imgs = [_f(str(app / f"cache/images/{i}.webp"), size=400_000, age_days=10 - i) for i in range(5)]
    miss = _f(str(app / "cache/images/9.none"), age_days=20)
    monkeypatch.setattr(HK, "MAX_IMAGES_MB", 1)                     # 1 MB：5 张 × 0.4 MB 要删掉旧的
    res = tmp_path / "res"
    store = res / "2026-11-22" / "FOIANO"
    for k in range(6):
        _f(str(store / "_备份" / f"20261122_12{k:02d}00" / "distribuire" / "x.xlsx"), age_days=1)
    _f(str(store / "_备份" / "last_allocation.json"))
    for k in range(5):
        _f(str(store / "_备份" / "门店库存" / f"确认订单_{k}.xlsx.json"), age_days=5 - k)
    keep_order = _f(str(store / "Ordini" / "Ordine_FOIANO_MELONI.xlsx"), age_days=30)
    tmp = tmp_path / "tmp"
    _f(str(tmp / "casate_quotes_x" / "a.xlsx"))
    os.utime(str(tmp / "casate_quotes_x"), (time.time() - 3 * 86400,) * 2)
    _f(str(tmp / "other_app" / "a"), age_days=9)
    monkeypatch.setattr(HK.tempfile, "gettempdir", lambda: str(tmp))

    st = HK.run(str(app), str(res))
    assert not os.path.exists(old_q) and os.path.exists(new_q)
    left = [p for p in imgs if os.path.exists(p)]
    assert len(left) == 2 and imgs[4] in left and os.path.exists(miss)     # 留最近用过的
    stamps = sorted(d for d in os.listdir(store / "_备份") if d.startswith("2026"))
    assert stamps == ["20261122_120300", "20261122_120400", "20261122_120500"]
    assert os.path.exists(store / "_备份" / "last_allocation.json") and os.path.exists(keep_order)
    assert sorted(os.listdir(store / "_备份" / "门店库存")) == [f"确认订单_{k}.xlsx.json" for k in (2, 3, 4)]
    assert not os.path.exists(tmp / "casate_quotes_x") and os.path.exists(tmp / "other_app")
    assert st["_n"] > 0 and "MB" in HK.text(st)
    assert HK.run(str(app), str(res)) is None                       # 一天只做一次


def test_touch_keeps_used_cache_and_log_rotates(tmp_path):
    p = _f(str(tmp_path / "q.json"), age_days=50)
    HK.touch(p)
    assert time.time() - os.path.getmtime(p) < 60
    log = _f(str(tmp_path / "a.log"), size=2000)
    HK.rotate_log(log, max_bytes=1000)
    assert not os.path.exists(log) and os.path.exists(log + ".old")
