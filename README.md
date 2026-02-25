# 🥑 KetoBOT — Telegram Keto-Diet Coach

Telegram-бот, который помогает с кето-диетой: подбирает рецепты из Supabase, учитывает ограничения пользователя (аллергии, непереносимость лактозы, диабет), и отвечает на вопросы о питании с помощью Gemini AI.

## Архитектура

```
Telegram → Webhook (aiohttp:8080) → Redis Queue → Worker → Gemini CLI → Outbox → Telegram
                                         ↕                    ↕
                                   Local Postgres         Supabase
                                   (state tables)       (recipes + users)
```

## Быстрый старт

```bash
# Проверить зависимости
./run.sh doctor

# Создать .env
./run.sh env
# → Заполнить TELEGRAM_BOT_TOKEN и SUPABASE_SERVICE_ROLE_KEY

# Запустить всё
./run.sh up

# Тесты
./run.sh test unit
```

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Начать / перезапустить |
| `/help` | Справка |
| `/profile` | Мой профиль |
| `/recipes` | Подобрать рецепт |

Или просто напишите: «Хочу кето-завтрак без молочки» 🍳

## Технологии

- **Python 3.10+** (asyncio)
- **aiohttp** — webhook HTTP server
- **asyncpg** — local Postgres (state tables)
- **supabase-py** — Supabase REST API (recipes + users)
- **redis-py** — queue, cache, distributed locks
- **Gemini CLI** — LLM via subprocess
- **Pydantic** — validation + settings
- **structlog** — structured JSON logging

## Структура

```
tg_keto/
├── src/
│   ├── config.py          # Settings from .env
│   ├── models.py          # Pydantic models
│   ├── bot/               # Webhook, sender, safety
│   ├── db/                # Postgres, Supabase, Redis
│   ├── llm/               # Executor, prompts, parser
│   ├── engine/            # Recipe engine, FSM, outbox
│   ├── queue/             # Worker
│   └── knowledge/         # Knowledge base (stub)
├── migrations/            # SQL (local + Supabase)
├── tests/                 # Unit / integration / e2e
├── run.sh                 # Master control script
├── docker-compose.yml     # Redis + Postgres
└── .env.example           # Environment template
```

## Лицензия

Private.
