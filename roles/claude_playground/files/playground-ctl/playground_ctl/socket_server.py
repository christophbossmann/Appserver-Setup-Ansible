import grp
import json
import os
import socket
import threading

from .actions import handle_request
from .policy import SOCKET_PATH, SOCKET_GROUP

MAX_REQUEST_BYTES = 65536


def send_response(conn, response):
    conn.sendall(json.dumps(response).encode("utf-8"))


def handle_client(conn):
    with conn:
        try:
            data = conn.recv(MAX_REQUEST_BYTES)

            if not data:
                send_response(
                    conn,
                    {
                        "ok": False,
                        "exit_code": 1,
                        "stdout": "",
                        "stderr": "Empty request.\n",
                    },
                )
                return

            request = json.loads(data.decode("utf-8"))
            response = handle_request(request)
            send_response(conn, response)

        except json.JSONDecodeError:
            send_response(
                conn,
                {
                    "ok": False,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "Invalid JSON.\n",
                },
            )
        except Exception as e:
            send_response(
                conn,
                {
                    "ok": False,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": str(e) + "\n",
                },
            )


def run_socket_server():
    group = grp.getgrnam(SOCKET_GROUP)

    # The socket lives in its OWN directory so the claude-ctl container can
    # bind-mount the DIRECTORY instead of the socket file. After a reboot
    # /run is empty (tmpfs); if docker starts the container first, it
    # auto-creates missing bind-mount paths as directories — mounting the
    # socket file directly therefore raced the broker into a crash loop
    # (IsADirectoryError on cleanup). With the directory mount the race is
    # harmless: whoever creates the dir first wins, the socket appears
    # inside it later and is visible in the container immediately.
    socket_dir = os.path.dirname(SOCKET_PATH)

    # HARD guard: we chown/chmod socket_dir below — that must only ever hit
    # the broker's DEDICATED directory. A mismatched policy/module mix could
    # otherwise point dirname at a shared path like /run and lock every other
    # user out of it (this happened once during a deploy race: /run became
    # 750 root:playgroundctl and broke D-Bus/logind for the whole host).
    if os.path.basename(socket_dir) != "playground-ctl":
        raise RuntimeError(
            "refusing to manage socket dir %r — SOCKET_PATH must live in a "
            "dedicated 'playground-ctl' directory" % socket_dir
        )

    os.makedirs(socket_dir, exist_ok=True)
    os.chown(socket_dir, 0, group.gr_gid)
    os.chmod(socket_dir, 0o750)

    if os.path.isdir(SOCKET_PATH):
        # Leftover from the old socket-file mount layout: docker created the
        # socket PATH as a directory. Empty -> removable; if this fails the
        # old claude-ctl container still holds the mount — recreate it.
        os.rmdir(SOCKET_PATH)
    elif os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)

    os.chown(SOCKET_PATH, 0, group.gr_gid)
    os.chmod(SOCKET_PATH, 0o660)

    server.listen(20)

    print(f"playground-ctl listening on {SOCKET_PATH}", flush=True)

    while True:
        conn, _ = server.accept()
        threading.Thread(target=handle_client, args=(conn,), daemon=True).start()