# Dockerfile for llm-gateway
# What it does: packages "code + deps + Python runtime" into a standard image that runs
# identically on any machine with Docker installed.
#
# Build:  docker build -t llm-gateway .
# Run:    docker run -p 8000:8000 -e ANTHROPIC_API_KEY=sk-... -e GATEWAY_API_KEY=my-secret llm-gateway

# --- 1. Base image: a slim system with Python 3.12 already installed ---
# slim = trimmed-down, small, only the essentials — good for production.
FROM python:3.12-slim

# --- 2. Working directory inside the container (like cd /app); later commands run here ---
WORKDIR /app

# --- 3. Layer-cache optimization: copy only the dependency manifests first, not the code ---
# Why copy these two first? Dependencies rarely change, while code changes often.
# So as long as deps are unchanged, the "install deps" layer below is cached, and editing
# code doesn't trigger a reinstall -> builds stay fast.
COPY pyproject.toml uv.lock ./

# --- 4. Install uv, then use it to install dependencies ---
# --frozen: install strictly per uv.lock for a reproducible env, no silent upgrades.
RUN pip install uv && uv sync --frozen

# --- 5. Only now copy the rest of the code (frequently changing things go last, keeping the deps layer cached) ---
COPY . .

# --- 6. Security: create a non-root user and switch to it ---
# Don't run the app as root; if compromised, the attacker only gets a restricted user, limiting the damage.
RUN useradd --create-home appuser
USER appuser

# --- 7. Declare that the container uses port 8000 (documentation only; real exposure still needs docker run -p) ---
EXPOSE 8000

# --- 8. Command run when the container starts ---
# Key: --host 0.0.0.0 (not 127.0.0.1!).
# Inside a container 127.0.0.1 refers only to the container itself and isn't reachable from outside;
# 0.0.0.0 means "accept any source", which together with docker run -p lets you reach the service from your machine.
CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
