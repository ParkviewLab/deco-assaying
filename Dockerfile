FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

# git is needed at runtime for `git clone` of GitHub/GitLab sources.
# (uv image is debian-slim; git isn't installed by default.)
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Resolve deps first so they cache across source changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-install-project

COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev

ENV PYTHONUNBUFFERED=1 \
    OUTPUT_ROOT=/data \
    PORT=35832 \
    HOST=0.0.0.0

EXPOSE 35832
VOLUME ["/data"]

CMD ["uv", "run", "python", "-m", "deco_assaying"]
