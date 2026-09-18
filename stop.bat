@echo off
echo Останавливаю сервер на порту 8000...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do (
    taskkill /F /PID %%p >nul 2>&1
)
echo Готово.
pause
