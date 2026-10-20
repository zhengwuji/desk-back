# -*- coding: utf-8 -*-
"""托盘层的无界面自检：建图标、验证回调通路、改提示、弹气泡、拆掉。

不点菜单（TrackPopupMenu 会阻塞等人点），改成单独验证菜单项拼装那几个 API 的参数类型。
这个脚本不需要管理员，也不碰桌面图标。

用法：python _dev/_tray_selftest.py     （退出码 0 = 通过）
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import desktop_layout_keeper as K  # noqa: E402

fails = []
count = [0]


def check(label, ok, extra=""):
    count[0] += 1
    print("  [%s] %s%s" % ("OK" if ok else "FAIL", label, ("  " + extra) if extra else ""))
    if not ok:
        fails.append(label)


print("--- 1. 建图标 ---")
tip = K.TrayIcon("桌面布局管家：锁定运行中")
check("create()", tip.create(), "error=%r" % tip.error)
check("alive()", tip.alive(), "hwnd=0x%X" % tip.hwnd)
check("窗口真实存在", bool(K._u32.IsWindow(tip.hwnd)))
check("图标句柄已取得", bool(tip._own_icon), "hIcon=0x%X" % (tip._own_icon or 0))

print("--- 2. 左键回调通路（PostMessage → pump → actions）---")
K._u32.PostMessageW(tip.hwnd, K._Win32.TRAY_CALLBACK_MSG, 0, K._Win32.WM_LBUTTONUP)
time.sleep(0.2)
tip.pump()
check("左键产生 open 动作", "open" in tip.actions, "actions=%r" % tip.actions)

print("--- 3. 菜单命令通路（WM_COMMAND）---")
tip.actions.clear()
K._u32.PostMessageW(tip.hwnd, K._Win32.WM_COMMAND, K.TrayIcon.CMD_CHECK, 0)
K._u32.PostMessageW(tip.hwnd, K._Win32.WM_COMMAND, K.TrayIcon.CMD_STOP_LOCK, 0)
time.sleep(0.2)
tip.pump()
check("命令映射正确", tip.actions == ["check", "stop_lock"], "actions=%r" % tip.actions)

print("--- 4. 菜单项拼装（只建菜单，不弹）---")
menu = K._u32.CreatePopupMenu()
if menu:
    a = K._u32.AppendMenuW(menu, K._Win32.MF_STRING, K.TrayIcon.CMD_OPEN, "打开主界面")
    b = K._u32.AppendMenuW(menu, K._Win32.MF_SEPARATOR, 0, None)
    c = K._u32.AppendMenuW(menu, K._Win32.MF_STRING, K.TrayIcon.CMD_QUIT, "退出程序")
    check("AppendMenuW 三项都成功", bool(a) and bool(b) and bool(c))
    check("GetMenuItemCount == 3", K._u32.GetMenuItemCount(menu) == 3)
    K._u32.DestroyMenu(menu)
else:
    check("CreatePopupMenu", False)

print("--- 5. 提示与气泡 ---")
tip.set_tip("桌面布局管家：已检查 12 次 / 拉回 2 次")
check("set_tip 后内存里的提示已更新", "已检查 12 次" in tip._tip, tip._tip)
try:
    tip.notify("锁定运行中", "已检查 12 次，共拉回 2 次")
    check("notify 不抛异常", True)
except Exception as exc:
    check("notify 不抛异常", False, repr(exc))

print("--- 6. 拆掉 ---")
tip.destroy()
check("destroy 后 alive() 为假", not tip.alive())
check("destroy 后 hwnd 清零", tip.hwnd == 0)

print()
if fails:
    print("FAILED: %s" % "; ".join(fails))
    raise SystemExit(1)
print("TRAY SELFTEST ALL PASS（%d 项）" % count[0])
