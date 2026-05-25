FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY ../dooers-protocol /tmp/dooers-protocol
COPY pyproject.toml .
COPY src ./src

RUN uv pip install --system --no-cache /tmp/dooers-protocol \
 && uv pip install --system --no-cache .

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "dooers_push.main:app", "--host", "0.0.0.0", "--port", "8080"]
