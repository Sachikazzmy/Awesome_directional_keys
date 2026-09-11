# 该文件负责撰写对应actions/目录里实现对应功能的函数。


# ---------------------------------------------------------------------------
# 快捷键动作 (键盘监控部分调用下面这些函数, 签名保持不变)
# ---------------------------------------------------------------------------
from actions.switch_windows import _switch_window



def on_activate_w():
    print('shift + w')

def on_activate_a():
    """shift+a: 切到当前窗口左边相邻的窗口 (已在最左则不切换)。"""
    _switch_window(direction=-1, label='shift+a')

def on_activate_s():
    print('shift + s')

def on_activate_d():
    """shift+d: 切到当前窗口右边相邻的窗口 (已在最右则不切换)。"""
    _switch_window(direction=+1, label='shift+d')

