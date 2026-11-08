$ErrorActionPreference = 'Stop'
$hostDir = Join-Path $env:LOCALAPPDATA 'DQ-youtube-history-renamer'
$buildDir = Join-Path $env:TEMP ('dq-history-renamer-build-' + $PID)
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
  throw 'Python 3.11 or newer was not found.'
}
$python = $pythonCommand.Source
$null = & $python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host 'Installing PyInstaller for the first-time native host setup...'
  & $python -m pip install --user pyinstaller
  if ($LASTEXITCODE -ne 0) { throw 'PyInstaller installation failed. Check network access and Python permissions.' }
}
New-Item -ItemType Directory -Force $hostDir | Out-Null
New-Item -ItemType Directory -Force $buildDir | Out-Null
Push-Location $PSScriptRoot
try {
  & $python -m PyInstaller --noconfirm --clean --onefile --name native_host `
    --distpath (Join-Path $buildDir 'dist') --workpath (Join-Path $buildDir 'build') `
    --specpath $buildDir native_host.py | Out-Null
}
finally { Pop-Location }
$installedExe = Join-Path $hostDir ('native_host-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $PID + '.exe')
Copy-Item (Join-Path $buildDir 'dist\native_host.exe') $installedExe -Force
$manifest = Get-Content (Join-Path $PSScriptRoot 'native_host_manifest.json.in') -Raw
$nativePath = $installedExe.Replace('\','\\')
$manifest = $manifest.Replace('native_host.exe', $nativePath)
$manifestPath = Join-Path $hostDir 'host-manifest.json'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($manifestPath, $manifest, $utf8NoBom)
New-Item -Path 'HKCU:\Software\Google\Chrome\NativeMessagingHosts\com.dq.history_rename' -Force | Out-Null
Set-ItemProperty -Path 'HKCU:\Software\Google\Chrome\NativeMessagingHosts\com.dq.history_rename' -Name '(default)' -Value $manifestPath
Write-Host 'Native host installation completed. Reload the extension.'
