#!/bin/bash

# Carange - Family Finance Tracker
# This script starts the Carange application

echo "========================================"
echo "  Carange - Family Finance Tracker"
echo "========================================"
echo ""

# Get script directory and go to project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Check if virtual environment exists
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate virtual environment
echo "Activating virtual environment..."
source .venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -q -r requirements.txt

# Run the application
echo ""
echo "Starting Carange server..."
echo "Local access: http://localhost:6868"
echo "Network access: http://$(hostname -I | awk '{print $1}'):6868"
echo ""
echo "Press Ctrl+C to stop the server"
echo "========================================"
echo ""

python main.py
