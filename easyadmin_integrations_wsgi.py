"""Production WSGI entry point for Easy Admin website integrations.

Render start command:
    gunicorn easyadmin_integrations_wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
"""
from __future__ import annotations

from typing import Any, Callable

from werkzeug.middleware.dispatcher import DispatcherMiddleware

import app as easyadmin_module
from app_modules.website_integrations import INTEGRATION_PREFIX, create_integration_app


def create_application() -> Callable[..., Any]:
    """Build the combined WSGI application with explicit startup validation."""
    primary_app = getattr(easyadmin_module, "app", None)
    if primary_app is None:
        module_path = getattr(easyadmin_module, "__file__", "unknown module path")
        raise RuntimeError(
            "Easy Admin's app.py was imported but no Flask object named 'app' was found. "
            f"Imported module: {module_path}"
        )

    integration_app = create_integration_app(easyadmin_module)
    return DispatcherMiddleware(primary_app, {INTEGRATION_PREFIX: integration_app})


# Gunicorn loads the explicit ``application`` object below.
application = create_application()

# Backwards-compatible alias for environments that still reference ``:app``.
app = application
