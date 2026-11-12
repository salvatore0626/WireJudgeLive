@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0" || exit /b 1

set "BUILD_DIR=build"
set "BUILD_PYTHON=%BUILD_DIR%\venv\Scripts\python.exe"

if exist "%BUILD_PYTHON%" (
    "%BUILD_PYTHON%" -c "import sys; raise SystemExit(sys.version_info < (3, 11))" >nul 2>&1
    if not errorlevel 1 goto install_dependencies
)

py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 (
    py -3 -m venv "%BUILD_DIR%\venv"
    if not errorlevel 1 goto install_dependencies
    echo Could not create the build environment with py. Trying python...
)

python -c "import sys; raise SystemExit(sys.version_info < (3, 11))" >nul 2>&1
if errorlevel 1 (
    echo Install Python 3.11 or newer from python.org and run this file again.
    goto build_failed
)
python -m venv "%BUILD_DIR%\venv"
if errorlevel 1 goto build_failed

:install_dependencies
"%BUILD_PYTHON%" --version
echo Installing build dependencies...
"%BUILD_PYTHON%" -m pip install -r requirements.txt "PyInstaller>=6.12,<7"
if errorlevel 1 goto build_failed

echo Building Wire Judge Live...
"%BUILD_PYTHON%" -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name WireJudgeLive ^
    --specpath "%BUILD_DIR%" ^
    --workpath "%BUILD_DIR%\pyinstaller" ^
    --distpath "%CD%" ^
    --add-data "%CD%\wirejudge\data:wirejudge/data" ^
    --icon "%CD%\wirejudge\data\app_icon.ico" ^
    --hidden-import OpenGL.platform.win32 ^
    "%CD%\app.py"
if errorlevel 1 goto build_failed
if not exist "WireJudgeLive.exe" goto build_failed

echo.
echo Built "%CD%\WireJudgeLive.exe"
set "BUILD_RESULT=0"
goto finish

:build_failed
echo.
echo Build failed. See the output above for details.
set "BUILD_RESULT=1"

:finish
popd
if /i not "%~1"=="/nopause" pause
exit /b %BUILD_RESULT%
