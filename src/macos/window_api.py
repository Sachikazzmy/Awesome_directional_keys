# -*- coding: utf-8 -*-
"""
window_api.py -- macos/ 平台层唯一文件 (单文件精简版): 本项目所有 macOS 系统
API 的导入与调用都集中在这里, 用分隔线划分区域职能。

上层 actions/ 层只使用本文件暴露的纯 Python 接口, 不出现任何 pyobjc / 子进程细节:
    QUARTZ_OK / AX_OK              -- pyobjc 子模块可用性标志 (True/False)
    list_visible_windows()         -- 当前 Space 内可见普通窗口 (z 序, 前 -> 后)
    mark_fullscreen_windows(wins)  -- 就地为窗口列表标记 fullscreen: True/False
    focus_window(target)           -- 把目标窗口带到最前, 并用 z 序实时确认生效
    scroll_window(target, delta, pixel_unit)
                                   -- 对目标窗口模拟鼠标滚轮上下滚动 (page_turn 用)
    scroll_support_info()          -- 滚动链路可用性探测 (只读, 诊断用)
    cursor_location()              -- 当前鼠标光标位置 (只读, 诊断用)
    entry_fields_info(target)      -- 只读: 窗口文本输入框清单 + 聚焦状态（诊断用）
    focused_entry_state(target)    -- 只读快查: 是否正聚焦在文本输入框（toggle 判定）
    focus_entry_field(target, index)
                                   -- 聚焦第 index 个可见输入框（insert 模式“进入”）
    blur_entry_field(target)       -- 取消聚焦（insert 模式“退出”），多级尝试 + 读回确认
    insert_support_info()          -- 聚焦链路可用性探测（只读, 诊断用）
    ax_error_name(err)             -- AXError 错误码 -> 中文名（诊断输出用）
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
    6) Quartz CGEvent -- 滚轮事件 (区域 8, 供 actions/page_turn.py 页面滚动用)
       CGEventCreateScrollWheelEvent 创建滚轮事件 (wheelAmount 正数=向上滚、
       负数=向下滚, 已在本机用密闭 Tk 窗口实测确认); CGEventSetLocation 把
       事件坐标设到目标窗口中心; CGEventPost 投入 HID 事件流后, 窗口服务器按
       “事件坐标落在哪个窗口”命中路由 —— 滚动去向与物理光标停在哪里无关
       (同一实验同时实测: CGEventPostToPid 直达指定应用不生效, 故不采用;
       坐标不落在任何普通窗口上的事件会被丢弃)。需“辅助功能”授权,
       与 pynput 键盘监听共用同一份。
    7) ApplicationServices (HIServices) -- AX 焦点定位 (区域 9, 供
       actions/insert_mode.py 的 vim 式 insert 模式用)
       AXUIElementCopyAttributeValue(app, "AXFocusedUIElement") 读应用当前
       聚焦的元素 (判定网页输入框是否聚焦, 并在聚焦/取消后读回复核);
       AXUIElementSetAttributeValue(element, "AXFocused", ...) 写 True/False
       等价“点进输入框/点回页面空白处”, 不移动鼠标、不会误点链接; 遍历
       AXChildren/AXRole 在网页子树 (AXWebArea) 里找输入框。Chrome/Electron
       默认不向辅助技术暴露网页子树, 写 AXManualAccessibility /
       AXEnhancedUserInterface 轻推开启 (其他应用不认识, 写失败即忽略)。
    8) Quartz CGEvent -- 键盘事件 (区域 10, 取消聚焦的 Esc 兜底)
       CGEventCreateKeyboardEvent + CGEventPost 合成 Esc 按下/抬起投入 HID
       事件流, 由系统路由给当前聚焦的应用; 仅作区域 9 的 AX 两级都失败后
       的最后兜底 (部分浏览器只有搜索框响应 Esc, 且会先结束输入法组字)。

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
#   CoreFoundation      -> kCFBooleanTrue / kCFBooleanFalse (AXFrontmost /
#                          AXFocused 属性写入常量, 后者供区域 9 聚焦/取消聚焦
#                          网页输入框使用)
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
    from CoreFoundation import kCFBooleanTrue, kCFBooleanFalse
    AX_OK = True
except Exception:                                          # pragma: no cover
    AX_OK = False

__all__ = [
    'QUARTZ_OK', 'AX_OK',
    'list_visible_windows', 'mark_fullscreen_windows',
    'focus_window', 'run_diagnostics',
    'scroll_window', 'scroll_support_info', 'cursor_location',
    'ax_error_name',
    'entry_fields_info', 'focused_entry_state',
    'focus_entry_field', 'blur_entry_field', 'insert_support_info',
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
# 区域 7: 诊断模式 -- 逐环自检切换链路 (排障用, 供 switch_windows 入口调用)
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


def ax_error_name(err):
    """把 AXError 错误码翻译成可读名称 (诊断输出用; 未知码原样带回)。"""
    if err in _AX_ERROR_NAMES:
        return _AX_ERROR_NAMES[err]
    return '未知错误(%s)' % err


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


# ===========================================================================
# 区域 8: 滚轮事件 (scroll wheel) -- 模拟鼠标滚轮, 让“当前窗口”上下滚动
# ---------------------------------------------------------------------------
# 系统 API (Quartz CGEvent, 需“辅助功能”授权, 与 pynput 键盘监听共用同一份):
#   CGEventCreateScrollWheelEvent(source, units, wheelCount, wheelAmount)
#       创建一根滚轴的滚轮事件: source 传 None (无事件来源); units 取
#       kCGScrollEventUnitPixel(0) 按像素滚动 (滚动距离跨应用一致) 或
#       kCGScrollEventUnitLine(1) 按“行”滚动 (语义对齐传统滚轮); wheelAmount
#       的符号即滚动方向 —— 正数 = 向上滚 (查看之前内容), 负数 = 向下滚 (查看
#       后续内容), 与 NSEvent.deltaY 同号 (本机密闭 Tk 窗口实测确认)。
#   CGEventSetLocation(event, point)
#       把事件坐标设到目标窗口中心 (CG 全局坐标, 与 CGWindowBounds 同一坐标
#       系)。实测确认: CGEventPost 投入事件流后, 窗口服务器按“事件坐标落在
#       哪个窗口”命中路由 —— 与物理光标停在哪里无关、与 key window 无关;
#       不设坐标的事件落在 (0,0), 会滚错目标。
#   CGEventPost(kCGHIDEventTap, event)
#       把事件投入 HID 事件流, 由窗口服务器按事件坐标命中路由到目标窗口。
#       (同族 API CGEventPostToPid 直达指定应用在本机实测不生效, 不采用。)
#   CGEventCreate(None) + CGEventGetLocation(event)
#       查询鼠标光标当前位置 (只读, 诊断用)。
# 平滑滚动: scroll_window 单次只投递一个事件; 上层 (actions/page_turn.py) 以
#   ~100Hz 高频、指数缓出的小步长连续调用它, 把一次按键的滚动量摊成丝滑
#   动画 —— 单发一笔大步长事件在多数应用里会“咯噔”一下跳变。
# 边界: CGEventPost 返回 void, “投递成功”不代表应用一定消费了滚动 (个别应用
#   不响应合成滚轮事件); 实际效果靠诊断模式的实测项肉眼确认。
# ===========================================================================

_QUARTZ_SCROLL_UNIT_PIXEL = 0          # kCGScrollEventUnitPixel: 按像素滚动
_QUARTZ_SCROLL_UNIT_LINE = 1           # kCGScrollEventUnitLine: 按行滚动


def _create_scroll_wheel_event(delta, pixel_unit):
    """创建一根滚轴的滚轮事件; wheelAmount 为整数 (正=向上滚, 负=向下滚)。"""
    unit = _QUARTZ_SCROLL_UNIT_PIXEL if pixel_unit else _QUARTZ_SCROLL_UNIT_LINE
    amount = int(round(delta))
    try:
        return Quartz.CGEventCreateScrollWheelEvent(None, unit, 1, amount)
    except TypeError:
        # 个别 pyobjc 绑定要求把滚轴的量给全 (wheelCount=1 时后两轴传 0 即可)
        return Quartz.CGEventCreateScrollWheelEvent(None, unit, 1, amount, 0, 0)


def cursor_location():
    """当前鼠标光标位置 (CG 全局坐标 (x, y)); 拿不到时返回 None。只读, 诊断用。"""
    if not QUARTZ_OK:
        return None
    try:
        point = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        return (float(point.x), float(point.y))
    except Exception:
        return None


def scroll_support_info():
    """滚动链路可用性探测 (只读, 供 actions/page_turn.py 的自检与排障使用)。

    返回字段 (纯 Python 值):
        quartz_ok     Quartz 子模块是否可用
        ax_trusted    辅助功能授权状态 (投递事件的前提, 与 pynput 共用授权)
        create_event  能否创建滚轮事件 (CGEventCreateScrollWheelEvent)
        post_hid      能否投递 HID 事件流 (CGEventPost)
        cursor        当前光标位置 (x, y) 或 None (滚动路由与光标无关, 供参照)
    """
    info = {
        'quartz_ok': QUARTZ_OK,
        'ax_trusted': False,
        'create_event': False,
        'post_hid': False,
        'cursor': None,
    }
    if not QUARTZ_OK:
        return info
    info['create_event'] = hasattr(Quartz, 'CGEventCreateScrollWheelEvent')
    info['post_hid'] = hasattr(Quartz, 'CGEventPost')
    if AX_OK:
        try:
            info['ax_trusted'] = bool(AXIsProcessTrusted())
        except Exception:
            info['ax_trusted'] = False
    info['cursor'] = cursor_location()
    return info


def scroll_window(target, delta, pixel_unit=True):
    """对目标窗口模拟一次鼠标滚轮滚动 (页面上下滚动的平台入口)。

    target: list_visible_windows() 返回的窗口 dict (取其 bounds);
    delta: 正数 = 向上滚 (查看之前内容), 负数 = 向下滚 (查看后续内容);
    pixel_unit: True 按像素滚动 / False 按“行”滚动。
    返回 (是否成功投递, 投递方式描述字符串); 投递成功不代表应用一定消费滚动。

    机制: 事件坐标设到目标窗口中心后投入 HID 事件流, 窗口服务器按事件坐标
    命中路由 —— 物理光标停在哪里都不影响滚动去向; 但要求目标窗口在该坐标处
    就是上层普通窗口 (业务层传入 z 序最前的窗口即可满足)。
    """
    if not QUARTZ_OK:
        return False, 'Quartz 不可用'
    try:
        event = _create_scroll_wheel_event(delta, pixel_unit)
    except Exception as exc:
        return False, '创建滚轮事件失败: %r' % (exc,)
    if event is None:
        return False, '创建滚轮事件失败: 返回空事件'
    # 事件坐标 = 目标窗口中心: 命中路由按它找窗口; 设置失败宁可报错也不投递,
    # 否则事件会用缺省坐标 (0,0), 滚到别的窗口上。
    try:
        Quartz.CGEventSetLocation(
            event, (float(target['x']) + float(target['w']) / 2.0,
                    float(target['y']) + float(target['h']) / 2.0))
    except Exception as exc:
        return False, '设置事件坐标失败: %r' % (exc,)
    try:
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        return True, 'CGEventPost -> HID 事件流 (按事件坐标命中路由)'
    except Exception as exc:
        return False, '事件投递失败: %r' % (exc,)


# ===========================================================================
# 区域 9: AX 焦点定位与文本框枚举 -- vim 式 insert 模式的平台机制
# (供 actions/insert_mode.py 使用: 聚焦/取消聚焦网页输入框)
# ---------------------------------------------------------------------------
# 系统 API (ApplicationServices / HIServices, 需“辅助功能”授权, 与 pynput
# 键盘监听共用同一份):
#   AXUIElementCopyAttributeValue(app, "AXFocusedUIElement")
#       读应用“当前聚焦的 UI 元素”: 判定网页输入框是否聚焦 (输入/阅览模式),
#       以及每次聚焦/取消后的实时复核 —— 与激活兜底链的 z 序确认同风格:
#       每级“声称成功”都必须读回确认, 未确认就换下一级。
#   AXUIElementSetAttributeValue(element, "AXFocused", kCFBooleanTrue/False)
#       把 AX 树里的某个元素设为聚焦/取消聚焦。浏览器把网页输入框暴露成
#       AXTextField/AXTextArea/AXSearchField/AXComboBox, 对它写 True 等价于
#       “点进输入框”但不移动鼠标; 写 False 或把焦点挪到网页区 (AXWebArea)/
#       窗口容器等价于“点回页面空白处”。相比合成鼠标点击, 不会误触链接。
#   AXUIElementCopyAttributeValue(element, "AXRole"/"AXChildren"/...)
#       深度优先遍历 AX 窗口子树找输入框; 树序约等于页面 DOM 顺序 (页面主
#       输入框通常靠前)。节点数/深度设上限, 避免超大页面拖垮 AX 往返。
#   AXUIElementSetAttributeValue(app, "AXManualAccessibility" /
#       "AXEnhancedUserInterface", kCFBooleanTrue)
#       Chrome/Electron 系应用检测不到辅助技术时默认不把网页内容暴露成
#       AX 子树 (扫不到输入框), 写这两个属性是让它在无扩展前提下开启网页
#       无障碍树的公认手段; 其他应用不认识这些属性, 写失败即静默忽略。
# 边界: 只考虑“可见”输入框 (有尺寸且与目标窗口 bounds 相交); 不写输入框的
#   AXValue 文本内容 (只在取标签时读一次并截断, 绝不修改); AX 元素不出本文件。
# ===========================================================================

_AX_FOCUSED_UI_ELEMENT_ATTR = 'AXFocusedUIElement'   # 应用当前聚焦的元素
_AX_FOCUSED_ATTR = 'AXFocused'                       # 元素的聚焦开关 (可写)
_AX_ROLE_ATTR = 'AXRole'
_AX_CHILDREN_ATTR = 'AXChildren'
_AX_TITLE_ATTR = 'AXTitle'
_AX_DESCRIPTION_ATTR = 'AXDescription'
_AX_PLACEHOLDER_ATTR = 'AXPlaceholderValue'
_AX_VALUE_ATTR = 'AXValue'

_AX_WEB_AREA_ROLE = 'AXWebArea'      # 浏览器网页内容的 AX 子树根
# 可输入文本的 AX 角色 (网页输入框/多行编辑框/搜索框/可编辑下拉框)
_AX_ENTRY_ROLES = ('AXTextField', 'AXTextArea', 'AXSearchField', 'AXComboBox')

_AX_MANUAL_ACCESSIBILITY_ATTR = 'AXManualAccessibility'   # Chromium 轻推键 (新)
_AX_ENHANCED_UI_ATTR = 'AXEnhancedUserInterface'          # Chromium 轻推键 (旧)

_AX_SCAN_MAX_NODES = 2000         # 单次 AX 树扫描的节点上限 (防超大页面)
_AX_SCAN_MAX_DEPTH = 20           # 单次 AX 树扫描的深度上限
_AX_FIELD_MIN_WIDTH = 16.0        # 比这更窄/更矮的“输入框”视为不可见装饰
_AX_FIELD_MIN_HEIGHT = 8.0
_NUDGE_POLL_INTERVAL = 0.25       # 轻推 Chromium 后轮询网页区出现的步长
_NUDGE_TOTAL_SECONDS = 2.0        # 轻推后等待网页无障碍树构建的总时限
_FOCUS_VERIFY_TIMEOUT = 0.8       # 聚焦/取消后的读回复核时限
_FOCUS_VERIFY_INTERVAL = 0.05


def _ax_role(element):
    """读 AX 元素的角色名 (如 AXTextField); 读不到返回 None。"""
    err, value = _ax_copy_attribute(element, _AX_ROLE_ATTR)
    return value if err == 0 and isinstance(value, str) else None


def _ax_children(element):
    """读 AX 元素的子元素列表; 读不到返回空列表。"""
    err, value = _ax_copy_attribute(element, _AX_CHILDREN_ATTR)
    if err != 0 or not value:
        return []
    return list(value)


def _ax_geometry(element):
    """读 AX 元素的位置/大小 (CG 全局坐标); 读不到返回 (None, None)。"""
    _, pos_value = _ax_copy_attribute(element, _AX_POSITION_ATTR)
    _, size_value = _ax_copy_attribute(element, _AX_SIZE_ATTR)
    if pos_value is None or size_value is None:
        return None, None
    pos = _axvalue_to_pair(pos_value, is_point=True)
    size = _axvalue_to_pair(size_value, is_point=False)
    return pos, size


def _ax_element_label(element):
    """输入框的可读标签: AXTitle -> AXDescription -> AXPlaceholderValue ->
    AXValue 前段 (只读展示用, 截断防长文本; 绝不写回)。"""
    for attr in (_AX_TITLE_ATTR, _AX_DESCRIPTION_ATTR, _AX_PLACEHOLDER_ATTR):
        err, value = _ax_copy_attribute(element, attr)
        if err == 0 and isinstance(value, str) and value.strip():
            return value.strip()[:24]
    err, value = _ax_copy_attribute(element, _AX_VALUE_ATTR)
    if err == 0 and isinstance(value, str) and value.strip():
        return '内容:%s' % value.strip()[:18]
    return ''


def _ax_focused_ui_element(app_element):
    """读应用当前聚焦的 UI 元素; 返回 (元素或 None, 错误码)。

    本机实测 (Chrome): 网页输入框聚焦时这里返回的就是该字段 (role=
    AXTextField); 浏览态返回网页区 (AXWebArea); 网页无障碍树未开启时可能
    给出属性探测不了的占位引用 —— 角色读不到时不能当作“阅览”乱报。
    """
    return _ax_copy_attribute(app_element, _AX_FOCUSED_UI_ELEMENT_ATTR)


def _ax_own_focused(element):
    """读 AX 元素“自身是否聚焦” (AXFocused 属性, 只读); 返回 (错误码, bool|None)。

    本机实测 (Chrome): 这是判定网页输入框聚焦与否的最可靠信号 —— 对字段写
    AXFocused=True 后 0.3s 内自身读回 True; app 层的 AXFocusedUIElement 在
    个别状态下会给出探测不了的引用, 不如自身读回稳。
    """
    err, value = _ax_copy_attribute(element, _AX_FOCUSED_ATTR)
    if err != 0:
        return err, None
    return 0, bool(value)


def _ax_element_matches_geometry(element, pos, size, tolerance=2.0):
    """判断 AX 元素的位置/大小是否与给定值一致 (识别“聚焦的就是那个框”)。

    个别应用把可编辑元素报成非标准角色时, 用几何位置兜底确认。
    """
    if element is None or pos is None or size is None:
        return False
    actual_pos, actual_size = _ax_geometry(element)
    if actual_pos is None or actual_size is None:
        return False
    return (abs(actual_pos[0] - pos[0]) <= tolerance
            and abs(actual_pos[1] - pos[1]) <= tolerance
            and abs(actual_size[0] - size[0]) <= tolerance
            and abs(actual_size[1] - size[1]) <= tolerance)


def _nudge_browser_accessibility(app_element):
    """轻推 Chrome/Electron 系应用开启网页无障碍树 (其他应用写入失败即忽略)。

    Chromium 检测不到辅助技术时默认不把网页内容暴露成 AX 子树; 写
    AXManualAccessibility (新) / AXEnhancedUserInterface (旧) 让它开启。
    本机实测: 轻推后网页区约 2s 才长出来, 所以调用方要轮询重扫而不是只等
    一次固定时长; 写入返回码不可信 (Chrome 对 AXEnhancedUserInterface 的
    写返回 -25208 但实际生效), 以“网页区是否出现”为准。
    """
    for attr in (_AX_MANUAL_ACCESSIBILITY_ATTR, _AX_ENHANCED_UI_ATTR):
        try:
            AXUIElementSetAttributeValue(app_element, attr, kCFBooleanTrue)
        except Exception:
            continue


def _ax_entry_scan(app_element, window_element, nudge=True):
    """深度优先遍历 AX 窗口子树, 按树序收集文本输入框与网页区。

    树序约等于页面 DOM 顺序 (页面主输入框通常靠前); 输入框本身不再往下
    遍历。每个输入框带 in_web 标记 (是否位于 AXWebArea 网页子树内) ——
    浏览器窗口里地址栏等原生输入框混在同一棵树上, 聚焦时必须优先网页内的,
    否则会把光标聚焦到地址栏而不是页面输入框。
    nudge=True 且首扫没有网页区 (也没有网页内输入框) 时, 先轻推 Chromium
    开启网页无障碍树, 再按 _NUDGE_POLL_INTERVAL 步长轮询重扫, 总时限
    _NUDGE_TOTAL_SECONDS (本机实测 Chrome/Electron 在轻推后约 2s 长出网页
    区, 出现即提前结束); 这是“进入输入模式”的主链路, 纯只读诊断传 False,
    避免诊断模式改变应用状态。
    返回 dict: fields 为 {'element': AX元素, 'in_web': bool} 列表 (AX 元素
    不出本文件对外), web_areas 为 AX 元素列表, 另有 scanned_nodes /
    truncated / nudged 计数与标记。
    """
    result = {'fields': [], 'web_areas': [], 'scanned_nodes': 0,
              'truncated': False, 'nudged': False}

    def _scan_once():
        fields, web_areas, visited = [], [], 0
        stack = [(window_element, 0, False)]
        while stack and visited < _AX_SCAN_MAX_NODES:
            element, depth, in_web = stack.pop()
            visited += 1
            role = _ax_role(element)
            if role == _AX_WEB_AREA_ROLE:
                web_areas.append(element)
                in_web = True               # 网页区子树内的元素都标记 in_web
            if role in _AX_ENTRY_ROLES:
                fields.append({'element': element, 'in_web': in_web})
                continue                    # 输入框无需再往下遍历
            if depth >= _AX_SCAN_MAX_DEPTH:
                continue
            for child in reversed(_ax_children(element)):
                stack.append((child, depth + 1, in_web))
        return fields, web_areas, visited, bool(stack)

    fields, web_areas, visited, truncated = _scan_once()
    if nudge and not web_areas and not any(item['in_web'] for item in fields):
        _nudge_browser_accessibility(app_element)
        result['nudged'] = True
        deadline = time.monotonic() + _NUDGE_TOTAL_SECONDS
        while True:
            time.sleep(_NUDGE_POLL_INTERVAL)
            fields, web_areas, visited, truncated = _scan_once()
            if web_areas or time.monotonic() >= deadline:
                break
    result['fields'] = fields
    result['web_areas'] = web_areas
    result['scanned_nodes'] = visited
    result['truncated'] = truncated
    return result


def _field_visible(pos, size, bounds):
    """输入框可见判定: 有实际尺寸, 且与目标窗口 bounds 相交 (面积>0)。"""
    if pos is None or size is None:
        return False
    if size[0] < _AX_FIELD_MIN_WIDTH or size[1] < _AX_FIELD_MIN_HEIGHT:
        return False
    bx, by, bw, bh = bounds
    overlap_w = min(pos[0] + size[0], bx + bw) - max(pos[0], bx)
    overlap_h = min(pos[1] + size[1], by + bh) - max(pos[1], by)
    return overlap_w > 0 and overlap_h > 0


def _ax_match_window_root(pid, bounds):
    """拿目标应用的 AX 窗口, 按 CGWindowBounds 匹配出目标窗口的 AX 根元素。

    返回 (根元素或 None, 是否按 bounds 精确匹配); 匹配失败时退回该应用的
    第一个 AX 窗口 (调用方会把兜底情况告诉上层)。
    """
    infos = _ax_window_infos(pid)
    if not infos:
        return None, False
    matched = _match_window_by_bounds(bounds, infos)
    if matched is not None:
        return matched['element'], True
    return infos[0]['element'], False


def _wait_until_focus(app_element, success_check,
                      timeout=_FOCUS_VERIFY_TIMEOUT,
                      interval=_FOCUS_VERIFY_INTERVAL):
    """轮询读回 AXFocusedUIElement, 直到 success_check(元素, 错误码) 成立。

    与 _wait_until_window_top 同风格: AX 写属性是异步生效的, 必须“读回确认”,
    不能只信 AXUIElementSetAttributeValue 的返回码。
    """
    deadline = time.monotonic() + timeout
    while True:
        focused, err = _ax_focused_ui_element(app_element)
        try:
            if success_check(focused, err):
                return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def insert_support_info():
    """聚焦链路可用性探测 (只读, 供 actions/insert_mode.py 的自检使用)。

    返回字段 (纯 Python 值):
        ax_ok           AX API 是否可用
        ax_trusted      辅助功能授权状态 (与 pynput 键盘监听共用同一份授权)
        keyboard_event  能否合成键盘事件 (Esc 兜底用, 缺了只少一级兜底)
    """
    info = {'ax_ok': AX_OK, 'ax_trusted': False, 'keyboard_event': False}
    if AX_OK:
        try:
            info['ax_trusted'] = bool(AXIsProcessTrusted())
        except Exception:
            info['ax_trusted'] = False
    if QUARTZ_OK:
        try:
            info['keyboard_event'] = (hasattr(Quartz, 'CGEventCreateKeyboardEvent')
                                      and hasattr(Quartz, 'CGEventPost'))
        except Exception:
            info['keyboard_event'] = False
    return info


def entry_fields_info(target, nudge=False):
    """只读探查目标窗口的文本输入框清单与当前聚焦状态 (诊断/预览用)。

    target: list_visible_windows() 返回的窗口 dict。nudge 默认 False (纯只读,
    不轻推 Chromium, 诊断模式不改变应用状态; “进入输入模式”的实测主链路才
    轻推)。AX 元素不出本文件, 返回的 fields 只有纯 Python 数据。
    """
    info = {
        'window_matched': False, 'web_areas': 0, 'scanned_nodes': 0,
        'truncated': False, 'nudged': False, 'fields': [],
        'focused': None, 'focused_is_entry': False, 'detail': '',
    }
    if not AX_OK:
        info['detail'] = 'AX API 不可用 (缺少 pyobjc ApplicationServices)'
        return info
    try:
        pid = int(target['pid'])
        bounds = (float(target['x']), float(target['y']),
                  float(target['w']), float(target['h']))
        app = AXUIElementCreateApplication(pid)
        root, matched = _ax_match_window_root(pid, bounds)
        info['window_matched'] = matched
        if root is None:
            info['detail'] = ('读不到该应用的 AX 窗口列表 (辅助功能未授权, '
                              '或应用不向辅助功能暴露窗口)')
            return info
        scan = _ax_entry_scan(app, root, nudge=nudge)
        info['web_areas'] = len(scan['web_areas'])
        info['scanned_nodes'] = scan['scanned_nodes']
        info['truncated'] = scan['truncated']
        info['nudged'] = scan['nudged']
        for item in scan['fields']:
            element = item['element']
            pos, size = _ax_geometry(element)
            info['fields'].append({
                'role': _ax_role(element) or '?',
                'label': _ax_element_label(element),
                'x': pos[0] if pos else 0.0, 'y': pos[1] if pos else 0.0,
                'w': size[0] if size else 0.0, 'h': size[1] if size else 0.0,
                'visible': _field_visible(pos, size, bounds),
                'in_web': item['in_web'],
            })
        focused, err = _ax_focused_ui_element(app)
        if focused is not None:
            pos, size = _ax_geometry(focused)
            role = _ax_role(focused)
            if role is not None and role not in _AX_ENTRY_ROLES:
                # 聚焦在容器 (网页区/窗口等) 时下钻一层找真实聚焦的输入框
                err2, inner = _ax_copy_attribute(
                    focused, _AX_FOCUSED_UI_ELEMENT_ATTR)
                if err2 == 0 and inner is not None:
                    inner_role = _ax_role(inner)
                    if inner_role in _AX_ENTRY_ROLES:
                        focused, role = inner, inner_role
                        pos, size = _ax_geometry(focused)
            if role is not None:
                info['focused'] = {
                    'role': role,
                    'label': _ax_element_label(focused),
                    'x': pos[0] if pos else 0.0, 'y': pos[1] if pos else 0.0,
                    'w': size[0] if size else 0.0, 'h': size[1] if size else 0.0,
                }
                info['focused_is_entry'] = role in _AX_ENTRY_ROLES
            else:
                # 聚焦元素属性读不到: 不能当作“阅览”乱报, 如实标为未知
                info['focused_is_entry'] = None
                info['detail'] = ('聚焦元素属性读不到 (典型: 浏览器网页无障碍树'
                                  '未开启), 聚焦状态无法判定')
        elif err != 0:
            info['detail'] = ('读不到聚焦元素: err=%s (%s)'
                              % (err, ax_error_name(err)))
    except Exception as exc:
        info['detail'] = 'AX 扫描异常: %r' % (exc,)
    return info


def _ax_pick_focused_entry(scan):
    """在扫描结果里找“自身 AXFocused 读回 True”的输入框; 返回 item 或 None。

    app 层 AXFocusedUIElement 在 Chrome 上部分状态会给出属性探测不了的
    占位引用, 逐字段自身读回是实测最可靠的聚焦判定。
    """
    for item in scan['fields']:
        e, v = _ax_own_focused(item['element'])
        if e == 0 and v:
            return item
    return None


def focused_entry_state(target):
    """快查当前窗口的聚焦状态 (只读, toggle 的判定依据)。

    判定顺序: 1) app 层 AXFocusedUIElement 角色可读且是输入框 -> True;
    2) 扫描子树逐字段自身 AXFocused 读回 (Chrome 网页字段的可靠信号) ->
    True; 3) 有输入框/网页区但无人自称聚焦 -> False (阅览); 4) 扫不到任何
    输入框且聚焦元素属性也读不到 -> None (无法判定, 上层按“进入”处理)。
    返回 (状态, 描述)。
    """
    if not AX_OK:
        return None, 'AX API 不可用 (缺少 pyobjc ApplicationServices)'
    try:
        pid = int(target['pid'])
        bounds = (float(target['x']), float(target['y']),
                  float(target['w']), float(target['h']))
        app = AXUIElementCreateApplication(pid)
        # 快路径: app 层聚焦元素角色可读且是输入框
        focused, err = _ax_focused_ui_element(app)
        if focused is not None:
            role = _ax_role(focused)
            if role in _AX_ENTRY_ROLES:
                return True, '聚焦在 %s [%s]' % (
                    role, _ax_element_label(focused) or '无标签')
        # 慢路径: 逐字段自身 AXFocused 读回
        root, _matched = _ax_match_window_root(pid, bounds)
        if root is None:
            return None, ('读不到该应用的 AX 窗口列表 (辅助功能未授权, 或应用'
                          '不向辅助功能暴露窗口)')
        scan = _ax_entry_scan(app, root, nudge=False)
        item = _ax_pick_focused_entry(scan)
        if item is not None:
            role = _ax_role(item['element']) or '?'
            return True, '聚焦在 %s [%s] (字段自身 AXFocused 读回确认)' % (
                role, _ax_element_label(item['element']) or '无标签')
        if scan['fields'] or scan['web_areas']:
            return False, ('扫到 %d 个输入框 (网页区 %d 个), 均未聚焦 (阅览模式)'
                           % (len(scan['fields']), len(scan['web_areas'])))
        if focused is not None and _ax_role(focused) is not None:
            return False, ('聚焦在 %s (非文本输入框, 阅览模式)'
                           % _ax_role(focused))
        return None, ('扫不到输入框且聚焦元素属性读不到 (浏览器网页无障碍树'
                      '未开启?), 状态无法判定')
    except Exception as exc:
        return None, '读取聚焦状态异常: %r' % (exc,)


def focus_entry_field(target, index=0):
    """聚焦目标窗口 AX 树序第 index 个可见文本输入框 (insert 模式“进入”)。

    机制: 遍历 AX 树找输入框 (Chrome 系会先轻推开启网页无障碍树) -> 对选中
    元素写 AXFocused=True -> 轮询读回 AXFocusedUIElement 确认真的聚焦了
    (与 focus_window 的 z 序确认同风格)。全程不合成鼠标/键盘事件, 不会误点
    链接、不会输入任何字符。返回 (是否成功, 描述字符串)。
    """
    if not AX_OK:
        return False, 'AX API 不可用 (缺少 pyobjc ApplicationServices)'
    try:
        pid = int(target['pid'])
        bounds = (float(target['x']), float(target['y']),
                  float(target['w']), float(target['h']))
        app = AXUIElementCreateApplication(pid)
        root, matched = _ax_match_window_root(pid, bounds)
        if root is None:
            return False, ('读不到该应用的 AX 窗口列表 (辅助功能未授权, 或应用'
                           '不向辅助功能暴露窗口)')
        scan = _ax_entry_scan(app, root, nudge=True)
        visible = []
        for item in scan['fields']:
            pos, size = _ax_geometry(item['element'])
            if _field_visible(pos, size, bounds):
                visible.append({
                    'element': item['element'], 'in_web': item['in_web'],
                    'pos': pos, 'size': size,
                    'role': _ax_role(item['element']) or '?',
                    'label': _ax_element_label(item['element']),
                })
        if not visible:
            where = ('该窗口' if matched
                     else '窗口 bounds 匹配失败, 用第一个 AX 窗口兜底后')
            hint = ('页面可能确实没有输入框' if scan['web_areas']
                    else '若这是浏览器页面, 网页无障碍树可能未开启或页面无输入框')
            return False, ('%s没有可见的文本输入框 (遍历 %d 个 AX 节点, 网页区 '
                           '%d 个) —— %s' % (where, scan['scanned_nodes'],
                                            len(scan['web_areas']), hint))
        # 浏览器窗口里地址栏等原生输入框会混在树里: 有网页输入框时优先网页的,
        # 避免把光标聚焦到地址栏而不是页面输入框。
        page_entries = [item for item in visible if item['in_web']]
        entries = page_entries if page_entries else visible
        native_count = len(visible) - len(page_entries)
        if index < 0 or index >= len(entries):
            return False, ('可见%s共 %d 个 (网页 %d 个 + 原生 %d 个), 要聚焦的'
                           '序号 %d 超出范围' % ('网页输入框' if page_entries
                                               else '输入框', len(entries),
                                               len(page_entries), native_count,
                                               index))
        chosen = entries[index]
        scope = '网页输入框' if chosen['in_web'] else '原生输入框'
        tag = chosen['label'] or chosen['role']
        err = AXUIElementSetAttributeValue(chosen['element'], _AX_FOCUSED_ATTR,
                                           kCFBooleanTrue)
        if err != 0:
            return False, ('写 AXFocused 失败: err=%s (%s)'
                           % (err, ax_error_name(err)))

        def _focused_on_chosen(_focused_now, _err_now):
            # 信号1 (本机实测最可靠): 字段自身 AXFocused 读回 True
            e, v = _ax_own_focused(chosen['element'])
            if e == 0:
                return bool(v)
            # 信号2: app 层聚焦元素可读, 角色是输入框或几何与选中字段吻合
            if _focused_now is not None:
                if _ax_role(_focused_now) in _AX_ENTRY_ROLES:
                    return True
                return _ax_element_matches_geometry(_focused_now,
                                                    chosen['pos'],
                                                    chosen['size'])
            return False

        if _wait_until_focus(app, _focused_on_chosen):
            return True, ('已聚焦%s第 %d 个 [%s] (AX 读回确认; 网页 %d 个 + '
                          '原生 %d 个可见输入框)' % (scope, index + 1, tag,
                                                   len(page_entries),
                                                   native_count))
        return False, ('写 AXFocused 声称成功 (err=0) 但读回的聚焦状态未变 —— '
                       '该应用可能不接受 AX 聚焦, 请跑诊断看 [5] 一环')
    except Exception as exc:
        return False, '聚焦输入框异常: %r' % (exc,)


def blur_entry_field(target):
    """取消聚焦目标窗口里正聚焦的文本输入框 (insert 模式“退出”)。

    取消目标按可靠性确定: app 层聚焦元素角色可读且是输入框 -> 直接用;
    否则扫描子树找“自身 AXFocused 读回 True”的字段 (Chrome 网页字段的
    可靠信号)。取消逐级尝试并读回确认, 未确认就换下一级 (与激活兜底链
    同风格):
      1) 对该字段写 AXFocused=False —— 部分原生应用支持;
      2) 把 AX 焦点挪到容器 —— 本机实测 (Chrome): 对网页区 (AXWebArea) 写
         AXFocused=True 等价“点回页面空白处”, 字段自身 AXFocused 随即翻
         False; 网页区不存在时退而写 AX 窗口;
      3) 合成 Esc 键兜底 (区域 10) —— 部分浏览器搜索框才响应, 且会先结束
         输入法组字, 所以只在前两级都失败时才用。
    本就没有聚焦的输入框时直接返回成功 (幂等)。返回 (是否成功, 描述字符串)。
    """
    if not AX_OK:
        return False, 'AX API 不可用 (缺少 pyobjc ApplicationServices)'
    try:
        pid = int(target['pid'])
        bounds = (float(target['x']), float(target['y']),
                  float(target['w']), float(target['h']))
        app = AXUIElementCreateApplication(pid)
        root, _matched = _ax_match_window_root(pid, bounds)
        if root is None:
            return False, ('读不到该应用的 AX 窗口列表 (辅助功能未授权, 或应用'
                           '不向辅助功能暴露窗口)')
        scan = _ax_entry_scan(app, root, nudge=False)

        # 确定要取消的聚焦目标 (app 层可读 -> 直接用; 否则逐字段自身读回)
        chosen = None
        focused, err = _ax_focused_ui_element(app)
        if focused is not None:
            role = _ax_role(focused)
            if role in _AX_ENTRY_ROLES:
                chosen = {'element': focused, 'role': role,
                          'label': _ax_element_label(focused)}
        if chosen is None:
            item = _ax_pick_focused_entry(scan)
            if item is not None:
                chosen = {'element': item['element'],
                          'role': _ax_role(item['element']) or '?',
                          'label': _ax_element_label(item['element'])}
        if chosen is None:
            # 没有任何字段自称聚焦: 区分“真阅览”与“读不到”
            if scan['fields'] or scan['web_areas']:
                return True, ('扫到 %d 个输入框 (网页区 %d 个), 均未聚焦 —— '
                              '本就处于阅览模式' % (len(scan['fields']),
                                                  len(scan['web_areas'])))
            if focused is not None and _ax_role(focused) is not None:
                return True, ('当前聚焦的是 %s, 不是文本输入框 —— 本就处于阅览'
                              '模式' % _ax_role(focused))
            return False, ('扫不到输入框且聚焦元素属性读不到 (浏览器网页无障碍'
                           '树未开启?), 无法确认也无法可靠取消')

        label = chosen['label'] or chosen['role']

        def _left_entry(_focused_now, _err_now):
            # 信号1 (本机实测最可靠): 原聚焦字段自身 AXFocused 读回 False
            e, v = _ax_own_focused(chosen['element'])
            if e == 0:
                return not bool(v)
            # 信号2: app 层聚焦元素角色可读且不再是输入框 (读不到不算成功)
            if _focused_now is not None:
                now_role = _ax_role(_focused_now)
                if now_role is not None:
                    return now_role not in _AX_ENTRY_ROLES
            return False

        # 1) 对聚焦字段写 AXFocused=False
        err = AXUIElementSetAttributeValue(chosen['element'], _AX_FOCUSED_ATTR,
                                           kCFBooleanFalse)
        if err == 0 and _wait_until_focus(app, _left_entry):
            return True, ('已取消聚焦 [%s] (方式: AXFocused=False, AX 读回确认)'
                          % label)

        # 2) 焦点挪到容器: 优先网页区 (本机实测 Chrome 此路必通), 其次 AX 窗口
        containers = []
        for web_area in scan['web_areas'][:1]:
            containers.append((web_area, '网页区 (AXWebArea)'))
        containers.append((root, 'AX 窗口'))
        for container, name in containers:
            try:
                err = AXUIElementSetAttributeValue(container, _AX_FOCUSED_ATTR,
                                                   kCFBooleanTrue)
            except Exception:
                continue
            if err == 0 and _wait_until_focus(app, _left_entry):
                return True, ('已取消聚焦 [%s] (方式: 焦点挪到%s, AX 读回确认)'
                              % (label, name))

        # 3) Esc 键兜底
        esc_ok, esc_path = _press_escape_key()
        if esc_ok and _wait_until_focus(app, _left_entry):
            return True, ('已取消聚焦 [%s] (方式: Esc 键兜底, AX 读回确认)'
                          % label)
        return False, ('取消聚焦失败: [%s] 读回仍在聚焦 (AX 两级%s; 请跑诊断看 '
                       '[5] 一环)' % (label,
                                      '与 Esc 兜底都无效' if esc_ok
                                      else '无效, Esc 兜底不可用'))
    except Exception as exc:
        return False, '取消聚焦异常: %r' % (exc,)


# ===========================================================================
# 区域 10: 键盘事件兜底 -- 合成 Esc 键 (insert 模式“取消聚焦”的最后一级)
# ---------------------------------------------------------------------------
# 系统 API (Quartz CGEvent, 需“辅助功能”授权, 与 pynput 键盘监听共用同一份):
#   CGEventCreateKeyboardEvent(source, virtualKey, keyDown)
#       创建键盘事件: source 传 None (无事件来源); virtualKey 用虚拟键码
#       (HIToolbox kVK_Escape = 53); keyDown True=按下 / False=抬起。
#   CGEventPost(kCGHIDEventTap, event)
#       投入 HID 事件流, 系统把它路由给“当前聚焦的应用” —— 正是要取消聚焦
#       的那个窗口 (调用前提就是它在 z 序最前)。
# 边界: Esc 在浏览器里通常只对搜索类输入框生效 (清除并退出), 对普通输入框
#   可能无效; 且会先结束输入法的组字状态 —— 因此只作为区域 9 的 AX 两级
#   都失败后的最后兜底, 每次使用后都由调用方读回聚焦状态确认真实效果。
# ===========================================================================

_KVK_ESCAPE = 53                        # HIToolbox 虚拟键码 kVK_Escape


def _press_escape_key():
    """合成一次 Esc 按下+抬起并投入 HID 事件流; 返回 (是否成功, 描述)。"""
    if not QUARTZ_OK:
        return False, 'Quartz 不可用'
    try:
        down = Quartz.CGEventCreateKeyboardEvent(None, _KVK_ESCAPE, True)
        up = Quartz.CGEventCreateKeyboardEvent(None, _KVK_ESCAPE, False)
        if down is None or up is None:
            return False, '创建 Esc 键盘事件失败: 返回空事件'
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        time.sleep(0.01)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
        return True, 'CGEventPost -> HID 事件流 (Esc 按下+抬起)'
    except Exception as exc:
        return False, '合成 Esc 键失败: %r' % (exc,)
