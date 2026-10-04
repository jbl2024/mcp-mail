from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml


class ConfigError(ValueError):
    """Invalid configuration."""


@dataclass(frozen=True)
class Settings:
    request_timeout_seconds: int = 20
    max_results: int = 100
    max_message_bytes: int = 10485760
    max_attachment_bytes: int = 5242880
    max_text_length: int = 100000
    max_thread_messages: int = 1000


@dataclass(frozen=True)
class CredentialProfile:
    username_env: str
    password_env: str

    def resolve(self) -> tuple[str, str]:
        values = tuple(os.environ.get(key, "") for key in (self.username_env, self.password_env))
        if not all(values):
            raise ConfigError("Missing mail credential environment variables")
        return values


@dataclass(frozen=True)
class AccountConfig:
    name: str
    label: str
    host: str
    port: int
    security: str
    credentials: str
    folders: tuple[str, ...] | None = None


@dataclass(frozen=True)
class AppConfig:
    settings: Settings
    credentials: dict[str, CredentialProfile]
    accounts: dict[str, AccountConfig]


def config_path_from_env() -> Path:
    return Path(os.environ.get("MCP_MAIL_CONFIG", "config.yaml")).expanduser()


def _integer(value, name, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ConfigError(f"{name} must be an integer between 1 and {maximum}")
    return value


def load_config(path: str | Path | None = None) -> AppConfig:
    try:
        raw = yaml.safe_load(Path(path or config_path_from_env()).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError("Cannot read mail configuration") from exc
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
        raise ConfigError("Configuration version must be 1")
    if set(raw) - {"version", "settings", "credentials", "accounts"}:
        raise ConfigError("Unknown configuration section")
    settings_raw = raw.get("settings", {})
    defaults = Settings()
    if not isinstance(settings_raw, dict) or set(settings_raw) - set(Settings.__annotations__):
        raise ConfigError("Unknown or invalid settings")
    settings = Settings(
        **{
            key: _integer(
                settings_raw.get(key, getattr(defaults, key)),
                key,
                {
                    "request_timeout_seconds": 120,
                    "max_results": 1000,
                    "max_thread_messages": 100000,
                    "max_text_length": 1000000,
                }.get(key, 100_000_000),
            )
            for key in Settings.__annotations__
        }
    )
    profiles = raw.get("credentials")
    if not isinstance(profiles, dict) or not profiles:
        raise ConfigError("credentials must be a non-empty mapping")
    credentials = {}
    for name, item in profiles.items():
        if not isinstance(item, dict) or set(item) != {"username_env", "password_env"}:
            raise ConfigError("Credentials must reference environment variables only")
        if not all(
            isinstance(v, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v) for v in item.values()
        ):
            raise ConfigError("Invalid credential environment variable")
        credentials[name] = CredentialProfile(**item)
    entries = raw.get("accounts")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("accounts must be a non-empty list")
    accounts = {}
    for item in entries:
        if not isinstance(item, dict) or set(item) - set(AccountConfig.__annotations__):
            raise ConfigError("Invalid account fields")
        name, host = item.get("name", ""), item.get("host", "")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
            raise ConfigError("Invalid account alias")
        if name in accounts:
            raise ConfigError("Duplicate account alias")
        if not isinstance(host, str) or not host or any(c in host for c in "/@\r\n\x00 "):
            raise ConfigError("host must be a hostname without credentials or URL")
        security = item.get("security", "tls")
        if security not in ("tls", "starttls"):
            raise ConfigError("security must be tls or starttls; plaintext is forbidden")
        profile = item.get("credentials")
        if not isinstance(profile, str) or profile not in credentials:
            raise ConfigError("Unknown credential profile")
        folders = item.get("folders")
        if folders is not None and (
            not isinstance(folders, list)
            or not folders
            or not all(
                isinstance(f, str) and f and not any(c in f for c in "\r\n\x00") for f in folders
            )
        ):
            raise ConfigError("folders must be omitted or a non-empty explicit allowlist")
        label = item.get("label", name)
        if not isinstance(label, str) or not label.strip():
            raise ConfigError("Invalid account label")
        accounts[name] = AccountConfig(
            name,
            label,
            host,
            _integer(item.get("port", 993 if security == "tls" else 143), "port", 65535),
            security,
            profile,
            tuple(dict.fromkeys(folders)) if folders is not None else None,
        )
    return AppConfig(settings, credentials, accounts)
