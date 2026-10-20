#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""锁定真能拉回图标的验证（不用开 GUI，直接驱动 LockWorker）。

做四件事：
  1. 存一套临时布局（记下第 0 个图标的原始坐标）
  2. 起一个 LockWorker 盯着它
  3. 把第 0 个图标搬到 (1200, 900) 模拟被人拖走
  4. 等两轮，确认图标回到原位、且事件流里出现了「拉回」

用法（在仓库根目录）：python _dev/_lock_pullback.py
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import desktop_layout_keeper as K  # noqa: E402

TMP = "__锁定拉回自检__"


def main():
    backend = K.DesktopBackend()
    store = K.ProfileStore(K.STORE_FILE)

    # 清掉上次可能留下的
    old = store.find_by_name(TMP)
    if old:
        store.delete(old["id"])

    snap = backend.snapshot()
    prof = store.add(TMP, snap)
    view0 = snap["views"][0]
    name0, x0, y0 = view0["items"][0]["name"], view0["items"][0]["x"], view0["items"][0]["y"]
    print("临时布局：%d 图标 / %d 视图；第 0 个是 %s (%d, %d)"
          % (prof["icon_count"], prof["view_count"], name0, x0, y0))

    events = []
    stop_flag = {}

    def on_event(msg):
        events.append(msg)
        print("  [事件] %s" % msg)

    def on_fatal(reason):
        stop_flag["fatal"] = reason

    def on_stop():
        stop_flag["stopped"] = True

    worker = K.LockWorker(
        backend, prof,
        interval=2.0,
        on_event=on_event,
        disable_auto_arrange=True,
        index_fallback=True,
        on_fatal=on_fatal,
        on_stop=on_stop,
    )
    worker.start()
    time.sleep(3.0)

    # ---- 搬走 ----
    views = backend.open_views()
    hwnd = views[0].hwnd
    K.set_auto_arrange(hwnd, False)
    K.set_item_position(views[0], 0, 1200, 900)
    moved = K.read_view_items(views[0])[0]
    print("已搬到 (1200, 900)，读回是 (%d, %d)" % (moved["x"], moved["y"]))

    time.sleep(6.0)
    worker.stop()
    time.sleep(0.5)

    back = K.read_view_items(views[0])[0]
    print("现在第 0 个是 %s (%d, %d)" % (back["name"], back["x"], back["y"]))

    # 用模块自己的「多数票自校准」判定，而不是拿两个坐标系里的数字直接比：
    # 开「对齐到网格」读 (0,5)、关掉读 (13,2)，这两个是同一个物理位置。
    cur = {}
    for i in K.read_view_items(views[0]):
        cur.setdefault(i["name"], []).append((i["x"], i["y"]))
    off, share = K.coordinate_offset(view0["items"], cur)
    drift = K.position_drift(view0["items"], cur, off)
    print("坐标系偏移 %s（支持率 %.2f），漂移 %d 处" % (off, share, len(drift)))
    for d in drift[:5]:
        print("   ", d)

    pulled = [m for m in events if ("已还原" in m or "拉回" in m)]
    ok = (not drift) and bool(pulled)

    # 清理现场
    store.delete(prof["id"])
    K.set_auto_arrange(hwnd, True)
    K.refresh_view(hwnd)
    print("已清理临时布局，桌面开关复位")
    print("RESULT:", "PASS —— 拉回原位且事件里报了还原" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
