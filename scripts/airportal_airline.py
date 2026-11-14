#!/usr/bin/env python3
"""국제선 출발·도착 여객 (항공사별) — airportal.go.kr 월간 자동 수집/갱신 스크립트.

데이터 원천
  항공정보포털 > 항공통계 > 상세조회 > 시계열 탭 (chartDetail5.do)
  상세구분=항공사, 여객, 노선=국제선(di_gubun=I), 출발(arvl_type=D) / 도착(arvl_type=A)
  → 페이지 JS(searchFn)가 호출하는 XHR:
     POST /stats/transport/getDetailedAirTransportStats1Time.do
     body(JSON): {"last_yearmonth":"YYYYMM","this_yearmonth":"YYYYMM",
                  "di_gubun":"I","arvl_type":"D"|"A","airline_gubun":"total"}
     응답 content[] 의 airline_name = "<항공사명>(<코드>) YYYY년 MM월", pass = 유임+환승 여객.
     행 순서: 전체 합계 → 국적사 계 → 국적사들 → 외항사 계 → 외항사들.

데이터 파일: ../data/airline-passengers.json
  {"meta":{...,"as_of":"YYYY-MM"},
   "data":{"months":[...],
           "dep":{"summary":{"total","domestic","foreign"},"airlines":[{full,name,code,category,values,total}]},
           "arr":{...같은 구조...}}}
  방향별로 항공사 목록이 따로 있다(없는 달은 0).

사용법
  python airportal_airline.py --check
  python airportal_airline.py --update [--month YYYY-MM] [--force]
  python airportal_airline.py --verify-month YYYY-MM [--dir dep|arr|both]

종료 코드
  --check        : 0 다음 달 출발·도착 모두 있음 / 1 아직 없음(또는 미완) / 2 네트워크·파싱 오류
  --update       : 0 기록함 / 1 거부(검증 실패·월 순서 위반 등) / 2 네트워크·파싱 오류
  --verify-month : 0 차이 없음 / 1 차이 있음 / 2 네트워크·파싱 오류

표준 라이브러리만 사용.
"""
import argparse
import datetime as dt
import http.cookiejar
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_PATH = HERE.parent / "data" / "airline-passengers.json"

BASE = "https://www.airportal.go.kr"
PAGE_URL = BASE + "/stats/transport/chartDetail5.do"
API_URL = BASE + "/stats/transport/getDetailedAirTransportStats1Time.do"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
TIMEOUT = 60
RETRIES = 3

DIRS = ("dep", "arr")
ARVL_TYPE = {"dep": "D", "arr": "A"}
DIR_LABEL = {"dep": "출발", "arr": "도착"}

ROW_RE = re.compile(r"^(.*\S)\s+(\d{4})년\s*(\d{2})월$")
AIRLINE_RE = re.compile(r"^(.*)\(([A-Z0-9]+)\)$")
# 전월 대비 이 비율 미만이면 '부분 게시' 의심으로 --update 거부 (--force로 무시)
MIN_RATIO_VS_PREV = 0.5


class FetchError(Exception):
    """네트워크/응답 파싱 오류 (exit 2)."""


class Refused(Exception):
    """검증 실패 등으로 쓰기를 거부 (exit 1)."""


# --------------------------------------------------------------------------
# 유틸
# --------------------------------------------------------------------------
def out(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def month_add(ym, n):
    y, m = int(ym[:4]), int(ym[5:7])
    k = y * 12 + (m - 1) + n
    return "%04d-%02d" % (k // 12, k % 12 + 1)


def valid_month(s):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", s or ""):
        raise argparse.ArgumentTypeError("YYYY-MM 형식이어야 합니다: %r" % s)
    return s


def pct(a, b):
    if not b:
        return None
    return round((a - b) / b * 100, 2)


def empty_block():
    return {"summary": {"total": [], "domestic": [], "foreign": []}, "airlines": []}


# --------------------------------------------------------------------------
# JSON 입출력 (사람이 diff 보기 좋게: 항공사 1개당 1줄)
# --------------------------------------------------------------------------
def load_doc():
    with open(DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def dumps_doc(doc):
    j = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":"))
    d = doc["data"]
    lines = ["{", '  "meta": ' + json.dumps(doc["meta"], ensure_ascii=False, indent=2).replace("\n", "\n  ") + ",",
             '  "data": {',
             '    "months": ' + j(d["months"]) + ","]
    for di, key in enumerate(DIRS):
        b = d[key]
        lines += ['    "%s": {' % key,
                  '      "summary": {',
                  '        "total": ' + j(b["summary"]["total"]) + ",",
                  '        "domestic": ' + j(b["summary"]["domestic"]) + ",",
                  '        "foreign": ' + j(b["summary"]["foreign"]),
                  "      },",
                  '      "airlines": [']
        al = b["airlines"]
        for i, a in enumerate(al):
            lines.append("        " + j(a) + ("," if i < len(al) - 1 else ""))
        lines += ["      ]", "    }" + ("," if di < len(DIRS) - 1 else "")]
    lines += ["  }", "}"]
    text = "\n".join(lines) + "\n"
    assert json.loads(text) == doc  # 직렬화 무결성
    return text


def save_doc(doc, path=None):
    path = Path(path or DATA_PATH)
    text = dumps_doc(doc)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".airline-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# --------------------------------------------------------------------------
# airportal 수집
# --------------------------------------------------------------------------
class Client:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.ready = False

    def _open(self, req):
        last = None
        for attempt in range(RETRIES):
            try:
                with self.opener.open(req, timeout=TIMEOUT) as r:
                    return r.status, r.read()
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last = e
                if isinstance(e, urllib.error.HTTPError) and e.code < 500:
                    break
                time.sleep(2 * (attempt + 1))
        raise FetchError("%s: %s" % (req.full_url, last))

    def warmup(self):
        if self.ready:
            return
        req = urllib.request.Request(PAGE_URL, headers={
            "User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9"})
        status, _ = self._open(req)
        if status != 200:
            raise FetchError("page GET status %s" % status)
        self.ready = True

    def post(self, url, payload):
        self.warmup()
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST", headers={
            "User-Agent": UA,
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "request-call": "web-rest",
            "Origin": BASE,
            "Referer": PAGE_URL})
        status, body = self._open(req)
        try:
            res = json.loads(body.decode("utf-8"))
        except Exception as e:
            raise FetchError("JSON 파싱 실패 (%s): %r" % (e, body[:200]))
        if str(res.get("resultCode")) != "200":
            raise FetchError("resultCode=%r" % res.get("resultCode"))
        return res

    def fetch_month(self, ym, direction):
        """한 달치 항공사별 국제선 출발(dep)/도착(arr) 여객. 데이터 없으면 airlines=[]."""
        yyyymm = ym.replace("-", "")
        res = self.post(API_URL, {"last_yearmonth": yyyymm, "this_yearmonth": yyyymm,
                                  "di_gubun": "I", "arvl_type": ARVL_TYPE[direction],
                                  "airline_gubun": "total"})
        content = res.get("content") or []
        if not isinstance(content, list):
            raise FetchError("content 형식 오류")
        rec = parse_rows(ym, content)
        rec["dir"] = direction
        return rec


def to_int(v):
    if v is None or v == "":
        return 0
    try:
        return int(str(v).replace(",", ""))
    except ValueError:
        f = float(str(v).replace(",", ""))
        if f != int(f):
            raise FetchError("정수가 아닌 여객수: %r" % v)
        return int(f)


def parse_rows(ym, content):
    rec = {"month": ym, "total": None, "domestic": None, "foreign": None, "airlines": []}
    section = None
    seen = set()
    for item in content:
        raw = (item.get("airline_name") or "").strip()
        m = ROW_RE.match(raw)
        if not m:
            raise FetchError("행 이름 파싱 실패: %r" % raw)
        name, row_ym = m.group(1).strip(), "%s-%s" % (m.group(2), m.group(3))
        if row_ym != ym:
            raise FetchError("요청 월(%s)과 다른 행: %r" % (ym, raw))
        val = to_int(item.get("pass"))
        if name == "전체 합계" or "실적" in name:
            rec["total"] = val
            continue
        if name == "국적사 계":
            rec["domestic"], section = val, "domestic"
            continue
        if name == "외항사 계":
            rec["foreign"], section = val, "foreign"
            continue
        am = AIRLINE_RE.match(name)
        if not am:
            raise FetchError("항공사명(코드) 파싱 실패: %r" % raw)
        if section is None:
            raise FetchError("국적사/외항사 구간 이전의 항공사 행: %r" % raw)
        code = am.group(2)
        if code in seen:
            raise FetchError("중복 코드: %s" % code)
        seen.add(code)
        rec["airlines"].append({"code": code, "name": am.group(1).strip(), "category": section, "value": val})
    if content and (rec["total"] is None or rec["domestic"] is None or rec["foreign"] is None):
        raise FetchError("합계 행(전체 합계/국적사 계/외항사 계) 누락")
    return rec


def reconcile(rec):
    """API 자체 합계 정합성. 문제 목록 반환(빈 목록이면 OK)."""
    probs = []
    if not rec["airlines"] or not rec["total"]:
        return ["데이터 없음"]
    s = sum(a["value"] for a in rec["airlines"])
    sd = sum(a["value"] for a in rec["airlines"] if a["category"] == "domestic")
    sf = sum(a["value"] for a in rec["airlines"] if a["category"] == "foreign")
    if rec["domestic"] + rec["foreign"] != rec["total"]:
        probs.append("국적사계+외항사계(%d) != 전체합계(%d)" % (rec["domestic"] + rec["foreign"], rec["total"]))
    if s != rec["total"]:
        probs.append("항공사 합(%d) != 전체합계(%d)" % (s, rec["total"]))
    if sd != rec["domestic"]:
        probs.append("국적사 합(%d) != 국적사계(%d)" % (sd, rec["domestic"]))
    if sf != rec["foreign"]:
        probs.append("외항사 합(%d) != 외항사계(%d)" % (sf, rec["foreign"]))
    return probs


# --------------------------------------------------------------------------
# 데이터 반영 / 검증
# --------------------------------------------------------------------------
def extend_month(data, ym):
    """새 달을 months 끝에 추가하고 모든 방향의 배열을 0으로 늘린다."""
    months = data["months"]
    if months and ym != month_add(months[-1], 1):
        raise Refused("연속되지 않는 월 append: %s (마지막 %s)" % (ym, months[-1]))
    months.append(ym)
    for key in DIRS:
        b = data[key]
        for k in ("total", "domestic", "foreign"):
            b["summary"][k].append(0)
        for a in b["airlines"]:
            a["values"].append(0)
    return len(months) - 1


def apply_rec(block, rec, idx, n, allow_rename):
    """한 방향 블록의 idx 번째 달에 rec 반영. 반환: (new_airlines, renamed, category_conflicts)"""
    by_code = {a["code"]: a for a in block["airlines"]}
    api = {a["code"]: a for a in rec["airlines"]}
    new_airlines, renamed, conflicts = [], [], []

    for a in block["airlines"]:
        r = api.get(a["code"])
        a["values"][idx] = r["value"] if r else 0  # 표에 없으면 0
        if r:
            if r["category"] != a["category"] and r["value"] > 0:
                conflicts.append({"code": a["code"], "json": a["category"], "airportal": r["category"]})
            if allow_rename and r["name"] != a["name"]:
                renamed.append({"code": a["code"], "from": a["name"], "to": r["name"]})
                a["name"] = r["name"]
                a["full"] = "%s(%s)" % (r["name"], a["code"])
    for r in rec["airlines"]:
        if r["code"] in by_code or r["value"] <= 0:
            continue
        vals = [0] * n
        vals[idx] = r["value"]
        block["airlines"].append({"full": "%s(%s)" % (r["name"], r["code"]), "name": r["name"],
                                  "code": r["code"], "category": r["category"], "values": vals, "total": 0})
        new_airlines.append({"code": r["code"], "name": r["name"], "category": r["category"], "value": r["value"]})

    for a in block["airlines"]:
        a["total"] = sum(a["values"])
    for k in ("total", "domestic", "foreign"):
        block["summary"][k][idx] = rec[k]

    A = block["airlines"]
    chk = (sum(a["values"][idx] for a in A),
           sum(a["values"][idx] for a in A if a["category"] == "domestic"),
           sum(a["values"][idx] for a in A if a["category"] == "foreign"))
    if not conflicts and chk != (rec["total"], rec["domestic"], rec["foreign"]):
        raise Refused("%s %s 반영 후 합계 불일치: %s vs %s" % (
            rec["month"], rec.get("dir"), chk, (rec["total"], rec["domestic"], rec["foreign"])))
    return new_airlines, renamed, conflicts


def validate(data):
    """전체 구조 + 모든 방향·모든 달의 합계 정합성 (0 오차). 문제 목록 반환."""
    probs = []
    months = data["months"]
    n = len(months)
    if months != sorted(months) or len(set(months)) != n:
        probs.append("months 정렬/중복 오류")
    for i in range(1, n):
        if months[i] != month_add(months[i - 1], 1):
            probs.append("months 불연속: %s → %s" % (months[i - 1], months[i]))
    for key in DIRS:
        if key not in data:
            probs.append("%s 블록 없음" % key)
            continue
        S, A = data[key]["summary"], data[key]["airlines"]
        bp = []
        for k in ("total", "domestic", "foreign"):
            if len(S[k]) != n:
                bp.append("%s.summary.%s 길이 %d != %d" % (key, k, len(S[k]), n))
        codes = [a["code"] for a in A]
        if len(codes) != len(set(codes)):
            bp.append("%s airlines 코드 중복" % key)
        for a in A:
            if len(a["values"]) != n:
                bp.append("%s %s values 길이 오류" % (key, a["code"]))
            elif a["total"] != sum(a["values"]):
                bp.append("%s %s total 오류" % (key, a["code"]))
            if a["category"] not in ("domestic", "foreign"):
                bp.append("%s %s category 오류" % (key, a["code"]))
        if bp:
            probs += bp
            continue
        for i, ym in enumerate(months):
            s = sum(a["values"][i] for a in A)
            sd = sum(a["values"][i] for a in A if a["category"] == "domestic")
            sf = sum(a["values"][i] for a in A if a["category"] == "foreign")
            if s != S["total"][i]:
                probs.append("%s %s 항공사 합 %d != 전체 합계 %d" % (key, ym, s, S["total"][i]))
            if sd != S["domestic"][i]:
                probs.append("%s %s 국적사 합 %d != 국적사 계 %d" % (key, ym, sd, S["domestic"][i]))
            if sf != S["foreign"][i]:
                probs.append("%s %s 외항사 합 %d != 외항사 계 %d" % (key, ym, sf, S["foreign"][i]))
            if sd + sf != s:
                probs.append("%s %s 국적사+외항사 != 전체" % (key, ym))
    return probs


def month_summary(data, key, idx):
    months, S, A = data["months"], data[key]["summary"], data[key]["airlines"]
    py = idx - 12 if idx >= 12 else None
    top = sorted((a for a in A if a["values"][idx] > 0), key=lambda a: -a["values"][idx])[:10]
    return {
        "total": S["total"][idx], "domestic": S["domestic"][idx], "foreign": S["foreign"][idx],
        "domestic_share_pct": round(S["domestic"][idx] / S["total"][idx] * 100, 2) if S["total"][idx] else None,
        "yoy_base_month": months[py] if py is not None else None,
        "yoy_pct": {k: (pct(S[k][idx], S[k][py]) if py is not None else None) for k in ("total", "domestic", "foreign")},
        "mom_pct": pct(S["total"][idx], S["total"][idx - 1]) if idx > 0 else None,
        "top10": [{"rank": i + 1, "code": a["code"], "name": a["name"], "category": a["category"],
                   "value": a["values"][idx],
                   "share_pct": round(a["values"][idx] / S["total"][idx] * 100, 2),
                   "yoy_pct": pct(a["values"][idx], a["values"][py]) if py is not None else None}
                  for i, a in enumerate(top)],
        "airline_count": len(A),
        "active_airlines": sum(1 for a in A if a["values"][idx] > 0),
    }


def apply_month_both(data, recs, allow_rename):
    """recs = {"dep": rec, "arr": rec} 를 data에 반영 (메모리상). 새 달이면 append."""
    ym = recs["dep"]["month"]
    if ym in data["months"]:
        idx = data["months"].index(ym)
    else:
        idx = extend_month(data, ym)
    n = len(data["months"])
    res = {"month": ym}
    for key in DIRS:
        new_airlines, renamed, conflicts = apply_rec(data[key], recs[key], idx, n, allow_rename)
        if conflicts:
            raise Refused("%s %s 국적사/외항사 구분이 바뀐 항공사: %s" % (ym, key, conflicts))
        s = month_summary(data, key, idx)
        s["new_airlines"] = new_airlines
        s["renamed_airlines"] = renamed
        res[key] = s
    return res


# --------------------------------------------------------------------------
# 명령
# --------------------------------------------------------------------------
def cmd_check(args):
    doc = load_doc()
    as_of = doc["meta"]["as_of"]
    target = month_add(as_of, 1)
    client = Client()
    res = {"command": "check", "as_of": as_of, "month": target}
    ok = True
    for key in DIRS:
        rec = client.fetch_month(target, key)
        probs = reconcile(rec)
        ok = ok and not probs
        res[key] = {"available": not probs, "total": rec["total"], "domestic": rec["domestic"],
                    "foreign": rec["foreign"], "rows": len(rec["airlines"]), "problems": probs}
    res["available"] = ok
    out(res)
    return 0 if ok else 1


def cmd_update(args):
    doc = load_doc()
    data = doc["data"]
    as_of = doc["meta"]["as_of"]
    if as_of != data["months"][-1]:
        raise Refused("meta.as_of(%s) != 마지막 month(%s)" % (as_of, data["months"][-1]))
    pre = validate(data)
    if pre:
        raise Refused("기존 JSON 검증 실패: %s" % "; ".join(pre[:10]))
    nxt = month_add(as_of, 1)
    target = args.month or nxt

    if target == nxt:
        todo = [target]
    elif not args.force:
        raise Refused("대상 월 %s 는 as_of+1(%s)이 아닙니다. --force 필요 "
                      "(이후 달이면 사이 달까지 순서대로 채우고, 이미 있는 달이면 값 교체)" % (target, nxt))
    elif target > nxt:
        todo, m = [], nxt
        while m <= target:
            todo.append(m)
            m = month_add(m, 1)
    else:
        todo = [target]  # 기존 달 교체(재수집)

    if len(todo) > 12:
        raise Refused("한 번에 12개월 넘게 채울 수 없습니다")

    # 1) 전부 수집·검증 (아직 data 변경 없음)
    client = Client()
    fetched = []
    for ym in todo:
        recs = {}
        for key in DIRS:
            rec = client.fetch_month(ym, key)
            probs = reconcile(rec)
            if probs:
                raise Refused("%s %s airportal 데이터 미완/불일치: %s" % (ym, DIR_LABEL[key], "; ".join(probs)))
            if ym not in data["months"] and not args.force:
                prev = data[key]["summary"]["total"][-1]
                if prev and rec["total"] < prev * MIN_RATIO_VS_PREV:
                    raise Refused("%s %s 전체 합계 %d 가 전월 %d 의 %.0f%% 미만 — 부분 게시 의심 (--force로 무시)"
                                  % (ym, DIR_LABEL[key], rec["total"], prev, MIN_RATIO_VS_PREV * 100))
            recs[key] = rec
        fetched.append(recs)

    # 2) 메모리상 반영 → 전체 검증 → 한 번에 기록 (all or nothing)
    results = []
    for recs in fetched:
        ym = recs["dep"]["month"]
        results.append(apply_month_both(data, recs, allow_rename=(ym >= data["months"][-1])))

    probs = validate(data)
    if probs:
        raise Refused("전체 검증 실패: %s" % "; ".join(probs[:10]))

    doc["meta"]["as_of"] = data["months"][-1]
    doc["meta"]["updated_at"] = dt.date.today().isoformat()
    save_doc(doc)
    out({"command": "update", "written": True, "path": str(DATA_PATH), "as_of": doc["meta"]["as_of"],
         "months_count": len(data["months"]),
         "airline_count": {k: len(data[k]["airlines"]) for k in DIRS},
         "results": results})
    return 0


def diff_block(data, key, idx, rec):
    block = data[key]
    diffs, notes = [], []
    for k in ("total", "domestic", "foreign"):
        if block["summary"][k][idx] != rec[k]:
            diffs.append({"dir": key, "field": "summary." + k, "json": block["summary"][k][idx], "airportal": rec[k]})
    api = {a["code"]: a for a in rec["airlines"]}
    for a in block["airlines"]:
        r = api.get(a["code"])
        v = r["value"] if r else 0
        if a["values"][idx] != v:
            diffs.append({"dir": key, "code": a["code"], "name": a["name"], "json": a["values"][idx], "airportal": v})
        if r and r["name"] != a["name"]:
            notes.append({"dir": key, "code": a["code"], "json_name": a["name"], "airportal_name": r["name"]})
        if r and r["value"] > 0 and r["category"] != a["category"]:
            diffs.append({"dir": key, "code": a["code"], "field": "category", "json": a["category"], "airportal": r["category"]})
    known = {a["code"] for a in block["airlines"]}
    for r in rec["airlines"]:
        if r["code"] not in known and r["value"] > 0:
            diffs.append({"dir": key, "code": r["code"], "name": r["name"], "json": None, "airportal": r["value"]})
    return diffs, notes


def cmd_verify(args):
    doc = load_doc()
    data = doc["data"]
    ym = args.verify_month
    if ym not in data["months"]:
        raise Refused("%s 는 JSON에 없는 달입니다" % ym)
    idx = data["months"].index(ym)
    dirs = DIRS if args.dir == "both" else (args.dir,)
    client = Client()
    res = {"command": "verify-month", "month": ym, "dirs": list(dirs)}
    all_diffs = []
    for key in dirs:
        rec = client.fetch_month(ym, key)
        diffs, notes = diff_block(data, key, idx, rec)
        all_diffs += diffs
        res[key] = {"diff_count": len(diffs), "diffs": diffs, "name_notes": notes,
                    "airportal_problems": reconcile(rec), "airportal_rows": len(rec["airlines"]),
                    "json_airlines": len(data[key]["airlines"])}
    res["diff_count"] = len(all_diffs)
    out(res)
    return 0 if not all_diffs else 1


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="airportal 국제선 출발·도착 여객(항공사별) 자동 갱신")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="as_of 다음 달 데이터(출발·도착) 게시 여부 확인")
    g.add_argument("--update", action="store_true", help="새 달 수집·검증·JSON 기록 (출발·도착 함께)")
    g.add_argument("--verify-month", type=valid_month, metavar="YYYY-MM", help="기존 달을 재수집해 JSON과 비교")
    p.add_argument("--month", type=valid_month, metavar="YYYY-MM", help="--update 대상 월 (기본: as_of+1)")
    p.add_argument("--dir", choices=("dep", "arr", "both"), default="both", help="--verify-month 방향 (기본 both)")
    p.add_argument("--force", action="store_true", help="as_of+1 이외 월 허용 / 부분 게시 의심 검사 무시")
    args = p.parse_args(argv)
    cmd = "check" if args.check else "update" if args.update else "verify-month"
    try:
        if args.check:
            return cmd_check(args)
        if args.update:
            return cmd_update(args)
        return cmd_verify(args)
    except FetchError as e:
        out({"command": cmd, "error": "fetch", "message": str(e)})
        return 2
    except Refused as e:
        out({"command": cmd, "error": "refused", "message": str(e), "written": False})
        return 1
    except Exception as e:  # 예상 못 한 오류 — 파싱 계열로 간주
        out({"command": cmd, "error": type(e).__name__, "message": str(e)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
