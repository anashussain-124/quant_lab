"""CI configuration and project setup scripts."""

# ruff.toml
# Already created at project root

# pytest.ini
# Already created at project root

# mypy.ini
# Already created at project root

# requirements.txt
# Already created at project root

# Dockerfile for non-MT5 parts
DOCKERFILE = '''FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-m", "pytest", "tests/", "-v"]
'''

# docker-compose.yml for infrastructure
DOCKER_COMPOSE = '''version: '3.8'

services:
  redis:
    image: redis:alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: quant_lab
      POSTGRES_USER: quant
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    ports:
      - "5432:5432"
    volumes:
      - pg_data:/var/lib/postgresql/data
    restart: unless-stopped

volumes:
  redis_data:
  pg_data:
'''

print("CI configuration files already created.")
print("Run tests with: python -m pytest tests/ -v")
