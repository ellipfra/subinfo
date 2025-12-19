#!/bin/bash
# Wrapper script to generate documentation SVGs
# Now uses generate-docs.py (simpler and more direct)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Check if venv exists, create if necessary
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate venv
source .venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -q rich requests web3

# Execute Python script from docs directory
python3 docs/generate-docs.py
