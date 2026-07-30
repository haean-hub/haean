"""대상 영화(target_movie) 자신의 일별 좌석수/좌석판매율을 수집 -> data/own_seat_daily.csv.

KOBIS Open API에는 좌석수 필드가 없어서, market_seat_history 수집과 같은 페이지
(findDailySeatTicketList.do)를 Playwright로 열어 우리 영화의 movieCd만 찾아 기록한다.
개봉 전이면 당연히 목록에 없으므로 found=0으로 남긴다. 좌석 배정은 하루 단위라 매시간
돌 필요는 없지만, run_cycle.ps1에 그냥 끼워도 무방(이미 개봉해서 값이 있으면 매번 같은
값을 다시 확인하는 정도).
"""
import csv
import datetime
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scrape_seat_history import wait_for_stable_rows  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "film_config.json"
DATA_PATH = ROOT / "data" / "own_seat_daily.csv"
LOG_PATH = ROOT / "logs" / "collect_own_seat_data.log"
URL = "https://www.kobis.or.kr/kobis/business/stat/boxs/findDailySeatTicketList.do"

FIELDS = [
    "collected_at", "target_date", "movie_cd", "movie_nm", "found",
    "seat_occupancy_pct", "seat_sell_ratio_pct", "seat_cnt", "audi_cnt",
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


def fetch_last_week_rows() -> list:
    """기본 화면(최근 7일)에서 전체 행을 뽑는다. collect_hourly와 같은 재시도 없이,
    실패하면 다음 스케줄 사이클에서 다시 시도한다(하루 단위라 급하지 않음)."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent="Mozilla/5.0")
        page.on("dialog", lambda d: d.accept())
        page.goto(URL, wait_until="networkidle")
        # 기본값은 "일반영화"만 -> "전체"로 바꿔 다시 조회(독립·예술영화 포함)
        page.evaluate('document.querySelector("select[name=searchType]").value = "2"')
        with page.expect_navigation(wait_until="networkidle", timeout=30000):
            page.evaluate('chkform("search")')
        wait_for_stable_rows(page)
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
                      seat_sell_ratio_pct: tds[3].textContent.trim().replace('%',''),
                      seat_occupancy_pct: tds[4].textContent.trim().replace('%',''),
                      seat_cnt: tds[5].textContent.trim().replace(/,/g,''),
                      audi_cnt: tds[8].textContent.trim().replace(/,/g,''),
                    });
                  }
                }
              }
              return groups;
            }
            """
        )
        browser.close()
        return data


def main() -> None:
    config = load_config()
    movie_cd = config["target_movie"]["movie_cd"]
    now = datetime.datetime.now().replace(microsecond=0).isoformat()
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y%m%d")

    try:
        groups = fetch_last_week_rows()
    except Exception as e:
        log(f"ERROR fetch failed: {e}")
        return

    match = None
    for g in groups:
        for r in g["rows"]:
            if r["movie_cd"] == movie_cd:
                match = r
                break
        if match:
            break

    if match:
        row = {
            "collected_at": now, "target_date": yesterday, "movie_cd": movie_cd,
            "movie_nm": match["movie_nm"], "found": 1,
            "seat_occupancy_pct": match["seat_occupancy_pct"],
            "seat_sell_ratio_pct": match["seat_sell_ratio_pct"],
            "seat_cnt": match["seat_cnt"], "audi_cnt": match["audi_cnt"],
        }
        log(f"OK seat_cnt={match['seat_cnt']} seat_sell={match['seat_sell_ratio_pct']}")
    else:
        row = {
            "collected_at": now, "target_date": yesterday, "movie_cd": movie_cd,
            "movie_nm": config["target_movie"]["title"], "found": 0,
            "seat_occupancy_pct": "", "seat_sell_ratio_pct": "", "seat_cnt": "", "audi_cnt": "",
        }
        log("WARN movie not found in seat ticket list (아직 개봉 전이거나 최근 7일 밖)")

    append_row(row)


if __name__ == "__main__":
    main()
