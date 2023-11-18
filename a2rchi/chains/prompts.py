# flake8: noqa
from langchain.prompts.prompt import PromptTemplate
from A2rchi.utils.config_loader import Config_Loader

config = Config_Loader().config["chains"]["prompts"]

def read_prompt(prompt_filepath, is_condense_prompt=False, is_main_prompt=False):
    with open(prompt_filepath, "r") as f:
        raw_prompt = f.read()

    prompt = ""
    for line in raw_prompt.split("\n"):
        if len(line.lstrip())>0 and line.lstrip()[0:1] != "#":
            prompt += line + "\n"

    if is_condense_prompt and ("{chat_history}" not in prompt or "{question}" not in prompt):
        raise ValueError("""Condensing prompt must contain \"{chat_history}\" and \"{question}\" tags. Instead, found prompt to be:
                         """ + prompt)
    if is_main_prompt and ("{context}" not in prompt or "{question}" not in prompt):
        raise ValueError("""Condensing prompt must contain \"{context}\" and \"{question}\" tags. Instead, found prompt to be:
                         """ + prompt)

    return prompt

QA_PROMPT = PromptTemplate(
    template=read_prompt(config["MAIN_PROMPT"], is_main_prompt=True), input_variables=["context", "question"]
)

CONDENSE_QUESTION_PROMPT = PromptTemplate(
    template=read_prompt(config["CONDENSING_PROMPT"], is_condense_prompt=True), input_variables=["chat_history", "question"]
)
