import json
import os
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

import app_update as U


def _write(folder, rel, data):
    p = os.path.join(folder, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)


def _release(tmp_path, old_files):
    """发布端：新版程序文件夹 → manifest + 完整包 + 补丁包"""
    new = tmp_path / "dist"
    files = {"app.exe": b"EXE-v2", "_internal/lib.dll": b"LIB", "_internal/data/x.json": b"{}",
             "suppliers_config.json": b"[]", "settings.json": b"PUBLISHER", "history/a.json.gz": b"H"}
    for rel, data in files.items():
        _write(str(new), rel, data)
    man = U.build_manifest(str(new), "v3.1", "notes")
    assert "settings.json" not in man["files"] and "history/a.json.gz" not in man["files"]
    man["patch_files"] = [r for r, i in man["files"].items() if old_files.get(r) != i["sha256"]]
    out = tmp_path / "rel"
    out.mkdir()
    with zipfile.ZipFile(out / U.FULL_ZIP, "w") as z:
        for rel in man["files"]:
            z.write(new / rel, rel)
    with zipfile.ZipFile(out / U.PATCH_ZIP, "w") as z:
        for rel in man["patch_files"]:
            z.write(new / rel, rel)
    (out / U.MANIFEST).write_text(json.dumps(man), encoding="utf-8")
    return out, man


def _serve(folder, token):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.headers.get("Authorization") != f"Bearer {token}":
                self.send_response(401)
                self.end_headers()
                return
            base = f"http://127.0.0.1:{self.server.server_port}"
            if self.path.endswith("/releases/latest"):
                assets = [{"name": n, "url": f"{base}/asset/{n}", "size": os.path.getsize(os.path.join(folder, n))}
                          for n in os.listdir(folder)]
                body = json.dumps({"tag_name": "v3.1", "body": "notes", "assets": assets}).encode()
                ctype = "application/json"
            elif self.path.startswith("/asset/"):
                assert self.headers.get("Accept") == "application/octet-stream"
                body = open(os.path.join(folder, self.path.split("/")[-1]), "rb").read()
                ctype = "application/octet-stream"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_update_flow_downloads_patch_and_keeps_user_data(tmp_path, monkeypatch):
    # 同事电脑上的旧版：exe 不同，lib 相同，自己的设置 / 历史 / 改过的 suppliers_config
    app = tmp_path / "app"
    _write(str(app), "app.exe", b"EXE-v1")
    _write(str(app), "_internal/lib.dll", b"LIB")
    _write(str(app), "_internal/data/x.json", b"{}")
    _write(str(app), "settings.json", b"MINE")
    _write(str(app), "history/mine.json.gz", b"MY-HISTORY")
    _write(str(app), "suppliers_config.json", b"[edited]")
    old = {"app.exe": U.sha256_file(str(app / "app.exe")), "_internal/lib.dll": U.sha256_file(str(app / "_internal/lib.dll")),
           "_internal/data/x.json": U.sha256_file(str(app / "_internal/data/x.json"))}
    rel_dir, man = _release(tmp_path, old)
    srv = _serve(str(rel_dir), "tok")
    monkeypatch.setattr(U, "API", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    cfg = {"repo": "me/upd", "token": "tok"}
    rel = U.latest_release(cfg)
    assert U.is_newer(rel["version"], "v3.0") and not U.is_newer("v3.0.0", "v3.0")
    m = U.fetch_manifest(cfg, rel)
    changed = U.changed_files(m, str(app))
    assert changed == ["app.exe"]                                  # suppliers_config 本机已有 → 不换
    asset = U.choose_package(m, changed, rel)
    assert asset["name"] == U.PATCH_ZIP
    z = U.download_asset(cfg, asset, str(tmp_path / "dl.zip"))
    staging = U.extract_needed(z, m, changed, str(tmp_path / "stage"))
    assert sorted(os.listdir(staging)) == ["app.exe"]
    srv.shutdown()
    # 补丁里没有的文件要换 → 改下完整包
    m2 = dict(m, patch_files=[])
    assert U.choose_package(m2, ["app.exe"], rel)["name"] == U.FULL_ZIP


def test_bad_token_message(tmp_path, monkeypatch):
    rel_dir, _ = _release(tmp_path, {})
    srv = _serve(str(rel_dir), "tok")
    monkeypatch.setattr(U, "API", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    try:
        U.latest_release({"repo": "me/upd", "token": "wrong"})
        assert False
    except U.UpdateError as e:
        assert "令牌" in str(e)
    finally:
        srv.shutdown()
