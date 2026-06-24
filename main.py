import logging
import os
from dotenv import load_dotenv
load_dotenv()
from src.log import configure_logging
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from src.pipeline import run_pipeline

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI()


class RunRequest(BaseModel):
    partition: str | None = None    # YYYY-MM, e.g. "2026-04"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/run")
def run(body: RunRequest = RunRequest()):
    logger.info("request.received", extra={
        "log_event": "request.received",
        "partition": body.partition,
    })
    try:
        result = run_pipeline(partition=body.partition)
        return result
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
