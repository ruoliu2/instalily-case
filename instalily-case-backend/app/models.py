from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    history: List[dict] = Field(default_factory=list)
    run_id: str = ""


class ChatTitleRequest(BaseModel):
    history: List[dict] = Field(default_factory=list)


class ChatTitleResponse(BaseModel):
    title: str


class CancelRunRequest(BaseModel):
    run_id: str = Field(min_length=1)


class CancelRunResponse(BaseModel):
    ok: bool
    run_id: str
    status: str


class Citation(BaseModel):
    url: str
    title: str = ""
    snippet: str = ""


class ToolTrace(BaseModel):
    step: str
    detail: str


class ChatResponse(BaseModel):
    answer: str
    intent: str
    confidence: float
    citations: List[Citation] = Field(default_factory=list)
    traces: List[ToolTrace] = Field(default_factory=list)


class RetrievedDoc(BaseModel):
    url: str
    title: str = ""
    text: str
    score: float = 0.0


class CompatibilityResult(BaseModel):
    model_number: str
    partselect_number: str
    compatible: bool
    confidence: float
    source_url: Optional[str] = None


class CompatibilityToolRequest(BaseModel):
    model_number: str = Field(min_length=3)
    partselect_number: str = Field(min_length=3)


class SiteSearchToolRequest(BaseModel):
    query: str = Field(min_length=2)
    limit: int = Field(default=6, ge=1, le=10)


class LiveCrawlToolRequest(BaseModel):
    url: str = Field(min_length=8)
    model_number: str = ""
    query: str = ""
    max_pages: int = Field(default=2, ge=1, le=6)
