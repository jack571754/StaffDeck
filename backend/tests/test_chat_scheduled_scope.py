"""Public chat payloads cannot claim scheduler execution scope."""
from __future__ import annotations

import pytest

from app.api import chat
from app.db.models import User
from app.session.session_schema import ChatTurnRequest


@pytest.mark.parametrize("endpoint", [chat.chat_turn, chat.chat_stream])
def test_public_chat_ignores_forged_scheduled_task_scope(monkeypatch, endpoint):
    request = ChatTurnRequest(tenant_id="tenant_demo", user_id="forged_user",
                              message="hello", scheduled_task_id="task_forged")
    current_user = User(id="owner", tenant_id="tenant_demo", username="owner", password_hash="x")
    captured = []

    class ReachedAttachmentValidation(Exception):
        pass

    def capture_sanitized(value):
        captured.append(value)
        raise ReachedAttachmentValidation

    monkeypatch.setattr(chat, "_validate_chat_turn_attachments", capture_sanitized)
    with pytest.raises(ReachedAttachmentValidation):
        endpoint(request, current_user=current_user, db=None)
    assert captured[0].scheduled_task_id is None
    assert captured[0].user_id == "owner"
    # Sanitizing ingress creates a copy; trusted scheduler requests retain the field
    # when they are passed directly to AgentLoop rather than through an HTTP handler.
    assert request.scheduled_task_id == "task_forged"
