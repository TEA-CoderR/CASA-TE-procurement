# -*- coding: utf-8 -*-
"""采购助手（agent / agent_tools）和不开窗口的总仓 / 门店采购命令（commands.store_*）"""
import os

import commands as C
import store_orders as S
import agent as A
import agent_tools as AT
from test_store_orders import _pdv_file


def _pdv_setup(tmp_path, monkeypatch):
    root = tmp_path / "PDV"
    day = root / "01 LUNEDI"
    day.mkdir(parents=True)
    _pdv_file(str(day / "SCHEDA PROVA 0726..xlsx"))
    settings = {"store_pdv": {"folder": str(root), "stores": [1, 2]}}
    audits = []
    monkeypatch.setattr(C, "audit", lambda *a, **k: audits.append(a))

    def fake_demand(db, schede, stores, days, cycle, use_cfg, algo, on_store):
        return {}

    def fake_base(sc, demand, stores=None, transit=True):
        rows = [it["row"] for it in sc["items"]]
        return {1: {rows[0]: 30}, 2: {rows[1]: 5}}            # FOIANO 30 箱 × €12 = 360 ≥ 300；MARLIA 5 箱 = 60
    monkeypatch.setattr(S, "demand_by_store", fake_demand)
    monkeypatch.setattr(S, "base_colli", fake_base)
    return settings, audits


def test_store_compute_summary_edit_and_generate_orders(tmp_path, monkeypatch):
    """门店采购不开窗口：列供应商 → 算 → 看结果 → 改箱数 → 生成订单文件（和页面同一套计算）"""
    settings, audits = _pdv_setup(tmp_path, monkeypatch)
    rows = C.store_suppliers(settings, "pdv", "周一")
    assert [r["name"] for r in rows] == ["PROVA"] and not rows[0]["skipped"]
    calc = C.store_compute(settings, "pdv", "周一", db=object(), log=lambda m: None)
    assert calc["_kind"] == "pdv" and calc["stores"] == [1, 2]
    summ = C.store_summary(settings, calc)
    assert len(summ) == 1 and summ[0]["orderable"] and summ[0]["amount"] == 360.0
    assert summ[0]["stores"]["MARLIA"]["status"] == S.status_text("MARLIA", "wait")
    lines = C.store_lines(calc, "prova", store=2)
    assert lines and lines[0]["boxes"] == 5 and lines[0]["name"].startswith("ART")
    # MARLIA 改成 30 箱 → 也够起订量
    assert C.store_set_boxes(calc, "PROVA", 2, lines[0]["row"], 30) == 5
    summ = C.store_summary(settings, calc)
    assert summ[0]["amount"] == 720.0
    res = C.store_allocate(settings, calc, log=lambda m: None)
    files = S.written_files(res["written"])
    assert len(files) == 2 and all(os.path.exists(p) for p in files)
    assert res["out_root"].startswith(str(tmp_path / "PDV"))
    assert [a[2] for a in audits] == ["store_compute", "store_allocate"] and audits[1][1] == "agent"


def test_store_compute_unknown_supplier_is_an_error(tmp_path, monkeypatch):
    settings, _ = _pdv_setup(tmp_path, monkeypatch)
    try:
        C.store_compute(settings, "pdv", "周一", suppliers=["NONEXIST"], db=object(), log=lambda m: None)
    except RuntimeError as e:
        assert "没有要算的供应商" in str(e)
    else:
        raise AssertionError("应该报错")


class FakeAPI:
    """按顺序返回准备好的回复，记下每次请求"""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, url, headers, payload):
        import copy
        self.calls.append(copy.deepcopy(payload))
        return self.replies.pop(0)


def _use(name, args, i="t1"):
    return {"type": "tool_use", "id": i, "name": name, "input": args}


def test_agent_calls_tools_and_answers():
    api = FakeAPI([
        {"stop_reason": "tool_use", "content": [{"type": "text", "text": "我查一下。"}, _use("ping", {"x": 1})]},
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": "今天有 3 家。"}]},
    ])
    seen = []
    tools = [A.Tool("ping", "测试", {"type": "object", "properties": {}}, lambda ctx, a: (seen.append(a), {"n": 3})[1])]
    ag = A.Agent({}, tools, confirm=lambda t, x: True, key="k", post=api)
    out = ag.send("今天要订哪些？")
    assert out == "我查一下。\n\n今天有 3 家。" and seen == [{"x": 1}]
    # 第二次请求带着工具结果；system / 工具说明都打了缓存标记
    res = api.calls[1]["messages"][-1]["content"][0]
    assert res["type"] == "tool_result" and res["tool_use_id"] == "t1" and '"n": 3' in res["content"]
    assert api.calls[0]["tools"][-1].get("cache_control") and api.calls[0]["system"][0].get("cache_control")
    assert "今天是" in api.calls[0]["system"][0]["text"]


def test_write_tool_needs_confirmation_and_cancel_skips_it():
    api = FakeAPI([
        {"stop_reason": "tool_use", "content": [_use("make", {"what": "订单"})]},
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": "好的，不生成。"}]},
    ])
    ran, asked = [], []
    tools = [A.Tool("make", "生成", {"type": "object", "properties": {}}, lambda ctx, a: ran.append(a) or "ok",
                    write=True, confirm_text=lambda ctx, a: f"要生成 {a['what']}", label="生成采购订单")]
    ag = A.Agent({}, tools, confirm=lambda t, x: (asked.append((t, x)), False)[1], key="k", post=api)
    assert ag.send("生成订单") == "好的，不生成。"
    assert ran == [] and asked == [("生成采购订单", "要生成 订单")]
    assert "取消" in api.calls[1]["messages"][-1]["content"][0]["content"]


def test_tool_error_goes_back_to_model_and_failed_request_is_rolled_back():
    def boom(ctx, a):
        raise RuntimeError("数据库连不上")
    api = FakeAPI([
        {"stop_reason": "tool_use", "content": [_use("q", {})]},
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": "数据库连不上。"}]},
    ])
    ag = A.Agent({}, [A.Tool("q", "", {}, boom)], confirm=lambda t, x: True, key="k", post=api)
    ag.send("算一下")
    r = api.calls[1]["messages"][-1]["content"][0]
    assert r["is_error"] and "数据库连不上" in r["content"]

    def down(url, headers, payload):
        raise A.AgentError("Claude 服务出错（529）")
    ag.post = down
    n = len(ag.messages)
    try:
        ag.send("再试")
    except A.AgentError:
        pass
    assert len(ag.messages) == n                     # 失败的那句撤掉，对话还是完整的


def test_no_key_is_a_clear_error():
    ag = A.Agent({}, [], confirm=lambda t, x: True, key="", post=lambda *a: None)
    try:
        ag.send("你好")
    except A.AgentError as e:
        assert "API 密钥" in str(e)
    else:
        raise AssertionError("应该报错")


def test_tools_have_valid_specs_and_write_tools_confirm():
    tools = AT.build_tools()
    names = [t.name for t in tools]
    assert len(names) == len(set(names))
    for t in tools:
        sp = t.spec()
        assert sp["input_schema"]["type"] == "object" and sp["description"]
        if t.write:
            assert t.confirm_text is not None, t.name
    assert {t.name for t in tools if t.write} == {"store_generate_orders", "orders_update", "mail_apply", "order_mail_send"}


def test_agent_store_tools_end_to_end(tmp_path, monkeypatch):
    """助手的工具：算 → 看明细 → 改箱数 → 确认文字 → 生成"""
    settings, _ = _pdv_setup(tmp_path, monkeypatch)
    real = C.store_compute
    monkeypatch.setattr(C, "store_compute", lambda *a, **k: real(*a, **dict(k, db=object())))
    ctx = {"settings": settings, "log": lambda m: None}
    tools = {t.name: t for t in AT.build_tools()}
    out = tools["store_compute"].run(ctx, {"kind": "pdv", "day": "周一"})
    assert out["订得起来的"] == 1 and out["明细"][0]["金额"] == 360.0
    lines = tools["store_supplier_lines"].run(ctx, {"kind": "pdv", "supplier": "PROVA", "store": "MARLIA"})
    tools["store_set_boxes"].run(ctx, {"kind": "pdv", "supplier": "PROVA", "store": "2", "row": lines[0]["行"], "boxes": 30})
    text = tools["store_generate_orders"].confirm_text(ctx, {"kind": "pdv"})
    assert "PROVA" in text and "720" in text and "待登记" in text
    got = tools["store_generate_orders"].run(ctx, {"kind": "pdv"})
    assert got["订单文件"] == 2


def test_provider_choice_url_key_and_model(monkeypatch):
    """服务商 Claude / DeepSeek：各自的地址、密钥、模型；老设置 agent.model 照用"""
    import credentials
    store = {}
    monkeypatch.setattr(credentials, "get", lambda k: store.get(k))
    monkeypatch.setattr(credentials, "set", lambda k, v: store.__setitem__(k, v))
    old = {"agent": {"model": "claude-opus-5-5"}}
    assert A.provider_of(old) == "claude" and A.model_of(old) == "claude-opus-5-5"
    assert A.provider_of({"agent": {"provider": "xx"}}) == "claude"
    ds = {"agent": {"provider": "deepseek"}}
    assert A.model_of(ds) == "deepseek-v4-pro"
    A.set_api_key("sk-ds", "deepseek")
    assert A.api_key("deepseek") == "sk-ds" and A.api_key("claude") == ""
    api = FakeAPI([{"stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]}])
    urls = []
    ag = A.Agent(ds, [], confirm=lambda t, x: True, post=lambda u, h, p: (urls.append((u, h)), api(u, h, p))[1])
    assert ag.send("你好") == "ok"
    assert urls[0][0].startswith("https://api.deepseek.com/anthropic") and urls[0][1]["x-api-key"] == "sk-ds"
    assert api.calls[0]["model"] == "deepseek-v4-pro"
    # 没填 DeepSeek 密钥 → 提示写 DeepSeek
    store.clear()
    try:
        A.Agent(ds, [], confirm=lambda t, x: True, post=api).send("x")
    except A.AgentError as e:
        assert "DeepSeek" in str(e)
    else:
        raise AssertionError("应该报错")


def test_agent_settings_not_synced_company_wide():
    import company_sync
    assert "agent" in company_sync.SKIP_TOP
