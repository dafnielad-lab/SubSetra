# -*- coding: utf-8 -*-
"""
Graphical interface (Tkinter) for the accounting reconciliation tool.

This file wraps the computation engine in subset_sum_reconcile.py. The search
runs on a separate thread so the window never freezes, it can be stopped
mid-run ("Stop"), and the results are shown in a table and also written to the
'Output' sheet.
"""

import math
import os
import queue
import sys
import threading


# Under pythonw.exe (the GUI launcher) there is no console, so sys.stdout
# and sys.stderr can be None. The engine occasionally print()s status/warnings;
# route those to a sink so nothing can break the GUI.
class _NullStream:
    def write(self, *a, **k):
        pass

    def flush(self):
        pass


if sys.stdout is None:
    sys.stdout = _NullStream()
if sys.stderr is None:
    sys.stderr = _NullStream()


import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter import font as tkfont

import subset_sum_reconcile as core


def resource_path(name):
    """Resolve a bundled file both when run as a script and inside the
    PyInstaller one-file EXE (where data is unpacked to sys._MEIPASS)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


# ---------------------------------------------------------------------------
# Visual tokens
# ---------------------------------------------------------------------------

BG          = "#F6F6F3"   # window background
SURFACE     = "#FFFFFF"   # cards / inputs
BORDER      = "#E4E4DF"   # hairline borders
BORDER_SOFT = "#EFEFEA"
TEXT        = "#1F2430"
TEXT_MUTED  = "#6B7280"
ACCENT      = "#2F6BE6"   # single accent — primary action
ACCENT_HOV  = "#2558C2"
ACCENT_DOWN = "#1E47A0"
DANGER      = "#B23A3A"
ROW_ALT     = "#FAFAF7"
ROW_SELECT  = "#E3ECFB"

RADIUS      = 14          # uniform corner radius for all components (cards, fields, buttons)


def rtl(s: str) -> str:
    """No-op in this LTR English build (the original RTL build wrapped Hebrew strings to fix bidi ordering)."""
    return s


def pick_font():
    """Pick a UI font that exists on this machine."""
    fams = set(tkfont.families())
    for cand in ("Segoe UI", "Arial Hebrew", "Arial", "Helvetica Neue",
                 "Helvetica", "Tahoma", "DejaVu Sans"):
        if cand in fams:
            return cand
    return "TkDefaultFont"


def _fmt_money(cents):
    """Format a single cent value as a signed shekel string, e.g. -1,250.30."""
    sign = "-" if cents < 0 else ""
    return f"{sign}{abs(cents) / 100:,.2f}"


def value_frequencies(solutions):
    """How many of the given solutions each distinct value appears in.

    Returns a list of (value_cents, count) sorted by count descending, then by
    value descending. A value that repeats inside one solution still counts
    that solution only once (presence, not multiplicity)."""
    from collections import Counter
    counter = Counter()
    for sol in solutions:
        for v in set(sol):
            counter[v] += 1
    return sorted(counter.items(), key=lambda kv: (-kv[1], -kv[0]))


# ---------------------------------------------------------------------------
# Rounded-rectangle geometry + a rounded container. Tkinter has no native
# rounded corners, so we trace real corner arcs and draw the card/field
# background on a Canvas. One radius (RADIUS) is reused everywhere so all
# cards, fields and buttons share the same corner.
# ---------------------------------------------------------------------------

def _round_rect_coords(x1, y1, x2, y2, r, steps=16):
    """Polygon points for a rectangle with uniform rounded corners (true arcs)."""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = []

    def arc(cx, cy, a0, a1):
        for i in range(steps + 1):
            a = math.radians(a0 + (a1 - a0) * i / steps)
            pts.extend((cx + r * math.cos(a), cy + r * math.sin(a)))

    arc(x2 - r, y1 + r, -90, 0)     # top-right
    arc(x2 - r, y2 - r, 0, 90)      # bottom-right
    arc(x1 + r, y2 - r, 90, 180)    # bottom-left
    arc(x1 + r, y1 + r, 180, 270)   # top-left
    return pts


class RoundedFrame(tk.Frame):
    """A container with a rounded-rectangle background. Add children to .body;
    pack the RoundedFrame itself.

    The background is drawn on a Canvas behind it; the body (same fill color)
    sits with a small margin so the rounded corners and border show around it.
    """

    def __init__(self, master, radius=RADIUS, fill=SURFACE, border=BORDER,
                 parent_bg=BG, border_width=1, pad=None):
        super().__init__(master, bg=parent_bg, bd=0, highlightthickness=0)
        self._radius = radius
        self._fill = fill
        self._border = border
        self._bw = border_width
        self._pad = pad if pad is not None else max(4, int(round(radius * 0.55)))
        self._bgc = tk.Canvas(self, bg=parent_bg, highlightthickness=0, bd=0)
        self._bgc.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=fill)
        self.body.pack(fill="both", expand=True, padx=self._pad, pady=self._pad)
        # Redraw on the CANVAS's own <Configure> — it reports the real drawn
        # size reliably (binding on the Frame can fire before the canvas has
        # resized, leaving the rounded background undrawn -> sharp corners).
        self._bgc.bind("<Configure>", self._redraw)
        # Belt-and-suspenders: force a draw after layout settles, in case the
        # canvas <Configure> didn't fire with the final size on this platform.
        self.after(60, self._redraw)

    def _redraw(self, event=None):
        c = self._bgc
        w = event.width if event is not None else c.winfo_width()
        h = event.height if event is not None else c.winfo_height()
        if w <= 2 or h <= 2:
            return
        c.delete("all")
        b = self._bw
        coords = _round_rect_coords(b, b, w - b, h - b, self._radius)
        c.create_polygon(coords, smooth=False,
                         fill=self._fill, outline=self._border, width=b)
        # (the canvas is created before .body, so it already sits behind it)


# ---------------------------------------------------------------------------
# A rounded Canvas button — used for the action buttons so corners match the
# cards/fields exactly (ttk.Button can't do rounded corners cross-platform).
# ---------------------------------------------------------------------------

class RoundedButton(tk.Canvas):
    def __init__(self, master, text, command=None,
                 bg=ACCENT, fg="#FFFFFF",
                 hover_bg=ACCENT_HOV, down_bg=ACCENT_DOWN,
                 disabled_bg="#B9C6E0", disabled_fg="#ECF1FB",
                 outline=None, outline_width=1,
                 radius=12, padx=28, pady=14,
                 font=None, parent_bg=BG, min_width=0):
        super().__init__(master, highlightthickness=0, bd=0, bg=parent_bg)
        self._text       = text
        self._command    = command
        self._bg         = bg
        self._fg         = fg
        self._hover_bg   = hover_bg
        self._down_bg    = down_bg
        self._dis_bg     = disabled_bg
        self._dis_fg     = disabled_fg
        self._outline    = outline           # visible outline (for light buttons)
        self._outline_w  = outline_width
        self._radius     = radius
        self._font       = font or ("Arial", 12, "bold")
        self._enabled    = True
        self._state      = "normal"

        f = tkfont.Font(font=self._font)
        tw = f.measure(text)
        th = f.metrics("linespace")
        # NOTE: do NOT use self._w / self._h here — tkinter reserves self._w
        # for the widget's Tcl path. Use _cw / _ch for our canvas size.
        self._cw = max(min_width, tw + padx * 2)
        self._ch = th + pady * 2
        self.configure(width=self._cw, height=self._ch)

        self.bind("<Enter>",        lambda e: self._set_state("hover"))
        self.bind("<Leave>",        lambda e: self._set_state("normal"))
        self.bind("<ButtonPress-1>",   lambda e: self._set_state("down"))
        self.bind("<ButtonRelease-1>", self._on_release)
        self._render()

    # --- public ----------------------------------------------------------
    def set_enabled(self, on: bool):
        self._enabled = bool(on)
        self._state = "normal"
        self._render()

    def set_text(self, text: str):
        self._text = text
        self._render()

    # --- internals -------------------------------------------------------
    def _set_state(self, s):
        if not self._enabled:
            return
        self._state = s
        self._render()

    def _on_release(self, _e):
        if not self._enabled:
            return
        was_down = self._state == "down"
        self._state = "hover"
        self._render()
        if was_down and self._command:
            self._command()

    def _current_fill(self):
        if not self._enabled:
            return self._dis_bg
        return {"normal": self._bg,
                "hover":  self._hover_bg,
                "down":   self._down_bg}[self._state]

    def _current_fg(self):
        return self._dis_fg if not self._enabled else self._fg

    def _render(self):
        self.delete("all")
        w, h = self._cw, self._ch
        fill = self._current_fill()
        # Inset by the line width so the border isn't clipped at the edge.
        ow = max(1, self._outline_w)
        outline = self._outline if self._outline else fill
        coords = _round_rect_coords(ow, ow, w - ow, h - ow, self._radius)
        self.create_polygon(coords, smooth=False, joinstyle="round",
                            fill=fill, outline=outline, width=ow)
        self.create_text(w // 2, h // 2,
                         text=self._text, fill=self._current_fg(),
                         font=self._font)


# ---------------------------------------------------------------------------
# A compact number field with up/down steppers. ttk.Spinbox arrows can't be
# resized to fit a padded field, so we build our own: an entry plus two
# arrow labels that fill the field height.
# ---------------------------------------------------------------------------

class NumberStepper(RoundedFrame):
    def __init__(self, master, textvariable, from_=1, to=240,
                 font=None, entry_width=3):
        super().__init__(master, radius=RADIUS, fill=SURFACE, border=BORDER,
                         parent_bg=SURFACE, pad=5)
        self._var = textvariable
        self._min, self._max = from_, to
        afont = (font[0] if font else "Arial", 8)
        p = self.body

        # arrows column on the LEFT (RTL: the number reads on the right)
        col = tk.Frame(p, bg=SURFACE)
        col.pack(side="left", fill="y", padx=(4, 6))
        self._up = tk.Label(col, text="▲", bg=SURFACE, fg=TEXT_MUTED,
                            font=afont, cursor="hand2")
        self._dn = tk.Label(col, text="▼", bg=SURFACE, fg=TEXT_MUTED,
                            font=afont, cursor="hand2")
        self._up.pack(side="top", expand=True)
        self._dn.pack(side="bottom", expand=True)
        for widget, delta in ((self._up, +1), (self._dn, -1)):
            widget.bind("<Button-1>", lambda e, d=delta: self._step(d))
            widget.bind("<Enter>", lambda e, w=widget: w.config(fg=ACCENT))
            widget.bind("<Leave>", lambda e, w=widget: w.config(fg=TEXT_MUTED))

        self._entry = tk.Entry(p, textvariable=textvariable, justify="right",
                               relief="flat", bd=0, bg=SURFACE, fg=TEXT,
                               width=entry_width, font=font, highlightthickness=0)
        self._entry.pack(side="right", fill="both", expand=True,
                         padx=(8, 8), pady=4)

    def _step(self, delta):
        try:
            v = int(float(str(self._var.get()).replace(",", ".")))
        except (ValueError, TypeError):
            v = self._min
        self._var.set(str(max(self._min, min(self._max, v + delta))))


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class ReconciliationApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Subsetra — Reconciliation Match Finder")
        self.geometry("1040x860")
        self.minsize(880, 720)
        self.configure(bg=BG)

        # Window / taskbar icon (best-effort; never let it break startup).
        try:
            self.iconbitmap(resource_path("subsetra_icon.ico"))
        except Exception:
            pass

        # Fonts
        fam = pick_font()
        self.f_title  = (fam, 22, "bold")
        self.f_sub    = (fam, 11)
        self.f_label  = (fam, 11)
        self.f_value  = (fam, 11)
        self.f_btn    = (fam, 11)
        self.f_cta    = (fam, 13, "bold")
        self.f_head   = (fam, 10, "bold")
        self.f_status = (fam, 10)

        # State
        self.var_file     = tk.StringVar(value="No file selected")
        self.var_minutes  = tk.StringVar(value="5")
        self.var_results  = tk.StringVar(value="10")
        self.var_status   = tk.StringVar(value=rtl("Ready"))

        # Search plumbing (set when a run starts)
        self._queue = None
        self._cancel = None
        self._worker = None
        self._filepath = None

        # Last search results (cent-tuples) + their stats, kept so the analysis
        # window and the "save to Excel" button can work on them without
        # re-running the engine.
        self._solutions = []
        self._stats = None

        self._configure_styles()
        self._build_layout()

    # ---------------- styling -------------------------------------------
    def _configure_styles(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass

        s.configure("App.TFrame",  background=BG)
        s.configure("Card.TFrame", background=SURFACE)
        s.configure("Sep.TFrame",  background=BORDER_SOFT)

        s.configure("App.TLabel",
                    background=BG, foreground=TEXT, font=self.f_label)
        s.configure("Title.TLabel",
                    background=BG, foreground=TEXT, font=self.f_title)
        s.configure("Sub.TLabel",
                    background=BG, foreground=TEXT_MUTED, font=self.f_sub)
        s.configure("CardLabel.TLabel",
                    background=SURFACE, foreground=TEXT, font=self.f_label)
        s.configure("CardMuted.TLabel",
                    background=SURFACE, foreground=TEXT_MUTED, font=self.f_sub)
        s.configure("Status.TLabel",
                    background=BG, foreground=TEXT_MUTED, font=self.f_status)

        # Entry (also covers Spinbox readability)
        s.configure("App.TEntry",
                    fieldbackground=SURFACE, background=SURFACE,
                    foreground=TEXT, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER,
                    relief="flat", padding=8)
        s.map("App.TEntry",
              bordercolor=[("focus", ACCENT)],
              lightcolor=[("focus", ACCENT)],
              darkcolor=[("focus", ACCENT)])

        # (The minutes field is a custom NumberStepper, not a ttk.Spinbox.)

        # (The file-picker and stop buttons are RoundedButton canvases, not
        #  ttk buttons, so no ttk button styles are needed.)

        # Progress bar
        s.configure("App.Horizontal.TProgressbar",
                    background=ACCENT,
                    troughcolor="#ECECE6",
                    bordercolor="#ECECE6",
                    lightcolor=ACCENT, darkcolor=ACCENT,
                    thickness=6)

        # Treeview
        s.configure("App.Treeview",
                    background=SURFACE, fieldbackground=SURFACE,
                    foreground=TEXT, bordercolor=BORDER,
                    rowheight=34, font=self.f_value)
        s.configure("App.Treeview.Heading",
                    background="#F1F1EC", foreground=TEXT_MUTED,
                    font=self.f_head, relief="flat", padding=(10, 10),
                    bordercolor=BORDER_SOFT)
        s.map("App.Treeview",
              background=[("selected", ROW_SELECT)],
              foreground=[("selected", TEXT)])
        s.map("App.Treeview.Heading",
              background=[("active", "#E8E8E2")])

        s.layout("App.Treeview",
                 [("App.Treeview.treearea", {"sticky": "nswe"})])

        # Vertical scrollbar — slim, no arrow buttons (modern look).
        s.layout("App.Vertical.TScrollbar",
                 [("Vertical.Scrollbar.trough", {"sticky": "ns", "children":
                     [("Vertical.Scrollbar.thumb",
                       {"expand": "1", "sticky": "nswe"})]})])
        s.configure("App.Vertical.TScrollbar",
                    troughcolor=SURFACE, background="#CFCFC9",
                    bordercolor=SURFACE, lightcolor=SURFACE, darkcolor=SURFACE,
                    arrowsize=12, gripcount=0)
        s.map("App.Vertical.TScrollbar",
              background=[("active", "#B6B6B0")])

    # ---------------- layout --------------------------------------------
    def _build_layout(self):
        outer = ttk.Frame(self, style="App.TFrame", padding=(28, 24, 28, 24))
        outer.pack(fill="both", expand=True)

        self._build_header(outer)
        self._build_info_card(outer)        # how to prepare the Excel file
        self._build_inputs_card(outer)      # just the file picker
        self._build_command_bar(outer)      # framed time cap + actions
        self._build_progress_row(outer)
        self._build_results_card(outer)

    # Header --------------------------------------------------------------
    def _build_header(self, parent):
        header = ttk.Frame(parent, style="App.TFrame")
        header.pack(fill="x", pady=(0, 18))

        title_wrap = ttk.Frame(header, style="App.TFrame")
        title_wrap.pack(side="left")

        ttk.Label(title_wrap, text="Subsetra — Reconciliation Match Finder",
                  style="Title.TLabel", anchor="w", justify="left"
                  ).pack(side="top", anchor="w")
        ttk.Label(title_wrap,
                  text="Find groups of transactions whose sum equals a target value",
                  style="Sub.TLabel", anchor="w", justify="left"
                  ).pack(side="top", anchor="w", pady=(4, 0))

    # Inputs card ---------------------------------------------------------
    def _build_inputs_card(self, parent):
        """File picker only — the time cap moved into the command bar."""
        card = self._card(parent)
        card.pack(fill="x", pady=(0, 14))

        inner = tk.Frame(card.body, bg=SURFACE)
        inner.pack(fill="x", padx=20, pady=18)

        file_row = tk.Frame(inner, bg=SURFACE)
        file_row.pack(fill="x")

        ttk.Label(file_row, text="Excel file",
                  style="CardLabel.TLabel", anchor="w"
                  ).pack(side="left")

        # LTR: label on the left, entry fills the middle, action buttons on the
        # right. Bordered RoundedButtons (not ttk) so the outline renders
        # reliably across themes.
        RoundedButton(file_row, text=rtl("Manual entry"),
                      command=self.open_manual_entry,
                      bg=SURFACE, hover_bg="#F1F1EC", down_bg="#E9E9E3",
                      fg=TEXT, outline=BORDER, outline_width=1,
                      font=self.f_btn, padx=18, pady=10, radius=RADIUS,
                      parent_bg=SURFACE, min_width=0
                      ).pack(side="right")

        RoundedButton(file_row, text=rtl("Choose file…"),
                      command=self.on_pick_file,
                      bg=SURFACE, hover_bg="#F1F1EC", down_bg="#E9E9E3",
                      fg=TEXT, outline=BORDER, outline_width=1,
                      font=self.f_btn, padx=18, pady=10, radius=RADIUS,
                      parent_bg=SURFACE, min_width=0
                      ).pack(side="right", padx=(0, 12))

        # Rounded field so its border matches the rounded buttons exactly;
        # the entry inside is borderless.
        field = RoundedFrame(file_row, radius=RADIUS, fill=SURFACE,
                             border=BORDER, parent_bg=SURFACE, pad=5)
        field.pack(side="left", fill="x", expand=True, padx=(12, 12))
        self.entry_file = tk.Entry(field.body,
                                   textvariable=self.var_file,
                                   state="readonly", justify="left",
                                   relief="flat", bd=0, highlightthickness=0,
                                   readonlybackground=SURFACE, fg=TEXT_MUTED,
                                   font=self.f_value)
        self.entry_file.pack(fill="x", expand=True, padx=8, ipady=6)

    # Info card ---------------------------------------------------------
    def _build_info_card(self, parent):
        """Instructions for how the input Excel file must be structured."""
        card = self._card(parent)
        card.pack(fill="x", pady=(0, 14))

        inner = tk.Frame(card.body, bg=SURFACE)
        inner.pack(fill="x", padx=20, pady=18)

        # Plain bold title (no icon) — matches the other card headers.
        head = tk.Frame(inner, bg=SURFACE)
        head.pack(fill="x")

        ttk.Label(head, text="How to use",
                  style="CardLabel.TLabel", anchor="w",
                  font=(self.f_label[0], 12, "bold")
                  ).pack(side="left")

        items = [
            'Create a new Excel workbook and add a sheet named "Input".',
            'Enter the list of transactions to analyze in column A, starting at cell A2.',
            'Enter the target sum in cell D2.',
            'In cell D3, enter the maximum number of transactions per solution (default: up to 8 transactions).',
        ]
        steps = tk.Frame(inner, bg=SURFACE)
        steps.pack(fill="x", pady=(10, 0))
        for n, txt in enumerate(items, start=1):
            line = tk.Frame(steps, bg=SURFACE)
            line.pack(fill="x", pady=(0, 4))
            ttk.Label(line, text=rtl(f"{n}.  {txt}"),
                      style="CardLabel.TLabel", anchor="w",
                      justify="left", wraplength=860,
                      ).pack(side="left", fill="x", expand=True)

    # Command bar (time cap + actions, framed) ---------------------------
    def _build_command_bar(self, parent):
        card = self._card(parent)
        card.pack(fill="x", pady=(0, 10))

        inner = tk.Frame(card.body, bg=SURFACE)
        inner.pack(fill="x", padx=20, pady=16)

        # RIGHT side (RTL): action buttons cluster.
        self.btn_run = RoundedButton(
            inner, text="Find matches",
            command=self.on_run,
            bg=ACCENT, hover_bg=ACCENT_HOV, down_bg=ACCENT_DOWN,
            font=self.f_cta, padx=32, pady=14, radius=RADIUS,
            parent_bg=SURFACE, min_width=200,
        )
        self.btn_run.pack(side="right")

        self.btn_stop = RoundedButton(
            inner, text="Stop",
            command=self.on_stop,
            bg=SURFACE, hover_bg="#FBEFEF", down_bg="#F6E0E0",
            fg=DANGER, disabled_bg=SURFACE, disabled_fg="#D9B6B6",
            outline="#D7A2A2", outline_width=1,
            font=self.f_cta, padx=28, pady=14, radius=RADIUS,
            parent_bg=SURFACE, min_width=140,
        )
        self.btn_stop.pack(side="right", padx=(0, 16))
        self.btn_stop.set_enabled(False)

        # LEFT side (RTL): two numeric fields — time limit and result count —
        # each a labelled stepper with a small helper line below.
        fields = tk.Frame(inner, bg=SURFACE)
        fields.pack(side="left", anchor="n")

        time_wrap, self.spin_minutes = self._field_group(
            fields, "Time limit (min)", self.var_minutes, 0, 1440,
            "Max search time (0 = unlimited)")
        results_wrap, self.spin_results = self._field_group(
            fields, "Result count", self.var_results, 1, 9999,
            "Max solutions to display")

        time_wrap.pack(side="left")
        results_wrap.pack(side="left", padx=(24, 0))

    def _field_group(self, parent, label_text, var, from_, to, helper_text):
        """Label + NumberStepper with a small helper line below. Returns
        (wrap, stepper); the caller packs the wrap."""
        wrap = tk.Frame(parent, bg=SURFACE)
        controls = tk.Frame(wrap, bg=SURFACE)
        controls.pack(anchor="w")
        ttk.Label(controls, text=rtl(label_text),
                  style="CardLabel.TLabel", anchor="w").pack(side="left")
        stepper = NumberStepper(controls, textvariable=var, from_=from_, to=to,
                                font=self.f_value, entry_width=5)
        stepper.pack(side="left", padx=(12, 0))   # gap between the label and its field
        ttk.Label(wrap, text=helper_text, background=SURFACE,
                  foreground=TEXT_MUTED, font=(self.f_label[0], 9)
                  ).pack(anchor="w", pady=(4, 0))
        return wrap, stepper

    # Progress row --------------------------------------------------------
    def _build_progress_row(self, parent):
        row = ttk.Frame(parent, style="App.TFrame")
        row.pack(fill="x", pady=(0, 10))

        # Left-inset to line up with the in-card text (card pad 8 + inner 20).
        ttk.Label(row, textvariable=self.var_status,
                  style="Status.TLabel", anchor="w"
                  ).pack(side="left", padx=(28, 0))

        # A fixed-width bar, shown only while a search runs (see on_run /
        # _finish_ui) so it doesn't dominate the row at idle.
        self.progress = ttk.Progressbar(
            row, style="App.Horizontal.TProgressbar",
            mode="determinate", value=0, maximum=100, length=260,
        )

    # Results card --------------------------------------------------------
    def _build_results_card(self, parent):
        card = self._card(parent)
        card.pack(fill="both", expand=True)

        # Header strip inside the card
        head = tk.Frame(card.body, bg=SURFACE)
        head.pack(fill="x", padx=20, pady=(16, 8))

        ttk.Label(head, text="Results",
                  style="CardLabel.TLabel", anchor="w",
                  font=(self.f_label[0], 12, "bold")
                  ).pack(side="left")

        # "Analyze results" — opens the elimination/frequency window. Enabled
        # only once there is more than one match to narrow down.
        self.btn_analyze = RoundedButton(
            head, text=rtl("Analyze results"),
            command=self.open_analysis,
            bg=SURFACE, hover_bg="#F1F1EC", down_bg="#E9E9E3",
            fg=TEXT, outline=BORDER, outline_width=1,
            font=self.f_btn, padx=16, pady=8, radius=RADIUS,
            parent_bg=SURFACE, min_width=0)
        # padx right-side gap = breathing room between the "Results" title and the button
        self.btn_analyze.pack(side="right", padx=(0, 18))
        self.btn_analyze.set_enabled(False)

        # "Save to Excel" — re-write the 'Output' sheet on demand. Always available
        # while results exist, so if the auto-save after a search failed because
        # the file was open, the user can just close Excel and save — no re-run.
        self.btn_save = RoundedButton(
            head, text=rtl("Save to Excel"),
            command=self.on_save_excel,
            bg=SURFACE, hover_bg="#F1F1EC", down_bg="#E9E9E3",
            fg=TEXT, outline=BORDER, outline_width=1,
            font=self.f_btn, padx=16, pady=8, radius=RADIUS,
            parent_bg=SURFACE, min_width=0)
        self.btn_save.pack(side="right", padx=(0, 10))
        self.btn_save.set_enabled(False)

        self.var_result_count = tk.StringVar(value=rtl("0 matches"))
        ttk.Label(head, textvariable=self.var_result_count,
                  style="CardMuted.TLabel", anchor="w"
                  ).pack(side="left", padx=(12, 0))

        # divider
        tk.Frame(card.body, bg=BORDER_SOFT, height=1).pack(fill="x", padx=20)

        # Treeview + scrollbar
        body = tk.Frame(card.body, bg=SURFACE)
        body.pack(fill="both", expand=True, padx=20, pady=(8, 18))

        # Columns are laid out left-to-right in declaration order. For this
        # English LTR build we declare them in natural reading order:
        #   Solution # | # items | Transactions | Sum
        cols = ("num", "count", "transactions", "sum")
        self.tree = ttk.Treeview(body,
                                 columns=cols, show="headings",
                                 style="App.Treeview", selectmode="browse")

        headings = {
            "num":          "Solution #",
            "count":        "# items",
            "transactions": "Transactions",
            "sum":          "Sum",
        }
        widths = {
            "num":          70,
            "count":        140,
            "transactions": 460,
            "sum":          140,
        }
        anchors = {
            "num":          "center",
            "count":        "center",
            "transactions": "w",
            "sum":          "w",
        }
        for cid in cols:
            self.tree.heading(cid, text=headings[cid], anchor=anchors[cid])
            self.tree.column(cid, width=widths[cid], anchor=anchors[cid], stretch=True)

        # Tree fills the whole body so its column-header row spans the full
        # width (aligned with the "Results" strip above it).
        self.tree.pack(fill="both", expand=True)

        # Auto-hiding scrollbar: created but not packed. It is overlaid on the
        # LEFT edge over the DATA rows only — below the column-header row —
        # and appears only when there is something to scroll.
        self.vsb = ttk.Scrollbar(body, orient="vertical",
                                 style="App.Vertical.TScrollbar",
                                 command=self.tree.yview)
        self.tree.configure(yscrollcommand=self._on_tree_scroll)
        self.tree.bind("<Configure>", lambda e: self._reposition_scrollbar())

        # Alternating row colors
        self.tree.tag_configure("odd",  background=ROW_ALT)
        self.tree.tag_configure("even", background=SURFACE)

    # --- auto-hiding scrollbar that sits beside the data rows only -------
    def _heading_height(self):
        kids = self.tree.get_children()
        if kids:
            bb = self.tree.bbox(kids[0])
            if bb:
                return bb[1]                 # y of first data row == header height
        return 26                            # fallback before any rows exist

    def _place_scrollbar(self):
        h = self.tree.winfo_height() - self._heading_height()
        if h > 0:
            self.vsb.place(in_=self.tree, x=0, y=self._heading_height(),
                           anchor="nw", height=h)

    def _on_tree_scroll(self, first, last):
        first, last = float(first), float(last)
        self.vsb.set(first, last)
        if first <= 0.0 and last >= 1.0:     # everything fits -> hide
            self.vsb.place_forget()
        else:
            self._place_scrollbar()

    def _reposition_scrollbar(self):
        if self.vsb.winfo_ismapped():
            self._place_scrollbar()

    # ---------------- helpers -------------------------------------------
    def _card(self, parent):
        """A rounded white card with a 1px border. Add children to card.body."""
        return RoundedFrame(parent, radius=RADIUS, fill=SURFACE,
                            border=BORDER, parent_bg=BG)

    # ===================================================================== #
    #  Callbacks — wired to the search engine (subset_sum_reconcile).
    # ===================================================================== #
    def on_pick_file(self):
        path = filedialog.askopenfilename(
            title="Choose Excel file",
            filetypes=[("Excel files", "*.xlsx *.xls"), ("All files", "*.*")],
        )
        if path:
            self.var_file.set(path)
            self.entry_file.configure(foreground=TEXT)

    def open_manual_entry(self):
        """Open the manual-entry dialog (skip preparing an Excel file by hand)."""
        ManualEntryDialog(self)

    def set_loaded_file(self, path):
        """Called by the manual-entry dialog once it has written the xlsx."""
        self.var_file.set(path)
        self.entry_file.configure(foreground=TEXT)

    def on_run(self):
        # already running?
        if self._worker is not None and self._worker.is_alive():
            return

        path = self.var_file.get().strip()
        if not path or path == "No file selected" or not os.path.isfile(path):
            messagebox.showwarning("Notice", "Please choose an existing Excel file.")
            return

        # time cap (minutes -> seconds); 0 (or less) means no limit, like the CLI
        try:
            minutes = float(self.var_minutes.get().replace(",", "."))
        except ValueError:
            minutes = 5.0
        max_seconds = minutes * 60.0 if minutes > 0 else None

        # result count
        try:
            max_results = int(float(self.var_results.get().replace(",", ".")))
        except ValueError:
            max_results = 10
        if max_results < 1:
            max_results = 10

        # reset UI for a fresh run
        self.clear_results()
        self.btn_run.set_enabled(False)
        self.btn_stop.set_enabled(True)
        self.var_status.set(rtl("Searching…"))
        self.progress.configure(mode="indeterminate")
        self.progress.pack(side="left", padx=(14, 0), pady=8)
        self.progress.start(12)

        # background worker + cross-thread queue
        self._filepath = path
        self._queue = queue.Queue()
        self._cancel = threading.Event()
        self._worker = threading.Thread(
            target=self._worker_run, args=(path, max_seconds, max_results),
            daemon=True)
        self._worker.start()
        self.after(100, self._poll_queue)

    def on_stop(self):
        if self._cancel is not None:
            self._cancel.set()
        self.btn_stop.set_enabled(False)
        self.var_status.set(rtl("Stopping…"))

    # --- worker thread (NO direct Tk calls — only the queue) -------------
    def _worker_run(self, path, max_seconds, max_results):
        q = self._queue
        cancel = self._cancel
        try:
            transactions, target, k_max = core.read_input(path)
        except SystemExit as e:                       # the engine reports input errors as SystemExit
            q.put(("error", str(e)))
            return
        except Exception as e:
            q.put(("error", f"Error reading the file: {e}"))
            return

        def progress_cb(nodes, n_sol, elapsed):
            q.put(("progress", nodes, n_sol, elapsed))

        try:
            solutions, stats = core.solve(
                transactions, target, k_max, max_seconds,
                cancel_event=cancel, progress_callback=progress_cb,
                max_results=max_results)
        except Exception as e:
            q.put(("error", f"Error during search: {e}"))
            return

        # Writing to Excel is not critical: if the file is open/locked, just warn.
        write_warn = None
        try:
            core.write_output(path, solutions, stats)
        except SystemExit as e:
            write_warn = str(e)
        except Exception as e:
            write_warn = f"Could not write to Excel: {e}"

        q.put(("done", solutions, stats, write_warn))

    # --- poll the queue on the main thread -------------------------------
    def _poll_queue(self):
        try:
            while True:
                msg = self._queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, nodes, n_sol, _elapsed = msg
                    self.var_status.set(rtl(
                        f"Searching… {nodes:,} combinations checked · {n_sol} found"))
                elif kind == "error":
                    self._finish_ui()
                    self.var_status.set(rtl("An error occurred"))
                    messagebox.showerror("Error", msg[1])
                    return
                elif kind == "done":
                    _, solutions, stats, write_warn = msg
                    self._show_results(solutions, stats)
                    self._finish_ui()
                    if write_warn:
                        messagebox.showwarning(
                            "Results shown here",
                            f"{write_warn}\n\n"
                            "The results are shown in the table and kept in memory. "
                            "Close the Excel file and click «Save to Excel» to save them — "
                            "no need to run the search again.")
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _show_results(self, solutions, stats):
        self.clear_results()
        self._solutions = list(solutions)
        self._stats = stats
        for i, sol in enumerate(solutions, start=1):
            txns = core.format_amounts(sol)
            total = f"{sum(sol) / 100:,.2f}"
            self.add_result(i, len(sol), txns, total)
        # Narrowing only makes sense when there is more than one match.
        self.btn_analyze.set_enabled(len(self._solutions) >= 2)
        # Results can always be (re-)saved while they exist.
        self.btn_save.set_enabled(bool(self._solutions))

        status = stats.get("status", "complete")
        if not solutions and status == "complete":
            self.var_status.set(rtl("No exact matches found."))
        else:
            self.var_status.set(rtl(stats.get("status_text", "")))

    def _finish_ui(self):
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.progress.pack_forget()
        self.btn_stop.set_enabled(False)
        self.btn_run.set_enabled(True)

    # --- table helpers ---------------------------------------------------
    def clear_results(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.var_result_count.set(rtl("0 matches"))
        self._solutions = []
        self._stats = None
        if hasattr(self, "btn_analyze"):
            self.btn_analyze.set_enabled(False)
        if hasattr(self, "btn_save"):
            self.btn_save.set_enabled(False)

    def on_save_excel(self):
        """(Re-)write the 'Output' sheet from the in-memory results. Lets the user
        save after a search even if the auto-save failed because the file was
        open — close Excel, click this, done. No need to search again."""
        if not self._solutions or not self._filepath:
            return
        try:
            core.write_output(self._filepath, self._solutions, self._stats)
        except SystemExit as e:                       # file open/locked in Excel
            messagebox.showwarning(
                "Save failed",
                f"{e}\n\nClose the Excel file and click «Save to Excel» again. "
                "The results are kept in memory — no need to search again.")
            return
        except Exception as e:
            messagebox.showerror("Error", f"Could not write to Excel: {e}")
            return
        messagebox.showinfo("Saved", "The results were written to the 'Output' sheet.")

    def open_analysis(self):
        """Open the results-analysis window to narrow many matches by
        eliminating / confirming individual values."""
        if len(self._solutions) >= 2:
            AnalysisDialog(self, self._solutions)

    def add_result(self, idx: int, count: int, transactions: str, total: str):
        tag = "odd" if len(self.tree.get_children()) % 2 else "even"
        # Column order is (num, count, transactions, sum) — match it here.
        self.tree.insert("", "end",
                         values=(idx, count, transactions, total),
                         tags=(tag,))
        self.var_result_count.set(rtl(f"{len(self.tree.get_children())} matches"))


# ---------------------------------------------------------------------------
# Manual-entry dialog: type values directly instead of preparing an Excel file.
# On "load" it writes a nicely-formatted xlsx (via core.write_input) and loads
# it into the main window.
# ---------------------------------------------------------------------------

class ManualEntryDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Manual entry")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(app)

        self.var_target = tk.StringVar()
        self.var_kmax = tk.StringVar(value="8")
        self.row_vars = []
        self.row_entries = []

        outer = ttk.Frame(self, style="App.TFrame", padding=(24, 20, 24, 20))
        outer.pack(fill="both", expand=True)
        self._build_header(outer)
        self._build_params_card(outer)
        self._build_values_card(outer)
        self._build_buttons(outer)

        self._add_value_row()           # start with a single empty row

        # center over the parent window
        self.update_idletasks()
        px, py = app.winfo_rootx(), app.winfo_rooty()
        pw, ph = app.winfo_width(), app.winfo_height()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px + (pw - w) // 2}+{py + max(0, (ph - h) // 2)}")
        self.grab_set()                 # modal

    # -- layout ----------------------------------------------------------
    def _build_header(self, parent):
        wrap = ttk.Frame(parent, style="App.TFrame")
        wrap.pack(fill="x", pady=(0, 16))
        ttk.Label(wrap, text="Manual entry", style="Title.TLabel",
                  anchor="w").pack(side="top", anchor="w")
        ttk.Label(wrap, text="Enter values and a target sum — an Excel file is created automatically",
                  style="Sub.TLabel", anchor="w").pack(side="top", anchor="w",
                                                       pady=(4, 0))

    def _build_params_card(self, parent):
        card = self.app._card(parent)
        card.pack(fill="x", pady=(0, 14))
        inner = tk.Frame(card.body, bg=SURFACE)
        inner.pack(fill="x", padx=20, pady=16)

        # target sum (decimal) on the right
        tgt_wrap = tk.Frame(inner, bg=SURFACE)
        tgt_wrap.pack(side="right")
        ttk.Label(tgt_wrap, text=rtl("Target sum"), style="CardLabel.TLabel",
                  anchor="w").pack(anchor="e")
        field = RoundedFrame(tgt_wrap, radius=RADIUS, fill=SURFACE,
                             border=BORDER, parent_bg=SURFACE, pad=5)
        field.pack(anchor="e", pady=(4, 0))
        ent = tk.Entry(field.body, textvariable=self.var_target, justify="right",
                       relief="flat", bd=0, highlightthickness=0, bg=SURFACE,
                       fg=TEXT, font=self.app.f_value, width=12)
        ent.pack(fill="x", expand=True, padx=8, ipady=5)
        ent.bind("<Return>",
                 lambda e: (self.row_entries[0].focus_set()
                            if self.row_entries else None) or "break")

        # max items (integer stepper) on the left
        km_wrap = tk.Frame(inner, bg=SURFACE)
        km_wrap.pack(side="left")
        ttk.Label(km_wrap, text=rtl("Max items per solution"),
                  style="CardLabel.TLabel", anchor="w").pack(anchor="e")
        NumberStepper(km_wrap, textvariable=self.var_kmax, from_=1, to=20,
                      font=self.app.f_value).pack(anchor="e", pady=(4, 0))

    def _build_values_card(self, parent):
        card = self.app._card(parent)
        card.pack(fill="x", pady=(0, 14))
        inner = tk.Frame(card.body, bg=SURFACE)
        inner.pack(fill="x", padx=20, pady=16)

        ttk.Label(inner, text=rtl("Transaction values (one per row)"),
                  style="CardLabel.TLabel", anchor="w",
                  font=(self.app.f_label[0], 12, "bold")).pack(fill="x")

        # fixed-height scroll area for the dynamic rows (kept modest so the
        # dialog fits small screens; more rows scroll within it)
        area = tk.Frame(inner, bg=SURFACE, height=210, width=460)
        area.pack(fill="x", pady=(10, 0))
        area.pack_propagate(False)

        self._canvas = tk.Canvas(area, bg=SURFACE, highlightthickness=0, bd=0)
        self._vsb = ttk.Scrollbar(area, orient="vertical",
                                  style="App.Vertical.TScrollbar",
                                  command=self._canvas.yview)
        self.rows_frame = tk.Frame(self._canvas, bg=SURFACE)
        self._win = self._canvas.create_window((0, 0), window=self.rows_frame,
                                               anchor="nw")
        self._canvas.configure(yscrollcommand=self._on_rows_scroll)
        self._canvas.pack(side="right", fill="both", expand=True)
        self.rows_frame.bind(
            "<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind(
            "<Configure>",
            lambda e: self._canvas.itemconfigure(self._win, width=e.width))
        self._canvas.bind(
            "<MouseWheel>",
            lambda e: self._canvas.yview_scroll(int(-e.delta / 120), "units"))

    def _build_buttons(self, parent):
        row = ttk.Frame(parent, style="App.TFrame")
        row.pack(fill="x")
        RoundedButton(row, text="Load data", command=self._load,
                      bg=ACCENT, hover_bg=ACCENT_HOV, down_bg=ACCENT_DOWN,
                      font=self.app.f_cta, padx=28, pady=12, radius=RADIUS,
                      parent_bg=BG, min_width=160).pack(side="right")
        RoundedButton(row, text="Cancel", command=self.destroy,
                      bg=BG, hover_bg="#ECECE6", down_bg="#E2E2DC",
                      fg=TEXT, outline=BORDER, outline_width=1,
                      font=self.app.f_cta, padx=24, pady=12, radius=RADIUS,
                      parent_bg=BG, min_width=120).pack(side="right", padx=(0, 12))

    def _on_rows_scroll(self, first, last):
        # auto-hide the rows scrollbar when everything fits
        first, last = float(first), float(last)
        self._vsb.set(first, last)
        if first <= 0.0 and last >= 1.0:
            self._vsb.pack_forget()
        elif not self._vsb.winfo_ismapped():
            self._vsb.pack(side="left", fill="y")

    # -- dynamic rows ----------------------------------------------------
    def _add_value_row(self):
        var = tk.StringVar()
        row = tk.Frame(self.rows_frame, bg=SURFACE)
        row.pack(fill="x", pady=3, padx=2)

        rm = tk.Label(row, text="✕", bg=SURFACE, fg=TEXT_MUTED, cursor="hand2",
                      font=(self.app.f_label[0], 10))
        rm.pack(side="left", padx=(8, 0))
        rm.bind("<Button-1>", lambda e, v=var, r=row: self._remove_value_row(v, r))
        rm.bind("<Enter>", lambda e: rm.config(fg=DANGER))
        rm.bind("<Leave>", lambda e: rm.config(fg=TEXT_MUTED))

        fieldwrap = tk.Frame(row, bg=SURFACE, highlightthickness=1,
                             highlightbackground=BORDER, highlightcolor=ACCENT)
        fieldwrap.pack(side="right", fill="x", expand=True)
        ent = tk.Entry(fieldwrap, textvariable=var, justify="right",
                       relief="flat", bd=0, highlightthickness=0, bg=SURFACE,
                       fg=TEXT, font=self.app.f_value)
        ent.pack(fill="x", expand=True, padx=8, ipady=5)
        ent.bind("<Return>", lambda e, w=ent: self._focus_next_row(w))

        self.row_vars.append(var)
        self.row_entries.append(ent)
        var.trace_add("write", lambda *a, v=var: self._on_value_change(v))
        return ent

    def _focus_next_row(self, ent):
        """Enter -> jump to the next value row (creating one if needed) and
        scroll it into view so it isn't hidden below the visible range."""
        if ent in self.row_entries:
            i = self.row_entries.index(ent)
            if i + 1 >= len(self.row_entries):
                self._add_value_row()
            nxt = self.row_entries[i + 1]
            nxt.focus_set()
            self.after_idle(lambda: self._ensure_visible(nxt))
        return "break"

    def _ensure_visible(self, widget):
        """Scroll the rows canvas so `widget` is fully within the viewport."""
        self.update_idletasks()
        total = self.rows_frame.winfo_height()
        if total <= 1:
            return
        y = widget.winfo_rooty() - self.rows_frame.winfo_rooty()
        h = widget.winfo_height()
        view_top = self._canvas.canvasy(0)
        view_h = self._canvas.winfo_height()
        if y < view_top:
            self._canvas.yview_moveto(max(0.0, y / total))
        elif y + h > view_top + view_h:
            self._canvas.yview_moveto(min(1.0, (y + h - view_h) / total))

    def _on_value_change(self, var):
        # when the LAST row gets a value, open a fresh empty row below it
        if self.row_vars and var is self.row_vars[-1] and var.get().strip():
            self._add_value_row()

    def _remove_value_row(self, var, row):
        if var in self.row_vars:
            i = self.row_vars.index(var)
            self.row_vars.pop(i)
            self.row_entries.pop(i)
        row.destroy()
        if not self.row_vars or self.row_vars[-1].get().strip():
            self._add_value_row()

    # -- load ------------------------------------------------------------
    def _load(self):
        values, bad = [], []
        for var in self.row_vars:
            s = var.get().strip().replace(",", ".")
            if not s:
                continue
            try:
                values.append(float(s))
            except ValueError:
                bad.append(var.get())
        if bad:
            messagebox.showerror("Error", "Invalid values: " + ", ".join(bad),
                                 parent=self)
            return
        if not values:
            messagebox.showwarning("Notice", "Enter at least one value.", parent=self)
            return

        ts = self.var_target.get().strip().replace(",", ".")
        try:
            target = float(ts)        # empty/non-numeric -> ValueError; 0 is allowed (offsets)
        except ValueError:
            messagebox.showerror("Error", "Invalid target sum.", parent=self)
            return

        try:
            k_max = int(float(self.var_kmax.get()))
        except (ValueError, TypeError):
            k_max = 8
        if k_max < 1:
            k_max = 8

        path = filedialog.asksaveasfilename(
            parent=self, title="Save reconciliation file", defaultextension=".xlsx",
            initialfile="reconciliation.xlsx",
            initialdir=os.path.dirname(os.path.abspath(__file__)),
            filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        try:
            core.write_input(path, values, target, k_max)
        except SystemExit as e:
            messagebox.showerror("Error", str(e), parent=self)
            return
        except Exception as e:
            messagebox.showerror("Error", f"Could not write the file: {e}",
                                 parent=self)
            return

        self.app.set_loaded_file(path)
        self.destroy()
        messagebox.showinfo("Loaded", f"{len(values)} values were loaded into the file:\n{path}")


# ---------------------------------------------------------------------------
# Results-analysis dialog: when a search returns many matches, help the user
# converge on the real one. It lists every value with how many of the remaining
# combinations contain it (most frequent first), and lets the user either
#   • Rule out — drop every combination containing it, or
#   • Confirm  — keep only combinations containing it.
# After each step the frequencies recompute over what is left, so the user can
# keep narrowing until a single combination (the likely match) remains.
# This is pure post-processing of the found solutions — the engine is untouched.
# ---------------------------------------------------------------------------

class AnalysisDialog(tk.Toplevel):
    def __init__(self, app, solutions):
        super().__init__(app)
        self.app = app
        self.title("Analyze results")
        self.configure(bg=BG)
        self.transient(app)
        self.minsize(560, 640)

        self.original = list(solutions)
        self.alive = set(range(len(self.original)))   # active solution indices
        self.history = []                             # [(label, value, removed_set)]
        self._warned_locked = False                   # warn once if xlsx is locked

        self.var_remaining = tk.StringVar()
        self.var_actions = tk.StringVar()

        outer = ttk.Frame(self, style="App.TFrame", padding=(24, 20, 24, 20))
        outer.pack(fill="both", expand=True)
        self._build_header(outer)
        self._build_summary(outer)
        self._build_values_card(outer)
        self._build_remaining_card(outer)
        self._build_buttons(outer)

        self._refresh()

        self.update_idletasks()
        px, py = app.winfo_rootx(), app.winfo_rooty()
        pw, ph = app.winfo_width(), app.winfo_height()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{px + (pw - w) // 2}+{py + max(0, (ph - h) // 2)}")
        self.grab_set()

    # -- layout ----------------------------------------------------------
    def _build_header(self, parent):
        wrap = ttk.Frame(parent, style="App.TFrame")
        wrap.pack(fill="x", pady=(0, 14))
        ttk.Label(wrap, text="Analyze results", style="Title.TLabel",
                  anchor="w").pack(side="top", anchor="w")
        ttk.Label(
            wrap,
            text=rtl("Rule out a value you have checked and excluded — every combination "
                     "containing it is removed. Confirm a value that definitely belongs to "
                     "the match — only combinations containing it remain."),
            style="Sub.TLabel", anchor="w", justify="left", wraplength=520
        ).pack(side="top", anchor="w", pady=(4, 0))

    def _build_summary(self, parent):
        card = self.app._card(parent)
        card.pack(fill="x", pady=(0, 12))
        inner = tk.Frame(card.body, bg=SURFACE)
        inner.pack(fill="x", padx=20, pady=14)
        ttk.Label(inner, textvariable=self.var_remaining, style="CardLabel.TLabel",
                  anchor="w", font=(self.app.f_label[0], 13, "bold")
                  ).pack(fill="x")
        ttk.Label(inner, textvariable=self.var_actions, style="CardMuted.TLabel",
                  anchor="w", justify="left", wraplength=500
                  ).pack(fill="x", pady=(6, 0))

    def _build_values_card(self, parent):
        card = self.app._card(parent)
        card.pack(fill="both", expand=True, pady=(0, 12))
        inner = tk.Frame(card.body, bg=SURFACE)
        inner.pack(fill="both", expand=True, padx=20, pady=14)
        ttk.Label(inner, text=rtl("Values across combinations (by frequency)"),
                  style="CardLabel.TLabel", anchor="w",
                  font=(self.app.f_label[0], 12, "bold")).pack(fill="x")

        area = tk.Frame(inner, bg=SURFACE, height=250)
        area.pack(fill="both", expand=True, pady=(10, 0))
        area.pack_propagate(False)

        self._canvas = tk.Canvas(area, bg=SURFACE, highlightthickness=0, bd=0)
        self._vsb = ttk.Scrollbar(area, orient="vertical",
                                  style="App.Vertical.TScrollbar",
                                  command=self._canvas.yview)
        self.rows_frame = tk.Frame(self._canvas, bg=SURFACE)
        self._win = self._canvas.create_window((0, 0), window=self.rows_frame,
                                               anchor="nw")
        self._canvas.configure(yscrollcommand=self._on_rows_scroll)
        self._canvas.pack(side="right", fill="both", expand=True)
        self.rows_frame.bind(
            "<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind(
            "<Configure>",
            lambda e: self._canvas.itemconfigure(self._win, width=e.width))
        # Wheel over the canvas background or the rows frame itself.
        self._canvas.bind("<MouseWheel>", self._on_wheel)
        self.rows_frame.bind("<MouseWheel>", self._on_wheel)

    def _on_wheel(self, e):
        self._canvas.yview_scroll(int(-e.delta / 120), "units")
        return "break"

    def _bind_wheel_recursive(self, widget):
        """Bind the wheel handler on a widget and all its descendants, so the
        list scrolls no matter which row/label/button the pointer is over."""
        widget.bind("<MouseWheel>", self._on_wheel)
        for child in widget.winfo_children():
            self._bind_wheel_recursive(child)

    def _on_rows_scroll(self, first, last):
        first, last = float(first), float(last)
        self._vsb.set(first, last)
        if first <= 0.0 and last >= 1.0:
            self._vsb.pack_forget()
        elif not self._vsb.winfo_ismapped():
            self._vsb.pack(side="left", fill="y")

    def _build_remaining_card(self, parent):
        card = self.app._card(parent)
        card.pack(fill="both", expand=True, pady=(0, 12))
        inner = tk.Frame(card.body, bg=SURFACE)
        inner.pack(fill="both", expand=True, padx=20, pady=14)
        ttk.Label(inner, text=rtl("Remaining combinations"),
                  style="CardLabel.TLabel", anchor="w",
                  font=(self.app.f_label[0], 12, "bold")).pack(fill="x")

        body = tk.Frame(inner, bg=SURFACE, height=150)
        body.pack(fill="both", expand=True, pady=(10, 0))
        body.pack_propagate(False)

        cols = ("transactions", "count")
        self.rtree = ttk.Treeview(body, columns=cols, show="headings",
                                  style="App.Treeview", selectmode="none")
        self.rtree.heading("transactions", text="Transactions", anchor="w")
        self.rtree.heading("count", text="# items", anchor="center")
        self.rtree.column("transactions", width=380, anchor="w", stretch=True)
        self.rtree.column("count", width=110, anchor="center", stretch=False)
        self.rtree.pack(side="right", fill="both", expand=True)

        rsb = ttk.Scrollbar(body, orient="vertical",
                            style="App.Vertical.TScrollbar",
                            command=self.rtree.yview)
        self.rtree.configure(yscrollcommand=rsb.set)
        rsb.pack(side="left", fill="y")

        self.rtree.tag_configure("odd",   background=ROW_ALT)
        self.rtree.tag_configure("even",  background=SURFACE)
        self.rtree.tag_configure("final", background=ROW_SELECT)

    def _build_buttons(self, parent):
        row = ttk.Frame(parent, style="App.TFrame")
        row.pack(fill="x")
        RoundedButton(row, text="Close", command=self.destroy,
                      bg=ACCENT, hover_bg=ACCENT_HOV, down_bg=ACCENT_DOWN,
                      font=self.app.f_cta, padx=28, pady=12, radius=RADIUS,
                      parent_bg=BG, min_width=120).pack(side="right")
        self.btn_undo = RoundedButton(
            row, text="Undo last action", command=self._undo,
            bg=BG, hover_bg="#ECECE6", down_bg="#E2E2DC",
            fg=TEXT, outline=BORDER, outline_width=1,
            disabled_bg=BG, disabled_fg="#C6C6C0",
            font=self.app.f_cta, padx=20, pady=12, radius=RADIUS,
            parent_bg=BG, min_width=0)
        self.btn_undo.pack(side="right", padx=(0, 12))
        self.btn_reset = RoundedButton(
            row, text="Reset", command=self._reset,
            bg=BG, hover_bg="#ECECE6", down_bg="#E2E2DC",
            fg=TEXT, outline=BORDER, outline_width=1,
            disabled_bg=BG, disabled_fg="#C6C6C0",
            font=self.app.f_cta, padx=20, pady=12, radius=RADIUS,
            parent_bg=BG, min_width=0)
        self.btn_reset.pack(side="right", padx=(0, 12))

    # -- state ------------------------------------------------------------
    def _alive_solutions(self):
        return [self.original[i] for i in sorted(self.alive)]

    def _eliminate(self, value):
        removed = {i for i in self.alive if value in self.original[i]}
        if not removed:
            return
        self._apply("Eliminate", value, removed)

    def _confirm(self, value):
        removed = {i for i in self.alive if value not in self.original[i]}
        if not removed:
            return                       # value already in every remaining combo
        self._apply("Confirm", value, removed)

    def _apply(self, action, value, removed):
        self.alive -= removed
        self.history.append((action, value, removed))
        self._refresh()
        self._write_step(action, value)

    def _write_step(self, action, value):
        """Append a NEW sheet documenting this step — the workbook keeps every
        previous step so the full elimination path is preserved. Undo/reset are
        UI-only and never delete already-written sheets."""
        path = getattr(self.app, "_filepath", None)
        if not path or not os.path.isfile(path):
            return
        try:
            core.append_analysis_sheet(path, action, value, self._alive_solutions())
        except SystemExit as e:                       # file open/locked in Excel
            if not self._warned_locked:
                self._warned_locked = True
                messagebox.showwarning(
                    "Results shown here",
                    f"{e}\n\nThe analysis steps were not saved to Excel, but they are shown here in the window.",
                    parent=self)
        except Exception as e:
            if not self._warned_locked:
                self._warned_locked = True
                messagebox.showwarning(
                    "Save failed",
                    f"Could not write the analysis sheet: {e}", parent=self)

    def _undo(self):
        if not self.history:
            return
        _, _, removed = self.history.pop()
        self.alive |= removed
        self._refresh()

    def _reset(self):
        self.alive = set(range(len(self.original)))
        self.history.clear()
        self._refresh()

    # -- rendering --------------------------------------------------------
    def _visible_frequencies(self, alive):
        """Frequencies to offer for action. A *confirmed* value is locked into
        every remaining combination, so there is nothing left to decide about
        it — drop it from the list (it stays visible in the 'Confirmed' summary
        and inside the remaining combinations)."""
        confirmed = {v for a, v, _ in self.history if a == "Confirm"}
        return [(v, c) for v, c in value_frequencies(alive) if v not in confirmed]

    def _refresh(self):
        alive = self._alive_solutions()
        total = len(self.original)
        n = len(alive)
        if n == 1:
            self.var_remaining.set(rtl(f"1 of {total} combinations remain — the likely match"))
        elif n == 0:
            self.var_remaining.set(rtl(f"No combinations remain (of {total})"))
        else:
            self.var_remaining.set(rtl(f"{n} of {total} combinations remain"))

        if self.history:
            elim = [_fmt_money(v) for lbl, v, _ in self.history if lbl == "Eliminate"]
            conf = [_fmt_money(v) for lbl, v, _ in self.history if lbl == "Confirm"]
            parts = []
            if elim:
                parts.append("Ruled out: " + ", ".join(elim))
            if conf:
                parts.append("Confirmed: " + ", ".join(conf))
            self.var_actions.set(rtl("    ·    ".join(parts)))
        else:
            self.var_actions.set(rtl("No actions yet"))

        # value rows (confirmed values are locked in -> excluded from the list)
        for w in self.rows_frame.winfo_children():
            w.destroy()
        freqs = self._visible_frequencies(alive)
        if not freqs:
            if n == 0:
                msg = "All combinations were ruled out — the true match is likely not among the results found."
            elif total:
                msg = "All values in the remaining combinations are already confirmed."
            else:
                msg = "No data to analyze."
            ttk.Label(self.rows_frame, text=rtl(msg), style="CardMuted.TLabel",
                      anchor="w", justify="left", wraplength=460
                      ).pack(fill="x", pady=8, padx=2)
        else:
            for value, count in freqs:
                self._make_value_row(value, count, n)
        # Re-arm wheel scrolling over the just-rebuilt rows and their children.
        self._bind_wheel_recursive(self.rows_frame)

        # remaining combinations
        for iid in self.rtree.get_children():
            self.rtree.delete(iid)
        final = (n == 1)
        for k, sol in enumerate(alive):
            tag = "final" if final else ("odd" if k % 2 else "even")
            self.rtree.insert("", "end",
                              values=(core.format_amounts(sol), len(sol)),
                              tags=(tag,))

        self.btn_undo.set_enabled(bool(self.history))
        self.btn_reset.set_enabled(bool(self.history))

    def _make_value_row(self, value, count, n_alive):
        row = tk.Frame(self.rows_frame, bg=SURFACE)
        row.pack(fill="x", pady=3, padx=2)

        # LTR: value (bold) on the LEFT, then its frequency, then the action
        # buttons ("Confirm" / "Rule out") on the RIGHT.
        ttk.Label(row, text=rtl(_fmt_money(value)), style="CardLabel.TLabel",
                  anchor="w", font=(self.app.f_value[0], 12, "bold")
                  ).pack(side="left", padx=(0, 10))
        ttk.Label(row, text=rtl(f"in {count} of {n_alive} combinations"),
                  style="CardMuted.TLabel", anchor="w"
                  ).pack(side="left")

        RoundedButton(row, text="Rule out", command=lambda v=value: self._eliminate(v),
                      bg=SURFACE, hover_bg="#FBEFEF", down_bg="#F6E0E0",
                      fg=DANGER, outline="#D7A2A2", outline_width=1,
                      font=self.app.f_btn, padx=14, pady=6, radius=RADIUS,
                      parent_bg=SURFACE, min_width=0).pack(side="right", padx=(8, 0))
        RoundedButton(row, text="Confirm", command=lambda v=value: self._confirm(v),
                      bg=SURFACE, hover_bg="#EAF1FB", down_bg="#DCE8FA",
                      fg=ACCENT, outline="#A8C0EC", outline_width=1,
                      font=self.app.f_btn, padx=14, pady=6, radius=RADIUS,
                      parent_bg=SURFACE, min_width=0).pack(side="right", padx=(8, 0))


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = ReconciliationApp()
    app.mainloop()
