#!/bin/sh
set -e

# Write container env vars to /etc/environment so crond jobs can source them
env > /etc/environment

# Start BusyBox crond in background; -d 8 logs to stderr at debug level
crond -b -d 8

# Hand off to uvicorn as PID 1
exec uvicorn main:app --host 0.0.0.0 --port 8000
