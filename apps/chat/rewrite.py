"""Rewrite follow-up questions into standalone search queries.

"What about page 2?" is useless as a search query on its own — it only makes
sense together with the conversation so far. Before retrieval, the latest
question is rewritten into a self-contained query using the recent turns.

The rewrite is strictly best effort:
- the first turn of a conversation is returned unchanged (nothing to resolve,
  no LLM call, no added latency);
- when disabled by settings the raw question flows through;
- any LLM failure, empty answer or oversized rewrite falls back to the raw
  question, so answering never blocks on the rewriter.
"""

from django.conf import settings

from apps.chat.models import Conversation
from apps.common.ai.llm import LLMError, get_llm_provider

REWRITE_SYSTEM_PROMPT = """You rewrite the user's latest question as one standalone search query.

Rules:
1. Use the conversation history only to resolve pronouns and ellipsis
   ("it", "that", "the second one"). Do not answer the question.
2. Output ONLY the rewritten query: no explanations, no quotes, no preamble.
3. Keep the user's language.
4. If the question is already standalone, output it unchanged."""


def rewrite_question(conversation: Conversation, question: str) -> str:
    """Return `question` rewritten as a standalone search query (best effort)."""
    if not settings.QUERY_REWRITE_ENABLED:
        return question
    history = _recent_history(conversation)
    if not history:
        return question  # first turn: nothing to resolve
    try:
        result = get_llm_provider().generate(
            system=REWRITE_SYSTEM_PROMPT,
            messages=[
                *history,
                {"role": "user", "content": f"Question to rewrite: {question}"},
            ],
        )
    except LLMError:
        return question  # the rewriter must never break answering
    rewritten = result.text.strip()
    if not rewritten or len(rewritten) > settings.REWRITE_MAX_CHARS:
        return question
    return rewritten


def _recent_history(conversation: Conversation) -> list[dict]:
    """The last QUERY_REWRITE_MAX_HISTORY messages, chronological."""
    messages = conversation.messages.order_by("-created_at", "-id")[
        : settings.QUERY_REWRITE_MAX_HISTORY
    ]
    return [{"role": message.role, "content": message.content} for message in reversed(messages)]
