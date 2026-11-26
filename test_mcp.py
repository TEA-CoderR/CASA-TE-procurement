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
