"""일별 관객(전일 확정치, Top10, 로그인 불필요) 수집 -> data/member_snapshots.csv 에 append.

KOBIS Open API의 공식 일별 박스오피스(searchDailyBoxOfficeList)를 사용한다.
대상 영화가 Top10 밖이면 found=0으로 기록한다.
순위 무관 정밀 데이터가 필요해지면(Top10 이탈 시) 브라우저 자동화 기반 수집을
Phase 2로 추가하는 것을 README에 남겨둔다.
"""
import argparse
import csv
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from kobis_client import get_daily_boxoffice  # noqa: E402

CONFIG_PATH = ROOT.parent / "config" / "film_config.json"
DATA_PATH = ROOT.parent / "data" / "member_snapshots.csv"
LOG_PATH = ROOT.parent / "logs" / "collect_daily.log"

FIELDS = [
    "collected_at", "target_date", "movie_cd", "movie_nm", "found", "rank",
    "daily_audi", "daily_sales", "cum_audi", "cum_sales", "screen_cnt", "show_cnt",
]


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat()} {msg}\n")


def append_row(row: dict) -> None:
    is_new = not DATA_PATH.exists()
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_PATH, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def main(target_dt: str | None = None) -> None:
    config = load_config()
    movie_cd = config["target_movie"]["movie_cd"]
    if target_dt is None:
        target_dt = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y%m%d")
    now = datetime.datetime.now().replace(microsecond=0).isoformat()

    try:
        rows = get_daily_boxoffice(target_dt, movie_cd)
    except Exception as e:
        log(f"ERROR fetch failed ({target_dt}): {e}")
        return

    if rows:
        r = rows[0]
        row = {
            "collected_at": now,
            "target_date": target_dt,
            "movie_cd": movie_cd,
            "movie_nm": r.get("movieNm"),
            "found": 1,
            "rank": r.get("rank"),
            "daily_audi": r.get("audiCnt"),
            "daily_sales": r.get("salesAmt"),
            "cum_audi": r.get("audiAcc"),
            "cum_sales": r.get("salesAcc"),
            "screen_cnt": r.get("scrnCnt"),
            "show_cnt": r.get("showCnt"),
        }
        log(f"OK {target_dt} rank={r.get('rank')} audi={r.get('audiCnt')}")
    else:
        row = {
            "collected_at": now,
            "target_date": target_dt,
            "movie_cd": movie_cd,
            "movie_nm": config["target_movie"]["title"],
            "found": 0,
            "rank": "",
            "daily_audi": "",
            "daily_sales": "",
            "cum_audi": "",
            "cum_sales": "",
            "screen_cnt": "",
            "show_cnt": "",
        }
        log(f"WARN {target_dt} not in daily top10 (Open API limitation)")

    append_row(row)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYYMMDD, default=어제")
    args = parser.parse_args()
    main(args.date)
