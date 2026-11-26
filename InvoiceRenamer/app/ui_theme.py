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
    style.configure("TButton", padding=(10, 5), background=COLORS["panel"], bordercolor=COLORS["line"])
    style.map("TButton", background=[("active", COLORS["gray_light"])])
    for name, color, light in (("Primary", "blue", "blue_light"), ("Success", "green", "green_light"),
                               ("Danger", "red", "red_light")):
        style.configure(f"{name}.TButton", background=COLORS[color], foreground="#ffffff",
                        bordercolor=COLORS[color], font=FONT_BOLD, padding=(12, 6))
        style.map(f"{name}.TButton", background=[("active", COLORS[color]), ("disabled", COLORS["line"])],
                  foreground=[("disabled", COLORS["muted"])])
    style.configure("TMenubutton", padding=(10, 5), background=COLORS["panel"])
    style.configure("Chip.TRadiobutton", padding=(10, 3), background=COLORS["bg"])
    style.configure("Treeview", rowheight=26, background=COLORS["panel"], fieldbackground=COLORS["panel"],
                    bordercolor=COLORS["line"])
    style.configure("Treeview.Heading", font=FONT_BOLD, background="#fafbfc", foreground=COLORS["muted"],
                    padding=(6, 4))
    style.map("Treeview", background=[("selected", COLORS["blue"])], foreground=[("selected", "#ffffff")])
    style.configure("TPanedwindow", background=COLORS["bg"])
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
