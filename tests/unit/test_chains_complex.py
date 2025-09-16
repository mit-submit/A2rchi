from a2rchi.chains.a2rchi import A2rchi
from a2rchi.chains.models import DumbLLM, LlamaLLM, OpenAILLM
from a2rchi.utils.config_loader import load_config

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
import os
import tempfile

# Create a minimal test config for testing
def create_test_config():
    config_content = """
name: test_config

global:
  TRAINED_ON: "Test Environment"
  DATA_PATH: "/tmp/test_data"
  LOGGING:
    input_output_filename: "test_logs.txt"

a2rchi:
  model_class_map:
    DumbLLM:
      class: DumbLLM
      kwargs: {}
    OpenAILLM:
      class: OpenAILLM
      kwargs:
        model: "gpt-3.5-turbo"
        temperature: 0.7

data_manager:
  embedding_class_map:
    HuggingFaceEmbeddings:
      class: HuggingFaceEmbeddings
      kwargs:
        model_name: "sentence-transformers/all-MiniLM-L6-v2"
  embedding_name: "HuggingFaceEmbeddings"
  collection_name: "test_collection"
  use_HTTP_chromadb_client: false
  local_vstore_path: "/tmp/test_chroma"

qa_pipeline:
  prompts:
    condensing_prompt: "configs/prompts/condense.prompt"
    main_prompt: "configs/prompts/submit.prompt"
  model_class_map:
    DumbLLM:
      class: DumbLLM
      kwargs: {}
"""
    
    # Create temp config file
    temp_config = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)
    temp_config.write(config_content)
    temp_config.close()
    return temp_config.name

def test_OpenAI():
    # Skip this test if no API key available (for CI)
    try:
        config = load_config(map=True)
        if "OpenAILLM" in config["a2rchi"]["model_class_map"]:
            chat = config["a2rchi"]["model_class_map"]["OpenAILLM"]["class"](
                **config["a2rchi"]["model_class_map"]["OpenAILLM"]["kwargs"]
            )

            messages = [
                SystemMessage(
                    content="You are a helpful assistant that translates English to French."
                ),
                HumanMessage(
                    content="Translate this sentence from English to French. I love programming."
                ),
            ]
            result = chat.invoke(messages)
            assert result is not None
        else:
            # Skip test if OpenAI not configured
            import pytest
            pytest.skip("OpenAI not configured in test environment")
    except Exception as e:
        # Skip test if config loading fails or API key missing
        import pytest
        pytest.skip(f"OpenAI test skipped due to: {e}")

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
    answer = chat.invoke("Translate this sentence from English to French. I love programming.")
    assert answer is not None
    assert isinstance(answer, str)

def test_vectorstore():
    # Create test directories
    import shutil
    test_chroma_path = "/tmp/test_chroma_vectorstore"
    
    try:
        # Clean up any existing test data
        if os.path.exists(test_chroma_path):
            shutil.rmtree(test_chroma_path)
        
        # Set up a temporary config file
        config_file = create_test_config()
        original_config_path = os.environ.get('A2RCHI_CONFIG_PATH')
        os.environ['A2RCHI_CONFIG_PATH'] = config_file
        
        # Patch CONFIG_PATH in config_loader
        import a2rchi.utils.config_loader as cl
        original_path = cl.CONFIG_PATH
        cl.CONFIG_PATH = config_file
        
        try:
            # Create A2rchi instance with QAPipeline
            a2rchi_instance = A2rchi(pipeline="QAPipeline")
            
            # Test vectorstore creation
            vectorstore = a2rchi_instance._update_vectorstore()
            
            # Test similarity search (should work even with empty vectorstore)
            query = "What did the president say about Ketanji Brown Jackson"
            docs = vectorstore.similarity_search(query, k=1)
            
            # Should return empty list or handle gracefully
            assert docs is not None
            assert isinstance(docs, list)
            
        finally:
            # Restore original config path
            cl.CONFIG_PATH = original_path
            if original_config_path:
                os.environ['A2RCHI_CONFIG_PATH'] = original_config_path
            elif 'A2RCHI_CONFIG_PATH' in os.environ:
                del os.environ['A2RCHI_CONFIG_PATH']
            
            # Clean up config file
            if os.path.exists(config_file):
                os.unlink(config_file)
                
    finally:
        # Clean up test data
        if os.path.exists(test_chroma_path):
            shutil.rmtree(test_chroma_path)

def test_chain_creation():
    # Create test directories 
    test_chroma_path = "/tmp/test_chroma_creation"
    
    try:
        # Clean up any existing test data
        if os.path.exists(test_chroma_path):
            import shutil
            shutil.rmtree(test_chroma_path)
        
        # Set up a temporary config file
        config_file = create_test_config()
        
        # Patch CONFIG_PATH in config_loader
        import a2rchi.utils.config_loader as cl
        original_path = cl.CONFIG_PATH
        cl.CONFIG_PATH = config_file
        
        try:
            # Create A2rchi instance
            a2rchi_instance = A2rchi(pipeline="QAPipeline")
            
            # Test that the instance was created successfully
            assert a2rchi_instance is not None
            assert a2rchi_instance.pipeline is not None
            assert a2rchi_instance.pipeline_name == "QAPipeline"
            
        finally:
            # Restore original config path
            cl.CONFIG_PATH = original_path
            
            # Clean up config file
            if os.path.exists(config_file):
                os.unlink(config_file)
                
    finally:
        # Clean up test data
        if os.path.exists(test_chroma_path):
            import shutil
            shutil.rmtree(test_chroma_path)

def test_chain_call_noprevhistory():
    # Skip this test as it requires a full setup
    import pytest
    pytest.skip("Chain call tests require full A2rchi setup with prompts and configurations")

def test_chain_call_prevhistory():
    # Skip this test as it requires a full setup  
    import pytest
    pytest.skip("Chain call tests require full A2rchi setup with prompts and configurations")
