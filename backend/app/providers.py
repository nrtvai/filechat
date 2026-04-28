from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from openai import OpenAI

from .openrouter import OPENROUTER_URL, ChatResult, EmbeddingResult, OpenRouterClient
from .settings_store import get_openrouter_key


DEFAULT_PROVIDER_ID = "openrouter"


class Provider(Protocol):
    id: str
    display_name: str

    def key_state(self) -> tuple[str | None, str]:
        ...

    def ocr_client(self) -> OpenAI | None:
        ...

    async def models(self, kind: str) -> list[dict[str, Any]]:
        ...

    async def verify(self, *, chat_model: str, embedding_model: str) -> dict[str, Any]:
        ...

    async def embedding_result(self, inputs: list[str], model: str) -> EmbeddingResult:
        ...

    async def chat(self, **kwargs: Any) -> ChatResult:
        ...

    async def plan_task(self, **kwargs: Any) -> dict[str, Any]:
        ...

    async def write_draft_from_evidence(self, **kwargs: Any) -> ChatResult:
        ...


class OpenRouterProvider:
    id = DEFAULT_PROVIDER_ID
    display_name = "OpenRouter"

    def _client(self) -> OpenRouterClient:
        return OpenRouterClient()

    def key_state(self) -> tuple[str | None, str]:
        return get_openrouter_key()

    def ocr_client(self) -> OpenAI | None:
        key, _ = self.key_state()
        if not key:
            return None
        return OpenAI(
            api_key=key,
            base_url=OPENROUTER_URL,
            default_headers={
                "HTTP-Referer": "http://127.0.0.1:5173",
                "X-Title": "FileChat",
            },
        )

    async def models(self, kind: str) -> list[dict[str, Any]]:
        return await self._client().models(kind)

    async def verify(self, *, chat_model: str, embedding_model: str) -> dict[str, Any]:
        return await self._client().verify_provider(chat_model=chat_model, embedding_model=embedding_model)

    async def embedding_result(self, inputs: list[str], model: str) -> EmbeddingResult:
        return await self._client().embedding_result(inputs, model)

    async def chat(self, **kwargs: Any) -> ChatResult:
        return await self._client().chat(**kwargs)

    async def plan_task(self, **kwargs: Any) -> dict[str, Any]:
        return await self._client().plan_task(**kwargs)

    async def write_draft_from_evidence(self, **kwargs: Any) -> ChatResult:
        return await self._client().write_draft_from_evidence(**kwargs)


@dataclass
class ProviderRegistry:
    providers: dict[str, Provider]
    default_provider_id: str = DEFAULT_PROVIDER_ID

    def active(self) -> Provider:
        return self.providers[self.default_provider_id]

    def get(self, provider_id: str) -> Provider:
        return self.providers[provider_id]


_REGISTRY = ProviderRegistry(providers={DEFAULT_PROVIDER_ID: OpenRouterProvider()})


def provider_registry() -> ProviderRegistry:
    return _REGISTRY
