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
    echo "Example: $0 192.168.0.152 admin"
    echo "         $0 --gen-key 192.168.0.152 admin"
    echo "         $0 192.168.0.152 admin ~/.ssh/ansible_key"
    echo "         $0 192.168.0.152 admin ~/.ssh/ansible_key ansible"
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
ssh -i "$ANSIBLE_KEY" -o BatchMode=yes "$SERVICE_ACCOUNT@$TARGET_HOST" 'echo "Success: connected as $(whoami) on $(hostname)"'