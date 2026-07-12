#!/usr/bin/env python3
"""
Claude Playground Helper
========================
A deliberately tiny, locked-down HTTP API. It is the ONLY component on the
server that is allowed to talk to Docker.

Claude Code runs in a *separate* container that has no Docker access at all and
can only reach these fixed endpoints. The single running container is always
named "playground"; you deploy different projects into it by name.

Each project lives in its own folder under PROJECTS_DIR (one Dockerfile per
project). The recipe controls only *what* is built -- never *how* it is run:
the run-time flags below are fixed here, so a project cannot mount host paths,
gain capabilities, or run privileged.

All configuration comes from environment variables.
"""
import os
import re
import json
import hmac
import asyncio
import datetime
import subprocess
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# --- Fixed configuration (never taken from the request) ---------------------
TOKEN = os.environ["HELPER_TOKEN"]
CONTAINER = "playground"                       # the single running container
HOST_PORT = os.environ.get("PLAYGROUND_HOST_PORT", "8090")  # 127.0.0.1:<this> -> container
PROJECTS_DIR = os.environ.get("PROJECTS_DIR", "/projects")
INTERNAL_PORT = os.environ.get("PLAYGROUND_PORT", "8080")
MEMORY = os.environ.get("PLAYGROUND_MEMORY", "512m")
CPUS = os.environ.get("PLAYGROUND_CPUS", "1.0")
PIDS = os.environ.get("PLAYGROUND_PIDS", "256")
READ_ONLY = os.environ.get("PLAYGROUND_READ_ONLY", "true").lower() == "true"
BUILD_TIMEOUT = int(os.environ.get("BUILD_TIMEOUT", "1800"))

# Project names must be lowercase + map cleanly onto a docker image repo.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# The helper may ONLY ever touch images under this prefix. The prefix is fixed
# here and the suffix is always a validated project name, so the helper can
# never deploy or delete any other image that exists on the host.
IMAGE_PREFIX = "playground-"

# Every image the helper builds carries this label, so cleanup can find even
# untagged (dangling) leftovers that belong to us -- and nothing else.
IMAGE_LABEL = "app=playground"

# Cleanup policy (two separate thresholds)
#   - superseded layers of the SAME image (dangling leftovers from a rebuild)
#   - distinct project images (kept much longer; rebuild after they expire)
DANGLING_AGE_HOURS = float(os.environ.get("CLEANUP_DANGLING_AGE_HOURS", "72"))
IMAGE_AGE_HOURS = float(os.environ.get("CLEANUP_IMAGE_AGE_DAYS", "14")) * 24.0
CLEANUP_INTERVAL_HOURS = float(os.environ.get("CLEANUP_INTERVAL_HOURS", "6"))
AUTO_CLEANUP = os.environ.get("AUTO_CLEANUP", "true").lower() == "true"

# Persistent per-project last-deploy timestamps (so the retention countdown is
# measured from the last deploy, even when deploy does not rebuild).
STATE_DIR = os.environ.get("STATE_DIR", "/state")
STATE_FILE = os.path.join(STATE_DIR, "deployed.json")

# Swagger UI / OpenAPI. Off by default; enable for development. The helper isn't
# published publicly, so in dev you reach /docs via an SSH tunnel to the
# host-local port (see compose). The docs page itself is unauthenticated, but
# every endpoint still requires the bearer token to do anything.
ENABLE_DOCS = os.environ.get("ENABLE_DOCS", "false").lower() == "true"

app = FastAPI(
    title="Claude Playground Helper",
    version="1.0.0",
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None,
)


class ProjectBody(BaseModel):
    project: str


def _auth(authorization: Optional[str]) -> None:
    if not authorization or not hmac.compare_digest(authorization, f"Bearer {TOKEN}"):
        raise HTTPException(status_code=401, detail="unauthorized")


def _docker(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    # Fixed binary + argument list, no shell. Request data is never interpolated
    # into a shell, and project names are validated before reaching here.
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout
    )


def _tail(text: str, lines: int = 60) -> str:
    return "\n".join(text.splitlines()[-lines:])


def _project_dir(name: str) -> str:
    """Validate a project name and resolve it safely under PROJECTS_DIR."""
    if not NAME_RE.fullmatch(name):
        raise HTTPException(400, "invalid project name (use a-z, 0-9, _ or -)")
    base = os.path.realpath(PROJECTS_DIR)
    full = os.path.realpath(os.path.join(base, name))
    # Reject anything that escapes the projects directory (path traversal).
    if full != base and not full.startswith(base + os.sep):
        raise HTTPException(400, "invalid project path")
    if not os.path.isdir(full):
        raise HTTPException(404, f"no project '{name}'")
    if not os.path.isfile(os.path.join(full, "Dockerfile")):
        raise HTTPException(400, f"project '{name}' has no Dockerfile")
    return full


def _image_for(name: str) -> str:
    """Build the image reference for a validated project name. The prefix is
    fixed, so this can never name a non-playground image."""
    if not NAME_RE.fullmatch(name):
        raise HTTPException(400, "invalid project name (use a-z, 0-9, _ or -)")
    return f"{IMAGE_PREFIX}{name}:latest"


def _live_image_id() -> str:
    r = _docker("inspect", "-f", "{{.Image}}", CONTAINER)
    return r.stdout.strip() if r.returncode == 0 else ""


def _parse_ts(s: str):
    try:
        s = s.strip()
        if s.endswith("Z"):
            s = s[:-1]
        s = s.split(".")[0].split("+")[0]
        return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


def _age_hours(created: str):
    dt = _parse_ts(created)
    if dt is None:
        return None
    return (datetime.datetime.utcnow() - dt).total_seconds() / 3600.0


def _uptime_seconds(started: str):
    dt = _parse_ts(started)
    if dt is None:
        return None
    return max(0, int((datetime.datetime.utcnow() - dt).total_seconds()))


def _fmt_duration(sec):
    if sec is None:
        return None
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m"
    return f"{s}s"


def _our_image_ids() -> list:
    """Unique IDs of all our images (tagged AND dangling), by label."""
    ids = []
    for extra in ([], ["--filter", "dangling=true"]):
        r = _docker("images", "--filter", f"label={IMAGE_LABEL}",
                    "-q", "--no-trunc", *extra)
        for ln in r.stdout.splitlines():
            ln = ln.strip()
            if ln and ln not in ids:
                ids.append(ln)
    return ids


def _project_from_tag(tag: str):
    """playground-<name>:latest -> <name>."""
    if tag.startswith(IMAGE_PREFIX) and ":" in tag:
        return tag[len(IMAGE_PREFIX):tag.rfind(":")]
    return None


def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass


def _mark_deployed(project: str) -> None:
    state = _load_state()
    state[project] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    _save_state(state)


def _cleanup(dangling_age_h: float, image_age_h: float) -> dict:
    """Remove our UNUSED images by two rules. Dangling leftovers (superseded
    layers of a rebuilt image) go after dangling_age_h; distinct tagged project
    images go only after the longer image_age_h. The live image and anything not
    labelled as ours is never touched, and we never force-remove."""
    live = _live_image_id()
    state = _load_state()
    removed, kept = [], []
    for img_id in _our_image_ids():
        if img_id == live:
            kept.append("(live)")
            continue
        insp = _docker("inspect", "-f", "{{.Created}}|{{json .RepoTags}}", img_id)
        created, _, tags_json = insp.stdout.strip().partition("|")
        try:
            tags = json.loads(tags_json) or []
        except Exception:
            tags = []
        if not tags:                                   # superseded layer of an image
            threshold, target, label, stamp = dangling_age_h, [img_id], img_id[:19], created
        else:                                          # distinct project image, not live
            project = _project_from_tag(tags[0])
            # age from last deploy (state); fall back to image build time
            stamp = state.get(project) or created
            threshold, target, label = image_age_h, tags, tags[0]
        age = _age_hours(stamp)
        if age is None:                                # unknown age -> keep, to be safe
            kept.append(label)
            continue
        if age > threshold:
            r = _docker("rmi", *target)
            (removed if r.returncode == 0 else kept).append(label)
        else:
            kept.append(label)
    return {"removed": removed, "kept": kept,
            "dangling_age_hours": dangling_age_h, "image_age_hours": image_age_h}


@app.get("/projects")
def projects(authorization: Optional[str] = Header(default=None)):
    """List deployable projects (folders containing a Dockerfile)."""
    _auth(authorization)
    base = os.path.realpath(PROJECTS_DIR)
    found = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            d = os.path.join(base, name)
            if os.path.isdir(d) and os.path.isfile(os.path.join(d, "Dockerfile")):
                found.append(name)
    return {"projects": found}


@app.get("/status")
def status(authorization: Optional[str] = Header(default=None)):
    _auth(authorization)
    state = _docker("inspect", "-f", "{{.State.Status}}", CONTAINER)
    if state.returncode != 0:
        return {"container": CONTAINER, "state": "absent"}
    image = _docker("inspect", "-f", "{{.Config.Image}}", CONTAINER)
    return {
        "container": CONTAINER,
        "state": state.stdout.strip(),
        "image": image.stdout.strip() if image.returncode == 0 else None,
    }


@app.get("/ps")
def ps(authorization: Optional[str] = Header(default=None)):
    """Detailed view of the playground container: state, image, uptime, port."""
    _auth(authorization)
    fmt = ("{{.State.Status}}|{{.State.Running}}|{{.Config.Image}}|"
           "{{.State.StartedAt}}|{{.Created}}|{{.HostConfig.RestartPolicy.Name}}")
    r = _docker("inspect", "-f", fmt, CONTAINER)
    if r.returncode != 0:
        return {"container": CONTAINER, "state": "absent"}
    status_, running, image, started, created, restart = \
        (r.stdout.strip().split("|") + [""] * 6)[:6]
    is_running = running.strip() == "true"
    up = _uptime_seconds(started) if is_running else None
    return {
        "container": CONTAINER,
        "state": status_.strip(),
        "running": is_running,
        "image": image.strip(),
        "internal_port": INTERNAL_PORT,
        "network_alias": CONTAINER,
        "started_at": started.strip() or None,
        "uptime_seconds": up,
        "uptime": _fmt_duration(up),
        "created": created.strip() or None,
        "restart_policy": restart.strip() or None,
    }


@app.get("/logs")
def logs(
    authorization: Optional[str] = Header(default=None),
    tail: int = Query(default=200, ge=1, le=2000),
):
    _auth(authorization)
    r = _docker("logs", "--tail", str(tail), CONTAINER)
    if r.returncode != 0:
        raise HTTPException(404, "container not found")
    return {"logs": r.stdout + r.stderr}


@app.post("/start")
def start(authorization: Optional[str] = Header(default=None)):
    _auth(authorization)
    r = _docker("start", CONTAINER)
    if r.returncode != 0:
        raise HTTPException(409, _tail(r.stderr))
    return {"ok": True, "action": "start"}


@app.post("/stop")
def stop(authorization: Optional[str] = Header(default=None)):
    _auth(authorization)
    r = _docker("stop", CONTAINER, timeout=60)
    if r.returncode != 0:
        raise HTTPException(409, _tail(r.stderr))
    return {"ok": True, "action": "stop"}


@app.post("/restart")
def restart(authorization: Optional[str] = Header(default=None)):
    _auth(authorization)
    r = _docker("restart", CONTAINER, timeout=90)
    if r.returncode != 0:
        raise HTTPException(409, _tail(r.stderr))
    return {"ok": True, "action": "restart"}


def _run_playground(image: str) -> subprocess.CompletedProcess:
    run_args = [
        "run", "-d",
        "--name", CONTAINER,
        # Reached by the host Caddy via the loopback, like the other services.
        "-p", f"127.0.0.1:{HOST_PORT}:{INTERNAL_PORT}",
        "--restart", "unless-stopped",
        # --- fixed hardening: not controllable by the recipe ---
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--memory", MEMORY,
        "--cpus", CPUS,
        "--pids-limit", PIDS,
        "--label", "managed-by=claude-playground-helper",
    ]
    if READ_ONLY:
        run_args += ["--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m"]
    run_args.append(image)
    return _docker(*run_args)


def _build_image(project: str):
    """Build (or update) a project's image. Returns (ok, image, log)."""
    project_dir = _project_dir(project)
    image = _image_for(project)
    b = subprocess.run(
        ["docker", "build", "--label", IMAGE_LABEL, "-t", image, project_dir],
        capture_output=True, text=True, timeout=BUILD_TIMEOUT,
    )
    return b.returncode == 0, image, _tail(b.stdout + b.stderr, 80)


@app.post("/build")
def build(body: ProjectBody, authorization: Optional[str] = Header(default=None)):
    """Build/update a project's image WITHOUT touching the running playground."""
    _auth(authorization)
    ok, image, log = _build_image(body.project)
    if not ok:
        return JSONResponse(status_code=422, content={
            "ok": False, "stage": "build", "project": body.project, "log": log})
    return {"ok": True, "stage": "built", "project": body.project,
            "image": image, "build_log": _tail(log, 20)}


@app.post("/deploy")
def deploy(body: ProjectBody, authorization: Optional[str] = Header(default=None)):
    """Run an already-built project image in the single playground container.

    Does NOT rebuild: switching projects just swaps the running image. Only
    builds as a fallback if the image does not exist yet. Records the deploy
    time so the retention countdown is measured from last deploy.
    """
    _auth(authorization)

    image = _image_for(body.project)
    built = False
    if _docker("image", "inspect", image).returncode != 0:
        ok, image, log = _build_image(body.project)   # fallback: never built yet
        if not ok:
            return JSONResponse(status_code=422, content={
                "ok": False, "stage": "build", "project": body.project, "log": log})
        built = True

    _docker("rm", "-f", CONTAINER)  # ignore result; may not exist yet
    run = _run_playground(image)
    if run.returncode != 0:
        return JSONResponse(status_code=422, content={
            "ok": False, "stage": "run", "project": body.project,
            "log": _tail(run.stderr, 40)})

    _mark_deployed(body.project)
    return {
        "ok": True, "stage": "deployed", "project": body.project,
        "container": CONTAINER, "image": image, "internal_port": INTERNAL_PORT,
        "built_first": built,
    }


@app.get("/images")
def images(authorization: Optional[str] = Header(default=None)):
    """ps-style listing of our images: tag, id, size, created, last deploy, and
    which one is currently live. Never lists other images on the host."""
    _auth(authorization)
    live = _live_image_id()
    state = _load_state()
    r = _docker("images", "--filter", f"label={IMAGE_LABEL}", "--no-trunc",
                "--format", "{{.Repository}}:{{.Tag}}|{{.ID}}|{{.Size}}|{{.CreatedAt}}")
    items = []
    for ln in r.stdout.splitlines():
        if not ln.strip() or ln.startswith("<none>"):
            continue
        tag, img_id, size, created = (ln.split("|") + ["", "", "", ""])[:4]
        project = _project_from_tag(tag)
        items.append({
            "project": project,
            "image": tag,
            "id": img_id[:19],
            "size": size,
            "created": created,
            "last_deployed": state.get(project),
            "live": img_id == live,
        })
    d = _docker("images", "--filter", f"label={IMAGE_LABEL}",
                "--filter", "dangling=true", "-q")
    dangling = len([x for x in d.stdout.splitlines() if x.strip()])
    return {"images": items, "dangling_leftovers": dangling}


@app.post("/images/delete")
def delete_image(body: ProjectBody, authorization: Optional[str] = Header(default=None)):
    """Delete a single playground image. No force: Docker refuses if the image
    is currently used by the running playground container."""
    _auth(authorization)
    image = _image_for(body.project)
    r = _docker("rmi", image)
    if r.returncode != 0:
        raise HTTPException(409, _tail(r.stderr))
    return {"ok": True, "deleted": image}


@app.post("/images/cleanup")
def cleanup_images(authorization: Optional[str] = Header(default=None)):
    """Apply the retention policy now: drop superseded layers after
    DANGLING_AGE_HOURS and distinct project images after IMAGE_AGE_HOURS. Only
    our labelled images, never forced, live image always kept."""
    _auth(authorization)
    return _cleanup(DANGLING_AGE_HOURS, IMAGE_AGE_HOURS)


@app.on_event("startup")
async def _schedule_cleanup():
    if not AUTO_CLEANUP:
        return

    async def loop():
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)
            try:
                await asyncio.to_thread(_cleanup, DANGLING_AGE_HOURS, IMAGE_AGE_HOURS)
            except Exception:
                pass

    asyncio.create_task(loop())


@app.post("/session/new")
def session_new(authorization: Optional[str] = Header(default=None)):
    """Restart the claude-ctl container so the entrypoint starts a FRESH Remote
    Control session. The current session ends; a new one registers within a few
    seconds (URL in `docker logs -f claude-ctl`, or pick it in the Claude app).
    This is the one action here that targets claude-ctl rather than playground;
    the container name is fixed, so no other container can be restarted."""
    _auth(authorization)
    r = _docker("restart", "claude-ctl", timeout=60)
    if r.returncode != 0:
        raise HTTPException(409, _tail(r.stderr))
    return {"ok": True, "action": "restarted claude-ctl -> new Remote Control session"}


@app.get("/healthz")
def healthz():
    return {"ok": True}
