#!/bin/bash
# Guardian Medical De-identifier - App Launcher

# Get the script's directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Ensure venv is activated
if [ -d ".venv" ]; then
    echo "Activating virtual environment..."
    source .venv/bin/activate
else
    echo "Error: Virtual environment (.venv) not found. Please set up the environment first."
    exit 1
fi

# Ensure dependencies are installed
if ! command -v streamlit &> /dev/null; then
    echo "Installing missing dependencies..."
    pip install -r requirements.txt streamlit
fi

# Start Streamlit application
echo "Starting Guardian Medical De-identifier..."
streamlit run app.py
