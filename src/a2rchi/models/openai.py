from langchain_openai import ChatOpenAI


class OpenAILLM(ChatOpenAI):
    """
    Loading the various OpenAI models, most commonly

        model_name = 'gpt-4'
        model_name = 'gpt-3.5-turbo'

    Make sure that the api key is loaded as an environment variable
    and the OpenAI package installed.
    """

    model_name: str = "gpt-4"
    temperature: int = 1
