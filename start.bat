@echo off
cd /d "%~dp0"

if not exist venv (
    echo Создаю виртуальное окружение...
    python -m venv venv
)

echo Устанавливаю зависимости...
venv\Scripts\python.exe -m pip install -q -r requirements.txt

if not exist .env (
    copy .env.example .env
    echo Создан файл .env — при необходимости заполни BOT_TOKEN и другие поля.
)

echo.
echo Открой в браузере: http://localhost:8000
echo Чтобы остановить сервер — закрой это окно или нажми Ctrl+C.
echo.
venv\Scripts\python.exe -m uvicorn server:app --reload --port 8000 --app-dir backend

pause
