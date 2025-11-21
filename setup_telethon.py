#!/usr/bin/env python3
"""
Telethon Setup Script for Bio Checking
Run this in the Replit shell to authenticate and create bio_session.session

This will ask for your phone number and verification code.
"""
import asyncio
import os
from telethon import TelegramClient

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE")

async def main():
    print("=" * 60)
    print("Telethon Authentication Setup")
    print("=" * 60)
    
    if not API_ID or not API_HASH:
        print("❌ ERROR: API_ID and API_HASH environment variables are required!")
        print("Please make sure these secrets are set in Replit Secrets.")
        return
    
    print(f"\n📱 Phone number from secrets: {TELEGRAM_PHONE}")
    
    use_env_phone = input("Use this phone number? (y/n): ").strip().lower()
    
    if use_env_phone == 'y':
        phone = TELEGRAM_PHONE
    else:
        phone = input("Enter your phone number (with country code, e.g., +1234567890): ").strip()
    
    print(f"\n🔄 Connecting to Telegram...")
    
    client = TelegramClient("bio_session", int(API_ID), API_HASH)
    await client.connect()
    
    if not await client.is_user_authorized():
        print(f"📲 Sending code to {phone}...")
        await client.send_code_request(phone)
        
        code = input("\n🔐 Enter the verification code you received: ").strip()
        
        try:
            await client.sign_in(phone, code)
        except Exception as e:
            if "Two-steps verification" in str(e) or "password" in str(e).lower():
                password = input("🔑 Two-step verification enabled. Enter your password: ").strip()
                await client.sign_in(password=password)
            else:
                raise e
    
    me = await client.get_me()
    print(f"\n✅ Successfully authenticated as: {me.first_name}")
    if me.username:
        print(f"   Username: @{me.username}")
    print(f"   User ID: {me.id}")
    
    print("\n✅ Session file 'bio_session.session' has been created!")
    print("✅ Bio checking feature is now enabled.")
    print("\nℹ️  Restart the bot to activate bio checking.")
    
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
