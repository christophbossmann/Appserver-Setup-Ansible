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
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)

    group = grp.getgrnam(SOCKET_GROUP)
    os.chown(SOCKET_PATH, 0, group.gr_gid)
    os.chmod(SOCKET_PATH, 0o660)

    server.listen(20)

    print(f"playground-ctl listening on {SOCKET_PATH}", flush=True)

    while True:
        conn, _ = server.accept()
        threading.Thread(target=handle_client, args=(conn,), daemon=True).start()