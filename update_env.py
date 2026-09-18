"""Обновляет (или добавляет) одну переменную в .env, не трогая остальные строки и комментарии.
Использование: python update_env.py WEBAPP_URL https://xxxx.ngrok-free.dev"""
import sys
from pathlib import Path

key, value = sys.argv[1], sys.argv[2]
env_path = Path(__file__).parent / ".env"

lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
found = False
for i, line in enumerate(lines):
    if line.strip().startswith(f"{key}="):
        lines[i] = f"{key}={value}"
        found = True
        break
if not found:
    lines.append(f"{key}={value}")

env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
