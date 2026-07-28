"""오늘 최종 관객 / 개봉 최종 누적 관객(스코어) 예측.

레퍼런스 프롬프트의 5개 가드를 함수 단위로 구현한다:
  가드1 요일유형 왜곡   -> day_type() 으로 비교 대상을 같은 요일유형끼리만 묶음
  가드2 수집 공백       -> common_hours() 로 양쪽에 다 있는 시각만 비교
  가드3 편성 절벽       -> seat_capacity_cap() 으로 미래일 예측을 상영 규모 기준으로 캡
  가드4 수집지연 블립    -> BLIP_CAP_RATIO 로 급등 외삽을 제한, 예매율 0%는 무시
  가드5 저표본 붕괴      -> MIN_SAMPLES 미만이면 곡선 외삽 대신 요일유형 평균으로 대체
"""
import csv
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEMBER_SNAPSHOTS = ROOT / "data" / "member_snapshots.csv"
HOURLY = ROOT / "data" / "hourly.csv"
BENCHMARK_HISTORY = ROOT / "data" / "benchmark_history.csv"

BLIP_CAP_RATIO = 1.4
MIN_SAMPLES = 3


def day_type(d: datetime.date) -> str:
    wd = d.weekday()  # 0=Mon ... 6=Sun
    if wd == 4:
        return "fri"
    if wd == 5:
        return "sat"
    if wd == 6:
        return "sun"
    return "weekday"


def _to_int(v):
    if v in (None, ""):
        return None
    return int(v)


def load_daily_series(csv_path: Path, movie_cd: str) -> list:
    """target_date/date 오름차순, 같은 날짜는 마지막 수집분만 남긴다."""
    if not csv_path.exists():
        return []
    by_date = {}
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("movie_cd") != movie_cd:
                continue
            date_key = row.get("target_date") or row.get("date")
            if not date_key or row.get("found") != "1":
                continue
            d = datetime.datetime.strptime(date_key, "%Y%m%d").date()
            by_date[d] = {
                "date": d,
                "daily_audi": _to_int(row.get("daily_audi")),
                "cum_audi": _to_int(row.get("cum_audi")),
                "screen_cnt": _to_int(row.get("screen_cnt")),
                "show_cnt": _to_int(row.get("show_cnt")),
            }
    return [by_date[d] for d in sorted(by_date)]


def load_hourly_series(movie_cd: str, on_date: datetime.date) -> list:
    """지정일의 시간대별 실시간 예매 스냅샷(같은 날짜만)."""
    if not HOURLY.exists():
        return []
    rows = []
    with open(HOURLY, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("movie_cd") != movie_cd or row.get("found") != "1":
                continue
            ts = datetime.datetime.fromisoformat(row["collected_at"])
            if ts.date() != on_date:
                continue
            rows.append({
                "ts": ts,
                "cum_audi": _to_int(row.get("cum_audi")),
                "reservation_audi": _to_int(row.get("reservation_audi")),
                "reservation_rate": float(row["reservation_rate"]) if row.get("reservation_rate") else 0.0,
            })
    return sorted(rows, key=lambda r: r["ts"])


def find_same_day_type_before(series: list, target_date: datetime.date, dtype: str, weeks_back: int = 1):
    """target_date 이전, 같은 요일유형인 날짜 중 가장 가까운 것 (기본: 지난주 같은 요일)."""
    candidates = [r for r in series if r["date"] < target_date and day_type(r["date"]) == dtype]
    if not candidates:
        return None
    candidates.sort(key=lambda r: r["date"])
    return candidates[-weeks_back] if len(candidates) >= weeks_back else candidates[0]


def predict_today_final(daily_series: list, hourly_today: list, today: datetime.date) -> dict:
    """오늘 최종 관객 = (a) 지난주 동일요일유형 실측 대비 역산 + (b) 당일 곡선 외삽, 평균.

    가드5: 오늘 표본이 MIN_SAMPLES 미만이면 (b)를 버리고 (a)만 사용.
    가드4: (b) 외삽은 최신값의 BLIP_CAP_RATIO배를 넘지 못하게 캡. 예매율 0%면 (b) 자체를 버림.
    """
    dtype = today_type = day_type(today)
    last_week = find_same_day_type_before(daily_series, today, dtype)

    method_a = None
    if last_week and last_week["daily_audi"]:
        method_a = last_week["daily_audi"]

    method_b = None
    n_samples = len(hourly_today)
    if n_samples >= MIN_SAMPLES:
        latest = hourly_today[-1]
        if latest["reservation_rate"] > 0:
            # 지금까지의 예매 흐름을 오늘 하루치로 단순 외삽(선형), 블립 캡 적용
            naive_extrap = latest["cum_audi"] or 0
            cap = naive_extrap * BLIP_CAP_RATIO
            method_b = min(naive_extrap, cap) if naive_extrap else None

    if method_a and method_b:
        final = round((method_a + method_b) / 2)
        basis = "same_day_type_avg + intraday_curve (평균)"
    elif method_a:
        final = method_a
        basis = "same_day_type_only (표본 부족 또는 예매율 0%로 곡선외삽 제외, 가드5/가드4)"
    elif method_b:
        final = method_b
        basis = "intraday_curve_only (지난주 동일요일유형 실측 없음)"
    else:
        final = None
        basis = "insufficient_data"

    return {
        "date": today.isoformat(),
        "day_type": dtype,
        "predicted_final": final,
        "basis": basis,
        "method_a_same_day_type": method_a,
        "method_b_intraday": method_b,
        "n_hourly_samples": n_samples,
    }


def benchmark_ratio_curve(benchmark_csv: Path, movie_cd: str) -> dict:
    """day_offset -> 그 시점까지의 누적/최종 비율. '최종'은 시리즈 내 최댓값으로 근사."""
    rows = []
    with open(benchmark_csv, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("movie_cd") != movie_cd or row.get("found") != "1":
                continue
            rows.append({"day_offset": int(row["day_offset"]), "cum_audi": _to_int(row["cum_audi"])})
    if not rows:
        return {}
    final = max(r["cum_audi"] for r in rows if r["cum_audi"])
    return {r["day_offset"]: r["cum_audi"] / final for r in rows if r["cum_audi"]}


def seat_capacity_cap(prev_actual: int, prev_screens: int, today_screens: int) -> float:
    """가드3: 스크린(편성) 급감 시, 미래일 예측 상한 = 전일 실적 × (오늘 스크린수/전일 스크린수)."""
    if not prev_screens:
        return float("inf")
    ratio = today_screens / prev_screens
    return prev_actual * min(ratio, 1.0) if ratio < 0.9 else float("inf")


def benchmark_daytype_decay(benchmark_csv: Path, movie_cd: str) -> dict:
    """요일유형별 '주간 실측 감쇠율' = 이번주 같은 요일유형 실적 / 지난주 같은 요일유형 실적의 중앙값.

    누적비율끼리 나누면 항상 1 이상이 나와 성장으로 오판하므로(버그),
    반드시 일별 실측치(daily_audi)를 요일유형별로 묶어 주 단위로 직접 비교한다.
    """
    rows = []
    with open(benchmark_csv, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("movie_cd") != movie_cd or row.get("found") != "1" or not row.get("daily_audi"):
                continue
            d = datetime.datetime.strptime(row["date"], "%Y%m%d").date()
            rows.append({"date": d, "daily_audi": int(row["daily_audi"])})
    by_date = {r["date"]: r["daily_audi"] for r in rows}

    ratios_by_type = {"weekday": [], "fri": [], "sat": [], "sun": []}
    for d, audi in by_date.items():
        prev_week = d - datetime.timedelta(days=7)
        if prev_week in by_date and by_date[prev_week]:
            ratios_by_type[day_type(d)].append(audi / by_date[prev_week])

    decay = {}
    for dtype, ratios in ratios_by_type.items():
        if ratios:
            ratios.sort()
            decay[dtype] = ratios[len(ratios) // 2]  # 중앙값 (블립 영향 최소화)
        else:
            decay[dtype] = 1.0
    return decay


def predict_opening_final(daily_series: list, benchmark_csv: Path, benchmark_movie_cd: str,
                            release_date: datetime.date, run_days: int = 70) -> dict:
    """개봉 최종 누적 = 실측(오늘까지) + 남은 일자 적산(동일요일유형 마지막 실측 × 벤치마크 주간 감쇠율^경과주).

    가드3: 스크린 급감이 확인되는 마지막 실측 구간에 한해 좌석(편성) 기준 캡 적용.
    가드4: 감쇠율이 1을 넘는 경우(블립 등으로 왜곡) 성장으로 취급하지 않도록 1.0으로 clip.
    """
    if not daily_series:
        return {"predicted_final": None, "basis": "no_data"}

    decay = benchmark_daytype_decay(benchmark_csv, benchmark_movie_cd)
    known_actual_to_date = daily_series[-1]["cum_audi"]
    last_date = daily_series[-1]["date"]
    last_offset = (last_date - release_date).days

    # 가드3: 최근 실측 구간에서 스크린수가 급감했는지 확인 (참고용, 로그에 남김)
    seat_cliff_detected = False
    if len(daily_series) >= 2:
        prev2, prev1 = daily_series[-2], daily_series[-1]
        if prev2["screen_cnt"] and prev1["screen_cnt"] and prev1["screen_cnt"] / prev2["screen_cnt"] < 0.9:
            seat_cliff_detected = True

    projected_extra = 0
    for offset in range(last_offset + 1, run_days):
        d = release_date + datetime.timedelta(days=offset)
        dtype = day_type(d)
        anchor = find_same_day_type_before(daily_series, d, dtype)
        if not anchor or not anchor["daily_audi"]:
            continue
        weeks_ahead = max(round((d - anchor["date"]).days / 7), 1)
        rate = min(decay.get(dtype, 1.0), 1.0)  # 가드4: 감쇠율이 1 넘으면 성장으로 오인하지 않도록 clip
        projected = anchor["daily_audi"] * (rate ** weeks_ahead)
        projected = min(projected, anchor["daily_audi"] * BLIP_CAP_RATIO)  # 가드4 안전판
        if seat_cliff_detected and daily_series[-1]["screen_cnt"] and daily_series[-2]["screen_cnt"]:
            cap = seat_capacity_cap(anchor["daily_audi"], daily_series[-2]["screen_cnt"], daily_series[-1]["screen_cnt"])
            projected = min(projected, cap)
        projected_extra += projected

    final = round(known_actual_to_date + projected_extra)
    return {
        "predicted_final": final,
        "known_actual_to_date": known_actual_to_date,
        "as_of_date": last_date.isoformat(),
        "projected_extra": round(projected_extra),
        "daytype_weekly_decay": {k: round(v, 3) for k, v in decay.items()},
        "seat_cliff_detected": seat_cliff_detected,
        "basis": "actual_to_date + remaining_days(same_day_type_last_actual x benchmark_weekly_decay^weeks_ahead, capped)",
    }


if __name__ == "__main__":
    import json

    target_movie_cd = "20234675"  # 파묘 (예시)
    benchmark_movie_cd = "20228797"  # 범죄도시4
    release_date = datetime.date(2024, 2, 22)

    full_series = load_daily_series(MEMBER_SNAPSHOTS, target_movie_cd)
    actual_final = max((r["cum_audi"] for r in full_series if r["cum_audi"]), default=None)

    # 백테스트: 개봉 20일차까지만 안다고 가정하고 최종 스코어를 맞춰본다
    cutoff_offset = 20
    cutoff_date = release_date + datetime.timedelta(days=cutoff_offset)
    partial_series = [r for r in full_series if r["date"] <= cutoff_date]

    today_pred = predict_today_final(partial_series, load_hourly_series(target_movie_cd, cutoff_date), cutoff_date)
    final_pred = predict_opening_final(partial_series, BENCHMARK_HISTORY, benchmark_movie_cd, release_date)

    result = {
        "backtest_cutoff": cutoff_date.isoformat(),
        "actual_final_from_full_data": actual_final,
        "today_final_prediction_at_cutoff": today_pred,
        "opening_final_prediction_at_cutoff": final_pred,
        "error_pct": (
            round((final_pred["predicted_final"] - actual_final) / actual_final * 100, 1)
            if final_pred.get("predicted_final") and actual_final else None
        ),
    }

    LOG = ROOT / "logs" / "predict_backtest.log"
    with open(LOG, "w", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=2))
