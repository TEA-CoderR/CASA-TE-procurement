# -*- coding: utf-8 -*-
"""
采购系统的 MCP 服务（2026-11-26）：Claude 桌面版按 claude_desktop_config.json 启动它（stdio），Claude 就能直接查程序里的数据。
协议：JSON-RPC 2.0，一行一条（MCP stdio 传输）；只实现 initialize / tools/list / tools/call / ping。不用第三方库。
工具在 mcp_tools.py（第 1 期全部只读）。安装：双击「MCP_install.bat」（= python mcp_server.py --install）。

stdout 只能写协议的消息：业务代码里的 print 一律改到 stderr（Claude 桌面版会把 stderr 记进它的 MCP 日志）。
"""
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_NAME = "casate-procurement"
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")


def _out_stream():
    out = sys.stdout
    sys.stdout = sys.stderr                     # 别的代码 print 的东西不能混进协议
    return out


class Server:
    def __init__(self, out, settings=None):
        self.out = out
        self.ctx = {"settings": settings, "calc": {}, "mail": None, "actor": "Claude",
                    "log": lambda m: print(m, file=sys.stderr)}
        self.tools = None

    def _settings(self):
        if self.ctx["settings"] is None:
            from settings import load_settings
            self.ctx["settings"] = load_settings()
        return self.ctx["settings"]

    def _tools(self):
        if self.tools is None:
            import mcp_tools
            self.tools = mcp_tools.build_tools()
        return self.tools

    def send(self, msg):
        self.out.write(json.dumps(msg, ensure_ascii=False, default=str) + "\n")
        self.out.flush()

    def handle(self, msg):
        """一条消息 → 回复（通知没有回复，返回 None）"""
        mid, method = msg.get("id"), msg.get("method")
        if method is None:                       # 客户端的回复，不用管
            return None
        try:
            if method == "initialize":
                want = (msg.get("params") or {}).get("protocolVersion")
                res = {"protocolVersion": want if want in PROTOCOLS else PROTOCOLS[0],
                       "capabilities": {"tools": {"listChanged": False}},
                       "serverInfo": {"name": SERVER_NAME, "version": _version()},
                       "instructions": "CASA&TE 采购系统（意大利五家门店 + BMS 仓库）的数据：采购订单跟踪、销售数据库（只读）、日志、设置、"
                                       "同步文件夹里的 Excel。全部只读；改数据要在程序里点（程序会先弹确认）。"}
            elif method == "ping":
                res = {}
            elif method == "tools/list":
                self._settings()
                res = {"tools": [t.spec() for t in self._tools()]}
            elif method == "tools/call":
                import mcp_tools
                p = msg.get("params") or {}
                self._settings()
                try:
                    txt = mcp_tools.call(self._tools(), self.ctx, p.get("name"), p.get("arguments") or {})
                    res = {"content": [{"type": "text", "text": txt}], "isError": False}
                except Exception as e:                     # 工具自己出错：照 MCP 规矩放进结果里，Claude 看得到原因
                    print(traceback.format_exc(), file=sys.stderr)
                    res = {"content": [{"type": "text", "text": f"出错：{type(e).__name__}: {e}"}], "isError": True}
            elif method.startswith("notifications/"):
                return None
            else:
                if mid is None:
                    return None
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"不支持：{method}"}}
        except Exception as e:
            print(traceback.format_exc(), file=sys.stderr)
            if mid is None:
                return None
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}
        if mid is None:
            return None
        return {"jsonrpc": "2.0", "id": mid, "result": res}

    def serve(self, inp):
        for line in inp:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "不是 JSON"}})
                continue
            batch = msg if isinstance(msg, list) else [msg]
            for m in batch:
                r = self.handle(m) if isinstance(m, dict) else None
                if r is not None:
                    self.send(r)


def _version():
    try:
        import theme
        return str(getattr(theme, "APP_VERSION", "") or "dev")
    except Exception:
        return "dev"


# ----------------------------------------------------------------------------- 安装进 Claude 桌面版
def config_paths():
    """Claude 桌面版的配置文件（普通安装在 %APPDATA%\\Claude；商店版在 Packages\\Claude_*\\LocalCache\\Roaming\\Claude）"""
    out = []
    ad = os.environ.get("APPDATA")
    if ad:
        out.append(os.path.join(ad, "Claude", "claude_desktop_config.json"))
    la = os.environ.get("LOCALAPPDATA")
    if la and os.path.isdir(os.path.join(la, "Packages")):
        for n in os.listdir(os.path.join(la, "Packages")):
            if n.lower().startswith("claude"):
                out.append(os.path.join(la, "Packages", n, "LocalCache", "Roaming", "Claude", "claude_desktop_config.json"))
    return out


def entry(python=None):
    py = python or sys.executable
    if py.lower().endswith("pythonw.exe"):
        py = py[:-len("pythonw.exe")] + "python.exe"
    return {"command": py, "args": [os.path.join(HERE, "mcp_server.py")], "env": {"PYTHONIOENCODING": "utf-8"}}


def install(paths=None, python=None):
    """把这个服务写进 Claude 桌面版的配置（别的服务留着；改之前备份一份）。返回写了哪几个文件"""
    import shutil
    from datetime import datetime
    done = []
    paths = paths or config_paths()
    for i, p in enumerate(paths):
        d = os.path.dirname(p)
        if not os.path.isdir(d):
            if i:                                # 商店版的文件夹不在就是没装商店版
                continue
            os.makedirs(d, exist_ok=True)        # %APPDATA%\Claude：商店版有时也读这里，没有就建

        cfg = {}
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                txt = f.read().strip()
            cfg = json.loads(txt) if txt else {}
            shutil.copy2(p, p + f".bak_{datetime.now():%Y%m%d_%H%M%S}")
        cfg.setdefault("mcpServers", {})[SERVER_NAME] = entry(python)
        with open(p, "w", encoding="utf-8", newline="") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        done.append(p)
    return done


def main():
    os.chdir(HERE)
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    if "--install" in sys.argv:
        done = install()
        if done:
            print("已经写进 Claude 桌面版的设置：\n  " + "\n  ".join(done))
            print("\n请完全退出 Claude 桌面版（右下角托盘图标 → 退出），再打开，就能用了。")
        else:
            print("没找到 Claude 桌面版的设置文件夹（%APPDATA%\\Claude）。请先装好 Claude 桌面版、打开一次再试。")
        if "--no-pause" not in sys.argv:
            input("\n按回车关闭…")
        return
    if "--list" in sys.argv:                     # 自己检查：列出工具
        import mcp_tools
        for t in mcp_tools.build_tools():
            print(t.name, "-", t.description[:60])
        return
    try:
        import sys as _s
        _s.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass
    out = _out_stream()
    try:
        out.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if os.name == "nt":
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass
    Server(out).serve(sys.stdin)


if __name__ == "__main__":
    main()
