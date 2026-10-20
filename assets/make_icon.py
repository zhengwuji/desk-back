# -*- coding: utf-8 -*-
"""生成程序图标 assets/app.ico（纯标准库，不需要 Pillow）。

画的东西：一块蓝色圆角底板 + 一块浅色桌面 + 3x2 彩色图标方块，
右下角压一枚金色小锁 —— 一眼能看出"桌面布局 + 锁定"。
小于 32px 时不画锁，只留色块，避免 16px 糊成一团。

输出：
  assets/app.ico          多尺寸图标（<=64 用 BMP/DIB，128/256 用 PNG 压缩）
  assets/app_preview.png  256px 预览图，方便肉眼看

用法：python assets/make_icon.py
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent

# 尺寸：Windows 会按场景自己挑，给全一点
SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
SUPERSAMPLE = 4          # 4x4 超采样做抗锯齿

BG_TOP = (0x3A, 0x86, 0xF0)
BG_BOTTOM = (0x10, 0x42, 0x9A)
PANEL = (0xF2, 0xF6, 0xFD)
TILE_COLORS = [
    (0xE8, 0xA3, 0x3D), (0x4C, 0x9A, 0xFF), (0x52, 0xC4, 0x1A),
    (0xF5, 0x69, 0x7B), (0x92, 0x54, 0xDE), (0x13, 0xC2, 0xC2),
]
LOCK_WHITE = (0xFF, 0xFF, 0xFF)
LOCK_BODY = (0xFF, 0xB3, 0x00)
LOCK_SHACKLE = (0xB2, 0x6A, 0x00)
LOCK_HOLE = (0x7A, 0x52, 0x00)


# ---------------------------------------------------------------------------
# 几何判定（都在 0..1 的归一化坐标里，y 向下）
# ---------------------------------------------------------------------------
def in_rrect(x, y, x0, y0, x1, y1, r):
    """圆角矩形内部判定：把点夹到内矩形上，看距离是否 <= 半径。"""
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    qx = x - min(max(x, x0 + r), x1 - r)
    qy = y - min(max(y, y0 + r), y1 - r)
    return qx * qx + qy * qy <= r * r


def in_circle(x, y, cx, cy, r):
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= r * r


def in_annulus(x, y, cx, cy, r_in, r_out):
    dx, dy = x - cx, y - cy
    d2 = dx * dx + dy * dy
    return r_in * r_in <= d2 <= r_out * r_out


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


# ---------------------------------------------------------------------------
# 场景：给一个归一化坐标，返回这一点的颜色
# ---------------------------------------------------------------------------
def scene(x, y, with_badge):
    # 1. 蓝色圆角底板（竖向渐变）
    if in_rrect(x, y, 0.02, 0.02, 0.98, 0.98, 0.20):
        t = (y - 0.02) / 0.96
        color = lerp(BG_TOP, BG_BOTTOM, t)
    else:
        return (0, 0, 0, 0)

    # 2. 浅色"桌面"面板
    if in_rrect(x, y, 0.105, 0.135, 0.895, 0.865, 0.085):
        color = PANEL

    # 3. 3 列 x 2 行 图标方块
    cols, rows = 3, 2
    px0, py0, px1, py1 = 0.105, 0.135, 0.895, 0.865
    pad, gap = 0.055, 0.038
    tw = ((px1 - px0) - 2 * pad - (cols - 1) * gap) / cols
    th = ((py1 - py0) - 2 * pad - (rows - 1) * gap) / rows
    for row in range(rows):
        for col in range(cols):
            tx = px0 + pad + col * (tw + gap)
            ty = py0 + pad + row * (th + gap)
            if in_rrect(x, y, tx, ty, tx + tw, ty + th, min(tw, th) * 0.26):
                color = TILE_COLORS[row * cols + col]

    # 4. 右下角小锁
    if with_badge:
        cx, cy, R = 0.742, 0.742, 0.212
        if in_circle(x, y, cx, cy, R):
            color = LOCK_WHITE
        if in_annulus(x, y, cx, cy - 0.036, 0.044, 0.085) and y <= cy - 0.036:
            color = LOCK_SHACKLE
        if in_rrect(x, y, cx - 0.083, cy - 0.027, cx + 0.083, cy + 0.100, 0.029):
            color = LOCK_BODY
        if in_circle(x, y, cx, cy + 0.049, 0.022):
            color = LOCK_HOLE

    return color[0], color[1], color[2], 255


def render(size):
    """渲染一张 size x size 的 RGBA 图，返回 bytes（逐行 RGBA）。"""
    with_badge = size >= 32
    ss = SUPERSAMPLE if size >= 24 else 6      # 小图多采几次，边缘更干净
    inv = 1.0 / size
    out = bytearray(size * size * 4)
    step = 1.0 / ss
    # 每个子采样点的权重一样，最后取平均
    total = float(ss * ss)
    for py in range(size):
        for px in range(size):
            r = g = b = a = 0.0
            for sy in range(ss):
                ny = (py + (sy + 0.5) * step) * inv
                for sx in range(ss):
                    nx = (px + (sx + 0.5) * step) * inv
                    cr, cg, cb, ca = scene(nx, ny, with_badge)
                    if ca:
                        r += cr
                        g += cg
                        b += cb
                        a += ca
            if a <= 0:
                continue
            n = a / 255.0                      # 覆盖到的子采样点数量
            # 颜色按覆盖率归一，alpha 用真实覆盖率，边缘才平滑
            idx = (py * size + px) * 4
            out[idx] = int(round(r / n))
            out[idx + 1] = int(round(g / n))
            out[idx + 2] = int(round(b / n))
            out[idx + 3] = int(round(a / total))
    return bytes(out)


# ---------------------------------------------------------------------------
# 编码器
# ---------------------------------------------------------------------------
def png_bytes(size, rgba):
    raw = bytearray()
    stride = size * 4
    for y in range(size):
        raw.append(0)                          # filter type 0
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def dib_bytes(size, rgba):
    """ICO 里的 BMP(DIB) 条目：BITMAPINFOHEADER + BGRA 自下而上 + AND 掩码。"""
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                         size * size * 4, 0, 0, 0, 0)
    xor = bytearray()
    for y in range(size - 1, -1, -1):          # 自下而上
        row = rgba[y * size * 4:(y + 1) * size * 4]
        for x in range(size):
            r, g, b, a = row[x * 4:x * 4 + 4]
            xor += bytes((b, g, r, a))         # BGRA
    mask_stride = ((size + 31) // 32) * 4      # 1bpp，行按 4 字节对齐
    and_mask = bytearray()
    for y in range(size - 1, -1, -1):
        bits = bytearray(mask_stride)
        for x in range(size):
            if rgba[(y * size + x) * 4 + 3] < 128:
                bits[x // 8] |= 0x80 >> (x % 8)   # 1 = 透明
        and_mask += bits
    return header + bytes(xor) + bytes(and_mask)


def ico_bytes(entries):
    """entries: [(size, payload)]，payload 已经是 PNG 或 DIB 字节。"""
    n = len(entries)
    out = struct.pack("<HHH", 0, 1, n)
    offset = 6 + 16 * n
    table = b""
    blob = b""
    for size, payload in entries:
        dim = 0 if size >= 256 else size
        table += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                             len(payload), offset)
        offset += len(payload)
        blob += payload
    return out + table + blob


def main() -> int:
    entries = []
    preview = None
    for size in SIZES:
        rgba = render(size)
        if size >= 128:
            entries.append((size, png_bytes(size, rgba)))
        else:
            entries.append((size, dib_bytes(size, rgba)))
        if size == 256:
            preview = png_bytes(size, rgba)
        print("  渲染 %3dpx ..." % size)
    ico = HERE / "app.ico"
    ico.write_bytes(ico_bytes(entries))
    if preview:
        (HERE / "app_preview.png").write_bytes(preview)
    print("已生成：%s（%d 个尺寸，%d 字节）"
          % (ico, len(entries), ico.stat().st_size))
    print("预览图：%s" % (HERE / "app_preview.png"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
