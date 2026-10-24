# -*- coding: utf-8 -*-
"""推送前的敏感信息自检 —— 这个仓库里不该出现任何能反推出本机环境的东西。

扫两层：
  默认        工作区里 git 跟踪的每个文件（按字节找字面串）
  --history   再加上 git 历史里每一次提交的每一个 blob（历史也要干净）

判定：命中分两类 —— 有理由的（见 ALLOW，比如仓库自己的公开账号名、编造的测试样本）
      报成「已知」，其余一律「可疑」并让退出码非 0。CI 在编译之前会跑这个脚本，
      所以只要有东西漏进去，Release 就不会发出来。

用法：
    python _dev/_privacy_scan.py            # 只扫跟踪的文件（CI 用这个）
    python _dev/_privacy_scan.py --history  # 连历史一起扫（人工审计用）
"""
import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELF = os.path.join("_dev", "_privacy_scan.py")

# 字面针脚（不用正则：转义层级一错就会漏掉单反斜杠路径）
BS = chr(92)
NEEDLES = [
    "G:" + BS + "Personal",            # 本机的工作目录
    "G:/Personal",
    "C:" + BS + "Users",
    "C:/Users",
    "Administrator",                   # 本机账号名
    "zhengwuji",                       # 仓库自己的账号名（有理由的那几个在 ALLOW 里）
    "ghp_", "github_pat_", "ghs_", "xoxb-", "AKIA",   # 各种令牌前缀
    "-----BEGIN", "sk-", "Bearer ",
    "password", "passwd", "api_key", "apikey", "client_secret",
    "dsh-ops", "dsh-desktop", "billion-context", "DSH_",   # 工具指纹
]

# 通用 Windows 绝对路径（要真跟着用户目录才算可疑，避免误伤 C:\Windows 这种通例）
ABS_RE = re.compile(r"[A-Za-z]:[\\/](?:Users|Personal|Documents|Desktop|AppData)[\\/]")

# (路径里出现这个子串, 命中哪个针脚, 为什么可以留)
ALLOW = [
    (SELF, "*", "扫描器自己的针脚表"),
    ("README.md", "zhengwuji", "仓库自己的公开账号名（徽章里的仓库地址）"),
    ("LICENSE", "zhengwuji", "MIT 版权署名"),
    ("build.py", "zhengwuji", "版权声明注释"),
    ("desktop_layout_keeper.py", "zhengwuji", "版权声明注释"),
    ("README.md", "sk-", "仓库名 de**sk-**back 的假命中"),
    ("_unit_tests.py", "C:/Users", "编造的测试样本（SomeUser），不是真账号"),
    ("_unit_tests.py", "绝对路径", "编造的测试样本"),
    ("_unit_tests.py", "secret", "测试样本里的文件名 secret.txt"),
    ("build-release.yml", "secret", "${{ secrets.GITHUB_TOKEN }} 是 GitHub 自带的"),
    (".gitignore", "dsh-ops", "只存在于历史里的旧版本，当前文件已改成通用的 *.lock"),
    ("commit ", "zhengwuji", "历史提交信息里提到仓库自己的账号名（改历史要 force-push，不值得）"),
]


def git(*args, binary=False):
    out = subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True)
    if out.returncode != 0:
        raise SystemExit("git %s 失败：%s" % (" ".join(args), out.stderr.decode("utf-8", "replace")))
    return out.stdout if binary else out.stdout.decode("utf-8", "replace")


def allowed(path, needle):
    for sub, nd, reason in ALLOW:
        if sub in path and (nd == "*" or nd == needle):
            return reason
    return None


def scan_blob(path, data, layer, hits):
    if b"\x00" in data[:8192]:
        return "bin"
    text = data.decode("utf-8", "replace").split("\n")
    for i, line in enumerate(text, 1):
        for needle in NEEDLES:
            if needle in line:
                hits.append((layer, path, i, needle, line.strip()[:100]))
                break
        else:
            if ABS_RE.search(line):
                hits.append((layer, path, i, "绝对路径", line.strip()[:100]))
    return "text"


def collect(with_history):
    hits, files = [], []
    # core.quotepath=off：中文文件名否则会被转成八进制转义，导致 open 失败被静默跳过
    tracked = [p for p in git("-c", "core.quotepath=off", "ls-files").split("\n") if p.strip()]
    files = tracked
    for rel in tracked:
        if os.path.normpath(rel) == os.path.normpath(SELF):
            continue
        try:
            data = open(os.path.join(ROOT, rel), "rb").read()
        except Exception:
            continue
        scan_blob(rel, data, "工作区", hits)

    blobs = 0
    if with_history:
        seen = set()
        for rev in [r for r in git("rev-list", "--all").split("\n") if r.strip()]:
            for entry in git("ls-tree", "-r", "-z", rev).split("\x00"):
                if not entry.strip():
                    continue
                meta, _, path = entry.partition("\t")
                parts = meta.split()
                if len(parts) < 3 or parts[1] != "blob" or parts[2] in seen:
                    continue
                seen.add(parts[2])
                blobs += 1
                if os.path.normpath(path) == os.path.normpath(SELF):
                    continue
                scan_blob(path, git("cat-file", "blob", parts[2], binary=True), "历史", hits)
        for chunk in git("log", "--format=%H%x00%B").split("\x00"):
            if chunk.strip():
                scan_blob("commit " + chunk[:7], chunk.encode("utf-8"), "提交信息", hits)
    return hits, files, blobs


def main() -> int:
    ap = argparse.ArgumentParser(description="推送前敏感信息自检")
    ap.add_argument("--history", action="store_true", help="连 git 历史里的 blob 一起扫")
    args = ap.parse_args()

    hits, files, blobs = collect(args.history)
    known, suspect = [], []
    for h in hits:
        reason = allowed(h[1], h[3])
        (known if reason else suspect).append((h, reason))

    print("扫描范围：跟踪文件 %d 个%s"
          % (len(files), "；历史 blob %d 个 + 全部提交信息" % blobs if args.history else ""))
    print("命中 %d 条：已知 %d 条，可疑 %d 条" % (len(hits), len(known), len(suspect)))

    if known:
        print("\n已知（有理由保留）：")
        agg = {}
        for (layer, path, ln, needle, line), reason in known:
            agg.setdefault((path, needle, reason), 0)
            agg[(path, needle, reason)] += 1
        for (path, needle, reason), n in sorted(agg.items()):
            print("  · %-34s %-12s ×%d   %s" % (path, needle, n, reason))

    if suspect:
        print("\n可疑（必须处理，否则别推）：")
        for (layer, path, ln, needle, line), _ in suspect:
            print("  ! [%s] %s:%d  命中「%s」  %s" % (layer, path, ln, needle, line))
        print("\n结果：不通过。")
        return 1

    print("\n结果：通过，工作区没有敏感信息。")
    if not args.history:
        print("（想连历史一起查就加 --history）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
