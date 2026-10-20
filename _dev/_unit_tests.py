#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯函数单测：几毫秒跑完，不碰真实桌面、不需要管理员、不改注册表。

为什么单独有这个文件：坐标换算、恢复计划、屏幕适配、打码这几块全是纯逻辑，
以前改一次就得靠 _selftest.py / _selftest_13.py 去真机上人肉回归（要点真实图标、
关掉自动排列、还可能被安全策略拦）。这里把不依赖桌面状态的部分钉死，
以后动这些逻辑，改完先跑这个。

跑法：  python _dev/_unit_tests.py        （退出码 0 = 全过，1 = 有失败）
CI 里在编译前跑一次，失败就不发布。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

import desktop_layout_keeper as K          # noqa: E402

COUNT = 0
FAILS = []


def check(label, got, want):
    """相等断言。顺便把「字符串里带了 ｜ 被吞掉」这类问题暴露出来。"""
    global COUNT
    COUNT += 1
    if got == want:
        return True
    FAILS.append(label)
    print("  [FAIL] %s\n         实际: %r\n         期望: %r" % (label, got, want))
    return False


def check_true(label, cond, extra=""):
    global COUNT
    COUNT += 1
    if cond:
        return True
    FAILS.append(label)
    print("  [FAIL] %s%s" % (label, ("\n         " + extra) if extra else ""))
    return False


def check_in(label, needle, hay):
    global COUNT
    COUNT += 1
    if needle in hay:
        return True
    FAILS.append(label)
    print("  [FAIL] %s\n         期望包含: %r\n         实际: %r" % (label, needle, hay))
    return False


def items(*pairs):
    """items(("A", 0, 5), ...) → [{"name": "A", "x": 0, "y": 5}, ...]"""
    return [{"name": n, "x": x, "y": y} for n, x, y in pairs]


def cur(**buckets):
    """cur(A=[(13, 2)]) → {"A": [(13, 2)]}"""
    return {k: list(v) for k, v in buckets.items()}


def cust(*pairs):
    """当前桌面的条目（带 index），items("A") 那种写法在这里不够用。"""
    return [{"index": i, "name": n, "x": x, "y": y} for i, (n, x, y) in enumerate(pairs)]


# --------------------------------------------------------------------------
# 1. coordinate_offset —— 判断「两套坐标系」还是「真被挪走了」
# --------------------------------------------------------------------------
def test_coordinate_offset():
    print("coordinate_offset：整体偏移识别")
    # 全部平移 (13,-3)：这就是本机实测的「对齐到网格」记号差
    off, share = K.coordinate_offset(items(("A", 0, 5), ("B", 0, 103), ("C", 0, 201)),
                                     cur(A=[(13, 2)], B=[(13, 100)], C=[(13, 198)]))
    check("整体平移 → 偏移", off, (13, -3))
    check("整体平移 → 支持率", round(share, 4), 1.0)

    # 空输入
    check("空目标 → 偏移", K.coordinate_offset([], {})[0], (0, 0))
    check("空目标 → 支持率", K.coordinate_offset([], {})[1], 0.0)
    check("名字对不上 → 偏移", K.coordinate_offset(items(("A", 0, 0)), cur(Z=[(1, 1)]))[0], (0, 0))

    # 偏移超过 OFFSET_MAX_PX(64) → 不当成记号差异
    off, share = K.coordinate_offset(items(("A", 0, 0), ("B", 0, 0), ("C", 0, 0)),
                                     cur(A=[(100, 0)], B=[(100, 0)], C=[(100, 0)]))
    check("偏移超上限 → 偏移归零", off, (0, 0))
    check("偏移超上限 → 支持率仍如实报", share, 1.0)

    # 票数分散 → 认定无关（每票都不一样）
    off, share = K.coordinate_offset(items(("A", 0, 0), ("B", 0, 0), ("C", 0, 0), ("D", 0, 0), ("E", 0, 0)),
                                     cur(A=[(1, 1)], B=[(2, 2)], C=[(3, 3)], D=[(4, 4)], E=[(5, 5)]))
    check("票分散 → 偏移归零", off, (0, 0))
    check("票分散 → 支持率 1/5", round(share, 4), 0.2)

    # 阈值边界：3/5 = 0.6，正好等于 OFFSET_MIN_SHARE，应当采纳
    off, share = K.coordinate_offset(items(("A", 0, 0), ("B", 0, 0), ("C", 0, 0), ("D", 0, 0), ("E", 0, 0)),
                                     cur(A=[(7, 7)], B=[(7, 7)], C=[(7, 7)], D=[(3, 3)], E=[(3, 3)]))
    check("支持率正好 0.6 → 采纳多数票", off, (7, 7))
    check("支持率正好 0.6 → 报值", round(share, 4), 0.6)

    # 阈值之下：2/5 = 0.4，不采纳
    off, share = K.coordinate_offset(items(("A", 0, 0), ("B", 0, 0), ("C", 0, 0), ("D", 0, 0), ("E", 0, 0)),
                                     cur(A=[(7, 7)], B=[(7, 7)], C=[(1, 1)], D=[(2, 2)], E=[(3, 3)]))
    check("支持率 0.4 → 偏移归零", off, (0, 0))
    check("支持率 0.4 → 报值", round(share, 4), 0.4)

    # 同名多图标按出现顺序配对：两个 X 都平移了同样的量 → 仍能认出偏移
    off, share = K.coordinate_offset(items(("X", 0, 0), ("X", 7, 0)),
                                     cur(X=[(13, 2), (20, 2)]))
    check("同名多图标按顺序配对 → 偏移", off, (13, 2))
    check("同名多图标按顺序配对 → 支持率", round(share, 4), 1.0)

    # 目标里没名字的条目应被跳过，不参与投票
    off, _ = K.coordinate_offset([{"name": "", "x": 0, "y": 0}, {"name": "A", "x": 0, "y": 0}],
                                 cur(A=[(13, -3)]))
    check("无名条目不投票", off, (13, -3))


# --------------------------------------------------------------------------
# 2. position_drift —— 按名字比对，比较前先减掉整体偏移
# --------------------------------------------------------------------------
def test_position_drift():
    print("position_drift：漂移比对")
    check("带偏移且一致 → 无漂移",
          K.position_drift(items(("A", 0, 5)), cur(A=[(13, 2)]), (13, -3)), [])
    check("默认偏移 0 且一致 → 无漂移",
          K.position_drift(items(("A", 0, 5)), cur(A=[(0, 5)])), [])
    check("真被挪走 → 报出来（期望值换算回保存坐标系）",
          K.position_drift(items(("A", 0, 5)), cur(A=[(500, 400)]), (13, -3)),
          [("A", (0, 5), (487, 403))])
    check("图标不在了 → 不算漂移（避免无限纠偏）",
          K.position_drift(items(("A", 0, 0), ("B", 0, 0)), cur(A=[(0, 0)])), [])
    check("同名第二个漂了 → 只报第二个",
          K.position_drift(items(("X", 10, 0), ("X", 20, 0)), cur(X=[(10, 0), (99, 0)])),
          [("X", (20, 0), (99, 0))])


# --------------------------------------------------------------------------
# 3. build_restore_plan —— 保存条目 → 当前条目的映射
# --------------------------------------------------------------------------
def test_build_restore_plan():
    print("build_restore_plan：恢复计划")
    plan, unmatched, leftover = K.build_restore_plan(
        items(("A", 1, 1), ("B", 2, 2)),
        cust(("B", 0, 0), ("A", 0, 0)))
    check("按名字配对，坐标取保存值", sorted(plan), [(0, 2, 2, "B"), (1, 1, 1, "A")])
    check("没有未匹配", unmatched, [])
    check("没有多余", leftover, [])

    # 大小写不一致也要配上（casefold 兜底）
    plan, unmatched, _ = K.build_restore_plan(items(("my pc", 5, 6)), cust(("My PC", 0, 0)))
    check("大小写不同 → 仍配对", plan, [(0, 5, 6, "My PC")])
    check("大小写不同 → 无未匹配", unmatched, [])

    # 多出来的新图标：index_fallback 打开时会按顺序兜底
    plan, unmatched, leftover = K.build_restore_plan(
        items(("A", 1, 1), ("B", 2, 2)), cust(("A", 0, 0), ("新图标", 0, 0)))
    check("index_fallback 打开 → 未匹配被兜底消化", unmatched, [])
    check("index_fallback 打开 → 兜底条目进计划", sorted(plan), [(0, 1, 1, "A"), (1, 2, 2, "新图标")])
    check("多余条目仍如实报出", [c["name"] for c in leftover], ["新图标"])

    # 关掉兜底：未匹配就留在未匹配里，不动新图标
    plan, unmatched, leftover = K.build_restore_plan(
        items(("A", 1, 1), ("B", 2, 2)), cust(("A", 0, 0), ("新图标", 0, 0)), index_fallback=False)
    check("index_fallback 关闭 → 未匹配保留", [s["name"] for s in unmatched], ["B"])
    check("index_fallback 关闭 → 计划里没有它", plan, [(0, 1, 1, "A")])

    # 数量不等时不乱配（2 个未匹配 vs 1 个多余）
    plan, unmatched, leftover = K.build_restore_plan(
        items(("A", 1, 1), ("B", 2, 2), ("C", 3, 3)), cust(("A", 0, 0), ("新图标", 0, 0)))
    check("数量不等 → 不兜底", sorted(s["name"] for s in unmatched), ["B", "C"])

    # 同名多图标各占一个 index
    plan, unmatched, leftover = K.build_restore_plan(
        items(("X", 1, 1), ("X", 2, 2)), cust(("X", 0, 0), ("X", 0, 0)))
    check("同名多图标各占一个 index", plan, [(0, 1, 1, "X"), (1, 2, 2, "X")])
    check("同名多图标 → 无剩余", leftover, [])


# --------------------------------------------------------------------------
# 4. 屏幕变化识别 + 适配
# --------------------------------------------------------------------------
SC_2560 = {"monitors": 1, "primary": [2560, 1440], "virtual": [0, 0, 2560, 1440]}
SC_1280 = {"monitors": 1, "primary": [1280, 720], "virtual": [0, 0, 1280, 720]}
SC_OFFSET = {"monitors": 1, "primary": [2560, 1440], "virtual": [2560, 0, 2560, 1440]}
SC_DUAL = {"monitors": 2, "primary": [2560, 1440], "virtual": [0, 0, 2560, 1440]}


def test_screen_changed():
    print("screen_changed：屏幕配置比对")
    check("同一套 → 空串", K.screen_changed(SC_2560, dict(SC_2560)), "")
    check("分辨率变了", K.screen_changed(SC_2560, SC_1280),
          "桌面范围从 2560x1440 变成了 1280x720")
    check("原点也变了要说明", K.screen_changed(SC_2560, SC_OFFSET),
          "桌面范围从 2560x1440 变成了 2560x1440（原点也从 (0,0) 变成了 (2560,0)）")
    check("只多了一块屏", K.screen_changed(SC_2560, SC_DUAL), "显示器数量从 1 个变成了 2 个")
    check("缺数据 → 空串", K.screen_changed({}, SC_2560), "")
    check("virtual 长度不对 → 空串", K.screen_changed({"virtual": [1, 2]}, SC_2560), "")
    check("screen_text 正常", K.screen_text(SC_2560), "1 显示器 / 虚拟桌面 2560x1440 / 主屏 2560x1440")
    check("screen_text 空 → -", K.screen_text({}), "-")


def test_plan_screen_fit():
    print("plan_screen_fit：按当前屏幕换算并保证不越界")
    orig = items(("a", 1000, 800))

    out, note = K.plan_screen_fit(orig, SC_2560, SC_2560)
    check("屏幕没变 → 数据原样", [(i["x"], i["y"]) for i in out], [(1000, 800)])
    check("屏幕没变 → 说明为空", note, "")
    check_true("屏幕没变 → 返回新列表（不共用同一个 list）", out is not orig and out == orig,
               "out is orig: %s" % (out is orig))

    out, note = K.plan_screen_fit(orig, {}, SC_2560)
    check("保存时没记屏幕 → 数据原样", [(i["x"], i["y"]) for i in out], [(1000, 800)])
    check("保存时没记屏幕 → 不编说明", note, "")

    # 2560x1440 → 1280x720：等比 50%
    out, note = K.plan_screen_fit(items(("a", 1000, 800)), SC_2560, SC_1280)
    check("缩放置位 50%", [(i["x"], i["y"]) for i in out], [(500, 400)])
    check_in("说明含缩放比例", "已按 50%×50% 缩放置位", note)
    check_in("说明含调整数量", "换算后 1 个图标的位置有调整", note)
    check_true("没越界就不提拉回", "拉回" not in note, note)

    # 越界必须拉回，而且一个图标只算一次（同一图标两轴都被夹只记 1）
    out, note = K.plan_screen_fit(items(("角", 3000, 3000), ("边", 3000, 10)), SC_2560, SC_1280)
    check("角落图标被夹进屏幕", (out[0]["x"], out[0]["y"]), (1279, 719))
    check("只横向越界的图标", (out[1]["x"], out[1]["y"]), (1279, 5))
    check_in("两轴都被夹只算 1 个", "其中 2 个原本会落到屏幕外", note)
    check_true("不重复计数", note.count("其中 2 个原本会落到屏幕外") == 1, note)

    # 原点右移但分辨率不变：只做平移，不提缩放
    out, note = K.plan_screen_fit(items(("a", 0, 0), ("b", 2559, 1439)), SC_2560, SC_OFFSET)
    check("原点右移 → 平移", [(i["x"], i["y"]) for i in out], [(2560, 0), (5119, 1439)])
    check_true("原点移动不越界", "拉回" not in note, note)

    # 只多了一块屏：坐标不变，但必须说明
    out, note = K.plan_screen_fit(items(("a", 300, 200)), SC_2560, SC_DUAL)
    check("只是多屏 → 坐标不动", [(i["x"], i["y"]) for i in out], [(300, 200)])
    check_in("只多屏要说明", "显示器数量由 1 变成 2", note)

    # 原条目不能被改（调用方还要用）
    src = items(("a", 1000, 800))
    K.plan_screen_fit(src, SC_2560, SC_1280)
    check("不改动传入的条目", (src[0]["x"], src[0]["y"]), (1000, 800))


# --------------------------------------------------------------------------
# 5. 外观（图标大小 / 排序 / 是否显示图标）
# --------------------------------------------------------------------------
def test_appearance():
    print("icon_size_name / appearance_drift / positions_by_name")
    check("256", K.icon_size_name(256), "超大图标(256)")
    check("96", K.icon_size_name(96), "大图标(96)")
    check("48", K.icon_size_name(48), "中等图标(48)")
    check("32", K.icon_size_name(32), "小图标(32)")
    check("64 对不上菜单项 → 说像素", K.icon_size_name(64), "64 像素")
    check("16 对不上菜单项 → 说像素", K.icon_size_name(16), "16 像素")
    check("0 → 未知", K.icon_size_name(0), "未知")
    check("None → 未知", K.icon_size_name(None), "未知")

    base = {"icon_size": 48, "sort": "00" * 20, "hide_icons": 0}
    check("外观完全一致 → 无差异", K.appearance_drift(base, dict(base)), [])
    check("缺数据 → 不猜（空）", K.appearance_drift({}, base), [])
    check("图标大小变了", K.appearance_drift(base, dict(base, icon_size=32)),
          ["图标大小：保存时是 中等图标(48)，现在是 小图标(32)。"])
    check("排序变了（不解释成具体哪种）",
          K.appearance_drift(base, dict(base, sort="ff" * 20)),
          ["排序方式：和保存时不一样了（它会决定图标的先后顺序）。"])
    check("显示桌面图标被关掉",
          K.appearance_drift(base, dict(base, hide_icons=1)),
          ["「显示桌面图标」：保存时是开启，现在是关闭。"])
    check("三项全变 → 三条", len(K.appearance_drift(base, {"icon_size": 32, "sort": "ff", "hide_icons": 1})), 3)
    check("值为 None → 跳过", K.appearance_drift({"icon_size": None, "sort": None, "hide_icons": None}, base), [])

    check("positions_by_name 同名收集", K.positions_by_name(
        items(("A", 1, 2), ("A", 3, 4), ("B", 5, 6))),
        {"A": [(1, 2), (3, 4)], "B": [(5, 6)]})
    check("positions_by_name 无名跳过",
          K.positions_by_name([{"name": "", "x": 1, "y": 1}]), {})


# --------------------------------------------------------------------------
# 6. 打码 —— 只测文字层，不涉及任何真实路径
# --------------------------------------------------------------------------
def test_redact():
    print("redact_text：本机路径打码（%s）" % K.REDACT_ENV)
    p1 = "C:" + "\\" + "Users" + "\\" + "SomeUser" + "\\" + "Desktop"
    p2 = "C:" + "\\" + "Users" + "\\" + "SomeUser" + "\\" + "AppData" + "\\" + "Roaming" + "\\" + "DesktopLayoutKeeper"
    unc = "\\" * 2 + "fileserver" + "\\" + "share" + "\\" + "secret.txt"

    os.environ[K.REDACT_ENV] = "off"
    check("off → 原样返回", K.redact_text(p1), p1)
    check("off → 日志行原样", K.redact_text("文件: " + p1 + " | 内核 1.3.0"),
          "文件: " + p1 + " | 内核 1.3.0")

    os.environ[K.REDACT_ENV] = "short"
    check("short → 只留最后一段", K.redact_text(p1), "\u2026\\Desktop")
    check("short → 数据目录那种长路径",
          K.redact_text(p2), "\u2026\\DesktopLayoutKeeper")
    check("short → UNC 也认", K.redact_text(unc), "\u2026\\secret.txt")
    check("short → 正斜杠也认",
          K.redact_text("C:/Users/SomeUser/x"), "\u2026\\x")
    line = K.redact_text("数据目录：" + p2)
    check_true("short → 结果里没有用户名", "SomeUser" not in line, line)
    check_true("short → 结果里没有盘符路径", ("C:" + "\\") not in line, line)
    line = K.redact_text("版本: v10（exe 编译于 2026-10-20）| 文件: " + p1 + ".exe | 内核 1.3.0")
    check_true("short → 文件名还在", "Desktop.exe" in line, line)
    check_true("short → 后面的字段没被吃掉", line.endswith("| 内核 1.3.0"), line)

    os.environ[K.REDACT_ENV] = "blocks"
    check("blocks → 等长纯色块", K.redact_text(p1), "\u25a0" * len(p1))
    line = K.redact_text("版本：v10｜文件：" + p1 + ".exe｜内核 1.3.0")
    check_true("blocks → 全角竖线也是终止符（不能吞掉后面的字段）",
               line.endswith("｜内核 1.3.0"), line)
    check_true("blocks → 路径确实被盖住", ("C:" + "\\") not in line, line)
    check("blocks → 块数等于路径长度", line.count("\u25a0"), len(p1) + len(".exe"))

    del os.environ[K.REDACT_ENV]
    check("未设置 → 等同 off", K.redact_text(p1), p1)
    check("模式解析：未设置", K.redaction_mode(), "")
    for raw, want in (("0", ""), ("off", ""), ("none", ""), ("blocks", "blocks"),
                      ("solid", "blocks"), ("1", "short"), ("short", "short"), ("YES", "short")):
        os.environ[K.REDACT_ENV] = raw
        check("模式解析：%s" % raw, K.redaction_mode(), want)
    del os.environ[K.REDACT_ENV]


def main():
    print("=" * 72)
    print("桌面布局管家 · 纯函数单测（不碰真实桌面）")
    print("  模块：%s" % K.__file__)
    print("  内核：%s" % K.APP_VERSION)
    print("=" * 72)
    for fn in (test_coordinate_offset, test_position_drift, test_build_restore_plan,
               test_screen_changed, test_plan_screen_fit, test_appearance, test_redact):
        fn()
        print()
    print("=" * 72)
    if FAILS:
        print("FAIL：%d 项断言失败（共 %d 项）" % (len(FAILS), COUNT))
        for f in FAILS:
            print("   - %s" % f)
        return 1
    print("ALL PASS：%d 项断言全部通过" % COUNT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
