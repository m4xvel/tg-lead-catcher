# Запускает userbot и panel локально (без Docker) как два фоновых процесса.
# Используется задачей Планировщика Windows «при входе в систему» — см. windows-task.md.
# Ничего не спрашивает и не падает при ошибке одного процесса — второй всё равно стартует.

$ErrorActionPreference = "Continue"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$logDir = Join-Path $repoRoot "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

Start-Process -FilePath "python" `
    -ArgumentList "-m", "userbot.main" `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput (Join-Path $logDir "userbot.out.log") `
    -RedirectStandardError (Join-Path $logDir "userbot.err.log") `
    -WindowStyle Hidden

Start-Process -FilePath "python" `
    -ArgumentList "-m", "panel.main" `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput (Join-Path $logDir "panel.out.log") `
    -RedirectStandardError (Join-Path $logDir "panel.err.log") `
    -WindowStyle Hidden
