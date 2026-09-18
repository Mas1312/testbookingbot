@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist venv (
    echo Создаю виртуальное окружение...
    python -m venv venv
)

echo Устанавливаю зависимости...
venv\Scripts\python.exe -m pip install -q -r requirements.txt

if not exist .env (
    copy .env.example .env
)

echo.
echo ВАЖНО: сервер должен быть уже запущен отдельно (start.bat), иначе ngrok
echo не сможет до него достучаться (ERR_NGROK_8012).
echo.
echo Поднимаю ngrok-туннель до localhost:8000...

where ngrok >nul 2>nul
if %errorlevel%==0 (
    start /min "" ngrok http 8000 --log=stdout --log-format=logfmt > ngrok.log 2>&1
) else (
    start /min "" npx --yes ngrok http 8000 --log=stdout --log-format=logfmt > ngrok.log 2>&1
)

echo Жду, пока ngrok поднимется...
set NGROK_URL=
for /l %%i in (1,1,20) do (
    if not defined NGROK_URL (
        timeout /t 1 /nobreak >nul
        for /f "delims=" %%u in ('venv\Scripts\python.exe get_ngrok_url.py 2^>nul') do set NGROK_URL=%%u
    )
)

if not defined NGROK_URL (
    echo Не удалось получить адрес ngrok автоматически. Открой http://127.0.0.1:4040
    echo и вставь адрес вручную в .env ^(поле WEBAPP_URL^), затем перезапусти bot.py.
    pause
    exit /b 1
)

echo Адрес туннеля: %NGROK_URL%
venv\Scripts\python.exe update_env.py WEBAPP_URL %NGROK_URL%

echo.
echo Запускаю бота...
echo Чтобы остановить бота и туннель — закрой это окно или нажми Ctrl+C.
echo.
venv\Scripts\python.exe backend\bot.py

pause
