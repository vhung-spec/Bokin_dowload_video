@echo off
rem Bam dup de chay Bokin Clip tren Windows.
rem Lan dau se tu cai moi thu can thiet vao thu muc .venv, cac lan sau mo ngay.
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto update

set "PY="
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=python"
if not defined PY goto nopython

echo Lan dau chay: dang cai dat, mat khoang 1-2 phut...
%PY% -m venv .venv
if errorlevel 1 goto novenv

:update
echo Dang kiem tra cap nhat...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -U -r requirements.txt
if errorlevel 1 echo Canh bao: khong cap nhat duoc thu vien (mat mang?), van thu chay app...

".venv\Scripts\python.exe" app.py --open
if errorlevel 1 (
  echo.
  echo App bi loi, xem thong bao phia tren.
  pause
)
exit /b

:nopython
echo Can Python 3.10 tro len.
echo Tai tai https://www.python.org/downloads/ - khi cai nho tick "Add python.exe to PATH",
echo roi chay lai file nay.
pause
exit /b 1

:novenv
echo Khong tao duoc moi truong Python (.venv).
pause
exit /b 1
