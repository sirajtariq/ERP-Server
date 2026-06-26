# Official fast Astral UV image
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Work directory set karein
WORKDIR /app

# Python outputs ko container logs mein direct dikhane ke liye
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Dependency files copy karein
COPY pyproject.toml uv.lock ./

# Dependencies install karein
RUN uv sync --frozen --no-cache

# Baki saara code copy karein
COPY . .

# Port 8000 expose karein
EXPOSE 8000

# Default entrypoint (isay compose file override karegi automatic migrations ke liye)
CMD ["uv", "run", "python", "manage.py", "runserver", "0.0.0.0:8000"]