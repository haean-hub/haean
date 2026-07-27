# 박스오피스 실시간 추적 대시보드

KOBIS 데이터로 특정 영화의 예매량·일일 관객·좌석판매율을 추적하고, 오늘 최종 관객수와 개봉 최종 누적 관객수(스코어)를 예측하는 개인/공개용 대시보드.

## 현재 상태

담당 영화가 아직 KOBIS에 등록되지 않아, `config/film_config.json`의 `target_movie`에는 구조 검증용 예시 벤치마크 영화가 들어가 있습니다. 담당 영화가 KOBIS에 movie_cd로 등록되면 `own_movie` 블록을 채운 뒤 `target_movie`와 교체하세요.

## 폴더 구조

```
config/
  film_config.json          영화명/movie_cd/개봉일/목표관객/벤치마크 설정
  credentials.example.json  자격증명 입력 템플릿 (커밋됨)
  credentials.json          실제 KOBIS API키·로그인 (gitignore, 직접 생성 필요)
data/
  hourly.csv                실시간 예매 수집 결과
  member_snapshots.csv      정밀 일별 관객 수집 결과 (로그인 필요)
  schedule_history.json     (선택) 편성표
scripts/
  kobis_client.py           KOBIS Open API 공통 클라이언트
  collect_hourly.py         실시간 예매 수집
  collect_daily.py          상영관 통계(로그인) 수집
  predict.py                예측 로직 (5개 가드 포함)
  build_dashboard.py        index.html 생성
index.html                  대시보드 산출물 (GitHub Pages 소스)
```

## 처음 세팅하는 법

1. `config/credentials.example.json`을 복사해 `config/credentials.json`으로 만들고, 본인 KOBIS API 키와 상영관 통계 로그인 정보를 직접 입력합니다. 이 파일은 절대 커밋하지 마세요(.gitignore에 이미 포함됨).
2. `scripts/kobis_client.py`로 연결 테스트 후, `config/film_config.json`의 benchmark_movies / target_movie의 movie_cd를 실제 값으로 채웁니다.
3. 이후 각 수집 스크립트 → `predict.py` → `build_dashboard.py` 순으로 실행하며 진행 상황을 확인합니다.

## 보안 원칙

- 자격증명은 `config/credentials.json`에만 두고 절대 커밋하지 않습니다.
- 대시보드에 올라가는 문구는 수집 데이터·공개 정보로 유추 가능한 내용만 사용합니다(내부 비공개 배급 정보 금지).
