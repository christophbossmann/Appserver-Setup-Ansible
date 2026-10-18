# Working rules for AI assistants in this repo

These rules apply to every session. They are the operator's decisions, not suggestions.

## Secrets (vault)

- The vault (`../ansible-private/group_vars/all/vault*.yml`) is encrypted with **ansible-vault**.
  Its password is only known to the operator.
- **The AI never reads, decrypts, prints or edits vault values** — not with `ansible-vault view/edit/
  decrypt`, not by dumping variables (`debug var=vault_*`, `-m debug`), not by reading files on the
  server that hold them.
- **New secrets:** the AI may *define* a new vault variable — its name, what it is for, how to
  generate it (e.g. `openssl rand -hex 32`) — and document it in `group_vars/all/vault.yml.example`.
  **The operator enters the value into the vault himself** (`ansible-vault edit`). The AI then asks
  him to do that and waits; it never generates or handles the real value.
- **The operator starts every Ansible run himself** (the vault files are encrypted as a whole, so
  every run needs the vault password). The AI prepares the exact command, always ending in
  `--ask-vault-pass 2>&1 | tee ~/run.log`, the operator runs it and says when it is done; the AI then
  reads `~/run.log`, judges the result and checks the server over SSH (no vault needed for that).
  The AI never runs `ansible-playbook` / `ansible` against the inventory itself and never asks for
  the vault password or a password file.

## How a run happens (step by step)

1. **The AI prepares the command** and says plainly what it is: a *check run* (`--check --diff`,
   changes nothing) or a *real run* (changes the server), which host and tags, and what it expects
   to happen. A real run on PROD is always preceded by a check run of the same tags.
2. **The command always has this shape** (only `--limit`, `--tags`, `--check --diff` and `-e` vary):
   ```bash
   cd ~/ansible
   ansible-playbook playbooks/server-all.yml --limit <host> --tags <tags> [--check --diff] \
     --ask-vault-pass 2>&1 | tee ~/run.log
   ```
   (other playbooks the same way: `playbooks/<name>.yml … --ask-vault-pass 2>&1 | tee ~/run.log`)
3. **The operator runs it** in WSL (vault password at the prompt) and answers "fertig". The SSH key
   is loaded once per session into the shared agent at `/tmp/claude-agent.sock` (the AI's SSH checks
   use the same one); the operator's terminal points to it with
   `export SSH_AUTH_SOCK=/tmp/claude-agent.sock` (or that line in `~/.bashrc`).
4. **The AI reads `~/run.log`** — failures, changed tasks, the diff — and checks the result on the
   server over SSH. It reports what changed, what was verified and what was not, and proposes the
   next command (e.g. the real run after a clean check run).

## Repository hygiene

- Two repos: this one is **public**; private config (inventory, group_vars, host_vars, vault) lives in
  `../ansible-private`. Never put private data (hostnames, IPs, keys, tokens, personal data) here;
  examples are `*.yml.example` (plain `*.yml` files under `group_vars/` would be loaded by Ansible).
- Before every commit: scan the staged diff for private data and binaries.
- Commit only when asked. **Never push** — the operator pushes himself.

## Changing the servers

- Every change goes through Ansible. Look at a `--check --diff` run before a real run on PROD.
- Prefer standard tools over home-grown scripts; no wrapper scripts or aliases for convenience.
