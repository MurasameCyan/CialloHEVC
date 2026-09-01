@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo [ERROR] Project virtual environment not found: %PYTHON%
    echo Create the virtual environment and install dependencies first.
    exit /b 1
)

"%PYTHON%" -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] PyInstaller is not installed in the project virtual environment.
    echo Run: %PYTHON% -m pip install pyinstaller
    exit /b 1
)

"%PYTHON%" -m PyInstaller --clean --noconsole --onefile --icon=icon.ico --add-data "icon.ico;." CialloHEVC.py
if errorlevel 1 exit /b 1

copy /Y dist\CialloHEVC.exe CialloHEVC.exe
if errorlevel 1 exit /b 1

echo [OK] CialloHEVC.exe updated.
endlocal
