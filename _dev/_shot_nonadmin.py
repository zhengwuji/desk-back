# -*- coding: utf-8 -*-
"""只用来截图：把 is_admin 打桩成 False，看"普通用户模式"那条橙色横幅长什么样。

不改任何系统设置，也不写布局数据；启动后由外部脚本截图并强杀。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import desktop_layout_keeper as K

K.is_admin = lambda: False          # 打桩：假装当前不是管理员
K.is_admin.__doc__ = "patched for screenshot"

sys.exit(K.main())
