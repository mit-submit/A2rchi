import asyncio
import os
import shlex
import yaml

from pathlib import Path
from mcp.server.fastmcp import FastMCP
from src.utils.logging import get_logger


logger = get_logger(__name__)

mcp = FastMCP("submit-status")

LOGIN_NODES = ["submit00", "submit01", "submit02", "submit03", "submit04", "submit05", "submit06", "submit07", "submit08"]
CEPH_NODES  = ["submit50", "submit51", "submit52", "submit53", "submit54", "submit55", "submit56", "submit57", "submit58", "submit59"]
SCRATCH_NODES = ["submit30"]
GPU_NODES: list[str] = []  # e.g. ["submitgpu01", "submitgpu02"]
CPU_NODES: list[str] = []  # e.g. ["submitcpu01", "submitcpu02"]
ALL_NODES = LOGIN_NODES + SCRATCH_NODES + CEPH_NODES + GPU_NODES + CPU_NODES

_ALL_NODES_SET   = set(ALL_NODES)
_LOGIN_NODES_SET = set(LOGIN_NODES)
_CEPH_NODES_SET  = set(CEPH_NODES)


SERVERS_FILE = Path(__file__).parent / "interest_servers.yaml"

# SSH user and key to connect as. The container runs as root but the submit nodes only
# authorize the cluster user's key. Set SUBMIT_SSH_USER and SUBMIT_SSH_KEY in the
# deployment env. The resulting command is: ssh -i <key> -l <user> <host> <cmd>
_SSH_USER: str = os.environ.get("SUBMIT_SSH_USER", "")
_SSH_KEY: str  = os.path.expanduser(os.environ.get("SUBMIT_SSH_KEY", ""))

_SSH_TIMEOUT = 30


def _ssh_opts() -> list[str]:
    opts = [
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=accept-new",
    ]
    if _SSH_KEY:
        opts += ["-i", _SSH_KEY]
    if _SSH_USER:
        opts += ["-l", _SSH_USER]
    return opts


def _validate_machine(machine: str, allowed: set[str]) -> str | None:
    if machine not in allowed:
        return f"Unknown machine '{machine}'. Allowed: {sorted(allowed)}"
    return None


def load_servers_of_interest() -> dict[str, list[str]]:
    with open(SERVERS_FILE, "r") as f:
        return yaml.safe_load(f) or {}


async def _ssh(host: str, command: str) -> str:
    fqdn = host if "." in host else f"{host}.mit.edu"
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", *_ssh_opts(), fqdn, command,
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


async def _ssh_multi(hosts: list[str], command: str) -> str:
    """Run the same command on multiple hosts in parallel, return combined results."""
    outputs = await asyncio.gather(*[_ssh(h, command) for h in hosts])
    return "\n\n".join(f"### {host}\n{out}" for host, out in zip(hosts, outputs))


# ---------------------------------------------------------------------------
# Login node tools (submit00-08)
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_services(machine: str) -> str:
    """
    List all systemd services on a submit login node and their current state.
    Valid machines: login nodes only (submit00-08).
    Returns unit name, load/active/sub state, and description for every service.
    Call this first to discover service names before calling check_service_status.
    """
    if err := _validate_machine(machine, _LOGIN_NODES_SET):
        return err
    return await _ssh(machine, "systemctl list-units --type=service --all --no-pager --no-legend --plain")


@mcp.tool()
async def check_service_status(machine: str, service: str) -> str:
    """
    Check the detailed status of a specific systemd service on a submit login node.
    Valid machines: login nodes only (submit00-08).
    Use list_services first to find the exact unit name (e.g. 'condor.service').
    Returns active state, PID, memory usage, and recent journal lines.
    """
    if err := _validate_machine(machine, _LOGIN_NODES_SET):
        return err
    return await _ssh(machine, f"systemctl status {shlex.quote(service)} --no-pager")


@mcp.tool()
async def check_interesting_services() -> str:
    """
    Check the configured services of interest across submit machines.
    Reads interest_servers.yaml (machine -> list of service unit names).
    Returns status output for each configured service, grouped by machine.
    """
    servers = load_servers_of_interest()
    results = []
    for machine, services in servers.items():
        results.append(f"## {machine}")
        for service in services:
            results.append(f"### {service}")
            results.append(await _ssh(machine, f"systemctl status {shlex.quote(service)} --no-pager"))
    return "\n\n".join(results)


# ---------------------------------------------------------------------------
# Ceph node tools (submit50-59)
# ---------------------------------------------------------------------------

@mcp.tool()
async def ceph_status(machine: str) -> str:
    """
    Get a snapshot of the Ceph cluster status from a ceph node.
    Valid machines: ceph nodes only (submit50-59).
    Returns cluster health, MON quorum, OSD map, data usage, and I/O stats.
    """
    if err := _validate_machine(machine, _CEPH_NODES_SET):
        return err
    return await _ssh(machine, "ceph -s")


@mcp.tool()
async def ceph_health_detail(machine: str) -> str:
    """
    Get detailed Ceph health warnings and errors from a ceph node.
    Valid machines: ceph nodes only (submit50-59).
    Use this when ceph_status shows HEALTH_WARN or HEALTH_ERR for the full breakdown.
    """
    if err := _validate_machine(machine, _CEPH_NODES_SET):
        return err
    return await _ssh(machine, "ceph health detail")


# ---------------------------------------------------------------------------
# Scratch node tools (submit30)
# ---------------------------------------------------------------------------

@mcp.tool()
async def check_scratch_disk() -> str:
    """
    Check disk usage of the /scratch filesystem on submit30.
    Returns df -h output for /scratch only.
    """
    return await _ssh("submit30", "df -h /scratch")


# ---------------------------------------------------------------------------
# Generic / all-node tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def check_disk_usage(machine: str) -> str:
    """
    Check full disk usage on a single submit node (df -h).
    Valid machines: all nodes (submit00-08, submit30, submit50-59).
    """
    if err := _validate_machine(machine, _ALL_NODES_SET):
        return err
    return await _ssh(machine, "df -h")


@mcp.tool()
async def check_all_disk_usage() -> str:
    """
    Check disk usage across all submit nodes in parallel (login, scratch, ceph).
    Returns df -h output from every node grouped by hostname.
    Use this for a cluster-wide storage overview.
    """
    return await _ssh_multi(ALL_NODES, "df -h")


# @mcp.tool()
# async def run_command(machine: str, command: str) -> str:
#     """
#     Run an arbitrary read-only command on any submit node via SSH.
#     Valid machines: all nodes (submit00-08, submit30, submit50-59).
#     Use for one-off checks not covered by other tools (e.g. 'uptime', 'free -h', 'mount').
#     """
#     if err := _validate_machine(machine, _ALL_NODES_SET):
#         return err
#     return await _ssh(machine, command)


# @mcp.tool()
# async def run_command_all(command: str) -> str:
#     """
#     Run an arbitrary read-only command on all submit nodes in parallel.
#     Returns output from every node grouped by hostname.
#     Useful for cluster-wide checks like 'uptime', 'free -h', or 'df -h /some/mount'.
#     """
#     return await _ssh_multi(ALL_NODES, command)
