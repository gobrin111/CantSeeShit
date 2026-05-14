"""
FPS Screen Magnifier
====================
Magnifies the center of your screen with a hotkey toggle.
Uses WDA_EXCLUDEFROMCAPTURE so the overlay never captures itself.

Requirements:
    pip install mss Pillow keyboard pywin32

Controls:
    v  → Toggle magnifier on/off
    +/-       → Zoom in/out
    [ / ]     → Shrink/grow capture region
    Esc       → Quit

Your game MUST be in Borderless Windowed mode — Fullscreen Exclusive
bypasses the Windows compositor and no overlay can draw over it.
"""

import sys
import time
import ctypes

import mss
import keyboard
from PIL import Image, ImageDraw

import win32gui
import win32con
import win32api

from Config import config



# ─── WIN32 HELPERS ───────────────────────────────────────────────────────────

user32 = ctypes.windll.user32
gdi32  = ctypes.windll.gdi32

WDA_EXCLUDEFROMCAPTURE = 0x00000011   # Win10 2004+

CLASS_NAME = "FPSMagOverlay"
_registered = False


def _wndproc(hwnd, msg, wp, lp):
    if msg == win32con.WM_NCHITTEST:
        return -1                       # HTTRANSPARENT → clicks pass through
    if msg == win32con.WM_DESTROY:
        win32gui.PostQuitMessage(0)
        return 0
    return win32gui.DefWindowProc(hwnd, msg, wp, lp)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize",          ctypes.c_uint32),
        ("biWidth",         ctypes.c_int32),
        ("biHeight",        ctypes.c_int32),
        ("biPlanes",        ctypes.c_uint16),
        ("biBitCount",      ctypes.c_uint16),
        ("biCompression",   ctypes.c_uint32),
        ("biSizeImage",     ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed",       ctypes.c_uint32),
        ("biClrImportant",  ctypes.c_uint32),
    ]


class OverlayWindow:
    def __init__(self, x, y, w, h):
        global _registered
        hinst = win32api.GetModuleHandle(None)

        if not _registered:
            wc = win32gui.WNDCLASS()
            wc.style = win32con.CS_HREDRAW | win32con.CS_VREDRAW
            wc.lpfnWndProc = _wndproc
            wc.hInstance = hinst
            wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
            wc.hbrBackground = win32gui.GetStockObject(win32con.BLACK_BRUSH)
            wc.lpszClassName = CLASS_NAME
            win32gui.RegisterClass(wc)
            _registered = True

        ex = (0x00080000 |   # WS_EX_LAYERED
              0x00000020 |   # WS_EX_TRANSPARENT
              0x00000008 |   # WS_EX_TOPMOST
              0x00000080 |   # WS_EX_TOOLWINDOW
              0x08000000)    # WS_EX_NOACTIVATE

        self.hwnd = win32gui.CreateWindowEx(
            ex, CLASS_NAME, "Magnifier", 0x80000000,   # WS_POPUP
            x, y, w, h, 0, 0, hinst, None
        )

        # Opaque layered window (needed for WS_EX_TRANSPARENT click-through)
        user32.SetLayeredWindowAttributes(self.hwnd, 0, 255, 0x02)

        # Tell Windows to exclude this window from all screen captures.
        # mss / BitBlt / PrintWindow will never see it → no recursive capture.
        self.exclude_ok = bool(
            user32.SetWindowDisplayAffinity(self.hwnd, WDA_EXCLUDEFROMCAPTURE)
        )
        if not self.exclude_ok:
            print("  ⚠  WDA_EXCLUDEFROMCAPTURE not supported (need Win10 2004+)")
            print("     Using hide/show fallback — may flicker slightly.\n")

        self.hdc = win32gui.GetDC(self.hwnd)
        self._topmost()

    # ── window management ────────────────────────────────────────────────

    def _topmost(self):
        win32gui.SetWindowPos(
            self.hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE |
            win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW
        )

    def show(self):
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNOACTIVATE)
        self._topmost()

    def hide(self):
        win32gui.ShowWindow(self.hwnd, win32con.SW_HIDE)

    def move(self, x, y, w, h):
        win32gui.SetWindowPos(
            self.hwnd, win32con.HWND_TOPMOST, x, y, w, h,
            win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW
        )

    def topmost(self):
        self._topmost()

    # ── drawing ──────────────────────────────────────────────────────────

    def blit(self, pil_img):
        w, h = pil_img.size
        raw = pil_img.tobytes("raw", "BGRX")
        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h          # negative = top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        gdi32.SetDIBitsToDevice(
            self.hdc, 0, 0, w, h, 0, 0, 0, h,
            raw, ctypes.byref(bmi), 0
        )

    def destroy(self):
        win32gui.ReleaseDC(self.hwnd, self.hdc)
        win32gui.DestroyWindow(self.hwnd)


# ─── MAGNIFIER ───────────────────────────────────────────────────────────────

class Magnifier:
    def __init__(self):
        self.on = False
        self.alive = True
        self.zoom = config.ZOOM
        self.radius = config.CAPTURE_RADIUS
        self.win = None
        self._last_topmost = 0
        self._last_size = 0

        # self.hotkey_hook = None
        self.zoomHook = None
        # self.bind_hotkey()

        self.sct = mss.mss()
        m = self.sct.monitors[1]
        self.sw, self.sh = m["width"], m["height"]
        self.cx, self.cy = self.sw // 2, self.sh // 2

    @property
    def size(self):
        return int(self.radius * 2 * self.zoom)

    # ── capture ──────────────────────────────────────────────────────────

    def grab(self):
        r = self.radius
        region = {
            "left": max(0, self.cx - r), "top": max(0, self.cy - r),
            "width": r * 2, "height": r * 2,
        }
        s = self.sct.grab(region)
        return Image.frombytes("RGB", s.size, s.bgra, "raw", "BGRX")

    def decorate(self, img):
        draw = ImageDraw.Draw(img)
        w, h = img.size

        if config.BORDER_PX:
            for i in range(config.BORDER_PX):
                draw.rectangle([i, i, w-1-i, h-1-i], outline=config.BORDER_COLOR)

        if config.CROSSHAIR:
            cx, cy = w // 2, h // 2
            g, s, lw, c = config.CROSS_GAP, config.CROSS_LEN, config.CROSS_WIDTH, config.CROSS_COLOR
            draw.line([(cx-s, cy), (cx-g, cy)], fill=c, width=lw)
            draw.line([(cx+g, cy), (cx+s, cy)], fill=c, width=lw)
            draw.line([(cx, cy-s), (cx, cy-g)], fill=c, width=lw)
            draw.line([(cx, cy+g), (cx, cy+s)], fill=c, width=lw)

    # ── window management ────────────────────────────────────────────────

    def ensure_window(self):
        sz = self.size
        ox, oy = self.cx - sz // 2, self.cy - sz // 2
        if self.win is None:
            self.win = OverlayWindow(ox, oy, sz, sz)
            self._last_size = sz
        elif sz != self._last_size:
            self.win.move(ox, oy, sz, sz)
            self._last_size = sz

    # ── main loop ────────────────────────────────────────────────────────

    def run(self):
        self.zoomHook = keyboard.on_press_key(config.TOGGLE_KEY, lambda _: self.toggle(), suppress=False)
        #keyboard.on_press_key("+", lambda _: self.adj_zoom(ZOOM_STEP))v
        keyboard.on_press_key("=", lambda _: self.adj_zoom(config.ZOOM_STEP))
        keyboard.on_press_key("-", lambda _: self.adj_zoom(-config.ZOOM_STEP))
        keyboard.on_press_key("]", lambda _: self.adj_radius(config.CAPTURE_STEP))
        keyboard.on_press_key("[", lambda _: self.adj_radius(-config.CAPTURE_STEP))
        #keyboard.on_press_key("esc", lambda _: self.quit())

        print("╔═══════════════════════════════════════════╗")
        print("║        FPS Screen Magnifier               ║")
        print("╠═══════════════════════════════════════════╣")
        print(f"║  Toggle:   {config.TOGGLE_KEY:<30s} ║")
        print(f"║  Zoom:     +/-  ({self.zoom:.1f}x)                    ║")
        print(f"║  Region:   [/]  ({self.radius*2}px)                  ║")
        print(f"║  Quit:     Esc                            ║")
        print("╠═══════════════════════════════════════════╣")
        print("║  ⚠  Game must be Borderless Windowed      ║")
        print("╚═══════════════════════════════════════════╝")
        print()

        dt = 1.0 / config.FPS

        while self.alive:
            t0 = time.perf_counter()

            if self.on:
                self.ensure_window()

                # Re-assert topmost so the game can't bury us
                now = time.perf_counter() * 1000
                if now - self._last_topmost > config.TOPMOST_MS:
                    self.win.topmost()
                    self._last_topmost = now

                # Fallback: hide overlay during capture if OS can't exclude it
                fallback = not self.win.exclude_ok
                if fallback:
                    self.win.hide()
                    time.sleep(0.002)

                img = self.grab()
                sz = self.size
                img = img.resize((sz, sz), config.SCALING)
                self.decorate(img)
                self.win.blit(img.convert("RGBX"))
                self.win.show()
            else:
                if self.win:
                    self.win.hide()

            win32gui.PumpWaitingMessages()

            elapsed = time.perf_counter() - t0
            if elapsed < dt:
                time.sleep(dt - elapsed)

        if self.win:
            self.win.destroy()
        keyboard.unhook_all()
        sys.exit(0)

    # ── hotkey callbacks ─────────────────────────────────────────────────

    def toggle(self):
        self.on = not self.on
        s = "ON " if self.on else "OFF"
        print(f"  ► Magnifier {s}  │  {self.zoom:.1f}x  │  {self.radius*2}px")

    def adj_zoom(self, d):
        if not self.on: return
        self.zoom = round(max(config.ZOOM_MIN, min(config.ZOOM_MAX, self.zoom + d)), 2)
        print(f"    zoom → {self.zoom:.1f}x")

    def adj_radius(self, d):
        if not self.on: return
        self.radius = max(config.CAPTURE_MIN, min(config.CAPTURE_MAX, self.radius + d))
        print(f"    region → {self.radius*2}px")

    def quit(self):
        print("  Shutting down...")
        self.alive = False

    # ── hotkey rebind ─────────────────────────────────────────────────────

    def bind_hotkey(self):
        # remove old binding
        if self.zoomHook is not None:
            keyboard.unhook(self.zoomHook)
        # register new binding
        self.zoomHook = keyboard.on_press_key(config.TOGGLE_KEY, lambda _: self.toggle(), suppress=False)
        print(config.TOGGLE_KEY)
        print("this was the new key change")

    def update_hotkey(self):
        self.bind_hotkey()

# ─── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    Magnifier().run()