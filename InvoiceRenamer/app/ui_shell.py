"""Main window: five steps on the left, month progress on top, one page at a time."""

import os
from datetime import date
from pathlib import Path
import sys
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from release_info import VERSION
from ui_theme import COLORS, FONT, FONT_BOLD, FONT_SMALL, apply_style, attach_help


APP_TITLE = "InvoiceRenamer · 독일 월마감"
STEPS = (
    (1, "인보이스 모으기·정리"),
    (2, "명세서 불러오기"),
    (3, "인보이스 매칭"),
    (4, "누락 점검"),
    (5, "세무사 제출"),
)
_MUTEX = None
_RESTART = {"wanted": False}


def restart_later(root):
    """v1.14: close the window; run_shell starts the program again (company switch)."""
    _RESTART["wanted"] = True
    root.event_generate("<<CloseShell>>")


def start_again():
    import subprocess
    if os.name == "nt" and _MUTEX:
        try:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(_MUTEX)
        except (AttributeError, OSError):
            pass
    command = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).resolve().parent / "invoice_renamer.py")]
    subprocess.Popen(command, close_fds=True)


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
            self.close.on_month = self.set_month
            self.app.on_manage_rules = self.close.manage_recurring
            self.app.on_manage_formats = self.close.manage_formats
            self.app.on_folders_moved = self.close.paths_moved
            self.app.on_restart = lambda: restart_later(self.root)
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
            self.backup_on_new_version()
            root.after(1500, self.remember_folder)
            root.bind("<FocusIn>", self.schedule_folder_check, add="+")
            root.bind("<F5>", lambda event: self.check_folder(force=True), add="+")
            root.after(2500, self.today_on_start)
        root.bind("<F1>", lambda event: self.show_shortcuts(), add="+")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<<CloseShell>>", lambda _event: self.on_close())

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
        if not self.practice_folder:
            try:
                import profiles
                if profiles.several():  # v1.14: which company is open
                    tk.Label(header, text=profiles.active_name(), background=COLORS["navy"], foreground="#ffd479",
                             font=("Malgun Gothic", 11, "bold")).pack(side="left", padx=(10, 0))
            except OSError:
                pass
        self.month = ttk.Combobox(header, values=["전체"], state="readonly", width=9)
        self.month.set("전체")
        self.month.pack(side="left", padx=(12, 16))
        self.month.bind("<<ComboboxSelected>>", self.change_month)
        self.kpi = tk.Frame(header, background=COLORS["navy"])
        self.kpi.pack(side="right")
        self.kpi_labels = {}
        for key, label, color in (("total", "거래", "#ffffff"), ("ok", "대조 완료", "#9be7b5"),
                                  ("transfer", "이체·환불", "#d6bcfa"), ("warn", "확인 필요", "#fbd38d"),
                                  ("bad", "인보이스 없음", "#feb2b2")):
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

    def set_month(self, month):
        """The header month box shows the month another screen switched to."""
        values = list(self.month.cget("values"))
        if month not in values:
            self.month.configure(values=[*values, month])
        self.month.set(month)

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
        tools = [("오늘 할 일", self.show_today), ("작업 이력", self.show_history), ("백업·복원", self.open_backups),
                 ("환경설정", self.app.open_settings), ("도움말 ▾", self.help_menu)]
        for label, command in tools:
            link = tk.Label(self.sidebar, text=label, anchor="w", font=FONT, background=COLORS["panel"],
                            foreground=COLORS["ink"], cursor="hand2", padx=18, pady=3)
            link.pack(fill="x")
            link.bind("<Button-1>", lambda event, c=command: c())
            link.bind("<Enter>", lambda event, w=link: w.configure(foreground=COLORS["blue"]))
            link.bind("<Leave>", lambda event, w=link: w.configure(foreground=COLORS["ink"]))
        tk.Label(self.sidebar, text=f"v{VERSION}", font=FONT_SMALL, background=COLORS["panel"],
                 foreground=COLORS["muted"]).pack(side="bottom", pady=8)

    # Toolbox ----------------------------------------------------------
    def help_menu(self):
        menu = tk.Menu(self.root, tearoff=False)
        items = [("사용법 열기", self.open_manual), ("단축키·마우스 (F1)", self.show_shortcuts)]
        if not self.practice_folder:
            items.append(("연습 모드 열기", self.app.open_practice))
        items += [("설치·작동 점검", self.app.diagnose), ("오류 진단 저장", self.app.save_diagnostics)]
        for label, command in items:
            menu.add_command(label=label, command=command)
        menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())

    def show_shortcuts(self):
        from ui_theme import InlineDialog
        dialog = InlineDialog(self.root)
        dialog.title("단축키·마우스")
        dialog.center(760, 560)
        rows = [
            ("어디서나", "F1", "이 안내"), ("어디서나", "F5", "작업 폴더에서 바뀐 파일 읽기"),
            ("어디서나", "Enter / Esc", "확인 창: 확인 / 취소"),
            ("1단계 목록", "더블클릭", "편집 (빈 발행일·업체명·총액·번호 칸이면 원본에서 그 항목 고르기)"),
            ("1단계 목록", "오른쪽 클릭", "선택한 파일: 열기·편집·제외(사유)·인보이스 맞음·보류·다시 읽기…"),
            ("1단계 목록", "F2 / Delete", "편집 / 제외 폴더로"), ("1단계 목록", "Ctrl+A", "모두 선택"),
            ("1단계 표 머리", "클릭 / 오른쪽 클릭", "정렬 / 정렬·맞춤·칸 숨기기"),
            ("원본 창", "끌기", "상자 안 글자를 지금 항목 값으로 (옆 글자를 보고 항목을 바꾸기도 해요)"),
            ("원본 창", "글자 오른쪽 클릭", "이 값으로 넣기 ▸ 항목"), ("원본 창", "Ctrl+휠", "확대·축소"),
            ("3단계 거래", "Enter", "추천 1순위 연결 (이상하면 먼저 물어요)"),
            ("3단계 거래", "더블클릭 / 오른쪽 클릭", "명세서에서 보기 / 메모·인보이스 없이 처리·메일 검색·환불 찾기…"),
            ("3단계 인보이스", "더블클릭 / 오른쪽 클릭", "열기 / 연결·해제·나란히 보기·값 고치기·제외…"),
            ("4단계 점검표", "더블클릭", "그 달 3단계로 · 항목이면 그 거래·인보이스로"),
            ("전달 이력", "더블클릭", "ZIP이 있는 폴더 열기"),
        ]
        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=("where", "key", "what"), show="headings", height=len(rows))
        for column, label, width in (("where", "화면", 120), ("key", "키·마우스", 160), ("what", "하는 일", 440)):
            tree.heading(column, text=label)
            tree.column(column, width=width)
        for row in rows:
            tree.insert("", "end", values=row)
        tree.pack(fill="both", expand=True)
        ttk.Button(dialog, text="닫기", command=dialog.destroy).pack(pady=(0, 12))

    def open_backups(self):
        """One window for all backups: automatic small backups, full backup with files, and restoring."""
        from ui_theme import InlineDialog, show_notice
        from invoice_renamer import load_settings, save_settings
        if self.close is None:
            return
        from monthly_close import backup_folder
        dialog = InlineDialog(self.root)
        dialog.title("백업·복원")
        dialog.center(820, 520)
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill="both", expand=True)
        info = tk.StringVar()

        def describe():
            files = sorted(backup_folder().glob("auto_*.json")) if backup_folder().is_dir() else []
            last = files[-1].stem.split("_", 2) if files else None
            info.set((f"마지막 자동 백업: {last[1]} {last[2][:2]}:{last[2][2:4]}" if last else "자동 백업이 아직 없어요")
                     + f" · 보관 {len(files)}개\n위치: {backup_folder()}")

        settings = load_settings()
        auto = tk.BooleanVar(value=settings.get("auto_backup", True))
        keep = tk.StringVar(value=str(settings.get("backup_keep", 20)))
        ttk.Label(body, text="작업 기록 백업 (작고 빠름)", font=FONT_BOLD).pack(anchor="w")
        ttk.Label(body, text="거래 번호·연결·메모·수정값·배운 양식·표시를 저장해요. 인보이스 파일은 들어가지 않아요.",
                  foreground=COLORS["muted"]).pack(anchor="w", pady=(0, 4))
        ttk.Label(body, textvariable=info).pack(anchor="w")
        row = ttk.Frame(body)
        row.pack(anchor="w", pady=6)

        def remember():
            try:
                count = max(3, min(200, int(keep.get())))
            except ValueError:
                count = 20
            save_settings({"auto_backup": auto.get(), "backup_keep": count})

        ttk.Checkbutton(row, text="프로그램을 닫을 때와 마감 확정 때 자동으로 백업", variable=auto, command=remember).pack(side="left")
        ttk.Label(row, text="  최근").pack(side="left")
        spin = ttk.Spinbox(row, from_=3, to=200, width=5, textvariable=keep, command=remember)
        spin.pack(side="left", padx=4)
        spin.bind("<FocusOut>", lambda event: remember())
        ttk.Label(row, text="개 보관").pack(side="left")
        buttons = ttk.Frame(body)
        buttons.pack(anchor="w", pady=(4, 14))

        def backup_now():
            path = self.close.backup_now()
            describe()
            if path:
                show_notice(dialog, f"작업 기록을 백업했어요.\n{path}")

        def restore():
            folder = backup_folder()
            from tkinter import filedialog
            selected = filedialog.askopenfilename(parent=self.root, filetypes=[("작업 백업", "*.json")],
                                                  initialdir=str(folder) if folder.is_dir() else None)
            if selected:
                dialog.destroy()
                self.close.restore_backup(selected)

        for label, command in (("지금 백업", backup_now), ("작업 기록 복원…", restore),
                               ("다른 곳에 저장…", self.close.export_backup), ("백업 폴더 열기", lambda: self.app.reveal(backup_folder() / "."))):
            ttk.Button(buttons, text=label, command=command).pack(side="left", padx=(0, 6))
        ttk.Label(body, text="전체 작업 백업 (파일 포함, 큼)", font=FONT_BOLD).pack(anchor="w")
        ttk.Label(body, text="인보이스·명세서·전달 ZIP·보조 자료까지 ZIP 하나로 묶어요. 다른 PC로 옮기거나 분기마다 보관할 때 써요.",
                  foreground=COLORS["muted"], wraplength=760).pack(anchor="w", pady=(0, 4))
        full = ttk.Frame(body)
        full.pack(anchor="w")
        ttk.Button(full, text="전체 작업 백업…", command=self.close.export_workspace_backup).pack(side="left", padx=(0, 6))
        ttk.Button(full, text="전체 작업 복원…", command=self.close.restore_workspace_backup).pack(side="left")
        ttk.Button(dialog, text="닫기", command=dialog.destroy).pack(side="bottom", pady=12)
        describe()

    def show_history(self):
        """What was changed and when: record changes (links, notes, close) and files set aside, newest first."""
        from ui_theme import InlineDialog, table_tools
        from invoice_renamer import load_exclusion_log
        if self.close is None:
            return
        dialog = InlineDialog(self.root)
        dialog.title("작업 이력")
        dialog.geometry("1040x560")
        ttk.Label(dialog, text="거래 기록 변경과 제외 폴더 이동을 시간 순서로 보여 줘요. 되돌리기는 각 화면의 ‘해제’, "
                               "1단계 ‘정리 되돌리기’, ‘제외 기록 보기’, ‘백업·복원’에서 해요.",
                  padding=(14, 10, 14, 6), wraplength=1000).pack(anchor="w")
        frame = ttk.Frame(dialog)
        frame.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        columns = ("at", "kind", "who", "what", "detail")
        tree = ttk.Treeview(frame, columns=columns, show="headings")
        for column, label, width in zip(columns, ("시각", "종류", "사용자", "거래번호·파일", "내용"), (170, 90, 100, 260, 380)):
            tree.heading(column, text=label)
            tree.column(column, width=width, minwidth=50)
        bar = ttk.Scrollbar(frame, command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        table_tools(tree, "work-history", stretch="detail")
        rows = [(entry.get("at", ""), "거래 기록", entry.get("user", ""), entry.get("number", ""), entry.get("fields", ""))
                for entry in self.close.state.get("audit", [])]
        rows += [(entry.get("at", ""), "파일 " + entry.get("action", ""), "", Path(entry.get("to", "")).name,
                  f"{entry.get('reason', '')} · {Path(entry.get('from', '')).parent.name} → {Path(entry.get('to', '')).parent.name}")
                 for entry in load_exclusion_log()]
        for row in sorted(rows, key=lambda row: str(row[0]), reverse=True)[:3000]:
            tree.insert("", "end", values=row)
        ttk.Button(dialog, text="닫기", command=dialog.destroy).pack(pady=(0, 10))

    def show_today(self):
        """Start overview: what is left in each step, with a button that goes there."""
        from ui_theme import InlineDialog
        from monthly_close import backup_folder, double_payments
        from invoice_renamer import load_settings
        dialog = InlineDialog(self.root)
        dialog.title("오늘 할 일")
        dialog.center(860, 560)
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill="both", expand=True)
        items = []
        summary = getattr(self.app, "summary", {}) or {}
        marks = getattr(self.app, "marks", {})
        if summary.get("bad"):
            items.append(("1단계", f"필수 값이 빠진 인보이스 {summary['bad']}개", lambda: self.go_view(1, "필수 누락")))
        if summary.get("warn"):
            items.append(("1단계", f"확인이 필요한 인보이스 {summary['warn']}개 (파일명·배운 양식·소액 등)", lambda: self.go_view(1, "확인 필요")))
        if summary.get("muted"):
            items.append(("1단계", f"인보이스가 아닌 것 같은 파일 {summary['muted']}개", lambda: self.go_view(1, "인보이스 아님")))
        held = sum(1 for entry in marks.values() if entry.get("hold"))
        if held:
            items.append(("1단계", f"보류로 표시한 파일 {held}개", lambda: self.go_view(1, "보류")))
        ready = len(getattr(self.app, "targets", {}))
        if ready:
            items.append(("1단계", f"파일명을 바꿀 수 있는 인보이스 {ready}개", lambda: self.go_view(1, "변경 가능")))
        if self.close is not None:
            close = self.close
            if not close.transactions:
                items.append(("2단계", "불러온 명세서가 없어요", lambda: self.show(2)))
            missing = [key for key in close.transactions if "인보이스 없음" in close.status_for(key) or close.status_for(key) == "인보이스 미연결"]
            if missing:
                items.append(("3단계", f"인보이스가 없는 거래 {len(missing)}건", lambda: self.show(3)))
            if close.invoices:
                unmatched = len(close.unmatched_documents(near_only=True))
                if unmatched:
                    items.append(("3단계", f"거래에 연결 안 된 인보이스 {unmatched}개", lambda: (self.show(3), close.manage_unmatched())))
            pairs = double_payments(close.transactions.values())
            if pairs:
                text = ", ".join(f"{close.record(a.key).get('number', '')}·{close.record(b.key).get('number', '')}" for a, b in pairs[:3])
                items.append(("3단계", f"이중 결제 의심 {len(pairs)}쌍 (같은 곳·같은 금액·3일 안): {text}",
                              lambda: self.show_pairs(pairs)))
            today = date.today().isoformat()
            late = [key for key in close.transactions if close.record(key).get("follow_up") == "요청 중"
                    and close.record(key).get("due", "9999") < today]
            if late:
                items.append(("3단계", f"인보이스 요청 기한이 지난 거래 {len(late)}건", lambda: (self.show(3), close.followup_list())))
        if self.close is not None:
            gone = self.close.missing_links()
            if gone:
                items.append(("3단계", f"작업 폴더에서 사라진 인보이스 파일 {len(gone)}개", self.close.find_missing_files))
        files = sorted(backup_folder().glob("auto_*.json")) if backup_folder().is_dir() else []
        if not files and not load_settings().get("auto_backup", True):
            items.append(("백업", "자동 백업이 꺼져 있어요 · 백업·복원에서 지금 백업할 수 있어요", self.open_backups))
        ttk.Label(body, text="지금 남은 일", font=FONT_BOLD).pack(anchor="w", pady=(0, 8))
        if not items:
            ttk.Label(body, text="남은 일이 없어요. 4단계 ‘누락 점검’과 5단계 ‘세무사 제출’을 확인하세요.").pack(anchor="w")
        for step, text, command in items:
            row = ttk.Frame(body)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=step, width=8, foreground=COLORS["muted"]).pack(side="left")
            ttk.Label(row, text=text, wraplength=600).pack(side="left")
            ttk.Button(row, text="가기", style="Small.TButton",
                       command=lambda c=command: (dialog.destroy(), c())).pack(side="right")
        bottom = ttk.Frame(dialog, padding=(16, 8))
        bottom.pack(side="bottom", fill="x")
        from invoice_renamer import load_settings, save_settings
        start = tk.BooleanVar(value=load_settings().get("today_on_start", True))
        ttk.Checkbutton(bottom, text="하루에 한 번 시작할 때 보여 주기", variable=start,
                        command=lambda: save_settings({"today_on_start": start.get()})).pack(side="left")
        ttk.Button(bottom, text="닫기", command=dialog.destroy).pack(side="right")

    def go_view(self, step, view):
        self.show(step)
        self.app.view.set(view)
        self.app.refresh()

    def show_pairs(self, pairs):
        self.show(3)
        close = self.close
        close.view.set("전체")
        close.refresh()
        keys = [item.key for pair in pairs for item in pair if close.cards.exists(item.key)]
        if keys:
            close.cards.selection_set(keys[:2])
            close.cards.see(keys[0])
        close.status.set("이중 결제 의심: " + " / ".join(
            f"{close.record(a.key).get('number', '')}·{close.record(b.key).get('number', '')} {a.amount} {a.currency}" for a, b in pairs[:5]))

    def backup_on_new_version(self):
        """v1.14: the first start of a new version keeps a backup of the records first (also when auto backup is off)."""
        from invoice_renamer import load_settings, save_settings
        from monthly_close import auto_backup
        last = load_settings().get("last_version")
        if last == VERSION:
            return
        if self.close is not None and self.close.state.get("records"):
            try:
                auto_backup(self.close.state, f"버전변경-{last or '이전'}-to-{VERSION}", int(load_settings().get("backup_keep", 20)))
            except (OSError, ValueError, TypeError) as error:
                from release_info import record_error
                record_error("auto_backup", error)
                return  # try again next start
        save_settings({"last_version": VERSION})

    def today_on_start(self, tries=0):
        from invoice_renamer import load_settings, save_settings
        from ui_theme import Busy, InlineDialog
        settings = load_settings()
        today = date.today().isoformat()
        if self.practice_folder or not settings.get("today_on_start", True) or settings.get("today_shown") == today:
            return
        # v1.13.1: wait until the first reading (progress card), a pop-up or another panel is finished,
        # so ‘오늘 할 일’ never covers the progress and its counts are complete
        try:
            self.root.focus_get()
            dialog_open = False
        except (KeyError, tk.TclError):
            dialog_open = True  # a message box has the focus
        busy = Busy._active is not None and Busy._active.frame.winfo_exists()
        panel = any(isinstance(child, InlineDialog) for child in self.root.winfo_children())
        if busy or panel or dialog_open:
            if tries < 600:  # give up after about 10 minutes
                self.root.after(1000, lambda: self.today_on_start(tries + 1))
            return
        save_settings({"today_shown": today})
        self.show_today()

    def on_close(self):
        """Closing the window: small automatic backup of the records first (if switched on)."""
        if self.close is not None:
            self.close.backup_now("종료")
        self.root.destroy()

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
            notes[1] = f"인보이스 {len(self.app.rows)}개 · 필수 누락 {summary.get('bad', 0)}"
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
    if _RESTART["wanted"] and practice_folder is None:
        start_again()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    run_shell()
