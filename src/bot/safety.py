"""
tg_keto.bot.safety — Health red flags detection and topic guardrails.

Two responsibilities:
  1. HEALTH RED FLAGS: detect dangerous symptoms in user messages.
     If detected, bot MUST recommend doctor/emergency, NOT treat.
  2. TOPIC GUARDRAILS: detect off-topic requests (math, coding, legal, etc.)
     Bot politely declines and redirects to keto/nutrition.

Implementation uses keyword/regex matching (deterministic, no LLM needed).
This runs BEFORE LLM to short-circuit dangerous or off-topic messages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class SafetyResult:
    """Result of safety check."""
    is_safe: bool = True
    is_red_flag: bool = False
    is_off_topic: bool = False
    red_flag_type: str | None = None
    safety_message: str | None = None


# ─── Health Red Flags ────────────────────────────────────────────────────

RED_FLAG_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    (
        "chest_pain",
        re.compile(r"бол[ьи].*(?:груд|серд|сердц)|(?:давит|жмёт|жмет|сжимает).*груд", re.IGNORECASE),
        "⚠️ Вы описываете симптомы, которые могут быть серьёзными. "
        "Пожалуйста, немедленно обратитесь к врачу или вызовите скорую помощь (103). "
        "Я не могу заменить медицинскую помощь.",
    ),
    (
        "hypoglycemia",
        re.compile(
            r"(?:сахар.*(?:упал|низк|падает|2\.|3\.)|гипогликеми|"
            r"трясёт|трясет|дрож.*рук|холодн.*пот|потер.*сознан)",
            re.IGNORECASE,
        ),
        "⚠️ Эти симптомы могут указывать на гипогликемию. "
        "Если у вас диабет — срочно съешьте что-то сладкое и обратитесь к врачу. "
        "Я не врач и не могу ставить диагнозы.",
    ),
    (
        "dehydration",
        re.compile(
            r"(?:обезвож|сильн.*жажд|не могу пить|рвот.*(?:весь|цел|дн)|"
            r"диаре.*(?:весь|дн|сутк)|понос.*(?:весь|дн))",
            re.IGNORECASE,
        ),
        "⚠️ Сильное обезвоживание может быть опасным. "
        "Пожалуйста, обратитесь к врачу, особенно если симптомы не проходят.",
    ),
    (
        "confusion",
        re.compile(
            r"(?:спутанн.*сознан|не понима.*где|теря.*сознан|обморо|упал.*в обморок)",
            re.IGNORECASE,
        ),
        "⚠️ Нарушение сознания — серьёзный симптом. "
        "Пожалуйста, вызовите скорую помощь (103) для себя или близкого.",
    ),
    (
        "other",
        re.compile(
            r"(?:кров.*(?:рвот|стул|кашл)|кровотечен|сильн.*бол.*живот|"
            r"не могу дышать|задыхаюсь|одышк.*покое|синеют.*губ)",
            re.IGNORECASE,
        ),
        "⚠️ Пожалуйста, обратитесь к врачу с этими симптомами. "
        "Я могу помочь с вопросами о кето-диете, но медицинская помощь — "
        "это то, что должен оказывать врач.",
    ),
]


# ─── Off-Topic Detection ────────────────────────────────────────────────

OFF_TOPIC_PATTERNS: list[re.Pattern] = [
    # Math / homework
    re.compile(r"(?:реши|посчитай|вычисли).*(?:уравнен|задач|пример|интеграл|производн)", re.IGNORECASE),
    re.compile(r"(?:помоги|помогите).*(?:домашн|дз|homework)", re.IGNORECASE),
    # Programming
    re.compile(r"(?:напиши|написать).*(?:код|программ|скрипт|функци|python|javascript)", re.IGNORECASE),
    re.compile(r"(?:debug|отладь|исправь.*баг|fix.*bug)", re.IGNORECASE),
    # Legal / financial
    re.compile(r"(?:юридическ|адвокат|суд.*иск|налогов|бухгалтер|инвестиц|акции)", re.IGNORECASE),
    # Political
    re.compile(r"(?:полити[кч]|выбор[ыа]|голосовани|партии|президент)", re.IGNORECASE),
]

OFF_TOPIC_REPLY = (
    "Я специализируюсь на кето-диете, подборе рецептов и здоровом питании. 🥑\n"
    "К сожалению, на этот вопрос ответить не смогу — "
    "он за пределами моей экспертизы.\n\n"
    "Чем могу помочь по питанию? Например:\n"
    "• Подобрать рецепт на завтрак/обед/ужин\n"
    "• Объяснить, можно ли продукт на кето\n"
    "• Помочь с заменой ингредиентов"
)

# ─── Dangerous advice detection ─────────────────────────────────────────

DANGEROUS_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?:голодать.*(?:\d+|недел|месяц)|голодовк|сухое.*голодан)", re.IGNORECASE),
]

DANGEROUS_REPLY = (
    "⚠️ Экстремальное голодание может быть опасно для здоровья. "
    "Кето-диета — это не голодание, а сбалансированное питание с низким содержанием углеводов.\n\n"
    "Если хотите, могу рассказать о правильном подходе к кето-диете "
    "или подобрать рецепты, которые помогут достичь ваших целей."
)


def check_safety(text: str) -> SafetyResult:
    """
    Run safety checks on user message text.
    Order: red flags → dangerous advice → off-topic.
    First match wins (most severe first).
    """
    if not text or not text.strip():
        return SafetyResult()

    # 1. Health red flags (most critical)
    for flag_type, pattern, message in RED_FLAG_PATTERNS:
        if pattern.search(text):
            logger.warning("safety_red_flag_detected", flag_type=flag_type)
            return SafetyResult(
                is_safe=False,
                is_red_flag=True,
                red_flag_type=flag_type,
                safety_message=message,
            )

    # 2. Dangerous advice requests
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(text):
            logger.warning("safety_dangerous_request")
            return SafetyResult(
                is_safe=False,
                is_red_flag=True,
                red_flag_type="dangerous_advice",
                safety_message=DANGEROUS_REPLY,
            )

    # 3. Off-topic detection
    for pattern in OFF_TOPIC_PATTERNS:
        if pattern.search(text):
            logger.info("safety_off_topic_detected")
            return SafetyResult(
                is_safe=False,
                is_off_topic=True,
                safety_message=OFF_TOPIC_REPLY,
            )

    return SafetyResult()
