#!/usr/bin/env bash
set -euo pipefail

# Install system packages needed for Python and Playwright.
sudo apt update
sudo apt install -y python3 python3-venv python3-pip curl ca-certificates

# Create virtual environment.
python3 -m venv .venv
source .venv/bin/activate

# Install Python dependencies.
pip install --upgrade pip
pip install -r requirements.txt

# Install Playwright Chromium with Linux dependencies.
python -m playwright install --with-deps chromium

echo "Install completed."
