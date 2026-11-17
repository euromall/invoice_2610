"""Windows: main screen (작업시작), Account Book and Stock, laid out like the old program."""
import datetime as dt
import os
import sys
import traceback
from copy import copy

import openpyxl
from PySide6.QtCore import Qt, QDate, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from . import db, compare
from .app import (Folder, Options, load_options, save_options, run_job, preview_run, person_candidates,
                  ADDRESS_API, ADDRESS_OFFLINE)
from .engine import MODE_PROFORMA, MODE_BILL, CARRIER_POST, CARRIER_CJ

TITLE = 'Euromall GmbH (euromallgmbh@gmail.com)'

STYLE = """
QWidget { background: #2f3b44; color: #e8edf0; font-size: 10pt; }
QPlainTextEdit, QTableWidget, QLineEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox {
    background: #4a5862; border: 1px solid #5d6b75; }
QGroupBox { border: 1px solid #5d6b75; margin-top: 8px; padding-top: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; }
QPushButton { background: #26323a; border: none; padding: 7px 14px; }
QPushButton:hover { background: #3c4a54; }
QPushButton:disabled { color: #7f8b93; }
QHeaderView::section { background: #26323a; color: #e8edf0; font-weight: bold; border: 1px solid #3c4a54; }
QTableWidget { gridline-color: #3c4a54; selection-background-color: #7fb3a8; selection-color: #1d262c; }
"""


def qdate(d: dt.date) -> QDate:
    return QDate(d.year, d.month, d.day)


def pydate(q: QDate) -> dt.date:
    return dt.date(q.year(), q.month(), q.day())


def date_edit(d: dt.date) -> QDateEdit:
    w = QDateEdit(qdate(d))
    w.setCalendarPopup(True)
    w.setDisplayFormat('yyyy-MM-dd')
    return w


# --- main screen ---------------------------------------------------------------------------------

class JobThread(QThread):
    message = Signal(str)
    done = Signal(bool)

    def __init__(self, folder, shipping_file, work_date, options, person_only, person_orders=None):
        super().__init__()
        self.args = (folder, shipping_file, work_date, options)
        self.person_only = person_only
        self.person_orders = person_orders

    def run(self):
        try:
            run_job(*self.args, log=self.message.emit, person_only=self.person_only,
                    person_orders=self.person_orders)
            self.done.emit(True)
        except Exception as e:
            self.message.emit(f'오류: {e}')
            self.message.emit(traceback.format_exc())
            self.done.emit(False)


class MainWindow(QMainWindow):
    def __init__(self, folder: Folder):
        super().__init__()
        self.folder = folder
        self.thread = None
        self.child_windows = []
        self.setWindowTitle(TITLE)
        self.resize(820, 760)

        self.log_box = QPlainTextEdit(readOnly=True)
        self.log_box.setFont(QFont('Consolas', 9))

        # 설정
        self.work_date = date_edit(dt.date.today())
        self.chk_hawb = QCheckBox('송장번호 자동입력')
        self.chk_pdf = QCheckBox('pdf 저장')
        self.chk_account = QCheckBox('회계반영')
        self.chk_person = QCheckBox('개인 인보이스 생성')
        self.chk_export = QCheckBox('업체별 수출용 인보이스 생성')
        self.chk_merge = QCheckBox('적하목록 취합 무게병합 생성')
        g_set = QGroupBox('설정')
        lay = QGridLayout(g_set)
        lay.addWidget(QLabel('작업일자'), 0, 0)
        lay.addWidget(self.work_date, 0, 1)
        for i, w in enumerate([self.chk_hawb, self.chk_pdf, self.chk_account, self.chk_person, self.chk_export,
                               self.chk_merge]):
            lay.addWidget(w, i + 1, 0, 1, 2)

        # 회계
        self.rdb_proforma = QRadioButton('견적서용')
        self.rdb_bill = QRadioButton('청구서용')
        self.mode_group = QButtonGroup(self)
        g_mode = self._radio_box('회계', [self.rdb_proforma, self.rdb_bill], self.mode_group)
        self.rdb_proforma.toggled.connect(self.mode_changed)

        # 송장번호
        self.rdb_post = QRadioButton('우체국')
        self.rdb_cj = QRadioButton('CJ 대한통운')
        g_carrier = self._radio_box('송장번호', [self.rdb_post, self.rdb_cj], QButtonGroup(self))

        # 영문주소 (개인 인보이스)
        self.rdb_addr_offline = QRadioButton('프로그램 내 변환')
        self.rdb_addr_api = QRadioButton('행정안전부 API')
        self.api_key = QLineEdit(placeholderText='API 승인키')
        g_addr = self._radio_box('영문주소', [self.rdb_addr_offline, self.rdb_addr_api], QButtonGroup(self))
        g_addr.layout().insertWidget(2, self.api_key)
        self.rdb_addr_api.toggled.connect(lambda on: self.api_key.setEnabled(on))

        # 테스트 (기존 프로그램과 병행 비교)
        self.chk_test = QCheckBox('테스트 모드')
        self.chk_test.setToolTip('결과는 Result_test, 회계·재고는 db/euromall_test.db 에 기록합니다.')
        btn_fresh = QPushButton('테스트 DB 새로 복사')
        btn_compare = QPushButton('결과 비교')
        btn_fresh.clicked.connect(self.fresh_test_db)
        btn_compare.clicked.connect(self.compare_results)
        self.chk_test.toggled.connect(self.test_mode_changed)
        g_test = QGroupBox('테스트')
        tl = QVBoxLayout(g_test)
        for w in (self.chk_test, btn_fresh, btn_compare):
            tl.addWidget(w)
        tl.addStretch(1)

        # 입력 파일
        self.file_combo = QComboBox()
        btn_browse = QPushButton('찾기')
        btn_browse.clicked.connect(self.browse)
        g_file = QGroupBox('입력 파일 (ShippingInvoice)')
        fl = QHBoxLayout(g_file)
        fl.addWidget(self.file_combo, 1)
        fl.addWidget(btn_browse)

        groups = QHBoxLayout()
        for g in (g_set, g_mode, g_carrier, g_addr, g_test):
            groups.addWidget(g)
        groups.addStretch(1)

        self.btn_person = QPushButton('개인 인보이스만 생성')
        btn_save = QPushButton('설정저장')
        btn_load = QPushButton('설정불러오기')
        btn_stock = QPushButton('재고관리')
        btn_account = QPushButton('회계관리')
        self.btn_start = QPushButton('작업시작')
        self.btn_person.clicked.connect(lambda: self.start(person_only=True))
        btn_save.clicked.connect(self.save_settings)
        btn_load.clicked.connect(self.load_settings)
        btn_stock.clicked.connect(lambda: self.open_window(StockWindow))
        btn_account.clicked.connect(lambda: self.open_window(AccountWindow))
        self.btn_start.clicked.connect(lambda: self.start(person_only=False))
        buttons = QHBoxLayout()
        buttons.setSpacing(1)
        for b in (self.btn_person, btn_save, btn_load, btn_stock, btn_account):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_start)

        root = QWidget()
        v = QVBoxLayout(root)
        v.addWidget(self.log_box, 1)
        v.addWidget(g_file)
        v.addLayout(groups)
        v.addLayout(buttons)
        self.setCentralWidget(root)

        self.refresh_files()
        self.apply_options(load_options(folder.ini))
        self.log(f'프로그램 폴더: {os.path.abspath(folder.root)}')
        missing = [p for p in (os.path.join(folder.base, 'Base.xlsx'), folder.db) if not os.path.exists(p)]
        for p in missing:
            self.log(f'주의: {p} 파일이 없습니다.')

    def _radio_box(self, title, radios, group):
        box = QGroupBox(title)
        lay = QVBoxLayout(box)
        for r in radios:
            group.addButton(r)
            lay.addWidget(r)
        lay.addStretch(1)
        return box

    def log(self, s):
        self.log_box.appendPlainText(s)

    # settings
    def options(self) -> Options:
        return Options(
            mode=MODE_PROFORMA if self.rdb_proforma.isChecked() else MODE_BILL,
            carrier=CARRIER_POST if self.rdb_post.isChecked() else CARRIER_CJ,
            auto_hawb=self.chk_hawb.isChecked(), save_pdf=self.chk_pdf.isChecked(),
            apply_accounting=self.chk_account.isChecked(), make_person_invoice=self.chk_person.isChecked(),
            make_export_per_company=self.chk_export.isChecked(), make_cargo_weight_merge=self.chk_merge.isChecked(),
            address_mode=ADDRESS_API if self.rdb_addr_api.isChecked() else ADDRESS_OFFLINE,
            address_api_key=self.api_key.text().strip(), test_mode=self.chk_test.isChecked())

    def apply_options(self, o: Options):
        self.rdb_proforma.blockSignals(True)
        (self.rdb_proforma if o.mode == MODE_PROFORMA else self.rdb_bill).setChecked(True)
        self.rdb_proforma.blockSignals(False)
        (self.rdb_post if o.carrier == CARRIER_POST else self.rdb_cj).setChecked(True)
        self.chk_hawb.setChecked(o.auto_hawb)
        self.chk_pdf.setChecked(o.save_pdf)
        self.chk_account.setChecked(o.apply_accounting)
        self.chk_person.setChecked(o.make_person_invoice)
        self.chk_export.setChecked(o.make_export_per_company)
        self.chk_merge.setChecked(o.make_cargo_weight_merge)
        (self.rdb_addr_api if o.address_mode == ADDRESS_API else self.rdb_addr_offline).setChecked(True)
        self.api_key.setText(o.address_api_key)
        self.api_key.setEnabled(o.address_mode == ADDRESS_API)
        self.chk_test.setChecked(o.test_mode)
        self.test_mode_changed(o.test_mode)

    def mode_changed(self, proforma_on):
        """견적서용/청구서용 each keep their own checkbox set, like the old program."""
        mode = MODE_PROFORMA if proforma_on else MODE_BILL
        o = load_options(self.folder.ini, mode)
        cur = self.options()
        o.address_mode, o.address_api_key = cur.address_mode, cur.address_api_key
        self.apply_options(o)

    def save_settings(self):
        save_options(self.folder.ini, self.options())
        self.log('설정을 저장했습니다.')

    def load_settings(self):
        self.apply_options(load_options(self.folder.ini, self.options().mode))
        self.log('설정을 불러왔습니다.')

    # input file
    def refresh_files(self, select=None):
        self.file_combo.clear()
        for f in self.folder.shipping_files():
            self.file_combo.addItem(os.path.basename(f), f)
        if select:
            if self.file_combo.findData(select) < 0:
                self.file_combo.insertItem(0, os.path.basename(select), select)
            self.file_combo.setCurrentIndex(self.file_combo.findData(select))

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, '입력 엑셀 선택', self.folder.shipping, 'Excel (*.xlsx)')
        if path:
            self.refresh_files(select=path)

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
        self.btn_start.setEnabled(False)
        self.btn_person.setEnabled(False)
        self.thread = JobThread(self.folder, path, work_date, o, person_only, person_orders)
        self.thread.message.connect(self.log)
        self.thread.done.connect(self.finished)
        self.thread.start()

    def finished(self, ok):
        self.btn_start.setEnabled(True)
        self.btn_person.setEnabled(True)
        self.log('작업 완료' if ok else '작업 실패')
        self.refresh_files()

    def test_mode_changed(self, on):
        self.folder.test = on
        self.setWindowTitle(TITLE + ('  [테스트 모드]' if on else ''))

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
        old_dir = QFileDialog.getExistingDirectory(self, '기존 프로그램 결과 폴더 선택',
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
        if sys.platform == 'win32':
            os.startfile(report)

    def open_window(self, cls):
        if not os.path.exists(self.folder.db):
            QMessageBox.warning(self, TITLE, f'{self.folder.db} 파일이 없습니다.')
            return
        w = cls(self.folder)
        w.show()
        self.child_windows = [x for x in self.child_windows if x.isVisible()] + [w]


class PersonPickDialog(QDialog):
    """Order list before a run: the checked orders get a person invoice (all checked at first)."""

    def __init__(self, parent, shipments):
        super().__init__(parent)
        self.setWindowTitle('개인 인보이스 만들 주문 선택')
        self.resize(760, 480)
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


# --- Account Book / Stock ------------------------------------------------------------------------

class Column:
    def __init__(self, key, title, kind='text', width=90, total=None):
        self.key, self.title, self.kind, self.width, self.total = key, title, kind, width, total


class RecordDialog(QDialog):
    """Add/edit form: one field per column definition."""

    def __init__(self, parent, title, fields, row):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.fields = fields
        self.widgets = {}
        form = QFormLayout(self)
        for key, label, kind, extra in fields:
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
            form.addRow(label, w)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.check)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def check(self):
        for key, label, kind, _ in self.fields:
            if kind == 'date':
                v = self.widgets[key].text().strip()
                if v:
                    try:
                        dt.date.fromisoformat(v)
                    except ValueError:
                        QMessageBox.warning(self, self.windowTitle(), f'{label}: yyyy-mm-dd 형식으로 입력하세요.')
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
    title = ''
    columns = []

    def __init__(self, folder: Folder):
        super().__init__()
        self.folder = folder
        self.rows = []
        self.setWindowTitle(self.title + ('  [테스트 DB]' if folder.test else ''))
        self.resize(900, 520)
        today = dt.date.today()
        self.date_from = date_edit(today)
        self.date_to = date_edit(today)
        self.company = QComboBox()
        self.company.addItem('전체', None)
        for uid in db.companies(folder.db):
            self.company.addItem(uid, uid)

        top = QHBoxLayout()
        top.addWidget(self.date_from)
        top.addWidget(self.date_to)
        top.addWidget(self.company)
        top.addStretch(1)
        for text, fn in (('엑셀', self.export), ('추가', self.add), ('수정', self.edit), ('삭제', self.delete),
                         ('조회', self.query)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            top.addWidget(b)

        self.table = QTableWidget(0, len(self.columns) + 1)
        self.table.setHorizontalHeaderLabels(['Nr.' if self.title == 'Account Book' else '순번'] +
                                             [c.title for c in self.columns])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.doubleClicked.connect(self.edit)
        self.table.setColumnWidth(0, 50)
        for i, c in enumerate(self.columns):
            self.table.setColumnWidth(i + 1, c.width)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)

        self.totals = QTableWidget(1, len(self.columns) + 1)
        self.totals.horizontalHeader().setVisible(False)
        self.totals.verticalHeader().setVisible(False)
        self.totals.setFixedHeight(28)
        self.totals.setEditTriggers(QTableWidget.NoEditTriggers)
        self.totals.setStyleSheet('QTableWidget { background: #26323a; font-weight: bold; }')
        self.table.horizontalHeader().sectionResized.connect(
            lambda i, _, w: self.totals.setColumnWidth(i, w))
        self.table.horizontalScrollBar().valueChanged.connect(self.totals.horizontalScrollBar().setValue)
        self.totals.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for i in range(self.table.columnCount()):
            self.totals.setColumnWidth(i, self.table.columnWidth(i))

        v = QVBoxLayout(self)
        v.addLayout(top)
        v.addWidget(self.table, 1)
        v.addWidget(self.totals)
        self.query()

    # to implement per window
    def fetch(self, date_from, date_to, company):
        raise NotImplementedError

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
                self.table.setItem(r, i + 1, it)
        self.totals.setItem(0, 0, QTableWidgetItem('합계'))
        for i, c in enumerate(self.columns):
            text = ''
            if c.total and self.rows:
                vals = [float(row.get(c.key) or 0) for row in self.rows]
                val = sum(vals) if c.total == 'sum' else sum(vals) / len(vals)
                text = f'{val:,.2f}'
            it = QTableWidgetItem(text)
            it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.totals.setItem(0, i + 1, it)

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
        default = os.path.join(self.folder.result, f'{self.title.replace(" ", "")}_{dt.date.today():%Y%m%d}.xlsx')
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
    title = 'Account Book'
    columns = [Column('ymd', 'Datum', 'date'), Column('company_uid', 'Firma', width=70),
               Column('invoice_no', 'Invoice Nr.', width=110), Column('status_cd', 'Abteilung', 'choice', 80),
               Column('profomal_amount', 'Proforma', 'money', total='sum'),
               Column('release_amount', 'Rechnung', 'money', total='sum'),
               Column('deposit_amount', 'Einzahlung', 'money', total='sum'),
               Column('deposit_ymd', 'Datum', 'date'), Column('remark', 'Bemerkung', width=120),
               Column('seq', 'SEQ', 'int', 60)]

    def fetch(self, a, b, company):
        return db.account_rows(self.folder.db, a, b, company)

    def display(self, col, row):
        if col.key == 'status_cd':
            return db.STATUS_LABELS.get(str(row.get('status_cd')), row.get('status_cd') or '')
        return super().display(col, row)

    def fields(self):
        return [('ymd', 'Datum', 'date', None), ('company_uid', 'Firma', 'text', None),
                ('invoice_no', 'Invoice Nr.', 'text', None), ('status_cd', 'Abteilung', 'choice', db.STATUS_LABELS),
                ('profomal_amount', 'Proforma', 'money', None), ('release_amount', 'Rechnung', 'money', None),
                ('deposit_amount', 'Einzahlung', 'money', None), ('deposit_ymd', 'Datum (Einzahlung)', 'date', None),
                ('remark', 'Bemerkung', 'text', None)]

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


class StockWindow(RecordWindow):
    title = 'Stock'
    TYPES = {'I': '입고', 'O': '출고'}
    columns = [Column('ymd', '일자', 'date'), Column('type', '구분', 'choice', 60),
               Column('company_name', '회사명', width=110), Column('item_name', '상품명', width=200),
               Column('quantity', '수량', 'int', 60, total='sum'), Column('item_price', '단가', 'money', 70, 'avg'),
               Column('amount', '총액', 'money', 80, 'sum'), Column('remark', '비고', width=100)]

    def fetch(self, a, b, company):
        rows = db.stock_rows(self.folder.db, a, b, company)
        return sorted(rows, key=lambda r: (r['ymd'], (r['company_name'] or '').lower(), r['item_name'] or ''))

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


def program_folder():
    """Folder with Base/, db/ and ShippingInvoice/: the first argument, else next to the exe (or this source)."""
    if len(sys.argv) > 1 and os.path.isdir(sys.argv[1]):
        return sys.argv[1]
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.getcwd()


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    w = MainWindow(Folder(program_folder()))
    w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
