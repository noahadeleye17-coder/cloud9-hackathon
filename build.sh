#!/usr/bin/env bash
# Render build script.
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --no-input
python manage.py load_champions --ddragon || python manage.py load_champions
# Demo data only on an empty database; remove once GRID data is imported.
python manage.py seed_demo --if-empty
