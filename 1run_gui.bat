@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto run_venv
echo [INFO] Project virtual environment not found. Using system Python.
python "CialloHEVC.py"
goto save_exit

:run_venv
".venv\Scripts\python.exe" "CialloHEVC.py"

:save_exit
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
