# playground_ctl/preflight.py
#
# Hard, fail-fast environment checks. These run ONCE at broker startup, before
# the Unix socket is opened. If any precondition is missing, the broker prints a
# clear "playgroundctl: ..." message and exits non-zero.
#
# This is a deliberate design choice (see task.md -> Python-Runtime-Aufgaben):
# the broker is a thin runtime component. It NEVER provisions anything (users,
# groups, subuid/subgid, linger, rootless Docker, systemd units). Those belong
# to Ansible. The broker only verifies that the expected environment exists and
# refuses to start otherwise.
#
# Note on systemd: the unit uses Restart=always, so a failed preflight makes the
# service crash-loop until the environment is provisioned. That is intentional —
# Ansible always provisions everything BEFORE starting the service, so on a
# correctly applied host the preflight passes on the first start.

import grp
import os
import pwd
import subprocess
import sys

from . import policy
from .policy import (
    PLAYGROUND_USER,
    PLAYGROUND_WORKSPACE_ROOT,
    SOCKET_GROUP,
    READ_TIMEOUT,
)


def _fail(message):
    sys.stderr.write(f"playgroundctl: {message}\n")
    sys.stderr.flush()
    sys.exit(1)


def run_preflight():
    # 1. playground user must exist
    try:
        user = pwd.getpwnam(PLAYGROUND_USER)
    except KeyError:
        _fail(f"playground user not found: {PLAYGROUND_USER}")

    uid = user.pw_uid

    # 2. broker socket group must exist (the socket is chowned root:<group>)
    try:
        grp.getgrnam(SOCKET_GROUP)
    except KeyError:
        _fail(f"socket group not found: {SOCKET_GROUP}")

    # 3. workspace root must exist and must NOT be a symlink
    if not PLAYGROUND_WORKSPACE_ROOT.exists():
        _fail(f"playground workspace root missing: {PLAYGROUND_WORKSPACE_ROOT}")

    if PLAYGROUND_WORKSPACE_ROOT.is_symlink():
        _fail(f"playground workspace root must not be a symlink: {PLAYGROUND_WORKSPACE_ROOT}")

    # 3b. the environments allowlist must load, and every environment's workspace
    # must exist as a real directory (not a symlink). A misconfigured allowlist
    # or a missing env dir must fail fast, not surface later per-request.
    try:
        envs, _default = policy.list_environments()
    except policy.PlaygroundEnvError as exc:
        _fail(str(exc))

    for name in sorted(envs):
        workspace = envs[name]["workspace"]
        if not workspace.exists():
            _fail(f"environment '{name}' workspace missing: {workspace}")
        if workspace.is_symlink():
            _fail(f"environment '{name}' workspace must not be a symlink: {workspace}")

    # 4. rootless docker socket must exist
    socket_path = f"/run/user/{uid}/docker.sock"
    if not os.path.exists(socket_path):
        _fail(f"rootless docker socket not found: {socket_path}")

    # 5. rootless docker daemon must be reachable
    try:
        completed = subprocess.run(
            [
                "sudo",
                "-u",
                PLAYGROUND_USER,
                "env",
                f"XDG_RUNTIME_DIR=/run/user/{uid}",
                f"DOCKER_HOST=unix:///run/user/{uid}/docker.sock",
                "docker",
                "info",
            ],
            capture_output=True,
            text=True,
            timeout=READ_TIMEOUT,
        )
    except FileNotFoundError:
        _fail("docker CLI not found on PATH")
    except subprocess.TimeoutExpired:
        _fail(f"rootless docker daemon not responding: {socket_path}")

    if completed.returncode != 0:
        _fail(f"rootless docker daemon not reachable: {socket_path}")
