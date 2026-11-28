"""Windows: the main screen (인보이스 작업), 작업 완료, 회계관리, 재고관리 and 설정, in one window with a side menu."""
import datetime as dt
import os
import sys
import traceback
from copy import copy

import openpyxl
from PySide6.QtCore import Qt, QDate, QThread, Signal, QTimer, QRect, QLocale
from PySide6.QtGui import QPainter, QColor
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCalendarWidget, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSpinBox,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import db, compare, update
from .app import (Folder, Options, load_options, save_options, run_job, preview_run, person_candidates,
                  worked_dates, previous_run, archive_run, RUN_DIR, read_lock, write_lock, release_lock, old_backups, remove_backups,
                  BACKUP_KEEP_DAYS, BACKUP_KEEP_NEWEST, ADDRESS_API, ADDRESS_OFFLINE)
from .engine import MODE_PROFORMA, MODE_BILL, CARRIER_POST, CARRIER_CJ

TITLE = 'InvoiceAuto · Euromall GmbH'
WEEKDAYS = '월화수목금토일'

ACCENT = '#0F6B63'
INK = '#18212B'
MUTED = '#5B6773'
LINE = '#DDE3E8'
WARN_BG, WARN_INK = '#FFF4DB', '#6B4A00'
PROFORMA_DOT, BILL_DOT = '#2563EB', '#16A34A'

STYLE = f"""
* {{ font-family: 'Malgun Gothic', 'Noto Sans KR', 'Segoe UI', sans-serif; font-size: 10pt; color: {INK}; }}
QMainWindow, #Content {{ background: #EEF1F4; }}
QDialog {{ background: #FFFFFF; }}
#Sidebar {{ background: #16232E; }}
#Sidebar QLabel {{ color: #93A3B1; background: transparent; }}
#Sidebar #Brand {{ color: #FFFFFF; font-size: 13pt; font-weight: bold; }}
#Sidebar #Logo {{ background: {ACCENT}; border-radius: 9px; color: #FFFFFF; font-weight: bold; font-size: 12pt; }}
#Nav {{ background: transparent; color: #C9D3DC; border: none; border-radius: 8px; padding: 10px 12px;
        text-align: left; font-size: 10.5pt; }}
#Nav:hover {{ background: #1F3242; }}
#Nav:checked {{ background: #22384A; color: #FFFFFF; font-weight: bold; }}
#FolderBox {{ background: #1C2D3B; border-radius: 8px; }}
#FolderBox QLabel {{ color: #C9D3DC; font-size: 8.5pt; }}

#Card {{ background: #FFFFFF; border: 1px solid {LINE}; border-radius: 12px; }}
#Card QLabel, #Card QCheckBox {{ background: transparent; }}
#CardTitle {{ font-size: 11pt; font-weight: bold; }}
#Step {{ background: #E3F1EF; color: {ACCENT}; border-radius: 12px; font-weight: bold; font-size: 9pt; }}
#Eyebrow {{ color: {MUTED}; font-size: 9pt; }}
#H1 {{ font-size: 17pt; font-weight: bold; }}
#Muted {{ color: {MUTED}; }}
#Tile {{ background: #F6F8FA; border-radius: 8px; }}
#TileWarn {{ background: {WARN_BG}; border-radius: 8px; }}
#TileValue {{ font-size: 15pt; font-weight: bold; background: transparent; }}
#TileLabel {{ color: {MUTED}; font-size: 9pt; background: transparent; }}
#TileWarn QLabel {{ color: {WARN_INK}; }}

#Banner {{ background: {WARN_BG}; border-bottom: 1px solid #F1DDA6; }}
#Banner QLabel {{ color: {WARN_INK}; background: transparent; }}
#Pill {{ background: {WARN_BG}; color: {WARN_INK}; border-radius: 11px; padding: 4px 11px; font-size: 9pt;
         font-weight: bold; }}
#PillOk {{ background: #E3F1EF; color: #0A4F49; border-radius: 11px; padding: 4px 11px; font-size: 9pt;
           font-weight: bold; }}

#DateBox {{ background: #FFFFFF; border: 2px solid {ACCENT}; border-radius: 12px; }}
#DateBox QDateEdit {{ border: none; font-size: 14pt; font-weight: bold; background: transparent; padding: 2px 4px;
                      min-width: 150px; }}
#DateBox QDateEdit::drop-down {{ width: 0px; border: none; }}
#Arrow {{ background: transparent; border: none; border-radius: 8px; font-size: 13pt; min-width: 36px;
          min-height: 40px; }}
#Arrow:hover {{ background: #EEF1F4; }}

QPushButton {{ background: #FFFFFF; border: 1px solid #CFD6DD; border-radius: 8px; padding: 0 14px;
               min-height: 38px; }}
QPushButton:hover {{ background: #F3F6F8; }}
QPushButton:disabled {{ color: #9AA5AF; background: #F3F5F7; }}
#Primary {{ background: {ACCENT}; color: #FFFFFF; border: none; border-radius: 12px; font-size: 13pt;
            font-weight: bold; min-height: 54px; }}
#Primary:hover {{ background: #0C5C55; }}
#Primary:disabled {{ background: #9CC3BF; color: #FFFFFF; }}
#Small {{ min-height: 30px; font-size: 9pt; padding: 0 10px; }}
#Seg {{ background: transparent; border: none; border-radius: 7px; min-height: 36px; color: #6B7682; }}
#Seg:hover {{ background: #E2E7EC; }}
#Seg:checked {{ background: {ACCENT}; color: #FFFFFF; font-weight: bold; border: none; }}
#Choice {{ border-radius: 10px; min-height: 64px; padding: 0; }}
#ChoiceRec {{ min-height: 64px; padding: 0; border-radius: 10px; border: 2px solid {ACCENT}; background: #E9F5F3; }}
#ChoiceRec:hover {{ background: #DDF0EC; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px; border: 1.5px solid #9AA5AF;
    background: #FFFFFF; }}
QCheckBox::indicator {{ border-radius: 5px; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 9px; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url(CHECK_SVG); }}
QRadioButton::indicator:checked {{ width: 8px; height: 8px; border: 5px solid {ACCENT}; border-radius: 9px; }}
QCheckBox::indicator:disabled {{ background: #EEF1F4; border-color: #CFD6DD; }}
#SegBox {{ background: #EEF1F4; border-radius: 9px; }}

QComboBox, QLineEdit, QDateEdit, QSpinBox, QDoubleSpinBox {{ background: #FFFFFF; border: 1px solid #CFD6DD;
    border-radius: 8px; padding: 0 10px; min-height: 38px; }}
QCheckBox {{ spacing: 9px; min-height: 30px; }}
QComboBox::drop-down, QDateEdit::drop-down {{ border: none; width: 26px; }}

#Log {{ background: #16232E; color: #C9D3DC; border: none; border-radius: 12px; padding: 8px;
        font-family: Consolas, monospace; font-size: 9pt; }}

QTableWidget {{ background: #FFFFFF; border: 1px solid {LINE}; border-radius: 10px; gridline-color: #EEF1F4;
                selection-background-color: #D7ECE9; selection-color: {INK}; alternate-background-color: #FAFBFC; }}
QHeaderView::section {{ background: #F6F8FA; color: {MUTED}; border: none; border-bottom: 1px solid {LINE};
                        padding: 7px 8px; font-weight: bold; }}
QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: #16232E; }}
QCalendarWidget QToolButton {{ color: #FFFFFF; background: transparent; font-weight: bold; padding: 4px 8px; }}
QCalendarWidget QAbstractItemView {{ selection-background-color: {ACCENT}; selection-color: #FFFFFF; }}
#CalPopup {{ background: #FFFFFF; border: 1px solid #CFD6DD; border-radius: 10px; }}
QScrollArea {{ border: none; background: transparent; }}
"""


def qdate(d: dt.date) -> QDate:
    return QDate(d.year, d.month, d.day)


def pydate(q: QDate) -> dt.date:
    return dt.date(q.year(), q.month(), q.day())


class DateField(QDateEdit):
    """A date box that opens a month calendar when clicked anywhere on it (typing a date still works)."""

    def __init__(self, d: dt.date, calendar=None, fmt='yyyy-MM-dd'):
        super().__init__(qdate(d))
        self.setDisplayFormat(fmt)
        self.setCalendarPopup(False)
        self.setButtonSymbols(QDateEdit.NoButtons)
        self.setCursor(Qt.PointingHandCursor)
        self.cal = calendar or WorkCalendar()
        self.popup = QFrame(self, Qt.Popup)
        self.popup.setObjectName('CalPopup')
        lay = QVBoxLayout(self.popup)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self.cal)
        self.cal.clicked.connect(self._picked)
        self.cal.activated.connect(self._picked)
        self.lineEdit().installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() == ev.Type.MouseButtonPress:
            self.open_calendar()
            return True
        return super().eventFilter(obj, ev)

    def mousePressEvent(self, ev):
        self.open_calendar()

    def open_calendar(self):
        if self.popup.isVisible():
            return
        self.cal.setSelectedDate(self.date())
        self.popup.adjustSize()
        pos = self.mapToGlobal(self.rect().bottomLeft())
        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        x = min(pos.x(), area.right() - self.popup.width())
        y = pos.y() + 4
        if y + self.popup.height() > area.bottom():
            y = self.mapToGlobal(self.rect().topLeft()).y() - self.popup.height() - 4
        self.popup.move(max(area.left(), x), y)
        self.popup.show()
        self.cal.setFocus()

    def _picked(self, q: QDate):
        self.setDate(q)
        self.popup.hide()


def date_edit(d: dt.date) -> QDateEdit:
    return DateField(d)


def label(text, name=None, wrap=False):
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


def card(title=None, step=None):
    """White rounded box; returns (frame, inner layout)."""
    f = QFrame()
    f.setObjectName('Card')
    v = QVBoxLayout(f)
    v.setContentsMargins(20, 18, 20, 18)
    v.setSpacing(12)
    if title:
        h = QHBoxLayout()
        h.setSpacing(10)
        if step:
            s = label(str(step), 'Step')
            s.setFixedSize(24, 24)
            s.setAlignment(Qt.AlignCenter)
            h.addWidget(s)
        h.addWidget(label(title, 'CardTitle'))
        h.addStretch(1)
        v.addLayout(h)
    return f, v


def tile(value, text, warn=False):
    f = QFrame()
    f.setObjectName('TileWarn' if warn else 'Tile')
    v = QVBoxLayout(f)
    v.setContentsMargins(12, 9, 12, 9)
    v.setSpacing(0)
    lb = label(text, 'TileLabel')
    val = label(value, 'TileValue')
    v.addWidget(lb)
    v.addWidget(val)
    f.value = val
    return f


def open_path(path):
    if sys.platform == 'win32' and os.path.exists(path):
        os.startfile(path)


# --- calendar with the days already worked ------------------------------------------------------

class WorkCalendar(QCalendarWidget):
    """Month calendar; days with a result folder get a dot (blue 견적서, green 청구서)."""

    def __init__(self):
        super().__init__()
        self.marks = {}
        self.setGridVisible(False)
        self.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.setFirstDayOfWeek(Qt.Monday)
        self.setMinimumSize(320, 250)

    def set_marks(self, marks):
        self.marks = marks
        self.updateCells()

    def paintCell(self, painter: QPainter, rect: QRect, date: QDate):
        super().paintCell(painter, rect, date)
        modes = self.marks.get(pydate(date))
        if not modes:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        colors = [c for m, c in ((MODE_PROFORMA, PROFORMA_DOT), (MODE_BILL, BILL_DOT)) if m in modes]
        x = rect.center().x() - (len(colors) - 1) * 4
        for c in colors:
            painter.setBrush(QColor(c))
            painter.drawEllipse(x - 3, rect.bottom() - 8, 6, 6)
            x += 8
        painter.restore()


# --- background jobs ------------------------------------------------------------------------------

class JobThread(QThread):
    message = Signal(str)
    done = Signal(bool, object)

    def __init__(self, folder, shipping_file, work_date, options, person_only, person_orders=None):
        super().__init__()
        self.args = (folder, shipping_file, work_date, options)
        self.person_only = person_only
        self.person_orders = person_orders

    def run(self):
        try:
            result = run_job(*self.args, log=self.message.emit, person_only=self.person_only,
                             person_orders=self.person_orders)
            self.done.emit(True, result)
        except Exception as e:
            self.message.emit(f'오류: {e}')
            self.message.emit(traceback.format_exc())
            self.done.emit(False, None)


class PreviewThread(QThread):
    """Reads the input file and Base.xlsx without writing anything, for the numbers on the main screen."""
    done = Signal(object, str)

    def __init__(self, folder, path, work_date, options):
        super().__init__()
        self.args = (folder, path, work_date, options)

    def run(self):
        try:
            self.done.emit(preview_run(*self.args), '')
        except Exception as e:
            self.done.emit(None, str(e))


# --- main window ----------------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, folder: Folder):
        super().__init__()
        self.folder = folder
        self.thread = None
        self.preview_thread = None
        self.preview = None
        self.last_result = None
        self.setWindowTitle(TITLE)
        self.resize(1240, 820)
        self.setMinimumSize(980, 680)

        self.stack = QStackedWidget()
        self.stack.setObjectName('Content')
        self.page_work = self._work_page()
        self.page_done = QWidget()
        self.page_settings = self._settings_page()
        self.page_account = None
        self.page_stock = None
        for p in (self.page_work, self.page_done, self.page_settings):
            self.stack.addWidget(p)

        root = QWidget()
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._sidebar())
        h.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.refresh_files()
        self.apply_options(load_options(folder.ini))
        self.log(f'프로그램 폴더: {os.path.abspath(folder.root)}')
        for p in (os.path.join(folder.base, 'Base.xlsx'), folder.db):
            if not os.path.exists(p):
                self.log(f'주의: {p} 파일이 없습니다.')
        self.file_combo.currentIndexChanged.connect(lambda _: self.start_preview())
        self.start_preview()

    # layout pieces
    def _sidebar(self):
        side = QFrame()
        side.setObjectName('Sidebar')
        side.setFixedWidth(220)
        v = QVBoxLayout(side)
        v.setContentsMargins(14, 20, 14, 16)
        v.setSpacing(4)
        brand = QHBoxLayout()
        logo = label('IA', 'Logo')
        logo.setFixedSize(34, 34)
        logo.setAlignment(Qt.AlignCenter)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(label('InvoiceAuto', 'Brand'))
        names.addWidget(label('Euromall GmbH'))
        brand.addWidget(logo)
        brand.addLayout(names)
        brand.addStretch(1)
        v.addLayout(brand)
        v.addSpacing(18)
        self.nav = QButtonGroup(self)
        self.nav_buttons = {}
        for key, text in (('work', '인보이스 작업'), ('stock', '재고관리'), ('account', '회계관리'),
                          ('compare', '결과 비교')):
            b = QPushButton(text)
            b.setObjectName('Nav')
            b.setCheckable(key != 'compare')
            b.clicked.connect(lambda _=False, k=key: self.go(k))
            self.nav.addButton(b)
            self.nav_buttons[key] = b
            v.addWidget(b)
        v.addStretch(1)
        b = QPushButton('설정')
        b.setObjectName('Nav')
        b.setCheckable(True)
        b.clicked.connect(lambda: self.go('settings'))
        self.nav.addButton(b)
        self.nav_buttons['settings'] = b
        v.addWidget(b)
        box = QFrame()
        box.setObjectName('FolderBox')
        bl = QVBoxLayout(box)
        bl.setContentsMargins(10, 8, 10, 8)
        bl.addWidget(label('프로그램 폴더'))
        path = label(os.path.basename(os.path.abspath(self.folder.root)) or self.folder.root, wrap=True)
        path.setToolTip(os.path.abspath(self.folder.root))
        bl.addWidget(path)
        v.addSpacing(6)
        v.addWidget(box)
        self.nav_buttons['work'].setChecked(True)
        return side

    def _work_page(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # test mode banner
        self.banner = QFrame()
        self.banner.setObjectName('Banner')
        bh = QHBoxLayout(self.banner)
        bh.setContentsMargins(28, 8, 28, 8)
        bh.addWidget(label('<b>테스트 모드</b> · 결과는 Result_test 폴더와 테스트 DB에만 저장됩니다. '
                           '실제 장부는 바뀌지 않습니다.'))
        bh.addStretch(1)
        b = QPushButton('실제 모드로 전환')
        b.setObjectName('Small')
        b.clicked.connect(lambda: self.set_test_mode(False, ask=True))
        bh.addWidget(b)
        outer.addWidget(self.banner)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName('Content')
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        v = QVBoxLayout(body)
        v.setContentsMargins(28, 22, 28, 24)
        v.setSpacing(16)

        # header: title, work date, buttons
        head = QHBoxLayout()
        head.setSpacing(18)
        t = QVBoxLayout()
        t.setSpacing(2)
        t.addWidget(label('인보이스 작업', 'Eyebrow'))
        t.addWidget(label('인보이스 만들기', 'H1'))
        head.addLayout(t)
        head.addStretch(1)
        b_save = QPushButton('설정 저장')
        b_save.clicked.connect(self.save_settings)
        b_open = QPushButton('결과 폴더 열기')
        b_open.clicked.connect(lambda: open_path(self.folder.result))
        head.addWidget(b_save)
        head.addWidget(b_open)
        v.addLayout(head)

        # work date: big, right under the title, because catching up on earlier days is common
        drow = QHBoxLayout()
        drow.setSpacing(14)
        dl = label('작업일자', 'CardTitle')
        drow.addWidget(dl)
        drow.addWidget(self._date_box())
        self.date_pill = label('', 'Pill')
        drow.addWidget(self.date_pill)
        drow.addStretch(1)
        v.addLayout(drow)

        cols = QHBoxLayout()
        cols.setSpacing(20)
        left = QVBoxLayout()
        left.setSpacing(16)
        right = QVBoxLayout()
        right.setSpacing(14)
        cols.addLayout(left, 3)
        cols.addLayout(right, 2)
        v.addLayout(cols)
        v.addStretch(1)

        # 1 input file
        c, cl = card('입력 파일', 1)
        row = QHBoxLayout()
        self.file_combo = QComboBox()
        self.file_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        b = QPushButton('다른 파일 찾기')
        b.clicked.connect(self.browse)
        row.addWidget(self.file_combo, 1)
        row.addWidget(b)
        cl.addLayout(row)
        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        self.t_orders = tile('–', '주문')
        self.t_lines = tile('–', '상품 줄')
        self.t_companies = tile('–', '업체')
        self.t_warn = tile('–', '미리 확인', warn=True)
        for x in (self.t_orders, self.t_lines, self.t_companies, self.t_warn):
            tiles.addWidget(x)
        cl.addLayout(tiles)
        left.addWidget(c)

        # 2 job type
        c, cl = card('작업 종류', 2)
        g = QGridLayout()
        g.setHorizontalSpacing(16)
        g.addWidget(label('인보이스', 'Muted'), 0, 0)
        g.addWidget(label('택배사', 'Muted'), 0, 1)
        self.btn_proforma, self.btn_bill, box1 = self._segmented('견적서', '청구서')
        self.btn_cj, self.btn_post, box2 = self._segmented('CJ 대한통운', '우체국')
        g.addWidget(box1, 1, 0)
        g.addWidget(box2, 1, 1)
        cl.addLayout(g)
        self.chk_hawb = QCheckBox('송장번호 자동으로 붙이기 (입력 파일의 첫 번호부터)')
        cl.addWidget(self.chk_hawb)
        self.btn_proforma.toggled.connect(self.mode_changed)
        left.addWidget(c)

        # 3 outputs
        c, cl = card('만들 파일', 3)
        g = QGridLayout()
        g.setHorizontalSpacing(20)
        g.setVerticalSpacing(4)
        g.addWidget(label('업체별 인보이스 · 수출 인보이스 · 적하목록 · 포장리스트 · 송장업로드는 늘 만듭니다.',
                          'Muted', wrap=True), 0, 0, 1, 2)
        self.chk_merge = QCheckBox('적하목록 무게병합')
        self.chk_export = QCheckBox('업체별 수출용 인보이스')
        self.chk_person = QCheckBox('개인 인보이스')
        self.chk_pdf = QCheckBox('PDF도 저장')
        self.chk_account = QCheckBox('회계·재고 장부에 반영')
        for i, w in enumerate((self.chk_merge, self.chk_export, self.chk_person, self.chk_pdf, self.chk_account)):
            g.addWidget(w, 1 + i // 2, i % 2)
        cl.addLayout(g)
        left.addWidget(c)
        left.addStretch(1)

        # right: checks, start, log
        c, cl = card('시작 전 확인')
        self.check_label = label('입력 파일을 읽는 중입니다…', wrap=True)
        self.check_label.setTextFormat(Qt.RichText)
        self.check_label.setOpenExternalLinks(False)
        self.check_label.linkActivated.connect(lambda _: self.show_warnings())
        cl.addWidget(self.check_label)
        right.addWidget(c)
        self.btn_start = QPushButton('작업 시작  →')
        self.btn_start.setObjectName('Primary')
        self.btn_start.clicked.connect(lambda: self.start(person_only=False))
        right.addWidget(self.btn_start)
        self.btn_person = QPushButton('개인 인보이스만 만들기')
        self.btn_person.clicked.connect(lambda: self.start(person_only=True))
        right.addWidget(self.btn_person)
        right.addWidget(label('작업 기록', 'Eyebrow'))
        self.log_box = QPlainTextEdit(readOnly=True)
        self.log_box.setObjectName('Log')
        self.log_box.setMinimumHeight(200)
        right.addWidget(self.log_box, 1)
        return page

    def _segmented(self, a, b):
        box = QFrame()
        box.setObjectName('SegBox')
        h = QHBoxLayout(box)
        h.setContentsMargins(3, 3, 3, 3)
        h.setSpacing(2)
        grp = QButtonGroup(box)
        out = []
        for text in (a, b):
            btn = QPushButton(text)
            btn.setObjectName('Seg')
            btn.setCheckable(True)
            grp.addButton(btn)
            h.addWidget(btn)
            out.append(btn)
        out[0].setChecked(True)
        return out[0], out[1], box

    def _date_box(self):
        box = QFrame()
        box.setObjectName('DateBox')
        h = QHBoxLayout(box)
        h.setContentsMargins(4, 2, 6, 2)
        h.setSpacing(2)
        prev = QPushButton('‹')
        prev.setObjectName('Arrow')
        prev.setToolTip('하루 전')
        nxt = QPushButton('›')
        nxt.setObjectName('Arrow')
        nxt.setToolTip('하루 뒤')
        self.calendar = WorkCalendar()
        self.work_date = DateField(dt.date.today(), self.calendar, 'yyyy-MM-dd (ddd)')
        self.work_date.setMinimumWidth(215)
        self.work_date.setToolTip('누르면 달력이 열립니다. 점: 이미 작업한 날 (파랑 견적서, 초록 청구서)')
        today = QPushButton('오늘')
        today.setObjectName('Small')
        prev.clicked.connect(lambda: self.work_date.setDate(self.work_date.date().addDays(-1)))
        nxt.clicked.connect(lambda: self.work_date.setDate(self.work_date.date().addDays(1)))
        today.clicked.connect(lambda: self.work_date.setDate(QDate.currentDate()))
        self.work_date.dateChanged.connect(lambda _: self.update_date_status())
        for w in (prev, self.work_date, nxt, today):
            h.addWidget(w)
        return box

    def _settings_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(28, 26, 28, 24)
        v.setSpacing(16)
        v.addWidget(label('설정', 'Eyebrow'))
        v.addWidget(label('프로그램 설정', 'H1'))

        c, cl = card('개인 인보이스 영문주소')
        self.rdb_addr_offline = QRadioButton('프로그램 안에서 변환')
        self.rdb_addr_api = QRadioButton('행정안전부 API 사용')
        grp = QButtonGroup(page)
        grp.addButton(self.rdb_addr_offline)
        grp.addButton(self.rdb_addr_api)
        self.api_key = QLineEdit(placeholderText='API 승인키')
        self.rdb_addr_api.toggled.connect(lambda on: self.api_key.setEnabled(on))
        for w in (self.rdb_addr_offline, self.rdb_addr_api, self.api_key):
            cl.addWidget(w)
        v.addWidget(c)

        c, cl = card('테스트 (기존 프로그램과 나란히 비교)')
        self.chk_test = QCheckBox('테스트 모드: 결과는 Result_test, 회계·재고는 db/euromall_test.db 에 기록')
        self.chk_test.toggled.connect(lambda on: self.set_test_mode(on))
        cl.addWidget(self.chk_test)
        row = QHBoxLayout()
        b1 = QPushButton('테스트 DB 새로 복사')
        b1.clicked.connect(self.fresh_test_db)
        b2 = QPushButton('결과 비교')
        b2.clicked.connect(self.compare_results)
        row.addWidget(b1)
        row.addWidget(b2)
        row.addStretch(1)
        cl.addLayout(row)
        v.addWidget(c)

        c, cl = card('백업 정리')
        cl.addWidget(label(f'db, Result, ShippingInvoice의 backup 폴더에서 {BACKUP_KEEP_DAYS}일이 지난 백업을 지웁니다. '
                           f'폴더마다 최근 {BACKUP_KEEP_NEWEST}개는 오래돼도 남깁니다.', 'Muted', wrap=True))
        row = QHBoxLayout()
        b = QPushButton('오래된 백업 찾기')
        b.clicked.connect(self.clean_backups)
        row.addWidget(b)
        row.addStretch(1)
        cl.addLayout(row)
        v.addWidget(c)

        c, cl = card('프로그램 업데이트')
        cl.addWidget(label(f'지금 버전: {update.current_version()}', 'Muted'))
        row = QHBoxLayout()
        b = QPushButton('새 버전 확인')
        b.clicked.connect(self.check_update)
        row.addWidget(b)
        row.addStretch(1)
        cl.addLayout(row)
        v.addWidget(c)

        row = QHBoxLayout()
        b1 = QPushButton('설정 저장')
        b1.clicked.connect(self.save_settings)
        b2 = QPushButton('저장된 설정 불러오기')
        b2.clicked.connect(self.load_settings)
        row.addWidget(b1)
        row.addWidget(b2)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        return page

    # navigation
    def go(self, key):
        if key == 'compare':
            self.compare_results()
            self.nav_buttons['work'].setChecked(True)
            self.stack.setCurrentWidget(self.page_work)
            return
        if key in ('account', 'stock'):
            if not os.path.exists(self.folder.db):
                QMessageBox.warning(self, TITLE, f'{self.folder.db} 파일이 없습니다.')
                self.nav_buttons['work'].click()
                return
            old = self.page_account if key == 'account' else self.page_stock
            if old is not None:
                self.stack.removeWidget(old)
                old.deleteLater()
            w = (AccountWindow if key == 'account' else StockWindow)(self.folder)
            if key == 'account':
                self.page_account = w
            else:
                self.page_stock = w
            self.stack.addWidget(w)
            self.stack.setCurrentWidget(w)
        elif key == 'settings':
            self.stack.setCurrentWidget(self.page_settings)
        else:
            self.stack.setCurrentWidget(self.page_work)
        self.nav_buttons[key].setChecked(True)

    def log(self, s):
        self.log_box.appendPlainText(s)

    # settings
    def options(self) -> Options:
        return Options(
            mode=MODE_PROFORMA if self.btn_proforma.isChecked() else MODE_BILL,
            carrier=CARRIER_POST if self.btn_post.isChecked() else CARRIER_CJ,
            auto_hawb=self.chk_hawb.isChecked(), save_pdf=self.chk_pdf.isChecked(),
            apply_accounting=self.chk_account.isChecked(), make_person_invoice=self.chk_person.isChecked(),
            make_export_per_company=self.chk_export.isChecked(), make_cargo_weight_merge=self.chk_merge.isChecked(),
            address_mode=ADDRESS_API if self.rdb_addr_api.isChecked() else ADDRESS_OFFLINE,
            address_api_key=self.api_key.text().strip(), test_mode=self.chk_test.isChecked())

    def apply_options(self, o: Options):
        self.btn_proforma.blockSignals(True)
        (self.btn_proforma if o.mode == MODE_PROFORMA else self.btn_bill).setChecked(True)
        self.btn_proforma.blockSignals(False)
        (self.btn_post if o.carrier == CARRIER_POST else self.btn_cj).setChecked(True)
        self.chk_hawb.setChecked(o.auto_hawb)
        self.chk_pdf.setChecked(o.save_pdf)
        self.chk_account.setChecked(o.apply_accounting)
        self.chk_person.setChecked(o.make_person_invoice)
        self.chk_export.setChecked(o.make_export_per_company)
        self.chk_merge.setChecked(o.make_cargo_weight_merge)
        (self.rdb_addr_api if o.address_mode == ADDRESS_API else self.rdb_addr_offline).setChecked(True)
        self.api_key.setText(o.address_api_key)
        self.api_key.setEnabled(o.address_mode == ADDRESS_API)
        self.chk_test.blockSignals(True)
        self.chk_test.setChecked(o.test_mode)
        self.chk_test.blockSignals(False)
        self.set_test_mode(o.test_mode)

    def mode_changed(self, proforma_on):
        """견적서/청구서 each keep their own checkbox set, like the old program."""
        mode = MODE_PROFORMA if proforma_on else MODE_BILL
        o = load_options(self.folder.ini, mode)
        cur = self.options()
        o.address_mode, o.address_api_key, o.test_mode = cur.address_mode, cur.address_api_key, cur.test_mode
        self.apply_options(o)
        self.update_date_status()

    def save_settings(self):
        save_options(self.folder.ini, self.options())
        self.log('설정을 저장했습니다.')

    def load_settings(self):
        self.apply_options(load_options(self.folder.ini, self.options().mode))
        self.log('설정을 불러왔습니다.')

    def set_test_mode(self, on, ask=False):
        if ask and QMessageBox.question(
                self, TITLE, '실제 모드로 바꿀까요?\n이제부터 결과는 Result 폴더에, 회계·재고는 실제 장부'
                             '(euromall.db)에 기록됩니다.') != QMessageBox.Yes:
            return
        self.folder.test = on
        if self.chk_test.isChecked() != on:
            self.chk_test.blockSignals(True)
            self.chk_test.setChecked(on)
            self.chk_test.blockSignals(False)
        self.banner.setVisible(on)
        self.setWindowTitle(TITLE + ('  [테스트 모드]' if on else ''))
        self.update_date_status()

    # work date
    def update_date_status(self):
        if not hasattr(self, 'date_pill'):
            return
        marks = worked_dates(self.folder)
        self.calendar.set_marks(marks)
        d = pydate(self.work_date.date())
        mode = self.options().mode
        done = mode in marks.get(d, set())
        kind = '견적서' if mode == MODE_PROFORMA else '청구서'
        parts = []
        if d != dt.date.today():
            parts.append('오늘이 아닌 날짜')
        parts.append(f'이 날짜에 {kind} 작업한 기록 있음' if done else f'{kind} 작업 기록 없음')
        self.date_pill.setText(' · '.join(parts))
        self.date_pill.setObjectName('Pill' if (done or d != dt.date.today()) else 'PillOk')
        self.date_pill.style().unpolish(self.date_pill)
        self.date_pill.style().polish(self.date_pill)

    # input file
    def refresh_files(self, select=None):
        self.file_combo.blockSignals(True)
        self.file_combo.clear()
        for f in self.folder.shipping_files():
            t = dt.datetime.fromtimestamp(os.path.getmtime(f))
            self.file_combo.addItem(f'{os.path.basename(f)}   ·   {t:%m-%d %H:%M}', f)
        if select:
            if self.file_combo.findData(select) < 0:
                self.file_combo.insertItem(0, os.path.basename(select), select)
            self.file_combo.setCurrentIndex(self.file_combo.findData(select))
        self.file_combo.blockSignals(False)

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, '입력 엑셀 선택', self.folder.shipping, 'Excel (*.xlsx)')
        if path:
            self.refresh_files(select=path)
            self.start_preview()

    def start_preview(self):
        path = self.file_combo.currentData()
        self.preview = None
        for t in (self.t_orders, self.t_lines, self.t_companies, self.t_warn):
            t.value.setText('–')
        if not path:
            self.check_label.setText('ShippingInvoice 폴더에 입력 엑셀이 없습니다.')
            return
        self.check_label.setText('입력 파일을 읽는 중입니다…')
        th = PreviewThread(self.folder, path, pydate(self.work_date.date()), self.options())
        th.done.connect(self.preview_done)
        self.preview_thread = th
        th.start()

    def preview_done(self, run, error):
        if self.sender() is not self.preview_thread:
            return
        if run is None:
            self.check_label.setText(f'<span style="color:#8A1F1F">입력 파일을 읽지 못했습니다: {error}</span>')
            return
        self.preview = run
        n_lines = sum(len(s.lines) for s in run.shipments)
        self.t_orders.value.setText(f'{len(run.shipments)}건')
        self.t_lines.value.setText(f'{n_lines}줄')
        self.t_companies.value.setText(f'{len(run.invoices)}곳')
        self.t_warn.value.setText(f'경고 {len(run.warnings)}')
        ok = '<span style="color:#0F6B63">✔</span>'
        items = [f'{ok} 주문 {len(run.shipments)}건, 상품 {n_lines}줄을 읽었습니다']
        missing = [w for w in run.warnings if 'newPrice에 없습니다' in w or 'info에 없습니다' in w]
        dup = [w for w in run.warnings if '여러 번 있어' in w]
        other = len(run.warnings) - len(missing) - len(dup)
        if missing:
            items.append(f'<span style="color:#8A1F1F">✖ Base.xlsx에 없는 상품·업체 {len(missing)}건</span>')
        else:
            items.append(f'{ok} 모든 상품을 Base.xlsx에서 찾았습니다')
        if dup:
            items.append(f'<span style="color:#6B4A00">● 상품 {len(dup)}줄이 Base.xlsx에 여러 줄 있어 '
                         f'<b>맨 아래(최신) 줄</b>을 씁니다</span>')
        if other > 0:
            items.append(f'<span style="color:#6B4A00">● 그 밖의 경고 {other}건</span>')
        if run.warnings:
            items.append('<a href="#" style="color:#0F6B63">경고 모두 보기</a>')
        self.check_label.setText('<br>'.join(items))

    def show_warnings(self):
        if self.preview and self.preview.warnings:
            QMessageBox.information(self, '경고', '\n'.join(self.preview.warnings))

    # job
    def start(self, person_only):
        path = self.file_combo.currentData()
        if not path:
            QMessageBox.warning(self, TITLE, 'ShippingInvoice 폴더에 입력 엑셀이 없습니다.')
            return
        o = self.options()
        if o.address_mode == ADDRESS_API and not o.address_api_key:
            self.log('주의: API 승인키가 없어 프로그램 내 변환을 사용합니다.')
        work_date = pydate(self.work_date.date())
        overwrite = None
        if not person_only:
            prev = previous_run(self.folder, work_date, o.mode)
            if prev:
                d = SameDateDialog(self, work_date, o.mode, prev)
                if not d.exec():
                    return
                if d.choice == 'new':
                    o.invoice_seq = prev.next_seq
                else:
                    overwrite = prev.folders[-1]
                    m = RUN_DIR.match(os.path.basename(overwrite))
                    o.invoice_seq = int(m.group(3)) if m and m.group(3) else 1
        person_orders = None
        if person_only or o.make_person_invoice:
            try:
                run = preview_run(self.folder, path, work_date, o)
            except Exception as e:
                self.log(f'오류: {e}')
                return
            d = PersonPickDialog(self, person_candidates(run))
            if not d.exec():
                return
            person_orders = d.picked()
        self.log_box.clear()
        if overwrite:
            # only now, after every question was answered, so a cancel leaves the old result in place
            self.log(f'지난 결과를 옮겼습니다: {archive_run(self.folder, overwrite)}')
        self.btn_start.setEnabled(False)
        self.btn_person.setEnabled(False)
        self.btn_start.setText('작업 중…')
        self.job_started = dt.datetime.now()
        self.job_options = o
        self.thread = JobThread(self.folder, path, work_date, o, person_only, person_orders)
        self.thread.message.connect(self.log)
        self.thread.done.connect(self.finished)
        self.thread.start()

    def finished(self, ok, result):
        self.btn_start.setEnabled(True)
        self.btn_person.setEnabled(True)
        self.btn_start.setText('작업 시작  →')
        self.log('작업 완료' if ok else '작업 실패')
        self.refresh_files()
        self.update_date_status()
        if ok and result:
            run, out_dir = result
            secs = (dt.datetime.now() - self.job_started).total_seconds()
            self.show_done(run, out_dir, secs)
        elif not ok:
            QMessageBox.warning(self, TITLE, '작업이 실패했습니다. 작업 기록을 확인해 주세요.')

    def show_done(self, run, out_dir, secs):
        new = DonePage(self, run, out_dir, secs)
        self.stack.removeWidget(self.page_done)
        self.page_done.deleteLater()
        self.page_done = new
        self.stack.addWidget(new)
        self.stack.setCurrentWidget(new)

    def back_to_work(self):
        self.stack.setCurrentWidget(self.page_work)
        self.nav_buttons['work'].setChecked(True)
        self.start_preview()

    # maintenance
    def clean_backups(self):
        found = old_backups(self.folder)
        if not found:
            QMessageBox.information(self, TITLE, f'{BACKUP_KEEP_DAYS}일이 지난 백업이 없습니다.')
            return
        size = sum(n for _, n in found) / 1024 / 1024
        if QMessageBox.question(self, TITLE, f'{BACKUP_KEEP_DAYS}일이 지난 백업 {len(found)}개 ({size:,.1f} MB)를 '
                                f'지울까요?\n\n지운 파일은 Dropbox의 "삭제된 파일"에서 30일 동안 되살릴 수 있습니다.') \
                != QMessageBox.Yes:
            return
        remove_backups([p for p, _ in found])
        self.log(f'오래된 백업 {len(found)}개 ({size:,.1f} MB)를 지웠습니다.')
        QMessageBox.information(self, TITLE, f'백업 {len(found)}개를 지웠습니다.')

    def check_update(self):
        target = update.install_dir()
        if target is None:
            QMessageBox.information(self, TITLE, '폴더형으로 설치된 프로그램(InvoiceAuto 폴더)에서만 업데이트할 수 있습니다.')
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            rel = update.latest_release()
        except Exception as e:
            rel, err = None, e
        else:
            err = None
        finally:
            QApplication.restoreOverrideCursor()
        if err or rel is None:
            QMessageBox.warning(self, TITLE, f'새 버전을 확인하지 못했습니다.\n{err or "올라온 버전이 없습니다."}')
            return
        version, url, notes = rel
        if not update.is_newer(version, update.current_version()):
            QMessageBox.information(self, TITLE, f'지금 버전({update.current_version()})이 최신입니다.')
            return
        if QMessageBox.question(self, TITLE, f'새 버전 {version} 이 있습니다 (지금 {update.current_version()}).\n\n'
                                f'{notes[:600]}\n\n받아서 바꿀까요? 프로그램이 잠깐 닫혔다가 다시 열립니다. '
                                '지금 버전은 InvoiceAuto_prev 폴더에 남습니다.') != QMessageBox.Yes:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            staged = update.download_and_stage(url)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, TITLE, f'새 버전을 받지 못했습니다.\n{e}')
            return
        QApplication.restoreOverrideCursor()
        update.start_swap(staged, target)
        QApplication.quit()

    # test tools
    def fresh_test_db(self):
        if not os.path.exists(self.folder.real_db):
            QMessageBox.warning(self, TITLE, f'{self.folder.real_db} 파일이 없습니다.')
            return
        if QMessageBox.question(self, TITLE, '지금의 euromall.db 를 euromall_test.db 로 새로 복사할까요?\n'
                                             '테스트 DB에 기록한 내용은 지워집니다.') != QMessageBox.Yes:
            return
        was = self.folder.test
        self.folder.test = True
        self.folder.prepare_test_db(fresh=True)
        self.folder.test = was
        self.log('euromall.db 를 euromall_test.db 로 새로 복사했습니다.')

    def compare_results(self):
        """Old program's result folder vs this program's, plus the account book of the work date."""
        old_dir = QFileDialog.getExistingDirectory(self, '기존 프로그램 결과 폴더 선택 (예: Result\\Profoma_261118)',
                                                   os.path.join(self.folder.root, 'Result'))
        if not old_dir:
            return
        guess = os.path.join(self.folder.root, 'Result_test', os.path.basename(old_dir))
        new_dir = QFileDialog.getExistingDirectory(self, '새 프로그램 결과 폴더 선택',
                                                   guess if os.path.isdir(guess) else self.folder.root)
        if not new_dir:
            return
        self.log_box.clear()
        self.log(f'비교: {old_dir}\n   ↔ {new_dir}')
        diffs = compare.compare_folders(old_dir, new_dir)
        ymd = pydate(self.work_date.date()).isoformat()
        if os.path.exists(self.folder.real_db) and os.path.exists(self.folder.test_db):
            diffs += compare.compare_accounts(self.folder.real_db, self.folder.test_db, ymd)
        for line in compare.summary(diffs):
            self.log(line)
        report = os.path.join(new_dir, f'{compare.REPORT_PREFIX}{os.path.basename(old_dir)}.xlsx')
        compare.write_report(report, diffs)
        self.log(f'비교 결과 저장: {report}')
        self.log('  기존 프로그램의 오류를 고친 차이는 결과 파일의 "알려진 차이" 시트에 정리돼 있습니다.')
        open_path(report)


# --- same date dialog -----------------------------------------------------------------------------

def choice_button(title, desc, recommended=False):
    """A big two-line button: bold title and a muted explanation."""
    b = QPushButton()
    b.setObjectName('ChoiceRec' if recommended else 'Choice')
    lay = QVBoxLayout(b)
    lay.setContentsMargins(16, 9, 16, 9)
    lay.setSpacing(2)
    for text, css in ((title, 'font-weight: bold; font-size: 10.5pt;'), (desc, 'color: #4A5663; font-size: 9pt;')):
        l = QLabel(text)
        l.setStyleSheet(css + ' background: transparent;')
        l.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(l)
    return b


class SameDateDialog(QDialog):
    """Shown when the work date already has a result folder for this mode."""

    def __init__(self, parent, work_date, mode, prev):
        super().__init__(parent)
        self.choice = None
        self.setWindowTitle('이미 작업한 날짜')
        self.setMinimumWidth(560)
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 24, 26, 20)
        v.setSpacing(12)
        v.addWidget(label(f'{work_date:%Y-%m-%d}에 이미 작업한 기록이 있습니다', 'H1'))
        v.addWidget(label('같은 날짜와 같은 차수로 다시 만들면 그날 결과 파일과 회계·재고 기록이 바뀝니다.',
                          'Muted', wrap=True))
        info = QFrame()
        info.setObjectName('Tile')
        g = QGridLayout(info)
        g.setContentsMargins(14, 12, 14, 12)
        g.setHorizontalSpacing(24)
        kind = '견적서' if mode == MODE_PROFORMA else '청구서'
        facts = [('지난 작업', f'{kind} · {prev.last_time:%m-%d %H:%M}'),
                 ('결과 폴더', ', '.join(os.path.basename(p) for p in prev.folders)),
                 ('회계 장부', f'{prev.companies}개 업체' if prev.companies else '기록 없음'),
                 ('입금 기록', ', '.join(prev.paid) + ' 입금 있음' if prev.paid else '없음')]
        for i, (k, val) in enumerate(facts):
            cell = QVBoxLayout()
            cell.setSpacing(0)
            cell.addWidget(label(k, 'TileLabel'))
            vl = label(val)
            vl.setStyleSheet('font-weight: bold; background: transparent;' +
                             (' color: #8A1F1F;' if k == '입금 기록' and prev.paid else ''))
            cell.addWidget(vl)
            g.addLayout(cell, i // 2, i % 2)
        v.addWidget(info)
        b_new = choice_button(f'차수 {prev.next_seq:02d}로 새로 만들기  (추천)',
                              f'지난 결과는 그대로 두고 EMG-{work_date:%y%m%d}{prev.next_seq:02d}처럼 다음 번호로 만듭니다.',
                              recommended=True)
        b_over = choice_button('지난 작업 덮어쓰기',
                               '입력 파일을 고쳐서 다시 만들 때. 지난 결과 폴더는 backup 폴더로 옮깁니다.')
        b_new.clicked.connect(lambda: self.pick('new'))
        b_over.clicked.connect(lambda: self.pick('overwrite'))
        v.addWidget(b_new)
        v.addWidget(b_over)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton('취소')
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        v.addLayout(row)
        b_new.setFocus()

    def pick(self, c):
        self.choice = c
        self.accept()


# --- done page ------------------------------------------------------------------------------------

FILE_KINDS = [('export_invoice', '수출용', '#E3F1EF', '#0A4F49'), ('person_invoice', '개인용', '#E3F1EF', '#0A4F49'),
              ('invoice', '업체용', '#E3F1EF', '#0A4F49'), ('적하목록', '택배사용', '#E8EEF8', '#1E3A6B'),
              ('송장업로드', '쇼핑몰용', '#E8EEF8', '#1E3A6B'), ('CR_업로드', '택배사용', '#E8EEF8', '#1E3A6B'), ('포장리스트', '창고용', '#F1ECE3', '#5C4521'),
              ('PackingList', '창고용', '#F1ECE3', '#5C4521'), ('수입식품', '신고용', '#F1ECE3', '#5C4521'),
              ('작업기록', '기록', '#EEF1F4', '#4A5663')]


class DonePage(QWidget):
    def __init__(self, win: MainWindow, run, out_dir, secs):
        super().__init__()
        self.setObjectName('Content')
        v = QVBoxLayout(self)
        v.setContentsMargins(28, 26, 28, 24)
        v.setSpacing(16)
        s = run.settings
        kind = '견적서' if s.mode == MODE_PROFORMA else '청구서'
        carrier = 'CJ 대한통운' if s.carrier == CARRIER_CJ else '우체국'

        c, cl = card()
        h = QHBoxLayout()
        h.setSpacing(16)
        mark = label('✔')
        mark.setFixedSize(52, 52)
        mark.setAlignment(Qt.AlignCenter)
        mark.setStyleSheet(f'background: #E3F1EF; color: {ACCENT}; border-radius: 26px; font-size: 20pt;')
        h.addWidget(mark)
        t = QVBoxLayout()
        t.addWidget(label('작업이 끝났습니다', 'H1'))
        seq = f' · 차수 {s.invoice_seq:02d}' if s.invoice_seq > 1 else ''
        t.addWidget(label(f'{kind} · {carrier} · {s.work_date:%Y-%m-%d}{seq} · 걸린 시간 {secs:.0f}초', 'Muted'))
        h.addLayout(t, 1)
        b_open = QPushButton('결과 폴더 열기')
        b_open.setObjectName('Primary')
        b_open.setMinimumWidth(170)
        b_open.clicked.connect(lambda: open_path(out_dir))
        b_back = QPushButton('새 작업')
        b_back.clicked.connect(win.back_to_work)
        h.addWidget(b_open)
        h.addWidget(b_back)
        cl.addLayout(h)
        v.addWidget(c)

        files = sorted(f for f in os.listdir(out_dir)) if os.path.isdir(out_dir) else []
        xlsx = [f for f in files if f.endswith('.xlsx')]
        pdfs = [f for f in files if f.endswith('.pdf')]
        hawbs = [sh.hawb for sh in run.shipments if sh.hawb]
        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        tiles.addWidget(tile(f'{len(xlsx)}개', f'만든 엑셀 파일 (PDF {len(pdfs)}개)'))
        tiles.addWidget(tile(f'{len(hawbs)}개', f'송장번호  {hawbs[0][-6:]} ~ {hawbs[-1][-6:]}' if hawbs else '송장번호'))
        tiles.addWidget(tile(f'€ {run.export_total:,.2f}', '수출 합계 (EMG)'))
        tiles.addWidget(tile(f'{len(run.warnings)}건', '확인할 경고', warn=bool(run.warnings)))
        v.addLayout(tiles)

        row = QHBoxLayout()
        row.setSpacing(18)
        table = QTableWidget(len(xlsx), 3)
        table.setHorizontalHeaderLabels(['파일', '종류', ''])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setShowGrid(False)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        for r, f in enumerate(xlsx):
            item = QTableWidgetItem('  ' + f[:-5])
            table.setItem(r, 0, item)
            tag = next(((t, bg, fg) for key, t, bg, fg in FILE_KINDS if key.lower() in f.lower()), ('기타', '#EEF1F4', '#4A5663'))
            chip = label(tag[0])
            chip.setAlignment(Qt.AlignCenter)
            chip.setStyleSheet(f'background: {tag[1]}; color: {tag[2]}; border-radius: 9px; padding: 2px 10px; '
                               'font-size: 9pt; margin: 7px 6px;')
            table.setCellWidget(r, 1, chip)
            b = QPushButton('열기')
            b.setObjectName('Small')
            path = os.path.join(out_dir, f)
            b.clicked.connect(lambda _=False, p=path: open_path(p))
            cell = QWidget()
            cl_ = QHBoxLayout(cell)
            cl_.setContentsMargins(4, 4, 10, 4)
            cl_.addWidget(b)
            table.setCellWidget(r, 2, cell)
            table.setRowHeight(r, 44)
        table.cellDoubleClicked.connect(lambda r, _c: open_path(os.path.join(out_dir, xlsx[r])))
        row.addWidget(table, 3)

        c, cl = card('확인할 경고')
        if run.warnings:
            for w in run.warnings[:6]:
                lb = label(w, wrap=True)
                lb.setStyleSheet('background: #FFF8E8; border-radius: 8px; padding: 8px 10px;')
                cl.addWidget(lb)
            if len(run.warnings) > 6:
                cl.addWidget(label(f'그 밖에 {len(run.warnings) - 6}건은 결과 폴더의 작업기록 파일에 있습니다.',
                                   'Muted', wrap=True))
        else:
            cl.addWidget(label('경고가 없습니다.', 'Muted'))
        cl.addStretch(1)
        row.addWidget(c, 2)
        v.addLayout(row, 1)


# --- person invoice picker ------------------------------------------------------------------------

class PersonPickDialog(QDialog):
    """Order list before a run: the checked orders get a person invoice (all checked at first)."""

    def __init__(self, parent, shipments):
        super().__init__(parent)
        self.setWindowTitle('개인 인보이스 만들 주문 선택')
        self.resize(780, 500)
        self.shipments = shipments
        self.table = QTableWidget(len(shipments), 6)
        self.table.setHorizontalHeaderLabels(['', '송장번호', '업체', '주문번호', '수령인', '금액 (EUR)'])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        for r, sh in enumerate(shipments):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked)
            self.table.setItem(r, 0, chk)
            for c, v in enumerate([sh.hawb, sh.company.uid, sh.order_no, sh.first.recv_name], start=1):
                self.table.setItem(r, c, QTableWidgetItem(v))
            amount = QTableWidgetItem(f'{sh.total:,.2f}')
            amount.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(r, 5, amount)
        self.table.cellClicked.connect(self.toggle)
        for c, w in enumerate((30, 120, 60, 220, 110, 90)):
            self.table.setColumnWidth(c, w)
        self.count = QLabel()
        self.table.itemChanged.connect(lambda _: self.update_count())
        btn_all = QPushButton('전체 선택')
        btn_none = QPushButton('전체 해제')
        btn_all.clicked.connect(lambda: self.set_all(Qt.Checked))
        btn_none.clicked.connect(lambda: self.set_all(Qt.Unchecked))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        for w in (btn_all, btn_none, self.count):
            bottom.addWidget(w)
        bottom.addStretch(1)
        bottom.addWidget(bb)
        v = QVBoxLayout(self)
        v.addWidget(self.table, 1)
        v.addLayout(bottom)
        self.update_count()

    def toggle(self, row, col):
        if col != 0:
            it = self.table.item(row, 0)
            it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)

    def set_all(self, state):
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(state)

    def update_count(self):
        n = len(self.picked())
        self.count.setText(f'{n} / {len(self.shipments)}건 선택')

    def picked(self) -> set:
        return {sh.order_no for r, sh in enumerate(self.shipments)
                if self.table.item(r, 0) and self.table.item(r, 0).checkState() == Qt.Checked}


# --- 회계관리 / 재고관리 ----------------------------------------------------------------------------

class Column:
    def __init__(self, key, title, kind='text', width=90, total=None):
        self.key, self.title, self.kind, self.width, self.total = key, title, kind, width, total


class RecordDialog(QDialog):
    """Add/edit form: one field per column definition."""

    def __init__(self, parent, title, fields, row):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        self.fields = fields
        self.widgets = {}
        form = QFormLayout(self)
        form.setContentsMargins(22, 20, 22, 18)
        form.setVerticalSpacing(10)
        for key, label_, kind, extra in fields:
            v = row.get(key)
            if kind == 'date':
                w = QLineEdit(v or '')
                w.setPlaceholderText('yyyy-mm-dd')
            elif kind == 'choice':
                w = QComboBox()
                for code, text in extra.items():
                    w.addItem(text, code)
                if v is not None and w.findData(str(v)) >= 0:
                    w.setCurrentIndex(w.findData(str(v)))
            elif kind == 'int':
                w = QSpinBox(minimum=-1_000_000, maximum=1_000_000)
                w.setValue(int(v or 0))
            elif kind == 'money':
                w = QDoubleSpinBox(minimum=-1e9, maximum=1e9, decimals=2)
                w.setValue(float(v or 0))
            else:
                w = QLineEdit('' if v is None else str(v))
            self.widgets[key] = w
            form.addRow(label_, w)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.check)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def check(self):
        for key, label_, kind, _ in self.fields:
            if kind == 'date':
                v = self.widgets[key].text().strip()
                if v:
                    try:
                        dt.date.fromisoformat(v)
                    except ValueError:
                        QMessageBox.warning(self, self.windowTitle(), f'{label_}: yyyy-mm-dd 형식으로 입력하세요.')
                        return
        self.accept()

    def values(self):
        out = {}
        for key, _, kind, _ in self.fields:
            w = self.widgets[key]
            if kind == 'choice':
                out[key] = w.currentData()
            elif kind in ('int', 'money'):
                out[key] = w.value()
            else:
                out[key] = w.text().strip() or None
        return out


class RecordWindow(QWidget):
    """A page of the main window: filter bar, summary tiles, table and totals row."""
    title = ''
    heading = ''
    columns = []

    def __init__(self, folder: Folder):
        super().__init__()
        self.folder = folder
        self.rows = []
        self.setObjectName('Content')
        today = dt.date.today()
        self.date_from = date_edit(today.replace(day=1))
        self.date_to = date_edit(today)
        self.company = QComboBox()
        self.company.setMinimumWidth(130)
        self.company.addItem('전체 업체', None)
        for uid in db.companies(folder.db):
            self.company.addItem(uid, uid)

        v = QVBoxLayout(self)
        v.setContentsMargins(28, 26, 28, 24)
        v.setSpacing(14)
        head = QHBoxLayout()
        t = QVBoxLayout()
        t.setSpacing(2)
        t.addWidget(label(self.title + ('  ·  테스트 DB' if folder.test else ''), 'Eyebrow'))
        t.addWidget(label(self.heading, 'H1'))
        head.addLayout(t)
        head.addStretch(1)
        b = QPushButton('엑셀로 내보내기')
        b.clicked.connect(self.export)
        head.addWidget(b)
        b = QPushButton('+ 추가')
        b.setStyleSheet(f'background: {ACCENT}; color: #FFFFFF; border: none; font-weight: bold;')
        b.clicked.connect(self.add)
        head.addWidget(b)
        v.addLayout(head)

        bar, bl = card()
        bl.setContentsMargins(16, 12, 16, 12)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(label('기간', 'Muted'))
        row.addWidget(self.date_from)
        row.addWidget(label('~', 'Muted'))
        row.addWidget(self.date_to)
        row.addSpacing(10)
        row.addWidget(label('업체', 'Muted'))
        row.addWidget(self.company)
        self.extra_filters(row)
        row.addStretch(1)
        for text, fn in (('수정', self.edit), ('삭제', self.delete), ('조회', self.query)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        bl.addLayout(row)
        v.addWidget(bar)

        self.tiles = QHBoxLayout()
        self.tiles.setSpacing(12)
        v.addLayout(self.tiles)

        self.table = QTableWidget(0, len(self.columns) + 1)
        self.table.setHorizontalHeaderLabels(['순번'] + [c.title for c in self.columns])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        self.table.doubleClicked.connect(self.edit)
        self.table.setColumnWidth(0, 50)
        for i, c in enumerate(self.columns):
            self.table.setColumnWidth(i + 1, c.width)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)

        self.totals = QTableWidget(1, len(self.columns) + 1)
        self.totals.horizontalHeader().setVisible(False)
        self.totals.verticalHeader().setVisible(False)
        self.totals.setFixedHeight(36)
        self.totals.setShowGrid(False)
        self.totals.setEditTriggers(QTableWidget.NoEditTriggers)
        self.totals.setStyleSheet('QTableWidget { background: #F6F8FA; font-weight: bold; }')
        self.table.horizontalHeader().sectionResized.connect(lambda i, _, w: self.totals.setColumnWidth(i, w))
        self.table.horizontalScrollBar().valueChanged.connect(self.totals.horizontalScrollBar().setValue)
        self.totals.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for i in range(self.table.columnCount()):
            self.totals.setColumnWidth(i, self.table.columnWidth(i))
        self.totals.horizontalHeader().setStretchLastSection(True)

        v.addWidget(self.table, 1)
        v.addWidget(self.totals)
        v.addWidget(label('줄을 두 번 누르면 수정 창이 열립니다.', 'Muted'))
        self.query()

    # to implement per window
    def fetch(self, date_from, date_to, company):
        raise NotImplementedError

    def extra_filters(self, row):
        pass

    def summary(self):
        return []

    def display(self, col: Column, row):
        v = row.get(col.key)
        if col.kind == 'money':
            return f'{float(v or 0):,.2f}'
        if col.kind == 'int':
            return '' if v in (None, '') else str(int(v))
        return '' if v is None else str(v)

    def query(self):
        self.rows = self.fetch(pydate(self.date_from.date()).isoformat(), pydate(self.date_to.date()).isoformat(),
                               self.company.currentData())
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            nr = QTableWidgetItem(str(r + 1))
            nr.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 0, nr)
            for i, c in enumerate(self.columns):
                it = QTableWidgetItem(self.display(c, row))
                if c.kind in ('money', 'int'):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                elif c.kind in ('date', 'choice'):
                    it.setTextAlignment(Qt.AlignCenter)
                self.style_item(c, row, it)
                self.table.setItem(r, i + 1, it)
        self.totals.setItem(0, 0, QTableWidgetItem(f'합계 {len(self.rows)}건'))
        for i, c in enumerate(self.columns):
            text = ''
            if c.total and self.rows:
                vals = [float(row.get(c.key) or 0) for row in self.rows]
                val = sum(vals) if c.total == 'sum' else sum(vals) / len(vals)
                text = f'{val:,.2f}'
            it = QTableWidgetItem(text)
            it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.totals.setItem(0, i + 1, it)
        while self.tiles.count():
            w = self.tiles.takeAt(0).widget()
            if w:
                w.deleteLater()
        for value, text, warn in self.summary():
            self.tiles.addWidget(tile(value, text, warn))

    def style_item(self, col, row, item):
        pass

    def selected(self):
        r = self.table.currentRow()
        return self.rows[r] if 0 <= r < len(self.rows) else None

    def add(self):
        row = {'ymd': pydate(self.date_to.date()).isoformat(), 'company_uid': self.company.currentData()}
        self.open_dialog('추가', row)

    def edit(self):
        row = self.selected()
        if row:
            self.open_dialog('수정', dict(row))

    def open_dialog(self, verb, row):
        d = RecordDialog(self, f'{self.title} {verb}', self.fields(), row)
        if d.exec():
            row.update(d.values())
            self.save(row)
            self.query()

    def delete(self):
        row = self.selected()
        if row and QMessageBox.question(self, self.title, '선택한 줄을 삭제할까요?') == QMessageBox.Yes:
            self.remove(row)
            self.query()

    def export(self):
        default = os.path.join(self.folder.result, f'{self.export_name}_{dt.date.today():%Y%m%d}.xlsx')
        path, _ = QFileDialog.getSaveFileName(self, '엑셀 저장', default, 'Excel (*.xlsx)')
        if not path:
            return
        wb = self.workbook()
        wb.save(path)
        QMessageBox.information(self, self.title, f'저장했습니다.\n{path}')

    def workbook(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['Nr.'] + [c.title for c in self.columns])
        for i, row in enumerate(self.rows):
            ws.append([i + 1] + [self.cell_value(c, row) for c in self.columns])
        return wb

    def cell_value(self, c, row):
        v = row.get(c.key)
        if c.kind == 'money':
            return float(v or 0)
        return self.display(c, row) if c.kind == 'choice' else v


class AccountWindow(RecordWindow):
    title = '회계관리'
    heading = '업체별 견적 · 청구 · 입금'
    export_name = 'AccountBook'
    columns = [Column('ymd', '일자', 'date'), Column('company_uid', '업체', width=60),
               Column('invoice_no', '인보이스 번호', width=165), Column('status_cd', '상태', 'choice', 70),
               Column('profomal_amount', '견적', 'money', 100, total='sum'),
               Column('release_amount', '청구', 'money', 100, total='sum'),
               Column('deposit_amount', '입금', 'money', 100, total='sum'),
               Column('deposit_ymd', '입금일', 'date'), Column('remark', '비고', width=120),
               Column('seq', 'SEQ', 'int', 50)]

    def extra_filters(self, row):
        self.chk_unpaid = QCheckBox('미결제만')
        self.chk_unpaid.toggled.connect(lambda _: self.query())
        row.addSpacing(10)
        row.addWidget(self.chk_unpaid)
        b = QPushButton('업체별 요약')
        b.setToolTip('지금 기간의 업체별 견적·청구·입금과 받을 돈')
        b.clicked.connect(self.show_summary)
        row.addWidget(b)

    def show_summary(self):
        a, b = pydate(self.date_from.date()), pydate(self.date_to.date())
        rows = db.account_rows(self.folder.db, a.isoformat(), b.isoformat(), self.company.currentData())
        CompanySummaryDialog(self, a, b, db.company_summary(rows)).exec()

    def fetch(self, a, b, company):
        rows = db.account_rows(self.folder.db, a, b, company)
        if getattr(self, 'chk_unpaid', None) and self.chk_unpaid.isChecked():
            rows = [r for r in rows if str(r.get('status_cd')) == db.STATUS_UNPAID]
        return rows

    def summary(self):
        s = lambda k: sum(float(r.get(k) or 0) for r in self.rows)
        open_ = sum(float(r.get('release_amount') or 0) - float(r.get('deposit_amount') or 0)
                    for r in self.rows if str(r.get('status_cd')) == db.STATUS_UNPAID and r.get('company_uid') != 'EMG')
        return [(f'€ {s("profomal_amount"):,.2f}', '견적 합계', False),
                (f'€ {s("release_amount"):,.2f}', '청구 합계', False),
                (f'€ {s("deposit_amount"):,.2f}', '입금 합계', False),
                (f'€ {max(open_, 0):,.2f}', '받을 돈 (미결제 청구 − 입금, EMG 제외)', open_ > 0)]

    def style_item(self, col, row, item):
        if col.key == 'status_cd':
            st = str(row.get('status_cd'))
            item.setForeground(QColor({db.STATUS_UNPAID: '#8A1F1F', db.STATUS_PAID: ACCENT}.get(st, MUTED)))

    def display(self, col, row):
        if col.key == 'status_cd':
            return db.STATUS_LABELS.get(str(row.get('status_cd')), row.get('status_cd') or '')
        return super().display(col, row)

    def fields(self):
        return [('ymd', '일자', 'date', None), ('company_uid', '업체', 'text', None),
                ('invoice_no', '인보이스 번호', 'text', None), ('status_cd', '상태', 'choice', db.STATUS_LABELS),
                ('profomal_amount', '견적 (Proforma)', 'money', None), ('release_amount', '청구 (Rechnung)', 'money', None),
                ('deposit_amount', '입금', 'money', None), ('deposit_ymd', '입금일', 'date', None),
                ('remark', '비고', 'text', None)]

    def save(self, row):
        db.save_account_row(self.folder.db, row)

    def remove(self, row):
        db.delete_account_row(self.folder.db, row['seq'])

    def workbook(self):
        """Uses Base/AccountBook.xlsx (header row and styles) when it is there."""
        tpl = os.path.join(self.folder.base, 'AccountBook.xlsx')
        if not os.path.exists(tpl):
            return super().workbook()
        wb = openpyxl.load_workbook(tpl)
        ws = wb.worksheets[0]
        order = ['ymd', 'company_uid', 'invoice_no', 'status_cd', 'profomal_amount', 'release_amount',
                 'deposit_amount', 'deposit_ymd', 'remark', 'seq']
        by_key = {c.key: c for c in self.columns}
        styles = [copy(ws.cell(2, col)._style) for col in range(1, 12)]
        for i, row in enumerate(self.rows):
            r = i + 2
            values = [i + 1] + [self.cell_value(by_key[k], row) for k in order]
            for col, v in enumerate(values, start=1):
                cell = ws.cell(r, col, v)
                cell._style = copy(styles[col - 1])
        return wb


class CompanySummaryDialog(QDialog):
    COLS = [('company_uid', '업체'), ('count', '건수'), ('proforma', '견적'), ('release', '청구'), ('deposit', '입금'),
            ('open', '받을 돈'), ('unpaid_count', '미결제 건수'), ('oldest_unpaid', '첫 미결제일')]

    def __init__(self, parent, date_from, date_to, data):
        super().__init__(parent)
        self.data, self.date_from, self.date_to = data, date_from, date_to
        self.setWindowTitle('업체별 요약')
        self.resize(860, 520)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.addWidget(label(f'업체별 요약  ·  {date_from:%Y-%m-%d} ~ {date_to:%Y-%m-%d}', 'CardTitle'))
        total_open = sum(d['open'] for d in data)
        v.addWidget(label(f'받을 돈 합계 € {total_open:,.2f}  (미결제 청구 − 입금, EMG 제외, 받을 돈이 큰 순)', 'Muted'))
        t = QTableWidget(len(data) + 1, len(self.COLS))
        t.setHorizontalHeaderLabels([c[1] for c in self.COLS])
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setShowGrid(False)
        t.setAlternatingRowColors(True)
        totals = {k: sum(d[k] for d in data) for k in ('count', 'proforma', 'release', 'deposit', 'open', 'unpaid_count')}
        for r, d in enumerate(data + [dict(totals, company_uid='합계', oldest_unpaid='')]):
            for c, (k, _) in enumerate(self.COLS):
                val = d.get(k)
                txt = f'{val:,.2f}' if isinstance(val, float) else ('' if val is None else str(val))
                it = QTableWidgetItem(txt)
                if isinstance(val, (int, float)):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if k == 'open' and isinstance(val, float) and val > 0:
                    it.setForeground(QColor('#8A1F1F'))
                if r == len(data):
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                t.setItem(r, c, it)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        v.addWidget(t, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        b = QPushButton('엑셀로 내보내기')
        b.clicked.connect(self.export)
        row.addWidget(b)
        b = QPushButton('닫기')
        b.clicked.connect(self.accept)
        row.addWidget(b)
        v.addLayout(row)

    def export(self):
        name = f'업체별요약_{self.date_from:%Y%m%d}-{self.date_to:%Y%m%d}.xlsx'
        path, _ = QFileDialog.getSaveFileName(self, '엑셀로 내보내기', name, 'Excel (*.xlsx)')
        if not path:
            return
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = '업체별 요약'
        ws.append([c[1] for c in self.COLS])
        for d in self.data:
            ws.append([d.get(k) for k, _ in self.COLS])
        wb.save(path)
        open_path(path)


class StockWindow(RecordWindow):
    title = '재고관리'
    heading = '입고 · 출고 기록'
    export_name = 'Stock'
    TYPES = {'I': '입고', 'O': '출고'}
    columns = [Column('ymd', '일자', 'date'), Column('type', '구분', 'choice', 60),
               Column('company_name', '회사명', width=120), Column('item_name', '상품명', width=300),
               Column('quantity', '수량', 'int', 60, total='sum'), Column('item_price', '단가', 'money', 80, 'avg'),
               Column('amount', '총액', 'money', 90, 'sum'), Column('remark', '비고', width=120)]

    def fetch(self, a, b, company):
        rows = db.stock_rows(self.folder.db, a, b, company)
        return sorted(rows, key=lambda r: (r['ymd'], (r['company_name'] or '').lower(), r['item_name'] or ''))

    def summary(self):
        qi = sum(int(r.get('quantity') or 0) for r in self.rows if r.get('type') == 'I')
        qo = sum(int(r.get('quantity') or 0) for r in self.rows if r.get('type') == 'O')
        amt = sum(float(r.get('amount') or 0) for r in self.rows if r.get('type') == 'O')
        return [(f'{qi:,}개', '입고 수량', False), (f'{qo:,}개', '출고 수량', False), (f'€ {amt:,.2f}', '출고 금액', False)]

    def display(self, col, row):
        if col.key == 'type':
            return self.TYPES.get(row.get('type'), row.get('type') or '')
        return super().display(col, row)

    def fields(self):
        return [('ymd', '일자', 'date', None), ('type', '구분', 'choice', self.TYPES),
                ('company_uid', '회사 코드', 'text', None), ('company_name', '회사명', 'text', None),
                ('item_name', '상품명', 'text', None), ('quantity', '수량', 'int', None),
                ('item_price', '단가', 'money', None), ('amount', '총액 (0이면 수량×단가)', 'money', None),
                ('remark', '비고', 'text', None)]

    def save(self, row):
        if not row.get('amount'):
            row['amount'] = round(float(row.get('quantity') or 0) * float(row.get('item_price') or 0), 2)
        if not row.get('company_uid'):
            QMessageBox.warning(self, self.title, '회사 코드를 입력하세요.')
            return
        db.save_stock_row(self.folder.db, row)

    def remove(self, row):
        db.delete_stock_row(self.folder.db, row['seq'])


# --- start-up: one window per folder, brought to the front ------------------------------------------

def program_folder():
    """Folder with Base/, db/ and ShippingInvoice/: the first argument, else next to the exe (or this source)."""
    if len(sys.argv) > 1 and os.path.isdir(sys.argv[1]):
        return sys.argv[1]
    if getattr(sys, 'frozen', False):
        here = os.path.dirname(sys.executable)
        # folder build: the program lives in <work folder>\InvoiceAuto\, the data one level up
        parent = os.path.dirname(here)
        if not os.path.isdir(os.path.join(here, 'Base')) and os.path.isdir(os.path.join(parent, 'Base')):
            return parent
        return here
    return os.getcwd()


def instance_name(root):
    import hashlib
    return 'InvoiceAuto-' + hashlib.md5(os.path.abspath(root).lower().encode('utf-8')).hexdigest()[:12]


def bring_to_front(w: QWidget):
    """Raises the window over the others and gives it the keyboard focus (Windows only lets a program take the
    foreground in some cases, so it is briefly made a top-most window)."""
    if w.isMinimized():
        w.showNormal()
    w.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    w.show()
    w.raise_()
    w.activateWindow()

    def release():
        w.setWindowFlag(Qt.WindowStaysOnTopHint, False)
        w.show()
        w.raise_()
        w.activateWindow()
    QTimer.singleShot(300, release)


def close_splash():
    """Closes the PyInstaller start-up splash ('불러오는 중'), if the exe has one."""
    try:
        import pyi_splash  # only inside the exe built with --splash
        pyi_splash.close()
    except Exception:
        pass


def setup_app(app):
    """Same look on every Windows version: Fusion style, Korean day names, the stylesheet."""
    app.setStyle('Fusion')
    QLocale.setDefault(QLocale(QLocale.Korean, QLocale.SouthKorea))
    import tempfile
    check = os.path.join(tempfile.gettempdir(), 'invoiceauto_check.svg')
    try:
        with open(check, 'w') as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 18 18"><path d="M4 9.5l3.2 3.2L14 5.8" '
                    'fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>')
    except OSError:
        pass
    app.setStyleSheet(STYLE.replace('CHECK_SVG', check.replace('\\', '/')))


def main():
    app = QApplication(sys.argv)
    root = program_folder()
    name = instance_name(root)

    # already open for this folder: show that window instead of a second one
    sock = QLocalSocket()
    sock.connectToServer(name)
    if sock.waitForConnected(500):
        sock.write(b'show')
        sock.flush()
        sock.waitForBytesWritten(500)
        sock.disconnectFromServer()
        close_splash()
        return
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.listen(name)

    setup_app(app)
    other = read_lock(root)
    if other:
        close_splash()
        m = QMessageBox(QMessageBox.Warning, TITLE,
                        f"다른 컴퓨터({other.get('computer')})에서 InvoiceAuto를 쓰고 있습니다.\n"
                        f"마지막 확인: {other['heartbeat_time']:%Y-%m-%d %H:%M}\n\n"
                        "두 컴퓨터에서 동시에 작업하면 Dropbox가 회계 장부(db)를 두 개로 갈라 놓을 수 있습니다.\n"
                        "그 컴퓨터에서 프로그램을 닫은 뒤 여는 것이 안전합니다.")
        b_open = m.addButton('그래도 열기', QMessageBox.DestructiveRole)
        m.addButton('닫기', QMessageBox.RejectRole)
        m.exec()
        if m.clickedButton() is not b_open:
            return
    write_lock(root)
    beat = QTimer()
    beat.timeout.connect(lambda: write_lock(root))
    beat.start(2 * 60 * 1000)
    app.aboutToQuit.connect(lambda: release_lock(root))
    w = MainWindow(Folder(root))

    def on_connection():
        c = server.nextPendingConnection()
        if c:
            c.readAll()
            c.disconnectFromServer()
        bring_to_front(w)
    server.newConnection.connect(on_connection)

    w.show()
    close_splash()
    bring_to_front(w)
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
