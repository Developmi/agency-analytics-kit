"""Unit tests for the shared client_config naming/resolution helper.

Covers spec E-R1 (client contract shape + ``schema`` consistency), E-R2
(naming single-sourced in a pure helper), NFR-2 (deterministic derived
names) and the clients-dir resolution precedence
``CLIENTS_DIR`` -> ``/app/clients`` -> repo ``clients/`` (design D6).

RED-first work unit: this file references ``agency_analytics.client_config``
which does not exist yet at write time; it is created in the GREEN step.
"""

from __future__ import annotations

import os

import pytest
import yaml

from agency_analytics import client_config

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_CLIENTS_DIR = os.path.normpath(os.path.join(REPO_ROOT, "clients"))

# Canonical top-level contract keys (E-R1): no field is added/removed by this
# change, so every tracked YAML must expose exactly this shape.
CANONICAL_KEYS = {"client_id", "client_name", "schema", "active", "connectors", "dbt"}

TRACKED_FILES = ("_template", "acme", "nike")


@pytest.fixture(autouse=True)
def _clear_clients_env(monkeypatch):
    """Default to repo-level resolution unless a test sets CLIENTS_DIR."""
    monkeypatch.delenv("CLIENTS_DIR", raising=False)


def _write_client_yaml(tmp_path, name, client):
    (tmp_path / f"{name}.yml").write_text(yaml.safe_dump(client), encoding="utf-8")


# ─── resolve_clients_dir ───────────────────────────────────────────────────


class TestResolveClientsDir:
    def test_env_var_wins(self, tmp_path, monkeypatch):
        target = tmp_path / "custom-clients"
        target.mkdir()
        monkeypatch.setenv("CLIENTS_DIR", str(target))
        assert client_config.resolve_clients_dir() == str(target)

    def test_container_dir_used_when_present(self, monkeypatch):
        real_exists = os.path.exists

        def _fake_exists(path):
            return path == client_config.CONTAINER_CLIENTS_DIR or real_exists(path)

        monkeypatch.setattr(os.path, "exists", _fake_exists)
        assert client_config.resolve_clients_dir() == client_config.CONTAINER_CLIENTS_DIR

    def test_repo_fallback_reaches_tracked_clients(self):
        result = client_config.resolve_clients_dir()
        assert result == REPO_CLIENTS_DIR
        # Behavioral: whichever fallback won, the tracked YAMLs must be reachable.
        assert os.path.exists(os.path.join(result, "acme.yml"))
        assert os.path.exists(os.path.join(result, "_template.yml"))


# ─── client_file_path ──────────────────────────────────────────────────────


class TestClientFilePath:
    def test_under_env_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLIENTS_DIR", str(tmp_path))
        expected = os.path.normpath(os.path.join(str(tmp_path), "acme.yml"))
        assert client_config.client_file_path("acme") == expected

    @pytest.mark.parametrize("client_id", ["acme", "nike"])
    def test_under_repo_dir_default(self, client_id):
        path = client_config.client_file_path(client_id)
        assert path == os.path.normpath(os.path.join(REPO_CLIENTS_DIR, f"{client_id}.yml"))
        assert os.path.exists(path)


# ─── load_client ───────────────────────────────────────────────────────────


class TestLoadClient:
    @pytest.mark.parametrize(
        ("file_stem", "client_id"),
        [("acme", "acme"), ("nike", "nike"), ("_template", "new_client")],
    )
    def test_loads_tracked_client(self, file_stem, client_id):
        client = client_config.load_client(file_stem)
        assert client["client_id"] == client_id
        assert isinstance(client.get("schema"), str)
        assert isinstance(client.get("connectors"), dict)

    def test_missing_client_raises(self):
        with pytest.raises(FileNotFoundError):
            client_config.load_client("ghost-client")

    def test_non_mapping_yaml_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLIENTS_DIR", str(tmp_path))
        (tmp_path / "bad.yml").write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(ValueError, match="mapping"):
            client_config.load_client("bad")


# ─── enabled_connectors ────────────────────────────────────────────────────


class TestEnabledConnectors:
    @pytest.mark.parametrize(
        ("client_id", "expected"),
        [
            ("acme", ["meta", "google"]),
            ("nike", ["meta", "tiktok"]),
        ],
    )
    def test_tracked_clients_reflect_yaml(self, client_id, expected):
        client = client_config.load_client(client_id)
        assert client_config.enabled_connectors(client) == expected

    def test_all_disabled_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLIENTS_DIR", str(tmp_path))
        _write_client_yaml(
            tmp_path,
            "quiet",
            {
                "client_id": "quiet",
                "schema": "client_quiet",
                "active": True,
                "connectors": {
                    "meta": {"enabled": False, "account_id": "1", "token_env": "M"},
                    "google": {"enabled": False, "customer_id": "2", "token_env": "G"},
                },
            },
        )
        client = client_config.load_client("quiet")
        assert client_config.enabled_connectors(client) == []

    def test_missing_connectors_key_returns_empty(self):
        client = {"client_id": "x", "schema": "client_x", "active": True}
        assert client_config.enabled_connectors(client) == []


# ─── raw_dataset (A-R1 naming + E-R2 single source) ────────────────────────


class TestRawDataset:
    @pytest.mark.parametrize("connector", ["meta", "instagram", "tiktok_organic", "ga4"])
    def test_default_scope_is_legacy(self, connector):
        # client_id=None is the default-scope call (freeze regression, legacy).
        assert client_config.raw_dataset(None, connector) == f"raw_{connector}"

    @pytest.mark.parametrize(
        ("client_id", "connector", "expected"),
        [
            ("acme", "meta", "raw_meta_acme"),
            ("acme", "google", "raw_google_acme"),
            ("nike", "meta", "raw_meta_nike"),
            ("nike", "tiktok_organic", "raw_tiktok_organic_nike"),
            ("delta", "ga4", "raw_ga4_delta"),
        ],
    )
    def test_tenant_scoped(self, client_id, connector, expected):
        assert client_config.raw_dataset(client_id, connector) == expected

    def test_tenants_and_legacy_do_not_collide(self):
        names = {
            client_config.raw_dataset(None, "meta"),
            client_config.raw_dataset("acme", "meta"),
            client_config.raw_dataset("nike", "meta"),
        }
        assert len(names) == 3


# ─── output_schema (B-R5 naming) ───────────────────────────────────────────


class TestOutputSchema:
    @pytest.mark.parametrize(
        ("client_id", "expected"),
        [("acme", "client_acme"), ("nike", "client_nike"), ("delta", "client_delta")],
    )
    def test_derived(self, client_id, expected):
        assert client_config.output_schema(client_id) == expected

    @pytest.mark.parametrize("file_stem", ["acme", "nike"])
    def test_matches_yaml_schema_field(self, file_stem):  # E-R1
        client = client_config.load_client(file_stem)
        assert client_config.output_schema(client["client_id"]) == client["schema"]


# ─── NFR-2 determinism of derived contracts ────────────────────────────────


class TestDeterministicNames:
    @pytest.mark.parametrize(
        ("client_id", "connector"),
        [("acme", "meta"), ("nike", "tiktok_organic"), (None, "google")],
    )
    def test_raw_dataset_stable_across_calls(self, client_id, connector):
        assert client_config.raw_dataset(client_id, connector) == client_config.raw_dataset(
            client_id, connector
        )

    def test_output_schema_stable_across_calls(self):
        assert client_config.output_schema("acme") == client_config.output_schema("acme")

    def test_enabled_connectors_stable_across_calls(self):
        client = client_config.load_client("nike")
        assert client_config.enabled_connectors(client) == client_config.enabled_connectors(client)

    def test_derived_names_stable_across_reloads(self):
        first = client_config.load_client("acme")
        second = client_config.load_client("acme")
        assert client_config.raw_dataset(first["client_id"], "meta") == client_config.raw_dataset(
            second["client_id"], "meta"
        )


# ─── E-R1 contract shape: no new fields, template canonical ────────────────


class TestClientContract:
    @pytest.mark.parametrize("file_stem", TRACKED_FILES)
    def test_top_level_keys_exact(self, file_stem):
        client = client_config.load_client(file_stem)
        assert set(client) == CANONICAL_KEYS

    def test_tracked_connector_keys_are_template_subset(self):
        template = client_config.load_client("_template")
        template_connectors = set(template["connectors"])
        for file_stem in ("acme", "nike"):
            client = client_config.load_client(file_stem)
            assert set(client["connectors"]) <= template_connectors


# ─── validate_client (SDD-C WU1: per-connector contract validator, pure) ───


def _client_with(connectors, active=True):
    """Minimal client dict — validate_client reads only ``active`` + ``connectors``."""
    client = {"connectors": connectors}
    if active is not None:
        client["active"] = active
    return client


# Contract-clean enabled section per supported connector (design D2 rows).
_CLEAN_ENABLED = {
    "meta": {"enabled": True, "account_id": "1", "token_env": "META_TOKEN"},
    "tiktok": {"enabled": True, "account_id": "2", "token_env": "TIKTOK_TOKEN"},
    "google": {"enabled": True, "customer_id": "3", "token_env": "GOOGLE_TOKEN"},
    "facebook": {"enabled": True, "page_id": "4", "token_env": "FACEBOOK_TOKEN"},
    "instagram": {
        "enabled": True,
        "instagram_business_id": "5",
        "token_env": "INSTAGRAM_TOKEN",
    },
    "tiktok_organic": {
        "enabled": True,
        "open_id": "6",
        "client_key_env": "TO_CLIENT_KEY",
        "client_secret_env": "TO_CLIENT_SECRET",
        "refresh_token_env": "TO_REFRESH_TOKEN",
    },
    "youtube": {"enabled": True, "channel_id": "7", "token_env": "YOUTUBE_TOKEN"},
    "pinterest": {"enabled": True, "token_env": "PINTEREST_TOKEN"},
    "ga4": {"enabled": True, "property_id": "8", "service_account": "sa-key"},
    "gtm": {"enabled": True, "account_path": "9", "token_env": "GTM_TOKEN"},
}

_REQUIRED_MISSING_CASES = [
    ("meta", "account_id"),
    ("tiktok", "token_env"),
    ("google", "customer_id"),
    ("facebook", "page_id"),
    ("instagram", "instagram_business_id"),
    ("tiktok_organic", "open_id"),
    ("tiktok_organic", "client_key_env"),
    ("youtube", "channel_id"),
    ("pinterest", "token_env"),
    ("ga4", "service_account"),
    ("gtm", "account_path"),
]

_OPTIONAL_BEARERS = ("facebook", "instagram", "youtube", "pinterest")


class TestValidateClient:
    # ── C-S1: every violation surfaces, [] once fixed ──────────────────────

    def test_cs1_broken_active_client_reports_every_error(self):
        client = _client_with(
            {
                "ga4": {"enabled": True, "property_id": "8"},
                "facebook": {
                    "enabled": True,
                    "page_id": "4",
                    "token_env": "FACEBOOK_TOKEN",
                    "insights_days_back": "x",
                },
            }
        )
        errors = client_config.validate_client(client)
        assert len(errors) == 2
        assert 'missing required key "service_account"' in errors[0]
        assert "ga4" in errors[0]
        assert 'key "insights_days_back" has type str, expected int' in errors[1]
        assert "facebook" in errors[1]

    def test_cs1_same_yaml_clean_once_fixed(self):
        client = _client_with(
            {
                "ga4": {"enabled": True, "property_id": "8", "service_account": "sa"},
                "facebook": {
                    "enabled": True,
                    "page_id": "4",
                    "token_env": "FACEBOOK_TOKEN",
                    "insights_days_back": 729,
                },
            }
        )
        assert client_config.validate_client(client) == []

    # ── C-S3: connectors root absent ───────────────────────────────────────

    def test_cs3_active_without_connectors_root_single_error(self):
        for client in (
            {"active": True, "client_id": "x"},
            {"client_id": "x"},  # active defaults True when absent (D3)
        ):
            assert client_config.validate_client(client) == [
                'client: "connectors" root is missing or not a mapping'
            ]

    def test_cs3_inactive_without_connectors_root_clean(self):
        client = {"active": False, "client_id": "x"}
        assert client_config.validate_client(client) == []

    # ── C-S4: youtube OAuth trio all-or-none ───────────────────────────────

    def test_cs4_partial_trio_one_of_three_is_error(self):
        section = dict(_CLEAN_ENABLED["youtube"])
        section["oauth_client_id_env"] = "YOUTUBE_OAUTH_CLIENT_ID_ACME"
        errors = client_config.validate_client(_client_with({"youtube": section}))
        assert len(errors) == 1
        assert "all present or all absent" in errors[0]
        assert "oauth_client_id_env" in errors[0]

    def test_cs4_partial_trio_two_of_three_is_error(self):
        section = dict(_CLEAN_ENABLED["youtube"])
        section["oauth_client_id_env"] = "YOUTUBE_OAUTH_CLIENT_ID_ACME"
        section["oauth_client_secret_env"] = "YOUTUBE_OAUTH_CLIENT_SECRET_ACME"
        errors = client_config.validate_client(_client_with({"youtube": section}))
        assert len(errors) == 1
        assert "oauth_client_id_env" in errors[0]
        assert "oauth_client_secret_env" in errors[0]

    def test_cs4_full_trio_is_clean(self):
        section = dict(_CLEAN_ENABLED["youtube"])
        section.update(
            {
                "oauth_client_id_env": "YOUTUBE_OAUTH_CLIENT_ID_ACME",
                "oauth_client_secret_env": "YOUTUBE_OAUTH_CLIENT_SECRET_ACME",
                "oauth_refresh_token_env": "YOUTUBE_OAUTH_REFRESH_TOKEN_ACME",
            }
        )
        assert client_config.validate_client(_client_with({"youtube": section})) == []

    # ── Required keys: missing / empty / whitespace / wrong type ───────────

    @pytest.mark.parametrize(("connector", "missing_key"), _REQUIRED_MISSING_CASES)
    def test_required_missing_reported_per_connector(self, connector, missing_key):
        section = dict(_CLEAN_ENABLED[connector])
        del section[missing_key]
        errors = client_config.validate_client(_client_with({connector: section}))
        assert len(errors) == 1
        assert f'connector "{connector}"' in errors[0]
        assert f'missing required key "{missing_key}"' in errors[0]

    @pytest.mark.parametrize(
        ("connector", "key", "broken_value"),
        [
            ("ga4", "service_account", ""),
            ("meta", "account_id", "   "),
            ("google", "customer_id", None),
        ],
    )
    def test_required_blank_or_null_is_missing(self, connector, key, broken_value):
        section = dict(_CLEAN_ENABLED[connector])
        section[key] = broken_value
        errors = client_config.validate_client(_client_with({connector: section}))
        assert len(errors) == 1
        assert f'missing required key "{key}"' in errors[0]

    @pytest.mark.parametrize(
        ("connector", "key", "wrong_value", "got_type"),
        [
            ("meta", "account_id", 123, "int"),
            ("ga4", "service_account", True, "bool"),
            ("google", "customer_id", ["a"], "list"),
        ],
    )
    def test_required_present_wrong_type_is_error(self, connector, key, wrong_value, got_type):
        section = dict(_CLEAN_ENABLED[connector])
        section[key] = wrong_value
        errors = client_config.validate_client(_client_with({connector: section}))
        assert len(errors) == 1
        assert f'key "{key}" has type {got_type}, expected str' in errors[0]

    # ── Optional keys: absent OK, present wrong-type/empty error ───────────

    @pytest.mark.parametrize("connector", _OPTIONAL_BEARERS)
    def test_optional_absent_is_clean(self, connector):
        # _CLEAN_ENABLED rows omit every optional key for these connectors.
        assert (
            client_config.validate_client(_client_with({connector: _CLEAN_ENABLED[connector]}))
            == []
        )

    def test_optional_present_wrong_type_is_error(self):
        instagram = dict(_CLEAN_ENABLED["instagram"])
        instagram["insights_days_back"] = "30"
        errors = client_config.validate_client(_client_with({"instagram": instagram}))
        assert len(errors) == 1
        assert 'key "insights_days_back" has type str, expected int' in errors[0]
        youtube = dict(_CLEAN_ENABLED["youtube"])
        youtube["captions_enabled"] = "false"
        errors = client_config.validate_client(_client_with({"youtube": youtube}))
        assert len(errors) == 1
        assert 'key "captions_enabled" has type str, expected bool' in errors[0]

    def test_optional_present_int_value_for_bool_key_is_error(self):
        youtube = dict(_CLEAN_ENABLED["youtube"])
        youtube["captions_enabled"] = 1  # truthy, but not a bool → D2 strict typing
        errors = client_config.validate_client(_client_with({"youtube": youtube}))
        assert len(errors) == 1
        assert 'key "captions_enabled" has type int, expected bool' in errors[0]

    def test_optional_present_empty_on_strict_key_is_error(self):
        youtube = dict(_CLEAN_ENABLED["youtube"])
        youtube["region_code"] = None  # run_youtube.py str(None) == "None" → bug
        errors = client_config.validate_client(_client_with({"youtube": youtube}))
        assert len(errors) == 1
        assert 'key "region_code" is empty' in errors[0]

    def test_board_id_empty_or_null_is_ok(self):
        # run_pinterest.py:208 connector.get("board_id") or None → ""/null legal.
        for board_id in ("", None):
            section = dict(_CLEAN_ENABLED["pinterest"])
            section["board_id"] = board_id
            assert client_config.validate_client(_client_with({"pinterest": section})) == []

    # ── enabled flag semantics (D3) ────────────────────────────────────────

    @pytest.mark.parametrize("enabled_value", ["false", 1, None])
    def test_enabled_present_non_bool_is_error(self, enabled_value):
        section = dict(_CLEAN_ENABLED["meta"])
        section["enabled"] = enabled_value
        errors = client_config.validate_client(_client_with({"meta": section}))
        assert len(errors) == 1
        assert 'key "enabled" has type' in errors[0]
        assert "expected bool" in errors[0]

    def test_enabled_absent_means_disabled_no_validation(self):
        # Absent enabled → disabled: keys are never consumed → never validated.
        section = {"account_id": "1", "token_env": "META_TOKEN"}
        assert client_config.validate_client(_client_with({"meta": section})) == []

    def test_disabled_connector_with_broken_keys_is_ignored(self):
        connectors = {
            "meta": dict(_CLEAN_ENABLED["meta"]),
            "ga4": {"enabled": False},  # placeholders live in disabled sections
        }
        assert client_config.validate_client(_client_with(connectors)) == []

    # ── Unknown connectors / extra keys / dbt (Q2, C-R3) ───────────────────

    def test_unknown_enabled_connector_is_error(self):
        connectors = {
            "meta": dict(_CLEAN_ENABLED["meta"]),
            "custom_api": {"enabled": True, "whatever": "x"},
        }
        errors = client_config.validate_client(_client_with(connectors))
        assert len(errors) == 1
        assert 'connector "custom_api"' in errors[0]
        assert "unknown" in errors[0]

    def test_unknown_disabled_connector_is_tolerated(self):
        connectors = {
            "meta": dict(_CLEAN_ENABLED["meta"]),
            "custom_api": {"enabled": False, "whatever": "x"},
        }
        assert client_config.validate_client(_client_with(connectors)) == []

    def test_extra_keys_within_connector_and_dbt_subtree_ignored(self):
        connectors = {
            "meta": {
                "enabled": True,
                "account_id": "1",
                "token_env": "META_TOKEN",
                "future_key": {"nested": [1, 2]},  # Q2: never validated
            }
        }
        client = _client_with(connectors)
        client["dbt"] = {"tags": ["acme"], "future": {"x": True}}
        assert client_config.validate_client(client) == []

    # ── Structural errors: non-mapping section / root ──────────────────────

    @pytest.mark.parametrize(
        ("bad_section", "got_type"),
        [("hello", "str"), ([1, 2], "list")],
    )
    def test_section_not_mapping_is_error(self, bad_section, got_type):
        errors = client_config.validate_client(_client_with({"meta": bad_section}))
        assert len(errors) == 1
        assert 'connector "meta": section is not a mapping, got ' + got_type in errors[0]

    def test_connectors_root_not_mapping_active_is_error(self):
        client = _client_with([{"enabled": True}])
        assert client_config.validate_client(client) == [
            'client: "connectors" root is missing or not a mapping'
        ]

    def test_connectors_root_not_mapping_inactive_clean(self):
        client = _client_with([{"enabled": True}], active=False)
        assert client_config.validate_client(client) == []

    # ── Active-independent enabled validation (D3, non-vacuous C-S2) ───────

    def test_inactive_client_still_validates_enabled_connectors(self):
        # C-S2 is non-vacuous: acme/nike (active:false) are NOT short-circuited.
        connectors = {"meta": {"enabled": True, "token_env": "META_TOKEN"}}
        errors = client_config.validate_client(_client_with(connectors, active=False))
        assert len(errors) == 1
        assert 'missing required key "account_id"' in errors[0]

    # ── NFR-2 determinism ──────────────────────────────────────────────────

    def test_same_dict_returns_same_ordered_errors(self):
        client = _client_with(
            {
                "ga4": {"enabled": True, "property_id": "8"},
                "facebook": {
                    "enabled": True,
                    "page_id": "4",
                    "token_env": "FACEBOOK_TOKEN",
                    "insights_days_back": "x",
                },
            }
        )
        first = client_config.validate_client(client)
        second = client_config.validate_client(dict(client))
        assert first == second
        assert len(first) == 2  # non-trivial: ordering is observable

    # ── C-S2: live tracked YAMLs validate clean ────────────────────────────

    @pytest.mark.parametrize("file_stem", TRACKED_FILES)
    def test_cs2_tracked_clients_validate_clean(self, file_stem):
        client = client_config.load_client(file_stem)
        errors = client_config.validate_client(client)
        # acme enables meta+google, nike meta+tiktok: those enabled sections are
        # contract-clean; the drift lives in DISABLED sections → [] (C-S2).
        assert errors == []


# ─── missing_envs (SDD-C WU2: env-presence guard helper, pure/injectable) ──


class TestMissingEnvs:
    """missing_envs derives the env names referenced by an enabled connector
    (design D4) from CONNECTOR_CONTRACT env_keys/env_globals. environ-injectable:
    unit tests pass a synthetic mapping; ``environ=None`` reads os.environ.
    """

    def test_meta_single_token_env_all_set_is_clean(self):
        section = dict(_CLEAN_ENABLED["meta"])  # token_env -> "META_TOKEN"
        assert client_config.missing_envs("meta", section, environ={"META_TOKEN": "1"}) == []

    def test_meta_single_token_env_unset_is_missing(self):
        section = dict(_CLEAN_ENABLED["meta"])
        assert client_config.missing_envs("meta", section, environ={}) == ["META_TOKEN"]

    def test_meta_empty_string_env_value_is_missing(self):
        section = dict(_CLEAN_ENABLED["meta"])
        assert client_config.missing_envs("meta", section, environ={"META_TOKEN": ""}) == [
            "META_TOKEN"
        ]

    def test_tiktok_organic_reports_only_absent_of_three(self):
        section = dict(_CLEAN_ENABLED["tiktok_organic"])  # *_env -> TO_CLIENT_*
        envs = {"TO_CLIENT_KEY": "k", "TO_CLIENT_SECRET": "s"}
        assert client_config.missing_envs("tiktok_organic", section, environ=envs) == [
            "TO_REFRESH_TOKEN"
        ]
        envs["TO_REFRESH_TOKEN"] = "r"
        assert client_config.missing_envs("tiktok_organic", section, environ=envs) == []

    def test_google_reports_fixed_global_even_when_token_set(self):
        section = dict(_CLEAN_ENABLED["google"])  # token_env -> "GOOGLE_TOKEN"
        missing = client_config.missing_envs("google", section, environ={"GOOGLE_TOKEN": "t"})
        assert missing == ["GOOGLE_ADS_DEVELOPER_TOKEN"]

    def test_google_all_set_is_clean(self):
        section = dict(_CLEAN_ENABLED["google"])
        envs = {"GOOGLE_TOKEN": "t", "GOOGLE_ADS_DEVELOPER_TOKEN": "d"}
        assert client_config.missing_envs("google", section, environ=envs) == []

    def test_youtube_token_env_reported_by_helper(self):
        # The youtube leniency (exit 0) is a call-site decision of run_youtube;
        # the helper itself still reports youtube's env_keys token_env (D4).
        section = dict(_CLEAN_ENABLED["youtube"])
        assert client_config.missing_envs("youtube", section, environ={}) == ["YOUTUBE_TOKEN"]
        assert client_config.missing_envs("youtube", section, environ={"YOUTUBE_TOKEN": "k"}) == []

    def test_environ_none_reads_os_environ(self, monkeypatch):
        section = dict(_CLEAN_ENABLED["pinterest"])  # token_env -> "PINTEREST_TOKEN"
        assert client_config.missing_envs("pinterest", section) == ["PINTEREST_TOKEN"]
        monkeypatch.setenv("PINTEREST_TOKEN", "1")
        assert client_config.missing_envs("pinterest", section) == []
