# market-data

한국 아웃바운드·항공 시장 공개 통계 대시보드 (GitHub Pages).
페이지: https://elf03190123-glitch.github.io/market-data/

| 대시보드 | 페이지 | 데이터 | 갱신 스크립트 | 원자료 |
|---|---|---|---|---|
| 한국인 아웃바운드 목적지 | `outbound/` | `data/korea-outbound.json` | `scripts/kto_outbound.py` | 한국관광공사 데이터랩 「국민 해외관광객 주요 목적지별 통계」 |
| 국제선 출발 여객 (항공사별) | `airline/` | `data/airline-departures.json` | `scripts/airportal_airline.py` | 항공정보포털 항공통계 |
| 주요 공항별 노선 분석 | `routes/` | `data/airport-routes.json` | `scripts/airportal_routes.py` | 항공정보포털 항공통계 |
| 출발편 항공 통계 (국가·도시별) | `region/` | `data/region-country.json` | `scripts/airportal_region.py` | 항공정보포털 항공통계 |

## 구조
- 페이지는 데이터 JSON을 `fetch`로 읽어 그린다. 매달 바뀌는 것은 `data/*.json`뿐이다.
- 모든 JSON은 `{"meta": {id, title, source, source_url, as_of, updated_at, schema_version, ...}, "data": {...}}` 형태다.
- 갱신 스크립트 공통 옵션: `--check`(새 달 공개 여부, exit 0 = 있음 / 1 = 없음 / 2 = 오류), `--update`(받아서 JSON 갱신·검증), 검증용 `--verify…`.
- 월간 실행은 로컬 PC의 Orca 자동화가 스크립트를 돌린 뒤 커밋·푸시한다. 푸시되면 GitHub Pages가 1~2분 안에 다시 배포한다.
