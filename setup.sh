#!/bin/bash
# One-time setup: creates a virtualenv, installs dependencies, and gives you a private ntfy topic.
set -e
cd "$(dirname "$0")"

python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

if [ ! -f config.local.yaml ]; then
  topic="pmh-bot-$(LC_ALL=C tr -dc 'a-z0-9' < /dev/urandom | head -c 12)"
  printf '# Private settings (not committed). Overrides config.yaml.\nnotifications:\n  ntfy_topic: "%s"\n' "$topic" > config.local.yaml
  echo "Your private ntfy topic: $topic"
  echo "  -> Install the 'ntfy' app on your phone, tap +, and subscribe to that topic."
fi

echo "Setup done. Try:  ./bot test-notify"
