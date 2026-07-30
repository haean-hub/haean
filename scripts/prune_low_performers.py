"""market_seat_history.csv에서 '개봉 1주일(offset 0~6)이 이미 지났는데 평균 좌석판매율이
8% 미만인 영화'를 원본 데이터에서 자동으로 제거한다.

- 개봉 후 1주일이 아직 안 지난 영화(=offset 6까지 데이터가 없는 영화)는 판단 보류하고 그대로 둔다.
- 이 스크립트는 scrape_seat_history.py 실행 끝에서 자동으로 호출된다(수집할 때마다 자동 정리).
"""
import csv
import datetime
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "market_seat_history.csv"
LOG_PATH = ROOT / "logs" / "prune_low_performers.log"

MIN_FIRST_WEEK_SEAT_SELL_PCT = 9.0


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat()} {msg}\n")


def prune() -> dict:
    if not DATA_PATH.exists():
        return {"removed_movies": 0, "removed_rows": 0, "kept_rows": 0}

    with open(DATA_PATH, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
        fieldnames = list(rows[0].keys()) if rows else []

    by_movie = defaultdict(list)
    for r in rows:
        by_movie[r["movie_cd"]].append(r)

    to_remove = set()
    for movie_cd, movie_rows in by_movie.items():
        offsets = set()
        first_week_vals = []
        for r in movie_rows:
            try:
                d = datetime.datetime.strptime(r["date"], "%Y%m%d").date()
                open_dt = datetime.datetime.strptime(r["open_dt"], "%Y-%m-%d").date()
            except ValueError:
                continue
            offset = (d - open_dt).days
            offsets.add(offset)
            if 0 <= offset <= 6 and r.get("seat_sell_ratio_pct"):
                first_week_vals.append(float(r["seat_sell_ratio_pct"]))

        week_elapsed = max(offsets, default=-1) >= 6
        if not week_elapsed:
            continue  # 아직 1주일 안 지남 -> 판단 보류

        if not first_week_vals:
            continue  # 1주일은 지났지만 첫주 데이터 자체가 없음 -> 판단 불가, 보류

        avg = sum(first_week_vals) / len(first_week_vals)
        if avg < MIN_FIRST_WEEK_SEAT_SELL_PCT:
            to_remove.add(movie_cd)

    kept_rows = [r for r in rows if r["movie_cd"] not in to_remove]
    removed_rows = len(rows) - len(kept_rows)

    if removed_rows:
        with open(DATA_PATH, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(kept_rows)

    result = {"removed_movies": len(to_remove), "removed_rows": removed_rows, "kept_rows": len(kept_rows)}
    log(f"removed_movies={result['removed_movies']} removed_rows={result['removed_rows']} kept_rows={result['kept_rows']}")
    return result


if __name__ == "__main__":
    print(prune())
