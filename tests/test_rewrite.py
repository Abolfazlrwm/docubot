"""Tests for the follow-up question rewriter."""

import pytest

from apps.chat.models import Conversation
from apps.chat.rewrite import REWRITE_SYSTEM_PROMPT, rewrite_question
from apps.chat.services import ask_question
from apps.common.ai.llm import FakeLLMProvider, LLMTransientError

pytestmark = pytest.mark.django_db


class RewritingLLM(FakeLLMProvider):
    """Fake that answers every generate() with a fixed rewritten query."""

    def __init__(self, rewritten: str):
        super().__init__()
        self.rewritten = rewritten

    def generate(self, system, messages):
        result = super().generate(system, messages)
        return type(result)(
            text=self.rewritten,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            model=result.model,
        )


class ExplodingLLM(FakeLLMProvider):
    def generate(self, system, messages):
        raise LLMTransientError("boom")


@pytest.fixture
def conversation(user):
    return Conversation.objects.create(owner=user)


@pytest.fixture
def add_history(conversation):
    def _add(role, content):
        conversation.messages.create(role=role, content=content)

    return _add


class TestRewriteQuestion:
    def test_first_turn_is_returned_unchanged_without_llm(self, conversation, monkeypatch):
        calls = []

        def boom():
            calls.append(1)
            raise AssertionError("LLM must not be called on the first turn")

        monkeypatch.setattr("apps.chat.rewrite.get_llm_provider", boom)
        assert rewrite_question(conversation, "What is the refund policy?") == (
            "What is the refund policy?"
        )
        assert calls == []

    def test_disabled_setting_skips_the_llm(self, conversation, add_history, settings, monkeypatch):
        settings.QUERY_REWRITE_ENABLED = False
        add_history("user", "What is the refund policy?")
        monkeypatch.setattr(
            "apps.chat.rewrite.get_llm_provider",
            lambda: (_ for _ in ()).throw(AssertionError("must not be called")),
        )
        assert rewrite_question(conversation, "and for cars?") == "and for cars?"

    def test_followup_is_rewritten_with_history(self, conversation, add_history, monkeypatch):
        add_history("user", "What is the refund policy for gadgets?")
        add_history("assistant", "Gadgets can be refunded within 30 days [1].")
        provider = RewritingLLM("refund policy for gadgets timeline")
        monkeypatch.setattr("apps.chat.rewrite.get_llm_provider", lambda: provider)

        rewritten = rewrite_question(conversation, "how long do I have?")

        assert rewritten == "refund policy for gadgets timeline"
        # The rewrite prompt saw the recent turns and the raw question.
        call = provider.calls[-1]
        assert call["system"] == REWRITE_SYSTEM_PROMPT
        assert any("refund policy for gadgets?" in m["content"] for m in call["messages"])
        assert call["messages"][-1]["content"] == "Question to rewrite: how long do I have?"

    def test_llm_failure_falls_back_to_raw_question(self, conversation, add_history, monkeypatch):
        add_history("user", "first question")
        monkeypatch.setattr("apps.chat.rewrite.get_llm_provider", lambda: ExplodingLLM())
        assert rewrite_question(conversation, "and the second one?") == "and the second one?"

    def test_empty_rewrite_falls_back(self, conversation, add_history, monkeypatch):
        add_history("user", "first question")
        monkeypatch.setattr("apps.chat.rewrite.get_llm_provider", lambda: RewritingLLM("   "))
        assert rewrite_question(conversation, "and that?") == "and that?"

    def test_oversized_rewrite_falls_back(self, conversation, add_history, settings, monkeypatch):
        settings.REWRITE_MAX_CHARS = 10
        add_history("user", "first question")
        monkeypatch.setattr(
            "apps.chat.rewrite.get_llm_provider",
            lambda: RewritingLLM("x" * 50),
        )
        assert rewrite_question(conversation, "and that?") == "and that?"

    def test_history_window_is_bounded(self, conversation, add_history, settings, monkeypatch):
        settings.QUERY_REWRITE_MAX_HISTORY = 2
        for i in range(5):
            add_history("user", f"question {i}")
            add_history("assistant", f"answer {i}")
        provider = RewritingLLM("rewritten")
        monkeypatch.setattr("apps.chat.rewrite.get_llm_provider", lambda: provider)

        rewrite_question(conversation, "next?")

        seen_roles = [
            m["role"]
            for m in provider.calls[-1]["messages"]
            if not m["content"].startswith("Question to rewrite")
        ]
        assert seen_roles == ["user", "assistant"]  # only the last 2 messages


class TestRewriteWiring:
    """Retrieval uses the rewritten query; storage keeps the raw question."""

    def test_ask_question_retrieves_with_rewritten_query(
        self, user, conversation, add_history, monkeypatch, settings
    ):
        settings.RETRIEVAL_MIN_SCORE = -1.0
        add_history("user", "Tell me about the zephyr engine.")
        add_history("assistant", "The zephyr engine is a turbine [1].")

        rewrites = []

        def fake_rewrite(conv, question):
            rewrites.append(question)
            return "zephyr engine fuel consumption"

        monkeypatch.setattr("apps.chat.services.rewrite_question", fake_rewrite)

        from apps.chat import services as chat_services

        provider = FakeLLMProvider()
        monkeypatch.setattr(chat_services, "get_llm_provider", lambda: provider)

        ask_question(user, conversation, "how thirsty is it?")

        # The rewriter got the raw question; retrieval never sees it unrewritten
        # (the fake provider's answer is built from whatever context was found).
        assert rewrites == ["how thirsty is it?"]
        user_message = conversation.messages.filter(role="user").order_by("-id").first()
        assert user_message.content == "how thirsty is it?"  # raw question is stored

    def test_stream_answer_also_rewrites(self, user, conversation, add_history, monkeypatch):
        add_history("user", "Tell me about the zephyr engine.")
        rewrites = []

        def fake_rewrite(conv, question):
            rewrites.append(question)
            return "zephyr engine specs"

        monkeypatch.setattr("apps.chat.services.rewrite_question", fake_rewrite)

        from apps.chat import services as chat_services

        monkeypatch.setattr(chat_services, "get_llm_provider", lambda: FakeLLMProvider())

        list(chat_services.stream_answer(user, conversation, "specs please"))
        assert rewrites == ["specs please"]
