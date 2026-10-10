# tailscale_enrol

Self-enrols a host's **own tailscale client into its CO-LOCATED headscale**, so a fresh
build converges in **one pass**.

## Why it exists

The `tailscale` role runs early (base network layer, before `docker`). For a host whose
control server is its **own** headscale, that is a bootstrap deadlock on a fresh build:
the client can't enrol into a headscale that isn't deployed yet, and the headscale
endpoint is normally reached through Caddy (`:443`), which comes up much later and — for
its `.int` sites — depends on the client's `tailscale0` having an IP.

This role breaks that: it runs **after `headscale_bootstrap`** (so the reusable pre-auth
key exists) but **before `caddy`**, and enrols via the headscale **loopback** endpoint
(`http://127.0.0.1:{{ headscale_port }}`) — which needs **neither Caddy nor DNS**. By the
time Caddy renders the `tailscale0`-bound `.int` sites, `tailscale0` already has an IP.

The infra node's control traffic then stays on-host (loopback), which is self-contained
and robust; other devices (family) still use the public `https://headscale.<host>` URL.

## Behaviour

- Runs only when `tailscale_self_enrol: true` (host_vars). No-op otherwise.
- No-op when the node is **already logged in** (steady-state re-runs never re-enrol).
- Reads the cached reusable pre-auth key from the controller at
  `{{ headscale_keys_dir }}/<host>/<tailscale_enrol_user>.preauthkey` (written by
  `headscale_bootstrap`), then `tailscale up --login-server=<loopback> --authkey=…`.
- Waits for `tailscale0` to get an IPv4.

Pair with the early `tailscale` role, which skips its interactive enrol on hosts where
`tailscale_self_enrol` is true (install + start only), deferring enrolment to here.

## Vars

See `defaults/main.yml`: `tailscale_self_enrol`, `tailscale_enrol_control_url`,
`tailscale_enrol_user` (defaults to `headscale_server_enrol_user`),
`tailscale_enrol_extra_args`.
