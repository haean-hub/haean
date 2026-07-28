"""실시간 예매율(Top10, 로그인 불필요) 수집 -> data/hourly.csv 에 append.

KOBIS 메인 페이지가 쓰는 공개 JSON 피드(searchMainRealTicket.do)를 그대로 사용한다.
대상 영화가 Top10 밖이면 found=0으로 기록해 수집 공백을 명시적으로 남긴다(가드2에서 사용).
"""
import csv
import datetime
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "film_config.json"
DATA_PATH = ROOT / "data" / "hourly.csv"
LOG_PATH = ROOT / "logs" / "collect_hourly.log"

REALTIME_URL = "https://www.kobis.or.kr/kobis/business/main/searchMainRealTicket.do"

FIELDS = [
    "collected_at", "movie_cd", "movie_nm", "found", "rank",
    "reservation_rate", "reservation_audi", "reservation_sales",
    "cum_audi", "cum_sales",
    "reservation_audi_delta", "minutes_since_prev",
]


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def fetch_realtime() -> list:
    req = urllib.request.Request(
        REALTIME_URL,
        method="POST",
        data=b"",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.kobis.or.kr/kobis/business/main/main.do",
        },
    )
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    raise last_err


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat()} {msg}\n")


def get_last_row(movie_cd: str) -> dict | None:
    """movie_cd의 가장 최근 found=1 행(직전 수집분)을 반환. 증감 계산용."""
    if not DATA_PATH.exists():
        return None
    last = None
    with open(DATA_PATH, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("movie_cd") == movie_cd and row.get("found") == "1":
                last = row
    return last


def append_row(row: dict) -> None:
    is_new = not DATA_PATH.exists()
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_PATH, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def main() -> None:
    config = load_config()
    movie_cd = config["target_movie"]["movie_cd"]
    now = datetime.datetime.now().replace(microsecond=0).isoformat()

    try:
        items = fetch_realtime()
    except Exception as e:
        log(f"ERROR fetch failed: {e}")
        return

    match = next((m for m in items if m.get("movieCd") == movie_cd), None)
    last_row = get_last_row(movie_cd)
    delta = ""
    minutes_since_prev = ""
    if match and last_row and last_row.get("reservation_audi"):
        prev_audi = int(last_row["reservation_audi"])
        cur_audi = int(match.get("audiCnt") or 0)
        delta = cur_audi - prev_audi
        prev_ts = datetime.datetime.fromisoformat(last_row["collected_at"])
        cur_ts = datetime.datetime.fromisoformat(now)
        minutes_since_prev = round((cur_ts - prev_ts).total_seconds() / 60)

    if match:
        row = {
            "collected_at": now,
            "movie_cd": movie_cd,
            "movie_nm": match.get("movieNm"),
            "found": 1,
            "rank": match.get("rank"),
            "reservation_rate": match.get("totIssuCntRatio"),
            "reservation_audi": match.get("audiCnt"),
            "reservation_sales": match.get("salesAmt"),
            "cum_audi": match.get("totalAudiCnt"),
            "cum_sales": match.get("totalSalesAmt"),
            "reservation_audi_delta": delta,
            "minutes_since_prev": minutes_since_prev,
        }
        log(f"OK rank={match.get('rank')} rate={match.get('totIssuCntRatio')} delta={delta}")
    else:
        row = {
            "collected_at": now,
            "movie_cd": movie_cd,
            "movie_nm": config["target_movie"]["title"],
            "found": 0,
            "rank": "",
            "reservation_rate": "",
            "reservation_audi": "",
            "reservation_sales": "",
            "cum_audi": "",
            "cum_sales": "",
            "reservation_audi_delta": "",
            "minutes_since_prev": "",
        }
        log("WARN movie not in realtime top10")

    append_row(row)


if __name__ == "__main__":
    main()
