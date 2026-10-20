#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v1.3（①②③）真机回归。

覆盖三件事：
  ① 锁定"静默失效"：拿不到桌面视图时必须报错/退出并弹提醒，不许假装正常
  ② 快照记录外观：图标大小 / 排序方式要能读到、能比对、能如实报告
  ③ 屏幕适配：保存和恢复之间屏幕变了，坐标要按比例缩放并夹进屏幕内

用法：python _dev/_selftest_13.py
会临时保存一份布局、故意构造"屏幕变小"的恢复，最后还原现场并删掉临时布局。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import desktop_layout_keeper as K  # noqa: E402

TMP = "__13自检临时__"
fails = []


def check(label, ok, extra=""):
    print(("  [OK]   " if ok else "  [FAIL] ") + label + (("   " + str(extra)) if extra else ""))
    if not ok:
        fails.append(label)


def read_layout(backend):
    views = backend.open_views()
    try:
        return [K.read_view_items(v) for v in views]
    finally:
        backend.close_views(views)


def main():
    backend = K.DesktopBackend()
    store = K.ProfileStore(K.STORE_FILE)

    # 先记下现场：桌面开关
    views = backend.open_views()
    if not views:
        print("拿不到桌面视图，无法自检（需要 explorer 在运行、桌面图标已显示）")
        return 2
    hwnd = views[0].hwnd
    orig_aa = K.auto_arrange_enabled(hwnd)
    orig_snap = K.snap_to_grid_enabled(hwnd)
    backend.close_views(views)

    prof = None
    try:
        print("== ② 快照记录外观 ==")
        snap = backend.snapshot()
        app = snap.get("appearance") or {}
        check("快照里有 appearance", bool(app), app)
        check("读到了图标大小", bool(app.get("icon_size")), app.get("icon_size_name"))
        check("读到了排序指纹", app.get("sort") is not None)
        check("读到了「显示桌面图标」", app.get("hide_icons") is not None)

        print("== 准备临时布局 ==")
        old = store.find_by_name(TMP)
        if old:
            store.delete(old["id"])
        prof = store.add(TMP, snap)
        check("临时布局已保存", prof["icon_count"] > 0,
              "%d 图标 / %d 视图" % (prof["icon_count"], prof["view_count"]))

        print("== ① 拿不到视图时不再静默 ==")
        orig_open = K.DesktopBackend.open_views
        K.DesktopBackend.open_views = lambda self: []
        raised = None
        try:
            backend.enforce(prof)
        except Exception as e:                                   # noqa: BLE001
            raised = e
        finally:
            K.DesktopBackend.open_views = orig_open
        check("enforce 抛 DesktopViewUnavailable", isinstance(raised, K.DesktopViewUnavailable),
              type(raised).__name__ if raised else "没有抛任何异常")

        print("== ① 连续失败会带原因退出并回调 ==")
        events, fatals, stops = [], [], []

        class BadBackend:
            def enforce(self, *a, **k):
                raise OSError("[Errno 5] OpenProcess 失败，错误 5（拒绝访问）")

        worker = K.LockWorker(BadBackend(), {"layout": {"views": []}}, interval=0.5,
                              on_event=events.append, on_fatal=fatals.append,
                              on_stop=lambda: stops.append(1))
        worker.start()
        worker.join(timeout=20)
        check("失败到上限后线程退出", not worker.is_alive(), "failures=%d" % worker.failures)
        check("on_fatal 拿到原因", bool(fatals) and "拒绝访问" in fatals[0])
        check("原因里带了可照做的建议", bool(fatals) and "管理员" in fatals[0])
        check("on_stop 被调用（界面能翻回关闭）", bool(stops))
        check("日志里有出错记录", any("锁定检查出错" in e for e in events))

        print("== ③ 屏幕变了：锁定会暂停而不是死循环 ==")
        import copy
        doctored = copy.deepcopy(prof)
        doctored["layout"]["screens"]["virtual"] = [0, 0, 1280, 720]
        raised = None
        try:
            backend.enforce(doctored)
        except Exception as e:                                   # noqa: BLE001
            raised = e
        check("enforce 抛 DesktopScreenChanged", isinstance(raised, K.DesktopScreenChanged),
              type(raised).__name__ if raised else "没有抛任何异常")

        print("== ③ 屏幕变了：恢复时缩放置位且不越界 ==")
        # 故意把最后 3 个图标"存"在远大于保存屏幕的坐标上，
        # 让适配逻辑必须缩2倍再把它们拉回屏幕内。
        items = doctored["layout"]["views"][0]["items"]
        for it in items[-3:]:
            it["x"], it["y"] = 2500, 1400
        stats = backend.restore(doctored)
        print("     restore stats: restored=%d verified=%d mismatch=%d screen_fit=%s"
              % (stats["restored"], stats["verified"], len(stats["mismatch"]),
                 stats.get("screen_fit")))
        check("报告了屏幕适配说明", bool(stats.get("screen_fit")), stats.get("screen_fit"))
        check("没有真偏差", not stats["mismatch"], stats["mismatch"][:3])
        check("每个图标都有交代（精确+吸附+适配）",
              stats["verified"] + stats["snapped"] + stats["adapted"] == stats["restored"],
              "verified=%d snapped=%d adapted=%d restored=%d"
              % (stats["verified"], stats["snapped"], stats["adapted"],
                 stats["restored"]))
        sc = K.screen_info()
        x0, y0, w, h = sc["virtual"]
        x1, y1 = x0 + w - 1, y0 + h - 1
        after = read_layout(backend)[0]
        off = [(i["name"], i["x"], i["y"]) for i in after
               if i["x"] < x0 or i["y"] < y0 or i["x"] > x1 or i["y"] > y1]
        check("没有图标落在屏幕外", not off, off[:5])

        print("== 还原现场 ==")
        stats = backend.restore(prof)
        off, _share = K.coordinate_offset(prof["layout"]["views"][0]["items"],
                                          K.positions_by_name(read_layout(backend)[0]))
        drift = K.position_drift(prof["layout"]["views"][0]["items"],
                                 K.positions_by_name(read_layout(backend)[0]), off)
        check("还原后无漂移", not drift, drift[:3])
        check("还原后精确落位",
              stats["verified"] + stats["snapped"] + stats["adapted"] == stats["restored"]
              and not stats["mismatch"],
              "verified=%d snapped=%d adapted=%d restored=%d"
              % (stats["verified"], stats["snapped"], stats["adapted"],
                 stats["restored"]))
    finally:
        try:
            if prof:
                store.delete(prof["id"])
        except Exception:
            pass
        try:
            if store.find_by_name(TMP):
                store.delete(store.find_by_name(TMP)["id"])
        except Exception:
            pass
        views = backend.open_views()
        if views:
            K.set_auto_arrange(views[0].hwnd, orig_aa)
            K.set_snap_to_grid(views[0].hwnd, orig_snap)
            backend.close_views(views)
        print("现场已还原：auto_arrange=%s snap_to_grid=%s" % (orig_aa, orig_snap))

    print()
    if fails:
        print("FAILED (%d)：%s" % (len(fails), "；".join(fails)))
        return 1
    print("ALL PASS —— ①②③ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
