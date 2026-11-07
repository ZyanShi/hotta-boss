import sys
import os
import ctypes
import threading
import time
import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont
import keyboard
import pygetwindow as gw
import pyautogui
import win32api
import win32con
import win32gui
import win32process
import psutil
import cv2
import numpy as np
from PIL import ImageGrab

# ========== DPI 感知（必须在创建 Tk 窗口前执行） ==========
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()    # 旧系统兜底
    except Exception:
        pass

try:
    cv2.setNumThreads(1)
    cv2.setUseOptimized(True)
except Exception:
    pass

# ============================================================
# 逻辑分辨率（所有硬编码坐标以此为准）
# 支持任意分辨率的窗口，前提宽高比保持 16:9
#   1080p → scale=1.0   720p → scale=0.6667   1440p → scale=1.3333
# ============================================================
LOGICAL_WIDTH = 1920
LOGICAL_HEIGHT = 1080

FORCE_ADMIN = True

APP_VERSION = "2.3.0"       # ← 新增：当前版本号，发新版时改这里
VERSION_URL = "https://raw.githubusercontent.com/ZyanShi/hotta-boss/refs/heads/main/version.json"


# ========== 资源路径兼容 ==========
def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


# ========== 窗口图标 ==========
def apply_window_icon(root):
    try:
        ico = resource_path("app.ico")
        if os.path.exists(ico):
            root.iconbitmap(ico)
            return
    except Exception:
        pass
    try:
        ico = resource_path("app.ico")
        if os.path.exists(ico):
            from PIL import Image, ImageTk
            img = Image.open(ico)
            root.iconphoto(True, ImageTk.PhotoImage(img))
    except Exception:
        pass


# ========== 管理员权限 ==========
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def ensure_admin():
    if not FORCE_ADMIN:
        return True
    if is_admin():
        return True
    print("需要管理员权限以操作游戏窗口，正在请求提权...")
    try:
        if getattr(sys, 'frozen', False):
            exe = sys.executable
            params = ' '.join(f'"{a}"' for a in sys.argv[1:])
        else:
            exe = sys.executable
            script = os.path.abspath(sys.argv[0])
            params = f'"{script}"'
            if len(sys.argv) > 1:
                params += ' ' + ' '.join(f'"{a}"' for a in sys.argv[1:])
        ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        if ret <= 32:
            print(f"提权失败，返回码: {ret}")
            return False
    except Exception as e:
        print(f"提权异常: {e}")
        return False
    return False


# ========== 降低自身进程优先级 ==========
def lower_own_priority():
    try:
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass


# ============================================================
# 游戏窗口绑定 + 逻辑坐标换算
# ============================================================
class GameWindow:
    """绑定 QRSL.exe 窗口客户区，提供逻辑坐标(1920x1080) ↔ 屏幕坐标换算。"""

    def __init__(self):
        self.hwnd = None
        self.left = 0
        self.top = 0
        self.width = LOGICAL_WIDTH
        self.height = LOGICAL_HEIGHT
        self.scale_x = 1.0
        self.scale_y = 1.0

    def bind(self, hwnd):
        self.hwnd = hwnd
        ok = self.refresh()
        if ok:
            ratio = self.width / self.height if self.height else 0
            print(f"窗口绑定: hwnd={hwnd}, 客户区={self.width}x{self.height}, "
                  f"左上角=({self.left},{self.top}), "
                  f"scale=({self.scale_x:.4f},{self.scale_y:.4f}), "
                  f"宽高比={ratio:.3f}")
            if abs(ratio - 16 / 9) > 0.02:
                print("⚠️ 宽高比不是 16:9，模板匹配会失真，建议改成 16:9 分辨率")
            try:
                c = pixel_logical(22, 63)
                print(f"[诊断] 逻辑(22,63) 当前像素={c}（主页蓝=(62,166,237)）")
            except Exception as e:
                print(f"[诊断] 读像素失败: {e}")
        else:
            print(f"⚠️ 窗口绑定失败（refresh 返回 False），hwnd={hwnd}")
        return ok

    def refresh(self):
        """刷新窗口客户区位置/尺寸（窗口可能被移动/缩放）。"""
        if not self.hwnd:
            return False
        try:
            client_rect = win32gui.GetClientRect(self.hwnd)
            client_w = client_rect[2]
            client_h = client_rect[3]
            if client_w <= 0 or client_h <= 0:
                return False
            pt = win32gui.ClientToScreen(self.hwnd, (0, 0))
            self.left = pt[0]
            self.top = pt[1]
            self.width = client_w
            self.height = client_h
            self.scale_x = client_w / LOGICAL_WIDTH
            self.scale_y = client_h / LOGICAL_HEIGHT
            return True
        except Exception as e:
            print(f"refresh 失败: {e}")
            return False

    def to_screen(self, x, y):
        return int(self.left + x * self.scale_x), int(self.top + y * self.scale_y)

    def to_logical(self, sx, sy):
        if self.scale_x <= 0 or self.scale_y <= 0:
            return sx, sy
        return int((sx - self.left) / self.scale_x), int((sy - self.top) / self.scale_y)

    def grab_pil(self):
        self.refresh()
        return ImageGrab.grab(bbox=(self.left, self.top,
                                    self.left + self.width,
                                    self.top + self.height))

    def grab_gray_logical(self):
        img = self.grab_pil()
        gray = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)
        if gray.shape[1] != LOGICAL_WIDTH or gray.shape[0] != LOGICAL_HEIGHT:
            gray = cv2.resize(gray, (LOGICAL_WIDTH, LOGICAL_HEIGHT),
                              interpolation=cv2.INTER_LINEAR)
        return gray


game_window = GameWindow()


# ========== 逻辑坐标辅助函数 ==========
def click_logical(x, y):
    sx, sy = game_window.to_screen(x, y)
    pyautogui.click(sx, sy)


def pixel_logical(x, y):
    sx, sy = game_window.to_screen(x, y)
    return pyautogui.pixel(sx, sy)


def moveTo_logical(x, y):
    sx, sy = game_window.to_screen(x, y)
    pyautogui.moveTo(sx, sy)


# ========== 按进程名查找 QRSL.exe 窗口 ==========
def find_qrsl_window():
    target = "qrsl.exe"
    candidates = []

    def enum_cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid)
            if proc.name().lower() != target:
                return
            rect = win32gui.GetWindowRect(hwnd)
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            if w > 400 and h > 300:
                title = win32gui.GetWindowText(hwnd)
                candidates.append((w * h, hwnd, title))
        except Exception:
            pass

    win32gui.EnumWindows(enum_cb, None)
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def activate_game_window(hwnd):
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        game_window.bind(hwnd)
        return True
    except Exception as e:
        print(f"激活窗口失败: {e}")
        try:
            wins = gw.getWindowsWithTitle('幻塔') or gw.getWindowsWithTitle('Tower of Fantasy')
            if wins:
                win = wins[0]
                if win.isMinimized:
                    win.restore()
                win.activate()
                game_window.bind(win._hWnd)
                return True
        except Exception as e2:
            print(f"备用激活也失败: {e2}")
        return False


# ========== 工具函数 ==========
def scroll_wheel(amount: int, x: int = None, y: int = None):
    if x is not None and y is not None:
        moveTo_logical(x, y)
    win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, int(amount * 120), 0)
    time.sleep(0.2)


def interruptible_sleep(stop_event, seconds):
    start = time.time()
    while time.time() - start < seconds:
        if stop_event.is_set():
            return False
        time.sleep(0.1)
    return True


# ========== 图像识别 ==========
def load_image_template(image_path):
    try:
        template = cv2.imread(image_path, 0)
        if template is None:
            return None, 0, 0
        h, w = template.shape
        return template, w, h
    except Exception:
        return None, 0, 0


def find_image_and_click(image_path, stop_event, timeout=10, confidence=0.75):
    if not os.path.exists(image_path):
        print(f"图片不存在: {image_path}")
        return False
    template, tw, th = load_image_template(image_path)
    if template is None:
        print(f"加载模板失败: {image_path}")
        return False
    scaled = []
    for s in (0.7, 0.85, 1.0):
        w2 = max(1, int(tw * s))
        h2 = max(1, int(th * s))
        t = cv2.resize(template, (w2, h2), interpolation=cv2.INTER_AREA)
        scaled.append((t, w2, h2))

    name = os.path.basename(image_path)
    start = time.time()
    while time.time() - start < timeout:
        if stop_event.is_set():
            return False
        try:
            gray = game_window.grab_gray_logical()
            gh, gw_ = gray.shape
            best = None
            for t, w2, h2 in scaled:
                if h2 >= gh or w2 >= gw_:
                    continue
                res = cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED)
                _, mx, _, ml = cv2.minMaxLoc(res)
                if best is None or mx > best[0]:
                    best = (mx, ml, w2, h2)
            if best and best[0] >= confidence:
                cx = best[1][0] + best[2] // 2
                cy = best[1][1] + best[3] // 2
                print(f"找到 {name} 逻辑坐标=({cx},{cy}) conf={best[0]:.2f}")
                click_logical(cx, cy)
                return True
        except Exception as e:
            print(f"找图异常: {e}")
        time.sleep(0.3)
    print(f"超时 {timeout}s 未找到 {name}")
    return False


# ========== 主页检测 ==========
def wait_for_main_page(stop_event):
    target_color = (62, 166, 237)
    print("等待主页面...")
    while not stop_event.is_set():
        try:
            if pixel_logical(22, 63) == target_color:
                print("主页面已出现")
                return True
        except Exception as e:
            print(f"像素读取异常: {e}")
        time.sleep(0.5)
    print("wait_for_main_page 因 stop_event 触发而退出")
    return False


def wait_for_main_page_with_esc(stop_event, timeout=30):
    target_color = (62, 166, 237)
    print(f"等待主页面（超时{timeout}秒后按ESC重试）...")
    while not stop_event.is_set():
        start = time.time()
        while not stop_event.is_set():
            try:
                if pixel_logical(22, 63) == target_color:
                    print("主页面已出现")
                    return True
            except Exception:
                pass
            if time.time() - start >= timeout:
                print("等待主页面超时，按ESC一次...")
                pyautogui.press('esc')
                time.sleep(0.5)
                break
            time.sleep(0.5)
    print("wait_for_main_page_with_esc 因 stop_event 触发而退出")
    return False


# ========== 战斗检测点（逻辑坐标） ==========
BATTLE_POINTS = [
    (1216, 157, (47, 209, 161)),
    (22, 410, (62, 166, 237)),
    (758, 975, (255, 255, 255))
]


def is_any_battle_point():
    try:
        for x, y, target in BATTLE_POINTS:
            if pixel_logical(x, y) == target:
                return True
        return False
    except Exception:
        return False


# ========== 玛格玛：传送海嘉德 ==========
def transport_haijiade(stop_event):
    if stop_event.is_set():
        return False
    print("传送海嘉德开始...")
    pyautogui.press('m')
    if not interruptible_sleep(stop_event, 1.0):
        return False
    click_logical(535, 930)
    if not interruptible_sleep(stop_event, 5.0):
        return False
    print("传送海嘉德完成")
    return True


# ========== 玛格玛：传送玛格玛 ==========
def transport_magema(stop_event, boss_name="玛格玛"):
    if stop_event.is_set():
        return False
    print("传送玛格玛开始...")
    pyautogui.press('m')
    if not interruptible_sleep(stop_event, 1.0):
        return False
    click_logical(350, 940)
    if not interruptible_sleep(stop_event, 1.0):
        return False
    if boss_name == "玛格玛":
        click_logical(1725, 930)
        if not interruptible_sleep(stop_event, 1.0):
            return False
        scroll_wheel(-5, x=1725, y=930)
        if not interruptible_sleep(stop_event, 1.0):
            return False
        click_logical(1725, 930)
        if not interruptible_sleep(stop_event, 1.0):
            return False
    click_logical(1835, 560)
    if not interruptible_sleep(stop_event, 1.0):
        return False
    click_logical(1740, 840)
    if not interruptible_sleep(stop_event, 5.0):
        return False
    print("传送玛格玛完成")
    return True


# ========== 哈伯拉：传送九域 ==========
def transport_jiuyu(stop_event):
    if stop_event.is_set():
        return False
    print("传送九域开始...")
    pyautogui.keyDown('alt')
    pyautogui.press('3')
    pyautogui.keyUp('alt')
    if not interruptible_sleep(stop_event, 2.0):
        return False
    click_logical(135, 625)
    if not interruptible_sleep(stop_event, 2.0):
        return False
    click_logical(520, 720)
    if not interruptible_sleep(stop_event, 2.0):
        return False
    click_logical(1260, 580)
    if not interruptible_sleep(stop_event, 5.0):
        return False
    print("传送九域完成")
    return True


# ========== 哈伯拉：传送哈伯拉（识别 habola.png） ==========
def transport_habola(stop_event):
    if stop_event.is_set():
        return False
    print("传送哈伯拉开始...")
    pyautogui.press('m')
    if not interruptible_sleep(stop_event, 2.0):
        return False
    click_logical(350, 940)
    if not interruptible_sleep(stop_event, 2.0):
        return False

    habola_img = resource_path("habola.png")
    if not os.path.exists(habola_img):
        habola_img = resource_path("images/habola.png")
    print("识别并点击 habola.png（超时 10s）...")
    ok = find_image_and_click(habola_img, stop_event, timeout=10, confidence=0.75)
    if not ok:
        if stop_event.is_set():
            return False
        print("未找到 habola.png，按 ESC 回到主页，本轮从头开始")
        try:
            pyautogui.press('esc')
        except Exception:
            pass
        return False
    if not interruptible_sleep(stop_event, 1.0):
        return False

    click_logical(1835, 610)
    if not interruptible_sleep(stop_event, 2.0):
        return False
    click_logical(1750, 850)
    if not interruptible_sleep(stop_event, 5.0):
        return False
    print("传送哈伯拉完成")
    return True


# ========== 玛格玛：准备动作 ==========
def prepare_for_battle_magema(stop_event, source_key, weapon_key, bubble_key):
    print("准备动作 1/5：反复按 Space 回主页")
    target_color = (62, 166, 237)
    while not stop_event.is_set():
        try:
            if pixel_logical(22, 63) == target_color:
                print("已回到主页")
                break
        except Exception:
            pass
        pyautogui.press('space')
        time.sleep(0.1)
    if stop_event.is_set():
        return False

    if source_key:
        print(f"准备动作 2/5：按源器1（{source_key}），延迟 3 秒")
        pyautogui.press(source_key)
        if not interruptible_sleep(stop_event, 3.0):
            return False

    if weapon_key:
        print(f"准备动作 3/5：按武器（{weapon_key}）")
        pyautogui.press(weapon_key)
        time.sleep(0.2)

    print("准备动作 4/5：按住 W + 右键 2 秒")
    pyautogui.keyDown('w')
    time.sleep(0.1)
    pyautogui.mouseDown(button='right')
    start = time.time()
    while time.time() - start < 2.0:
        if stop_event.is_set():
            pyautogui.mouseUp(button='right')
            pyautogui.keyUp('w')
            return False
        time.sleep(0.05)
    pyautogui.mouseUp(button='right')
    pyautogui.keyUp('w')

    if bubble_key:
        print(f"准备动作 5/5：按源器2（{bubble_key}）")
        pyautogui.press(bubble_key)
        time.sleep(0.1)

    print("准备动作完成")
    return True


# ========== 哈伯拉：准备动作（源器1 → 3s → 武器） ==========
def prepare_for_battle_habola(stop_event, source_key, weapon_key):
    if source_key:
        print(f"执行源器1准备（{source_key}），延迟 3 秒...")
        pyautogui.press(source_key)
        if not interruptible_sleep(stop_event, 3.0):
            return False
    if stop_event.is_set():
        return False
    if weapon_key:
        print(f"执行武器准备（{weapon_key}）...")
        pyautogui.press(weapon_key)
        time.sleep(0.2)
    if stop_event.is_set():
        return False
    print("准备动作完成后延迟1秒...")
    if not interruptible_sleep(stop_event, 1.0):
        return False
    print("准备动作完成")
    return True


# ========== 玛格玛：战斗循环 ==========
def battle_loop_magema(stop_event, bubble_key, timeout):
    attack_stop = threading.Event()

    def attack_worker():
        while not attack_stop.is_set() and not stop_event.is_set():
            start = time.time()
            while time.time() - start < 25:
                if attack_stop.is_set() or stop_event.is_set():
                    return
                time.sleep(0.1)

            pyautogui.keyDown('s')
            start = time.time()
            while time.time() - start < 1.4:
                if attack_stop.is_set() or stop_event.is_set():
                    pyautogui.keyUp('s')
                    return
                time.sleep(0.05)
            pyautogui.keyUp('s')

            start = time.time()
            while time.time() - start < 0.5:
                if attack_stop.is_set() or stop_event.is_set():
                    return
                time.sleep(0.05)

            pyautogui.keyDown('w')
            pyautogui.mouseDown(button='right')
            start = time.time()
            while time.time() - start < 1.0:
                if attack_stop.is_set() or stop_event.is_set():
                    pyautogui.mouseUp(button='right')
                    pyautogui.keyUp('w')
                    return
                time.sleep(0.05)
            pyautogui.mouseUp(button='right')
            pyautogui.keyUp('w')

            if bubble_key:
                try:
                    pyautogui.press(bubble_key)
                except Exception:
                    pass

    attack_thread = threading.Thread(target=attack_worker, daemon=True)
    attack_thread.start()

    print(f"等待进入战斗（超时 {timeout} 秒）...")
    entered = False
    enter_start = time.time()
    while not stop_event.is_set():
        if is_any_battle_point():
            entered = True
            print("已进入战斗")
            break
        if time.time() - enter_start >= timeout:
            print(f"等待进入战斗超时（{timeout}秒），重头再来")
            break
        time.sleep(0.5)

    if not entered:
        attack_stop.set()
        pyautogui.keyUp('s')
        pyautogui.keyUp('w')
        pyautogui.mouseUp(button='right')
        attack_thread.join(timeout=1)
        return False

    print("战斗进行中，等待战斗结束...")
    while not stop_event.is_set():
        if not is_any_battle_point():
            print("战斗结束")
            break
        time.sleep(0.5)

    attack_stop.set()
    pyautogui.keyUp('s')
    pyautogui.keyUp('w')
    pyautogui.mouseUp(button='right')
    attack_thread.join(timeout=1)
    return True


# ========== 哈伯拉：战斗循环 ==========
def habola_battle_loop(stop_event, timeout, source_key):
    attack_stop = threading.Event()

    def attack_worker():
        while not attack_stop.is_set() and not stop_event.is_set():
            pyautogui.mouseDown(button='left')
            start = time.time()
            while time.time() - start < 60:
                if attack_stop.is_set() or stop_event.is_set():
                    pyautogui.mouseUp(button='left')
                    return
                time.sleep(0.1)
            pyautogui.mouseUp(button='left')

            start = time.time()
            while time.time() - start < 10:
                if attack_stop.is_set() or stop_event.is_set():
                    return
                time.sleep(0.1)

            if source_key:
                try:
                    pyautogui.press(source_key)
                except Exception:
                    pass

            start = time.time()
            while time.time() - start < 2:
                if attack_stop.is_set() or stop_event.is_set():
                    return
                time.sleep(0.1)

    attack_thread = threading.Thread(target=attack_worker, daemon=True)
    attack_thread.start()

    print(f"等待进入战斗（超时 {timeout} 秒）...")
    entered = False
    start = time.time()
    while not stop_event.is_set():
        if is_any_battle_point():
            entered = True
            print("已进入战斗")
            break
        if time.time() - start >= timeout:
            print("等待进入战斗超时，停止攻击，从头开始")
            break
        time.sleep(0.5)

    if not entered:
        attack_stop.set()
        pyautogui.mouseUp(button='left')
        attack_thread.join(timeout=1)
        return False

    print("等待战斗结束...")
    while not stop_event.is_set():
        if not is_any_battle_point():
            print("战斗结束")
            break
        time.sleep(0.5)

    attack_stop.set()
    pyautogui.mouseUp(button='left')
    attack_thread.join(timeout=1)
    return True


# ========== 宝箱模块 ==========
class ChestModule:
    def __init__(self, chest_image_path):
        self.chest_image_path = chest_image_path
        self.chest_search_params = {'confidence_threshold': 0.75}
        self.reward_type = "yaxia"

        self._template, self._tw, self._th = load_image_template(chest_image_path)
        self._scaled_templates = []
        if self._template is not None:
            for s in (0.7, 0.85, 1.0):
                tw = max(1, int(self._tw * s))
                th = max(1, int(self._th * s))
                t = cv2.resize(self._template, (tw, th),
                               interpolation=cv2.INTER_AREA)
                self._scaled_templates.append((s, t, tw, th))

    def search_chest_fast(self, confidence=None):
        if confidence is None:
            confidence = self.chest_search_params['confidence_threshold']
        if not self._scaled_templates:
            return None, 0.0
        try:
            gray = game_window.grab_gray_logical()
            gh, gw_ = gray.shape
            best = None
            for s, t, tw, th in self._scaled_templates:
                if th >= gh or tw >= gw_:
                    continue
                res = cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED)
                _, mx, _, ml = cv2.minMaxLoc(res)
                if best is None or mx > best[0]:
                    best = (mx, ml, tw, th)
            if best and best[0] >= confidence:
                cx = best[1][0] + best[2] // 2
                cy = best[1][1] + best[3] // 2
                return (cx, cy), best[0]
            return None, 0.0
        except Exception:
            return None, 0.0

    def smooth_move_mouse(self, dx, dy, mouse_base_speed=3, smooth_factor=0.2,
                          dead_zone=5, max_single_move=200):
        try:
            if abs(dx) < dead_zone and abs(dy) < dead_zone:
                return
            smooth_dx = dx * smooth_factor
            smooth_dy = dy * smooth_factor
            smooth_dx = np.clip(smooth_dx, -max_single_move, max_single_move)
            smooth_dy = np.clip(smooth_dy, -max_single_move, max_single_move)
            final_dx = int(smooth_dx * mouse_base_speed)
            final_dy = int(smooth_dy * mouse_base_speed)
            if abs(final_dx) > 0 or abs(final_dy) > 0:
                win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, final_dx, final_dy, 0, 0)
        except Exception:
            pass

    def smart_lock_chest_to_center(self):
        if not self._scaled_templates:
            return False, 0.0
        start = time.time()
        max_time = 15
        screen_center_x, screen_center_y = 960, 540
        center_rect = (940, 500, 980, 580)
        while time.time() - start < max_time:
            pos, conf = self.search_chest_fast()
            if pos:
                cx, cy = pos
                left, top, right, bottom = center_rect
                if left <= cx <= right and top <= cy <= bottom:
                    return True, time.time() - start
                offset_x = cx - screen_center_x
                offset_y = cy - screen_center_y
                self.smooth_move_mouse(offset_x, offset_y,
                                       mouse_base_speed=3, smooth_factor=0.2)
                time.sleep(0.1)
            else:
                time.sleep(0.15)
        return False, time.time() - start

    def center_lock_pickup_phase(self, stop_event):
        interrupt = threading.Event()
        gold_found = threading.Event()

        def hold_w_and_right():
            try:
                pyautogui.keyDown('w')
                pyautogui.mouseDown(button='right')
                time.sleep(0.05)
                pyautogui.mouseUp(button='right')
            except Exception:
                pass
            start = time.time()
            while (not interrupt.is_set() and not stop_event.is_set()
                   and time.time() - start < 10):
                time.sleep(0.1)
            try:
                pyautogui.keyUp('w')
            except Exception:
                pass

        def press_f_and_detect_gold():
            start = time.time()
            while (not interrupt.is_set() and not stop_event.is_set()
                   and time.time() - start < 10):
                try:
                    pyautogui.press('f')
                except Exception:
                    pass
                try:
                    if pixel_logical(1840, 30) == (210, 173, 72):
                        gold_found.set()
                        interrupt.set()
                        break
                except Exception:
                    pass
                time.sleep(0.2)

        wth = threading.Thread(target=hold_w_and_right, daemon=True)
        fth = threading.Thread(target=press_f_and_detect_gold, daemon=True)
        wth.start()
        fth.start()
        fth.join(timeout=11)
        interrupt.set()
        wth.join(timeout=1)
        time.sleep(0.2)
        try:
            pyautogui.keyUp('w')
        except Exception:
            pass

        if stop_event.is_set() or not gold_found.is_set():
            print("10 秒内未检测到金色，按 ESC 从头开始")
            try:
                pyautogui.press('esc')
            except Exception:
                pass
            return False

        click_logical(1665, 764)
        time.sleep(2)
        click_logical(1255, 575)
        time.sleep(10)
        return True

    def south_sound_pickup_phase(self, stop_event):
        interrupt = threading.Event()
        gold_found = threading.Event()

        def press_f():
            while not interrupt.is_set() and not stop_event.is_set():
                pyautogui.press('f')
                time.sleep(0.05)

        def detect_gold():
            start = time.time()
            while not interrupt.is_set() and not stop_event.is_set() and time.time() - start < 20:
                try:
                    if pixel_logical(1840, 30) == (210, 173, 72):
                        gold_found.set()
                        interrupt.set()
                        break
                except Exception:
                    pass
                time.sleep(0.1)

        fth = threading.Thread(target=press_f, daemon=True)
        dth = threading.Thread(target=detect_gold, daemon=True)
        fth.start()
        dth.start()
        dth.join(timeout=20)
        interrupt.set()
        time.sleep(0.2)

        if stop_event.is_set() or not gold_found.is_set():
            print("等待金色超时或已停止，放弃本次宝箱，重新开始下一轮")
            return False

        click_logical(1665, 764)
        time.sleep(2)
        click_logical(1255, 575)
        time.sleep(10)
        return True

    def process_chest(self, stop_event):
        MAX_CHEST_RETRIES = 3

        pos, conf = None, 0.0
        for attempt in range(1, MAX_CHEST_RETRIES + 1):
            if stop_event.is_set():
                return False

            print(f"中心锁定：按住空格 5 秒（第 {attempt}/{MAX_CHEST_RETRIES} 次）")
            try:
                pyautogui.keyDown('space')
            except Exception:
                pass
            start = time.time()
            aborted = False
            while time.time() - start < 5.0:
                if stop_event.is_set():
                    aborted = True
                    break
                time.sleep(0.1)
            try:
                pyautogui.keyUp('space')
            except Exception:
                pass
            if aborted:
                return False

            print(f"中心锁定：全屏搜索宝箱（第 {attempt}/{MAX_CHEST_RETRIES} 次）")
            pos, conf = self.search_chest_fast()
            if pos and conf >= 0.75:
                print(f"找到宝箱 pos={pos} conf={conf:.2f}")
                break

            print(f"第 {attempt} 次未找到宝箱，1 秒后重试（不按 ESC）...")
            if not interruptible_sleep(stop_event, 1.0):
                return False

        if not pos or conf < 0.75:
            print("多次重试仍未找到宝箱，返回主流程重新开始（不按 ESC）")
            return False

        print("中心锁定：转动视角朝宝箱")
        self.smart_lock_chest_to_center()
        time.sleep(0.2)

        print("中心锁定：向前跑 5 秒接近宝箱")
        try:
            pyautogui.keyDown('w')
            pyautogui.mouseDown(button='right')
            time.sleep(0.05)
            pyautogui.mouseUp(button='right')
        except Exception:
            pass
        start = time.time()
        aborted = False
        while time.time() - start < 5.0:
            if stop_event.is_set():
                aborted = True
                break
            time.sleep(0.1)
        try:
            pyautogui.keyUp('w')
        except Exception:
            pass
        if aborted:
            return False

        print("中心锁定：再次搜索宝箱并锁定到中心")
        self.smart_lock_chest_to_center()
        time.sleep(0.2)

        return self.center_lock_pickup_phase(stop_event)

    def execute_south_sound_route(self, stop_event):
        if stop_event.is_set():
            return False
        print("执行南音传送路线...")
        pyautogui.press('m')
        if not interruptible_sleep(stop_event, 1.0):
            return False
        click_logical(960, 540)
        if not interruptible_sleep(stop_event, 1.0):
            return False
        click_logical(1400, 600)
        if not interruptible_sleep(stop_event, 1.0):
            return False
        click_logical(1645, 850)
        if not interruptible_sleep(stop_event, 1.0):
            return False
        click_logical(1250, 580)
        if not interruptible_sleep(stop_event, 8.0):
            return False
        if not wait_for_main_page(stop_event):
            return False
        self.south_sound_pickup_phase(stop_event)
        print("南音传送路线完成")
        return True


# ========== 玛格玛主流程 ==========
def flow_magema(stop_event, boss_name, source_key, weapon_key, bubble_key,
                timeout, chest_module, chest_mode_var):
    while not stop_event.is_set():
        game_window.refresh()
        if not wait_for_main_page_with_esc(stop_event, timeout=30):
            break
        if not transport_haijiade(stop_event):
            break
        if not wait_for_main_page(stop_event):
            break
        if not transport_magema(stop_event, boss_name):
            break
        if not prepare_for_battle_magema(stop_event, source_key, weapon_key, bubble_key):
            break
        if not bubble_key:
            print("未设置源器2按键，跳过战斗")
            break

        battle_result = battle_loop_magema(stop_event, bubble_key, timeout)
        if stop_event.is_set():
            break
        if not battle_result:
            print("超时未进入战斗，重新开始本轮流程")
            continue

        chest_mode = chest_mode_var.get()
        if chest_mode == "中心锁定":
            print("战斗结束，开始中心锁定宝箱...")
            chest_module.process_chest(stop_event)
        else:
            print("战斗结束，开始南音传送路线...")
            chest_module.execute_south_sound_route(stop_event)
        print("本轮结束，继续下一轮")


# ========== 哈伯拉主流程 ==========
def flow_habola(stop_event, source_key, weapon_key, timeout,
                chest_module, chest_mode_var):
    while not stop_event.is_set():
        game_window.refresh()
        if not wait_for_main_page_with_esc(stop_event, timeout=30):
            break
        if not transport_jiuyu(stop_event):
            break
        if not wait_for_main_page(stop_event):
            break

        ret = transport_habola(stop_event)
        if ret is False:
            if stop_event.is_set():
                break
            print("哈伯拉传送失败（未找到 habola.png），本轮从头开始")
            continue

        if not wait_for_main_page(stop_event):
            break
        if not prepare_for_battle_habola(stop_event, source_key, weapon_key):
            break
        entered_and_finished = habola_battle_loop(stop_event, timeout, source_key)
        if stop_event.is_set():
            break
        if not entered_and_finished:
            print("哈伯拉：等待进入战斗超时，重新开始本轮流程")
            continue

        chest_mode = chest_mode_var.get()
        if chest_mode == "中心锁定":
            print("战斗结束，开始中心锁定宝箱...")
            chest_module.process_chest(stop_event)
        else:
            print("战斗结束，开始南音传送路线...")
            chest_module.execute_south_sound_route(stop_event)
        print("本轮结束，继续下一轮")


# ========== 主任务入口 ==========
def user_task(stop_event, boss_var, source_key_var, weapon_key_var, timeout_var, bubble_key_var,
              chest_module, chest_mode_var):
    print("任务线程启动")
    try:
        timeout = int(timeout_var.get())
    except Exception:
        timeout = 180

    boss = boss_var.get()
    source_key = source_key_var.get().strip().lower()
    weapon_key = weapon_key_var.get().strip().lower()
    bubble_key = bubble_key_var.get().strip().lower()

    if boss == "哈伯拉":
        print("=== 哈伯拉流程 ===")
        flow_habola(stop_event, source_key, weapon_key, timeout,
                    chest_module, chest_mode_var)
    else:
        print("=== 玛格玛流程 ===")
        flow_magema(stop_event, boss, source_key, weapon_key, bubble_key,
                    timeout, chest_module, chest_mode_var)
    print("任务线程停止")


# ============================================================
# 现代化 UI
# ============================================================
C_BG = "#F2F2F7"
C_CARD = "#FFFFFF"
C_FILL = "#F2F2F7"
C_TEXT = "#1C1C1E"
C_TEXT2 = "#8E8E93"
C_SEP = "#E5E5EA"
C_ACCENT = "#007AFF"
C_ACCENT_HOVER = "#0063D1"
C_ACCENT_DIS = "#BFDBFF"
C_GREEN = "#34C759"
C_DANGER = "#FF3B30"
C_BTN_GRAY = "#E9E9EE"
C_BTN_GRAY_HOVER = "#DEDEE3"
C_BTN_DIS = "#F2F2F7"
C_BTN_DIS_FG = "#C7C7CC"


def _round_points(x1, y1, x2, y2, r):
    return [
        x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1,
        x2, y1, x2, y1 + r, x2, y1 + r, x2, y2 - r,
        x2, y2 - r, x2, y2, x2 - r, y2, x2 - r, y2,
        x1 + r, y2, x1 + r, y2, x1, y2, x1, y2 - r,
        x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1,
    ]


class RoundedButton(tk.Canvas):
    def __init__(self, parent, text, command, width=160, height=44, radius=12,
                 bg=C_ACCENT, fg="#FFFFFF", hover_bg=C_ACCENT_HOVER,
                 disabled_bg=C_BTN_DIS, disabled_fg=C_BTN_DIS_FG,
                 font=None, outer_bg=C_BG):
        super().__init__(parent, width=width, height=height, bg=outer_bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._cmd = command
        self._bg = bg
        self._fg = fg
        self._hover = hover_bg
        self._dbg = disabled_bg
        self._dfg = disabled_fg
        self._enabled = True
        self._pressed = False

        self._shape = self.create_polygon(
            _round_points(1, 1, width - 1, height - 1, radius),
            smooth=True, fill=bg, outline="")
        self._label = self.create_text(
            width // 2, height // 2, text=text, fill=fg,
            font=font or ("Segoe UI", 11, "bold"))

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _on_enter(self, _):
        if self._enabled:
            self.itemconfig(self._shape, fill=self._hover)

    def _on_leave(self, _):
        self._pressed = False
        if self._enabled:
            self.itemconfig(self._shape, fill=self._bg)

    def _on_press(self, _):
        if self._enabled:
            self._pressed = True

    def _on_release(self, _):
        if self._enabled and self._pressed:
            self._pressed = False
            self.itemconfig(self._shape, fill=self._hover)
            if self._cmd:
                self._cmd()

    def set_enabled(self, enabled):
        self._enabled = enabled
        if enabled:
            self.itemconfig(self._shape, fill=self._bg)
            self.itemconfig(self._label, fill=self._fg)
            self.config(cursor="hand2")
        else:
            self._pressed = False
            self.itemconfig(self._shape, fill=self._dbg)
            self.itemconfig(self._label, fill=self._dfg)
            self.config(cursor="")


class Card(tk.Canvas):
    def __init__(self, parent, width, height, radius=16,
                 fill=C_CARD, bg=C_BG, pad=16):
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0)
        self.create_polygon(
            _round_points(0.5, 0.5, width - 0.5, height - 0.5, radius),
            smooth=True, fill=fill, outline="")
        self.body = tk.Frame(self, bg=fill)
        self.create_window(width // 2, height // 2, window=self.body,
                           width=width - pad * 2, anchor="center")


class AutomationApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("幻塔BOSS")
        apply_window_icon(self.root)
        self.root.configure(bg=C_BG)
        self.root.geometry("400x480")
        self.root.resizable(False, False)

        self.F = self._pick_font()
        self._setup_ttk()

        self.stop_event = threading.Event()
        self.worker = None
        self.is_running = False

        self.boss_var = tk.StringVar(value="玛格玛")
        self.source_key_var = tk.StringVar(value="x")
        self.weapon_key_var = tk.StringVar(value="2")
        self.timeout_var = tk.StringVar(value="180")
        self.bubble_key_var = tk.StringVar(value="c")
        self.chest_mode_var = tk.StringVar(value="南音传送")

        chest_path = resource_path("chest.png")
        if not os.path.exists(chest_path):
            chest_path = resource_path("images/chest.png")
        self.chest_module = ChestModule(chest_path)
        self.chest_module.reward_type = "yaxia"

        self._build_ui()

        keyboard.add_hotkey('f10', lambda: self.root.after(0, self.start))
        keyboard.add_hotkey('f12', lambda: self.root.after(0, self.stop))
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    @staticmethod
    def _pick_font():
        try:
            fams = set(tkfont.families())
        except Exception:
            fams = set()
        for name in ("PingFang SC", "Microsoft YaHei UI", "Microsoft YaHei",
                     "Segoe UI", "Helvetica Neue", "Arial"):
            if name in fams:
                return name
        return "TkDefaultFont"

    def _setup_ttk(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("Modern.TCombobox",
                        fieldbackground=C_FILL, background=C_FILL,
                        foreground=C_TEXT, arrowcolor=C_TEXT2,
                        bordercolor=C_FILL, lightcolor=C_FILL,
                        darkcolor=C_FILL, padding=(8, 4),
                        relief="flat", font=(self.F, 10))
        style.map("Modern.TCombobox",
                  fieldbackground=[("readonly", C_FILL), ("disabled", C_FILL)],
                  background=[("active", C_FILL), ("readonly", C_FILL)],
                  bordercolor=[("focus", C_ACCENT), ("!focus", C_FILL)],
                  arrowcolor=[("active", C_TEXT)],
                  foreground=[("readonly", C_TEXT), ("disabled", C_TEXT2)])

        style.configure("Modern.TEntry",
                        fieldbackground=C_FILL, foreground=C_TEXT,
                        bordercolor=C_FILL, lightcolor=C_FILL,
                        darkcolor=C_FILL, padding=(8, 4),
                        relief="flat", insertcolor=C_TEXT, font=(self.F, 10))
        style.map("Modern.TEntry",
                  bordercolor=[("focus", C_ACCENT), ("!focus", C_FILL)],
                  fieldbackground=[("disabled", C_FILL)])

        self.root.option_add('*TCombobox*Listbox.background', '#FFFFFF')
        self.root.option_add('*TCombobox*Listbox.foreground', C_TEXT)
        self.root.option_add('*TCombobox*Listbox.selectBackground', C_ACCENT)
        self.root.option_add('*TCombobox*Listbox.selectForeground', '#FFFFFF')
        self.root.option_add('*TCombobox*Listbox.font', (self.F, 10))
        self.root.option_add('*TCombobox*Listbox.borderWidth', 0)
        self.root.option_add('*TCombobox*Listbox.highlightThickness', 0)

    def _build_ui(self):
        container = tk.Frame(self.root, bg=C_BG)
        container.pack(fill=tk.BOTH, expand=True, padx=24, pady=(20, 20))

        tk.Label(container, text="幻塔BOSS", bg=C_BG, fg=C_TEXT,
                 font=(self.F, 20, "bold"), anchor="w").pack(fill=tk.X)
        tk.Label(container, text="F10 启动   ·   F12 停止", bg=C_BG, fg=C_TEXT2,
                 font=(self.F, 10), anchor="w").pack(fill=tk.X, pady=(2, 0))

        card = Card(container, width=352, height=248, radius=16, bg=C_BG)
        card.pack(pady=(16, 0))
        body = card.body

        def sep():
            tk.Frame(body, bg=C_SEP, height=1).pack(fill=tk.X, pady=7)

        def row_frame():
            f = tk.Frame(body, bg=C_CARD)
            f.pack(fill=tk.X, pady=5)
            return f

        def label(parent, text, secondary=False):
            tk.Label(parent, text=text, bg=C_CARD,
                     fg=C_TEXT2 if secondary else C_TEXT,
                     font=(self.F, 11 if not secondary else 10)).pack(side=tk.LEFT)

        r1 = row_frame()
        label(r1, "Boss")
        self.timeout_entry = ttk.Entry(r1, textvariable=self.timeout_var,
                                       width=6, justify="center",
                                       style="Modern.TEntry")
        self.timeout_entry.pack(side=tk.RIGHT)
        label(r1, "超时(秒)", secondary=True)
        self.boss_combo = ttk.Combobox(r1, textvariable=self.boss_var,
                                       values=["玛格玛", "哈伯拉"],
                                       state="readonly", width=8,
                                       height=2, style="Modern.TCombobox")
        self.boss_combo.pack(side=tk.RIGHT, padx=(0, 18))

        sep()

        for text, var in (("武器", self.weapon_key_var),
                          ("源器1", self.source_key_var),
                          ("源器2", self.bubble_key_var)):
            r = row_frame()
            label(r, text)
            ttk.Entry(r, textvariable=var, width=5, justify="center",
                      style="Modern.TEntry").pack(side=tk.RIGHT)

        sep()

        r5 = row_frame()
        label(r5, "宝箱模式")
        ttk.Combobox(r5, textvariable=self.chest_mode_var,
                     values=["南音传送", "中心锁定"],
                     state="readonly", width=10, height=2,
                     style="Modern.TCombobox").pack(side=tk.RIGHT)

        status_row = tk.Frame(container, bg=C_BG)
        status_row.pack(fill=tk.X, pady=(16, 0))
        self.status_dot = tk.Label(status_row, text="●", bg=C_BG, fg=C_TEXT2,
                                   font=(self.F, 9))
        self.status_dot.pack(side=tk.LEFT)
        self.status = tk.Label(status_row, text="未启动", bg=C_BG, fg=C_TEXT2,
                               font=(self.F, 10))
        self.status.pack(side=tk.LEFT, padx=(6, 0))

        btn_row = tk.Frame(container, bg=C_BG)
        btn_row.pack(pady=(12, 0))

        self.start_btn = RoundedButton(
            btn_row, "启动", self.start, width=164, height=44, radius=12,
            bg=C_ACCENT, fg="#FFFFFF", hover_bg=C_ACCENT_HOVER,
            disabled_bg=C_ACCENT_DIS, disabled_fg="#FFFFFF",
            font=(self.F, 12, "bold"), outer_bg=C_BG)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.stop_btn = RoundedButton(
            btn_row, "停止", self.stop, width=164, height=44, radius=12,
            bg=C_BTN_GRAY, fg=C_TEXT, hover_bg=C_BTN_GRAY_HOVER,
            disabled_bg=C_BTN_DIS, disabled_fg=C_BTN_DIS_FG,
            font=(self.F, 12, "bold"), outer_bg=C_BG)
        self.stop_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.stop_btn.set_enabled(False)

    def _set_status(self, text, color=C_TEXT2):
        self.status.config(text=text, fg=color)
        self.status_dot.config(fg=color)

    def start(self):
        if self.is_running:
            return
        src = self.source_key_var.get().strip().lower()
        wpn = self.weapon_key_var.get().strip().lower()
        bubble = self.bubble_key_var.get().strip().lower()
        boss = self.boss_var.get()

        if not wpn or len(wpn) != 1:
            self._set_status("武器按键必须为单字符", C_DANGER)
            return
        if not src or len(src) != 1:
            self._set_status("源器1按键必须为单字符", C_DANGER)
            return
        if boss != "哈伯拉" and (not bubble or len(bubble) != 1):
            self._set_status("源器2按键必须为单字符", C_DANGER)
            return
        try:
            timeout = int(self.timeout_var.get())
            if timeout <= 0:
                raise ValueError
        except Exception:
            self._set_status("超时必须是正整数", C_DANGER)
            return

        try:
            self.root.update_idletasks()
        except Exception:
            pass
        time.sleep(0.5)

        hwnd = find_qrsl_window()
        if not hwnd:
            print("未找到 QRSL.exe 窗口，请先启动游戏")
            self._set_status("未找到 QRSL.exe 窗口", C_DANGER)
            return
        title = win32gui.GetWindowText(hwnd)
        print(f"已锁定 QRSL.exe 窗口: hwnd={hwnd}, 标题='{title}'")
        if not activate_game_window(hwnd):
            print("警告：窗口激活可能失败，仍尝试继续")
        time.sleep(2)

        self.stop_event.clear()
        self.worker = threading.Thread(
            target=user_task,
            args=(self.stop_event, self.boss_var, self.source_key_var,
                  self.weapon_key_var, self.timeout_var, self.bubble_key_var,
                  self.chest_module, self.chest_mode_var),
            daemon=True
        )
        self.worker.start()
        self.is_running = True
        self._set_status("运行中", C_GREEN)
        self.start_btn.set_enabled(False)
        self.stop_btn.set_enabled(True)

    def stop(self):
        if not self.is_running:
            return
        self.stop_event.set()
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=1)
        self.is_running = False
        self._set_status("已停止", C_TEXT2)
        self.start_btn.set_enabled(True)
        self.stop_btn.set_enabled(False)
        try:
            self.root.deiconify()
        except Exception:
            pass

    def on_close(self):
        if self.is_running:
            self.stop()
        try:
            self.root.deiconify()
        except Exception:
            pass
        keyboard.unhook_all()
        self.root.destroy()
        sys.exit(0)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    try:
        import keyboard, pygetwindow, pyautogui, win32api, win32con, cv2, numpy
        import psutil, win32gui, win32process
    except ImportError:
        print("请安装依赖: pip install keyboard pygetwindow pyautogui pywin32 "
              "opencv-python numpy pillow psutil")
        sys.exit(1)

    if not ensure_admin():
        sys.exit(0)

    lower_own_priority()

    app = AutomationApp()
    app.run()