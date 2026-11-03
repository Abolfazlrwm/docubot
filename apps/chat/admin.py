from django.contrib import admin

from apps.chat.models import Conversation, Message


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "owner", "title", "updated_at")
    list_filter = ("updated_at",)
    search_fields = ("title", "owner__username")
    raw_id_fields = ("owner",)
    ordering = ("-updated_at",)
    show_full_result_count = False


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "role", "created_at", "model")
    list_filter = ("role", "created_at")
    search_fields = ("content", "conversation__title")
    raw_id_fields = ("conversation",)
    ordering = ("created_at",)
