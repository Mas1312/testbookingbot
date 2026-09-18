"""Печатает текущий публичный HTTPS-адрес локального ngrok-туннеля (или ничего, если он ещё не поднялся).
Используется start-bot.bat, чтобы не вписывать адрес в .env вручную при каждом перезапуске."""
import json
import urllib.request

try:
    with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=2) as r:
        data = json.load(r)
    for tunnel in data.get("tunnels", []):
        if tunnel.get("proto") == "https":
            print(tunnel["public_url"])
            break
except Exception:
    pass
