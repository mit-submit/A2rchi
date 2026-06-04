import asyncio
import os
import yaml

from pathlib import Path
from mcp.server.fastmcp import FastMCP
from src.utils.logging import get_logger


logger = get_logger(__name__)

mcp = FastMCP("submit-status")

SUBMIT_MACHINES: list[str] = ["submit00", "submit06", "submit82"]
SERVERS_FILE = Path("src/archi/mcp_servers/submit_status/interest_servers.yaml")

# SSH user to connect as. The container runs as root but the submit nodes only
# authorize the cluster user's key. Set SUBMIT_SSH_USER in the deployment env.

_SSH_USER: str = os.environ.get("SUBMIT_SSH_USER", "")

_SSH_TIMEOUT = 30


def _ssh_opts() -> list[str]:
    opts = [
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=no",
    ]
    if _SSH_USER:
        opts += ["-l", _SSH_USER]
    return opts

def load_servers_of_interest() -> dict[str, list[str]]:
    with open(SERVERS_FILE, "r") as f:
        return yaml.safe_load(f) or {}


async def _ssh(host: str, command: str) -> str:
    """Run a command on a remote host via SSH. Returns stdout or an error string."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", *_ssh_opts(), host, command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_SSH_TIMEOUT)
        if proc.returncode == 0:
            logger.info("ssh %s: command succeeded: %s", host, command)
            return stdout.decode().strip()
        error = stderr.decode().strip() or f"ssh exited with code {proc.returncode}"
        logger.warning("ssh %s: command failed (rc=%d): %s", host, proc.returncode, error)
        return error
    except asyncio.TimeoutError:
        logger.warning("ssh %s: timed out after %ds running: %s", host, _SSH_TIMEOUT, command)
        return f"SSH to {host} timed out after {_SSH_TIMEOUT}s"
    except Exception as exc:
        logger.error("ssh %s: unexpected error running '%s': %s", host, command, exc)
        return f"SSH error connecting to {host}: {exc}"


@mcp.tool()
async def check_node_status(machines: str = "") -> str:
    """
    Check reachability and system load for submit machines by running 'uptime' over SSH.
    Returns one line per machine: '<hostname>: <uptime output or error>'.
    machines: comma-separated hostnames (e.g. "submit00,submit06"). Leave empty to check all known machines.
    """
    targets = [m.strip() for m in machines.split(",") if m.strip()] if machines.strip() else SUBMIT_MACHINES
    if not targets:
        return "No machines specified and SUBMIT_MACHINES list is empty"
    results = await asyncio.gather(*[_ssh(h, "uptime") for h in targets])
    return "\n".join(f"{host}: {result}" for host, result in zip(targets, results))


@mcp.tool()
async def check_disk_usage(machine: str) -> str:
    """
    Check disk usage on a submit machine by running 'df -h'.
    Returns the full df output or an SSH error.
    """
    return await _ssh(machine, "df -h")



@mcp.tool()
async def list_services(machine: str) -> str:
    """
    List all systemd services on a submit machine and their current state.
    Returns a table of unit name, load state, active state, sub-state, and description.
    Call this first to discover available service names before calling check_service_status.
    """
    return await _ssh(
        machine,
        "systemctl list-units --type=service --all --no-pager --no-legend --plain",
    )


@mcp.tool()
async def check_service_status(machine: str, service: str) -> str:
    """
    Check the detailed status of a specific systemd service on a submit machine.
    Use list_services first to find the exact service unit name (e.g. 'condor.service').
    Returns the full systemctl status output including active state, PID, memory, and recent logs.
    """
    return await _ssh(machine, f"systemctl status {service} --no-pager")


@mcp.tool()
async def check_interesting_services() -> str:
    """
    Contains all of the servers of interest to us on each machine. 
    Check the configured services of interest on each submit machine.

    Reads configs/submit_servers.yaml, where each machine maps to a list
    of systemd service names.
    """
    servers = load_servers_of_interest()

    results = []

    for machine, services in servers.items():
        results.append(f"## {machine}")

        for service in services:
            status = await _ssh(
                machine,
                f"systemctl status {service} --no-pager"
            )

            results.append(f"### {service}")
            results.append(status)

    return "\n\n".join(results)
