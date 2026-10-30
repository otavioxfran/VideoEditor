<#
.SYNOPSIS
  Builds the Windows installer: embedded Python + dependencies + FFmpeg + the app.

.DESCRIPTION
  Produces build\windows\stage (the files the installer copies) and, when Inno Setup's
  ISCC.exe is available, build\windows\VideoEditor-Setup-<version>.exe.
  Run from the repository root with a Python 3.11 on PATH (used only to fetch wheels):

      powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
#>
param(
    [string]$PythonVersion = '3.11.9',
    [string]$Out = 'build\windows',
    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $Root

$Version = (Select-String -Path pyproject.toml -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
$OutDir = Join-Path $Root $Out
$Stage = Join-Path $OutDir 'stage'
$Cache = Join-Path $OutDir 'downloads'
$Gpu = Join-Path $OutDir 'gpu'
Write-Host "VideoEditor $Version -> $OutDir"

if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
if (Test-Path $Gpu) { Remove-Item $Gpu -Recurse -Force }
New-Item -ItemType Directory -Force $Stage, $Cache, $Gpu | Out-Null

function Get-File($Url, $Name) {
    $path = Join-Path $Cache $Name
    if (-not (Test-Path $path)) {
        Write-Host "Downloading $Url"
        Invoke-WebRequest -Uri $Url -OutFile $path -UseBasicParsing
    }
    return $path
}

# 1. Embedded Python (official python.org build; does not touch any system Python).
$pyZip = Get-File "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" "python-$PythonVersion-embed-amd64.zip"
$python = Join-Path $Stage 'python'
Expand-Archive $pyZip -DestinationPath $python -Force
$pth = Get-ChildItem $python -Filter 'python3*._pth' | Select-Object -First 1
$tag = $pth.BaseName
Set-Content -Path $pth.FullName -Encoding ascii -Value @("$tag.zip", '.', 'Lib\site-packages', '..\app', 'import site')

# 2. Dependencies as Windows wheels, installed straight into the embedded Python.
$site = Join-Path $python 'Lib\site-packages'
$short = ($PythonVersion -split '\.')[0..1] -join ''
python -m pip install --disable-pip-version-check --no-compile --target $site `
    --only-binary=:all: --platform win_amd64 --python-version $short --implementation cp `
    -r requirements.txt -r requirements-editorial.txt
if ($LASTEXITCODE) { throw 'pip install failed' }

# 3. The CTranslate2 wheel loads cuBLAS but does not ship it. NVIDIA's own wheel provides it
#    (12.4 keeps older drivers working); it becomes the optional "GPU" component, copied next
#    to ctranslate2.dll so Windows finds it without touching PATH.
$cublasTmp = Join-Path $OutDir 'cublas-tmp'
if (Test-Path $cublasTmp) { Remove-Item $cublasTmp -Recurse -Force }
python -m pip install --disable-pip-version-check --no-compile --no-deps --target $cublasTmp `
    --only-binary=:all: --platform win_amd64 --python-version $short --implementation cp `
    'nvidia-cublas-cu12==12.4.5.8'
if ($LASTEXITCODE) { throw 'cuBLAS download failed' }
New-Item -ItemType Directory -Force (Join-Path $Gpu 'ctranslate2') | Out-Null
Get-ChildItem (Join-Path $cublasTmp 'nvidia\cublas\bin') -Filter '*.dll' | Copy-Item -Destination (Join-Path $Gpu 'ctranslate2')
Copy-Item (Join-Path $cublasTmp 'nvidia\cublas\LICENSE*') (Join-Path $Gpu 'ctranslate2') -ErrorAction SilentlyContinue
Remove-Item $cublasTmp -Recurse -Force
if (-not (Test-Path (Join-Path $Gpu 'ctranslate2\cublas64_12.dll'))) { throw 'cublas64_12.dll missing from the GPU component' }

# 4. FFmpeg (gyan.dev release "essentials" build, GPL), checked against its published SHA-256.
$ffZip = Get-File 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' 'ffmpeg-release-essentials.zip'
$expected = (Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip.sha256' -UseBasicParsing).Content.Trim().Split(' ')[0]
$actual = (Get-FileHash $ffZip -Algorithm SHA256).Hash
if ($actual -ne $expected.ToUpper()) {
    Remove-Item $ffZip
    throw "FFmpeg checksum mismatch ($actual vs $expected); downloaded file removed"
}
$ffTmp = Join-Path $OutDir 'ffmpeg-tmp'
if (Test-Path $ffTmp) { Remove-Item $ffTmp -Recurse -Force }
Expand-Archive $ffZip -DestinationPath $ffTmp -Force
$ffRoot = Get-ChildItem $ffTmp -Directory | Select-Object -First 1
$ffBin = Join-Path $Stage 'ffmpeg\bin'
New-Item -ItemType Directory -Force $ffBin | Out-Null
Copy-Item (Join-Path $ffRoot.FullName 'bin\ffmpeg.exe'), (Join-Path $ffRoot.FullName 'bin\ffprobe.exe') $ffBin
Copy-Item (Join-Path $ffRoot.FullName 'LICENSE') (Join-Path $Stage 'ffmpeg\LICENSE.txt')
Copy-Item (Join-Path $ffRoot.FullName 'README.txt') (Join-Path $Stage 'ffmpeg\README.txt')
Remove-Item $ffTmp -Recurse -Force

# 5. The app itself (no tests, no local configuration, no caches).
$app = Join-Path $Stage 'app'
New-Item -ItemType Directory -Force $app | Out-Null
foreach ($item in 'video_editor', 'ui', 'config') {
    Copy-Item $item (Join-Path $app $item) -Recurse
}
Get-ChildItem $app -Recurse -Force -Include '__pycache__', '*.local.json', '*.pyc' | Remove-Item -Recurse -Force
foreach ($file in 'cli.py', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md', 'CHANGELOG.md', 'PERSONA.example.md') {
    Copy-Item $file $app
}
Copy-Item (Join-Path $PSScriptRoot 'launcher.py') $app

# 6. Smoke test: the embedded Python imports the whole app with GPU libraries absent.
$env:VIDEOEDITOR_DATA_DIR = Join-Path $OutDir 'smoke-data'
& (Join-Path $python 'python.exe') -c "import cli, faster_whisper, cv2, ctranslate2, video_editor.server.app; print('smoke ok', ctranslate2.__version__)"
if ($LASTEXITCODE) { throw 'smoke test failed' }
Remove-Item Env:VIDEOEDITOR_DATA_DIR
Remove-Item (Join-Path $OutDir 'smoke-data') -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $Stage -Recurse -Force -Include '__pycache__' | Remove-Item -Recurse -Force

$size = (Get-ChildItem $Stage -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
$gpuSize = (Get-ChildItem $Gpu -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("Stage: {0:N0} MB, GPU component: {1:N0} MB" -f $size, $gpuSize)

# 7. Installer.
if ($SkipInstaller) { return }
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Warning 'Inno Setup (ISCC.exe) not found; the stage folder is ready but no installer was built.'
    return
}
& $iscc "/DAppVersion=$Version" "/DStageDir=$Stage" "/DGpuDir=$Gpu" "/DOutputDir=$OutDir" (Join-Path $PSScriptRoot 'VideoEditor.iss')
if ($LASTEXITCODE) { throw 'ISCC failed' }
Get-ChildItem $OutDir -Filter 'VideoEditor-Setup-*.exe' | ForEach-Object { Write-Host "Installer: $($_.FullName) ($([math]::Round($_.Length / 1MB)) MB)" }
