"""
Unit tests for Supabase client patch sanitization.
"""

from __future__ import annotations

from datetime import datetime

from src.db import supabase_client as supa


class _FakeQuery:
    def __init__(self):
        self.update_payload: dict | None = None
        self.insert_payload: dict | None = None
        self.eq_field: str | None = None
        self.eq_value: str | None = None
        self.text_search_calls: list[tuple[str, str, dict]] = []
        self.ilike_calls: list[tuple[str, str]] = []
        self.order_calls: list[tuple[str, bool]] = []
        self.limit_calls: list[int] = []
        self._response_data = []

    def update(self, payload: dict):
        self.update_payload = payload
        return self

    def select(self, *_args, **_kwargs):
        return self

    def insert(self, payload: dict):
        self.insert_payload = payload
        return self

    def eq(self, field: str, value: str):
        self.eq_field = field
        self.eq_value = value
        return self

    def text_search(self, column: str, query: str, options: dict):
        self.text_search_calls.append((column, query, options))
        return self

    def ilike(self, column: str, pattern: str):
        self.ilike_calls.append((column, pattern))
        return self

    def order(self, column: str, desc: bool = False):
        self.order_calls.append((column, desc))
        return self

    def limit(self, size: int):
        self.limit_calls.append(size)
        return self

    def execute(self):
        return type("Resp", (), {"data": self._response_data})()


class _FakeClient:
    def __init__(self):
        self.query = _FakeQuery()
        self.table_name: str | None = None

    def table(self, name: str):
        self.table_name = name
        return self.query


def test_update_user_profile_allows_only_safe_fields(monkeypatch):
    fake_client = _FakeClient()
    monkeypatch.setattr(supa, "get_client", lambda: fake_client)

    supa.update_user_profile(
        user_id="user-1",
        patch={
            "weight_kg": 71.2,
            "unknown_field": "must-not-pass",
            "telegram_username": "john",
            "lactose_intolerant": True,
            "drop_none": None,
        },
    )

    payload = fake_client.query.update_payload
    assert fake_client.table_name == "users"
    assert payload is not None
    assert "unknown_field" not in payload
    assert "drop_none" not in payload
    assert payload["weight_kg"] == 71.2
    assert payload["telegram_username"] == "john"
    assert payload["lactose_intolerant"] is True
    assert "updated_at" in payload
    datetime.fromisoformat(payload["updated_at"].replace("Z", "+00:00"))


def test_search_recipes_uses_text_search_options_dict(monkeypatch):
    fake_client = _FakeClient()
    monkeypatch.setattr(supa, "get_client", lambda: fake_client)

    supa.search_recipes("кето суп", excluded=None, limit=10)

    assert fake_client.query.text_search_calls
    column, query, options = fake_client.query.text_search_calls[0]
    assert column == "title"
    assert query == "кето суп"
    assert options == {"config": "russian"}


def test_create_user_returns_inserted_row(monkeypatch):
    fake_client = _FakeClient()
    fake_client.query._response_data = [
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "telegram_id": 777,
            "telegram_first_name": "Ivan",
            "language_code": "ru",
        }
    ]
    monkeypatch.setattr(supa, "get_client", lambda: fake_client)

    profile = supa.create_user(tg_id=777, first_name="Ivan")

    assert fake_client.table_name == "users"
    assert fake_client.query.insert_payload is not None
    assert fake_client.query.insert_payload["telegram_id"] == 777
    assert profile.telegram_id == 777


def test_create_user_fallbacks_to_existing_on_empty_insert_response(monkeypatch):
    fake_client = _FakeClient()
    fake_client.query._response_data = None
    monkeypatch.setattr(supa, "get_client", lambda: fake_client)
    monkeypatch.setattr(
        supa,
        "get_user_by_telegram_id",
        lambda tg_id: supa.UserProfile(id="x", telegram_id=tg_id, telegram_first_name="Recovered"),
    )

    profile = supa.create_user(tg_id=888)

    assert profile.telegram_id == 888
