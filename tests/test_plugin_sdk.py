"""SDK-1 专项测试（PH-001）：元数据结构/语义校验、注册快照与旧manifest回归。"""
import importlib.util
import json
import unittest
import urllib.request
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from unittest import mock

from django.db import connection
from django.apps import apps
from django.conf import settings
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from core.plugin_runtime import Registry
from core.plugin_sdk import (
    MetadataError,
    build_domain_registry,
    validate_domain_metadata,
)
from pmo_plugins.example_domain.domain_metadata import DOMAIN_METADATA
from pmo_plugins.example_domain.plugin import Plugin as ExamplePlugin

BASE_DIR = Path(__file__).resolve().parent.parent
LEAF_PLUGINS = ("project_health", "delivery_watch", "agent_gateway")
MANIFEST_KEYS = {"id", "name", "version", "kernel_api", "requires",
                 "default_enabled", "capabilities", "description"}
INSTALLED = {"example_domain": "pmo_plugins.example_domain", "core": "core"}
CATALOG = {"contract_catalog": [{
    "id": "C01",
    "owner": "core authorization",
    "commands": ["identity.membership.change/v1"],
    "queries": ["identity.context.get/v1"],
    "events": ["identity.membership_changed.v1"],
}]}


def rejected_codes(case, metadata, manifest=None, **context):
    context.setdefault("catalog", CATALOG)
    context.setdefault("installed_apps", INSTALLED)
    with case.assertRaises(MetadataError) as ctx:
        validate_domain_metadata(metadata, manifest or ExamplePlugin.manifest, **context)
    return ctx.exception.codes


def labelled(plugin_id, label=None):
    data = deepcopy(DOMAIN_METADATA)
    data["plugin_id"] = plugin_id
    data["django_app"] = {"module": f"pmo_plugins.{plugin_id}", "label": label or plugin_id}
    return data


def changed(**updates):
    data = deepcopy(DOMAIN_METADATA)
    data.update(updates)
    return data


def descriptor(plugin_id="example_domain", metadata=None, manifest=None, **extra):
    if metadata is None:
        metadata = deepcopy(DOMAIN_METADATA)
        metadata["plugin_id"] = plugin_id
        metadata["django_app"] = {"module": f"pmo_plugins.{plugin_id}", "label": plugin_id}
    if manifest is None:
        manifest = dict(ExamplePlugin.manifest, id=plugin_id)
    data = {"plugin_id": plugin_id, "metadata": metadata, "manifest": manifest,
            "catalog": CATALOG, "installed_apps": dict(INSTALLED, **{metadata["django_app"]["label"]: metadata["django_app"]["module"]})}
    data.update(extra)
    return data


class MetadataValidationTests(TestCase):
    def rejected_codes(self, metadata, manifest=None, **context):
        return rejected_codes(self, metadata, manifest, **context)

    def test_canonical_example_matches_frozen_json_and_validates(self):
        frozen = json.loads(
            (BASE_DIR / "docs/v1.0-development/promotion-draft/sdk1-metadata.example.json").read_text()
        )
        self.assertEqual(frozen, DOMAIN_METADATA)
        validated = validate_domain_metadata(DOMAIN_METADATA, ExamplePlugin.manifest, CATALOG, INSTALLED)
        self.assertEqual(validated, DOMAIN_METADATA)
        self.assertIsNot(validated, DOMAIN_METADATA)
        spec = Registry.validate(ExamplePlugin.manifest)
        self.assertEqual(set(spec), MANIFEST_KEYS)

    def test_unknown_missing_and_wrong_typed_fields_rejected(self):
        data = deepcopy(DOMAIN_METADATA)
        data["capabilities"] = []
        self.assertIn("metadata.fields", self.rejected_codes(data))
        data = deepcopy(DOMAIN_METADATA)
        del data["contracts"]["queries"]
        self.assertIn("metadata.contracts.fields", self.rejected_codes(data))
        for wrong in (1, True, 1.0, None):
            self.assertIn("metadata.schema_version", self.rejected_codes(changed(schema_version=wrong)))
        self.assertIn("metadata.plugin_id", self.rejected_codes(changed(plugin_id="Example")))
        self.assertIn("metadata.django_app.label",
                      self.rejected_codes(changed(django_app={"module": "pmo_plugins.example_domain", "label": True})))
        bad_manifest = dict(ExamplePlugin.manifest, default_enabled=1)
        self.assertIn("manifest.invalid", self.rejected_codes(deepcopy(DOMAIN_METADATA), bad_manifest))

    def test_contract_group_rules_rejected(self):
        data = deepcopy(DOMAIN_METADATA)
        data["contracts"]["commands"] = ["a/v1", "a/v1"]
        self.assertIn("metadata.contracts.commands.unique", self.rejected_codes(data))
        data["contracts"]["commands"] = [f"cmd{i}" for i in range(129)]
        self.assertIn("metadata.contracts.commands.unique", self.rejected_codes(data))
        data["contracts"]["commands"] = ["c" * 201]
        self.assertIn("metadata.contracts.commands.items", self.rejected_codes(data))
        data["contracts"]["commands"] = [42]
        self.assertIn("metadata.contracts.commands.items", self.rejected_codes(data))
        data["contracts"]["events"] = [""]
        self.assertIn("metadata.contracts.events.items", self.rejected_codes(data))

    def test_route_static_rules_rejected(self):
        def route(**values):
            data = deepcopy(DOMAIN_METADATA)
            data["routes"] = [{"name": "overview", "path": "", "access": "business_read"}]
            data["routes"][0].update(values)
            return data

        self.assertIn("metadata.routes.path", self.rejected_codes(route(path="pages/<int:pk>/")))
        self.assertIn("metadata.routes.path", self.rejected_codes(route(path="Pages/")))
        self.assertIn("metadata.routes.access", self.rejected_codes(route(access="admin")))
        self.assertIn("metadata.routes.access", self.rejected_codes(route(access=True)))
        self.assertIn("metadata.routes.name", self.rejected_codes(route(name="Overview")))
        broad = deepcopy(DOMAIN_METADATA)
        broad["routes"][0]["extra"] = 1
        self.assertIn("metadata.routes.fields", self.rejected_codes(broad))
        many = deepcopy(DOMAIN_METADATA)
        many["routes"] = [{"name": f"r{i}", "path": "", "access": "business_read"} for i in range(65)]
        self.assertIn("metadata.routes.limit", self.rejected_codes(many))
        static = deepcopy(DOMAIN_METADATA)
        static["routes"] = [{"name": "overview", "path": "", "access": "business_read"},
                            {"name": "reports", "path": "reports/summary/", "access": "history_read"}]
        validate_domain_metadata(static, ExamplePlugin.manifest, CATALOG, INSTALLED)

    def test_context_semantics_rejected_or_accepted(self):
        context = {"catalog": CATALOG, "installed_apps": INSTALLED}
        self.assertIn("metadata.plugin_id.mismatch",
                      self.rejected_codes(changed(plugin_id="other_domain"), **context))
        data = changed(django_app={"module": "pmo_plugins.other", "label": "example_domain"})
        self.assertIn("metadata.django_app.module.mismatch", self.rejected_codes(data, **context))
        self.assertIn("metadata.django_app.label.not_installed",
                      self.rejected_codes(deepcopy(DOMAIN_METADATA), installed_apps={"core": "core"}))
        wrong_category = deepcopy(DOMAIN_METADATA)
        wrong_category["contracts"]["commands"] = ["identity.context.get/v1"]
        self.assertIn("metadata.contracts.commands.unknown", self.rejected_codes(wrong_category, **context))
        unknown = deepcopy(DOMAIN_METADATA)
        unknown["contracts"]["queries"] = ["identity.unknown.get/v1"]
        self.assertIn("metadata.contracts.queries.unknown", self.rejected_codes(unknown, **context))
        right_category = deepcopy(DOMAIN_METADATA)
        right_category["contracts"]["commands"] = ["identity.membership.change/v1"]
        right_category["contracts"]["events"] = ["identity.membership_changed.v1"]
        validate_domain_metadata(right_category, ExamplePlugin.manifest, **context)

        class AppStub:
            label = "example_domain"
            name = "pmo_plugins.example_domain"

        validate_domain_metadata(DOMAIN_METADATA, ExamplePlugin.manifest, CATALOG, [AppStub()])
        with self.assertRaises(MetadataError):
            validate_domain_metadata(unknown, ExamplePlugin.manifest)

    def test_migration_dependency_semantics(self):
        context = {"catalog": CATALOG, "installed_apps": INSTALLED}
        self.assertIn("metadata.migration_dependencies.self",
                      self.rejected_codes(changed(migration_dependencies=["example_domain"]), **context))
        self.assertIn("metadata.migration_dependencies.not_installed",
                      self.rejected_codes(changed(migration_dependencies=["nope"]), **context))
        installed = dict(INSTALLED, contracts="pmo_plugins.contracts")
        self.assertIn("metadata.migration_dependencies.undeclared",
                      self.rejected_codes(changed(migration_dependencies=["contracts"]),
                                     catalog=CATALOG, installed_apps=installed))
        declared = dict(ExamplePlugin.manifest, requires={"contracts": ">=0.1"})
        validate_domain_metadata(changed(migration_dependencies=["contracts"]), declared,
                                 catalog=CATALOG, installed_apps=installed)
        validate_domain_metadata(changed(migration_dependencies=["core"]), ExamplePlugin.manifest, **context)

    def test_actual_app_config_loading(self):
        with override_settings(INSTALLED_APPS=[*settings.INSTALLED_APPS,
                "pmo_plugins.example_domain.apps.ExampleDomainConfig"]):
            config = apps.get_app_config("example_domain")
            self.assertEqual(config.name, "pmo_plugins.example_domain")
            validated = validate_domain_metadata(DOMAIN_METADATA, ExamplePlugin.manifest,
                                                 CATALOG, apps.get_app_configs())
            self.assertEqual(validated, DOMAIN_METADATA)
            wrong = changed(django_app={"module": "pmo_plugins.wrong", "label": "example_domain"})
            self.assertIn("metadata.django_app.module.mismatch",
                          self.rejected_codes(wrong, installed_apps=apps.get_app_configs()))

    @unittest.skipUnless(importlib.util.find_spec("jsonschema") is not None,
                         "Supplemental schema parity uses the existing pinned MCP verification environment")
    def test_schema_python_structural_agreement(self):
        from jsonschema import Draft202012Validator
        schema = json.loads((BASE_DIR / "docs/v1.0-development/promotion-draft/sdk1-metadata.schema.json").read_text())
        validator = Draft202012Validator(schema)
        cases = [deepcopy(DOMAIN_METADATA), changed(schema_version=1), changed(extra=True),
                 changed(routes=[{"name": "read", "path": "<int:pk>/", "access": "business_read"}]),
                 changed(migration_dependencies=["core", "core"]), changed(plugin_id="X"),
                 changed(contracts={"commands": [], "queries": [], "events": []})]
        for data in cases:
            with self.subTest(data=data):
                schema_accepts = validator.is_valid(data)
                try:
                    validate_domain_metadata(data, ExamplePlugin.manifest, CATALOG, INSTALLED)
                    python_accepts = True
                except MetadataError:
                    python_accepts = False
                self.assertEqual(schema_accepts, python_accepts)

    def test_validation_is_pure_no_db_import_or_network(self):
        with CaptureQueriesContext(connection) as captured:
            validate_domain_metadata(DOMAIN_METADATA, ExamplePlugin.manifest, CATALOG, INSTALLED)
            build_domain_registry([descriptor(catalog=CATALOG, installed_apps=INSTALLED)])
        self.assertEqual(captured.captured_queries, [])
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError), \
                mock.patch("importlib.import_module", side_effect=AssertionError) as importer:
            validate_domain_metadata(DOMAIN_METADATA, ExamplePlugin.manifest, CATALOG, INSTALLED)
        importer.assert_not_called()


class RegistrySnapshotTests(TestCase):
    def test_snapshot_is_deep_immutable_and_resists_caller_mutation(self):
        data = deepcopy(DOMAIN_METADATA)
        registry = build_domain_registry([descriptor(metadata=data, catalog=CATALOG, installed_apps=INSTALLED)])
        snapshot = registry.domains["example_domain"]["metadata"]
        with self.assertRaises(TypeError):
            snapshot["plugin_id"] = "mutated"
        with self.assertRaises(TypeError):
            registry.domains["example_domain"]["metadata"]["routes"][0]["name"] = "mutated"
        before = snapshot["routes"]
        original = deepcopy(data)
        data["routes"].append({"name": "hack", "path": "hack/", "access": "business_write"})
        self.assertEqual(registry.domains["example_domain"]["metadata"]["routes"], before)
        rebuilt = build_domain_registry([descriptor(metadata=original, catalog=CATALOG, installed_apps=INSTALLED)])
        self.assertEqual(rebuilt.domains, registry.domains)
        self.assertEqual(rebuilt.errors, registry.errors)
        validated = validate_domain_metadata(DOMAIN_METADATA, ExamplePlugin.manifest, CATALOG, INSTALLED)
        validated["routes"].append({"name": "x", "path": "x/", "access": "history_read"})
        self.assertEqual(len(DOMAIN_METADATA["routes"]), 1)

    def test_duplicate_namespace_and_label_reject_both_sides_keep_unrelated(self):
        registry = build_domain_registry([descriptor(), descriptor(),
                                          descriptor("other_domain")])
        self.assertFalse(registry.is_available("example_domain"))
        self.assertIn("duplicate_app_label", registry.errors["example_domain"])
        self.assertTrue(registry.is_available("other_domain"))
        alpha = changed(plugin_id="alpha", django_app={"module": "pmo_plugins.alpha", "label": "example_domain"})
        beta = dict(ExamplePlugin.manifest, id="beta")
        registry = build_domain_registry([
            descriptor("alpha", metadata=alpha),
            descriptor("beta", metadata=changed(plugin_id="beta", django_app=alpha["django_app"]), manifest=beta),
        ])
        self.assertIn("duplicate_app_label", registry.errors["alpha"])
        self.assertIn("duplicate_app_label", registry.errors["beta"])
        self.assertEqual(registry.domains, {})

    def test_collision_is_order_independent_even_with_invalid_peer(self):
        good = descriptor()
        bad = descriptor()
        bad["manifest"]["kernel_api"] = ">=99"
        first = build_domain_registry([good, bad, descriptor("other_domain")])
        second = build_domain_registry([bad, good, descriptor("other_domain")])
        self.assertEqual(first.domains, second.domains)
        self.assertEqual(first.errors, second.errors)
        self.assertFalse(first.is_available("example_domain"))
        self.assertIn("duplicate_plugin_id", first.errors["example_domain"])
        self.assertTrue(first.is_available("other_domain"))

    def test_route_conflicts_reject_whole_domain(self):
        clash = deepcopy(DOMAIN_METADATA)
        clash["routes"] = [{"name": "overview", "path": "", "access": "business_read"},
                           {"name": "same_path", "path": "", "access": "history_read"}]
        registry = build_domain_registry([descriptor(metadata=clash)])
        self.assertFalse(registry.is_available("example_domain"))
        self.assertIn("duplicate_route", registry.errors["example_domain"])
        same_name = deepcopy(DOMAIN_METADATA)
        same_name["routes"] = [{"name": "overview", "path": "a/", "access": "business_read"},
                               {"name": "overview", "path": "b/", "access": "business_read"}]
        registry = build_domain_registry([descriptor(metadata=same_name)])
        self.assertIn("duplicate_route", registry.errors["example_domain"])
        distinct = deepcopy(DOMAIN_METADATA)
        distinct["routes"] = [{"name": "overview", "path": "", "access": "business_read"},
                              {"name": "reports", "path": "reports/", "access": "history_read"}]
        registry = build_domain_registry([descriptor(metadata=distinct)])
        self.assertTrue(registry.is_available("example_domain"))
        self.assertEqual(registry.errors, {})

    def test_dependency_missing_cycle_and_impact_chain(self):
        def dependent(key, target):
            item = descriptor(key)
            item["metadata"]["migration_dependencies"] = [target]
            item["manifest"]["requires"] = {target: ">=0.1"}
            item["installed_apps"][target] = f"pmo_plugins.{target}"
            return item
        bad = descriptor("bb")
        del bad["metadata"]["contracts"]["queries"]
        registry = build_domain_registry([dependent("aa", "bb"), bad, descriptor("cc")])
        self.assertIn("manifest.dependency_graph", registry.errors["aa"])
        self.assertIn("metadata.contracts.fields", registry.errors["bb"])
        self.assertEqual(set(registry.domains), {"cc"})
        registry = build_domain_registry([dependent("aa", "bb"), dependent("bb", "aa"),
                                          dependent("dd", "aa"), descriptor("cc")])
        self.assertIn("migration_dependency_cycle", registry.errors["aa"])
        self.assertIn("migration_dependency_cycle", registry.errors["bb"])
        self.assertIn("migration_dependency_missing", registry.errors["dd"])
        self.assertEqual(set(registry.domains), {"cc"})

    def test_distinct_app_label_dependency_uses_controlled_identity(self):
        dependency = descriptor("billing_plugin", metadata=labelled("billing_plugin", "billing_app"))
        consumer = descriptor("consumer_plugin")
        consumer["metadata"]["migration_dependencies"] = ["billing_app"]
        consumer["manifest"]["requires"] = {"billing_plugin": ">=0.1"}
        deployment = {"billing_app": {"module": "pmo_plugins.billing_plugin", "plugin_id": "billing_plugin"},
                      "consumer_plugin": "pmo_plugins.consumer_plugin"}
        validate_domain_metadata(consumer["metadata"], consumer["manifest"], CATALOG, deployment)
        for item in (consumer, dependency):
            item["installed_apps"] = deployment
        registry = build_domain_registry([consumer, dependency])
        self.assertEqual(set(registry.domains), {"consumer_plugin", "billing_plugin"})
        # Legacy string deployment index may gain identity only for an installed matching peer.
        for item in (consumer, dependency):
            item["installed_apps"] = {"billing_app": "pmo_plugins.billing_plugin",
                                      "consumer_plugin": "pmo_plugins.consumer_plugin"}
        self.assertEqual(set(build_domain_registry([consumer, dependency]).domains),
                         {"consumer_plugin", "billing_plugin"})
        consumer["manifest"]["requires"] = {}
        self.assertIn("metadata.migration_dependencies.undeclared",
                      build_domain_registry([consumer, dependency]).errors["consumer_plugin"])
        consumer["manifest"]["requires"] = {"billing_plugin": ">=0.1"}
        dependency["installed_apps"] = {"billing_app": "pmo_plugins.other"}
        consumer["installed_apps"]["billing_app"] = "pmo_plugins.other"
        registry = build_domain_registry([consumer, dependency])
        self.assertIn("metadata.django_app.module.mismatch", registry.errors["billing_plugin"])
        self.assertFalse(registry.is_available("consumer_plugin"))

    def test_duplicate_app_module_rejects_both_different_labels(self):
        first = descriptor("alpha", metadata=labelled("alpha", "alpha_app"))
        second = descriptor("beta", metadata=labelled("beta", "beta_app"))
        second["metadata"]["django_app"]["module"] = "pmo_plugins.alpha"
        second["installed_apps"]["beta_app"] = "pmo_plugins.alpha"
        registry = build_domain_registry([first, second, descriptor("other_domain")])
        for key in ("alpha", "beta"):
            self.assertIn("duplicate_app_module", registry.errors[key])
        self.assertEqual(set(registry.domains), {"other_domain"})

    def test_manifest_graph_missing_version_cycle_and_bad_chain(self):
        def requires(key, target, constraint=">=0.1"):
            item = descriptor(key)
            item["manifest"]["requires"] = {target: constraint}
            return item
        for items in ([requires("aa", "bb")],
                      [requires("aa", "bb", ">=9"), descriptor("bb")],
                      [requires("aa", "bb"), requires("bb", "aa")],
                      [requires("aa", "bb"), requires("bb", "absent")]):
            registry = build_domain_registry(items + [descriptor("cc")])
            self.assertIn("manifest.dependency_graph", registry.errors["aa"])
            self.assertTrue(registry.is_available("cc"))

    def test_missing_context_rejected_and_snapshot_attributes_frozen(self):
        for key in ("catalog", "installed_apps"):
            item = descriptor()
            del item[key]
            self.assertIn("metadata.context.required", build_domain_registry([item]).errors["example_domain"])
        plugin = ExamplePlugin()
        registry = build_domain_registry([descriptor(plugin=plugin)])
        self.assertNotIn("plugin", registry.domains["example_domain"])
        for key in ("domains", "errors", "_domains", "_errors"):
            with self.assertRaises(AttributeError):
                setattr(registry, key, {})



class LegacyManifestTests(TestCase):
    def test_old_registry_rejects_extended_or_incomplete_manifest(self):
        with self.assertRaises(ValueError):
            Registry.validate(dict(ExamplePlugin.manifest, domain_metadata=DOMAIN_METADATA))
        incomplete = {key: value for key, value in ExamplePlugin.manifest.items() if key != "capabilities"}
        with self.assertRaises(ValueError):
            Registry.validate(incomplete)

    def test_example_plugin_loads_in_old_registry_and_defaults_disabled(self):
        registry = Registry(instances=[ExamplePlugin()])
        self.assertEqual(set(registry.plugins), {"example_domain"})
        self.assertEqual(registry.errors, {})
        self.assertFalse(registry.manifests["example_domain"]["default_enabled"])

    def test_example_upgrade_keeps_schema_version_and_leaf_sources_unchanged(self):
        leaf_files = [BASE_DIR / "pmo_plugins" / name / "plugin.py" for name in LEAF_PLUGINS]
        before = {str(path): sha256(path.read_bytes()).hexdigest() for path in leaf_files}
        upgraded = dict(ExamplePlugin.manifest, version="0.2.0")
        registry = Registry(instances=[type("UpgradedPlugin", (), {"manifest": upgraded})()])
        self.assertEqual(registry.manifests["example_domain"]["version"], "0.2.0")
        self.assertEqual(DOMAIN_METADATA["schema_version"], "1")
        after = {str(path): sha256(path.read_bytes()).hexdigest() for path in leaf_files}
        self.assertEqual(before, after)
        for name in LEAF_PLUGINS:
            module = __import__(f"pmo_plugins.{name}.plugin", fromlist=["Plugin"])
            self.assertEqual(set(module.Plugin.manifest), MANIFEST_KEYS)


    def test_upgrade_state_default_rules_without_automatic_writes(self):
        from core.models import Organization, PluginState
        from core.plugin_runtime import is_enabled
        from django.contrib.auth import get_user_model
        organization = Organization.objects.create(name="SDK upgrade rehearsal")
        user = get_user_model().objects.create_user(username="sdk_upgrade")
        upgraded = dict(ExamplePlugin.manifest, version="0.2.0")
        for default in (False, True):
            upgraded["default_enabled"] = default
            registry = Registry(instances=[type("Plugin", (), {"manifest": deepcopy(upgraded)})()])
            with mock.patch("core.plugin_runtime.get_registry", return_value=registry):
                before = PluginState.objects.count()
                self.assertEqual(is_enabled(organization, "example_domain"), default)
                self.assertEqual(PluginState.objects.count(), before)
                state = PluginState.objects.create(organization=organization, plugin_id="example_domain",
                    enabled=True, version="0.1.0", changed_by=user)
                self.assertFalse(is_enabled(organization, "example_domain"))
                state.refresh_from_db()
                self.assertEqual(state.version, "0.1.0")
                self.assertTrue(state.enabled)
                self.assertEqual(PluginState.objects.count(), before + 1)
                state.delete()
