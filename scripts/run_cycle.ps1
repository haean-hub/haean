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

python scripts\collect_hourly.py
Write-Log "collect_hourly.py exit=$LASTEXITCODE"

python scripts\collect_daily.py
Write-Log "collect_daily.py exit=$LASTEXITCODE"

python scripts\collect_competitors.py
Write-Log "collect_competitors.py exit=$LASTEXITCODE"

python scripts\collect_own_seat_data.py
Write-Log "collect_own_seat_data.py exit=$LASTEXITCODE"

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
