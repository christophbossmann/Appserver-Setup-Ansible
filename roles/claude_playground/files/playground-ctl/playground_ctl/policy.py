import json
import os
import re
from pathlib import Path

SOCKET_PATH = "/run/playground-ctl/playground-ctl.sock"
SOCKET_GROUP = "playgroundctl"

PLAYGROUND_USER = "playground"

# Root that holds every per-environment workspace. Each environment is a real
# subdirectory below it (e.g. /home/playground/workspace/pg1). Daemon-wide
# read-only commands (ps, image ls, compose ls) run with this as cwd.
PLAYGROUND_WORKSPACE_ROOT = Path("/home/playground/workspace")

READ_TIMEOUT = 20
MUTATION_TIMEOUT = 300
MAX_LOG_TAIL = 1000

ALLOWED_IMAGE_PREFIXES = {
    "playground/",
}

# Root-owned allowlist that maps an environment name -> {workspace, port}.
# It is rendered by Ansible (broker.yml) and is the ONLY source of truth for
# what environments exist. The sandboxed AI only ever sends an env KEY; it can
# never inject a workspace path or a port. Restart the broker to pick up edits.
ENVIRONMENTS_FILE = os.environ.get(
    "PLAYGROUND_CTL_ENVIRONMENTS", "/opt/playground-ctl/environments.json"
)

# Defense-in-depth: even though names come from the root-owned JSON, refuse any
# name that is not a plain slug. Blocks path traversal / surprises if the file
# is ever hand-edited. The name doubles as a Caddy hostname label, so this also
# matches what DNS accepts.
_ENV_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class PlaygroundEnvError(Exception):
    """Raised when an environment cannot be resolved (unknown, or the allowlist
    is missing/malformed). Callers turn this into a normal error response."""


def _load_environments():
    """Parse ENVIRONMENTS_FILE -> (envs: dict[name -> {workspace: Path, port: int}],
    default: str|None). Read fresh on every call (the file is tiny and a restart
    is not required to reason about it). Raises PlaygroundEnvError on any problem."""
    try:
        with open(ENVIRONMENTS_FILE) as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise PlaygroundEnvError(
            "environments allowlist missing: " + ENVIRONMENTS_FILE
        )
    except (OSError, ValueError) as exc:
        raise PlaygroundEnvError(
            "environments allowlist unreadable (" + ENVIRONMENTS_FILE + "): " + str(exc)
        )

    raw = data.get("environments")
    if not isinstance(raw, dict) or not raw:
        raise PlaygroundEnvError(
            "no environments defined in " + ENVIRONMENTS_FILE
        )

    envs = {}
    for name, cfg in raw.items():
        if not isinstance(name, str) or not _ENV_NAME_RE.match(name):
            raise PlaygroundEnvError("invalid environment name: " + repr(name))
        if not isinstance(cfg, dict):
            raise PlaygroundEnvError("invalid environment config for: " + name)
        try:
            workspace = Path(cfg["workspace"])
            port = int(cfg["port"])
        except (KeyError, TypeError, ValueError):
            raise PlaygroundEnvError("environment " + name + " needs workspace + port")
        envs[name] = {"workspace": workspace, "port": port}

    default = data.get("default")
    if default not in envs:
        default = next(iter(envs), None)

    return envs, default


def resolve_environment(name):
    """Return (workspace: Path, port: int) for a validated env name. `name` may
    be None -> the configured default environment. Raises PlaygroundEnvError with
    a helpful message on an unknown env."""
    envs, default = _load_environments()

    if name is None:
        name = default

    if name not in envs:
        raise PlaygroundEnvError(
            "unknown environment: "
            + repr(name)
            + ". Known: "
            + (", ".join(sorted(envs)) or "(none configured)")
        )

    entry = envs[name]
    return entry["workspace"], entry["port"]


def list_environments():
    """Return (envs, default) for the `playground envs` discovery action."""
    return _load_environments()
