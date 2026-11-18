"""Program folder, settings file and the 작업시작 job, shared by the window and the command line."""
import configparser
import datetime as dt
import glob
import re
import os
import shutil
from dataclasses import dataclass
from typing import Callable, List, Optional

from . import db
from .engine import Settings, build_run, MODE_PROFORMA, MODE_BILL, CARRIER_POST, CARRIER_CJ
from .generate import (Paths, generate, run_folder, person_folder, offline_english_address, save_pdfs,
                       write_person_invoices)
from .refdata import RefData
from .shipping import read_shipping

INI_NAME = 'InvoiceAuto.ini'
ADDRESS_OFFLINE = 'offline'
ADDRESS_API = 'api'

# checkbox keys in the ini, same names as the old program's ini
FLAGS = ['chkAutoInvoiceNo', 'chkSavePdf', 'chkApplyAccounting', 'chkMakePerosonInvoice', 'chkMakeExportSum',
         'chkMakeShoppingMerge']


@dataclass
class Folder:
    """The program folder: same layout as the old program (Base, db, ShippingInvoice, Result).

    In test mode the run writes to db/euromall_test.db and Result_test/, so it can be run next to the old
    program on the same input without touching the real account book.
    """
    root: str
    test: bool = False

    @property
    def base(self):
        return os.path.join(self.root, 'Base')

    @property
    def real_db(self):
        return os.path.join(self.root, 'db', 'euromall.db')

    @property
    def test_db(self):
        return os.path.join(self.root, 'db', 'euromall_test.db')

    @property
    def db(self):
        return self.test_db if self.test else self.real_db

    @property
    def shipping(self):
        return os.path.join(self.root, 'ShippingInvoice')

    @property
    def result(self):
        return os.path.join(self.root, 'Result_test' if self.test else 'Result')

    def prepare_test_db(self, fresh=False) -> bool:
        """Copies the real database to the test one when it is missing (or always, with fresh). True if copied."""
        if self.test and os.path.exists(self.real_db) and (fresh or not os.path.exists(self.test_db)):
            shutil.copy2(self.real_db, self.test_db)
            return True
        return False

    @property
    def ini(self):
        return os.path.join(self.root, INI_NAME)

    def shipping_files(self) -> List[str]:
        files = [f for f in glob.glob(os.path.join(self.shipping, '*.xlsx')) if not os.path.basename(f).startswith('~$')]
        return sorted(files, key=os.path.getmtime, reverse=True)


@dataclass
class Options:
    mode: str = MODE_PROFORMA
    carrier: str = CARRIER_CJ
    auto_hawb: bool = True
    save_pdf: bool = True
    apply_accounting: bool = True
    make_person_invoice: bool = False
    make_export_per_company: bool = False
    make_cargo_weight_merge: bool = True
    address_mode: str = ADDRESS_OFFLINE
    address_api_key: str = ''
    test_mode: bool = False

    def settings(self, work_date: dt.date) -> Settings:
        return Settings(work_date=work_date, mode=self.mode, carrier=self.carrier, auto_hawb=self.auto_hawb,
                        save_pdf=self.save_pdf, apply_accounting=self.apply_accounting,
                        make_person_invoice=self.make_person_invoice,
                        make_export_per_company=self.make_export_per_company,
                        make_cargo_weight_merge=self.make_cargo_weight_merge)


def _read_ini(path) -> configparser.ConfigParser:
    """Tolerant ini reader: the old program's ini can start with a broken section header
    (seen in use: '[10.' + newline + '.P/ ... ,Proforma]'), so lines before the first proper
    [Section] are skipped and a repeated section does not fail."""
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    text_ = ''
    for enc in ('utf-8-sig', 'cp949', 'latin-1'):
        try:
            with open(path, encoding=enc) as f:
                text_ = f.read()
            break
        except (OSError, UnicodeDecodeError):
            continue
    lines = text_.splitlines()
    start = next((i for i, ln in enumerate(lines) if re.fullmatch(r'\s*\[[A-Za-z_]\w*\]\s*', ln)), len(lines))
    try:
        cp.read_string('\n'.join(lines[start:]))
    except configparser.Error:
        pass
    return cp


def old_ini_path(root):
    """The old program's ini next to its exe (00_Invice Auto ....ini), used until our own ini exists."""
    found = sorted(f for f in glob.glob(os.path.join(root, '*.ini')) if os.path.basename(f) != INI_NAME)
    return found[0] if found else None


def load_options(path, mode=None) -> Options:
    """Reads the [Proforma] or [Bill] section, like the old program's 설정불러오기.
    Without our own ini yet, the old program's ini in the same folder is read instead."""
    first_run = not os.path.exists(path)
    if first_run:
        path = old_ini_path(os.path.dirname(path)) or path
    cp = _read_ini(path)
    general = cp['General'] if cp.has_section('General') else {}
    mode = mode or general.get('mode', MODE_PROFORMA)
    sec = cp['Proforma' if mode == MODE_PROFORMA else 'Bill'] if cp.has_section(
        'Proforma' if mode == MODE_PROFORMA else 'Bill') else {}
    flag = lambda k, d: str(sec.get(k, '1' if d else '0')) == '1'
    o = Options(mode=mode)
    o.auto_hawb = flag('chkAutoInvoiceNo', True)
    o.save_pdf = flag('chkSavePdf', True)
    o.apply_accounting = flag('chkApplyAccounting', True)
    o.make_person_invoice = flag('chkMakePerosonInvoice', mode == MODE_BILL)
    o.make_export_per_company = flag('chkMakeExportSum', False)
    o.make_cargo_weight_merge = flag('chkMakeShoppingMerge', True)
    o.carrier = CARRIER_POST if str(sec.get('rdbPostOffice', '0')) == '1' else CARRIER_CJ
    o.address_mode = general.get('address_mode', ADDRESS_OFFLINE)
    o.address_api_key = general.get('address_api_key', '')
    # starts in test mode until settings are saved once, so a first try never touches euromall.db or Result/
    o.test_mode = str(general.get('test_mode', '1' if first_run else '0')) == '1'
    return o


def save_options(path, o: Options):
    """Always writes our own ini (InvoiceAuto.ini); the old program's ini is never changed."""
    cp = _read_ini(path) if os.path.exists(path) else configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    name = 'Proforma' if o.mode == MODE_PROFORMA else 'Bill'
    if not cp.has_section(name):
        cp.add_section(name)
    b = lambda v: '1' if v else '0'
    cp[name].update({
        'chkAutoInvoiceNo': b(o.auto_hawb), 'chkSavePdf': b(o.save_pdf), 'chkApplyAccounting': b(o.apply_accounting),
        'chkMakePerosonInvoice': b(o.make_person_invoice), 'chkMakeExportSum': b(o.make_export_per_company),
        'chkMakeShoppingMerge': b(o.make_cargo_weight_merge),
        'rdbProforma': b(o.mode == MODE_PROFORMA), 'rdbBill': b(o.mode == MODE_BILL),
        'rdbPostOffice': b(o.carrier == CARRIER_POST), 'rdbCjLogistics': b(o.carrier == CARRIER_CJ),
    })
    if not cp.has_section('General'):
        cp.add_section('General')
    cp['General'].update({'mode': o.mode, 'address_mode': o.address_mode, 'address_api_key': o.address_api_key,
                          'test_mode': '1' if o.test_mode else '0'})
    with open(path, 'w', encoding='utf-8') as f:
        cp.write(f)


def english_address_function(o: Options, log: Callable[[str], None]):
    if o.address_mode == ADDRESS_API and o.address_api_key:
        from .address_api import JusoClient
        client = JusoClient(o.address_api_key)

        def lookup(shipment):
            try:
                found = client.english_address(shipment)
                if found:
                    return found
            except Exception as e:
                log(f'  영문주소 API 실패, 프로그램 내 변환 사용: {e}')
            return offline_english_address(shipment)
        return lookup
    return offline_english_address


def preview_run(folder: Folder, shipping_file: str, work_date: dt.date, o: Options):
    """Builds the run without writing anything, for the person-invoice order list."""
    ref = RefData.load(os.path.join(folder.base, 'Base.xlsx'))
    return build_run(read_shipping(shipping_file), ref, o.settings(work_date))


def person_candidates(run):
    """Orders that can get a person invoice: all but those of delivery-only (L) companies."""
    return [sh for sh in run.shipments if not sh.company.is_delivery_only]


def run_job(folder: Folder, shipping_file: str, work_date: dt.date, o: Options,
            log: Callable[[str], None] = print, person_only: bool = False,
            person_orders: Optional[set] = None):
    """person_orders: order numbers picked for person invoices; None means every candidate."""
    """작업시작: reads the shipping file, writes the result folder, then the account book and stock."""
    if folder.test:
        log('[테스트 모드] 결과는 Result_test, 회계·재고는 db/euromall_test.db 에 기록합니다.')
        if folder.prepare_test_db():
            log('  euromall.db 를 euromall_test.db 로 복사했습니다.')
    log(f'입력 파일: {os.path.basename(shipping_file)}')
    ref = RefData.load(os.path.join(folder.base, 'Base.xlsx'))
    log(f'Base.xlsx: 거래처 {len(ref.companies)}곳, 상품 {len(ref.items)}개')
    lines = read_shipping(shipping_file)
    settings = o.settings(work_date)
    if person_only:
        settings.make_person_invoice = True
    run = build_run(lines, ref, settings)
    log(f'주문 {len(run.shipments)}건, 상품 줄 {len(lines)}개, 거래처 {len(run.invoices)}곳')
    for w in run.warnings:
        log('  주의: ' + w)

    paths = Paths(base_dir=folder.base, result_dir=folder.result)
    person = [sh for sh in person_candidates(run) if person_orders is None or sh.order_no in person_orders]
    if person_only:
        out_dir = person_folder(run, paths)
        outputs = write_person_invoices(run, paths, person, english_address_function(o, log))
        if o.save_pdf:
            try:
                save_pdfs(outputs, 'Profoma_')
            except Exception as e:
                log(f'  주의: PDF 저장 실패: {e}')
        log(f'개인 인보이스 {len(person)}건 저장: {out_dir}')
        return run, out_dir

    outputs = generate(run, paths, english_address=english_address_function(o, log), person_shipments=person)
    if settings.make_person_invoice:
        log(f'개인 인보이스 {len(person)}건 저장: {person_folder(run, paths)}')
    out_dir = run_folder(run, paths)
    log(f'결과 파일 {len(outputs)}개 저장: {out_dir}')
    for w in run.warnings:
        if w.startswith('PDF'):
            log('  주의: ' + w)

    if o.apply_accounting:
        b = db.backup(folder.db)
        db.record_run(folder.db, run)
        log(f'회계·재고 반영 완료 (DB 백업: {os.path.basename(b)})')
        for inv in run.invoices:
            log(f'  {inv.invoice_no}: {inv.total:.2f} EUR')
        log(f'  {run.export_invoice_no}: {sum(i.total for i in run.invoices):.2f} EUR')

    if not folder.test:
        backup_dir = os.path.join(os.path.dirname(shipping_file), 'backup')
        os.makedirs(backup_dir, exist_ok=True)
        shutil.copy2(shipping_file, backup_dir)
    return run, out_dir
