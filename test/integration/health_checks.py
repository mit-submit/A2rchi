#!/usr/bin/env python3
"""
Health Check Utilities for A2rchi Integration Tests

Provides utilities for checking service health, API endpoints,
and overall system functionality.
"""

import time
import requests
import subprocess
import json
from typing import Dict, List, Tuple


class ServiceHealthChecker:
    """Utility class for checking A2rchi service health."""
    
    def __init__(self, container_engine: str = "docker", deployment_name: str = "a2rchi"):
        self.container_engine = container_engine
        self.deployment_name = deployment_name
    
    def get_container_status(self) -> Dict[str, str]:
        """Get the status of all containers for this deployment."""
        if self.container_engine == "docker":
            cmd = f"docker ps --filter name={self.deployment_name} --format 'json'"
        else:
            cmd = f"podman ps --filter name={self.deployment_name} --format 'json'"
        
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
            containers = {}
            
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    container_info = json.loads(line)
                    name = container_info.get('Names', '')
                    status = container_info.get('Status', '')
                    containers[name] = status
            
            return containers
            
        except subprocess.CalledProcessError as e:
            print(f"Failed to get container status: {e}")
            return {}
    
    def wait_for_container_health(self, container_name: str, timeout: int = 300) -> bool:
        """Wait for a specific container to become healthy."""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            containers = self.get_container_status()
            
            if container_name in containers:
                status = containers[container_name]
                if "healthy" in status.lower() or "up" in status.lower():
                    return True
            
            print(f"Waiting for {container_name} to become healthy...")
            time.sleep(10)
        
        print(f"Timeout waiting for {container_name} to become healthy")
        return False
    
    def check_all_services_healthy(self, required_services: List[str] = None) -> Tuple[bool, Dict]:
        """Check that all required services are healthy."""
        if required_services is None:
            required_services = [
                f"chat-{self.deployment_name}",
                f"chromadb-{self.deployment_name}",
                f"postgres-{self.deployment_name}"
            ]
        
        containers = self.get_container_status()
        results = {}
        all_healthy = True
        
        for service in required_services:
            if service in containers:
                status = containers[service]
                is_healthy = "healthy" in status.lower() or "up" in status.lower()
                results[service] = {
                    "present": True,
                    "healthy": is_healthy,
                    "status": status
                }
                if not is_healthy:
                    all_healthy = False
            else:
                results[service] = {
                    "present": False,
                    "healthy": False,
                    "status": "Not found"
                }
                all_healthy = False
        
        return all_healthy, results
    
    def get_container_logs(self, container_name: str, lines: int = 50) -> str:
        """Get recent logs from a container."""
        if self.container_engine == "docker":
            cmd = f"docker logs --tail {lines} {container_name}"
        else:
            cmd = f"podman logs --tail {lines} {container_name}"
        
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
            return result.stdout
        except subprocess.CalledProcessError as e:
            return f"Failed to get logs: {e}"


class APIHealthChecker:
    """Utility class for checking A2rchi API health."""
    
    def __init__(self, base_url: str = "http://localhost:7861"):
        self.base_url = base_url.rstrip('/')
    
    def check_endpoint_health(self, endpoint: str, timeout: int = 10) -> Tuple[bool, Dict]:
        """Check if a specific endpoint is responding."""
        url = f"{self.base_url}{endpoint}"
        
        try:
            response = requests.get(url, timeout=timeout)
            return True, {
                "status_code": response.status_code,
                "response_time": response.elapsed.total_seconds(),
                "content_length": len(response.content)
            }
        except requests.exceptions.RequestException as e:
            return False, {"error": str(e)}
    
    def check_main_page(self) -> Tuple[bool, Dict]:
        """Check if the main chat page is accessible."""
        return self.check_endpoint_health("/")
    
    def check_health_endpoint(self) -> Tuple[bool, Dict]:
        """Check if the health endpoint is responding."""
        return self.check_endpoint_health("/health")
    
    def check_api_status(self) -> Tuple[bool, Dict]:
        """Check if the API status endpoint is responding."""
        return self.check_endpoint_health("/api/status")
    
    def test_chat_api(self, test_message: str = "Hello, this is a test") -> Tuple[bool, Dict]:
        """Test the chat API with a simple message."""
        url = f"{self.base_url}/api/chat"
        
        payload = {
            "message": test_message,
            "conversation_id": "test-conversation"
        }
        
        try:
            response = requests.post(url, json=payload, timeout=30)
            
            if response.status_code == 200:
                try:
                    response_data = response.json()
                    return True, {
                        "status_code": response.status_code,
                        "response": response_data,
                        "response_time": response.elapsed.total_seconds()
                    }
                except json.JSONDecodeError:
                    return False, {
                        "status_code": response.status_code,
                        "error": "Invalid JSON response"
                    }
            else:
                return False, {
                    "status_code": response.status_code,
                    "error": f"HTTP {response.status_code}"
                }
                
        except requests.exceptions.RequestException as e:
            return False, {"error": str(e)}
    
    def comprehensive_health_check(self) -> Dict[str, Dict]:
        """Run a comprehensive health check on all API endpoints."""
        results = {}
        
        # Check main page
        healthy, data = self.check_main_page()
        results["main_page"] = {"healthy": healthy, "data": data}
        
        # Check health endpoint
        healthy, data = self.check_health_endpoint()
        results["health_endpoint"] = {"healthy": healthy, "data": data}
        
        # Check API status
        healthy, data = self.check_api_status()
        results["api_status"] = {"healthy": healthy, "data": data}
        
        # Test chat API
        healthy, data = self.test_chat_api()
        results["chat_api"] = {"healthy": healthy, "data": data}
        
        return results


class DatabaseHealthChecker:
    """Utility class for checking database connectivity and health."""
    
    def __init__(self, container_engine: str = "docker", deployment_name: str = "a2rchi"):
        self.container_engine = container_engine
        self.deployment_name = deployment_name
    
    def check_postgres_health(self) -> Tuple[bool, Dict]:
        """Check PostgreSQL database health."""
        postgres_container = f"postgres-{self.deployment_name}"
        
        if self.container_engine == "docker":
            cmd = f"docker exec {postgres_container} pg_isready -U a2rchi"
        else:
            cmd = f"podman exec {postgres_container} pg_isready -U a2rchi"
        
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
            return True, {"output": result.stdout, "status": "ready"}
        except subprocess.CalledProcessError as e:
            return False, {"error": str(e), "status": "not ready"}
    
    def check_chromadb_health(self) -> Tuple[bool, Dict]:
        """Check ChromaDB health."""
        # ChromaDB typically runs on port 8000
        try:
            response = requests.get("http://localhost:8000/api/v1/heartbeat", timeout=10)
            if response.status_code == 200:
                return True, {"status": "healthy", "response": response.json()}
            else:
                return False, {"status": "unhealthy", "status_code": response.status_code}
        except requests.exceptions.RequestException as e:
            return False, {"status": "unreachable", "error": str(e)}
    
    def comprehensive_db_check(self) -> Dict[str, Dict]:
        """Run comprehensive database health checks."""
        results = {}
        
        # Check PostgreSQL
        healthy, data = self.check_postgres_health()
        results["postgres"] = {"healthy": healthy, "data": data}
        
        # Check ChromaDB
        healthy, data = self.check_chromadb_health()
        results["chromadb"] = {"healthy": healthy, "data": data}
        
        return results


class SystemResourceChecker:
    """Utility class for checking system resources and performance."""
    
    def __init__(self, container_engine: str = "docker", deployment_name: str = "a2rchi"):
        self.container_engine = container_engine
        self.deployment_name = deployment_name
    
    def get_container_stats(self) -> Dict[str, Dict]:
        """Get resource usage statistics for containers."""
        if self.container_engine == "docker":
            cmd = f"docker stats --no-stream --format 'table {{{{.Name}}}}\\t{{{{.CPUPerc}}}}\\t{{{{.MemUsage}}}}\\t{{{{.NetIO}}}}\\t{{{{.BlockIO}}}}' $(docker ps --filter name={self.deployment_name} -q)"
        else:
            cmd = f"podman stats --no-stream --format 'table {{{{.Name}}}}\\t{{{{.CPUPerc}}}}\\t{{{{.MemUsage}}}}\\t{{{{.NetIO}}}}\\t{{{{.BlockIO}}}}' $(podman ps --filter name={self.deployment_name} -q)"
        
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
            lines = result.stdout.strip().split('\n')[1:]  # Skip header
            
            stats = {}
            for line in lines:
                if line.strip():
                    parts = line.split('\t')
                    if len(parts) >= 5:
                        stats[parts[0]] = {
                            "cpu_percent": parts[1],
                            "memory_usage": parts[2],
                            "network_io": parts[3],
                            "block_io": parts[4]
                        }
            
            return stats
            
        except subprocess.CalledProcessError as e:
            return {"error": str(e)}
    
    def check_disk_usage(self) -> Dict[str, str]:
        """Check disk usage for A2rchi volumes."""
        if self.container_engine == "docker":
            cmd = "docker volume ls --filter name=a2rchi --format 'table {{.Name}}\\t{{.Size}}'"
        else:
            cmd = "podman volume ls --filter name=a2rchi --format 'table {{.Name}}\\t{{.Size}}'"
        
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
            return {"volumes": result.stdout}
        except subprocess.CalledProcessError as e:
            return {"error": str(e)}


def wait_for_full_deployment(deployment_name: str, container_engine: str = "docker", timeout: int = 600) -> bool:
    """
    Wait for a complete A2rchi deployment to be fully healthy.
    
    Args:
        deployment_name: Name of the deployment
        container_engine: Container engine (docker or podman)
        timeout: Maximum time to wait in seconds
        
    Returns:
        bool: True if deployment is healthy, False if timeout or failure
    """
    print(f"Waiting for {deployment_name} deployment to become fully healthy...")
    
    service_checker = ServiceHealthChecker(container_engine, deployment_name)
    api_checker = APIHealthChecker()
    db_checker = DatabaseHealthChecker(container_engine, deployment_name)
    
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        print(f"Health check attempt at {int(time.time() - start_time)}s...")
        
        # Check container health
        containers_healthy, container_results = service_checker.check_all_services_healthy()
        if not containers_healthy:
            print("Containers not yet healthy:")
            for service, result in container_results.items():
                if not result["healthy"]:
                    print(f"  - {service}: {result['status']}")
            time.sleep(15)
            continue
        
        print("✅ All containers are healthy")
        
        # Check database health
        db_results = db_checker.comprehensive_db_check()
        if not all(result["healthy"] for result in db_results.values()):
            print("Databases not yet healthy:")
            for db, result in db_results.items():
                if not result["healthy"]:
                    print(f"  - {db}: {result['data']}")
            time.sleep(15)
            continue
        
        print("✅ All databases are healthy")
        
        # Check API health
        api_results = api_checker.comprehensive_health_check()
        if not api_results["main_page"]["healthy"]:
            print(f"API not yet healthy: {api_results['main_page']['data']}")
            time.sleep(15)
            continue
        
        print("✅ API is healthy")
        print("🎉 Deployment is fully healthy!")
        return True
    
    print(f"❌ Timeout after {timeout}s waiting for deployment to become healthy")
    return False


if __name__ == "__main__":
    # Example usage
    deployment_name = "test-deployment"
    container_engine = "docker"
    
    if wait_for_full_deployment(deployment_name, container_engine):
        print("Deployment is ready for testing!")
    else:
        print("Deployment failed to become healthy")