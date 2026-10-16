import json

import httpx
import pytest

from courier.adapters.matrix import MatrixDestination
from courier.domain import Attachment


@pytest.mark.asyncio
async def test_send_text_uses_matrix_room_api_and_access_token():
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"event_id": "$event"}, request=request)

    async with httpx.AsyncClient(
        base_url="https://matrix.example.org",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer matrix-token"},
    ) as client:
        destination = MatrixDestination(
            homeserver_url="https://matrix.example.org",
            access_token="unused",
            client=client,
        )
        await destination.send("!room:example.org", "hello from WhatsApp")

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "PUT"
    assert request.url.raw_path.decode().startswith(
        "/_matrix/client/v3/rooms/%21room%3Aexample.org/send/m.room.message/"
    )
    assert request.headers["Authorization"] == "Bearer matrix-token"
    assert json.loads(request.content) == {"msgtype": "m.text", "body": "hello from WhatsApp"}


@pytest.mark.asyncio
async def test_send_image_uploads_media_then_sends_matrix_attachment():
    requests = []

    async def handler(request):
        requests.append(request)
        if request.url.path.endswith("/upload"):
            return httpx.Response(200, json={"content_uri": "mxc://matrix.example.org/media-id"}, request=request)
        return httpx.Response(200, json={"event_id": "$event"}, request=request)

    async with httpx.AsyncClient(
        base_url="https://matrix.example.org",
        transport=httpx.MockTransport(handler),
    ) as client:
        destination = MatrixDestination(
            homeserver_url="https://matrix.example.org",
            access_token="token",
            client=client,
        )
        await destination.send(
            "!room:example.org",
            "WhatsApp image caption",
            attachments=(Attachment("image/jpeg", "photo.jpg", b"image-bytes"),),
        )

    assert len(requests) == 2
    upload_request, send_request = requests
    assert upload_request.method == "POST"
    assert upload_request.url.path == "/_matrix/client/v3/upload"
    assert upload_request.url.params["filename"] == "photo.jpg"
    assert upload_request.headers["content-type"] == "image/jpeg"
    assert upload_request.content == b"image-bytes"
    assert json.loads(send_request.content) == {
        "msgtype": "m.image",
        "body": "WhatsApp image caption",
        "filename": "photo.jpg",
        "url": "mxc://matrix.example.org/media-id",
        "info": {"mimetype": "image/jpeg", "size": 11},
    }


@pytest.mark.asyncio
async def test_send_rejects_successful_upload_without_matrix_content_uri():
    async def handler(request):
        return httpx.Response(200, json={}, request=request)

    async with httpx.AsyncClient(
        base_url="https://matrix.example.org",
        transport=httpx.MockTransport(handler),
    ) as client:
        destination = MatrixDestination(
            homeserver_url="https://matrix.example.org",
            access_token="token",
            client=client,
        )

        with pytest.raises(ValueError, match="content_uri"):
            await destination.send(
                "!room:example.org",
                "caption",
                attachments=(Attachment("application/pdf", "report.pdf", b"pdf"),),
            )


@pytest.mark.asyncio
async def test_matrix_http_errors_are_propagated():
    async def handler(request):
        return httpx.Response(403, request=request)

    async with httpx.AsyncClient(
        base_url="https://matrix.example.org",
        transport=httpx.MockTransport(handler),
    ) as client:
        destination = MatrixDestination(
            homeserver_url="https://matrix.example.org",
            access_token="token",
            client=client,
        )

        with pytest.raises(httpx.HTTPStatusError):
            await destination.send("!room:example.org", "hello")
