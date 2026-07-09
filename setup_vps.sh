#!/bin/bash
# setup_vps.sh
# Automated Setup & Scheduling Script for Ubuntu/Debian VPS
set -e

echo "=========================================================="
echo "      Insider Accumulation & Momentum Strategy (IAMS)"
echo "             VPS Setup and Cron Scheduler"
echo "=========================================================="

# Check if running on Linux
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo "Linux OS detected. Proceeding..."
else
    echo "Warning: This setup script is designed for a Linux VPS (Ubuntu/Debian)."
fi

# Update packages and install python dependencies
echo "1. Checking and installing Python 3, pip, and venv..."
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv sqlite3

# Get absolute path of this project directory
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
echo "Project directory: $PROJECT_DIR"

# Create virtual environment
echo "2. Setting up Python virtual environment..."
python3 -m venv "$PROJECT_DIR/venv"

# Activate venv and install requirements
echo "3. Installing dependencies from requirements.txt..."
"$PROJECT_DIR/venv/bin/pip" install --upgrade pip
"$PROJECT_DIR/venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

# Create logs directory
mkdir -p "$PROJECT_DIR/logs"

# Verify database initialization
echo "4. Initializing database schema..."
"$PROJECT_DIR/venv/bin/python" -c "from db.connection import init_db; init_db(); print('Database initialized successfully.')"

# Configure Crontab for automation (Runs hourly)
echo "5. Configuring Cron scheduler..."
CRON_JOB="0 * * * * cd $PROJECT_DIR && $PROJECT_DIR/venv/bin/python run.py >> $PROJECT_DIR/logs/cron.log 2>&1"

# Check if job already exists in crontab
(crontab -l 2>/dev/null | grep -F "$PROJECT_DIR/venv/bin/python run.py") && echo "Cron job is already set up." || {
    # Append the new cron job
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    echo "Hourly cron job successfully added to crontab."
}

echo "=========================================================="
echo "Setup Complete!"
echo "Your IAMS Bot is scheduled to run every hour."
echo "You can check progress logs at: $PROJECT_DIR/logs/cron.log"
echo "Make sure to copy your '.env' file to this directory!"
echo "=========================================================="
