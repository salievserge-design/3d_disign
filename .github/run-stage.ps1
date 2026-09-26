# Запускает собранный exe в режиме самопроверки и не даёт сборке зависнуть.
param([int]$Stage = 0, [int]$TimeoutSec = 180)

$ErrorActionPreference = "Stop"
$exe = Get-ChildItem dist -Filter *.exe | Select-Object -First 1
if (-not $exe) { throw "exe не собрался" }
"Проверяю $($exe.Name) ($([math]::Round($exe.Length/1MB,1)) МБ), стадия $Stage"

$log = Join-Path $env:TEMP "nomerok3d.log"
Remove-Item $log -ErrorAction SilentlyContinue

$p = Start-Process -FilePath $exe.FullName -ArgumentList "--selftest", "$Stage" -PassThru
$done = $p.WaitForExit($TimeoutSec * 1000)

if (Test-Path $log) { "--- лог приложения ---"; Get-Content $log }
else { "!!! лог не появился — приложение умерло на самом старте" }

if (-not $done) {
  try { $p.Kill() } catch {}
  throw "стадия $Stage зависла: exe не завершился за $TimeoutSec с"
}
if ($p.ExitCode -ne 0) { throw "стадия $Stage вернула код $($p.ExitCode)" }
"стадия $Stage пройдена"
