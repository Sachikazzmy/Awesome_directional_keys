# 该文件负责撰写对应actions/目录里实现对应功能的函数。


# ---------------------------------------------------------------------------
# 快捷键动作 (键盘监控部分调用下面这些函数, 签名保持不变)
# ---------------------------------------------------------------------------
from actions.switch_windows import _switch_window
from actions.page_turn import scroll_page
from actions.insert_mode import enter_insert_mode, exit_insert_mode, toggle_insert_mode



def on_activate_w():
    """shift+w: 当前页面向上滚动 (教程网页翻页阅读)。"""
    scroll_page(direction=-1, label='shift+w')

def on_activate_a():
    """shift+a: 切到当前窗口左边相邻的窗口 (已在最左则不切换)。"""
    _switch_window(direction=-1, label='shift+a')

def on_activate_s():
    """shift+s: 当前页面向下滚动。"""
    scroll_page(direction=+1, label='shift+s')

def on_activate_d():
    """shift+d: 切到当前窗口右边相邻的窗口 (已在最右则不切换)。"""
    _switch_window(direction=+1, label='shift+d')

def on_activate_i():  
    """shift+i: vim 的 i, 聚焦网页输入框进入输入模式"""
    enter_insert_mode(label='shift+i')
def on_activate_esc():  
    """shift+esc: vim 的 Esc, 退回阅览模式"""
    exit_insert_mode(label='shift+esc')
# 单键方案: on_activate_i 里改调 toggle_insert_mode(label='ctrl+shift+i')