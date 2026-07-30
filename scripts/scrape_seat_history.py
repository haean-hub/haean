"""KOBIS '일별 좌석판매율' 통계(findDailySeatTicketList.do)를 Playwright로 대량 수집.

이 페이지는 봇 방지 장치 때문에 requests로는 안 되고(껍데기만 반환),
실제 브라우저 렌더링이 필요해 Playwright를 쓴다. 한 번에 최대 7일치만 조회 가능
(에러 메시지는 "6개월"이라 나오지만 실제 코드는 6일 제한 -> 7일 창으로 순회).

data/market_seat_history.csv 에 영화 구분 없이 스크린에 걸린 모든 영화·모든 날짜를 쌓는다.
predict.py의 "시장 평균 감쇠 곡선" 계산은 별도 스크립트(aggregate_market_curve.py)에서 처리.
"""
import argparse
import csv
import datetime
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "market_seat_history.csv"
LOG_PATH = ROOT / "logs" / "scrape_seat_history.log"
URL = "https://www.kobis.or.kr/kobis/business/stat/boxs/findDailySeatTicketList.do"

FIELDS = [
    "date", "movie_cd", "movie_nm", "open_dt",
    "seat_occupancy_pct", "seat_sell_ratio_pct", "seat_cnt",
    "sales_amt", "sales_amt_acc", "audi_cnt", "audi_cnt_acc",
]

DATE_RE = re.compile(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})\D*\(")


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat()} {msg}\n")


def already_have_dates() -> set:
    if not DATA_PATH.exists():
        return set()
    with open(DATA_PATH, encoding="utf-8-sig", newline="") as f:
        return {row["date"] for row in csv.DictReader(f)}


def append_rows(rows: list) -> None:
    if not rows:
        return
    is_new = not DATA_PATH.exists()
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_PATH, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def extract_week(page) -> list:
    """현재 페이지(조회 결과)에서 (날짜 -> 행 리스트) 전체를 추출."""
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
                  seat_occupancy_pct: tds[3].textContent.trim(),
                  seat_sell_ratio_pct: tds[4].textContent.trim(),
                  seat_cnt: tds[5].textContent.trim(),
                  sales_amt: tds[6].textContent.trim(),
                  sales_amt_acc: tds[7].textContent.trim(),
                  audi_cnt: tds[8].textContent.trim(),
                  audi_cnt_acc: tds[9].textContent.trim(),
                });
              }
            }
          }
          return groups;
        }
        """
    )
    out = []
    for group in data:
        m = DATE_RE.search(group["label"])
        if not m:
            continue
        date_str = f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
        for r in group["rows"]:
            r["date"] = date_str
            for k in ("seat_occupancy_pct", "seat_sell_ratio_pct"):
                r[k] = r[k].replace("%", "")
            for k in ("seat_cnt", "sales_amt", "sales_amt_acc", "audi_cnt", "audi_cnt_acc"):
                r[k] = r[k].replace(",", "")
            out.append(r)
    return out


def scrape_range(start: datetime.date, end: datetime.date) -> None:
    have = already_have_dates()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent="Mozilla/5.0")
        page.on("dialog", lambda d: d.accept())
        page.goto(URL, wait_until="networkidle")

        cur = start
        while cur <= end:
            window_end = min(cur + datetime.timedelta(days=6), end)
            s_str, e_str = cur.strftime("%Y-%m-%d"), window_end.strftime("%Y-%m-%d")
            all_in_range_done = all(
                (cur + datetime.timedelta(days=i)).strftime("%Y%m%d") in have
                for i in range((window_end - cur).days + 1)
            )
            if all_in_range_done:
                log(f"SKIP {s_str}~{e_str} (already collected)")
                cur = window_end + datetime.timedelta(days=1)
                continue

            page.evaluate(f'document.getElementById("startDate").value = "{s_str}"')
            page.evaluate(f'document.getElementById("endDate").value = "{e_str}"')
            try:
                with page.expect_navigation(wait_until="networkidle", timeout=30000):
                    page.evaluate('chkform("search")')
                page.wait_for_timeout(800)
                rows = extract_week(page)
                new_rows = [r for r in rows if r["date"] not in have]
                append_rows(new_rows)
                for r in rows:
                    have.add(r["date"])
                log(f"OK {s_str}~{e_str} rows={len(rows)}")
            except Exception as e:
                log(f"ERROR {s_str}~{e_str}: {e}")

            cur = window_end + datetime.timedelta(days=1)
            time.sleep(0.5)

        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()
    scrape_range(
        datetime.datetime.strptime(args.start, "%Y-%m-%d").date(),
        datetime.datetime.strptime(args.end, "%Y-%m-%d").date(),
    )
