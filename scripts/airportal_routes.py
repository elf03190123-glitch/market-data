#!/usr/bin/env python3
"""한국 주요 공항별 노선 분석 — airportal 원자료 수집 + 대시보드 데이터 빌드.

데이터 출처: 국토교통부 항공정보포털(airportal.go.kr) 항공통계 > 일자별 통계 > 노선
  (페이지 /stats/transport/dailyRoute.do 가 부르는 POST /stats/transport/selectDailyStatisticsByRoute.do,
   JSON {"startDt":"YYYYMMDD","endDt":"YYYYMMDD","koreaAirport":"<ICAO>","airlineType":"I"})
  응답 행 = 출발공항 × 도착지(ICAO) × 항공사(ICAO) 의 운항·여객·화물(출발/도착/소계). 출발(dep) 값만 저장한다.

파일 (이 스크립트 기준 상대경로):
  ../data/airport-routes-raw.json   원자료(월 × 공항 × 도착지 × 항공사, 출발 편·여객·화물) + 코드표
  ../data/airport-routes.json       대시보드(routes/index.html)가 읽는 집계 데이터 {meta, data}

사용법:
  python airportal_routes.py --check                 # meta.as_of 다음 달이 airportal에 완전히 올라왔는지 (0=있음, 1=아직, 2=오류)
  python airportal_routes.py --update [--month YYYY-MM] [--force]
  python airportal_routes.py --rebuild               # 원자료로 airport-routes.json 재생성
  python airportal_routes.py --verify-month YYYY-MM  # 해당 월을 다시 받아 원자료와 대조 (차이 0이면 0)
표준 라이브러리만 사용한다.
"""
import argparse
import calendar
import datetime as dt
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.request
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
RAW_PATH = os.path.join(SITE, 'data', 'airport-routes-raw.json')
OUT_PATH = os.path.join(SITE, 'data', 'airport-routes.json')

BASE = 'https://www.airportal.go.kr'
PAGE_URL = BASE + '/stats/transport/dailyRoute.do'
API_URL = BASE + '/stats/transport/selectDailyStatisticsByRoute.do'
AIRLINE_LIST_URL = BASE + '/common/getAirlineGubun.do'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36')

# (대시보드 코드, airportal ICAO, 짧은 이름, 긴 이름)
AIRPORTS = [
    ('ICN', 'RKSI', '인천', '인천공항'),
    ('PUS', 'RKPK', '김해', '김해공항'),
    ('GMP', 'RKSS', '김포', '김포공항'),
    ('CJJ', 'RKTU', '청주', '청주공항'),
    ('TAE', 'RKTN', '대구', '대구공항'),
]
AP_CODES = [a[0] for a in AIRPORTS]
AP_ICAO = {a[0]: a[1] for a in AIRPORTS}
AP_LABEL = {a[0]: a[2] for a in AIRPORTS}

TYPES = ['국적 FSC', '국적 LCC', '외항 FSC', '외항 LCC']
UNKNOWN_AIRLINE = '(미상)'           # 항공사 코드가 빈 행
ETC = '기타'
TOP_ROUTES = 30
TOP_AIRLINES = 15
TOP_TREND = 6                         # 월별 궤적에 그리는 상위 노선/항공사 수
RA_MAX_ROWS = 7                       # 노선별 항공사 표: 7개사 이하면 모두, 넘으면 상위 6 + 기타
RA_MIN_PAX = 50000                    # 노선 선택 목록: TOP30 · 미국 · 유럽 외에는 당해 누적 5만 명 이상
RA_ALL_MAX = 60                       # 당해 여객 있는 노선이 이 수 이하인 공항은 전 노선을 선택 목록에
WINDOW_YEARS = 3                      # 대시보드 기간: (당해-2)년 1월 ~ 최신월

# 노선 지역 그룹 (도착지 ICAO 접두어). 위에서부터 먼저 맞는 규칙을 쓴다.
REGION_ORDER = ['일본', '중국 · 홍콩 · 마카오', '대만', '기타', '미국 (괌 · 사이판 포함)', '유럽']
REGION_RULES = [
    (('RJ', 'RO'), '일본'),
    (('Z', 'VH', 'VM'), '중국 · 홍콩 · 마카오'),
    (('RC',), '대만'),
    (('K', 'PG', 'PH', 'PA', 'PW'), '미국 (괌 · 사이판 포함)'),
    (('E', 'L', 'UB', 'UD', 'UG', 'UT', 'UU', 'UL', 'UW', 'UR', 'UM', 'UK'), '유럽'),
]


def region_of(icao):
    for prefixes, label in REGION_RULES:
        if icao.startswith(prefixes):
            return label
    return '기타'


# ---------------------------------------------------------------- 유틸
def jprint(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + '\n')
    sys.stdout.flush()


def ym_to_dash(ym):
    return ym[:4] + '-' + ym[4:]


def dash_to_ym(s):
    m = re.fullmatch(r'(\d{4})-?(\d{2})', s.strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ValueError('월 형식은 YYYY-MM: %r' % s)
    return m.group(1) + m.group(2)


def next_ym(ym):
    y, m = int(ym[:4]), int(ym[4:])
    return '%04d%02d' % ((y + 1, 1) if m == 12 else (y, m + 1))


def prev_ym(ym):
    y, m = int(ym[:4]), int(ym[4:])
    return '%04d%02d' % ((y - 1, 12) if m == 1 else (y, m - 1))


def month_last_day(ym):
    return calendar.monthrange(int(ym[:4]), int(ym[4:]))[1]


def rhalf(x, nd=0):
    """JS Math.round / toFixed 와 같은 반올림(0.5 올림)."""
    k = 10 ** nd
    v = int(x * k + 0.5 + 1e-9) / k if x >= 0 else -int(-x * k + 0.5 + 1e-9) / k
    return int(v) if nd == 0 else v


def pct1(a, b):
    return rhalf(a / b * 100, 1) if b else 0.0


# ---------------------------------------------------------------- airportal 호출
class Portal:
    def __init__(self):
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._open(urllib.request.Request(PAGE_URL, headers={'User-Agent': UA}))

    def _open(self, req, tries=3):
        last = None
        for i in range(tries):
            try:
                with self.op.open(req, timeout=60) as r:
                    return r.read().decode('utf-8')
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(2 + 3 * i)
        raise RuntimeError('airportal 요청 실패: %s' % last)

    def post(self, url, payload, referer=PAGE_URL):
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={
            'User-Agent': UA, 'Content-Type': 'application/json;charset=UTF-8',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'request-call': 'web-rest', 'X-Requested-With': 'XMLHttpRequest',
            'Origin': BASE, 'Referer': referer})
        d = json.loads(self._open(req))
        if str(d.get('resultCode')) != '200' or not isinstance(d.get('content'), list):
            raise RuntimeError('airportal 응답 이상: %s' % str(d)[:200])
        return d['content']

    def routes(self, ap, start, end):
        """start/end = YYYYMMDD. 국제선 노선 × 항공사 행 목록(원본 dict)."""
        return self.post(API_URL, {'startDt': start, 'endDt': end,
                                   'koreaAirport': AP_ICAO[ap], 'airlineType': 'I'})

    def month(self, ap, ym):
        return self.routes(ap, ym + '01', '%s%02d' % (ym, month_last_day(ym)))

    def airline_names(self):
        out = {}
        for a in self.post(AIRLINE_LIST_URL, {}, referer=BASE + '/stats/transport/chartDetail5.do'):
            nm = re.sub(r'\(%s\)$' % re.escape(a['codeId']), '', a['codeVal']).strip()
            out[a['codeId']] = nm
        return out


def _int(s):
    s = str(s if s is not None else '0').replace(',', '').strip() or '0'
    v = float(s)
    return int(v) if v == int(v) else v


DEST_RE = re.compile(r'^(.*)\(([A-Z0-9]{3,4})\)\s*$')


def parse_rows(content):
    """airportal 행 → ([dest_icao, airline_icao, flights, pax, cargo], {icao: name})."""
    rows, names = [], {}
    for x in content:
        icao = (x.get('ap_ROUTE') or '').strip()
        m = DEST_RE.match(x.get('ap2') or '')
        if not icao and m:
            icao = m.group(2)
        if not icao:
            raise RuntimeError('도착지 코드 없음: %r' % x)
        names[icao] = (m.group(1).strip() if m else '')
        rows.append([icao, (x.get('al_ICAO') or '').strip(),
                     _int(x.get('dep_FP')), _int(x.get('dep_PERSON')), _int(x.get('dep_WEIGHT'))])
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows, names


def fetch_month(portal, ym, pause=0.4):
    """{ap: rows}, {icao: name}"""
    out, names = {}, {}
    for ap in AP_CODES:
        rows, nm = parse_rows(portal.month(ap, ym))
        out[ap] = rows
        names.update(nm)
        time.sleep(pause)
    return out, names


# ---------------------------------------------------------------- 원자료 파일
def load_raw():
    with open(RAW_PATH, encoding='utf-8') as f:
        return json.load(f)


def dump_compact(obj):
    """최상위·months 단위로 줄을 나눈 압축 JSON (diff가 읽히도록)."""
    if isinstance(obj, dict) and 'months' in obj and isinstance(obj['months'], dict):
        parts = []
        for k, v in obj.items():
            if k == 'months':
                inner = []
                for ym, aps in v.items():
                    inner.append(json.dumps(ym) + ':{' + ','.join(
                        json.dumps(ap) + ':' + json.dumps(rows, ensure_ascii=False, separators=(',', ':'))
                        for ap, rows in aps.items()) + '}')
                parts.append(json.dumps(k) + ':{\n' + ',\n'.join(inner) + '\n}')
            else:
                parts.append(json.dumps(k) + ':' + json.dumps(v, ensure_ascii=False, separators=(',', ':')))
        return '{\n' + ',\n'.join(parts) + '\n}\n'
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':')) + '\n'


def write_text(path, text):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    os.replace(tmp, path)


def save_raw(raw):
    raw['months'] = dict(sorted(raw['months'].items()))
    write_text(RAW_PATH, dump_compact(raw))


# ---------------------------------------------------------------- 빌드
def dest_name(raw, icao):
    return (raw['dests'].get(icao) or '').strip() or icao


def airline_info(raw, code):
    a = raw['airlines'].get(code)
    if a:
        return a['name'], a['type']
    return (code or UNKNOWN_AIRLINE), '외항 FSC'


def flat_rows(raw, months):
    """(ym_index, ap, route_name, icao, airline_name, type, f, p, c)"""
    idx = {m: i for i, m in enumerate(months)}
    out = []
    for ym in months:
        for ap in AP_CODES:
            for icao, al, f, p, c in raw['months'][ym].get(ap, []):
                an, at = airline_info(raw, al)
                out.append((idx[ym], ap, dest_name(raw, icao), icao, an, at, f, p, c))
    return out


def build(raw):
    months_all = sorted(raw['months'])
    if not months_all:
        raise RuntimeError('원자료가 비어 있음')
    cur = months_all[-1]
    CY = int(cur[:4])
    N = int(cur[4:])
    first = '%04d01' % (CY - WINDOW_YEARS + 1)
    months = [m for m in months_all if m >= first]
    # 기간 안 월은 빠짐없이 연속이어야 한다
    m, expect = first, []
    while m <= cur:
        expect.append(m)
        m = next_ym(m)
    missing = [x for x in expect if x not in raw['months']]
    if missing:
        raise RuntimeError('원자료에 빠진 월: %s' % ', '.join(missing))
    months = expect
    years = [str(CY - k) for k in range(WINDOW_YEARS - 1, -1, -1)]   # [BY, PY, CY]
    BY, PY, CYs = years[0], years[-2], years[-1]
    T = len(months)
    yidx = {y: [i for i, mm in enumerate(months) if mm[:4] == y] for y in years}
    ytdidx = {y: [i for i in yidx[y] if int(months[i][4:]) <= N] for y in years}
    in_ytd = {y: set(v) for y, v in ytdidx.items()}
    in_year = {y: set(v) for y, v in yidx.items()}

    rows = flat_rows(raw, months)

    def scope(ap):
        rs = [r for r in rows if ap == 'ALL' or r[1] == ap]
        tot = {'flight': [0] * T, 'pax': [0] * T, 'cargo': [0] * T}
        kr = [0] * T
        rmon = defaultdict(lambda: [0] * T)       # route -> pax by month
        rflt = defaultdict(lambda: [0] * T)       # route -> flights by month
        rico = defaultdict(lambda: defaultdict(int))
        amon = defaultdict(lambda: [0] * T)       # airline -> pax by month
        atype = {}
        tmon = {t: [0] * T for t in TYPES}
        ra = defaultdict(lambda: defaultdict(lambda: [0] * T))   # route -> airline -> pax
        ra_seen = defaultdict(set)                                # route -> airlines with any activity
        ract = defaultdict(set)                                   # route -> month idx with activity
        aact = defaultdict(set)                                   # airline -> month idx with activity
        for i, _ap, rn, icao, an, at, f, p, c in rs:
            tot['flight'][i] += f
            tot['pax'][i] += p
            tot['cargo'][i] += c
            if at.startswith('국적'):
                kr[i] += p
            rmon[rn][i] += p
            rflt[rn][i] += f
            rico[rn][icao] += p + f
            amon[an][i] += p
            atype[an] = at
            tmon[at][i] += p
            ra[rn][an][i] += p
            if f or p or c:
                ra_seen[rn].add(an)
                ract[rn].add(i)
                aact[an].add(i)

        def ssum(arr, ii):
            return sum(arr[i] for i in ii)

        D = {'months': months, 'total': tot}
        D['ppf'] = [rhalf(p / f) if f else 0 for p, f in zip(tot['pax'], tot['flight'])]
        D['krShare'] = [pct1(k, p) for k, p in zip(kr, tot['pax'])]

        def rank(series, ii, n, prev_ii=None, base_ii=None, tie=None):
            # 대상 = 기간 안에 한 번이라도 나온 모든 노선/항공사 (여객 0도 포함 — 원본 대시보드와 같은 방식)
            items = []
            for name, s in series.items():
                o = {'name': name, 'v': ssum(s, ii)}
                if prev_ii is not None:
                    o['p'] = ssum(s, prev_ii)
                if base_ii is not None:
                    o['pb'] = ssum(s, base_ii)
                items.append(o)
            items.sort(key=lambda o: (-o['v'], -(tie(o['name']) if tie else 0), o['name']))
            return items[:n]

        cur_ii, prev_ii, base_ii = ytdidx[CYs], ytdidx[PY], ytdidx[BY]
        D['topRoutes'] = rank(rmon, cur_ii, TOP_ROUTES, prev_ii, base_ii)
        D['topAirlines'] = rank(amon, cur_ii, TOP_AIRLINES, prev_ii, base_ii)
        rcur = lambda n: ssum(rmon[n], cur_ii)
        acur = lambda n: ssum(amon[n], cur_ii)
        D['topRoutesFY'] = {BY: rank(rmon, yidx[BY], TOP_ROUTES, tie=rcur),
                            PY: rank(rmon, yidx[PY], TOP_ROUTES, yidx[BY], tie=rcur)}
        D['topAirlinesFY'] = {BY: rank(amon, yidx[BY], TOP_AIRLINES, tie=acur),
                              PY: rank(amon, yidx[PY], TOP_AIRLINES, yidx[BY], tie=acur)}
        D['routeMonthly'] = {o['name']: rmon[o['name']] for o in D['topRoutes'][:TOP_TREND]}
        D['airMonthly'] = {o['name']: amon[o['name']] for o in D['topAirlines'][:TOP_TREND]}

        # 신규 노선: 당해 운항 기록이 있고 앞의 두 해에는 기록이 없음 / 중단 노선: 전년 1~N월 기록이 있고 당해 기록 없음
        hist = in_year[BY] | in_year[PY]
        new, gone = [], []
        for rn, act in ract.items():
            had_hist = bool(act & hist)
            had_cur = bool(act & in_ytd[CYs])
            if had_cur and not had_hist:
                new.append({'name': rn, 'v': ssum(rmon[rn], cur_ii), 'f': ssum(rflt[rn], cur_ii)})
            elif (act & in_ytd[PY]) and not (act & in_year[CYs]):
                gone.append({'name': rn, 'v': ssum(rmon[rn], prev_ii), 'f': ssum(rflt[rn], prev_ii)})
        for lst in (new, gone):
            lst.sort(key=lambda o: (-o['v'], -o['f'], o['name']))
        D['newRoutes'], D['goneRoutes'] = new, gone

        # 노선 선택 목록 (지역 그룹)
        #  당해 여객이 있는 노선이 RA_ALL_MAX개 이하인 공항: 그 노선 전부
        #  그 밖: TOP30 + 미국·유럽 전 노선(당해 여객 > 0) + 당해 누적 RA_MIN_PAX명 이상
        rv = {rn: ssum(rmon[rn], cur_ii) for rn in rmon}
        top30 = {o['name'] for o in D['topRoutes'] if o['v'] > 0}
        active = [rn for rn in rmon if rv[rn] > 0]
        rule_all = len(active) <= RA_ALL_MAX
        groups = defaultdict(list)
        for rn in active:
            icao = max(rico[rn].items(), key=lambda kv: (kv[1], kv[0]))[0]
            reg = region_of(icao)
            if rule_all or rn in top30 or rv[rn] >= RA_MIN_PAX or reg in ('미국 (괌 · 사이판 포함)', '유럽'):
                groups[reg].append(rn)
        D['routeGroupsRule'] = 'all' if rule_all else 'major'
        D['routeGroups'] = []
        for reg in REGION_ORDER:
            if groups.get(reg):
                lst = sorted(groups[reg], key=lambda r: (-rv[r], -sum(rmon[r]), r))
                D['routeGroups'].append({'label': reg, 'routes': lst})

        # 노선별 취항 항공사
        RA = {}
        for g in D['routeGroups']:
            for rn in g['routes']:
                als = [a for a in ra[rn] if a in ra_seen[rn] or any(ra[rn][a])]
                stats = [{'name': a, 'v': ssum(ra[rn][a], cur_ii), 'p': ssum(ra[rn][a], prev_ii)} for a in als]
                stats.sort(key=lambda o: (-o['v'], -o['p'], -sum(ra[rn][o['name']]), o['name']))
                if len(stats) > RA_MAX_ROWS:
                    keep, rest = stats[:RA_MAX_ROWS - 1], stats[RA_MAX_ROWS - 1:]
                else:
                    keep, rest = stats, []
                series = {o['name']: ra[rn][o['name']] for o in keep}
                rws = [dict(o) for o in keep]
                etc = []
                if rest:
                    es = [0] * T
                    for o in rest:
                        for i, v in enumerate(ra[rn][o['name']]):
                            es[i] += v
                    series[ETC] = es
                    etc = [{'name': o['name'], 'v': o['v']} for o in rest if o['v'] > 0]
                    rws.append({'name': ETC, 'v': sum(o['v'] for o in rest),
                                'p': sum(o['p'] for o in rest), 'n': len(etc)})
                RA[rn] = {'series': series, 'rows': rws,
                          'totalCur': sum(o['v'] for o in stats),
                          'totalPrev': sum(o['p'] for o in stats), 'etc': etc}
        D['routeAirline'] = RA

        # 누적(1~N월) 요약
        ytd = {}
        for y in years:
            ii = ytdidx[y]
            routes_n = sum(1 for rn, act in ract.items() if act & in_ytd[y])
            airlines_n = sum(1 for an, act in aact.items() if act & in_ytd[y])
            ytd[y] = {'flight': ssum(tot['flight'], ii), 'pax': ssum(tot['pax'], ii),
                      'cargo': ssum(tot['cargo'], ii), 'kr': ssum(kr, ii),
                      'routes': routes_n, 'airlines': airlines_n}
        D['ytd'] = ytd
        D['top30Share'] = pct1(sum(o['v'] for o in D['topRoutes']), ytd[CYs]['pax'])
        counts = {t: 0 for t in TYPES}
        for an, act in aact.items():
            if act & in_ytd[CYs]:
                counts[atype[an]] += 1
        D['airType'] = {'types': TYPES, 'monthly': tmon,
                        'ytd': {t: {y: ssum(tmon[t], ytdidx[y]) for y in years} for t in TYPES},
                        'counts': counts}
        return D

    data = {'airports': [{'code': 'ALL', 'label': '전체 5개 공항'}] +
                        [{'code': c, 'label': AP_LABEL[c]} for c in AP_CODES],
            'period': {'first': months[0], 'last': cur, 'cy': CYs, 'py': PY, 'by': BY,
                       'n': N, 'years': years, 'nMonths': T}}
    for ap in ['ALL'] + AP_CODES:
        data[ap] = scope(ap)
    tv = sum(data[c]['ytd'][CYs]['pax'] for c in AP_CODES)
    data['apCompare'] = [{
        'code': c, 'label': AP_LABEL[c],
        'v': data[c]['ytd'][CYs]['pax'], 'p': data[c]['ytd'][PY]['pax'], 'pb': data[c]['ytd'][BY]['pax'],
        'f': data[c]['ytd'][CYs]['flight'], 'c': data[c]['ytd'][CYs]['cargo'],
        'n': data[c]['ytd'][CYs]['routes'], 'share': pct1(data[c]['ytd'][CYs]['pax'], tv),
        'monthly': data[c]['total']['pax']} for c in AP_CODES]
    data['labels'] = raw.get('labels', {})
    return data


def envelope(raw, data, updated_at=None):
    return {'meta': {
        'id': 'airport-routes',
        'title': '한국 주요 공항별 노선 분석',
        'source': '국토교통부 항공정보포털(airportal.go.kr)',
        'source_url': PAGE_URL,
        'basis': '국제선 출발, 인천·김해·김포·청주·대구',
        'as_of': ym_to_dash(data['period']['last']),
        'updated_at': updated_at or dt.date.today().isoformat(),
        'schema_version': 1,
    }, 'data': data}


def validate(raw, data):
    """원자료 합계 == 빌드 결과 (공항·월별 편/여객/화물), ALL == 5개 공항 합."""
    errs = []
    months = data['ALL']['months']
    for i, ym in enumerate(months):
        allsum = [0, 0, 0]
        for ap in AP_CODES:
            rs = raw['months'][ym].get(ap, [])
            want = [sum(r[2] for r in rs), sum(r[3] for r in rs), sum(r[4] for r in rs)]
            T = data[ap]['total']
            got = [T['flight'][i], T['pax'][i], T['cargo'][i]]
            if want != got:
                errs.append('%s %s 원자료 %s != 빌드 %s' % (ap, ym, want, got))
            allsum = [a + b for a, b in zip(allsum, want)]
            if not rs:
                errs.append('%s %s 행 없음' % (ap, ym))
        T = data['ALL']['total']
        if allsum != [T['flight'][i], T['pax'][i], T['cargo'][i]]:
            errs.append('ALL %s 합계 불일치' % ym)
    for ap in ['ALL'] + AP_CODES:
        d = data[ap]
        cy = data['period']['cy']
        ts = sum(d['airType']['ytd'][t][cy] for t in TYPES)
        if ts != d['ytd'][cy]['pax']:
            errs.append('%s 유형별 합 %s != 누적 여객 %s' % (ap, ts, d['ytd'][cy]['pax']))
        for rn, o in d['routeAirline'].items():
            if sum(r['v'] for r in o['rows']) != o['totalCur']:
                errs.append('%s %s 항공사 합 불일치' % (ap, rn))
    return errs


def write_outputs(raw, data, updated_at=None):
    env = envelope(raw, data, updated_at)
    write_text(OUT_PATH, json.dumps(env, ensure_ascii=False, separators=(',', ':')) + '\n')
    return env


# ---------------------------------------------------------------- 명령
def read_meta_as_of():
    try:
        with open(OUT_PATH, encoding='utf-8') as f:
            return dash_to_ym(json.load(f)['meta']['as_of'])
    except FileNotFoundError:
        return max(load_raw()['months'])


def month_complete(portal, ym, today=None):
    """해당 월 말일 하루치가 5개 공항 모두 조회되면 완전 게시로 본다.
    (포털은 최근 7일 자료를 막아 두므로 말일+7일이 지나기 전에는 기다린다.)"""
    today = today or dt.date.today()
    last = dt.date(int(ym[:4]), int(ym[4:]), month_last_day(ym))
    detail = {}
    if today <= last + dt.timedelta(days=7):
        return False, {'reason': 'too_early', 'earliest': (last + dt.timedelta(days=8)).isoformat()}
    day = last.strftime('%Y%m%d')
    first = last.replace(day=1).strftime('%Y%m%d')
    ok = True
    for ap in AP_CODES:
        n_last = len(portal.routes(ap, day, day))
        n_first = len(portal.routes(ap, first, first))
        detail[ap] = {'first_day_rows': n_first, 'last_day_rows': n_last}
        if n_last == 0 or n_first == 0:
            ok = False
        time.sleep(0.3)
    return ok, detail


def cmd_check(args):
    try:
        target = next_ym(read_meta_as_of())
        portal = Portal()
        ok, detail = month_complete(portal, target)
        jprint({'month': ym_to_dash(target), 'available': ok, 'detail': detail})
        return 0 if ok else 1
    except Exception as e:  # noqa: BLE001
        jprint({'error': str(e)})
        return 2


def yoy(a, b):
    return rhalf((a / b - 1) * 100, 1) if b else None


def month_summary(raw, data, ym, new_routes, new_airlines):
    py = '%04d%s' % (int(ym[:4]) - 1, ym[4:])
    per = {}
    for ap in AP_CODES:
        rs = raw['months'][ym][ap]
        f, p = sum(r[2] for r in rs), sum(r[3] for r in rs)
        prs = raw['months'].get(py, {}).get(ap, [])
        pf, pp = sum(r[2] for r in prs), sum(r[3] for r in prs)
        per[ap] = {'flights': f, 'pax': p, 'flights_yoy_pct': yoy(f, pf), 'pax_yoy_pct': yoy(p, pp),
                   'rows': len(rs)}
    rt = defaultdict(int)
    for ap in AP_CODES:
        for icao, al, f, p, c in raw['months'][ym][ap]:
            rt[dest_name(raw, icao)] += p
    top = sorted(rt.items(), key=lambda kv: -kv[1])[:10]
    tot_p = sum(v['pax'] for v in per.values())
    tot_f = sum(v['flights'] for v in per.values())
    ptot = sum(sum(r[3] for r in raw['months'].get(py, {}).get(ap, [])) for ap in AP_CODES)
    return {'month': ym_to_dash(ym), 'total': {'flights': tot_f, 'pax': tot_p, 'pax_yoy_pct': yoy(tot_p, ptot)},
            'airports': per, 'top_routes': [{'name': k, 'pax': v} for k, v in top],
            'new_routes': new_routes, 'new_airlines': new_airlines,
            'as_of': ym_to_dash(data['period']['last'])}


def cmd_update(args):
    try:
        raw = load_raw()
        last = max(raw['months'])
        target = dash_to_ym(args.month) if args.month else next_ym(read_meta_as_of())
        if target in raw['months'] and not args.force:
            jprint({'error': '%s 은(는) 이미 원자료에 있음 (덮어쓰려면 --force)' % ym_to_dash(target)})
            return 2
        if target != next_ym(last) and target not in raw['months'] and not args.force:
            jprint({'error': '연속되지 않은 월 %s (원자료 마지막 %s). --force 필요' % (
                ym_to_dash(target), ym_to_dash(last))})
            return 2
        portal = Portal()
        ok, detail = month_complete(portal, target)
        if not ok and not args.force:
            jprint({'error': '%s 자료가 아직 완전하지 않음' % ym_to_dash(target), 'detail': detail})
            return 1
        rows, names = fetch_month(portal, target)
        empty = [ap for ap in AP_CODES if not rows[ap]]
        if empty:
            jprint({'error': '빈 공항 응답: %s' % empty})
            return 2
        old_routes = {dest_name(raw, r[0]) for m in raw['months'].values() for a in m.values() for r in a}
        renamed = {k: [raw['dests'][k], v] for k, v in names.items()
                   if k in raw['dests'] and v and raw['dests'][k] != v}
        for k, v in names.items():
            if v or k not in raw['dests']:
                raw['dests'][k] = v
        new_codes = sorted({r[1] for a in rows.values() for r in a} - set(raw['airlines']))
        new_airlines = []
        if new_codes:
            try:
                portal_names = portal.airline_names()
            except Exception:  # noqa: BLE001
                portal_names = {}
            lcc = set(raw.get('lcc_codes_extra', []))
            for code in new_codes:
                nm = portal_names.get(code) or code or UNKNOWN_AIRLINE
                typ = '외항 LCC' if code in lcc else '외항 FSC'
                raw['airlines'][code] = {'name': nm, 'type': typ}
                new_airlines.append({'code': code, 'name': nm, 'type': typ})
        raw['months'][target] = rows
        raw.setdefault('fetched', {})[target] = dt.date.today().isoformat()
        data = build(raw)
        errs = validate(raw, data)
        if errs:
            jprint({'error': '검증 실패', 'details': errs[:20]})
            return 2
        new_routes = sorted({dest_name(raw, r[0]) for a in rows.values() for r in a} - old_routes)
        save_raw(raw)
        write_outputs(raw, data)
        s = month_summary(raw, data, target, new_routes, new_airlines)
        if renamed:
            s['renamed_destinations'] = renamed
        if new_airlines:
            s['new_airlines_note'] = ('유형은 자동 분류(lcc_codes_extra 목록이면 외항 LCC, 아니면 외항 FSC). '
                                      '국적사이거나 틀리면 원자료 airlines 표를 고친 뒤 --rebuild')
        jprint(s)
        return 0
    except Exception as e:  # noqa: BLE001
        jprint({'error': str(e)})
        return 2


def cmd_rebuild(args):
    try:
        raw = load_raw()
        data = build(raw)
        errs = validate(raw, data)
        if errs:
            jprint({'error': '검증 실패', 'details': errs[:20]})
            return 2
        updated = None
        if args.keep_date and os.path.exists(OUT_PATH):
            with open(OUT_PATH, encoding='utf-8') as f:
                updated = json.load(f)['meta'].get('updated_at')
        env = write_outputs(raw, data, updated)
        jprint({'rebuilt': True, 'as_of': env['meta']['as_of'], 'months': len(data['ALL']['months']),
                'raw_rows': sum(len(a) for m in raw['months'].values() for a in m.values())})
        return 0
    except Exception as e:  # noqa: BLE001
        jprint({'error': str(e)})
        return 2


def cmd_verify(args):
    try:
        raw = load_raw()
        ym = dash_to_ym(args.verify_month)
        if ym not in raw['months']:
            jprint({'error': '%s 은(는) 원자료에 없음' % ym_to_dash(ym)})
            return 2
        portal = Portal()
        rows, names = fetch_month(portal, ym)
        diffs = []
        for ap in AP_CODES:
            have = {(r[0], r[1]): r[2:] for r in raw['months'][ym].get(ap, [])}
            new = {(r[0], r[1]): r[2:] for r in rows[ap]}
            for k in sorted(set(have) | set(new)):
                if have.get(k) != new.get(k):
                    diffs.append({'airport': ap, 'dest': k[0], 'airline': k[1],
                                  'raw': have.get(k), 'portal': new.get(k)})
        name_diffs = {k: [raw['dests'].get(k), v] for k, v in names.items() if v and raw['dests'].get(k) != v}
        jprint({'month': ym_to_dash(ym), 'row_diffs': len(diffs), 'diffs': diffs[:50],
                'dest_name_diffs': name_diffs,
                'rows': {ap: len(rows[ap]) for ap in AP_CODES}})
        return 0 if not diffs and not name_diffs else 1
    except Exception as e:  # noqa: BLE001
        jprint({'error': str(e)})
        return 2


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--check', action='store_true')
    g.add_argument('--update', action='store_true')
    g.add_argument('--rebuild', action='store_true')
    g.add_argument('--verify-month', metavar='YYYY-MM')
    ap.add_argument('--month', metavar='YYYY-MM', help='--update 대상 월 (기본: meta.as_of 다음 달)')
    ap.add_argument('--force', action='store_true', help='연속되지 않은 월/미완성 월/이미 있는 월도 강제로 반영')
    ap.add_argument('--keep-date', action='store_true', help='--rebuild 시 updated_at 유지')
    a = ap.parse_args(argv)
    if a.check:
        return cmd_check(a)
    if a.update:
        return cmd_update(a)
    if a.rebuild:
        return cmd_rebuild(a)
    return cmd_verify(a)


if __name__ == '__main__':
    sys.exit(main())
