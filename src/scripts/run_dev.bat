@echo off
TITLE Chad [DEV MODE]

echo ===================================================
echo           CHAD - DEVELOPMENT LAUNCHER
echo ===================================================

:: Check for virtual environment
if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate
    echo [INFO] Virtual environment activated.
) else (
    echo [WARN] .venv not found. Running with system python.
)

:: Run the GUI application
echo [INFO] Starting Chad GUI...
python main_gui.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Chad crashed with exit code %ERRORLEVEL%
    pause
)
