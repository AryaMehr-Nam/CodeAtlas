# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

RUN pip install --no-cache-dir .


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN groupadd --system codeatlas \
    && useradd --system --gid codeatlas --create-home --home-dir /home/codeatlas codeatlas

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=codeatlas:codeatlas pyproject.toml README.md LICENSE ./
COPY --chown=codeatlas:codeatlas src/ ./src/
COPY --chown=codeatlas:codeatlas demo/ ./demo/

USER codeatlas

EXPOSE 8770

ENTRYPOINT ["codeatlas"]
CMD ["--help"]
