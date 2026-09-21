"""
Secrets, in one place.

Running locally, credentials live in files the repo never sees:
    .env                         ANTHROPIC_API_KEY
    config/google_credentials.json
    config/auth.yaml

On Streamlit Community Cloud there is no filesystem to put them in, so the
same values come from st.secrets. Every module asks this helper instead of
reading files or the environment directly, which means deploying changes
nothing about how the rest of the code is written.

Lookup order, always: st.secrets -> environment -> local file.
"""

import json
import os
from pathlib import Path

# Load .env once, at import. Without this, settings.get() only sees
# variables already exported in the shell, so a bare
# `python -c "import settings; settings.describe()"` reported the Anthropic
# key as missing while the app found it (insights.py called load_dotenv
# first). A settings module that depends on someone else having loaded the
# environment is not a settings module.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

CRED_PATH = Path("config/google_credentials.json")
AUTH_PATH = Path("config/auth.yaml")
SHEETS_PATH = Path("config/sheets.json")


def _streamlit_secrets():
    """Returns st.secrets, or None outside a Streamlit process."""
    try:
        import streamlit as st
    except ImportError:
        return None
    try:
        # Touching st.secrets with no secrets.toml raises; treat as absent.
        _ = st.secrets
        return st.secrets
    except Exception:
        return None


def get(name, default=None):
    """A plain value: API key, spreadsheet id, anything scalar."""
    secrets = _streamlit_secrets()
    if secrets is not None:
        try:
            if name in secrets:
                return secrets[name]
        except Exception:
            pass
    return os.environ.get(name, default)


# ---------------------------------------------------------------------------
# Google service account
# ---------------------------------------------------------------------------

def google_credentials():
    """
    The service-account JSON as a dict, or None.

    On Streamlit Cloud it is pasted into Secrets as a [gcp_service_account]
    table; locally it is the downloaded file.
    """
    secrets = _streamlit_secrets()
    if secrets is not None:
        try:
            if "gcp_service_account" in secrets:
                return dict(secrets["gcp_service_account"])
        except Exception:
            pass

    raw = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if raw:
        return json.loads(raw)

    if CRED_PATH.exists():
        return json.loads(CRED_PATH.read_text(encoding="utf-8"))
    return None


def spreadsheet_id():
    value = get("SPREADSHEET_ID")
    if value:
        return value
    if SHEETS_PATH.exists():
        return json.loads(SHEETS_PATH.read_text(encoding="utf-8")).get(
            "spreadsheet_id")
    return None


# ---------------------------------------------------------------------------
# Application users
# ---------------------------------------------------------------------------

def auth_users():
    """
    {email: {user_id, name, role, team, password}} or None.

    Only ever holds PBKDF2 hashes — no plaintext password exists anywhere,
    on disk or in the deployment secrets.
    """
    secrets = _streamlit_secrets()
    if secrets is not None:
        try:
            if "auth" in secrets:
                return {email: dict(data)
                        for email, data in secrets["auth"].items()}
        except Exception:
            pass

    if AUTH_PATH.exists():
        import yaml
        return yaml.safe_load(AUTH_PATH.read_text(encoding="utf-8")) or {}
    return None


def describe():
    """Where each credential is coming from right now — for diagnostics."""
    secrets = _streamlit_secrets()
    return {
        "streamlit_secrets": secrets is not None,
        "anthropic_key": bool(get("ANTHROPIC_API_KEY")),
        "google_credentials": google_credentials() is not None,
        "spreadsheet_id": bool(spreadsheet_id()),
        "auth_users": len(auth_users() or {}),
    }