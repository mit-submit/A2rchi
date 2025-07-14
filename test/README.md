# A2rchi Testing Suite

This directory contains comprehensive tests for A2rchi, including unit tests, integration tests, and performance benchmarks.

## Overview

The testing suite is designed to validate A2rchi functionality across different deployment scenarios:

- **Docker deployments**
- **Podman deployments** (versions 4.9.4+ and 5.4.0+)
- **GPU-enabled deployments**
- **Performance characteristics**

## Test Structure

```
test/
├── README.md                           # This file
├── run_integration_tests.sh           # Local test runner script
├── __init__.py                        # Python package marker
├── test_chains.py                     # Unit tests for chain functionality
├── test_interfaces.py                 # Unit tests for interfaces
├── test_mailbox.py                    # Unit tests for mailbox functionality
├── integration/                       # Integration tests
│   ├── test_deployment.py            # Main integration test suite
│   ├── health_checks.py              # Service health check utilities
│   ├── configs/                      # Test configuration files
│   │   ├── ci_test_docker.yaml      # Docker test config
│   │   ├── ci_test_podman.yaml      # Podman test config
│   │   └── ci_test_gpu.yaml         # GPU test config
│   ├── logs/                         # Test execution logs (generated)
│   └── outputs/                      # Test results and artifacts (generated)
└── performance/                       # Performance benchmarks
    └── test_performance.py           # Performance test suite
```

## Running Tests Locally

### Prerequisites

1. **Python 3.10+** with A2rchi installed:
   ```bash
   pip install .
   ```

2. **Container Engine**: At least one of:
   - Docker 24+
   - Podman 4.9.4+

3. **Test Dependencies**:
   ```bash
   pip install pytest pytest-cov pytest-benchmark requests psutil
   ```

### Quick Start

Run all tests with the convenience script:

```bash
# Run all tests (unit + integration)
./test/run_integration_tests.sh

# Run specific test types
./test/run_integration_tests.sh unit          # Unit tests only
./test/run_integration_tests.sh docker        # Docker integration tests
./test/run_integration_tests.sh podman        # Podman integration tests
./test/run_integration_tests.sh performance   # Performance benchmarks

# Options
./test/run_integration_tests.sh --no-cleanup all    # Skip initial cleanup
./test/run_integration_tests.sh --keep docker       # Keep test deployment running
```

### Manual Test Execution

#### Unit Tests
```bash
python -m pytest test/ -v --ignore=test/integration --ignore=test/performance
```

#### Integration Tests
```bash
# Docker tests
CONTAINER_ENGINE=docker COMPOSE_CMD="docker compose" python test/integration/test_deployment.py

# Podman tests
CONTAINER_ENGINE=podman COMPOSE_CMD="podman compose" python test/integration/test_deployment.py

# Podman with legacy compose
CONTAINER_ENGINE=podman COMPOSE_CMD="podman-compose" python test/integration/test_deployment.py
```

#### Performance Tests
```bash
python -m pytest test/performance/ --benchmark-only --benchmark-sort=mean
```

## GitHub Actions CI/CD

The repository includes GitHub Actions workflows for automated testing:

### Workflow: `integration-tests.yml`

**Triggers:**
- Push to `main` or `develop` branches
- Pull requests to `main` or `develop`
- Manual workflow dispatch

**Test Matrix:**
- **docker**: Latest Docker with native compose
- **podman-4.9**: Podman 4.9.4 with podman-compose
- **podman-5.4**: Podman 5.4.0+ with native compose

**Test Stages:**

1. **Unit Tests**
   - Python 3.10 environment
   - Full test coverage reporting
   - Codecov integration

2. **Integration Tests**
   - Matrix testing across container engines
   - Service health validation
   - API endpoint testing
   - Deployment lifecycle testing

3. **Security Scan**
   - Trivy vulnerability scanning
   - SARIF report generation
   - GitHub Security tab integration

4. **Performance Tests** (main branch only)
   - Benchmark collection
   - Performance regression detection
   - Historical comparison

### Test Artifacts

GitHub Actions uploads test artifacts including:
- Test execution logs
- Performance benchmark results
- Container deployment outputs
- Error diagnostics

## Test Configuration

### Test Secrets

Tests use dummy secrets stored in `~/.a2rchi-test-secrets/`:
- `openai_api_key.txt`: Dummy OpenAI API key
- `anthropic_api_key.txt`: Dummy Anthropic API key  
- `pg_password.txt`: Test database password

### Test Configurations

Located in `test/integration/configs/`:

- **`ci_test_docker.yaml`**: Docker-optimized configuration
- **`ci_test_podman.yaml`**: Podman-optimized configuration (different ports)
- **`ci_test_gpu.yaml`**: GPU-enabled configuration for testing

All test configs use `DumbLLM` for predictable responses and `HuggingFaceEmbeddings` to avoid API dependencies.

## Test Components

### Integration Test Suite (`test_deployment.py`)

Tests the complete deployment lifecycle:

1. **Prerequisites Check**
   - Python/A2rchi installation
   - Container engine availability
   - Version compatibility (for Podman)

2. **Deployment Creation**
   - Configuration validation
   - Container orchestration
   - Volume management
   - Service dependencies

3. **Service Health**
   - Container status monitoring
   - Health check endpoints
   - Database connectivity

4. **API Functionality**
   - HTTP endpoint testing
   - Chat API validation
   - Error handling

5. **Cleanup**
   - Container removal
   - Volume cleanup
   - Resource deallocation

### Health Check Utilities (`health_checks.py`)

Comprehensive health monitoring:

- **ServiceHealthChecker**: Container status and logs
- **APIHealthChecker**: HTTP endpoint validation
- **DatabaseHealthChecker**: PostgreSQL and ChromaDB connectivity
- **SystemResourceChecker**: Resource usage monitoring

### Performance Benchmarks (`test_performance.py`)

Performance validation using pytest-benchmark:

- **API Response Times**: Health checks, chat responses
- **Concurrent Load**: Multiple simultaneous requests
- **Resource Usage**: Memory and CPU utilization
- **Deployment Speed**: Service startup times

## Troubleshooting

### Common Issues

1. **Port Conflicts**
   - Test configs use different ports to avoid conflicts
   - Check for existing A2rchi deployments: `docker ps` or `podman ps`

2. **Permission Issues (Podman)**
   - Ensure rootless Podman is configured: `podman system info`
   - Check cgroup settings: `podman info | grep cgroup`

3. **Container Engine Not Ready**
   - Docker: `sudo systemctl start docker`
   - Podman: `systemctl --user start podman.socket`

4. **Test Secrets Missing**
   - Run setup: `mkdir -p ~/.a2rchi-test-secrets && echo "dummy" > ~/.a2rchi-test-secrets/openai_api_key.txt`

### Debug Mode

Enable verbose logging in tests:

```bash
# Integration tests with debug output
CONTAINER_ENGINE=docker python test/integration/test_deployment.py --verbose

# Performance tests with detailed output  
python -m pytest test/performance/ --benchmark-verbose
```

### Log Locations

Test execution logs are saved to:
- Integration tests: `test/integration/logs/`
- Test results: `test/integration/outputs/`
- Container logs: Retrieved via `docker logs` or `podman logs`

## Contributing

### Adding New Tests

1. **Unit Tests**: Add to existing `test_*.py` files or create new ones
2. **Integration Tests**: Extend `test_deployment.py` or create specialized test modules
3. **Performance Tests**: Add benchmarks to `test_performance.py`

### Test Naming Conventions

- Unit tests: `test_function_name()`
- Integration tests: `test_deployment_aspect()`
- Performance tests: `test_performance_metric()`

### CI/CD Considerations

- Tests should be deterministic and not rely on external services
- Use appropriate timeouts for container operations
- Include cleanup in test teardown
- Consider resource constraints in GitHub Actions runners

## Support

For test-related issues:

1. Check the test logs in `test/integration/logs/`
2. Verify container engine installation and configuration
3. Ensure A2rchi dependencies are properly installed
4. Review GitHub Actions workflow runs for CI/CD issues

For questions about the testing framework, please refer to the main A2rchi documentation or open an issue in the repository.