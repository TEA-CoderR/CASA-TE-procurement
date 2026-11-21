"""第 0 阶段：业务命令（commands.py）、本机密码保管（credentials.py）、命令行（cli.py）"""
import json
import os
import sys
import tempfile
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _own_cred_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CASATE_CRED_DIR", str(tmp_path / "cred"))


# ---------------------------------------------------------------- 密码保管
def test_password_moves_out_of_settings_json(monkeypatch, tmp_path):
    import settings as S
    import credentials as C
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"db": {"host": "10.0.0.1", "password": "obf:abc"}}), encoding="utf-8")
    monkeypatch.setattr(S, "SETTINGS_PATH", str(path))
    data = S.load_settings()
    assert data["db"]["password"] == "obf:abc"                     # 程序里照旧拿得到（老代码自己解密）
    assert "password" not in json.loads(path.read_text(encoding="utf-8"))["db"]   # 文件里没有了
    assert C.get_protected("db.password") == "obf:abc"
    # 在设置里改了密码 → 存进保管处，文件里照样没有
    data["db"]["password"] = "nuova"
    S.save_settings(data)
    assert "password" not in json.loads(path.read_text(encoding="utf-8"))["db"]
    assert C.get("db.password") == "nuova"
    assert S.load_settings()["db"]["password"] == C.get_protected("db.password")
    assert C.names() == ["db.password"]


def test_credentials_set_get_delete():
    import credentials as C
    C.set("mail.ordine.foiano", "segreto")
    raw = open(C.cred_path(), encoding="utf-8").read()
    assert "segreto" not in raw                                    # 文件里不是明文
    assert C.get("mail.ordine.foiano") == "segreto"
    C.delete("mail.ordine.foiano")
    assert C.get("mail.ordine.foiano") == "" and C.names() == []


# ---------------------------------------------------------------- 检查清单命令
class _DB:
    class engine:
        @staticmethod
        def dispose():
            pass


def _stub_db(monkeypatch, in_db=True):
    import reorder_service as R
    calls = {"stores": []}
    monkeypatch.setattr(R, "make_db", lambda dbs: _DB())
    monkeypatch.setattr(R, "codes_from_eans", lambda db, eans: {"8000000000017": "C2"})
    monkeypatch.setattr(R, "ping", lambda db: 12.0)
    monkeypatch.setattr(R, "sales_latest", lambda db: None)
    monkeypatch.setattr(R, "supplier_info", lambda db, codes: {})

    def compute(db, sid, codes, days, cycle, sup, use_supplier_cycles=True, algo=None):
        calls["stores"].append(sid)
        return {"sid": sid, "codes": list(codes)}
    monkeypatch.setattr(R, "compute_suggestions", compute)
    monkeypatch.setattr(R, "stock_of", lambda df, days: {c: {"inv": 1, "daily": 0.1} for c in df["codes"]})

    def to_rows(df, file_rows, days):
        return [{"code": r["code"], "ean": r.get("ean"), "name": r.get("name"), "in_db": in_db} for r in file_rows if r.get("code")]
    monkeypatch.setattr(R, "to_rows", to_rows)

    def check_rows(file_rows, result_rows, unit="colli"):
        return [{"code": r["code"], "ean": r.get("ean"), "name": r.get("name"), "in_db": r["in_db"], "file_qty": 2,
                 "pack": 6, "price": 1.5, "suggested": 6, "inv0": 0, "daily0": 0.2, "cycle": 30}
                for r in result_rows]
    monkeypatch.setattr(R, "check_rows", check_rows)
    return calls


def _lists():
    return [{"store": 1, "unit": "colli", "path": "/x/FOIANO.xlsx",
             "rows": [{"code": "C1", "ean": "8000000000010", "name": "A", "qty": 2},
                      {"code": "", "ean": "8000000000017", "name": "B", "qty": 1}]}]


def test_run_check_without_window(monkeypatch):
    import commands as CMD
    calls = _stub_db(monkeypatch)
    steps = []

    class P(CMD.NullProgress):
        def post_step(self, i, state="running", count="", detail=""):
            steps.append((i, state))
    settings = {"reorder": {"days": [30, 60, 90, 180], "cycle": 30}}
    rows, meta = CMD.run_check(settings, _lists(), price_dir="", log=lambda *_: None, progress=P())
    assert [r["code"] for r in rows] == ["C1", "C2"]                # 条码 → 商品编号补上了
    assert calls["stores"][0] == 1 and sorted(calls["stores"]) == [1, 2, 3, 4, 5]   # 本店 + 其他门店（调货用）
    assert set(meta["snap"]) == {1, 2, 3, 4, 5} and meta["store"] == 1 and meta["days"] == [30, 60, 90, 180]
    assert (3, "done") in steps and (2, "done") in steps
    CMD.review(settings, rows, meta)
    assert all("fbox" in x and x["verdict"] for x in rows)


def test_run_check_refuses_when_nothing_in_db(monkeypatch):
    import commands as CMD
    _stub_db(monkeypatch, in_db=False)
    with pytest.raises(RuntimeError):
        CMD.run_check({}, _lists(), price_dir="", log=lambda *_: None)


def test_order_check_still_exports_moved_rules():
    pytest.importorskip("PySide6")
    import order_check as OC
    import check_rules as CR
    assert OC.init_boxes is CR.init_boxes and OC._problem is CR._problem and OC.write_problem_report is CR.write_problem_report


# ---------------------------------------------------------------- 确认已下单 + 审计 + 命令行
def test_confirm_orders_records_audit(monkeypatch, tmp_path):
    import order_tracking as OT
    import commands as CMD
    import settings as S
    monkeypatch.setattr(S, "app_dir", lambda: str(tmp_path))
    d = tempfile.mkdtemp()
    settings = {"tracking": {"folder": d, "me": "Lucas"}}
    OT.register_store_orders(settings, [("/x/Ordine_DOPLA_0211.xlsx", "DOPLA", "FOIANO", 3, 120.0, "direct", "s")])
    st = CMD.tracking_store(settings)
    ids = [o["id"] for o in CMD.list_drafts(st)]
    assert len(ids) == 1
    assert CMD.confirm_orders(st, ids, via="cli", today=date(2026, 11, 6)) == 1
    st2 = CMD.tracking_store(settings)
    assert not CMD.list_drafts(st2) and st2.orders[ids[0]]["date"] == "2026-11-06"
    line = json.loads(open(tmp_path / "logs" / "audit.jsonl", encoding="utf-8").read().splitlines()[-1])
    assert line["actor"] == "Lucas" and line["via"] == "cli" and line["result"] == {"confirmed": 1}


def test_cli_drafts_and_confirm(monkeypatch, tmp_path, capsys):
    import order_tracking as OT
    import cli
    import settings as S
    monkeypatch.setattr(S, "app_dir", lambda: str(tmp_path))
    d = tempfile.mkdtemp()
    settings = {"tracking": {"folder": d, "me": "Lucas"}, "language": "zh"}
    OT.register_store_orders(settings, [("/x/Ordine_LAMPA_0211.xlsx", "LAMPA", "MARLIA", 2, 80.0, "direct", "s")])
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    assert cli.main(["drafts"]) == 0
    out = capsys.readouterr().out
    oid = out.split("\t")[0]
    assert "LAMPA" in out and "1 张待登记" in out
    monkeypatch.setattr("builtins.input", lambda *_: "n")             # 问的时候说不
    assert cli.main(["confirm", oid]) == 1
    assert cli.main(["confirm", oid, "--yes"]) == 0
    assert "已确认 1 张" in capsys.readouterr().out


def test_counted_stock_used_for_suggestion_and_restored():
    import commands as C
    # 有目标库存：目标 − 门店点的数
    x = {"store_inv": 12.0, "inv0": 12.0, "suggested": 0.0, "target": 10.0}
    C.use_counted_stock(x, 0)
    assert x["inv0"] == 0 and x["store_inv"] == 0 and x["suggested"] == 10.0
    assert x["inv0_sys"] == 12.0
    C.use_counted_stock(x, None)                     # 报告处理了 → 回到系统库存
    assert x["inv0"] == 12.0 and x["suggested"] == 0.0 and "inv0_sys" not in x
    # 没有目标库存：按差多少件加减；原来不订的按日均 × 周期
    y = {"store_inv": 5.0, "inv0": 5.0, "suggested": 3.0}
    C.use_counted_stock(y, 2)
    assert y["suggested"] == 6.0
    z = {"store_inv": 40.0, "inv0": 40.0, "suggested": 0.0, "daily": 0.5, "cycle": 30}
    C.use_counted_stock(z, 4)
    assert z["suggested"] == 11.0
    C.use_counted_stock(z, 4)                        # 再套一次结果一样（先还原）
    assert z["suggested"] == 11.0 and z["inv0_sys"] == 40.0
