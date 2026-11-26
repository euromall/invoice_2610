"""Colours, ttk styles and status tags shared by every screen."""

import re
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


# Mouse-over explanations for buttons, menu items and sidebar links (label → what it does).
HELP = {
    # 1단계
    "폴더 열기": "인보이스가 있는 폴더를 골라요. 그 폴더의 파일을 모두 보여 주고, 처음 시작할 때 한 번 쓰면 돼요.",
    "새 인보이스 (미분류)": "증빙 폴더 안 ‘미분류’ 폴더에 새로 넣은 파일만 보여 줘요. 이름을 바꾸고 발행월 폴더로 옮길 수 있는 파일을 미리 골라 둬요.",
    "편집": "한 개를 고르면 발행일·업체명·총액·번호를 직접 고쳐요(더블클릭도 돼요). 여러 개를 고르면 업체명·발행일·통화를 한 번에 넣어요.",
    "파일명 변경": "체크한 파일의 이름을 규칙대로 바꾸고, PDF 내용을 같은 이름의 마크다운(.md)으로 함께 저장해요.",
    "정리하기": "체크한 파일을 발행월 폴더로 옮기고 이름을 바꾼 뒤 마크다운(.md)도 함께 저장해요.",
    "정리하기 (미분류)": "미분류의 체크한 파일을 이름을 바꿔 발행월 폴더로 옮기고 마크다운(.md)도 함께 저장해요.",
    "원본·내용 보기": "왼쪽에 PDF, 오른쪽에 읽어 낸 내용(마크다운)을 함께 보여 줘요. 숫자가 맞는지 눈으로 확인할 때 써요.",
    "중복 정리": "같은 인보이스가 두 번 저장된 파일을 찾아요. 하나만 남기고 나머지는 ‘중복’ 폴더로 옮겨요. 지우지 않아요.",
    "중복 인보이스 정리": "같은 인보이스가 두 번 저장된 파일을 찾아요. 하나만 남기고 나머지는 ‘중복’ 폴더로 옮겨요. 지우지 않아요.",
    "인보이스 아닌 파일 → 제외 폴더": "광고·약관처럼 인보이스가 아닌 파일을 ‘제외’ 폴더로 옮겨요. 지우지 않고, 정리 되돌리기로 돌릴 수 있어요.",
    "마크다운 다시 만들기": "선택한 파일의 마크다운(.md)을 다시 만들어요. 값을 고친 뒤 내용을 새로 맞출 때 써요. 보통은 파일명 변경 때 자동으로 저장돼요.",
    "정리 되돌리기": "마지막으로 이름을 바꾸거나 옮긴 파일을 원래 이름·위치로 되돌려요. 마크다운도 함께 돌아가요.",
    "하위 폴더 포함": "고른 폴더 아래 폴더에 있는 파일까지 함께 보여 줘요.",
    "발행월 폴더로 이동": "파일명 변경 때 발행일 기준 ‘2026-11’ 같은 월 폴더로도 옮겨요.",
    # 2단계
    "은행·카드 명세서 추가": "은행·카드 명세서(CSV 또는 PDF) 한 개를 불러와요. 원본은 증빙 폴더 ‘명세서/월’ 폴더로 옮겨 보관해요.",
    "여러 명세서 한 번에 추가": "명세서 여러 개를 한 번에 골라 불러와요. 처음 보는 양식만 열 설정을 해 주면 돼요.",
    "이어서 작업": "마지막으로 작업하던 명세서와 증빙 폴더를 다시 열어요.",
    "지난 달 열기": "예전에 불러온 명세서를 월별로 보여 줘요. 고르면 그달을 다시 열어 보고 고칠 수 있어요.",
    "예시 명세서 불러오기": "연습 모드용 예시 카드 명세서를 불러와요.",
    "명세서 양식 관리": "저장된 명세서 양식(은행·카드별 열 설정)을 보고, 이름을 바꾸거나 지워요.",
    "번호 표시 PDF 명세서 보기": "PDF 명세서의 금액 옆에 거래번호를 찍은 사본을 열어 봐요. 원본은 그대로예요.",
    "명세서 기간 점검": "명세서끼리 빠진 날짜나 겹치는 기간이 없는지 확인해요.",
    # 3단계
    "증빙 폴더 선택": "인보이스가 있는 증빙 폴더를 골라요. 1단계에서 연 폴더가 있으면 자동으로 써요.",
    "일괄 매칭": "금액·날짜가 1:1로 맞는 거래와 증빙을 모아 보여 줘요. 체크한 것을 한 번에 연결해요.",
    "한 번에 연결하기": "금액·날짜가 1:1로 맞는 후보를 모아 보여 줘요. ‘확실’은 미리 체크돼 있어요.",
    "매칭완료 폴더로 이동": "연결이 끝난 증빙을 ‘매칭완료_월’ 폴더로 옮기고 이름 앞에 [거래번호]를 붙여요. 마크다운도 함께 옮겨요.",
    "명세서 근거로 처리 (수수료 등)": "은행 수수료처럼 따로 인보이스가 없는 거래를 명세서를 근거로 처리해요. 사유를 남겨요.",
    "이동 되돌리기": "마지막 매칭완료 이동을 원래 폴더·이름으로 되돌려요.",
    "증빙 없이 처리": "인보이스 없이 처리하는 거래: 명세서 근거, 카드대금·계좌 이체, 결제·환불 묶기.",
    "카드대금·내부이체 후보": "카드대금 자동이체처럼 내 계좌끼리 오간 거래를 짝지어요. 인보이스가 필요 없어요.",
    "결제·환불 연결": "결제와 그 환불 거래를 묶어요. 거래 두 건 이상을 Ctrl로 함께 고른 뒤 누르세요.",
    "메모·기록": "거래에 내 메모, 작업 기록, 후속 상태, 세무사 전달 메모(독일어)를 남겨요.",
    "인보이스 요청 초안": "업체에 인보이스를 요청하는 독일어 메일 초안을 만들어요.",
    "Gmail 검색": "거래 날짜 전후의 첨부 PDF를 Gmail에서 찾는 검색을 브라우저로 열어요.",
    "선택 연결": "왼쪽 거래와 오른쪽에서 고른 증빙을 연결해요.",
    "연결": "추천 1순위 증빙을 이 거래에 바로 연결해요.",
    "해제": "선택한 증빙과 거래의 연결을 풀어요.",
    "나란히 보기": "명세서 거래 줄과 인보이스를 한 화면에 나란히 보여 줘요.",
    "열기": "선택한 증빙 파일을 열어요.",
    "전체 증빙에서 고르기": "금액이 달라 추천에 없는 증빙까지 모두 보여 줘요. 부분결제·환율 차이 때 써요.",
    "배분·차액": "한 증빙을 여러 거래에 나누거나, 금액 차이(수수료·환율·할인)의 사유를 적어요.",
    "정정·원본 연결": "수정 인보이스(Gutschrift·Storno)를 원래 인보이스와 연결해요.",
    "접대비 정보": "접대비(Bewirtung) 증빙에 날짜·장소·참석자·목적을 적어요.",
    "보조 자료 첨부": "확인 메일 같은 보조 자료를 거래에 붙여요. ZIP의 Anlagen 폴더에 들어가요.",
    "증빙 전달 메모 (독일어)": "이 증빙에 대해 세무사에게 전할 독일어 메모를 적어요.",
    "다음 단계: 누락 점검": "4단계로 넘어가 빠진 증빙과 확인할 거래를 점검해요.",
    "확인 필요": "아직 증빙이 없거나 확인할 것이 남은 거래만 보여 줘요. 연결이 끝나면 여기서 빠져요.",
    "전체": "모든 거래를 보여 줘요.",
    "증빙 없음": "증빙이 하나도 연결되지 않은 거래만 보여 줘요.",
    "대조 완료": "증빙 연결과 금액 대조가 끝난 거래를 보여 줘요.",
    # 4단계
    "월별 누락 점검표": "월마다 증빙 없는 거래, 거래 없는 증빙, 번역 대기 메모, 명세서 공백을 한눈에 보여 줘요.",
    "미연결 증빙": "어느 거래에도 연결되지 않은 인보이스를 보여 줘요.",
    "후속 목록": "‘요청 중’·‘세무사 확인 필요’ 같은 후속 상태가 남은 거래를 모아 봐요.",
    "전체 월 검색": "모든 달의 거래와 증빙을 업체명·번호·금액으로 찾아요.",
    # 5단계
    "세무사용 ZIP 만들기": "제출할 월·거래를 고르고, 넣을 파일을 확인한 뒤 세무사용 ZIP을 만들어요.",
    "추가 제출 ZIP": "이미 보낸 마감본 이후 바뀐 거래와 새 증빙만 담아 보내요.",
    "마감본 이력·전달 상태": "만든 ZIP 목록과 ‘전달함’·‘회신 대기’ 같은 상태를 관리해요.",
    "월 마감 확정·재열기": "보낸 달을 잠가 실수로 바뀌지 않게 하거나, 다시 열어요.",
    "CSV 내보내기": "거래 대조표를 CSV 파일 하나로 저장해요.",
    # 도구
    "작업 기록 백업": "연결·메모 같은 작업 기록만 파일 하나로 백업해요.",
    "작업 기록 복원": "백업한 작업 기록을 되살려요.",
    "전체 작업 백업": "작업 기록과 증빙·명세서 파일까지 모두 ZIP 하나로 백업해요. 다른 PC로 옮길 때 써요.",
    "전체 작업 복원": "전체 작업 백업 ZIP을 풀어 그대로 이어서 작업해요.",
    "연습 모드 열기": "예시 자료로 연습해요. 실제 파일과 기록은 건드리지 않아요.",
    "설치·작동 점검": "폴더 쓰기, PDF 읽기, OCR, ZIP 저장이 되는지 점검해요.",
    "오류 진단 저장": "문제가 생겼을 때 보낼 진단 파일을 저장해요. 개인 파일 내용은 넣지 않아요.",
    "사용법 열기": "그림이 있는 사용법을 열어요.",
    # 메모·문구
    "✎ 문구 관리": "세무사 전달 메모에 넣는 독일어 문구를 추가·수정·삭제해요.",
    "상태·예정일로 독일어 초안 만들기": "후속 상태와 예정일로 세무사 전달 메모 독일어 초안을 만들어요.",
}


def help_key(text):
    """Label text without counts, arrows and drop-down marks, for HELP lookups."""
    text = re.sub(r"\s*\(\d+건\)", "", str(text))
    text = re.sub(r"\s*[▾→]+\s*$", "", text).strip()
    return re.sub(r"\s+\d+$", "", text)


class Tooltip:
    """Small explanation bubble shown while the mouse rests on a widget."""

    current = None

    def __init__(self, widget, text_for):
        self.widget, self.text_for, self.job, self.tip = widget, text_for, None, None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def schedule(self, _event=None):
        self.cancel()
        self.job = self.widget.after(450, self.show)

    def cancel(self):
        if self.job:
            try:
                self.widget.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None

    def show(self, x=None, y=None):
        self.job = None
        text = self.text_for()
        if not text or not self.widget.winfo_exists():
            return
        Tooltip.close_current()
        self.tip = show_bubble(self.widget, text,
                               x if x is not None else self.widget.winfo_rootx() + 8,
                               y if y is not None else self.widget.winfo_rooty() + self.widget.winfo_height() + 6)
        Tooltip.current = self

    def hide(self, _event=None):
        self.cancel()
        if self.tip is not None:
            try:
                self.tip.destroy()
            except tk.TclError:
                pass
            self.tip = None

    @classmethod
    def close_current(cls):
        if cls.current is not None:
            cls.current.hide()
            cls.current = None


def show_bubble(widget, text, x, y):
    tip = tk.Toplevel(widget)
    tip.wm_overrideredirect(True)
    try:
        tip.attributes("-topmost", True)
    except tk.TclError:
        pass
    tk.Label(tip, text=text, justify="left", wraplength=320, background="#fffbe6", foreground=COLORS["ink"],
             relief="solid", borderwidth=1, font=FONT_SMALL, padx=8, pady=5).pack()
    tip.update_idletasks()
    screen = widget.winfo_screenwidth()
    x = min(x, screen - tip.winfo_reqwidth() - 8)
    tip.wm_geometry(f"+{max(0, x)}+{y}")
    tip.after(8000, lambda: tip.winfo_exists() and tip.destroy())  # never leave a bubble behind
    return tip


def attach_help(widget):
    """Give every button, check box, choice and sidebar link with a HELP entry a mouse-over bubble."""
    if isinstance(widget, (ttk.Button, ttk.Checkbutton, ttk.Radiobutton, ttk.Menubutton, tk.Label)) \
            and not getattr(widget, "_help_attached", False):
        try:
            label = str(widget.cget("text"))
        except tk.TclError:
            label = ""
        if label and (HELP.get(help_key(label)) or not isinstance(widget, tk.Label)):
            widget._help_attached = True
            Tooltip(widget, lambda w=widget: HELP.get(help_key(w.cget("text")), ""))
    for child in widget.winfo_children():
        attach_help(child)


def menu_help(menu):
    """Explanations for drop-down menu entries, shown beside the highlighted entry."""
    state = {"tip": None}

    def close(_event=None):
        if state["tip"] is not None:
            try:
                state["tip"].destroy()
            except tk.TclError:
                pass
            state["tip"] = None

    def selected(_event=None):
        close()
        try:
            index = menu.index("active")
        except tk.TclError:
            return
        if index is None or menu.type(index) != "command":
            return
        text = HELP.get(help_key(menu.entrycget(index, "label")))
        if text:
            x = menu.winfo_rootx() + max(menu.winfo_width(), 160) + 6
            y = menu.winfo_pointery() - 10
            state["tip"] = show_bubble(menu, text, x, y)

    menu.bind("<<MenuSelect>>", selected, add="+")
    menu.bind("<Unmap>", close, add="+")


def more_menu(parent, label, items):
    """A '더보기' button that opens a drop-down list of (label, command)."""
    button = ttk.Menubutton(parent, text=label)
    menu = tk.Menu(button, tearoff=False)
    for text, command in items:
        if text == "-":
            menu.add_separator()
        else:
            menu.add_command(label=text, command=lambda c=command: (Tooltip.close_current(), c()))
    button["menu"] = menu
    menu_help(menu)
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
        attach_help(self)
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


def show_notice(parent, message, title="안내", kind="info", action=None):
    """A small pop-up card in the middle of the main window. Enter, Esc or 확인 closes it.

    action: optional (label, command) shown as the main button, e.g. ("증빙 매칭으로 이동 →", go)."""
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

    buttons = tk.Frame(body, background=COLORS["panel"])
    buttons.pack(anchor="e")
    if action:
        label, command = action

        def run(_event=None):
            close()
            command()

        button = ttk.Button(buttons, text=label, style="Primary.TButton", command=run)
        ttk.Button(buttons, text="닫기", command=close).pack(side="left", padx=(0, 6))
        button.pack(side="left")
        button.bind("<Return>", run)
    else:
        button = ttk.Button(buttons, text="확인", style="Primary.TButton", command=close)
        button.pack(side="left")
    card.place(relx=0.5, rely=0.42, anchor="center")
    card.lift()
    for widget in (card, body, button):
        if not action or widget is not button:
            widget.bind("<Return>", close)
        widget.bind("<Escape>", close)
    card.after_idle(button.focus_set)
    try:
        card.grab_set()
    except tk.TclError:
        pass
    return card
