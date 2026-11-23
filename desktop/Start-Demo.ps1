$ErrorActionPreference = 'Stop'
$liliDemoRoot = $PSScriptRoot
$liliPython = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $liliPython) { $liliPython = (Get-Command python.exe -ErrorAction Stop).Source }
$liliRuntime = Join-Path $liliDemoRoot '.runtime'
New-Item -ItemType Directory -Path $liliRuntime -Force | Out-Null
Start-Process -FilePath $liliPython -ArgumentList @('"' + (Join-Path $liliDemoRoot 'main.py') + '"') -WorkingDirectory $liliDemoRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $liliRuntime 'app.log') -RedirectStandardError (Join-Path $liliRuntime 'error.log')
