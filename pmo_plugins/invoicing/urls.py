"""Trusted callable bindings for the PH-338 invoice import projection routes.

The plugin runtime mounts every ``DOMAIN_METADATA`` route, but only explicitly
trusted callables can dispatch. ``pmo_plugins.delivery.urls`` owns the deployment
root binding; ``entry_point`` returns the same complete business registry with the
invoicing import handler merged in so the owning integration slice can wire it
without inventing a second import path.
"""

from core.plugin_sdk.runtime import PluginEntryPoint

from . import views


def handlers():
    return {'invoice_import': views.invoice_import}


def entry_point():
    from pmo_plugins.delivery.urls import entry_point as business_entry_point
    base = business_entry_point()
    bindings = {}
    for (plugin_id, route_name), handler in base.handlers.items():
        bindings.setdefault(plugin_id, {})[route_name] = handler
    bindings.setdefault('invoicing', {}).update(handlers())
    return PluginEntryPoint(base.domain_registry, bindings)
