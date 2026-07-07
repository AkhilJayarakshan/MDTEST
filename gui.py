"""
MDAQ Desktop Application
Bluetooth-enabled Data Acquisition & Monitoring System
Compatible: Windows 10+  |  Python 3.10+
Dependencies: bleak, tkinter (stdlib)
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import queue
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
import math
import sys

from ble_manager import BLEManager, BLE_AVAILABLE
from protocol import PacketProtocol
from settings import load_settings, save_settings, WD_CHANNELS


class MDAQApp(tk.Tk):
    # ── palette ──────────────────────────────────────────────────────────────
    C = {
        "bg":        "#0D1117",
        "panel":     "#161B22",
        "border":    "#30363D",
        "accent":    "#00C8FF",
        "accent2":   "#00FF9F",
        "warn":      "#FF6B35",
        "text":      "#E6EDF3",
        "subtext":   "#8B949E",
        "entry_bg":  "#0D1117",
        "btn_bg":    "#1F6FEB",
        "btn_hover": "#388BFD",
        "success":   "#3FB950",
        "danger":    "#F85149",
    }

    ERROR_CODE_MAP = {
        1: "Battery low",
        2: "DAQ ID mismatch",
        4: "Wearable ID mismatch",
        8: "UHI ID mismatch",
        16: "Wearable size mismatch",
    }

    @staticmethod
    def decode_error_code(code: int) -> list[str]:
        errors = []
        for bit, label in MDAQApp.ERROR_CODE_MAP.items():
            if code & bit:
                errors.append(label)
        return errors

    def __init__(self):
        super().__init__()
        self.title("MDAQ — Data Acquisition System")
        
        # Calculate optimal window size based on available screen space (accounting for taskbar)
        # Typical taskbar height: ~40-50px on Windows; add buffer to be safe
        taskbar_height = 60  # conservative estimate for Windows taskbar
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        available_height = screen_height - taskbar_height
        
        # Use 85% of available space, with reasonable limits
        optimal_width = min(int(screen_width * 0.85), 1400)
        optimal_height = min(int(available_height * 0.90), 900)
        # Ensure minimum usable size
        optimal_width = max(optimal_width, 1000)
        optimal_height = max(optimal_height, 650)
        
        self.geometry(f"{optimal_width}x{optimal_height}")
        self.minsize(900, 600)  # More reasonable minimum for laptops
        self.configure(bg=self.C["bg"])
        
        # increase default UI scaling to improve readability and reduce pixelation
        try:
            self.tk.call('tk', 'scaling', 1.5)
        except Exception:
            pass

        self.settings = load_settings()
        self.settings.setdefault("history", {})
        self.settings.setdefault("channel_map", {})
        for size, default_channels in WD_CHANNELS.items():
            if size not in self.settings["channel_map"] or not isinstance(self.settings["channel_map"][size], list):
                self.settings["channel_map"][size] = default_channels.copy()
        save_settings(self.settings)
        Path(self.settings["download_path"]).mkdir(parents=True, exist_ok=True)

        self.ble_q: queue.Queue = queue.Queue()
        self.ble = BLEManager(self.ble_q)

        self.connected_address: str = ""
        self.connected_name: str = ""
        self._ble_connected_state: bool = False
        self.notifications: list[dict] = []
        self._page_history: list[str] = []
        self.unseen_notif_count: int = 0
        self.bi_registered: bool = False
        self.device_info: dict = {}
        # track which error codes we've already notified about to avoid spam
        self._notified_error_codes: set = set()
        self.screening_session_active: bool = False
        self.screening_packets: list[dict] = []
        self.screening_count: int = 0
        # retrieval collection state
        self.collecting_retrieve: bool = False
        self.retrieve_packets: list[dict] = []
        self._retrieve_timer = None
        self.current_page = ""
        self.nav_buttons = {}  # Store button references for highlighting
        self._pending_retrieve_uhid: str | None = None
        self._pending_retrieve_path: str | None = None
        self._pending_retrieve_wd_size: str | None = None
        self._pending_retrieve_waiting_for_basic_info: bool = False
        self._pending_retrieve_after_basic_info_timer = None
        self._pending_initiation_response: bool = False
        self._pending_initiation_timer = None

        self._style()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_app_close)
        self._show_welcome()
        self._poll_queue()

    # ── ttk style ────────────────────────────────────────────────────────────
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        C = self.C
        s.configure(".",             background=C["bg"],    foreground=C["text"],
                     font=("Segoe UI", 13))
        s.configure("TFrame",        background=C["bg"])
        s.configure("Panel.TFrame",  background=C["panel"])
        s.configure("TLabel",        background=C["bg"],    foreground=C["text"],
                     font=("Segoe UI", 13))
        s.configure("Panel.TLabel",  background=C["panel"], foreground=C["text"],
                     font=("Segoe UI", 13))
        s.configure("Sub.TLabel",    background=C["panel"], foreground=C["subtext"],
                     font=("Segoe UI", 12))
        s.configure("Accent.TLabel", background=C["panel"], foreground=C["accent"],
                     font=("Segoe UI", 13, "bold"))
        s.configure("TEntry",        fieldbackground=C["entry_bg"], foreground=C["text"],
                     insertcolor=C["accent"], bordercolor=C["border"],
                     font=("Segoe UI", 13))
        s.configure("TCombobox",     fieldbackground=C["entry_bg"], foreground=C["text"],
                     selectbackground=C["accent"], selectforeground=C["bg"],
                     insertcolor=C["accent"], arrowcolor=C["text"],
                     background=C["entry_bg"], font=("Segoe UI", 13))
        s.map("TCombobox",           fieldbackground=[("readonly", C["entry_bg"])],
              foreground=[("readonly", C["text"])],
              arrowcolor=[("!disabled", C["text"]), ("disabled", C["subtext"])])
        s.configure("Treeview",      background=C["panel"], foreground=C["text"],
                     fieldbackground=C["panel"], rowheight=40,
                     font=("Segoe UI", 12))
        s.configure("Treeview.Heading", background=C["border"], foreground=C["accent"],
                     font=("Segoe UI", 12, "bold"))
        s.map("Treeview", background=[("selected", C["accent"])],
              foreground=[("selected", C["bg"])])

    def _get_history_values(self, key: str, max_items: int = 2) -> list[str]:
        history = self.settings.get("history", {}) or {}
        values = history.get(key, [])
        return values[:max_items]

    def _push_history_value(self, key: str, value: str, max_items: int = 2):
        if not value:
            return
        value = str(value).strip()
        if not value:
            return
        history = self.settings.setdefault("history", {})
        items = history.get(key, [])
        if value in items:
            items.remove(value)
        items.insert(0, value)
        history[key] = items[:max_items]
        save_settings(self.settings)

    def _make_history_combobox(self, parent, textvariable, key: str, width=30, state="normal"):
        cb = ttk.Combobox(parent, textvariable=textvariable,
                          values=self._get_history_values(key),
                          width=width,
                          state=state)

        def _activate(cb_widget, cb_key: str):
            cb_widget.configure(values=self._get_history_values(cb_key))
            try:
                cb_widget.focus_set()
            except Exception:
                pass

        cb.bind("<FocusIn>", lambda e, cb_widget=cb, cb_key=key: _activate(cb_widget, cb_key))
        cb.bind("<Button-1>", lambda e, cb_widget=cb, cb_key=key: _activate(cb_widget, cb_key))
        cb.bind("<KeyRelease>", lambda e, cb_widget=cb, cb_key=key: _activate(cb_widget, cb_key))
        try:
            cb.configure(cursor="xterm")
            cb.configure(insertbackground=self.C["accent"])
        except Exception:
            pass
        return cb

    # ─────────────────────────────────────────────────────────────────────────
    def _add_mousewheel_bindings(self, widget, is_text: bool = False):
        """Bind mousewheel and touchpad scrolling to the given widget while
        the pointer is over it. Works on Windows/macOS/Linux (basic support).
        """
        def _on_mousewheel(event):
            try:
                delta = int(event.delta / 120)
            except Exception:
                # Linux uses Button-4/Button-5; event.delta may not exist
                delta = 1 if event.num == 5 else -1
            if is_text:
                widget.yview_scroll(-delta, "units")
            else:
                widget.yview_scroll(-delta, "units")

        def _bind(_):
            widget.bind_all("<MouseWheel>", _on_mousewheel)
            widget.bind_all("<Button-4>", _on_mousewheel)
            widget.bind_all("<Button-5>", _on_mousewheel)

        def _unbind(_):
            try:
                widget.unbind_all("<MouseWheel>")
                widget.unbind_all("<Button-4>")
                widget.unbind_all("<Button-5>")
            except Exception:
                pass

        widget.bind("<Enter>", _bind)
        widget.bind("<Leave>", _unbind)

    #  UI STRUCTURE
    # ─────────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        C = self.C

        # root grid with proper row/column weighting
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(3, minsize=10)  # bottom padding for taskbar safety

        # ── left sidebar ─────────────────────────────────────────────────────
        self.sidebar = tk.Frame(self, bg=C["panel"], width=420)
        self.sidebar.grid(row=0, column=0, rowspan=3, sticky="ns")
        self.sidebar.grid_propagate(False)
        self._build_sidebar()

        # ── top navigation bar ───────────────────────────────────────────────
        self.top_nav = tk.Frame(self, bg=C["bg"], height=90)
        self.top_nav.grid(row=0, column=1, sticky="ew")
        self.top_nav.grid_propagate(False)
        self._build_top_nav()

        # ── main content ─────────────────────────────────────────────────────
        self.content = tk.Frame(self, bg=C["bg"])
        self.content.grid(row=1, column=1, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)

        # ── live monitor bar (bottom) ─────────────────────────────────────────
        self._build_monitor_bar()

        # pages dict
        self.pages: dict[str, tk.Frame] = {}
        self._build_welcome_page()
        self._build_home_page()
        self._build_screening_page()
        self._build_screening_live_page()
        self._build_subject_page()
        self._build_retrieve_options_page()
        self._build_patient_info_page()
        self._build_retrieve_page()
        self._build_notifications_page()
        self._build_settings_page()
        self._build_about_page()
        self._build_channel_mapping_page()

    # ─────────────────────────────────────────────────────────────────────────
    #  SIDEBAR
    # ─────────────────────────────────────────────────────────────────────────
    def _build_sidebar(self):
        C = self.C
        sb = self.sidebar

        # logo area
        logo_frame = tk.Frame(sb, bg=C["panel"], pady=18)
        logo_frame.pack(fill="x")
        # Try multiple possible logo image filenames (user-provided screenshot preferred)
        logo_dir = Path(__file__).resolve().parent
        candidates = [
            logo_dir / "Screenshot 2026-07-07 010121.png",
            logo_dir / "ChatGPT Image Jul 7, 2026, 12_28_22 AM.png",
            logo_dir / "ChatGPT Image Jul 5, 2026, 11_10_34 PM.png",
        ]
        found = None
        for p in candidates:
            if p.exists():
                found = p
                break
        if not found:
            # fallback to any Screenshot*.png in the folder
            for p in logo_dir.glob("Screenshot*.png"):
                found = p
                break
        if found:
            try:
                img = tk.PhotoImage(file=str(found))
                max_width = 360
                max_height = 120
                w, h = img.width(), img.height()
                scale_w = math.ceil(w / max_width) if w > max_width else 1
                scale_h = math.ceil(h / max_height) if h > max_height else 1
                scale = max(1, scale_w, scale_h)
                if scale > 1:
                    img = img.subsample(scale, scale)
                self.sidebar_logo = img
                tk.Label(logo_frame, image=self.sidebar_logo, bg=C["panel"]).pack()
            except Exception:
                tk.Label(logo_frame, text="⬡ MDAQ", bg=C["panel"],
                     fg=C["accent"], font=("Segoe UI", 28, "bold")).pack()
        else:
            tk.Label(logo_frame, text="⬡ MDAQ", bg=C["panel"],
                 fg=C["accent"], font=("Segoe UI", 28, "bold")).pack()
        tk.Label(logo_frame, text="v1.2.0", bg=C["panel"],
             fg=C["subtext"], font=("Segoe UI", 11)).pack()

        ttk.Separator(sb, orient="horizontal").pack(fill="x", padx=10)

        # ── BLE section ──────────────────────────────────────────────────────
        ble_frame = tk.Frame(sb, bg=C["panel"], pady=10, padx=12)
        ble_frame.pack(fill="x")

        tk.Label(ble_frame, text="BLUETOOTH", bg=C["panel"],
                 fg=C["subtext"], font=("Segoe UI", 11, "bold")).pack(anchor="w")

        self.conn_label = tk.Label(ble_frame, text="● Not connected",
                       bg=C["panel"], fg=C["danger"],
                       font=("Segoe UI", 14, "bold"))
        self.conn_label.pack(anchor="w", pady=(6, 12))

        self.scan_btn = self._sb_btn(ble_frame, "🔍  Scan Devices", self._ble_scan)
        self.scan_btn.pack(fill="x", pady=2)
        self.disconnect_btn = self._sb_btn(ble_frame, "⏏  Disconnect", self._ble_disconnect,
                                           state="disabled")
        self.disconnect_btn.pack(fill="x", pady=2)

        # device list
        tk.Label(ble_frame, text="Discovered Devices", bg=C["panel"],
                 fg=C["subtext"], font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(14, 4))

        list_frame = tk.Frame(ble_frame, bg=C["border"], bd=1)
        list_frame.pack(fill="both", expand=True, pady=(0, 10))

        self.device_listbox = tk.Listbox(list_frame, bg=C["entry_bg"], fg=C["text"],
                         selectbackground=C["accent"],
                         selectforeground=C["bg"],
                         font=("Segoe UI", 13), height=10,
                         borderwidth=0, highlightthickness=0, activestyle="none")
        self.device_listbox.pack(fill="both", expand=True)
        self.device_listbox.bind("<Double-Button-1>", self._ble_connect_selected)

        self._sb_btn(ble_frame, "↩  Connect Selected",
                     self._ble_connect_selected).pack(fill="x", pady=(4, 0))

    def _sb_btn(self, parent, text, cmd, state="normal"):
        C = self.C
        btn = tk.Button(parent, text=text, command=cmd,
                        bg=C["panel"], fg=C["text"],
                        activebackground=C["border"],
                        activeforeground=C["accent"],
                        relief="flat", cursor="hand2",
                        font=("Segoe UI", 13, "bold"), anchor="w",
                        padx=12, pady=11, state=state)
        btn.bind("<Enter>", lambda e: btn.configure(fg=C["accent"]))
        btn.bind("<Leave>", lambda e: btn.configure(fg=C["text"]))
        return btn

    # ─────────────────────────────────────────────────────────────────────────
    #  TOP NAVIGATION BAR
    # ─────────────────────────────────────────────────────────────────────────
    def _build_top_nav(self):
        C = self.C
        nav_bar = tk.Frame(self.top_nav, bg=C["bg"])
        nav_bar.pack(fill="both", expand=True, padx=20, pady=12)

        # Navigation buttons (left side)
        nav_buttons = tk.Frame(nav_bar, bg=C["bg"])
        nav_buttons.pack(side="left", fill="y")

        nav_items = [
            ("🏠  Home",          "home"),
            ("🔔  Notifications", "notifications"),
            ("⚙️  Settings",      "settings"),
        ]

        self.nav_buttons = {}  # Store button references
        self.notif_nav_btn = None
        for label, page in nav_items:
            btn = self._top_nav_btn(nav_buttons, label, lambda p=page: self._show_page(p))
            btn.pack(side="left", padx=8)
            self.nav_buttons[page] = btn
            if page == "notifications":
                self.notif_nav_btn = btn

        # Monitor toggle (right side)
        mon_frame = tk.Frame(nav_bar, bg=C["bg"])
        mon_frame.pack(side="right", fill="y", padx=(20, 0))

        self.monitor_var = tk.BooleanVar(value=False)
        tk.Checkbutton(mon_frame, text="◉ Log Monitor",
                       variable=self.monitor_var,
                       command=self._toggle_monitor,
                       bg=C["bg"], fg=C["accent"],
                       selectcolor=C["border"],
                       activebackground=C["bg"],
                       activeforeground=C["accent"],
                       font=("Segoe UI", 13, "bold"),
                       padx=8, pady=6).pack(side="left")

        tk.Button(mon_frame, text="About",
                  command=lambda: self._show_page("about"),
                  bg=C["border"], fg=C["text"],
                  activebackground=C["accent"],
                  activeforeground=C["bg"],
                  relief="flat", cursor="hand2",
                  font=("Segoe UI", 10), padx=1, pady=0.1).pack(side="right", padx=(1, 0))

    def _top_nav_btn(self, parent, text, cmd, state="normal"):
        C = self.C
        btn = tk.Button(parent, text=text, command=cmd,
                        bg=C["border"], fg=C["text"],
                        activebackground=C["accent"],
                        activeforeground=C["bg"],
                        relief="flat", cursor="hand2",
                        font=("Segoe UI", 13, "bold"),
                        padx=14, pady=10, state=state,
                        highlightthickness=0)
        btn.bind("<Enter>", lambda e, b=btn: self._on_btn_enter(b))
        btn.bind("<Leave>", lambda e, b=btn: self._on_btn_leave(b))
        return btn

    def _on_btn_enter(self, btn):
        """Highlight button on hover if not active"""
        current_bg = btn.cget("bg")
        if current_bg != self.C["btn_bg"]:
            btn.configure(bg=self.C["accent"], fg=self.C["bg"])

    def _on_btn_leave(self, btn):
        """Reset button on hover leave if not active"""
        current_bg = btn.cget("bg")
        if current_bg != self.C["btn_bg"]:
            btn.configure(bg=self.C["border"], fg=self.C["text"])

    # ─────────────────────────────────────────────────────────────────────────
    #  MONITOR BAR
    # ─────────────────────────────────────────────────────────────────────────
    def _build_monitor_bar(self):
        C = self.C
        self.monitor_frame = tk.Frame(self.content, bg="#0A0E14", height=180)
        self.monitor_frame.grid(row=1, column=0, sticky="nsew", padx=0, pady=(8, 0))
        self.monitor_frame.grid_remove()   # hidden initially
        self.monitor_frame.columnconfigure(0, weight=1)
        self.monitor_frame.rowconfigure(1, weight=1)  # allow scroll area to expand

        hdr = tk.Frame(self.monitor_frame, bg="#0A0E14")
        hdr.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 0))

        tk.Label(hdr, text="◉ LOG MONITOR", bg="#0A0E14",
                 fg=C["accent2"], font=("Courier", 13, "bold")).pack(side="left")

        tk.Button(hdr, text="CLEAR", command=self._monitor_clear,
                  bg=C["border"], fg=C["subtext"], font=("Segoe UI", 11),
                  relief="flat", padx=10, pady=4).pack(side="right", padx=3)
        tk.Button(hdr, text="SAVE AS", command=self._monitor_save,
                  bg=C["border"], fg=C["subtext"], font=("Segoe UI", 11),
                  relief="flat", padx=10, pady=4).pack(side="right", padx=3)

        self.monitor_text = scrolledtext.ScrolledText(
            self.monitor_frame, bg="#0A0E14", fg=C["accent2"],
            font=("Courier", 12), height=7, borderwidth=0,
            insertbackground=C["accent2"], wrap="word")
        self.monitor_text.grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 8))
        self.monitor_text.bind("<Key>", lambda e: "break")
        self.monitor_text.bind("<Control-v>", lambda e: "break")
        self.monitor_text.bind("<Control-x>", lambda e: "break")
        try:
            self._add_mousewheel_bindings(self.monitor_text, is_text=True)
        except Exception:
            pass

    def _toggle_monitor(self):
        if self.monitor_var.get():
            self.monitor_frame.grid(row=1, column=0, sticky="nsew", padx=0, pady=(8, 0))
        else:
            self.monitor_frame.grid_remove()

    def _monitor_log(self, msg: str):
        self.monitor_text.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self.monitor_text.see("end")

    def _monitor_clear(self):
        self.monitor_text.delete("1.0", "end")

    def _monitor_save(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text Files", "*.txt"), ("All", "*.*")],
            initialdir=self.settings["download_path"])
        if path:
            content = self.monitor_text.get("1.0", "end")
            with open(path, "w") as f:
                f.write(content)
            #self._add_notification("Monitor data saved.", level="info")

    # ─────────────────────────────────────────────────────────────────────────
    #  PAGE SWITCHER
    # ─────────────────────────────────────────────────────────────────────────
    def _show_page(self, name: str, record_history: bool = True):
        # maintain history so Back buttons can return to the last visited page
        current = getattr(self, "_current_page", None)
        if record_history and current and current != name:
            self._page_history.append(current)
        self._current_page = name

        # Hide top nav for welcome page, show for others
        if name == "welcome":
            self.top_nav.grid_remove()
        else:
            self.top_nav.grid()
        
        # Hide all pages
        for p in self.pages.values():
            p.grid_remove()
        self.pages[name].grid(row=0, column=0, sticky="nsew")
        
        # Highlight active button
        self._update_nav_button_states(name)
        
        if name == "notifications":
            self.unseen_notif_count = 0
            self._refresh_notif_badge()
            self._refresh_notifications()

    def _go_back(self):
        if self._page_history:
            previous = self._page_history.pop()
            self._show_page(previous, record_history=False)
        else:
            self._show_page("home", record_history=False)

    def _update_nav_button_states(self, active_page: str):
        """Highlight the active navigation button in blue"""
        C = self.C
        for page, btn in self.nav_buttons.items():
            if page == active_page:
                # Active button - blue background
                btn.configure(bg=C["btn_bg"], fg=C["text"])
            else:
                # Inactive button - dark background
                btn.configure(bg=C["border"], fg=C["text"])

    def _show_welcome(self):
        self.sidebar.grid_remove()
        self.top_nav.grid_remove()  # Hide nav bar on welcome screen
        self._show_page("welcome")
        self.after(2500, self._enter_main)

    def _enter_main(self):
        self.sidebar.grid()
        self.top_nav.grid()  # Show nav bar
        self._show_page("home")  # This will also highlight the Home button

    # ─────────────────────────────────────────────────────────────────────────
    #  WELCOME PAGE
    # ─────────────────────────────────────────────────────────────────────────
    def _build_welcome_page(self):
        C = self.C
        f = tk.Frame(self.content, bg=C["bg"])
        self.pages["welcome"] = f
        f.columnconfigure(0, weight=1)
        f.rowconfigure(0, weight=1)

        inner = tk.Frame(f, bg=C["bg"])
        inner.place(relx=0.5, rely=0.5, anchor="center")

        image_path = Path(__file__).resolve().parent / "ChatGPT Image Jul 7, 2026, 12_28_22 AM.png"
        if image_path.exists():
            try:
                self.welcome_image = tk.PhotoImage(file=str(image_path))
                tk.Label(inner, image=self.welcome_image, bg=C["bg"]).pack(pady=(0, 20))
            except Exception:
                tk.Label(inner, text="MDAQ", bg=C["bg"], fg=C["text"],
                         font=("Segoe UI", 56, "bold")).pack(pady=(0, 24))
        else:
            tk.Label(inner, text="MDAQ", bg=C["bg"], fg=C["text"],
                     font=("Segoe UI", 56, "bold")).pack(pady=(0, 24))


    # ─────────────────────────────────────────────────────────────────────────
    #  HOME PAGE
    # ─────────────────────────────────────────────────────────────────────────
    def _build_home_page(self):
        C = self.C
        f = tk.Frame(self.content, bg=C["bg"])
        self.pages["home"] = f
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(2, weight=1)

        tk.Label(f, text="Home", bg=C["bg"], fg=C["text"],
                 font=("Segoe UI", 28, "bold")).grid(
                     row=0, column=0, columnspan=2, sticky="w", padx=32, pady=(28, 8))
        tk.Label(f, text="Select an action to begin", bg=C["bg"], fg=C["subtext"],
                 font=("Segoe UI", 15)).grid(
                     row=1, column=0, columnspan=2, sticky="w", padx=32, pady=(0, 28))

        card_row = tk.Frame(f, bg=C["bg"])
        card_row.grid(row=2, column=0, columnspan=2, padx=32, sticky="nsew")
        card_row.columnconfigure(0, weight=1)
        card_row.columnconfigure(1, weight=1)

        self._home_card(card_row, 0,
                        icon="▶",
                        title="Screening",
                        desc="",
                        color=C["accent"],
                        cmd=lambda: self._show_page("screening"))

        self._home_card(card_row, 1,
                        icon="⤓",
                        title="Retrieve Data",
                        desc="",
                        color=C["accent2"],
                        cmd=lambda: self._show_page("retrieve_options"))

    def _home_card(self, parent, col, icon, title, desc, color, cmd):
        C = self.C
        card = tk.Frame(parent, bg=C["panel"], padx=32, pady=32, bd=0)
        card.grid(row=0, column=col, padx=(0 if col else 0, 16) if col else (0, 16),
                  sticky="nsew", pady=0)

        tk.Label(card, text=icon, bg=C["panel"], fg=color,
                 font=("Segoe UI", 48)).pack(anchor="w")
        tk.Label(card, text=title, bg=C["panel"], fg=C["text"],
                 font=("Segoe UI", 18, "bold")).pack(anchor="w", pady=(12, 8))
        if desc:
            tk.Label(card, text=desc, bg=C["panel"], fg=C["subtext"],
                     font=("Segoe UI", 13), justify="left").pack(anchor="w")

        btn = tk.Button(card, text=f"Open →", command=cmd,
                        bg=color, fg=C["bg"],
                        font=("Segoe UI", 13, "bold"),
                        relief="flat", cursor="hand2", padx=18, pady=12)
        btn.pack(anchor="w", pady=(24, 0))
        card.bind("<Enter>", lambda e: card.configure(bg="#1E2530"))
        return card

    # ─────────────────────────────────────────────────────────────────────────
    #  SUBJECT INFO PAGE
    # ─────────────────────────────────────────────────────────────────────────
    def _build_subject_page(self):
        C = self.C
        f = tk.Frame(self.content, bg=C["bg"])
        self.pages["subject"] = f
        f.columnconfigure(0, weight=1)

        # header
        hdr = tk.Frame(f, bg=C["bg"])
        hdr.pack(fill="x", padx=32, pady=(20, 12))
        back_btn = tk.Button(hdr, text="← Back", command=self._go_back,
                  bg=C["border"], fg=C["accent"], font=("Segoe UI", 14, "bold"),
                  relief="flat", cursor="hand2", padx=14, pady=10,
                  activebackground=C["accent"], activeforeground=C["bg"],
                  highlightthickness=0)
        back_btn.pack(side="left")
        back_btn.bind("<Enter>", lambda e: back_btn.configure(bg=C["accent"], fg=C["bg"]))
        back_btn.bind("<Leave>", lambda e: back_btn.configure(bg=C["border"], fg=C["accent"]))
        tk.Label(hdr, text="Subject Information", bg=C["bg"], fg=C["text"],
                 font=("Segoe UI", 24, "bold")).pack(side="left", padx=20)

        # make form scrollable with mousewheel
        canvas = tk.Canvas(f, bg=C["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(f, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=32, pady=(0, 24))
        scrollbar.pack(side="right", fill="y", pady=(0, 24))

        scroll_frame = tk.Frame(canvas, bg=C["bg"])
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        try:
            self._add_mousewheel_bindings(canvas)
        except Exception:
            pass
        
        card = tk.Frame(scroll_frame, bg=C["panel"], padx=32, pady=28)
        card.pack(fill="both", expand=True)

        self.si_vars = {}
        fields = [
            ("Name",             "text",    "Max 50 chars"),
            ("Age",              "int",     "1–120"),
            ("UHI ID",           "uhid",    "16 alphanumeric (caps)"),
            ("MDAQ ID",          "mdaqid",  "10 chars, e.g. MDTEST0001"),
            ("WD Size",          "combo",   ["S","M","L","XL","XXL","XXXL","Full"]),
            ("WD ID",            "wdid",    "10 chars, e.g. WDTEST0001"),
            ("Room Temperature", "temp",    "##.#### °C"),
            ("Body Temperature", "temp",    "##.#### °C"),
        ]

        card.columnconfigure(1, weight=1)
        for row, (label, ftype, hint) in enumerate(fields):
            tk.Label(card, text=label, bg=C["panel"], fg=C["text"],
                     font=("Segoe UI", 13), anchor="e", width=18).grid(
                         row=row, column=0, padx=(0, 16), pady=10, sticky="e")

            if ftype == "combo":
                var = tk.StringVar(value=hint[0])
                w = ttk.Combobox(card, textvariable=var, values=hint,
                                 state="readonly", width=28)
            else:
                var = tk.StringVar()
                w = self._make_history_combobox(card, var, label, width=30)
                w.configure(background=C["entry_bg"], foreground=C["text"], font=("Segoe UI", 13))
                try:
                    w.configure(insertbackground=C["accent"])
                except Exception:
                    pass
            w.grid(row=row, column=1, sticky="w", pady=10)

            hint_text = hint if isinstance(hint, str) else ""
            tk.Label(card, text=hint_text, bg=C["panel"], fg=C["subtext"],
                     font=("Segoe UI", 11)).grid(row=row, column=2, sticky="w", padx=10)

            self.si_vars[label] = (var, ftype)

        # buttons
        btn_row = tk.Frame(card, bg=C["panel"])
        btn_row.grid(row=len(fields), column=0, columnspan=3, pady=(24, 6), sticky="w")

        self._action_btn(btn_row, "Reset",    self._si_reset,    C["border"]).pack(side="left", padx=(0, 12))
        self._action_btn(btn_row, "Register", self._si_register, C["btn_bg"]).pack(side="left", padx=(0, 12))
        # Removed Start/Stop buttons from Subject page per UI request
        self.start_btn = None
        self.stop_btn = None

        self.si_status = tk.Label(card, text="", bg=C["panel"],
                                  fg=C["success"], font=("Segoe UI", 12))
        self.si_status.grid(row=len(fields)+1, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def _action_btn(self, parent, text, cmd, bg, state="normal"):
        C = self.C
        return tk.Button(parent, text=text, command=cmd,
                         bg=bg, fg=C["text"],
                         font=("Segoe UI", 13, "bold"),
                         relief="flat", cursor="hand2",
                         padx=18, pady=10, state=state,
                         activebackground=C["btn_hover"],
                         activeforeground=C["text"])

    def _si_reset(self):
        for label, (var, ftype) in self.si_vars.items():
            if ftype == "combo":
                var.set("S")
            else:
                var.set("")
        self.bi_registered = False
        if getattr(self, "start_btn", None):
            self.start_btn.configure(state="disabled")
        if getattr(self, "stop_btn", None):
            self.stop_btn.configure(state="disabled")
        self.si_status.configure(text="")

    def _si_validate(self) -> tuple[bool, str]:
        v = {k: var.get().strip() for k, (var, _) in self.si_vars.items()}

        if not v["Name"] or len(v["Name"]) > 50:
            return False, "Name must be 1–50 characters."
        if not v["Age"].isdigit() or not (1 <= int(v["Age"]) <= 120):
            return False, "Age must be a number between 1 and 120."
        if not re.fullmatch(r"[A-Z0-9_]{16}", v["UHI ID"]):
            return False, "UHI ID must be exactly 16 capital alphanumeric characters or underscore."
        if not re.fullmatch(r"MD[A-Z0-9]{8}", v["MDAQ ID"]):
            return False, "MDAQ ID must be 10 chars starting with 'MD' (e.g., MDTEST0001)."
        if not re.fullmatch(r"WD[A-Z0-9]{8}", v["WD ID"]):
            return False, "WD ID must be 10 chars starting with 'WD' (e.g., WDTEST0001)."

        for field in ("Room Temperature", "Body Temperature"):
            raw = v[field]
            try:
                val = float(raw)
                if not re.fullmatch(r"\d{1,2}\.\d{4}", raw):
                    raise ValueError
            except ValueError:
                return False, f"{field} must be ##.#### format."

        return True, ""

    def _si_register(self):
        if not self._ble_connected_state:
            self.si_status.configure(text="⚠ Connect to a device first.", fg=self.C["warn"])
            return
        ok, msg = self._si_validate()
        if not ok:
            self.si_status.configure(text=f"⚠ {msg}", fg=self.C["warn"])
            return
        info = {k: var.get().strip() for k, (var, _) in self.si_vars.items()}
        pkt = PacketProtocol.build_register_bi(info)
        self.ble.write(pkt, "bi_register")
        for key, value in info.items():
            self._push_history_value(key, value)

        self.si_status.configure(text="⏳ Sending to device…", fg=self.C["subtext"])
        if not BLE_AVAILABLE:
            self.after(800, lambda: self._on_bi_registered(success=True))

    def _on_bi_registered(self, success: bool):
        if success:
            self.bi_registered = True
            if getattr(self, "start_btn", None):
                self.start_btn.configure(state="normal")
            self.si_status.configure(text="✓ Registration successful. You may start screening.",
                                     fg=self.C["success"])
            #self._add_notification("BI registration successful.", level="success")
        else:
            self.si_status.configure(text="✗ Registration failed. Check connection.",
                                     fg=self.C["danger"])

    def _si_start(self):
        if not self.bi_registered or not self._ble_connected_state:
            self.si_status.configure(text="⚠ Connect and register before starting screening.", fg=self.C["warn"])
            return
        pkt = PacketProtocol.build_start_screening()
        self.ble.write(pkt, "start_screening")
        if getattr(self, "start_btn", None):
            self.start_btn.configure(state="disabled")
        if getattr(self, "stop_btn", None):
            self.stop_btn.configure(state="normal")
        self.si_status.configure(text="⏳ Screening start command sent…", fg=self.C["accent"])
        #self._add_notification("Start screening command sent.", level="info")
        self._monitor_log("Start screening command sent.")

    def _si_stop(self):
        if not self._ble_connected_state:
            self.si_status.configure(text="⚠ Device not connected.", fg=self.C["warn"])
            return
        pkt = PacketProtocol.build_stop_screening()
        self.ble.write(pkt, "stop_screening")
        if getattr(self, "stop_btn", None):
            self.stop_btn.configure(state="disabled")
        self.si_status.configure(text="⏳ Stop command sent…", fg=self.C["accent"])
        #self._add_notification("Stop screening command sent.", level="info")
        self._monitor_log("Stop screening command sent.")

    def _on_screening_started(self):
        self.si_status.configure(text="✓ Screening started.", fg=self.C["success"])
        #self._add_notification("Screening is active.", level="success")
        self._monitor_log("Screening active.")

    def _on_screening_stopped(self):
        self.si_status.configure(text="✓ Screening stopped.", fg=self.C["success"])
        if getattr(self, "start_btn", None):
            self.start_btn.configure(state="normal")
        if getattr(self, "stop_btn", None):
            self.stop_btn.configure(state="disabled")
        #self._add_notification("Screening stopped.", level="warn")
        self._monitor_log("Screening stopped.")



    # ─────────────────────────────────────────────────────────────────────────
    #  RETRIEVE DATA PAGE
    # ─────────────────────────────────────────────────────────────────────────
    def _build_screening_page(self):
        C = self.C
        f = tk.Frame(self.content, bg=C["bg"])
        self.pages["screening"] = f
        f.columnconfigure(0, weight=1)

        hdr = tk.Frame(f, bg=C["bg"])
        hdr.pack(fill="x", padx=32, pady=(20, 12))
        back_btn = tk.Button(hdr, text="← Back", command=self._go_back,
                  bg=C["border"], fg=C["accent"], font=("Segoe UI", 14, "bold"),
                  relief="flat", cursor="hand2", padx=14, pady=10,
                  activebackground=C["accent"], activeforeground=C["bg"],
                  highlightthickness=0)
        back_btn.pack(side="left")
        back_btn.bind("<Enter>", lambda e: back_btn.configure(bg=C["accent"], fg=C["bg"]))
        back_btn.bind("<Leave>", lambda e: back_btn.configure(bg=C["border"], fg=C["accent"]))
        tk.Label(hdr, text="Screening", bg=C["bg"], fg=C["text"],
                 font=("Segoe UI", 24, "bold")).pack(side="left", padx=20)

        card = tk.Frame(f, bg=C["panel"], padx=32, pady=28)
        card.pack(fill="x", padx=32, pady=(0, 24))

        tk.Label(card, text="Choose your screening action:", bg=C["panel"], fg=C["text"],
                 font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(0, 10))

        card_row = tk.Frame(card, bg=C["panel"])
        card_row.pack(fill="x", pady=(8, 0))
        card_row.columnconfigure(0, weight=1)
        card_row.columnconfigure(1, weight=1)
        card_row.columnconfigure(2, weight=1)

        self._home_card(card_row, 0,
                        icon="👨‍🦲",
                        title="Register New Subject",
                        desc=None,
                        color=C["accent"],
                        cmd=lambda: self._show_page("subject"))

        self._home_card(card_row, 1,
                        icon="⏯️",
                        title="Start Screening",
                        desc=None,
                        color=C["success"],
                        cmd=self._start_screening_page)

        self._home_card(card_row, 2,
                        icon="🔄",
                        title="Resume Screening",
                        desc=None,
                        color=C["accent2"],
                        cmd=self._resume_screening_page)

    def _start_screening_page(self):
        if not self._ble_connected_state:
            messagebox.showwarning("No device", "Please connect to a device before starting screening.")
            return

        self._enter_live_screening_mode("Starting new screening…", send_start_packet=True)

    def _resume_screening_page(self):
        if not self._ble_connected_state:
            messagebox.showwarning("No device", "Please connect to a device before resuming screening.")
            return

        self._enter_live_screening_mode("Resuming screening… Listening for incoming packets.", send_start_packet=False)

    def _enter_live_screening_mode(self, status_text: str, send_start_packet: bool):
        # Navigate to the live screening page
        self._show_page("screening_live")

        # initialize live session state
        self.screening_session_active = True
        self.screening_packets = []
        self.screening_count = 0
        if hasattr(self, "screening_live_text") and self.screening_live_text:
            self.screening_live_text.delete("1.0", "end")
        if hasattr(self, "screening_live_print_btn") and self.screening_live_print_btn:
            self.screening_live_print_btn.configure(state="disabled")
        if hasattr(self, "screening_live_status") and self.screening_live_status:
            self.screening_live_status.configure(text=f"⏳ {status_text}", fg=self.C["accent"])

        if send_start_packet:
            pkt = PacketProtocol.build_start_screening()
            self.ble.write(pkt, "start_screening")

        if not BLE_AVAILABLE:
            self._add_notification(
                "BLE is not available. Cannot start screening.",
                level="error"
            )
            return

    def _on_screening_data(self, payload: dict):
        self.screening_count += 1
        self.screening_packets.append(payload)
        date_time = payload.get("date_time", datetime.now().strftime("%d%m%y%H%M%S"))
        
        # Format timestamp
        try:
            if len(date_time) == 12 and date_time.isdigit():
                dt = datetime.strptime(date_time, "%d%m%y%H%M%S")
            else:
                dt = datetime.now()
        except Exception:
            dt = datetime.now()
        
        date_str = dt.strftime("%d/%m/%Y")
        time_str = dt.strftime("%I:%M:%S %p")
        
        target_text = None
        if hasattr(self, "screening_live_text") and self.screening_live_text:
            target_text = self.screening_live_text

        if target_text:
            # Add header on first packet
            if self.screening_count == 1:
                target_text.insert("end", f"{'Date':<10}  {'Time':<15}  {'Reading':<13}  {'Alarm':<15}  {'Channel'}\n")
                target_text.insert("end", "=" * 80 + "\n")
            
            # Add all temperature readings for this packet (format readings to 4 decimals)
            temps = payload.get("temperature_data", [])
            for idx, val in enumerate(temps):
                try:
                    reading = f"{float(val):.4f} C"
                except Exception:
                    reading = str(val)
                alarm = ""
                target_text.insert("end", f"{date_str:<10}  {time_str:<15}  {reading:<13}  {alarm:<15}  {idx+1}\n")

            # Keep the live screening session active and continue appending incoming packets
            # Update live status with current packet count
            if hasattr(self, "screening_live_status") and self.screening_live_status:
                self.screening_live_status.configure(text=f"⏳ Receiving packets: {self.screening_count}", fg=self.C["accent"]) 

    def _print_screening_data(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")],
            initialdir=self.settings["download_path"],
            title="Save Screening Data")
        if not path:
            return
        # use live widget
        if not (hasattr(self, "screening_live_text") and self.screening_live_text):
            messagebox.showwarning("No Data", "No screening data available to save.")
            return

        with open(path, "w") as f:
            f.write(self.screening_live_text.get("1.0", "end"))
        #self._add_notification(f"Screening data saved: {Path(path).name}", level="success")
        messagebox.showinfo("Saved", f"Screening data saved to:\n{path}")

    # def _simulate_screening_packet(self):
    #     if not self.screening_session_active or self.screening_count >= 10:
    #         return
    #     payload = {
    #         "temperature_data": [round(35.0 + i * 0.02 + self.screening_count * 0.01, 4) for i in range(48)],
    #         "date_time": datetime.now().strftime("%d%m%y%H%M%S"),
    #     }
    #     self._on_screening_data(payload)
    #     self.after(500, self._simulate_screening_packet)

    def _build_screening_live_page(self):
        C = self.C
        f = tk.Frame(self.content, bg=C["bg"])
        self.pages["screening_live"] = f
        f.columnconfigure(0, weight=1)

        hdr = tk.Frame(f, bg=C["bg"])
        hdr.pack(fill="x", padx=32, pady=(20, 12))
        back_btn = tk.Button(hdr, text="← Back", command=lambda: self._show_page("screening"),
                  bg=C["border"], fg=C["accent"], font=("Segoe UI", 14, "bold"),
                  relief="flat", cursor="hand2", padx=14, pady=10,
                  activebackground=C["accent"], activeforeground=C["bg"],
                  highlightthickness=0)
        back_btn.pack(side="left")
        back_btn.bind("<Enter>", lambda e: back_btn.configure(bg=C["accent"], fg=C["bg"]))
        back_btn.bind("<Leave>", lambda e: back_btn.configure(bg=C["border"], fg=C["accent"]))
        tk.Label(hdr, text="Screening — Live", bg=C["bg"], fg=C["text"],
                 font=("Segoe UI", 24, "bold")).pack(side="left", padx=20)

        card = tk.Frame(f, bg=C["panel"], padx=24, pady=20)
        card.pack(fill="both", expand=True, padx=32, pady=(0, 24))
        tk.Label(card, text="Live Temperature Stream", bg=C["panel"], fg=C["accent"],
                 font=("Segoe UI", 15, "bold")).pack(anchor="w", pady=(0, 12))

        self.screening_live_text = scrolledtext.ScrolledText(card,
            bg="#0A0E14", fg=C["accent2"], font=("Courier", 12), height=20, wrap="none")
        self.screening_live_text.pack(fill="both", expand=True)
        self.screening_live_text.bind("<Key>", lambda e: "break")
        self.screening_live_text.bind("<Control-v>", lambda e: "break")
        self.screening_live_text.bind("<Control-x>", lambda e: "break")
        try:
            self._add_mousewheel_bindings(self.screening_live_text, is_text=True)
        except Exception:
            pass

        bar = tk.Frame(card, bg=C["panel"])
        bar.pack(fill="x", pady=(12, 0))
        # self.screening_live_print_btn = tk.Button(bar, text="Save Live Data",
        #                                           command=self._print_screening_data,
        #                                           bg=C["btn_bg"], fg=C["text"],
        #                                           font=("Segoe UI", 12, "bold"), relief="flat",
        #                                           cursor="hand2", padx=18, pady=10, state="disabled")
        # self.screening_live_print_btn.pack(side="left")

        self.screening_live_status = tk.Label(card, text="", bg=C["panel"], fg=C["accent"], font=("Segoe UI", 12))
        self.screening_live_status.pack(anchor="w", pady=(8, 0))

    # ─────────────────────────────────────────────────────────────────────────
    #  RETRIEVE OPTIONS PAGE (Choose between get patient info or read data)
    # ─────────────────────────────────────────────────────────────────────────
    def _build_retrieve_options_page(self):
        C = self.C
        f = tk.Frame(self.content, bg=C["bg"])
        self.pages["retrieve_options"] = f
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(2, weight=1)

        hdr = tk.Frame(f, bg=C["bg"])
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew", padx=32, pady=(20, 12))
        back_btn = tk.Button(hdr, text="← Back", command=self._go_back,
                  bg=C["border"], fg=C["accent"], font=("Segoe UI", 14, "bold"),
                  relief="flat", cursor="hand2", padx=14, pady=10,
                  activebackground=C["accent"], activeforeground=C["bg"],
                  highlightthickness=0)
        back_btn.pack(side="left")
        back_btn.bind("<Enter>", lambda e: back_btn.configure(bg=C["accent"], fg=C["bg"]))
        back_btn.bind("<Leave>", lambda e: back_btn.configure(bg=C["border"], fg=C["accent"]))
        tk.Label(hdr, text="Retrieve Data", bg=C["bg"], fg=C["text"],
                 font=("Segoe UI", 28, "bold")).pack(side="left", padx=20)

        tk.Label(f, text="Select what to retrieve", bg=C["bg"], fg=C["subtext"],
                 font=("Segoe UI", 15)).grid(
                     row=1, column=0, columnspan=2, sticky="w", padx=32, pady=(0, 28))

        card_row = tk.Frame(f, bg=C["bg"])
        card_row.grid(row=2, column=0, columnspan=2, padx=32, sticky="nsew")
        card_row.columnconfigure(0, weight=1)
        card_row.columnconfigure(1, weight=1)

        self._home_card(card_row, 0,
                        icon="👤",
                        title="Get Patient Information",
                        desc="",
                        color=C["accent"],
                        cmd=self._go_to_patient_info_page)

        self._home_card(card_row, 1,
                        icon="📊",
                        title="Retrieve Temperature Data",
                        desc="",
                        color=C["accent2"],
                        cmd=lambda: self._show_page("retrieve"))

    def _go_to_patient_info_page(self):
        if not self._ble_connected_state:
            messagebox.showwarning("No device", "Please connect to a device first.")
            return
        # show patient-info page where the UHI is entered
        self.patient_request_uhid = None
        self._show_page("patient_info")
        # focus the UHI entry if available
        if getattr(self, "patient_uhid_entry", None):
            try:
                self.patient_uhid_entry.focus_set()
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    #  GET PATIENT INFORMATION PAGE
    # ─────────────────────────────────────────────────────────────────────────
    def _build_patient_info_page(self):
        C = self.C
        f = tk.Frame(self.content, bg=C["bg"])
        self.pages["patient_info"] = f

        # ---------------- Scrollable Area ----------------
        canvas = tk.Canvas(f, bg=C["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(f, orient="vertical", command=canvas.yview)

        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        scroll_frame = tk.Frame(canvas, bg=C["bg"])

        canvas_window = canvas.create_window(
            (0, 0),
            window=scroll_frame,
            anchor="nw"
        )

        # Update scroll region
        def on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        scroll_frame.bind("<Configure>", on_frame_configure)

        # Make frame same width as canvas
        def on_canvas_configure(event):
            canvas.itemconfigure(canvas_window, width=event.width)

        canvas.bind("<Configure>", on_canvas_configure)

        self._add_mousewheel_bindings(canvas)

        # ---------------- Header ----------------
        hdr = tk.Frame(scroll_frame, bg=C["bg"])
        hdr.pack(fill="x", padx=32, pady=(20, 12))
        back_btn = tk.Button(hdr, text="← Back", command=self._go_back,
                  bg=C["border"], fg=C["accent"], font=("Segoe UI", 14, "bold"),
                  relief="flat", cursor="hand2", padx=14, pady=10,
                  activebackground=C["accent"], activeforeground=C["bg"],
                  highlightthickness=0)
        back_btn.pack(side="left")
        back_btn.bind("<Enter>", lambda e: back_btn.configure(bg=C["accent"], fg=C["bg"]))
        back_btn.bind("<Leave>", lambda e: back_btn.configure(bg=C["border"], fg=C["accent"]))
        tk.Label(hdr, text="Get Patient Information", bg=C["bg"], fg=C["text"],
                 font=("Segoe UI", 24, "bold")).pack(side="left", padx=20)
        

        card = tk.Frame(scroll_frame, bg=C["panel"], padx=32, pady=28)
        card.pack(fill="both", expand=True, padx=32, pady=(0, 24))

        # UHI input row (enter UHI on this page)
        uhi_row = tk.Frame(card, bg=C["panel"]) 
        uhi_row.pack(fill="x", pady=(0,12))
        tk.Label(uhi_row, text="UHI ID:", bg=C["panel"], fg=C["subtext"],
                 width=20, anchor="w", font=("Segoe UI", 13)).pack(side="left", padx=(0, 12))
        self.patient_uhid_entry_var = tk.StringVar()
        self.patient_uhid_entry = self._make_history_combobox(
            uhi_row,
            self.patient_uhid_entry_var,
            "UHI ID",
            width=30,
            state="normal")
        self.patient_uhid_entry.pack(side="left", padx=(0,12))
        tk.Label(uhi_row, text="(16 capital alphanumeric or underscore)", bg=C["panel"], fg=C["subtext"], font=("Segoe UI", 11)).pack(side="left")

        # Patient Info Display
        self.patient_info_display = {}
        patient_keys = [
            "Name", "Age", "UHI ID", "Room Temperature", "Body Temperature", "Date & Time"
        ]
        for key in patient_keys:
            row = tk.Frame(card, bg=C["panel"])
            row.pack(fill="x", pady=6)
            tk.Label(row, text=f"{key}:", bg=C["panel"], fg=C["subtext"],
                     width=20, anchor="w", font=("Segoe UI", 13)).pack(side="left", padx=(0, 12))
            lbl = tk.Label(row, text="—", bg=C["panel"], fg=C["text"],
                           font=("Segoe UI", 13, "bold"))
            lbl.pack(side="left")
            self.patient_info_display[key] = lbl

        self.patient_retrieve_status = tk.Label(card, text="", bg=C["panel"],
                                       fg=C["subtext"], font=("Segoe UI", 12))
        self.patient_retrieve_status.pack(anchor="w", pady=(16, 0))

        btn_row = tk.Frame(card, bg=C["panel"])
        btn_row.pack(anchor="w", pady=(20, 0))

        tk.Button(btn_row, text="⤓  Retrieve Patient Data",
                  command=self._retrieve_patient_data,
                  bg=C["btn_bg"], fg=C["text"],
                  font=("Segoe UI", 12, "bold"),
                  relief="flat", cursor="hand2", padx=14, pady=8).pack(side="left")

    def _retrieve_patient_data(self):
        def _send_request(u: str):
            pkt = PacketProtocol.build_get_device_info(u)
            self.ble.write(pkt, "patient_info")
            self.patient_retrieve_status.configure(text="⏳ Retrieving patient information…", fg=self.C["subtext"])            

        # read UHI from the page entry if present
        uhid = None
        if getattr(self, "patient_uhid_entry", None):
            uhid = self.patient_uhid_entry.get().strip().upper()
            if uhid == "":
                uhid = None

        if not uhid:
            messagebox.showwarning("Missing UHI", "Please enter the UHI ID on this page before retrieving.")
            return

        if not re.fullmatch(r"[A-Z0-9_]{16}", uhid):
            messagebox.showwarning("Invalid UHI", "UHI ID must be exactly 16 capital alphanumeric characters or underscore.")
            return

        self.patient_request_uhid = uhid

        if not self._ble_connected_state:
            self.patient_retrieve_status.configure(
                text="⚠ Connect to a device first.", fg=self.C["warn"])
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        suggested = f"{uhid}_{ts}_PATIENT_INFO.txt"
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                                            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")],
                                            initialdir=self.settings["download_path"],
                                            title="Save patient information as",
                                            initialfile=suggested)
        if not path:
            self.patient_retrieve_status.configure(text="✗ Download cancelled.", fg=self.C["warn"])
            return

        self._pending_patient_info_path = path
        _send_request(uhid)

    # ─────────────────────────────────────────────────────────────────────────
    #  RETRIEVE DATA PAGE
    # ─────────────────────────────────────────────────────────────────────────
    def _build_retrieve_page(self):
        C = self.C
        f = tk.Frame(self.content, bg=C["bg"])
        self.pages["retrieve"] = f

        hdr = tk.Frame(f, bg=C["bg"])
        hdr.pack(fill="x", padx=32, pady=(20, 12))
        back_btn = tk.Button(hdr, text="← Back", command=lambda: self._show_page("home"),
                  bg=C["border"], fg=C["accent"], font=("Segoe UI", 14, "bold"),
                  relief="flat", cursor="hand2", padx=14, pady=10,
                  activebackground=C["accent"], activeforeground=C["bg"],
                  highlightthickness=0)
        back_btn.pack(side="left")
        back_btn.bind("<Enter>", lambda e: back_btn.configure(bg=C["accent"], fg=C["bg"]))
        back_btn.bind("<Leave>", lambda e: back_btn.configure(bg=C["border"], fg=C["accent"]))
        tk.Label(hdr, text="Retrieve Data", bg=C["bg"], fg=C["text"],
                 font=("Segoe UI", 24, "bold")).pack(side="left", padx=20)

        card = tk.Frame(f, bg=C["panel"], padx=32, pady=28)
        card.pack(fill="x", padx=32, pady=(0, 24))

        tk.Label(card, text="UHI ID", bg=C["panel"], fg=C["text"],
                 font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(0, 8))
        self.retrieve_uhid = tk.StringVar()
        retrieve_combo = self._make_history_combobox(card, self.retrieve_uhid, "UHI ID", width=30)
        retrieve_combo.pack(anchor="w", pady=(0, 24))

        tk.Button(card, text="⤓  Read & Retrieve Data",
                  command=self._retrieve_data,
                  bg=C["accent"], fg=C["bg"],
                  font=("Segoe UI", 14, "bold"),
                  relief="flat", cursor="hand2", padx=18, pady=12).pack(anchor="w")

        self.retrieve_status = tk.Label(card, text="", bg=C["panel"],
                                        fg=C["success"], font=("Segoe UI", 12))
        self.retrieve_status.pack(anchor="w", pady=(14, 0))

    def _retrieve_data(self):
        uhid = self.retrieve_uhid.get().strip()
        if not re.fullmatch(r"[A-Z0-9_]{16}", uhid):
            self.retrieve_status.configure(
                text="⚠ UHI ID must be exactly 16 capital alphanumeric characters or underscore.",
                fg=self.C["warn"])
            return
        if not self._ble_connected_state:
            self.retrieve_status.configure(text="⚠ Connect to a device first.", fg=self.C["warn"])
            return

        self._push_history_value("UHI ID", uhid)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        suggested = f"{uhid}_{ts}_TEMP_DOWNLOAD.txt"
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                                            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")],
                                            initialdir=self.settings["download_path"],
                                            title="Save temperature download as",
                                            initialfile=suggested)
        if not path:
            self.retrieve_status.configure(text="✗ Download cancelled.", fg=self.C["warn"])
            return

        self._pending_retrieve_uhid = uhid
        self._pending_retrieve_path = path
        self._pending_retrieve_wd_size = None
        self._pending_retrieve_waiting_for_basic_info = True
        self.collecting_retrieve = False
        self.retrieve_packets = []
        if getattr(self, "_retrieve_timer", None):
            try:
                self.after_cancel(self._retrieve_timer)
            except Exception:
                pass
            self._retrieve_timer = None

        pkt = PacketProtocol.build_initiation()
        self.ble.write(pkt, "retrieve_device_info")
        self.retrieve_status.configure(text=f"⏳ Identifying WD size for {uhid} before retrieving data…", fg=self.C["subtext"])
        if not BLE_AVAILABLE:
            self._add_notification(
                "BLE is not available. Cannot start screening.",
                level="error"
            )
            return

    def _continue_retrieve_after_basic_info(self):
        """After WD size is known, begin the actual retrieve data request."""
        self._pending_retrieve_waiting_for_basic_info = False
        self.collecting_retrieve = True
        self.retrieve_packets = []

        if not getattr(self, "_pending_retrieve_uhid", None):
            self.retrieve_status.configure(text="✗ Missing UHID; cannot retrieve data.", fg=self.C["danger"])
            return

        uhid = self._pending_retrieve_uhid
        self.retrieve_status.configure(
            text=f"⏳ Retrieving data for {uhid} (WD Size: {self._pending_retrieve_wd_size})…",
            fg=self.C["subtext"])
        pkt = PacketProtocol.build_retrieve_data(uhid)
        self.ble.write(pkt, "retrieve_data")

    def _save_channel_mapping(self):
        saved_map = {}
        for size, vars_by_channel in self.channel_map_vars.items():
            selected_channels = [idx for idx, var in vars_by_channel.items() if var.get() == 1]
            if not selected_channels:
                selected_channels = WD_CHANNELS.get(size, [])
            saved_map[size] = sorted(selected_channels)

        self.settings["channel_map"] = saved_map
        save_settings(self.settings)
        self.channel_map_status.configure(text="✓ Channel mapping saved.")
        self.after(3000, lambda: self.channel_map_status.configure(text=""))

    def _on_retrieve_response(self, payload):
        if isinstance(payload, dict) and payload.get("type") == "retrieve_data":
            uhid = self.retrieve_uhid.get().strip()
            self.retrieve_status.configure(text=f"✓ Data received for {uhid}", fg=self.C["success"])
            #self._add_notification(f"Temperature data retrieved for {uhid}.", level="success")
            temperature_data = payload.get("temperature_data", [])
            channels = {str(i): temperature_data[i] for i in range(len(temperature_data))}
            self._generate_text_file({
                "MDAQ ID": payload.get("device_id", "MD.UNKNOWN"),
                "WD ID": payload.get("wd_id", "WD.UNKNOWN"),
                "WD Size": payload.get("wd_size", "Full"),
                "UHID": uhid,
                "Data validity": payload.get("validation_status", 1),
                "date_time": payload.get("date_time", ""),
                "Module Model": payload.get("module_model", "2564"),
                "Module Serial": payload.get("module_serial", "B56626"),
                "channels": channels,
            })
        else:
            self.retrieve_status.configure(text="✗ Failed to parse retrieved data.", fg=self.C["danger"])

    def _handle_retrieve_packet(self, payload: dict):
        """Collect retrieve packets while in collecting mode and schedule finalization."""
        if not isinstance(payload, dict):
            return
        # append packet
        self.retrieve_packets.append(payload)
        count = len(self.retrieve_packets)
        if hasattr(self, "retrieve_status") and self.retrieve_status:
            self.retrieve_status.configure(text=f"⏳ Collected {count} packet(s)…", fg=self.C["subtext"])
        # reset idle timer (2s)
        if getattr(self, "_retrieve_timer", None):
            try:
                self.after_cancel(self._retrieve_timer)
            except Exception:
                pass
        self._retrieve_timer = self.after(2000, self._finalize_retrieve_collection)

    def _finalize_retrieve_collection(self):
        """Called when retrieve transmission is idle for 2s — generate combined file with all channels."""
        self.collecting_retrieve = False
        self._retrieve_timer = None
        packets = self.retrieve_packets or []
        if not packets:
            self.retrieve_status.configure(text="✗ No packets received.", fg=self.C["danger"])
            return
        first = packets[0]
        uhid = self.retrieve_uhid.get().strip() or "UNKNOWN"
        module_model = first.get("module_model", "2564")
        module_serial = first.get("module_serial", "B56626")
        wd_size = getattr(self, "_pending_retrieve_wd_size", None) or "Full"
        selected_channels = self.settings.get("channel_map", {}).get(wd_size)
        if not isinstance(selected_channels, list) or not selected_channels:
            selected_channels = WD_CHANNELS.get(wd_size, WD_CHANNELS["Full"])

        # use user-selected path if provided
        fpath = None
        fname = None
        if getattr(self, "_pending_retrieve_path", None):
            fpath = Path(self._pending_retrieve_path)
            fname = fpath.name
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"{uhid}_{ts}_TEMP_DOWNLOAD.txt"
            outdir = Path(self.settings["download_path"])
            outdir.mkdir(parents=True, exist_ok=True)
            fpath = outdir / fname

        # Header for temperature download (include timestamp and device info)
        header = f"Basic Info downloaded by MDAQ v1.2.0 Desktop GUI on {datetime.now().strftime('%d/%m/%Y %I:%M:%S %p')}"
        sep = "=" * max(80, len(header))
        # Prefer explicit values from the packet; fallback to last-known device info
        device_id = first.get('device_id') or self.device_info.get('device_id') or 'MD.UNKNOWN'
        wd_id = first.get('wd_id') or self.device_info.get('wd_id') or 'WD.UNKNOWN'
        wd_size = getattr(self, '_pending_retrieve_wd_size', None) or first.get('wd_size') or self.device_info.get('wd_size') or 'Full'
        validation = 'Pass' if (first.get('validation_status') or first.get('validation') or 0) == 1 else 'Fail'

        lines = [header, sep, "",
             f"Device ID\t: {device_id}",
             f"WD ID\t\t: {wd_id}",
             f"WD Size\t\t: {wd_size}",
             f"Data Validation : {validation}",
             "", sep, ""]

        # Table header
        lines += [f"{'Date':<10}\t{'Time':<11}\t{'Reading':<13}\t{'Alarm':<8}\t{'Channel'}", sep]

        for pkt in packets:
            date_time = pkt.get("date_time", "")
            date_str = ""
            time_str = ""
            if isinstance(date_time, str) and len(date_time) >= 12 and date_time[:12].isdigit():
                try:
                    dt = datetime.strptime(date_time[:12], "%d%m%y%H%M%S")
                    date_str = dt.strftime("%d/%m/%Y")
                    time_str = dt.strftime("%I:%M:%S %p")
                except Exception:
                    date_str = ""
                    time_str = ""
            temps = pkt.get("temperature_data", [])
            for ch in selected_channels:
                if ch < len(temps):
                    val = temps[ch]
                    try:
                        reading = f"{float(val):.4f} C"
                    except Exception:
                        reading = str(val)
                    lines.append(f"{date_str:<10}\t{time_str:<11}\t{reading:<13}\t{'':<8}\t{ch+1}")

        lines += ["", "=" * 80]

        with open(fpath, "w") as f:
            f.write("\n".join(lines))

        # clear pending path
        if getattr(self, "_pending_retrieve_path", None):
            try:
                del self._pending_retrieve_path
            except Exception:
                pass

        self.retrieve_status.configure(text=f"✓ File saved: {fname}", fg=self.C["success"])
        #self._add_notification(f"Temperature file generated: {fname}", level="success")

    def _generate_text_file(self, bi: dict):
        wd_size = bi.get("WD Size", "Full")
        channels_map = self.settings.get("channel_map", {}).get(wd_size)
        if not isinstance(channels_map, list) or not channels_map:
            channels_map = WD_CHANNELS.get(wd_size, WD_CHANNELS["Full"])

        module_model = bi.get("Module Model", "2564")
        module_serial = bi.get("Module Serial", "B56626")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        uhid = bi.get("UHID", "UNKNOWN")
        outdir = Path(self.settings["download_path"])
        outdir.mkdir(parents=True, exist_ok=True)

        # If `channels` is present, treat this as a temperature download
        if isinstance(bi.get('channels'), dict):
            fname = f"{uhid}_{ts}_TEMP_DOWNLOAD.txt"
        else:
            fname = f"{uhid}_{ts}_BI.txt"
        fpath = outdir / fname

        raw_channels = bi.get("channels", {})
        date_time = bi.get("date_time", "")
        try:
            if len(date_time) == 12 and date_time.isdigit():
                dt = datetime.strptime(date_time, "%d%m%y%H%M%S")
            else:
                dt = datetime.now()
        except Exception:
            dt = datetime.now()

        def format_record(index: int, channel: int, value):
            record_dt = dt + timedelta(seconds=index * 3)
            date_str = record_dt.strftime("%d/%m/%Y")
            time_str = record_dt.strftime("%I:%M:%S %p")
            try:
                reading = f"{float(value):.4f} C"
            except Exception:
                reading = str(value)
            return f"{date_str}\t{time_str}\t{reading}\t\t{channel}"


        # Build header + device info for temperature download
        header = f"Basic Info downloaded by MDAQ v1.2.0 Desktop GUI on {datetime.now().strftime('%d/%m/%Y %I:%M:%S %p')}"
        sep = "=" * max(80, len(header))
        device_id = bi.get('MDAQ ID') or bi.get('device_id') or self.device_info.get('device_id') or 'MD.UNKNOWN'
        wd_id = bi.get('WD ID') or bi.get('wd_id') or self.device_info.get('wd_id') or 'WD.UNKNOWN'
        wd_size = bi.get('WD Size') or bi.get('wd_size') or self.device_info.get('wd_size') or 'Full'
        def _parse_validation_field(v):
            try:
                # bytes like b'1' or b'0'
                if isinstance(v, (bytes, bytearray)):
                    if len(v) == 1:
                        # ASCII digit -> convert to 0/1
                        return v[0] - 48 if 48 <= v[0] <= 57 else int(v[0])
                    try:
                        return int(v.decode(errors='ignore'))
                    except Exception:
                        return 0
                # string like '1' or '0'
                if isinstance(v, str):
                    return int(v) if v.isdigit() else 0
                # integer: could be actual 1/0 or ASCII code 49/48
                if isinstance(v, int):
                    if v in (48, 49):
                        return v - 48
                    return v
            except Exception:
                return 0
            return 0

        raw_val = bi.get('Data validity') if 'Data validity' in bi else bi.get('validation_status')
        validation = 'Pass' if _parse_validation_field(raw_val) == 1 else 'Fail'

        lines = [header, sep, "", f"Device ID\t: {device_id}", f"WD ID\t\t: {wd_id}", f"WD Size\t\t: {wd_size}", f"Data Validation : {validation}    // 1 = Pass, 0 = Fail (byte 210, Req type 5)", "", sep, ""]
        lines += ["Date\tTime\tReading\tAlarm\tChannel", sep]

        for idx, ch in enumerate(channels_map):
            value = raw_channels.get(str(ch), 0.0)
            # channel display numbers are 1-based
            lines.append(format_record(idx, ch+1, value))

        lines += ["", sep]

        with open(fpath, "w") as f:
            f.write("\n".join(lines))

        self.retrieve_status.configure(
            text=f"✓ File saved: {fname}", fg=self.C["success"])
        #self._add_notification(f"Data file generated: {fname}", level="success")

    # ─────────────────────────────────────────────────────────────────────────
    #  NOTIFICATIONS PAGE
    # ─────────────────────────────────────────────────────────────────────────
    def _build_notifications_page(self):
        C = self.C
        f = tk.Frame(self.content, bg=C["bg"])
        self.pages["notifications"] = f
        f.columnconfigure(0, weight=1)
        hdr = tk.Frame(f, bg=C["bg"])
        hdr.pack(fill="x", padx=32, pady=(20, 12))
        back_btn = tk.Button(hdr, text="← Back", command=self._go_back,
                  bg=C["border"], fg=C["accent"], font=("Segoe UI", 14, "bold"),
                  relief="flat", cursor="hand2", padx=14, pady=10,
                  activebackground=C["accent"], activeforeground=C["bg"],
                  highlightthickness=0)
        back_btn.pack(side="left")
        back_btn.bind("<Enter>", lambda e: back_btn.configure(bg=C["accent"], fg=C["bg"]))
        back_btn.bind("<Leave>", lambda e: back_btn.configure(bg=C["border"], fg=C["accent"]))
        tk.Label(hdr, text="Notifications", bg=C["bg"], fg=C["text"],
                 font=("Segoe UI", 24, "bold")).pack(side="left", padx=20)
        tk.Button(hdr, text="CLEAR", command=self._clear_notifications,
                  bg=C["border"], fg=C["subtext"], font=("Segoe UI", 12),
                  relief="flat", padx=12, pady=8).pack(side="right")

        # make notifications scrollable with mousewheel
        canvas = tk.Canvas(f, bg=C["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(f, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.notif_container = tk.Frame(canvas, bg=C["bg"]) 
        canvas.create_window((0, 0), window=self.notif_container, anchor="nw")
        self.notif_container.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        try:
            self._add_mousewheel_bindings(canvas)
        except Exception:
            pass

    def _refresh_notifications(self):
        for w in self.notif_container.winfo_children():
            w.destroy()
        C = self.C
        if not self.notifications:
            tk.Label(self.notif_container, text="No notifications yet.",
                     bg=C["bg"], fg=C["subtext"],
                     font=("Segoe UI", 14)).pack(pady=60)
            return
        level_colors = {"success": C["success"], "info": C["accent"],
                        "warn": C["warn"], "error": C["danger"]}
        for n in reversed(self.notifications):
            row = tk.Frame(self.notif_container, bg=C["panel"], padx=18, pady=14)
            row.pack(fill="x", pady=4)
            color = level_colors.get(n["level"], C["text"])
            tk.Label(row, text="●", bg=C["panel"], fg=color,
                     font=("Segoe UI", 12)).pack(side="left")
            tk.Label(row, text=n["msg"], bg=C["panel"], fg=C["text"],
                     font=("Segoe UI", 13)).pack(side="left", padx=12)
            tk.Label(row, text=n["time"], bg=C["panel"], fg=C["subtext"],
                     font=("Segoe UI", 11)).pack(side="right")

    def _clear_notifications(self):
        self.notifications.clear()
        self.unseen_notif_count = 0
        self._refresh_notif_badge()
        if getattr(self, "notif_container", None):
            self._refresh_notifications()

    def _add_notification(self, msg: str, level: str = "info"):
        self.notifications.append({
            "msg":   msg,
            "level": level,
            "time":  datetime.now().strftime("%H:%M:%S"),
        })
        self.unseen_notif_count += 1
        self._refresh_notif_badge()

    def _refresh_notif_badge(self):
        if self.notif_nav_btn:
            base = "🔔  Notifications"
            if self.unseen_notif_count:
                self.notif_nav_btn.configure(
                    text=f"{base}  [{self.unseen_notif_count}]",
                    fg=self.C["warn"])
            else:
                self.notif_nav_btn.configure(text=base, fg=self.C["text"])

    # ─────────────────────────────────────────────────────────────────────────
    #  SETTINGS PAGE
    # ─────────────────────────────────────────────────────────────────────────
    def _build_settings_page(self):
        C = self.C
        f = tk.Frame(self.content, bg=C["bg"])
        self.pages["settings"] = f

        hdr = tk.Frame(f, bg=C["bg"])
        hdr.pack(fill="x", padx=32, pady=(20, 12))
        back_btn = tk.Button(hdr, text="← Back", command=lambda: self._show_page("home"),
                  bg=C["border"], fg=C["accent"], font=("Segoe UI", 14, "bold"),
                  relief="flat", cursor="hand2", padx=14, pady=10,
                  activebackground=C["accent"], activeforeground=C["bg"],
                  highlightthickness=0)
        back_btn.pack(side="left")
        back_btn.bind("<Enter>", lambda e: back_btn.configure(bg=C["accent"], fg=C["bg"]))
        back_btn.bind("<Leave>", lambda e: back_btn.configure(bg=C["border"], fg=C["accent"]))
        tk.Label(hdr, text="Settings", bg=C["bg"], fg=C["text"],
                 font=("Segoe UI", 24, "bold")).pack(side="left", padx=20)

        canvas = tk.Canvas(f, bg=C["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(f, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        scroll_frame = tk.Frame(canvas, bg=C["bg"])
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        # enable mousewheel/touchpad scrolling when hovering the settings area
        try:
            self._add_mousewheel_bindings(canvas)
        except Exception:
            pass

        # tk.Label(scroll_frame, text="Settings", bg=C["bg"], fg=C["text"],
        #          font=("Segoe UI", 24, "bold")).pack(anchor="w", padx=32, pady=(24, 8))

        # ── device info card ─────────────────────────────────────────────────
        self._settings_card(scroll_frame, "Device Information", self._build_device_info_card)
        # ── file management card ─────────────────────────────────────────────
        self._settings_card(scroll_frame, "File Management", self._build_file_mgmt_card)
        # ── channel mapping card ─────────────────────────────────────────────
        self._settings_card(scroll_frame, "Channel Mapping", self._build_channel_mapping_card)

    def _settings_card(self, parent, title, builder):
        C = self.C
        frame = tk.Frame(parent, bg=C["panel"], padx=24, pady=20)
        frame.pack(fill="x", padx=32, pady=(0, 18))
        tk.Label(frame, text=title, bg=C["panel"], fg=C["accent"],
                 font=("Segoe UI", 15, "bold")).pack(anchor="w", pady=(0, 14))
        builder(frame)

    def _build_device_info_card(self, parent):
        C = self.C
        
        device_info_frame = tk.Frame(parent, bg=C["panel"])
        device_info_frame.pack(fill="x", pady=(0, 20))

        self.device_info_labels = {}
        device_keys = ["Device ID", "WD Size", "WD ID", "Battery(%)"]
        # add Error Code label under Battery%
        device_keys.append("Error Code")
        for key in device_keys:
            row = tk.Frame(device_info_frame, bg=C["panel"])
            row.pack(fill="x", pady=4)
            tk.Label(row, text=f"{key}:", bg=C["panel"], fg=C["subtext"],
                     width=18, anchor="w", font=("Segoe UI", 13)).pack(side="left", padx=(0, 12))
            lbl = tk.Label(row, text="—", bg=C["panel"], fg=C["text"],
                           font=("Segoe UI", 13, "bold"))
            lbl.pack(side="left")
            self.device_info_labels[key] = lbl

        self.device_info_status = tk.Label(parent, text="", bg=C["panel"],
                                       fg=C["subtext"], font=("Segoe UI", 12))
        self.device_info_status.pack(anchor="w", pady=(10, 0))

        btn_row = tk.Frame(parent, bg=C["panel"])
        btn_row.pack(anchor="w", pady=(16, 0))

        tk.Button(btn_row, text="Retrieve Device Info",
                  command=self._retrieve_device_info,
                  bg=C["btn_bg"], fg=C["text"],
                  font=("Segoe UI", 12, "bold"),
                  relief="flat", cursor="hand2", padx=14, pady=8).pack(side="left", padx=(0, 12))

        tk.Button(btn_row, text="🔊 Find Device",
                  command=self._find_device,
                  bg=C["border"], fg=C["text"],
                  font=("Segoe UI", 12),
                  relief="flat", cursor="hand2", padx=14, pady=8).pack(side="left")

    def _build_file_mgmt_card(self, parent):
        C = self.C
        path_row = tk.Frame(parent, bg=C["panel"])
        path_row.pack(fill="x", pady=(0, 12))

        tk.Label(path_row, text="Download path:", bg=C["panel"],
                 fg=C["subtext"], font=("Segoe UI", 13)).pack(side="left")

        self.path_var = tk.StringVar(value=self.settings["download_path"])
        tk.Label(path_row, textvariable=self.path_var, bg=C["panel"],
                 fg=C["text"], font=("Segoe UI", 12)).pack(side="left", padx=12)

        btn_row = tk.Frame(parent, bg=C["panel"])
        btn_row.pack(anchor="w")

        tk.Button(btn_row, text="Change Path",
                  command=self._change_download_path,
                  bg=C["btn_bg"], fg=C["text"],
                  font=("Segoe UI", 12, "bold"),
                  relief="flat", cursor="hand2", padx=14, pady=8).pack(side="left", padx=(0, 12))

        tk.Button(btn_row, text="📂 Open Folder",
                  command=self._open_download_folder,
                  bg=C["border"], fg=C["text"],
                  font=("Segoe UI", 12),
                  relief="flat", cursor="hand2", padx=14, pady=8).pack(side="left")

    def _build_channel_mapping_card(self, parent):
        C = self.C
        tk.Label(parent, text="Configure which sensor channels are used for each WD size.",
                 bg=C["panel"], fg=C["subtext"], font=("Segoe UI", 12), wraplength=640, justify="left").pack(anchor="w", pady=(0, 8))

        btn_row = tk.Frame(parent, bg=C["panel"])
        btn_row.pack(anchor="w")

        tk.Button(btn_row, text="Open Channel Mapping",
                  command=self._show_channel_mapping_page,
                  bg=C["btn_bg"], fg=C["text"],
                  font=("Segoe UI", 12, "bold"),
                  relief="flat", cursor="hand2", padx=14, pady=8).pack(side="left")

    def _build_about_page(self):
        C = self.C
        f = tk.Frame(self.content, bg=C["bg"])
        self.pages["about"] = f
        f.columnconfigure(0, weight=1)

        hdr = tk.Frame(f, bg=C["bg"])
        hdr.pack(fill="x", padx=32, pady=(20, 12))
        back_btn = tk.Button(hdr, text="← Back", command=self._go_back,
                  bg=C["border"], fg=C["accent"], font=("Segoe UI", 14, "bold"),
                  relief="flat", cursor="hand2", padx=14, pady=10,
                  activebackground=C["accent"], activeforeground=C["bg"],
                  highlightthickness=0)
        back_btn.pack(side="left")
        back_btn.bind("<Enter>", lambda e: back_btn.configure(bg=C["accent"], fg=C["bg"]))
        back_btn.bind("<Leave>", lambda e: back_btn.configure(bg=C["border"], fg=C["accent"]))
        tk.Label(hdr, text="About", bg=C["bg"], fg=C["text"],
                 font=("Segoe UI", 24, "bold")).pack(side="left", padx=20)

        card = tk.Frame(f, bg=C["panel"], padx=32, pady=28)
        card.pack(fill="both", expand=True, padx=32, pady=(0, 24))

        about_text = (
            "This application is designed for use with the Multichannel Data Acquisition (MDAQ) System developed by C-MET (Centre for Materials for Electronics Technology).\n\n"
            "© 2026 C-MET. All rights reserved.\n\n"
            "This software and related information are proprietary to C-MET and intended solely for authorized use. Unauthorized copying, modification, distribution, disclosure, or use is prohibited without prior written authorization from the Director General, C-MET, except as permitted by applicable law or agreement."
        )

        tk.Label(card, text=about_text, bg=C["panel"], fg=C["text"],
                 font=("Segoe UI", 13), justify="left", anchor="nw",
                 wraplength=760).pack(fill="both", expand=True)

    def _retrieve_device_info(self):
        """Retrieve device info using the initiation packet (Req_Type 0)"""
        if not self._ble_connected_state:
            messagebox.showwarning("No device", "Please connect to a device before retrieving device info.")
            return
        pkt = PacketProtocol.build_initiation()
        self.ble.write(pkt, "retrieve_device_info")
        self.device_info_status.configure(text="⏳ Retrieving device info…")

    def _show_channel_mapping_page(self):
        self._show_page("channel_mapping")

    def _build_channel_mapping_page(self):
        C = self.C
        f = tk.Frame(self.content, bg=C["bg"])
        self.pages["channel_mapping"] = f
        f.columnconfigure(0, weight=1)

        hdr = tk.Frame(f, bg=C["bg"])
        hdr.pack(fill="x", padx=32, pady=(20, 12))
        back_btn = tk.Button(hdr, text="← Back", command=lambda: self._show_page("settings"),
                  bg=C["border"], fg=C["accent"], font=("Segoe UI", 14, "bold"),
                  relief="flat", cursor="hand2", padx=14, pady=10,
                  activebackground=C["accent"], activeforeground=C["bg"],
                  highlightthickness=0)
        back_btn.pack(side="left")
        back_btn.bind("<Enter>", lambda e: back_btn.configure(bg=C["accent"], fg=C["bg"]))
        back_btn.bind("<Leave>", lambda e: back_btn.configure(bg=C["border"], fg=C["accent"]))
        tk.Label(hdr, text="Channel Mapping", bg=C["bg"], fg=C["text"],
                 font=("Segoe UI", 24, "bold")).pack(side="left", padx=20)

        container = tk.Frame(f, bg=C["bg"])
        container.pack(fill="both", expand=True, padx=32, pady=(0, 16))

        canvas = tk.Canvas(container, bg=C["bg"], highlightthickness=0)
        v_scroll = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        h_scroll = ttk.Scrollbar(container, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        v_scroll.pack(side="right", fill="y")
        h_scroll.pack(side="bottom", fill="x")

        grid_frame = tk.Frame(canvas, bg=C["bg"])
        window_id = canvas.create_window((0, 0), window=grid_frame, anchor="nw")
        grid_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(window_id, width=max(canvas.winfo_width(), grid_frame.winfo_reqwidth())))
        try:
            self._add_mousewheel_bindings(canvas)
        except Exception:
            pass

        sizes = list(WD_CHANNELS.keys())
        tk.Label(grid_frame, text="Channel", bg=C["bg"], fg=C["text"],
                 font=("Segoe UI", 12, "bold"), width=10).grid(row=0, column=0, padx=8, pady=4)
        for col, size in enumerate(sizes, start=1):
            tk.Label(grid_frame, text=size, bg=C["bg"], fg=C["accent"],
                     font=("Segoe UI", 12, "bold"), width=12).grid(row=0, column=col, padx=8, pady=4)

        self.channel_map_vars = {size: {} for size in sizes}
        for row in range(48):
            tk.Label(grid_frame, text=str(row + 1), bg=C["bg"], fg=C["text"],
                     font=("Segoe UI", 12), width=10).grid(row=row+1, column=0, padx=8, pady=2)
            for col, size in enumerate(sizes, start=1):
                val = 1 if row in self.settings["channel_map"].get(size, []) else 0
                var = tk.IntVar(value=val)
                cb = tk.Checkbutton(grid_frame, variable=var,
                                    bg=C["bg"], fg=C["text"], activebackground=C["bg"],
                                    activeforeground=C["text"], selectcolor=C["bg"],
                                    highlightthickness=1, highlightbackground=C["accent"],
                                    highlightcolor=C["accent"], bd=0, relief="flat",
                                    font=("Segoe UI", 14), pady=4, padx=4,
                                    onvalue=1, offvalue=0)
                cb.grid(row=row+1, column=col, padx=6, pady=4)
                self.channel_map_vars[size][row] = var

        note = tk.Label(f, text="Select the active sensor channels for each WD size. Save to preserve your mapping.",
                        bg=C["bg"], fg=C["subtext"], font=("Segoe UI", 12), wraplength=900, justify="left")
        note.pack(anchor="w", padx=32, pady=(0, 8))

        save_row = tk.Frame(f, bg=C["bg"])
        save_row.pack(fill="x", padx=32, pady=(0, 16))
        tk.Button(save_row, text="Save Channel Mapping",
                  command=self._save_channel_mapping,
                  bg=C["btn_bg"], fg=C["text"],
                  font=("Segoe UI", 12, "bold"),
                  relief="flat", cursor="hand2", padx=14, pady=10).pack(side="left")

        self.channel_map_status = tk.Label(save_row, text="", bg=C["bg"], fg=C["success"],
                                           font=("Segoe UI", 12))
        self.channel_map_status.pack(side="left", padx=(12, 0))

    def _apply_device_info(self, payload: dict):
        """Apply patient info response (126-byte packet from Req_Type 4)"""
        if not payload:
            return
        
        # Parse and format date_time
        date_time_raw = payload.get("date_time", "—")
        date_time_formatted = date_time_raw
        if date_time_raw and date_time_raw != "—":
            try:
                # Expected format: DDMMYYHHMMSS (12 chars)
                if len(date_time_raw) >= 12 and date_time_raw[:12].isdigit():
                    dt = datetime.strptime(date_time_raw[:12], "%d%m%y%H%M%S")
                    date_time_formatted = dt.strftime("%d/%m/%Y\t%I:%M:%S %p")
                else:
                    date_time_formatted = date_time_raw
            except Exception:
                date_time_formatted = date_time_raw
        
        patient_mapping = {
            "Name": payload.get("name", "—"),
            "Age": payload.get("subject_age", "—"),
            "UHI ID": payload.get("uhi_id", "—"),
            "Room Temperature": payload.get("room_temperature", "—"),
            "Body Temperature": payload.get("body_temperature", "—"),
            "Date & Time": date_time_formatted,
        }
        for k, v in patient_mapping.items():
            if k in self.patient_info_display:
                self.patient_info_display[k].configure(text=v)
        self.patient_retrieve_status.configure(text="✓ Patient information retrieved.")

        # store device info for later use (used when building temp download headers)
        try:
            self.device_info.setdefault('device_id', payload.get('device_id'))
            self.device_info.setdefault('wd_id', payload.get('wd_id'))
            self.device_info.setdefault('wd_size', payload.get('wd_size'))
        except Exception:
            pass

        if getattr(self, "_pending_patient_info_path", None):
            self._save_patient_info_file(payload)

    def _save_patient_info_file(self, payload: dict):
        uhid = payload.get("uhi_id", self.patient_request_uhid or "UNKNOWN")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if getattr(self, "_pending_patient_info_path", None):
            fpath = Path(self._pending_patient_info_path)
            fname = fpath.name
        else:
            outdir = Path(self.settings["download_path"])
            outdir.mkdir(parents=True, exist_ok=True)
            fname = f"{uhid}_{ts}_PATIENT_INFO.txt"
            fpath = outdir / fname

        date_time_raw = payload.get('date_time', '—')
        date_time_formatted = date_time_raw
        if date_time_raw and date_time_raw != "—":
            try:
                if len(date_time_raw) >= 12 and date_time_raw[:12].isdigit():
                    dt = datetime.strptime(date_time_raw[:12], "%d%m%y%H%M%S")
                    date_time_formatted = dt.strftime("%d/%m/%Y\t%I:%M:%S %p")
            except Exception:
                date_time_formatted = date_time_raw

        header = f"Basic Info downloaded by MDAQ Desktop GUI on {datetime.now().strftime('%d/%m/%Y %I:%M:%S %p')}"
        sep = "=" * max(60, len(header))
        lines = [header, sep, ""]
        lines += [
            f"Name: {payload.get('name', '—')}",
            f"Age: {payload.get('subject_age', '—')}",
            f"UHI ID: {uhid}",
            f"MDAQ ID: {payload.get('device_id', '—')}",
            f"WD Size: {payload.get('wd_size', '—')}",
            f"WD ID: {payload.get('wd_id', '—')}",
            f"Room Temperature: {payload.get('room_temperature', '—')}",
            f"Body Temperature: {payload.get('body_temperature', '—')}",
            f"Date & Time: {date_time_formatted}",
        ]

        with open(fpath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        try:
            del self._pending_patient_info_path
        except Exception:
            pass

        self.patient_retrieve_status.configure(text=f"✓ File saved: {fname}", fg=self.C["success"])

    def _apply_retrieve_device_info(self, payload: dict):
        """Apply device info response (30-byte packet from Req_Type 0)"""
        if not payload:
            return
        
        device_mapping = {
            "Device ID": payload.get("device_id", "—"),
            "WD Size": payload.get("wd_size", "—"),
            "WD ID": payload.get("wd_id", "—"),
            "Battery(%)": f"{payload.get('battery_percentage', '—')}%",
            "Error Code": payload.get("error_code", "—"),
        }
        for k, v in device_mapping.items():
            if k in self.device_info_labels:
                self.device_info_labels[k].configure(text=v)
        self.device_info_status.configure(text="✓ Device info updated.")

        # store device info for later use when building retrieve headers
        try:
            if payload.get('device_id'):
                self.device_info['device_id'] = payload.get('device_id')
            if payload.get('wd_id'):
                self.device_info['wd_id'] = payload.get('wd_id')
            if payload.get('wd_size'):
                self.device_info['wd_size'] = payload.get('wd_size')
        except Exception:
            pass

        if getattr(self, "_pending_retrieve_waiting_for_basic_info", False):
            self._pending_retrieve_wd_size = payload.get("wd_size", "Full") or "Full"
            self.retrieve_status.configure(
                text=f"⏳ WD size {self._pending_retrieve_wd_size} received. Preparing retrieve request…",
                fg=self.C["subtext"])
            if getattr(self, "_pending_retrieve_after_basic_info_timer", None):
                try:
                    self.after_cancel(self._pending_retrieve_after_basic_info_timer)
                except Exception:
                    pass
                self._pending_retrieve_after_basic_info_timer = None
            self._pending_retrieve_after_basic_info_timer = self.after(1500, self._continue_retrieve_after_basic_info)
        #self._add_notification("Device information retrieved.", level="info")

    def _find_device(self):
        pkt = PacketProtocol.build_find_device()
        self.ble.write(pkt, "find_device")
        #self._add_notification("Find Device command sent (buzzer activated).", level="info")

    def _change_download_path(self):
        path = filedialog.askdirectory(initialdir=self.settings["download_path"])
        if path:
            self.settings["download_path"] = path
            self.path_var.set(path)
            save_settings(self.settings)

    def _open_download_folder(self):
        path = self.settings["download_path"]
        Path(path).mkdir(parents=True, exist_ok=True)
        import subprocess, sys
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    # ─────────────────────────────────────────────────────────────────────────
    #  BLE ACTIONS
    # ─────────────────────────────────────────────────────────────────────────
    def _ble_scan(self):
        self.device_listbox.delete(0, "end")
        self.device_listbox.insert("end", "  Scanning…")
        self.scan_btn.configure(state="disabled")
        self.ble.scan()

    def _ble_connect_selected(self, _event=None):
        sel = self.device_listbox.curselection()
        if not sel:
            return
        item = self.device_listbox.get(sel[0])
        if "Scanning" in item or not item.strip():
            return
        parts = item.split(" | ")
        if len(parts) < 2:
            return
        name, addr = parts[0].strip(), parts[1].strip()
        self.conn_label.configure(text=f"⏳ Connecting…", fg=self.C["subtext"])
        self.connected_name = name
        self.ble.connect(addr)

    def _stop_active_workflows(self):
        if getattr(self, "screening_session_active", False):
            self.screening_session_active = False
            self.screening_packets = []
            self.screening_count = 0
            if getattr(self, "screening_live_status", None):
                self.screening_live_status.configure(text="⚠ Screening aborted.", fg=self.C["warn"])
            if getattr(self, "start_btn", None):
                self.start_btn.configure(state="normal")
            if getattr(self, "stop_btn", None):
                self.stop_btn.configure(state="disabled")

        if getattr(self, "collecting_retrieve", False) or getattr(self, "_pending_retrieve_waiting_for_basic_info", False):
            self.collecting_retrieve = False
            self._pending_retrieve_waiting_for_basic_info = False
            self._pending_retrieve_uhid = None
            self._pending_retrieve_path = None
            self._pending_retrieve_wd_size = None
            if getattr(self, "retrieve_status", None):
                self.retrieve_status.configure(text="⚠ Retrieval aborted.", fg=self.C["warn"])
            if getattr(self, "_retrieve_timer", None):
                try:
                    self.after_cancel(self._retrieve_timer)
                except Exception:
                    pass
                self._retrieve_timer = None
            if getattr(self, "_pending_retrieve_after_basic_info_timer", None):
                try:
                    self.after_cancel(self._pending_retrieve_after_basic_info_timer)
                except Exception:
                    pass
                self._pending_retrieve_after_basic_info_timer = None

        if getattr(self, "_pending_initiation_response", False):
            self._pending_initiation_response = False
            if getattr(self, "device_info_status", None):
                self.device_info_status.configure(text="⚠ Pending device sync cancelled.", fg=self.C["warn"])

    def _ble_disconnect(self):
        self._stop_active_workflows()
        if self._ble_connected_state or getattr(self.ble, 'client', None):
            self.conn_label.configure(text="⏳ Disconnecting…", fg=self.C["subtext"])
            self.disconnect_btn.configure(state="disabled")
            self.scan_btn.configure(state="normal")
            self._ble_connected_state = False
            self.connected_address = ""
            self.connected_name = ""
        try:
            self.ble.disconnect_and_wait(timeout=3.0)
        except Exception:
            self.ble.disconnect()

    def _on_app_close(self):
        self._stop_active_workflows()
        try:
            if getattr(self.ble, 'connected', False) or getattr(self.ble, 'client', None):
                self.ble.disconnect_and_wait(timeout=2.0)
        except Exception:
            pass
        self.destroy()

    # ─────────────────────────────────────────────────────────────────────────
    #  QUEUE POLL  – bridge async BLE events → tkinter UI
    # ─────────────────────────────────────────────────────────────────────────
    def _poll_queue(self):
        try:
            while True:
                event, payload = self.ble_q.get_nowait()
                self._handle_ble_event(event, payload)
        except queue.Empty:
            pass
        self._refresh_connection_state()
        self.after(100, self._poll_queue)

    def _refresh_connection_state(self):
        C = self.C
        if self._ble_connected_state:
            if self.conn_label.cget("text") == "● Not connected":
                self.conn_label.configure(text=f"● {self.connected_name}", fg=C["success"])
                self.disconnect_btn.configure(state="normal")
                self.scan_btn.configure(state="disabled")
        else:
            if self.conn_label.cget("text") not in ("● Not connected", "⏳ Connecting…"):
                self.conn_label.configure(text="● Not connected", fg=C["danger"])
                self.disconnect_btn.configure(state="disabled")
                self.scan_btn.configure(state="normal")

    def _handle_ble_event(self, event: str, payload):
        C = self.C
        if event == "ble_scan_start":
            self.scan_btn.configure(state="disabled")

        elif event == "ble_scan_done":
            self.scan_btn.configure(state="normal")
            self.device_listbox.delete(0, "end")
            devices: list[dict] = payload
            if not devices:
                self.device_listbox.insert("end", "  No devices found")
            for d in devices:
                self.device_listbox.insert("end", f"  {d['name']} | {d['address']}")
            #self._add_notification(f"Scan complete – {len(devices)} device(s) found.", level="info")

        elif event == "ble_connected":
            self._ble_connected_state = True
            self.connected_address = payload
            # reset notified errors on new connection
            try:
                self._notified_error_codes.clear()
            except Exception:
                self._notified_error_codes = set()
            # mark simulated client as connected
            if not BLE_AVAILABLE:
                    self._add_notification(
                        "BLE is not available. Please install the BLE library or enable Bluetooth.",
                        level="error"
                    )
                    return
            self.conn_label.configure(
                text=f"● {self.connected_name}", fg=C["success"])
            self.disconnect_btn.configure(state="normal")
            self.scan_btn.configure(state="disabled")
            self._add_notification(f"Connected to {self.connected_name}.", level="success")
            self._send_device_time_initiation()

        elif event == "ble_disconnected":
            self._stop_active_workflows()
            self._ble_connected_state = False
            self.ble.client = None
            self.connected_address = ""
            self.connected_name = ""
            # clear notified errors on disconnect so future devices can notify again
            try:
                self._notified_error_codes.clear()
            except Exception:
                self._notified_error_codes = set()
            self.conn_label.configure(text="● Not connected", fg=C["danger"])
            self.disconnect_btn.configure(state="disabled")
            self.scan_btn.configure(state="normal")
            if getattr(self, "start_btn", None):
                self.start_btn.configure(state="disabled")
            if getattr(self, "stop_btn", None):
                self.stop_btn.configure(state="disabled")
            self._add_notification("Disconnected from device.", level="warn")

        elif event == "ble_notify":
            parsed: dict = payload
            self._monitor_log(f"RX: {parsed}")
            # If packet carries an error_code, update UI and notify for every packet.
            ec = parsed.get("error_code")
            if ec is not None:
                # Update device info UI if present
                if "Error Code" in getattr(self, "device_info_labels", {}):
                    try:
                        self.device_info_labels["Error Code"].configure(text=str(ec if ec != 0 else "—"))
                    except Exception:
                        pass

                if ec != 0:
                    errors = self.decode_error_code(ec)
                    if errors:
                        joined = "; ".join(errors)
                        msg = f"Device reported error(s): {joined} (code {ec})"
                    else:
                        msg = f"Device reported unknown error code: {ec}"
                    self._add_notification(msg, level="error")
            ptype = parsed.get("type")
            if ptype == "bi_ack":
                self._on_bi_registered(parsed.get("error_code") == 0)
            elif ptype == "start_screening_ack":
                if parsed.get("error_code") == 0:
                    self._on_screening_started()
                else:
                    self.si_status.configure(text="✗ Screening start failed.", fg=self.C["danger"])
            elif ptype == "stop_screening_ack":
                if parsed.get("status") == "OK":
                    self._on_screening_stopped()
                else:
                    self.si_status.configure(text="✗ Stop failed.", fg=self.C["danger"])
            # elif ptype == "buzzer_ack":
            # #self._add_notification("Buzzer command acknowledged.", level="info")
            elif self.screening_session_active and parsed.get("temperature_data") is not None:
                self._on_screening_data(parsed)
            elif ptype == "retrieve_data":
                # If we're actively collecting a retrieve/download, append packets
                if getattr(self, "collecting_retrieve", False):
                    self._handle_retrieve_packet(parsed)
                elif self.screening_session_active:
                    self._on_screening_data(parsed)
                else:
                    self._on_retrieve_response(parsed)
            elif ptype == "device_info":
                self._apply_device_info(parsed)
                if self._pending_initiation_response:
                    self._on_initiation_response(parsed)
            elif ptype == "retrieve_device_info":
                self._apply_retrieve_device_info(parsed)
                if self._pending_initiation_response:
                    self._on_initiation_response(parsed)
            elif self._pending_initiation_response:
                self._on_initiation_response(parsed)
            else:
                self._monitor_log(f"Unhandled packet type: {ptype}")

        elif event == "ble_ack":
            key = payload.get("key", "")
            self._monitor_log(f"ACK: {key} = {payload.get('status')}")

        elif event == "ble_error":
            if not payload:
                payload = "Unknown BLE error."
            if not self._ble_connected_state:
                self.connected_name = ""
                self.conn_label.configure(text="● Not connected", fg=C["danger"])
                self.disconnect_btn.configure(state="disabled")
                self.scan_btn.configure(state="normal")
            self._add_notification(f"BLE error: {payload}", level="error")
            messagebox.showerror("BLE Error", str(payload))

    def _send_device_time_initiation(self):
        if not self._ble_connected_state:
            return

        pkt = PacketProtocol.build_initiation()
        self.ble.write(pkt, "device_time_init")
        self._pending_initiation_response = True
        self.device_info_status.configure(text="⌛ Syncing device clock…", fg=self.C["subtext"])

        if getattr(self, "_pending_initiation_timer", None):
            try:
                self.after_cancel(self._pending_initiation_timer)
            except Exception:
                pass
        self._pending_initiation_timer = self.after(2000, self._on_initiation_timeout)

    def _on_initiation_response(self, parsed: dict):
        if not self._pending_initiation_response:
            return
        self._pending_initiation_response = False
        if getattr(self, "_pending_initiation_timer", None):
            try:
                self.after_cancel(self._pending_initiation_timer)
            except Exception:
                pass
            self._pending_initiation_timer = None

        self.device_info_status.configure(text="✓ Device clock set. Connected successfully.", fg=self.C["success"])
        self._add_notification("Connected: Device Idle.", level="success")

    def _on_initiation_timeout(self):
        self._pending_initiation_timer = None
        if not self._pending_initiation_response:
            return
        self._pending_initiation_response = False
        self.device_info_status.configure(text="⚠ Device busy; still connected.", fg=self.C["warn"])
        self._add_notification("Connected: Device Busy.", level="warn")


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = MDAQApp()
    app.mainloop()
