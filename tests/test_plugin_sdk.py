"""SDK-1/2 专项：元数据快照、版本授权、统一入口、CSRF及公司锁回归。"""
import importlib.util
import json
import unittest
import urllib.request
from collections import OrderedDict
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

    def test_legacy_accepted_container_subclasses_are_deep_frozen(self):
        class CapabilityList(list):
            pass
        manifest = OrderedDict(ExamplePlugin.manifest)
        manifest["requires"] = OrderedDict()
        manifest["capabilities"] = CapabilityList(["sample:read"])
        Registry.validate(manifest)
        result = build_domain_registry([descriptor(manifest=manifest)])
        self.assertEqual(dict(result.errors), {})
        frozen = result.domains["example_domain"]["manifest"]
        with self.assertRaises(TypeError):
            frozen["default_enabled"] = True
        with self.assertRaises(TypeError):
            frozen["requires"]["ghost"] = ">=0.1"
        self.assertEqual(frozen["capabilities"], ("sample:read",))
        manifest["requires"]["later"] = ">=1"
        manifest["capabilities"].append("changed")
        self.assertEqual(dict(frozen["requires"]), {})
        self.assertEqual(frozen["capabilities"], ("sample:read",))

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


import types
import pluggy
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.utils import timezone

from core.models import (AuditEvent, DomainEvent, EventDelivery, Membership,
                         Organization, PluginState, Project)
from core.plugin_sdk.runtime import PluginEntryPoint
from core.plugin_views import plugin_entry
from core.services import audit


hookimpl = pluggy.HookimplMarker("project_hub")


class EntryPointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="SDK2 entry")
        cls.other_organization = Organization.objects.create(name="SDK2 other")
        cls.user = get_user_model().objects.create_user("sdk2-owner")
        cls.member = Membership.objects.create(
            organization=cls.organization, user=cls.user, role="owner"
        )
        today = timezone.localdate()
        cls.project = Project.objects.create(
            organization=cls.organization, manager=cls.member, code="SDK2-1",
            name="SDK2 project", client="client", start_date=today, end_date=today,
        )
        cls.other_project = Project.objects.create(
            organization=cls.other_organization, manager=cls.member, code="SDK2-2",
            name="Other project", client="client", start_date=today, end_date=today,
        )

    def setUp(self):
        self.read_handler = mock.Mock(return_value={"ok": "read"})
        self.write_handler = mock.Mock(return_value={"ok": "write"})
        self.history_handler = mock.Mock(return_value={"ok": "history"})
        self.reversal_handler = mock.Mock(return_value={"ok": "reversal"})
        metadata = changed(routes=[
            {"name": "overview", "path": "", "access": "business_read"},
            {"name": "command", "path": "commands/", "access": "business_write"},
            {"name": "history", "path": "history/", "access": "history_read"},
        ])
        self.manifest = dict(ExamplePlugin.manifest, default_enabled=True)
        installed = descriptor(metadata=metadata, manifest=self.manifest)
        domains = build_domain_registry([installed])
        self.assertTrue(domains.is_available("example_domain"))
        legacy = Registry(instances=[type("Plugin", (), {"manifest": self.manifest})()])
        patcher = mock.patch("core.plugin_runtime.get_registry", return_value=legacy)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.entry = PluginEntryPoint(
            domains,
            {"example_domain": {
                "overview": self.read_handler,
                "command": self.write_handler,
                "history": self.history_handler,
            }},
            {"example_domain": {"command": self.reversal_handler}},
        )

    def disable(self, version=None):
        return PluginState.objects.update_or_create(
            organization=self.organization,
            plugin_id="example_domain",
            defaults={"enabled": False, "version": version or self.manifest["version"],
                      "changed_by": self.user},
        )[0]

    def assert_no_new_business_facts(self, counts):
        current = (AuditEvent.objects.count(), DomainEvent.objects.count(),
                   EventDelivery.objects.count())
        self.assertEqual(current, counts)

    def fact_counts(self):
        return (AuditEvent.objects.count(), DomainEvent.objects.count(),
                EventDelivery.objects.count())

    def test_service_import_and_background_use_same_guard_and_call_handler(self):
        for channel in ("service", "import", "background"):
            with self.subTest(channel=channel):
                result = self.entry.dispatch(
                    self.member, "example_domain", "command",
                    payload={"value": channel}, project=self.project, channel=channel,
                )
                self.assertEqual(result, {"ok": "write"})
        self.assertEqual(self.write_handler.call_count, 3)
        contexts = [call.args[0] for call in self.write_handler.call_args_list]
        self.assertEqual([context.channel for context in contexts],
                         ["service", "import", "background"])
        for context in contexts:
            self.assertEqual(context.organization, self.organization)
            self.assertEqual(context.member.pk, self.member.pk)
            self.assertEqual(context.project, self.project)

    def test_real_http_static_routes_and_disabled_rejection(self):
        urlconf = types.ModuleType("sdk2_test_urls")
        urlconf.urlpatterns = self.entry.urlpatterns(plugin_entry)
        self.assertEqual([pattern.pattern._route for pattern in urlconf.urlpatterns], [
            "modules/example_domain/", "modules/example_domain/commands/",
            "modules/example_domain/history/",
        ])
        client = Client()
        client.force_login(self.user)
        with override_settings(ROOT_URLCONF=urlconf, PMO_PLUGIN_ENTRY_POINT=self.entry):
            self.assertEqual(client.get("/modules/example_domain/").json(), {"ok": "read"})
            response = client.post("/modules/example_domain/commands/", {"value": "1"})
            self.assertEqual(response.json(), {"ok": "write"})
            self.assertEqual(client.get("/modules/example_domain/commands/").status_code, 405)
            self.disable()
            before = self.write_handler.call_count
            counts = self.fact_counts()
            self.assertEqual(client.post("/modules/example_domain/commands/").status_code, 403)
            self.assertEqual(client.get("/modules/example_domain/").status_code, 403)
            self.assertEqual(self.write_handler.call_count, before)
            self.assert_no_new_business_facts(counts)
            response = client.get("/modules/example_domain/history/")
            self.assertEqual(response.json(), {"ok": "history"})
        self.assertTrue(self.read_handler.called)
        self.assertTrue(self.history_handler.called)

    def test_project_urlconf_mounts_only_explicit_trusted_bindings(self):
        from importlib import reload
        from django.urls import clear_url_caches, resolve, Resolver404
        import pmo.urls as project_urls

        client = Client()
        client.force_login(self.user)
        try:
            with override_settings(ROOT_URLCONF="pmo.urls", PMO_PLUGIN_ENTRY_POINT=self.entry):
                reload(project_urls)
                clear_url_caches()
                self.assertEqual(resolve("/modules/example_domain/").func, plugin_entry)
                self.assertEqual(client.get("/modules/example_domain/").json(), {"ok": "read"})
                self.disable()
                self.assertEqual(client.post("/modules/example_domain/commands/").status_code, 403)
            with override_settings(PMO_PLUGIN_ENTRY_POINT=None):
                reload(project_urls)
                clear_url_caches()
                with self.assertRaises(Resolver404):
                    resolve("/modules/example_domain/", urlconf=project_urls)
                self.assertEqual(resolve("/health/", urlconf=project_urls).url_name, "health")
        finally:
            reload(project_urls)
            clear_url_caches()

    def test_disabled_preserves_history_and_queue_without_new_writes(self):
        audit(self.member, "project.updated", self.project, {})
        delivery = EventDelivery.objects.create(
            event=DomainEvent.objects.get(), plugin_id="example_domain", status="pending"
        )
        self.disable()
        counts = self.fact_counts()
        with self.assertRaises(PermissionDenied):
            self.entry.dispatch(self.member, "example_domain", "command", payload={})
        self.assertFalse(self.write_handler.called)
        self.assert_no_new_business_facts(counts)
        self.assertEqual(
            self.entry.dispatch(self.member, "example_domain", "history", channel="background"),
            {"ok": "history"},
        )
        delivery.refresh_from_db()
        self.assertEqual((delivery.status, delivery.attempts), ("pending", 0))
        self.assert_no_new_business_facts(counts)

    def test_revoked_cross_company_project_and_stale_upgrade_are_closed(self):
        cases = []
        self.member.active = False
        self.member.save(update_fields=["active"])
        cases.append(lambda: self.entry.dispatch(self.member, "example_domain", "command"))
        with self.assertRaises(PermissionDenied):
            cases.pop()()
        self.assertFalse(self.write_handler.called)
        self.member.active = True
        self.member.save(update_fields=["active"])
        cases.extend([
            lambda: self.entry.dispatch(self.member, "example_domain", "command",
                                        project=self.other_project),
            lambda: self.entry.dispatch(self.member, "example_domain", "command",
                                        organization=self.other_organization),
        ])
        for operation in cases:
            with self.subTest(operation=operation):
                counts = self.fact_counts()
                with self.assertRaises(PermissionDenied):
                    operation()
                self.assertFalse(self.write_handler.called)
                self.assert_no_new_business_facts(counts)
        PluginState.objects.create(
            organization=self.organization, plugin_id="example_domain", enabled=True,
            version="0.0.1", changed_by=self.user,
        )
        with self.assertRaises(PermissionDenied):
            self.entry.dispatch(self.member, "example_domain", "command")
        self.assertFalse(self.write_handler.called)

    def test_read_role_project_and_history_authorization_still_apply(self):
        self.member.role = "auditor"
        self.member.save(update_fields=["role"])
        with self.assertRaises(PermissionDenied):
            self.entry.dispatch(self.member, "example_domain", "command")
        self.assertFalse(self.write_handler.called)
        with self.assertRaises(PermissionDenied):
            self.entry.dispatch(self.member, "example_domain", "history",
                                project=self.other_project)
        self.assertFalse(self.history_handler.called)

    def test_only_registered_reversal_survives_disable(self):
        self.disable()
        with self.assertRaises(PermissionDenied):
            self.entry.dispatch(self.member, "example_domain", "command",
                                payload={"is_reversal": True})
        self.assertFalse(self.write_handler.called)
        result = self.entry.reverse(self.member, "example_domain", "command",
                                    payload={"source": "registered"})
        self.assertEqual(result, {"ok": "reversal"})
        self.reversal_handler.assert_called_once()
        with self.assertRaises(PermissionDenied):
            self.entry.reverse(self.member, "example_domain", "overview")

    def test_legacy_direct_hook_requires_company_and_enabled_state(self):
        received = mock.Mock(return_value={"message": "ok"})
        class LegacyPlugin:
            manifest = self.manifest
            @hookimpl
            def event_received(self, event):
                return received(event)
        legacy = Registry(instances=[LegacyPlugin()])
        with mock.patch("core.plugin_runtime.get_registry", return_value=legacy):
            with self.assertRaises(TypeError):
                legacy.call_one("example_domain", "event_received", event={})
            self.assertFalse(received.called)
            result = legacy.call_one("example_domain", "event_received",
                                     organization=self.organization, event={})
            self.assertEqual(result, [{"message": "ok"}])
            self.disable()
            with self.assertRaises(PermissionDenied):
                legacy.call_one("example_domain", "event_received",
                                organization=self.organization, event={})
            self.assertEqual(received.call_count, 1)

    def test_unknown_static_handler_binding_is_rejected(self):
        with self.assertRaises(ValueError):
            PluginEntryPoint(self.entry.domain_registry,
                             {"example_domain": {"missing": self.read_handler}})
        with self.assertRaises(PermissionDenied):
            PluginEntryPoint(self.entry.domain_registry, {}).dispatch(
                self.member, "example_domain", "overview"
            )
        self.assertFalse(self.read_handler.called)

    def test_snapshot_upgrade_cannot_borrow_old_global_approval(self):
        PluginState.objects.create(organization=self.organization, plugin_id="example_domain",
            enabled=True, version=self.manifest["version"], changed_by=self.user)
        newer = dict(self.manifest, version="0.2.0")
        snapshot = build_domain_registry([descriptor(
            metadata=deepcopy(dict(DOMAIN_METADATA, routes=[
                {"name": "command", "path": "commands/", "access": "business_write"}])),
            manifest=newer)])
        entry = PluginEntryPoint(snapshot, {"example_domain": {"command": self.write_handler}})
        counts = self.fact_counts()
        with self.assertRaises(PermissionDenied):
            entry.dispatch(self.member, "example_domain", "command")
        self.write_handler.assert_not_called()
        self.assert_no_new_business_facts(counts)
        # With both registries upgraded and no persisted approval, retain defaults.
        PluginState.objects.all().delete()
        live = Registry(instances=[type("Plugin", (), {"manifest": newer})()])
        with mock.patch("core.plugin_runtime.get_registry", return_value=live):
            self.assertEqual(entry.dispatch(self.member, "example_domain", "command"),
                             {"ok": "write"})
        self.assertFalse(PluginState.objects.exists())

    def test_dependency_snapshot_mismatch_blocks_only_affected_domain(self):
        consumer = descriptor("consumer", manifest=dict(self.manifest, id="consumer",
            requires={"example_domain": ">=0.1"}))
        dependency = descriptor(manifest=dict(self.manifest, version="0.2.0"))
        unrelated = descriptor("unrelated", manifest=dict(self.manifest, id="unrelated"))
        domains = build_domain_registry([consumer, dependency, unrelated])
        live = Registry(instances=[type("Plugin", (), {"manifest": m})() for m in
            (consumer["manifest"], self.manifest, unrelated["manifest"])])
        affected, safe = mock.Mock(), mock.Mock(return_value={"ok": True})
        entry = PluginEntryPoint(domains, {"consumer": {"overview": affected},
                                          "unrelated": {"overview": safe}})
        with mock.patch("core.plugin_runtime.get_registry", return_value=live):
            with self.assertRaises(PermissionDenied):
                entry.dispatch(self.member, "consumer", "overview")
            self.assertEqual(entry.dispatch(self.member, "unrelated", "overview"), {"ok": True})
        affected.assert_not_called()
        safe.assert_called_once()

    def test_disabled_channels_and_company_lock_recheck(self):
        self.disable()
        counts = self.fact_counts()
        for channel in ("service", "import", "background", "legacy"):
            with self.subTest(channel=channel), self.assertRaises(PermissionDenied):
                self.entry.dispatch(self.member, "example_domain", "command", channel=channel)
        self.write_handler.assert_not_called()
        self.assert_no_new_business_facts(counts)
        PluginState.objects.all().delete()
        from core.services import lock_company
        def disable_while_locking(member):
            result = lock_company(member)
            self.disable()
            return result
        with mock.patch("core.plugin_sdk.runtime.lock_company", side_effect=disable_while_locking):
            with self.assertRaises(PermissionDenied):
                self.entry.dispatch(self.member, "example_domain", "command")
        self.write_handler.assert_not_called()
        self.assertFalse(PluginState.objects.exists())  # transaction rolled back

    def test_snapshot_version_rechecked_after_company_lock(self):
        from core.services import lock_company
        initial = self.entry.domain_registry
        upgraded = build_domain_registry([descriptor(manifest=dict(self.manifest, version="0.2.0"),
            metadata=changed(routes=[{"name": "command", "path": "commands/",
                                      "access": "business_write"}]))])
        def swap_snapshot(member):
            result = lock_company(member)
            self.entry.domain_registry = upgraded
            return result
        try:
            with mock.patch("core.plugin_sdk.runtime.lock_company", side_effect=swap_snapshot):
                with self.assertRaises(PermissionDenied):
                    self.entry.dispatch(self.member, "example_domain", "command")
        finally:
            self.entry.domain_registry = initial
        self.write_handler.assert_not_called()

    def test_http_write_missing_csrf_is_rejected_before_handler(self):
        urlconf = types.ModuleType("sdk2_csrf_urls")
        urlconf.urlpatterns = self.entry.urlpatterns(plugin_entry)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        counts = self.fact_counts()
        with override_settings(ROOT_URLCONF=urlconf, PMO_PLUGIN_ENTRY_POINT=self.entry):
            response = client.post("/modules/example_domain/commands/", {"value": "1"})
        self.assertEqual(response.status_code, 403)
        self.write_handler.assert_not_called()
        self.assert_no_new_business_facts(counts)

    def test_handler_failure_rolls_back_audit_and_event(self):
        def failing(context, payload):
            audit(context.member, "project.updated", self.project, {})
            raise ValueError("rehearsal failure")
        entry = PluginEntryPoint(self.entry.domain_registry,
                                 {"example_domain": {"command": failing}})
        counts = self.fact_counts()
        with self.assertRaisesRegex(ValueError, "rehearsal failure"):
            entry.dispatch(self.member, "example_domain", "command")
        self.assert_no_new_business_facts(counts)

from django.test import TransactionTestCase


@unittest.skipUnless(connection.vendor == "postgresql", "Real company-lock concurrency requires PostgreSQL")
class EntryPointPostgresLockTests(TransactionTestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="SDK2 PG rehearsal")
        self.user = get_user_model().objects.create_user("sdk2-pg-owner")
        self.member = Membership.objects.create(organization=self.organization,
                                                user=self.user, role="owner")
        self.manifest = dict(ExamplePlugin.manifest, default_enabled=True)
        self.registry = Registry(instances=[type("Plugin", (), {"manifest": self.manifest})()])
        metadata = changed(routes=[{"name": "command", "path": "commands/",
                                    "access": "business_write"}])
        self.domains = build_domain_registry([descriptor(metadata=metadata, manifest=self.manifest)])

    def test_write_and_disable_share_company_lock_and_failure_rolls_back(self):
        import threading
        import time
        from django.db import close_old_connections
        from core.plugin_runtime import set_enabled
        entered, release, contender_ready = (threading.Event() for _ in range(3))
        errors, pids = [], {}

        def handler(context, payload):
            audit(context.member, "sdk.rehearsal", self.organization, {})
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pids["writer"] = cursor.fetchone()[0]
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test release timeout")
            raise ValueError("rollback rehearsal")

        entry = PluginEntryPoint(self.domains, {"example_domain": {"command": handler}})

        def write():
            close_old_connections()
            try:
                entry.dispatch(Membership.objects.get(pk=self.member.pk), "example_domain", "command")
            except ValueError as error:
                if str(error) != "rollback rehearsal":
                    errors.append(error)
            except BaseException as error:
                errors.append(error)
            finally:
                connection.close()

        def disable():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids["disabler"] = cursor.fetchone()[0]
                contender_ready.set()
                set_enabled(Membership.objects.select_related("organization", "user").get(pk=self.member.pk),
                            "example_domain", False)
            except BaseException as error:
                errors.append(error)
            finally:
                connection.close()

        with mock.patch("core.plugin_runtime.get_registry", return_value=self.registry):
            writer = threading.Thread(target=write)
            disabler = threading.Thread(target=disable)
            writer.start()
            try:
                self.assertTrue(entered.wait(5), errors)
                disabler.start()
                self.assertTrue(contender_ready.wait(5), errors)
                blockers = []
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_blocking_pids(%s)", [pids["disabler"]])
                        blockers = cursor.fetchone()[0]
                    if pids["writer"] in blockers:
                        break
                    time.sleep(0.01)
                self.assertIn(pids["writer"], blockers)
            finally:
                release.set()
                writer.join(10)
                if disabler.ident is not None:
                    disabler.join(10)
            self.assertFalse(writer.is_alive())
            self.assertFalse(disabler.is_alive())
            self.assertEqual(errors, [])
            self.assertFalse(AuditEvent.objects.filter(action="sdk.rehearsal").exists())
            self.assertFalse(DomainEvent.objects.exists())
            state = PluginState.objects.get(organization=self.organization, plugin_id="example_domain")
            self.assertFalse(state.enabled)
            # A later invocation sees the completed disable and cannot run a handler.
            normal = mock.Mock()
            entry = PluginEntryPoint(self.domains, {"example_domain": {"command": normal}})
            with self.assertRaises(PermissionDenied):
                entry.dispatch(self.member, "example_domain", "command")
            normal.assert_not_called()
