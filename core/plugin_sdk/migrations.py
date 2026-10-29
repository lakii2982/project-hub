"""Read-only deployment checks. Publish returned candidates only after success.

Use Django's effective graph (including run_before and replacement rewiring),
never interpret metadata as import instructions or apply migrations here.
"""
from django.apps import apps
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.exceptions import CircularDependencyError, NodeNotFoundError

from .registry import build_domain_registry
from .runtime import PluginEntryPoint


_CODES = frozenset({
    "candidate.invalid", "deployment.app_mismatch", "migration.graph_invalid",
    "migration.missing_node", "migration.cycle", "migration.undeclared_dependency",
    "migration.reverse_dependency", "migration.models_without_migrations",
    "binding.invalid",
})


class MigrationContractError(ValueError):
    """Only fixed, sorted, deduplicated codes cross the deployment boundary."""

    def __init__(self, codes):
        self.codes = tuple(sorted({c if type(c) is str and c in _CODES
                                   else "migration.graph_invalid" for c in codes}))
        super().__init__("; ".join(self.codes))


def _checked(operation, fallback):
    # Raise outside the exception handler: even __context__ must not retain
    # loader exceptions containing filesystem paths, settings or SQL.
    codes = None
    try:
        return operation()
    except MigrationContractError as error:
        codes = error.codes
    except NodeNotFoundError:
        codes = ("migration.missing_node",)
    except CircularDependencyError:
        codes = ("migration.cycle",)
    except Exception:
        codes = (fallback,)
    raise MigrationContractError(codes)


def validate_migration_dependencies(domain_registry, loader):
    """Validate a complete candidate against installed Apps and a loaded graph."""
    return _checked(lambda: _validate(domain_registry, loader), "migration.graph_invalid")


def _validate(registry, loader):
    if registry.errors:
        raise MigrationContractError(["candidate.invalid"])
    if not isinstance(loader, MigrationLoader):
        raise MigrationContractError(["migration.graph_invalid"])
    graph = loader.graph
    graph.validate_consistency()
    graph.ensure_not_cyclic()
    configs = {app.label: app for app in apps.get_app_configs()}
    migrated, unmigrated = set(loader.migrated_apps), set(loader.unmigrated_apps)
    codes = set()
    if migrated & unmigrated or not (migrated | unmigrated) <= configs.keys():
        codes.add("migration.graph_invalid")
    owners = {entry["label"]: entry for entry in registry.domains.values()}
    for label, entry in owners.items():
        config = configs.get(label)
        if (config is None or config.name != entry["metadata"]["django_app"]["module"]
                or label not in migrated | unmigrated):
            codes.add("deployment.app_mismatch")
            continue
        nodes = [key for key in graph.nodes if key[0] == label]
        disk = [key for key in loader.disk_migrations if key[0] == label]
        if list(config.get_models()) and (label not in migrated or not nodes or not disk):
            codes.add("migration.models_without_migrations")
        for target in entry["metadata"]["migration_dependencies"]:
            if target not in configs or target not in migrated | unmigrated:
                codes.add("deployment.app_mismatch")
    for key, node in graph.node_map.items():
        if key not in loader.disk_migrations or key[0] not in migrated or key[0] not in configs:
            codes.add("migration.graph_invalid")
        for parent in node.parents:
            source, target = key[0], parent.key[0]
            if source == target:
                continue
            if source in owners:
                if target not in owners[source]["metadata"]["migration_dependencies"]:
                    codes.add("migration.undeclared_dependency")
            elif target in owners:
                codes.add("migration.reverse_dependency")
    if codes:
        raise MigrationContractError(codes)


def prepare_domain_registry(installed_descriptors, *, loader=None):
    """Build and check without changing caller references or database state."""
    def prepare():
        candidate = _checked(lambda: build_domain_registry(installed_descriptors), "candidate.invalid")
        if candidate.errors:
            raise MigrationContractError(["candidate.invalid"])
        actual = MigrationLoader(None) if loader is None else loader
        validate_migration_dependencies(candidate, actual)
        return candidate
    return _checked(prepare, "migration.graph_invalid")


def prepare_plugin_entry_point(installed_descriptors, handlers, reversal_handlers=None, *, loader=None):
    """Return one complete candidate; missing bindings retain SDK-2 semantics."""
    candidate = prepare_domain_registry(installed_descriptors, loader=loader)
    return _checked(lambda: PluginEntryPoint(candidate, handlers, reversal_handlers), "binding.invalid")
