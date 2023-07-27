from langchain.schema import AIMessage, HumanMessage, SystemMessage

from chains.chain import Chain
from chains.models import DumbLLM, LlamaLLM, OpenAILLM

from utils.config_loader import Config_Loader
config = Config_Loader().config["chains"]["chain"]
global_config = Config_Loader().config["global"]

def test_OpenAI():
    chat = OpenAILLM(**config["MODEL_CLASS_MAP"]["OpenAILLM"]["kwargs"])

    messages = [
        SystemMessage(
            content="You are a helpful assistant that translates English to French."
        ),
        HumanMessage(
            content="Translate this sentence from English to French. I love programming."
        ),
    ]
    assert chat(messages) is not None

#def test_LlambdaAI():
#    chat = LlamaLLM(**config["MODEL_CLASS_MAP"]["LlamaLLM"]["kwargs"])
#
#    messages = [
#        SystemMessage(
#            content="You are a helpful assistant that translates English to French."
#        ),
#        HumanMessage(
#            content="Translate this sentence from English to French. I love programming."
#        ),
#    ]
#    assert chat(messages) is not None

def test_DumbAI():
    chat = DumbLLM()
    answer = chat._call("Translate this sentence from English to French. I love programming.")

    assert answer is not None

def test_vectorstore():
    c1 = Chain()
    db = c1.vectorstore

    query = "What did the president say about Ketanji Brown Jackson"
    docs = db.similarity_search(query)

    assert docs is not None

def test_chain_creation():
    c1 = Chain()
    question = "What did the president say about Ketanji Brown Jackson"
    prev_history = []
    result = c1.chain({"question": question, "chat_history": prev_history})
    assert result["answer"] is not None

def test_chain_call_noprevhistory():
    c1 = Chain()
    question = "What did the president say about Ketanji Brown Jackson"
    result = c1([("User", question)])
    assert result["answer"] is not None

def test_chain_call_prevhistory():
    c1 = Chain()
    question = "What did the president say about Ketanji Brown Jackson"
    answer = "Don't know"
    follow_up = "Could you elaborate?"
    result = c1([("User", question), ("A2rchi", answer), ("User", follow_up)])
    assert result["answer"] is not None
    

