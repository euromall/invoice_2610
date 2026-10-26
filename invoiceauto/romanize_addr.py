"""Offline Korean road-name address -> English (Revised Romanization)."""
import re

CHO = ['g','kk','n','d','tt','r','m','b','pp','s','ss','','j','jj','ch','k','t','p','h']
JUNG = ['a','ae','ya','yae','eo','e','yeo','ye','o','wa','wae','oe','yo','u','wo','we','wi','yu','eu','ui','i']
# final consonant index -> representative sound class
JONG = ['', 'k','k','k','n','n','n','t','l','k','m','l','l','l','p','l','m','p','p','t','t','ng','t','t','k','t','p','t']
# jong index -> carried-over onset when next syllable starts with ㅇ (liaison)
LIAISON = {1:'g',2:'kk',3:'gs',4:'n',5:'nj',6:'n',7:'d',8:'r',9:'lg',10:'lm',11:'lb',12:'ls',13:'lt',14:'lp',15:'l',
           16:'m',17:'b',18:'bs',19:'s',20:'ss',21:'ng',22:'j',23:'ch',24:'k',25:'t',26:'p',27:''}

def _split(ch):
    c = ord(ch) - 0xAC00
    return c // 588, (c % 588) // 28, c % 28

def romanize(word):
    """Romanize a Hangul word with the main RR sound-change rules (no hyphen inside)."""
    syl = [_split(c) if '가' <= c <= '힣' else c for c in word]
    out = []
    for i, s in enumerate(syl):
        if isinstance(s, str):
            out.append(s); continue
        cho, jung, jong = s
        prev = syl[i-1] if i > 0 and not isinstance(syl[i-1], str) else None
        onset = CHO[cho]
        if prev:
            pj = prev[2]
            pclass = JONG[pj]
            if cho == 11 and pj:            # ㅇ onset: liaison handled on prev
                onset = ''
            elif cho == 2 and pclass == 'l':  # ㄹ+ㄴ -> ll
                onset = 'l'
            elif cho == 5:                  # ㄹ onset
                onset = 'r' if not pj else ('l' if pclass in ('l', 'n') else 'n')
        out.append(onset + JUNG[jung])
        # coda
        if jong:
            nxt = syl[i+1] if i + 1 < len(syl) and not isinstance(syl[i+1], str) else None
            cls = JONG[jong]
            if nxt is None:
                out.append(cls)
            else:
                ncho = nxt[0]
                if ncho == 11:
                    out.append(LIAISON[jong])
                elif ncho in (2, 6):        # nasalisation before ㄴ/ㅁ
                    out.append({'k': 'ng', 't': 'n', 'p': 'm'}.get(cls, cls) if cls != 'l' else 'l')
                elif ncho == 5:             # before ㄹ
                    if cls == 'n': out[-1] = out[-1]; out.append('l'); out[-1] = 'l'
                    else: out.append({'k': 'ng', 't': 'n', 'p': 'm'}.get(cls, cls))
                else:
                    out.append(cls)
    s = ''.join(out)
    return s

SIDO = {
 '서울특별시':'Seoul','서울시':'Seoul','서울':'Seoul','부산광역시':'Busan','부산':'Busan','대구광역시':'Daegu','대구':'Daegu',
 '인천광역시':'Incheon','인천':'Incheon','광주광역시':'Gwangju','광주':'Gwangju','대전광역시':'Daejeon','대전':'Daejeon',
 '울산광역시':'Ulsan','울산':'Ulsan','세종특별자치시':'Sejong-si','세종':'Sejong-si','경기도':'Gyeonggi-do','경기':'Gyeonggi-do',
 '강원특별자치도':'Gangwon-do','강원도':'Gangwon-do','강원':'Gangwon-do','충청북도':'Chungcheongbuk-do','충북':'Chungcheongbuk-do',
 '충청남도':'Chungcheongnam-do','충남':'Chungcheongnam-do','전북특별자치도':'Jeonbuk-do','전라북도':'Jeonbuk-do','전북':'Jeonbuk-do',
 '전라남도':'Jeollanam-do','전남':'Jeollanam-do','경상북도':'Gyeongsangbuk-do','경북':'Gyeongsangbuk-do','경상남도':'Gyeongsangnam-do',
 '경남':'Gyeongsangnam-do','제주특별자치도':'Jeju-do','제주도':'Jeju-do','제주':'Jeju-do',
}
SIDO_NO_SUFFIX = {'Seoul','Busan','Daegu','Incheon','Gwangju','Daejeon','Ulsan'}

def _unit(tok):
    """Romanize an admin unit like 강동구 -> Gangdong-gu."""
    for suf, eng in (('특별자치시','-si'),('시','-si'),('군','-gun'),('구','-gu'),('읍','-eup'),('면','-myeon'),('동','-dong'),('리','-ri'),('가','-ga')):
        if tok.endswith(suf) and len(tok) > len(suf):
            return romanize(tok[:-len(suf)]).capitalize() + eng
    return romanize(tok).capitalize()

def _road(tok, num):
    m = re.fullmatch(r'(.+?)(대로|로|길)', tok)
    m2 = re.fullmatch(r'(.+?(?:대로|로))(\d+)(번길|길)', tok)
    if m2:
        base = _road(m2.group(1), None)
        return f"{base} {m2.group(2)}{'beon-gil' if m2.group(3) == '번길' else '-gil'}"
    m3 = re.fullmatch(r'(.+?)(\d+)(길)', tok)
    if m3:
        return f"{romanize(m3.group(1)).capitalize()} {m3.group(2)}-gil"
    if m:
        return romanize(m.group(1)).capitalize() + {'대로':'-daero','로':'-ro','길':'-gil'}[m.group(2)]
    return romanize(tok).capitalize()

def _jibun(toks, sido):
    """Lot-number address (no road name): '... 청학리 123-4' -> '123-4, Cheonghak-ri, ...'."""
    units, num = [], None
    for t in toks:
        if re.search(r'(시|군|구|읍|면|동|리|가)$', t) and not re.search(r'\d', t):
            units.append(t)
        elif re.fullmatch(r'(산)?\d+(-\d+)?(번지)?', t) and units:
            num = re.sub(r'번지$', '', t)
            break
        elif units:
            break
    if not units:
        return None
    parts = [num] + [_unit(u) for u in reversed(units)] + [sido, 'Republic of Korea']
    return ', '.join(p for p in parts if p)


def to_english(addr):
    """'서울특별시 강동구 천호대로 1239 (길동, ...) 110동' -> '1239, Cheonho-daero, Gangdong-gu, Seoul, Republic of Korea'"""
    addr = re.sub(r'\(.*?\)', ' ', addr or '').replace(',', ' ')
    addr = re.sub(r'(대로|로|길)(\d+(?:-\d+)?)(?=\s|$)', r'\1 \2', addr)   # '대평로34' -> '대평로 34'
    toks = addr.split()
    sido = None; units = []; road = None; num = None
    i = 0
    if toks and toks[0] in SIDO:
        sido = SIDO[toks[0]]; i = 1
    while i < len(toks):
        t = toks[i]
        if re.fullmatch(r'\d+(번길|길)', t) and units and not re.search(r'(시|군|구|읍|면)$', units[-1]):
            road = units.pop() + t                     # '돈마루 1길' -> '돈마루1길'
            if i + 1 < len(toks) and re.fullmatch(r'\d+(-\d+)?', toks[i+1]):
                num = toks[i+1]
            break
        if re.search(r'(대로|로|길)$', t) or re.search(r'(로|길)\d+(번길|길)$', t) or re.search(r'\d+길$', t):
            if i + 1 < len(toks) and re.fullmatch(r'\d+(-\d+)?', toks[i+1]):
                road, num = t, toks[i+1]; break
            # "돈마루 1길" style: name + "N길"
        if re.fullmatch(r'\d+(번길|길)', t) and units:
            prev = units.pop()
            road = prev + t
            if i + 1 < len(toks) and re.fullmatch(r'\d+(-\d+)?', toks[i+1]):
                num = toks[i+1]
            break
        if re.search(r'(시|군|구|읍|면)$', t):
            units.append(t)
        elif not re.search(r'\d', t) and not road:
            units.append(t)  # candidate road-name prefix
        i += 1
    units = [u for u in units if re.search(r'(시|군|구|읍|면)$', u)] if road else units
    if not road:
        return _jibun(toks[1 if sido else 0:], sido)
    parts = [num, _road(road, num)] + [_unit(u) for u in reversed(units)]
    if sido:
        parts.append(sido)
    parts.append('Republic of Korea')
    return ', '.join(p for p in parts if p)
