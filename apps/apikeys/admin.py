from django.contrib import admin

from apps.apikeys.models import APIKey


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "scope", "is_active", "created_at", "last_used_at")
    list_filter = ("scope", "is_active")
    search_fields = ("name", "user__username", "prefix")
    readonly_fields = ("hashed_key", "prefix", "last_used_at", "revoked_at")
