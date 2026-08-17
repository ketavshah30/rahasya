FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml requirements.txt README.md ./
COPY rahasya ./rahasya
RUN pip install --no-cache-dir . \
    && command -v maigret \
    && command -v sherlock \
    && maigret --help >/dev/null \
    && sherlock --version
COPY . .

EXPOSE 8501 9108
CMD ["streamlit", "run", "rahasya/dashboard/app.py", "--server.address=0.0.0.0"]
