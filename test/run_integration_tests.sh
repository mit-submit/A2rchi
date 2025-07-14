#!/bin/bash
# Local Integration Test Runner for A2rchi
#
# This script runs integration tests locally for both Docker and Podman,
# providing an easy way to validate A2rchi functionality before CI/CD.

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${TEST_DIR}/.." && pwd)"
LOGS_DIR="${TEST_DIR}/integration/logs"
OUTPUTS_DIR="${TEST_DIR}/integration/outputs"

# Test configuration
DOCKER_TEST_NAME="a2rchi-docker-test"
PODMAN_TEST_NAME="a2rchi-podman-test"
SECRETS_DIR="$HOME/.a2rchi-test-secrets"

# Functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_prerequisites() {
    log_info "Checking prerequisites..."
    
    # Check Python and A2rchi
    if ! python -c "import a2rchi" 2>/dev/null; then
        log_error "A2rchi is not installed. Run 'pip install .' from the project root."
        exit 1
    fi
    
    # Check Docker
    DOCKER_AVAILABLE=false
    if command -v docker &> /dev/null; then
        if docker info &> /dev/null; then
            DOCKER_AVAILABLE=true
            log_success "Docker is available"
        else
            log_warning "Docker is installed but not running"
        fi
    else
        log_warning "Docker is not installed"
    fi
    
    # Check Podman
    PODMAN_AVAILABLE=false
    if command -v podman &> /dev/null; then
        PODMAN_AVAILABLE=true
        PODMAN_VERSION=$(podman --version | cut -d' ' -f3)
        log_success "Podman is available (version: $PODMAN_VERSION)"
        
        # Check podman-compose
        if command -v podman-compose &> /dev/null; then
            log_success "podman-compose is available"
        else
            log_warning "podman-compose is not installed"
        fi
    else
        log_warning "Podman is not installed"
    fi
    
    if [ "$DOCKER_AVAILABLE" = false ] && [ "$PODMAN_AVAILABLE" = false ]; then
        log_error "Neither Docker nor Podman is available. Install at least one to run tests."
        exit 1
    fi
}

setup_test_environment() {
    log_info "Setting up test environment..."
    
    # Create directories
    mkdir -p "$LOGS_DIR" "$OUTPUTS_DIR"
    
    # Create test secrets
    mkdir -p "$SECRETS_DIR"
    echo "dummy_key_for_testing" > "$SECRETS_DIR/openai_api_key.txt"
    echo "dummy_key_for_testing" > "$SECRETS_DIR/anthropic_api_key.txt"
    echo "test_password_123" > "$SECRETS_DIR/pg_password.txt"
    
    log_success "Test environment setup complete"
}

cleanup_test_deployments() {
    log_info "Cleaning up any existing test deployments..."
    
    # Docker cleanup
    if [ "$DOCKER_AVAILABLE" = true ]; then
        docker ps -q --filter "name=a2rchi-.*-test" | xargs -r docker stop 2>/dev/null || true
        docker ps -aq --filter "name=a2rchi-.*-test" | xargs -r docker rm 2>/dev/null || true
        a2rchi delete --name "$DOCKER_TEST_NAME" 2>/dev/null || true
    fi
    
    # Podman cleanup
    if [ "$PODMAN_AVAILABLE" = true ]; then
        podman ps -q --filter "name=a2rchi-.*-test" | xargs -r podman stop 2>/dev/null || true
        podman ps -aq --filter "name=a2rchi-.*-test" | xargs -r podman rm 2>/dev/null || true
        a2rchi delete --name "$PODMAN_TEST_NAME" 2>/dev/null || true
    fi
}

run_docker_tests() {
    if [ "$DOCKER_AVAILABLE" = false ]; then
        log_warning "Skipping Docker tests - Docker not available"
        return 0
    fi
    
    log_info "Running Docker integration tests..."
    
    export CONTAINER_ENGINE="docker"
    export COMPOSE_CMD="docker compose"
    export TEST_NAME="$DOCKER_TEST_NAME"
    
    cd "$PROJECT_ROOT"
    
    if python "$TEST_DIR/integration/test_deployment.py"; then
        log_success "Docker tests PASSED"
        return 0
    else
        log_error "Docker tests FAILED"
        return 1
    fi
}

run_podman_tests() {
    if [ "$PODMAN_AVAILABLE" = false ]; then
        log_warning "Skipping Podman tests - Podman not available"
        return 0
    fi
    
    log_info "Running Podman integration tests..."
    
    export CONTAINER_ENGINE="podman"
    export COMPOSE_CMD="podman compose"
    export TEST_NAME="$PODMAN_TEST_NAME"
    
    # Determine podman version and compose command
    PODMAN_VERSION=$(podman --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
    if command -v podman-compose &> /dev/null; then
        # Check if version is less than 5.4.0
        if printf '%s\n5.4.0\n' "$PODMAN_VERSION" | sort -V | head -n1 | grep -q "^$PODMAN_VERSION$"; then
            export COMPOSE_CMD="podman-compose"
            log_info "Using podman-compose for compatibility"
        fi
    fi
    
    export PODMAN_VERSION="$PODMAN_VERSION"
    
    cd "$PROJECT_ROOT"
    
    if python "$TEST_DIR/integration/test_deployment.py"; then
        log_success "Podman tests PASSED"
        return 0
    else
        log_error "Podman tests FAILED"
        return 1
    fi
}

run_unit_tests() {
    log_info "Running unit tests..."
    
    cd "$PROJECT_ROOT"
    
    if python -m pytest "$TEST_DIR" -v --ignore="$TEST_DIR/integration" --ignore="$TEST_DIR/performance"; then
        log_success "Unit tests PASSED"
        return 0
    else
        log_error "Unit tests FAILED"
        return 1
    fi
}

run_performance_tests() {
    log_info "Running performance tests..."
    
    cd "$PROJECT_ROOT"
    
    if python -m pytest "$TEST_DIR/performance" --benchmark-only --benchmark-sort=mean -v; then
        log_success "Performance tests PASSED"
        return 0
    else
        log_error "Performance tests FAILED"
        return 1
    fi
}

show_test_results() {
    log_info "Test Results Summary:"
    echo
    
    if [ -f "$OUTPUTS_DIR/test_results_docker.json" ]; then
        echo "📊 Docker Test Results:"
        python -c "
import json
with open('$OUTPUTS_DIR/test_results_docker.json') as f:
    data = json.load(f)
    print(f\"  Overall Success: {data.get('overall_success', 'Unknown')}\")
    tests = data.get('tests', {})
    passed = sum(1 for t in tests.values() if isinstance(t, dict) and t.get('status') == 'PASS')
    total = len(tests)
    print(f\"  Tests Passed: {passed}/{total}\")
" 2>/dev/null || echo "  Results file not found or invalid"
        echo
    fi
    
    if [ -f "$OUTPUTS_DIR/test_results_podman.json" ]; then
        echo "📊 Podman Test Results:"
        python -c "
import json
with open('$OUTPUTS_DIR/test_results_podman.json') as f:
    data = json.load(f)
    print(f\"  Overall Success: {data.get('overall_success', 'Unknown')}\")
    tests = data.get('tests', {})
    passed = sum(1 for t in tests.values() if isinstance(t, dict) and t.get('status') == 'PASS')
    total = len(tests)
    print(f\"  Tests Passed: {passed}/{total}\")
" 2>/dev/null || echo "  Results file not found or invalid"
        echo
    fi
}

print_usage() {
    echo "Usage: $0 [OPTIONS] [TEST_TYPE]"
    echo
    echo "TEST_TYPE:"
    echo "  all          Run all tests (default)"
    echo "  unit         Run unit tests only"
    echo "  docker       Run Docker integration tests only"
    echo "  podman       Run Podman integration tests only"
    echo "  performance  Run performance tests only"
    echo
    echo "OPTIONS:"
    echo "  -h, --help   Show this help message"
    echo "  --no-cleanup Skip cleanup of existing test deployments"
    echo "  --keep       Keep test deployments running after tests"
    echo
    echo "Examples:"
    echo "  $0                    # Run all tests"
    echo "  $0 docker            # Run only Docker tests"
    echo "  $0 unit              # Run only unit tests"
    echo "  $0 --no-cleanup all  # Run all tests without initial cleanup"
}

# Main execution
main() {
    # Parse arguments
    NO_CLEANUP=false
    KEEP_DEPLOYMENTS=false
    TEST_TYPE="all"
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            -h|--help)
                print_usage
                exit 0
                ;;
            --no-cleanup)
                NO_CLEANUP=true
                shift
                ;;
            --keep)
                KEEP_DEPLOYMENTS=true
                shift
                ;;
            all|unit|docker|podman|performance)
                TEST_TYPE="$1"
                shift
                ;;
            *)
                log_error "Unknown option: $1"
                print_usage
                exit 1
                ;;
        esac
    done
    
    # Start tests
    echo "🧪 A2rchi Integration Test Runner"
    echo "=================================="
    echo
    
    check_prerequisites
    setup_test_environment
    
    if [ "$NO_CLEANUP" = false ]; then
        cleanup_test_deployments
    fi
    
    # Track test results
    TESTS_FAILED=0
    
    case $TEST_TYPE in
        "all")
            run_unit_tests || ((TESTS_FAILED++))
            run_docker_tests || ((TESTS_FAILED++))
            run_podman_tests || ((TESTS_FAILED++))
            ;;
        "unit")
            run_unit_tests || ((TESTS_FAILED++))
            ;;
        "docker")
            run_docker_tests || ((TESTS_FAILED++))
            ;;
        "podman")
            run_podman_tests || ((TESTS_FAILED++))
            ;;
        "performance")
            run_performance_tests || ((TESTS_FAILED++))
            ;;
    esac
    
    # Cleanup unless --keep specified
    if [ "$KEEP_DEPLOYMENTS" = false ]; then
        cleanup_test_deployments
    fi
    
    # Show results
    show_test_results
    
    # Final summary
    echo "=================================="
    if [ $TESTS_FAILED -eq 0 ]; then
        log_success "All tests completed successfully! 🎉"
        exit 0
    else
        log_error "$TESTS_FAILED test suite(s) failed ❌"
        exit 1
    fi
}

# Run main function
main "$@"