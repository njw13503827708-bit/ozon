"""Expose configuration state only. Keys are configured by local hidden input."""
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from backend.api.optimization import handled, operator
from backend.integrations.ai.provider_config import public_settings, configure

ROOT = Path(__file__).resolve().parents[2]
router = APIRouter(prefix='/api/workbench/ai-provider', tags=['backend-model-configuration'])


class ProviderSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_url: str
    model: str
    models: list[str] = Field(default_factory=list)
    api_mode: str = 'responses'


@router.get('')
@handled
def read_provider(request: Request):
    operator(request)
    return public_settings(ROOT)


@router.patch('')
@handled
def save_provider(body: ProviderSettings, request: Request):
    operator(request, mutation=True)
    return configure(ROOT, body.model_dump())
