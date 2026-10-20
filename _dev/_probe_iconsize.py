#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真机确认：桌面图标大小能不能靠「改 IconSize 注册表 + 某种刷新」还原。

（第一版探针写错了常量：用了 0x1005，那是 LVM_SCROLL 不是 LVM_GETITEMSPACING，
  所以测出来的间距永远是 0。源码里的 0x1033 是对的。这版修掉。）

观测信号用三个，互相印证：
  A. LVM_GETITEMSPACING(0x1033)        → 网格单元格尺寸
  B. LVM_GETITEMRECT(0x100E, LVIR_ICON) → 第 0 个图标的实际绘制尺寸
  C. 注册表 IconLayouts 二进制块长度    → 每个分辨率的布局存档数量

刷新方式逐个试：SHChangeNotify / WM_SETTINGCHANGE / refresh_view /
切换「显示桌面图标」(HideIcons) 逼 DefView 重建。

全程 try/finally 还原：坐标写回、IconSize 写回 48、自动排列恢复。
"""
import ctypes
import os
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import desktop_layout_keeper as K

BAG = r"Software\Microsoft\Windows\Shell\Bags\1\Desktop"
ADV = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
LVM_GETITEMRECT = 0x100E
LVIR_BOUNDS, LVIR_ICON = 0, 1

advapi = ctypes.WinDLL("advapi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

HKEY_CURRENT_USER = 0x80000001
KEY_READ, KEY_WRITE = 0x20019, 0x20006
REG_DWORD = 4

for fn, argt in (
    ("RegOpenKeyExW", [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                       ctypes.POINTER(ctypes.c_void_p)]),
    ("RegQueryValueExW", [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p,
                          ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
                          ctypes.POINTER(ctypes.c_uint32)]),
    ("RegSetValueExW", [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                        ctypes.c_void_p, ctypes.c_uint32]),
    ("RegCloseKey", [ctypes.c_void_p]),
):
    getattr(advapi, fn).argtypes = argt


def _open(path, mode):
    h = ctypes.c_void_p()
    if advapi.RegOpenKeyExW(HKEY_CURRENT_USER, path, 0, mode, ctypes.byref(h)) != 0:
        raise OSError("打不开注册表键 %s" % path)
    return h


def reg_get_dword(path, name):
    h = _open(path, KEY_READ)
    try:
        val, size = ctypes.c_uint32(), ctypes.c_uint32(4)
        rc = advapi.RegQueryValueExW(h, name, None, None, ctypes.byref(val), ctypes.byref(size))
        return val.value if rc == 0 else None
    finally:
        advapi.RegCloseKey(h)


def reg_set_dword(path, name, value):
    h = _open(path, KEY_WRITE)
    try:
        val = ctypes.c_uint32(value)
        rc = advapi.RegSetValueExW(h, name, 0, REG_DWORD, ctypes.byref(val), ctypes.sizeof(val))
        if rc != 0:
            raise OSError("写 %s\\%s 失败 rc=%d" % (path, name, rc))
    finally:
        advapi.RegCloseKey(h)


def reg_get_binary(path, name):
    h = _open(path, KEY_READ)
    try:
        size = ctypes.c_uint32(0)
        if advapi.RegQueryValueExW(h, name, None, None, None, ctypes.byref(size)) != 0:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if advapi.RegQueryValueExW(h, name, None, None, buf, ctypes.byref(size)) != 0:
            return None
        return buf.raw[:size.value]
    finally:
        advapi.RegCloseKey(h)


def notify_assoc_changed():
    shell32.SHChangeNotify(0x08000000, 0x0000, None, None)


def notify_setting_change():
    res = ctypes.c_ulong()
    user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, ctypes.c_wchar_p("Desktop"),
                               0x0002, 2000, ctypes.byref(res))


def measure(view, label):
    hwnd = view.hwnd
    sp = K._send(hwnd, K._Win32.LVM_GETITEMSPACING, 1)
    spacing = (sp & 0xFFFF, (sp >> 16) & 0xFFFF) if sp else None

    addr = view.remote.alloc(16)
    try:
        view.remote.write(addr, struct.pack("<4i", LVIR_ICON, 0, 0, 0))
        K._send(hwnd, LVM_GETITEMRECT, 0, addr)
        l, t, r, b2 = struct.unpack("<4i", view.remote.read(addr, 16))
        icon = (r - l, b2 - t)
    finally:
        view.remote.free(addr)

    layouts = reg_get_binary(BAG, "IconLayouts")
    print("  %-30s IconSize=%-4s 间距=%-10s 图标矩形=%-10s IconLayouts=%s 字节"
          % (label, reg_get_dword(BAG, "IconSize"), spacing, icon,
             len(layouts) if layouts else None))
    return spacing, icon


def probe():
    b = K.DesktopBackend()
    views = b.open_views()
    if not views:
        print("拿不到桌面视图")
        return 1
    v = views[0]
    hwnd = v.hwnd
    saved_auto = K.auto_arrange_enabled(hwnd)
    saved_hide = reg_get_dword(ADV, "HideIcons")
    saved_items = None
    try:
        print("起始：自动排列=%s 对齐到网格=%s HideIcons=%s"
              % (saved_auto, K.snap_to_grid_enabled(hwnd), saved_hide))
        measure(v, "基线")

        K.set_auto_arrange(hwnd, False)
        K.refresh_view(hwnd)
        time.sleep(0.4)
        saved_items = [it for it in K.read_view_items(v) if it["name"]]
        print("已关自动排列，记住 %d 个图标坐标\n" % len(saved_items))

        print("--- IconSize 48 → 32，逐个刷新方式试 ---")
        reg_set_dword(BAG, "IconSize", 32)
        measure(v, "写入后（未刷新）")

        notify_assoc_changed()
        time.sleep(1.2)
        measure(v, "+SHChangeNotify")

        notify_setting_change()
        time.sleep(1.2)
        measure(v, "+WM_SETTINGCHANGE")

        K.refresh_view(hwnd)
        time.sleep(0.8)
        measure(v, "+refresh_view")

        print("  （切 HideIcons 逼 DefView 重建）")
        reg_set_dword(ADV, "HideIcons", 1)
        notify_assoc_changed()
        time.sleep(2.5)
        print("    HideIcons=1 时视图数 =", len(b.open_views()))
        reg_set_dword(ADV, "HideIcons", 0)
        notify_assoc_changed()
        time.sleep(2.5)
        views2 = b.open_views()
        print("    HideIcons=0 后视图数 =", len(views2))
        if views2:
            v = views2[0]
            measure(v, "重建后")

        print("\n--- 还原 IconSize 48 ---")
        reg_set_dword(BAG, "IconSize", 48)
        notify_assoc_changed()
        time.sleep(1.2)
        K.refresh_view(hwnd)
        time.sleep(0.6)
        measure(v, "还原后")

        print("\n--- Sort 二进制块 ---")
        blob = reg_get_binary(BAG, "Sort")
        print("长度 %s，hex %s" % (len(blob) if blob else None, blob.hex() if blob else None))
        return 0
    finally:
        if saved_items:
            for it in saved_items:
                try:
                    K.set_item_position(v, it["index"], it["x"], it["y"])
                except OSError:
                    pass
            K.refresh_view(hwnd)
        reg_set_dword(BAG, "IconSize", 48)
        if saved_hide is not None:
            reg_set_dword(ADV, "HideIcons", saved_hide)
        K.set_auto_arrange(hwnd, saved_auto)
        K.refresh_view(hwnd)
        print("\n现场已还原：IconSize=%s HideIcons=%s 自动排列=%s 对齐到网格=%s 图标数=%d"
              % (reg_get_dword(BAG, "IconSize"), reg_get_dword(ADV, "HideIcons"),
                 K.auto_arrange_enabled(hwnd), K.snap_to_grid_enabled(hwnd),
                 len([it for it in K.read_view_items(v) if it["name"]])))
        b.close_views(views)


if __name__ == "__main__":
    sys.exit(probe())
