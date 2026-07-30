"""경쟁작(competitor_movies) 실시간 예매율 + 누적관객수 수집 -> data/competitors.csv 에 append.

config/film_config.json 의 competitor_movies 리스트([{title, movie_cd}, ...])를 대상으로 한다.
대상 영화(collect_hourly.py)와 같은 공개 실시간 피드(searchMainRealTicket.do)를 재사용하며,
한 번의 호출로 여러 경쟁작을 동시에 조회한다. 목록이 비어 있으면 아무 것도 하지 않는다.
"""
import csv
import datetime
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "film_config.json"
DATA_PATH = ROOT / "data" / "competitors.csv"
LOG_PATH = ROOT / "logs" / "collect_competitors.log"

REALTIME_URL = "https://www.kobis.or.kr/kobis/business/main/searchMainRealTicket.do"

FIELDS = ["collected_at", "movie_cd", "movie_nm", "found", "reservation_rate", "cum_audi"]


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


def append_rows(rows: list) -> None:
    is_new = not DATA_PATH.exists()
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_PATH, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    config = load_config()
    competitors = config.get("competitor_movies", [])
    if not competitors:
        log("SKIP no competitor_movies configured")
        return

    now = datetime.datetime.now().replace(microsecond=0).isoformat()
    try:
        items = fetch_realtime()
    except Exception as e:
        log(f"ERROR fetch failed: {e}")
        return

    by_cd = {m.get("movieCd"): m for m in items}
    rows = []
    for comp in competitors:
        movie_cd = comp["movie_cd"]
        match = by_cd.get(movie_cd)
        if match:
            rows.append({
                "collected_at": now,
                "movie_cd": movie_cd,
                "movie_nm": match.get("movieNm"),
                "found": 1,
                "reservation_rate": match.get("totIssuCntRatio"),
                "cum_audi": match.get("totalAudiCnt"),
            })
            log(f"OK {movie_cd} rate={match.get('totIssuCntRatio')} cum={match.get('totalAudiCnt')}")
        else:
            rows.append({
                "collected_at": now,
                "movie_cd": movie_cd,
                "movie_nm": comp.get("title", ""),
                "found": 0,
                "reservation_rate": "",
                "cum_audi": "",
            })
            log(f"WARN {movie_cd} not in realtime top10")

    append_rows(rows)


if __name__ == "__main__":
    main()
