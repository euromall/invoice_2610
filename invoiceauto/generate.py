"""Writes every output workbook of a run into the result folder."""
import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .engine import Run, MODE_PROFORMA
from . import pdf
from .outputs import invoices, lists
from .romanize_addr import to_english
from .shipping import C_ADDR
from .util import text


@dataclass
class Output:
    path: str
    pdf: bool = False      # the old program also saved these as PDF


@dataclass
class Paths:
    base_dir: str          # folder with Base.xlsx and the templates
    result_dir: str        # parent of the per-run folders

    def template(self, name):
        return os.path.join(self.base_dir, name + '.xlsx')


def offline_english_address(shipment) -> str:
    return to_english(text(shipment.first.get(C_ADDR))) or ''


def run_folder(run: Run, paths: Paths) -> str:
    d = run.settings.work_date
    prefix = 'Profoma' if run.settings.mode == MODE_PROFORMA else 'Bill'
    return os.path.join(paths.result_dir, f'{prefix}_{d:%y%m%d}')


def generate(run: Run, paths: Paths, english_address: Optional[Callable[[str], str]] = None,
             person_shipments=None) -> List[Output]:
    folder = run_folder(run, paths)
    os.makedirs(folder, exist_ok=True)
    s = run.settings
    pre = 'Profoma_' if s.mode == MODE_PROFORMA else ''
    date = f'{s.work_date:%Y%m%d}'
    outputs = []

    def save(wb, name, pdf=False):
        path = os.path.join(folder, pre + name + '.xlsx')
        wb.save(path)
        outputs.append(Output(path, pdf))

    for inv in run.invoices:
        if inv.company.is_delivery_only:
            tpl = 'LiferInvoice'
        elif s.mode == MODE_PROFORMA:
            tpl = 'ProfomaInvoice'
        else:
            tpl = 'OutsideInvoice'
        save(invoices.company_invoice(run, inv, paths.template(tpl)),
             invoices.company_invoice_filename(run, inv), pdf=True)

    if run.export_invoices:
        save(invoices.export_invoice(run, paths.template('ExportInvoiceSum')),
             invoices.export_invoice_filename(run), pdf=True)

    if s.make_person_invoice:
        targets = person_shipments if person_shipments is not None else [
            sh for sh in run.shipments if not sh.company.is_delivery_only]
        for sh in targets:
            addr = (english_address or offline_english_address)(sh) or ''
            save(invoices.person_invoice(run, sh, paths.template('PersonInvoice'), addr),
                 invoices.person_invoice_filename(sh))

    save(lists.invoice_upload(run, paths.template('InvoiceUpload')), f'송장업로드_{date}')
    save(lists.food_declaration(run, paths.template('ImportFoodDeclaration')), f'수입식품신고_유로몰_{date}', pdf=True)
    save(lists.cargo_list(run, paths.template('CargoList')), f'적하목록 취합_{date}')
    if s.make_cargo_weight_merge:
        save(lists.cargo_weight_merge(run), f'적하목록 취합_무게병합_{date}')
    save(lists.packing(run, paths.template('Packing')), f'포장리스트_{date}')
    save(lists.packing_list(run, paths.template('PackingList')), f'PackingList_{date}')

    if s.save_pdf:
        pairs = []
        for o in outputs:
            if o.pdf:
                name = os.path.splitext(os.path.basename(o.path))[0]
                if pre and name.startswith(pre):
                    name = name[len(pre):]           # the PDF names never had the Profoma_ prefix
                pairs.append((o.path, os.path.join(folder, name + '.pdf')))
        try:
            pdf.export(pairs)
            outputs.extend(Output(p) for _, p in pairs if os.path.exists(p))
        except Exception as e:                      # PDFs are a convenience; the Excel files are done
            run.warnings.append(f'PDF 저장 실패: {e}')
    return outputs
