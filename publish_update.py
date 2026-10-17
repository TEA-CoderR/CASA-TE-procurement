"""
发布新版本到 GitHub（在你自己的电脑上运行：双击 publish_update.bat）

第一次运行会问：
  · GitHub 仓库（例如 casate/caigou-update，建议「私有」仓库）
  · 发布令牌（有这个仓库 Contents 读写权限的 fine-grained token，只存在你电脑的 publish_config.json 里）
  · 只读令牌（只有 Contents 只读权限，会随程序发给同事，用来下载更新）

每次发布：
  1. 输入新版本号和更新说明
  2. 自动改版本号 → 打包（build.bat）→ 生成 manifest、完整包、补丁包
  3. 在 GitHub 建 Release 并上传；同事那边程序会提示「发现新版本」，点一下就更新
源代码不会上传，仓库里只有打包好的程序。
"""
import json
import os
import re
import subprocess
import zipfile

import requests

import app_update as U

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "publish_config.json")
DIST = os.path.join(HERE, "dist", "Procurement")
OUT = os.path.join(HERE, "dist", "_release")
LAST_MANIFEST = os.path.join(HERE, "build", "last_release_manifest.json")
API = "https://api.github.com"


def ask(prompt, default=""):
    v = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return v or default


def load_cfg():
    cfg = {}
    if os.path.exists(CFG_PATH):
        with open(CFG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    changed = False
    if not cfg.get("repo"):
        cfg["repo"] = ask("GitHub 仓库（用户名/仓库名）")
        changed = True
    if not cfg.get("publish_token"):
        cfg["publish_token"] = ask("发布令牌（Contents 读写）")
        changed = True
    if not cfg.get("read_token"):
        cfg["read_token"] = ask("只读令牌（Contents 只读，会随程序发给同事）")
        changed = True
    if changed:
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print(f"已保存到 {CFG_PATH}（不要发给别人）")
    # 令牌加密保存在本机
    import secure_store
    for k in ("publish_token", "read_token"):
        cfg[k] = secure_store.unprotect(cfg[k])
    if changed:
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(dict(cfg, publish_token=secure_store.protect(cfg["publish_token"]),
                           read_token=secure_store.protect(cfg["read_token"])), f, ensure_ascii=False, indent=2)
    return cfg


def current_version():
    with open(os.path.join(HERE, "theme.py"), encoding="utf-8") as f:
        m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', f.read())
    return m.group(1) if m else "v3.0"


def bump(v):
    a, b, c = U.parse_version(v)
    return f"v{a}.{b}.{c + 1}"


def set_version(v):
    p = os.path.join(HERE, "theme.py")
    with open(p, encoding="utf-8") as f:
        s = f.read()
    s = re.sub(r'APP_VERSION\s*=\s*"[^"]+"', f'APP_VERSION = "{v}"', s, count=1)
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


def gh(cfg, method, url, **kw):
    h = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {cfg['publish_token']}",
         "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "CASATE-publisher"}
    h.update(kw.pop("headers", {}))
    r = requests.request(method, url, headers=h, timeout=kw.pop("timeout", 60), **kw)
    if r.status_code >= 400:
        raise SystemExit(f"GitHub 返回 {r.status_code}：{r.text[:400]}")
    return r.json() if r.content else {}


def ensure_repo_has_commit(cfg):
    """空仓库不能建 Release：先放一个 README"""
    r = requests.get(f"{API}/repos/{cfg['repo']}/commits", headers={"Authorization": f"Bearer {cfg['publish_token']}"},
                     timeout=30)
    if r.status_code == 409 or (r.status_code == 200 and not r.json()):
        import base64
        text = "# CASA&TE采购系统 更新\n\n这里只放打包好的程序（Releases），程序会自动检查并下载。\n"
        gh(cfg, "PUT", f"{API}/repos/{cfg['repo']}/contents/README.md",
           json={"message": "init", "content": base64.b64encode(text.encode("utf-8")).decode()})


def zip_files(folder, files, dest):
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for rel in files:
            z.write(os.path.join(folder, rel), rel)
    return dest


def upload(cfg, release, path, name):
    url = release["upload_url"].split("{")[0] + f"?name={name}"
    size = os.path.getsize(path)
    print(f"  上传 {name}（{size / 1e6:.1f} MB）…")
    with open(path, "rb") as f:
        gh(cfg, "POST", url, data=f, headers={"Content-Type": "application/octet-stream",
                                               "Content-Length": str(size)}, timeout=1800)


def main():
    os.chdir(HERE)
    cfg = load_cfg()
    cur = current_version()
    print(f"\n现在的版本：{cur}")
    ver = ask("新版本号", bump(cur))
    if not ver.startswith("v"):
        ver = "v" + ver
    if not U.is_newer(ver, cur) and ver != cur:
        print("⚠ 新版本号不比现在大，同事那边不会提示更新。")
    print("更新说明（可以多行，空行结束）：")
    lines = []
    while True:
        line = input()
        if not line.strip():
            break
        lines.append(line)
    notes = "\n".join(lines) or "改进和修复"

    set_version(ver)
    with open(os.path.join(HERE, U.CONFIG_NAME), "w", encoding="utf-8") as f:
        import secure_store
        json.dump({"repo": cfg["repo"], "token": secure_store.obf(cfg["read_token"])}, f)     # 不放明文

    print("\n[1/4] 打包程序（3-5 分钟）…")
    if subprocess.call(["cmd", "/c", "build.bat", "nopause"], cwd=HERE) != 0:
        raise SystemExit("打包失败，没有发布。")
    import shutil
    shutil.copy(os.path.join(HERE, U.CONFIG_NAME), os.path.join(DIST, U.CONFIG_NAME))

    print("[2/4] 生成文件清单…")
    man = U.build_manifest(DIST, ver, notes)
    last = None
    if os.path.exists(LAST_MANIFEST):
        with open(LAST_MANIFEST, encoding="utf-8") as f:
            last = json.load(f)
    if last:
        man["patch_base"] = last.get("version")
        man["patch_files"] = [rel for rel, info in man["files"].items()
                              if last["files"].get(rel, {}).get("sha256") != info["sha256"]]
    else:
        man["patch_files"] = []
    os.makedirs(OUT, exist_ok=True)
    for n in os.listdir(OUT):
        os.remove(os.path.join(OUT, n))
    mpath = os.path.join(OUT, U.MANIFEST)
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False)

    print(f"[3/4] 压缩：完整包 {len(man['files'])} 个文件，补丁包 {len(man['patch_files'])} 个文件…")
    full = zip_files(DIST, list(man["files"]), os.path.join(OUT, U.FULL_ZIP))
    patch = zip_files(DIST, man["patch_files"], os.path.join(OUT, U.PATCH_ZIP)) if man["patch_files"] else None

    print("[4/4] 上传到 GitHub…")
    ensure_repo_has_commit(cfg)
    rel = gh(cfg, "POST", f"{API}/repos/{cfg['repo']}/releases",
             json={"tag_name": ver, "name": f"CASA&TE采购系统 {ver}", "body": notes, "draft": False,
                   "prerelease": False})
    upload(cfg, rel, mpath, U.MANIFEST)
    if patch:
        upload(cfg, rel, patch, U.PATCH_ZIP)
    upload(cfg, rel, full, U.FULL_ZIP)
    os.makedirs(os.path.dirname(LAST_MANIFEST), exist_ok=True)
    with open(LAST_MANIFEST, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False)
    print(f"\n✓ 已发布 {ver}。同事打开程序后会提示更新（或在「系统设置 → 关于」点「检查更新」）。")
    print(f"  首次给同事装：把 {DIST} 整个文件夹压缩发过去，解压时覆盖到原来的文件夹（不要先删除），历史记录会保留。")


if __name__ == "__main__":
    try:
        main()
    finally:
        input("\n按回车关闭…")
