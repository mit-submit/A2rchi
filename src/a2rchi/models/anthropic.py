from langchain_anthropic import ChatAnthropic


class AnthropicLLM(ChatAnthropic):
    """
    Loading Anthropic model from langchain package and specifying version. Options include:
        model: str = "claude-3-opus-20240229"
        model: str = "claude-3-sonnet-20240229"
    Model comparison: https://docs.anthropic.com/en/docs/about-claude/models#model-comparison
    """

    model_name: str = "claude-3-opus-20240229"
    temp: int = 1
