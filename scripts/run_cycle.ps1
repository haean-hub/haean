# 매시간 실행: 수집(실시간예매/일별박스오피스) -> 대시보드 빌드 -> git commit/push
# Windows 작업 스케줄러에서 이 스크립트를 호출한다.
# 콘솔 인코딩 문제 방지를 위해 진행 로그는 파일로만 남기고, 화면 출력·커밋 메시지는 영문으로 고정한다.
# 네이티브 명령(git)의 stderr는 정상 출력에도 섞여 나오므로 2>&1로 감싸지 않고 $LASTEXITCODE로만 판단한다.

$root = Split-Path -Parent $PSScriptRoot
$logFile = Join-Path $root "logs\run_cycle.log"

function Write-Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ss') $msg"
    Add-Content -Path $logFile -Value $line -Encoding utf8
}

Set-Location $root

# 작업 스케줄러의 세션 컨텍스트에서는 %LOCALAPPDATA%가 대화형 로그인 때와 다르게
# 풀리는 경우가 있어(모니터만 끄고 로그아웃은 안 한 상태 등), Playwright가 브라우저
# 실행파일을 못 찾는 문제가 3일간 조용히 발생했었다(collect_competitors.py, collect_company_stat.py
# 둘 다 exit=0인데 실제로는 매 사이클 재시도 끝에 실패). 경로를 고정 지정해서 이 의존성을 없앤다.
$env:PLAYWRIGHT_BROWSERS_PATH = "C:\Users\admin\AppData\Local\ms-playwright"

python scripts\collect_hourly.py
Write-Log "collect_hourly.py exit=$LASTEXITCODE"

python scripts\collect_daily.py
Write-Log "collect_daily.py exit=$LASTEXITCODE"

python scripts\collect_company_stat.py
Write-Log "collect_company_stat.py exit=$LASTEXITCODE"

python scripts\collect_competitors.py
Write-Log "collect_competitors.py exit=$LASTEXITCODE"

python scripts\build_dashboard.py
Write-Log "build_dashboard.py exit=$LASTEXITCODE"

git add -A -- data index.html ai_comment.json
$hasChanges = git status --porcelain
if ([string]::IsNullOrWhiteSpace($hasChanges)) {
    Write-Log "no changes, skip commit/push"
    exit 0
}

$commitMsg = "auto: data update $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
git commit -m $commitMsg | Out-Null
Write-Log "committed: $commitMsg"

$pushed = $false
for ($i = 0; $i -lt 3; $i++) {
    git push
    if ($LASTEXITCODE -eq 0) {
        $pushed = $true
        break
    }
    Write-Log "push failed (attempt $($i+1)), trying pull --rebase"
    git pull --rebase
    Start-Sleep -Seconds 5
}

if ($pushed) {
    Write-Log "push OK"
} else {
    Write-Log "push FAILED after retries"
}
