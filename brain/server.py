from __future__ import annotations

from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel

from core.graph import process
from core.schemas import NormalizedEvent

load_dotenv()

app = FastAPI(title="Gatekeep Brain")


class ProcessRequest(BaseModel):
    event: NormalizedEvent
    config_yaml: Optional[str] = None


@app.post("/process")
def process_endpoint(req: ProcessRequest) -> dict:
    return process(req.event.model_dump(), req.config_yaml)
