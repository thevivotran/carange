#!/bin/bash

# Carange Auto-Setup Script
# This script sets up Carange to run automatically on system boot

set -e

echo "========================================"
echo "  Carange Auto-Setup"
echo "========================================"
echo ""

# Get script directory and go to project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root (use sudo)"
    exit 1
fi

echo "Setting up Carange systemd service..."

# Copy service file
cp "$PROJECT_ROOT/config/carange.service" /etc/systemd/system/

# Reload systemd daemon
systemctl daemon-reload

# Enable service to start on boot
systemctl enable carange.service

echo ""
echo "Service installed successfully!"
echo ""
echo "Commands to manage the service:"
echo "  sudo systemctl start carange    # Start the service"
echo "  sudo systemctl stop carange     # Stop the service"
echo "  sudo systemctl restart carange  # Restart the service"
echo "  sudo systemctl status carange   # Check service status"
echo ""
echo "Would you like to start the service now? (y/n)"
read -r response
if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
    systemctl start carange.service
    echo ""
    echo "Service started! Check status with: sudo systemctl status carange"
fi

echo ""
echo "Setup complete!"
