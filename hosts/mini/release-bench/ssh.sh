#!/bin/bash
IP=${RB_IP:-$(cat "${RB_DIR:?RB_DIR}/guest-ip")}
exec ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10 -o ServerAliveInterval=30 lume@$IP "$@"
