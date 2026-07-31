"""Backward-compatible WSGI alias for the generic website integration framework.

New Render deployments should use:
    gunicorn easyadmin_integrations_wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
"""
from easyadmin_integrations_wsgi import application

# Keep both conventional Gunicorn attribute names available.
app = application
