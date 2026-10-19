"""批发商比价：重复处理同一批文件时先恢复成原文件，结果每次一样；结果页换方案不重算"""
import os

import originals as OG


def _w(p, text):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)


def _r(p):
    return open(p).read()


def test_restore_when_file_is_what_program_wrote(tmp_path):
    root = str(tmp_path)
    main = os.path.join(root, "ORDINE", "ord.xlsx")
    _w(main, "原始")
    assert OG.prepare(main, root, "ORDINE", None) == "saved"            # 第一次：现在这个就是原文件
    _w(main, "扣减后")                                                    # 程序处理后写回
    written = OG.record([main])
    assert OG.prepare(main, root, "ORDINE", written) == "restored"      # 再点处理：恢复成原文件
    assert _r(main) == "原始"


def test_new_or_edited_file_becomes_new_original(tmp_path):
    root = str(tmp_path)
    main = os.path.join(root, "ORDINE", "ord.xlsx")
    _w(main, "原始")
    OG.prepare(main, root, "ORDINE", None)
    _w(main, "扣减后")
    written = OG.record([main])
    _w(main, "自己在 WPS 里改过")                                          # 指纹对不上 → 不恢复，当作新的原文件
    assert OG.prepare(main, root, "ORDINE", written) == "saved"
    assert _r(main) == "自己在 WPS 里改过"
    assert _r(os.path.join(OG.store_dir(root, "ORDINE"), "ord.xlsx")) == "自己在 WPS 里改过"
    assert OG.prepare(os.path.join(root, "ORDINE", "nope.xlsx"), root, "ORDINE", written) == "missing"


def test_no_stored_original_means_no_restore(tmp_path):
    root = str(tmp_path)
    p = os.path.join(root, "distribuire", "MEGA.xlsx")
    _w(p, "写过的")
    assert OG.prepare(p, root, "distribuire", OG.record([p])) == "saved"    # 没存过原文件，不能乱恢复
    assert _r(p) == "写过的"


def test_switch_scenario_uses_stored_plan(monkeypatch):
    import service
    seen = {}

    def fake_rewrite(data, new, sources, log, progress=None, force=False):
        seen.update(data=data, new=new, sources=sources, force=force)
        return data, 1

    monkeypatch.setattr(service, "rewrite_assignment", fake_rewrite)
    rows = [{"row": 2}, {"row": 3}, {"row": 4}]
    rules = [{"target": "MEGA.xlsx", "name": "MEGA"}, {"target": "MELONI.xlsx", "name": "MELONI"}]
    data = {"scenario": "全部供应商",
            "scenarios": [{"name": "全部供应商", "excluded": [], "chosen": True},
                          {"name": "不含 MEGA", "excluded": ["MEGA.xlsx"], "chosen": False}],
            "state": {"rows": rows, "baseline": {2: "MEGA.xlsx", 3: "MELONI.xlsx", 4: "MELONI.xlsx"},
                      "assignment": {2: "MEGA.xlsx", 3: "MELONI.xlsx", 4: "MEGA.xlsx"},
                      "sources": {4: "手动"}, "rules": rules, "rules_all": rules,
                      "scenario_assigns": [{2: "MEGA.xlsx", 3: "MELONI.xlsx", 4: "MELONI.xlsx"},
                                           {2: "MELONI.xlsx", 3: "MELONI.xlsx"}]}}
    assert service.can_switch_scenario(data)
    service.switch_scenario(data, 1, lambda *a: None)
    d = seen["data"]
    assert seen["new"] == {2: "MELONI.xlsx", 3: "MELONI.xlsx"} and seen["force"]
    assert seen["sources"] == {2: "方案排除", 4: "自动凑单"}
    assert d["scenario"] == "不含 MEGA" and [x["chosen"] for x in d["scenarios"]] == [False, True]
    assert [r["target"] for r in d["state"]["rules"]] == ["MELONI.xlsx"]
    assert data["scenarios"][0]["chosen"]                                      # 原来的结果不被改动
    old = dict(data, state=dict(data["state"], scenario_assigns=[]))
    assert not service.can_switch_scenario(old)                                # 旧版本结果：不能换


def test_state_keeps_scenario_assigns_through_history():
    import history
    st = {"assignment": {2: "A"}, "baseline": {}, "sources": {}, "scenario_assigns": [{2: "A"}, {3: "B"}]}
    s = history._int_keys_to_str(st)
    assert s["scenario_assigns"] == [{"2": "A"}, {"3": "B"}]
    assert history._str_keys_to_int(s)["scenario_assigns"] == [{2: "A"}, {3: "B"}]
