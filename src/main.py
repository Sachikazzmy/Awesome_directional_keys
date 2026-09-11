import paths
from pynput import keyboard
from keyboards.keyboard import *
from config.config_loader import load_config

HOTKEYS = load_config()["hotkeys"]

# 此处应该在actions文件夹引用对应的操作函数，测试时以pynput内置函数为例

# 上文部分应在后续更新中去掉



with keyboard.GlobalHotKeys({
        '<shift>+w': on_activate_w,

        '<shift>+a': on_activate_a,

        '<shift>+s': on_activate_s,

        '<shift>+d': on_activate_d,

        }) as h:
    h.join()

