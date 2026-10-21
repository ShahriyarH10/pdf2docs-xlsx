@echo off
rem pdf2xlsx web app, one click: double-click this file (Windows).
rem The first run installs the Python packages; later runs only start the app and open it in the browser.
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python is not installed. Install it with:  winget install Python.Python.3.12
    echo then close this window and double-click start.bat again.
    pause
    exit /b 1
)

where tesseract >nul 2>nul
if errorlevel 1 if not exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" if not exist "%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe" (
    echo Tesseract ^(the OCR program^) is not installed. Install it with:  winget install UB-Mannheim.TesseractOCR
    echo then close this window and double-click start.bat again.
    pause
    exit /b 1
)

if not exist venv\Scripts\python.exe (
    echo Creating the Python environment ^(first run only^)...
    py -3 -m venv venv || goto :failed
)
fc /b requirements.txt venv\requirements.txt >nul 2>nul
if errorlevel 1 (
    echo Installing the Python packages...
    venv\Scripts\python -m pip install -q --upgrade pip
    venv\Scripts\python -m pip install -r requirements.txt || goto :failed
    copy /y requirements.txt venv\requirements.txt >nul
)

venv\Scripts\python app.py %*
pause
exit /b 0

:failed
echo.
echo Setting up failed: see the messages above.
pause
exit /b 1
