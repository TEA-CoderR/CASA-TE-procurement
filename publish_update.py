"""
发布新版本到 GitHub（双击 publish_update.bat）

第一次运行会问：
  · GitHub 仓库（TEA-CoderR/CASA-TE-procurement）
  · 发布令牌（这个仓库 Contents 读写的 fine-grained token，加密存在本机 publish_config.json）
  · 只读令牌（Contents 只读，会随程序发给同事，用来下载更新）

每次发布：
  0. 检查 git：有没提交的改动会提示先提交；GitHub 上有你本地没有的提交会要求先 git pull
  1. 输入新版本号和更新说明
  2. 改 theme.APP_VERSION → 打包（build.bat）→ 生成 manifest、完整包、补丁包（和上一版对比；
     上一版清单本机没有就从 GitHub 最新 Release 取，所以换电脑 / 换人也能出补丁包）
  3. 自动提交版本号并 git push，在 GitHub 建 Release（标签指向这次提交）并上传
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
    with open(p, encoding="utf-8", newline="") as f:          # newline="" 保持原来的换行符，不让 git 以为整个文件都改了
        s = f.read()
    s = re.sub(r'APP_VERSION\s*=\s*"[^"]+"', f'APP_VERSION = "{v}"', s, count=1)
    with open(p, "w", encoding="utf-8", newline="") as f:
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


# ---------------------------------------------------------------------
# git：发布前检查、发布后自动提交版本号并推送，Release 的标签指向这次提交
# ---------------------------------------------------------------------
def _git(*args, check=True):
    r = subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def in_git():
    import shutil
    return os.path.isdir(os.path.join(HERE, ".git")) and bool(shutil.which("git"))


def git_precheck():
    """有没提交的改动 / 没推送的提交时先处理，保证发布的程序 = GitHub 上的代码"""
    if not in_git():
        print("（这个文件夹不是 git 仓库，跳过提交检查）")
        return
    # 这台电脑没设过 git 的名字 / 邮箱时，提交会失败（而且是打包完才失败），所以先问
    if not _git("config", "user.email", check=False) or not _git("config", "user.name", check=False):
        print("这台电脑还没设置 git 提交用的名字和邮箱（只设在这个文件夹，记录是谁发布的）。")
        _git("config", "user.name", ask("名字", "TEA-CoderR"))
        _git("config", "user.email", ask("邮箱"))
    _git("fetch", "-q", "origin", check=False)
    dirty = _git("status", "--porcelain", "--untracked-files=no")
    if dirty:
        print("有还没提交的改动：\n" + dirty)
        if ask("先把这些改动提交再发布？(y/n)", "y").lower() != "y":
            raise SystemExit("已取消。先提交（git add / git commit）再发布。")
        msg = ask("提交说明", "发布前的改动")
        _git("add", "-u")
        _git("commit", "-q", "-m", msg)
    behind = _git("rev-list", "--count", "HEAD..@{u}", check=False)
    if behind and behind != "0":
        raise SystemExit(f"GitHub 上有 {behind} 个新提交你本地还没有。先 git pull 再发布。")


def git_commit_release(ver, notes):
    """提交改过的版本号并推送；返回这次提交的 SHA（Release 标签指向它）"""
    if not in_git():
        return None
    _git("add", "theme.py")
    if _git("status", "--porcelain", "--untracked-files=no"):
        _git("commit", "-q", "-m", f"发布 {ver}\n\n{notes}")
    _git("push", "-q")                     # 推到当前分支跟踪的远程分支（一般是 origin/main）
    return _git("rev-parse", "HEAD")


def last_release_manifest(cfg):
    """上一版的文件清单：先找本机记录，没有（换了电脑 / 换了人）就从 GitHub 最新 Release 下载"""
    if os.path.exists(LAST_MANIFEST):
        with open(LAST_MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    try:
        r = requests.get(f"{API}/repos/{cfg['repo']}/releases/latest", timeout=30,
                         headers={"Authorization": f"Bearer {cfg['publish_token']}", "Accept": "application/vnd.github+json"})
        if r.status_code != 200:
            return None
        a = next((x for x in r.json().get("assets", []) if x["name"] == U.MANIFEST), None)
        if not a:
            return None
        m = requests.get(a["url"], timeout=60, headers={"Authorization": f"Bearer {cfg['publish_token']}",
                                                       "Accept": "application/octet-stream"})
        return m.json() if m.status_code == 200 else None
    except Exception:
        return None


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
    git_precheck()
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
    last = last_release_manifest(cfg)
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

    print("[4/4] 提交版本号、上传到 GitHub…")
    sha = git_commit_release(ver, notes)
    ensure_repo_has_commit(cfg)
    body = {"tag_name": ver, "name": f"CASA&TE采购系统 {ver}", "body": notes, "draft": False, "prerelease": False}
    if sha:
        body["target_commitish"] = sha          # 标签指向发布时的代码
    rel = gh(cfg, "POST", f"{API}/repos/{cfg['repo']}/releases", json=body)
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
    # 出错的原因要在「按回车关闭」之前打印出来，不然窗口里什么都看不到
    try:
        main()
    except SystemExit as e:
        if e.code not in (None, 0):
            print(f"\n✗ 没有发布：{e.code}")
    except KeyboardInterrupt:
        print("\n已取消。")
    except Exception:
        import traceback
        traceback.print_exc()
        print("\n✗ 发布出错，没有完成。把上面的内容截图发给开发的人。")
    input("\n按回车关闭…")
