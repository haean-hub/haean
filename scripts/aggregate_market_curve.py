"""data/market_seat_history.csv(다수 영화 좌석판매율 히스토리)를 집계해
'요일유형별 시장 평균 주간 감쇠율'을 계산한다.

조건(사용자 지정):
  1) 2026-01-01 이후 개봉작만
  2) 영화구분 전체(장르/규모 구분 없음)
  3) 좌석판매율/좌석수/관객수 사용
  4) 개봉 1주차 평균 좌석판매율이 8% 미만이면 학습 대상에서 제외(잡음 제거)

predict.py의 benchmark_daytype_decay()와 같은 개념(이번주 같은 요일유형 실적 / 지난주
같은 요일유형 실적의 중앙값)을, 영화 1편이 아니라 조건에 맞는 전체 영화에 대해 계산한다.
결과는 data/market_daytype_decay.json 에 저장해 predict.py가 다시 계산하지 않고 읽기만 하게 한다.
"""
import csv
import datetime
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INPUT_PATH = ROOT / "data" / "market_seat_history.csv"
OUTPUT_PATH = ROOT / "data" / "market_daytype_decay.json"
LOG_PATH = ROOT / "logs" / "aggregate_market_curve.log"

MIN_FIRST_WEEK_SEAT_SELL_PCT = 8.0
RELEASE_CUTOFF = datetime.date(2026, 1, 1)


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat()} {msg}\n")


def day_type(d: datetime.date) -> str:
    wd = d.weekday()
    if wd == 4:
        return "fri"
    if wd == 5:
        return "sat"
    if wd == 6:
        return "sun"
    return "weekday"


def load_by_movie() -> dict:
    by_movie = defaultdict(list)
    with open(INPUT_PATH, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if not row.get("movie_cd") or not row.get("open_dt"):
                continue
            try:
                d = datetime.datetime.strptime(row["date"], "%Y%m%d").date()
                open_dt = datetime.datetime.strptime(row["open_dt"], "%Y-%m-%d").date()
                audi_cnt = int(row["audi_cnt"]) if row["audi_cnt"] else 0
                seat_sell = float(row["seat_sell_ratio_pct"]) if row["seat_sell_ratio_pct"] else 0.0
            except ValueError:
                continue
            if open_dt < RELEASE_CUTOFF:
                continue
            offset = (d - open_dt).days
            if offset < 0:
                continue
            by_movie[row["movie_cd"]].append({
                "date": d, "offset": offset, "audi_cnt": audi_cnt, "seat_sell": seat_sell,
                "movie_nm": row["movie_nm"],
            })
    return by_movie


def build() -> dict:
    by_movie = load_by_movie()
    ratios_by_type = defaultdict(list)
    included, excluded = [], []

    for movie_cd, rows in by_movie.items():
        rows.sort(key=lambda r: r["offset"])
        by_offset = {r["offset"]: r for r in rows}
        first_week = [by_offset[o]["seat_sell"] for o in range(7) if o in by_offset]
        if not first_week:
            continue
        avg_first_week = sum(first_week) / len(first_week)
        movie_nm = rows[0]["movie_nm"]
        if avg_first_week < MIN_FIRST_WEEK_SEAT_SELL_PCT:
            excluded.append((movie_cd, movie_nm, round(avg_first_week, 1)))
            continue
        included.append((movie_cd, movie_nm, round(avg_first_week, 1)))

        for offset, r in by_offset.items():
            prev = by_offset.get(offset - 7)
            if not prev or not prev["audi_cnt"]:
                continue
            ratio = r["audi_cnt"] / prev["audi_cnt"]
            if ratio <= 0 or ratio > 5:  # 명백한 이상치 제외
                continue
            ratios_by_type[day_type(r["date"])].append(ratio)

    decay = {}
    sample_counts = {}
    for dtype, ratios in ratios_by_type.items():
        ratios.sort()
        decay[dtype] = round(ratios[len(ratios) // 2], 4)  # 중앙값
        sample_counts[dtype] = len(ratios)

    result = {
        "generated_at": datetime.datetime.now().isoformat(),
        "condition": {
            "release_since": RELEASE_CUTOFF.isoformat(),
            "min_first_week_seat_sell_pct": MIN_FIRST_WEEK_SEAT_SELL_PCT,
        },
        "movies_included": len(included),
        "movies_excluded": len(excluded),
        "sample_counts_by_daytype": sample_counts,
        "daytype_weekly_decay": decay,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    log(f"included={len(included)} excluded={len(excluded)} decay={decay}")
    return result


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
