from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any


logger = logging.getLogger(__name__)

WebhookHandler = Callable[[dict[str, Any]], Awaitable[None]]


class PayloadTooLargeError(ValueError):
    """Raised when an inbound webhook exceeds the configured size limit."""


class WebhookServer:
    """Small HTTP ingress server for WAHA webhook events and health checks."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        handler: WebhookHandler,
        max_body_size: int = 1024 * 1024,
        max_header_size: int = 64 * 1024,
        request_timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.handler = handler
        self.max_body_size = max_body_size
        self.max_header_size = max_header_size
        self.request_timeout = request_timeout
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        if self._server is None:
            self._server = await asyncio.start_server(
                self._handle_request,
                self.host,
                self.port,
                limit=self.max_header_size,
            )

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle_request(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            method, target, body = await asyncio.wait_for(self._read_request(reader), timeout=self.request_timeout)
        except TimeoutError:
            await self._write_response(writer, 408, b"Request timeout")
            return
        except PayloadTooLargeError:
            await self._write_response(writer, 413, b"Payload too large")
            return
        except asyncio.IncompleteReadError, ValueError, UnicodeDecodeError:
            await self._write_response(writer, 400, b"Bad request")
            return

        if method == "GET" and target == "/health":
            await self._write_response(writer, 200, b"OK")
            return
        if method != "POST" or target != "/webhook":
            status = 405 if target == "/webhook" else 404
            await self._write_response(writer, status, b"Not found")
            return

        try:
            payload = json.loads(body)
        except json.JSONDecodeError, UnicodeDecodeError:
            await self._write_response(writer, 400, b"Invalid JSON")
            return
        if not isinstance(payload, dict):
            await self._write_response(writer, 400, b"Expected JSON object")
            return

        try:
            await self.handler(payload)
        except Exception:
            logger.exception("Webhook event processing failed")
            await self._write_response(writer, 500, b"Webhook processing failed")
            return
        await self._write_response(writer, 200, b"OK")

    async def _read_request(self, reader: asyncio.StreamReader) -> tuple[str, str, bytes]:
        request_line_bytes, header_size = await self._read_header_line(reader, 0)
        request_line = request_line_bytes.decode("ascii").strip()
        parts = request_line.split()
        if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
            raise ValueError("Invalid HTTP request line")
        method, target, _ = parts

        headers, _ = await self._read_headers(reader, header_size)
        if "transfer-encoding" in headers:
            raise ValueError("Transfer-Encoding is not supported")
        content_length = self._parse_content_length(headers.get("content-length", "0"))
        if content_length < 0:
            raise ValueError("Negative Content-Length")
        if content_length > self.max_body_size:
            raise PayloadTooLargeError("Webhook body exceeds limit")
        body = await reader.readexactly(content_length) if content_length else b""
        return method.upper(), target, body

    async def _read_headers(self, reader: asyncio.StreamReader, header_size: int) -> tuple[dict[str, str], int]:
        headers: dict[str, str] = {}
        while True:
            line, header_size = await self._read_header_line(reader, header_size)
            if line == b"\r\n":
                return headers, header_size
            name, separator, value = line.decode("iso-8859-1").partition(":")
            if not separator or not name.strip():
                raise ValueError("Invalid HTTP header")
            key = name.strip().lower()
            if key in headers:
                raise ValueError(f"Duplicate HTTP header: {key}")
            headers[key] = value.strip()

    async def _read_header_line(self, reader: asyncio.StreamReader, header_size: int) -> tuple[bytes, int]:
        line = await reader.readline()
        header_size += len(line)
        if header_size > self.max_header_size:
            raise PayloadTooLargeError("HTTP headers exceed limit")
        if not line.endswith(b"\r\n"):
            raise ValueError("HTTP line is not terminated")
        return line, header_size

    @staticmethod
    def _parse_content_length(value: str) -> int:
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc

    async def _write_response(self, writer: asyncio.StreamWriter, status: int, body: bytes) -> None:
        status_text = {
            200: "OK",
            400: "Bad Request",
            404: "Not Found",
            405: "Method Not Allowed",
            408: "Request Timeout",
            413: "Payload Too Large",
            500: "Internal Server Error",
        }[status]
        writer.write(
            f"HTTP/1.1 {status} {status_text}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n\r\n".encode()
            + body
        )
        try:
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
