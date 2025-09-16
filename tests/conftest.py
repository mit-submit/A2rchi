import pytest
import os
import tempfile
import shutil


@pytest.fixture(scope="session", autouse=True)
def setup_test_config():
    """Set up test configuration for all tests"""
    
    # Create temporary config file
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
  chromadb_host: "localhost"
  chromadb_port: 8000

qa_pipeline:
  prompts:
    condensing_prompt: "configs/prompts/condense.prompt"
    main_prompt: "configs/prompts/submit.prompt"
  model_class_map:
    DumbLLM:
      class: DumbLLM
      kwargs: {}

utils:
  test_setting: true
"""
    
    # Create temporary config file
    temp_config = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)
    temp_config.write(config_content)
    temp_config.close()
    
    # Create test data directory
    os.makedirs("/tmp/test_data", exist_ok=True)
    
    # Set up environment variable if specified
    config_path = os.getenv('A2RCHI_CONFIG_PATH', temp_config.name)
    
    # Monkey patch the config loader
    try:
        import a2rchi.utils.config_loader as cl
        original_path = cl.CONFIG_PATH
        cl.CONFIG_PATH = config_path
        
        yield config_path
        
        # Restore original path
        cl.CONFIG_PATH = original_path
    except ImportError:
        # If config loader can't be imported, just yield the path
        yield config_path
    finally:
        # Clean up
        if os.path.exists(temp_config.name):
            os.unlink(temp_config.name)
        if os.path.exists("/tmp/test_chroma"):
            shutil.rmtree("/tmp/test_chroma")


@pytest.fixture
def skip_if_no_api_key():
    """Skip test if OpenAI API key is not available"""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not available")