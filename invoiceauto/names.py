"""Korean personal names -> Revised Romanization ('정아름' -> 'Jeong Areum').

Names are romanized syllable by syllable (no sound changes between syllables),
which is the rule for personal names and matches the existing tb_convert_name entries.
"""
import re

CHO = ['g', 'kk', 'n', 'd', 'tt', 'r', 'm', 'b', 'pp', 's', 'ss', '', 'j', 'jj', 'ch', 'k', 't', 'p', 'h']
JUNG = ['a', 'ae', 'ya', 'yae', 'eo', 'e', 'yeo', 'ye', 'o', 'wa', 'wae', 'oe', 'yo', 'u', 'wo', 'we', 'wi',
        'yu', 'eu', 'ui', 'i']
JONG = ['', 'k', 'k', 'k', 'n', 'n', 'n', 't', 'l', 'k', 'm', 'l', 'l', 'l', 'p', 'l', 'm', 'p', 'p', 't', 't',
        'ng', 't', 't', 'k', 't', 'p', 't']

HANGUL = re.compile(r'^[가-힣]+$')


def _syllable(ch, first):
    c = ord(ch) - 0xAC00
    cho, jung, jong = c // 588, (c % 588) // 28, c % 28
    onset = CHO[cho]
    if cho == 5 and first:      # word-initial ㄹ
        onset = 'r'
    return onset + JUNG[jung] + JONG[jong]


def romanize_name(name: str) -> str:
    """'정아름' -> 'Jeong Areum'. Non-Hangul names are returned unchanged."""
    name = (name or '').strip()
    if not name or not HANGUL.match(name):
        return name
    # two-syllable surnames
    surname_len = 2 if len(name) >= 3 and name[:2] in TWO_SYLLABLE_SURNAMES else 1
    sur, given = name[:surname_len], name[surname_len:]
    sur_r = ''.join(_syllable(c, i == 0) for i, c in enumerate(sur)).capitalize()
    given_r = ''.join(_syllable(c, False) for c in given).capitalize()
    return f'{sur_r} {given_r}'.strip()


TWO_SYLLABLE_SURNAMES = {'남궁', '황보', '제갈', '사공', '선우', '서문', '독고', '동방', '어금', '망절'}
