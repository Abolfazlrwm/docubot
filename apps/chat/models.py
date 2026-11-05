from django.conf import settings
from django.db import models


class Conversation(models.Model):
    """A chat thread between a user and the assistant over their documents."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    title = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["owner", "-updated_at"])]

    def __str__(self) -> str:
        return self.title or f"Conversation {self.pk}"


class Message(models.Model):
    """One turn in a conversation.

    `sources` is a JSON *snapshot* of the chunks used for an assistant answer —
    deliberately NOT a FK/M2M to Chunk: chunks are deleted and recreated when a
    document is reprocessed or deleted, and chat history must survive that.
    """

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    class Rating(models.TextChoices):
        """User feedback on an assistant answer."""

        UP = "up", "Up"
        DOWN = "down", "Down"

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField()
    sources = models.JSONField(default=list, blank=True)
    model = models.CharField(max_length=100, blank=True, default="")
    prompt_tokens = models.PositiveIntegerField(null=True, blank=True)
    completion_tokens = models.PositiveIntegerField(null=True, blank=True)
    # Thumbs up/down from the user. Only assistant messages are rateable; the
    # comment is optional context for why an answer was (not) helpful.
    rating = models.CharField(
        max_length=8, choices=Rating.choices, blank=True, default="", db_index=True
    )
    rating_comment = models.TextField(blank=True, default="")
    rated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["conversation", "created_at"])]

    def __str__(self) -> str:
        return f"{self.conversation_id} {self.role}: {self.content[:40]}"
