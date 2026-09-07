"""Client configuration and derived naming — shared pure helper (design D6).

Single source of truth (spec E-R2) for per-client file resolution and the
derived dataset/schema names consumed by the connector mains, dbt sources and
regression checks. Pure module: no ``dlt`` import and no side effects beyond
reading the client YAML through the resolved path.

Derived-name contract (NFR-2 deterministic; E-R1 keeps the YAML contract
unchanged — ``schema: client_<id>`` stays the dbt output schema):

* tenant raw dataset   : ``raw_<connector>_<client_id>``  (dlt ``dataset_name``)
* default-scope raw    : ``raw_<connector>`` when ``client_id`` is ``None``
  (legacy shared namespace, used by the freeze-regression default scope)
* dbt output schema    : ``client_<client_id>``

Clients directory resolution precedence (run_meta.py pattern):
``CLIENTS_DIR`` env -> ``/app/clients`` (container) -> repo ``../../clients``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import yaml

CONTAINER_CLIENTS_DIR = "/app/clients"


def resolve_clients_dir() -> str:
    """Return the clients directory: ``CLIENTS_DIR`` -> container -> repo."""
    clients_dir = os.environ.get("CLIENTS_DIR")
    if clients_dir:
        return clients_dir
    if os.path.exists(CONTAINER_CLIENTS_DIR):
        return CONTAINER_CLIENTS_DIR
    package_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(package_dir, "..", "..", "clients"))


def client_file_path(client_id: str) -> str:
    """Absolute path of the client YAML for ``client_id``."""
    return os.path.normpath(os.path.join(resolve_clients_dir(), f"{client_id}.yml"))


def load_client(client_id: str) -> dict[str, Any]:
    """Load and return the client YAML mapping for ``client_id``.

    Raises :class:`FileNotFoundError` when the file does not exist and
    :class:`ValueError` when the YAML root is not a mapping.
    """
    file_path = client_file_path(client_id)
    with open(file_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(
            f"Client file {file_path} must contain a YAML mapping, got {type(data).__name__}"
        )
    return data


def enabled_connectors(client: dict[str, Any]) -> list[str]:
    """Connector keys with ``enabled: true`` in YAML document order."""
    connectors = client.get("connectors", {})
    enabled: list[str] = []
    if not isinstance(connectors, dict):
        return enabled
    for name, config in connectors.items():
        if isinstance(name, str) and isinstance(config, dict) and config.get("enabled"):
            enabled.append(name)
    return enabled


def raw_dataset(client_id: str | None, connector: str) -> str:
    """dlt dataset name: ``raw_<connector>_<client_id>`` (legacy when None)."""
    base = f"raw_{connector}"
    if client_id is None:
        return base
    return f"{base}_{client_id}"


def output_schema(client_id: str) -> str:
    """dbt output schema for a tenant: ``client_<client_id>`` (B-R5)."""
    return f"client_{client_id}"


# ─── SDD-C: per-connector contract table + pure validator ──────────────────

# Contract row shape (design D2): ``required`` maps key -> expected type;
# ``optional`` maps key -> (type, default, empty_ok) where an absent optional
# applies its default silently; ``groups`` holds all-or-none key groups (the
# youtube OAuth trio, C-S4); ``env_keys`` lists YAML keys whose values name
# environment variables and ``env_globals`` fixed env names (google's
# GOOGLE_ADS_DEVELOPER_TOKEN, read at run_google.py:240). Types are
# str|int|bool only; ``int`` excludes bool and ``bool`` requires exact type.
CONNECTOR_CONTRACT: dict[str, dict[str, Any]] = {
    "meta": {
        "required": {"account_id": str, "token_env": str},
        "optional": {},
        "env_keys": ["token_env"],
        "env_globals": [],
    },
    "tiktok": {
        "required": {"account_id": str, "token_env": str},
        "optional": {},
        "env_keys": ["token_env"],
        "env_globals": [],
    },
    "google": {
        "required": {"customer_id": str, "token_env": str},
        "optional": {},
        "env_keys": ["token_env"],
        "env_globals": ["GOOGLE_ADS_DEVELOPER_TOKEN"],
    },
    "facebook": {
        "required": {"page_id": str, "token_env": str},
        "optional": {"insights_days_back": (int, 729, False)},
        "env_keys": ["token_env"],
        "env_globals": [],
    },
    "instagram": {
        "required": {"instagram_business_id": str, "token_env": str},
        "optional": {"insights_days_back": (int, 729, False)},
        "env_keys": ["token_env"],
        "env_globals": [],
    },
    "tiktok_organic": {
        "required": {
            "open_id": str,
            "client_key_env": str,
            "client_secret_env": str,
            "refresh_token_env": str,
        },
        "optional": {},
        "env_keys": ["client_key_env", "client_secret_env", "refresh_token_env"],
        "env_globals": [],
    },
    "youtube": {
        "required": {"channel_id": str, "token_env": str},
        "optional": {
            "captions_enabled": (bool, False, False),
            "region_code": (str, "CO", False),
            "analytics_start_date": (str, "2016-08-08", False),
        },
        "groups": [
            (
                "OAuth env keys",
                [
                    "oauth_client_id_env",
                    "oauth_client_secret_env",
                    "oauth_refresh_token_env",
                ],
            )
        ],
        "env_keys": ["token_env"],
        "env_globals": [],
    },
    "pinterest": {
        "required": {"token_env": str},
        # board_id is empty-ok: run_pinterest.py:208 ``.get("board_id") or None``.
        "optional": {"board_id": (str, None, True)},
        "env_keys": ["token_env"],
        "env_globals": [],
    },
    "ga4": {
        # service_account holds base64-or-path; deep validation happens at
        # runtime in run_ga4.py:96-107 (kept out to preserve purity, D2).
        "required": {"property_id": str, "service_account": str},
        "optional": {},
        "env_keys": [],
        "env_globals": [],
    },
    "gtm": {
        "required": {"account_path": str, "token_env": str},
        "optional": {},
        "env_keys": ["token_env"],
        "env_globals": [],
    },
}

_ROOT_ERROR = 'client: "connectors" root is missing or not a mapping'

_TYPE_LABELS = {str: "str", int: "int", bool: "bool"}


def _expected_label(expected: Any) -> str:
    return _TYPE_LABELS[expected]


def _is_empty(value: Any, expected: Any) -> bool:
    """True for null values and for blank strings on str-typed keys (D2)."""
    if value is None:
        return True
    if expected is str and isinstance(value, str):
        return not value.strip()
    return False


def _matches_type(value: Any, expected: Any) -> bool:
    """Strict type match; int excludes bool, bool needs exact type (D2)."""
    if expected is str:
        return isinstance(value, str)
    if expected is int:
        return type(value) is int
    if expected is bool:
        return type(value) is bool
    return False


def _missing_msg(connector: str, key: str, expected: Any) -> str:
    return (
        f'connector "{connector}": missing required key "{key}" '
        f"(expected non-empty {_expected_label(expected)})"
    )


def _type_msg(connector: str, key: str, value: Any, expected: Any) -> str:
    return (
        f'connector "{connector}": key "{key}" has type {type(value).__name__}, '
        f"expected {_expected_label(expected)}"
    )


def _empty_msg(connector: str, key: str, expected: Any) -> str:
    return (
        f'connector "{connector}": key "{key}" is empty '
        f"(expected non-empty {_expected_label(expected)})"
    )


def _validate_enabled_section(
    name: str, section: dict[str, Any], contract: dict[str, Any]
) -> list[str]:
    """Contract checks for one enabled connector section; one msg per violation."""
    errors: list[str] = []
    for key, expected in contract.get("required", {}).items():
        if key not in section:
            errors.append(_missing_msg(name, key, expected))
            continue
        value = section[key]
        if _is_empty(value, expected):
            errors.append(_missing_msg(name, key, expected))
        elif not _matches_type(value, expected):
            errors.append(_type_msg(name, key, value, expected))
    for key, spec in contract.get("optional", {}).items():
        if key not in section:
            continue
        expected, _default, empty_ok = spec
        value = section[key]
        if _is_empty(value, expected):
            if not empty_ok:
                errors.append(_empty_msg(name, key, expected))
        elif not _matches_type(value, expected):
            errors.append(_type_msg(name, key, value, expected))
    for label, keys in contract.get("groups", []):
        found = [
            key
            for key in keys
            if key in section and isinstance(section[key], str) and section[key].strip()
        ]
        if 0 < len(found) < len(keys):
            errors.append(
                f'connector "{name}": {label} must be all present or all absent '
                f"(found: {', '.join(found)})"
            )
    return errors


def validate_client(client: dict[str, Any]) -> list[str]:
    """Validate a client config against the per-connector contract (SDD-C).

    Pure and deterministic (NFR-2): no ``os.environ``, no dlt, no I/O beyond
    the dict. Only ``enabled`` connectors are validated (C-R2); ``dbt``, extra
    keys inside a connector and unknown disabled connectors are never validated
    (Q2). The ``connectors`` root error fires only for active clients (C-R3)
    while enabled-connector validation is active-independent (D3): an inactive
    client still gets its enabled sections checked, keeping C-S2 non-vacuous.
    ``active`` uses ``.get("active", True)`` semantics and is not type-checked.
    """
    errors: list[str] = []
    active = bool(client.get("active", True))
    connectors = client.get("connectors")
    if not isinstance(connectors, dict):
        if active:
            errors.append(_ROOT_ERROR)
        return errors
    for name, section in connectors.items():
        if not isinstance(section, dict):
            errors.append(
                f'connector "{name}": section is not a mapping, got {type(section).__name__}'
            )
            continue
        if "enabled" in section and type(section["enabled"]) is not bool:
            errors.append(
                f'connector "{name}": key "enabled" has type '
                f"{type(section['enabled']).__name__}, expected bool"
            )
            continue
        if section.get("enabled") is not True:
            continue
        contract = CONNECTOR_CONTRACT.get(name)
        if contract is None:
            supported = ", ".join(CONNECTOR_CONTRACT)
            errors.append(f'connector "{name}": unknown enabled connector (supported: {supported})')
            continue
        errors.extend(_validate_enabled_section(name, section, contract))
    return errors


def missing_envs(
    connector: str,
    section: dict[str, Any],
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Env names referenced by an enabled connector that are unset or empty.

    Mains-side env-presence guard helper (design D4 / M-R2): derives the env
    names from the ``env_keys``/``env_globals`` of CONNECTOR_CONTRACT (single
    source of truth, D2) and reports each name that ``environ.get(name)``
    treats as unset (absent or empty value). ``environ=None`` reads
    ``os.environ``; an injected mapping keeps the helper deterministic for
    unit tests. Pure: no I/O, no SystemExit — the callers print and exit.
    """
    contract = CONNECTOR_CONTRACT.get(connector)
    if contract is None:
        return []
    env = os.environ if environ is None else environ
    names = [section[key] for key in contract.get("env_keys", []) if section.get(key)]
    names += contract.get("env_globals", [])
    return [name for name in names if not env.get(name)]
