"""English address from the 행정안전부 도로명주소 영문주소 API (business.juso.go.kr, addrEngApi.do)."""
import json
import re
import urllib.parse
import urllib.request

from .shipping import C_ADDR
from .util import text

API_URL = 'https://business.juso.go.kr/addrlink/addrEngApi.do'


class AddressApiError(Exception):
    pass


def search_keyword(addr: str) -> str:
    """Korean address without the parts the API cannot search: (…) notes and the detail after the building number."""
    addr = re.sub(r'\(.*?\)', ' ', addr or '').replace(',', ' ')
    addr = re.sub(r'(대로|로|길)(\d+(?:-\d+)?)(?=\s|$)', r'\1 \2', addr)
    toks = addr.split()
    for i, t in enumerate(toks):
        if re.fullmatch(r'(산)?\d+(-\d+)?(번지)?', t) and i > 0:
            return ' '.join(toks[:i + 1])
    return ' '.join(toks)


class JusoClient:
    def __init__(self, key: str, timeout: float = 5.0):
        self.key = key
        self.timeout = timeout
        self._cache = {}

    def search(self, keyword: str):
        q = urllib.parse.urlencode({'confmKey': self.key, 'currentPage': 1, 'countPerPage': 1,
                                    'keyword': keyword, 'resultType': 'json'})
        with urllib.request.urlopen(f'{API_URL}?{q}', timeout=self.timeout) as r:
            data = json.loads(r.read().decode('utf-8'))
        res = data.get('results', {})
        common = res.get('common', {})
        if common.get('errorCode', '0') != '0':
            raise AddressApiError(f"{common.get('errorCode')} {common.get('errorMessage', '')}".strip())
        return res.get('juso') or []

    def english_address(self, shipment) -> str:
        """'' when nothing matched; raises on a network or key error so the caller can fall back."""
        keyword = search_keyword(text(shipment.first.get(C_ADDR)))
        if not keyword:
            return ''
        if keyword not in self._cache:
            found = self.search(keyword)
            road = found[0].get('roadAddr', '') if found else ''
            self._cache[keyword] = f'{road}, Republic of Korea' if road else ''
        return self._cache[keyword]
