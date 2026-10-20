# -*- coding: utf-8 -*-
"""端到端回归（v1.2）：
  1) 快照必须"无副作用"（读完之后桌面状态原样）
  2) 打乱后一键恢复：吸附模式一致 + 布局与初始逐项一致
  3) 桌面被切到相反吸附模式后恢复，也要拨回去并精确落位
  4) 锁定纠偏把被挪走的图标拉回来，且第二次检查零漂移（收敛）
  5) 无论成败，最后把桌面还原成原样

注意：桌面「对齐到网格」会改变坐标的记号（同一格子 (0,299) ↔ (13,296)），
所以所有"是否一致"的判断都走模块自己的坐标系自校准比对，而不是裸比数字。
"""
import json

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import desktop_layout_keeper as m

k = m.DesktopBackend()


def as_items(sig):
    return [{"name": n, "x": x, "y": y} for n, x, y in sig]


def layout_equal(sig_a, sig_b):
    """允许整体坐标系偏移的布局比对。返回 (是否一致, 偏移, 支持率, 差异明细)。"""
    ta = as_items(sig_a)
    cb = m.positions_by_name(as_items(sig_b))
    off, share = m.coordinate_offset(ta, cb)
    d = m.position_drift(ta, cb, off)
    return (not d), off, share, d


views = k.open_views()
assert views, "没有找到桌面视图"
v = views[0]
orig_aa = m.auto_arrange_enabled(v.hwnd)
orig_snap = m.snap_to_grid_enabled(v.hwnd)
raw_before = m.read_view_items(v)
sig_before = [(i["name"], i["x"], i["y"]) for i in raw_before]
print("原始状态: auto_arrange=%s snap_to_grid=%s 图标=%d" % (orig_aa, orig_snap, len(raw_before)))
print("原始前 5 项:", sig_before[:5])


def current_state():
    vs = k.open_views()
    vv = vs[0]
    out = (m.auto_arrange_enabled(vv.hwnd), m.snap_to_grid_enabled(vv.hwnd),
           [(i["name"], i["x"], i["y"]) for i in m.read_view_items(vv)])
    k.close_views(vs)
    return out


def scramble():
    vs = k.open_views()
    vv = vs[0]
    m.set_auto_arrange(vv.hwnd, False)
    m.set_snap_to_grid(vv.hwnd, False)
    for idx, x, y in [(0, 900, 400), (1, 500, 900), (2, 137, 37)]:
        m.set_item_position(vv, idx, x, y)
    m.refresh_view(vv.hwnd)
    got = {i["index"]: (i["x"], i["y"]) for i in m.read_view_items(vv)}
    k.close_views(vs)
    return got


try:
    # ---- 1) 快照无副作用 ----
    layout = k.snapshot()
    _aa, _snap, sig_after_snap = current_state()
    same = [(n, x, y) for n, x, y in sig_before] == sig_after_snap
    print("快照后桌面原样（同坐标系逐项一致）:", same)
    assert same, "快照读取改变了桌面状态"

    sv = layout["views"][0]
    items = sv["items"]
    print("快照记录的吸附模式: snap_to_grid=%s  前 3 项: %s"
          % (sv["snap_to_grid"], [(i["name"], i["x"], i["y"]) for i in items[:3]]))
    assert sv["snap_to_grid"] == orig_snap, "快照没有记录正确的吸附状态"
    assert len(items) >= 3

    profile = {"id": "test", "name": "test", "layout": layout}

    # ---- 2) 打乱 + 恢复（打乱时故意把吸附关掉，制造坐标系不一致）----
    got = scramble()
    print("打乱后精确写入校验:", {i: got[i] for i in (0, 1, 2)})
    assert got[0] == (900, 400) and got[1] == (500, 900) and got[2] == (137, 37)

    stats = k.restore(profile, disable_auto_arrange=True, index_fallback=True)
    print("恢复统计:", json.dumps({kk: vv for kk, vv in stats.items() if kk != "missing"},
                                  ensure_ascii=False))
    assert stats["restored"] == len(items), "恢复数量不符"
    assert not stats["mismatch"], "存在位置偏差: %s" % stats["mismatch"][:5]
    assert stats["verified"] == stats["restored"], "复核未全部通过"

    aa2, snap2, sig_restored = current_state()
    print("恢复后 auto_arrange=%s snap_to_grid=%s（应回到快照时的 %s）"
          % (aa2, snap2, orig_snap))
    assert snap2 == orig_snap, "恢复后吸附模式没有对齐到快照状态"
    ok, off, share, diff = layout_equal(sig_before, sig_restored)
    print("与初始布局比对：一致=%s 坐标系偏移=%s 支持率=%.2f" % (ok, off, share))
    assert ok, "恢复后与初始布局不一致: %s" % diff[:4]
    print("== 一键恢复：%d/%d 精确落位，布局与初始一致 ==" % (stats["verified"], stats["restored"]))

    # ---- 3) 桌面被切到相反模式后再恢复 ----
    vs = k.open_views()
    m.set_auto_arrange(vs[0].hwnd, False)
    m.set_snap_to_grid(vs[0].hwnd, not orig_snap)
    m.refresh_view(vs[0].hwnd)
    k.close_views(vs)
    scramble()
    stats2 = k.restore(profile, disable_auto_arrange=True, index_fallback=True)
    aa3, snap3, sig_after2 = current_state()
    ok3, off3, share3, diff3 = layout_equal(sig_before, sig_after2)
    print("反模式恢复：verified=%d/%d snap_to_grid=%s 一致=%s 偏移=%s"
          % (stats2["verified"], stats2["restored"], snap3, ok3, off3))
    assert snap3 == orig_snap and ok3, "反模式恢复失败: %s" % diff3[:4]
    print("== 吸附模式被拨回且布局一致 ==")

    # ---- 4) 锁定纠偏 ----
    vs = k.open_views()
    vv = vs[0]
    victim = m.read_view_items(vv)[0]
    m.set_item_position(vv, victim["index"], 1200, 620)
    m.refresh_view(vv.hwnd)
    k.close_views(vs)

    drift = k.enforce(profile, True, True)
    print("enforce 检测到漂移 %d 处: %s" % (len(drift), drift[:2]))
    assert drift, "enforce 没有检测到漂移"

    aa4, snap4, sig_back = current_state()
    ok4, off4, share4, diff4 = layout_equal(sig_before, sig_back)
    assert ok4, "锁定未拉回原位: %s" % diff4[:4]
    print("== 锁定纠偏：%s 已拉回，布局恢复一致（坐标系偏移 %s）==" % (victim["name"], off4))

    # 再跑一次必须零漂移：证明纠偏收敛，不会反复刷桌面
    drift2 = k.enforce(profile, True, True)
    print("第二次 enforce 漂移数:", len(drift2))
    assert not drift2, "纠偏不收敛，第二次仍报漂移: %s" % drift2[:3]
    print("== 纠偏收敛（第二次零漂移）==")

    # ---- 5) 网格吸附按钮 ----
    n = k.apply_grid_snap()
    print("「把图标对齐到网格」影响图标数:", n)

finally:
    vs = k.open_views()
    vv = vs[0]
    m.set_auto_arrange(vv.hwnd, bool(orig_aa))
    m.set_snap_to_grid(vv.hwnd, bool(orig_snap))
    if orig_aa:
        m._send(vv.hwnd, m._Win32.LVM_ARRANGE, m._Win32.LVA_DEFAULT, 0)
    m.refresh_view(vv.hwnd)
    print("现场还原 -> auto_arrange=%s snap_to_grid=%s"
          % (m.auto_arrange_enabled(vv.hwnd), m.snap_to_grid_enabled(vv.hwnd)))
    k.close_views(vs)

print("\nALL PASS")
