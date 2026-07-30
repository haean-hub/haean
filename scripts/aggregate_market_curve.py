"""data/market_seat_history.csv(다수 영화 좌석판매율 히스토리)를 집계해
'경과일(day_offset)별 시장 평균 좌석판매율'을 계산한다.

용도: 대상 영화의 그날 좌석수 x 해당 경과일의 시장 평균 좌석판매율 = 참고용 예상 관객수
(예: "오늘 최종 관객(2026년 평균 기준)"). 메인 예측 로직에는 섞지 않고 별도 참고 지표로만 쓴다.

조건(사용자 지정):
  1) 2026-01-01 이후 개봉작만
  2) 영화구분 전체(장르/규모 구분 없음)
  3) 좌석판매율 사용
  4) 개봉 1주차(day_offset 0~6) 평균 좌석판매율이 8% 미만이면 제외(흥행 무의미 데이터 배제)

결과는 data/market_seat_sell_curve.json 에 저장해 predict.py/build_dashboard.py가 다시
계산하지 않고 읽기만 하게 한다.
"""
import csv
import datetime
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INPUT_PATH = ROOT / "data" / "market_seat_history.csv"
OUTPUT_PATH = ROOT / "data" / "market_seat_sell_curve.json"
LOG_PATH = ROOT / "logs" / "aggregate_market_curve.log"

MIN_FIRST_WEEK_SEAT_SELL_PCT = 8.0
RELEASE_CUTOFF = datetime.date(2026, 1, 1)


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat()} {msg}\n")


def load_by_movie() -> dict:
    by_movie = defaultdict(list)
    with open(INPUT_PATH, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if not row.get("movie_cd") or not row.get("open_dt"):
                continue
            try:
                d = datetime.datetime.strptime(row["date"], "%Y%m%d").date()
                open_dt = datetime.datetime.strptime(row["open_dt"], "%Y-%m-%d").date()
                seat_sell = float(row["seat_sell_ratio_pct"]) if row["seat_sell_ratio_pct"] else 0.0
            except ValueError:
                continue
            if open_dt < RELEASE_CUTOFF:
                continue
            offset = (d - open_dt).days
            if offset < 0:
                continue
            by_movie[row["movie_cd"]].append({
                "offset": offset, "seat_sell": seat_sell, "movie_nm": row["movie_nm"],
            })
    return by_movie


def build() -> dict:
    by_movie = load_by_movie()
    seat_sell_by_offset = defaultdict(list)
    included, excluded = [], []

    for movie_cd, rows in by_movie.items():
        by_offset = {}
        for r in rows:
            by_offset.setdefault(r["offset"], []).append(r["seat_sell"])
        first_week = [v for o in range(7) if o in by_offset for v in by_offset[o]]
        if not first_week:
            continue
        avg_first_week = sum(first_week) / len(first_week)
        movie_nm = rows[0]["movie_nm"]
        if avg_first_week < MIN_FIRST_WEEK_SEAT_SELL_PCT:
            excluded.append((movie_cd, movie_nm, round(avg_first_week, 1)))
            continue
        included.append((movie_cd, movie_nm, round(avg_first_week, 1)))

        for offset, vals in by_offset.items():
            # 하루에 여러 행이 있을 리는 없지만 방어적으로 평균
            seat_sell_by_offset[offset].append(sum(vals) / len(vals))

    curve = {}
    sample_counts = {}
    for offset, vals in seat_sell_by_offset.items():
        curve[str(offset)] = round(sum(vals) / len(vals), 2)
        sample_counts[str(offset)] = len(vals)

    result = {
        "generated_at": datetime.datetime.now().isoformat(),
        "condition": {
            "release_since": RELEASE_CUTOFF.isoformat(),
            "min_first_week_seat_sell_pct": MIN_FIRST_WEEK_SEAT_SELL_PCT,
        },
        "movies_included": len(included),
        "movies_excluded": len(excluded),
        "avg_seat_sell_pct_by_offset": curve,
        "sample_counts_by_offset": sample_counts,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    log(f"included={len(included)} excluded={len(excluded)} offsets={len(curve)}")
    return result


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
