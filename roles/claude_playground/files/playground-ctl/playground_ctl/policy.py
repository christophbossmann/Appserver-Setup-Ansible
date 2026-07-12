from pathlib import Path

SOCKET_PATH = "/run/playground-ctl.sock"
SOCKET_GROUP = "playgroundctl"

PLAYGROUND_USER = "playground"
PLAYGROUND_WORKSPACE = Path("/home/playground/workspace")

READ_TIMEOUT = 20
MUTATION_TIMEOUT = 300
MAX_LOG_TAIL = 1000

ALLOWED_IMAGE_PREFIXES = {
    "playground/",
}