"""수집 데이터 + predict.py 예측치를 읽어 자체완결 index.html 대시보드를 생성한다.

외부 CDN 의존 없이 인라인 CSS/SVG만 사용한다(레퍼런스 프롬프트 요구사항).
"""
import csv
import datetime
import json
from pathlib import Path

from predict import (
    BENCHMARK_HISTORY, MEMBER_SNAPSHOTS, HOURLY,
    load_daily_series, load_hourly_series, predict_today_final, predict_opening_final,
)

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "film_config.json"
AI_COMMENT_PATH = ROOT / "ai_comment.json"
OUTPUT_PATH = ROOT / "index.html"

DEFAULT_AI_COMMENT = {
    "현황": "아직 코멘트가 생성되지 않았습니다.",
    "최종전망": "아직 코멘트가 생성되지 않았습니다.",
    "주말": "아직 코멘트가 생성되지 않았습니다.",
    "신규수요분해": "아직 코멘트가 생성되지 않았습니다.",
}


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


def svg_line_chart(series: list, width=640, height=220, color="var(--accent)", value_fmt=fmt_num) -> str:
    """series: [(label, value), ...]. 값이 없으면 빈 차트 안내를 그린다."""
    pts = [(l, v) for l, v in series if v is not None]
    if len(pts) < 2:
        return f'<div class="chart-empty">데이터가 아직 충분하지 않습니다 ({len(pts)}건)</div>'

    pad_l, pad_r, pad_t, pad_b = 40, 16, 16, 28
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    values = [v for _, v in pts]
    vmin, vmax = min(values), max(values)
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

    # x축 라벨: 처음/중간/끝만
    label_idxs = sorted(set([0, len(pts) // 2, len(pts) - 1]))
    labels_svg = "".join(
        f'<text x="{x_of(i):.1f}" y="{height-8}" class="axis-label" text-anchor="middle">{pts[i][0]}</text>'
        for i in label_idxs
    )

    last_label, last_val = pts[-1]
    return f'''<svg viewBox="0 0 {width} {height}" class="chart-svg" role="img" aria-label="추이 차트">
  <path d="{area_d}" fill="{color}" opacity="0.12" stroke="none"></path>
  <path d="{path_d}" fill="none" stroke="{color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"></path>
  <circle cx="{x_of(len(pts)-1):.1f}" cy="{y_of(last_val):.1f}" r="4" fill="{color}"></circle>
  <text x="{x_of(len(pts)-1):.1f}" y="{y_of(last_val)-10:.1f}" class="point-label" text-anchor="end">{value_fmt(last_val)}</text>
  {labels_svg}
</svg>'''


def svg_multi_line_chart(series_a: list, series_b: list, name_a: str, name_b: str,
                           width=640, height=240, color_a="var(--accent)", color_b="var(--muted)") -> str:
    pts_a = [(x, v) for x, v in series_a if v is not None]
    pts_b = [(x, v) for x, v in series_b if v is not None]
    if len(pts_a) < 2 or len(pts_b) < 2:
        return f'<div class="chart-empty">비교할 데이터가 아직 충분하지 않습니다</div>'

    pad_l, pad_r, pad_t, pad_b = 40, 16, 16, 28
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    all_x = sorted(set([x for x, _ in pts_a] + [x for x, _ in pts_b]))
    xmin, xmax = min(all_x), max(all_x)
    all_v = [v for _, v in pts_a] + [v for _, v in pts_b]
    vmin, vmax = 0, max(all_v)
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

    return f'''<svg viewBox="0 0 {width} {height}" class="chart-svg" role="img" aria-label="비교 차트">
  <path d="{path_for(pts_a)}" fill="none" stroke="{color_a}" stroke-width="2.5"></path>
  <path d="{path_for(pts_b)}" fill="none" stroke="{color_b}" stroke-width="2" stroke-dasharray="5,4"></path>
  <text x="{pad_l}" y="16" class="legend" fill="{color_a}">● {name_a}</text>
  <text x="{pad_l+120}" y="16" class="legend" fill="{color_b}">--- {name_b}</text>
</svg>'''


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
    hourly_series = [(h["ts"].strftime("%H:%M"), h["cum_audi"]) for h in hourly_today]
    avg_per_show_series = [
        (r["date"].strftime("%m/%d"), (r["daily_audi"] / r["show_cnt"]) if r["show_cnt"] else None)
        for r in daily_series
    ]

    # 대상 vs 벤치마크: day_offset 기준 누적 비교
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

    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f'''<meta charset="utf-8">
<title>{target["title"]} 박스오피스 추적</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{
    --bg: #ffffff; --fg: #1a1a1a; --muted: #6b7280; --accent: #2563eb;
    --card-bg: #f8fafc; --border: #e5e7eb;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #0f1115; --fg: #e5e7eb; --muted: #9ca3af; --accent: #60a5fa; --card-bg: #1a1d23; --border: #2a2e37; }}
  }}
  :root[data-theme="dark"] {{ --bg: #0f1115; --fg: #e5e7eb; --muted: #9ca3af; --accent: #60a5fa; --card-bg: #1a1d23; --border: #2a2e37; }}
  :root[data-theme="light"] {{ --bg: #ffffff; --fg: #1a1a1a; --muted: #6b7280; --accent: #2563eb; --card-bg: #f8fafc; --border: #e5e7eb; }}
  * {{ box-sizing: border-box; }}
  body {{ background: var(--bg); color: var(--fg); font-family: -apple-system, "Malgun Gothic", sans-serif; margin: 0; padding: 24px 16px 60px; }}
  .wrap {{ max-width: 920px; margin: 0 auto; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 4px; }}
  .updated {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 24px; }}
  .hero {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 28px; }}
  .hero-card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }}
  .hero-label {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 6px; }}
  .hero-value {{ font-size: 1.9rem; font-weight: 700; }}
  .hero-sub {{ color: var(--muted); font-size: 0.8rem; margin-top: 4px; }}
  .comments {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 28px; }}
  .comment-card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }}
  .comment-title {{ font-size: 0.8rem; color: var(--muted); margin-bottom: 6px; }}
  .comment-body {{ font-size: 0.92rem; line-height: 1.5; }}
  .section {{ margin-bottom: 32px; }}
  .section h2 {{ font-size: 1.05rem; margin-bottom: 10px; }}
  .chart-svg {{ width: 100%; height: auto; overflow: visible; }}
  .axis-label, .point-label, .legend {{ font-size: 10px; fill: var(--muted); }}
  .point-label {{ font-weight: 600; fill: var(--fg); }}
  .chart-empty {{ color: var(--muted); font-size: 0.85rem; padding: 40px 0; text-align: center; border: 1px dashed var(--border); border-radius: 8px; }}
  footer {{ color: var(--muted); font-size: 0.75rem; margin-top: 40px; }}
  @media (max-width: 600px) {{ .hero, .comments {{ grid-template-columns: 1fr; }} }}
</style>
<div class="wrap">
  <h1>{target["title"]} 박스오피스 실시간 추적</h1>
  <div class="updated">최종 갱신: {generated_at}</div>

  <div class="hero">
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
    <h2>오늘 시간대별 누적 관객(실시간 예매)</h2>
    {svg_line_chart(hourly_series)}
  </div>

  <div class="section">
    <h2>일별 누적 관객</h2>
    {svg_line_chart(cum_series)}
  </div>

  <div class="section">
    <h2>회당 평균 관객(상영 규모 대비, 좌석판매율 근사)</h2>
    {svg_line_chart(avg_per_show_series, value_fmt=lambda v: f"{v:,.1f}")}
  </div>

  <div class="section">
    <h2>벤치마크 비교 (개봉일 기준 경과일, 누적 관객)</h2>
    {svg_multi_line_chart(target_by_offset, benchmark_by_offset, target["title"], benchmark.get("title","벤치마크"))}
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
