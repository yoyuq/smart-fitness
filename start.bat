@echo off
REM Smart Fitness v1.0 一键启动 (Windows)
chcp 65001 > nul
echo ====================================
echo Smart Fitness v1.0 starting...
echo ====================================

cd /d "%~dp0"

REM 1. 启动后端
echo [1/2] starting backend on :8080 ...
cd backend
start "smart-fitness-backend" cmd /k "python -m uvicorn main:app --host 0.0.0.0 --port 8080"
cd ..

REM 2. 启动 APK 下载服务
echo [2/2] starting APK download on :8090 ...
start "smart-fitness-apk" cmd /k "python -m http.server 8090"

timeout /t 3 > nul
echo.
echo ====================================
echo  All services started:
echo    Backend:     http://localhost:8080
echo    Health:      http://localhost:8080/health
echo    APK Download: http://localhost:8090/smart_fitness_v10_csv.apk
echo ====================================
echo.
echo Press any key to exit (services keep running)...
pause > nul
