##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: web/app.py
# Purpose: NiceGUI web application -- entry point.
#
#          This is a THIN presentation layer. All business
#          logic is in rcm_denial.services / rcm_denial.main.
#
#          Launch:
#            rcm-denial web                   (via CLI)
#            python -m rcm_denial.web.app     (direct)
#
##########################################################

from __future__ import annotations

from nicegui import app, ui

# Import page modules -- each registers its own routes
from rcm_denial.web.pages import dashboard, process, review, claim_detail, stats, evals  # noqa: F401
# Auth module registers /login and /logout routes
from rcm_denial.web import auth  # noqa: F401


# ──────────────────────────────────────────────────────────────────────
# Static file serving for output PDFs
# ──────────────────────────────────────────────────────────────────────

try:
    from rcm_denial.config.settings import settings
    app.add_static_files("/output", str(settings.output_dir))
except Exception:
    pass


# ──────────────────────────────────────────────────────────────────────
# Server startup
# ──────────────────────────────────────────────────────────────────────

def start(host: str = "0.0.0.0", port: int = 8888, reload: bool = False) -> None:
    """Launch the NiceGUI web server."""
    from rcm_denial.config.settings import settings

    ui.run(
        host=host,
        port=port,
        title="RCM Denial Management",
        favicon="🏥",
        reload=reload,
        show=False,
        storage_secret=settings.web_auth_secret,
    )


if __name__ in {"__main__", "__mp_main__"}:
    start(reload=True)
