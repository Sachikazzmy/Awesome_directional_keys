# -*- coding: utf-8 -*-
"""
insert_mode.py -- 网页输入框的 vim 式 insert 模式 (业务逻辑层)。

    进入输入模式: 定位当前网页的输入框并聚焦 (等价于用鼠标点进输入框, 但
                  不移动鼠标、不会误点链接), 光标落进输入框, 可以正常打字
    退出输入模式: 取消聚焦 (等价于点回页面空白处), 回到“纯阅览”状态 ——
                  此时快捷键漏进页面的按键不会再落进任何输入框

痛点与语义 (对齐 vim): 切换窗口后, 网页经常自动把光标放进自己的输入框 (比如
搜索框), 这时按 shift+w/s 之类的快捷键, 漏进页面的 "W"/"S" 会直接打进输入框
造成误输入。希望像 vim 一样分两种模式: 平时切到窗口只阅览 (输入框不聚焦);
需要打字时按一下快捷键, 直接聚焦到网页输入框进入输入模式; 打完再按一下退回
阅览模式。

“当前窗口” = z 序最靠前的窗口 (list_visible_windows() 的 windows[0]), 与
switch_windows / page_turn 的判定一致 —— 不用 NSWorkspace.frontmostApplication()
(无 RunLoop 的脚本进程里它是陈旧快照)。聚焦/取消聚焦全部走 AX API (写
AXFocused + 读 AXFocusedUIElement 轮询复核, 每步“声称成功”都读回确认, 与
switch 的 z 序确认同风格), Chrome/Electron 系应用会先用 AXManualAccessibility
轻推开启网页无障碍树; 平台机制见 macos/window_api.py 区域 9/10。取消聚焦按
“AXFocused=False -> 焦点挪到网页区/窗口 -> 合成 Esc”逐级尝试并读回确认, 全部
失败时如实报告 (刻意不合成鼠标点击, 避免误触页面链接)。本模块不新增配置键。

“哪个输入框”: 按 AX 树序 (约等于页面从上到下的 DOM 顺序) 取第 index 个“可见”
输入框, 默认第一个; 序号由 enter_insert_mode 的 index 参数控制。虽然面向网页
输入框设计, 但对任何向辅助功能暴露 AXTextField/AXTextArea 的应用同样适用。

诊断模式 (自检手段, 参照 switch_windows/page_turn 的诊断骨架):
    python src/actions/insert_mode.py           # 只读检查, 不产生任何副作用
    python src/actions/insert_mode.py test      # 只读检查 + 实测: 3 秒后聚焦
                                                # 当前页面第一个输入框, 停 2 秒
                                                # 后取消聚焦, 再实测向下/向上
                                                # 滚动各一次 (需辅助功能授权;
                                                # 聚焦/取消用 AX 读回确认成败,
                                                # 滚动与 page_turn 一样靠肉眼)
"""

import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 导入引导: 主链路 (main.py 先 import paths) 时 paths.py 已把 src 注入
# sys.path; 但直接 `python src/actions/insert_mode.py` 运行诊断时
# sys.path[0] 是 src/actions, 找不到 src 下的 macos/config/paths, 这里补一次
# 同样的注入 (与 paths.py 等价, 幂等)。
# ---------------------------------------------------------------------------
_SRC_DIR = str(Path(__file__).resolve().parents[1])
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from macos.window_api import (
    AX_OK,
    QUARTZ_OK,
    blur_entry_field,
    entry_fields_info,
    focus_entry_field,
    focused_entry_state,
    insert_support_info,
    list_visible_windows,
    scroll_window,
)

# 实测滚动用的小步长 (只验证阅览模式下滚动链路连通, 固定值不读配置;
# 日常滚动的量仍由 page_turn 的 scroll_unit/scroll_amount 决定)
_SELF_TEST_SCROLL_PIXEL = 300


def _frontmost_window():
    """z 序最前的可见普通窗口 (前->后列表的第一个); 没有则返回 None。"""
    windows = list_visible_windows()
    return windows[0] if windows else None


# ---------------------------------------------------------------------------
# 对外入口 (给 keyboards/keyboard.py 回调层用): 进入 / 退出 / 实时切换
# ---------------------------------------------------------------------------
def enter_insert_mode(label='ctrl+shift+i', index=0):
    """进入输入模式: 聚焦当前窗口第 index 个可见输入框 (默认第一个)。

    label: 日志前缀, 形如 'ctrl+shift+i', 方便用户对照热键;
    index: 聚焦 AX 树序第几个可见输入框 (0 起), 默认第一个。
    成功返回 True (光标已落进输入框); 失败打印原因并返回 False (最常见的
    两种: 页面本身没有输入框 / 辅助功能未授权)。
    """
    target = _frontmost_window()
    if target is None:
        print('[%s] 当前 Space 没有可见的普通窗口, 无输入框可聚焦' % label)
        return False
    ok, detail = focus_entry_field(target, index=index)
    if ok:
        print('[%s] 进入输入模式 -> %s (pid %s): %s' % (
            label, target['owner'], target['pid'], detail))
    else:
        print('[%s] 进入输入模式失败 (%s)。请运行 "python src/actions/insert_mode.py" '
              '查看是哪一环失败; 常见原因: 页面本身没有输入框, 或 系统设置 -> '
              '隐私与安全性 -> 辅助功能 没有授予运行脚本的终端/IDE' % (label, detail))
    return ok


def exit_insert_mode(label='ctrl+shift+esc'):
    """退出输入模式: 取消聚焦当前输入框, 回到阅览模式 (快捷键不再误输入)。

    本就不在输入模式时是幂等成功 (会打印“本就处于阅览模式”的说明)。
    """
    target = _frontmost_window()
    if target is None:
        print('[%s] 当前 Space 没有可见的普通窗口, 无需退出输入模式' % label)
        return False
    ok, detail = blur_entry_field(target)
    if ok:
        print('[%s] 退出输入模式 -> %s (pid %s): %s' % (
            label, target['owner'], target['pid'], detail))
    else:
        print('[%s] 退出输入模式失败 (%s)。请运行 "python src/actions/insert_mode.py" '
              '查看是哪一环失败' % (label, detail))
    return ok


def toggle_insert_mode(label='ctrl+shift+i', index=0):
    """按当前网页的实时聚焦状态切换输入模式 (一个键同时当 i 和 Esc 用)。

    状态每次都从 AX 实时读取 (focused_entry_state, 不扫全树), 不维护可能
    过期的开关变量 —— 用户手动点进输入框后再按, 也会被正确判定为“退出”。
    """
    target = _frontmost_window()
    if target is None:
        print('[%s] 当前 Space 没有可见的普通窗口, 无输入框可切换' % label)
        return False
    state, detail = focused_entry_state(target)
    if state is True:
        print('[%s] 检测到输入框正聚焦 (%s) -> 退出输入模式' % (label, detail))
        return exit_insert_mode(label=label)
    if state is None:
        print('[%s] 聚焦状态读不到 (%s), 按进入输入模式处理' % (label, detail))
    return enter_insert_mode(label=label, index=index)


# ---------------------------------------------------------------------------
# 诊断模式入口: 直接运行本文件, 在与监听脚本相同的环境里自检聚焦链路
#   python src/actions/insert_mode.py           # 只读检查
#   python src/actions/insert_mode.py test      # 只读检查 + 实测聚焦/取消/滚动
# ---------------------------------------------------------------------------
def _self_check(live_test=False):
    """insert 模式链路逐环自检; live_test=True 时附加实测 (默认只读, 无副作用)。"""
    problems = []
    print('== insert_mode (输入框聚焦/取消) 诊断 ==')
    support = insert_support_info()
    print('[1] 模块可用性: Quartz=%s, AX API=%s' % (QUARTZ_OK, AX_OK))
    if not AX_OK:
        print('!! AX API 不可用 (缺少 pyobjc ApplicationServices), '
              '聚焦链路无法工作, 诊断终止')
        return
    print('[2] 辅助功能授权 AXIsProcessTrusted: %s' % support['ax_trusted'])
    if not support['ax_trusted']:
        problems.append('辅助功能未授权: 请在 系统设置 -> 隐私与安全性 -> 辅助功能 '
                        '勾选运行本脚本的终端/IDE (与 pynput 键盘监听共用同一份授权)')
    print('[3] Esc 键兜底 (CGEventCreateKeyboardEvent/CGEventPost): %s' % (
        '可用' if support['keyboard_event']
        else '不可用 (取消聚焦少了最后一级兜底, 前两级 AX 手段不受影响)'))
    if not support['keyboard_event']:
        problems.append('键盘事件 API 不可用 (pyobjc Quartz 过旧或缺失): '
                        '取消聚焦的 Esc 兜底失效')

    windows = list_visible_windows()
    print('[4] CGWindowList 实时窗口 (前->后, 共 %d 个):' % len(windows))
    for win in windows[:8]:
        print('      %s (pid %s, #%s, x=%.0f y=%.0f %.0fx%.0f)' % (
            win['owner'], win['pid'], win['number'],
            win['x'], win['y'], win['w'], win['h']))
    if not windows:
        print('!! 当前 Space 没有可见普通窗口, 聚焦无目标, 诊断终止')
        return
    target = windows[0]
    print('    聚焦目标 (z 序最前): %s (pid %s, #%s)' % (
        target['owner'], target['pid'], target['number']))

    print('[5] AX 扫描 (只读): 输入框清单与聚焦状态')
    info = entry_fields_info(target)
    print('      AX 窗口按 bounds 匹配: %s' % (
        '成功' if info['window_matched'] else '失败 (改用该应用第一个 AX 窗口兜底)'))
    if info['detail']:
        print('      说明: %s' % info['detail'])
    print('      遍历 AX 节点 %d 个%s, 网页区 (AXWebArea) %d 个' % (
        info['scanned_nodes'],
        '(达到扫描上限, 结果可能不全)' if info['truncated'] else '',
        info['web_areas']))
    fields = [f for f in info['fields'] if f['visible']]
    hidden_count = len(info['fields']) - len(fields)
    if fields:
        print('      可见文本输入框 %d 个 (树序≈页面从上到下; 另有 %d 个不可见已略过):'
              % (len(fields), hidden_count))
        for i, field in enumerate(fields[:8]):
            print('        [%d] [%s] %s %s (%.0f,%.0f %.0fx%.0f)' % (
                i + 1, '网页' if field.get('in_web') else '原生',
                field['role'], field['label'] or '(无标签)',
                field['x'], field['y'], field['w'], field['h']))
        if len(fields) > 8:
            print('        ... 其余 %d 个略' % (len(fields) - 8))
        print('      (进入输入模式优先聚焦 [网页] 输入框, 避免误聚焦浏览器地址栏)')
    else:
        print('      没有找到可见文本输入框 (页面可能确实没有输入框; 浏览器页面')
        print('      也可能尚未开启网页无障碍树 —— 实测进入时会自动轻推开启后重扫)')
    if info['focused'] is not None:
        print('      当前聚焦元素: %s %s (%.0f,%.0f %.0fx%.0f)' % (
            info['focused']['role'], info['focused']['label'] or '(无标签)',
            info['focused']['x'], info['focused']['y'],
            info['focused']['w'], info['focused']['h']))
    else:
        print('      当前聚焦元素: 读不到 (应用未上报聚焦状态, 不影响聚焦尝试)')
    if info['focused_is_entry'] is None:
        print('      聚焦状态判定: 无法判定 (聚焦元素属性读不到) —— 切换键会按“进入”处理')
    elif info['focused_is_entry']:
        print('      聚焦状态判定: 输入框聚焦中 (输入模式) —— 按退出键回到阅览')
    else:
        print('      聚焦状态判定: 没有聚焦输入框 (阅览模式) —— 按进入键聚焦第一个可见输入框')
    if not info['window_matched'] and support['ax_trusted']:
        problems.append('AX 窗口与 CGWindowList bounds 匹配失败 (已用该应用第一个 '
                        'AX 窗口兜底; 同应用开多窗口时可能定位到别的窗口)')

    print('[6] 将要做出的决策: 进入键 -> 聚焦 %s (pid %s) 第 1 个可见输入框 '
          '(当前可见 %d 个); 退出键 -> 取消聚焦回到阅览; 切换键 -> 按上面判定'
          '二选一' % (target['owner'], target['pid'], len(fields)))

    if not live_test:
        print('== 诊断结论 ==')
        if problems:
            for problem in problems:
                print('问题: %s' % problem)
            print('修复后重跑本命令; 链路通了以后可实测: '
                  'python src/actions/insert_mode.py test')
        else:
            print('只读检查全部正常; 实测请运行: '
                  'python src/actions/insert_mode.py test')
        return

    # ------------------------------------------------------------------
    # 以下为实测 (需显式加参数才执行): 聚焦/取消每步都用 AX 读回确认, 可以
    # 程序化判定成败; 滚动与 page_turn 一样, CGEventPost 没有应用是否消费
    # 的反馈, 需肉眼确认。实测会临时改变当前页面的聚焦状态, 建议把要测的
    # 网页放在最前再运行。
    # ------------------------------------------------------------------
    print('== 以下为实测 ==')
    if problems:
        print('!! 存在未解决的问题, 实测大概率失败; 建议先修复上面“诊断结论”里的问题')
    print('[7] 实测目标: %s (pid %s) —— 若这不是想测的网页, 先把它切到最前' % (
        target['owner'], target['pid']))
    print('    3 秒后聚焦它的第 1 个可见输入框 (进入输入模式), 光标应落进输入框')
    for count in (3, 2, 1):
        print('    %d...' % count)
        time.sleep(1)
    enter_ok, enter_detail = focus_entry_field(target, index=0)
    print('    进入: %s (%s)' % ('成功' if enter_ok else '失败', enter_detail))
    print('    停 2 秒, 此刻可直接打字验证输入模式是否生效')
    time.sleep(2)
    print('[8] 取消聚焦 (退出输入模式), 光标应离开输入框')
    exit_ok, exit_detail = blur_entry_field(target)
    print('    退出: %s (%s)' % ('成功' if exit_ok else '失败', exit_detail))
    if not enter_ok or not exit_ok:
        problems.append('实测聚焦/取消失败 (进入: %s; 退出: %s)' % (
            enter_detail, exit_detail))
    print('[9] 阅览模式下实测上下滚动: 向下滚 %d 像素, 1 秒后向上滚回 —— 肉眼'
          '确认页面滚动过、且没有字符被打进网页 (这正是阅览模式要保证的效果)'
          % _SELF_TEST_SCROLL_PIXEL)
    down_ok, down_path = scroll_window(target, -_SELF_TEST_SCROLL_PIXEL, True)
    time.sleep(1.0)
    up_ok, up_path = scroll_window(target, _SELF_TEST_SCROLL_PIXEL, True)
    print('    ↓ 向下滚动: %s (%s)' % ('已投递' if down_ok else '失败', down_path))
    print('    ↑ 向上滚回: %s (%s)' % ('已投递' if up_ok else '失败', up_path))
    if not down_ok or not up_ok:
        problems.append('实测滚动事件投递失败 (向下: %s; 向上: %s)' % (
            down_path, up_path))

    print('== 诊断结论 ==')
    if problems:
        for problem in problems:
            print('问题: %s' % problem)
    else:
        print('聚焦链路全部正常 (进入/退出均有 AX 读回确认), 可以接线使用; [9] 的')
        print('    滚动请肉眼确认; 若监听脚本还在运行旧代码, 请重启 main.py 后再试。')


if __name__ == '__main__':
    _self_check(live_test=len(sys.argv) > 1)
