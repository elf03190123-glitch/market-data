"""월간 통계 일괄 확인·갱신·배포 (Orca 자동화용)

  python run_monthly.py --check    # 새 달 자료가 하나라도 있으면 exit 0, 없으면 1 (precheck)
  python run_monthly.py --update   # 새 자료가 있는 것만 --update → 성공한 데이터만 커밋·푸시
  python run_monthly.py --update --no-push   # 커밋·푸시 없이 갱신만

각 데이터셋 스크립트는 같은 규약을 따른다: --check (0 새 자료 / 1 없음 / 2 오류), --update (0 성공).
stdout 마지막 줄은 JSON 요약.
"""
import json
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
DATASETS = [
    # id, script, data files (site 기준), 이름
    ("korea-outbound", "kto_outbound.py", ["data/korea-outbound.json"], "한국인 아웃바운드 목적지"),
    ("airline-passengers", "airportal_airline.py", ["data/airline-passengers.json"], "국제선 출발·도착 여객 (항공사별)"),
    ("airport-routes", "airportal_routes.py", ["data/airport-routes.json", "data/airport-routes-raw.json"], "주요 공항별 노선 분석"),
    ("region-country", "airportal_region.py", ["data/region-country.json", "data/region-country-raw.json"], "출발편 항공 통계 (국가·도시별)"),
]
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")


def run(args, timeout):
    p = subprocess.run([sys.executable] + args, cwd=SITE, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, env=ENV)
    last = (p.stdout.strip().splitlines() or [""])[-1]
    try:
        info = json.loads(last)
    except Exception:  # noqa: BLE001
        info = {"raw": last[-500:], "stderr": p.stderr[-1500:]}
    return p.returncode, info


def git(*args):
    p = subprocess.run(["git"] + list(args), cwd=SITE, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 실패: {p.stderr.strip()[-800:]}")
    return p.stdout.strip()


def check_all():
    res = []
    for did, script, files, name in DATASETS:
        path = os.path.join(HERE, script)
        if not os.path.exists(path):
            res.append({"id": did, "name": name, "status": "not-installed"})
            continue
        try:
            code, info = run([path, "--check"], timeout=240)
        except subprocess.TimeoutExpired:
            code, info = 2, {"error": "timeout"}
        status = {0: "new", 1: "up-to-date"}.get(code, "error")
        res.append({"id": did, "name": name, "status": status, "detail": info})
    return res


def main():
    checks = check_all()
    if "--check" in sys.argv:
        new = [c["id"] for c in checks if c["status"] == "new"]
        print(json.dumps({"mode": "check", "new": new, "checks": checks}, ensure_ascii=False))
        sys.exit(0 if new else 1)
    if "--update" not in sys.argv:
        print(__doc__)
        sys.exit(2)
    results, staged = [], []
    for c in checks:
        if c["status"] != "new":
            results.append(c)
            continue
        did, script, files, name = next(d for d in DATASETS if d[0] == c["id"])
        try:
            code, info = run([os.path.join(HERE, script), "--update"], timeout=1800)
        except subprocess.TimeoutExpired:
            code, info = 2, {"error": "timeout"}
        ok = code == 0 and not info.get("error")
        r = {"id": did, "name": name, "status": "updated" if ok else "update-failed", "detail": info}
        if ok:
            for f in files:
                if os.path.exists(os.path.join(SITE, f)):
                    git("add", f)
            try:
                as_of = json.load(open(os.path.join(SITE, files[0]), encoding="utf-8"))["meta"]["as_of"]
            except Exception:  # noqa: BLE001
                as_of = "?"
            r["as_of"] = as_of
            staged.append(f"{did} {as_of}")
        else:
            # 실패한 데이터셋의 부분 변경은 되돌린다
            for f in files:
                subprocess.run(["git", "checkout", "--", f], cwd=SITE, capture_output=True)
        results.append(r)
    out = {"mode": "update", "results": results, "committed": None, "pushed": False}
    if staged and git("diff", "--cached", "--name-only"):
        msg = "Update data: " + ", ".join(staged)
        git("commit", "-m", msg)
        out["committed"] = git("rev-parse", "--short", "HEAD") + " " + msg
        if "--no-push" not in sys.argv:
            git("push", "origin", "main")
            out["pushed"] = True
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(1 if any(r["status"] == "update-failed" for r in results) else 0)


if __name__ == "__main__":
    main()
