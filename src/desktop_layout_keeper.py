#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 桌面布局管家 (Desktop Layout Keeper) — Copyright (c) 2026 zhengwuji
# 以 MIT 许可证发布，许可证全文见仓库根目录的 LICENSE
r"""
桌面布局管家 / DesktopLayoutKeeper
==================================
记住 Windows 桌面图标的排列布局，一键恢复，并可长期锁定。

功能
  * 快照       读取当前桌面全部图标的「名称 + 坐标」（支持多个桌面视图）
  * 多套布局   保存任意多套命名布局，随时切换
  * 一键恢复   把图标搬回快照位置，并在恢复后逐项复核、报告是否精确落位
  * 锁定布局   后台守护线程持续监控，图标被拖动后自动还原
  * 网格整理   把图标吸附到网格（不改变顺序，只对齐）
  * 精确还原   恢复后会把坐标读回来逐项复核，并报告精确落位数量
               （本机实测 62 个图标：62/62 精确落位，布局与初始逐项一致）
  * 适配屏幕   保存和恢复之间换了分辨率/显示器时，按比例缩放置位并夹进屏幕，
               不会把图标写到屏幕外面去；锁定期间检测到屏幕变了会主动暂停
  * 记录外观   快照里一并记下图标大小与排序方式，不一致时明确报出来并给出
               一步就能照做的改法（不假装能把它们当场改回去，见「坑 3」）
  * 管理员     显示当前是否以管理员身份运行；可一键以管理员身份重启；
               可勾选「总是以管理员身份运行」，未提权时启动会主动提醒
  * 自动备份   每次恢复前自动留一份当前布局，可回退

实现说明
  桌面图标由 explorer.exe 的 SysListView32 控件承载（Progman / WorkerW → SHELLDLL_DefView）。
  本程序用 ListView 消息在 explorer 进程内读写图标坐标：
      LVM_GETITEMCOUNT / LVM_GETITEMPOSITION / LVM_GETITEMTEXTW / LVM_SETITEMPOSITION
      LVM_GET/SETEXTENDEDLISTVIEWSTYLE（读/写「对齐到网格」状态）
  跨进程存取结构体走 VirtualAllocEx + Read/WriteProcessMemory。
  「锁定」= 关闭 LVS_AUTOARRANGE + 守护线程按记录坐标纠偏（不注入、不 hook，纯消息层）。

两个必须处理的坑
  1) 「自动排列图标」(LVS_AUTOARRANGE) 开着时，LVM_SETITEMPOSITION 完全无效。→ 恢复前关掉。
  2) 坐标有两套"记号"，靠上面的开关切换。实测同一批图标：
       开「对齐到网格」读出来是 (0,5)   (0,103)   (0,201) …
       关掉之后同一个位置读出来是 (13,2) (13,100) (13,198) …
     也就是整体差 (13,-3)。这是同一个物理位置的两种写法，不是真的挪了位置。
     一旦读的时候一个记号、写的时候另一个记号，就会整屏错位（早期版本踩过这个坑）。
     → 做法有两条，都不需要写死任何魔法数字：
        a. 快照时记下当时的吸附状态，恢复时先把它拨回同一个状态；
        b. 比对前先"自校准"：让每个图标各投一票（当前坐标 - 目标坐标），
           票数集中到同一个偏移就认定是记号差异（coordinate_offset），
           票分散就认定是真漂移。因此恢复/锁定都不会被记号变化带偏。
  3) 「图标大小」和「排序方式」不在 ListView 里，而在资源管理器自己的注册表：
     HKCU\Software\Microsoft\Windows\Shell\Bags\1\Desktop 下的 IconSize(DWORD)
     与 Sort(20 字节)，以及 Explorer\Advanced 下的 HideIcons。
     实测（_dev/_probe_iconsize.py）：改完之后 SHChangeNotify、WM_SETTINGCHANGE、
     重画视图、甚至切 HideIcons 想逼 DefView 重建 —— 间距、图标矩形、IconLayouts
     全都没动。结论是这些值只在「视图创建时」被读取（登录 / 重启资源管理器）。
     → 所以本程序只做两件事：把保存值写回注册表（下次登录生效），以及
       在恢复前把差异明确报出来。绝不假装"已经改回来了"，也绝不为让它生效
       去重启 explorer（那会关掉用户所有资源管理器窗口）。

依赖：仅标准库（ctypes + tkinter）。
用法：
  桌面布局管家.exe                                 启动 GUI（双击）
  桌面布局管家.exe --cli-snapshot                  打印当前桌面布局 JSON
  桌面布局管家.exe --cli-save NAME                 保存一套布局（--overwrite 覆盖同名）
  桌面布局管家.exe --cli-restore NAME              恢复某套布局
  桌面布局管家.exe --version                       看当前是哪一版
源码运行把 exe 换成 python desktop_layout_keeper.py 即可。
"""

from __future__ import annotations

import ctypes
import json
import os
import struct
import sys
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

APP_NAME = "DesktopLayoutKeeper"      # 英文 id：决定数据目录名，别改
APP_TITLE = "桌面布局管家"             # 中文名：只用于窗口标题
APP_VERSION = "1.3.0"                 # 语义版本；exe 的 vN 由 build.py 写进 PE 资源
IS_WINDOWS = sys.platform.startswith("win")
from ctypes import wintypes as wt   # noqa: E402  （模块级别名，函数里也有局部同名导入）


# ---------------------------------------------------------------------------
# 版本：exe 显示自己的版本，源码运行显示 v-dev
# ---------------------------------------------------------------------------
def read_exe_version(path: str = None):
    """读一个 PE 文件的版本资源，返回 (ProductVersion, FileVersion)。

    版本号是 build.py 编译时烧进 exe 资源里的，所以标题栏显示的版本
    永远等于"这个 exe 是哪一次编译出来的"，不会出现改了源码却没重新编译
    却显示新版本号的错位。
    """
    if not IS_WINDOWS:
        return None, None
    path = path or sys.executable
    try:
        ver = ctypes.WinDLL("version", use_last_error=True)
    except Exception:
        return None, None
    try:
        wt = ctypes.wintypes
        ver.GetFileVersionInfoSizeW.restype = wt.DWORD
        ver.GetFileVersionInfoSizeW.argtypes = [wt.LPCWSTR, ctypes.POINTER(wt.DWORD)]
        ver.GetFileVersionInfoW.restype = wt.BOOL
        ver.GetFileVersionInfoW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD,
                                            ctypes.c_void_p]
        ver.VerQueryValueW.restype = wt.BOOL
        ver.VerQueryValueW.argtypes = [ctypes.c_void_p, wt.LPCWSTR,
                                       ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(wt.UINT)]

        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None, None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path, 0, size, buf):
            return None, None

        ptr = ctypes.c_void_p()
        plen = wt.UINT()
        if not ver.VerQueryValueW(buf, "\\VarFileInfo\\Translation",
                                  ctypes.byref(ptr), ctypes.byref(plen)):
            return None, None
        if not plen.value or not ptr.value:
            return None, None
        lang, codepage = struct.unpack("<HH", ctypes.string_at(ptr.value, 4))

        def query(field: str):
            sub = "\\StringFileInfo\\%04x%04x\\%s" % (lang, codepage, field)
            p2, n2 = ctypes.c_void_p(), wt.UINT()
            if not ver.VerQueryValueW(buf, sub, ctypes.byref(p2), ctypes.byref(n2)):
                return None
            if not n2.value or not p2.value:
                return None
            return ctypes.wstring_at(p2.value, n2.value).rstrip("\x00").strip()

        return query("ProductVersion"), query("FileVersion")
    except Exception:
        return None, None


def version_text() -> str:
    """标题栏用的版本串：打包后是 exe 里烧的 vN，直接跑源码是 v-dev。"""
    if getattr(sys, "frozen", False):
        prod, _fv = read_exe_version()
        return (prod or "v?").strip()
    return "v-dev"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _handle_ok(h) -> bool:
    """判断 GetStdHandle 拿到的句柄是不是真的能用。"""
    if h is None:
        return False
    try:
        v = int(h) if not isinstance(h, int) else h
    except (TypeError, ValueError):
        return False
    return v != 0 and v != -1


def _stdout_handle():
    try:
        return _k32.GetStdHandle(_Win32.STD_OUTPUT_HANDLE)
    except Exception:
        return None


def _encode_for_handle(h, text: str) -> bytes:
    """控制台按控制台代码页编码（中文 Windows 是 936），管道/文件用 UTF-8。"""
    enc = "utf-8"
    try:
        if _k32.GetFileType(h) == _Win32.FILE_TYPE_CHAR:
            cp = _k32.GetConsoleOutputCP() or 0
            if cp:
                enc = "cp%d" % cp
    except Exception:
        pass
    try:
        return text.encode(enc, "replace")
    except LookupError:
        return text.encode("utf-8", "replace")


def _write_handle(h, text: str) -> bool:
    data = _encode_for_handle(h, text)
    if not data:
        return True
    try:
        written = wt.DWORD(0)
        buf = ctypes.create_string_buffer(data, len(data))
        ok = _k32.WriteFile(h, buf, len(data), ctypes.byref(written), None)
        return bool(ok)
    except Exception:
        return False


def ensure_console_output() -> bool:
    """让打包成「无控制台」的 exe 也能在命令行里输出。

    --noconsole 的 exe（PyInstaller runw 引导）默认没有 std 句柄：
      - 被 cmd / PowerShell 以管道或重定向调用 → 句柄是继承来的，能直接写；
      - 被 cmd 直接调用（没有 STARTF_USESTDHANDLES）→ 句柄是空的，
        得 AttachConsole(ATTACH_PARENT_PROCESS) 挂到父控制台上；
      - 双击运行 → 两样都没有，调用方改用文件输出。
    """
    if not IS_WINDOWS:
        return sys.stdout is not None
    h = _stdout_handle()
    if _handle_ok(h):
        return True
    if not is_frozen():
        return sys.stdout is not None
    try:
        if _k32.AttachConsole(_Win32.ATTACH_PARENT_PROCESS):
            return _handle_ok(_stdout_handle())
    except Exception:
        pass
    return False


def cli_write(text: str) -> str:
    """命令行模式的统一输出口。

    依次尝试：继承来的标准输出（控制台 / 管道 / 重定向文件）
    → 挂到父控制台 → 落到 %APPDATA%\\DesktopLayoutKeeper\\cli-output.txt。
    返回实际写到了哪儿。
    """
    if not IS_WINDOWS:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
            return "控制台"
        except Exception:
            return ""

    h = _stdout_handle()
    if _handle_ok(h) and _write_handle(h, text):
        try:
            is_console = _k32.GetFileType(h) == _Win32.FILE_TYPE_CHAR
        except Exception:
            is_console = False
        return "控制台" if is_console else "标准输出"

    if is_frozen():
        try:
            if _k32.AttachConsole(_Win32.ATTACH_PARENT_PROCESS):
                h = _stdout_handle()
                if _handle_ok(h) and _write_handle(h, text):
                    return "控制台"
        except Exception:
            pass

    out = data_dir() / "cli-output.txt"
    try:
        out.write_text(text, encoding="utf-8")
        return str(out)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 路径与持久化位置
# ---------------------------------------------------------------------------
def data_dir() -> Path:
    base = (
        os.environ.get("APPDATA")
        or os.environ.get("LOCALAPPDATA")
        or str(Path.home())
    )
    p = Path(base) / APP_NAME
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        p = Path.home() / (".%s" % APP_NAME)
        p.mkdir(parents=True, exist_ok=True)
    return p


STORE_FILE = data_dir() / "layouts.json"

DEFAULT_CONFIG = {
    "prefer_admin": False,       # 总是以管理员身份运行
    "lock_interval": 2.0,        # 锁定守护轮询间隔（秒）
    "auto_backup_keep": 8,       # 自动备份保留条数
    "disable_auto_arrange": True,
    "index_fallback": True,
    # 没有「自由摆放」开关：桌面「对齐到网格」会改变坐标的语义单位，
    # 恢复时必须沿用保存时的状态，否则整屏会偏移半格。详见文件头说明。
}

# 坐标系自校准参数：至少这么多比例的图标同意同一个偏移，才认为"是记号不同而不是被挪走"；
# 偏移量超过这个像素数一律不认，避免把大面积真实漂移当成坐标系差异掩盖掉。
OFFSET_MIN_SHARE = 0.6
OFFSET_MAX_PX = 64


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Win32 底层
# ---------------------------------------------------------------------------
if IS_WINDOWS:
    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _adv32 = ctypes.WinDLL("advapi32", use_last_error=True)
else:  # 仅为让模块在非 Windows 上也能被 import（运行时会拒绝启动）
    _u32 = _k32 = _shell32 = _adv32 = None


class _Win32:
    """Win32 常量集合。"""

    # --- 权限 / 内存 ---
    PROCESS_VM_OPERATION = 0x0008
    PROCESS_VM_READ = 0x0010
    PROCESS_VM_WRITE = 0x0020
    PROCESS_QUERY_INFORMATION = 0x0400

    MEM_COMMIT = 0x1000
    MEM_RESERVE = 0x2000
    MEM_RELEASE = 0x8000
    PAGE_READWRITE = 0x04

    # --- SendMessageTimeout ---
    SMTO_BLOCK = 0x0001
    SMTO_ABORTIFHUNG = 0x0002

    # --- 窗口 ---
    GWL_STYLE = -16
    GWL_EXSTYLE = -20

    # --- ListView 样式 (style) ---
    LVS_AUTOARRANGE = 0x0100
    LVS_ALIGNMASK = 0x0C00
    LVS_ALIGNTOP = 0x0000
    LVS_ALIGNLEFT = 0x0800

    # --- ListView 扩展样式 (extended style) ---
    LVS_EX_AUTOARRANGE = 0x00000100
    LVS_EX_SNAPTOGRID = 0x00080000

    # --- ListView 消息 ---
    LVM_FIRST = 0x1000
    LVM_GETITEMCOUNT = LVM_FIRST + 4        # 0x1004
    LVM_SETITEMPOSITION = LVM_FIRST + 15    # 0x100F
    LVM_GETITEMPOSITION = LVM_FIRST + 16    # 0x1010
    LVM_REDRAWITEMS = LVM_FIRST + 21        # 0x1015
    LVM_ARRANGE = LVM_FIRST + 22            # 0x1016
    LVM_SETITEMPOSITION32 = LVM_FIRST + 49  # 0x1031
    LVM_GETITEMSPACING = LVM_FIRST + 51     # 0x1033
    LVM_SETEXTENDEDLISTVIEWSTYLE = LVM_FIRST + 54  # 0x1036
    LVM_GETEXTENDEDLISTVIEWSTYLE = LVM_FIRST + 55  # 0x1037
    LVM_GETITEMTEXTW = LVM_FIRST + 115      # 0x1073

    LVIF_TEXT = 0x0001

    # LVM_ARRANGE 的 wParam
    LVA_DEFAULT = 0x0000
    LVA_ALIGNLEFT = 0x0001
    LVA_ALIGNTOP = 0x0002
    LVA_SNAPTOGRID = 0x0005

    # RedrawWindow 标志
    RDW_INVALIDATE = 0x0001
    RDW_UPDATENOW = 0x0100
    RDW_ALLCHILDREN = 0x0080

    # GetSystemMetrics 索引
    SM_CXSCREEN = 0
    SM_CYSCREEN = 1
    SM_XVIRTUALSCREEN = 76
    SM_YVIRTUALSCREEN = 77
    SM_CXVIRTUALSCREEN = 78
    SM_CYVIRTUALSCREEN = 79
    SM_CMONITORS = 80

    # 跨进程共享缓冲区布局
    BUF_TOTAL = 4096
    OFF_LVITEM = 0
    OFF_TEXT = 1024
    OFF_POINT = 2048
    TEXT_CHARS = 260

    # ---- 控制台 / 标准输出（打包成无控制台 exe 后靠这几个把输出捞回来）----
    STD_OUTPUT_HANDLE = -11
    ATTACH_PARENT_PROCESS = -1
    FILE_TYPE_CHAR = 0x0002

    # ---- 注册表（桌面外观：图标大小、排序方式、是否显示桌面图标）----
    HKEY_CURRENT_USER = 0x80000001
    KEY_READ = 0x20019
    KEY_WRITE = 0x20006
    REG_DWORD = 4
    SHELL_BAG_KEY = r"Software\Microsoft\Windows\Shell\Bags\1\Desktop"
    EXPLORER_ADV_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"


class LVITEMW(ctypes.Structure if IS_WINDOWS else object):
    _fields_ = [
        ("mask", ctypes.c_uint32),
        ("iItem", ctypes.c_int),
        ("iSubItem", ctypes.c_int),
        ("state", ctypes.c_uint32),
        ("stateMask", ctypes.c_uint32),
        ("pszText", ctypes.c_void_p),
        ("cchTextMax", ctypes.c_int),
        ("iImage", ctypes.c_int),
        ("lParam", ctypes.c_void_p),
        ("iIndent", ctypes.c_int),
        ("iGroupId", ctypes.c_int),
        ("cColumns", ctypes.c_uint32),
        ("puColumns", ctypes.c_void_p),
        ("piColFmt", ctypes.c_void_p),
        ("iGroup", ctypes.c_int),
    ]


def _setup_signatures() -> None:
    """显式声明签名，避免 64 位下指针被截断。"""
    if not IS_WINDOWS:
        return
    from ctypes import wintypes as wt

    _u32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
    _u32.SendMessageTimeoutW.argtypes = [
        wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM,
        wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_size_t),
    ]
    _u32.FindWindowExW.restype = wt.HWND
    _u32.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
    _u32.GetClassNameW.restype = ctypes.c_int
    _u32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    _u32.GetWindowThreadProcessId.restype = wt.DWORD
    _u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    _u32.IsWindow.restype = wt.BOOL
    _u32.IsWindow.argtypes = [wt.HWND]
    _u32.IsHungAppWindow.restype = wt.BOOL
    _u32.IsHungAppWindow.argtypes = [wt.HWND]
    _u32.InvalidateRect.restype = wt.BOOL
    _u32.InvalidateRect.argtypes = [wt.HWND, ctypes.c_void_p, wt.BOOL]
    _u32.RedrawWindow.restype = wt.BOOL
    _u32.RedrawWindow.argtypes = [wt.HWND, ctypes.c_void_p, wt.HANDLE, wt.UINT]
    _u32.GetSystemMetrics.restype = ctypes.c_int
    _u32.GetSystemMetrics.argtypes = [ctypes.c_int]
    _u32.SetProcessDPIAware.restype = wt.BOOL
    try:
        _u32.SetProcessDpiAwarenessContext.restype = wt.BOOL
        _u32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    except AttributeError:
        pass

    _k32.OpenProcess.restype = wt.HANDLE
    _k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    _k32.GetConsoleWindow.restype = wt.HWND
    _k32.GetConsoleWindow.argtypes = []
    _k32.AttachConsole.restype = wt.BOOL
    _k32.AttachConsole.argtypes = [wt.DWORD]
    _k32.GetStdHandle.restype = wt.HANDLE
    _k32.GetStdHandle.argtypes = [wt.DWORD]
    _k32.GetFileType.restype = wt.DWORD
    _k32.GetFileType.argtypes = [wt.HANDLE]
    _k32.GetConsoleOutputCP.restype = wt.UINT
    _k32.GetConsoleOutputCP.argtypes = []
    _k32.WriteFile.restype = wt.BOOL
    _k32.WriteFile.argtypes = [
        wt.HANDLE, wt.LPCVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID,
    ]
    _k32.CloseHandle.restype = wt.BOOL
    _k32.CloseHandle.argtypes = [wt.HANDLE]
    _k32.VirtualAllocEx.restype = ctypes.c_void_p
    _k32.VirtualAllocEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, wt.DWORD]
    _k32.VirtualFreeEx.restype = wt.BOOL
    _k32.VirtualFreeEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD]
    _k32.WriteProcessMemory.restype = wt.BOOL
    _k32.WriteProcessMemory.argtypes = [
        wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    _k32.ReadProcessMemory.restype = wt.BOOL
    _k32.ReadProcessMemory.argtypes = [
        wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    _k32.QueryFullProcessImageNameW.restype = wt.BOOL
    _k32.QueryFullProcessImageNameW.argtypes = [
        wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD),
    ]


if IS_WINDOWS:
    _setup_signatures()
    _IS64 = ctypes.sizeof(ctypes.c_void_p) == 8
    if _IS64:
        _get_window_long = _u32.GetWindowLongPtrW
        _set_window_long = _u32.SetWindowLongPtrW
    else:
        _get_window_long = _u32.GetWindowLongW
        _set_window_long = _u32.SetWindowLongW
    _get_window_long.restype = ctypes.c_ssize_t
    _get_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _set_window_long.restype = ctypes.c_ssize_t
    _set_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _lp(value: int) -> int:
    """把无符号数值转成 LPARAM 可安全承载的有符号值（兼容 32 位 Python）。"""
    if not IS_WINDOWS:
        return int(value)
    if ctypes.sizeof(ctypes.c_void_p) == 4 and value > 0x7FFFFFFF:
        return value - 0x100000000
    return int(value)


def _send(hwnd, msg: int, wparam: int = 0, lparam: int = 0, timeout_ms: int = 3000) -> int:
    """带超时的 SendMessage（跨进程安全，目标挂起时不会把本进程卡死）。"""
    result = ctypes.c_size_t(0)
    ok = _u32.SendMessageTimeoutW(
        hwnd, msg, _lp(wparam), _lp(lparam),
        _Win32.SMTO_ABORTIFHUNG | _Win32.SMTO_BLOCK,
        timeout_ms, ctypes.byref(result),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "SendMessageTimeout 失败/超时 (msg=0x%X)" % msg)
    return int(result.value)


def window_class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _u32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd) -> int:
    from ctypes import wintypes as wt

    pid = wt.DWORD(0)
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def process_image_name(pid: int):
    from ctypes import wintypes as wt

    h = _k32.OpenProcess(_Win32.PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        return None
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return None
    finally:
        _k32.CloseHandle(h)


def is_admin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(_shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """以管理员身份重新启动本程序；返回是否成功拉起 UAC。"""
    if not IS_WINDOWS:
        return False
    exe = sys.executable.replace('"', "")
    if getattr(sys, "frozen", False):
        params = ""
    else:
        params = '"%s"' % os.path.abspath(sys.argv[0])
    for a in sys.argv[1:]:
        params += ' "%s"' % a
    try:
        rc = _shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    except Exception:
        return False
    return int(rc) > 32


def choose_dpi_awareness() -> None:
    """在创建 Tk 之前调用，保证界面按物理像素渲染。"""
    if not IS_WINDOWS:
        return
    try:
        if _u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
    except Exception:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        _u32.SetProcessDPIAware()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 跨进程内存
# ---------------------------------------------------------------------------
class RemoteProcess:
    def __init__(self, pid: int):
        self.pid = int(pid)
        flags = (
            _Win32.PROCESS_VM_OPERATION
            | _Win32.PROCESS_VM_READ
            | _Win32.PROCESS_VM_WRITE
            | _Win32.PROCESS_QUERY_INFORMATION
        )
        self.handle = _k32.OpenProcess(flags, False, self.pid)
        if not self.handle:
            err = ctypes.get_last_error()
            hint = "（拒绝访问：请尝试以管理员身份运行本程序）" if err == 5 else ""
            raise OSError(err, "OpenProcess(pid=%d) 失败，错误 %d%s" % (self.pid, err, hint))

    def alloc(self, size: int) -> int:
        addr = _k32.VirtualAllocEx(
            self.handle, None, ctypes.c_size_t(size),
            _Win32.MEM_COMMIT | _Win32.MEM_RESERVE, _Win32.PAGE_READWRITE,
        )
        if not addr:
            err = ctypes.get_last_error()
            raise OSError(err, "VirtualAllocEx 失败，错误 %d" % err)
        return int(addr)

    def free(self, addr: int) -> None:
        if addr:
            _k32.VirtualFreeEx(self.handle, ctypes.c_void_p(addr), ctypes.c_size_t(0),
                               _Win32.MEM_RELEASE)

    def write(self, addr: int, data: bytes) -> int:
        written = ctypes.c_size_t(0)
        buf = ctypes.create_string_buffer(data, len(data))
        ok = _k32.WriteProcessMemory(
            self.handle, ctypes.c_void_p(addr), buf,
            ctypes.c_size_t(len(data)), ctypes.byref(written),
        )
        if not ok:
            raise OSError(ctypes.get_last_error(), "WriteProcessMemory 失败")
        return int(written.value)

    def write_struct(self, addr: int, obj) -> int:
        written = ctypes.c_size_t(0)
        size = ctypes.sizeof(obj)
        ok = _k32.WriteProcessMemory(
            self.handle, ctypes.c_void_p(addr), ctypes.byref(obj),
            ctypes.c_size_t(size), ctypes.byref(written),
        )
        if not ok:
            raise OSError(ctypes.get_last_error(), "WriteProcessMemory 失败")
        return int(written.value)

    def read(self, addr: int, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t(0)
        ok = _k32.ReadProcessMemory(
            self.handle, ctypes.c_void_p(addr), buf,
            ctypes.c_size_t(size), ctypes.byref(got),
        )
        if not ok:
            raise OSError(ctypes.get_last_error(), "ReadProcessMemory 失败")
        return buf.raw[: int(got.value)]

    def close(self) -> None:
        if self.handle:
            _k32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# ---------------------------------------------------------------------------
# 桌面视图发现
# ---------------------------------------------------------------------------
def find_desktop_listviews() -> list:
    """枚举承载桌面图标的 SysListView32 句柄。"""
    if not IS_WINDOWS:
        return []
    from ctypes import wintypes as wt

    handles = []

    def _cb(hwnd, _lparam):
        if window_class(hwnd) in ("Progman", "WorkerW"):
            defview = _u32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
            while defview:
                lv = _u32.FindWindowExW(defview, None, "SysListView32", None)
                if lv:
                    handles.append(lv)
                defview = _u32.FindWindowExW(hwnd, defview, "SHELLDLL_DefView", None)
        return True

    cb = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(_cb)
    _u32.EnumWindows(cb, 0)
    return handles


def _looks_like_shell(pid: int) -> bool:
    name = process_image_name(pid)
    return True if name is None else name.lower() in ("explorer.exe", "explorer")


class DesktopView:
    __slots__ = ("hwnd", "pid", "remote")

    def __init__(self, hwnd, pid, remote):
        self.hwnd = hwnd
        self.pid = pid
        self.remote = remote


# ---------------------------------------------------------------------------
# 样式：自动排列 / 对齐到网格
# ---------------------------------------------------------------------------
def get_style(hwnd) -> int:
    return int(_get_window_long(hwnd, _Win32.GWL_STYLE)) & 0xFFFFFFFF


def set_style(hwnd, style: int) -> None:
    if _u32.IsHungAppWindow(hwnd):
        raise OSError(0, "explorer 无响应，稍后再试")
    _set_window_long(hwnd, _Win32.GWL_STYLE, _lp(style & 0xFFFFFFFF))


def set_auto_arrange(hwnd, enabled: bool) -> bool:
    """开关「自动排列图标」。开着时 LVM_SETITEMPOSITION 无效。返回是否发生改变。"""
    style = get_style(hwnd)
    has = bool(style & _Win32.LVS_AUTOARRANGE)
    if has == bool(enabled):
        return False
    new_style = (style | _Win32.LVS_AUTOARRANGE) if enabled else (style & ~_Win32.LVS_AUTOARRANGE)
    set_style(hwnd, new_style)
    return True


def auto_arrange_enabled(hwnd) -> bool:
    return bool(get_style(hwnd) & _Win32.LVS_AUTOARRANGE)


def get_extended_style(hwnd) -> int:
    return _send(hwnd, _Win32.LVM_GETEXTENDEDLISTVIEWSTYLE, 0, 0)


def set_extended_style_flag(hwnd, flag: int, enabled: bool) -> bool:
    """按 mask/lParam 语义设置或清除一个扩展样式位。返回是否发生改变。"""
    cur = get_extended_style(hwnd)
    has = bool(cur & flag)
    if has == bool(enabled):
        return False
    new = (cur | flag) if enabled else (cur & ~flag)
    _send(hwnd, _Win32.LVM_SETEXTENDEDLISTVIEWSTYLE, flag, new)
    return True


def snap_to_grid_enabled(hwnd) -> bool:
    return bool(get_extended_style(hwnd) & _Win32.LVS_EX_SNAPTOGRID)


def set_snap_to_grid(hwnd, enabled: bool) -> bool:
    """开关「对齐到网格」(LVS_EX_SNAPTOGRID)。

    这个开关不只是"吸附手感"，它还决定坐标的语义单位：
    同一个格子，开着时读出来是格点坐标，关掉时是像素坐标（差半格）。
    所以读和写必须处在同一个模式下。
    """
    return set_extended_style_flag(hwnd, _Win32.LVS_EX_SNAPTOGRID, enabled)


# ---------------------------------------------------------------------------
# 读 / 写图标
# ---------------------------------------------------------------------------
def read_view_items(view: DesktopView) -> list:
    """读取一个桌面视图里的全部图标：名称 + 坐标。

    坐标处在「当前吸附模式」的坐标空间里：开着「对齐到网格」= 格点坐标，
    关掉 = 像素坐标。调用方必须记住读时的模式，写回前先恢复同一个模式。
    """
    lv = view.hwnd
    count = _send(lv, _Win32.LVM_GETITEMCOUNT)
    if count <= 0:
        return []

    base = view.remote.alloc(_Win32.BUF_TOTAL)
    items = []
    try:
        lvitem_addr = base + _Win32.OFF_LVITEM
        text_addr = base + _Win32.OFF_TEXT
        point_addr = base + _Win32.OFF_POINT

        item = LVITEMW()
        item.mask = _Win32.LVIF_TEXT
        item.iSubItem = 0
        item.pszText = ctypes.c_void_p(text_addr)
        item.cchTextMax = _Win32.TEXT_CHARS

        for i in range(count):
            name = ""
            try:
                item.iItem = i
                view.remote.write_struct(lvitem_addr, item)
                _send(lv, _Win32.LVM_GETITEMTEXTW, i, lvitem_addr)
                raw = view.remote.read(text_addr, _Win32.TEXT_CHARS * 2)
                name = raw.decode("utf-16-le", "ignore").split("\x00", 1)[0]
            except OSError:
                name = ""

            try:
                _send(lv, _Win32.LVM_GETITEMPOSITION, i, point_addr)
                raw = view.remote.read(point_addr, 8)
                x, y = struct.unpack("<ii", raw)
            except OSError:
                x = y = -1

            items.append({"index": i, "name": name, "x": int(x), "y": int(y)})
    finally:
        view.remote.free(base)
    return items


def set_item_position(view: DesktopView, index: int, x: int, y: int) -> None:
    lv = view.hwnd
    if -32768 <= x <= 32767 and -32768 <= y <= 32767:
        packed = (x & 0xFFFF) | ((y & 0xFFFF) << 16)
        _send(lv, _Win32.LVM_SETITEMPOSITION, index, packed)
    else:  # 超大坐标走 32 位版本，需要在目标进程里放一个 POINT
        addr = view.remote.alloc(8)
        try:
            view.remote.write(addr, struct.pack("<ii", x, y))
            _send(lv, _Win32.LVM_SETITEMPOSITION32, index, addr)
        finally:
            view.remote.free(addr)


def refresh_view(hwnd) -> None:
    try:
        _u32.RedrawWindow(
            hwnd, None, None,
            _Win32.RDW_INVALIDATE | _Win32.RDW_UPDATENOW | _Win32.RDW_ALLCHILDREN,
        )
    except Exception:
        pass


def screen_info() -> dict:
    g = _u32.GetSystemMetrics
    return {
        "monitors": g(_Win32.SM_CMONITORS),
        "primary": [g(_Win32.SM_CXSCREEN), g(_Win32.SM_CYSCREEN)],
        "virtual": [
            g(_Win32.SM_XVIRTUALSCREEN),
            g(_Win32.SM_YVIRTUALSCREEN),
            g(_Win32.SM_CXVIRTUALSCREEN),
            g(_Win32.SM_CYVIRTUALSCREEN),
        ],
    }


def screen_text(info: dict) -> str:
    if not info:
        return "-"
    v = info.get("virtual") or [0, 0, 0, 0]
    pr = info.get("primary") or [0, 0]
    return "%d 显示器 / 虚拟桌面 %dx%d / 主屏 %dx%d" % (
        info.get("monitors", 0), v[2], v[3], pr[0], pr[1],
    )


# ---------------------------------------------------------------------------
# 异常：让上层能区分"没漂移"和"压根没检查成功"
# ---------------------------------------------------------------------------
class DesktopViewUnavailable(RuntimeError):
    """拿不到桌面图标视图（explorer 没跑 / 权限不够 / 桌面图标被隐藏）。"""


class DesktopScreenChanged(RuntimeError):
    """屏幕配置和保存时不一样了，继续拿旧坐标纠偏只会反复刷桌面。"""


def lock_error_hint(exc: BaseException) -> str:
    """把锁定失败翻译成能直接照做的一句话。"""
    msg = str(exc).strip() or exc.__class__.__name__
    low = msg.lower()
    if ("错误 5" in msg or "errno 5" in low or "拒绝访问" in msg
            or "access is denied" in low):
        return "%s\n→ 权限不够：右键程序 → 以管理员身份运行，再重新开启锁定。" % msg
    if isinstance(exc, DesktopViewUnavailable) or "explorer" in low:
        return ("%s\n→ 请确认 explorer.exe 在运行、桌面图标没被隐藏"
                "（桌面右键 → 查看 → 显示桌面图标）。" % msg)
    return msg


# ---------------------------------------------------------------------------
# 桌面外观：图标大小 / 排序方式 / 是否显示桌面图标
#
# 这三项都不是 ListView 消息能改的，而是存在资源管理器自己的注册表里：
#   HKCU\Software\Microsoft\Windows\Shell\Bags\1\Desktop\IconSize  (DWORD)
#   HKCU\Software\Microsoft\Windows\Shell\Bags\1\Desktop\Sort      (20 字节)
#   HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced\HideIcons
# 实测（_dev/_probe_iconsize.py）：写进去之后不管怎么刷新都不当场生效，
# 只在视图创建时（登录 / 重启资源管理器）被读取。所以这里只提供"读"与
# "写回"，是否生效由调用方如实报告，绝不谎称已经还原。
# ---------------------------------------------------------------------------
_adv_ready = False
ICON_SIZE_NAMES = ((256, "超大图标"), (96, "大图标"), (48, "中等图标"), (32, "小图标"))


def _ensure_adv_signatures() -> None:
    global _adv_ready
    if _adv_ready or not IS_WINDOWS:
        return
    _adv32.RegOpenKeyExW.restype = wt.LONG
    _adv32.RegOpenKeyExW.argtypes = [wt.HKEY, wt.LPCWSTR, wt.DWORD, wt.DWORD,
                                     ctypes.POINTER(wt.HKEY)]
    _adv32.RegQueryValueExW.restype = wt.LONG
    _adv32.RegQueryValueExW.argtypes = [wt.HKEY, wt.LPCWSTR, ctypes.c_void_p,
                                        ctypes.POINTER(wt.DWORD), ctypes.c_void_p,
                                        ctypes.POINTER(wt.DWORD)]
    _adv32.RegSetValueExW.restype = wt.LONG
    _adv32.RegSetValueExW.argtypes = [wt.HKEY, wt.LPCWSTR, wt.DWORD, wt.DWORD,
                                      ctypes.c_void_p, wt.DWORD]
    _adv32.RegCloseKey.restype = wt.LONG
    _adv32.RegCloseKey.argtypes = [wt.HKEY]
    _adv_ready = True


def _reg_open(key_path: str, mode: int):
    """打开注册表键；打不开返回 None（普通用户也能读 HKCU，写一般也行）。"""
    if not IS_WINDOWS:
        return None
    _ensure_adv_signatures()
    handle = ctypes.c_void_p()
    if _adv32.RegOpenKeyExW(_Win32.HKEY_CURRENT_USER, key_path, 0, mode,
                            ctypes.byref(handle)) != 0:
        return None
    return handle


def reg_read_int(key_path: str, name: str):
    """读一个 DWORD；读不到返回 None。"""
    handle = _reg_open(key_path, _Win32.KEY_READ)
    if handle is None:
        return None
    try:
        value = ctypes.c_uint32()
        size = ctypes.c_uint32(4)
        if _adv32.RegQueryValueExW(handle, name, None, None, ctypes.byref(value),
                                   ctypes.byref(size)) != 0:
            return None
        return int(value.value)
    finally:
        _adv32.RegCloseKey(handle)


def reg_read_bytes(key_path: str, name: str):
    """读一个 REG_BINARY；读不到返回 None。"""
    handle = _reg_open(key_path, _Win32.KEY_READ)
    if handle is None:
        return None
    try:
        size = ctypes.c_uint32(0)
        if _adv32.RegQueryValueExW(handle, name, None, None, None,
                                  ctypes.byref(size)) != 0:
            return None
        if not (0 < size.value <= 65536):
            return None
        buf = ctypes.create_string_buffer(size.value)
        if _adv32.RegQueryValueExW(handle, name, None, None, buf,
                                  ctypes.byref(size)) != 0:
            return None
        return buf.raw[: size.value]
    finally:
        _adv32.RegCloseKey(handle)


def reg_write_int(key_path: str, name: str, value: int) -> bool:
    """写一个 DWORD；返回是否成功。"""
    handle = _reg_open(key_path, _Win32.KEY_WRITE)
    if handle is None:
        return False
    try:
        v = ctypes.c_uint32(int(value))
        return _adv32.RegSetValueExW(handle, name, 0, _Win32.REG_DWORD,
                                     ctypes.byref(v), ctypes.sizeof(v)) == 0
    finally:
        _adv32.RegCloseKey(handle)


def icon_size_name(size) -> str:
    """把 IconSize 的像素值翻译成菜单里那一项；对不上就说像素。"""
    if not size:
        return "未知"
    size = int(size)
    near = min(ICON_SIZE_NAMES, key=lambda kv: abs(kv[0] - size))
    if abs(near[0] - size) <= max(4, size * 0.2):
        return "%s(%d)" % (near[1], size)
    return "%d 像素" % size


def desktop_appearance() -> dict:
    """读一次桌面外观三件套；任何一项读不到就是 None，不猜。"""
    bag = _Win32.SHELL_BAG_KEY
    size = reg_read_int(bag, "IconSize")
    blob = reg_read_bytes(bag, "Sort")
    hidden = reg_read_int(_Win32.EXPLORER_ADV_KEY, "HideIcons")
    return {
        "icon_size": size,
        "icon_size_name": icon_size_name(size),
        "sort": blob.hex() if blob is not None else None,
        "hide_icons": hidden,
    }


def appearance_drift(saved: dict, current: dict) -> list:
    """比对外观三件套，返回可以直接念给用户听的差异清单。

    Sort 那 20 字节的内部含义没有公开定义（本机实测全零，即默认按名称），
    所以只判"不一样了"，不假装能解释成具体哪一种排序。
    """
    out = []
    if not saved or not current:
        return out
    s_size, c_size = saved.get("icon_size"), current.get("icon_size")
    if s_size and c_size and int(s_size) != int(c_size):
        out.append("图标大小：保存时是 %s，现在是 %s。"
                   % (icon_size_name(s_size), icon_size_name(c_size)))
    s_sort, c_sort = saved.get("sort"), current.get("sort")
    if s_sort and c_sort and s_sort != c_sort:
        out.append("排序方式：和保存时不一样了（它会决定图标的先后顺序）。")
    s_hide, c_hide = saved.get("hide_icons"), current.get("hide_icons")
    if s_hide is not None and c_hide is not None and int(s_hide) != int(c_hide):
        out.append("「显示桌面图标」：保存时是%s，现在是%s。"
                   % ("关闭" if s_hide else "开启", "关闭" if c_hide else "开启"))
    return out


# ---------------------------------------------------------------------------
# 屏幕适配：保存和恢复之间换了显示器/分辨率时，坐标得先换算再写
# ---------------------------------------------------------------------------
def _virtual_rect(info: dict):
    v = (info or {}).get("virtual") or []
    if len(v) != 4:
        return None
    try:
        x, y, w, h = (int(i) for i in v)
    except (TypeError, ValueError):
        return None
    return (x, y, w, h) if w > 0 and h > 0 else None


def screen_changed(saved: dict, current: dict) -> str:
    """屏幕配置是否实质变了；变了返回一句人话，没变返回空串。"""
    s, c = _virtual_rect(saved), _virtual_rect(current)
    if s and c and s != c:
        text = "桌面范围从 %dx%d 变成了 %dx%d" % (s[2], s[3], c[2], c[3])
        if (s[0], s[1]) != (c[0], c[1]):
            text += "（原点也从 (%d,%d) 变成了 (%d,%d)）" % (s[0], s[1], c[0], c[1])
        return text
    s_m, c_m = (saved or {}).get("monitors"), (current or {}).get("monitors")
    if s_m and c_m and int(s_m) != int(c_m):
        return "显示器数量从 %d 个变成了 %d 个" % (int(s_m), int(c_m))
    return ""


def plan_screen_fit(items: list, saved_screens: dict, current_screens: dict):
    """把保存的坐标换算到当前屏幕，并保证不会落到屏幕外面。

    返回 (新条目列表, 说明)。屏幕没变时原样返回、说明为空串。
    测不出任一边的矩形时也不动数据（宁可不动，也不乱推）。
    """
    s, c = _virtual_rect(saved_screens), _virtual_rect(current_screens)
    if not s or not c:
        return list(items), ""
    s_m = (saved_screens or {}).get("monitors")
    c_m = (current_screens or {}).get("monitors")
    same_mon = (s_m == c_m) or not s_m or not c_m
    if s == c and same_mon:
        return list(items), ""

    sx = c[2] / float(s[2])
    sy = c[3] / float(s[3])
    right, bottom = c[0] + c[2] - 1, c[1] + c[3] - 1
    out, moved, clamped = [], 0, 0
    for it in items:
        copy = dict(it)
        try:
            x, y = int(it.get("x", 0)), int(it.get("y", 0))
        except (TypeError, ValueError):
            out.append(copy)
            continue
        nx = int(round((x - s[0]) * sx + c[0]))
        ny = int(round((y - s[1]) * sy + c[1]))
        hit_edge = False
        if nx < c[0]:
            nx, hit_edge = c[0], True
        elif nx > right:
            nx, hit_edge = right, True
        if ny < c[1]:
            ny, hit_edge = c[1], True
        elif ny > bottom:
            ny, hit_edge = bottom, True
        if hit_edge:
            clamped += 1
        if (nx, ny) != (x, y):
            moved += 1
        copy["x"], copy["y"] = nx, ny
        out.append(copy)

    parts = []
    if s != c:
        parts.append("保存时桌面 %dx%d，现在 %dx%d，已按 %.0f%%×%.0f%% 缩放置位"
                     % (s[2], s[3], c[2], c[3], sx * 100, sy * 100))
        if moved:
            parts.append("换算后 %d 个图标的位置有调整" % moved)
    if not same_mon:
        parts.append("显示器数量由 %s 变成 %s" % (s_m, c_m))
    if clamped:
        parts.append("其中 %d 个原本会落到屏幕外，已拉回屏幕内" % clamped)
    return out, "；".join(parts)


# ---------------------------------------------------------------------------
# 恢复计划
# ---------------------------------------------------------------------------
def build_restore_plan(saved_items: list, current_items: list, index_fallback: bool = True):
    """把保存条目映射到当前条目。

    返回 (plan, unmatched_saved, leftover_current)
      plan     : [(current_index, x, y, name), ...]
      unmatched: 保存过、但现在找不到对应图标的条目
      leftover : 当前存在、保存时没有的图标（新增的，不动它）
    """
    plan = []
    unmatched_saved = []
    used = set()

    def take(pred):
        for c in current_items:
            if c["index"] in used:
                continue
            if pred(c):
                used.add(c["index"])
                return c
        return None

    for s in saved_items:
        name = (s.get("name") or "").strip()
        c = None
        if name:
            c = take(lambda cur, n=name: cur["name"] == n)
            if c is None:
                low = name.casefold()
                c = take(lambda cur, n=low: cur["name"].casefold() == n)
        if c is None:
            unmatched_saved.append(s)
        else:
            plan.append((c["index"], int(s["x"]), int(s["y"]), c["name"]))

    leftover = [c for c in current_items if c["index"] not in used]
    if index_fallback and unmatched_saved and len(unmatched_saved) == len(leftover):
        for s, c in zip(unmatched_saved, leftover):
            plan.append((c["index"], int(s["x"]), int(s["y"]), c["name"]))
        unmatched_saved = []

    return plan, unmatched_saved, leftover


def positions_by_name(items: list) -> dict:
    out = defaultdict(list)
    for it in items:
        if it.get("name"):
            out[it["name"]].append((int(it["x"]), int(it["y"])))
    return dict(out)


def coordinate_offset(target_items: list, current: dict) -> tuple:
    """估计"当前坐标"和"目标坐标"之间的整体偏移，返回 ((dx, dy), 支持率)。

    为什么需要它：桌面「对齐到网格」不只是吸附手感，它还决定坐标的"记号"——
    同一个格子，开着时读出来是 (0,299)，关掉后同一个位置读出来是 (13,296)。
    也就是说两次读取可能处在两套坐标系里，直接逐项比数字会把"记号不同"
    误判成"62 个图标全被挪走了"，然后疯狂刷桌面。

    判定方式：让每个图标各投一票（当前坐标 - 目标坐标），
    票数集中到同一个偏移 → 认定是坐标系差异；票分散 → 认为偏移为 0，
    交给纠偏逻辑处理。这样不依赖我们猜偏移是多少，也不需要写死任何数字。
    """
    votes = defaultdict(int)
    seen = defaultdict(int)
    total = 0
    for t in target_items:
        name = (t.get("name") or "").strip()
        if not name:
            continue
        bucket = current.get(name)
        if not bucket:
            continue
        k = seen[name]
        if k >= len(bucket):
            continue
        seen[name] += 1
        total += 1
        cx, cy = bucket[k]
        votes[(cx - int(t["x"]), cy - int(t["y"]))] += 1

    if not total:
        return (0, 0), 0.0
    # 票数最高者胜；票数相同时优先选偏移最小的（更保守，宁可判定为"真漂移"）
    (dx, dy), n = max(votes.items(),
                      key=lambda kv: (kv[1], -abs(kv[0][0]) - abs(kv[0][1])))
    share = n / float(total)
    if share < OFFSET_MIN_SHARE or abs(dx) > OFFSET_MAX_PX or abs(dy) > OFFSET_MAX_PX:
        return (0, 0), share
    return (dx, dy), share


def position_drift(target_items: list, current: dict, offset=(0, 0)) -> list:
    """按名称比较位置，返回漂移列表 [(name, 期望, 实际), ...]（同名多图标按顺序配对）。

    offset 是坐标系整体偏移，比较前先减掉，避免把"换了记号"误报成漂移。
    报告出来的期望/实际都换算回"保存时的坐标系"，方便肉眼核对。
    """
    dx, dy = offset
    drift = []
    seen = defaultdict(int)
    for t in target_items:
        name = (t.get("name") or "").strip()
        if not name:
            continue
        bucket = current.get(name)
        if not bucket:
            continue  # 图标不在了：不算漂移，避免无限纠偏
        k = seen[name]
        if k >= len(bucket):
            continue
        cx, cy = bucket[k]
        seen[name] += 1
        want = (int(t["x"]), int(t["y"]))
        got = (cx - dx, cy - dy)
        if got != want:
            drift.append((name, want, got))
    return drift


# ---------------------------------------------------------------------------
# 后端：快照 / 恢复 / 锁定
# ---------------------------------------------------------------------------
class DesktopBackend:
    """桌面布局读写。"""

    # ---- 视图句柄管理 ----
    def open_views(self) -> list:
        views = []
        remotes = {}
        for hwnd in find_desktop_listviews():
            pid = window_pid(hwnd)
            if pid <= 0 or not _looks_like_shell(pid):
                continue
            r = remotes.get(pid)
            if r is None:
                r = RemoteProcess(pid)
                remotes[pid] = r
            views.append(DesktopView(hwnd, pid, r))
        if not views:
            for r in remotes.values():
                r.close()
        return views

    @staticmethod
    def close_views(views: list) -> None:
        done = set()
        for v in views:
            if id(v.remote) in done:
                continue
            done.add(id(v.remote))
            try:
                v.remote.close()
            except Exception:
                pass

    # ---- 查询 ----
    def describe(self) -> dict:
        views = self.open_views()
        try:
            total = 0
            auto = None
            snap = None
            detail = []
            for v in views:
                n = _send(v.hwnd, _Win32.LVM_GETITEMCOUNT)
                total += n
                aa = auto_arrange_enabled(v.hwnd)
                sg = snap_to_grid_enabled(v.hwnd)
                auto = aa if auto is None else (auto or aa)
                snap = sg if snap is None else (snap or sg)
                detail.append({"hwnd": int(v.hwnd), "pid": v.pid, "icons": n,
                               "auto_arrange": aa, "snap_to_grid": sg})
            return {
                "views": len(views),
                "icons": total,
                "auto_arrange": auto,
                "snap_to_grid": snap,
                "detail": detail,
                "screens": screen_info(),
                "appearance": desktop_appearance(),
            }
        finally:
            self.close_views(views)

    def snapshot(self) -> dict:
        views = self.open_views()
        if not views:
            raise RuntimeError(
                "找不到桌面图标视图。请确认 explorer.exe 正在运行，且桌面图标已显示"
                "（桌面右键 → 查看 → 显示桌面图标）。"
            )
        try:
            out_views = []
            for idx, v in enumerate(views):
                try:
                    auto = auto_arrange_enabled(v.hwnd)
                    snap = snap_to_grid_enabled(v.hwnd)
                except OSError:
                    auto = snap = False
                try:
                    spacing = _send(v.hwnd, _Win32.LVM_GETITEMSPACING, 1)
                    gap = [spacing & 0xFFFF, (spacing >> 16) & 0xFFFF]
                    if not (10 <= gap[0] <= 600 and 10 <= gap[1] <= 600):
                        raise ValueError
                except Exception:
                    gap = [0, 0]
                # 按当前模式原样读取：坐标的语义单位跟着「对齐到网格」走，
                # 恢复时必须用同一个模式写回，所以这里把状态一起记进快照。
                items = read_view_items(v)
                out_views.append({
                    "key": "desktop%d" % idx,
                    "auto_arrange": auto,
                    "snap_to_grid": snap,
                    "grid": gap,
                    "items": items,
                })
            return {
                "views": out_views,
                "screens": screen_info(),
                # 图标大小 / 排序方式 / 是否显示桌面图标：都不在 ListView 里，
                # 而是资源管理器自己的注册表，恢复时只做比对与如实报告。
                "appearance": desktop_appearance(),
                "captured_at": now_str(),
            }
        finally:
            self.close_views(views)

    def apply_grid_snap(self) -> int:
        """把所有图标吸附到网格（不排序），返回影响的图标数。"""
        views = self.open_views()
        if not views:
            raise RuntimeError("找不到桌面图标视图。")
        try:
            n = 0
            for v in views:
                try:
                    was_snap = snap_to_grid_enabled(v.hwnd)
                except OSError:
                    was_snap = True
                if auto_arrange_enabled(v.hwnd):
                    set_auto_arrange(v.hwnd, False)
                if not was_snap:
                    set_snap_to_grid(v.hwnd, True)
                n += _send(v.hwnd, _Win32.LVM_GETITEMCOUNT)
                _send(v.hwnd, _Win32.LVM_ARRANGE, _Win32.LVA_SNAPTOGRID, 0)
                if not was_snap:
                    set_snap_to_grid(v.hwnd, False)
                refresh_view(v.hwnd)
            return n
        finally:
            self.close_views(views)

    # ---- 恢复 ----
    def restore(self, profile: dict, disable_auto_arrange: bool = True,
                index_fallback: bool = True) -> dict:
        layout = profile.get("layout") or {}
        saved_views = layout.get("views") or []
        views = self.open_views()
        if not views:
            raise DesktopViewUnavailable(
                "找不到桌面图标视图。请确认 explorer.exe 正在运行，且桌面图标已显示"
                "（桌面右键 → 查看 → 显示桌面图标）。"
            )

        stats = {
            "restored": 0, "verified": 0, "mismatch": [], "missing": [],
            "extra": 0, "views": 0, "auto_fixed": 0, "snap_mode": [],
            "snapped": 0, "adapted": 0, "screen_fit": [], "appearance": [],
            "appearance_restored": [],
        }
        current_screens = screen_info()
        try:
            if len(saved_views) == 1 and len(views) >= 1:
                pairs = [(views[0], saved_views[0])]
                if len(views) > 1:
                    pairs += [(v, saved_views[0]) for v in views[1:]]
                    stats["note"] = "视图数量不一致，已用同一份布局兜底到 %d 个视图" % len(views)
            else:
                pairs = [(v, saved_views[i]) for i, v in enumerate(views) if i < len(saved_views)]
                if len(saved_views) > len(views):
                    stats["note"] = "保存时 %d 个视图，现在只有 %d 个" % (len(saved_views), len(views))

            for view, sv in pairs:
                if disable_auto_arrange and set_auto_arrange(view.hwnd, False):
                    stats["auto_fixed"] += 1

                # 关键一步：把「对齐到网格」拨回保存这套布局时的状态。
                # 这开关决定坐标的单位（格点坐标 / 像素坐标），
                # 读的时候一个模式、写的时候另一个模式，整屏就会偏半格。
                want_snap = bool(sv.get("snap_to_grid", True))
                try:
                    now_snap = snap_to_grid_enabled(view.hwnd)
                except OSError:
                    now_snap = want_snap
                if now_snap != want_snap:
                    set_snap_to_grid(view.hwnd, want_snap)
                stats["snap_mode"].append("对齐到网格：%s（沿用保存时）"
                                          % ("开" if want_snap else "关"))

                current = read_view_items(view)
                target = sv.get("items") or []
                # 屏幕换了（改分辨率 / 接投影 / 拔掉外接屏）就先把保存的坐标按比例
                # 换算到当前屏幕并夹进边界内，否则照抄旧数值会把图标写到屏幕外面去。
                target, fit_note = plan_screen_fit(
                    target, layout.get("screens") or {}, current_screens)
                fit_applied = bool(fit_note)
                if fit_note and fit_note not in stats["screen_fit"]:
                    stats["screen_fit"].append(fit_note)
                # 写之前先对齐坐标系：当前读数和"保存时"的记号未必一样
                # （例如中途动过「对齐到网格」），直接照抄数值会整屏串位。
                pre_off, _share = coordinate_offset(target, positions_by_name(current))
                plan, unmatched, leftover = build_restore_plan(target, current, index_fallback)
                for (index, x, y, _name) in plan:
                    set_item_position(view, index, x + pre_off[0], y + pre_off[1])
                refresh_view(view.hwnd)

                # 复核：把写进去的位置再读回来。写操作本身可能让桌面切换坐标系记号，
                # 所以这里重新算一次偏移，减掉之后必须逐项吻合。
                after_items = read_view_items(view)
                after = positions_by_name(after_items)
                post_off, share = coordinate_offset(target, after)
                # 「对齐到网格」开着时，写进去的坐标会被系统吸附到最近的格点，
                # 差半个格子以内算"吸附落位"（本机实测同一位置两种记号相差 (13,-3)），
                # 超过半个格子才算真偏差 —— 否则会把"格子允许范围内的取整"报成失败。
                tol_x = tol_y = 0
                if want_snap:
                    gap = sv.get("grid") or [0, 0]
                    tol_x, tol_y = int(gap[0]) // 2, int(gap[1]) // 2

                seen = defaultdict(int)
                for (index, x, y, name) in plan:
                    bucket = after.get(name) or []
                    k = seen[name]
                    seen[name] += 1
                    actual = bucket[k] if k < len(bucket) else None
                    want = (x + post_off[0], y + post_off[1])
                    if actual == want:
                        stats["verified"] += 1
                    elif fit_applied:
                        # 屏幕变了：坐标是按比例换算过的，写进去之后还会被
                        # 「对齐到网格」吸附到最近的格点，逐项复核没有意义
                        #（只保证落在屏幕内）。如实算作"已适配"，不当成偏差。
                        stats["adapted"] += 1
                    elif (actual is not None and tol_x and tol_y
                          and abs(actual[0] - want[0]) <= tol_x
                          and abs(actual[1] - want[1]) <= tol_y):
                        stats["snapped"] += 1
                    else:
                        stats["mismatch"].append((name, (x, y), actual))

                if post_off != (0, 0):
                    stats["coord"] = list(post_off)
                    stats["coord_share"] = round(share, 3)

                stats["restored"] += len(plan)
                stats["extra"] += len(leftover)
                stats["missing"].extend((s.get("name") or "(无名)") for s in unmatched)
                stats["views"] += 1

            # 外观三件套（图标大小 / 排序方式 / 是否显示桌面图标）不是位置，
            # 改不了当场生效（实测：写注册表 + SHChangeNotify + WM_SETTINGCHANGE +
            # 重画视图 + 切 HideIcons 都不重画），所以只如实报告差异，
            # 并把图标大小写回注册表等下次登录或重启资源管理器时生效。
            saved_app = layout.get("appearance") or {}
            if saved_app:
                now_app = desktop_appearance()
                stats["appearance"] = appearance_drift(saved_app, now_app)
                want_size = saved_app.get("icon_size")
                if want_size and now_app.get("icon_size") != want_size:
                    if reg_write_int(_Win32.SHELL_BAG_KEY, "IconSize", int(want_size)):
                        stats["appearance_restored"].append(
                            "桌面图标大小已写回注册表：%s。这一项要下次登录或重启"
                            "资源管理器才会看到效果。" % icon_size_name(want_size))
            return stats
        finally:
            self.close_views(views)

    # ---- 锁定纠偏 ----
    def enforce(self, profile: dict, disable_auto_arrange: bool = True,
                index_fallback: bool = True) -> list:
        """检查一次是否漂移，漂移就整体还原。返回漂移明细。"""
        layout = profile.get("layout") or {}
        saved_views = layout.get("views") or []
        target = []
        for sv in saved_views:
            target.extend(sv.get("items") or [])
        if not target:
            return []

        # 屏幕配置变了就**不许**再拿旧坐标纠偏：换算过的位置和保存值天然对不上，
        # 会变成每轮都"发现漂移 → 还原 → 再发现"，把桌面按在刷新循环里出不来。
        changed = screen_changed(layout.get("screens") or {}, screen_info())
        if changed:
            raise DesktopScreenChanged(
                "%s，所以锁定期间不会用旧坐标纠偏（否则会反复刷桌面）。"
                "请点一次「恢复选中布局」重新适配屏幕，再开锁定。" % changed)

        views = self.open_views()
        if not views:
            raise DesktopViewUnavailable(
                "拿不到桌面图标视图：explorer.exe 可能没在运行、桌面图标被隐藏"
                "（桌面右键 → 查看 → 显示桌面图标），或者权限不足（错误 5）。"
            )
        try:
            # 比对前先把每个视图的「对齐到网格」拨回保存时的状态。
            # 否则坐标单位不同，会把"整体偏半格"误判成漂移，然后反复刷桌面。
            for i, v in enumerate(views):
                sv = saved_views[i] if i < len(saved_views) else saved_views[0]
                want = bool(sv.get("snap_to_grid", True))
                try:
                    if snap_to_grid_enabled(v.hwnd) != want:
                        set_snap_to_grid(v.hwnd, want)
                except OSError:
                    pass

            current = defaultdict(list)
            for v in views:
                for it in read_view_items(v):
                    if it["name"]:
                        current[it["name"]].append((it["x"], it["y"]))
            current = dict(current)

            # 先自校准坐标系，再比位置，否则"记号不同"会变成满屏假漂移
            off, _share = coordinate_offset(target, current)
            drift = position_drift(target, current, off)
            if drift:
                self.close_views(views)
                views = []
                self.restore(profile, disable_auto_arrange, index_fallback)
            return drift
        finally:
            self.close_views(views)


# ---------------------------------------------------------------------------
# 锁定守护线程
# ---------------------------------------------------------------------------
class LockWorker(threading.Thread):
    """轮询桌面，发现图标被挪动就拉回锁定时的位置。

    一条硬规矩：**不许悄悄什么都不做**。
    早先的实现在拿不到桌面视图时直接返回“无漂移”，界面照样显示「锁定：开」，
    用户根本看不出锁定其实一次都没生效。现在改成：
      * 检查失败会累计；连续 FAIL_LIMIT 次就带着原因走 on_fatal 退出，
        界面把按钮翻回「关」并弹一次提醒；
      * 屏幕配置变了不硬撑，报一次「已暂停」（不算失败），
        屏幕换回来会自动恢复检查；
      * 正常运行时每 HEARTBEAT 秒报一次心跳，用户能看出它还活着。
    """

    FAIL_LIMIT = 3
    HEARTBEAT = 60.0

    def __init__(self, backend: DesktopBackend, profile: dict, interval: float,
                 on_event, disable_auto_arrange: bool = True,
                 index_fallback: bool = True, on_fatal=None, on_stop=None):
        super().__init__(daemon=True, name="DesktopLockWorker")
        self.backend = backend
        self.profile = profile
        self.interval = max(0.5, float(interval))
        self.on_event = on_event
        self.disable_auto_arrange = disable_auto_arrange
        self.index_fallback = index_fallback
        self.on_fatal = on_fatal
        self.on_stop = on_stop
        self._stop = threading.Event()
        self.locked = 0            # 拉回过的次数
        self.checks = 0            # 成功检查过的次数
        self.failures = 0          # 连续失败次数
        self.suspended = False     # 是否因为屏幕变化而暂停
        self.last_error = ""
        self._last_beat = time.monotonic()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        fatal = ""
        self.on_event("锁定已启用：每 %.1f 秒检查一次桌面图标位置。" % self.interval)
        while not self._stop.wait(self.interval):
            try:
                drift = self.backend.enforce(
                    self.profile, self.disable_auto_arrange,
                    self.index_fallback,
                )
            except DesktopScreenChanged as e:
                # 这不是故障：屏幕变了就不能用旧坐标纠偏。
                # 不累计失败次数，等屏幕换回来自动继续。
                self.failures = 0
                if not self.suspended:
                    self.suspended = True
                    self.last_error = str(e)
                    self.on_event("锁定已暂停：%s" % e)
                continue
            except Exception as e:
                self.suspended = False
                self.failures += 1
                msg = lock_error_hint(e)
                if msg != self.last_error:
                    self.last_error = msg
                    self.on_event("锁定检查出错（连续第 %d 次）：%s" % (self.failures, msg))
                if self.failures >= self.FAIL_LIMIT:
                    fatal = ("连续 %d 次检查都没能执行，锁定守护已退出。"
                             "最后一次的错误是：\n%s" % (self.failures, msg))
                    break
                continue

            self.failures = 0
            self.checks += 1
            if self.suspended:
                self.suspended = False
                self.on_event("锁定已恢复检查：屏幕配置回到保存时的状态。")
            if drift:
                self.locked += 1
                sample = "，".join("%s %s→%s" % (n, a, b) for n, a, b in drift[:3])
                more = "" if len(drift) <= 3 else " 等 %d 处" % len(drift)
                self.on_event("检测到 %d 处图标被移动，已还原：%s%s"
                              % (len(drift), sample, more))
            now = time.monotonic()
            if now - self._last_beat >= self.HEARTBEAT:
                self._last_beat = now
                self.on_event("锁定运行中：已检查 %d 次，共拉回 %d 次，当前正常。"
                              % (self.checks, self.locked))

        self.on_event("锁定已停止（%s）。" % ("异常退出" if fatal else "正常结束"))
        if fatal and self.on_fatal:
            self.on_fatal(fatal)
        if self.on_stop:
            self.on_stop()


# ---------------------------------------------------------------------------
# 布局仓库
# ---------------------------------------------------------------------------
class ProfileStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = {"version": 1, "config": dict(DEFAULT_CONFIG), "profiles": []}
        self.load()

    # ---- IO ----
    def load(self) -> None:
        with self.lock:
            if not self.path.exists():
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("根结构不是对象")
                self.data["version"] = raw.get("version", 1)
                cfg = dict(DEFAULT_CONFIG)
                cfg.update(raw.get("config") or {})
                self.data["config"] = cfg
                profs = raw.get("profiles")
                self.data["profiles"] = profs if isinstance(profs, list) else []
            except Exception:
                try:
                    self.path.replace(self.path.with_suffix(".corrupt.json"))
                except Exception:
                    pass

    def save(self) -> None:
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.path)

    # ---- 配置 ----
    @property
    def config(self) -> dict:
        return self.data["config"]

    def set_config(self, key: str, value) -> None:
        with self.lock:
            self.data["config"][key] = value
        self.save()

    # ---- 布局 ----
    @property
    def profiles(self) -> list:
        return self.data["profiles"]

    def get(self, pid: str):
        for p in self.profiles:
            if p.get("id") == pid:
                return p
        return None

    def find_by_name(self, name: str):
        for p in self.profiles:
            if p.get("name") == name:
                return p
        return None

    def add(self, name: str, layout: dict, auto_backup: bool = False) -> dict:
        prof = {
            "id": uuid.uuid4().hex,
            "name": name,
            "created": now_str(),
            "auto_backup": bool(auto_backup),
            "screens": layout.get("screens") or {},
            "icon_count": sum(len(v.get("items") or []) for v in (layout.get("views") or [])),
            "view_count": len(layout.get("views") or []),
            "layout": layout,
        }
        with self.lock:
            self.profiles.insert(0, prof)
        self.save()
        return prof

    def rename(self, pid: str, name: str) -> bool:
        p = self.get(pid)
        if not p:
            return False
        p["name"] = name
        self.save()
        return True

    def delete(self, pid: str) -> bool:
        with self.lock:
            before = len(self.profiles)
            self.data["profiles"] = [p for p in self.profiles if p.get("id") != pid]
            changed = len(self.data["profiles"]) != before
        if changed:
            self.save()
        return changed

    def prune_auto_backups(self, keep: int) -> int:
        keep = max(1, int(keep))
        autos = [p for p in self.profiles if p.get("auto_backup")]
        if len(autos) <= keep:
            return 0
        drop = {p["id"] for p in autos[keep:]}
        with self.lock:
            self.data["profiles"] = [p for p in self.profiles if p.get("id") not in drop]
        self.save()
        return len(drop)

    def export_to(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    def import_from(self, path: Path) -> int:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        incoming = raw.get("profiles") if isinstance(raw, dict) else raw
        if not isinstance(incoming, list):
            raise ValueError("不是本程序导出的布局文件")
        existing = {p.get("name") for p in self.profiles}
        added = 0
        with self.lock:
            for p in incoming:
                if not isinstance(p, dict) or "layout" not in p:
                    continue
                name = p.get("name") or "导入布局"
                while name in existing:
                    name += " (导入)"
                existing.add(name)
                p = dict(p)
                p["id"] = uuid.uuid4().hex
                p["name"] = name
                p["created"] = p.get("created") or now_str()
                self.data["profiles"].insert(0, p)
                added += 1
        self.save()
        return added


# ---------------------------------------------------------------------------
# 程序入口
# ---------------------------------------------------------------------------
def main() -> int:
    if not IS_WINDOWS:
        cli_write("本工具仅支持 Windows。\n")
        return 2

    argv = sys.argv[1:]

    # ---- 命令行模式（便于脚本化 / 排障）----
    # 打包成无控制台 exe 之后 print 没地方去，所以统一走 cli_write：
    # 从 cmd/PowerShell 调用 → 挂到父控制台输出；双击调用 → 落到 数据目录\cli-output.txt
    if argv and argv[0] in ("--cli-snapshot", "-s"):
        backend = DesktopBackend()
        cli_write(json.dumps(backend.snapshot(), ensure_ascii=False, indent=2) + "\n")
        return 0
    if argv and argv[0] in ("--cli-restore", "-r") and len(argv) >= 2:
        store = ProfileStore(STORE_FILE)
        prof = store.find_by_name(argv[1]) or store.get(argv[1])
        if not prof:
            cli_write("找不到布局：%s\n" % argv[1])
            return 1
        stats = DesktopBackend().restore(prof)
        cli_write(json.dumps(stats, ensure_ascii=False, indent=2) + "\n")
        return 0
    if argv and argv[0] in ("--cli-save", "-S") and len(argv) >= 2:
        # 不带 GUI 也能存一套布局（方便做计划任务 / 开机自动快照）
        name = argv[1]
        store = ProfileStore(STORE_FILE)
        existing = store.find_by_name(name)
        if existing and "--overwrite" not in argv[2:]:
            cli_write("已存在同名布局：%s（加 --overwrite 覆盖）\n" % existing["name"])
            return 1
        if existing:
            store.delete(existing["id"])
        prof = store.add(name, DesktopBackend().snapshot())
        cli_write(json.dumps({"saved": prof["name"], "icons": prof["icon_count"],
                              "views": prof["view_count"], "总计": len(store.profiles)},
                             ensure_ascii=False, indent=2) + "\n")
        return 0
    if argv and argv[0] in ("--version", "-V"):
        cli_write("%s %s（内核 %s，%s）\n" % (
            APP_TITLE, version_text(), APP_VERSION,
            "打包版" if is_frozen() else "源码运行"))
        return 0
    if argv and argv[0] in ("--help", "-h"):
        cli_write(__doc__)
        return 0

    choose_dpi_awareness()

    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog, simpledialog

    backend = DesktopBackend()
    store = ProfileStore(STORE_FILE)

    class App(tk.Tk):
        REFRESH_MS = 2500

        def __init__(self):
            super().__init__()
            self.backend = backend
            self.store = store
            self.lock_worker = None
            self._lock_pid = None

            self.title("%s  %s" % (APP_TITLE, version_text()))
            self.geometry("1060x900")
            self.minsize(980, 700)
            self._init_style()
            self._build_ui()
            self.refresh_profiles()
            self.refresh_status()
            self.refresh_admin_banner()
            self._tick()
            self.protocol("WM_DELETE_WINDOW", self.on_close)
            self.log("数据目录：%s" % data_dir())
            self.log(self.version_line())
            self.after(400, self._startup_admin_check)

        def version_line(self) -> str:
            """日志里那行版本信息：打包后把 exe 的路径和编译版本一起写出来，
            方便一眼确认"我现在跑的是哪一版、文件在哪"。"""
            if is_frozen():
                prod, fv = read_exe_version()
                exe = sys.executable
                try:
                    stamp = datetime.fromtimestamp(
                        os.path.getmtime(exe)).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    stamp = "?"
                return "版本：%s（exe 编译于 %s）｜文件：%s｜内核 %s" % (
                    prod or "v?", stamp, exe, APP_VERSION)
            return ("版本：v-dev（直接运行源码，不是打包版）"
                    "｜内核 %s｜文件：%s" % (APP_VERSION, os.path.abspath(sys.argv[0])))

        # ---------------- 样式 ----------------
        def _init_style(self):
            try:
                self.tk.call("tk", "scaling", self.winfo_fpixels("1i") / 72.0)
            except Exception:
                pass
            style = ttk.Style(self)
            for theme in ("vista", "winnative", "clam"):
                try:
                    style.theme_use(theme)
                    break
                except tk.TclError:
                    continue
            style.configure("Treeview", rowheight=26, font=("Microsoft YaHei UI", 10))
            style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"))
            style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(10, 6))
            style.configure("Big.TButton", font=("Microsoft YaHei UI", 11, "bold"), padding=(12, 9))
            style.configure("TCheckbutton", font=("Microsoft YaHei UI", 9))
            style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))

        # ---------------- 界面 ----------------
        def _build_ui(self):
            # 顶部：管理员横幅
            self.banner = tk.Frame(self)
            self.banner.pack(fill="x", side="top")
            self.lbl_admin = tk.Label(
                self.banner, anchor="w", justify="left", wraplength=780,
                font=("Microsoft YaHei UI", 11, "bold"), padx=16, pady=10, text="",
            )
            self.lbl_admin.pack(side="left", fill="x", expand=True)
            self.btn_admin = ttk.Button(self.banner, text="以管理员身份重启",
                                        command=self.on_relaunch_admin)
            self.btn_admin.pack(side="right", padx=12, pady=8)
            # 横幅文字跟着窗口宽度换行；留 220px 给右边的按钮，
            # 免得"普通用户模式"那段长提醒把按钮挤出可视区
            self.banner.bind(
                "<Configure>",
                lambda e: self.lbl_admin.configure(wraplength=max(240, e.width - 220)))

            # 底部固定区按"从下往上"的顺序先摆好：状态栏 → 日志 → 选项。
            # 这样无论窗口被拉多小，它们都不会被中部那块可伸缩的区域挤掉。
            self.lbl_status = ttk.Label(self, text="桌面状态：读取中…", anchor="w",
                                        relief="sunken", padding=(8, 4))
            self.lbl_status.pack(fill="x", side="bottom")

            # 日志
            log_box = ttk.Labelframe(self, text=" 运行日志 ")
            log_box.pack(fill="both", expand=True, side="bottom", padx=10, pady=(0, 4))
            self.txt_log = tk.Text(log_box, height=7, wrap="word",
                                   font=("Consolas", 9), background="#fbfbfb", relief="flat")
            log_sb = ttk.Scrollbar(log_box, orient="vertical", command=self.txt_log.yview)
            self.txt_log.configure(yscrollcommand=log_sb.set, state="disabled")
            self.txt_log.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
            log_sb.pack(side="right", fill="y", pady=6)

            # 中部：左右分栏（pack 放到最后，见 _build_ui 末尾）
            mid = ttk.Frame(self)

            left = ttk.Labelframe(mid, text=" 已保存的布局（双击 = 恢复） ")
            left.pack(side="left", fill="both", expand=True)

            cols = ("name", "icons", "views", "screens", "created")
            self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
            headers = {
                "name": ("布局名称", 230),
                "icons": ("图标数", 70),
                "views": ("视图", 55),
                "screens": ("显示器", 90),
                "created": ("保存时间", 150),
            }
            for c in cols:
                text, width = headers[c]
                self.tree.heading(c, text=text)
                self.tree.column(c, width=width,
                                 anchor="w" if c in ("name", "created", "screens") else "center")
            vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
            self.tree.configure(yscrollcommand=vsb.set)
            self.tree.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
            vsb.pack(side="right", fill="y", pady=6)
            self.tree.bind("<Double-1>", lambda e: self.on_restore())
            self.tree.bind("<<TreeviewSelect>>", lambda e: self._sync_lock_button())

            right = ttk.Frame(mid)
            right.pack(side="right", fill="y", padx=(10, 0))

            def add_btn(parent, text, cmd, style="TButton"):
                b = ttk.Button(parent, text=text, command=cmd, style=style)
                b.pack(fill="x", pady=2)
                return b

            ttk.Label(right, text="操作", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
            self.btn_save = add_btn(right, "保存当前桌面布局", self.on_save, "Big.TButton")
            self.btn_restore = add_btn(right, "恢复选中布局", self.on_restore, "Big.TButton")

            # 锁定是主操作，放在前排：窗口被拉矮时也不会先被挤掉
            self.btn_lock = ttk.Button(right, text="锁定选中布局",
                                       command=self.on_toggle_lock, style="Big.TButton")
            self.btn_lock.pack(fill="x", pady=2)
            self.lbl_lock = ttk.Label(right, text="锁定：关闭", foreground="#666")
            self.lbl_lock.pack(anchor="w", pady=(0, 2))

            ttk.Separator(right).pack(fill="x", pady=4)
            add_btn(right, "重命名选中布局", self.on_rename)
            add_btn(right, "删除选中布局", self.on_delete)
            ttk.Separator(right).pack(fill="x", pady=4)
            add_btn(right, "把图标对齐到网格", self.on_grid_snap)
            add_btn(right, "立即刷新桌面状态", self.refresh_status)
            ttk.Separator(right).pack(fill="x", pady=4)
            add_btn(right, "导入布局…", self.on_import)
            add_btn(right, "导出全部布局…", self.on_export)
            add_btn(right, "打开数据目录", self.on_open_dir)

            # 选项区
            opt = ttk.Labelframe(self, text=" 选项 ")

            self.var_auto_backup = tk.BooleanVar(value=True)
            self.var_disable_aa = tk.BooleanVar(
                value=bool(store.config.get("disable_auto_arrange", True)))
            self.var_index_fb = tk.BooleanVar(
                value=bool(store.config.get("index_fallback", True)))
            self.var_prefer_admin = tk.BooleanVar(
                value=bool(store.config.get("prefer_admin", False)))
            self.var_interval = tk.DoubleVar(
                value=float(store.config.get("lock_interval", 2.0)))

            row1 = ttk.Frame(opt)
            row1.pack(fill="x", padx=8, pady=(6, 2))
            ttk.Checkbutton(row1, text="恢复前自动备份当前布局",
                            variable=self.var_auto_backup).pack(side="left", padx=(0, 16))
            ttk.Checkbutton(row1, text="恢复时关闭「自动排列图标」",
                            variable=self.var_disable_aa,
                            command=self._on_opt_change).pack(side="left", padx=(0, 16))

            row2 = ttk.Frame(opt)
            row2.pack(fill="x", padx=8, pady=(2, 2))
            ttk.Checkbutton(row2, text="名称对不上时按顺序兜底",
                            variable=self.var_index_fb,
                            command=self._on_opt_change).pack(side="left", padx=(0, 16))
            ttk.Checkbutton(row2, text="总是以管理员身份运行（下次启动生效）",
                            variable=self.var_prefer_admin,
                            command=self._on_opt_change).pack(side="left", padx=(0, 16))
            ttk.Label(row2, text="锁定检查间隔（秒）：").pack(side="left")
            sp = ttk.Spinbox(row2, from_=0.5, to=30, increment=0.5, width=6,
                             textvariable=self.var_interval, command=self._on_opt_change)
            sp.pack(side="left")
            sp.bind("<FocusOut>", lambda e: self._on_opt_change())

            tip = ttk.Label(
                opt, foreground="#666", font=("Microsoft YaHei UI", 8),
                justify="left", anchor="w", wraplength=980,
                text="关于「对齐到网格」：它决定坐标的单位（开着读出来是格点坐标，"
                     "关掉是像素坐标）。本工具在保存时记下当时的开关，恢复时先拨回同一个"
                     "状态再写坐标，因此能逐个精确还原，也不会改乱你桌面右键里的设置。"
                     "想手动整理，用上面的「把图标对齐到网格」。",
            )
            # 提示文字跟着窗口宽度换行，避免被右边裁掉
            opt.bind("<Configure>",
                     lambda e, lbl=tip: lbl.configure(wraplength=max(240, e.width - 24)))
            tip.pack(fill="x", padx=10, pady=(0, 8))
            opt.pack(fill="x", side="bottom", padx=10, pady=4)

            # 中部左右分栏最后 pack：吃掉剩下的全部空间
            mid.pack(fill="both", expand=True, side="top", padx=10, pady=(10, 4))

        # ---------------- 日志 / 状态 ----------------
        def log(self, msg: str):
            ts = datetime.now().strftime("%H:%M:%S")
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", "[%s] %s\n" % (ts, msg))
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")

        def log_threadsafe(self, msg: str):
            self.after(0, lambda m=msg: self.log(m))

        def refresh_admin_banner(self):
            admin = is_admin()
            if admin:
                self.lbl_admin.configure(
                    text="● 管理员模式：当前以【管理员】身份运行，桌面布局读写与锁定全部可用。",
                    background="#1f7a3d", foreground="#ffffff",
                )
                self.btn_admin.configure(text="已提权", state="disabled")
            else:
                self.lbl_admin.configure(
                    text="● 普通用户模式：当前【不是】管理员运行。"
                         "部分系统/安全策略下读写桌面布局会被拒绝（错误 5），"
                         "或无法操作以管理员身份启动的 explorer。建议点击右侧按钮提权。",
                    background="#b26a00", foreground="#ffffff",
                )
                self.btn_admin.configure(text="以管理员身份重启", state="normal")
            self.var_prefer_admin.set(bool(self.store.config.get("prefer_admin", False)))
            self.log("权限检测：%s" % ("管理员" if admin else "普通用户"))

        def refresh_status(self):
            try:
                info = self.backend.describe()
            except Exception as e:
                self.lbl_status.configure(text="桌面状态：读取失败（%s）" % e)
                return
            aa = info.get("auto_arrange")
            sg = info.get("snap_to_grid")
            app = info.get("appearance") or {}
            self.lbl_status.configure(
                text=("桌面视图 %d 个  |  图标 %d 个  |  图标大小：%s  |  "
                      "自动排列：%s  |  对齐到网格：%s  |  %s")
                     % (info["views"], info["icons"],
                        app.get("icon_size_name") or "未知",
                        "未知" if aa is None else ("开" if aa else "关"),
                        "未知" if sg is None else ("开" if sg else "关"),
                        screen_text(info.get("screens") or {}))
            )

        def _tick(self):
            try:
                self.refresh_status()
                self._refresh_lock_life()
            except Exception:
                pass
            self.after(self.REFRESH_MS, self._tick)

        def _refresh_lock_life(self):
            """锁定中把「已检查 N 次 / 拉回 M 次」摆到标签上。

            没有这个，用户根本判断不出锁定到底在干活还是已经死了
            （以前就是这样：界面显示「开启」，实际一次都没检查成功）。
            """
            w = self.lock_worker
            if w is None or not w.is_alive():
                return
            prof = self.store.get(self._lock_pid)
            nm = prof.get("name") if prof else "?"
            if getattr(w, "suspended", False):
                self.lbl_lock.configure(
                    text="锁定：已暂停（%s）· 屏幕配置变了" % nm,
                    foreground="#b26a00")
            else:
                self.lbl_lock.configure(
                    text="锁定：开启（%s）· 已检查 %d 次 / 拉回 %d 次"
                         % (nm, w.checks, w.locked),
                    foreground="#1f7a3d")

        # ---------------- 列表 ----------------
        def refresh_profiles(self, select_new: bool = False):
            self.tree.delete(*self.tree.get_children())
            for p in self.store.profiles:
                name = p.get("name", "?")
                if p.get("auto_backup"):
                    name = "[自动] " + name
                self.tree.insert(
                    "", "end", iid=p["id"],
                    values=(name, p.get("icon_count", 0), p.get("view_count", 0),
                            (p.get("screens") or {}).get("monitors", "-"),
                            p.get("created", "")),
                )
            kids = self.tree.get_children()
            if kids:
                self.tree.selection_set(kids[0])
                self.tree.focus(kids[0])
            self._sync_lock_button()

        def _selected_profile(self):
            sel = self.tree.selection()
            return self.store.get(sel[0]) if sel else None

        def _default_name(self) -> str:
            base = datetime.now().strftime("布局 %m-%d %H:%M")
            if not self.store.find_by_name(base):
                return base
            i = 2
            while self.store.find_by_name("%s (%d)" % (base, i)):
                i += 1
            return "%s (%d)" % (base, i)

        # ---------------- 选项 ----------------
        def _on_opt_change(self):
            try:
                interval = float(self.var_interval.get())
            except Exception:
                interval = 2.0
            self.store.config["disable_auto_arrange"] = bool(self.var_disable_aa.get())
            self.store.config["index_fallback"] = bool(self.var_index_fb.get())
            self.store.config["prefer_admin"] = bool(self.var_prefer_admin.get())
            self.store.config["lock_interval"] = interval
            self.store.save()
            if self.lock_worker and self.lock_worker.is_alive():
                self.lock_worker.interval = max(0.5, interval)

        # ---------------- 动作 ----------------
        def on_save(self):
            try:
                layout = self.backend.snapshot()
            except Exception as e:
                messagebox.showerror("读取桌面失败", str(e), parent=self)
                return
            total = sum(len(v.get("items") or []) for v in layout["views"])
            if total == 0:
                messagebox.showwarning(
                    "没有读到图标",
                    "没有从桌面读到任何图标。\n\n请检查：\n"
                    "1) 桌面图标是否已显示（桌面右键 → 查看 → 显示桌面图标）\n"
                    "2) explorer.exe 是否正常运行",
                    parent=self,
                )
                return
            name = simpledialog.askstring(
                "保存布局", "为当前布局命名（共 %d 个图标）：" % total,
                initialvalue=self._default_name(), parent=self,
            )
            if not name:
                return
            self.store.add(name.strip(), layout)
            self.refresh_profiles(select_new=True)
            self.log("已保存布局「%s」：%d 个图标，%d 个桌面视图。"
                     % (name.strip(), total, len(layout["views"])))

        def on_restore(self):
            prof = self._selected_profile()
            if not prof:
                messagebox.showinfo("提示", "请先在左侧列表里选中一套布局。", parent=self)
                return
            if prof.get("auto_backup") and not messagebox.askyesno(
                "确认", "这是一条自动备份，确定要恢复到它吗？", parent=self
            ):
                return

            if self.var_auto_backup.get():
                try:
                    snap = self.backend.snapshot()
                    self.store.add("恢复前备份", snap, auto_backup=True)
                    dropped = self.store.prune_auto_backups(
                        int(self.store.config.get("auto_backup_keep", 8)))
                    self.refresh_profiles()
                    self.log("已自动备份当前布局%s。"
                             % ("，清理旧备份 %d 条" % dropped if dropped else ""))
                except Exception as e:
                    self.log("自动备份失败（已忽略）：%s" % e)

            try:
                stats = self.backend.restore(
                    prof,
                    disable_auto_arrange=self.var_disable_aa.get(),
                    index_fallback=self.var_index_fb.get(),
                )
            except Exception as e:
                messagebox.showerror("恢复失败", lock_error_hint(e), parent=self)
                self.log("恢复失败：%s" % lock_error_hint(e).replace("\n", " "))
                return

            msg = ("已恢复 %d 个图标（%d 个视图）。\n复核结果：精确落位 %d 个"
                   "，吸附到格点 %d 个，按屏幕适配 %d 个，偏差 %d 个。" % (
                       stats["restored"], stats["views"], stats["verified"],
                       stats.get("snapped", 0), stats.get("adapted", 0),
                       len(stats["mismatch"])))
            for name, want, got in stats["mismatch"][:5]:
                msg += "\n    · %s：期望 %s，实际 %s" % (name, want, got)
            if stats.get("missing"):
                msg += "\n\n有 %d 个保存过的图标现在找不到了，已跳过：\n%s" % (
                    len(stats["missing"]), "、".join(stats["missing"][:8]))
            if stats.get("extra"):
                msg += "\n\n桌面另有 %d 个保存时不存在的图标，未移动。" % stats["extra"]
            if stats.get("auto_fixed"):
                msg += "\n\n已关闭桌面的「自动排列图标」，否则写进去的位置会被系统立刻打乱。"
            if stats.get("screen_fit"):
                msg += "\n\n已按当前屏幕适配：%s。" % "；".join(stats["screen_fit"])
            if stats.get("snap_mode"):
                msg += "\n%s，坐标体系与保存时一致。" % stats["snap_mode"][0]
            if stats.get("coord"):
                msg += ("\n（读回时坐标系整体偏移 %s，属同一位置的不同记号；"
                        "已按多数票自校准后逐项比对，支持率 %.0f%%）"
                        % (stats["coord"], stats.get("coord_share", 0) * 100))
            if stats.get("appearance_restored"):
                msg += "\n\n" + "\n".join(stats["appearance_restored"])
            if stats.get("appearance"):
                msg += ("\n\n外观提醒（不在图标视图里，改不了当场生效）：\n    · "
                        + "\n    · ".join(stats["appearance"])
                        + "\n    → 改法：桌面右键 → 查看 → 选图标大小；"
                          "排序方式也在右键菜单里。")
            if stats.get("note"):
                msg += "\n\n注意：%s" % stats["note"]

            self.log(msg.replace("\n", " ").replace("    · ", ""))
            messagebox.showinfo("恢复完成", msg, parent=self)

        def on_grid_snap(self):
            try:
                n = self.backend.apply_grid_snap()
            except Exception as e:
                messagebox.showerror("对齐失败", str(e), parent=self)
                return
            self.log("已把 %d 个图标对齐到网格（顺序未改变）。" % n)
            messagebox.showinfo("完成", "已把 %d 个图标对齐到网格。\n\n建议现在保存一套布局。"
                                % n, parent=self)

        def on_rename(self):
            prof = self._selected_profile()
            if not prof:
                messagebox.showinfo("提示", "请先选中一套布局。", parent=self)
                return
            name = simpledialog.askstring("重命名", "新的名称：",
                                          initialvalue=prof.get("name", ""), parent=self)
            if not name:
                return
            self.store.rename(prof["id"], name.strip())
            self.refresh_profiles()
            self.log("已重命名为「%s」。" % name.strip())

        def on_delete(self):
            prof = self._selected_profile()
            if not prof:
                messagebox.showinfo("提示", "请先选中一套布局。", parent=self)
                return
            if not messagebox.askyesno("删除", "确定删除布局「%s」？" % prof.get("name"), parent=self):
                return
            if self._lock_pid == prof["id"]:
                self.stop_lock()
            self.store.delete(prof["id"])
            self.refresh_profiles()
            self.log("已删除布局「%s」。" % prof.get("name"))

        def on_import(self):
            path = filedialog.askopenfilename(
                title="导入布局", filetypes=[("JSON", "*.json"), ("全部文件", "*.*")], parent=self)
            if not path:
                return
            try:
                n = self.store.import_from(Path(path))
            except Exception as e:
                messagebox.showerror("导入失败", str(e), parent=self)
                return
            self.refresh_profiles()
            self.log("从 %s 导入 %d 套布局。" % (path, n))

        def on_export(self):
            path = filedialog.asksaveasfilename(
                title="导出布局", defaultextension=".json",
                initialfile="desktop-layouts-%s.json" % datetime.now().strftime("%Y%m%d"),
                filetypes=[("JSON", "*.json")], parent=self)
            if not path:
                return
            try:
                self.store.export_to(Path(path))
            except Exception as e:
                messagebox.showerror("导出失败", str(e), parent=self)
                return
            self.log("已导出全部布局到 %s。" % path)

        def on_open_dir(self):
            try:
                os.startfile(str(data_dir()))
            except Exception as e:
                self.log("打开目录失败：%s" % e)

        # ---------------- 锁定 ----------------
        def _sync_lock_button(self):
            running = self.lock_worker is not None and self.lock_worker.is_alive()
            if running:
                prof = self.store.get(self._lock_pid)
                self.btn_lock.configure(text="解除锁定")
                self.lbl_lock.configure(
                    text="锁定：开启（%s）" % (prof.get("name") if prof else "?"),
                    foreground="#1f7a3d")
            else:
                self.btn_lock.configure(text="锁定选中布局")
                self.lbl_lock.configure(text="锁定：关闭", foreground="#666")

        def on_toggle_lock(self):
            if self.lock_worker is not None and self.lock_worker.is_alive():
                self.stop_lock()
                return
            prof = self._selected_profile()
            if not prof:
                messagebox.showinfo("提示", "请先选中要锁定的布局。", parent=self)
                return
            try:
                self.backend.restore(
                    prof,
                    disable_auto_arrange=self.var_disable_aa.get(),
                    index_fallback=self.var_index_fb.get(),
                )
            except Exception as e:
                messagebox.showerror("锁定失败", lock_error_hint(e), parent=self)
                self.log("锁定失败：%s" % lock_error_hint(e).replace("\n", " "))
                return
            self.lock_worker = LockWorker(
                self.backend, prof,
                interval=float(self.store.config.get("lock_interval", 2.0)),
                on_event=self.log_threadsafe,
                disable_auto_arrange=self.var_disable_aa.get(),
                index_fallback=self.var_index_fb.get(),
                on_fatal=self._lock_fatal_threadsafe,
                on_stop=self._lock_stopped_threadsafe,
            )
            self._lock_pid = prof["id"]
            self.lock_worker.start()
            self._sync_lock_button()

        def stop_lock(self):
            if self.lock_worker is not None:
                self.lock_worker.stop()
                self.lock_worker = None
            self._lock_pid = None
            self._sync_lock_button()

        def _lock_fatal_threadsafe(self, reason: str):
            self.after(0, lambda r=reason: self._on_lock_fatal(r))

        def _lock_stopped_threadsafe(self):
            self.after(0, self._on_lock_stopped)

        def _on_lock_stopped(self):
            """锁定线程自己结束了（不管是正常停还是出错退出），界面得跟着回到关闭。"""
            worker = self.lock_worker
            if worker is not None and worker.is_alive():
                return
            self.lock_worker = None
            self._lock_pid = None
            self._sync_lock_button()

        def _on_lock_fatal(self, reason: str):
            self._on_lock_stopped()
            self.log("锁定异常退出：%s" % reason.replace("\n", " "))
            messagebox.showwarning(
                "锁定已停止",
                "%s\n\n桌面现在可以被自由拖动。修好上面的问题后，"
                "再点一次「锁定选中布局」即可重新锁上。" % reason, parent=self)

        # ---------------- 提权 ----------------
        def _startup_admin_check(self):
            if is_admin():
                return
            if not self.store.config.get("prefer_admin"):
                self.log("提示：可在选项里勾选「总是以管理员身份运行」，或点右上角按钮随时提权。")
                return
            if messagebox.askyesno(
                "需要管理员权限",
                "你已勾选「总是以管理员身份运行」，但当前是普通用户模式。\n\n"
                "是否现在以管理员身份重新启动？", parent=self
            ):
                self.on_relaunch_admin()

        def on_relaunch_admin(self):
            if is_admin():
                messagebox.showinfo("提示", "当前已经是管理员模式了。", parent=self)
                return
            if not messagebox.askyesno(
                "以管理员身份重启",
                "将通过 UAC 重新启动本程序（当前窗口会关闭）。继续？", parent=self
            ):
                return
            self.stop_lock()
            if not relaunch_as_admin():
                messagebox.showwarning(
                    "提权未完成",
                    "UAC 提权被取消或失败。可以手动：右键程序 → 以管理员身份运行。", parent=self)
                return
            self.destroy()
            os._exit(0)

        # ---------------- 关闭 ----------------
        def on_close(self):
            running = self.lock_worker is not None and self.lock_worker.is_alive()
            if running:
                if not messagebox.askyesno("退出", "锁定正在运行，退出会解除锁定。确定退出？",
                                           parent=self):
                    return
                self.stop_lock()
            self.destroy()

    app = App()
    if "--selftest-gui" in argv:
        # 自检：把整套界面建出来，跑几个刷新周期，然后自动关闭。
        # 用来确认 GUI 代码本身没有问题（不需要人盯着看）。
        app.after(1800, app.destroy)
        app.mainloop()
        print("GUI OK")
        return 0
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
