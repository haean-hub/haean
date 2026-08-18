"""경쟁작(competitor_movies) 실시간 예매율 + 예매관객수/누적관객수 수집 -> data/competitors.csv 에 append.

기존에는 KOBIS 홈페이지 위젯(searchMainRealTicket.do)을 썼는데, 이건 실시간 예매 순위
TOP10짜리만 보여주는 구조라 아직 개봉 전이라 순위가 낮은 경쟁작들이 전부 못 잡혔다.
대신 KOBIS 통계 메뉴의 "예매율 > 실시간"(findRealTicketList.do) 페이지를 쓴다 —
이 페이지는 예매가 조금이라도 있는 영화 전체(수백 건)를 순위 제한 없이 보여준다.
로그인 불필요하지만 봇 방지 때문에 Playwright로 렌더링해야 한다(요청만으로는 껍데기만 옴).
"""
import csv
import datetime
import json
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "film_config.json"
DATA_PATH = ROOT / "data" / "competitors.csv"
LOG_PATH = ROOT / "logs" / "collect_competitors.log"
URL = "https://www.kobis.or.kr/kobis/business/stat/boxs/findRealTicketList.do"

FIELDS = [
    "collected_at", "movie_cd", "movie_nm", "found", "rank",
    "reservation_rate", "reservation_audi", "reservation_sales",
    "cum_audi", "cum_sales",
]

TOTAL_RE = re.compile(r"총\s*([\d,]+)\s*건")


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat()} {msg}\n")


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def fetch_all_rows(page, max_retries: int = 3) -> list:
    """실시간 예매율 페이지 전체 행을 추출. 표시된 총건수와 대조해서 불일치 시 재시도."""
    for attempt in range(1, max_retries + 1):
        page.goto(URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        data = page.evaluate(
            """
            () => {
              const trs = Array.from(document.querySelectorAll('table tbody tr'));
              const rows = trs.map(tr => {
                const tds = Array.from(tr.querySelectorAll('td'));
                if (tds.length < 8) return null;
                const link = tr.querySelector('a[onclick*="mstView"]');
                let movieCd = '';
                if (link) {
                  const m = link.getAttribute('onclick').match(/'movie','(\\w+)'/);
                  if (m) movieCd = m[1];
                }
                return {
                  movieCd,
                  rank: tds[0].textContent.trim(),
                  movieNm: tds[1].textContent.trim(),
                  openDt: tds[2].textContent.trim(),
                  reservationRate: tds[3].textContent.trim(),
                  reservationSales: tds[4].textContent.trim(),
                  cumSales: tds[5].textContent.trim(),
                  reservationAudi: tds[6].textContent.trim(),
                  cumAudi: tds[7].textContent.trim(),
                };
              }).filter(r => r);
              return { rows, bodyText: document.body.innerText };
            }
            """
        )
        rows = data["rows"]
        m = TOTAL_RE.search(data["bodyText"])
        displayed_total = int(m.group(1).replace(",", "")) if m else None

        if displayed_total is None:
            log(f"WARN attempt{attempt}: 총건수 표시를 못 찾음 (rows={len(rows)})")
            return rows
        if len(rows) == displayed_total:
            return rows
        log(f"MISMATCH attempt{attempt}: 추출={len(rows)} 표시={displayed_total} -> 재시도")
        page.wait_for_timeout(1500)

    log(f"FAIL {max_retries}회 재시도 후에도 행 수 불일치 -> 이번 수집 건너뜀")
    return None


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

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent="Mozilla/5.0")
        page.on("dialog", lambda d: d.accept())
        try:
            all_rows = fetch_all_rows(page)
        except Exception as e:
            log(f"ERROR fetch failed: {e}")
            browser.close()
            return
        browser.close()

    if all_rows is None:
        return

    by_cd = {r["movieCd"]: r for r in all_rows if r["movieCd"]}
    rows = []
    for comp in competitors:
        movie_cd = comp["movie_cd"]
        match = by_cd.get(movie_cd)
        if match:
            rows.append({
                "collected_at": now,
                "movie_cd": movie_cd,
                "movie_nm": match["movieNm"],
                "found": 1,
                "rank": match["rank"],
                "reservation_rate": match["reservationRate"].replace("%", ""),
                "reservation_audi": match["reservationAudi"].replace(",", ""),
                "reservation_sales": match["reservationSales"].replace(",", ""),
                "cum_audi": match["cumAudi"].replace(",", ""),
                "cum_sales": match["cumSales"].replace(",", ""),
            })
            log(f"OK {movie_cd} rank={match['rank']} rate={match['reservationRate']} audi={match['reservationAudi']}")
        else:
            rows.append({
                "collected_at": now,
                "movie_cd": movie_cd,
                "movie_nm": comp.get("title", ""),
                "found": 0,
                "rank": "",
                "reservation_rate": "",
                "reservation_audi": "",
                "reservation_sales": "",
                "cum_audi": "",
                "cum_sales": "",
            })
            log(f"WARN {movie_cd} 예매 데이터 없음(리스트에 없음)")

    append_rows(rows)


if __name__ == "__main__":
    main()
