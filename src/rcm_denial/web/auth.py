##########################################################
#
# Project: RCM - Denial Management
# Author:  RK (kvrkr866@gmail.com)
# File name: web/auth.py
# Purpose: Basic username/password authentication for
#          the NiceGUI web interface.
#
#          Enable via: WEB_AUTH_ENABLED=true
#          Users via:  WEB_AUTH_USERS=admin:admin,reviewer:pass123
#
##########################################################

from __future__ import annotations

from nicegui import app, ui


def _parse_users() -> dict[str, str]:
    """Parse WEB_AUTH_USERS into {username: password} dict."""
    from rcm_denial.config.settings import settings
    users: dict[str, str] = {}
    for pair in settings.web_auth_users.split(","):
        pair = pair.strip()
        if ":" in pair:
            username, password = pair.split(":", 1)
            users[username.strip()] = password.strip()
    return users


def is_authenticated() -> bool:
    """Check if the current user session is authenticated."""
    from rcm_denial.config.settings import settings
    if not settings.web_auth_enabled:
        return True
    return app.storage.user.get("authenticated", False)


def get_current_user() -> str:
    """Return current username or 'anonymous'."""
    return app.storage.user.get("username", "anonymous")


def require_auth() -> bool:
    """
    Guard for protected pages. Returns True if authenticated.
    Redirects to /login if not.
    """
    if not is_authenticated():
        ui.navigate.to("/login")
        return False
    return True


@ui.page("/login")
def login_page():
    """Login page — only shown when WEB_AUTH_ENABLED=true."""
    from rcm_denial.config.settings import settings

    if not settings.web_auth_enabled:
        ui.navigate.to("/")
        return

    if is_authenticated():
        ui.navigate.to("/")
        return

    with ui.column().classes("absolute-center items-center gap-4"):
        with ui.card().classes("w-80 p-6"):
            ui.label("RCM Denial Management").classes("text-xl font-bold text-center w-full")
            ui.label("Sign in to continue").classes("text-sm text-gray-500 text-center w-full mb-4")

            username_input = ui.input("Username").classes("w-full") \
                .props('autofocus outlined')
            password_input = ui.input("Password", password=True, password_toggle_button=True) \
                .classes("w-full").props("outlined")
            error_label = ui.label("").classes("text-red-500 text-sm hidden")

            async def try_login():
                users = _parse_users()
                uname = username_input.value.strip()
                passwd = password_input.value

                if uname in users and users[uname] == passwd:
                    app.storage.user["authenticated"] = True
                    app.storage.user["username"] = uname
                    ui.navigate.to("/")
                else:
                    error_label.set_text("Invalid username or password")
                    error_label.classes(remove="hidden")

            ui.button("Sign In", on_click=try_login).classes("w-full mt-2") \
                .props("color=primary")

            # Allow Enter key to submit
            password_input.on("keydown.enter", try_login)


@ui.page("/logout")
def logout_page():
    """Clear session and redirect to login."""
    app.storage.user.clear()
    ui.navigate.to("/login")
