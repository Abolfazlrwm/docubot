from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.chat.views import (
    ConversationViewSet,
    MessageListCreateView,
    MessageStreamView,
    SearchView,
)

app_name = "chat"

router = DefaultRouter()
router.register("conversations", ConversationViewSet, basename="conversation")

urlpatterns = [
    *router.urls,
    path("search/", SearchView.as_view(), name="search"),
    path(
        "conversations/<int:conversation_pk>/messages/",
        MessageListCreateView.as_view(),
        name="conversation-messages",
    ),
    path(
        "conversations/<int:conversation_pk>/messages/stream/",
        MessageStreamView.as_view(),
        name="conversation-messages-stream",
    ),
]
