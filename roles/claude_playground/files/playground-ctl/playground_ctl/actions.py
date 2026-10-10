import os
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request

from . import docker_backend as docker
from . import policy

mutation_lock = threading.Lock()


def error(message):
    return {
        "ok": False,
        "exit_code": 1,
        "stdout": "",
        "stderr": message.rstrip() + "\n",
    }


# ---------------------------------------------------------------------------
# Environment selection.
#
# Env-scoped actions carry an optional "env" field (the environment NAME only).
# It is resolved against the root-owned allowlist (policy.resolve_environment)
# into a (workspace, port) pair. The sandbox never supplies a path or a port —
# only the key. Unknown/misconfigured envs return a normal error response.
# ---------------------------------------------------------------------------

def _resolve_env(request):
    """Return (workspace, port, None) or (None, None, error_dict)."""
    try:
        workspace, port = policy.resolve_environment(request.get("env"))
        return workspace, port, None
    except policy.PlaygroundEnvError as exc:
        return None, None, error(str(exc))


# ---------------------------------------------------------------------------
# playground_http_get — let the sandboxed AI verify its own deploys.
#
# The workspace app publishes on the HOST loopback (127.0.0.1:<env port>),
# which the claude-ctl container cannot reach (separate daemon, no exec by
# design). This broker-side fetch closes that gap through the same audited
# socket channel as compose: the target host is FIXED (loopback) and the port
# comes from the resolved environment's allowlist entry; the client only chooses
# the env + path. Redirects are not followed (a Location header could otherwise
# point the root broker at arbitrary hosts).
# ---------------------------------------------------------------------------

HTTP_GET_MAX_BODY = 8192


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def playground_http_get(request):
    workspace, port, err = _resolve_env(request)
    if err:
        return err

    path = request.get("path", "/")

    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or any(ord(c) < 0x21 or ord(c) > 0x7E for c in path)
    ):
        return error(
            "path must start with '/' and contain no whitespace/control characters"
        )

    url = "http://127.0.0.1:" + str(port) + path

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


# ---------------------------------------------------------------------------
# host_diag — read-only CPU-vs-I/O-vs-storage snapshot of the HOST.
#
# The sandbox sees the shared kernel's loadavg but has no attribution and no
# I/O/storage signal. This bundle (PSI + vmstat/iostat/mpstat + df/free) tells
# it WHICH resource is saturated. iostat/mpstat come from the sysstat package
# (installed by provisioning); every command is read-only.
# ---------------------------------------------------------------------------

_DIAG_INTERPRETATION = """\
=== how to read this ===
- pressure/cpu 'some avg10' high            -> CPU-bound (matches high loadavg)
- pressure/io 'some avg10' > ~20%, or vmstat 'wa' high, or iostat %util ~100%
  with high await                            -> DISK is the bottleneck, not CPU
- pressure/memory non-trivial or vmstat si/so > 0 -> memory/swap pressure
- df -h / df -i near full                    -> full disk/inodes silently break writes
Per-container attribution: use `playgroundctl host stats` (snapshot) or
`playgroundctl host promql '<expr>'` (history via Prometheus/cAdvisor).
"""


def host_diag(request):
    sections = []

    psi = []
    for res in ("cpu", "io", "memory"):
        path = "/proc/pressure/" + res
        try:
            with open(path) as handle:
                psi.append(res + ":\n" + handle.read().rstrip())
        except OSError as exc:
            psi.append(res + ": unreadable (" + str(exc) + ")")
    sections.append("=== PSI /proc/pressure (% of time stalled) ===\n" + "\n".join(psi))

    commands = [
        ("vmstat 1 3   (r=runqueue b=blocked-on-IO wa=iowait%)", ["vmstat", "1", "3"]),
        ("iostat -xz 1 2   (%util ~100 = saturated, await = latency ms)", ["iostat", "-xz", "1", "2"]),
        ("mpstat -P ALL 1 2   (per-core usr/sys/iowait/idle)", ["mpstat", "-P", "ALL", "1", "2"]),
        ("df -h", ["df", "-h"]),
        ("df -i   (inode exhaustion breaks writes even with free space)", ["df", "-i"]),
        ("free -h", ["free", "-h"]),
    ]

    for title, cmd in commands:
        try:
            res = docker.run_command(cmd, timeout=30)
            body = res["stdout"] if res["ok"] else res["stdout"] + res["stderr"]
        except FileNotFoundError:
            body = cmd[0] + ": not installed (host needs the sysstat package)\n"
        except subprocess.TimeoutExpired:
            body = cmd[0] + ": timed out\n"
        sections.append("=== " + title + " ===\n" + body.rstrip())

    sections.append(_DIAG_INTERPRETATION)

    return {
        "ok": True,
        "exit_code": 0,
        "stdout": "\n\n".join(sections) + "\n",
        "stderr": "",
    }


# ---------------------------------------------------------------------------
# host_promql — read-only Prometheus instant-query proxy.
#
# Prometheus (+ node-exporter + cAdvisor) already runs loopback-only on the
# host. This proxies ONLY GET /api/v1/query — no admin, no write, no other
# endpoints — so the agent gets history ("which container, since when") without
# any new network exposure. PromQL itself covers trends via range vectors /
# subqueries (e.g. avg_over_time(node_load1[1h])).
# ---------------------------------------------------------------------------

PROMETHEUS_URL = os.environ.get(
    "PLAYGROUND_CTL_PROMETHEUS_URL", "http://127.0.0.1:9090"
)
PROMQL_MAX_EXPR = 2000
PROMQL_MAX_BODY = 65536


def host_promql(request):
    query = request.get("query")

    if not isinstance(query, str) or not query.strip():
        return error("missing PromQL expression")

    if len(query) > PROMQL_MAX_EXPR:
        return error("PromQL expression too long (max " + str(PROMQL_MAX_EXPR) + " chars)")

    url = (
        PROMETHEUS_URL
        + "/api/v1/query?query="
        + urllib.parse.quote(query, safe="")
    )

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = resp.read(PROMQL_MAX_BODY + 1)
    except urllib.error.HTTPError as e:
        body = e.read(PROMQL_MAX_BODY + 1) if e.fp else b""
        return {
            "ok": True,
            "exit_code": 0,
            "stdout": body[:PROMQL_MAX_BODY].decode("utf-8", errors="replace") + "\n",
            "stderr": "prometheus returned HTTP " + str(e.code) + "\n",
        }
    except Exception as e:
        return error("prometheus query failed: " + str(e))

    text = body[:PROMQL_MAX_BODY].decode("utf-8", errors="replace")
    if len(body) > PROMQL_MAX_BODY:
        text += "\n[... response truncated at " + str(PROMQL_MAX_BODY) + " bytes ...]"

    return {
        "ok": True,
        "exit_code": 0,
        "stdout": text + "\n",
        "stderr": "",
    }


def playground_environments(request):
    """Discovery action: list the configured environments + their ports so the
    session knows what it can target with --env."""
    try:
        envs, default = policy.list_environments()
    except policy.PlaygroundEnvError as exc:
        return error(str(exc))

    lines = []
    for name in sorted(envs):
        marker = "  (default)" if name == default else ""
        lines.append(name + "\tport " + str(envs[name]["port"]) + marker)

    return {
        "ok": True,
        "exit_code": 0,
        "stdout": "\n".join(lines) + "\n",
        "stderr": "",
    }


# --- Env-scoped compose wrappers (resolve env -> workspace, then dispatch) ---

def _compose_ps(request):
    workspace, _port, err = _resolve_env(request)
    return err or docker.playground_compose_ps(workspace)


def _compose_logs(request):
    workspace, _port, err = _resolve_env(request)
    return err or docker.playground_compose_logs(workspace, request.get("tail", 200))


def _compose_up(request):
    workspace, _port, err = _resolve_env(request)
    return err or docker.playground_compose_up(workspace)


def _compose_stop(request):
    workspace, _port, err = _resolve_env(request)
    return err or docker.playground_compose_stop(workspace)


def _compose_restart(request):
    workspace, _port, err = _resolve_env(request)
    return err or docker.playground_compose_restart(workspace)


def _compose_down(request):
    workspace, _port, err = _resolve_env(request)
    return err or docker.playground_compose_down(workspace, remove_volumes=False)


def _compose_destroy(request):
    workspace, _port, err = _resolve_env(request)
    return err or docker.playground_compose_down(workspace, remove_volumes=True)


def _compose_kill(request):
    workspace, _port, err = _resolve_env(request)
    return err or docker.playground_compose_kill(workspace)


def _compose_build(request):
    workspace, _port, err = _resolve_env(request)
    return err or docker.playground_compose_build(workspace)


def _compose_reset(request):
    workspace, _port, err = _resolve_env(request)
    return err or docker.playground_compose_reset(workspace)


READ_ONLY_ACTIONS = {
    # Host Docker, read-only.
    "host_ps": lambda req: docker.host_ps(),
    "host_image_ls": lambda req: docker.host_image_ls(),
    "host_compose_ls": lambda req: docker.host_compose_ls(),

    # Host diagnostics, read-only (attribute saturation to container/subsystem).
    "host_stats": lambda req: docker.host_stats(),
    "host_diag": host_diag,
    "host_promql": host_promql,

    # Playground rootless Docker, read-only, daemon-wide (not env-scoped).
    "playground_ps": lambda req: docker.playground_ps(),
    "playground_image_ls": lambda req: docker.playground_image_ls(),
    "playground_compose_ls": lambda req: docker.playground_compose_ls(),
    "playground_environments": playground_environments,

    # Playground rootless Docker, read-only, env-scoped.
    "playground_compose_ps": _compose_ps,
    "playground_compose_logs": _compose_logs,

    # HTTP fetch of the env's workspace app via host loopback (verify deploys).
    "playground_http_get": playground_http_get,
}


MUTATING_ACTIONS = {
    # Playground rootless Docker, write-enabled, daemon-wide.
    "playground_image_rm": lambda req: docker.playground_image_rm(req.get("image")),

    # The ONE host-daemon mutation, hardcoded target: restart the supabase
    # edge-runtime so edited playground functions shed their cached workers.
    "functions_reload": lambda req: docker.functions_reload(),

    # Playground rootless Docker, write-enabled, env-scoped.
    "playground_compose_up": _compose_up,
    "playground_compose_stop": _compose_stop,
    "playground_compose_restart": _compose_restart,
    "playground_compose_down": _compose_down,
    "playground_compose_destroy": _compose_destroy,
    "playground_compose_kill": _compose_kill,
    "playground_compose_build": _compose_build,
    "playground_compose_reset": _compose_reset,
}


ALLOWED_FIELDS_BY_ACTION = {
    # Host read-only.
    "host_ps": {"action"},
    "host_image_ls": {"action"},
    "host_compose_ls": {"action"},
    "host_stats": {"action"},
    "host_diag": {"action"},
    "host_promql": {"action", "query"},
    "functions_reload": {"action"},

    # Playground read-only, daemon-wide.
    "playground_ps": {"action"},
    "playground_image_ls": {"action"},
    "playground_compose_ls": {"action"},
    "playground_environments": {"action"},

    # Playground read-only, env-scoped.
    "playground_compose_ps": {"action", "env"},
    "playground_compose_logs": {"action", "env", "tail"},
    "playground_http_get": {"action", "env", "path"},

    # Playground mutating, daemon-wide.
    "playground_image_rm": {"action", "image"},

    # Playground mutating, env-scoped.
    "playground_compose_up": {"action", "env"},
    "playground_compose_stop": {"action", "env"},
    "playground_compose_restart": {"action", "env"},
    "playground_compose_down": {"action", "env"},
    "playground_compose_destroy": {"action", "env"},
    "playground_compose_kill": {"action", "env"},
    "playground_compose_build": {"action", "env"},
    "playground_compose_reset": {"action", "env"},
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
