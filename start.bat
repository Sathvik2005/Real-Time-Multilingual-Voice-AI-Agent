@echo off
title Voice AI Clinic Agent - Startup
cd /d "%~dp0"

echo ============================================
echo  Voice AI Clinic Agent - Starting servers
echo ============================================

:: Kill anything on port 8080 (our backend port)
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8080 " ^| findstr LISTEN') do (
    taskkill /F /PID %%a >nul 2>&1
)

:: Kill existing Vite dev server
taskkill /F /IM node.exe /T >nul 2>&1
timeout /t 2 /nobreak >nul

echo [1/2] Starting backend on port 8080...
start "Backend - Voice AI" cmd /k "cd /d "%~dp0" && C:\Users\SATHVIK\python.exe -m uvicorn backend.main:app --port 8080 --host 0.0.0.0 --reload"

echo Waiting for backend to start...
timeout /t 10 /nobreak >nul

:: Verify backend is up
curl -s http://localhost:8080/api/health >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Backend failed to start! Check the backend window for errors.
    pause
    exit /b 1
)
echo [OK] Backend is healthy!

echo [2/2] Starting frontend on port 5173...
start "Frontend - Voice AI" cmd /k "cd /d "%~dp0\frontend" && npm run dev"

echo Waiting for frontend to start...
timeout /t 8 /nobreak >nul

echo.
echo ============================================
echo  Both servers are running!
echo.
echo  App:      http://localhost:5173
echo  API docs: http://localhost:8080/api/docs
echo ============================================
echo.
echo Press any key to open the app in browser...
pause >nul
start http://localhost:5173
