"""승인된 학습 대상 영화(config/training_targets.json)의 개봉일~3주차(21일)
일자별 좌석판매율/좌석점유율/좌석수/관객수/누적관객수를 KOBIS 공개 통계 페이지
(findDailySeatTicketList.do, 로그인 불필요)에서 수집한다.

이 페이지는 조회 기간 내 상영된 "모든" 영화를 날짜별로 보여준다(영화 지정 조회 불가).
그래서 필요한 날짜 구간(영화당 21일 -> 7일 창 3번)만 조회한 뒤, 그 결과에서
config에 등록된 movieCd만 골라서 저장한다 -> 시장 전체 데이터를 다시 쌓지 않고
승인된 영화분만 남긴다.

[재현된 과거 버그 방지] 이전에 대량 재스크래핑 중 "총 N건"으로 표시되는 실제
행 수보다 적게 수집되는 원인불명 버그가 있었다(원인 미해결로 기능 전체 폐기까지 갔었음).
이번엔 각 조회마다 페이지에 표시된 "총 N건" 문구와 실제 추출된 행 수를 비교해서
불일치하면 즉시 재시도하고, 그래도 안 맞으면 경고 로그를 남기고 건너뛴다(조용히
틀린 데이터를 저장하지 않는다).
"""
import csv
import datetime
import json
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from kobis_browser import launch_chromium

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "training_targets.json"
DATA_DIR = ROOT / "data" / "training"
LOG_PATH = ROOT / "logs" / "collect_training_seat_history.log"
URL = "https://www.kobis.or.kr/kobis/business/stat/boxs/findDailySeatTicketList.do"

FIELDS = [
    "batch_id", "date", "movie_cd", "movie_nm", "open_dt", "day_offset",
    "seat_sell_ratio_pct", "seat_occupancy_pct", "seat_cnt",
    "sales_amt", "sales_amt_acc", "audi_cnt", "audi_cnt_acc",
]

DATE_RE = re.compile(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})\D*\(")
TOTAL_RE = re.compile(r"총\s*([\d,]+)\s*건")


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat()} {msg}\n")


def load_batches() -> list:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)["batches"]


def windows_for_movie(open_dt: str) -> list:
    """개봉일 포함 21일(3주차까지)을 7일 창 3개로 분할."""
    start = datetime.datetime.strptime(open_dt, "%Y%m%d").date()
    out = []
    for w in range(3):
        ws = start + datetime.timedelta(days=w * 7)
        we = ws + datetime.timedelta(days=6)
        out.append((ws, we))
    return out


def extract(page) -> tuple:
    """현재 조회 결과에서 (날짜->행) 전체와 페이지 표시 총건수를 함께 반환."""
    data = page.evaluate(
        """
        () => {
          const container = document.querySelector('#contents') || document.body;
          const nodes = Array.from(container.querySelectorAll('h4, table'));
          const groups = [];
          let current = null;
          for (const el of nodes) {
            if (el.tagName === 'H4') {
              const t = el.textContent.trim();
              if (t) { current = { label: t, rows: [] }; groups.push(current); }
            } else if (el.tagName === 'TABLE' && current) {
              const trs = Array.from(el.querySelectorAll('tbody tr'));
              for (const tr of trs) {
                const tds = Array.from(tr.querySelectorAll('td'));
                if (tds.length < 10) continue;
                const movieLink = tr.querySelector('a[onclick*="mstView"]');
                let movieCd = '';
                if (movieLink) {
                  const m = movieLink.getAttribute('onclick').match(/'movie','(\\d+|\\w+)'/);
                  if (m) movieCd = m[1];
                }
                current.rows.push({
                  movie_cd: movieCd,
                  movie_nm: tds[1].textContent.trim(),
                  open_dt: tds[2].textContent.trim(),
                  seat_sell_ratio_pct: tds[3].textContent.trim(),
                  seat_occupancy_pct: tds[4].textContent.trim(),
                  seat_cnt: tds[5].textContent.trim(),
                  sales_amt: tds[6].textContent.trim(),
                  sales_amt_acc: tds[7].textContent.trim(),
                  audi_cnt: tds[8].textContent.trim(),
                  audi_cnt_acc: tds[9].textContent.trim(),
                });
              }
            }
          }
          const bodyText = document.body.innerText;
          return { groups, bodyText };
        }
        """
    )
    m = TOTAL_RE.search(data["bodyText"])
    displayed_total = int(m.group(1).replace(",", "")) if m else None

    rows = []
    for group in data["groups"]:
        gm = DATE_RE.search(group["label"])
        if not gm:
            continue
        date_str = f"{gm.group(1)}{int(gm.group(2)):02d}{int(gm.group(3)):02d}"
        for r in group["rows"]:
            r["date"] = date_str
            for k in ("seat_occupancy_pct", "seat_sell_ratio_pct"):
                r[k] = r[k].replace("%", "")
            for k in ("seat_cnt", "sales_amt", "sales_amt_acc", "audi_cnt", "audi_cnt_acc"):
                r[k] = r[k].replace(",", "")
            rows.append(r)
    return rows, displayed_total


def query_window(page, ws: datetime.date, we: datetime.date, max_retries: int = 3) -> list:
    s_str, e_str = ws.strftime("%Y-%m-%d"), we.strftime("%Y-%m-%d")
    for attempt in range(1, max_retries + 1):
        page.evaluate(f'document.getElementById("startDate").value = "{s_str}"')
        page.evaluate(f'document.getElementById("endDate").value = "{e_str}"')
        page.evaluate('document.querySelector("select[name=searchType]").value = "2"')
        with page.expect_navigation(wait_until="networkidle", timeout=30000):
            page.evaluate('chkform("search")')
        page.wait_for_timeout(2500)

        rows, displayed_total = extract(page)
        if displayed_total is None:
            log(f"WARN {s_str}~{e_str} attempt{attempt}: 총건수 표시를 못 찾음 (rows={len(rows)})")
            return rows
        if len(rows) == displayed_total:
            log(f"OK {s_str}~{e_str} rows={len(rows)} (표시된 총건수와 일치)")
            return rows
        log(f"MISMATCH {s_str}~{e_str} attempt{attempt}: 추출={len(rows)} 표시={displayed_total} -> 재시도")
        page.wait_for_timeout(1500)

    log(f"FAIL {s_str}~{e_str}: {max_retries}회 재시도 후에도 행 수 불일치 -> 이 구간 건너뜀 (데이터 저장 안 함)")
    return None


def collect_batch(batch: dict, page) -> list:
    target_cds = {m["movieCd"]: m for m in batch["movies"]}
    open_dt_by_cd = {m["movieCd"]: m["openDt"] for m in batch["movies"]}

    # 필요한 (start,end) 창을 영화들 간에 중복 없이 모음
    all_windows = set()
    for m in batch["movies"]:
        for ws, we in windows_for_movie(m["openDt"]):
            all_windows.add((ws, we))

    matched_rows = []
    seen_key = set()  # (movie_cd, date) 중복 저장 방지
    for i, (ws, we) in enumerate(sorted(all_windows), 1):
        log(f"[{batch['batch_id']}] window {i}/{len(all_windows)}: {ws}~{we}")
        rows = query_window(page, ws, we)
        if rows is None:
            continue
        for r in rows:
            cd = r["movie_cd"]
            if cd not in target_cds:
                continue
            key = (cd, r["date"])
            if key in seen_key:
                continue
            seen_key.add(key)
            open_dt = open_dt_by_cd[cd]
            day_offset = (
                datetime.datetime.strptime(r["date"], "%Y%m%d").date()
                - datetime.datetime.strptime(open_dt, "%Y%m%d").date()
            ).days
            if not (0 <= day_offset <= 20):
                continue
            matched_rows.append({
                "batch_id": batch["batch_id"],
                "date": r["date"],
                "movie_cd": cd,
                "movie_nm": r["movie_nm"],
                "open_dt": open_dt,
                "day_offset": day_offset,
                "seat_sell_ratio_pct": r["seat_sell_ratio_pct"],
                "seat_occupancy_pct": r["seat_occupancy_pct"],
                "seat_cnt": r["seat_cnt"],
                "sales_amt": r["sales_amt"],
                "sales_amt_acc": r["sales_amt_acc"],
                "audi_cnt": r["audi_cnt"],
                "audi_cnt_acc": r["audi_cnt_acc"],
            })
        time.sleep(0.4)

    return matched_rows


def write_csv(batch_id: str, rows: list) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / f"seat_history_{batch_id}.csv"
    rows.sort(key=lambda r: (r["movie_cd"], r["date"]))
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return out_path


if __name__ == "__main__":
    import sys

    only_batch = sys.argv[1] if len(sys.argv) > 1 else None
    batches = load_batches()
    if only_batch:
        batches = [b for b in batches if b["batch_id"] == only_batch]

    with sync_playwright() as p:
        browser = launch_chromium(p)
        page = browser.new_page(user_agent="Mozilla/5.0")
        page.on("dialog", lambda d: d.accept())
        page.goto(URL, wait_until="networkidle")

        for batch in batches:
            rows = collect_batch(batch, page)
            expected_movies = len(batch["movies"])
            got_movies = len({r["movie_cd"] for r in rows})
            out_path = write_csv(batch["batch_id"], rows)
            log(
                f"[{batch['batch_id']}] DONE rows={len(rows)} "
                f"movies_with_data={got_movies}/{expected_movies} -> {out_path}"
            )
            print(f"{batch['batch_id']}: rows={len(rows)}, movies_with_data={got_movies}/{expected_movies}")

        browser.close()
