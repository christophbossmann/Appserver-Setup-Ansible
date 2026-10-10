#!/usr/bin/env python3

import json
import os
import socket
import sys

SOCKET_PATH = os.environ.get("PLAYGROUND_CTL_SOCKET", "/run/playground-ctl/playground-ctl.sock")

# Actions that operate on ONE environment's workspace and therefore accept the
# --env selector. Everything else (ps, image ls/rm, compose ls, envs, host ...)
# is daemon-wide and rejects --env.
ENV_SCOPED_ACTIONS = {
    "playground_compose_ps",
    "playground_compose_logs",
    "playground_compose_up",
    "playground_compose_stop",
    "playground_compose_restart",
    "playground_compose_down",
    "playground_compose_destroy",
    "playground_compose_kill",
    "playground_compose_build",
    "playground_compose_reset",
    "playground_http_get",
}


def call(request):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

    try:
        sock.connect(SOCKET_PATH)
        sock.sendall(json.dumps(request).encode("utf-8"))

        chunks = []

        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)

        return json.loads(b"".join(chunks).decode("utf-8"))

    except FileNotFoundError:
        return {
            "exit_code": 111,
            "stdout": "",
            "stderr": f"playgroundctl: socket not found: {SOCKET_PATH}\n",
        }

    except ConnectionRefusedError:
        return {
            "exit_code": 111,
            "stdout": "",
            "stderr": f"playgroundctl: connection refused: broker is not running for {SOCKET_PATH}\n",
        }

    except PermissionError:
        return {
            "exit_code": 13,
            "stdout": "",
            "stderr": f"playgroundctl: permission denied for socket: {SOCKET_PATH}\n",
        }

    except json.JSONDecodeError:
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": "playgroundctl: invalid response from broker\n",
        }

    finally:
        sock.close()


def command_text(args):
    return " ".join(args)


def client_error(message, show_usage=True):
    return {
        "client_error": message,
        "show_usage": show_usage,
    }


def denied(daemon, args):
    return client_error(
        (
            f"permission denied: command '{command_text(args)}' "
            f"is not allowed on daemon '{daemon}'"
        ),
        show_usage=False,
    )


def extract_env(args):
    """Pull an optional `--env <name>` / `--env=<name>` out of args (it may
    appear anywhere). Returns (env, remaining_args, error_message)."""
    env = None
    remaining = []
    i = 0

    while i < len(args):
        token = args[i]

        if token == "--env":
            if i + 1 >= len(args):
                return None, None, "missing value for --env"
            env = args[i + 1]
            i += 2
            continue

        if token.startswith("--env="):
            env = token.split("=", 1)[1]
            i += 1
            continue

        remaining.append(token)
        i += 1

    if env is not None and env == "":
        return None, None, "empty value for --env"

    return env, remaining, None


def parse_args(command_args):
    """command_args = argv[1:] with --env already stripped."""
    args = command_args

    if not args:
        return client_error("missing daemon")

    daemon = args[0]
    command = args[1:]

    if daemon not in {"host", "playground", "functions"}:
        return client_error(f"unknown daemon: {daemon}")

    if not command:
        return client_error(f"missing command for daemon '{daemon}'")

    # -----------------
    # functions — supabase edge functions (deploy = drop files in
    # /workspace/supabase/functions/<name>/index.ts; reload sheds cached workers)
    # -----------------
    if daemon == "functions":
        if command == ["reload"]:
            return {"action": "functions_reload"}
        return client_error(
            f"unknown command for daemon '{daemon}': {command_text(command)}"
        )

    # -----------------
    # host daemon
    # read-only only
    # -----------------
    if daemon == "host":
        if command == ["ps"]:
            return {"action": "host_ps"}

        if command == ["image", "ls"]:
            return {"action": "host_image_ls"}

        if command == ["compose", "ls"]:
            return {"action": "host_compose_ls"}

        # Read-only diagnostics: attribute host saturation to a container or
        # subsystem (CPU vs I/O vs storage) instead of only seeing loadavg.
        if command == ["stats"]:
            return {"action": "host_stats"}

        if command == ["diag"]:
            return {"action": "host_diag"}

        if command[0] == "promql":
            if len(command) < 2:
                return client_error("missing PromQL expression (quote it: host promql 'node_load1')")
            if len(command) > 2:
                return client_error("too many arguments — quote the whole expression")
            return {"action": "host_promql", "query": command[1]}

        # Known command families, but not allowed on host.
        if command[0] in {
            "compose",
            "image",
            "container",
            "volume",
            "network",
            "build",
            "run",
            "exec",
            "start",
            "stop",
            "restart",
            "kill",
            "rm",
            "rmi",
        }:
            return denied(daemon, command)

        return client_error(
            f"unknown command for daemon '{daemon}': {command_text(command)}"
        )

    # -----------------
    # playground daemon
    # rootless read/write
    # -----------------
    if daemon == "playground":
        if command == ["ps"]:
            return {"action": "playground_ps"}

        if command == ["envs"]:
            return {"action": "playground_environments"}

        if command == ["image", "ls"]:
            return {"action": "playground_image_ls"}

        if len(command) >= 2 and command[0:2] == ["image", "rm"]:
            if len(command) < 3:
                return client_error("missing image name")

            if len(command) > 3:
                return client_error("too many arguments for playground image rm")

            return {
                "action": "playground_image_rm",
                "image": command[2],
            }

        if command == ["compose", "ls"]:
            return {"action": "playground_compose_ls"}

        if command == ["compose", "ps"]:
            return {"action": "playground_compose_ps"}

        if command == ["compose", "up"]:
            return {"action": "playground_compose_up"}

        if command == ["compose", "stop"]:
            return {"action": "playground_compose_stop"}

        if command == ["compose", "restart"]:
            return {"action": "playground_compose_restart"}

        if command == ["compose", "down"]:
            return {"action": "playground_compose_down"}

        if command == ["compose", "destroy"]:
            return {"action": "playground_compose_destroy"}

        if command == ["compose", "kill"]:
            return {"action": "playground_compose_kill"}

        if command == ["compose", "build"]:
            return {"action": "playground_compose_build"}

        if command == ["compose", "reset"]:
            return {"action": "playground_compose_reset"}

        if len(command) >= 2 and command[0:2] == ["compose", "logs"]:
            if len(command) == 2:
                return {"action": "playground_compose_logs"}

            if len(command) == 3:
                return {
                    "action": "playground_compose_logs",
                    "tail": command[2],
                }

            return client_error("too many arguments for playground compose logs")

        # HTTP fetch of the workspace app via the broker (host loopback) —
        # the sandbox has no network path to 127.0.0.1:<env port> itself.
        if len(command) >= 2 and command[0:2] == ["http", "get"]:
            if len(command) == 2:
                return {"action": "playground_http_get"}

            if len(command) == 3:
                return {
                    "action": "playground_http_get",
                    "path": command[2],
                }

            return client_error("too many arguments for playground http get")

        return client_error(
            f"unknown command for daemon '{daemon}': {command_text(command)}"
        )

    return client_error("unreachable parser state")


def usage():
    print("Usage:", file=sys.stderr)
    print("  playgroundctl host ps", file=sys.stderr)
    print("  playgroundctl host image ls", file=sys.stderr)
    print("  playgroundctl host compose ls", file=sys.stderr)
    print("  playgroundctl host stats                     # per-container CPU/mem/net/disk snapshot + docker df", file=sys.stderr)
    print("  playgroundctl host diag                      # CPU vs I/O vs storage: PSI, vmstat, iostat, df, free", file=sys.stderr)
    print("  playgroundctl host promql '<expr>'           # read-only Prometheus query (history, per-container via cAdvisor)", file=sys.stderr)
    print("", file=sys.stderr)
    print("  playgroundctl functions reload               # restart the supabase edge-runtime (pick up edited functions)", file=sys.stderr)
    print("", file=sys.stderr)
    print("  playgroundctl playground envs                # list deploy environments", file=sys.stderr)
    print("  playgroundctl playground ps", file=sys.stderr)
    print("  playgroundctl playground image ls", file=sys.stderr)
    print("  playgroundctl playground image rm playground/<image>:<tag>", file=sys.stderr)
    print("  playgroundctl playground compose ls", file=sys.stderr)
    print("", file=sys.stderr)
    print("  Environment-scoped (pick a target with --env <name>; default env if omitted):", file=sys.stderr)
    print("  playgroundctl playground compose ps      [--env <name>]", file=sys.stderr)
    print("  playgroundctl playground compose up      [--env <name>]", file=sys.stderr)
    print("  playgroundctl playground compose stop    [--env <name>]", file=sys.stderr)
    print("  playgroundctl playground compose restart [--env <name>]", file=sys.stderr)
    print("  playgroundctl playground compose down    [--env <name>]", file=sys.stderr)
    print("  playgroundctl playground compose destroy [--env <name>]", file=sys.stderr)
    print("  playgroundctl playground compose kill    [--env <name>]", file=sys.stderr)
    print("  playgroundctl playground compose build   [--env <name>]", file=sys.stderr)
    print("  playgroundctl playground compose reset   [--env <name>]", file=sys.stderr)
    print("  playgroundctl playground compose logs [tail]   [--env <name>]", file=sys.stderr)
    print("  playgroundctl playground http get [/path]      [--env <name>]   # verify a deploy", file=sys.stderr)


def main():
    env, command_args, env_error = extract_env(sys.argv[1:])

    if env_error:
        print(f"playgroundctl: {env_error}", file=sys.stderr)
        usage()
        sys.exit(1)

    request = parse_args(command_args)

    if "client_error" in request:
        print(f"playgroundctl: {request['client_error']}", file=sys.stderr)

        if request.get("show_usage", True):
            usage()

        sys.exit(1)

    if env is not None:
        if request["action"] not in ENV_SCOPED_ACTIONS:
            print(
                f"playgroundctl: --env is not valid for '{command_text(command_args)}' "
                f"(only environment-scoped commands accept --env)",
                file=sys.stderr,
            )
            sys.exit(1)
        request["env"] = env

    response = call(request)

    stdout = response.get("stdout", "")
    stderr = response.get("stderr", "")

    if stdout:
        print(stdout, end="")

    if stderr:
        print(stderr, end="", file=sys.stderr)

    sys.exit(int(response.get("exit_code", 1)))


if __name__ == "__main__":
    main()
