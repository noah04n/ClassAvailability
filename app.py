"""ClassAvailability — McGill VSB Course Availability Tracker.

Main application entry point. Wires together config, GUI, polling tracker, and the
system tray icon. Run via `pythonw app.py` (no console) or `python app.py`
(with console for debugging).
"""

from __future__ import annotations

import sys
import threading
import tkinter as tk
from tkinter import messagebox
from pathlib import Path

import config
from gui import App

# pystray + PIL are optional — the app works without a tray icon, it just
# doesn't survive a close-to-background. If they're missing we fall back to a
# plain "minimize" on close.
try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False


def _make_tray_icon_image() -> "Image.Image":
    """Generate a small icon at runtime so we don't ship a .ico file."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Background rounded square
    d.rounded_rectangle((4, 4, size - 4, size - 4), radius=12, fill=(13, 71, 161, 255))
    # Bold "C" mark
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("arialbd.ttf", 40)
    except (OSError, ImportError):
        font = None
    text = "C"
    try:
        bbox = d.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1] - 2),
               text, font=font, fill=(255, 255, 255, 255))
    except Exception:
        d.text((20, 12), "C", fill=(255, 255, 255, 255), font=font)
    return img


class TrayController:
    """Owns the pystray.Icon and bridges menu actions back to the tk thread."""

    def __init__(self, app: App):
        self.app = app
        self.icon: "pystray.Icon | None" = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not TRAY_AVAILABLE:
            return
        image = _make_tray_icon_image()
        menu = pystray.Menu(
            pystray.MenuItem("Show ClassAvailability", self._show, default=True),
            pystray.MenuItem("Start polling", self._start_polling,
                             checked=lambda _i: self.app.tracker.is_running,
                             enabled=lambda _i: not self.app.tracker.is_running),
            pystray.MenuItem("Stop polling", self._stop_polling,
                             enabled=lambda _i: self.app.tracker.is_running),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        )
        self.icon = pystray.Icon(
            "ClassAvailability",
            image,
            "ClassAvailability — VSB Tracker",
            menu,
        )
        # pystray.Icon.run() blocks, so run it on a daemon thread.
        self._thread = threading.Thread(target=self.icon.run, name="pystray-tray", daemon=True)
        self._thread.start()

    def hide_window_to_tray(self) -> None:
        if not TRAY_AVAILABLE or self.icon is None:
            # Without tray support, fall back to iconifying.
            self.app.root.iconify()
            return
        self.app.root.withdraw()

    # --- menu actions (called from pystray thread) ---

    def _show(self, _icon=None, _item=None) -> None:
        self.app.root.after(0, self._show_main_thread)

    def _show_main_thread(self) -> None:
        self.app.root.deiconify()
        self.app.root.state("normal")
        self.app.root.lift()
        self.app.root.focus_force()

    def _start_polling(self, _icon=None, _item=None) -> None:
        self.app.root.after(0, self.app.tracker.start)

    def _stop_polling(self, _icon=None, _item=None) -> None:
        self.app.root.after(0, self.app.tracker.stop)

    def _quit(self, _icon=None, _item=None) -> None:
        # Stop the tracker, save config, then schedule tk shutdown on its
        # own thread so pystray.stop() doesn't deadlock.
        try:
            if self.icon is not None:
                self.icon.stop()
        except Exception:
            pass
        try:
            self.app.root.after(0, self.app.quit_app)
        except RuntimeError:
            # tk already gone — just exit.
            sys.exit(0)


def _acquire_single_instance_lock() -> "object | None":
    """Claim a process-wide lock so only one ClassAvailability runs at a time.

    Returns a handle to keep alive for the process lifetime, or None if another
    instance already holds the lock. On non-Windows (no kernel32) we don't block
    startup and just return a placeholder handle."""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # Named mutex is shared across processes for this user session.
        handle = kernel32.CreateMutexW(None, False, "ClassAvailability.VSB.Tracker.SingleInstance")
        ERROR_ALREADY_EXISTS = 183
        if not handle or kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            return None
        return handle
    except Exception:
        # ctypes/kernel32 unavailable (e.g. non-Windows) — don't block launch.
        return object()


def _enable_dpi_awareness() -> None:
    """Tell Windows we'll handle DPI ourselves so text isn't bitmap-scaled
    (i.e. blurry) on high-DPI displays. Must run before tk.Tk() is created."""
    try:
        import ctypes
        # Try per-monitor v2 first (Win10 1703+), fall back to system-DPI aware.
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _apply_tk_scaling(root: tk.Tk) -> None:
    """Match Tk's internal point→pixel scaling to the actual monitor DPI so
    fonts and ttk widgets render at the right size after DPI awareness is on."""
    try:
        scaling = root.winfo_fpixels("1i") / 72.0
        root.tk.call("tk", "scaling", scaling)
    except tk.TclError:
        pass


def main() -> int:
    # Refuse to start a second copy — a second poller would double up emails.
    instance_lock = _acquire_single_instance_lock()
    if instance_lock is None:
        try:
            warn = tk.Tk()
            warn.withdraw()
            messagebox.showinfo(
                "ClassAvailability",
                "ClassAvailability is already running.\n\n"
                "Look for its icon in the system tray (near the clock).",
            )
            warn.destroy()
        except Exception:
            pass
        return 0

    cfg = config.load()

    _enable_dpi_awareness()
    root = tk.Tk()
    _apply_tk_scaling(root)
    # On Windows, give the taskbar entry a distinct AppUserModelID so it doesn't
    # group under generic "Python".
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ClassAvailability.VSB.Tracker")
    except Exception:
        pass

    tray_holder: dict[str, TrayController] = {}

    def close_to_tray() -> None:
        controller = tray_holder.get("controller")
        if controller is None:
            # No tray available; just minimize.
            root.iconify()
            return
        controller.hide_window_to_tray()

    app = App(root, cfg, on_close_to_tray=close_to_tray)

    tray_controller = TrayController(app)
    tray_holder["controller"] = tray_controller
    tray_controller.start()

    if cfg.settings.start_polling_on_launch:
        app.tracker.start()

    # If pystray isn't installed and minimize_to_tray was on, warn once.
    if not TRAY_AVAILABLE and cfg.settings.minimize_to_tray_on_close:
        try:
            print(
                "[ClassAvailability] pystray not installed; closing the window will "
                "minimize instead of going to the system tray.\n"
                "Run install.bat (or `pip install -r requirements.txt`) for tray support.",
                file=sys.stderr,
            )
        except Exception:
            pass

    root.mainloop()
    # Mainloop returned — make sure tracker/tray are stopped.
    app.tracker.stop()
    if tray_controller.icon is not None:
        try:
            tray_controller.icon.stop()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
