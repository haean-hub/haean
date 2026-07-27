"""KOBIS Open API 공통 클라이언트.

공개 Open API(발급 키 필요)만 다룬다. 실시간 예매 페이지 스크래핑과
상영관 통계 로그인 스크래핑은 별도 모듈(collect_hourly.py, collect_daily.py)에서 처리한다.
"""
import json
import urllib.request
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRED_PATH = ROOT / "config" / "credentials.json"

BASE_URL = "https://www.kobis.or.kr/kobisopenapi/webservice/rest"


def load_api_key() -> str:
    with open(CRED_PATH, encoding="utf-8") as f:
        cred = json.load(f)
    key = cred.get("kobis_api_key", "")
    if not key or key.startswith("여기에"):
        raise RuntimeError("config/credentials.json 에 kobis_api_key가 채워져 있지 않습니다.")
    return key


def _get(path: str, params: dict) -> dict:
    key = load_api_key()
    params = {"key": key, **params}
    url = f"{BASE_URL}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search_movie_list(movie_nm: str) -> list:
    """영화명으로 movieCd 후보를 검색한다."""
    data = _get("movie/searchMovieList.json", {"movieNm": movie_nm, "itemPerPage": "10"})
    return data.get("movieListResult", {}).get("movieList", [])


def get_movie_info(movie_cd: str) -> dict:
    data = _get("movie/searchMovieInfo.json", {"movieCd": movie_cd})
    return data.get("movieInfoResult", {}).get("movieInfo", {})


def get_daily_boxoffice(target_dt: str, movie_cd: str | None = None) -> list:
    """target_dt: YYYYMMDD. 특정 영화만 보려면 movie_cd(=itemPerPage 늘려서 필터링)."""
    params = {"targetDt": target_dt}
    if movie_cd:
        params["itemPerPage"] = "50"
    data = _get("boxoffice/searchDailyBoxOfficeList.json", params)
    rows = data.get("boxOfficeResult", {}).get("dailyBoxOfficeList", [])
    if movie_cd:
        rows = [r for r in rows if r.get("movieCd") == movie_cd]
    return rows


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage: python kobis_client.py <search|info|daily> ...")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "search":
        name = sys.argv[2]
        for m in search_movie_list(name):
            print(m.get("movieCd"), m.get("movieNm"), m.get("openDt"), m.get("prdtYear"))
    elif cmd == "info":
        info = get_movie_info(sys.argv[2])
        print(json.dumps(info, ensure_ascii=False, indent=2))
    elif cmd == "daily":
        rows = get_daily_boxoffice(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
        for r in rows:
            print(r.get("rank"), r.get("movieNm"), r.get("audiCnt"), r.get("audiAcc"))
    else:
        print("unknown command:", cmd)
