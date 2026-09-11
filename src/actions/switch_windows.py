# -*- coding: utf-8 -*-
"""
simple_actions.py -- shift + W/A/S/D 快捷键动作实现 (业务逻辑层)。

shift+a / shift+d: 按“屏幕横向位置”切换当前最靠前的窗口。

    shift+a  把焦点切到当前窗口“左边相邻”的窗口; 已在最左则不做任何切换
    shift+d  把焦点切到当前窗口“右边相邻”的窗口; 已在最右则不做任何切换

“左右关系”按窗口左上角在 macOS CG 全局坐标系里的 X 坐标从左到右排序得到,
天然覆盖内建屏 + 外接屏的多显示器布局。例如: 内建屏的 chatgpt 在最左、外接屏
左半的 chrome 居中、外接屏右半的 vscode 在最右, 焦点在 vscode 时:
    按 shift+a -> 切到 chrome; 再按 -> 切到 chatgpt; 已在最左, 再按不切换
    按 shift+d -> 方向相反

本文件只保留与平台无关的业务逻辑: 窗口排序、相邻窗口选取、fullscreen_only
过滤决策与日志打印。所有 macOS 系统 API (Quartz/AppKit/Accessibility/兜底
子进程) 的导入与调用都集中在 macos/window_api.py; 配置读取统一走 paths.py
(配置路径权威) + config/config_loader.load_config (解析), 本文件不再自带
“向上查找文件 + 手写解析 yaml”的逻辑。

fullscreen_only (config/config.yml, 经 load_config 读取, 键写在 yaml 顶层):
当屏幕上存在“铺满全屏”的窗口 (原生全屏, 或 Split View 左右分屏铺满) 时, 忽略
其之下的其他普通窗口, shift+a/d 只在铺满的窗口之间切换 —— 避免从分屏层切到
底下的应用时被整屏切走、破坏分屏观感。缺省视为 True (文件缺失/解析失败/键
不存在都用缺省值); 每次按键都会重新读取, 改动即时生效, 无需重启监听。

诊断模式 (切换链路自检, 实现在 macos/window_api.run_diagnostics):
    python src/actions/simple_actions.py            # 只读检查
    python src/actions/simple_actions.py 目标pid    # 只读检查 + 实测切换到该应用
"""

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 导入引导: 主链路 (main.py 先 import paths) 时 paths.py 已把 src 注入
# sys.path; 但直接 `python src/actions/simple_actions.py` 运行诊断时
# sys.path[0] 是 src/actions, 找不到 src 下的 macos/config/paths, 这里补一次
# 同样的注入 (与 paths.py 等价, 幂等)。
# ---------------------------------------------------------------------------
_SRC_DIR = str(Path(__file__).resolve().parents[1])
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from config.config_loader import load_config
from macos.window_api import (
    QUARTZ_OK,
    focus_window,
    list_visible_windows,
    mark_fullscreen_windows,
    run_diagnostics,
)

# ---------------------------------------------------------------------------
# fullscreen_only 开关 (config/config.yml): 存在铺满全屏的窗口时, 是否忽略
# 其之下的普通窗口 (True=忽略, 只在铺满窗口间切换)。文件路径由 paths.CONFIG_FILE
# 权威定义, 解析统一用 config/config_loader.load_config; 每次按键重新读取,
# 改动即时生效; 文件缺失/解析失败/键不存在时用缺省值。
# ---------------------------------------------------------------------------
_FULLSCREEN_FILTER_DEFAULT = True


def _load_fullscreen_filter_enabled():
    """读取 fullscreen_only 开关; 缺省 True, 改 config 后即时生效、无需重启。

    经 config_loader.load_config (PyYAML) 读取 config/config.yml 顶层的
    fullscreen_only; True/False 之外的字符串 ("true"/"off" 等) 做一次兜底
    归一化, 其余情况一律回退缺省值。
    """
    try:
        data = load_config()
    except Exception:
        return _FULLSCREEN_FILTER_DEFAULT
    if not isinstance(data, dict):
        return _FULLSCREEN_FILTER_DEFAULT
    value = data.get('fullscreen_only', _FULLSCREEN_FILTER_DEFAULT)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):              # 手写 "true"/"off" 之类字符串的兜底
        flag = value.strip().lower()
        if flag in ('true', 'yes', 'on', '1'):
            return True
        if flag in ('false', 'no', 'off', '0'):
            return False
    return _FULLSCREEN_FILTER_DEFAULT


# ---------------------------------------------------------------------------
# shift + a / shift + d 的业务逻辑 (排序 / 相邻选取 / 过滤决策 / 日志)
# ---------------------------------------------------------------------------
def _switch_window(direction, label):
    """在按 X 坐标“从左到右”排序的可见窗口里, 把焦点移到当前窗口相邻的窗口。

    direction: -1 = 左边相邻窗口 (shift+a), +1 = 右边相邻窗口 (shift+d)。
    不循环: 已在最左再按 a、已在最右再按 d, 都不做任何切换。
    """
    if not QUARTZ_OK:
        print('[%s] 缺少 pyobjc (Quartz/AppKit)。pynput 在 macOS 上会自动安装它,'
              '请检查当前 Python 环境' % label)
        return

    windows = list_visible_windows()
    if len(windows) < 2:
        print('[%s] 屏幕上可见的普通窗口不足 2 个, 无需切换' % label)
        return

    # fullscreen_only (config/config.yml): 存在铺满全屏的窗口 (原生全屏或
    # Split View 分屏) 时, 忽略其之下的普通窗口, 只在铺满的窗口之间切换,
    # 避免从分屏层切到底下的应用时被整屏切走。
    filtered = False
    if _load_fullscreen_filter_enabled():
        mark_fullscreen_windows(windows)
        fullscreen_windows = [w for w in windows if w.get('fullscreen')]
        if fullscreen_windows and len(fullscreen_windows) < len(windows):
            windows = fullscreen_windows
            filtered = True
        if filtered and len(windows) < 2:
            print('[%s] 铺满全屏的窗口不足 2 个, 按 fullscreen_only 规则不切换' % label)
            return

    # “当前窗口” = z 序最靠前的窗口 (用户眼前的最上面一页)。
    # 注意: 不能用 NSWorkspace.frontmostApplication() —— 在没有 RunLoop 的脚本
    # 进程里它是陈旧快照, 不会随窗口切换刷新, 会导致当前窗口判定错乱
    # (日志表现为当前窗口一直是同一个、每次都切到同一个目标)。
    current = windows[0]           # list_visible_windows 保持 z 序 (前 -> 后)

    # 按窗口左上角 X 坐标从左到右排序 (跨内建屏/外接屏同样适用), X 相同再按 Y
    ordered = sorted(windows, key=lambda win: (win['x'], win['y'], win['number']))
    index = ordered.index(current)
    # 不循环: 直接取相邻索引, 越过两端即不切换 (替代原 % len 的循环写法)
    target_index = index + direction
    if target_index < 0 or target_index >= len(ordered):
        edge = '最左' if direction < 0 else '最右'
        print('[%s] %s (pid %s) 已是%s侧窗口, 不切换' % (
            label, current['owner'], current['pid'], edge))
        return
    target = ordered[target_index]

    if target is current:
        return

    arrow = '←' if direction < 0 else '→'
    current_tag = '[铺满] ' if current.get('fullscreen') else ''
    target_tag = '[铺满] ' if target.get('fullscreen') else ''
    print('[%s] %s%s (pid %s) %s %s%s (pid %s)  x=%.0f, y=%.0f, %.0fx%.0f' % (
        label, current_tag, current['owner'], current['pid'], arrow,
        target_tag, target['owner'], target['pid'], target['x'], target['y'],
        target['w'], target['h']))

    if not focus_window(target):
        print('[%s] 切换失败 (目标: %s, pid %s)。请在运行监听脚本的同一终端里执行 '
              '"python src/actions/simple_actions.py" 查看是哪一环失败; 最常见原因是 '
              '系统设置 -> 隐私与安全性 -> 辅助功能 没有授予运行脚本的终端/IDE' % (
                  label, target['owner'], target['pid']))


# ---------------------------------------------------------------------------
# 诊断模式入口: 直接运行本文件, 在与监听脚本相同的环境里自检切换链路
#   python src/actions/simple_actions.py            # 只读检查
#   python src/actions/simple_actions.py 目标pid    # 只读检查 + 实测切换到该应用
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    run_diagnostics(sys.argv[1] if len(sys.argv) > 1 else None)
