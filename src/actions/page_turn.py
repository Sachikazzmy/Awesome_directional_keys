# -*- coding: utf-8 -*-
"""
page_turn.py -- 当前页面上下滚动 (业务逻辑层), 面向教程类网页的翻页阅读。

    shift+w  当前页面向上滚 (查看之前的内容)
    shift+s  当前页面向下滚 (查看后续的内容)

“当前页面” = z 序最靠前的窗口 (list_visible_windows() 的 windows[0]), 与
switch_windows 的“当前窗口”判定一致 —— 不用 NSWorkspace.frontmostApplication()
(无 RunLoop 的脚本进程里它是陈旧快照)。滚动通过模拟鼠标滚轮事件实现
(CGEvent 滚轮事件, 平台细节见 macos/window_api.py 区域 8): 事件坐标设为当前
窗口中心后投入 HID 事件流, 窗口服务器按“事件坐标落在哪个窗口”命中路由 ——
滚动去向与物理光标停在哪里无关, 天然适用于浏览器 (教程网页)、PDF 阅读器、
编辑器等一切可滚动视图。

滚动手感 (丝滑滚动): 单发一笔大步长滚轮事件会“咯噔”一下跳过去, 本模块把每
次按键的滚动量摊成一小段高频事件流 —— 后台线程按 ~100Hz 节拍、以指数缓出
(起步快、临近目标优雅减速, 与触控板惯性同款曲线) 逐帧发出小步长滚轮事件,
500/300 像素约 0.4 秒优雅滑完。动画进行中再次按键会把新滚动量累加到同一段
动画上 (连续按压 = 连续顺滑的加速; 中途反向则平滑减速掉头), 不另起抖动的
新段。动画线程只调用 macos.window_api.scroll_window 这个纯 Python 接口,
入队即返回、不阻塞监听线程; 不需要平滑时可用配置一键关掉, 回到单发全额。

配置 (config/config.yml 顶层, 经 load_config 读取; 建议由 owner 增加这些键,
缺键/非法值回退缺省, 每次按键重新读取、改动即时生效):
    scroll_unit         'pixel' (按像素, 距离跨应用一致, 缺省) 或 'line'
                        (按“行”, 语义对齐传统滚轮)
    scroll_amount       对称的每次滚动量 (上下同值)
    scroll_amount_down  向下滚动量, 缺省 500 像素 (约小半屏)
    scroll_amount_up    向上滚动量, 缺省 300 像素
    scroll_smooth       True (缺省) 平滑动画; False 单发全额事件 (对比手感/排障)
    滚动量优先级: 方向键 > 对称键 > 内置缺省。

诊断模式 (自检手段, 参照 switch_windows 的诊断骨架裁剪, 平台层只读探测
复用 macos.window_api.scroll_support_info):
    python src/actions/page_turn.py           # 只读检查, 不产生任何副作用
    python src/actions/page_turn.py test      # 只读检查 + 实测: 当前窗口先向下
                                              # 再向上滚回原位 (需辅助功能授权)
只读检查覆盖: 模块可用性 -> 辅助功能授权 -> 滚轮事件 API 探测 -> CGWindowList
实时窗口与滚动目标 -> 光标位置参照 -> 配置读取结果与决策预览; 实测模式必须
显式加参数才执行, 输出里明确提示“以下为实测”。
"""

import math
import sys
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 导入引导: 主链路 (main.py 先 import paths) 时 paths.py 已把 src 注入
# sys.path; 但直接 `python src/actions/page_turn.py` 运行诊断时
# sys.path[0] 是 src/actions, 找不到 src 下的 macos/config/paths, 这里补一次
# 同样的注入 (与 paths.py 等价, 幂等)。
# ---------------------------------------------------------------------------
_SRC_DIR = str(Path(__file__).resolve().parents[1])
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from config.config_loader import load_config
from macos.window_api import (
    AX_OK,
    QUARTZ_OK,
    list_visible_windows,
    scroll_support_info,
    scroll_window,
)

# ---------------------------------------------------------------------------
# 滚动配置 (config/config.yml 顶层)。文件路径由 paths.CONFIG_FILE 权威定义,
# 解析统一用 config/config_loader.load_config; 每次按键重新读取, 缺键/非法值
# 回退缺省 (建议在 config.yml 增加这些键)。
# ---------------------------------------------------------------------------
_SCROLL_UNIT_DEFAULT = 'pixel'
_SCROLL_AMOUNT_DOWN_DEFAULT = 500     # 向下缺省滚动量 (约小半屏)
_SCROLL_AMOUNT_UP_DEFAULT = 300       # 向上缺省滚动量
_SCROLL_AMOUNT_MAX = 100000           # 防手滑: 单次滚动量上限
_SCROLL_UNIT_PIXEL = ('pixel', 'px', '像素')
_SCROLL_UNIT_LINE = ('line', '行')
_SCROLL_SMOOTH_DEFAULT = True
_SCROLL_SMOOTH_TRUE = ('true', 'yes', 'on', '1')
_SCROLL_SMOOTH_FALSE = ('false', 'no', 'off', '0')

# ---------------------------------------------------------------------------
# 滚动手感参数 (平滑动画的节拍与曲线; 想调观感改这两个常量即可):
#   每帧发出 剩余量×_ANIM_DECAY 的小步长, 剩余量按 (1-_ANIM_DECAY) 几何衰减,
#   500 像素约 0.40 秒、300 像素约 0.37 秒优雅滑完 —— 起步快、收尾优雅的缓出。
# ---------------------------------------------------------------------------
_ANIM_TICK_INTERVAL = 0.01            # 一帧 10ms (~100Hz, 与触控板事件频率同级)
_ANIM_DECAY = 0.16                    # 每帧消耗剩余量的比例 (指数缓出系数)
_ANIM_EPS = 0.5                       # 剩余量小于半个最小单位即收尾 (补齐残量)
_ANIM_MAX_TICKS = 300                 # 单段动画帧数上限 (约 3 秒), 兜底终止


def _norm_amount(value):
    """把配置里的滚动量归一成合法正整数; 非法/越界返回 None (由调用方回退)。"""
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return None
    if amount <= 0 or amount > _SCROLL_AMOUNT_MAX:
        return None
    return amount


def _load_scroll_settings():
    """读取滚动配置; 返回 (pixel_unit, amount_down, amount_up, smooth)。

    scroll_unit: 'pixel'/'px'/'像素' -> True, 'line'/'行' -> False, 其余回退
    缺省 'pixel'。滚动量优先级: scroll_amount_down / scroll_amount_up >
    scroll_amount (对称) > 内置缺省 (下 500 / 上 300); 非法、<=0 或超过上限
    一律视为未设置。scroll_smooth: 布尔或 'true'/'off' 之类字符串, 其余回退
    缺省 True。文件缺失/解析失败同样整体回退缺省; 每次按键重新读取, 改动
    即时生效。
    """
    pixel_unit = True
    smooth = _SCROLL_SMOOTH_DEFAULT
    defaults = (pixel_unit, _SCROLL_AMOUNT_DOWN_DEFAULT, _SCROLL_AMOUNT_UP_DEFAULT,
                smooth)
    try:
        data = load_config()
    except Exception:
        return defaults
    if not isinstance(data, dict):
        return defaults

    raw_unit = data.get('scroll_unit')
    if isinstance(raw_unit, str):
        unit = raw_unit.strip().lower()
        if unit in _SCROLL_UNIT_PIXEL:
            pixel_unit = True
        elif unit in _SCROLL_UNIT_LINE:
            pixel_unit = False

    symmetric = _norm_amount(data.get('scroll_amount'))
    down = _norm_amount(data.get('scroll_amount_down'))
    up = _norm_amount(data.get('scroll_amount_up'))
    amount_down = (down if down is not None else
                   symmetric if symmetric is not None else _SCROLL_AMOUNT_DOWN_DEFAULT)
    amount_up = (up if up is not None else
                 symmetric if symmetric is not None else _SCROLL_AMOUNT_UP_DEFAULT)

    raw_smooth = data.get('scroll_smooth')
    if isinstance(raw_smooth, bool):
        smooth = raw_smooth
    elif isinstance(raw_smooth, str):
        flag = raw_smooth.strip().lower()
        if flag in _SCROLL_SMOOTH_TRUE:
            smooth = True
        elif flag in _SCROLL_SMOOTH_FALSE:
            smooth = False
    return pixel_unit, amount_down, amount_up, smooth


def _estimate_duration(amount):
    """平滑动画的预计时长 (秒): 指数缓出衰减到 _ANIM_EPS 以内所需帧数×帧间隔。"""
    try:
        ticks = math.log(amount / _ANIM_EPS) / -math.log(1.0 - _ANIM_DECAY)
    except Exception:
        return 0.0
    return max(0.0, ticks) * _ANIM_TICK_INTERVAL


# ---------------------------------------------------------------------------
# 平滑滚动动画: 把一次按键的滚动量摊成高频小步长的滚轮事件流
# ---------------------------------------------------------------------------
class _SmoothScroller(object):
    """后台动画线程: 按 ~100Hz 节拍、指数缓出地消费“剩余滚动量”。

    手感设计:
        每帧发出 剩余量×_ANIM_DECAY 的小步长 —— 起步快、临近目标优雅减速,
        与触控板惯性同款曲线; 取整残量在收尾帧一次补齐, 总量分毫不差。
        动画进行中同一窗口再次按键: 新滚动量直接累加到本段 (连续按压 =
        连续顺滑的加速); 中途反向: 剩余量变号, 动画平滑减速掉头;
        换了目标窗口: 无缝切到新目标 (旧段剩余部分随收尾帧了结)。
    线程说明: daemon 线程在第一次入队时惰性启动, 平时阻塞等待、来活即干;
        内部锁只保护纯 Python 账本 (total/emitted), 事件投递走
        macos.window_api.scroll_window 的纯 Python 接口, 不持有平台对象。
        scroll_page 入队即返回, 不阻塞 pynput 监听线程。
    """

    def __init__(self):
        self._cond = threading.Condition()
        self._running = False       # 是否有一段动画在进行
        self._window = None         # 当前段目标窗口 dict (纯 Python 数据)
        self._pixel_unit = True
        self._label = ''
        self._total = 0.0           # 本段要滚的总量 (含符号, 浮点记账)
        self._emitted = 0.0         # 本段已发出的累计量 (事件按取整差值发)
        self._thread = None

    # -- 对外接口: scroll_page 调用, 入队即返回 ----------------------------
    def enqueue(self, window, delta, pixel_unit, label):
        """记入一笔滚动量; 动画中同窗口则累加续滑, 异窗口则无缝切换目标。"""
        with self._cond:
            if self._running and self._same_target(window):
                self._total += float(delta)          # 丝滑续上, 不另起新段
                if label:
                    self._label = label
                return
            self._window = window
            self._pixel_unit = pixel_unit
            self._label = label
            self._total = float(delta)
            self._emitted = 0.0
            first = not self._running
            self._running = True
            self._ensure_thread_locked()
            if first:
                self._cond.notify()

    def idle(self):
        """当前是否没有动画在进行 (诊断/实测等待用)。"""
        with self._cond:
            return not self._running

    # -- 内部实现 -----------------------------------------------------------
    def _same_target(self, window):
        if self._window is None or window is None:
            return False
        return (window.get('pid') == self._window.get('pid')
                and window.get('number') == self._window.get('number'))

    def _ensure_thread_locked(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._worker, name='page-turn-smooth-scroll', daemon=True)
            self._thread.start()

    def _worker(self):
        while True:
            with self._cond:
                while not self._running:
                    self._cond.wait()
            self._run_segment()

    def _run_segment(self):
        failure = None
        tick = 0
        next_tick = time.monotonic()
        while True:
            with self._cond:
                remaining = self._total - self._emitted
                if abs(remaining) < _ANIM_EPS or tick >= _ANIM_MAX_TICKS:
                    # 收尾: 补齐取整残量 (最多差 1 个最小单位), 总量分毫不差
                    final = int(round(self._total)) - int(round(self._emitted))
                    if final:
                        scroll_window(self._window, final, self._pixel_unit)
                    self._running = False
                    self._total = 0.0
                    self._emitted = 0.0
                    return
                window = self._window
                pixel_unit = self._pixel_unit
                step = remaining * _ANIM_DECAY       # 指数缓出: 越近越慢
                prev = int(round(self._emitted))
                self._emitted += step
                want = int(round(self._emitted)) - prev
            if want:
                ok, path = scroll_window(window, want, pixel_unit)
                if not ok:
                    failure = path
                    with self._cond:
                        self._running = False        # 放弃本段剩余量, 防逐帧刷错
                        self._total = 0.0
                        self._emitted = 0.0
                    break
            tick += 1
            next_tick += _ANIM_TICK_INTERVAL
            delay = next_tick - time.monotonic()     # 绝对节拍, 不累积漂移
            if delay > 0:
                time.sleep(delay)
        if failure is not None:
            print('[%s] 平滑滚动中断 (%s)。请运行 "python src/actions/page_turn.py" '
                  '自检; 最常见原因是 系统设置 -> 隐私与安全性 -> 辅助功能 没有'
                  '授予运行脚本的终端/IDE' % (self._label, failure))


_SCROLLER = _SmoothScroller()


# ---------------------------------------------------------------------------
# shift + w / shift + s 的业务逻辑 (目标选取 / 方向换算 / 配置决策 / 日志)
# ---------------------------------------------------------------------------
def scroll_page(direction, label='shift+w'):
    """当前页面 (z 序最前的窗口) 上下滚动, 面向教程类网页的翻页阅读。

    direction: -1 = 向上滚 (查看之前内容), +1 = 向下滚 (查看后续内容);
    label: 日志前缀, 形如 'shift+w' / 'shift+s', 方便用户对照热键。

    默认把滚动量摊成 ~100Hz 小步长的指数缓出事件流 (后台线程, 入队即返回;
    动画中再按键会丝滑续加), scroll_smooth: False 时退回单发全额事件。
    """
    direction = -1 if direction < 0 else 1
    if not QUARTZ_OK:
        print('[%s] 缺少 pyobjc (Quartz)。pynput 在 macOS 上会自动安装它, '
              '请检查当前 Python 环境' % label)
        return
    windows = list_visible_windows()
    if not windows:
        print('[%s] 当前 Space 没有可见的普通窗口, 无页面可滚动' % label)
        return

    current = windows[0]           # list_visible_windows 保持 z 序 (前 -> 后)
    pixel_unit, amount_down, amount_up, smooth = _load_scroll_settings()
    amount = amount_up if direction < 0 else amount_down
    # 滚轮事件符号约定 (见 window_api 区域 8): 正数 = 向上滚, 负数 = 向下滚
    delta = amount if direction < 0 else -amount
    unit_name = '像素' if pixel_unit else '行'
    arrow = '↑' if direction < 0 else '↓'
    print('[%s] %s (pid %s) %s 滚动 %s %s (%s)' % (
        label, current['owner'], current['pid'], arrow, amount, unit_name,
        '平滑' if smooth else '单发'))

    if smooth:
        _SCROLLER.enqueue(current, delta, pixel_unit, label)
        return
    delivered, path = scroll_window(current, delta, pixel_unit)
    if not delivered:
        print('[%s] 滚动事件投递失败 (%s)。请运行 "python src/actions/page_turn.py" '
              '查看是哪一环失败; 最常见原因是 系统设置 -> 隐私与安全性 -> 辅助功能 '
              '没有授予运行脚本的终端/IDE' % (label, path))


# ---------------------------------------------------------------------------
# 诊断模式入口: 直接运行本文件, 在与监听脚本相同的环境里自检滚动链路
#   python src/actions/page_turn.py           # 只读检查
#   python src/actions/page_turn.py test      # 只读检查 + 实测滚动
# ---------------------------------------------------------------------------
def _wait_scroller_idle(timeout=5.0):
    """等待平滑动画结束 (实测自检用); 超时返回 False。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _SCROLLER.idle():
            return True
        time.sleep(0.05)
    return _SCROLLER.idle()


def _self_check(live_test=False):
    """滚动链路逐环自检; live_test=True 时附加实测 (默认只读, 无副作用)。"""
    problems = []
    print('== page_turn (页面滚动) 诊断 ==')
    print('[1] 模块可用性: Quartz=%s, AX API=%s' % (QUARTZ_OK, AX_OK))
    if not QUARTZ_OK:
        print('!! Quartz 不可用 (缺少 pyobjc), 滚动链路无法工作, 诊断终止')
        return
    support = scroll_support_info()

    print('[2] 辅助功能授权 AXIsProcessTrusted: %s' % support['ax_trusted'])
    if not support['ax_trusted']:
        problems.append('辅助功能未授权: 请在 系统设置 -> 隐私与安全性 -> 辅助功能 '
                        '勾选运行本脚本的终端/IDE (与 pynput 键盘监听共用同一份授权)')

    print('[3] 滚轮事件 API: 创建事件=%s, 投递 HID 事件流=%s' % (
        support['create_event'], support['post_hid']))
    if not support['create_event'] or not support['post_hid']:
        problems.append('滚轮事件的创建/投递 API 不可用 (pyobjc Quartz 过旧或缺失)')

    windows = list_visible_windows()
    print('[4] CGWindowList 实时窗口 (前->后, 共 %d 个):' % len(windows))
    for win in windows[:8]:
        print('      %s (pid %s, #%s, x=%.0f y=%.0f %.0fx%.0f)' % (
            win['owner'], win['pid'], win['number'],
            win['x'], win['y'], win['w'], win['h']))
    if not windows:
        print('!! 当前 Space 没有可见普通窗口, 滚动无目标, 诊断终止')
        return
    target = windows[0]
    print('    滚动目标 (z 序最前): %s (pid %s, #%s)' % (
        target['owner'], target['pid'], target['number']))

    cursor = support['cursor']
    if cursor is None:
        print('[5] 鼠标光标位置: 未知 (不影响功能)')
    else:
        print('[5] 鼠标光标位置: (%.0f, %.0f) —— 仅供参照; 滚动去向由事件坐标'
              '命中路由决定, 与光标停在哪里无关' % cursor)

    pixel_unit, amount_down, amount_up, smooth = _load_scroll_settings()
    unit_name = '像素' if pixel_unit else '行'
    print('[6] 滚动配置: scroll_unit=%s (%s), 向下 %s / 向上 %s, 平滑=%s' % (
        'pixel' if pixel_unit else 'line', unit_name,
        amount_down, amount_up, '开' if smooth else '关'))
    print('    将要做出的决策: 按 shift+s -> %s (pid %s) 向下滚动 %s %s%s; '
          '按 shift+w -> 向上滚动 %s %s%s' % (
              target['owner'], target['pid'],
              amount_down, unit_name,
              ' (平滑约 %.1f 秒)' % _estimate_duration(amount_down) if smooth else '',
              amount_up, unit_name,
              ' (平滑约 %.1f 秒)' % _estimate_duration(amount_up) if smooth else ''))

    if not live_test:
        print('== 诊断结论 ==')
        if problems:
            for problem in problems:
                print('问题: %s' % problem)
            print('修复后重跑本命令; 链路通了以后可实测: '
                  'python src/actions/page_turn.py test')
        else:
            print('只读检查全部正常; 实测请运行: '
                  'python src/actions/page_turn.py test')
        return

    # ------------------------------------------------------------------
    # 以下为实测 (需显式加参数才执行): 当前窗口先向下滚, 停 1.5 秒再向上滚回
    # 原位 (上下用同一滚动量, 保证复原)。肉眼确认页面丝滑滚过即链路通畅;
    # 事件投递本身无法用返回值确认应用是否消费 (见 window_api 区域 8 边界)。
    # ------------------------------------------------------------------
    print('== 以下为实测 ==')
    if problems:
        print('!! 存在未解决的问题, 实测大概率失败; 建议先修复上面“诊断结论”里的问题')
    amount = amount_down
    mode = '平滑' if smooth else '单发'
    print('[7] 3 秒后把当前窗口 %s (pid %s) %s向下滚动 %s %s%s, 停 1.5 秒后滚回' % (
        target['owner'], target['pid'], mode, amount, unit_name,
        ' (约 %.1f 秒)' % _estimate_duration(amount) if smooth else ''))
    for count in (3, 2, 1):
        print('    %d...' % count)
        time.sleep(1)
    if smooth:
        _SCROLLER.enqueue(target, -amount, pixel_unit, 'page_turn实测')
        down_ok = _wait_scroller_idle()
        print('    ↓ 向下滚动: %s' % ('动画完整结束' if down_ok else '超时未结束'))
        time.sleep(1.5)
        _SCROLLER.enqueue(target, amount, pixel_unit, 'page_turn实测')
        up_ok = _wait_scroller_idle()
        print('    ↑ 向上滚回: %s' % ('动画完整结束' if up_ok else '超时未结束'))
        if not (down_ok and up_ok):
            problems.append('平滑动画未在预期时间内结束 (向下=%s, 向上=%s)' % (
                down_ok, up_ok))
    else:
        down_ok, down_path = scroll_window(target, -amount, pixel_unit)
        print('    ↓ 向下滚动: %s (%s)' % ('已投递' if down_ok else '失败', down_path))
        time.sleep(1.5)
        up_ok, up_path = scroll_window(target, amount, pixel_unit)
        print('    ↑ 向上滚回: %s (%s)' % ('已投递' if up_ok else '失败', up_path))
        if not (down_ok and up_ok):
            problems.append('实测滚动事件投递失败 (向下: %s; 向上: %s)' % (
                down_path, up_path))
    if down_ok and up_ok:
        print('    两段动画都完整结束 —— 请肉眼确认页面丝滑滚动过再弹回; 若页面纹丝')
        print('    不动, 多为该应用不响应合成滚轮事件 (少数应用如此), 或 [2] 授权未生效')

    print('== 诊断结论 ==')
    if problems:
        for problem in problems:
            print('问题: %s' % problem)
    else:
        print('滚动链路全部正常, 可以直接使用 shift+w/s; 若监听脚本还在运行旧代码, '
              '请重启 main.py 后再试。')


if __name__ == '__main__':
    _self_check(live_test=len(sys.argv) > 1)
