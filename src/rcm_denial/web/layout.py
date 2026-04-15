##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: web/layout.py
# Purpose: Shared layout components (header, footer, nav).
#          Separated from app.py to avoid circular imports.
#
##########################################################

from __future__ import annotations

from nicegui import ui


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
                    from rcm_denial.web.auth import get_current_user
                    user = get_current_user()
                    ui.label(f"{user}").classes("text-sm text-blue-200 ml-4")
                    ui.button("Logout", icon="logout",
                              on_click=lambda: ui.navigate.to("/logout")) \
                        .props("flat color=white size=sm")
            except Exception:
                pass


def create_footer() -> None:
    with ui.footer().classes("bg-gray-100 text-gray-500 text-xs py-2 px-6"):
        ui.label("RCM Denial Management v1.0.0 -- Agentic AI System")
