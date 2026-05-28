FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
COPY vm_stats_publisher.py .
COPY entrypoint.sh .

RUN pip install --no-cache-dir -r requirements.txt
RUN apt-get update && apt-get install -y iputils-ping && rm -rf /var/lib/apt/lists/*
RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]
