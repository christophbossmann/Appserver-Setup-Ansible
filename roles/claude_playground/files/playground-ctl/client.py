#!/usr/bin/env python3

import json
import os
import socket
import sys

SOCKET_PATH = os.environ.get("PLAYGROUND_CTL_SOCKET", "/run/playground-ctl.sock")


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


def parse_args(argv):
    args = argv[1:]

    if not args:
        return client_error("missing daemon")

    daemon = args[0]
    command = args[1:]

    if daemon not in {"host", "playground"}:
        return client_error(f"unknown daemon: {daemon}")

    if not command:
        return client_error(f"missing command for daemon '{daemon}'")

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

        return client_error(
            f"unknown command for daemon '{daemon}': {command_text(command)}"
        )

    return client_error("unreachable parser state")


def usage():
    print("Usage:", file=sys.stderr)
    print("  playgroundctl host ps", file=sys.stderr)
    print("  playgroundctl host image ls", file=sys.stderr)
    print("  playgroundctl host compose ls", file=sys.stderr)
    print("", file=sys.stderr)
    print("  playgroundctl playground ps", file=sys.stderr)
    print("  playgroundctl playground image ls", file=sys.stderr)
    print("  playgroundctl playground image rm playground/<image>:<tag>", file=sys.stderr)
    print("  playgroundctl playground compose ls", file=sys.stderr)
    print("  playgroundctl playground compose ps", file=sys.stderr)
    print("  playgroundctl playground compose up", file=sys.stderr)
    print("  playgroundctl playground compose stop", file=sys.stderr)
    print("  playgroundctl playground compose restart", file=sys.stderr)
    print("  playgroundctl playground compose down", file=sys.stderr)
    print("  playgroundctl playground compose destroy", file=sys.stderr)
    print("  playgroundctl playground compose kill", file=sys.stderr)
    print("  playgroundctl playground compose build", file=sys.stderr)
    print("  playgroundctl playground compose reset", file=sys.stderr)
    print("  playgroundctl playground compose logs [tail]", file=sys.stderr)


def main():
    request = parse_args(sys.argv)

    if "client_error" in request:
        print(f"playgroundctl: {request['client_error']}", file=sys.stderr)

        if request.get("show_usage", True):
            usage()

        sys.exit(1)

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