"""data/market_seat_history.csv(다수 영화 좌석판매율 히스토리)를 집계해
'경과일(day_offset) x 요일유형별' 시장 평균 좌석판매율을 계산한다.

요일유형 보정: 같은 "3일차"라도 개봉요일에 따라 토요일이 되기도, 평일이 되기도 해서
그냥 경과일로만 평균내면 요일 효과가 섞여 왜곡된다(수요일 개봉작이 많아 3·10·17일차처럼
7일 간격 지점이 하필 주말과 겹쳐 계속 높게 나오는 톱니 패턴). 그래서 (경과일, 요일유형)
조합별로 따로 평균을 낸다. 표본이 너무 적은 조합(기본 5개 미만)은 그 경과일의
요일유형 무관 평균으로 대체한다(가드5와 같은 원리).

조건(사용자 지정):
  1) 2026-01-01 이후 개봉작만
  2) 영화구분 전체(일반영화 + 독립·예술영화)
  3) 좌석판매율 사용
  4) 개봉 1주차(day_offset 0~6)가 이미 다 지난 영화만 판정 대상으로 하고,
     그 평균이 9% 미만이면 제외. 아직 1주일이 안 지난 최신 영화는 판정 보류(제외하지 않음).
     주: market_seat_history.csv는 prune_low_performers.py가 이미 확정 제외 대상을
     물리적으로 삭제해두므로, 여기서는 남아있는 데이터를 그대로 다 쓰면 된다.

결과는 data/market_seat_sell_curve.json 에 저장.
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

MIN_FIRST_WEEK_SEAT_SELL_PCT = 9.0
RELEASE_CUTOFF = datetime.date(2026, 1, 1)
MIN_SAMPLES_FOR_DAYTYPE = 5


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
                seat_sell = float(row["seat_sell_ratio_pct"]) if row["seat_sell_ratio_pct"] else 0.0
            except ValueError:
                continue
            if open_dt < RELEASE_CUTOFF:
                continue
            offset = (d - open_dt).days
            if offset < 0:
                continue
            by_movie[row["movie_cd"]].append({"offset": offset, "date": d, "seat_sell": seat_sell})
    return by_movie


def build() -> dict:
    by_movie = load_by_movie()

    # (offset, day_type) -> [values]
    by_bucket = defaultdict(list)
    # offset -> [values] (요일유형 무관, 저표본 폴백용)
    by_offset_only = defaultdict(list)

    included, excluded, pending = 0, 0, 0

    for movie_cd, rows in by_movie.items():
        by_offset = defaultdict(list)
        for r in rows:
            by_offset[r["offset"]].append(r)

        week_elapsed = max(by_offset.keys(), default=-1) >= 6
        first_week_vals = [r["seat_sell"] for o in range(7) if o in by_offset for r in by_offset[o]]

        if week_elapsed:
            if not first_week_vals:
                pending += 1
                continue
            avg_first_week = sum(first_week_vals) / len(first_week_vals)
            if avg_first_week < MIN_FIRST_WEEK_SEAT_SELL_PCT:
                excluded += 1
                continue
            included += 1
        else:
            pending += 1  # 1주일 안 지남: 제외는 안 하되, 판정 보류 상태 표시용 카운트만

        # 판정 보류(아직 1주 안 지남) 영화도 지금까지 쌓인 데이터는 그대로 집계에 반영한다.
        for offset, day_rows in by_offset.items():
            for r in day_rows:
                dtype = day_type(r["date"])
                by_bucket[(offset, dtype)].append(r["seat_sell"])
                by_offset_only[offset].append(r["seat_sell"])

    curve = {}
    sample_counts = {}
    fallback_used = {}

    all_offsets = sorted(by_offset_only.keys())
    for offset in all_offsets:
        for dtype in ("weekday", "fri", "sat", "sun"):
            key = f"{offset}:{dtype}"
            vals = by_bucket.get((offset, dtype), [])
            if len(vals) >= MIN_SAMPLES_FOR_DAYTYPE:
                curve[key] = round(sum(vals) / len(vals), 2)
                sample_counts[key] = len(vals)
                fallback_used[key] = False
            else:
                fallback_vals = by_offset_only[offset]
                curve[key] = round(sum(fallback_vals) / len(fallback_vals), 2)
                sample_counts[key] = len(fallback_vals)
                fallback_used[key] = True

    result = {
        "generated_at": datetime.datetime.now().isoformat(),
        "condition": {
            "release_since": RELEASE_CUTOFF.isoformat(),
            "min_first_week_seat_sell_pct": MIN_FIRST_WEEK_SEAT_SELL_PCT,
            "min_samples_for_daytype": MIN_SAMPLES_FOR_DAYTYPE,
        },
        "movies_included": included,
        "movies_excluded": excluded,
        "movies_pending_first_week": pending,
        "key_format": "{day_offset}:{day_type}  (day_type: weekday/fri/sat/sun)",
        "avg_seat_sell_pct_by_offset_daytype": curve,
        "sample_counts": sample_counts,
        "fallback_to_offset_only": fallback_used,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    log(f"included={included} excluded={excluded} pending={pending} buckets={len(curve)}")
    return result


if __name__ == "__main__":
    r = build()
    print(json.dumps({k: v for k, v in r.items() if k not in ("avg_seat_sell_pct_by_offset_daytype", "sample_counts", "fallback_to_offset_only")}, ensure_ascii=False, indent=2))
