"""MCP 服务（2026-11-26）：协议、只读 SQL 检查、去掉密码、只能看允许的文件夹"""
import io
import json
import os

import pytest

import mcp_server as MS
import mcp_tools as MT


def _run(lines, settings):
    out = io.StringIO()
    srv = MS.Server(out, settings=settings)
    srv.serve(io.StringIO("\n".join(json.dumps(x) for x in lines) + "\n"))
    return [json.loads(l) for l in out.getvalue().splitlines()]


def test_protocol_initialize_list_and_call(tmp_path, monkeypatch):
    monkeypatch.setattr(MT, "_app_dir", lambda: str(tmp_path))
    os.makedirs(tmp_path / "logs")
    (tmp_path / "logs" / "procurement.log").write_text("riga 1\nERRORE x\nriga 3\n", encoding="utf-8")
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "log_tail", "arguments": {"grep": "errore"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "db_query", "arguments": {"sql": "DELETE FROM x"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "nope"}]
    r = _run(msgs, {"db": {"password": "secret"}})
    assert [x["id"] for x in r] == [1, 2, 3, 4, 5]                      # 通知没有回复
    assert r[0]["result"]["protocolVersion"] == "2024-11-05"
    names = {t["name"] for t in r[1]["result"]["tools"]}
    assert {"db_query", "log_tail", "tracking_summary", "order_get", "excel_peek"} <= names
    assert not names & {"orders_update", "mail_apply", "order_mail_send", "store_generate_orders"}   # 会改数据的不给
    assert "ERRORE x" in r[2]["result"]["content"][0]["text"] and "riga 3" not in r[2]["result"]["content"][0]["text"]
    assert r[3]["result"]["isError"] is True
    assert r[4]["error"]["code"] == -32601


def test_sql_only_reads():
    assert MT.check_sql("SELECT * FROM prodotti WHERE Descrizione LIKE '%delete%';") .startswith("SELECT")
    assert MT.check_sql("select replace(Descrizione, 'a', 'b') from prodotti")
    assert MT.check_sql("SHOW TABLES")
    for bad in ("UPDATE prodotti SET x=1", "SELECT 1; DROP TABLE x", "select * from x into outfile '/tmp/a'",
                "delete from x", "CALL p()", "with a as (select 1) delete from x", "SET @a = 1"):
        with pytest.raises(ValueError):
            MT.check_sql(bad)


def test_settings_redacted_and_files_limited(tmp_path, monkeypatch):
    monkeypatch.setattr(MT, "_app_dir", lambda: str(tmp_path / "app"))
    os.makedirs(tmp_path / "app")
    os.makedirs(tmp_path / "sync")
    (tmp_path / "sync" / "a.txt").write_text("x")
    (tmp_path / "other.txt").write_text("x")
    s = {"db": {"host": "h", "password": "p"}, "mail": {"accounts": [{"address": "a", "token": "t"}]},
         "sync_root": str(tmp_path / "sync"), "agent": {"api_key": "k"}}
    v = MT.settings_view({"settings": s}, {})
    assert v["db"]["password"] == "***" and v["db"]["host"] == "h" and v["mail"]["accounts"][0]["token"] == "***"
    assert v["agent"]["api_key"] == "***"
    ctx = {"settings": s}
    assert MT.files_list(ctx, {"path": str(tmp_path / "sync")})["个数"] == 1
    with pytest.raises(PermissionError):
        MT.files_list(ctx, {"path": str(tmp_path)})
    with pytest.raises(PermissionError):
        MT._check_path(ctx, str(tmp_path / "app" / "settings.json"))


def test_install_keeps_other_servers(tmp_path):
    p = tmp_path / "Claude" / "claude_desktop_config.json"
    os.makedirs(p.parent)
    p.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "light"}), encoding="utf-8")
    done = MS.install([str(p)], python="C:\\py\\pythonw.exe")
    cfg = json.loads(p.read_text(encoding="utf-8"))
    assert done == [str(p)] and cfg["theme"] == "light" and "other" in cfg["mcpServers"]
    e = cfg["mcpServers"][MS.SERVER_NAME]
    assert e["command"].endswith("python.exe") and e["args"][0].endswith("mcp_server.py")
    assert any(n.startswith("claude_desktop_config.json.bak_") for n in os.listdir(p.parent))


def test_write_needs_ticket_then_apply_and_undo(tmp_path):
    """第 2 期：先 prepare 给确认号 → apply_change 才改 → undo_change 改回去；确认收货不给"""
    from datetime import date
    import order_tracking as OT
    os.makedirs(tmp_path / OT.DATA_DIR)
    st = OT.Store(str(tmp_path), me="xh", host="PC1").reload()
    OT.register_drafts(st, "store_orders", [{"store": "1", "supplier": "METALTEX", "amount": 100}], today=date(2026, 11, 4))
    OT.register_drafts(st, "store_orders", [{"store": "1", "supplier": "METALTEX", "amount": 90}], today=date(2026, 11, 5),
                       in_scope=lambda o: OT.sup_key(o.get("supplier")) == OT.sup_key("METALTEX"))
    ctx = {"settings": {"tracking": {"folder": str(tmp_path), "me": "xh"}}}
    st = OT.Store(str(tmp_path), me="xh", host="PC1").reload()
    old = [oid for oid, o in st.orders.items() if OT.is_draft(o)]
    before = {i for i, o in st.orders.items() if OT.status(o) == "void"}   # 生成第二次时作废的
    assert old
    pre = MT.drafts_void_prepare(ctx, {})
    assert pre["张数"] >= 1 and pre["确认号"]
    st = OT.Store(str(tmp_path), me="xh", host="PC1").reload()
    assert all(OT.is_draft(st.orders[i]) for i in old)                 # prepare 不改任何东西
    with pytest.raises(RuntimeError):
        MT.apply_change(ctx, {"ticket": "nope"})
    res = MT.apply_change(ctx, {"ticket": pre["确认号"]})
    st = OT.Store(str(tmp_path), me="xh", host="PC1").reload()
    voided = [i for i, o in st.orders.items() if OT.status(o) == "void" and i not in before]
    assert voided and "作废了" in res["结果"]
    assert any("Claude" in f for f in os.listdir(tmp_path / OT.DATA_DIR))   # 写在 Claude 自己的日志文件里
    with pytest.raises(RuntimeError):
        MT.apply_change(ctx, {"ticket": pre["确认号"]})                  # 一个确认号只能用一次
    MT.undo_change(ctx, {"ticket": pre["确认号"]})
    st = OT.Store(str(tmp_path), me="xh", host="PC1").reload()
    assert all(OT.status(st.orders[i]) != "void" for i in voided)
    # 改状态 + 备注；确认收货不给
    oid = voided[0]
    with pytest.raises(RuntimeError):
        MT.orders_update_prepare(ctx, {"ids": [oid], "action": "arrived"})
    p = MT.orders_update_prepare(ctx, {"ids": [oid], "action": "note", "note": "chiesto al fornitore"})
    MT.apply_change(ctx, {"ticket": p["确认号"]})
    st = OT.Store(str(tmp_path), me="xh", host="PC1").reload()
    assert "chiesto" in st.orders[oid]["note"]
    names = {t.name for t in MT.build_tools()}
    assert {"drafts_void_prepare", "apply_change", "undo_change", "mail_apply_prepare"} <= names
    assert not names & {"order_mail_send", "store_generate_orders"}
