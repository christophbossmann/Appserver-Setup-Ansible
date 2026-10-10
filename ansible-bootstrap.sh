#!/bin/bash
# bootstrap.sh - Run from the Ansible controller
# Usage: ./bootstrap.sh [--gen-key] <target_ip> <ssh_user> [ssh_key_path] [service_account]

set -e

GEN_KEY=false

# Parse --gen-key flag
if [ "$1" = "--gen-key" ]; then
    GEN_KEY=true
    shift
fi

TARGET_HOST="$1"
SSH_USER="$2"
ANSIBLE_KEY="${3:-$HOME/.ssh/ansible_key}"
SERVICE_ACCOUNT="${4:-ansible}"

if [ -z "$TARGET_HOST" ] || [ -z "$SSH_USER" ]; then
    echo "Usage: $0 [--gen-key] <target_ip> <ssh_user> [ssh_key_path] [service_account]"
    echo ""
    echo "Example: $0 203.0.113.10 admin"
    echo "         $0 --gen-key 203.0.113.10 admin"
    echo "         $0 203.0.113.10 admin ~/.ssh/ansible_key"
    echo "         $0 203.0.113.10 admin ~/.ssh/ansible_key ansible"
    exit 1
fi

# Check SSH key exists
if [ ! -f "$ANSIBLE_KEY" ]; then
    if [ "$GEN_KEY" = true ]; then
        echo "Generating SSH key at $ANSIBLE_KEY..."
        ssh-keygen -t ed25519 -f "$ANSIBLE_KEY" -C "ansible controller" -N ""
    else
        echo "Error: SSH key not found at $ANSIBLE_KEY"
        echo "Run with --gen-key to generate one, or specify an existing key as third argument."
        exit 1
    fi
fi

PUBLIC_KEY=$(cat "${ANSIBLE_KEY}.pub")

echo "Bootstrapping $TARGET_HOST via user $SSH_USER (service account: $SERVICE_ACCOUNT)..."

ssh -tt "$SSH_USER@$TARGET_HOST" "
    sudo useradd -m -s /bin/bash $SERVICE_ACCOUNT 2>/dev/null && echo 'User $SERVICE_ACCOUNT created' || echo 'User $SERVICE_ACCOUNT already exists';
    sudo mkdir -p /home/$SERVICE_ACCOUNT/.ssh;
    echo '$PUBLIC_KEY' | sudo tee /home/$SERVICE_ACCOUNT/.ssh/authorized_keys > /dev/null;
    sudo chmod 700 /home/$SERVICE_ACCOUNT/.ssh;
    sudo chmod 600 /home/$SERVICE_ACCOUNT/.ssh/authorized_keys;
    sudo chown -R $SERVICE_ACCOUNT:$SERVICE_ACCOUNT /home/$SERVICE_ACCOUNT/.ssh;
    echo '$SERVICE_ACCOUNT ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/$SERVICE_ACCOUNT > /dev/null;
    sudo chmod 440 /etc/sudoers.d/$SERVICE_ACCOUNT;
    sudo passwd -l $SERVICE_ACCOUNT;
    echo \"Done! $SERVICE_ACCOUNT user is ready on \$(hostname)\";
    exit
"

# Verify
echo ""
echo "Verifying SSH key login..."

# A passphrase-protected key can't be used non-interactively — and Ansible never prompts
# for it either. So a protected key MUST sit in a running ssh-agent: load it there now
# (one passphrase prompt), then verify the same way Ansible will connect (BatchMode).
if ! ssh-keygen -y -P "" -f "$ANSIBLE_KEY" >/dev/null 2>&1; then
    KEY_FP=$(ssh-keygen -lf "${ANSIBLE_KEY}.pub" | awk '{print $2}')
    AGENT_RC=0; ssh-add -l >/dev/null 2>&1 || AGENT_RC=$?   # 0 = keys loaded, 1 = empty agent, 2 = no agent
    if [ "$AGENT_RC" -eq 2 ]; then
        echo "Key $ANSIBLE_KEY has a passphrase, but no ssh-agent is running."
        echo "Ansible needs the key in an agent. Start one and load the key, e.g.:"
        echo "    eval \"\$(ssh-agent -s)\" && ssh-add $ANSIBLE_KEY"
        echo "(or use keychain / the Windows agent bridge so it survives new shells). Verifying"
        echo "once interactively now:"
        ssh -i "$ANSIBLE_KEY" -o IdentitiesOnly=yes "$SERVICE_ACCOUNT@$TARGET_HOST" 'echo "Success: connected as $(whoami) on $(hostname)"'
        exit $?
    fi
    if ! ssh-add -l 2>/dev/null | grep -q "$KEY_FP"; then
        echo "Key $ANSIBLE_KEY has a passphrase — adding it to the ssh-agent (needed for Ansible too):"
        ssh-add "$ANSIBLE_KEY"
    fi
fi

ssh -i "$ANSIBLE_KEY" -o IdentitiesOnly=yes -o BatchMode=yes "$SERVICE_ACCOUNT@$TARGET_HOST" 'echo "Success: connected as $(whoami) on $(hostname)"'