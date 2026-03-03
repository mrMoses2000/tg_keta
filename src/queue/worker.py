"""
tg_keto.queue.worker — Main worker loop: dequeue → process → outbox.

This is the heart of the bot. One worker does:
  1. BRPOP from Redis queue (blocking, FIFO)
  2. Acquire per-user lock (Redis SET NX EX)
  3. Safety check (health red flags, off-topic) → short-circuit if unsafe
  4. Load user profile (Supabase) + conversation state (local Postgres)
  5. Auto-create user if new (first message)
  6. Send "typing..." indicator + optional placeholder "Подбираю рецепты..."
  7. Find candidate recipes (recipe_engine → Supabase + cache)
  8. Build LLM prompt (profile + state + recipes + user message)
  9. Call LLM CLI (subprocess with semaphore)
  10. Parse + validate actions_json
  11. TX2: apply profile_patch, state_patch, update conversation history
  12. Create outbox event (pending)
  13. Dispatch outbox (send to Telegram)
  14. Mark update as completed
  15. Release per-user lock
"""

from __future__ import annotations

import asyncio
import time

import structlog

from src.config import settings
from src.models import Job, ConversationState, UserProfile, RecipeQuery, ActionsJson
from src.db import postgres as pg
from src.db import redis_client as rc
from src.db import supabase_client as supa
from src.bot import telegram_sender as tg
from src.bot.safety import check_safety
from src.engine import recipe_engine, fsm
from src.engine.outbox_dispatcher import dispatch_pending
from src.llm.executor import call_llm
from src.llm.prompt_builder import build_prompt
from src.llm.actions_parser import parse_actions
from src.llm.actions_applier import apply_actions

logger = structlog.get_logger(__name__)

# ─── Commands ────────────────────────────────────────────────────────────

COMMANDS = {
    "/start": "start",
    "/help": "help",
    "/profile": "profile",
    "/recipes": "recipes",
}

HELP_TEXT = (
    "🥑 <b>КетоБот — ваш помощник по кето-диете</b>\n\n"
    "Я могу:\n"
    "• Подобрать рецепт по вашим ограничениям\n"
    "• Ответить на вопросы о кето-диете\n"
    "• Предложить замену продуктов\n"
    "• Помочь с мотивацией\n\n"
    "<b>Команды:</b>\n"
    "/start — начать заново\n"
    "/help — помощь\n"
    "/profile — мой профиль\n"
    "/recipes — подобрать рецепт\n\n"
    "Просто напишите мне, что хотите приготовить! 🍳"
)

PLACEHOLDER_TEXT = "Подбираю для вас... 🔍"
MAX_LOCK_REQUEUE_ATTEMPTS = 5


async def process_job(job_data: dict) -> None:
    """
    Process a single job from the queue.
    This is the main processing function called by the worker loop.
    """
    job = Job.model_validate(job_data)
    log = logger.bind(update_id=job.update_id, chat_id=job.chat_id, user_id=job.user_id)
    t_start = time.monotonic()

    # ── Acquire per-user lock ──
    lock_acquired = await rc.acquire_user_lock(job.user_id, ttl=120)
    if not lock_acquired:
        # Re-enqueue with bounded retries to avoid infinite queue loops.
        next_attempt = job.attempt + 1
        if next_attempt > MAX_LOCK_REQUEUE_ATTEMPTS:
            log.warning(
                "worker_user_locked_drop",
                attempt=next_attempt,
                max_attempts=MAX_LOCK_REQUEUE_ATTEMPTS,
            )
            await pg.mark_update_failed(job.update_id)
            return

        log.info(
            "worker_user_locked_requeue",
            attempt=next_attempt,
            max_attempts=MAX_LOCK_REQUEUE_ATTEMPTS,
        )
        await asyncio.sleep(min(next_attempt, 5))
        await rc.enqueue_job(
            job.model_copy(update={"attempt": next_attempt}).model_dump(mode="json")
        )
        return

    try:
        await _process_message(job, log)
    except Exception as e:
        log.error("worker_process_error", error=str(e))
        # Try to send error reply
        try:
            await tg.send_message(
                job.chat_id,
                "Извините, произошла ошибка. Попробуйте ещё раз через минуту. 🙏",
            )
        except Exception:
            pass
        await pg.mark_update_failed(job.update_id)
    finally:
        await rc.release_user_lock(job.user_id)
        t_end = time.monotonic()
        log.info("worker_job_done", total_seconds=round(t_end - t_start, 2))


async def _process_message(job: Job, log: structlog.BoundLogger) -> None:
    """Core message processing logic."""
    text = job.text.strip()

    # ── Handle commands ──
    command = COMMANDS.get(text.split()[0].lower()) if text.startswith("/") else None

    if command == "help":
        await _send_and_track(job, HELP_TEXT)
        return

    # ── Safety check (before LLM) ──
    safety = check_safety(text)
    if not safety.is_safe:
        log.info("worker_safety_triggered", red_flag=safety.is_red_flag, off_topic=safety.is_off_topic)
        await _send_and_track(job, safety.safety_message or "")
        return

    # ── Load or create user profile ──
    profile = supa.get_user_by_telegram_id(job.user_id)
    if profile is None:
        log.info("worker_creating_user")
        # Extract first_name from raw update
        raw_from = job.raw_update.get("message", {}).get("from", {})
        profile = supa.create_user(
            tg_id=job.user_id,
            first_name=raw_from.get("first_name"),
            last_name=raw_from.get("last_name"),
            username=raw_from.get("username"),
            language_code=raw_from.get("language_code", "ru"),
        )

    if profile.is_blocked:
        log.warning("worker_user_blocked")
        return

    # ── Handle /start and /profile commands ──
    if command == "start":
        initial_mode = fsm.determine_initial_mode(profile.bot_onboarding_completed)
        await pg.upsert_conversation_state(
            user_id=profile.id,
            chat_id=job.chat_id,
            mode=initial_mode,
            step="ask_restrictions" if initial_mode == "onboarding" else None,
        )
        if initial_mode == "onboarding":
            welcome = (
                f"Привет{', ' + profile.telegram_first_name if profile.telegram_first_name else ''}! 🥑\n\n"
                "Я КетоБот — ваш помощник по кето-диете.\n"
                "Чтобы подбирать рецепты максимально точно, "
                "расскажите немного о себе:\n\n"
                "Есть ли у вас ограничения в питании? "
                "(аллергии, непереносимость лактозы, диабет)"
            )
        else:
            welcome = (
                f"С возвращением{', ' + profile.telegram_first_name if profile.telegram_first_name else ''}! 🥑\n\n"
                "Чем могу помочь? Подобрать рецепт, ответить на вопрос о кето, "
                "или просто поболтать о здоровом питании?"
            )
        await _send_and_track(job, welcome)
        return

    if command == "profile":
        profile_text = _format_profile(profile)
        await _send_and_track(job, profile_text)
        return

    # ── Load conversation state ──
    state_data = await pg.get_conversation_state(profile.id)
    if state_data:
        state = ConversationState.model_validate(state_data)
    else:
        state = ConversationState(
            user_id=profile.id,
            telegram_chat_id=job.chat_id,
            mode=fsm.determine_initial_mode(profile.bot_onboarding_completed),
        )

    # ── Send typing indicator + placeholder ──
    if settings.send_typing_indicator:
        await tg.send_chat_action(job.chat_id, "typing")

    placeholder_msg = None
    if settings.send_placeholder_message:
        result = await tg.send_message(job.chat_id, PLACEHOLDER_TEXT)
        if result and result.get("ok"):
            placeholder_msg = result["result"]["message_id"]

    # ── Recipe search ──
    recipe_query = None
    if command == "recipes" or _looks_like_recipe_request(text):
        recipe_query = RecipeQuery(limit=5)  # basic; LLM may refine

    recipes = await recipe_engine.find_recipes(profile, recipe_query)
    log.info("worker_recipes_found", count=len(recipes))

    # ── Build prompt and call LLM ──
    prompt = build_prompt(text, profile, state, recipes)
    log.info("worker_calling_llm", prompt_chars=len(prompt))

    try:
        raw_output = await call_llm(prompt)
    except (TimeoutError, RuntimeError) as e:
        log.error("worker_llm_failed", error=str(e))
        fallback = "Извините, у меня сейчас проблемы с подбором ответа. Попробуйте через минуту. 🙏"
        if placeholder_msg:
            await tg.edit_message(job.chat_id, placeholder_msg, fallback)
        else:
            await _send_and_track(job, fallback)
        await pg.mark_update_failed(job.update_id)
        return

    # ── Parse and validate LLM response ──
    actions_json = parse_actions(raw_output)

    # ── Apply actions (TX2 logic) ──
    await apply_actions(actions_json, profile, state, job.chat_id)

    # ── Update conversation history ──
    new_messages = fsm.update_last_messages(state, text, actions_json.reply_text)
    new_mode = state.mode
    new_step = state.step
    if actions_json.actions and actions_json.actions.state_patch:
        sp = actions_json.actions.state_patch
        if sp.mode and fsm.is_valid_transition(state.mode, sp.mode):
            new_mode = sp.mode
        if sp.step is not None:
            new_step = sp.step

    await pg.upsert_conversation_state(
        user_id=profile.id,
        chat_id=job.chat_id,
        mode=new_mode,
        step=new_step,
        context_summary=state.context_summary,
        last_messages=new_messages,
    )

    # ── Send reply (edit placeholder or new message) ──
    reply_text = actions_json.reply_text
    if placeholder_msg:
        await tg.edit_message(job.chat_id, placeholder_msg, reply_text)
        # Still track in outbox as sent
        event_id = await pg.insert_outbound_event(
            chat_id=job.chat_id,
            reply_text=reply_text,
        )
        await pg.mark_outbound_sent(event_id)
    else:
        await _send_and_track(job, reply_text)

    # ── Mark complete ──
    await pg.mark_update_completed(job.update_id)
    log.info("worker_message_processed")


async def _send_and_track(job: Job, text: str) -> None:
    """Send message and track in outbox."""
    event_id = await pg.insert_outbound_event(
        chat_id=job.chat_id,
        reply_text=text,
    )
    result = await tg.send_message(job.chat_id, text)
    if result and result.get("ok"):
        await pg.mark_outbound_sent(event_id)
    else:
        # Will be retried by outbox dispatcher
        logger.warning("worker_send_failed_outbox_will_retry", event_id=event_id)
    await pg.mark_update_completed(job.update_id)


def _looks_like_recipe_request(text: str) -> bool:
    """Simple heuristic: does the message look like a recipe request?"""
    keywords = [
        "рецепт", "приготов", "готовить", "что поесть", "что съесть",
        "завтрак", "обед", "ужин", "перекус", "десерт", "суп", "салат",
        "хочу есть", "хочу кушать", "что можно", "подбери", "предложи",
    ]
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)


def _format_profile(profile: UserProfile) -> str:
    """Format user profile for display."""
    lines = ["📋 <b>Ваш профиль:</b>\n"]

    if profile.telegram_first_name:
        lines.append(f"👤 {profile.telegram_first_name}")
    if profile.weight_kg:
        lines.append(f"⚖️ Вес: {profile.weight_kg} кг")
    if profile.target_weight_kg:
        lines.append(f"🎯 Цель: {profile.target_weight_kg} кг")
    if profile.height_cm:
        lines.append(f"📏 Рост: {profile.height_cm} см")
    if profile.dietary_restrictions:
        lines.append(f"🚫 Ограничения: {', '.join(profile.dietary_restrictions)}")
    if profile.lactose_intolerant:
        lines.append("🥛 Непереносимость лактозы: да")
    if profile.diabetes_type:
        lines.append(f"💉 Диабет: {profile.diabetes_type}")
    if profile.taste_preferences:
        lines.append(f"😋 Вкусы: {', '.join(profile.taste_preferences)}")
    if profile.health_goals:
        lines.append(f"🎯 Цели: {', '.join(profile.health_goals)}")

    if len(lines) <= 1:
        lines.append("Профиль пока пуст. Расскажите о себе или используйте /start")

    return "\n".join(lines)


# ─── Worker Loop ─────────────────────────────────────────────────────────

async def run_worker() -> None:
    """
    Main worker loop: dequeue jobs and process them.
    Runs indefinitely until cancelled.
    """
    logger.info("worker_started")

    while True:
        try:
            job_data = await rc.dequeue_job(timeout=5)
            if job_data is None:
                continue  # No job, loop back

            await process_job(job_data)

        except asyncio.CancelledError:
            logger.info("worker_cancelled")
            break
        except Exception as e:
            logger.error("worker_loop_error", error=str(e))
            await asyncio.sleep(1)  # avoid tight error loop
