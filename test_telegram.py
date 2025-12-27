#!/usr/bin/env python3
"""
test_telegram.py - Test envoi Telegram (DM + Channel)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from config import config
from telegram import telegram_bot

print("=== Test Telegram ===")
print(f"Bot token: {'***' + config.telegram.bot_token[-10:] if config.telegram.bot_token else 'NON CONFIGURE'}")
print(f"Chat ID: {config.telegram.chat_id or 'NON CONFIGURE'}")
print(f"Channel ID: {config.telegram.channel_id or 'NON CONFIGURE'}")
print(f"Enabled: {telegram_bot.enabled}")
print()

if not telegram_bot.enabled:
    print("❌ Telegram non configuré!")
    sys.exit(1)

# Test 1: Envoi au chat_id (DM)
print("📤 Test 1: Envoi DM...")
result1 = telegram_bot.send_message("🧪 Test PiTrader - Message DM", to_channel=False)
print(f"   {'✅ OK' if result1 else '❌ ERREUR'}")

# Test 2: Envoi au channel (si configuré)
if config.telegram.channel_id:
    print(f"📤 Test 2: Envoi Channel ({config.telegram.channel_id})...")
    result2 = telegram_bot.send_message("🧪 Test PiTrader - Message Channel", to_channel=True)
    print(f"   {'✅ OK' if result2 else '❌ ERREUR'}")

    if not result2:
        print("\n⚠️  Vérifiez que:")
        print("   1. Le bot est administrateur du channel")
        print("   2. Le bot a la permission 'Post Messages'")
        print("   3. Le channel_id est correct (@username ou -100...)")
else:
    print("⏭️  Test 2: Channel non configuré - skip")

print("\n=== Fin du test ===")
