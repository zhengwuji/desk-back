# -*- coding: utf-8 -*-
"""诊断：桌面 ListView 的几何/样式，以及坐标被改写的真实原因。"""
import ctypes
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import desktop_layout_keeper as m

LVM_GETEXTENDEDLISTVIEWSTYLE = m._Win32.LVM_FIRST + 55
LVM_SETEXTENDEDLISTVIEWSTYLE = m._Win32.LVM_FIRST + 54
LVS_EX_SNAPTOGRID = 0x00080000
LVS_EX_AUTOARRANGE = 0x00000100

g = m._u32.GetSystemMetrics
print("screen      =", g(0), g(1))
print("virtual     =", g(76), g(77), g(78), g(79))
print("monitors    =", g(80))

k = m.DesktopBackend()
views = k.open_views()
print("views       =", len(views))
v = views[0]
hwnd = v.hwnd

style = m.get_style(hwnd)
exstyle = m._u32.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF
lv_ex = m._send(hwnd, LVM_GETEXTENDEDLISTVIEWSTYLE, 0, 0)
print("STYLE       = 0x%08X  autoarrange=%s" % (style, bool(style & 0x100)))
print("EXSTYLE     = 0x%08X" % exstyle)
print("LVS_EX      = 0x%08X  snaptogrid=%s autoarrange_ex=%s"
      % (lv_ex, bool(lv_ex & LVS_EX_SNAPTOGRID), bool(lv_ex & LVS_EX_AUTOARRANGE)))
sp = m._send(hwnd, m._Win32.LVM_GETITEMSPACING, 1)
print("SPACING(1)  =", sp & 0xFFFF, (sp >> 16) & 0xFFFF)
sp0 = m._send(hwnd, m._Win32.LVM_GETITEMSPACING, 0)
print("SPACING(0)  =", sp0 & 0xFFFF, (sp0 >> 16) & 0xFFFF)

before = m.read_view_items(v)
print("before: n=%d  %s" % (len(before), [(i["index"], i["name"], i["x"], i["y"]) for i in before[:4]]))

name0 = before[0]["name"]
orig = m.set_auto_arrange(hwnd, False)
print("auto-arrange off ->", orig)

# 关掉网格吸附，看看能否自由摆放
lv_ex2 = m._send(hwnd, LVM_GETEXTENDEDLISTVIEWSTYLE, 0, 0)
m._send(hwnd, LVM_SETEXTENDEDLISTVIEWSTYLE, 0, lv_ex2 & ~LVS_EX_SNAPTOGRID)
m._send(hwnd, LVM_SETEXTENDEDLISTVIEWSTYLE, LVS_EX_SNAPTOGRID, 0)
print("after clear snap -> LVS_EX = 0x%08X"
      % (m._send(hwnd, LVM_GETEXTENDEDLISTVIEWSTYLE, 0, 0)))

for (idx, x, y) in [(0, 900, 400), (1, 500, 900), (2, 137, 37)]:
    m.set_item_position(v, idx, x, y)
m.refresh_view(hwnd)

after = m.read_view_items(v)
print("after : n=%d  %s" % (len(after), [(i["index"], i["name"], i["x"], i["y"]) for i in after[:4]]))
byname = {i["name"]: (i["x"], i["y"]) for i in after}
print("被移动的图标现在在哪：")
print("   ", name0, "->", byname.get(name0))
print("    控制面板 ->", byname.get("控制面板"))
print("    网络     ->", byname.get("网络"))

# 还原现场
m.set_auto_arrange(hwnd, True)
m._send(hwnd, m._Win32.LVM_ARRANGE, m._Win32.LVA_DEFAULT, 0)
m.refresh_view(hwnd)
print("restored auto =", bool(m.get_style(hwnd) & 0x100))
k.close_views(views)
