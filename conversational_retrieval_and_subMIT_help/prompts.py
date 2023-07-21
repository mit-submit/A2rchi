# flake8: noqa
from langchain.prompts.prompt import PromptTemplate

_template = """Given the following conversation and a follow up question, rephrase the follow up question to be a standalone question, in its original language.

Chat History:
{chat_history}
Follow Up Input: {question}
Standalone question:"""
CONDENSE_QUESTION_PROMPT = PromptTemplate.from_template(_template)

prompt_template_old = """Use the following pieces of context to answer the question at the end. If you don't know the answer, just say that you don't know, don't try to make up an answer.

{context}

Question: {question}
Helpful Answer:"""

prompt_template = """You are a conversational chatbot named A2rchi who helps people navigate a computing resource named subMIT. You will be provided context to help you answer their questions. 
Using your linux and computing knowledge, answer the question at the end. Unless otherwise indicated, assume the users are not well versed computing.
 Please do not assume that subMIT machines have anything installed on top of native linux except if the context mensions it.
If you don't know, say "I don't know", if you need to ask a follow up question, please do.

Context: {context} Additionially, it is always preferred to use conda, if possible.

Question: {question}
Helpful Answer:"""

QA_PROMPT = PromptTemplate(
    template=prompt_template, input_variables=["context", "question"]
)