"""
tg_keto.llm.actions_parser — Parse and validate LLM output (actions_json).

The LLM returns raw text that SHOULD be JSON following our contract.
In practice:
  - LLM may wrap JSON in markdown ```json ... ``` blocks
  - LLM may add preamble text before JSON
  - LLM may return malformed JSON
  - LLM may include disallowed fields

This module:
  1. Extracts JSON from raw text (handles markdown fences, preamble)
  2. Validates against ActionsJson Pydantic model (allowlist)
  3. Returns validated result or a fallback safe response
"""

from __future__ import annotations

import json
import re

import structlog

from src.models import ActionsJson, Actions, SafetyFlags

logger = structlog.get_logger(__name__)

# Regex to extract JSON from markdown code fences
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)
# Regex to find the outermost { ... } block
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)

FALLBACK_REPLY = (
    "Извините, произошла техническая ошибка. "
    "Пожалуйста, попробуйте переформулировать ваш вопрос. 🙏"
)


def parse_actions(raw_output: str) -> ActionsJson:
    """
    Parse LLM raw output into validated ActionsJson.
    Falls back to safe reply on any parsing/validation error.
    """
    try:
        json_str = _extract_json(raw_output)
        if json_str is None:
            logger.warning("actions_no_json_found", output_preview=raw_output[:200])
            return _fallback(raw_output)

        data = json.loads(json_str)

        # Validate with Pydantic
        result = ActionsJson.model_validate(data)
        logger.info("actions_parsed_ok", has_profile_patch=bool(result.actions and result.actions.profile_patch))
        return result

    except json.JSONDecodeError as e:
        logger.warning("actions_json_decode_error", error=str(e), output_preview=raw_output[:200])
        return _fallback(raw_output)
    except Exception as e:
        logger.warning("actions_validation_error", error=str(e), output_preview=raw_output[:200])
        return _fallback(raw_output)


def _extract_json(text: str) -> str | None:
    """
    Extract JSON string from LLM output.
    Tries in order:
      1. JSON inside markdown ```json ... ``` fences
      2. First { ... } block in the text
      3. The whole text as JSON
    """
    # Strategy 1: markdown fence
    match = _JSON_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()

    # Strategy 2: find { ... } block
    match = _JSON_OBJECT_RE.search(text)
    if match:
        return match.group(0)

    # Strategy 3: whole text might be JSON
    stripped = text.strip()
    if stripped.startswith("{"):
        return stripped

    return None


def _fallback(raw_output: str) -> ActionsJson:
    """
    Create a fallback ActionsJson when parsing fails.
    If the raw output contains readable text, use it as reply.
    Otherwise use the generic fallback message.
    """
    # Try to use the raw text as a reply if it looks human-readable
    clean = raw_output.strip()
    if clean and len(clean) > 10 and not clean.startswith("{"):
        # LLM returned plain text instead of JSON — use it as reply
        reply = clean[:2000]
        logger.info("actions_fallback_plain_text", chars=len(reply))
    else:
        reply = FALLBACK_REPLY

    return ActionsJson(
        reply_text=reply,
        actions=Actions(
            safety_flags=SafetyFlags(),
        ),
    )
