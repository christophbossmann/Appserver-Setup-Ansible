import os
import subprocess
import threading
import urllib.error
import urllib.request

from . import docker_backend as docker

mutation_lock = threading.Lock()


def error(message):
    return {
        "ok": False,
        "exit_code": 1,
        "stdout": "",
        "stderr": message.rstrip() + "\n",
    }


# ---------------------------------------------------------------------------
# playground_http_get — let the sandboxed AI verify its own deploys.
#
# The workspace app publishes on the HOST loopback (127.0.0.1:<public port>),
# which the claude-ctl container cannot reach (separate daemon, no exec by
# design). This broker-side fetch closes that gap through the same audited
# socket channel as compose: target host+port are FIXED (loopback + the
# configured public port); the client only chooses the path. Redirects are not
# followed (a Location header could otherwise point the root broker at
# arbitrary hosts).
# ---------------------------------------------------------------------------

HTTP_GET_MAX_BODY = 8192


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def playground_http_get(request):
    path = request.get("path", "/")

    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or any(ord(c) < 0x21 or ord(c) > 0x7E for c in path)
    ):
        return error(
            "path must start with '/' and contain no whitespace/control characters"
        )

    port = os.environ.get("PLAYGROUND_PUBLIC_PORT", "8090")
    url = "http://127.0.0.1:" + port + path

    def response_text(status, reason, headers, body_bytes):
        text = body_bytes[:HTTP_GET_MAX_BODY].decode("utf-8", errors="replace")
        out = "HTTP " + str(status) + " " + str(reason) + "\n"
        location = headers.get("Location") if headers else None
        if location:
            out += "Location: " + location + "\n"
        out += "\n" + text
        if len(body_bytes) > HTTP_GET_MAX_BODY:
            out += "\n[... body truncated at " + str(HTTP_GET_MAX_BODY) + " bytes ...]\n"
        return out

    opener = urllib.request.build_opener(_NoRedirect())

    try:
        with opener.open(url, timeout=5) as resp:
            body = resp.read(HTTP_GET_MAX_BODY + 1)
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": response_text(resp.status, resp.reason, resp.headers, body),
                "stderr": "",
            }
    except urllib.error.HTTPError as e:
        # 4xx/5xx and unfollowed redirects land here — the transport worked,
        # so report the status like curl does (exit 0, status in line 1).
        body = e.read(HTTP_GET_MAX_BODY + 1) if e.fp else b""
        return {
            "ok": True,
            "exit_code": 0,
            "stdout": response_text(e.code, e.reason, e.headers, body),
            "stderr": "",
        }
    except Exception as e:
        return error("http get " + url + " failed: " + str(e))


READ_ONLY_ACTIONS = {
    # Host Docker, read-only.
    "host_ps": lambda req: docker.host_ps(),
    "host_image_ls": lambda req: docker.host_image_ls(),
    "host_compose_ls": lambda req: docker.host_compose_ls(),

    # Playground rootless Docker, read-only.
    "playground_ps": lambda req: docker.playground_ps(),
    "playground_image_ls": lambda req: docker.playground_image_ls(),
    "playground_compose_ls": lambda req: docker.playground_compose_ls(),
    "playground_compose_ps": lambda req: docker.playground_compose_ps(),
    "playground_compose_logs": lambda req: docker.playground_compose_logs(req.get("tail", 200)),

    # HTTP fetch of the workspace app via host loopback (verify deploys).
    "playground_http_get": playground_http_get,
}


MUTATING_ACTIONS = {
    # Playground rootless Docker, write-enabled.
    "playground_image_rm": lambda req: docker.playground_image_rm(req.get("image")),
    "playground_compose_up": lambda req: docker.playground_compose_up(),
    "playground_compose_stop": lambda req: docker.playground_compose_stop(),
    "playground_compose_restart": lambda req: docker.playground_compose_restart(),
    "playground_compose_down": lambda req: docker.playground_compose_down(remove_volumes=False),
    "playground_compose_destroy": lambda req: docker.playground_compose_down(remove_volumes=True),
    "playground_compose_kill": lambda req: docker.playground_compose_kill(),
    "playground_compose_build": lambda req: docker.playground_compose_build(),
    "playground_compose_reset": lambda req: docker.playground_compose_reset(),
}


ALLOWED_FIELDS_BY_ACTION = {
    # Host read-only.
    "host_ps": {"action"},
    "host_image_ls": {"action"},
    "host_compose_ls": {"action"},

    # Playground read-only.
    "playground_ps": {"action"},
    "playground_image_ls": {"action"},
    "playground_compose_ls": {"action"},
    "playground_compose_ps": {"action"},
    "playground_compose_logs": {"action", "tail"},
    "playground_http_get": {"action", "path"},

    # Playground mutating.
    "playground_image_rm": {"action", "image"},
    "playground_compose_up": {"action"},
    "playground_compose_stop": {"action"},
    "playground_compose_restart": {"action"},
    "playground_compose_down": {"action"},
    "playground_compose_destroy": {"action"},
    "playground_compose_kill": {"action"},
    "playground_compose_build": {"action"},
    "playground_compose_reset": {"action"},
}


def validate_request_fields(request, action):
    allowed_fields = ALLOWED_FIELDS_BY_ACTION.get(action)

    if allowed_fields is None:
        return None

    unknown_fields = set(request.keys()) - allowed_fields

    if unknown_fields:
        return error(
            "Unknown fields not allowed for "
            + str(action)
            + ": "
            + ", ".join(sorted(unknown_fields))
        )

    return None


def handle_request(request):
    if not isinstance(request, dict):
        return error("Request must be a JSON object.")

    action = request.get("action")

    field_error = validate_request_fields(request, action)
    if field_error:
        return field_error

    try:
        if action in READ_ONLY_ACTIONS:
            return READ_ONLY_ACTIONS[action](request)

        if action in MUTATING_ACTIONS:
            with mutation_lock:
                return MUTATING_ACTIONS[action](request)

        allowed = sorted(
            list(READ_ONLY_ACTIONS.keys()) + list(MUTATING_ACTIONS.keys())
        )

        return error(
            "Action not allowed: "
            + str(action)
            + "\nAllowed actions: "
            + ", ".join(allowed)
        )

    except subprocess.TimeoutExpired:
        return error("Command timeout.")
    except Exception as e:
        return error(str(e))