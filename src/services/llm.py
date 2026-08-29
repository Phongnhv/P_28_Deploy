import os
from typing import Literal

from src.config import get_settings
from src.services.eval_telemetry import EvalTelemetryCallback

Provider_type = Literal["openai", "anthropic", "mistral", "google"]


def get_llm(provider: Provider_type, temperature: float | None = None):
    """Tạo LLM instance cho provider được chỉ định.

    Args:
        provider: Tên provider ("openai", "anthropic", "mistral").
        temperature: Nếu None, dùng settings.llm_temperature (default 0.7).
                     Truyền giá trị cụ thể để override — ví dụ 0.1 cho rule proposer.
    """
    if os.getenv("EVALGATE_DETERMINISTIC_LLM") == "1":
        from src.services.deterministic_eval_llm import DeterministicEvalLLM
        return DeterministicEvalLLM()
    settings = get_settings()
    temp = temperature if temperature is not None else settings.llm_temperature

    model_names = {
        "openai": settings.openai_model_name,
        "anthropic": settings.anthropic_model_name,
        "mistral": settings.mistral_model_name,
        "google": settings.google_model_name,
    }
    callbacks = [EvalTelemetryCallback(provider=provider, model=model_names[provider])]

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.openai_model_name,
            api_key=settings.openai_api_key,
            temperature=temp,
            timeout=settings.llm_request_timeout_seconds,
            max_retries=6,
            callbacks=callbacks,
        )
    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=settings.anthropic_model_name,
            api_key=settings.anthropic_api_key,
            temperature=temp,
            callbacks=callbacks,
        )
    elif provider == "mistral":
        from langchain_mistralai import ChatMistralAI

        return ChatMistralAI(
            model=settings.mistral_model_name,
            api_key=settings.mistral_api_key,
            temperature=temp,
            callbacks=callbacks,
        )
    elif provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=settings.google_model_name,
            api_key=settings.google_api_key,
            temperature=temp,
            callbacks=callbacks,
        )
    else:
        raise ValueError(f"Invalid provider: {provider}")


if __name__ == "__main__":
    llm = get_llm(provider="mistral")
    result = llm.invoke("Hello")
    print(result)
