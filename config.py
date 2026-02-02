import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")  # має бути формату -100xxxx або @channel_name
MOD_GROUP_ID = os.getenv("MOD_GROUP_ID")  # має бути формату -100xxxx

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не встановлено!")
if not CHANNEL_ID:
    raise ValueError("CHANNEL_ID не встановлено!")
if not MOD_GROUP_ID:
    raise ValueError("MOD_GROUP_ID не встановлено!")
