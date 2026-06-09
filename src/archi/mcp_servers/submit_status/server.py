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


async def _ssh(host: str, command: str, timeout: int = _SSH_TIMEOUT) -> str:
    fqdn = host if "." in host else f"{host}.mit.edu"
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", *_ssh_opts(), fqdn, command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode == 0:
            logger.info("ssh %s: command succeeded: %s", host, command)
            return stdout.decode().strip()
        error = stderr.decode().strip() or f"ssh exited with code {proc.returncode}"
        logger.warning("ssh %s: command failed (rc=%d): %s", host, proc.returncode, error)
        return error
    except asyncio.TimeoutError:
        logger.warning("ssh %s: timed out after %ds running: %s", host, timeout, command)
        return f"SSH to {host} timed out after {timeout}s"
    except Exception as exc:
        logger.error("ssh %s: unexpected error running '%s': %s", host, command, exc)
        return f"SSH error connecting to {host}: {exc}"


async def _ssh_multi(hosts: list[str], command: str) -> str:
    """Run the same command on multiple hosts in parallel, return combined results."""
    outputs = await asyncio.gather(*[_ssh(h, command) for h in hosts])
    return "\n\n".join(f"### {host}\n{out}" for host, out in zip(hosts, outputs))




# ---------------------------------------------------------------------------
# General MCP Tools
# ---------------------------------------------------------------------------

_ALLOWED_COMMANDS: dict[str, set[str] | None] = {
    "df":          None,
    "free":        None,
    "uptime":      None,
    "mount":       None,
    "w":           None,
    "uname":       None,
    "condor_q":    None,
    "condor_status": None,
    "condor_history": None,
    "ceph":        {"-s", "status", "health", "df", "osd", "mon"},
    "rados":       {"df", "lspools"},
    "systemctl":   {"status", "list-units", "show", "is-active", "is-enabled"},
    "journalctl":  None,
}


def _validate_command(command: str, args: list[str]) -> str | None:
    allowed_first_args = _ALLOWED_COMMANDS.get(command)
    if allowed_first_args is None and command not in _ALLOWED_COMMANDS:
        return f"Command '{command}' is not whitelisted. Allowed: {sorted(_ALLOWED_COMMANDS)}"
    if allowed_first_args is not None and args and args[0] not in allowed_first_args:
        return (
            f"Argument '{args[0]}' is not allowed as the first argument to '{command}'. "
            f"Allowed: {sorted(allowed_first_args)}"
        )
    return None


@mcp.tool()
async def run_command(machine: str, command: str, args: list[str] | None = None) -> str:
    """
    Run a read-only diagnostic command on a submit node, choosing both the
    command and its arguments yourself from a fixed whitelist of safe binaries.
    Valid machines: all nodes (submit00-08, submit30, submit50-59).

    Whitelisted commands: df, free, uptime, mount, w, uname, condor_q, condor_status,
    condor_history, ceph (status/health/df/osd/mon subcommands only),
    rados (df/lspools only), systemctl (status/list-units/show/is-active/is-enabled only),
    journalctl.

    Each argument is passed as a separate, independently-quoted token — you
    cannot chain commands, pipe, redirect, or use shell substitution. Use this
    for one-off diagnostic queries not covered by a more specific tool.

    Examples: command="df", args=["-h", "/scratch"]
              command="systemctl", args=["status", "condor.service"]
              command="journalctl", args=["-u", "condor.service", "-n", "50", "--no-pager"]
    """
    if err := _validate_machine(machine, _ALL_NODES_SET):
        return err
    args = args or []
    if err := _validate_command(command, args):
        return err
    full_command = " ".join(shlex.quote(tok) for tok in [command, *args])
    return await _ssh(machine, full_command)


@mcp.tool()
async def run_command_all(command: str, args: list[str] | None = None) -> str:
    """
    Run a whitelisted read-only diagnostic command on all submit nodes in parallel.
    Same whitelist and argument rules as run_command — see its description for
    the allowed commands and examples. Returns output from every node grouped by hostname.
    Useful for cluster-wide checks like uptime, free, or df on a specific mount.
    """
    args = args or []
    if err := _validate_command(command, args):
        return err
    full_command = " ".join(shlex.quote(tok) for tok in [command, *args])
    return await _ssh_multi(ALL_NODES, full_command)
