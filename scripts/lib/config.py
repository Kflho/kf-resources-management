#!/usr/bin/env python3
"""Local, git-ignored configuration for kf-resources-management.

Priority: ``KF_PERSONAL_SERVICE_CONFIG`` > ``local.json`` next to SKILL.md > defaults.
No machine paths are committed; ``local.example.json`` is the template.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCAL = ROOT / 'local.json'


def config_path() -> Path:
    override = (os.environ.get('KF_PERSONAL_SERVICE_CONFIG', '').strip()
                or os.environ.get('MACHINE_KEEPER_CONFIG', '').strip())
    return Path(override) if override else LOCAL


def load() -> dict:
    path = config_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError):
        return {}


def get(section: str, key: str, default=None):
    value = (load().get(section) or {}).get(key)
    return default if value in (None, '') else value


def get_path(section: str, key: str, default: str = '') -> Path:
    return Path(str(get(section, key, default)))


def state_dir() -> Path:
    base = Path(os.environ.get('LOCALAPPDATA') or str(Path.home()))
    new = base / 'kf-personal-service'
    old = base / 'machine-keeper'
    if old.is_dir() and not new.exists():
        try:
            old.rename(new)
        except OSError:
            pass
    return new


def log_dir() -> Path:
    return state_dir() / 'logs'
