from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_mistralai import ChatMistralAI
from typing import Literal

from src.config import get_settings

Provider_type = Literal["openai", "anthropic", "mistral"]

def get_llm(provider: Provider_type, temperature: float | None = None):
    """Tạo LLM instance cho provider được chỉ định.

    Args:
        provider: Tên provider ("openai", "anthropic", "mistral").
        temperature: Nếu None, dùng settings.llm_temperature (default 0.7).
                     Truyền giá trị cụ thể để override — ví dụ 0.1 cho rule proposer.
    """
    settings = get_settings()
    temp = temperature if temperature is not None else settings.llm_temperature

    if provider == "openai":
        return ChatOpenAI(
            model=settings.model_name,
            api_key=settings.openai_api_key,
            temperature=temp,
        )
    elif provider == "anthropic":
        return ChatAnthropic(
            model=settings.model_name,
            api_key=settings.anthropic_api_key,
            temperature=temp,
        )
    elif provider == "mistral":
        return ChatMistralAI(
            model=settings.model_name,
            api_key=settings.mistral_api_key,
            temperature=temp,
        )
    else:
        raise ValueError(f"Invalid provider: {provider}")


if __name__ == "__main__":
    llm = get_llm(provider="mistral")
    result = llm.invoke("Hello")
    print(result)
