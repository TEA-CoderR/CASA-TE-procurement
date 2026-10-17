"""
自动更新（GitHub Releases，私有仓库）

发布端（你的电脑）：publish_update.bat → 打包 → 上传到 GitHub 仓库的 Release：
    manifest.json          每个程序文件的大小和 SHA256
    CASATE_full.zip        完整程序（不含 settings.json / history / logs 等个人数据）
    CASATE_patch.zip       只含和上一版不同的文件（一般只有几 MB）

同事端（程序里）：启动后自动检查 / 系统设置里点「检查更新」
    → 对比 manifest 和本机文件，只下载需要的包 → 校验 → 程序退出后覆盖程序文件 → 自动重新打开
    settings.json、history、logs、performance_logs 永远不会被覆盖或删除；suppliers_config.json 只在本机没有时才放进去。

更新源配置：程序文件夹里的 update_config.json（发布时自动放进去）：
    {"repo": "用户名/仓库名", "token": "只读令牌"}
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile

API = "https://api.github.com"
CONFIG_NAME = "update_config.json"
MANIFEST = "manifest.json"
FULL_ZIP = "CASATE_full.zip"
PATCH_ZIP = "CASATE_patch.zip"
# 个人数据：发布时不打包，更新时不覆盖
USER_FILES = {"settings.json", "publish_config.json"}
USER_DIRS = {"history", "logs", "performance_logs", "shadow_results", "_update"}
KEEP_IF_EXISTS = {"suppliers_config.json"}


class UpdateError(Exception):
    pass


class Cancelled(Exception):
    pass


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def load_config(settings=None):
    cfg = {}
    p = os.path.join(app_dir(), CONFIG_NAME)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                cfg = json.load(f) or {}
        except (OSError, ValueError):
            cfg = {}
    if cfg.get("token"):
        import secure_store
        cfg["token"] = secure_store.unprotect(cfg["token"])
    if settings and settings.get("update"):           # 系统设置里手填的优先
        u = settings["update"]
        cfg = dict(cfg, **{k: v for k, v in u.items() if v and k in ("repo", "token")})
    return cfg if cfg.get("repo") else None


def parse_version(v):
    nums = [int(x) for x in re.findall(r"\d+", str(v or ""))]
    return tuple(nums + [0] * (3 - len(nums)))[:3] if nums else (0, 0, 0)


def is_newer(remote, local):
    return parse_version(remote) > parse_version(local)


def is_user_path(rel):
    rel = rel.replace("\\", "/")
    first = rel.split("/", 1)[0]
    return rel in USER_FILES or first in USER_DIRS or rel.endswith((".tmp", ".lnk"))


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def build_manifest(folder, version, notes=""):
    """发布端用：folder 里每个程序文件 → {size, sha256}"""
    files = {}
    for root, dirs, names in os.walk(folder):
        rel_root = os.path.relpath(root, folder)
        if rel_root != "." and rel_root.replace("\\", "/").split("/", 1)[0] in USER_DIRS:
            dirs[:] = []
            continue
        for n in names:
            p = os.path.join(root, n)
            rel = os.path.relpath(p, folder).replace("\\", "/")
            if is_user_path(rel):
                continue
            files[rel] = {"size": os.path.getsize(p), "sha256": sha256_file(p)}
    return {"version": version, "notes": notes, "files": files}


# ---------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------
def _headers(cfg, accept="application/vnd.github+json"):
    h = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "CASATE-updater"}
    if cfg.get("token"):
        h["Authorization"] = f"Bearer {cfg['token']}"
    return h


def latest_release(cfg, timeout=15):
    import requests
    r = requests.get(f"{API}/repos/{cfg['repo']}/releases/latest", headers=_headers(cfg), timeout=timeout)
    if r.status_code == 404:
        raise UpdateError("找不到更新：仓库不存在、还没有发布过，或者令牌没有权限")
    if r.status_code == 401:
        raise UpdateError("更新令牌无效或已过期")
    r.raise_for_status()
    rel = r.json()
    assets = {a["name"]: a for a in rel.get("assets", [])}
    return {"version": rel.get("tag_name") or rel.get("name"), "notes": rel.get("body") or "",
            "published": rel.get("published_at"), "assets": assets}


def download_asset(cfg, asset, dest, progress=None, cancel=None):
    """下载 Release 附件（私有仓库要走 API + octet-stream）"""
    import requests
    url = asset["url"]
    with requests.get(url, headers=_headers(cfg, "application/octet-stream"), stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or asset.get("size") or 0)
        done = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                if cancel and cancel():
                    raise Cancelled()
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    return dest


def fetch_manifest(cfg, rel):
    import requests
    a = rel["assets"].get(MANIFEST)
    if not a:
        raise UpdateError("这个版本缺少 manifest.json，不能自动更新")
    r = requests.get(a["url"], headers=_headers(cfg, "application/octet-stream"), timeout=60)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------
# 本机对比 / 安装
# ---------------------------------------------------------------------
def changed_files(manifest, folder=None, progress=None):
    """本机哪些程序文件和新版不一样（缺少或内容不同）"""
    folder = folder or app_dir()
    out = []
    items = list(manifest["files"].items())
    for i, (rel, info) in enumerate(items):
        p = os.path.join(folder, rel)
        if progress:
            progress(i, len(items))
        if rel in KEEP_IF_EXISTS and os.path.exists(p):
            continue
        if not os.path.exists(p) or os.path.getsize(p) != info["size"] or sha256_file(p) != info["sha256"]:
            out.append(rel)
    return out


def choose_package(manifest, changed, rel):
    """补丁包里包含所有要换的文件 → 下补丁包，否则下完整包"""
    patch = set(manifest.get("patch_files") or [])
    if changed and PATCH_ZIP in rel["assets"] and set(changed) <= patch:
        return rel["assets"][PATCH_ZIP]
    if FULL_ZIP not in rel["assets"]:
        raise UpdateError("这个版本缺少完整程序包")
    return rel["assets"][FULL_ZIP]


def extract_needed(zip_path, manifest, changed, staging):
    """只解压要换的文件，并逐个校验 SHA256"""
    os.makedirs(staging, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        names = {n.replace("\\", "/"): n for n in z.namelist()}
        for rel in changed:
            if rel not in names:
                raise UpdateError(f"更新包里缺少文件：{rel}")
            target = os.path.join(staging, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(names[rel]) as src, open(target, "wb") as dst:
                while True:
                    b = src.read(1 << 20)
                    if not b:
                        break
                    dst.write(b)
            if sha256_file(target) != manifest["files"][rel]["sha256"]:
                raise UpdateError(f"文件校验失败：{rel}（下载可能不完整，请重试）")
    return staging


def launch_installer(staging, folder=None, exe=None, pid=None):
    """写一个 PowerShell 脚本：等程序退出 → 复制新文件（不删除任何文件）→ 重新打开程序"""
    folder = folder or app_dir()
    exe = exe or sys.executable
    pid = pid or os.getpid()
    script = os.path.join(os.path.dirname(staging), "install_update.ps1")
    log = os.path.join(folder, "logs", "update.log")

    def q(s):
        return "'" + str(s).replace("'", "''") + "'"
    ps = f"""$ErrorActionPreference = 'Continue'
$src = {q(staging)}
$dst = {q(folder)}
$log = {q(log)}
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
"$(Get-Date) 开始更新" | Out-File -FilePath $log -Append -Encoding utf8
try {{ Wait-Process -Id {int(pid)} -Timeout 120 -ErrorAction SilentlyContinue }} catch {{}}
Start-Sleep -Milliseconds 800
$ok = $true
for ($i = 0; $i -lt 5; $i++) {{
    robocopy $src $dst /E /R:3 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -lt 8) {{ $ok = $true; break }} else {{ $ok = $false; Start-Sleep -Seconds 2 }}
}}
"$(Get-Date) 复制完成 ok=$ok code=$LASTEXITCODE" | Out-File -FilePath $log -Append -Encoding utf8
Start-Process -FilePath {q(exe)} -WorkingDirectory $dst
Start-Sleep -Seconds 2
Remove-Item -Recurse -Force (Split-Path $src) -ErrorAction SilentlyContinue
"""
    with open(script, "w", encoding="utf-8-sig") as f:
        f.write(ps)
    flags = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                      "-File", script], creationflags=flags, close_fds=True)
    return script


def work_dir():
    """下载和解压用的临时目录（在系统临时文件夹，避开程序文件夹）"""
    d = os.path.join(tempfile.gettempdir(), "CASATE_update")
    os.makedirs(d, exist_ok=True)
    return d
