# -*- coding: utf-8 -*-
# 桌面布局管家 (Desktop Layout Keeper) — Copyright (c) 2026 zhengwuji
# 以 MIT 许可证发布，许可证全文见仓库根目录的 LICENSE
"""一键编译：桌面布局管家 → 单文件 exe。

它做四件事：
  1. 版本号 +1（读 VERSION，写回 VERSION，追加到 history）
  2. 把版本号烧进 exe 的 PE 资源（ProductVersion = v3 这种），
     所以 exe 显示的版本永远等于"这次编译"，不会和源码对不上
  3. 图标：assets/app.ico 不存在就自动调 assets/make_icon.py 生成
  4. PyInstaller 打包 → dist\\桌面布局管家_v3.exe，同时覆盖根目录的 桌面布局管家.exe

用法：
  python build.py                    版本 +1 并编译（最常用）
  python build.py -m "加了开机自启"   带一条备注，记进 VERSION
  python build.py --no-bump          不 +1，原地重编译（改完代码反复试的时候用）
  python build.py --clean            清掉 build\\ 和 dist\\ 再编译
  python build.py --keep-versions 5  只保留最近 5 个历史版本 exe
  python build.py --ascii-name      产物叫 DesktopLayoutKeeper_vN.exe（CI 用）

GitHub Actions 用的是另一个编号来源：

  python build.py --build-number 7 --ascii-name -m "修了 XX"

--build-number 直接指定编译号，**不读也不写 VERSION**。云端这么做的原因：CI 的编号
来自 Actions 的运行序号（严格递增），如果还写回 VERSION，就会和本地各自涨号、互相
覆盖、还得处理推送分叉。所以约定「本地编号看 VERSION，云端编号看 Actions 运行序号」，
两条轨道各自单调递增，Release 页面上的 v1、v2、v3 就是云端那条。

--ascii-name 是给发布用的：Windows 上 gh 把中文资产名传给 Release 时会把中文整段丢掉，
实测「桌面布局管家_v1.exe」变成了「_v1.exe」，所以发布走 ASCII 文件名。

结尾会多打一行机器可读的结果，方便 CI 取用：

  ##BUILD-RESULT## {"build":7,"kernel":"1.2.0","exe":"...","size":13302912,"sha256":"..."}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src" / "desktop_layout_keeper.py"
ENTRY = ROOT / "src" / "desktop_layout_keeper.py"
VERSION_FILE = ROOT / "VERSION"
ICON = ROOT / "assets" / "app.ico"
ICON_MAKER = ROOT / "assets" / "make_icon.py"
DIST = ROOT / "dist"
WORK = ROOT / "build"
LATEST = ROOT / "桌面布局管家.exe"      # 根目录永远是最新那一版，双击就用它

# 版本资源的语言/代码页：0804 = 简体中文，04B0 = 1200(Unicode)
LANG, CODEPAGE = 0x0804, 1200


def log(msg: str) -> None:
    print(msg, flush=True)


def load_version() -> dict:
    data = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
    data.setdefault("build", 0)
    data.setdefault("history", [])
    data.setdefault("kernel", "1.0.0")
    return data


def save_version(data: dict) -> None:
    VERSION_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return "%.1f %s" % (size, unit) if unit != "B" else "%d B" % size
        size /= 1024.0
    return "%.1f GB" % size


def make_version_info(data: dict, build: int, exe_name: str = None) -> Path:
    """生成 PyInstaller 的 --version-file 内容（PE 版本资源）。

    PyInstaller 会直接 eval 这个文件，所以里面的东西必须能求值：
    VSVersionInfo / FixedFileInfo / StringFileInfo / StringTable /
    StringStruct / VarFileInfo / VarStruct 都是它预先注入到作用域里的。

    exe_name 要传最终的文件名，它会被写进 OriginalFilename；不传就按产品名拼。
    """
    kernel = str(data.get("kernel", "1.0.0"))
    parts = (kernel.split(".") + ["0", "0", "0"])[:3]
    try:
        nums = tuple(int(p) for p in parts) + (build,)
    except ValueError:
        nums = (1, 0, 0, build)
    quad = ", ".join(str(n) for n in nums)

    product = data.get("product", "桌面布局管家")
    internal = data.get("internal_name", "DesktopLayoutKeeper")
    if not exe_name:
        exe_name = "%s_v%d.exe" % (product, build)
    when = time.strftime("%Y-%m-%d %H:%M:%S")

    # StringTable 的键是 '<lang><codepage>' 的小写十六进制，VarStruct 里放的是十进制
    lang_key = "%04x%04x" % (LANG, CODEPAGE)

    text = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(%(quad)s), prodvers=(%(quad)s),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)
  ),
  kids=[
    StringFileInfo([
      StringTable('%(lang_key)s', [
        StringStruct('CompanyName', '%(company)s'),
        StringStruct('FileDescription', '%(product)s - 桌面图标布局快照/恢复/锁定'),
        StringStruct('FileVersion', '%(kernel)s.%(build)d'),
        StringStruct('InternalName', '%(internal)s'),
        StringStruct('OriginalFilename', '%(exe_name)s'),
        StringStruct('ProductName', '%(product)s'),
        StringStruct('ProductVersion', 'v%(build)d'),
        StringStruct('LegalCopyright', ''),
        StringStruct('Comments', 'build %(build)d  %(when)s')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [%(lang)d, %(cp)d])])
  ]
)
""" % {
        "quad": quad,
        "lang_key": lang_key,
        "lang": LANG,
        "cp": CODEPAGE,
        "product": product,
        "internal": internal,
        "exe_name": exe_name,
        "kernel": kernel,
        "build": build,
        "company": product,
        "when": when,
    }
    WORK.mkdir(parents=True, exist_ok=True)
    path = WORK / "version_info.txt"
    path.write_text(text, encoding="utf-8")
    return path


def ensure_icon() -> None:
    if ICON.exists():
        return
    log("图标不存在，先生成：%s" % ICON.name)
    subprocess.run([sys.executable, str(ICON_MAKER)], check=True, cwd=str(ROOT))


def prune_versions(keep: int) -> None:
    """只保留最近 keep 个历史 exe，别让 dist 无限膨胀。"""
    if keep <= 0:
        return
    exes = sorted(set(DIST.glob("桌面布局管家_v*.exe")) | set(DIST.glob("*_v[0-9]*.exe")),
                  key=lambda p: (p.stat().st_mtime, p.name))
    for old in exes[:-keep]:
        try:
            old.unlink()
            log("  清理旧版本：%s" % old.name)
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="编译桌面布局管家为单文件 exe")
    ap.add_argument("-m", "--message", default="", help="这一版的备注，记进 VERSION")
    ap.add_argument("--no-bump", action="store_true", help="版本号不 +1，原地重编译")
    ap.add_argument("--build-number", type=int, default=None,
                    help="直接指定编译号（CI 用；不读也不写 VERSION）")
    ap.add_argument("--ascii-name", action="store_true",
                    help="产物用 ASCII 文件名 DesktopLayoutKeeper_vN.exe（CI 用："
                         "gh 在 Windows 上给 Release 传中文资产名会把中文丢成 _vN.exe）")
    ap.add_argument("--skip-tests", action="store_true",
                    help="跳过编译前的纯函数单测（_dev/_unit_tests.py）")
    ap.add_argument("--clean", action="store_true", help="先删 build\\ 和 dist\\")
    ap.add_argument("--keep-versions", type=int, default=0,
                    help="只保留最近 N 个历史版本 exe（0 = 全留）")
    args = ap.parse_args()

    if not SRC.exists():
        log("找不到源码：%s" % SRC)
        return 2
    if not (ROOT / "src").is_dir():
        log("源码应该放在 src\\ 里，见 README 的根目录规则。")
        return 2

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        log("没装 PyInstaller。执行：%s -m pip install pyinstaller" % sys.executable)
        return 2

    if args.clean:
        for d in (WORK, DIST):
            if d.exists():
                log("清理 %s" % d)
                shutil.rmtree(d, ignore_errors=True)

    data = load_version()
    if args.build_number is not None:
        # CI 模式：编号由调用方给定，VERSION 一个字节都不动（避免和本地分叉）
        build = int(args.build_number)
        data["build"] = build
        data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if args.message:
            data["note"] = args.message
    else:
        bumped = not args.no_bump
        build = int(data["build"]) + 1 if bumped else int(data["build"])
        data["build"] = build
        data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if args.message:
            data["note"] = args.message
        if bumped:
            # --no-bump 是「同一版重编译」，不是新版本，别再往 history 里塞一条重复记录
            data["history"] = (data.get("history") or [])[-49:] + [
                {"v": build, "time": data["updated"], "note": args.message}
            ]
        save_version(data)

    # 最终产物叫什么：默认中文名（给人双击的），CI 用 --ascii-name 换成英文名 ——
    # Windows 上 gh 给 Release 传中文资产名时会把中文整段丢掉（实测变成 _v1.exe），
    # 所以发布用的名字固定走 ASCII，本地编译仍然是中文名。
    if args.ascii_name:
        final_name = "%s_v%d.exe" % (data.get("internal_name", "DesktopLayoutKeeper"), build)
    else:
        final_name = "%s_v%d.exe" % (data.get("product", "桌面布局管家"), build)

    log("=" * 62)
    log("  编译 %s  v%d（内核 %s）" % (data.get("product"), build, data.get("kernel")))
    log("=" * 62)

    # 编译前先跑纯函数单测：坐标换算 / 恢复计划 / 屏幕适配 / 打码这些纯逻辑
    # 以前只能靠真机回归，现在钉在这里，没过就不出包（CI 里另跑一遍，重复几毫秒无所谓）。
    tests = ROOT / "_dev" / "_unit_tests.py"
    if args.skip_tests:
        log("跳过纯函数单测（--skip-tests）")
    elif tests.is_file():
        log("先跑纯函数单测：%s" % tests)
        trc = subprocess.run([sys.executable, str(tests)], cwd=str(ROOT)).returncode
        if trc != 0:
            log("单测没过（退出码 %d），不编译。" % trc)
            return 4
        log("单测通过。")
    else:
        log("没找到 %s，跳过单测。" % tests)

    ensure_icon()
    vinfo = make_version_info(data, build, exe_name=final_name)

    DIST.mkdir(parents=True, exist_ok=True)
    internal_name = "%s_v%d" % (data.get("internal_name", "DesktopLayoutKeeper"), build)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",                 # 单文件，拷走就能用
        "--noconsole",               # GUI 程序，不要黑框（命令行模式会自动挂父控制台）
        "--icon", str(ICON),
        "--version-file", str(vinfo),
        "--name", internal_name,     # 内部用 ASCII 名，避免中文名在构建链上出幺蛾子
        "--distpath", str(DIST),
        "--workpath", str(WORK / "pyi"),
        "--specpath", str(WORK),
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
        "--exclude-module", "email",
        "--exclude-module", "http",
        "--exclude-module", "xml",
        "--exclude-module", "sqlite3",
        str(ENTRY),
    ]
    log("打包中…（第一次会慢一点）")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        log("打包失败，退出码 %d" % r.returncode)
        return r.returncode

    produced = DIST / ("%s.exe" % internal_name)
    if not produced.exists():
        log("没找到产物：%s" % produced)
        return 3

    final = DIST / final_name
    if os.path.normcase(str(final)) != os.path.normcase(str(produced)):
        if final.exists():
            final.unlink()
        produced.replace(final)

    # 根目录永远放一份最新的，双击就用（CI 里没这个需要，别在生产工作区里留垃圾）
    local_copy = args.build_number is None
    if local_copy:
        shutil.copy2(final, LATEST)

    digest = sha256_of(final)
    log("")
    log("编译完成：%.1f 秒" % (time.time() - t0))
    log("  版本：v%d（内核 %s）" % (build, data.get("kernel")))
    log("  产物：%s" % final)
    if local_copy:
        log("  最新：%s" % LATEST)
    log("  大小：%s" % human(final.stat().st_size))
    log("  SHA256：%s" % digest)

    # CI 靠这一行取结果，不要改格式
    log("##BUILD-RESULT## " + json.dumps({
        "build": build,
        "kernel": data.get("kernel", ""),
        "product": data.get("product", ""),
        "note": data.get("note", ""),
        "exe": str(final),
        "exe_name": final.name,
        "size": final.stat().st_size,
        "size_human": human(final.stat().st_size),
        "sha256": digest,
    }, ensure_ascii=False))

    if args.keep_versions:
        prune_versions(args.keep_versions)

    log("")
    log("验证一下版本号是不是真烧进去了：")
    log('  "%s" --version' % LATEST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
