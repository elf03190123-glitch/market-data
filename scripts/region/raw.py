# 원자료 파일(data/region-country-raw.json) 읽기·쓰기
# 형식: {"schema_version":1,"as_of":"YYYY-MM","variants":{"dep_mix":{"A":[CSV 줄...],"C":[...],"D":[...]}, dep_paid/arr_mix/arr_paid}}
#   A = 지역/국가 (ym,area,nation,flight,pass,cargo)  C = 도시 (ym,city,flight,pass,cargo)  D = 국내선 (ym,route,flight,pass,cargo)
#   각 목록의 첫 줄은 헤더. 아티팩트 시절 gzip+base64 안에 있던 CSV 텍스트를 줄 단위 배열로 풀어 둔 것(매달 줄만 늘어남 → git diff가 작음).
import csv, io, json, os

VARS = ['dep_mix', 'dep_paid', 'arr_mix', 'arr_paid']
AREAS = ['일본', '중국', '아시아', '미주', '유럽', '대양주', '기타']
TABLES = ['A', 'C', 'D']


def parse_lines(lines):
    rows = list(csv.reader(lines))
    return (rows[0], [r for r in rows[1:] if r])


def csv_line(row):
    s = io.StringIO()
    csv.writer(s, lineterminator='\n').writerow(row)
    return s.getvalue().rstrip('\n')


def read_raw_file(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def parse_raw(obj):
    """build.py가 쓰는 구조 {variant:{'A':(header,rows),...}}"""
    return {v: {k: parse_lines(obj['variants'][v][k]) for k in TABLES} for v in VARS}


def load_raw(path):
    return parse_raw(read_raw_file(path))


def write_raw_file(path, obj):
    # 배열 원소마다 한 줄(indent=0) — 월 추가 시 diff가 추가된 줄로만 보이게
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, ensure_ascii=False, indent=0)
        f.write('\n')
    os.replace(tmp, path)
