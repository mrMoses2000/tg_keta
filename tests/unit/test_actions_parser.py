"""
Unit tests for LLM actions parser.
"""
import pytest
from src.llm.actions_parser import parse_actions, _extract_json


class TestExtractJson:
    """Test JSON extraction from various LLM output formats."""

    def test_plain_json(self):
        text = '{"reply_text": "hello", "actions": null}'
        result = _extract_json(text)
        assert result is not None
        assert '"reply_text"' in result

    def test_markdown_fenced_json(self):
        text = 'Here is the response:\n```json\n{"reply_text": "hello"}\n```\nDone.'
        result = _extract_json(text)
        assert result == '{"reply_text": "hello"}'

    def test_json_with_preamble(self):
        text = 'OK, here you go:\n{"reply_text": "рецепт"}'
        result = _extract_json(text)
        assert result is not None
        assert '"reply_text"' in result

    def test_no_json_at_all(self):
        text = "Just plain text without any JSON"
        result = _extract_json(text)
        assert result is None


class TestParseActions:
    """Test full parse + validate pipeline."""

    def test_valid_json(self):
        raw = '{"reply_text": "Вот рецепт!", "actions": {"safety_flags": {"medical_concern": false}}}'
        result = parse_actions(raw)
        assert result.reply_text == "Вот рецепт!"

    def test_markdown_wrapped(self):
        raw = '```json\n{"reply_text": "test"}\n```'
        result = parse_actions(raw)
        assert result.reply_text == "test"

    def test_invalid_json_fallback_to_text(self):
        raw = "Вот ваш ответ, кето рецепт будет готов через 30 минут"
        result = parse_actions(raw)
        # Should use the raw text as reply (not the generic fallback)
        assert "кето" in result.reply_text.lower() or "ответ" in result.reply_text.lower()

    def test_totally_broken_json(self):
        raw = "{broken json here"
        result = parse_actions(raw)
        # Should produce some reply_text
        assert len(result.reply_text) > 0

    def test_empty_input(self):
        result = parse_actions("")
        assert len(result.reply_text) > 0
