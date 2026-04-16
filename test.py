"""Тест подключения и конфигурации."""
from dotenv import load_dotenv
import os

load_dotenv()

api_id    = os.getenv("TG_API_ID")
api_hash  = os.getenv("TG_API_HASH")
ant_key   = os.getenv("ANTHROPIC_API_KEY")
oai_key   = os.getenv("OPENAI_API_KEY")

print("=" * 40)
print("  ПРОВЕРКА КОНФИГУРАЦИИ")
print("=" * 40)
print(f"TG_API_ID:          {api_id or '❌ НЕ ЗАДАН'}")
print(f"TG_API_HASH:        {'✅ ' + api_hash[:8] + '...' if api_hash else '❌ НЕ ЗАДАН'}")
print(f"ANTHROPIC_API_KEY:  {'✅ задан' if ant_key else '❌ НЕ ЗАДАН'}")
print(f"OPENAI_API_KEY:     {'✅ задан (голос работает)' if oai_key else '⚠️  не задан (голос выключен)'}")

# Проверка ffmpeg
import subprocess
try:
    result = subprocess.run(["ffmpeg", "-version"], capture_output=True)
    print(f"ffmpeg:             ✅ установлен")
except FileNotFoundError:
    print(f"ffmpeg:             ⚠️  не найден (нужен для голосовых)")

print()
