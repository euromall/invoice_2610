"""Colours, ttk styles and status tags shared by every screen."""

import tkinter as tk
from tkinter import ttk


COLORS = {
    "bg": "#f4f6f9", "panel": "#ffffff", "line": "#dfe4ea", "ink": "#1f2933", "muted": "#6b7785",
    "navy": "#24394d", "navy_light": "#3d5468", "blue": "#2f6fde", "blue_light": "#e8f0fd",
    "green": "#1f9d55", "green_light": "#e6f6ec", "amber": "#b7791f", "amber_light": "#fff4dc",
    "red": "#c53030", "red_light": "#fde8e8", "violet": "#6b46c1", "violet_light": "#efe9fb",
    "gray_light": "#eef1f4",
}
FONT = ("Malgun Gothic", 10)
FONT_BOLD = ("Malgun Gothic", 10, "bold")
FONT_TITLE = ("Malgun Gothic", 14, "bold")
FONT_SMALL = ("Malgun Gothic", 9)

TAGS = {
    "ok": (COLORS["green_light"], COLORS["green"]),
    "warn": (COLORS["amber_light"], COLORS["amber"]),
    "bad": (COLORS["red_light"], COLORS["red"]),
    "transfer": (COLORS["violet_light"], COLORS["violet"]),
    "muted": (COLORS["gray_light"], COLORS["muted"]),
}


def apply_style(root):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    root.configure(background=COLORS["bg"])
    root.option_add("*Font", FONT)
    for name, value in (("*Text.relief", "flat"), ("*Text.background", COLORS["panel"]),
                        ("*Text.highlightThickness", 1), ("*Text.highlightBackground", COLORS["line"]),
                        ("*Text.highlightColor", COLORS["blue"]), ("*Text.padX", 8), ("*Text.padY", 6),
                        ("*Text.borderWidth", 0), ("*Listbox.relief", "flat"), ("*Listbox.highlightThickness", 1),
                        ("*Listbox.highlightBackground", COLORS["line"])):
        root.option_add(name, value)
    style.configure(".", font=FONT, background=COLORS["bg"], foreground=COLORS["ink"])
    style.configure("TFrame", background=COLORS["bg"])
    style.configure("Panel.TFrame", background=COLORS["panel"])
    style.configure("Side.TFrame", background=COLORS["panel"])
    style.configure("Header.TFrame", background=COLORS["navy"])
    style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["ink"])
    style.configure("Muted.TLabel", foreground=COLORS["muted"])
    style.configure("Title.TLabel", font=FONT_TITLE)
    style.configure("Panel.TLabel", background=COLORS["panel"])
    style.configure("Header.TLabel", background=COLORS["navy"], foreground="#ffffff", font=FONT_BOLD)
    style.configure("Status.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], padding=(12, 6))
    style.configure("TCheckbutton", background=COLORS["bg"])
    style.configure("Panel.TCheckbutton", background=COLORS["panel"])
    style.configure("TButton", padding=(10, 5), background=COLORS["panel"], bordercolor=COLORS["line"])
    style.map("TButton", background=[("active", COLORS["gray_light"])])
    for name, color, light in (("Primary", "blue", "blue_light"), ("Success", "green", "green_light"),
                               ("Danger", "red", "red_light")):
        style.configure(f"{name}.TButton", background=COLORS[color], foreground="#ffffff",
                        bordercolor=COLORS[color], font=FONT_BOLD, padding=(12, 6))
        style.map(f"{name}.TButton", background=[("active", COLORS[color]), ("disabled", COLORS["line"])],
                  foreground=[("disabled", COLORS["muted"])])
    style.configure("TMenubutton", padding=(10, 5), background=COLORS["panel"])
    style.configure("Small.TButton", padding=(7, 4), width=-4, background=COLORS["panel"], bordercolor=COLORS["line"])
    style.configure("Chip.TRadiobutton", padding=(10, 3), background=COLORS["bg"])
    style.configure("Treeview", rowheight=26, background=COLORS["panel"], fieldbackground=COLORS["panel"],
                    bordercolor=COLORS["line"])
    style.configure("Treeview.Heading", font=FONT_BOLD, background="#fafbfc", foreground=COLORS["muted"],
                    padding=(6, 4))
    style.map("Treeview", background=[("selected", COLORS["blue"])], foreground=[("selected", "#ffffff")])
    style.configure("TPanedwindow", background=COLORS["bg"])
    style.configure("TEntry", padding=(6, 4), fieldbackground=COLORS["panel"], bordercolor=COLORS["line"])
    style.configure("TCombobox", padding=(6, 3), bordercolor=COLORS["line"])
    style.map("TCombobox", fieldbackground=[("readonly", COLORS["panel"])], foreground=[("readonly", COLORS["ink"])])
    style.configure("Card.TFrame", background=COLORS["panel"], bordercolor=COLORS["line"], relief="solid", borderwidth=1)
    style.configure("Card.TLabel", background=COLORS["panel"])
    style.configure("CardTitle.TLabel", background=COLORS["panel"], foreground=COLORS["ink"], font=FONT_BOLD)
    style.configure("CardHint.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=FONT_SMALL)
    style.configure("Summary.TLabel", background=COLORS["blue_light"], foreground=COLORS["ink"], font=FONT_BOLD,
                    padding=(12, 8))
    style.configure("Chip.TButton", padding=(8, 2), font=FONT_SMALL, background=COLORS["gray_light"])
    return style


def configure_tags(tree):
    for tag, (background, foreground) in TAGS.items():
        tree.tag_configure(tag, background=background, foreground=foreground)


def transaction_tag(status, follow_up=""):
    if follow_up in ("세무사 확인 필요", "증빙 확보 불가"):
        return "warn"
    if status == "금액 대조 완료":
        return "ok"
    if status in ("내부이체 확인", "전액 환불 확인") or status.startswith("일부 환불"):
        return "transfer"
    if status in ("증빙 미연결", "연결 PDF 위치 확인"):
        return "bad"
    return "warn"


def more_menu(parent, label, items):
    """A '더보기' button that opens a drop-down list of (label, command)."""
    button = ttk.Menubutton(parent, text=label)
    menu = tk.Menu(button, tearoff=False)
    for text, command in items:
        if text == "-":
            menu.add_separator()
        else:
            menu.add_command(label=text, command=command)
    button["menu"] = menu
    return button


PRIMARY_ENDINGS = ("저장", "적용", "사용", "연결")


class InlineDialog(tk.Frame):
    """A detail panel that opens inside the main window (right side) instead of a separate window.

    It accepts the Toplevel calls the screens already use (title, geometry, transient, grab_set,
    protocol, destroy, bind), so a screen only swaps tk.Toplevel for InlineDialog.
    """

    def __init__(self, parent, **_options):
        root = parent.winfo_toplevel()
        super().__init__(root, background=COLORS["panel"], highlightthickness=1,
                         highlightbackground="#9aa3ad", highlightcolor="#9aa3ad")
        self._close_command = None
        self._width = None
        self._tag = f"InlineDialog{id(self)}"
        self._previous_focus = root.focus_get()
        header = tk.Frame(self, background=COLORS["navy"], padx=14, pady=9)
        header.pack(side="top", fill="x")
        self._title = tk.Label(header, text="", background=COLORS["navy"], foreground="#ffffff",
                               font=("Malgun Gothic", 12, "bold"), anchor="w")
        self._title.pack(side="left", fill="x", expand=True)
        close = tk.Label(header, text="✕  닫기", background=COLORS["navy"], foreground="#d5dee8",
                         cursor="hand2", font=FONT)
        close.pack(side="right")
        close.bind("<Button-1>", lambda event: self._request_close())
        self.configure(background=COLORS["bg"])
        self._area = getattr(root, "dialog_area", None) or root
        self._place()
        self.lift()
        self.bind_class(self._tag, "<Escape>", lambda event: self._request_close())
        self.after(30, self._finish)

    # Toplevel-compatible calls ------------------------------------------
    def title(self, text=None):
        if text is None:
            return self._title.cget("text")
        self._title.configure(text=text)
        return None

    def geometry(self, spec=None):
        if spec:
            size = spec.split("+")[0]
            if "x" in size:
                self._width = int(size.split("x")[0])
                self._place()
        return ""

    def transient(self, _master=None):
        return None

    def resizable(self, *_args):
        return None

    def minsize(self, *_args):
        return None

    def protocol(self, name, command=None):
        if name == "WM_DELETE_WINDOW":
            self._close_command = command

    def bind(self, sequence=None, func=None, add=None):
        if sequence and func:
            self.bind_class(self._tag, sequence, func, add)
            self.after_idle(self._retag)
            return None
        return super().bind(sequence, func, add)

    def destroy(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        previous = self._previous_focus
        super().destroy()
        try:
            if previous is not None and previous.winfo_exists():
                previous.focus_set()
        except (tk.TclError, KeyError):
            pass

    # Layout --------------------------------------------------------------
    def _available_width(self):
        width = self._area.winfo_width()
        if width <= 1:
            self._area.update_idletasks()
            width = self._area.winfo_width()
        return max(420, width - 12)

    def _place(self):
        width = min(self._width or 640, self._available_width())
        self.place(in_=self._area, relx=1.0, x=-6, y=6, anchor="ne", relheight=1.0, height=-12, width=width)

    def _finish(self):
        if not self.winfo_exists():
            return
        if self._width is None:
            self.update_idletasks()
            self._width = max(520, self.winfo_reqwidth() + 24)
            self._place()
        self._style_buttons(self)
        self._retag()
        self.focus_set()

    def _style_buttons(self, widget):
        for child in widget.winfo_children():
            if isinstance(child, ttk.Button) and str(child.cget("style")) in ("", "TButton"):
                text = str(child.cget("text"))
                if (text.endswith(PRIMARY_ENDINGS) or text == "확인") and "해제" not in text:
                    child.configure(style="Primary.TButton")
            self._style_buttons(child)

    def _retag(self):
        if not self.winfo_exists():
            return
        stack = [self]
        while stack:
            widget = stack.pop()
            tags = widget.bindtags()
            if self._tag not in tags:
                widget.bindtags((self._tag, *tags))
            stack.extend(widget.winfo_children())

    def _request_close(self):
        if self._close_command:
            self._close_command()
        else:
            self.destroy()


def show_notice(parent, message, title="안내", kind="info"):
    """A small pop-up card in the middle of the main window. Enter, Esc or 확인 closes it."""
    root = parent.winfo_toplevel()
    accent = {"info": COLORS["blue"], "warn": COLORS["amber"], "error": COLORS["red"]}.get(kind, COLORS["blue"])
    card = tk.Frame(root, background=COLORS["panel"], highlightthickness=1, highlightbackground="#9aa3ad",
                    highlightcolor="#9aa3ad")
    tk.Frame(card, background=accent, width=6).pack(side="left", fill="y")
    body = tk.Frame(card, background=COLORS["panel"], padx=18, pady=14)
    body.pack(side="left", fill="both", expand=True)
    tk.Label(body, text=title, font=FONT_BOLD, background=COLORS["panel"], foreground=accent,
             anchor="w").pack(fill="x")
    tk.Label(body, text=message, font=FONT, background=COLORS["panel"], foreground=COLORS["ink"],
             wraplength=380, justify="left", anchor="w").pack(fill="x", pady=(6, 12))
    previous = root.focus_get()

    def close(_event=None):
        try:
            card.grab_release()
        except tk.TclError:
            pass
        card.destroy()
        try:
            if previous is not None and previous.winfo_exists():
                previous.focus_set()
        except (tk.TclError, KeyError):
            pass

    button = ttk.Button(body, text="확인", style="Primary.TButton", command=close)
    button.pack(anchor="e")
    card.place(relx=0.5, rely=0.42, anchor="center")
    card.lift()
    for widget in (card, body, button):
        widget.bind("<Return>", close)
        widget.bind("<Escape>", close)
    card.after_idle(button.focus_set)
    try:
        card.grab_set()
    except tk.TclError:
        pass
    return card
