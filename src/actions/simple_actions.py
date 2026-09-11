# -*- coding: utf-8 -*-
"""
simple_actions.py -- shift + W/A/S/D 快捷键动作实现。

本次实现 shift+a / shift+d: 按“屏幕横向位置”切换当前最靠前的窗口。

    shift+a  把焦点切到当前窗口“左边相邻”的窗口; 已在最左则不做任何切换
    shift+d  把焦点切到当前窗口“右边相邻”的窗口; 已在最右则不做任何切换

“左右关系”按窗口左上角在 macOS CG 全局坐标系里的 X 坐标从左到右排序得到,
天然覆盖内建屏 + 外接屏的多显示器布局。例如: 内建屏的 chatgpt 在最左、外接屏
左半的 chrome 居中、外接屏右半的 vscode 在最右, 焦点在 vscode 时:
    按 shift+a -> 切到 chrome; 再按 -> 切到 chatgpt; 已在最左, 再按不切换
    按 shift+d -> 方向相反

用到的 macOS API:
    1) Quartz.CGWindowListCopyWindowInfo(OnScreenOnly | ExcludeDesktopElements)
       一次调用拿到当前 Space 内全部可见普通窗口 (layer 0) 的 pid、应用名、
       全局坐标 bounds, 且结果自带“从前到后”的 z 序 -> 用于确定“当前窗口”。
    2) “当前窗口”直接取 CGWindowList z 序最靠前的窗口 (用户眼前的最上面一页)。
       注意: 不能用 NSWorkspace.frontmostApplication() —— 在没有 RunLoop 的
       脚本进程里它是陈旧快照, 不会随窗口切换刷新, 会导致当前窗口判定错乱、
       每次都切到同一个目标; CGWindowList 的 z 序是窗口服务器的实时状态。
    3) ApplicationServices (HIServices) 的 Accessibility API: 在目标应用内按
       位置/大小匹配到具体窗口后执行 AXRaise, 再置 AXFrontmost —— 同一应用开了
       多个窗口 (如 Chrome 多窗口) 时也能准确抬升目标窗口, 而不是只激活应用。
    4) 失败兜底: 每次切换后都用 z 序实时确认目标窗口真的到了最前, 未确认则
       依次退回 osascript (System Events)、open -b (LaunchServices)、
       NSRunningApplication.activateWithOptions_。

权限: 与 pynput 键盘监听相同, 需要在 系统设置 -> 隐私与安全性 -> 辅助功能
里给运行本脚本的终端/IDE 授权 (pynput 监听已授权的话, 这里直接复用同一份授权)。

已知边界 (有意保持简单): 只在当前 Space 内切换; 已最小化的窗口不参与切换
(OnScreenOnly 不会返回它们); 切到全屏窗口时系统会自动做 Space 切换。

铺满全屏优先 (fullscreen_only, 可在 config/config.yaml 里用 True/False 开关):
当屏幕上存在“铺满全屏”的窗口 (原生全屏, 或 Split View 左右分屏铺满) 时, 忽略
其之下的其他普通窗口, shift+a/d 只在铺满的窗口之间切换 —— 避免从分屏层切到
底下的应用时被整屏切走、破坏分屏观感。判定依据: AX 的 AXFullScreen 属性
(原生全屏与 Split View 两半都会返回 True), 兜底为 AXSubrole == AXFullScreen、
或窗口 bounds 几乎完全覆盖某块显示器。开关读取自 <项目根>/config/config.yaml
(本文件向上查找), 形如:
    fullscreen_only: True
键可写在 yaml 任意层级; 缺省视为 True (文件缺失或解析失败都用缺省值);
每次按键都会重新读取, 改动即时生效, 无需重启监听。

实现只依赖 pynput 在 macOS 上自带的 pyobjc (Quartz/AppKit/ApplicationServices),
不引入新的第三方依赖。
"""

import os
import subprocess
import time

# ---------------------------------------------------------------------------
# macOS API 导入 (pynput 在 macOS 上已依赖 pyobjc, 这里直接复用, 不新增依赖)
# ---------------------------------------------------------------------------
try:
    import Quartz                                          # pyobjc-framework-Quartz
    from AppKit import NSRunningApplication                # pyobjc-framework-Cocoa
    _QUARTZ_OK = True
except Exception:                                          # pragma: no cover
    Quartz = NSRunningApplication = None
    _QUARTZ_OK = False

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
    _AX_OK = True
except Exception:                                          # pragma: no cover
    _AX_OK = False

# AX 常量直接写成字符串/数值, 规避不同 pyobjc 版本导出差异
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

# 过滤参数: 排除输入法候选框之类的迷你窗口和几乎透明的辅助窗口
_MIN_WINDOW_WIDTH = 120
_MIN_WINDOW_HEIGHT = 80
_MIN_WINDOW_ALPHA = 0.05
# CGWindowBounds 与 AX 窗口位置匹配的容差 (pt)
_AX_MATCH_TOLERANCE = 24.0

# ---------------------------------------------------------------------------
# 可选配置接口 (config/config.yaml)
#   fullscreen_only: True/False
#   存在铺满全屏的窗口时, 是否忽略其之下的普通窗口 (True=忽略, 只切铺满窗口)
# ---------------------------------------------------------------------------
_CONFIG_RELATIVE_PATH = ('config', 'config.yaml')
_FULLSCREEN_FILTER_KEY = 'fullscreen_only'
_FULLSCREEN_FILTER_DEFAULT = True
_TRUE_VALUES = ('true', 'yes', 'on', '1')
_FALSE_VALUES = ('false', 'no', 'off', '0')


# ---------------------------------------------------------------------------
# 配置读取 (config/config.yaml -> fullscreen_only: True/False)
# ---------------------------------------------------------------------------
def _find_config_file():
    """从本文件所在目录向上查找 <项目根>/config/config.yaml; 找不到返回 None。"""
    try:
        current = os.path.dirname(os.path.abspath(__file__))
    except NameError:                                  # pragma: no cover
        return None
    for _ in range(6):
        candidate = os.path.join(current, *_CONFIG_RELATIVE_PATH)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


def _load_fullscreen_filter_enabled():
    """读取 fullscreen_only 开关; 缺省 True, 改 config 后即时生效、无需重启。

    轻量解析, 不依赖 PyYAML: 值支持 true/false/yes/no/on/off/1/0 (大小写不敏感,
    可带引号), 行内 # 注释会被忽略; 键写在 yaml 任意层级都能匹配 (先到先得)。
    文件缺失或解析失败时回退缺省值。
    """
    path = _find_config_file()
    if path is None:
        return _FULLSCREEN_FILTER_DEFAULT
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            text = fh.read()
    except Exception:
        return _FULLSCREEN_FILTER_DEFAULT
    for line in text.splitlines():
        stripped = line.split('#', 1)[0].strip()
        if ':' not in stripped:
            continue
        key, _, raw = stripped.partition(':')
        if key.strip().strip('"\'').lower() != _FULLSCREEN_FILTER_KEY:
            continue
        value = raw.strip().strip('"\'').lower()
        if value in _TRUE_VALUES:
            return True
        if value in _FALSE_VALUES:
            return False
        break                      # 键存在但值无法识别 -> 用缺省值
    return _FULLSCREEN_FILTER_DEFAULT


# ---------------------------------------------------------------------------
# shift + a / shift + d 的支撑函数
# ---------------------------------------------------------------------------
def _list_visible_windows():
    """收集当前 Space 内所有可见的普通窗口, 按 CGWindowList 原生 z 序(前->后)返回。

    元素字段: pid / owner / number / x / y / w / h
    坐标为 CG 全局坐标系 (pt): 主显示器左上角为原点, 其他显示器按系统设置的
    排列可能出现负坐标, 因此天然覆盖多显示器。
    """
    if not _QUARTZ_OK:
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
            'fullscreen': False,    # 是否铺满全屏, 由 _mark_fullscreen_windows 标记
        })
    return windows


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


def _display_bounds_list():
    """所有活动显示器的 bounds (CG 全局坐标系); 拿不到时返回空列表。"""
    if not _QUARTZ_OK:
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


def _ax_window_infos(pid):
    """拿应用 pid 的全部 AX 窗口及其位置/大小; 失败返回空列表。

    返回元素: {'element': AX窗口对象, 'pos': (x, y), 'size': (w, h)}
    """
    if not _AX_OK:
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


def _mark_fullscreen_windows(windows):
    """为窗口列表就地标记“铺满全屏”状态 (原生全屏或 Split View 分屏铺满)。

    返回被标记为铺满的窗口数量。AX 不可用时退化为纯 bounds 判定
    (此时只能识别覆盖整块显示器的原生全屏)。
    """
    if not windows:
        return 0
    displays = _display_bounds_list()
    if not _AX_OK:
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


def _bundle_id_for_pid(pid):
    """拿 pid 对应应用的 bundle id。

    NSRunningApplication 依赖 RunLoop 维护运行列表, 在无 RunLoop 的脚本进程里
    经常查不到 (诊断 [8] 未找到的同族问题), 因此再用 System Events 兜底一次。
    """
    if _QUARTZ_OK:
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
    if not _QUARTZ_OK:
        return False
    try:
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))
        if app is None:
            return False
        return bool(app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps))
    except Exception:
        return False


def _ax_raise_and_focus(pid, bounds):
    """用 Accessibility API 抬升目标窗口并把应用置前, 成功返回 True。"""
    if not _AX_OK:
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


def _is_window_top(target):
    """检查目标窗口现在是否就是全屏幕 z 序最靠前的窗口 (实时查 CGWindowList)。"""
    if not _QUARTZ_OK:
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


def _focus_window(target):
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


def _switch_window(direction, label):
    """在按 X 坐标“从左到右”排序的可见窗口里, 把焦点移到当前窗口相邻的窗口。

    direction: -1 = 左边相邻窗口 (shift+a), +1 = 右边相邻窗口 (shift+d)。
    不循环: 已在最左再按 a、已在最右再按 d, 都不做任何切换。
    """
    if not _QUARTZ_OK:
        print('[%s] 缺少 pyobjc (Quartz/AppKit)。pynput 在 macOS 上会自动安装它,'
              '请检查当前 Python 环境' % label)
        return

    windows = _list_visible_windows()
    if len(windows) < 2:
        print('[%s] 屏幕上可见的普通窗口不足 2 个, 无需切换' % label)
        return

    # fullscreen_only (config/config.yaml): 存在铺满全屏的窗口 (原生全屏或
    # Split View 分屏) 时, 忽略其之下的普通窗口, 只在铺满的窗口之间切换,
    # 避免从分屏层切到底下的应用时被整屏切走。
    filtered = False
    if _load_fullscreen_filter_enabled():
        _mark_fullscreen_windows(windows)
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
    current = windows[0]           # _list_visible_windows 保持 z 序 (前 -> 后)

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

    if not _focus_window(target):
        print('[%s] 切换失败 (目标: %s, pid %s)。请在运行监听脚本的同一终端里执行 '
              '"python src/actions/simple_actions.py" 查看是哪一环失败; 最常见原因是 '
              '系统设置 -> 隐私与安全性 -> 辅助功能 没有授予运行脚本的终端/IDE' % (
                  label, target['owner'], target['pid']))


# ---------------------------------------------------------------------------
# 快捷键动作 (键盘监控部分调用下面这些函数, 签名保持不变)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# 诊断模式: 直接运行本文件, 在与监听脚本相同的环境里自检切换链路
#   python src/actions/simple_actions.py            # 只读检查
#   python src/actions/simple_actions.py 目标pid    # 只读检查 + 实测切换到该应用
# ---------------------------------------------------------------------------
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


def _print_diagnostics(target_pid=None):
    """打印授权与切换链路每一环的真实状态, 用于定位“切换失败”。"""
    print('== simple_actions 诊断 ==')
    print('[1] 模块可用性: Quartz=%s, AX API=%s' % (_QUARTZ_OK, _AX_OK))
    trusted = False
    if _AX_OK:
        trusted = bool(AXIsProcessTrusted())
        print('[2] 辅助功能授权 AXIsProcessTrusted: %s' % trusted)
    if not _QUARTZ_OK:
        print('!! Quartz 不可用, 诊断终止')
        return
    windows = _list_visible_windows()
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
    if _AX_OK:
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
                top = _list_visible_windows()
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
    if _QUARTZ_OK:
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


if __name__ == '__main__':
    import sys as _sys
    _print_diagnostics(_sys.argv[1] if len(_sys.argv) > 1 else None)
