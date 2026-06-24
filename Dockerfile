FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh

ENV PORT=8080
EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
# Default: Cloud Run Job mode.
# To run as HTTP server: override CMD with uvicorn args (see Cloud Run Service deployment in README).
CMD ["python", "run_job.py"]
