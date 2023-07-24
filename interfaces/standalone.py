import os
import pandas as pd
import openai
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
import csv
import io
from utils.embedding_utils import get_embedding
from tenacity import retry, stop_after_attempt, wait_random_exponential

MAX_LENGTH = 1000
n_relevant_chunks = 3

with open("./config/system_prompt_submit.txt") as f:
    """
    Load system prompt.
    """
    system_prompt = " ".join(line.rstrip() for line in f)

with open("./config/context_prompt_submit.txt") as f:
    """
    Load context prompt.
    """
    context_prompt = " ".join(line.rstrip() for line in f)

def semantic_search(query_embedding, embeddings):
    """
    Indices of embeddings ranked by similarity to query embedding.
    """
    similarities = cosine_similarity([query_embedding], embeddings)[0]
    ranked_indices = np.argsort(-similarities)
    return ranked_indices

@retry(wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(6))

def completion_with_backoff(**kwargs):
    """
    Chat completion with exponential backoff to prevent rate limiting.
    """
    return openai.ChatCompletion.create(**kwargs)

#def answer(chunk, question, api_key=None, model="gpt-3.5-turbo", max_tokens=300, temperature=0.25):
def answer(chunk, question, api_key=None, model="gpt-4", max_tokens=5000, temperature=0.25):
    """
    Find answer to query given context chunk.
    """
    if api_key is None: # Use API key that is provided by the server itself
        openai.api_key = os.environ.get("OPENAI_API_KEY")
    else:               # Ask user to provide their own API key
        openai.api_key = api_key

    prompt = f"Use the following context to answer the question at the end.\nContext: {chunk}.\n{context_prompt}\nQuestion: {question}"
    response = completion_with_backoff(
        model=model,
        messages = [{"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}],
        max_tokens = max_tokens, n = 1, temperature = temperature,
    )
    return response["choices"][0]["message"]["content"]

def run(query, api_key=None):
    """
    Run the subMIT support chat robot.
    """
    if not query:
        return "Enter your question in the box."

    if len(query) > MAX_LENGTH:
        return "Please, shorten your question."
    else:        
        # Load local files
        df_text = pd.read_csv("./data/db/text_chunks.csv")
        embeddings = np.load("./data/db/embeddings.npy")
        query_embedding = get_embedding(query, api_key=api_key)
        ranked_indices = semantic_search(np.array(query_embedding), embeddings)
        most_relevant_chunk = " ".join(df_text.loc[ranked_indices[:n_relevant_chunks], "text_chunks"].values.flatten())
        return answer(most_relevant_chunk, query, api_key)
