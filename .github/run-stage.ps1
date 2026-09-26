# Запускает собранный exe в режиме самопроверки, не даёт сборке зависнуть
# и складывает всё сказанное программой в общий файл диагностики.
param([int]$Stage = 0, [string]$Name = "NOMEROK-3D-console", [int]$TimeoutSec = 120)

$ErrorActionPreference = "Stop"
$exe = Get-ChildItem dist -Filter "$Name.exe" | Select-Object -First 1
if (-not $exe) { throw "не найден dist\$Name.exe" }

$diag = Join-Path $env:RUNNER_TEMP "diag.txt"
$out  = Join-Path $env:RUNNER_TEMP "stage$Stage.out.txt"
$err  = Join-Path $env:RUNNER_TEMP "stage$Stage.err.txt"
$log  = Join-Path $env:TEMP "nomerok3d.log"
Remove-Item $log, $out, $err -ErrorAction SilentlyContinue

$head = "=== стадия $Stage | $($exe.Name) | $([math]::Round($exe.Length/1MB,1)) МБ ==="
$head | Tee-Object -FilePath $diag -Append

$sw = [Diagnostics.Stopwatch]::StartNew()
$p = Start-Process -FilePath $exe.FullName -ArgumentList "--selftest", "$Stage" -PassThru `
                   -RedirectStandardOutput $out -RedirectStandardError $err
$done = $p.WaitForExit($TimeoutSec * 1000)
$sw.Stop()

$text = @("время: $([math]::Round($sw.Elapsed.TotalSeconds,1)) с, завершился: $done")
foreach ($f in @(@("stdout", $out), @("stderr", $err), @("лог", $log))) {
  if (Test-Path $f[1]) {
    $c = (Get-Content $f[1] -Raw -ErrorAction SilentlyContinue)
    if ($c) { $text += "--- $($f[0]) ---"; $text += $c.Trim() }
  }
}
if (-not (Test-Path $log)) { $text += "!!! лог приложения не появился — упало до первой строки" }
$text | Tee-Object -FilePath $diag -Append

if (-not $done) { try { $p.Kill() } catch {}; throw "стадия $Stage зависла ($TimeoutSec с)" }
if ($p.ExitCode -ne 0) { throw "стадия $Stage вернула код $($p.ExitCode)" }
"стадия $Stage пройдена"
