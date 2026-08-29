from typing import Literal

from langchain.chat_models import init_chat_model

from src.config import get_settings

Provider_type = Literal["openai", "anthropic", "mistral", "google"]


def get_llm(provider: Provider_type, temperature: float | None = None, callbacks: list | None = None):
    """Tạo LLM instance cho provider được chỉ định.

    Args:
        provider: Tên provider ("openai", "anthropic", "mistral", "google").
        temperature: Nếu None, dùng settings.llm_temperature (default 0.7).
                     Truyền giá trị cụ thể để override — ví dụ 0.1 cho rule proposer.
        callbacks: Danh sách callback handlers. Mặc định tự động gắn MetricsTracker.
    """
    from src.utils.metrics_tracker import get_metrics_tracker

    settings = get_settings()
    temp = temperature if temperature is not None else settings.llm_temperature
    cb_list = callbacks if callbacks is not None else [get_metrics_tracker()]

    if provider == "openai":
        return init_chat_model(
            f"openai:{settings.openai_model_name}",
            api_key=settings.openai_api_key,
            temperature=temp,
            timeout=settings.llm_request_timeout_seconds,
            max_retries=3,
            callbacks=cb_list,
            use_responses_api=True,
        )

    elif provider == "anthropic":
        return init_chat_model(
            f"anthropic:{settings.anthropic_model_name}",
            api_key=settings.anthropic_api_key,
            temperature=temp,
            timeout=settings.llm_request_timeout_seconds,
            callbacks=cb_list,
        )
    elif provider == "mistral":
        return init_chat_model(
            f"mistralai:{settings.mistral_model_name}",
            api_key=settings.mistral_api_key,
            temperature=temp,
            callbacks=cb_list,
        )
    elif provider == "google":
        return init_chat_model(
            f"google_genai:{settings.google_model_name}",
            api_key=settings.google_api_key,
            temperature=temp,
            callbacks=cb_list,
        )
    else:
        raise ValueError(f"Invalid provider: {provider}")


if __name__ == "__main__":
    llm = get_llm(provider="openai")
    result = llm.invoke("Hello")
    print(result)
