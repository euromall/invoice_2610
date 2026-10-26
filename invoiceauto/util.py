"""Small conversions shared by every module.

Numbers are always parsed and formatted here, never with the Windows locale,
so a German system (decimal comma, DD.MM.YYYY) gives the same result as a Korean one.
"""
import datetime as dt
import unicodedata


def text(v) -> str:
    """Cell value -> str. Whole floats lose their '.0' (20160002991.0 -> '20160002991')."""
    if v is None:
        return ''
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return repr(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime('%Y-%m-%d')
    return str(v)


def num(v, default=0.0) -> float:
    """Cell value -> float. Accepts '1,5' and '1.5'; blanks and formulas give default."""
    if v is None or isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(' ', '').replace(' ', '')
    if not s or s.startswith('='):
        return default
    if ',' in s and '.' not in s:
        s = s.replace(',', '.')
    else:
        s = s.replace(',', '')
    try:
        return float(s)
    except ValueError:
        return default


def clean_number(x: float):
    """3.0 -> 3, 2.5 -> 2.5, with float noise removed (18.400000000000002 -> 18.4)."""
    x = round(float(x), 10)
    return int(x) if x.is_integer() else x


def ascii_latin(s):
    """'CAFFÈ Präbiotika' -> 'CAFFE Prabiotika'. Accented Latin letters lose the accent; Hangul is kept."""
    if not isinstance(s, str):
        return s
    out = []
    for ch in s:
        if ord(ch) < 128:
            out.append(ch)
        elif ch == 'ß':
            out.append('ss')
        else:
            base = unicodedata.normalize('NFKD', ch)
            out.append(base[0] if base[0].isascii() and base[0].isalpha() else ch)
    return ''.join(out)


def yymmdd(d: dt.date) -> str:
    return d.strftime('%y%m%d')
