"""批发商比价：拖入文件放进 eliminare / distribuire，旧文件挪进 _旧文件（不删除）"""
import os

import file_inbox as FI


def _mk(folder, name, text=None):
    os.makedirs(folder, exist_ok=True)
    p = os.path.join(folder, name)
    with open(p, "w") as f:
        f.write(name if text is None else text)       # 默认内容 = 文件名（不同文件内容不同；一样的旧文件只留一份）
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


def test_sync_shared_prices(tmp_path):
    """几家门店共用 总文件夹\\distribuire：同步到门店文件夹（新的 / 更新的复制，共用里没有了的挪走，门店副本写过订单不算更新）"""
    import time
    shared = str(tmp_path / "root" / "distribuire")
    store = str(tmp_path / "root" / "批发商比价" / "2026-10-20" / "MARLIA" / "distribuire")
    rec = str(tmp_path / "root" / "批发商比价" / "2026-10-20" / "MARLIA" / "_备份" / "报价来源.json")
    _mk(shared, "MEGA.xlsx", "v1")
    _mk(shared, "MELONI.xlsx", "m1")
    _mk(shared, "~$MEGA.xlsx", "lock")
    c, a = FI.sync_shared_prices(shared, store, rec)
    assert sorted(c) == ["MEGA.xlsx", "MELONI.xlsx"] and not a
    with open(os.path.join(store, "MEGA.xlsx"), "w") as f:     # 比价时写了订单
        f.write("v1 + ordine")
    _mk(store, "缺失报告.xlsx")
    assert FI.sync_shared_prices(shared, store, rec) == ([], [])
    assert open(os.path.join(store, "MEGA.xlsx")).read() == "v1 + ordine"
    time.sleep(0.01)
    os.remove(os.path.join(shared, "MELONI.xlsx"))
    _mk(shared, "MEGA.xlsx", "v2 nuovo")                       # 每周更新
    c, a = FI.sync_shared_prices(shared, store, rec)
    assert c == ["MEGA.xlsx"] and len(a) == 1
    assert open(os.path.join(store, "MEGA.xlsx")).read() == "v2 nuovo"
    assert sorted(FI.excel_files(store)) == ["MEGA.xlsx", "缺失报告.xlsx"]
    assert FI.sync_shared_prices(str(tmp_path / "nope"), store, rec) == ([], [])


def test_old_folder_keeps_one_copy_of_identical_files(tmp_path):
    """2026-11-07 用户：_旧文件 里很多一模一样的文件 → 一样的内容只留一份"""
    import time
    folder = tmp_path / "2 供应商报价"
    old = folder / "_旧文件"
    old.mkdir(parents=True)

    def put(p, text):
        p.write_text(text)
    # 以前留下的重复：同名 + 加了时间后缀的，内容一样
    put(old / "formattato.xlsx", "A")
    put(old / "formattato_20261107_154855.xlsx", "A")
    put(old / "Listino.xlsx", "B")
    put(old / "Listino_20261107_154704.xlsx", "B2")          # 内容不一样：两份都留
    gone = FI.dedupe(str(old))
    assert gone == ["formattato_20261107_154855.xlsx"]
    assert sorted(os.listdir(old)) == ["Listino.xlsx", "Listino_20261107_154704.xlsx", "formattato.xlsx"]
    # 再挪一份一模一样的进去：不多存
    p = folder / "formattato.xlsx"
    put(p, "A")
    assert FI.archive(str(p), str(old)) == str(old / "formattato.xlsx")
    assert not p.exists() and len(os.listdir(old)) == 3
    # 内容不一样的照样加时间留着
    put(p, "A-new")
    time.sleep(0.01)
    FI.archive(str(p), str(old))
    assert len(os.listdir(old)) == 4
    # 拖进来一个和现在那份一模一样的文件：不复制、不往旧文件夹放
    put(folder / "Meloni.xlsx", "M")
    src = tmp_path / "Meloni.xlsx"
    put(src, "M")
    dst, moved = FI._copy_in(str(src), str(folder))
    assert moved is None and len(os.listdir(old)) == 4


def test_supplier_chosen_on_drop_replaces_same_supplier(tmp_path):
    """拖入报价时选好供应商：同一家的旧文件被替换，名称记下来，起订规则的名称在下拉里"""
    import supplier_names as SN
    st = {"rules": [{"name": "CASH & CARRY X", "keyword": "ccx"}], "supplier_names": {"formattato_1.xlsx": "MEGA CASH"}}
    existing = ["formattato_1.xlsx", "Listino_01.xlsx"]
    assert SN.recognized("formattato_1.xlsx", st) and not SN.recognized("Listino_01.xlsx", st)
    opts = SN.choices(st, existing)
    assert "MEGA CASH" in opts and "CASH & CARRY X" in opts and "Listino_01" not in opts
    labels = SN.labels(existing, st)
    assert SN.file_for("mega cash", labels) == "formattato_1.xlsx"
    assert SN.file_for("MELONI", labels) is None
    assert SN.apply_names(st, [("formattato_2.xlsx", "formattato_1.xlsx"), ("Listino_02.xlsx", None)],
                          {"formattato_2.xlsx": "MEGA CASH", "Listino_02.xlsx": "MELONI"})
    assert st["supplier_names"] == {"formattato_2.xlsx": "MEGA CASH", "Listino_02.xlsx": "MELONI"}
