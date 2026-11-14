#!/usr/bin/env python3
"""출발편 항공 통계(국가, 도시별) — airportal 지역/국가·도시·국내선 월간 자동 업데이트 (브라우저·사람 개입 없음)

사용:
  python airportal_region.py --check
      meta.as_of 다음 달이 포털에 있는지 확인. JSON 한 줄 출력.
      종료 코드 0=있음, 1=아직 없음("데이터가 아직 없습니다"), 2=오류
  python airportal_region.py --update [--month YYYY-MM] [--force] [--date YYYY-MM-DD]
      새 달 수집(4개 조합 × 지역국가·도시·국내선) → 포털 그랜드토탈 대조 → 원자료에 행 추가 → DATA 전체 재계산
      → 요약·정리 문장 재생성 → data/region-country-raw.json, data/region-country.json 저장 → 요약 표 출력.
      기본 대상은 as_of 다음 달. 연속되지 않는 달은 --force 없이는 거부.
      종료 코드 0=완료, 1=아직 없음, 2=오류(검증 실패 포함, 파일은 그대로), 3=사람 조치 필요(지역 미지정 도시)
  python airportal_region.py --verify-month YYYY-MM
      이미 들어 있는 달을 다시 받아 원자료와 비교. 차이 0건이면 종료 코드 0, 차이 있으면 1, 오류 2.
  python airportal_region.py --rebuild
      수집 없이 원자료에서 region-country.json을 다시 만든다(cityarea.py 수정 후 등). xmeta(포털 합계)는 유지.

경로는 이 파일 기준: ../data/region-country-raw.json, ../data/region-country.json
'지역 미지정 도시'(운항·여객·화물 중 값이 있는 도시, 종료 코드 3)가 나오면 scripts/region/cityarea.py의 해당 지역 목록에 공항코드를 추가하고 --update를 다시 실행한다.
(airportal 지역 구분: 중앙아시아·튀르키예·코카서스=유럽, 하와이·괌·사이판=대양주, 중동·아프리카=기타, 몽골·대만·홍콩·마카오=아시아)
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'region'))

from raw import VARS, TABLES, csv_line, read_raw_file, write_raw_file  # noqa: E402
import collect as C  # noqa: E402
import pipeline as P  # noqa: E402

DATA_DIR = os.path.normpath(os.path.join(HERE, '..', 'data'))
RAW_PATH = os.path.join(DATA_DIR, 'region-country-raw.json')
ENV_PATH = os.path.join(DATA_DIR, 'region-country.json')
VLAB = {'dep_mix': '출발 · 유임+환승', 'dep_paid': '출발 · 유임여객', 'arr_mix': '도착 · 유임+환승', 'arr_paid': '도착 · 유임여객'}

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def read_env():
    with open(ENV_PATH, encoding='utf-8') as f:
        return json.load(f)


def write_env(env):
    tmp = ENV_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(env, f, ensure_ascii=False, separators=(',', ':'))
        f.write('\n')
    os.replace(tmp, ENV_PATH)


def ym_arg(s):
    s = s.strip().replace('.', '-')
    if len(s) == 6 and s.isdigit():
        s = s[:4] + '-' + s[4:]
    d = datetime.datetime.strptime(s, '%Y-%m')
    return f'{d.year}{d.month:02d}'


def out_json(obj):
    print(json.dumps(obj, ensure_ascii=False))


# ---------------------------------------------------------------- --check
def cmd_check():
    try:
        as_of = read_env()['meta']['as_of']
        nxt = P.next_ym(as_of.replace('-', ''))
        p = C.Portal()
        res = {'as_of': as_of, 'next': P.ym_dash(nxt)}
        tots = {}
        for at in ('D', 'A'):
            tots[at] = C.grand_total(p.region(nxt, '1', at))
        icn = [r for r in p.route(nxt, 'ICN', '1', 'I', 'D') if r.get('code_name') != '전체 합계']
        ok = all(t and t[1] > 0 for t in tots.values()) and len(icn) > 0
        res['portal_total_paid'] = {('dep' if at == 'D' else 'arr'): (dict(zip(['flights', 'pass', 'cargo'], t)) if t else None)
                                    for at, t in tots.items()}
        res['icn_intl_routes'] = len(icn)
        if ok:
            res['status'] = 'available'
            out_json(res)
            return 0
        res['status'] = 'not_yet'
        res['message'] = f'{P.ym_dash(nxt)} 데이터가 아직 없습니다'
        out_json(res)
        return 1
    except Exception as e:  # noqa: BLE001
        out_json({'status': 'error', 'message': f'{type(e).__name__}: {e}'})
        return 2


# ---------------------------------------------------------------- --update
def names_before(raw_obj, v, table, col, ym):
    out = set()
    for line in raw_obj['variants'][v][table][1:]:
        r = line.split(',')
        if r[0] < ym:
            out.add(tuple(r[c] for c in col) if isinstance(col, tuple) else r[col])
    return out


def summary(raw_obj, nm, DATA):
    ym = nm['ym']
    t = nm['variants']
    lines = [f'■ {ym[:4]}년 {int(ym[4:])}월 국제선 합계 (포털 그랜드토탈 = 원자료 합계 확인)',
             f'{"구분":<16}{"운항(편)":>10}{"여객(명)":>14}{"화물(톤)":>12}']
    for v in VARS:
        f, p, c = t[v]['tot']
        lines.append(f'{VLAB[v]:<16}{f:>10,}{p:>14,}{round(c):>12,}')
    dom = DATA['dep_mix']['domestic']
    lines.append(f'국내선 여객: {dom["pass"][-1]:,}명 (운항 {dom["flight"][-1]:,}편)')
    # 새로 등장한 국가·도시(전체 기간 처음)
    new_nat, new_city = set(), set()
    seen_n = set().union(*(names_before(raw_obj, v, 'A', (1, 2), ym) for v in VARS))
    seen_c = set().union(*(names_before(raw_obj, v, 'C', 1, ym) for v in VARS))
    for v in VARS:
        for a, n, *_ in t[v]['A']:
            if (a, n) not in seen_n:
                new_nat.add(f'{n}({a})')
        for c, *_ in t[v]['C']:
            if c not in seen_c:
                new_city.add(c)
    lines.append('새로 등장한 국가: ' + (', '.join(sorted(new_nat)) if new_nat else '없음'))
    lines.append('새로 등장한 도시: ' + (', '.join(sorted(new_city)) if new_city else '없음'))
    return '\n'.join(lines), sorted(new_nat), sorted(new_city)


def cmd_update(month, force, date):
    try:
        raw_obj = read_raw_file(RAW_PATH)
        last = P.last_month(raw_obj)
        env_asof = read_env()['meta']['as_of'].replace('-', '')
        if env_asof != last:
            raise RuntimeError(f'원자료 마지막 달({last})과 region-country.json as_of({env_asof})가 다릅니다 — --rebuild로 맞춘 뒤 다시 실행')
        exp = P.next_ym(last)
        ym = ym_arg(month) if month else exp
        if ym <= last:
            raise RuntimeError(f'{P.ym_dash(ym)}은 이미 들어 있습니다(현재 마지막 {P.ym_dash(last)}).')
        if ym != exp and not force:
            raise RuntimeError(f'다음 달은 {P.ym_dash(exp)}여야 합니다(입력 {P.ym_dash(ym)}). 건너뛰려면 --force')
        log(f'[1/4] {P.ym_dash(ym)} 수집 시작 (현재 마지막 {P.ym_dash(last)})')
        d = C.collect(ym, log=log)
    except C.NotYetAvailable as e:
        out_json({'status': 'not_yet', 'message': str(e)})
        return 1
    except Exception as e:  # noqa: BLE001
        out_json({'status': 'error', 'message': f'{type(e).__name__}: {e}'})
        return 2
    try:
        log('[2/4] 포털 그랜드토탈 대조·변환')
        nm = C.to_variants(d)
        um, um_zero = P.unmapped_cities(nm)
        if um_zero:
            log('WARN 지역 미지정 도시(값이 모두 0이라 계속 진행):', ', '.join(um_zero))
        if um:
            codes = sorted({c[c.rindex('(') + 1:-1] for c in um})
            msg = ('지역 미지정 도시가 있어 중단합니다(파일 변경 없음). scripts/region/cityarea.py의 알맞은 지역 목록에 '
                   f'공항코드를 추가한 뒤 --update를 다시 실행하세요: {", ".join(um)}')
            out_json({'status': 'needs_action', 'message': msg, 'unmapped_cities': um, 'codes': codes})
            return 3
        log('[3/4] 원자료 추가 · DATA 전체 재계산 · 서술 재생성')
        new_raw = P.append_month(raw_obj, nm)
        DATA = P.build_all(new_raw)
        P.check_totals(DATA, nm)
        updated = date or datetime.date.today().isoformat()
        env = P.make_envelope(DATA, P.xmeta_from(nm, updated), updated)
        text, new_nat, new_city = summary(raw_obj, nm, DATA)
        log('[4/4] 저장')
        write_raw_file(RAW_PATH, new_raw)
        write_env(env)
        print(text)
        out_json({'status': 'updated', 'as_of': P.ym_dash(ym), 'updated_at': updated,
                  'totals': {v: dict(zip(['flights', 'pass', 'cargo'], nm['variants'][v]['tot'])) for v in VARS},
                  'domestic_pass': DATA['dep_mix']['domestic']['pass'][-1],
                  'new_nations': new_nat, 'new_cities': new_city})
        return 0
    except Exception as e:  # noqa: BLE001
        out_json({'status': 'error', 'message': f'{type(e).__name__}: {e}'})
        return 2


# ---------------------------------------------------------------- --verify-month
def cmd_verify(month):
    try:
        ym = ym_arg(month)
        raw_obj = read_raw_file(RAW_PATH)
        present = {line.split(',', 1)[0] for line in raw_obj['variants']['dep_mix']['A'][1:]}
        if ym not in present:
            raise RuntimeError(f'{P.ym_dash(ym)}은 원자료에 없습니다.')
        log(f'{P.ym_dash(ym)} 다시 받아 원자료와 비교')
        nm = C.to_variants(C.collect(ym, log=log))
    except Exception as e:  # noqa: BLE001
        out_json({'status': 'error', 'message': f'{type(e).__name__}: {e}'})
        return 2
    diffs = []
    keyn = {'A': 3, 'C': 2, 'D': 2}  # 키 열 수(ym 포함)
    for v in VARS:
        rows = P.month_rows(nm['variants'][v], ym)
        for k in TABLES:
            cur = {}
            for line in raw_obj['variants'][v][k][1:]:
                r = line.split(',')
                if r[0] == ym and not (k == 'A' and r[1] == '전체 합계'):
                    cur[tuple(r[:keyn[k]])] = r[keyn[k]:]
            new = {}
            for r in rows[k]:
                s = csv_line(r).split(',')
                new[tuple(s[:keyn[k]])] = s[keyn[k]:]
            for key in sorted(set(cur) | set(new)):
                if cur.get(key) != new.get(key):
                    diffs.append({'variant': v, 'table': k, 'key': list(key[1:]), 'raw': cur.get(key), 'portal': new.get(key)})
    res = {'status': 'ok' if not diffs else 'diff', 'month': P.ym_dash(ym), 'diff_count': len(diffs), 'diffs': diffs[:50]}
    out_json(res)
    return 0 if not diffs else 1


# ---------------------------------------------------------------- --rebuild
def cmd_rebuild():
    try:
        raw_obj = read_raw_file(RAW_PATH)
        old = read_env()
        DATA = P.build_all(raw_obj)
        env = P.make_envelope(DATA, old['data']['xmeta'], old['meta']['updated_at'])
        same = env == old
        write_env(env)
        out_json({'status': 'rebuilt', 'as_of': env['meta']['as_of'], 'unchanged': same})
        return 0
    except Exception as e:  # noqa: BLE001
        out_json({'status': 'error', 'message': f'{type(e).__name__}: {e}'})
        return 2


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--check', action='store_true')
    g.add_argument('--update', action='store_true')
    g.add_argument('--verify-month', metavar='YYYY-MM')
    g.add_argument('--rebuild', action='store_true')
    ap.add_argument('--month', metavar='YYYY-MM', help='--update 대상 월(기본: as_of 다음 달)')
    ap.add_argument('--force', action='store_true', help='연속되지 않는 달도 추가')
    ap.add_argument('--date', metavar='YYYY-MM-DD', help='추출일/updated_at (기본: 오늘)')
    a = ap.parse_args()
    if a.check:
        return cmd_check()
    if a.update:
        return cmd_update(a.month, a.force, a.date)
    if a.verify_month:
        return cmd_verify(a.verify_month)
    return cmd_rebuild()


if __name__ == '__main__':
    sys.exit(main())
