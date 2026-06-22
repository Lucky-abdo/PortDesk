#!/bin/bash
# Run PortDesk server in the background on Linux
cd "$(dirname "$0")"
nohup python3 portdesk-server.py > portdesk-server.log 2>&1 &
echo "PortDesk server started in background. PID=$!"
