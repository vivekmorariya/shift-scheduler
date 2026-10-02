@echo off
echo ============================================
echo      Shift Scheduler - Starting Server
echo ============================================
echo.

cd /d "%~dp0"

:: Get local IP
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4"') do (
    set ip=%%a
    goto :found
)
:found
set ip=%ip: =%

echo Access the app on your devices:
echo   This PC    : http://localhost:5000
echo   Android    : http://%ip%:5000
echo   Manager    : http://%ip%:5000
echo.
echo Login credentials:
echo   Admin   : admin / admin123
echo   Manager : manager / mgr456
echo.
echo Press Ctrl+C to stop the server.
echo ============================================
echo.
python app.py
pause
