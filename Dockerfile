FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir .

COPY spend_tracker ./spend_tracker

EXPOSE 8000

CMD ["uvicorn", "spend_tracker.main:app", "--host", "0.0.0.0", "--port", "8000"]
