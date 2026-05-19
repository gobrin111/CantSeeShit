# ─── CONFIG ──────────────────────────────────────────────────────────────────
class Config:
    def __init__(self):
        # ── Keybinds ─────────────────────────────────────────────────────
        self.TOGGLE_KEY      = "v"
        self.ZOOM_IN_KEY     = "="
        self.ZOOM_OUT_KEY    = "-"
        self.REGION_UP_KEY   = "]"
        self.REGION_DOWN_KEY = "["

        # ── Zoom ─────────────────────────────────────────────────────────
        self.ZOOM            = 2.0
        self.ZOOM_MIN        = 1.5
        self.ZOOM_MAX        = 6.0
        self.ZOOM_STEP       = 0.25

        # ── Capture ──────────────────────────────────────────────────────
        self.CAPTURE_RADIUS  = 200
        self.CAPTURE_MIN     = 80
        self.CAPTURE_MAX     = 400
        self.CAPTURE_STEP    = 20

        # ── Performance ──────────────────────────────────────────────────
        self.FPS             = 60
        self.TOPMOST_MS      = 500

        # ── Appearance ───────────────────────────────────────────────────
        self.BORDER_COLOR    = (0.0, 0.78, 0.0)
        self.BORDER_PX       = 2

        self.CROSSHAIR       = True
        self.CROSS_COLOR     = (1.0, 0.2, 0.2)
        self.CROSS_LEN       = 12
        self.CROSS_GAP       = 4
        self.CROSS_WIDTH     = 1

        # "bicubic" = Catmull-Rom shader, "linear" = GPU bilinear, "nearest" = pixel-perfect
        self.GPU_FILTER      = "bicubic"

config = Config()