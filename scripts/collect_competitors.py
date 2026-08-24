"""경쟁작(competitor_movies) 실시간 예매율 + 예매관객수/누적관객수 수집 -> data/competitors.csv 에 append.

처음엔 KOBIS 홈페이지 위젯(searchMainRealTicket.do, top10 제한)을 썼고, 그다음엔
"예매율 > 실시간"(findRealTicketList.do, top10 제한 없음)을 Playwright로 긁었다.
근데 Playwright 브라우저 실행이 Windows 작업 스케줄러의 무인 세션에서만(대화형으로는
재현 안 됨) "Executable doesn't exist"로 계속 실패해서 데이터가 며칠씩 끊기는 일이
반복됐다. 알고 보니 이 페이지도 실제로는 순수 HTML 폼 POST로 렌더링되는 서버사이드
페이지라(자바스크립트로 별도 AJAX를 쏘는 게 아니라 <form>.submit()), 브라우저 없이
urllib만으로 CSRFToken/세션 쿠키를 받아서 그대로 재현하면 똑같은 결과를 받을 수 있다.
Playwright 의존성을 완전히 제거해서 이 문제 자체를 회피한다.
"""
import csv
import datetime
import http.cookiejar
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "film_config.json"
DATA_PATH = ROOT / "data" / "competitors.csv"
LOG_PATH = ROOT / "logs" / "collect_competitors.log"
URL = "https://www.kobis.or.kr/kobis/business/stat/boxs/findRealTicketList.do"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

FIELDS = [
    "collected_at", "movie_cd", "movie_nm", "found", "rank",
    "reservation_rate", "reservation_audi", "reservation_sales",
    "cum_audi", "cum_sales",
]

TOTAL_RE = re.compile(r"총[^\d]{0,60}?([\d,]+)[^\d]{0,10}?건")
TOKEN_RE = re.compile(r'name="CSRFToken" value="([^"]+)"')
ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
MOVIE_CD_RE = re.compile(r"mstView\('movie','(\w+)'\)")
TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
TAG_RE = re.compile(r"<[^>]+>")


def log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat()} {msg}\n")


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def fetch_all_rows(max_retries: int = 3) -> list:
    """전체 실시간 예매 목록(top10 제한 없음)을 순수 HTTP로 가져온다.
    표시된 총건수와 대조해서 불일치하면 재시도."""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    for attempt in range(1, max_retries + 1):
        try:
            req1 = urllib.request.Request(URL, headers={"User-Agent": UA})
            with opener.open(req1, timeout=15) as resp:
                body1 = resp.read().decode("utf-8", errors="replace")
            m = TOKEN_RE.search(body1)
            if not m:
                log(f"ERROR attempt{attempt}: CSRFToken을 못 찾음")
                continue
            token = m.group(1)

            form = [
                ("CSRFToken", token), ("loadEnd", "0"), ("repNationCd", ""), ("areaCd", ""),
                ("repNationSelected", ""), ("dmlMode", "search"), ("repNationChk", ""),
                ("repNationKor", "on"), ("repNationKor", "on"), ("wideareaAll", "ALL"),
                ("sNomal", "Y"), ("sMulti", "Y"), ("sIndie", "Y"),
            ]
            data = urllib.parse.urlencode(form).encode("utf-8")
            req2 = urllib.request.Request(
                URL, data=data, method="POST",
                headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded", "Referer": URL},
            )
            with opener.open(req2, timeout=15) as resp:
                body2 = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            log(f"ERROR attempt{attempt}: {type(e).__name__}: {e}")
            continue

        tm = TOTAL_RE.search(body2)
        displayed_total = int(tm.group(1).replace(",", "")) if tm else None

        idx = body2.find("<tbody")
        end = body2.find("</tbody>", idx)
        tbody = body2[idx:end] if idx >= 0 and end >= 0 else ""
        rows = []
        for tr in ROW_RE.findall(tbody):
            cd_m = MOVIE_CD_RE.search(tr)
            tds = [TAG_RE.sub("", td).strip() for td in TD_RE.findall(tr)]
            if len(tds) < 8:
                continue
            rows.append({
                "movieCd": cd_m.group(1) if cd_m else "",
                "rank": tds[0], "movieNm": tds[1], "openDt": tds[2],
                "reservationRate": tds[3], "reservationSales": tds[4],
                "cumSales": tds[5], "reservationAudi": tds[6], "cumAudi": tds[7],
            })

        if displayed_total is None:
            log(f"WARN attempt{attempt}: 총건수 표시를 못 찾음 (rows={len(rows)})")
            return rows
        if len(rows) == displayed_total:
            return rows
        log(f"MISMATCH attempt{attempt}: 추출={len(rows)} 표시={displayed_total} -> 재시도")

    log(f"FAIL {max_retries}회 재시도 후에도 실패 -> 이번 수집 건너뜀")
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
    import sys

    config = load_config()
    competitors = config.get("competitor_movies", [])
    if not competitors:
        log("SKIP no competitor_movies configured")
        return

    now = datetime.datetime.now().replace(microsecond=0).isoformat()
    all_rows = fetch_all_rows()
    if all_rows is None:
        sys.exit(1)

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
