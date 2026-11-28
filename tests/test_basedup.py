import openpyxl
from openpyxl.styles import PatternFill

from invoiceauto import basedup
from invoiceauto.refdata import RefData
from tests.synthetic import make_base


def _base_with_dups(tmp_path):
    path = make_base(str(tmp_path))
    wb = openpyxl.load_workbook(path)
    ws = wb['newPrice']
    a = [c.value for c in ws[2]]
    b = [c.value for c in ws[3]]
    ws.append(a)                         # row 5: identical copy of row 2
    changed = list(b)
    changed[4] = 99.0                    # row 6: Beta with another unit price (newest)
    ws.append(changed)
    ws.append(a)                         # row 7: Alpha again
    for r in range(2, 8):
        ws.cell(r, 11).value = f'=E{r}+D{r}*2'
    ws.cell(4, 13).hyperlink = 'https://example.com/c'
    ws.cell(4, 1).fill = PatternFill('solid', fgColor='FFFF00')
    wb.save(path)
    return path


def test_groups_and_clean(tmp_path):
    path = _base_with_dups(tmp_path)
    groups = {g.name: g for g in basedup.find_groups(path)}
    assert set(groups) == {'Alpha Cream 50ml', 'Beta Candy 100g'}
    assert groups['Alpha Cream 50ml'].rows == [2, 5, 7]
    assert groups['Alpha Cream 50ml'].identical
    assert not groups['Beta Candy 100g'].identical

    before = RefData.load(path)
    drop = basedup.rows_to_drop(groups.values())
    assert drop == {2, 3, 5}
    out = str(tmp_path / 'clean.xlsx')
    assert basedup.write_clean(path, out, drop) == 3
    after = RefData.load(out)
    assert after.duplicate_rows == {}
    assert set(after.items) == set(before.items)
    ws = openpyxl.load_workbook(out)['newPrice']
    assert ws.max_row == 4
    assert [ws.cell(r, 9).value for r in (2, 3, 4)] == ['Gamma Tea', 'Beta Candy 100g', 'Alpha Cream 50ml']
    assert ws.cell(3, 11).value == '=E3+D3*2'          # formula follows its row
    assert ws.cell(3, 5).value == 99.0
    assert ws.cell(2, 1).fill.fgColor.rgb.endswith('FFFF00')
    assert ws.cell(2, 13).hyperlink.target == 'https://example.com/c'
    # original untouched
    assert openpyxl.load_workbook(path)['newPrice'].max_row == 7


def test_keep_choice(tmp_path):
    path = _base_with_dups(tmp_path)
    groups = basedup.find_groups(path)
    drop = basedup.rows_to_drop(groups, keep={'Beta Candy 100g': 3})
    out = str(tmp_path / 'clean.xlsx')
    basedup.write_clean(path, out, drop)
    assert RefData.load(out).item('Beta Candy 100g').unit_price == 4.0
