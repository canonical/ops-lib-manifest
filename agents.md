# ops-lib-manifest - Agent Guide

**For user-facing documentation, see [README.md](README.md).**

This guide provides agent-specific information for making code changes to this library.

## Quick Reference

- **Repository**: canonical/ops-lib-manifest
- **Language**: Python 3.8+
- **Key dependencies**: ops, lightkube
- **Main modules**: `ops/manifests/{manifest.py, collector.py, manipulations.py}`
- **Public API**: Exported in `ops/manifests/__init__.py`

## Development Workflow

### Testing
```bash
tox -e unit              # Run unit tests
tox -e unit -- -vvs      # Verbose output
tox -e unit -- tests/unit/test_manifests.py::test_name  # Single test
```

### Linting & Formatting
```bash
tox -e lint              # Run ruff + mypy
tox -e format            # Auto-format code
```

### Code Style
- Line length: 99 characters
- Type hints: Required for public APIs (checked by mypy)
- Imports: Auto-sorted by ruff
- Docstrings: Present on classes and public methods

## Key Implementation Details

### Caching Behavior
- `@cached_property`: Cached for object lifetime (`client`, `releases`, `default_release`, `latest_release`)
- `@lru_cache()`: LRU-cached (`_safe_load()` for YAML parsing)
- **Implication**: Config changes during charm lifecycle may not be reflected in cached properties

### Resource Processing Order
1. **Additions** created first
2. **Subtractions** remove unwanted resources
3. **Patches** modify remaining resources

### Label-Based Safety
By default, `delete_resources()` only deletes resources with matching:
- `juju.io/application` label
- `juju.io/manifest` label
- Kind, name, and namespace

Set `ignore_labels=True` to skip this check (use with caution).

### Version Sorting
Natural ordering via `_by_version()`:
- "v1.2.10" > "v1.2.9" (numeric comparison)
- "v1.2.0-alpha" < "v1.2.0" (string comparison)

### Handling List Resources
YAML files with `kind: *List` are automatically flattened into individual resources.

### CRDs
- Loaded on client creation via `load_in_cluster_generic_resources()`
- `kind: CustomResourceDefinition` in manifests auto-registered via `create_resources_from_crd()`

## Common Changes

### Adding a Manipulation
1. Create class in `ops/manifests/manipulations.py` inheriting from `Patch`, `Addition`, or `Subtraction`
2. Implement `__call__` method
3. Export in `ops/manifests/__init__.py` (add to imports and `__all__`)
4. Add tests in `tests/unit/test_manipulations.py`

### Adding a Method to Manifests
1. Add to `Manifests` class in `ops/manifests/manifest.py`
2. Add docstring
3. Consider caching if expensive (`@cached_property` or `@lru_cache`)
4. Add tests in `tests/unit/test_manifests.py`

### Adding a Collector Feature
1. Add to `Collector` class in `ops/manifests/collector.py`
2. If action handler, accept `event` as first parameter
3. Add tests in `tests/unit/test_collector.py`

## Testing Patterns

### Fixtures (conftest.py)
- `manifest`: Pre-configured Manifests instance
- `mock_load_in_cluster_generic_resources`: Mocks CRD loading

### Test Data
- `tests/data/mock_manifests/`: Example manifest directory with versions v0.1, v0.2, v0.3.1

### Common Mocks
```python
# Mock ops Model
model = mock.MagicMock(autospec="ops.model.Model")

# Mock lightkube resource
resource = mock.MagicMock()
resource.kind = "Deployment"
resource.metadata.name = "test-deployment"
resource.metadata.namespace = "default"
```

## Troubleshooting

### Type Checking Failures
- Add type hints to function signatures
- Use `# type: ignore` with comment explaining why (unavoidable issues only)
- Ensure `types-PyYAML` and `types-backports` installed

### Lightkube Issues
- Verify resource kind and apiVersion correct
- Check CRDs loaded properly
- Ensure field_manager set on client
- Look for ApiError exceptions in logs

## Security Notes
- Never log secrets (audited for LP#2025283)
- Field manager on lightkube client prevents conflicts
- Labels prevent accidental deletion of other charm resources

---

**Note**: This guide focuses on implementation details. For usage examples and API overview, see README.md.
