"""벤치마크/대상 영화의 과거 일별 박스오피스를 한 번에 채워넣는 유틸리티.

predict.py의 '누적/최종 비율 곡선' 보정, 요일유형별 비교 검증용으로
개봉일부터 지정 기간까지의 일별 데이터를 data/benchmark_history.csv 에 쌓는다.
평소 운영 중에는 쓰지 않고, 새 벤치마크 영화를 등록할 때 1회성으로 실행한다.
"""
import argparse
import csv
import datetime
import time
from pathlib import Path

from kobis_client import get_daily_boxoffice

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "benchmark_history.csv"
LOG_PATH = ROOT / "logs" / "backfill_history.log"

FIELDS = [
    "movie_cd", "movie_nm", "date", "day_offset", "found",
    "rank", "daily_audi", "daily_sales", "cum_audi", "cum_sales",
    "screen_cnt", "show_cnt",
]


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat()} {msg}\n")


def already_have(movie_cd: str, date_str: str) -> bool:
    if not DATA_PATH.exists():
        return False
    with open(DATA_PATH, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row["movie_cd"] == movie_cd and row["date"] == date_str:
                return True
    return False


def append_row(row: dict) -> None:
    is_new = not DATA_PATH.exists()
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_PATH, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def backfill(movie_cd: str, release_date: str, days: int, sleep_sec: float = 0.2) -> None:
    start = datetime.datetime.strptime(release_date, "%Y-%m-%d").date()
    for offset in range(days):
        d = start + datetime.timedelta(days=offset)
        if d > datetime.date.today():
            break
        date_str = d.strftime("%Y%m%d")
        if already_have(movie_cd, date_str):
            continue
        rows = get_daily_boxoffice(date_str, movie_cd)
        if rows:
            r = rows[0]
            row = {
                "movie_cd": movie_cd, "movie_nm": r.get("movieNm"), "date": date_str,
                "day_offset": offset, "found": 1, "rank": r.get("rank"),
                "daily_audi": r.get("audiCnt"), "daily_sales": r.get("salesAmt"),
                "cum_audi": r.get("audiAcc"), "cum_sales": r.get("salesAcc"),
                "screen_cnt": r.get("scrnCnt"), "show_cnt": r.get("showCnt"),
            }
            log(f"OK {movie_cd} {date_str} rank={r.get('rank')} audi={r.get('audiCnt')}")
        else:
            row = {
                "movie_cd": movie_cd, "movie_nm": "", "date": date_str,
                "day_offset": offset, "found": 0, "rank": "", "daily_audi": "",
                "daily_sales": "", "cum_audi": "", "cum_sales": "", "screen_cnt": "", "show_cnt": "",
            }
            log(f"MISS {movie_cd} {date_str} (top10 밖)")
        append_row(row)
        time.sleep(sleep_sec)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--movie-cd", required=True)
    parser.add_argument("--release-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=70)
    args = parser.parse_args()
    backfill(args.movie_cd, args.release_date, args.days)
