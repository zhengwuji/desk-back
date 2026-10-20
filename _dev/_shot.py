# -*- coding: utf-8 -*-
"""抓一张窗口截图（纯标准库：ctypes + zlib 手写 PNG）。

用法：
    python _dev/_shot.py --exe "桌面布局管家.exe" --out _dev/shot.png
    python _dev/_shot.py --python src/desktop_layout_keeper.py --out _dev/shot.png
    python _dev/_shot.py --pythonw _dev/_shot_nonadmin.py --out _dev/shot_nonadmin.png

它会：启动目标 → 找到该进程的主窗口 → PrintWindow 抓像素 → 存 PNG → 杀掉进程。
不点任何按钮、不改任何设置。
"""

from __future__ import annotations

import argparse
import ctypes
import os
import struct
import subprocess
import sys
import time
import zlib
from ctypes import wintypes as wt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

u32 = ctypes.WinDLL("user32", use_last_error=True)
g32 = ctypes.WinDLL("gdi32", use_last_error=True)

u32.EnumWindows.restype = wt.BOOL
u32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
u32.IsWindowVisible.restype = wt.BOOL
u32.IsWindowVisible.argtypes = [wt.HWND]
u32.GetWindowTextLengthW.restype = ctypes.c_int
u32.GetWindowTextLengthW.argtypes = [wt.HWND]
u32.GetWindowTextW.restype = ctypes.c_int
u32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u32.GetWindowThreadProcessId.restype = wt.DWORD
u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
u32.GetWindowRect.restype = wt.BOOL
u32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u32.PrintWindow.restype = wt.BOOL
u32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
u32.GetClientRect.restype = wt.BOOL
u32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u32.GetDC.restype = wt.HDC
u32.GetDC.argtypes = [wt.HWND]
u32.ReleaseDC.restype = ctypes.c_int
u32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
g32.CreateCompatibleDC.restype = wt.HDC
g32.CreateCompatibleDC.argtypes = [wt.HDC]
g32.CreateCompatibleBitmap.restype = wt.HBITMAP
g32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
g32.SelectObject.restype = wt.HGDIOBJ
g32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
g32.DeleteObject.restype = wt.BOOL
g32.DeleteObject.argtypes = [wt.HGDIOBJ]
g32.DeleteDC.restype = wt.BOOL
g32.DeleteDC.argtypes = [wt.HDC]
g32.GetDIBits.restype = ctypes.c_int
g32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p,
                          ctypes.c_void_p, wt.UINT]

PW_RENDERFULLCONTENT = 0x00000002
DIB_RGB_COLORS = 0


def child_pids(root_pid: int) -> set:
    """PyInstaller onefile 的 exe 会再 fork 一个子进程，窗口在子进程里。
    所以按 pid 找窗口要把整棵进程树都算上。"""
    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
            ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
            ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wt.DWORD), ("szExeFile", wt.WCHAR * 260),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    k32.Process32FirstW.restype = wt.BOOL
    k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.c_void_p]
    k32.Process32NextW.restype = wt.BOOL
    k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.c_void_p]

    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    pairs = []
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            pairs.append((pe.th32ProcessID, pe.th32ParentProcessID))
            ok = k32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        k32.CloseHandle(snap)

    pids = {root_pid}
    for _ in range(6):                      # 最多往下追 6 层，够用了
        grown = {c for c, p in pairs if p in pids}
        if grown <= pids:
            break
        pids |= grown
    return pids


def find_window(pids, title_contains: str = "", min_w: int = 200, min_h: int = 150):
    """在这些进程里找第一个「够大的、可见的、有标题的」顶层窗口。

    title_contains 非空时优先按标题匹配（跨进程树也能找到），否则按 pid 匹配。
    """
    if isinstance(pids, int):
        pids = {pids}
    hits = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if not u32.IsWindowVisible(hwnd):
            return True
        n = u32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        rect = wt.RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w < min_w or h < min_h:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value

        wpid = wt.DWORD(0)
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        by_pid = wpid.value in pids
        by_title = bool(title_contains) and title_contains in title
        if by_pid or by_title:
            hits.append((hwnd, title, w, h, by_pid, by_title))
        return True

    u32.EnumWindows(cb, 0)
    if not hits:
        return None
    # pid 对上的优先，其次是标题对上的
    hits.sort(key=lambda t: (not t[4], not t[5]))
    return hits[0][:4]


def grab(hwnd) -> tuple[int, int, bytes]:
    """PrintWindow 抓整窗像素，返回 (w, h, BGR 自底向上字节)。"""
    rect = wt.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top

    hdc_win = u32.GetDC(hwnd)
    hdc_mem = g32.CreateCompatibleDC(hdc_win)
    hbmp = g32.CreateCompatibleBitmap(hdc_win, w, h)
    old = g32.SelectObject(hdc_mem, hbmp)
    try:
        u32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD),
            ]

        bih = BITMAPINFOHEADER()
        bih.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bih.biWidth = w
        bih.biHeight = -h              # 负数 = 自顶向下
        bih.biPlanes = 1
        bih.biBitCount = 32
        bih.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        got = g32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bih), DIB_RGB_COLORS)
        if got == 0:
            raise RuntimeError("GetDIBits 失败")
        return w, h, buf.raw
    finally:
        g32.SelectObject(hdc_mem, old)
        g32.DeleteObject(hbmp)
        g32.DeleteDC(hdc_mem)
        u32.ReleaseDC(hwnd, hdc_win)


def save_png(path: str, w: int, h: int, bgra_topdown: bytes) -> None:
    """BGRA 自顶向下 → RGB PNG。手写，不依赖 Pillow。"""
    rows = bytearray()
    for y in range(h):
        rows.append(0)                                  # filter type 0
        off = y * w * 4
        line = bgra_topdown[off:off + w * 4]
        rgb = bytearray(w * 3)
        rgb[0::3] = line[2::4]                          # R
        rgb[1::3] = line[1::4]                          # G
        rgb[2::3] = line[0::4]                          # B
        rows += rgb

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(rows), 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def all_pids_of(image_name: str) -> set:
    """按映像名找出所有正在运行的 pid（不区分子进程层级）。"""
    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
            ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
            ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wt.DWORD), ("szExeFile", wt.WCHAR * 260),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    k32.Process32FirstW.restype = wt.BOOL
    k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.c_void_p]
    k32.Process32NextW.restype = wt.BOOL
    k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.c_void_p]

    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    pids = set()
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            if pe.szExeFile.lower() == image_name.lower():
                pids.add(int(pe.th32ProcessID))
            ok = k32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        k32.CloseHandle(snap)
    return pids


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=False)
    g.add_argument("--exe", help="要启动的 exe（相对项目根目录）")
    g.add_argument("--python", help="用 python 启动的脚本（会带控制台）")
    g.add_argument("--pythonw", help="用 pythonw 启动的脚本（无控制台，等价于 exe）")
    g.add_argument("--attach", metavar="IMAGE",
                   help="不启动任何东西，直接抓已经在运行的 IMAGE 的窗口（如 桌面布局管家.exe）")
    ap.add_argument("--out", default="_dev/shot.png")
    ap.add_argument("--wait", type=float, default=4.0, help="等窗口出现的最长秒数")
    ap.add_argument("--title", default="桌面布局管家",
                    help="按标题关键字找窗口（跨进程树，默认 桌面布局管家）")
    ap.add_argument("--kill-old", action="store_true",
                    help="先杀掉同名的旧实例，避免抓到上一轮残留的窗口")
    args = ap.parse_args()

    if not (args.exe or args.python or args.pythonw or args.attach):
        ap.error("要给出 --exe / --python / --pythonw / --attach 之一")

    if args.attach:
        out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
        shot = find_window(all_pids_of(args.attach), args.title)
        if not shot:
            print("没找到 %s 的窗口" % args.attach)
            return 1
        hwnd, title, w, h = shot
        time.sleep(0.6)
        gw, gh, pixels = grab(hwnd)
        save_png(out, gw, gh, pixels)
        print("窗口标题：%s" % title)
        print("尺寸：%dx%d" % (gw, gh))
        print("截图：%s（%d 字节）" % (out, os.path.getsize(out)))
        return 0

    if args.kill_old:
        for name in ("桌面布局管家", "DesktopLayoutKeeper_v1", "pythonw"):
            subprocess.run(["taskkill", "/F", "/IM", name + ".exe"],
                           capture_output=True)

    if args.exe:
        target = args.exe if os.path.isabs(args.exe) else os.path.join(ROOT, args.exe)
        cmd = [target]
    else:
        script = os.path.join(ROOT, args.python or args.pythonw)
        exe = "python.exe" if args.python else "pythonw.exe"
        cmd = [exe, script]

    print("启动：%s" % " ".join(cmd))
    proc = subprocess.Popen(cmd, cwd=ROOT)

    shot = None
    t0 = time.time()
    while time.time() - t0 < args.wait:
        time.sleep(0.4)
        if proc.poll() is not None:
            print("进程提前退出了，退出码 %s" % proc.returncode)
            break
        tree = child_pids(proc.pid)
        win = find_window(tree, args.title)
        if win:
            shot = win
            break

    if not shot:
        proc.kill()
        print("没找到窗口")
        return 1

    hwnd, title, w, h = shot
    time.sleep(0.6)                      # 等它把首帧画完
    gw, gh, pixels = grab(hwnd)
    out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    save_png(out, gw, gh, pixels)
    print("窗口标题：%s" % title)
    print("尺寸：%dx%d" % (gw, gh))
    print("截图：%s（%d 字节）" % (out, os.path.getsize(out)))

    proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
