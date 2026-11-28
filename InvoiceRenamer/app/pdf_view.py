"""A PDF page viewer used inside the program's panels.

Page buttons (◀ 1/3쪽 ▶), zoom (− 100% +, 폭 맞춤, 쪽 맞춤), Ctrl + mouse wheel to zoom, wheel to scroll,
orange boxes around found values, and optionally dragging a box with the mouse to pick the text inside it.
"""

import base64
import tkinter as tk
from tkinter import ttk

import pymupdf

from ui_theme import COLORS, FONT_SMALL

ZOOM_STEPS = (0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0)
HIGHLIGHT = "#e8620d"


def open_document(path):
    """PDF or photo as a PDF document (photos are converted), via the same reader the rest of the program uses."""
    from invoice_renamer import open_evidence
    return open_evidence(path)


class PdfView(tk.Frame):
    """view = PdfView(parent); view.pack(fill="both", expand=True); view.load(path, page=0, boxes={0: [rect]})

    on_pick(page index, pymupdf.Rect, text): when given, dragging with the left mouse button draws a box and
    hands over the words inside it (for filling a missing value)."""

    def __init__(self, parent, on_pick=None, background=None, compact=False, on_menu=None):
        super().__init__(parent, background=background or COLORS["panel"])
        self.on_pick = on_pick
        self.on_menu = on_menu  # on_menu(event, page index, word text, word Rect): right click on a word
        self.document = None
        self.path = None
        self.page = 0
        self.zoom = 1.0
        self.fit = "width"  # "width", "page" or None (fixed zoom)
        self.boxes = {}
        self.focus_box = None
        self.image = None
        self.scale = 1.0
        self.offset = (0, 0)
        self._drag = None
        self._after = None
        self._size = None
        bar = tk.Frame(self, background=self["background"])
        bar.pack(side="top", fill="x", pady=(0, 4))
        style = "Small.TButton"
        self.prev_button = ttk.Button(bar, text="◀", width=3, style=style, command=lambda: self.go(-1))
        self.prev_button.pack(side="left")
        self.page_text = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.page_text, background=self["background"], font=FONT_SMALL,
                 width=9).pack(side="left")
        self.next_button = ttk.Button(bar, text="▶", width=3, style=style, command=lambda: self.go(1))
        self.next_button.pack(side="left")
        tk.Frame(bar, width=12, background=self["background"]).pack(side="left")
        ttk.Button(bar, text="−", width=3, style=style, command=lambda: self.step_zoom(-1)).pack(side="left")
        self.zoom_text = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.zoom_text, background=self["background"], font=FONT_SMALL,
                 width=7).pack(side="left")
        ttk.Button(bar, text="+", width=3, style=style, command=lambda: self.step_zoom(1)).pack(side="left")
        if not compact:
            ttk.Button(bar, text="폭 맞춤", style=style, command=lambda: self.set_fit("width")).pack(side="left", padx=(6, 0))
            ttk.Button(bar, text="쪽 맞춤", style=style, command=lambda: self.set_fit("page")).pack(side="left", padx=(4, 0))
        self.hint = tk.StringVar(value="Ctrl+휠: 확대·축소" + (" · 끌어서 값 고르기" if on_pick else ""))
        tk.Label(bar, textvariable=self.hint, background=self["background"], foreground=COLORS["muted"],
                 font=FONT_SMALL).pack(side="left", padx=8)
        area = tk.Frame(self, background=self["background"])
        area.pack(side="top", fill="both", expand=True)
        self.canvas = tk.Canvas(area, background="#e9ecef", highlightthickness=0)
        down = ttk.Scrollbar(area, command=self.canvas.yview)
        across = ttk.Scrollbar(area, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=down.set, xscrollcommand=across.set)
        down.pack(side="right", fill="y")
        across.pack(side="bottom", fill="x")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", self._resized)
        for widget in (self.canvas,):
            widget.bind("<Control-MouseWheel>", self._wheel_zoom)
            widget.bind("<MouseWheel>", self._wheel)
            widget.bind("<Shift-MouseWheel>", self._wheel_across)
            widget.bind("<Control-Button-4>", lambda event: self.step_zoom(1))
            widget.bind("<Control-Button-5>", lambda event: self.step_zoom(-1))
            widget.bind("<Button-4>", lambda event: self.canvas.yview_scroll(-3, "units"))
            widget.bind("<Button-5>", lambda event: self.canvas.yview_scroll(3, "units"))
        if on_pick:
            self.canvas.configure(cursor="crosshair")
            self.canvas.bind("<ButtonPress-1>", self._drag_start)
            self.canvas.bind("<B1-Motion>", self._drag_move)
            self.canvas.bind("<ButtonRelease-1>", self._drag_end)
        if on_menu:
            self.canvas.bind("<Button-3>", self._menu)
        self.bind("<Destroy>", self._closed, add="+")

    # Loading -------------------------------------------------------------
    def load(self, path, page=0, boxes=None, focus=None):
        """Show a file. boxes: {page index: [Rect, ...]} drawn in orange; focus: a Rect on `page` scrolled into view."""
        if self.document is not None and self.path != path:
            self.document.close()
            self.document = None
        if self.document is None:
            try:
                self.document = open_document(path)
            except (OSError, RuntimeError, ValueError) as error:
                self.path = None
                self.message(f"미리보기를 열 수 없어요.\n{error}")
                return
        self.path = path
        self.boxes = boxes or {}
        self.focus_box = focus
        self.page = max(0, min(page, len(self.document) - 1)) if len(self.document) else 0
        self.render()

    def message(self, text):
        self.canvas.delete("all")
        self.canvas.create_text(14, 14, text=text, anchor="nw", fill=COLORS["ink"])
        self.page_text.set("")

    def show(self, page, focus=None, boxes=None):
        if boxes is not None:
            self.boxes = boxes
        self.focus_box = focus
        if self.document is not None and len(self.document):
            self.page = max(0, min(page, len(self.document) - 1))
            self.render()

    def go(self, step):
        if self.document is None or not len(self.document):
            return
        target = self.page + step
        if 0 <= target < len(self.document):
            self.page = target
            self.focus_box = None
            self.render()
            self.canvas.yview_moveto(0 if step > 0 else 0)

    # Zoom ----------------------------------------------------------------
    def set_fit(self, mode):
        self.fit = mode
        self.render()

    def step_zoom(self, direction):
        current = self.scale_for_page() if self.fit else self.zoom
        if direction > 0:
            self.zoom = next((step for step in ZOOM_STEPS if step > current + 0.01), ZOOM_STEPS[-1])
        else:
            self.zoom = next((step for step in reversed(ZOOM_STEPS) if step < current - 0.01), ZOOM_STEPS[0])
        self.fit = None
        self.render()

    def scale_for_page(self):
        if self.document is None or not len(self.document):
            return 1.0
        rect = self.document[self.page].rect
        width = max(100, self.canvas.winfo_width() - 24)
        height = max(100, self.canvas.winfo_height() - 24)
        if self.fit == "page":
            return min(width / max(1, rect.width), height / max(1, rect.height))
        if self.fit == "width":
            return width / max(1, rect.width)
        return self.zoom

    def _wheel_zoom(self, event):
        self.step_zoom(1 if event.delta > 0 else -1)
        return "break"

    def _wheel(self, event):
        self.canvas.yview_scroll(-3 if event.delta > 0 else 3, "units")
        return "break"

    def _wheel_across(self, event):
        self.canvas.xview_scroll(-3 if event.delta > 0 else 3, "units")
        return "break"

    def _resized(self, event):
        size = (event.width, event.height)
        if size == self._size:
            return
        self._size = size
        if self.fit and self.document is not None:
            if self._after:
                self.after_cancel(self._after)
            self._after = self.after(120, self.render)

    # Drawing -------------------------------------------------------------
    def render(self):
        self._after = None
        if self.document is None or not len(self.document):
            return
        page = self.document[self.page]
        scale = max(0.1, min(5.0, self.scale_for_page()))
        self.scale = scale
        try:
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            self.image = tk.PhotoImage(data=base64.b64encode(pixmap.tobytes("png")).decode("ascii"))
        except (RuntimeError, ValueError, tk.TclError) as error:
            self.message(f"미리보기를 만들 수 없어요.\n{error}")
            return
        self.canvas.delete("all")
        margin = 12
        width = max(self.canvas.winfo_width(), self.image.width() + 2 * margin)
        x = max(margin, (width - self.image.width()) // 2)
        self.offset = (x, margin)
        self.canvas.create_image(x, margin, image=self.image, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, width, self.image.height() + 2 * margin))
        matrix = page.rotation_matrix * pymupdf.Matrix(scale, scale)
        for rect in self.boxes.get(self.page, []):
            shown = pymupdf.Rect(rect) * matrix
            self.canvas.create_rectangle(x + shown.x0 - 3, margin + shown.y0 - 3, x + shown.x1 + 3, margin + shown.y1 + 3,
                                         outline=HIGHLIGHT, width=2)
        count = len(self.document)
        self.page_text.set(f"{self.page + 1}/{count}쪽")
        self.prev_button.state(["!disabled"] if self.page > 0 else ["disabled"])
        self.next_button.state(["!disabled"] if self.page < count - 1 else ["disabled"])
        self.zoom_text.set(f"{round(scale * 100)}%")
        if self.focus_box is not None:
            shown = pymupdf.Rect(self.focus_box) * matrix
            total = self.image.height() + 2 * margin
            visible = max(1, self.canvas.winfo_height())
            top = max(0, margin + shown.y0 - visible / 3)
            self.canvas.yview_moveto(top / max(1, total))

    # Picking a value -----------------------------------------------------
    def _drag_start(self, event):
        self._drag = (self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        self.canvas.delete("pick")

    def _drag_move(self, event):
        if not self._drag:
            return
        self.canvas.delete("pick")
        x0, y0 = self._drag
        self.canvas.create_rectangle(x0, y0, self.canvas.canvasx(event.x), self.canvas.canvasy(event.y),
                                     outline=COLORS["blue"], width=2, dash=(4, 2), tags="pick")

    def _drag_end(self, event):
        if not self._drag or self.document is None:
            return
        x0, y0 = self._drag
        x1, y1 = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        self._drag = None
        if abs(x1 - x0) < 4 or abs(y1 - y0) < 4:
            self.canvas.delete("pick")
            return
        page = self.document[self.page]
        left, top = self.offset
        shown = pymupdf.Rect(min(x0, x1) - left, min(y0, y1) - top, max(x0, x1) - left, max(y0, y1) - top)
        inverse = ~(page.rotation_matrix * pymupdf.Matrix(self.scale, self.scale))
        area = shown * inverse
        area.normalize()
        text = self.words_in(page, area)
        if self.on_pick:
            self.on_pick(self.page, area, text)

    def page_point(self, event):
        """The point on the PDF page (unrotated coordinates) under a mouse event."""
        left, top = self.offset
        x, y = self.canvas.canvasx(event.x) - left, self.canvas.canvasy(event.y) - top
        return pymupdf.Point(x, y) * ~(self.document[self.page].rotation_matrix * pymupdf.Matrix(self.scale, self.scale))

    def _menu(self, event):
        if self.document is None or not self.on_menu:
            return
        page = self.document[self.page]
        point = self.page_point(event)
        words = [word for word in page.get_text("words") if pymupdf.Rect(word[:4]).contains(point)]
        if not words:
            return
        word = words[0]
        # the whole run of words on that line that touch each other (e.g. "1.234,56 EUR" or "RE-2026-001")
        line = sorted((w for w in page.get_text("words") if (w[5], w[6]) == (word[5], word[6])), key=lambda w: w[0])
        self.on_menu(event, self.page, word[4], pymupdf.Rect(word[:4]), " ".join(w[4] for w in line))

    @staticmethod
    def label_near(page, area):
        """Words that name a value: the words to the left on the same line, else the line just above it."""
        words = page.get_text("words")
        middle = (area.y0 + area.y1) / 2
        left = [w for w in words if w[1] <= middle <= w[3] and w[2] <= area.x0 + 1]
        left.sort(key=lambda w: w[0])
        if left:
            run = [left[-1]]
            for word in reversed(left[:-1]):  # stop at a wide gap: that is another column
                if run[0][0] - word[2] > 40:
                    break
                run.insert(0, word)
            return " ".join(w[4] for w in run[-4:]).rstrip(":：#").strip()
        above = [w for w in words if w[3] <= area.y0 + 1 and w[2] >= area.x0 - 5 and w[0] <= area.x1 + 5]
        if not above:
            return ""
        bottom = max(w[3] for w in above)
        line = sorted((w for w in above if abs(w[3] - bottom) < 3), key=lambda w: w[0])
        return " ".join(w[4] for w in line[-4:]).rstrip(":：#").strip()

    @staticmethod
    def words_in(page, area):
        """Words whose middle lies inside the box, in reading order (OCR text is not available here)."""
        words = [word for word in page.get_text("words") if pymupdf.Rect(word[:4]).intersects(area)
                 and area.contains(pymupdf.Point((word[0] + word[2]) / 2, (word[1] + word[3]) / 2))]
        words.sort(key=lambda word: (word[5], word[6], word[7]))
        # Some PDFs print the same text twice on the same spot (fake bold, shadow): keep one copy.
        kept = []
        for word in words:
            if any(word[4] == other[4] and abs(word[0] - other[0]) < 2 and abs(word[1] - other[1]) < 2 for other in kept):
                continue
            kept.append(word)
        return " ".join(word[4] for word in kept)

    def _closed(self, event):
        if event.widget is self and self.document is not None:
            self.document.close()
            self.document = None
