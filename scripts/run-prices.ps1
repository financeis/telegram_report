<#
.SYNOPSIS
  매일 주가 스냅샷 갱신: 저장소 폴더에서 `python -m research_desk prices update`를 한 번 실행한다.

.DESCRIPTION
  윈도우 작업 스케줄러가 평일 장 마감 뒤(기본 18:30) 부르는 스크립트다. 스케줄러 등록은 따로 한다.
  저장소 폴더(이 스크립트의 위 폴더)로 옮겨 메인 .venv 파이썬으로 명령을 실행하고, 그 종료 코드를
  그대로 돌려준다. 명령이 저장소 폴더 기준의 상대 경로(종목표 기본 경로 등)를 쓰기 때문에 폴더를 옮긴다.

  종료 코드 (명령과 같다):
    0 = 갱신 성공. ok(모두 받음) 또는 partial(못 받은 종목이 20% 이하, 그 종목은 이전 값 유지)
    1 = 갱신 실패. failed(못 받은 종목이 20%를 넘어 스냅샷을 바꾸지 않음) 또는 실행 중 오류
    4 = 준비 문제. DB 접속 설정, KIS_APP_KEY / KIS_APP_SECRET, 종목표 중 하나가 없다.
        명령이 출력한 안내대로 고친 뒤 다시 실행한다. 파이썬을 찾지 못했을 때도 4다(명령은 실행하지 않음).

  KIS 키는 사용자의 다른 프로젝트(masterdb)와 같은 키다. 두 쪽의 KIS 수집을 같은 시간에 돌리지 않는다.

.PARAMETER Python
  python.exe 경로. 비우면 $env:RESEARCH_DESK_PY, 이 스크립트 위 폴더의 .venv\Scripts\python.exe,
  메인 작업 폴더(`git rev-parse --git-common-dir`의 상위 폴더)의 .venv\Scripts\python.exe 순서로 찾는다.
  그래서 별도 작업 폴더(git worktree)에서도 메인 작업 폴더의 .venv를 쓴다.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run-prices.ps1
#>
[CmdletBinding()]
param(
    [string]$Python = ""
)

# PS 5.1 wraps a native exe's stderr as ErrorRecord; with Stop that makes a
# warning line abort the script. Use Continue and rely on $LASTEXITCODE.
$ErrorActionPreference = "Continue"

# The repository folder (scripts\run-prices.ps1 -> repository)
$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $RepoRoot

function Get-MainWorkingFolder {
    # The main working folder (the clone itself, not a linked worktree) is the
    # parent of git's common dir. git prints that dir relative to the folder it
    # runs in (".git" in the main folder) or absolute (in a linked worktree), so
    # resolve it against $Root. Returns $null quietly when git is missing or fails.
    param([string]$Root)
    if (-not (Get-Command git -CommandType Application -ErrorAction SilentlyContinue)) { return $null }
    try {
        $out = @(& git -C $Root rev-parse --git-common-dir 2>$null)
        if ($LASTEXITCODE -ne 0 -or $out.Count -eq 0 -or -not $out[0]) { return $null }
        $commonDir = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($Root, ([string]$out[0]).Trim()))
        return (Split-Path -Parent $commonDir)
    } catch {
        return $null
    }
}

# Python: -Python > RESEARCH_DESK_PY > .venv in this folder > the main working folder's .venv
if (-not $Python) { $Python = $env:RESEARCH_DESK_PY }
if (-not $Python) {
    $cand = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $cand) { $Python = $cand }
}
if (-not $Python) {
    $mainFolder = Get-MainWorkingFolder -Root $RepoRoot
    if ($mainFolder) {
        $cand = Join-Path $mainFolder ".venv\Scripts\python.exe"
        if (Test-Path $cand) { $Python = $cand }
    }
}
if (-not $Python -or -not (Test-Path $Python)) {
    Write-Host "파이썬을 찾지 못해 주가 갱신을 실행하지 않았습니다. -Python <python.exe 경로>를 주거나 RESEARCH_DESK_PY를 설정하세요."
    exit 4
}

Write-Host "주가 갱신 시작: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "folder: $RepoRoot"
Write-Host "python: $Python"

# A run that could not even start leaves no exit code: count it as a failure.
$global:LASTEXITCODE = $null
& $Python -u -m research_desk prices update
$code = $LASTEXITCODE
if ($null -eq $code) { $code = 1 }

if ($code -eq 0) {
    Write-Host "주가 갱신이 끝났습니다."
} elseif ($code -eq 4) {
    Write-Host "준비 문제로 주가를 갱신하지 않았습니다. 위 안내를 따른 뒤 다시 실행하세요."
} else {
    Write-Host "주가 갱신이 실패했습니다(종료 코드 $code). 위 메시지를 확인하세요."
}
exit $code
