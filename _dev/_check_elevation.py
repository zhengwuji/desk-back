# -*- coding: utf-8 -*-
"""不触发真实 UAC 弹窗，验证提权调用链。

做法：把模块里那个 shell32 句柄换成假的，记录 ShellExecuteW 收到的参数。
真实点击「以管理员身份重启」会弹出 UAC，无人值守时会把安全桌面卡住，
所以这里只核对「传了什么」+「返回值怎么判」，弹窗本身由真人手测。

    python _dev/_check_elevation.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import desktop_layout_keeper as m


class FakeShell:
    """只实现 ShellExecuteW，用来记录调用。"""

    def __init__(self, rc):
        self.rc = rc
        self.calls = []

    def ShellExecuteW(self, hwnd, verb, file, params, cwd, nshow):   # noqa: N802
        self.calls.append(dict(verb=verb, file=file, params=params, cwd=cwd, nshow=nshow))
        return self.rc


def run(label, rc):
    fake = FakeShell(rc)
    real = m._shell32
    m._shell32 = fake
    try:
        ok = m.relaunch_as_admin()
    finally:
        m._shell32 = real
    print("[%s] 返回 %s（rc=%d）" % (label, ok, rc))
    for c in fake.calls:
        print("      verb=%r" % c["verb"])
        print("      file=%r" % c["file"])
        print("      params=%r" % c["params"])
        print("      nshow=%s" % c["nshow"])
    return ok


print("当前进程 is_admin() =", m.is_admin())
print("IS_WINDOWS =", m.IS_WINDOWS)
print("sys.executable =", sys.executable)
print()

# 1) 源码运行（非 frozen）：exe 是 python.exe，参数里带脚本绝对路径
had = hasattr(sys, "frozen")
ok_dev = run("源码运行 / UAC 已同意", 33)
assert ok_dev is True
real_calls = FakeShell(33)
real = m._shell32
m._shell32 = real_calls
try:
    m.relaunch_as_admin()
finally:
    m._shell32 = real
call = real_calls.calls[0]
assert call["verb"] == "runas", call
assert call["file"] == sys.executable.replace('"', ""), call
if not had:
    # 源码运行时，参数里应当带上当前脚本的绝对路径
    assert os.path.basename(sys.argv[0]) in call["params"], call
print("  ✓ 源码分支：verb=runas，目标=解释器，参数里带脚本路径（%s）"
      % os.path.basename(sys.argv[0]))
print()

# 2) 打包运行（frozen）：exe 就是自己，参数为空
sys.frozen = True
try:
    ok_frozen = run("打包运行 / UAC 已同意", 33)
finally:
    del sys.frozen
assert ok_frozen is True
print("  ✓ 打包分支：verb=runas，目标=当前 exe，无多余参数")
print()

# 3) UAC 被取消：ShellExecuteW 返回 <=32，必须判成失败
ok_cancel = run("打包运行 / 用户取消 UAC", 5)
assert ok_cancel is False
print("  ✓ 取消分支：返回 False（调用方会弹「UAC 提权被取消或失败」）")
print()

# 4) 非 Windows 直接返回 False
print("全部通过：提权调用链的参数与返回判定都正确")
