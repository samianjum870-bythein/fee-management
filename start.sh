#!/bin/bash
set -e

# Wait for database to be ready (max 30s)
echo "Waiting for PostgreSQL..."
for i in {1..30}; do
    if python -c "import os, psycopg2; psycopg2.connect(os.environ['DATABASE_URL'])" 2>/dev/null; then
        echo "Database ready!"
        break
    fi
    echo "Waiting... ($i/30)"
    sleep 1
done

# Run migrations (public + tenants)
echo "Running migrations..."
python manage.py migrate
python manage.py migrate_schemas --shared

# Ensure default tenant 'sh' exists
echo "Creating default tenant (if missing)..."
python manage.py shell -c "
from axis_saas.models import SchoolClient
SchoolClient.objects.get_or_create(
    schema_name='sh',
    defaults={
        'name': 'School',
        'admin_username': 'admin',
        'admin_password': 'admin123',
        'is_active': True
    }
)
print('Tenant check complete.')
"

# Start Gunicorn
echo "Starting Gunicorn..."
exec gunicorn axis_saas.wsgi:application --bind 0.0.0.0:7860 --workers 2 --threads 4 --worker-class gthread
