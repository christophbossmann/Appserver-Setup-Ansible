import os
import pwd
import subprocess

from .policy import (
    PLAYGROUND_USER,
    PLAYGROUND_WORKSPACE,
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


def require_playground_workspace():
    if not PLAYGROUND_WORKSPACE.exists():
        return result(
            ok=False,
            exit_code=1,
            stderr=f"Playground workspace missing: {PLAYGROUND_WORKSPACE}\n",
        )

    if PLAYGROUND_WORKSPACE.is_symlink():
        return result(
            ok=False,
            exit_code=1,
            stderr=f"Playground workspace must not be a symlink: {PLAYGROUND_WORKSPACE}\n",
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


# ---------------------------------------
# Playground rootless Docker: read/write
# ---------------------------------------

def playground_docker(cmd, timeout=60, cwd=None):
    if cwd is None:
        cwd = PLAYGROUND_WORKSPACE

    return run_command(
        playground_prefix_command(cmd),
        timeout=timeout,
        cwd=cwd,
    )


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


def playground_compose_ps():
    err = require_playground_workspace()
    if err:
        return err

    return playground_docker(
        ["docker", "compose", "ps"],
        timeout=READ_TIMEOUT,
        cwd=PLAYGROUND_WORKSPACE,
    )


def playground_compose_logs(tail=200):
    err = require_playground_workspace()
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
        cwd=PLAYGROUND_WORKSPACE,
    )


def playground_compose_up():
    err = require_playground_workspace()
    if err:
        return err

    return playground_docker(
        ["docker", "compose", "up", "-d", "--build", "--remove-orphans"],
        timeout=MUTATION_TIMEOUT,
        cwd=PLAYGROUND_WORKSPACE,
    )


def playground_compose_stop():
    err = require_playground_workspace()
    if err:
        return err

    return playground_docker(
        ["docker", "compose", "stop"],
        timeout=MUTATION_TIMEOUT,
        cwd=PLAYGROUND_WORKSPACE,
    )


def playground_compose_restart():
    err = require_playground_workspace()
    if err:
        return err

    return playground_docker(
        ["docker", "compose", "restart"],
        timeout=MUTATION_TIMEOUT,
        cwd=PLAYGROUND_WORKSPACE,
    )


def playground_compose_down(remove_volumes=False):
    err = require_playground_workspace()
    if err:
        return err

    cmd = ["docker", "compose", "down", "--remove-orphans"]

    if remove_volumes:
        cmd.append("--volumes")

    return playground_docker(
        cmd,
        timeout=MUTATION_TIMEOUT,
        cwd=PLAYGROUND_WORKSPACE,
    )


def playground_compose_kill():
    err = require_playground_workspace()
    if err:
        return err

    return playground_docker(
        ["docker", "compose", "kill"],
        timeout=MUTATION_TIMEOUT,
        cwd=PLAYGROUND_WORKSPACE,
    )


def playground_compose_build():
    err = require_playground_workspace()
    if err:
        return err

    return playground_docker(
        ["docker", "compose", "build"],
        timeout=MUTATION_TIMEOUT,
        cwd=PLAYGROUND_WORKSPACE,
    )


def playground_compose_reset():
    down = playground_compose_down(remove_volumes=True)

    if not down["ok"]:
        return down

    return playground_compose_up()