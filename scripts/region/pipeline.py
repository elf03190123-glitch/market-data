"""원자료 → 화면 데이터 파이프라인 — 아티팩트 도구 update.py를 HTML 대신 JSON 파일에 쓰도록 옮긴 것.

update.py가 하던 일 중 그대로 유지: 원자료 3종(A/C/D)에 새 달 행 추가(같은 반올림 규칙) → DATA를 원자료에서 전체 재계산
(부분 패치 없음) → 새 달 여객 합계 = 포털 그랜드토탈 확인 → narr.py로 요약 노트·07 정리 문장(한/영) 재작성 → 엑셀용 메타(XMETA).
HTML 문자열 치환(1~N월, 기간, 개월 수, 추출일, 하단 검증문)은 이제 페이지가 data에서 직접 계산하므로 필요 없다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from raw import VARS, TABLES, csv_line, parse_raw  # noqa: E402
from build import build  # noqa: E402
from narr import gen  # noqa: E402
from cityarea import city_area  # noqa: E402

META_BASE = {
    'id': 'region-country',
    'title': '출발편 항공 통계(국가, 도시별)',
    'source': '국토교통부 항공정보포털(airportal.go.kr)',
    'source_url': 'https://www.airportal.go.kr/stats/transport/chartDetail4.do',
    'schema_version': 1,
}


def ym_dash(ym):
    return f'{ym[:4]}-{ym[4:]}'


def next_ym(ym):
    y, m = int(ym[:4]), int(ym[4:])
    return f'{y + 1}01' if m == 12 else f'{y}{m + 1:02d}'


def last_month(raw_obj):
    return max(line.split(',', 1)[0] for line in raw_obj['variants']['dep_mix']['A'][1:] if line)


def month_rows(nm_variant, ym):
    """update.py와 같은 규칙으로 새 달 CSV 행 생성(화물만 round)"""
    add = nm_variant
    return {
        'A': [[ym, a, n, f, p, round(c)] for a, n, f, p, c in add['A']],
        'C': [[ym, c, f, p, round(cg)] for c, f, p, cg in add['C']],
        'D': [[ym, r, f, p, round(c)] for r, f, p, c in add['D']],
    }


def unmapped_cities(nm):
    """cityarea.py에 지역이 없는 도시 → (값이 있는 도시, 운항·여객·화물이 모두 0인 도시)
    값이 0뿐인 행은 지역별 견인 도시 계산에 영향이 없으므로 경고만 한다(예: 2026-10 도착의 김해(PUS) 0행)."""
    vals = {}
    for v in VARS:
        for c, f, p, cg in nm['variants'][v]['C']:
            if city_area(c) is None:
                vals[c] = vals.get(c, 0) + abs(f) + abs(p) + abs(cg)
    return sorted(c for c, x in vals.items() if x), sorted(c for c, x in vals.items() if not x)


def append_month(raw_obj, nm):
    """raw_obj(파일 구조)에 새 달 행을 붙인 새 객체를 돌려준다"""
    ym = nm['ym']
    out = {'schema_version': 1, 'as_of': ym_dash(ym), 'variants': {}}
    for v in VARS:
        rows = month_rows(nm['variants'][v], ym)
        out['variants'][v] = {k: list(raw_obj['variants'][v][k]) + [csv_line(r) for r in rows[k]] for k in TABLES}
    return out


def build_all(raw_obj):
    R = parse_raw(raw_obj)
    return {v: build(R[v]) for v in VARS}


def check_totals(DATA, nm):
    for v in VARS:
        t = DATA[v]['total']['pass'][-1]
        api = nm['variants'][v]['tot'][1]
        if t != api:
            raise RuntimeError(f'{v} 새 달 여객 합계 {t:,} ≠ 포털 {api:,}')


def xmeta_from(nm, extracted):
    xm = {'extracted': extracted}
    for v in VARS:
        f, p, c = nm['variants'][v]['tot']
        xm[v] = {'f': f, 'p': p, 'c': c}
    return xm


def make_envelope(DATA, xmeta, updated_at):
    KO, EN = gen(DATA)
    last = DATA['dep_mix']['months'][-1]
    meta = dict(META_BASE)
    meta.update({'as_of': ym_dash(last), 'updated_at': updated_at})
    keys = list(META_BASE)[:4] + ['as_of', 'updated_at', 'schema_version']
    meta = {k: meta[k] for k in keys}
    return {'meta': meta, 'data': {'DATA': DATA, 'TXT': KO, 'TXT_EN': EN, 'xmeta': xmeta}}
