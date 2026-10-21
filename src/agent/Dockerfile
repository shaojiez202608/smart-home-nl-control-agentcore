# Optional container path (not used by default; see DECISIONS.md D26). The default deploys the agent as a zip.
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    AGENT_OBSERVABILITY_ENABLED=true \
    OTEL_PYTHON_DISTRO=aws_distro \
    OTEL_PYTHON_CONFIGURATOR=aws_configurator

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-compile -r requirements.txt

COPY agent_app ./agent_app

RUN useradd --create-home --uid 1000 agent
USER agent

EXPOSE 8080
# AgentCore Runtime service contract: GET /ping on port 8080.
HEALTHCHECK --interval=30s --timeout=3s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ping', timeout=2)"]
CMD ["opentelemetry-instrument", "python", "-m", "agent_app.main"]
