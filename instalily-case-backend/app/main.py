from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from .agent import MainAgent
from .agent_tools import AgentToolbox
from .config import settings
from .models import (
    ChatRequest,
    ChatResponse,
    CompatibilityToolRequest,
    SiteSearchToolRequest,
)
from .retrieval import SampleRepository

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
repo = SampleRepository(settings.sample_dir)
tools = AgentToolbox(repo)
agent = MainAgent(repo, tools)


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "sample_pages_loaded": len(repo.pages),
        "llm_enabled": settings.use_llm,
        "data_mode": settings.data_mode,
    }


@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    return agent.run(payload.message)


@app.get("/tools")
def list_tools() -> dict:
    return {
        "data_mode": settings.data_mode,
        "tools": tools.tool_schemas(),
    }


@app.post("/tools/check-compatibility")
def check_compatibility(payload: CompatibilityToolRequest) -> dict:
    result = tools.check_part_compatibility(
        model_number=payload.model_number,
        partselect_number=payload.partselect_number,
    )
    return {
        "data_mode": settings.data_mode,
        "result": result.model_dump(),
    }


@app.post("/tools/search-site")
def search_site(payload: SiteSearchToolRequest) -> dict:
    docs = tools.search_partselect_content(query=payload.query, limit=payload.limit)
    return {
        "data_mode": settings.data_mode,
        "count": len(docs),
        "results": [d.model_dump() for d in docs],
    }


def run_server():
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
