# CyClaw Dockerfile - Production-grade, zero-trust, reproducible
# Python 3.12 + uv for fast installs. Seccomp/AppArmor ready. Non-root.
# Aligns with v1.9.0 pyproject + constraints for hermetic deps; CI uses requirements.txt for compat.

FROM python:3.12-slim-bookworm AS builder

# Install uv (fast, reproducible). Pinned to the multi-arch manifest digest of
# the 0.7 tag (fetched from GHCR 2026-07-18): a bare tag is mutable, so a
# re-tagged/compromised uv image would silently enter every build. The tag is
# kept alongside the digest for human readability; re-pin on any uv bump.
COPY --from=ghcr.io/astral-sh/uv:0.7@sha256:629240833dd25d03949509fc01ceff56ae74f5e5f0fd264da634dd2f70e9cc70 /uv /bin/uv

WORKDIR /app

# Dependency files first for layer caching
COPY pyproject.toml constraints.txt requirements.txt ./

# Install with uv (fast resolver) against requirements.txt, NOT pyproject.toml/-e .:
# `uv pip install` is uv's pip-compatible interface, which does not honor
# pyproject.toml's [tool.uv.sources]/[[tool.uv.index]] CPU-wheel routing (only
# uv's project commands like `uv sync` do) -- verified via dry-run, 2026-07,
# `uv pip install -r pyproject.toml` fails outright with "no version of
# torch==2.13.0+cpu". This build stage also hasn't COPYed the actual source yet
# (line 16 copies manifests only), so `-e .` couldn't build the cyclaw wheel
# here regardless. requirements.txt's own --extra-index-url line resolves the
# CPU wheel correctly for both the uv and pip paths below.
# Fallback to plain pip pre-installs the CPU torch wheel explicitly (mirrors
# ci.yml / pip-audit.yml) in case uv itself is unavailable or fails for an
# unrelated reason -- otherwise constraints.txt's `torch==2.13.0+cpu` pin is
# unresolvable from the default index once uv is out of the picture.
# The fallback pre-install MUST match the constraints.txt torch pin exactly —
# when constraints moved 2.12.1 -> 2.13.0 this line stayed behind, so the
# fallback path installed 2.12.1 and then immediately failed the constrained
# resolve. Keep the two in lock-step on any torch bump.
RUN uv pip install --system --no-cache-dir -r requirements.txt -c constraints.txt 2>/dev/null || \
    ( pip install --no-cache-dir torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu && \
      pip install --no-cache-dir -r requirements.txt -c constraints.txt )

# Runtime stage
FROM python:3.12-slim-bookworm

WORKDIR /app

# Copy site-packages and bins from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# App code
COPY . .

# Non-root user (Veeam-style least privilege)
RUN groupadd --gid 1000 cyclaw && \
    useradd --create-home --uid 1000 --gid cyclaw cyclaw && \
    chown -R cyclaw:cyclaw /app /tmp
USER cyclaw

# Offline-first + security env
ENV CYCLAW_OFFLINE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CYCLAW_TELEMETRY_KILL=1

EXPOSE 8787

# Simple healthcheck (assumes /health in gate.py)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://127.0.0.1:8787/health', timeout=4)" || exit 1

# In-container bind is 0.0.0.0 so docker-compose's port publish can reach
# uvicorn. Binding 127.0.0.1 here is the CONTAINER's private loopback: under
# default bridge networking docker-proxy forwards the published port to the
# container's eth0, where nothing would be listening — the host-side
# 127.0.0.1:8787 publish is dead while the in-container healthcheck stays
# green. Host exposure remains loopback-only via docker-compose.yml's
# "127.0.0.1:8787:8787" publish (the loopback invariant lives at the host
# boundary); TrustedHostMiddleware additionally rejects non-allow-listed
# Host headers. Port 8787 matches config.yaml api.port.
CMD ["uvicorn", "gate:app", "--host", "0.0.0.0", "--port", "8787", "--log-level", "info"]
