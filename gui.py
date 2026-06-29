"""Tkinter GUI: three tabs (Tracked / Add Course / Settings) + status bar.

The tracker thread is forbidden from touching tk directly. Whenever the tracker
fires `on_update`, we marshal back to the tk main loop via `root.after(0, ...)`.
"""

from __future__ import annotations

import threading
import tkinter as tk
from datetime import date
from tkinter import messagebox, ttk
from typing import Callable

import config
import notifier
import vsb_client
from tracker import Tracker


TERM_NAMES = {"01": "Winter", "05": "Summer", "09": "Fall"}


# Color palettes for the two themes. ttk's "vista" theme is heavily
# native-painted and ignores most Style configures, so dark mode switches to
# "clam" which respects everything we throw at it.
THEMES = {
    "light": {
        "ttk_theme": "vista",
        "bg": "#f0f0f0",
        "fg": "#000000",
        "panel_bg": "#ffffff",
        "entry_bg": "#ffffff",
        "entry_fg": "#000000",
        "muted_fg": "#555555",
        "subtle_fg": "#666666",
        "success_fg": "#2a6e2a",
        "danger_fg": "#c33333",
        "warning_fg": "#a55555",
        "tree_bg": "#ffffff",
        "tree_fg": "#000000",
        "tree_heading_bg": "#f0f0f0",
        "tree_sel_bg": "#0a64a4",
        "tree_sel_fg": "#ffffff",
        "tag_open_bg": "#dff4dd",
        "tag_full_bg": "#fff7d6",
        "tag_error_bg": "#fde2e2",
        "tag_never_bg": "#eef0f3",
        "tag_fg": "#000000",
        "indicator_outline": "#333333",
    },
    "dark": {
        "ttk_theme": "clam",
        "bg": "#1e1e1e",
        "fg": "#e6e6e6",
        "panel_bg": "#252526",
        "entry_bg": "#2d2d30",
        "entry_fg": "#e6e6e6",
        "muted_fg": "#b0b0b0",
        "subtle_fg": "#9a9a9a",
        "success_fg": "#5cb85c",
        "danger_fg": "#e57373",
        "warning_fg": "#e0a050",
        "tree_bg": "#252526",
        "tree_fg": "#e6e6e6",
        "tree_heading_bg": "#2d2d30",
        "tree_sel_bg": "#094771",
        "tree_sel_fg": "#ffffff",
        "tag_open_bg": "#1f3a1f",
        "tag_full_bg": "#3d3416",
        "tag_error_bg": "#4a1f1f",
        "tag_never_bg": "#33333a",
        "tag_fg": "#e6e6e6",
        "indicator_outline": "#888888",
    },
}


def _generate_term_options(today: date | None = None) -> list[tuple[str, str]]:
    """Return [(label, code), ...] for the previous, current, and next ~6 terms.
    Term code is YYYYMM where MM ∈ {01,05,09}."""
    today = today or date.today()
    months = ["01", "05", "09"]
    # Build a sequence centered on today: a few past terms + several upcoming.
    out: list[tuple[str, str]] = []
    for year_offset in range(-1, 3):
        y = today.year + year_offset
        for mm in months:
            code = f"{y}{mm}"
            label = f"{TERM_NAMES[mm]} {y} ({code})"
            out.append((label, code))
    return out


def _default_term_code(today: date | None = None) -> str:
    today = today or date.today()
    # Pick the term whose registration window is most likely active.
    # Heuristic: from Mar–Aug → upcoming Fall, Sep–Nov → upcoming Winter,
    # Dec–Feb → upcoming Summer. Good enough as a default; user can change it.
    m = today.month
    if 3 <= m <= 8:
        return f"{today.year}09"
    if 9 <= m <= 11:
        return f"{today.year + 1}01"
    # Dec-Feb
    y = today.year if m <= 2 else today.year + 1
    return f"{y}05"


class App:
    def __init__(self, root: tk.Tk, cfg: config.AppConfig,
                 on_close_to_tray: Callable[[], None]) -> None:
        self.root = root
        self.cfg = cfg
        self.on_close_to_tray = on_close_to_tray
        self.log_lines: list[str] = []

        self.tracker = Tracker(
            cfg=self.cfg,
            on_update=self._tracker_update_callback,
            log=self._tracker_log_callback,
        )

        self._build_ui()
        self.refresh_tracked_view()
        self.refresh_status_bar()

        # Wire window-close → tray-or-quit
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- UI construction ----

    def _build_ui(self) -> None:
        self.root.title("ClassAvailability — McGill VSB Tracker")
        self.root.geometry("960x560")
        self.root.minsize(820, 480)

        self._style = ttk.Style(self.root)
        self.theme = THEMES.get(self.cfg.settings.theme, THEMES["dark"])
        self._apply_theme(self.cfg.settings.theme)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 0))

        self._build_tracked_tab()
        self._build_add_tab()
        self._build_profiles_tab()
        self._build_settings_tab()

        self._build_status_bar()

        # Size the window to actually fit its content so nothing (e.g. the
        # Settings tab's Save/Test buttons) spills below the bottom edge on
        # launch, then center it on screen.
        self._fit_window_to_content()

    def _fit_window_to_content(self) -> None:
        """Grow the window to the size its widgets request (the Settings tab is
        the tallest), clamped to the screen and centered."""
        self.root.update_idletasks()
        w = max(self.root.winfo_reqwidth(), 960)
        h = max(self.root.winfo_reqheight(), 560)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w = min(w, int(sw * 0.95))
        h = min(h, int(sh * 0.90))
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 3)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _apply_theme(self, name: str) -> None:
        """(Re)style every widget for the chosen theme. Safe to call repeatedly
        — first call happens during _build_ui before any widgets exist, later
        calls reach into widgets that have since been created."""
        if name not in THEMES:
            name = "dark"
        t = THEMES[name]
        self.theme = t

        style = self._style
        try:
            style.theme_use(t["ttk_theme"])
        except tk.TclError:
            pass

        self.root.configure(background=t["bg"])

        # Base + per-widget ttk styles. "clam" honors all of these; "vista"
        # ignores most but the light-theme defaults are already close to these
        # values, so it doesn't matter.
        style.configure(".", background=t["bg"], foreground=t["fg"],
                        fieldbackground=t["entry_bg"])
        style.configure("TFrame", background=t["bg"])
        style.configure("TLabel", background=t["bg"], foreground=t["fg"])
        style.configure("TLabelframe", background=t["bg"], foreground=t["fg"])
        style.configure("TLabelframe.Label", background=t["bg"], foreground=t["fg"])
        style.configure("TButton", background=t["panel_bg"], foreground=t["fg"])
        style.map("TButton", background=[("active", t["entry_bg"])])
        style.configure("TCheckbutton", background=t["bg"], foreground=t["fg"])
        style.map("TCheckbutton",
                  background=[("active", t["bg"])],
                  foreground=[("disabled", t["subtle_fg"])])
        style.configure("TNotebook", background=t["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=t["panel_bg"],
                        foreground=t["fg"], padding=(10, 4))
        style.map("TNotebook.Tab",
                  background=[("selected", t["bg"])],
                  foreground=[("selected", t["fg"])])
        style.configure("TEntry", fieldbackground=t["entry_bg"],
                        foreground=t["entry_fg"], insertcolor=t["entry_fg"])
        style.configure("TCombobox", fieldbackground=t["entry_bg"],
                        foreground=t["entry_fg"], background=t["entry_bg"],
                        arrowcolor=t["fg"])
        style.map("TCombobox", fieldbackground=[("readonly", t["entry_bg"])],
                  foreground=[("readonly", t["entry_fg"])])
        style.configure("TSpinbox", fieldbackground=t["entry_bg"],
                        foreground=t["entry_fg"], background=t["entry_bg"],
                        arrowcolor=t["fg"])
        style.configure("TScrollbar", background=t["panel_bg"],
                        troughcolor=t["bg"], arrowcolor=t["fg"])
        style.configure("TSeparator", background=t["panel_bg"])
        style.configure("Treeview",
                        background=t["tree_bg"], fieldbackground=t["tree_bg"],
                        foreground=t["tree_fg"], rowheight=22, borderwidth=0)
        style.configure("Treeview.Heading",
                        background=t["tree_heading_bg"], foreground=t["fg"])
        style.map("Treeview",
                  background=[("selected", t["tree_sel_bg"])],
                  foreground=[("selected", t["tree_sel_fg"])])

        # Custom-named label styles used for muted/coloured copy in the form.
        style.configure("Muted.TLabel", background=t["bg"], foreground=t["muted_fg"])
        style.configure("Subtle.TLabel", background=t["bg"], foreground=t["subtle_fg"])
        style.configure("Success.TLabel", background=t["bg"], foreground=t["success_fg"])
        style.configure("Danger.TLabel", background=t["bg"], foreground=t["danger_fg"])
        style.configure("Warning.TLabel", background=t["bg"], foreground=t["warning_fg"])
        # Panel.* lives on the white/dark card inside the Add Course results pane.
        style.configure("Panel.TFrame", background=t["panel_bg"])
        style.configure("Panel.TLabel", background=t["panel_bg"], foreground=t["fg"])
        style.configure("PanelMuted.TLabel", background=t["panel_bg"], foreground=t["subtle_fg"])
        style.configure("PanelSuccess.TLabel", background=t["panel_bg"], foreground=t["success_fg"])
        style.configure("PanelDanger.TLabel", background=t["panel_bg"], foreground=t["danger_fg"])
        style.configure("PanelWarning.TLabel", background=t["panel_bg"], foreground=t["warning_fg"])
        style.configure("Panel.TCheckbutton", background=t["panel_bg"], foreground=t["fg"])
        style.map("Panel.TCheckbutton", background=[("active", t["panel_bg"])])

        # Combobox dropdown list isn't a ttk widget — themed via tk option DB.
        self.root.option_add("*TCombobox*Listbox.background", t["entry_bg"])
        self.root.option_add("*TCombobox*Listbox.foreground", t["entry_fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", t["tree_sel_bg"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", t["tree_sel_fg"])

        # Re-tag Treeview rows + recolor tk-native widgets that we kept refs to.
        if hasattr(self, "tree"):
            self.tree.tag_configure("open_row", background=t["tag_open_bg"], foreground=t["tag_fg"])
            self.tree.tag_configure("full_row", background=t["tag_full_bg"], foreground=t["tag_fg"])
            self.tree.tag_configure("error_row", background=t["tag_error_bg"], foreground=t["tag_fg"])
            self.tree.tag_configure("never_row", background=t["tag_never_bg"], foreground=t["tag_fg"])
        if hasattr(self, "results_canvas"):
            self.results_canvas.configure(background=t["panel_bg"])
        if hasattr(self, "indicator"):
            self._indicator_color = ""  # force redraw with new outline
            self.refresh_status_bar()
        # Re-render the Add Course results pane so any rows pick up the new colors.
        if hasattr(self, "results_frame") and hasattr(self, "section_vars"):
            self._rerender_results_for_theme()

    # -- Tab 1: Tracked --

    def _build_tracked_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(frame, text="Tracked Courses")

        cols = ("course", "title", "section", "status", "open", "profile", "last_checked", "last_notified")
        headers = {
            "course": "Course",
            "title": "Title",
            "section": "Section",
            "status": "Status",
            "open": "Open",
            "profile": "Profile",
            "last_checked": "Last checked",
            "last_notified": "Last notified",
        }
        widths = {
            "course": 80, "title": 200, "section": 70, "status": 200,
            "open": 50, "profile": 100, "last_checked": 130, "last_notified": 130,
        }
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended")
        for c in cols:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, width=widths[c], anchor=tk.W if c in ("title", "status") else tk.CENTER)
        # Tag colors are theme-driven and set by _apply_theme().
        t = self.theme
        self.tree.tag_configure("open_row", background=t["tag_open_bg"], foreground=t["tag_fg"])
        self.tree.tag_configure("full_row", background=t["tag_full_bg"], foreground=t["tag_fg"])
        self.tree.tag_configure("error_row", background=t["tag_error_bg"], foreground=t["tag_fg"])
        self.tree.tag_configure("never_row", background=t["tag_never_bg"], foreground=t["tag_fg"])

        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        btns = ttk.Frame(frame)
        btns.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(btns, text="Refresh now", command=self._on_refresh_now).pack(side=tk.LEFT)
        ttk.Button(btns, text="Change profile…", command=self._on_change_profile).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Remove selected", command=self._on_remove_selected).pack(side=tk.LEFT, padx=6)
        self.start_stop_btn = ttk.Button(btns, text="Start polling", command=self._on_toggle_polling)
        self.start_stop_btn.pack(side=tk.RIGHT)

        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

    # -- Tab 2: Add Course --

    def _build_add_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(frame, text="Add Course")

        top = ttk.Frame(frame)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Term:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.term_options = _generate_term_options()
        self.term_var = tk.StringVar()
        default_code = _default_term_code()
        default_label = next((lbl for lbl, c in self.term_options if c == default_code),
                             self.term_options[0][0])
        self.term_combo = ttk.Combobox(
            top, textvariable=self.term_var, width=24, state="readonly",
            values=[lbl for lbl, _ in self.term_options],
        )
        self.term_var.set(default_label)
        self.term_combo.grid(row=0, column=1, sticky="w")

        ttk.Label(top, text="Course code:").grid(row=0, column=2, sticky="w", padx=(16, 6))
        self.course_var = tk.StringVar()
        self.course_entry = ttk.Entry(top, textvariable=self.course_var, width=18)
        self.course_entry.grid(row=0, column=3, sticky="w")
        self.course_entry.bind("<Return>", lambda _e: self._on_lookup())

        ttk.Button(top, text="Look up sections", command=self._on_lookup).grid(
            row=0, column=4, sticky="w", padx=(10, 0),
        )

        # Profile multi-picker: a section can fan its notifications out to
        # multiple profiles (one email per recipient) so e.g. student + advisor
        # can both be pinged for the same course.
        ttk.Label(top, text="Notify profiles:").grid(row=1, column=0, sticky="nw",
                                                     padx=(0, 6), pady=(8, 0))
        self.add_profiles_frame = ttk.Frame(top)
        self.add_profiles_frame.grid(row=1, column=1, columnspan=4, sticky="w", pady=(8, 0))
        # name -> BooleanVar so callers can read selection state on submit.
        self.add_profile_vars: dict[str, tk.BooleanVar] = {}
        ttk.Label(top, text="(manage in the Profiles tab)",
                  style="Subtle.TLabel").grid(row=2, column=1, sticky="w", padx=(0, 0))

        ttk.Label(frame, text="Sections:").pack(anchor="w", pady=(12, 4))

        results_wrapper = ttk.Frame(frame, relief="groove", borderwidth=1)
        results_wrapper.pack(fill=tk.BOTH, expand=True)

        self.results_canvas = tk.Canvas(
            results_wrapper, highlightthickness=0, background=self.theme["panel_bg"],
        )
        rsb = ttk.Scrollbar(results_wrapper, orient="vertical", command=self.results_canvas.yview)
        self.results_canvas.configure(yscrollcommand=rsb.set)
        self.results_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        rsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.results_frame = ttk.Frame(self.results_canvas, style="Panel.TFrame")
        self.results_window_id = self.results_canvas.create_window((0, 0), window=self.results_frame, anchor="nw")
        self.results_frame.bind(
            "<Configure>",
            lambda _e: self.results_canvas.configure(scrollregion=self.results_canvas.bbox("all")),
        )
        self.results_canvas.bind(
            "<Configure>",
            lambda e: self.results_canvas.itemconfigure(self.results_window_id, width=e.width),
        )

        self.section_vars: list[tuple[tk.BooleanVar, vsb_client.Section, str]] = []
        self._last_results: tuple[str, str, str, list[vsb_client.Section], str | None] | None = None
        self._results_placeholder: str | None = None
        self._lookup_busy = False
        self._set_results_placeholder("Look up a course to see its sections.")

        add_bar = ttk.Frame(frame)
        add_bar.pack(fill=tk.X, pady=(10, 0))
        self.lookup_status = ttk.Label(add_bar, text="", style="Muted.TLabel")
        self.lookup_status.pack(side=tk.LEFT)
        ttk.Button(add_bar, text="Add selected to tracker",
                   command=self._on_add_selected).pack(side=tk.RIGHT)

    def _set_results_placeholder(self, text: str) -> None:
        for w in self.results_frame.winfo_children():
            w.destroy()
        self.section_vars = []
        self._last_results = None
        self._results_placeholder = text
        ttk.Label(self.results_frame, text=text, padding=12,
                  style="PanelMuted.TLabel").pack(anchor="w")

    # -- Tab 3: Profiles --

    def _build_profiles_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(frame, text="Profiles")

        intro = (
            "Profiles let you route notifications for different courses to "
            "different email addresses. The “Default” profile is always "
            "present; assign it to courses that should use whatever recipient "
            "you set under Settings."
        )
        ttk.Label(frame, text=intro, style="Muted.TLabel", wraplength=900,
                  justify="left").pack(anchor="w", pady=(0, 8))

        cols = ("name", "email")
        self.profiles_tree = ttk.Treeview(frame, columns=cols, show="headings",
                                          selectmode="browse", height=10)
        self.profiles_tree.heading("name", text="Profile name")
        self.profiles_tree.heading("email", text="Recipient email")
        self.profiles_tree.column("name", width=200, anchor=tk.W)
        self.profiles_tree.column("email", width=400, anchor=tk.W)
        self.profiles_tree.pack(fill=tk.BOTH, expand=True)
        self.profiles_tree.bind("<Double-1>", lambda _e: self._on_edit_profile())

        pbtns = ttk.Frame(frame)
        pbtns.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(pbtns, text="Add profile…", command=self._on_add_profile).pack(side=tk.LEFT)
        ttk.Button(pbtns, text="Edit selected…", command=self._on_edit_profile).pack(side=tk.LEFT, padx=6)
        ttk.Button(pbtns, text="Remove selected", command=self._on_remove_profile).pack(side=tk.LEFT, padx=6)

        self._refresh_profiles_view()
        self._refresh_profile_dropdowns()

    # -- Tab 4: Settings --

    def _build_settings_tab(self) -> None:
        frame = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(frame, text="Settings")

        s = self.cfg.settings

        # Polling
        poll_lf = ttk.LabelFrame(frame, text="Polling", padding=10)
        poll_lf.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(poll_lf, text="Check interval (seconds):").grid(row=0, column=0, sticky="w")
        self.var_interval = tk.IntVar(value=s.poll_interval_seconds)
        ttk.Spinbox(poll_lf, from_=5, to=3600, increment=5, width=8,
                    textvariable=self.var_interval).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(poll_lf, text="(min 5s; 30s is recommended)",
                  style="Subtle.TLabel").grid(row=0, column=2, sticky="w", padx=(8, 0))

        # Email
        em_lf = ttk.LabelFrame(frame, text="Email notifications (Gmail SMTP)", padding=10)
        em_lf.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(em_lf, text="Send notifications to:").grid(row=0, column=0, sticky="w")
        self.var_recipient = tk.StringVar(value=s.recipient_email)
        ttk.Entry(em_lf, textvariable=self.var_recipient, width=40).grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=2)

        ttk.Label(em_lf, text="Send from (Gmail address):").grid(row=1, column=0, sticky="w")
        self.var_sender = tk.StringVar(value=s.sender_email)
        ttk.Entry(em_lf, textvariable=self.var_sender, width=40).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=2)

        ttk.Label(em_lf, text="Gmail App Password:").grid(row=2, column=0, sticky="w")
        self.var_password = tk.StringVar(value=s.sender_app_password)
        self.password_entry = ttk.Entry(em_lf, textvariable=self.var_password, width=40, show="•")
        self.password_entry.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=2)
        self.var_show_pw = tk.BooleanVar(value=False)
        ttk.Checkbutton(em_lf, text="Show", variable=self.var_show_pw,
                        command=self._toggle_password_visibility).grid(row=2, column=2, sticky="w", padx=(6, 0))

        help_text = (
            "Use a Google App Password (16 chars). Generate one at:\n"
            "  myaccount.google.com/apppasswords\n"
            "Your Google account must have 2-Step Verification enabled."
        )
        ttk.Label(em_lf, text=help_text, style="Muted.TLabel", justify="left").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(6, 0),
        )

        adv = ttk.Frame(em_lf)
        adv.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Label(adv, text="SMTP host:").grid(row=0, column=0, sticky="w")
        self.var_smtp_host = tk.StringVar(value=s.smtp_host)
        ttk.Entry(adv, textvariable=self.var_smtp_host, width=22).grid(row=0, column=1, sticky="w", padx=(8, 16))
        ttk.Label(adv, text="Port:").grid(row=0, column=2, sticky="w")
        self.var_smtp_port = tk.IntVar(value=s.smtp_port)
        ttk.Spinbox(adv, textvariable=self.var_smtp_port, from_=1, to=65535, width=8).grid(row=0, column=3, sticky="w", padx=(8, 0))

        em_lf.columnconfigure(1, weight=1)

        # Appearance
        app_lf = ttk.LabelFrame(frame, text="Appearance", padding=10)
        app_lf.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(app_lf, text="Theme:").grid(row=0, column=0, sticky="w")
        self.var_theme = tk.StringVar(value=s.theme if s.theme in THEMES else "dark")
        ttk.Combobox(app_lf, textvariable=self.var_theme, width=10, state="readonly",
                     values=["dark", "light"]).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(app_lf, text="(applies immediately on Save)",
                  style="Subtle.TLabel").grid(row=0, column=2, sticky="w", padx=(8, 0))

        # Behavior
        beh_lf = ttk.LabelFrame(frame, text="Behavior", padding=10)
        beh_lf.pack(fill=tk.X, pady=(0, 10))
        self.var_edge = tk.BooleanVar(value=s.edge_trigger_notifications)
        ttk.Checkbutton(beh_lf,
                        text="Only email when a section transitions from full to open (no spam while it stays open)",
                        variable=self.var_edge).pack(anchor="w")
        self.var_notify_waitlist = tk.BooleanVar(value=s.notify_waitlist)
        ttk.Checkbutton(beh_lf,
                        text="Also email when a full section has a spot open on its waitlist",
                        variable=self.var_notify_waitlist).pack(anchor="w")
        self.var_tray = tk.BooleanVar(value=s.minimize_to_tray_on_close)
        ttk.Checkbutton(beh_lf,
                        text="Close button minimizes to system tray (keeps polling in background)",
                        variable=self.var_tray).pack(anchor="w")
        self.var_autostart_poll = tk.BooleanVar(value=s.start_polling_on_launch)
        ttk.Checkbutton(beh_lf,
                        text="Start polling automatically when the app launches",
                        variable=self.var_autostart_poll).pack(anchor="w")

        # Status Updates (Heartbeat)
        hb_lf = ttk.LabelFrame(frame, text="Status Updates (Heartbeat)", padding=10)
        hb_lf.pack(fill=tk.X, pady=(0, 10))
        self.var_hb_enabled = tk.BooleanVar(value=s.heartbeat_enabled)
        ttk.Checkbutton(hb_lf,
                        text="Send an email periodically to confirm the app is still awake and polling",
                        variable=self.var_hb_enabled).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(hb_lf, text="Send update email every:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.var_hb_interval = tk.IntVar(value=s.heartbeat_interval_hours)
        ttk.Spinbox(hb_lf, from_=1, to=168, increment=1, width=6,
                    textvariable=self.var_hb_interval).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        ttk.Label(hb_lf, text="hour(s)").grid(row=1, column=2, sticky="w", padx=(4, 0), pady=(6, 0))

        # Buttons
        btn_bar = ttk.Frame(frame)
        btn_bar.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(btn_bar, text="Save settings", command=self._on_save_settings).pack(side=tk.LEFT)
        ttk.Button(btn_bar, text="Send test email", command=self._on_send_test_email).pack(side=tk.LEFT, padx=8)
        self.settings_msg = ttk.Label(btn_bar, text="", style="Success.TLabel")
        self.settings_msg.pack(side=tk.LEFT, padx=(12, 0))

    # -- Status bar --

    def _build_status_bar(self) -> None:
        bar = ttk.Frame(self.root, padding=(10, 6))
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Separator(self.root, orient="horizontal").pack(fill=tk.X, side=tk.BOTTOM, before=bar)

        self.indicator = tk.Canvas(
            bar, width=14, height=14, highlightthickness=0,
            background=self.theme["bg"],
        )
        self.indicator.pack(side=tk.LEFT)
        self._indicator_color = ""
        self._draw_indicator("#999")

        self.status_label = ttk.Label(bar, text="Polling stopped.")
        self.status_label.pack(side=tk.LEFT, padx=(8, 0))

        self.last_check_label = ttk.Label(bar, text="", style="Muted.TLabel")
        self.last_check_label.pack(side=tk.RIGHT)

    def _draw_indicator(self, color: str) -> None:
        if color == self._indicator_color:
            return
        self.indicator.configure(background=self.theme["bg"])
        self.indicator.delete("all")
        self.indicator.create_oval(2, 2, 12, 12, fill=color,
                                   outline=self.theme["indicator_outline"])
        self._indicator_color = color

    # ---- callbacks from tracker thread ----

    def _tracker_update_callback(self) -> None:
        # Always marshal to tk main loop.
        try:
            self.root.after(0, self._on_tracker_tick)
        except RuntimeError:
            # tk may already be torn down on exit; ignore.
            pass

    def _tracker_log_callback(self, msg: str) -> None:
        self.log_lines.append(msg)
        # Cap log to last 500 entries to bound memory in 24/7 runs.
        if len(self.log_lines) > 500:
            del self.log_lines[:-500]

    def _on_tracker_tick(self) -> None:
        self.refresh_tracked_view()
        self.refresh_status_bar()

    # ---- view refresh ----

    def refresh_tracked_view(self) -> None:
        # Re-populate the treeview from cfg.tracked.
        existing = set(self.tree.get_children())
        for iid in existing:
            self.tree.delete(iid)
        for ts in self.cfg.tracked:
            if ts.last_open_seats is None:
                tag = "never_row"
                open_text = "—"
            elif ts.last_is_open:
                tag = "open_row"
                open_text = str(ts.last_open_seats)
            elif "error" in (ts.last_status or "").lower():
                tag = "error_row"
                open_text = str(ts.last_open_seats)
            else:
                tag = "full_row"
                open_text = str(ts.last_open_seats)
            self.tree.insert(
                "", "end", iid=ts.key,
                values=(
                    ts.course_code,
                    ts.course_title,
                    f"{ts.block_type} {ts.section_no}",
                    ts.last_status,
                    open_text,
                    ", ".join(ts.profile_names) if ts.profile_names else config.DEFAULT_PROFILE_NAME,
                    ts.last_checked_iso or "—",
                    ts.last_notified_iso or "—",
                ),
                tags=(tag,),
            )

    def refresh_status_bar(self) -> None:
        t = self.tracker
        if t.is_running:
            status = t.last_cycle_status or "Polling..."
            if status.lower().startswith("error"):
                self._draw_indicator("#e34")
            elif "no tracked" in status.lower():
                self._draw_indicator("#cb0")
            else:
                self._draw_indicator("#2a6")
            self.start_stop_btn.configure(text="Stop polling")
            self.status_label.configure(text=f"Polling every {self.cfg.settings.poll_interval_seconds}s — {status}")
        else:
            self._draw_indicator("#999")
            self.start_stop_btn.configure(text="Start polling")
            self.status_label.configure(text="Polling stopped.")
        last = t.last_cycle_finished_iso or "never"
        self.last_check_label.configure(text=f"Last check: {last}")

    # ---- handlers ----

    def _on_toggle_polling(self) -> None:
        if self.tracker.is_running:
            self.tracker.stop()
        else:
            if not self._guard_email_configured(allow_unconfigured=True):
                return
            self.tracker.start()
        self.refresh_status_bar()

    def _on_refresh_now(self) -> None:
        if not self.tracker.is_running:
            # Run a one-shot poll in a background thread so the UI stays responsive.
            threading.Thread(
                target=self._one_shot_poll, name="vsb-oneshot-poll", daemon=True,
            ).start()
        else:
            self.tracker.poke()

    def _one_shot_poll(self) -> None:
        # Reuse the tracker's _poll_once but without starting the loop.
        try:
            self.tracker._poll_once()  # noqa: SLF001 — intentional reuse
        except Exception:
            pass
        self.refresh_tracked_view()
        self.refresh_status_bar()

    # ---- profile management ----

    def _refresh_profiles_view(self) -> None:
        for iid in self.profiles_tree.get_children():
            self.profiles_tree.delete(iid)
        for p in self.cfg.profiles:
            email = p.recipient_email or "(empty — falls back to default recipient)"
            self.profiles_tree.insert("", "end", iid=p.name, values=(p.name, email))

    def _refresh_profile_dropdowns(self) -> None:
        """Rebuild every profile-picker widget so it reflects cfg.profiles.
        Preserves the user's current selections when possible (so renaming or
        adding a profile doesn't clobber unrelated checkboxes)."""
        if not hasattr(self, "add_profiles_frame"):
            return
        names = [p.name for p in self.cfg.profiles]
        # Snapshot which boxes were ticked so we can carry them over.
        previously_checked = {
            name for name, var in self.add_profile_vars.items() if var.get()
        }
        for w in self.add_profiles_frame.winfo_children():
            w.destroy()
        self.add_profile_vars = {}
        # First-time render (no prior state) → tick Default for convenience.
        first_time = not previously_checked
        for name in names:
            checked = (name in previously_checked) or (first_time and name == config.DEFAULT_PROFILE_NAME)
            var = tk.BooleanVar(value=checked)
            self.add_profile_vars[name] = var
            ttk.Checkbutton(self.add_profiles_frame, text=name, variable=var).pack(
                side=tk.LEFT, padx=(0, 10),
            )

    def _profile_dialog(self, title: str, initial_name: str = "",
                        initial_email: str = "", name_locked: bool = False
                        ) -> tuple[str, str] | None:
        """Modal dialog for add/edit. Returns (name, email) or None on cancel.
        Validation happens here so callers stay simple."""
        existing = {p.name for p in self.cfg.profiles}

        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(background=self.theme["bg"])
        dlg.resizable(False, False)

        result: dict[str, tuple[str, str] | None] = {"value": None}

        body = ttk.Frame(dlg, padding=14)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="Profile name:").grid(row=0, column=0, sticky="w", pady=2)
        name_var = tk.StringVar(value=initial_name)
        name_entry = ttk.Entry(body, textvariable=name_var, width=32)
        name_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=2)
        if name_locked:
            name_entry.state(["disabled"])
            ttk.Label(body, text='("Default" cannot be renamed)',
                      style="Subtle.TLabel").grid(row=1, column=1, sticky="w", padx=(8, 0))

        ttk.Label(body, text="Recipient email:").grid(row=2, column=0, sticky="w", pady=(8, 2))
        email_var = tk.StringVar(value=initial_email)
        email_entry = ttk.Entry(body, textvariable=email_var, width=32)
        email_entry.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=(8, 2))

        err_lbl = ttk.Label(body, text="", style="Danger.TLabel")
        err_lbl.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        def submit() -> None:
            name = name_var.get().strip()
            email = email_var.get().strip()
            if not name:
                err_lbl.configure(text="Name is required.")
                return
            if not name_locked and name != initial_name and name in existing:
                err_lbl.configure(text=f"A profile named “{name}” already exists.")
                return
            # Allow empty email — it just means "use settings.recipient_email
            # as fallback". Make that explicit in the UI but don't block save.
            result["value"] = (name, email)
            dlg.destroy()

        def cancel() -> None:
            dlg.destroy()

        btns = ttk.Frame(body)
        btns.grid(row=4, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Save", command=submit).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Cancel", command=cancel).pack(side=tk.RIGHT, padx=(0, 6))
        body.columnconfigure(1, weight=1)

        dlg.bind("<Return>", lambda _e: submit())
        dlg.bind("<Escape>", lambda _e: cancel())
        (email_entry if name_locked else name_entry).focus_set()

        dlg.wait_window()
        return result["value"]

    def _on_add_profile(self) -> None:
        res = self._profile_dialog(title="Add profile")
        if res is None:
            return
        name, email = res
        self.cfg.profiles.append(config.Profile(name=name, recipient_email=email))
        config.save(self.cfg)
        self._refresh_profiles_view()
        self._refresh_profile_dropdowns()

    def _on_edit_profile(self) -> None:
        sel = self.profiles_tree.selection()
        if not sel:
            messagebox.showinfo("Select a profile", "Pick a profile to edit first.")
            return
        old_name = sel[0]
        profile = self.cfg.get_profile(old_name)
        if profile is None:
            return
        locked = profile.name == config.DEFAULT_PROFILE_NAME
        res = self._profile_dialog(
            title=f"Edit profile — {profile.name}",
            initial_name=profile.name,
            initial_email=profile.recipient_email,
            name_locked=locked,
        )
        if res is None:
            return
        new_name, new_email = res
        profile.recipient_email = new_email
        if not locked and new_name != profile.name:
            # Cascade-rename across tracked sections so they keep their binding.
            for ts in self.cfg.tracked:
                ts.profile_names = [
                    new_name if n == profile.name else n for n in ts.profile_names
                ]
            profile.name = new_name
        config.save(self.cfg)
        self._refresh_profiles_view()
        self._refresh_profile_dropdowns()
        self.refresh_tracked_view()

    def _on_remove_profile(self) -> None:
        sel = self.profiles_tree.selection()
        if not sel:
            return
        name = sel[0]
        if name == config.DEFAULT_PROFILE_NAME:
            messagebox.showwarning("Can't remove",
                                   "The Default profile can't be removed.")
            return
        n_affected = sum(1 for t in self.cfg.tracked if name in t.profile_names)
        msg = f"Remove profile “{name}”?"
        if n_affected:
            msg += (f"\n\n{n_affected} tracked section(s) reference this profile; "
                    f"it will be removed from their notification list. Sections "
                    f"left with no profiles will fall back to “Default”.")
        if not messagebox.askyesno("Remove profile", msg):
            return
        self.cfg.profiles = [p for p in self.cfg.profiles if p.name != name]
        for ts in self.cfg.tracked:
            if name in ts.profile_names:
                ts.profile_names = [n for n in ts.profile_names if n != name]
                if not ts.profile_names:
                    ts.profile_names = [config.DEFAULT_PROFILE_NAME]
        config.save(self.cfg)
        self._refresh_profiles_view()
        self._refresh_profile_dropdowns()
        self.refresh_tracked_view()

    def _on_change_profile(self) -> None:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select sections",
                                "Pick one or more tracked sections first.")
            return
        names = [p.name for p in self.cfg.profiles]
        if not names:
            return

        # Seed checkbox state: any profile already on EVERY selected section
        # starts ticked. (Profiles on only some selected sections start
        # unchecked — applying the dialog overwrites, doesn't merge, which
        # matches how a "Change profiles" action usually reads.)
        keys = set(sel)
        selected_sections = [t for t in self.cfg.tracked if t.key in keys]
        common = set.intersection(*(set(t.profile_names) for t in selected_sections)) \
            if selected_sections else set()

        dlg = tk.Toplevel(self.root)
        dlg.title("Change profiles")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.configure(background=self.theme["bg"])
        dlg.resizable(False, False)

        body = ttk.Frame(dlg, padding=14)
        body.pack()
        ttk.Label(body,
                  text=f"Notify these profiles for the selected {len(sel)} section(s):",
                  ).pack(anchor="w")
        cb_frame = ttk.Frame(body)
        cb_frame.pack(anchor="w", pady=(6, 4))
        vars_by_name: dict[str, tk.BooleanVar] = {}
        for name in names:
            v = tk.BooleanVar(value=name in common)
            vars_by_name[name] = v
            ttk.Checkbutton(cb_frame, text=name, variable=v).pack(anchor="w")
        ttk.Label(body, text="(Pick at least one. Nothing ticked → Default.)",
                  style="Subtle.TLabel").pack(anchor="w", pady=(0, 6))

        def apply_change() -> None:
            chosen = [n for n, v in vars_by_name.items() if v.get()]
            if not chosen:
                chosen = [config.DEFAULT_PROFILE_NAME]
            for ts in self.cfg.tracked:
                if ts.key in keys:
                    ts.profile_names = list(chosen)
            config.save(self.cfg)
            self.refresh_tracked_view()
            dlg.destroy()

        btns = ttk.Frame(body)
        btns.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(btns, text="Apply", command=apply_change).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side=tk.RIGHT, padx=(0, 6))

        dlg.bind("<Return>", lambda _e: apply_change())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        dlg.wait_window()

    def _on_remove_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        if not messagebox.askyesno("Remove tracked sections",
                                   f"Remove {len(sel)} tracked section(s)?"):
            return
        keys = set(sel)
        self.cfg.tracked = [t for t in self.cfg.tracked if t.key not in keys]
        config.save(self.cfg)
        self.refresh_tracked_view()

    def _on_lookup(self) -> None:
        if self._lookup_busy:
            return
        course = self.course_var.get().strip()
        if not course:
            messagebox.showwarning("Course code required", "Type a course code like 'COMP 521'.")
            return
        term_label = self.term_var.get()
        term = next((c for lbl, c in self.term_options if lbl == term_label), None)
        if not term:
            messagebox.showwarning("Term required", "Select a term.")
            return
        self._lookup_busy = True
        self.lookup_status.configure(text=f"Looking up {course} for {term_label}…", style="Muted.TLabel")
        self._set_results_placeholder("Loading…")
        threading.Thread(
            target=self._lookup_worker, args=(term, course, term_label), daemon=True,
        ).start()

    def _lookup_worker(self, term: str, course: str, term_label: str) -> None:
        # Snapshot results / error into local vars *before* the lambda is queued:
        # `except ... as exc` clears `exc` at block exit, so a lambda that closes
        # over it would crash with NameError when it later runs on the main
        # thread (leaving _lookup_busy stuck True — looks like the UI froze).
        sections: list[vsb_client.Section] = []
        err: str | None = None
        try:
            sections = vsb_client.fetch_course(term, course)
        except vsb_client.VSBError as e:
            err = str(e)
        except Exception as e:
            err = f"Unexpected error: {e}"
        self.root.after(
            0,
            lambda s=sections, m=err:
                self._render_lookup_results(term, course, term_label, s, m),
        )

    def _render_lookup_results(self, term: str, course: str, term_label: str,
                               sections: list[vsb_client.Section], err: str | None) -> None:
        self._lookup_busy = False
        # Cache so we can re-render with the same data if the theme changes,
        # without forcing the user to re-look-up the course.
        self._last_results = (term, course, term_label, sections, err)
        self._results_placeholder = None
        for w in self.results_frame.winfo_children():
            w.destroy()
        self.section_vars = []
        if err:
            self.lookup_status.configure(text=f"Error: {err}", style="Danger.TLabel")
            ttk.Label(self.results_frame, text=err, padding=12,
                      style="PanelDanger.TLabel", wraplength=860).pack(anchor="w")
            return
        if not sections:
            self.lookup_status.configure(text="No sections returned.", style="Danger.TLabel")
            ttk.Label(self.results_frame, text="No sections found.", padding=12,
                      style="PanelMuted.TLabel").pack(anchor="w")
            return
        self.lookup_status.configure(
            text=f"Found {len(sections)} section(s) for {sections[0].course_key} — {sections[0].course_title}",
            style="Success.TLabel",
        )
        tracked_keys = {t.key for t in self.cfg.tracked}
        for sec in sections:
            ts_key = f"{term}|{sec.course_key}|{sec.block_type}|{sec.section_no}"
            already = ts_key in tracked_keys
            var = tk.BooleanVar(value=False)
            self.section_vars.append((var, sec, term))
            avail = (f"OPEN — {sec.open_seats} seat(s)"
                     if sec.is_open
                     else (f"FULL (waitlist {sec.waitlist_seats}/{sec.waitlist_capacity})"
                           if sec.waitlist_capacity > 0 else "FULL"))
            avail_style = "PanelSuccess.TLabel" if sec.is_open else "PanelWarning.TLabel"
            row = ttk.Frame(self.results_frame, style="Panel.TFrame")
            row.pack(fill=tk.X, padx=10, pady=4, anchor="w")
            cb = ttk.Checkbutton(row, variable=var, style="Panel.TCheckbutton",
                                 text=f"{sec.block_type} {sec.section_no}")
            cb.pack(side=tk.LEFT)
            if already:
                cb.state(["disabled"])
                ttk.Label(row, text=" (already tracked)",
                          style="PanelMuted.TLabel").pack(side=tk.LEFT)
            ttk.Label(row, text=f"  —  {avail}", style=avail_style).pack(side=tk.LEFT)
            if sec.note:
                ttk.Label(row, text=f"   {sec.note}",
                          style="PanelMuted.TLabel").pack(side=tk.LEFT)

    def _rerender_results_for_theme(self) -> None:
        """Replay whatever was last shown in the Add Course results pane so the
        rows pick up the new theme's colors."""
        if self._results_placeholder is not None:
            self._set_results_placeholder(self._results_placeholder)
        elif self._last_results is not None:
            term, course, term_label, sections, err = self._last_results
            self._render_lookup_results(term, course, term_label, sections, err)

    def _on_add_selected(self) -> None:
        if not self.section_vars:
            messagebox.showinfo("Nothing to add", "Look up a course first.")
            return
        # Collect every checked profile, validate each still exists, and fall
        # back to Default if the user ticked nothing.
        valid_names = {p.name for p in self.cfg.profiles}
        chosen = [n for n, var in self.add_profile_vars.items()
                  if var.get() and n in valid_names]
        if not chosen:
            chosen = [config.DEFAULT_PROFILE_NAME]
        existing = {t.key for t in self.cfg.tracked}
        added = 0
        for var, sec, term in self.section_vars:
            if not var.get():
                continue
            ts = config.TrackedSection(
                course_code=sec.course_key,
                course_title=sec.course_title,
                term=term,
                section_no=sec.section_no,
                block_type=sec.block_type,
                last_open_seats=sec.open_seats,
                last_is_open=sec.is_open,
                last_status=(f"OPEN — {sec.open_seats} seat(s) available"
                             if sec.is_open
                             else f"Full (waitlist {sec.waitlist_seats}/{sec.waitlist_capacity})"),
                last_checked_iso=None,
                last_notified_iso=None,
                profile_names=list(chosen),
            )
            if ts.key in existing:
                continue
            self.cfg.tracked.append(ts)
            existing.add(ts.key)
            added += 1
        if added == 0:
            messagebox.showinfo("Nothing added", "No new sections were selected.")
            return
        config.save(self.cfg)
        self.refresh_tracked_view()
        self.tracker.poke()
        self.notebook.select(0)  # jump to Tracked tab
        messagebox.showinfo("Added", f"Added {added} section(s) to the tracker.")

    def _toggle_password_visibility(self) -> None:
        self.password_entry.configure(show="" if self.var_show_pw.get() else "•")

    def _on_save_settings(self) -> None:
        s = self.cfg.settings
        try:
            interval = int(self.var_interval.get())
        except (tk.TclError, ValueError):
            interval = s.poll_interval_seconds
        s.poll_interval_seconds = max(5, interval)
        s.recipient_email = self.var_recipient.get().strip()
        s.sender_email = self.var_sender.get().strip()
        s.sender_app_password = self.var_password.get()
        s.smtp_host = self.var_smtp_host.get().strip() or "smtp.gmail.com"
        try:
            s.smtp_port = int(self.var_smtp_port.get())
        except (tk.TclError, ValueError):
            s.smtp_port = 587
        s.edge_trigger_notifications = bool(self.var_edge.get())
        s.notify_waitlist = bool(self.var_notify_waitlist.get())
        s.minimize_to_tray_on_close = bool(self.var_tray.get())
        s.start_polling_on_launch = bool(self.var_autostart_poll.get())
        s.heartbeat_enabled = bool(self.var_hb_enabled.get())
        try:
            hb_interval = int(self.var_hb_interval.get())
        except (tk.TclError, ValueError):
            hb_interval = s.heartbeat_interval_hours
        s.heartbeat_interval_hours = max(1, hb_interval)
        new_theme = self.var_theme.get() if self.var_theme.get() in THEMES else s.theme
        theme_changed = new_theme != s.theme
        s.theme = new_theme
        config.save(self.cfg)
        if theme_changed:
            self._apply_theme(s.theme)
        self.tracker.poke()  # in case interval changed
        self.refresh_status_bar()
        self.settings_msg.configure(text="Saved.", style="Success.TLabel")
        self.root.after(2500, lambda: self.settings_msg.configure(text=""))

    def _on_send_test_email(self) -> None:
        # Save first so the test uses the current form values.
        self._on_save_settings()
        if not self._guard_email_configured():
            return
        self.settings_msg.configure(text="Sending…", style="Muted.TLabel")
        self.root.update_idletasks()

        def worker():
            try:
                notifier.send_test(self.cfg.settings)
                self.root.after(0, lambda: self.settings_msg.configure(
                    text=f"Test email sent to {self.cfg.settings.recipient_email}.",
                    style="Success.TLabel"))
            except notifier.EmailError as exc:
                self.root.after(0, lambda: messagebox.showerror("Email failed", str(exc)))
                self.root.after(0, lambda: self.settings_msg.configure(text="Failed.", style="Danger.TLabel"))

        threading.Thread(target=worker, daemon=True, name="vsb-test-email").start()

    def _guard_email_configured(self, allow_unconfigured: bool = False) -> bool:
        s = self.cfg.settings
        missing = []
        if not s.sender_email:
            missing.append("sender Gmail address")
        if not s.sender_app_password:
            missing.append("Gmail app password")
        if not s.recipient_email:
            missing.append("recipient email")
        if not missing:
            return True
        msg = "Email is not fully configured (" + ", ".join(missing) + ")."
        if allow_unconfigured:
            return messagebox.askyesno(
                "Email not configured",
                msg + "\n\nPolling will still run but no emails will be sent. Continue?",
            )
        messagebox.showwarning("Email not configured", msg)
        return False

    def _on_close(self) -> None:
        if self.cfg.settings.minimize_to_tray_on_close:
            self.on_close_to_tray()
        else:
            self.quit_app()

    def quit_app(self) -> None:
        self.tracker.stop()
        try:
            config.save(self.cfg)
        except OSError:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass
