"""Main window: five steps on the left, month progress on top, one page at a time."""

import os
from pathlib import Path
import sys
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from release_info import VERSION
from ui_theme import COLORS, FONT, FONT_BOLD, FONT_SMALL, apply_style, attach_help


APP_TITLE = "InvoiceRenamer · 독일 월마감"
STEPS = (
    (1, "증빙 모으기·정리"),
    (2, "명세서 불러오기"),
    (3, "증빙 매칭"),
    (4, "누락 점검"),
    (5, "세무사 제출"),
)
_MUTEX = None


def window_title(practice=False):
    return ("[연습 모드] " if practice else "") + f"{APP_TITLE} v{VERSION}"


def acquire_single_instance():
    """True when this is the only running copy. Otherwise brings the running window forward (Windows)."""
    global _MUTEX
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        _MUTEX = kernel32.CreateMutexW(None, False, "Local\\InvoiceRenamer.SingleInstance")
        if kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
            return True
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, window_title())
        if hwnd:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
        return False
    except (AttributeError, OSError):
        return True


def close_splash():
    try:
        import pyi_splash  # only present in the EXE built with --splash
    except ImportError:
        return
    try:
        pyi_splash.close()
    except RuntimeError:
        pass


def splash_text(text):
    try:
        import pyi_splash
        pyi_splash.update_text(text)
    except (ImportError, RuntimeError):
        pass


def bring_to_front(root):
    root.deiconify()
    root.lift()
    root.attributes("-topmost", True)
    root.after(400, lambda: root.attributes("-topmost", False))
    root.focus_force()


class Shell:
    def __init__(self, root, practice_folder=None):
        from invoice_renamer import App
        from monthly_close import MonthlyClose

        self.root = root
        self.practice_folder = practice_folder
        apply_style(root)
        root.title(window_title(bool(practice_folder)))
        root.geometry("1320x820")
        root.minsize(1100, 680)
        splash_text("화면을 준비하고 있어요…")
        self.build_header()
        body = ttk.Frame(root)
        body.pack(fill="both", expand=True)
        self.sidebar = tk.Frame(body, background=COLORS["panel"], width=230, highlightthickness=1,
                                highlightbackground=COLORS["line"])
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self.content = ttk.Frame(body)
        self.content.pack(side="left", fill="both", expand=True)
        # v1.13: outside changes (Dropbox, Explorer) are announced here instead of being read at once
        self.change_bar = tk.Frame(self.content, background=COLORS["amber_light"], padx=14, pady=6)
        self.change_text = tk.StringVar()
        tk.Label(self.change_bar, textvariable=self.change_text, background=COLORS["amber_light"],
                 foreground=COLORS["ink"]).pack(side="left")
        ttk.Button(self.change_bar, text="나중에", style="Small.TButton", command=self.later).pack(side="right")
        ttk.Button(self.change_bar, text="지금 읽기", style="Small.TButton",
                   command=lambda: self.check_folder(force=True)).pack(side="right", padx=6)
        root.dialog_area = self.content  # detail panels open over this area
        self.status_bar = ttk.Label(root, style="Status.TLabel")
        self.status_bar.pack(side="bottom", fill="x", before=body)

        self.page1 = ttk.Frame(self.content)
        self.page2 = ttk.Frame(self.content)
        self.app = App(root, practice_folder, container=self.page1, on_change=self.update_overview)
        self.app.on_loaded = self.remember_folder
        root.report_callback_exception = self.app.report_callback_error
        splash_text("작업 기록을 불러오고 있어요…")
        try:
            self.close = MonthlyClose(root, None, practice_folder, container=self.page2,
                                      on_change=self.update_overview)
            self.close.navigate = self.show
            if not practice_folder and self.close.folder is None and self.app.folder is not None:
                self.close.folder = self.app.folder  # statements are filed into the work folder; PDFs are read in step 3
            self.app.on_work_folder = self.work_folder_changed
            self.app.on_manage_reasons = self.close.manage_reasons
            self.app.on_manage_mail = self.close.manage_mailboxes
            self.app.on_account_order = self.close.edit_account_order
            self.app.on_import_statements = lambda paths: (self.show(2), self.close.open_batch([str(path) for path in paths]))
            self.close.on_loaded = self.remember_folder
        except RuntimeError:
            self.close = None
        self.build_sidebar()
        self.current = None
        self.show(1)
        root.after(300, lambda: attach_help(root))  # mouse-over explanations
        self.update_overview()
        # v1.10.1: files put into the work folder with Explorer are picked up when the window is used again
        self.folder_seen = None
        self.folder_check_job = None
        if not practice_folder:
            root.after(1500, self.remember_folder)
            root.bind("<FocusIn>", self.schedule_folder_check, add="+")
            root.bind("<F5>", lambda event: self.check_folder(force=True), add="+")

    # Work folder watching ---------------------------------------------
    def folder_signature(self):
        folder = self.app.folder
        if folder is None or not Path(folder).is_dir():
            return None
        from invoice_renamer import evidence_in
        try:
            return frozenset((str(path.relative_to(folder)), path.stat().st_size, int(path.stat().st_mtime))
                             for path in evidence_in(folder))
        except OSError:
            return None

    def remember_folder(self):
        """The program read the folder itself (or changed it): what is there now counts as seen."""
        if getattr(self, "folder_seen", None) is None and not hasattr(self, "folder_check_job"):
            return  # still starting up
        self.folder_seen = self.folder_signature()
        self.change_bar.pack_forget()

    def later(self):
        """‘나중에’: keep working with the lists as they are; the bar comes back on the next outside change."""
        self.folder_seen = self.folder_signature()
        self.change_bar.pack_forget()

    def schedule_folder_check(self, event):
        if event.widget is not self.root:
            return
        if self.folder_check_job:
            self.root.after_cancel(self.folder_check_job)
        self.folder_check_job = self.root.after(400, self.check_folder)

    def check_folder(self, force=False):
        """Read the work folder again when files were added, removed or changed outside the program."""
        self.folder_check_job = None
        if any(type(widget).__name__ == "InlineDialog" for widget in self.root.winfo_children()) \
                or self.root.place_slaves() or self.root.grab_current() is not None:
            return  # a panel or progress overlay is open: do not change the lists under it
        signature = self.folder_signature()
        if signature is None or (signature == self.folder_seen and not force):
            return
        before = self.folder_seen or frozenset()
        names, old_names = {name for name, *_ in signature}, {name for name, *_ in before}
        added = len(names - old_names)
        if not force:
            removed = len(old_names - names)
            changed = len({entry for entry in signature if entry[0] in old_names} - set(before))
            parts = [f"새 파일 {added}개" if added else "", f"없어진 파일 {removed}개" if removed else "",
                     f"내용이 바뀐 파일 {changed}개" if changed else ""]
            self.change_text.set("작업 폴더가 밖에서 바뀌었어요 (" + " · ".join(part for part in parts if part)
                                 + "). 작업이 끝나면 ‘지금 읽기’를 누르세요. 바뀐 파일만 읽어요.  F5 = 지금 읽기")
            if not self.change_bar.winfo_ismapped():
                self.change_bar.pack(side="top", fill="x", before=self.page1 if self.page1.winfo_ismapped() else self.page2)
            return
        self.change_bar.pack_forget()
        self.folder_seen = signature
        if self.current == 1:
            self.app.load_folder()
            status = self.app.status
        elif self.close is not None:
            self.close.pdfs_loaded = False
            if self.current == 3 and self.close.folder is not None:
                self.close.load_pdfs(self.close.folder)
                self.close.refresh()
            status = self.close.status
        else:
            return
        self.folder_seen = self.folder_signature()  # names the program changed while reading count as seen
        if force:
            status.set("작업 폴더에서 바뀐 파일을 읽었어요." + (f" 새 파일 {added}개." if added else ""))
        elif added:
            status.set(f"작업 폴더에 새로 넣은 파일 {added}개를 읽었어요.")
        else:
            status.set("작업 폴더의 파일이 바뀌어 다시 읽었어요.")

    # Header -----------------------------------------------------------
    def build_header(self):
        header = tk.Frame(self.root, background=COLORS["navy"], padx=18, pady=10)
        header.pack(fill="x")
        tk.Label(header, text="월마감", background=COLORS["navy"], foreground="#ffffff",
                 font=("Malgun Gothic", 15, "bold")).pack(side="left")
        self.month = ttk.Combobox(header, values=["전체"], state="readonly", width=9)
        self.month.set("전체")
        self.month.pack(side="left", padx=(12, 16))
        self.month.bind("<<ComboboxSelected>>", self.change_month)
        self.kpi = tk.Frame(header, background=COLORS["navy"])
        self.kpi.pack(side="right")
        self.kpi_labels = {}
        for key, label, color in (("total", "거래", "#ffffff"), ("ok", "대조 완료", "#9be7b5"),
                                  ("transfer", "이체·환불", "#d6bcfa"), ("warn", "확인 필요", "#fbd38d"),
                                  ("bad", "증빙 없음", "#feb2b2")):
            widget = tk.Label(self.kpi, background=COLORS["navy"], foreground=color, font=FONT_BOLD)
            widget.pack(side="left", padx=(12, 0))
            self.kpi_labels[key] = (widget, label)
        self.bar = tk.Canvas(header, height=12, background=COLORS["navy_light"], highlightthickness=0)
        self.bar.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.bar.bind("<Configure>", lambda event: self.draw_bar())
        self.bar_counts = {}

    def draw_bar(self):
        self.bar.delete("all")
        width = self.bar.winfo_width()
        total = sum(self.bar_counts.values())
        if not total or width <= 1:
            return
        x = 0
        for key, color in (("ok", "#38c172"), ("transfer", "#9f7aea"), ("warn", "#f6ad55"), ("bad", "#fc8181")):
            part = width * self.bar_counts.get(key, 0) / total
            if part:
                self.bar.create_rectangle(x, 0, x + part, 12, fill=color, width=0)
            x += part

    def change_month(self, _event=None):
        if self.close:
            self.close.month_filter.set(self.month.get())
            self.close.refresh()

    # Sidebar ----------------------------------------------------------
    def build_sidebar(self):
        self.step_widgets = {}
        tk.Frame(self.sidebar, height=10, background=COLORS["panel"]).pack()
        for number, title in STEPS:
            row = tk.Frame(self.sidebar, background=COLORS["panel"], padx=10, pady=8, cursor="hand2")
            row.pack(fill="x", padx=8, pady=2)
            circle = tk.Label(row, text=str(number), width=3, font=FONT_BOLD, background=COLORS["gray_light"],
                              foreground=COLORS["muted"])
            circle.pack(side="left")
            texts = tk.Frame(row, background=COLORS["panel"])
            texts.pack(side="left", padx=10, fill="x")
            name = tk.Label(texts, text=title, anchor="w", font=FONT_BOLD, background=COLORS["panel"],
                            foreground=COLORS["ink"])
            name.pack(anchor="w")
            note = tk.Label(texts, text="", anchor="w", font=FONT_SMALL, background=COLORS["panel"],
                            foreground=COLORS["muted"])
            note.pack(anchor="w")
            for widget in (row, circle, texts, name, note):
                widget.bind("<Button-1>", lambda event, n=number: self.show(n))
            self.step_widgets[number] = (row, circle, texts, name, note)
        tk.Frame(self.sidebar, height=1, background=COLORS["line"]).pack(fill="x", padx=14, pady=(16, 8))
        tk.Label(self.sidebar, text="도구", font=FONT_BOLD, background=COLORS["panel"], foreground=COLORS["muted"],
                 anchor="w").pack(fill="x", padx=18)
        tools = [("지난 달 열기", self.open_past_month),
                 ("작업 기록 백업", lambda: self.close and self.close.export_backup()),
                 ("작업 기록 복원", lambda: self.close and self.close.restore_backup()),
                 ("전체 작업 백업", lambda: self.close and self.close.export_workspace_backup()),
                 ("전체 작업 복원", lambda: self.close and self.close.restore_workspace_backup()),
                 ("설치·작동 점검", self.app.diagnose), ("오류 진단 저장", self.app.save_diagnostics),
                 ("사용법 열기", self.open_manual),
                 ("환경설정", self.app.open_settings)]
        if not self.practice_folder:
            tools.insert(5, ("연습 모드 열기", self.app.open_practice))
        for label, command in tools:
            link = tk.Label(self.sidebar, text=label, anchor="w", font=FONT, background=COLORS["panel"],
                            foreground=COLORS["ink"], cursor="hand2", padx=18, pady=3)
            link.pack(fill="x")
            link.bind("<Button-1>", lambda event, c=command: c())
            link.bind("<Enter>", lambda event, w=link: w.configure(foreground=COLORS["blue"]))
            link.bind("<Leave>", lambda event, w=link: w.configure(foreground=COLORS["ink"]))
        tk.Label(self.sidebar, text=f"v{VERSION}", font=FONT_SMALL, background=COLORS["panel"],
                 foreground=COLORS["muted"]).pack(side="bottom", pady=8)

    def open_past_month(self):
        if self.close:
            self.show(2)
            self.close.open_past_month()

    def open_manual(self):
        folder = Path(__file__).resolve().parent
        manual = folder / "사용법.html"
        if not manual.is_file():
            manual = folder / "사용법.txt"
        try:
            if os.name == "nt":
                os.startfile(manual)
            elif not webbrowser.open(manual.as_uri()):
                raise OSError("사용법 파일을 열 수 없어요.")
        except OSError as error:
            messagebox.showerror("사용법 열기 실패", str(error), parent=self.root)

    # Pages ------------------------------------------------------------
    def show(self, number):
        if number > 1 and self.close is None:
            messagebox.showerror("작업 기록", "작업 기록(monthly-close.json)을 열지 못해 2~5단계를 사용할 수 없어요.", parent=self.root)
            return
        previous, self.current = self.current, number
        self.page1.pack_forget()
        self.page2.pack_forget()
        if number == 1:
            self.page1.pack(fill="both", expand=True)
            self.status_bar.configure(textvariable=self.app.status)
        else:
            if number == 3 and self.close.folder is None and self.app.folder is not None:
                self.close.load_pdfs(self.app.folder)
                self.close.file_loaded_statements()
            elif number == 3 and self.close.folder is not None and (previous == 1 or not self.close.pdfs_loaded):
                self.close.load_pdfs(self.close.folder)  # pick up names changed in step 1
            self.close.show_step(number)
            self.page2.pack(fill="both", expand=True)
            self.status_bar.configure(textvariable=self.close.status)
        self.update_overview()

    def work_folder_changed(self, folder):
        if self.close is not None:
            self.close.folder = folder
            self.close.pdfs_loaded = False
            self.close.file_loaded_statements()
        self.update_overview()

    def update_overview(self):
        if not hasattr(self, "step_widgets"):
            return
        notes, done = {}, set()
        summary = getattr(self.app, "summary", None)
        if self.app.rows and summary:
            notes[1] = f"증빙 {len(self.app.rows)}개 · 필수 누락 {summary.get('bad', 0)}"
            if not summary.get("bad"):
                done.add(1)
        else:
            notes[1] = "인보이스 폴더 열기"
        if self.close:
            counts, months = self.close.summary()
            total = sum(counts.values())
            values = ["전체", *months]
            if list(self.month["values"]) != values:
                self.month["values"] = values
            if self.month.get() != self.close.month_filter.get():
                self.month.set(self.close.month_filter.get())
            sources = len(self.close.statement_sources)
            notes[2] = f"명세서 {sources}개 · 거래 {len(self.close.transactions)}건" if sources else "명세서 추가"
            if sources:
                done.add(2)
            open_items = counts.get("warn", 0) + counts.get("bad", 0)
            notes[3] = f"남은 거래 {open_items}건" if total else "명세서를 먼저 불러오세요"
            if total and not open_items:
                done.add(3)
            notes[4] = "누락·번역·기간 점검"
            exports = self.close.state.get("exports", {})
            versions = [entry.get("version", 0) for entries in exports.values() for entry in entries]
            notes[5] = f"마감본 v{max(versions)} 저장됨" if versions else "ZIP·Excel 만들기"
            self.bar_counts = dict(counts)
            self.draw_bar()
            values = {"total": total, **counts}
            for key, (widget, label) in self.kpi_labels.items():
                widget.configure(text=f"{values.get(key, 0)} {label}")
        for number, (row, circle, texts, name, note) in self.step_widgets.items():
            active = number == self.current
            background = COLORS["blue_light"] if active else COLORS["panel"]
            for widget in (row, texts, name, note):
                widget.configure(background=background)
            if active:
                circle.configure(text=str(number), background=COLORS["blue"], foreground="#ffffff")
            elif number in done:
                circle.configure(text="✓", background=COLORS["green"], foreground="#ffffff")
            else:
                circle.configure(text=str(number), background=COLORS["gray_light"], foreground=COLORS["muted"])
            note.configure(text=notes.get(number, ""))


def run_shell(practice_folder=None):
    if practice_folder is None and not acquire_single_instance():
        close_splash()
        return
    root = tk.Tk()
    root.withdraw()
    try:
        Shell(root, practice_folder)
    finally:
        close_splash()
    bring_to_front(root)
    root.mainloop()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    run_shell()
