"""Generates a fixture run: python -m tests.run_fixture <run_dir> <yyyy-mm-dd> <proforma|bill> [post|cj]"""
import datetime as dt, sys, os, shutil
from invoiceauto.refdata import RefData
from invoiceauto.shipping import read_shipping
from invoiceauto.engine import build_run, Settings
from invoiceauto.generate import generate, Paths, run_folder

def main(run_dir, date, mode, carrier='cj'):
    base = os.path.join(os.path.dirname(run_dir.rstrip('/')), 'base')
    ref = RefData.load(os.path.join(base, 'Base.xlsx'))
    lines = read_shipping(os.path.join(run_dir, 'input.xlsx'))
    st = Settings(work_date=dt.date.fromisoformat(date), mode=mode, carrier=carrier,
                  make_person_invoice=(mode == 'bill'))
    run = build_run(lines, ref, st)
    for w in run.warnings: print('WARN', w)
    out = os.path.join(run_dir, 'generated')
    shutil.rmtree(out, ignore_errors=True)
    paths = Paths(base_dir=base, result_dir=out)
    person = None
    pf = os.path.join(run_dir, 'person_hawbs.txt')
    if os.path.exists(pf):
        want = open(pf).read().split()
        person = [s for s in run.shipments if s.hawb in want]
    generate(run, paths, person_shipments=person)
    return run_folder(run, paths)

if __name__ == '__main__':
    print(main(*sys.argv[1:]))
