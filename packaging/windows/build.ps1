<#
  Build Bokin Clip cho Windows vào build\app: Python nhúng (embeddable) của python.org, các thư
  viện (pip), code app và file chạy "BokinClip.exe". Inno Setup (installer.iss) đóng gói thư mục
  này thành bộ cài. Python nhúng vẫn có pip, nên app tự cập nhật yt-dlp sau khi cài.

  Chạy trên Windows (GitHub Actions dùng windows-latest), với một Python đầy đủ cùng phiên bản
  (dùng để dựng wheel cho các gói chỉ có mã nguồn):
      pwsh packaging/windows/build.ps1 -PythonVersion 3.13.8 [-BuildPython python]
#>
param(
    [Parameter(Mandatory)] [string] $PythonVersion,
    [string] $BuildPython = "python"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest chậm hơn rất nhiều khi hiện thanh tiến độ

function Invoke-Native([string] $Exe, [string[]] $Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe lỗi (mã $LASTEXITCODE)" }
}

$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$app = Join-Path $root "build\app"
$py = Join-Path $app "python"
$tmp = Join-Path $root "build\tmp"
Remove-Item -Recurse -Force (Join-Path $root "build") -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $py, $tmp | Out-Null

Write-Host "== Python nhúng $PythonVersion"
$zip = Join-Path $tmp "python-embed.zip"
Invoke-WebRequest "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" -OutFile $zip
Expand-Archive $zip -DestinationPath $py
$short = ($PythonVersion.Split(".")[0..1] -join "")
# Bật site-packages (cho pip) và thêm thư mục app (..) vào sys.path để "import app" chạy được.
Set-Content -Encoding ascii (Join-Path $py "python$short._pth") @("python$short.zip", ".", "Lib\site-packages", "..", "import site")
# desktop.py chỉ tự chạy pip cập nhật yt-dlp khi thấy file đánh dấu này.
New-Item -ItemType File (Join-Path $py "bokin-embedded") | Out-Null

Write-Host "== pip và thư viện"
$getPip = Join-Path $tmp "get-pip.py"
Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip
Invoke-Native "$py\python.exe" @($getPip, "--no-warn-script-location", "-q")
# Python nhúng không tự dựng được gói chỉ có mã nguồn (vd. proxy_tools của pywebview): dựng sẵn
# mọi gói thành wheel bằng Python đầy đủ, rồi cài từ thư mục wheel đó.
$requirements = @("-r", (Join-Path $root "requirements.txt"), "-r", (Join-Path $root "requirements-desktop.txt"))
$wheels = Join-Path $tmp "wheels"
Invoke-Native $BuildPython (@("-m", "pip", "wheel", "-q", "-w", $wheels) + $requirements)
Invoke-Native "$py\python.exe" (@("-m", "pip", "install", "--no-warn-script-location", "-q",
    "--no-index", "--find-links", $wheels) + $requirements)

Write-Host "== Code app"
foreach ($file in "app.py", "clipper.py", "splitter.py", "desktop.py", "requirements.txt") {
    Copy-Item (Join-Path $root $file) $app
}
Copy-Item (Join-Path $root "templates"), (Join-Path $root "static") $app -Recurse
Copy-Item (Join-Path $root "assets\bokin.ico") $app

Write-Host "== BokinClip.exe (bản sao pythonw.exe gắn biểu tượng app)"
$exe = Join-Path $py "BokinClip.exe"
Copy-Item (Join-Path $py "pythonw.exe") $exe
$rcedit = Join-Path $tmp "rcedit.exe"
Invoke-WebRequest "https://github.com/electron/rcedit/releases/download/v2.0.0/rcedit-x64.exe" -OutFile $rcedit
Invoke-Native $rcedit @($exe, "--set-icon", (Join-Path $root "assets\bokin.ico"),
    "--set-version-string", "FileDescription", "Bokin Clip",
    "--set-version-string", "ProductName", "Bokin Clip")

$size = (Get-ChildItem $app -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("== Xong: {0} ({1:N0} MB)" -f $app, $size)
