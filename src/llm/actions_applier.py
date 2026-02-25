"""
tg_keto.llm.actions_applier — Apply validated LLM actions to the database.

This module takes a validated ActionsJson and applies its side effects:
  - profile_patch → update user profile in Supabase
  - state_patch → update conversation state in local Postgres
  - recipe_query → trigger recipe search (returned, not applied)
  - safety_flags → logged (informational)

Security invariant: LLM never writes to DB directly.
Our code validates → applies allowlisted changes only.
"""

from __future__ import annotations

import structlog

from src.models import ActionsJson, UserProfile, ConversationState
from src.db import supabase_client as supa
from src.db import postgres as pg

logger = structlog.get_logger(__name__)


async def apply_actions(
    actions_json: ActionsJson,
    profile: UserProfile,
    state: ConversationState,
    chat_id: int,
) -> None:
    """
    Apply side effects from validated LLM actions.

    This runs within the worker's TX2 logical block.
    """
    actions = actions_json.actions
    if actions is None:
        return

    # 1. Apply profile_patch to Supabase users table
    if actions.profile_patch:
        patch_data = actions.profile_patch.model_dump(exclude_none=True)
        if patch_data:
            supa.update_user_profile(profile.id, patch_data)
            logger.info("actions_profile_patched", user_id=profile.id, fields=list(patch_data.keys()))

    # 2. Apply state_patch to local Postgres conversation_state
    if actions.state_patch:
        new_mode = actions.state_patch.mode or state.mode
        new_step = actions.state_patch.step

        await pg.upsert_conversation_state(
            user_id=profile.id,
            chat_id=chat_id,
            mode=new_mode,
            step=new_step,
            context_summary=state.context_summary,
            last_messages=state.last_messages,
        )
        logger.info("actions_state_patched", mode=new_mode, step=new_step)

    # 3. Log safety flags
    if actions.safety_flags:
        if actions.safety_flags.medical_concern:
            logger.warning("actions_medical_concern_flagged", user_id=profile.id)
        if actions.safety_flags.off_topic:
            logger.info("actions_off_topic_flagged", user_id=profile.id)
