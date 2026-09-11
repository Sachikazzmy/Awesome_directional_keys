# -*- coding: utf-8 -*-
"""
window_api.py -- macos/ 平台层唯一文件 (单文件精简版): 本项目所有 macOS 系统
API 的导入与调用都集中在这里, 用分隔线划分区域职能。

上层 actions/simple_actions.py 只使用本文件暴露的纯 Python 接口, 不再出现任何
pyobjc / 子进程细节:
    QUARTZ_OK / AX_OK              -- pyobjc 子模块可用性标志 (True/False)
    list_visible_windows()         -- 当前 Space 内可见普通窗口 (z 序, 前 -> 后)
    mark_fullscreen_windows(wins)  -- 就地为窗口列表标记 fullscreen: True/False
    focus_window(target)           -- 把目标窗口带到最前, 并用 z 序实时确认生效
    run_diagnostics(target_pid)    -- 诊断模式: 逐环自检切换链路 (排障用)

依赖: pynput 在 macOS 上自动安装 pyobjc (Quartz/AppKit/ApplicationServices),
本文件直接复用, 不引入新的第三方依赖。

用到的 macOS API 总览 (各区域分隔线下有更具体的说明):
    1) Quartz / CoreGraphics Window Services
       CGWindowListCopyWindowInfo 一次拿到当前 Space 全部可见窗口的 pid、应用名、
       全局坐标 bounds, 且结果自带“从前到后”的 z 序 -> 用于确定“当前窗口”。
       注意: 不能用 NSWorkspace.frontmostApplication() —— 无 RunLoop 的脚本
       进程里它是陈旧快照, 不会随窗口切换刷新; CGWindowList 的 z 序才是窗口
       服务器的实时状态。CGGetActiveDisplayList + CGDisplayBounds 提供各显示器
       bounds, 用于“几乎铺满某块显示器”的兜底判定 (多显示器坐标可能为负)。
    2) ApplicationServices (HIServices) -- Accessibility API
       AXUIElementCreateApplication / AXUIElementCopyAttributeValue /
       AXUIElementSetAttributeValue / AXUIElementPerformAction /
       AXValueGetValue / AXIsProcessTrusted: 在目标应用内按位置/大小匹配到具体
       窗口后执行 AXRaise, 再置 AXFrontmost —— 同一应用开了多个窗口 (如 Chrome
       多窗口) 时也能准确抬升目标窗口, 而不是只激活应用。AXFullScreen /
       AXSubrole 用于铺满全屏判定。
       权限: 与 pynput 键盘监听相同, 需在 系统设置 -> 隐私与安全性 -> 辅助功能
       里给运行脚本的终端/IDE 授权 (pynput 已授权则复用同一份授权)。
    3) AppKit (Cocoa)
       NSRunningApplication.activateWithOptions_(
       NSApplicationActivateIgnoringOtherApps): 最后一级激活兜底。它依赖 RunLoop
       维护运行列表, 无 RunLoop 的脚本进程里经常查不到目标, 只作兜底不作主链路。
    4) CoreFoundation
       kCFBooleanTrue: 写 AXFrontmost 属性时使用的 CFBoolean 常量。
    5) 兜底子进程
       osascript + System Events (Apple 事件): 置前应用并抬升匹配窗口, 也可查
       bundle id (需要“自动化”权限, 与辅助功能相互独立, 失败不影响主链路)。
       open -b <bundle-id> / -a <应用名>: 走 LaunchServices 激活, 不需要 TCC 授权。

失败兜底链 (focus_window): AX API -> osascript (System Events) -> open
(LaunchServices) -> NSRunningApplication; 每级“声称成功”后都用 z 序实时确认
目标窗口真的到了最前, 未确认则自动换下一级。

已知边界 (有意保持简单): 只在当前 Space 内切换; 已最小化的窗口不参与切换
(OnScreenOnly 不会返回它们); 切到全屏窗口时系统会自动做 Space 切换。
不同 pyobjc 版本的绑定差异 (AX 函数是否保留 out 参数、AXValue 的桥接形式等)
都在本文件内部的兼容封装里消化, 不外泄到上层。
"""

import os
import subprocess
import time

# ===========================================================================
# 区域 1: macOS API 导入与可用性探测
# ---------------------------------------------------------------------------
# pyobjc 按框架拆分绑定:
#   Quartz              -> CoreGraphics 窗口服务 (CGWindowList / CGDisplay)
#   AppKit              -> Cocoa (NSRunningApplication 激活兜底)
#   ApplicationServices -> HIServices 的 Accessibility API (窗口枚举/置前)
#   CoreFoundation      -> kCFBooleanTrue (AXFrontmost 属性写入常量)
# 任一导入失败都只降级不崩溃: 对应 *_OK 置 False, 上层据此提示并跳过相应链路,
# 诊断模式也依赖这两个标志报告真实可用性。
# ===========================================================================

try:
    import Quartz                                          # pyobjc-framework-Quartz
    from AppKit import NSRunningApplication                # pyobjc-framework-Cocoa
    QUARTZ_OK = True
except Exception:                                          # pragma: no cover
    Quartz = NSRunningApplication = None
    QUARTZ_OK = False

try:
    from AppKit import NSApplicationActivateIgnoringOtherApps
except Exception:                                          # pragma: no cover
    NSApplicationActivateIgnoringOtherApps = 1 << 1        # AppKit 稳定常量值

try:
    # Accessibility API (HIServices), 随 pyobjc-framework-ApplicationServices 提供
    from ApplicationServices import (
        AXUIElementCreateApplication,
        AXUIElementCopyAttributeValue,
        AXUIElementSetAttributeValue,
        AXUIElementPerformAction,
        AXValueGetValue,
        AXIsProcessTrusted,
    )
    from CoreFoundation import kCFBooleanTrue
    AX_OK = True
except Exception:                                          # pragma: no cover
    AX_OK = False

__all__ = [
    'QUARTZ_OK', 'AX_OK',
    'list_visible_windows', 'mark_fullscreen_windows',
    'focus_window', 'run_diagnostics',
]


# ===========================================================================
# 区域 2: AX 属性常量与窗口过滤参数
# ---------------------------------------------------------------------------
# AX 常量直接写成字符串/数值, 规避不同 pyobjc 版本导出差异:
#   AXWindows / AXPosition / AXSize / AXFrontmost -- AX 属性名
#       (AXFrontmost: 置 True 把应用整体置前; AXRaise 只在应用内部排序窗口)
#   AXRaise       -- 可以对 AX 窗口执行的动作名
#   kAXValueCGPointType(1) / kAXValueCGSizeType(2) -- AXValueGetValue 类型常量,
#       用于把 AXPosition/AXSize 的 AXValue 拆成数值对
#   AXFullScreen / AXSubrole == AXFullScreen -- 铺满全屏判定 (原生全屏与
#       Split View 左右分屏的两半都返回 True)
# 过滤参数: _MIN_WINDOW_* 排除输入法候选框之类的迷你窗口和几乎透明的辅助窗口;
# _AX_MATCH_TOLERANCE: CGWindowBounds 与 AX 窗口位置/大小匹配的容差 (pt)。
# ===========================================================================

_AX_WINDOWS_ATTR = 'AXWindows'
_AX_POSITION_ATTR = 'AXPosition'
_AX_SIZE_ATTR = 'AXSize'
_AX_FRONTMOST_ATTR = 'AXFrontmost'
_AX_RAISE_ACTION = 'AXRaise'
_AX_VALUE_POINT_TYPE = 1              # kAXValueCGPointType
_AX_VALUE_SIZE_TYPE = 2               # kAXValueCGSizeType

_AX_FULLSCREEN_ATTR = 'AXFullScreen'      # 窗口是否处于全屏/分屏(Split View)状态
_AX_SUBROLE_ATTR = 'AXSubrole'
_AX_SUBROLE_FULLSCREEN = 'AXFullScreen'   # 全屏窗口的 subrole (兜底判定)

_MIN_WINDOW_WIDTH = 120
_MIN_WINDOW_HEIGHT = 80
_MIN_WINDOW_ALPHA = 0.05
_AX_MATCH_TOLERANCE = 24.0


# ===========================================================================
# 区域 3: Quartz -- 窗口列表 / z 序确认 / 显示器 bounds
# ---------------------------------------------------------------------------
# 系统 API:
#   Quartz.CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly |
#       kCGWindowListExcludeDesktopElements, kCGNullWindowID)
#       一次调用拿到当前 Space 内全部可见窗口的 pid/应用名/全局坐标 bounds,
#       返回顺序就是窗口服务器的实时 z 序 (前 -> 后), windows[0] 即“当前窗口”;
#       kCGWindowLayer == 0 只保留普通窗口, 排除菜单栏/Dock/悬浮提示等。
#       它是无 RunLoop 脚本进程里唯一可靠的“当前窗口”来源。
#   Quartz.CGGetActiveDisplayList + Quartz.CGDisplayBounds
#       所有活动显示器的 bounds (CG 全局坐标系, 原点在主显示器左上角,
#       其他显示器按系统排列可能出现负坐标), 供“窗口几乎铺满某块显示器”
#       的兜底判定。
# ===========================================================================

def list_visible_windows():
    """收集当前 Space 内所有可见的普通窗口, 按 CGWindowList 原生 z 序(前->后)返回。

    元素字段: pid / owner / number / x / y / w / h
    坐标为 CG 全局坐标系 (pt): 主显示器左上角为原点, 其他显示器按系统设置的
    排列可能出现负坐标, 因此天然覆盖多显示器。
    """
    if not QUARTZ_OK:
        return []
    options = (Quartz.kCGWindowListOptionOnScreenOnly
               | Quartz.kCGWindowListExcludeDesktopElements)
    raw = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID)
    if not raw:
        return []
    own_pid = os.getpid()
    windows = []
    for item in raw:
        if int(item.get(Quartz.kCGWindowLayer, 1)) != 0:
            continue        # 只要 layer 0 的普通窗口, 排除菜单栏/Dock/悬浮提示等
        pid = item.get(Quartz.kCGWindowOwnerPID)
        if not pid or int(pid) == own_pid:
            continue        # 排除键盘监听进程自己
        bounds = item.get(Quartz.kCGWindowBounds) or {}
        width = float(bounds.get('Width', 0.0))
        height = float(bounds.get('Height', 0.0))
        if width < _MIN_WINDOW_WIDTH or height < _MIN_WINDOW_HEIGHT:
            continue        # 排除输入法候选框之类的迷你窗口
        alpha = item.get(Quartz.kCGWindowAlpha)
        if alpha is not None and float(alpha) < _MIN_WINDOW_ALPHA:
            continue        # 排除几乎透明的辅助窗口
        windows.append({
            'pid': int(pid),
            'owner': item.get(Quartz.kCGWindowOwnerName) or 'pid %s' % pid,
            'number': int(item.get(Quartz.kCGWindowNumber, 0) or 0),
            'x': float(bounds.get('X', 0.0)),
            'y': float(bounds.get('Y', 0.0)),
            'w': width,
            'h': height,
            'fullscreen': False,    # 是否铺满全屏, 由 mark_fullscreen_windows 标记
        })
    return windows


def _display_bounds_list():
    """所有活动显示器的 bounds (CG 全局坐标系); 拿不到时返回空列表。"""
    if not QUARTZ_OK:
        return []
    try:
        raw = Quartz.CGGetActiveDisplayList(16, None, None)
        display_ids = []
        if isinstance(raw, (list, tuple)):
            inner = [part for part in raw if isinstance(part, (list, tuple))]
            if inner:
                for part in inner:
                    display_ids.extend(int(d) for d in part)
            else:
                display_ids.extend(int(d) for d in raw)
    except Exception:
        return []
    bounds_list = []
    for display_id in display_ids:
        try:
            display_bounds = Quartz.CGDisplayBounds(display_id)
            bounds_list.append((float(display_bounds.origin.x),
                                float(display_bounds.origin.y),
                                float(display_bounds.size.width),
                                float(display_bounds.size.height)))
        except Exception:
            continue
    return bounds_list


def _is_window_top(target):
    """检查目标窗口现在是否就是全屏幕 z 序最靠前的窗口 (实时查 CGWindowList)。"""
    if not QUARTZ_OK:
        return False
    options = (Quartz.kCGWindowListOptionOnScreenOnly
               | Quartz.kCGWindowListExcludeDesktopElements)
    raw = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID)
    for item in raw or []:
        if int(item.get(Quartz.kCGWindowLayer, 1)) != 0:
            continue
        # 第一个 layer 0 窗口就是 z 序最靠前的窗口
        return (int(item.get(Quartz.kCGWindowOwnerPID, -1)) == int(target['pid'])
                and int(item.get(Quartz.kCGWindowNumber, -2)) == int(target['number']))
    return False


def _wait_until_window_top(target, timeout=0.6, interval=0.05):
    """轮询确认目标窗口已成为 z 序最前 (实时 CGWindowList, 无 RunLoop 依赖)。"""
    deadline = time.monotonic() + timeout
    while True:
        if _is_window_top(target):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


# ===========================================================================
# 区域 4: Accessibility (AX) -- 属性读写 / 窗口枚举 / 全屏判定 / 抬升置前
# ---------------------------------------------------------------------------
# 系统 API (ApplicationServices / HIServices, 需“辅助功能”授权):
#   AXUIElementCreateApplication(pid)      -- 创建目标应用的 AX 元素
#   AXUIElementCopyAttributeValue          -- 读 AXWindows/AXPosition/AXSize/
#                                             AXFullScreen/AXSubrole 等属性
#   AXUIElementSetAttributeValue           -- 写 AXFrontmost 把应用整体置前
#   AXUIElementPerformAction(w, "AXRaise") -- 抬升应用内指定窗口: 同一应用开了
#       多个窗口 (如 Chrome 多窗口) 时也能准确抬升目标窗口, 而不是只激活应用
#   AXValueGetValue                        -- 把 AXValue 拆成数值对 (兼容封装见下)
#   AXIsProcessTrusted                     -- 查询“辅助功能”授权状态 (诊断用)
# 铺满判定优先级: AXFullScreen 明确给出值 -> 直接信任 (含 False);
# 兜底 AXSubrole == AXFullScreen; 再兜底“bounds 几乎覆盖某块显示器”的纯几何判定
# (普通最大化窗口会被菜单栏挡住差出几十 pt, 不会误判)。
# ===========================================================================

def _ax_copy_attribute(element, attribute):
    """AXUIElementCopyAttributeValue 的兼容封装, 统一返回 (AXError, value)。

    不同 pyobjc 版本对该函数的绑定形式不同 (是否保留 out 参数占位), 这里两种
    调用形式都兼容; 失败时 AXError 非 0。
    """
    try:
        result = AXUIElementCopyAttributeValue(element, attribute, None)
    except TypeError:
        try:
            result = AXUIElementCopyAttributeValue(element, attribute)
        except Exception:
            return -1, None
    except Exception:
        return -1, None
    if isinstance(result, tuple) and len(result) == 2:
        return int(result[0] or 0), result[1]
    return (0, result) if result is not None else (-1, None)


def _axvalue_to_pair(value, is_point):
    """把 AXValue (CGPoint/CGSize) 转成 (a, b); 兼容不同 pyobjc 绑定形式。"""
    type_const = _AX_VALUE_POINT_TYPE if is_point else _AX_VALUE_SIZE_TYPE
    try:
        result = AXValueGetValue(value, type_const, None)
        if isinstance(result, tuple) and len(result) == 2:
            struct = result[1]
        else:
            struct = result
    except Exception:
        struct = value      # 某些版本直接把 AXValue 桥接成 CGPoint/CGSize 对象
    for first, second in (('x', 'y'), ('width', 'height')):
        try:
            return float(getattr(struct, first)), float(getattr(struct, second))
        except Exception:
            continue
    return None


def _ax_window_infos(pid):
    """拿应用 pid 的全部 AX 窗口及其位置/大小; 失败返回空列表。

    返回元素: {'element': AX窗口对象, 'pos': (x, y), 'size': (w, h)}
    """
    if not AX_OK:
        return []
    try:
        app = AXUIElementCreateApplication(int(pid))
        err, ax_windows = _ax_copy_attribute(app, _AX_WINDOWS_ATTR)
        if err != 0 or not ax_windows:
            return []
        infos = []
        for ax_window in ax_windows:
            _, pos_value = _ax_copy_attribute(ax_window, _AX_POSITION_ATTR)
            _, size_value = _ax_copy_attribute(ax_window, _AX_SIZE_ATTR)
            if pos_value is None or size_value is None:
                continue
            pos = _axvalue_to_pair(pos_value, is_point=True)
            size = _axvalue_to_pair(size_value, is_point=False)
            if pos is None or size is None:
                continue
            infos.append({'element': ax_window, 'pos': pos, 'size': size})
        return infos
    except Exception:
        return []


def _looks_fullscreen_by_bounds(bounds, displays):
    """兜底判定: 窗口几乎完全覆盖某块显示器 (连菜单栏区域一起盖住) => 全屏。

    普通的最大化/缩放窗口会被菜单栏挡住而差出几十个 pt, 不会误判; 只用于
    应用不提供 AXFullScreen 属性时的兜底。
    """
    if not bounds or not displays:
        return False
    x, y, width, height = bounds
    for dx, dy, display_width, display_height in displays:
        if (width >= display_width - 2 and height >= display_height - 2
                and abs(x - dx) <= 2 and abs(y - dy) <= 2):
            return True
    return False


def _ax_fullscreen_flag(ax_window, cg_bounds, displays):
    """单个 AX 窗口的“铺满全屏”判定: AXFullScreen -> AXSubrole -> bounds 兜底。"""
    err, value = _ax_copy_attribute(ax_window, _AX_FULLSCREEN_ATTR)
    if err == 0 and value is not None:
        return bool(value)          # AX 明确给出结果时直接信任 (含 False)
    err, subrole = _ax_copy_attribute(ax_window, _AX_SUBROLE_ATTR)
    if err == 0 and subrole == _AX_SUBROLE_FULLSCREEN:
        return True
    return _looks_fullscreen_by_bounds(cg_bounds, displays)


def _match_window_by_bounds(cg_bounds, ax_infos):
    """在 AX 窗口信息里找与 CGWindowBounds 最接近的窗口; 找不到返回 None。"""
    x, y, width, height = cg_bounds
    best = None
    best_distance = None
    for info in ax_infos:
        pos_x, pos_y = info['pos']
        size_w, size_h = info['size']
        if (abs(pos_x - x) > _AX_MATCH_TOLERANCE
                or abs(pos_y - y) > _AX_MATCH_TOLERANCE
                or abs(size_w - width) > _AX_MATCH_TOLERANCE
                or abs(size_h - height) > _AX_MATCH_TOLERANCE):
            continue
        distance = (abs(pos_x - x) + abs(pos_y - y)
                    + abs(size_w - width) + abs(size_h - height))
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best = info
    return best


def _ax_raise_and_focus(pid, bounds):
    """用 Accessibility API 抬升目标窗口并把应用置前, 成功返回 True。"""
    if not AX_OK:
        return False
    try:
        # 1) 在目标应用的所有窗口里, 按“位置 + 大小”找与 CGWindowBounds 最接近
        #    的那个窗口并 AXRaise —— 同一应用开了多个窗口 (如 Chrome 多窗口) 时
        #    也能切到指定窗口, 而不是只把应用整体调到最前。
        target_window = _match_window_by_bounds(bounds, _ax_window_infos(pid))
        if target_window is not None:
            AXUIElementPerformAction(target_window['element'], _AX_RAISE_ACTION)

        # 2) 把应用整体置前 (AXRaise 只在应用内部排序, 不会激活后台应用)
        app = AXUIElementCreateApplication(int(pid))
        if AXUIElementSetAttributeValue(app, _AX_FRONTMOST_ATTR, kCFBooleanTrue) == 0:
            return True
        return _activate_with_nsrunning(int(pid))
    except Exception:
        return False


# ===========================================================================
# 区域 5: 激活兜底 -- NSRunningApplication / osascript / open
# ---------------------------------------------------------------------------
# 系统 API:
#   AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
#       查 pid 对应的运行应用 (拿 bundle id / 激活)。依赖 RunLoop 维护运行
#       列表, 无 RunLoop 的脚本进程里经常查不到, 因此每一处都有非 AppKit 兜底。
#   NSApplicationActivateIgnoringOtherApps
#       activateWithOptions_ 的选项: 即使目标不是当前应用也强制激活。
#   osascript + "System Events" (Apple 事件 / 自动化权限)
#       1) 查 bundle id: first application process whose unix id is <pid>
#       2) 置前 + 抬升窗口: set frontmost of _proc to true; 对 position/size
#          匹配的窗口 perform action "AXRaise"
#       与“辅助功能”权限相互独立, 失败不影响 AX 主链路。
#   open -b <bundle-id> / -a <应用名> (LaunchServices)
#       不需要任何 TCC 授权的激活方式, 作为 AX 与 osascript 都失败时的兜底。
# ===========================================================================

def _bundle_id_for_pid(pid):
    """拿 pid 对应应用的 bundle id。

    NSRunningApplication 依赖 RunLoop 维护运行列表, 在无 RunLoop 的脚本进程里
    经常查不到 (诊断 [8] 未找到的同族问题), 因此再用 System Events 兜底一次。
    """
    if QUARTZ_OK:
        try:
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(
                int(pid))
            if app is not None and app.bundleIdentifier():
                return app.bundleIdentifier()
        except Exception:
            pass
    try:
        result = subprocess.run(
            ['osascript', '-e',
             'tell application "System Events" to get bundle identifier of '
             'first application process whose unix id is %d' % int(pid)],
            capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            bundle_id = (result.stdout or '').strip()
            if bundle_id:
                return bundle_id
    except Exception:
        pass
    return None


def _activate_with_open(pid, owner_name=None):
    """用 `open -b/-a` 走 LaunchServices 激活应用; 不需要任何 TCC 授权。"""
    bundle_id = _bundle_id_for_pid(pid)
    if bundle_id:
        cmd = ['open', '-b', bundle_id]
    elif owner_name:
        cmd = ['open', '-a', owner_name]
    else:
        return False
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def _activate_with_nsrunning(pid):
    """兜底: 用 NSRunningApplication 把目标应用激活。"""
    if not QUARTZ_OK:
        return False
    try:
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))
        if app is None:
            return False
        return bool(app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps))
    except Exception:
        return False


def _applescript_raise_and_focus(pid, bounds):
    """用 osascript + System Events 置前应用并抬升匹配窗口 (AX 方案失败时兜底)。"""
    x, y, width, height = bounds
    ix, iy, iw, ih = (int(round(v)) for v in (x, y, width, height))
    script = """
tell application "System Events"
    set _proc to first application process whose unix id is {pid}
    set frontmost of _proc to true
    try
        repeat with _w in windows of _proc
            if (position of _w) is {{{x}, {y}}} and (size of _w) is {{{w}, {h}}} then
                perform action "AXRaise" of _w
                exit repeat
            end if
        end repeat
    end try
end tell
""".format(pid=int(pid), x=ix, y=iy, w=iw, h=ih)
    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


# ===========================================================================
# 区域 6: 编排层 -- 铺满标记与焦点切换 (对上层的稳定入口)
# ---------------------------------------------------------------------------
# mark_fullscreen_windows: 先用 Quartz 拿显示器 bounds, 再按 pid 分组走 AX
#   枚举 + bounds 匹配, 就地给窗口 dict 打 fullscreen 标记 (纯 Python 数据,
#   AX 元素不出本文件)。AX 不可用时退化为纯 bounds 判定。
# focus_window: 完整兜底链, 每级“声称成功”后都用 z 序实时确认:
#   AX API -> osascript (System Events) -> open (LaunchServices)
#   -> NSRunningApplication。某一级没有真的把目标窗口带到最前就自动换下一级
#   —— 否则下一次按键会以错误的“当前窗口”计算邻居, 表现为切不到预期目标。
# ===========================================================================

def mark_fullscreen_windows(windows):
    """为窗口列表就地标记“铺满全屏”状态 (原生全屏或 Split View 分屏铺满)。

    返回被标记为铺满的窗口数量。AX 不可用时退化为纯 bounds 判定
    (此时只能识别覆盖整块显示器的原生全屏)。
    """
    if not windows:
        return 0
    displays = _display_bounds_list()
    if not AX_OK:
        marked = 0
        for win in windows:
            if _looks_fullscreen_by_bounds(
                    (win['x'], win['y'], win['w'], win['h']), displays):
                win['fullscreen'] = True
                marked += 1
        return marked
    groups = {}
    for win in windows:
        groups.setdefault(win['pid'], []).append(win)
    marked = 0
    for pid, group in groups.items():
        ax_infos = _ax_window_infos(pid)
        if not ax_infos:
            continue
        for win in group:
            bounds = (win['x'], win['y'], win['w'], win['h'])
            match = _match_window_by_bounds(bounds, ax_infos)
            if match is None:
                continue
            if _ax_fullscreen_flag(match['element'], bounds, displays):
                win['fullscreen'] = True
                marked += 1
    return marked


def focus_window(target):
    """把目标窗口带到最前, 并用 z 序实时确认切换真的生效。

    依次尝试: AX API -> osascript (System Events) -> open (LaunchServices)
    -> NSRunningApplication。某一级“声称成功”但目标窗口没有真的到最前时,
    自动换下一级 —— 否则下一次按键会以错误的“当前窗口”计算邻居, 表现为
    切不到预期的目标应用。
    """
    pid = int(target['pid'])
    bounds = (target['x'], target['y'], target['w'], target['h'])
    if _ax_raise_and_focus(pid, bounds) and _wait_until_window_top(target):
        return True
    if _applescript_raise_and_focus(pid, bounds) and _wait_until_window_top(target):
        return True
    if _activate_with_open(pid, target.get('owner')) and _wait_until_window_top(target):
        return True
    if _activate_with_nsrunning(pid) and _wait_until_window_top(target):
        return True
    return False


# ===========================================================================
# 区域 7: 诊断模式 -- 逐环自检切换链路 (排障用, 供 simple_actions 入口调用)
# ---------------------------------------------------------------------------
# 依次检查: 模块可用性 -> AXIsProcessTrusted 授权 -> CGWindowList 实时窗口 ->
# AX 读 AXWindows / 写 AXFrontmost 的真实返回码 -> osascript + System Events
# (自动化权限) -> NSRunningApplication 查询 (无 RunLoop 查不到属正常), 最后
# 汇总问题清单。_AX_ERROR_NAMES 把 AXError 负数错误码翻译成可读名称。
# ===========================================================================

_AX_ERROR_NAMES = {
    0: '成功',
    -25200: 'kAXErrorFailure',
    -25201: 'kAXErrorIllegalArgument 参数错误',
    -25202: 'kAXErrorInvalidUIElement 无效UI元素',
    -25204: 'kAXErrorAPIDisabled 辅助功能未授权',
    -25205: 'kAXErrorAttributeUnsupported 属性不支持',
    -25206: 'kAXErrorActionUnsupported 动作不支持',
    -25208: 'kAXErrorNotImplemented 未实现',
    -25211: 'kAXErrorCannotComplete 无法完成',
}


def run_diagnostics(target_pid=None):
    """打印授权与切换链路每一环的真实状态, 用于定位“切换失败”。"""
    print('== simple_actions 诊断 ==')
    print('[1] 模块可用性: Quartz=%s, AX API=%s' % (QUARTZ_OK, AX_OK))
    trusted = False
    if AX_OK:
        trusted = bool(AXIsProcessTrusted())
        print('[2] 辅助功能授权 AXIsProcessTrusted: %s' % trusted)
    if not QUARTZ_OK:
        print('!! Quartz 不可用, 诊断终止')
        return
    windows = list_visible_windows()
    print('[3] CGWindowList 实时窗口 (前->后, 共 %d 个):' % len(windows))
    for win in windows[:8]:
        print('      %s (pid %s, #%s, x=%.0f y=%.0f)' % (
            win['owner'], win['pid'], win['number'], win['x'], win['y']))
    if not windows:
        print('!! 没有可见窗口, 诊断终止')
        return
    target = windows[0]
    if target_pid is not None:
        target = next((w for w in windows if w['pid'] == int(target_pid)), target)
    print('[4] 测试目标: %s (pid %s)' % (target['owner'], target['pid']))

    ax_read_err = None
    ax_set_err = None
    switched = None
    problems = []
    if AX_OK:
        if not trusted:
            print('[5] 跳过 AX 实测 (辅助功能未授权)')
            problems.append('辅助功能未授权 ([2]=False): 请在 系统设置 -> 隐私与安全性 '
                            '-> 辅助功能 勾选运行本脚本的终端/IDE')
        else:
            try:
                app = AXUIElementCreateApplication(int(target['pid']))
                ax_read_err, ax_windows = _ax_copy_attribute(app, _AX_WINDOWS_ATTR)
                print('[5] AX 读 AXWindows: err=%s (%s), 窗口数=%s' % (
                    ax_read_err, _AX_ERROR_NAMES.get(ax_read_err, '未知错误'),
                    len(ax_windows) if ax_windows else 0))
                ax_set_err = AXUIElementSetAttributeValue(
                    app, _AX_FRONTMOST_ATTR, kCFBooleanTrue)
                print('[6] AX 置 AXFrontmost: err=%s (%s)' % (
                    ax_set_err, _AX_ERROR_NAMES.get(ax_set_err, '未知错误')))
                time.sleep(0.5)
                top = list_visible_windows()
                switched = bool(top) and top[0]['pid'] == target['pid']
                print('    实测结果: z 序最前 = %s (pid %s)%s' % (
                    top[0]['owner'] if top else '?',
                    top[0]['pid'] if top else '?',
                    ' —— 已切到目标' if switched else ' —— 未切到目标'))
            except Exception as exc:
                problems.append('AX 调用异常: %r' % (exc,))
                print('[5/6] AX 调用异常: %r' % (exc,))
    se_rc = None
    try:
        result = subprocess.run(
            ['osascript', '-e',
             'tell application "System Events" to get name of '
             'first application process whose frontmost is true'],
            capture_output=True, text=True, timeout=5)
        se_rc = result.returncode
        print('[7] osascript + System Events: rc=%s, 输出=%s, 错误=%s' % (
            se_rc, (result.stdout or '').strip(),
            ((result.stderr or '').strip() or '无')[:160]))
    except Exception as exc:
        print('[7] osascript 异常: %r' % (exc,))
    if QUARTZ_OK:
        try:
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(
                int(target['pid']))
            print('[8] NSRunningApplication 查询: %s (无 RunLoop 的脚本进程里查不到'
                  '属正常, 程序已用 System Events/open 兜底)' % (
                      '找到' if app is not None else '未找到'))
        except Exception as exc:
            print('[8] NSRunningApplication 异常: %r' % (exc,))
    if ax_read_err not in (None, 0):
        problems.append('AX 读取失败 (err=%s, %s)' % (
            ax_read_err, _AX_ERROR_NAMES.get(ax_read_err, '未知错误')))
    if ax_set_err not in (None, 0):
        problems.append('AX 置 AXFrontmost 失败 (err=%s, %s)' % (
            ax_set_err, _AX_ERROR_NAMES.get(ax_set_err, '未知错误')))
    if switched is False:
        problems.append('AX 声称成功但窗口没有真的到最前 (激活被系统忽略), '
                        '程序会自动改用 open 等兜底')
    if se_rc not in (None, 0):
        problems.append('osascript + System Events 不可用 (rc=%s) —— 这属于“自动化”'
                        '权限, 与辅助功能相互独立; 不影响主链路, 程序会走 AX/open 兜底'
                        % se_rc)
    print('== 诊断结论 ==')
    if problems:
        for problem in problems:
            print('问题: %s' % problem)
    else:
        print('切换链路全部正常, 可以直接使用 shift+a/d; 若监听脚本还在运行旧代码, '
              '请重启 main.py 后再试。')
