from __future__ import annotations

import json
import os
import signal
import subprocess

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import uvicorn

from .agent import MainAgent
from .agent_tools import AgentToolbox
from .config import settings
from .models import (
    ChatRequest,
    ChatResponse,
    ChatTitleRequest,
    ChatTitleResponse,
    CompatibilityToolRequest,
    LiveCrawlToolRequest,
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
        "data_mode": settings.data_mode,
    }


@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    return agent.run_sync_from_stream(payload.message)


@app.post("/chat/title", response_model=ChatTitleResponse)
def chat_title(payload: ChatTitleRequest) -> ChatTitleResponse:
    return ChatTitleResponse(title=agent.summarize_title(payload.history))


@app.post("/chat/stream")
def chat_stream(payload: ChatRequest) -> StreamingResponse:
    def event_stream():
        for event in agent.run_stream(payload.message):
            yield json.dumps(event, ensure_ascii=True) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


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


@app.post("/tools/crawl-live")
def crawl_live(payload: LiveCrawlToolRequest) -> dict:
    docs = tools.crawl_partselect_live(
        url=payload.url,
        model_number=payload.model_number,
        query=payload.query,
        max_pages=payload.max_pages,
    )
    return {
        "data_mode": settings.data_mode,
        "count": len(docs),
        "results": [d.model_dump() for d in docs],
    }


def run_server():
    port = int(os.getenv("APP_PORT", os.getenv("PORT", "8000")))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=settings.backend_reload,
    )


def run_server_dev():
    port = int(os.getenv("APP_PORT", os.getenv("PORT", "8000")))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)


def run_server_prod():
    port = int(os.getenv("APP_PORT", os.getenv("PORT", "8000")))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)


def stop_server():
    port = int(os.getenv("APP_PORT", os.getenv("PORT", "8000")))
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("Could not stop server: `lsof` is not installed.")
        return

    pids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not pids:
        print(f"No listening process found on port {port}.")
        return

    for pid_str in pids:
        try:
            os.kill(int(pid_str), signal.SIGTERM)
            print(f"Stopped process {pid_str} on port {port}.")
        except ProcessLookupError:
            continue


def start_mcp():
    cmd = [settings.mcp_browser_command, *settings.mcp_browser_args]
    print(f"Starting MCP browser: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        pass
