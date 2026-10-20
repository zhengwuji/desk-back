# -*- coding: utf-8 -*-
"""把桌面两个开关复位成 Windows 默认：自动排列 = 开，对齐到网格 = 开。

做完实验/回归之后跑一下，免得桌面停在实验状态。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import desktop_layout_keeper as K  # noqa: E402

hs = K.find_desktop_listviews()
for h in hs:
    K.set_auto_arrange(h, True)
    K.set_snap_to_grid(h, True)
    K.refresh_view(h)
for h in hs:
    print("hwnd=0x%x" % h,
          "自动排列=", K.auto_arrange_enabled(h),
          " 对齐到网格=", K.snap_to_grid_enabled(h))
