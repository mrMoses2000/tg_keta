"""
Unit tests for safety module (health red flags + off-topic detection).
"""
import pytest
from src.bot.safety import check_safety, SafetyResult


class TestHealthRedFlags:
    """Test detection of dangerous health symptoms."""

    def test_chest_pain(self):
        result = check_safety("У меня сильная боль в груди и тяжело дышать")
        assert result.is_red_flag
        assert result.red_flag_type == "chest_pain"
        assert "врач" in result.safety_message.lower() or "скорой" in result.safety_message.lower()

    def test_hypoglycemia(self):
        result = check_safety("У меня сахар упал до 2.5, руки трясёт")
        assert result.is_red_flag
        assert result.red_flag_type == "hypoglycemia"

    def test_dehydration(self):
        result = check_safety("Рвота весь день, не могу остановить")
        assert result.is_red_flag
        assert result.red_flag_type == "dehydration"

    def test_confusion(self):
        result = check_safety("Потерял сознание на минуту")
        assert result.is_red_flag
        assert result.red_flag_type == "confusion"

    def test_bleeding(self):
        result = check_safety("Кровь в стуле уже неделю")
        assert result.is_red_flag
        assert result.red_flag_type == "other"


class TestOffTopicDetection:
    """Test off-topic request blocking."""

    def test_math_homework(self):
        result = check_safety("Реши уравнение x^2 + 3x - 5 = 0")
        assert result.is_off_topic
        assert "специализируюсь" in result.safety_message

    def test_programming(self):
        result = check_safety("Напиши код на python для парсинга JSON")
        assert result.is_off_topic

    def test_legal(self):
        result = check_safety("Нужна консультация адвоката по налоговому вопросу")
        assert result.is_off_topic


class TestDangerousAdvice:
    """Test dangerous diet advice detection."""

    def test_extreme_fasting(self):
        result = check_safety("Хочу голодать 2 недели полностью")
        assert result.is_red_flag
        assert result.red_flag_type == "dangerous_advice"


class TestSafeMessages:
    """Test that normal keto questions pass through."""

    def test_recipe_request(self):
        result = check_safety("Подбери мне рецепт на завтрак без молочки")
        assert result.is_safe

    def test_keto_question(self):
        result = check_safety("Можно ли есть авокадо на кето?")
        assert result.is_safe

    def test_empty_message(self):
        result = check_safety("")
        assert result.is_safe

    def test_greeting(self):
        result = check_safety("Привет!")
        assert result.is_safe
