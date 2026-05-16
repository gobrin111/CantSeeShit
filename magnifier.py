"""
FPS Screen Magnifier — GPU Accelerated
=======================================
Captures the center of your screen with mss, uploads directly to an
OpenGL texture (skipping PIL entirely), and renders a scaled quad with
GPU bilinear filtering.

Requirements:
    pip install mss keyboard pywin32 PyOpenGL

Controls:
    v (default) → Toggle magnifier on/off
    +/-         → Zoom in/out
    [ / ]       → Shrink/grow capture region

Your game MUST be in Borderless Windowed mode.
"""

import sys
import time
import ctypes

import mss
import keyboard

import win32gui
import win32con
import win32api

from OpenGL.GL import *

# Use ctypes for WGL — PyOpenGL's WGL wrappers choke on pywin32 handle types
opengl32 = ctypes.windll.opengl32

from Config import config

# ─── Win32 / WGL constants ──────────────────────────────────────────────────

user32 = ctypes.windll.user32
gdi32  = ctypes.windll.gdi32

# ── Declare proper 64-bit types (ctypes defaults to c_int = 32-bit,
#    which truncates HDC/HGLRC pointers on x64 and silently breaks GL) ───

_vp = ctypes.c_void_p   # shorthand for pointer-sized handles

user32.GetDC.restype         = _vp
user32.GetDC.argtypes        = [_vp]
user32.ReleaseDC.argtypes    = [_vp, _vp]

gdi32.ChoosePixelFormat.restype  = ctypes.c_int
gdi32.ChoosePixelFormat.argtypes = [_vp, ctypes.c_void_p]
gdi32.SetPixelFormat.restype     = ctypes.c_bool
gdi32.SetPixelFormat.argtypes    = [_vp, ctypes.c_int, ctypes.c_void_p]
gdi32.SwapBuffers.restype        = ctypes.c_bool
gdi32.SwapBuffers.argtypes       = [_vp]

opengl32.wglCreateContext.restype  = _vp
opengl32.wglCreateContext.argtypes = [_vp]
opengl32.wglMakeCurrent.restype    = ctypes.c_bool
opengl32.wglMakeCurrent.argtypes   = [_vp, _vp]
opengl32.wglDeleteContext.restype  = ctypes.c_bool
opengl32.wglDeleteContext.argtypes = [_vp]

WDA_EXCLUDEFROMCAPTURE = 0x00000011

PFD_DRAW_TO_WINDOW = 0x00000004
PFD_SUPPORT_OPENGL = 0x00000020
PFD_DOUBLEBUFFER   = 0x00000001
PFD_TYPE_RGBA      = 0

GL_BGRA_EXT = 0x80E1   # BGRA pixel format (matches mss output directly)

CLASS_NAME = "FPSMagOverlayGL"
_registered = False


# ─── Structures ──────────────────────────────────────────────────────────────

class PIXELFORMATDESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ("nSize",           ctypes.c_ushort),
        ("nVersion",        ctypes.c_ushort),
        ("dwFlags",         ctypes.c_ulong),
        ("iPixelType",      ctypes.c_ubyte),
        ("cColorBits",      ctypes.c_ubyte),
        ("cRedBits",        ctypes.c_ubyte),
        ("cRedShift",       ctypes.c_ubyte),
        ("cGreenBits",      ctypes.c_ubyte),
        ("cGreenShift",     ctypes.c_ubyte),
        ("cBlueBits",       ctypes.c_ubyte),
        ("cBlueShift",      ctypes.c_ubyte),
        ("cAlphaBits",      ctypes.c_ubyte),
        ("cAlphaShift",     ctypes.c_ubyte),
        ("cAccumBits",      ctypes.c_ubyte),
        ("cAccumRedBits",   ctypes.c_ubyte),
        ("cAccumGreenBits", ctypes.c_ubyte),
        ("cAccumBlueBits",  ctypes.c_ubyte),
        ("cAccumAlphaBits", ctypes.c_ubyte),
        ("cDepthBits",      ctypes.c_ubyte),
        ("cStencilBits",    ctypes.c_ubyte),
        ("cAuxBuffers",     ctypes.c_ubyte),
        ("iLayerType",      ctypes.c_ubyte),
        ("bReserved",       ctypes.c_ubyte),
        ("dwLayerMask",     ctypes.c_ulong),
        ("dwVisibleMask",   ctypes.c_ulong),
        ("dwDamageMask",    ctypes.c_ulong),
    ]


# ─── WndProc ────────────────────────────────────────────────────────────────

def _wndproc(hwnd, msg, wp, lp):
    if msg == win32con.WM_NCHITTEST:
        return -1                       # HTTRANSPARENT → clicks fall through
    if msg == win32con.WM_DESTROY:
        win32gui.PostQuitMessage(0)
        return 0
    return win32gui.DefWindowProc(hwnd, msg, wp, lp)


# ─── OpenGL Overlay Window ──────────────────────────────────────────────────

class OverlayWindow:
    """Win32 popup + OpenGL context. Always-on-top, click-through, excluded
    from screen capture. Renders a textured quad with GPU filtering."""

    def __init__(self, x, y, w, h):
        global _registered
        hinst = win32api.GetModuleHandle(None)

        if not _registered:
            wc = win32gui.WNDCLASS()
            wc.style = win32con.CS_OWNDC    # dedicated DC for OpenGL
            wc.lpfnWndProc = _wndproc
            wc.hInstance = hinst
            wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
            wc.hbrBackground = win32gui.GetStockObject(win32con.BLACK_BRUSH)
            wc.lpszClassName = CLASS_NAME
            win32gui.RegisterClass(wc)
            _registered = True

        # No WS_EX_LAYERED — incompatible with OpenGL.
        # Click-through via WM_NCHITTEST returning HTTRANSPARENT.
        ex = (0x00000008 |   # WS_EX_TOPMOST
              0x00000080 |   # WS_EX_TOOLWINDOW
              0x08000000)    # WS_EX_NOACTIVATE

        self.hwnd = win32gui.CreateWindowEx(
            ex, CLASS_NAME, "Magnifier", 0x80000000,   # WS_POPUP
            x, y, w, h, 0, 0, hinst, None
        )

        # Exclude from screen capture (so mss never sees us)
        self.exclude_ok = bool(
            user32.SetWindowDisplayAffinity(self.hwnd, WDA_EXCLUDEFROMCAPTURE)
        )
        if not self.exclude_ok:
            print("  ⚠  WDA_EXCLUDEFROMCAPTURE failed (need Win10 2004+)")
            print("     Overlay may capture itself.\n")

        # ── Set up OpenGL context ────────────────────────────────────────
        # Use ctypes GetDC (not win32gui) so handle types stay consistent
        self.hdc = user32.GetDC(self.hwnd)

        pfd = PIXELFORMATDESCRIPTOR()
        pfd.nSize = ctypes.sizeof(PIXELFORMATDESCRIPTOR)
        pfd.nVersion = 1
        pfd.dwFlags = PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER
        pfd.iPixelType = PFD_TYPE_RGBA
        pfd.cColorBits = 32

        fmt = gdi32.ChoosePixelFormat(self.hdc, ctypes.byref(pfd))
        if fmt == 0:
            raise RuntimeError("ChoosePixelFormat failed")
        if not gdi32.SetPixelFormat(self.hdc, fmt, ctypes.byref(pfd)):
            raise RuntimeError("SetPixelFormat failed")

        self.hglrc = opengl32.wglCreateContext(self.hdc)
        if not self.hglrc:
            raise RuntimeError("wglCreateContext failed")
        if not opengl32.wglMakeCurrent(self.hdc, self.hglrc):
            raise RuntimeError("wglMakeCurrent failed")

        # ── OpenGL state ─────────────────────────────────────────────────
        glEnable(GL_TEXTURE_2D)
        glClearColor(0, 0, 0, 1)

        # Create texture
        self.tex = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, self.tex)

        gl_filter = GL_LINEAR if config.GPU_FILTER == "linear" else GL_NEAREST
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, gl_filter)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, gl_filter)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

        self._tex_w = 0
        self._tex_h = 0
        self.w = w
        self.h = h

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
        self.w, self.h = w, h
        win32gui.SetWindowPos(
            self.hwnd, win32con.HWND_TOPMOST, x, y, w, h,
            win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW
        )

    def topmost(self):
        self._topmost()

    # ── rendering ────────────────────────────────────────────────────────

    def render(self, raw_bgra, cap_w, cap_h):
        """Upload raw BGRA pixels from mss and draw a fullscreen textured quad."""
        opengl32.wglMakeCurrent(self.hdc, self.hglrc)
        glViewport(0, 0, self.w, self.h)
        glClear(GL_COLOR_BUFFER_BIT)

        # Upload texture — use TexSubImage when dimensions match (faster)
        glBindTexture(GL_TEXTURE_2D, self.tex)
        if cap_w != self._tex_w or cap_h != self._tex_h:
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, cap_w, cap_h, 0,
                         GL_BGRA_EXT, GL_UNSIGNED_BYTE, raw_bgra)
            self._tex_w = cap_w
            self._tex_h = cap_h
        else:
            glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, cap_w, cap_h,
                            GL_BGRA_EXT, GL_UNSIGNED_BYTE, raw_bgra)

        # Draw textured quad (full viewport)
        glEnable(GL_TEXTURE_2D)
        glColor3f(1, 1, 1)
        glBegin(GL_QUADS)
        glTexCoord2f(0, 1); glVertex2f(-1, -1)
        glTexCoord2f(1, 1); glVertex2f( 1, -1)
        glTexCoord2f(1, 0); glVertex2f( 1,  1)
        glTexCoord2f(0, 0); glVertex2f(-1,  1)
        glEnd()

        # ── Decorations (drawn over the texture) ────────────────────────
        glDisable(GL_TEXTURE_2D)

        # One pixel in normalized device coords
        px = 2.0 / self.w
        py = 2.0 / self.h

        # Border
        if config.BORDER_PX > 0:
            r, g, b = config.BORDER_COLOR
            glColor3f(r, g, b)
            glLineWidth(config.BORDER_PX)
            # Inset by half a pixel so the line sits inside the window
            inset_x = config.BORDER_PX * 0.5 * px
            inset_y = config.BORDER_PX * 0.5 * py
            glBegin(GL_LINE_LOOP)
            glVertex2f(-1 + inset_x, -1 + inset_y)
            glVertex2f( 1 - inset_x, -1 + inset_y)
            glVertex2f( 1 - inset_x,  1 - inset_y)
            glVertex2f(-1 + inset_x,  1 - inset_y)
            glEnd()

        # Crosshair with center gap
        if config.CROSSHAIR:
            r, g, b = config.CROSS_COLOR
            glColor3f(r, g, b)
            glLineWidth(config.CROSS_WIDTH)
            gap_x = config.CROSS_GAP * px
            gap_y = config.CROSS_GAP * py
            len_x = config.CROSS_LEN * px
            len_y = config.CROSS_LEN * py
            glBegin(GL_LINES)
            # Horizontal
            glVertex2f(-len_x, 0); glVertex2f(-gap_x, 0)
            glVertex2f( gap_x, 0); glVertex2f( len_x, 0)
            # Vertical
            glVertex2f(0, -len_y); glVertex2f(0, -gap_y)
            glVertex2f(0,  gap_y); glVertex2f(0,  len_y)
            glEnd()

        gdi32.SwapBuffers(self.hdc)

    def destroy(self):
        opengl32.wglMakeCurrent(self.hdc, None)
        opengl32.wglDeleteContext(self.hglrc)
        user32.ReleaseDC(self.hwnd, self.hdc)
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

        self.zoomHook = None

        self.sct = mss.mss()
        m = self.sct.monitors[1]
        self.sw, self.sh = m["width"], m["height"]
        self.cx, self.cy = self.sw // 2, self.sh // 2

    @property
    def size(self):
        return int(self.radius * 2 * self.zoom)

    # ── capture (returns raw BGRA bytes, no PIL) ─────────────────────────

    def grab(self):
        r = self.radius
        region = {
            "left": max(0, self.cx - r), "top": max(0, self.cy - r),
            "width": r * 2, "height": r * 2,
        }
        return self.sct.grab(region)

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
        self.zoomHook = keyboard.on_press_key(
            config.TOGGLE_KEY, lambda _: self.toggle(), suppress=False
        )
        keyboard.on_press_key("=", lambda _: self.adj_zoom(config.ZOOM_STEP))
        keyboard.on_press_key("-", lambda _: self.adj_zoom(-config.ZOOM_STEP))
        keyboard.on_press_key("]", lambda _: self.adj_radius(config.CAPTURE_STEP))
        keyboard.on_press_key("[", lambda _: self.adj_radius(-config.CAPTURE_STEP))

        print("╔═══════════════════════════════════════════╗")
        print("║     FPS Screen Magnifier  (GPU)           ║")
        print("╠═══════════════════════════════════════════╣")
        print(f"║  Toggle:   {config.TOGGLE_KEY:<30s} ║")
        print(f"║  Zoom:     +/-  ({self.zoom:.1f}x)                    ║")
        print(f"║  Region:   [/]  ({self.radius*2}px)                  ║")
        print(f"║  Filter:   {config.GPU_FILTER:<30s} ║")
        print("╠═══════════════════════════════════════════╣")
        print("║  ⚠  Game must be Borderless Windowed      ║")
        print("╚═══════════════════════════════════════════╝")
        print()

        dt = 1.0 / config.FPS

        while self.alive:
            t0 = time.perf_counter()

            if self.on:
                self.ensure_window()

                now = time.perf_counter() * 1000
                if now - self._last_topmost > config.TOPMOST_MS:
                    self.win.topmost()
                    self._last_topmost = now

                # Fallback hide/show if WDA_EXCLUDEFROMCAPTURE isn't available
                fallback = self.win and not self.win.exclude_ok
                if fallback:
                    self.win.hide()
                    time.sleep(0.002)

                shot = self.grab()

                # Ensure visible BEFORE render — SwapBuffers on a hidden
                # window discards the frame (black screen on re-toggle).
                # ShowWindow is a no-op when already visible, so this is cheap.
                win32gui.ShowWindow(self.win.hwnd, win32con.SW_SHOWNOACTIVATE)

                self.win.render(bytes(shot.raw), shot.width, shot.height)
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

    # ── hotkey rebind ────────────────────────────────────────────────────

    def bind_hotkey(self):
        if self.zoomHook is not None:
            keyboard.unhook(self.zoomHook)
        self.zoomHook = keyboard.on_press_key(
            config.TOGGLE_KEY, lambda _: self.toggle(), suppress=False
        )

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