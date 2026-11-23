def setting(overrides: dict, defaults: dict, name: str):
    """Return an override when present, otherwise use the configured default."""
    return overrides.get(name) or defaults[name]
