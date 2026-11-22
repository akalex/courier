from __future__ import annotations

import asyncio
from dataclasses import dataclass
from http import HTTPStatus


class PayloadTooLargeError(ValueError):
    """Raised when an HTTP request exceeds a configured size limit."""


@dataclass(frozen=True, slots=True)
class IncomingRequest:
    method: str
    target: str
    body: bytes
    headers: dict[str, str]


class HTTPRequestReader:
    def __init__(self, *, max_body_size: int, max_header_size: int) -> None:
        self.max_body_size = max_body_size
        self.max_header_size = max_header_size

    async def read(self, reader: asyncio.StreamReader) -> IncomingRequest:
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
            raise PayloadTooLargeError("HTTP body exceeds limit")
        body = await reader.readexactly(content_length) if content_length else b""
        return IncomingRequest(method=method.upper(), target=target, body=body, headers=headers)

    async def _read_headers(
        self,
        reader: asyncio.StreamReader,
        header_size: int,
    ) -> tuple[dict[str, str], int]:
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
                raise ValueError("Duplicate HTTP header")
            headers[key] = value.strip()

    async def _read_header_line(
        self,
        reader: asyncio.StreamReader,
        header_size: int,
    ) -> tuple[bytes, int]:
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


async def write_response(writer: asyncio.StreamWriter, status: HTTPStatus, body: bytes) -> None:
    writer.write(
        f"HTTP/1.1 {status.value} {status.phrase}\r\n"
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
