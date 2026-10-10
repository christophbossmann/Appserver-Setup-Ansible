import os
import pwd
import subprocess

from .policy import (
    PLAYGROUND_USER,
    PLAYGROUND_WORKSPACE_ROOT,
    READ_TIMEOUT,
    MUTATION_TIMEOUT,
    MAX_LOG_TAIL,
    ALLOWED_IMAGE_PREFIXES,
)


def result(ok, exit_code=0, stdout="", stderr=""):
    return {
        "ok": ok,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
    }


def run_command(cmd, timeout=60, cwd=None, env=None):
    completed = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )

    return result(
        ok=completed.returncode == 0,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def playground_user_info():
    try:
        return pwd.getpwnam(PLAYGROUND_USER)
    except KeyError:
        return None


def playground_env():
    user = playground_user_info()

    if user is None:
        raise RuntimeError(f"Playground user not found: {PLAYGROUND_USER}")

    uid = user.pw_uid

    env = os.environ.copy()
    env["HOME"] = user.pw_dir
    env["USER"] = PLAYGROUND_USER
    env["LOGNAME"] = PLAYGROUND_USER
    env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"
    env["DOCKER_HOST"] = f"unix:///run/user/{uid}/docker.sock"

    return env


def playground_prefix_command(cmd):
    user = playground_user_info()

    if user is None:
        raise RuntimeError(f"Playground user not found: {PLAYGROUND_USER}")

    return ["sudo", "-u", PLAYGROUND_USER, "env"] + [
        f"HOME={user.pw_dir}",
        f"USER={PLAYGROUND_USER}",
        f"LOGNAME={PLAYGROUND_USER}",
        f"XDG_RUNTIME_DIR=/run/user/{user.pw_uid}",
        f"DOCKER_HOST=unix:///run/user/{user.pw_uid}/docker.sock",
    ] + cmd


def require_playground_workspace(workspace):
    if not workspace.exists():
        return result(
            ok=False,
            exit_code=1,
            stderr=f"Playground workspace missing: {workspace}\n",
        )

    if workspace.is_symlink():
        return result(
            ok=False,
            exit_code=1,
            stderr=f"Playground workspace must not be a symlink: {workspace}\n",
        )

    return None


def image_name_allowed_for_rm(image):
    if not image:
        return False

    if image.startswith("sha256:"):
        return False

    hex_chars = set("0123456789abcdef")
    if len(image) >= 12 and all(c.lower() in hex_chars for c in image):
        return False

    return any(image.startswith(prefix) for prefix in ALLOWED_IMAGE_PREFIXES)


# ----------------------------
# Host Docker: read-only only
# ----------------------------

def host_ps():
    return run_command(
        ["docker", "ps"],
        timeout=READ_TIMEOUT,
    )


def host_image_ls():
    return run_command(
        ["docker", "image", "ls"],
        timeout=READ_TIMEOUT,
    )


def host_compose_ls():
    return run_command(
        ["docker", "compose", "ls"],
        timeout=READ_TIMEOUT,
    )


def functions_reload():
    """The ONE allowed host-daemon mutation, surgically scoped: restart the
    supabase edge-runtime container so edited playground functions drop their
    cached workers immediately (new functions are picked up without this; an
    edited one may otherwise be served stale for up to ~60s worker idle-timeout).
    The container name is HARDCODED — the sandbox cannot pick a target."""
    return run_command(
        ["docker", "restart", "supabase-edge-functions"],
        timeout=MUTATION_TIMEOUT,
    )


def host_stats():
    """Read-only, per-container attribution of host load: docker stats snapshot
    (CPU/mem/net/block per container) + docker system df -v (image/volume/build-
    cache sizes). Lets the sandboxed agent name the container that eats the host
    instead of only seeing the aggregate loadavg."""
    stats = run_command(
        [
            "docker", "stats", "--no-stream", "--format",
            "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}",
        ],
        timeout=60,
    )
    df = run_command(["docker", "system", "df", "-v"], timeout=60)

    out = "=== docker stats (snapshot) ===\n" + stats["stdout"]
    out += "\n=== docker system df -v ===\n" + df["stdout"]

    return result(
        ok=stats["ok"] and df["ok"],
        exit_code=0 if (stats["ok"] and df["ok"]) else 1,
        stdout=out,
        stderr=stats["stderr"] + df["stderr"],
    )


# ---------------------------------------
# Playground rootless Docker: read/write
# ---------------------------------------

def playground_docker(cmd, timeout=60, cwd=None):
    if cwd is None:
        cwd = PLAYGROUND_WORKSPACE_ROOT

    return run_command(
        playground_prefix_command(cmd),
        timeout=timeout,
        cwd=cwd,
    )


# --- Daemon-wide (not scoped to a single environment) -----------------------

def playground_ps():
    return playground_docker(
        ["docker", "ps"],
        timeout=READ_TIMEOUT,
    )


def playground_image_ls():
    return playground_docker(
        ["docker", "image", "ls"],
        timeout=READ_TIMEOUT,
    )


def playground_image_rm(image):
    if not image_name_allowed_for_rm(image):
        allowed = ", ".join(sorted(ALLOWED_IMAGE_PREFIXES))
        return result(
            ok=False,
            exit_code=1,
            stderr=(
                f"Image not allowed for removal: {image}\n"
                f"Allowed image prefixes: {allowed}\n"
            ),
        )

    return playground_docker(
        ["docker", "rmi", image],
        timeout=MUTATION_TIMEOUT,
    )


def playground_compose_ls():
    return playground_docker(
        ["docker", "compose", "ls"],
        timeout=READ_TIMEOUT,
    )


# --- Environment-scoped (cwd = that environment's workspace) ----------------

def playground_compose_ps(workspace):
    err = require_playground_workspace(workspace)
    if err:
        return err

    return playground_docker(
        ["docker", "compose", "ps"],
        timeout=READ_TIMEOUT,
        cwd=workspace,
    )


def playground_compose_logs(workspace, tail=200):
    err = require_playground_workspace(workspace)
    if err:
        return err

    try:
        tail = int(tail)
    except Exception:
        tail = 200

    tail = max(1, min(tail, MAX_LOG_TAIL))

    return playground_docker(
        ["docker", "compose", "logs", "--no-color", f"--tail={tail}"],
        timeout=READ_TIMEOUT,
        cwd=workspace,
    )


def playground_compose_up(workspace):
    err = require_playground_workspace(workspace)
    if err:
        return err

    return playground_docker(
        ["docker", "compose", "up", "-d", "--build", "--remove-orphans"],
        timeout=MUTATION_TIMEOUT,
        cwd=workspace,
    )


def playground_compose_stop(workspace):
    err = require_playground_workspace(workspace)
    if err:
        return err

    return playground_docker(
        ["docker", "compose", "stop"],
        timeout=MUTATION_TIMEOUT,
        cwd=workspace,
    )


def playground_compose_restart(workspace):
    err = require_playground_workspace(workspace)
    if err:
        return err

    return playground_docker(
        ["docker", "compose", "restart"],
        timeout=MUTATION_TIMEOUT,
        cwd=workspace,
    )


def playground_compose_down(workspace, remove_volumes=False):
    err = require_playground_workspace(workspace)
    if err:
        return err

    cmd = ["docker", "compose", "down", "--remove-orphans"]

    if remove_volumes:
        cmd.append("--volumes")

    return playground_docker(
        cmd,
        timeout=MUTATION_TIMEOUT,
        cwd=workspace,
    )


def playground_compose_kill(workspace):
    err = require_playground_workspace(workspace)
    if err:
        return err

    return playground_docker(
        ["docker", "compose", "kill"],
        timeout=MUTATION_TIMEOUT,
        cwd=workspace,
    )


def playground_compose_build(workspace):
    err = require_playground_workspace(workspace)
    if err:
        return err

    return playground_docker(
        ["docker", "compose", "build"],
        timeout=MUTATION_TIMEOUT,
        cwd=workspace,
    )


def playground_compose_reset(workspace):
    down = playground_compose_down(workspace, remove_volumes=True)

    if not down["ok"]:
        return down

    return playground_compose_up(workspace)
