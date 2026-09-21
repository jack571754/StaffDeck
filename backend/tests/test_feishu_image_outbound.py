from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.channels.adapters.feishu import FeishuAdapter
from app.db.models import ChannelBinding


@pytest.fixture
def adapter():
    return FeishuAdapter()


@pytest.fixture
def binding():
    return ChannelBinding(
        id="bind_feishu_1",
        tenant_id="tenant_feishu",
        channel="feishu",
        agent_id="agent_1",
        config_json={"app_id": "cli_123", "app_secret": "sec_456"},
    )


def test_upload_image_success(adapter: FeishuAdapter, binding: ChannelBinding):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"code":0,"data":{"image_key":"img_v2_mock_123"}}'
    mock_resp.json.return_value = {"code": 0, "data": {"image_key": "img_v2_mock_123"}}

    mock_client = MagicMock()
    mock_client.post.return_value = mock_resp

    with (
        patch.object(adapter._tokens, "get", return_value="mock_feishu_token"),
        patch.object(adapter, "_client_factory", return_value=MagicMock(__enter__=MagicMock(return_value=mock_client))),
    ):
        img_key = adapter.upload_image(binding, b"fake_png_bytes")

    assert img_key == "img_v2_mock_123"
    assert mock_client.post.called
    call_kwargs = mock_client.post.call_args[1]
    assert call_kwargs["headers"]["Authorization"] == "Bearer mock_feishu_token"
    assert "image" in call_kwargs["files"]


def test_send_image_message(adapter: FeishuAdapter, binding: ChannelBinding):
    target = {"receive_id": "ou_user_123", "receive_id_type": "open_id"}

    with patch.object(adapter, "_post", return_value={"data": {"message_id": "om_img_msg_1"}}) as mock_post:
        msg_id = adapter.send_image(
            binding=binding,
            target=target,
            image_key="img_v2_mock_123",
            idempotency_key="idemp_send_img_1",
        )

    assert msg_id == "om_img_msg_1"
    assert mock_post.called
    body = mock_post.call_args[1]["body"]
    assert body["msg_type"] == "image"
    content = json.loads(body["content"])
    assert content["image_key"] == "img_v2_mock_123"


def test_send_direct_image_type(adapter: FeishuAdapter, binding: ChannelBinding):
    target = {
        "receive_id": "ou_user_123",
        "receive_id_type": "open_id",
        "msg_type": "image",
    }

    with patch.object(adapter, "_post", return_value={"data": {"message_id": "om_img_msg_2"}}) as mock_post:
        msg_id = adapter.send(
            binding=binding,
            target=target,
            text="img_v2_direct_key",
            idempotency_key="idemp_send_img_2",
        )

    assert msg_id == "om_img_msg_2"
    body = mock_post.call_args[1]["body"]
    assert body["msg_type"] == "image"
    content = json.loads(body["content"])
    assert content["image_key"] == "img_v2_direct_key"
