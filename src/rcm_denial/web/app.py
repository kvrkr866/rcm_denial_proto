##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: web/app.py
# Purpose: NiceGUI web application — entry point and layout.
#
#          This is a THIN presentation layer. All business
#          logic is in rcm_denial.services / rcm_denial.main.
#          This file can be replaced with React, Vue, etc.
#          without changing any backend code.
#
#          Launch:
#            rcm-denial web                   (via CLI)
#            python -m rcm_denial.web.app     (direct)
#
##########################################################

from __future__ import annotations

from nicegui import app, ui

# Import page modules — each registers its own routes
from rcm_denial.web.pages import dashboard, process, review, claim_detail, stats, evals  # noqa: F401
# Auth module registers /login and /logout routes
from rcm_denial.web import auth  # noqa: F401


# ──────────────────────────────────────────────────────────────────────
# Shared layout
# ──────────────────────────────────────────────────────────────────────

ACCENT = "#1976D2"

NAV_ITEMS = [
    ("Dashboard",      "/",           "dashboard"),
    ("Process Claims", "/process",    "play_circle"),
    ("Review Queue",   "/review",     "rate_review"),
    ("Stats",          "/stats",      "bar_chart"),
    ("Evals",          "/evals",      "science"),
]


def create_header() -> None:
    with ui.header().classes("items-center justify-between bg-blue-800 text-white px-6"):
        ui.label("RCM Denial Management").classes("text-xl font-bold")
        with ui.row().classes("gap-1"):
            for label, path, icon in NAV_ITEMS:
                ui.button(label, icon=icon, on_click=lambda p=path: ui.navigate.to(p)) \
                    .props("flat color=white size=sm")

            # Logout button (only when auth is enabled)
            try:
                from rcm_denial.config.settings import settings
                if settings.web_auth_enabled:
                    user = auth.get_current_user()
                    ui.label(f"{user}").classes("text-sm text-blue-200 ml-4")
                    ui.button("Logout", icon="logout",
                              on_click=lambda: ui.navigate.to("/logout")) \
                        .props("flat color=white size=sm")
            except Exception:
                pass


def create_footer() -> None:
    with ui.footer().classes("bg-gray-100 text-gray-500 text-xs py-2 px-6"):
        ui.label("RCM Denial Management v1.0.0 — Agentic AI System")


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

def start(host: str = "0.0.0.0", port: int = 8080, reload: bool = False) -> None:
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
