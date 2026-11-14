"""한국관광공사 데이터랩 「국민 해외관광객 주요 목적지별 통계」 → data/korea-outbound.json

사용:
  python kto_outbound.py --check                 # 새 기준월 게시 여부 (exit 0 새 자료 / 1 없음 / 2 오류)
  python kto_outbound.py --update [--xlsx F]     # 최신 xlsx 받아 JSON 갱신 (F를 주면 그 파일 사용)
  python kto_outbound.py --verify [--xlsx F]     # xlsx와 JSON 대조만 (쓰지 않음, 차이 0이면 exit 0)
  공통 옵션: --raw-dir DIR  받은 xlsx 보관 폴더 (기본: 저장소 밖 ../../data/kto-outbound)

stdout 마지막 줄은 항상 JSON 요약 한 줄.
"""
import datetime
import html
import json
import os
import re
import sys
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
JSON_PATH = os.path.join(SITE, "data", "korea-outbound.json")
DEFAULT_RAW = os.path.normpath(os.path.join(SITE, "..", "data", "kto-outbound"))
BASE = "https://datalab.visitkorea.or.kr"
LIST_URL = BASE + "/site/portal/ex/bbs/List.do?cbIdx=1127"
TITLE_RE = re.compile(r"(\d{4})년\s*(\d{1,2})월[^<]{0,30}?해외관광객\s*주요\s*목적지별\s*통계")
UA = {"User-Agent": "Mozilla/5.0", "Referer": LIST_URL}
SHEETS = {"Asia": "아시아", "Europe": "유럽", "Africa": "아프리카", "America": "아메리카", "Oceania": "오세아니아"}


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest_post():
    page = fetch(LIST_URL).decode("utf-8", errors="ignore")
    best = None
    for chunk in page.split("<li")[1:]:
        if "cbIdx=1127" not in chunk:
            continue
        text = re.sub(r"<[^>]+>", " ", chunk)
        m = TITLE_RE.search(text)
        dl = re.search(r'href="\s*(/common/board/Download\.do\?[^"]*?\.xlsx)"', chunk)
        if not (m and dl):
            continue
        date = re.search(r"\d{4}-\d{2}-\d{2}", text)
        view = re.search(r"View\.do\?cbIdx=1127&bcIdx=(\d+)", chunk)
        post = {
            "month": f"{int(m.group(1)):04d}-{int(m.group(2)):02d}",
            "title": re.sub(r"\s+", " ", m.group(0)).strip(),
            "posted": date.group(0) if date else None,
            "xlsx_url": BASE + html.unescape(dl.group(1)),
            "view_url": f"{BASE}/site/portal/ex/bbs/View.do?cbIdx=1127&bcIdx={view.group(1)}" if view else None,
        }
        if best is None or post["month"] > best["month"]:
            best = post
    if not best:
        raise RuntimeError("목록에서 '주요 목적지별 통계' 게시물을 찾지 못함")
    return best


def download(post, raw_dir):
    os.makedirs(raw_dir, exist_ok=True)
    path = os.path.join(raw_dir, f"kto-outbound-{post['month'].replace('-', '')}.xlsx")
    data = fetch(post["xlsx_url"], timeout=180)
    if not data.startswith(b"PK"):
        raise RuntimeError("받은 파일이 xlsx가 아님")
    with open(path, "wb") as f:
        f.write(data)
    return path


def num(v):
    """xlsx 셀 → 정수 또는 None (빈칸·공백·문자열은 None). 추정치 소수·0.01 표기는 반올림."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return int(round(v))
    return None


def parse_xlsx(path):
    """returns {"total": {ym: n}, "countries": {kr: {"g","e","m":{ym:n}, "y":{year:n}}}, "title", "published"}"""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    title = published = None
    for r in wb.worksheets[0].iter_rows(values_only=True):
        for v in r:
            if isinstance(v, str) and "목적지별 통계" in v and not title:
                title = v.strip()
            if isinstance(v, str) and "발표" in v or (isinstance(v, str) and re.search(r"\d{4}\.\s*\d{1,2}\.\s*\d{1,2}", v or "")):
                published = published or v.strip()
    total, countries = {}, {}
    for sheet, cont in SHEETS.items():
        rows = list(wb[sheet].iter_rows(values_only=True))
        head = rows[1]
        cols = []  # (col index, kr, en)
        for i in range(4, len(head), 2):
            kr = head[i]
            if isinstance(kr, str) and kr.strip():
                en = head[i + 1] if i + 1 < len(head) else None
                cols.append((i, kr.strip(), en.strip() if isinstance(en, str) and en.strip() else None))
        year = None
        for r in rows[3:]:
            a, b = r[0], r[1]
            if isinstance(a, str) and re.match(r"^\d{4}년$", a.strip()):
                year = int(a.strip()[:4])
            if isinstance(b, str) and b.strip() == "누계":
                for i, kr, en in cols:
                    countries.setdefault(kr, {"g": cont, "e": en, "m": {}, "y": {}})["y"][str(year)] = num(r[i])
                continue
            mm = re.match(r"^(\d{1,2})월$", b.strip()) if isinstance(b, str) else None
            if not (mm and year):
                continue
            ym = f"{year:04d}-{int(mm.group(1)):02d}"
            t = num(r[2])
            if t is not None:
                total[ym] = t
            for i, kr, en in cols:
                c = countries.setdefault(kr, {"g": cont, "e": en, "m": {}, "y": {}})
                c["m"][ym] = num(r[i]) if i < len(r) else None
    return {"total": total, "countries": countries, "title": title, "published": published}


def month_range(start, end):
    y, m = map(int, start.split("-"))
    out = []
    while f"{y:04d}-{m:02d}" <= end:
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def build(doc, x, last):
    """현재 JSON(doc) 구조를 유지하며 xlsx(x) 값으로 months~last 전체를 다시 채운다."""
    d = doc["data"]
    months = month_range(d["start"], last)
    nd = dict(d)
    nd["months"], nd["n"] = months, len(months)
    nd["total"] = [x["total"].get(ym) for ym in months]
    nc = {}
    for kr, c in d["c"].items():
        src = x["countries"].get(kr)
        if src is None:
            raise RuntimeError(f"xlsx에 대시보드 국가 '{kr}' 열이 없음 (이름 변경?)")
        cc = dict(c)
        cc["v"] = [src["m"].get(ym) for ym in months]
        # y(예전 버전의 1~5월 누계)·j 는 페이지에서 쓰지 않는 레거시 필드 — 그대로 둔다
        nc[kr] = cc
    nd["c"] = nc
    meta = dict(d["meta"])
    meta["last"] = last
    yy, mm = last.split("-")
    meta["src"] = re.sub(r"\(\d{4}년 \d{1,2}월\)", f"({yy}년 {int(mm)}월)", meta["src"])
    # meta.tot: 연도별 1~5월·1~6월 전체 출국자 누계 (기존 필드 유지·재계산)
    tot = {}
    for yr in meta.get("tot", {}):
        vals = [x["total"].get(f"{yr}-{k:02d}") for k in range(1, 7)]
        tot[yr] = {"m5": sum(vals[:5]) if None not in vals[:5] else None,
                   "m6": sum(vals) if None not in vals else None}
    meta["tot"] = tot
    nd["meta"] = meta
    return nd


def diff(old, new):
    out = []
    om, nm = old["months"], new["months"]
    for i, ym in enumerate(nm):
        if i < len(om) and om[i] == ym:
            if old["total"][i] != new["total"][i]:
                out.append(("전체", ym, old["total"][i], new["total"][i]))
            for kr in new["c"]:
                a, b = old["c"][kr]["v"][i], new["c"][kr]["v"][i]
                if a != b:
                    out.append((kr, ym, a, b))
    return out


def yoy(cur, prev):
    return round(cur / prev - 1, 4) if cur and prev else None


def summary(d):
    m = d["months"]
    i = len(m) - 1
    j = i - 12
    ytd_i = [k for k in range(len(m)) if m[k][:4] == m[i][:4]]
    ytd_j = [k - 12 for k in ytd_i]
    s = lambda arr, idx: sum(arr[k] for k in idx) if all(arr[k] is not None for k in idx) else None
    top = sorted(((kr, c["v"][i]) for kr, c in d["c"].items() if c["v"][i] is not None), key=lambda t: -t[1])[:5]
    return {
        "month": m[i],
        "total": d["total"][i], "total_yoy": yoy(d["total"][i], d["total"][j]),
        "ytd": s(d["total"], ytd_i), "ytd_yoy": yoy(s(d["total"], ytd_i), s(d["total"], ytd_j)),
        "reported_countries": sum(1 for c in d["c"].values() if c["v"][i] is not None),
        "top5": [{"country": kr, "visitors": v, "yoy": yoy(v, d["c"][kr]["v"][j])} for kr, v in top],
    }


def main():
    out = {"mode": None}
    try:
        doc = json.load(open(JSON_PATH, encoding="utf-8"))
        as_of = doc["meta"]["as_of"]
        out["as_of"] = as_of
        if "--check" in sys.argv:
            out["mode"] = "check"
            post = latest_post()
            out["latest"] = post
            out["new"] = post["month"] > as_of
            print(json.dumps(out, ensure_ascii=False))
            sys.exit(0 if out["new"] else 1)
        mode = "update" if "--update" in sys.argv else "verify" if "--verify" in sys.argv else None
        if not mode:
            print(__doc__)
            sys.exit(2)
        out["mode"] = mode
        xlsx = arg("--xlsx")
        if not xlsx:
            post = latest_post()
            out["latest"] = post
            xlsx = download(post, arg("--raw-dir", DEFAULT_RAW))
        out["xlsx"] = xlsx
        x = parse_xlsx(xlsx)
        last = max(ym for ym, v in x["total"].items() if v is not None)
        out["xlsx_last"] = last
        new = build(doc, x, max(last, as_of))
        changes = diff(doc["data"], new)
        out["revised_cells"] = len(changes)
        out["revised_sample"] = changes[:30]
        new_countries = sorted(k for k in x["countries"] if k not in doc["data"]["c"])
        out["xlsx_countries_not_in_dashboard"] = len(new_countries)
        if mode == "verify":
            out["ok"] = not changes and last == as_of
            print(json.dumps(out, ensure_ascii=False))
            sys.exit(0 if out["ok"] else 1)
        # update
        if last < as_of:
            raise RuntimeError(f"xlsx 기준월 {last} 이 현재 {as_of} 보다 이전")
        # 검증: 길이 일치
        n = new["n"]
        assert len(new["months"]) == n == len(new["total"]), "months/total 길이 불일치"
        for kr, c in new["c"].items():
            assert len(c["v"]) == n, f"{kr} v 길이 불일치"
        assert new["total"][-1] is not None, "최신월 전체 합계 없음"
        doc["data"] = new
        doc["meta"]["as_of"] = new["meta"]["last"]
        doc["meta"]["updated_at"] = datetime.date.today().isoformat()
        if out.get("latest"):
            doc["meta"]["source_post"] = out["latest"]["view_url"]
            doc["meta"]["source_posted"] = out["latest"]["posted"]
        tmp = JSON_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, JSON_PATH)
        out["changed"] = new["meta"]["last"] != as_of or bool(changes)
        out["summary"] = summary(new)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
        print(json.dumps(out, ensure_ascii=False))
        sys.exit(2)
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
