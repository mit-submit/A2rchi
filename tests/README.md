# Unit Tests

This directory contains unit tests for the A2rchi project.

## Current Status

The unit tests have been updated to work with the current codebase. Many of the original tests were referencing outdated modules and interfaces that no longer exist, so they have been updated or marked as skipped.

### Working Tests

- `test_models_import`: Tests that core model classes can be imported
- `test_DumbAI`: Tests the DumbLLM model functionality

### Skipped Tests

- `test_OpenAI`: Requires OPENAI_API_KEY environment variable
- `test_chain_creation`: Requires full A2rchi configuration with prompts
- `test_vectorstore`: Requires ChromaDB setup and configuration
- `test_chain_call_*`: Require full pipeline configuration
- `test_cleo_overall`: Cleo interface no longer exists
- `test_mailbox`: Mailbox interface needs refactoring

## Running Tests

### Local Development

```bash
# Install test dependencies
pip install pytest pytest-cov

# Run all tests
pytest tests/unit/

# Run specific test
pytest tests/unit/test_chains.py::test_DumbAI -v

# Run with coverage
pytest tests/unit/ --cov=a2rchi --cov-report=html
```

### CI/CD

Tests run automatically on:
- Push to main/develop branches
- Pull requests to main/develop branches

The GitHub workflow is configured to:
- Test on Python 3.10, 3.11, and 3.12
- Install all necessary dependencies
- Create test configuration files
- Run tests with proper environment setup

## Test Configuration

Tests use a temporary configuration file created by `conftest.py` to avoid dependency on system configuration files.

## Future Improvements

To expand test coverage:

1. **Add integration tests** for full A2rchi pipeline functionality
2. **Mock external dependencies** (APIs, databases) for more comprehensive unit testing
3. **Add tests for new modules** as they are developed
4. **Create fixtures** for common test data and configurations
5. **Add performance tests** for critical paths