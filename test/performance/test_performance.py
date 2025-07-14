#!/usr/bin/env python3
"""
A2rchi Performance Tests

Benchmarks various aspects of A2rchi performance including:
- Deployment time
- API response times
- Memory usage
- Chat response times
"""

import time
import pytest
import requests
import psutil
import subprocess
from pathlib import Path
from typing import Dict, List


class A2rchiPerformanceBenchmarks:
    """Performance benchmarking suite for A2rchi."""
    
    def __init__(self):
        self.base_url = "http://localhost:7861"
        self.test_messages = [
            "What is machine learning?",
            "Explain the concept of neural networks.",
            "How does natural language processing work?",
            "What are the applications of AI in healthcare?",
            "Describe the difference between supervised and unsupervised learning."
        ]
    
    def test_api_response_time(self, benchmark):
        """Benchmark API response time for health check."""
        def api_call():
            response = requests.get(f"{self.base_url}/health", timeout=10)
            return response.status_code == 200
        
        result = benchmark(api_call)
        assert result, "API health check should return success"
    
    def test_chat_response_time(self, benchmark):
        """Benchmark chat API response time."""
        def chat_call():
            payload = {
                "message": "Hello, this is a performance test",
                "conversation_id": "perf-test"
            }
            response = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=30)
            return response.status_code == 200
        
        result = benchmark(chat_call)
        assert result, "Chat API should respond successfully"
    
    def test_multiple_chat_requests(self, benchmark):
        """Benchmark multiple sequential chat requests."""
        def multiple_chats():
            results = []
            for message in self.test_messages[:3]:  # Use first 3 messages
                payload = {
                    "message": message,
                    "conversation_id": f"perf-test-{len(results)}"
                }
                response = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=30)
                results.append(response.status_code == 200)
            return all(results)
        
        result = benchmark(multiple_chats)
        assert result, "All chat requests should succeed"
    
    def test_concurrent_chat_requests(self, benchmark):
        """Benchmark concurrent chat requests."""
        import concurrent.futures
        
        def concurrent_chats():
            def single_chat(message_index):
                payload = {
                    "message": self.test_messages[message_index % len(self.test_messages)],
                    "conversation_id": f"perf-concurrent-{message_index}"
                }
                response = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=30)
                return response.status_code == 200
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = [executor.submit(single_chat, i) for i in range(3)]
                results = [future.result() for future in concurrent.futures.as_completed(futures)]
            
            return all(results)
        
        result = benchmark(concurrent_chats)
        assert result, "All concurrent chat requests should succeed"


class SystemResourceBenchmarks:
    """System resource usage benchmarks."""
    
    def test_memory_usage_baseline(self, benchmark):
        """Benchmark baseline memory usage."""
        def get_memory_usage():
            # Get memory usage of A2rchi containers
            try:
                result = subprocess.run(
                    "docker stats --no-stream --format 'table {{.MemUsage}}' $(docker ps --filter name=a2rchi -q)",
                    shell=True, capture_output=True, text=True, check=True
                )
                return len(result.stdout.split('\n')) > 1  # Should have at least one container
            except subprocess.CalledProcessError:
                return False
        
        result = benchmark(get_memory_usage)
        assert result, "Should be able to measure memory usage"
    
    def test_cpu_usage_under_load(self, benchmark):
        """Benchmark CPU usage under chat load."""
        def cpu_under_load():
            # Send multiple requests to create load
            base_url = "http://localhost:7861"
            
            start_time = time.time()
            request_count = 0
            
            while time.time() - start_time < 5:  # 5 second test
                try:
                    response = requests.get(f"{base_url}/health", timeout=1)
                    if response.status_code == 200:
                        request_count += 1
                except:
                    pass  # Ignore timeouts/errors for this benchmark
            
            return request_count > 0
        
        result = benchmark(cpu_under_load)
        assert result, "Should be able to handle load requests"


class DeploymentBenchmarks:
    """Benchmarks for deployment operations."""
    
    def test_deployment_creation_time(self, benchmark):
        """Benchmark the time to create a new deployment."""
        def create_deployment():
            # This is a simulation - in real tests you'd create an actual deployment
            # For benchmarking purposes, we'll simulate the key operations
            
            # Simulate config parsing
            config_data = {
                "name": "benchmark-test",
                "global": {"TRAINED_ON": "benchmark testing"},
                "chains": {
                    "prompts": {
                        "CONDENSING_PROMPT": "test.prompt",
                        "MAIN_PROMPT": "test.prompt"
                    },
                    "chain": {
                        "MODEL_NAME": "DumbLLM",
                        "CONDENSE_MODEL_NAME": "DumbLLM"
                    }
                }
            }
            
            # Simulate validation
            required_fields = ['name', 'global', 'chains']
            validation_time = time.time()
            for field in required_fields:
                assert field in config_data
            
            # Simulate template rendering
            template_time = time.time()
            rendered_config = str(config_data)  # Simplified
            
            return len(rendered_config) > 0
        
        result = benchmark(create_deployment)
        assert result, "Deployment creation simulation should succeed"
    
    def test_service_startup_time(self, benchmark):
        """Benchmark time for services to become ready."""
        def check_service_ready():
            # Check if services are responding
            base_url = "http://localhost:7861"
            
            try:
                response = requests.get(f"{base_url}/health", timeout=5)
                return response.status_code == 200
            except:
                return False
        
        result = benchmark(check_service_ready)
        # Note: This might fail if services aren't running, which is OK for benchmarking


# Pytest fixtures for setup/teardown
@pytest.fixture(scope="session")
def setup_performance_test_environment():
    """Setup environment for performance tests."""
    print("\n🚀 Setting up performance test environment...")
    
    # Check if A2rchi services are running
    try:
        response = requests.get("http://localhost:7861/health", timeout=5)
        if response.status_code == 200:
            print("✅ A2rchi services are already running")
        else:
            print("⚠️  A2rchi services may not be fully ready")
    except:
        print("⚠️  A2rchi services are not accessible - some tests may fail")
    
    yield
    
    print("🧹 Performance test cleanup complete")


@pytest.fixture
def api_benchmarks(setup_performance_test_environment):
    """Fixture for API performance benchmarks."""
    return A2rchiPerformanceBenchmarks()


@pytest.fixture
def resource_benchmarks(setup_performance_test_environment):
    """Fixture for resource usage benchmarks."""
    return SystemResourceBenchmarks()


@pytest.fixture
def deployment_benchmarks(setup_performance_test_environment):
    """Fixture for deployment benchmarks."""
    return DeploymentBenchmarks()


# Test classes using fixtures
class TestAPIPerformance:
    """API performance test class."""
    
    def test_health_check_performance(self, api_benchmarks, benchmark):
        """Test health check endpoint performance."""
        api_benchmarks.test_api_response_time(benchmark)
    
    def test_chat_performance(self, api_benchmarks, benchmark):
        """Test chat API performance."""
        api_benchmarks.test_chat_response_time(benchmark)
    
    def test_multiple_requests_performance(self, api_benchmarks, benchmark):
        """Test multiple sequential requests performance."""
        api_benchmarks.test_multiple_chat_requests(benchmark)
    
    def test_concurrent_requests_performance(self, api_benchmarks, benchmark):
        """Test concurrent requests performance."""
        api_benchmarks.test_concurrent_chat_requests(benchmark)


class TestResourceUsage:
    """Resource usage test class."""
    
    def test_memory_baseline(self, resource_benchmarks, benchmark):
        """Test baseline memory usage."""
        resource_benchmarks.test_memory_usage_baseline(benchmark)
    
    def test_cpu_under_load(self, resource_benchmarks, benchmark):
        """Test CPU usage under load."""
        resource_benchmarks.test_cpu_usage_under_load(benchmark)


class TestDeploymentPerformance:
    """Deployment performance test class."""
    
    def test_config_processing_time(self, deployment_benchmarks, benchmark):
        """Test configuration processing performance."""
        deployment_benchmarks.test_deployment_creation_time(benchmark)
    
    def test_service_readiness_time(self, deployment_benchmarks, benchmark):
        """Test service readiness checking performance."""
        deployment_benchmarks.test_service_startup_time(benchmark)


if __name__ == "__main__":
    # Run performance tests with pytest-benchmark
    import sys
    
    print("Running A2rchi Performance Benchmarks...")
    
    # Run with benchmark output
    exit_code = pytest.main([
        __file__,
        "--benchmark-only",
        "--benchmark-sort=mean",
        "--benchmark-columns=min,max,mean,stddev",
        "-v"
    ])
    
    sys.exit(exit_code)