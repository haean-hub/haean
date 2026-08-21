"""KOBIS 로그인 후 '회원용통계보기'(findCompanyStat.do)에서 우리 회사 영화의
스크린수·상영횟수·관객수·누적관객수를 수집 -> data/company_stat.csv.

이 페이지는 로그인한 배급사 계정에 등록된 영화만 자동으로 보여준다(영화명 검색 불필요).
로그인은 credentials.json의 kobis_login_id/pw/sms를 스크립트가 직접 읽어 처리하며,
값은 어디에도 출력하지 않는다. SMS 인증번호는 약 15일 유효 — 만료되면 로그인이 실패하니
config/credentials.json 의 kobis_login_sms 값을 새로 받아 갱신해야 한다.

[중요] 여기서 나오는 "상영횟수"는 실제 관객이 든 회차만 집계된 값이라, 배정받은 전체
회차수와 다를 수 있다(사용자 확인 사항). 좌석수도 이 페이지엔 없다 — 둘 다 필요하면
사용자가 별도로 입력해야 한다. 이 스크립트는 그 나머지(관객수/누적관객수/상영횟수/스크린수
등 로그인 계정으로 확인 가능한 값)만 자동 수집한다.
"""
import argparse
import csv
import datetime
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from kobis_browser import launch_chromium

ROOT = Path(__file__).resolve().parent.parent
CRED_PATH = ROOT / "config" / "credentials.json"
DATA_PATH = ROOT / "data" / "company_stat.csv"
LOG_PATH = ROOT / "logs" / "collect_company_stat.log"

LOGIN_URL = "https://www.kobis.or.kr/kobis/business/comm/user/openLogin.do"
TARGET_URL = "https://www.kobis.or.kr/kobis/business/mast/thea/findCompanyStat.do"

FIELDS = [
    "collected_at", "target_date", "movie_nm", "screen_cnt", "show_cnt",
    "sales_amt", "sales_amt_acc", "audi_cnt", "audi_cnt_acc", "free_audi_cnt",
]


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat()} {msg}\n")


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


def collect(target_date: str) -> tuple:
    with open(CRED_PATH, encoding="utf-8") as f:
        cred = json.load(f)

    now = datetime.datetime.now().replace(microsecond=0).isoformat()

    with sync_playwright() as p:
        browser = launch_chromium(p)
        page = browser.new_page(user_agent="Mozilla/5.0")
        page.on("dialog", lambda d: d.accept())

        page.goto(LOGIN_URL, wait_until="networkidle")
        page.fill("#ipt_id", cred["kobis_login_id"])
        page.fill("#ipt_pw", cred["kobis_login_pw"])
        page.fill("#ipt_sms", cred["kobis_login_sms"])
        with page.expect_navigation(wait_until="networkidle", timeout=20000):
            page.click('button[type="submit"]:has-text("로그인")')

        body_text = page.eval_on_selector("body", "el => el.innerText")
        if "로그아웃" not in body_text:
            browser.close()
            return [], "login_failed"

        page.goto(TARGET_URL, wait_until="networkidle")
        page.evaluate(f'document.getElementById("sStartDt").value = "{target_date}"')
        page.evaluate(f'document.getElementById("sEndDt").value = "{target_date}"')
        with page.expect_navigation(wait_until="networkidle", timeout=20000):
            page.click('button:has-text("조회")')
        page.wait_for_timeout(1000)

        rows_raw = page.eval_on_selector_all(
            "table tbody tr",
            """
            els => els.map(tr => Array.from(tr.querySelectorAll('td')).map(td => td.textContent.trim()))
            """,
        )
        browser.close()

    rows = []
    for r in rows_raw:
        if len(r) < 8:
            continue
        rows.append({
            "collected_at": now,
            "target_date": target_date,
            "movie_nm": r[0],
            "screen_cnt": r[1].replace(",", ""),
            "show_cnt": r[2].replace(",", ""),
            "sales_amt": r[3].replace(",", ""),
            "sales_amt_acc": r[4].replace(",", ""),
            "audi_cnt": r[5].replace(",", ""),
            "audi_cnt_acc": r[6].replace(",", ""),
            "free_audi_cnt": r[7].replace(",", ""),
        })
    return rows, "ok"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD, 기본값=오늘")
    args = parser.parse_args()
    target = args.date or datetime.date.today().strftime("%Y-%m-%d")

    try:
        rows, status = collect(target)
    except Exception as e:
        log(f"ERROR collect failed: {e}")
        rows, status = [], "exception"

    if rows:
        append_rows(rows)
        log(f"OK {target} rows={len(rows)}")
    elif status == "login_failed":
        log("ERROR login failed (SMS 인증번호 만료 가능성 - credentials.json 갱신 필요)")
        sys.exit(1)
    elif status == "exception":
        sys.exit(1)
    else:
        log(f"WARN {target} no rows (영화 미상영 - 정상일 수 있음)")
