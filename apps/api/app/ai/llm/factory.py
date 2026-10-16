"""Build the configured LLM provider. Called once when the worker starts, so a missing key or
an unpriced model stops the worker at boot instead of failing the first review."""

from datetime import date

from pydantic import BaseModel

from app.ai.llm import gemini, groq
from app.ai.llm.base import LLMProvider
from app.ai.llm.fake import FakeLLM
from app.ai.llm.pricing import price_for
from app.ai.llm.rate_limit import RequestRateLimiter
from app.config import Settings

DEFAULT_MODELS = {
    "groq": groq.DEFAULT_MODEL,
    "gemini": gemini.DEFAULT_MODEL,
    "fake": "fake-llm",
}

# What the unscripted FakeLLM answers when a developer runs the worker without a key. It says
# plainly that no review happened, so a "clean" fake review can never pass for a real one.
FAKE_NO_REVIEW = (
    '{"change_summary": "LLM_PROVIDER=fake: no AI review was performed on this pull request.", '
    '"findings": []}'
)


class LLMConfigError(ValueError):
    """The LLM settings cannot work. The message never contains a key."""


def model_name(settings: Settings) -> str:
    return settings.llm_model or DEFAULT_MODELS[settings.llm_provider]


def build_llm(settings: Settings) -> LLMProvider:
    provider = settings.llm_provider
    model = model_name(settings)
    if provider == "fake":
        if settings.environment == "production":
            raise LLMConfigError(
                "LLM_PROVIDER=fake is for development and tests only; set groq or gemini"
            )
        return FakeLLM(model=model, fallback=_fake_fallback)

    price_for(model, date.today())  # raises UnknownModelPrice for an unpriced model
    limiter = RequestRateLimiter(settings.llm_requests_per_minute)
    if provider == "groq":
        if settings.groq_api_key is None:
            raise LLMConfigError("LLM_PROVIDER=groq needs GROQ_API_KEY")
        return groq.GroqProvider(
            api_key=settings.groq_api_key.get_secret_value(),
            model=model,
            limiter=limiter,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
    if settings.gemini_api_key is None:
        raise LLMConfigError("LLM_PROVIDER=gemini needs GEMINI_API_KEY")
    return gemini.GeminiProvider(
        api_key=settings.gemini_api_key.get_secret_value(),
        model=model,
        limiter=limiter,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


def _fake_fallback(schema: type[BaseModel] | None) -> str:
    return FAKE_NO_REVIEW if schema is not None else "LLM_PROVIDER=fake: no AI was called."
