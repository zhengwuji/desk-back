# -*- coding: utf-8 -*-
"""端到端（走真实 layouts.json 存储 + --cli-restore 这条命令行路径）。

  python _e2e_store.py prep    保存一套临时布局 -> 打乱桌面
  python src/desktop_layout_keeper.py --cli-restore __自检临时__
  python _e2e_store.py check   复核 + 清理临时布局 + 还原现场
"""
import json
import sys

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import desktop_layout_keeper as m

TMP = "__自检临时__"
STATE = m.data_dir() / "_e2e_state.json"

k = m.DesktopBackend()
store = m.ProfileStore(m.STORE_FILE)


def as_items(sig):
    return [{"name": n, "x": x, "y": y} for n, x, y in sig]


def read_sig():
    vs = k.open_views()
    out = [(i["name"], i["x"], i["y"]) for i in m.read_view_items(vs[0])]
    aa = m.auto_arrange_enabled(vs[0].hwnd)
    snap = m.snap_to_grid_enabled(vs[0].hwnd)
    k.close_views(vs)
    return aa, snap, out


def prep():
    aa, snap, sig = read_sig()
    layout = k.snapshot()
    prof = store.add(TMP, layout)
    store.save()
    STATE.write_text(json.dumps({"id": prof["id"], "aa": aa, "snap": snap,
                                 "sig": sig}, ensure_ascii=False), encoding="utf-8")
    print("已保存临时布局 %r：%d 个图标 / %d 个视图（原始 auto_arrange=%s snap=%s）"
          % (TMP, prof["icon_count"], prof["view_count"], aa, snap))

    vs = k.open_views()
    v = vs[0]
    m.set_auto_arrange(v.hwnd, False)
    m.set_snap_to_grid(v.hwnd, False)
    for idx, x, y in [(0, 900, 400), (1, 500, 900), (2, 137, 37), (3, 1500, 700)]:
        m.set_item_position(v, idx, x, y)
    m.refresh_view(v.hwnd)
    got = {i["index"]: (i["x"], i["y"]) for i in m.read_view_items(v)}
    k.close_views(vs)
    print("已打乱:", {i: got[i] for i in (0, 1, 2, 3)})
    assert got[0] == (900, 400) and got[3] == (1500, 700)


def check():
    st = json.loads(STATE.read_text(encoding="utf-8"))
    aa, snap, sig = read_sig()
    ta = as_items([tuple(x) for x in st["sig"]])
    cb = m.positions_by_name(as_items(sig))
    off, share = m.coordinate_offset(ta, cb)
    drift = m.position_drift(ta, cb, off)
    print("恢复后：图标 %d 个，auto_arrange=%s，snap=%s" % (len(sig), aa, snap))
    print("   与打乱前逐项比对：漂移 %d 处，坐标系偏移 %s（支持率 %.2f）"
          % (len(drift), off, share))
    if drift:
        print("   前几处:", drift[:4])
    assert not drift, "命令行恢复没有精确还原"
    print("== --cli-restore 端到端：布局与打乱前一致 ==")

    # 清理：删掉临时布局，还原桌面设置与排列
    store.data["profiles"] = [p for p in store.profiles if p.get("name") != TMP]
    store.save()
    vs = k.open_views()
    v = vs[0]
    m.set_auto_arrange(v.hwnd, bool(st["aa"]))
    m.set_snap_to_grid(v.hwnd, bool(st["snap"]))
    if st["aa"]:
        m._send(v.hwnd, m._Win32.LVM_ARRANGE, m._Win32.LVA_DEFAULT, 0)
    m.refresh_view(v.hwnd)
    print("已清理临时布局；现场还原 -> auto_arrange=%s snap=%s"
          % (m.auto_arrange_enabled(v.hwnd), m.snap_to_grid_enabled(v.hwnd)))
    k.close_views(vs)
    try:
        STATE.unlink()
    except OSError:
        pass


if __name__ == "__main__":
    {"prep": prep, "check": check}[sys.argv[1]]()
