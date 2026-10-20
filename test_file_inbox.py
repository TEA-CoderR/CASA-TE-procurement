"""批发商比价：拖入文件放进 eliminare / distribuire，旧文件挪进 _旧文件（不删除）"""
import os

import file_inbox as FI


def _mk(folder, name, text="x"):
    os.makedirs(folder, exist_ok=True)
    p = os.path.join(folder, name)
    with open(p, "w") as f:
        f.write(text)
    return p


def test_eliminare_add_and_replace(tmp_path):
    root = str(tmp_path / "root")
    _mk(os.path.join(root, "eliminare"), "a.xlsx")
    b = _mk(str(tmp_path / "dl"), "b.xlsx")
    FI.import_eliminare(root, [b])
    assert FI.excel_files(os.path.join(root, "eliminare")) == ["a.xlsx", "b.xlsx"]
    c = _mk(str(tmp_path / "dl"), "c.xlsx")
    r = FI.import_eliminare(root, [c], replace_all=True)
    assert FI.excel_files(os.path.join(root, "eliminare")) == ["c.xlsx"]
    assert sorted(os.listdir(os.path.join(root, "eliminare", "_旧文件"))) == ["a.xlsx", "b.xlsx"]
    assert len(r["archived"]) == 2
    FI.remove_file(root, "eliminare", "c.xlsx")
    assert FI.excel_files(os.path.join(root, "eliminare")) == []


def test_match_supplier():
    old = ["MEGA CASH 12-10.xlsx", "MELONI WALTER listino.xlsx", "NEW GRIECO 1210.xlsx", "PETRILLO ottobre.xlsx"]
    assert FI.match_supplier("MEGA CASH 19-10.xlsx", old) == "MEGA CASH 12-10.xlsx"
    assert FI.match_supplier("listino meloni 19.10.xlsx", old) == "MELONI WALTER listino.xlsx"
    assert FI.match_supplier("New Grieco 1910.xlsx", old) == "NEW GRIECO 1210.xlsx"     # NEW 不算（太常见）
    assert FI.match_supplier("F.LLI PETRILLO.xlsx", old) == "PETRILLO ottobre.xlsx"
    assert FI.match_supplier("ALTRO FORNITORE.xlsx", old) is None
    assert FI.match_supplier("mega19.xlsx", old, keywords=["mega"]) == "MEGA CASH 12-10.xlsx"   # 起订规则的关键词
    assert FI.match_supplier("NEW.xlsx", ["NEW A.xlsx", "NEW B.xlsx"]) is None


def test_suppliers_replace_and_add(tmp_path):
    root = str(tmp_path / "root")
    dist = os.path.join(root, "distribuire")
    _mk(dist, "MEGA CASH 12-10.xlsx", "old mega")
    _mk(dist, "PETRILLO.xlsx", "old petrillo")
    _mk(dist, "MEGA CASH 12-10.calc.xlsx", "temp")                          # 临时文件不参与对应
    m = _mk(str(tmp_path / "dl"), "MEGA CASH 19-10.xlsx", "new mega")
    x = _mk(str(tmp_path / "dl"), "NUOVO FORNITORE.xlsx", "new one")
    plan = FI.plan_suppliers(root, [m, x])
    assert [(os.path.basename(p["src"]), p["replace"], p["auto"]) for p in plan] == [
        ("MEGA CASH 19-10.xlsx", "MEGA CASH 12-10.xlsx", True), ("NUOVO FORNITORE.xlsx", None, False)]
    done = FI.apply_suppliers(root, plan)
    assert done == [("MEGA CASH 19-10.xlsx", "MEGA CASH 12-10.xlsx"), ("NUOVO FORNITORE.xlsx", None)]
    files = FI.excel_files(dist)
    assert "MEGA CASH 12-10.xlsx" not in files and "MEGA CASH 19-10.xlsx" in files and "NUOVO FORNITORE.xlsx" in files
    assert "PETRILLO.xlsx" in files
    assert os.path.exists(os.path.join(dist, "_旧文件", "MEGA CASH 12-10.xlsx"))


def test_suppliers_user_choice_and_same_name(tmp_path):
    root = str(tmp_path / "root")
    dist = os.path.join(root, "distribuire")
    _mk(dist, "PETRILLO.xlsx", "old")
    same = _mk(str(tmp_path / "dl"), "PETRILLO.xlsx", "new")
    odd = _mk(str(tmp_path / "dl"), "listino settimana 42.xlsx", "new2")
    _mk(dist, "MELONI.xlsx", "old meloni")
    plan = FI.plan_suppliers(root, [same, odd])
    assert plan[0]["replace"] == "PETRILLO.xlsx" and plan[1]["replace"] is None
    plan[1]["replace"] = "MELONI.xlsx"                                      # 界面上用户选了「替换 MELONI」
    FI.apply_suppliers(root, plan)
    assert open(os.path.join(dist, "PETRILLO.xlsx")).read() == "new"
    assert sorted(FI.excel_files(dist)) == ["PETRILLO.xlsx", "listino settimana 42.xlsx"]
    assert sorted(os.listdir(os.path.join(dist, "_旧文件"))) == ["MELONI.xlsx", "PETRILLO.xlsx"]
