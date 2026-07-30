"""수집 데이터 + predict.py 예측치를 읽어 자체완결 index.html 대시보드를 생성한다.

외부 CDN 의존 없이 인라인 CSS/SVG만 사용한다(레퍼런스 프롬프트 요구사항).
색/타이포/차트 스펙은 dataviz 스킬의 검증된 기본 팔레트를 그대로 사용한다.
"""
import csv
import datetime
import json
from pathlib import Path

from predict import (
    BENCHMARK_HISTORY, MEMBER_SNAPSHOTS, HOURLY,
    load_daily_series, load_hourly_series, predict_today_final, predict_opening_final,
    latest_hourly_snapshot,
)

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "film_config.json"
AI_COMMENT_PATH = ROOT / "ai_comment.json"
COMPETITORS_PATH = ROOT / "data" / "competitors.csv"
OUTPUT_PATH = ROOT / "index.html"

DEFAULT_AI_COMMENT = {
    "현황": "아직 코멘트가 생성되지 않았습니다.",
    "최종전망": "아직 코멘트가 생성되지 않았습니다.",
    "주말": "아직 코멘트가 생성되지 않았습니다.",
    "신규수요분해": "아직 코멘트가 생성되지 않았습니다.",
}

# --- 색: dataviz 스킬 references/palette.md 의 검증된 기본값 그대로 사용 ---
SERIES_1 = "var(--series-1)"  # blue  — 대상 영화
SERIES_2 = "var(--series-2)"  # aqua  — 벤치마크/비교


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_ai_comment() -> dict:
    if not AI_COMMENT_PATH.exists():
        return DEFAULT_AI_COMMENT
    with open(AI_COMMENT_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return {**DEFAULT_AI_COMMENT, **data}


def fmt_num(n) -> str:
    if n is None:
        return "-"
    return f"{n:,.0f}"


def hour_bucket_series(rows: list, metric: str) -> tuple:
    """수집분을 1시간 단위(예: 10:00-11:00)로 묶어, 그 시간대 마지막 값을 대표값으로 쓴다.
    반환: (series[(label, value)], tooltips[str]) — 툴팁엔 직전 시간 대비 증감을 함께 담는다.
    """
    buckets = {}
    for h in rows:
        key = h["ts"].replace(minute=0, second=0, microsecond=0)
        buckets[key] = h  # 같은 시간대 내 마지막 수집분으로 덮어씀
    keys = sorted(buckets)

    series, tooltips, prev_val = [], [], None
    for k in keys:
        val = buckets[k][metric]
        label = f"{k.hour:02d}:00-{(k.hour + 1) % 24:02d}:00"
        if prev_val is not None and val is not None:
            d = val - prev_val
            sign = "+" if d >= 0 else ""
            delta_txt = f"직전 시간 대비 {sign}{d:,}명"
        else:
            delta_txt = "직전 시간 데이터 없음"
        val_txt = f"{val:,}명" if val is not None else "-"
        tooltips.append(f"{label}\n{val_txt}\n{delta_txt}")
        series.append((label, val))
        if val is not None:
            prev_val = val
    return series, tooltips


def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def nice_ticks(vmin: float, vmax: float, n: int = 3) -> list:
    """y축용 깔끔한 눈금 n개(대략)를 만든다."""
    if vmax <= vmin:
        return [vmin]
    span = vmax - vmin
    raw_step = span / (n - 1)
    mag = 10 ** (len(str(int(raw_step))) - 1) if raw_step >= 1 else 1
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if step >= raw_step:
            break
    start = (int(vmin / step)) * step
    ticks = []
    v = start
    while v <= vmax + step * 0.01:
        if v >= vmin - step * 0.01:
            ticks.append(round(v))
        v += step
    return ticks or [vmin, vmax]


def svg_line_chart(series: list, width=640, height=240, color=SERIES_1, value_fmt=fmt_num,
                    tooltips: list = None) -> str:
    """series: [(label, value), ...]. tooltips가 주어지면 각 점에 마우스오버 시 표시(SVG <title>, JS 불필요)."""
    pts_all = [(i, l, v) for i, (l, v) in enumerate(series) if v is not None]
    pts = [(l, v) for _, l, v in pts_all]
    tip_by_pt = [tooltips[i] for i, _, _ in pts_all] if tooltips else None
    if len(pts) < 2:
        return f'<div class="chart-empty">데이터가 아직 충분하지 않습니다 ({len(pts)}건)</div>'

    pad_l, pad_r, pad_t, pad_b = 46, 16, 16, 28
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    values = [v for _, v in pts]
    vmin, vmax = min(0, min(values)), max(values)
    if vmax == vmin:
        vmax = vmin + 1

    def x_of(i):
        return pad_l + (i / (len(pts) - 1)) * plot_w

    def y_of(v):
        return pad_t + plot_h - ((v - vmin) / (vmax - vmin)) * plot_h

    path_d = " ".join(
        f"{'M' if i == 0 else 'L'}{x_of(i):.1f},{y_of(v):.1f}" for i, (_, v) in enumerate(pts)
    )
    area_d = path_d + f" L{x_of(len(pts)-1):.1f},{pad_t+plot_h} L{x_of(0):.1f},{pad_t+plot_h} Z"

    # y축: 눈금 + 헤어라인 그리드
    ticks = nice_ticks(vmin, vmax, 3)
    grid_svg = "".join(
        f'<line x1="{pad_l}" y1="{y_of(t):.1f}" x2="{width-pad_r}" y2="{y_of(t):.1f}" class="gridline"></line>'
        f'<text x="{pad_l-8}" y="{y_of(t)+3:.1f}" class="axis-label" text-anchor="end">{fmt_num(t)}</text>'
        for t in ticks
    )

    # x축 라벨: 처음/중간/끝만
    label_idxs = sorted(set([0, len(pts) // 2, len(pts) - 1]))
    labels_svg = "".join(
        f'<text x="{x_of(i):.1f}" y="{height-8}" class="axis-label" text-anchor="middle">{pts[i][0]}</text>'
        for i in label_idxs
    )

    last_label, last_val = pts[-1]

    hover_points_svg = ""
    if tip_by_pt:
        hover_points_svg = "".join(
            f'<circle cx="{x_of(i):.1f}" cy="{y_of(v):.1f}" r="10" fill="transparent" class="hover-dot">'
            f'<title>{html_escape(tip_by_pt[i])}</title></circle>'
            for i, (_, v) in enumerate(pts)
        )

    return f'''<svg viewBox="0 0 {width} {height}" class="chart-svg" role="img" aria-label="추이 차트">
  {grid_svg}
  <path d="{area_d}" fill="{color}" opacity="0.10" stroke="none"></path>
  <path d="{path_d}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"></path>
  <circle cx="{x_of(len(pts)-1):.1f}" cy="{y_of(last_val):.1f}" r="4" fill="{color}" stroke="var(--surface-1)" stroke-width="2"></circle>
  <text x="{x_of(len(pts)-1):.1f}" y="{y_of(last_val)-10:.1f}" class="point-label" text-anchor="end">{value_fmt(last_val)}</text>
  {labels_svg}
  {hover_points_svg}
</svg>'''


def svg_multi_line_chart(series_a: list, series_b: list, name_a: str, name_b: str,
                          width=640, height=240, color_a=SERIES_1, color_b=SERIES_2) -> str:
    pts_a = [(x, v) for x, v in series_a if v is not None]
    pts_b = [(x, v) for x, v in series_b if v is not None]
    if len(pts_a) < 2 or len(pts_b) < 2:
        return '<div class="chart-empty">비교할 데이터가 아직 충분하지 않습니다</div>'

    pad_l, pad_r, pad_t, pad_b = 46, 16, 16, 28
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    all_x = sorted(set([x for x, _ in pts_a] + [x for x, _ in pts_b]))
    xmin, xmax = min(all_x), max(all_x)
    all_v = [v for _, v in pts_a] + [v for _, v in pts_b]
    vmin, vmax = 0, max(all_v) if all_v else 1
    if vmax == 0:
        vmax = 1

    def x_of(x):
        if xmax == xmin:
            return pad_l
        return pad_l + ((x - xmin) / (xmax - xmin)) * plot_w

    def y_of(v):
        return pad_t + plot_h - (v / vmax) * plot_h

    def path_for(pts):
        return " ".join(f"{'M' if i == 0 else 'L'}{x_of(x):.1f},{y_of(v):.1f}" for i, (x, v) in enumerate(pts))

    ticks = nice_ticks(0, vmax, 3)
    grid_svg = "".join(
        f'<line x1="{pad_l}" y1="{y_of(t):.1f}" x2="{width-pad_r}" y2="{y_of(t):.1f}" class="gridline"></line>'
        f'<text x="{pad_l-8}" y="{y_of(t)+3:.1f}" class="axis-label" text-anchor="end">{fmt_num(t)}</text>'
        for t in ticks
    )

    return f'''<div class="chart-legend">
  <span class="legend-item"><span class="legend-swatch" style="background:{color_a}"></span>{html_escape(name_a)}</span>
  <span class="legend-item"><span class="legend-swatch legend-swatch--dashed" style="border-color:{color_b}"></span>{html_escape(name_b)}</span>
</div>
<svg viewBox="0 0 {width} {height}" class="chart-svg" role="img" aria-label="비교 차트">
  {grid_svg}
  <path d="{path_for(pts_a)}" fill="none" stroke="{color_a}" stroke-width="2" stroke-linecap="round"></path>
  <path d="{path_for(pts_b)}" fill="none" stroke="{color_b}" stroke-width="2" stroke-linecap="round" stroke-dasharray="6,4"></path>
</svg>'''


def load_competitor_rows() -> list:
    """경쟁작별 가장 최근 수집 행 하나씩."""
    if not COMPETITORS_PATH.exists():
        return []
    latest = {}
    with open(COMPETITORS_PATH, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            latest[row["movie_cd"]] = row
    return list(latest.values())


def competitor_table(rows: list, own_title: str, own_rate, own_cum) -> str:
    """경쟁작 비교는 색상 6개 이상이 겹쳐 선그래프보다 표가 더 읽기 쉬워 표로 낸다."""
    entries = [{
        "title": own_title, "rate": own_rate, "cum": own_cum, "own": True, "found": own_rate is not None,
    }]
    for r in rows:
        rate = float(r["reservation_rate"]) if r.get("reservation_rate") else None
        cum = int(r["cum_audi"]) if r.get("cum_audi") else None
        entries.append({"title": r["movie_nm"] or r["movie_cd"], "rate": rate, "cum": cum,
                         "own": False, "found": r.get("found") == "1"})

    entries.sort(key=lambda e: (e["rate"] is None, -(e["rate"] or 0)))

    if len(entries) <= 1:
        return '<div class="chart-empty">등록된 경쟁작이 없습니다</div>'

    rows_html = []
    for e in entries:
        row_cls = "cmp-row cmp-row--own" if e["own"] else "cmp-row"
        rate_txt = f'{e["rate"]:.1f}%' if e["rate"] is not None else "—"
        cum_txt = fmt_num(e["cum"]) + "명" if e["cum"] is not None else "—"
        status = "" if e["found"] else '<span class="cmp-pending">집계 전</span>'
        rows_html.append(
            f'<tr class="{row_cls}"><td class="cmp-title">{html_escape(e["title"])}{status}</td>'
            f'<td class="cmp-num">{rate_txt}</td><td class="cmp-num">{cum_txt}</td></tr>'
        )

    return f'''<table class="cmp-table">
  <thead><tr><th>영화</th><th>실시간 예매율</th><th>누적관객수</th></tr></thead>
  <tbody>{"".join(rows_html)}</tbody>
</table>'''


def build() -> None:
    config = load_config()
    target = config["target_movie"]
    movie_cd = target["movie_cd"]
    benchmark = config.get("benchmark_movies", [{}])[0]

    daily_series = load_daily_series(MEMBER_SNAPSHOTS, movie_cd)
    today = datetime.date.today()
    hourly_today = load_hourly_series(movie_cd, today)

    today_pred = predict_today_final(daily_series, hourly_today, today) if daily_series else None
    final_pred = predict_opening_final(
        daily_series, BENCHMARK_HISTORY, benchmark.get("movie_cd", ""),
        datetime.datetime.strptime(target["release_date"], "%Y-%m-%d").date(),
    ) if daily_series and benchmark.get("movie_cd") else None

    ai_comment = load_ai_comment()

    # 차트용 데이터
    cum_series = [(r["date"].strftime("%m/%d"), r["cum_audi"]) for r in daily_series]
    released_today = any((h["cum_audi"] or 0) > 0 for h in hourly_today)
    hourly_metric = "cum_audi" if released_today else "reservation_audi"
    hourly_chart_title = "오늘 시간대별 누적 관객(실제 입장)" if released_today else "오늘 시간대별 예매 관객(개봉 전)"
    hourly_series, hourly_tooltips = hour_bucket_series(hourly_today, hourly_metric)
    avg_per_show_series = [
        (r["date"].strftime("%m/%d"), (r["daily_audi"] / r["show_cnt"]) if r["show_cnt"] else None)
        for r in daily_series
    ]

    release_date = datetime.datetime.strptime(target["release_date"], "%Y-%m-%d").date()
    target_by_offset = [((r["date"] - release_date).days, r["cum_audi"]) for r in daily_series]
    benchmark_by_offset = []
    if benchmark.get("movie_cd") and BENCHMARK_HISTORY.exists():
        with open(BENCHMARK_HISTORY, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("movie_cd") == benchmark["movie_cd"] and row.get("found") == "1" and row.get("cum_audi"):
                    benchmark_by_offset.append((int(row["day_offset"]), int(row["cum_audi"])))
        benchmark_by_offset.sort()

    hero_today = fmt_num(today_pred["predicted_final"]) if today_pred else "-"
    hero_final = fmt_num(final_pred["predicted_final"]) if final_pred else "-"
    target_pct = ""
    if final_pred and final_pred.get("predicted_final") and target.get("target_admissions"):
        pct = final_pred["predicted_final"] / target["target_admissions"] * 100
        target_pct = f'<div class="hero-sub">목표 {fmt_num(target["target_admissions"])}명 대비 {pct:.0f}%</div>'

    latest_snapshot = latest_hourly_snapshot(movie_cd)
    hero_reservation = fmt_num(latest_snapshot["reservation_audi"]) if latest_snapshot else "-"
    reservation_sub = ""
    if latest_snapshot:
        as_of = latest_snapshot["collected_at"].strftime("%H:%M")
        delta = latest_snapshot["reservation_audi_delta"]
        mins = latest_snapshot["minutes_since_prev"]
        if delta is not None and mins:
            sign = "+" if delta >= 0 else ""
            delta_cls = "delta-up" if delta >= 0 else "delta-down"
            reservation_sub = (
                f'<div class="hero-sub">{as_of} 기준 · '
                f'<span class="{delta_cls}">직전 {mins}분간 {sign}{delta:,}명</span></div>'
            )
        else:
            reservation_sub = f'<div class="hero-sub">{as_of} 기준</div>'

    title_logo = target.get("title_logo")
    if title_logo and (ROOT / title_logo).exists():
        title_html = f'<img src="{title_logo}" alt="{html_escape(target["title"])}" class="title-logo">'
    else:
        title_html = f'<h1>{target["title"]}</h1>'

    side_art = target.get("side_art", "")
    has_side_art = bool(side_art) and (ROOT / side_art).exists()
    side_art_html = (
        f'<div class="side-art side-art--left"><img src="{side_art}" alt=""></div>'
        f'<div class="side-art side-art--right"><img src="{side_art}" alt=""></div>'
        if has_side_art else ""
    )

    d_day = (release_date - today).days
    if d_day > 0:
        status_badge = f'<span class="badge badge--upcoming">D-{d_day}</span>'
    elif d_day == 0:
        status_badge = '<span class="badge badge--live">개봉 당일</span>'
    else:
        status_badge = f'<span class="badge badge--live">개봉 {abs(d_day)}일차</span>'

    competitor_rows = load_competitor_rows()
    own_rate = None
    if HOURLY.exists():
        with open(HOURLY, encoding="utf-8-sig", newline="") as f:
            last_found = None
            for row in csv.DictReader(f):
                if row.get("movie_cd") == movie_cd and row.get("found") == "1":
                    last_found = row
            if last_found and last_found.get("reservation_rate"):
                own_rate = float(last_found["reservation_rate"])
    own_cum = daily_series[-1]["cum_audi"] if daily_series else (
        latest_snapshot["reservation_audi"] if latest_snapshot else None
    )

    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f'''<meta charset="utf-8">
<title>{target["title"]} 박스오피스 추적</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  /* 퓨리어스 공식 포스터(딥 틸 + 레드) 톤을 라이트/다크 구분 없이 고정 테마로 사용.
     series-1(레드)=대상 영화, series-2(틸)=벤치마크/비교. 증감 상태색(up/down)은
     브랜드 레드와 헷갈리지 않도록 별도 색(초록/주황)으로 분리했다.
     six-checks validator는 Node.js가 필요해 이 환경에서 실행하지 못했고,
     알려진 대비 기준(WCAG AA 근접)에 맞춰 수동으로 골랐다. */
  :root {{
    color-scheme: dark;
    --page-bg: #0c1918; --surface-1: #142826;
    --text-primary: #f5f7f6; --text-secondary: #b9c4c2; --text-muted: #8fa19e;
    --gridline: #24413e; --border: rgba(255,255,255,0.10);
    --series-1: #e0574c; --series-2: #2dd4bf;
    --delta-up: #2ea043; --delta-down: #e0972f;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    background: var(--page-bg); color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", "Malgun Gothic", sans-serif;
    margin: 0; padding: 32px 16px 64px;
  }}
  .wrap {{ max-width: 960px; margin: 0 auto; position: relative; z-index: 1; }}
  /* 키아트(furious-keyart.jpg)에서 인물별로 잘라 보여준다. 원본 3840x2160 기준,
     100vh 높이로 스케일했을 때 왼쪽 인물은 0px, 오른쪽 인물은 -500px 오프셋에서
     각각 가장 깔끔하게 잡혀 crop_test.html로 확인 후 고정했다(뷰포트 높이 비례를
     위해 px 대신 vh 단위: -500/900 ≈ -55.6vh). */
  .side-art {{
    position: fixed; top: 0; height: 100vh; width: 300px; overflow: hidden;
    z-index: 0; opacity: 0.85; pointer-events: none;
  }}
  .side-art img {{ position: absolute; top: 0; height: 100vh; width: auto; max-width: none; }}
  .side-art--left {{
    left: 0;
    -webkit-mask-image: linear-gradient(to right, black 55%, transparent 100%);
    mask-image: linear-gradient(to right, black 55%, transparent 100%);
  }}
  .side-art--left img {{ left: 0; }}
  .side-art--right {{
    right: 0;
    -webkit-mask-image: linear-gradient(to left, black 55%, transparent 100%);
    mask-image: linear-gradient(to left, black 55%, transparent 100%);
  }}
  .side-art--right img {{ left: -55.6vh; }}
  @media (max-width: 1400px) {{ .side-art {{ display: none; }} }}
  header {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 4px; }}
  h1 {{ font-size: 1.5rem; font-weight: 600; margin: 0; letter-spacing: -0.01em; }}
  .title-logo {{ height: 40px; width: auto; display: block; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 0.75rem; font-weight: 600; }}
  .badge--upcoming {{ background: color-mix(in srgb, var(--series-1) 16%, transparent); color: var(--series-1); }}
  .badge--live {{ background: color-mix(in srgb, var(--delta-up) 16%, transparent); color: var(--delta-up); }}
  .updated {{ color: var(--text-muted); font-size: 0.82rem; margin: 4px 0 28px; }}

  .hero {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px; margin-bottom: 28px; }}
  .hero-card {{
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 14px;
    padding: 20px 22px; box-shadow: 0 1px 2px rgba(0,0,0,0.04);
  }}
  .hero-label {{ color: var(--text-secondary); font-size: 0.83rem; margin-bottom: 8px; }}
  .hero-value {{ font-size: 2rem; font-weight: 600; letter-spacing: -0.02em; line-height: 1.1; }}
  .hero-sub {{ color: var(--text-muted); font-size: 0.8rem; margin-top: 6px; }}
  .delta-up {{ color: var(--delta-up); font-weight: 600; }}
  .delta-down {{ color: var(--delta-down); font-weight: 600; }}

  .comments {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 32px; }}
  .comment-card {{ background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px; }}
  .comment-title {{ font-size: 0.78rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 8px; }}
  .comment-body {{ font-size: 0.92rem; line-height: 1.55; color: var(--text-primary); }}

  .section {{ margin-bottom: 36px; }}
  .section h2 {{ font-size: 1rem; font-weight: 600; margin: 0 0 14px; color: var(--text-primary); }}
  .card {{ background: var(--surface-1); border: 1px solid var(--border); border-radius: 14px; padding: 18px 20px 10px; }}

  .chart-svg {{ width: 100%; height: auto; overflow: visible; display: block; }}
  .gridline {{ stroke: var(--gridline); stroke-width: 1; }}
  .axis-label, .point-label {{ font-size: 10px; fill: var(--text-muted); }}
  .point-label {{ font-weight: 600; fill: var(--text-primary); }}
  .chart-empty {{ color: var(--text-muted); font-size: 0.85rem; padding: 44px 0; text-align: center; }}
  .hover-dot {{ cursor: pointer; }}
  .hover-dot:hover {{ fill: var(--series-1); opacity: 0.18; }}

  .chart-legend {{ display: flex; gap: 18px; margin-bottom: 10px; font-size: 0.82rem; color: var(--text-secondary); }}
  .legend-item {{ display: inline-flex; align-items: center; gap: 6px; }}
  .legend-swatch {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  .legend-swatch--dashed {{ background: transparent; border: 2px dashed; border-radius: 0; width: 12px; height: 0; }}

  .cmp-table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  .cmp-table th {{ text-align: left; font-size: 0.78rem; font-weight: 600; color: var(--text-muted); padding: 0 8px 10px; border-bottom: 1px solid var(--gridline); }}
  .cmp-table th:not(:first-child), .cmp-table td:not(:first-child) {{ text-align: right; }}
  .cmp-table td {{ padding: 10px 8px; border-bottom: 1px solid var(--gridline); }}
  .cmp-table tr:last-child td {{ border-bottom: none; }}
  .cmp-num {{ font-variant-numeric: tabular-nums; color: var(--text-primary); }}
  .cmp-title {{ color: var(--text-primary); }}
  .cmp-row--own {{ font-weight: 700; }}
  .cmp-row--own .cmp-title::before {{ content: "●"; color: var(--series-1); margin-right: 7px; font-size: 0.7rem; }}
  .cmp-pending {{ color: var(--text-muted); font-size: 0.76rem; margin-left: 8px; font-weight: 400; }}

  footer {{ color: var(--text-muted); font-size: 0.76rem; margin-top: 44px; line-height: 1.6; }}

  @media (max-width: 600px) {{
    .hero, .comments {{ grid-template-columns: 1fr; }}
  }}
</style>
{side_art_html}
<div class="wrap">
  <header>
    {title_html}
    {status_badge}
  </header>
  <div class="updated">박스오피스 실시간 추적 · 최종 갱신 {generated_at}</div>

  <div class="hero">
    <div class="hero-card">
      <div class="hero-label">예매관객수</div>
      <div class="hero-value">{hero_reservation}명</div>
      {reservation_sub}
    </div>
    <div class="hero-card">
      <div class="hero-label">오늘 최종 관객 예측</div>
      <div class="hero-value">{hero_today}명</div>
    </div>
    <div class="hero-card">
      <div class="hero-label">개봉 최종 총관객 전망</div>
      <div class="hero-value">{hero_final}명</div>
      {target_pct}
    </div>
  </div>

  <div class="comments">
    <div class="comment-card"><div class="comment-title">현황</div><div class="comment-body">{ai_comment.get("현황","")}</div></div>
    <div class="comment-card"><div class="comment-title">최종전망</div><div class="comment-body">{ai_comment.get("최종전망","")}</div></div>
    <div class="comment-card"><div class="comment-title">주말</div><div class="comment-body">{ai_comment.get("주말","")}</div></div>
    <div class="comment-card"><div class="comment-title">신규수요분해</div><div class="comment-body">{ai_comment.get("신규수요분해","")}</div></div>
  </div>

  <div class="section">
    <h2>{hourly_chart_title}</h2>
    <div class="card">{svg_line_chart(hourly_series, tooltips=hourly_tooltips)}</div>
  </div>

  <div class="section">
    <h2>일별 누적 관객</h2>
    <div class="card">{svg_line_chart(cum_series)}</div>
  </div>

  <div class="section">
    <h2>회당 평균 관객 (상영 규모 대비, 좌석판매율 근사)</h2>
    <div class="card">{svg_line_chart(avg_per_show_series, value_fmt=lambda v: f"{v:,.1f}")}</div>
  </div>

  <div class="section">
    <h2>벤치마크 비교 (개봉일 기준 경과일, 누적 관객)</h2>
    <div class="card">{svg_multi_line_chart(target_by_offset, benchmark_by_offset, target["title"], benchmark.get("title","벤치마크"))}</div>
  </div>

  <div class="section">
    <h2>경쟁작 현황</h2>
    <div class="card">{competitor_table(competitor_rows, target["title"], own_rate, own_cum)}</div>
  </div>

  <footer>
    본 페이지의 수치는 KOBIS 공개 데이터 및 공식 Open API에서 자동 수집·추정한 것으로, 실제 결과와 다를 수 있습니다.
  </footer>
</div>
'''
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    build()
