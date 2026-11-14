"""airportal 지역/국가·노선 통계 한 달 수집 — 아티팩트 도구 collect.js + assemble.py를 파이썬(표준 라이브러리)으로 옮긴 것.

collect(ym)      -> collect.js의 out 객체와 같은 구조 {ym, D:{tot,tot4,rows,city,pass4}, A:{...}, dom:[...]}
to_variants(d)   -> assemble.py 결과와 같은 구조 {ym, variants:{dep_mix:{A,C,D,tot}, ...}} (update 입력 newmonth.json)

API(브라우저 화면이 쓰는 것과 같음):
  지역/국가 POST /stats/transport/getDetailedAirTransportStats4.do  (1회로 전국 합산, 첫 행 '전체 합계')
  노선      POST /stats/transport/getDetailedAirTransportStats3.do  (국내 15개 공항 × 국내선 D / 국제선 I)
  pass_gubun 1=유임여객, 4=유임+환승 / arvl_type D=출발, A=도착. 유임/유임+환승은 여객만 다르고 운항·화물은 같다.
"""
import http.cookiejar
import json
import time
import urllib.error
import urllib.request

BASE = 'https://www.airportal.go.kr'
PAGE = BASE + '/stats/transport/chartDetail4.do'
REGION_API = '/stats/transport/getDetailedAirTransportStats4.do'
ROUTE_API = '/stats/transport/getDetailedAirTransportStats3.do'
APS = ['ICN', 'GMP', 'PUS', 'CJU', 'TAE', 'KWJ', 'CJJ', 'MWX', 'RSU', 'USN', 'HIN', 'KPO', 'KUV', 'WJU', 'YNY']
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36')


class NotYetAvailable(Exception):
    """포털에 해당 월 데이터가 아직 없음"""


class Portal:
    def __init__(self, timeout=60, retries=3):
        self.timeout = timeout
        self.retries = retries
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._open(urllib.request.Request(PAGE, headers={'User-Agent': UA}))  # 세션 쿠키(JSESSIONID)

    def _open(self, req):
        last = None
        for i in range(self.retries):
            try:
                with self.op.open(req, timeout=self.timeout) as r:
                    return r.read()
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = e
                time.sleep(2 * (i + 1))
        raise RuntimeError(f'airportal 요청 실패: {req.full_url} ({last})')

    def post(self, path, body):
        req = urllib.request.Request(
            BASE + path, data=json.dumps(body).encode('utf-8'),
            headers={'User-Agent': UA, 'Content-Type': 'application/json', 'Referer': PAGE,
                     'Accept': 'application/json, text/plain, */*', 'X-Requested-With': 'XMLHttpRequest'})
        raw = self._open(req)
        try:
            j = json.loads(raw.decode('utf-8'))
        except ValueError:
            raise RuntimeError(f'airportal 응답이 JSON이 아닙니다: {path} {raw[:200]!r}')
        return j.get('content') or []

    def region(self, ym, pg, at):
        return self.post(REGION_API, {'last_yearmonth': ym, 'this_yearmonth': ym, 'sn_gubun': 'total', 'pass_gubun': pg,
                                      'carge_gubun': 'total', 'pyn_gubun': 'total', 'arvl_type': at})

    def route(self, ym, ap, pg, di, at):
        return self.post(ROUTE_API, {'last_yearmonth': ym, 'this_yearmonth': ym, 'sn_gubun': 'total', 'airport_gubun': ap,
                                     'pass_gubun': pg, 'carge_gubun': 'total', 'forreign_airport_gubun': 'total',
                                     'di_gubun': di, 'arvl_type': at, 'pyn_gubun': 'total'})


def N(x):
    """JS Number(x||0)과 같게: 정수면 int, 아니면 float"""
    if x is None or x == '':
        return 0
    v = float(x)
    return int(v) if v.is_integer() else v


def grand_total(rows):
    """지역/국가 응답의 '전체 합계' 행 → [운항, 여객, 화물] (없으면 None)"""
    for r in rows:
        if r.get('area_name') == '전체 합계':
            return [N(r.get('flight_cnt')), N(r.get('pass')), N(r.get('cargo'))]
    return None


def _get(p, ym, pg, at):
    reg = p.region(ym, pg, at)
    R = {'tot': None, 'rows': []}
    for r in reg:
        v = [r.get('area_name'), r.get('area_listorder'), r.get('nation_name'), N(r.get('flight_cnt')), N(r.get('pass')), N(r.get('cargo'))]
        if r.get('area_name') == '전체 합계':
            R['tot'] = v[3:]
        else:
            R['rows'].append(v)
    city, dom = {}, []
    for ap in APS:
        for di in ('D', 'I'):
            for r in p.route(ym, ap, pg, di, at):
                if r.get('code_name') == '전체 합계':
                    continue
                v = [N(r.get('flight_cnt')), N(r.get('pass')), N(r.get('cargo'))]
                if di == 'I':
                    k = r.get('airport_name')
                    c = city.setdefault(k, [0, 0, 0])
                    for i in range(3):
                        c[i] += v[i]
                else:
                    dom.append([r.get('code_name'), r.get('airport_name')] + v)
    return R, city, dom


def collect(ym, portal=None, log=None):
    """collect.js와 같은 순서·검사로 한 달(YYYYMM) 수집"""
    p = portal or Portal()
    out = {'ym': ym}
    for at in ('D', 'A'):
        if log:
            log(f'  수집 {ym} {"출발" if at == "D" else "도착"} (유임여객 / 유임+환승)')
        aR, acity, adom = _get(p, ym, '1', at)
        if not aR['tot'] or not aR['tot'][1]:
            raise NotYetAvailable(f'{ym} 데이터가 아직 없습니다')
        bR, bcity, _ = _get(p, ym, '4', at)
        if not bR['tot']:
            raise NotYetAvailable(f'{ym} 데이터가 아직 없습니다(유임+환승)')
        ck = list(acity.keys())
        if [r[2] for r in aR['rows']] != [r[2] for r in bR['rows']] or ck != list(bcity.keys()):
            raise RuntimeError('유임/유임+환승 행 순서 불일치')
        out[at] = {'tot': aR['tot'], 'tot4': bR['tot'], 'rows': aR['rows'],
                   'city': [[k] + acity[k] for k in ck],
                   'pass4': {'reg': [r[4] for r in bR['rows']], 'city': [bcity[k][1] for k in ck]}}
        if at == 'D':
            out['dom'] = adom
    return out


KPO = {'포항 경주(KPO)': '포항경주(KPO)'}
KPO_INV = {v: k for k, v in KPO.items()}


def to_variants(d):
    """assemble.py 변환부(체크섬 대조 제외 — 브라우저 전사가 없으므로 불필요)"""
    domD = [[f'{o}>{t}', f, p, c] for o, t, f, p, c in d['dom']]
    domA = [[f'{KPO.get(t, t)}>{KPO_INV.get(o, o)}', f, p, c] for o, t, f, p, c in d['dom']]  # 도착 라벨 = 출발 조회 행을 뒤집은 것
    out = {'ym': d['ym'], 'variants': {}}
    for v, at, u4, dom in [('dep_mix', 'D', 1, domD), ('dep_paid', 'D', 0, domD), ('arr_mix', 'A', 1, domA), ('arr_paid', 'A', 0, domA)]:
        x = d[at]
        A = [[r[0], r[2], r[3], (x['pass4']['reg'][i] if u4 else r[4]), r[5]] for i, r in enumerate(x['rows'])]
        C = [[r[0], r[1], (x['pass4']['city'][i] if u4 else r[2]), r[3]] for i, r in enumerate(x['city'])]
        tot = x['tot4'] if u4 else x['tot']
        sa, sc = sum(r[3] for r in A), sum(r[2] for r in C)
        if not (sa == tot[1] == sc):
            raise RuntimeError(f'{v} 여객 합계 불일치: 지역국가 {sa:,} / 포털 총계 {tot[1]:,} / 도시 {sc:,}')
        out['variants'][v] = {'A': A, 'C': C, 'D': dom, 'tot': tot}
    if sum(r[2] for r in domD) != sum(r[2] for r in domA):
        raise RuntimeError('국내선 출발/도착 여객 합계 불일치')
    return out
