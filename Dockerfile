FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

COPY . .

RUN python -m pip install --upgrade pip && \
    pip install uv && \
    uv sync --frozen --no-dev

EXPOSE 8000

CMD ["uv", "run", "python", "-m", "courier.main"]
