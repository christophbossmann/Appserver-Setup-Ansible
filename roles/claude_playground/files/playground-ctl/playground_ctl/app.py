# playground_ctl/app.py
# Start the broker:  sudo python3 -m playground_ctl.app
#
# The broker runs the hard preflight first (fail-fast on a misconfigured host),
# then opens the Unix socket and serves allowed requests. It performs NO
# provisioning of any kind.

from .preflight import run_preflight
from .socket_server import run_socket_server


def main():
    run_preflight()
    run_socket_server()


if __name__ == "__main__":
    main()
