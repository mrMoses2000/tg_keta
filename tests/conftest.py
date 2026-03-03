"""
Global test environment bootstrap.

Ensures required settings can be constructed in modules that import src.config.
"""

import os


REQUIRED_ENV = {
    "TELEGRAM_BOT_TOKEN": "test-telegram-token",
    "TELEGRAM_WEBHOOK_SECRET": "test-webhook-secret",
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "test-service-role-key",
}

for key, value in REQUIRED_ENV.items():
    os.environ.setdefault(key, value)
