import os

import openai
from tenacity import retry, stop_after_attempt, wait_random_exponential


def sliding_window(text, window_size, stride):
    tokens = text.split()
    window_start = 0
    while window_start < len(tokens):
        window_end = min(window_start + window_size, len(tokens))
        yield " ".join(tokens[window_start:window_end])
        window_start += stride

@retry(wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(6))
def embedding_with_backoff(**kwargs):
    return openai.Embedding.create(**kwargs)

def get_embedding(text, model="text-embedding-ada-002", api_key=None):
    if api_key is None:
        openai.api_key = os.environ.get("OPENAI_API_KEY")
    else:
        openai.api_key = api_key
    text = text.replace("\n", " ")
    return embedding_with_backoff(input=[text], model=model)["data"][0]["embedding"]
