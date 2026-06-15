FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/511130_live_monitor/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY scripts/511130_live_monitor /app/scripts/511130_live_monitor

EXPOSE 8787

CMD ["python", "-u", "scripts/511130_live_monitor/live_a_dashboard.py", "--host", "0.0.0.0", "--auto-run", "--interval", "1"]
