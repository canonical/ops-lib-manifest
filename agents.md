# ops-lib-manifest - Agent Guide

This document provides comprehensive information about the ops-lib-manifest library to help AI agents (like GitHub Copilot) understand the codebase and make effective changes.

## Repository Overview

**Repository**: canonical/ops-lib-manifest  
**Purpose**: A Python library for managing Kubernetes manifest files in Juju charms using the Operator Framework (ops)  
**Language**: Python (3.8+)  
**Main Dependencies**: ops, lightkube  
**Current Version**: 1.6.0

## What This Library Does

The ops-lib-manifest library provides a framework for Juju charms to:
1. Load and manage multiple versions of Kubernetes manifest files
2. Manipulate manifest resources before deploying them (patching, adding, subtracting resources)
3. Apply manifests to a Kubernetes cluster using lightkube
4. Track the status of deployed resources
5. Compare expected vs. installed resources
6. Support multiple concurrent manifest releases

### Key Use Case

Kubernetes projects often distribute reference manifest YAML files, but these manifests:
- Have inconsistent ways to indicate required vs. variable options
- May contain placeholder values like `<ENTER_YOUR_VCENTER_USERNAME>`
- Require automation to manipulate and deploy with real configuration values

This library solves these problems by providing a structured way to load, manipulate, and deploy manifests from Juju charms.

## Repository Structure

```
ops-lib-manifest/
├── ops/
│   └── manifests/           # Main library code
│       ├── __init__.py      # Public API exports
│       ├── manifest.py      # Core Manifests class
│       ├── collector.py     # Collector class for managing multiple manifests
│       ├── manipulations.py # Manipulation classes (Patch, Addition, Subtraction)
│       ├── exceptions.py    # Custom exceptions
│       └── literals.py      # Constants (label names)
├── tests/
│   ├── data/               # Test data (mock manifests)
│   └── unit/               # Unit tests
│       ├── conftest.py     # Pytest fixtures
│       ├── test_manifests.py
│       ├── test_collector.py
│       └── test_manipulations.py
├── pyproject.toml          # Project metadata and dependencies
├── tox.ini                 # Test/lint/format configuration
├── README.md               # User documentation
├── CHANGELOG.md            # Version history
└── agents.md              # This file

```

## Core Architecture

### Main Classes and Their Responsibilities

#### 1. `Manifests` (manifest.py)
The central class that represents a collection of manifest files for a specific application release.

**Key responsibilities:**
- Load manifest YAML files from a versioned directory structure
- Parse YAML into lightkube resource objects
- Apply manipulations (patches, additions, subtractions) to resources
- Deploy resources to Kubernetes cluster via lightkube
- Query installed resources and their status
- Support multiple release versions

**Key properties/methods:**
- `config` (property): Must be implemented by subclasses to provide configuration
- `current_release`: Determines which version to use based on config
- `resources`: All unique resources after applying manipulations
- `apply_manifests()`: Deploy all resources to cluster
- `delete_manifests()`: Remove all resources from cluster
- `installed_resources()`: Query what's currently in the cluster
- `status()`: Get status of deployed resources

#### 2. `Collector` (collector.py)
Manages multiple `Manifests` instances for charms that deploy multiple applications.

**Key responsibilities:**
- Aggregate multiple manifest collections
- Provide combined status across all manifests
- Handle actions like list-versions, list-resources, scrub-resources
- Report overall readiness state

**Key properties/methods:**
- `unready`: List of resources with non-ready conditions
- `conditions`: Mapping of (manifest_name, resource) to condition
- `all_conditions`: List of all (manifest_name, resource, condition) tuples
- `short_version`: Comma-separated current releases (e.g., "v1.2.3,v2.0.1")
- `long_version`: Formatted version string (e.g., "Versions: app1=v1.2.3, app2=v2.0.1")
- `list_versions(event)`: Action handler to list available versions
- `list_resources(event, manifests, resources)`: Action handler to analyze resources
- `scrub_resources(event, manifests, resources)`: Remove extra installed resources
- `apply_missing_resources(event, manifests, resources)`: Apply resources that are missing
- `analyze_resources(event, manifests, resources)`: Returns List[ResourceAnalysis] with detailed analysis

#### 3. `Manipulation` Classes (manipulations.py)

**Base Classes:**
- `Manipulation`: Abstract base for all manipulations
- `Patch`: Modify existing resources in manifests
- `Addition`: Add new resources not in original manifests
- `Subtraction`: Remove resources from manifests

**Built-in Implementations:**
- `ManifestLabel`: Adds Juju-related labels to all resources (juju.io/application, juju.io/manifest, juju.io/manifest-version)
- `ConfigRegistry`: Updates image registry across all container resources (Pods, DaemonSets, Deployments, StatefulSets)
- `CreateNamespace`: Adds a namespace resource
- `SubtractEq`: Removes a specific resource by comparing kind, name, and namespace
- `update_tolerations()`: Helper function (not a class) to update tolerations on Pod-like resources

**Custom Manipulations:**
Charms can create custom manipulation classes by inheriting from Patch, Addition, or Subtraction.

#### 4. `HashableResource` (manipulations.py)
Wrapper around lightkube resource objects to make them hashable and comparable.

**Purpose:**
- Enable resources to be used in sets/dicts
- Provide equality comparison based on kind/namespace/name
- Format resources as strings for logging/display

#### 5. `ResourceAnalysis` (collector.py)
Dataclass that represents the analysis of resources for a specific manifest.

**Fields:**
- `manifest` (str): Name of the manifest being analyzed
- `conflicting` (FrozenSet[HashableResource]): Resources that exist but were not installed by this charm
- `correct` (FrozenSet[HashableResource]): Resources that match expected state
- `extra` (FrozenSet[HashableResource]): Resources installed but no longer in the manifest
- `missing` (FrozenSet[HashableResource]): Resources expected but not yet installed

**Usage:**
Returned by `Collector.analyze_resources()` to provide detailed resource analysis for actions like list-resources, scrub-resources, and apply-missing-resources.

### Directory Structure for Manifest Files

Charms using this library must organize manifest files like this:

```
<base_path>/
├── version                    # Text file with default version (e.g., "v1.2.3")
└── manifests/                 # Folder with all releases
    ├── v1.1.10/              # Version folder
    │   ├── manifest-1.yaml
    │   └── manifest-2.yaml
    ├── v1.1.11/              # Another version
    │   ├── manifest-1.yaml
    │   ├── manifest-2.yaml
    │   └── manifest-3.yaml
    └── v1.2.0/               # Latest version
        └── manifest.yaml
```

**Key requirements:**
- `version` file contains the default version string
- `manifests/` folder contains version subdirectories
- Each version folder contains `.yaml` or `.yml` files
- Files can be named anything with the right extension

## Development Workflow

### Setting Up Development Environment

```bash
# Install tox (if not already installed)
pip install tox

# Run all checks
tox
```

### Running Tests

```bash
# Run unit tests
tox -e unit

# Run tests with coverage
tox -e unit -- --cov-report=html

# Run specific test
tox -e unit -- tests/unit/test_manifests.py::test_manifest

# Run tests with verbose output
tox -e unit -- -vvs
```

### Code Quality Tools

#### Linting
```bash
# Run linting checks (ruff + mypy)
tox -e lint

# Check specific file
tox -e lint -- ops/manifests/manifest.py
```

Linting tools:
- **ruff**: Fast Python linter (replaces flake8, isort, etc.)
- **mypy**: Static type checker

#### Formatting
```bash
# Auto-format code
tox -e format
```

Formatting tools:
- **ruff format**: Code formatter (black-compatible)
- **ruff check --fix --select I**: Auto-fix import sorting

### Configuration Files

- **pyproject.toml**: Project metadata, dependencies, tool config
- **tox.ini**: Test environments and commands
- **.github/workflows/ci.yaml**: GitHub Actions CI pipeline

### Code Style Guidelines

1. **Line length**: 99 characters (configured in pyproject.toml)
2. **Type hints**: Required for public APIs (checked by mypy)
3. **Import sorting**: Automatic via ruff
4. **Naming conventions**:
   - Classes: PascalCase
   - Functions/methods: snake_case
   - Constants: UPPER_SNAKE_CASE (in literals.py)
5. **Docstrings**: Present on classes and public methods

### Testing Patterns

The test suite uses pytest with these patterns:

**Fixtures (conftest.py):**
- `manifest`: Pre-configured Manifests instance for testing
- `mock_load_in_cluster_generic_resources`: Mocks lightkube's CRD loading

**Test organization:**
- `test_manifests.py`: Tests for Manifests class
- `test_collector.py`: Tests for Collector class
- `test_manipulations.py`: Tests for manipulation classes

**Test data:**
- `tests/data/mock_manifests/`: Example manifest directory structure
- Contains multiple versions (v0.1, v0.2, v0.3.1) for testing

**Common testing patterns:**
```python
# Mocking the ops Model
model = mock.MagicMock(autospec="ops.model.Model")

# Creating a test Manifests instance
m = Manifests("test-name", model, "tests/data/mock_manifests")

# Mocking lightkube resources
resource = mock.MagicMock()
resource.kind = "Deployment"
resource.metadata.name = "test-deployment"
resource.metadata.namespace = "default"
```

## Common Code Patterns

### Creating a Manifests Implementation

```python
from ops.manifests import Manifests, ManifestLabel, ConfigRegistry

class MyAppManifests(Manifests):
    def __init__(self, charm, charm_config):
        manipulations = [
            ManifestLabel(self),
            ConfigRegistry(self),
            # Add custom manipulations here
        ]
        super().__init__(
            "my-app",              # Unique name for this manifest
            charm.model,           # ops Model object
            "upstream/my-app",     # Path to manifest files
            manipulations          # List of manipulations
        )
        self.charm_config = charm_config

    @property
    def config(self) -> Dict:
        """Returns config mapped from charm config and joined relations."""
        config = dict(**self.charm_config)
        
        # Remove unset values
        for key, value in dict(**config).items():
            if value == "" or value is None:
                del config[key]
        
        # Map release config
        config["release"] = config.pop("my-app-release", None)
        
        return config
```

### Creating Custom Manipulations

#### Custom Patch
```python
from ops.manifests import Patch

class UpdateSecretData(Patch):
    """Update secret data in Secret resources."""
    
    def __call__(self, obj):
        if obj.kind == "Secret" and obj.metadata.name == "my-secret":
            # Modify the secret
            obj.stringData = {
                "username": self.manifests.config.get("username"),
                "password": self.manifests.config.get("password"),
            }
```

#### Custom Addition
```python
from ops.manifests import Addition
from lightkube import codecs

class AddCustomConfigMap(Addition):
    """Add a custom ConfigMap resource."""
    
    def __call__(self):
        return codecs.from_dict({
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "my-config", "namespace": "default"},
            "data": {"key": "value"}
        })
```

#### Custom Subtraction
```python
from ops.manifests import Subtraction

class RemoveTestResources(Subtraction):
    """Remove resources with 'test' in their name."""
    
    def __call__(self, obj):
        # Return True to subtract this resource
        return "test" in obj.metadata.name.lower()
```

#### Using SubtractEq
```python
from ops.manifests import SubtractEq
from lightkube import codecs

class MyAppManifests(Manifests):
    def __init__(self, charm, charm_config):
        # Create a resource to subtract
        unwanted_resource = codecs.from_dict({
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": "unwanted-sa", "namespace": "default"}
        })
        
        manipulations = [
            ManifestLabel(self),
            SubtractEq(self, unwanted_resource),  # Remove this specific resource
        ]
        super().__init__("my-app", charm.model, "upstream/my-app", manipulations)
```

#### Using update_tolerations
```python
from ops.manifests import Patch, update_tolerations
from lightkube.models.core_v1 import Toleration

class UpdateTolerations(Patch):
    """Add custom tolerations to workload resources."""
    
    def __call__(self, obj):
        def adjuster(existing_tolerations):
            # Add a new toleration
            new_toleration = Toleration(
                key="node.kubernetes.io/disk-pressure",
                operator="Exists",
                effect="NoSchedule"
            )
            # Combine existing and new tolerations
            all_tolerations = list(existing_tolerations or [])
            all_tolerations.append(new_toleration)
            return all_tolerations
        
        # Apply tolerations using the helper function
        update_tolerations(obj, adjuster)
```

### Using Collector in a Charm

```python
from ops.charm import CharmBase
from ops.manifests import Collector

class MyCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        
        # Create manifest implementations
        self.app1_manifests = App1Manifests(self, self.config)
        self.app2_manifests = App2Manifests(self, self.config)
        
        # Create collector
        self.collector = Collector(
            self.app1_manifests,
            self.app2_manifests,
        )
        
        # Register action handlers
        self.framework.observe(
            self.on.list_versions_action,
            self.collector.list_versions
        )
        self.framework.observe(
            self.on.list_resources_action,
            self._on_list_resources
        )
        
        # Register update status
        self.framework.observe(
            self.on.update_status,
            self._update_status
        )
    
    def _on_list_resources(self, event):
        manifests = event.params.get("manifests")
        resources = event.params.get("resources")
        self.collector.list_resources(event, manifests, resources)
    
    def _update_status(self, event):
        unready = self.collector.unready
        if unready:
            self.unit.status = WaitingStatus(", ".join(unready))
        else:
            self.unit.status = ActiveStatus("Ready")
            self.unit.set_workload_version(self.collector.short_version)
```

## Key Concepts

### Release Management

The library supports multiple release versions:

1. **Default Release**: Read from the `version` file in the base path
2. **Latest Release**: The highest version number found in manifests/
3. **Current Release**: Determined by (in order):
   - `config["release"]` if set
   - Default release from `version` file
   - Latest release as fallback

### Resource Lifecycle

1. **Load**: Read YAML files from the current release directory
2. **Parse**: Convert YAML to lightkube resource objects
3. **Manipulate**: Apply manipulations in order:
   - Additions are created first
   - Subtractions remove unwanted resources
   - Patches modify remaining resources
4. **Deploy**: Apply resources to cluster using lightkube client
5. **Monitor**: Query installed resources and their conditions

### Labels

The library automatically adds these labels to all resources (via ManifestLabel):
- `juju.io/application`: The Juju application name
- `juju.io/manifest`: The manifest name
- `juju.io/manifest-version`: Combined manifest name and version

These labels enable:
- Tracking which charm deployed which resources
- Querying resources by manifest
- Cleaning up resources when removing a charm

### Resource Status and Conditions

Kubernetes resources have status conditions that indicate their state:
- Deployments: Available, Progressing
- Pods: Ready, Initialized, ContainersReady
- Custom resources: Varies by type

The library:
1. Queries resources with `.status.conditions`
2. Checks if conditions are "True" (ready) or "False" (not ready)
3. Manifests can override `is_ready()` to customize readiness logic
4. Collector aggregates readiness across all manifests

### Error Handling

**Custom Exceptions:**
- `ManifestBaseError`: Base class for all manifest errors
- `ManifestClientError`: Errors from Kubernetes API client

**Common error scenarios:**
- Cluster connection failures (SSL issues, etc.)
- Missing manifest files
- Invalid YAML syntax
- API errors when applying resources

### Charm Actions

The library is designed to support several Juju actions that charms should implement:

1. **list-versions**: List all available manifest versions
   - Handler: `collector.list_versions(event)`
   - Returns: `{manifest-name}-versions` for each manifest

2. **list-resources**: Analyze installed resources
   - Handler: `collector.list_resources(event, manifests, resources)`
   - Params: `manifests` (optional filter), `resources` (optional filter)
   - Returns: Analysis showing correct, extra, missing, and conflicting resources

3. **scrub-resources**: Remove extra resources
   - Handler: `collector.scrub_resources(event, manifests, resources)`
   - Params: Same as list-resources
   - Action: Deletes resources that are installed but no longer in manifests

4. **apply-missing-resources**: Apply resources that are missing
   - Handler: `collector.apply_missing_resources(event, manifests, resources)`
   - Params: Same as list-resources
   - Action: Applies resources that are in manifests but not installed

Example action definition in `actions.yaml`:
```yaml
list-versions:
  description: List all available manifest versions
list-resources:
  description: Analyze installed resources
  params:
    manifests:
      type: string
      description: Space-separated list of manifests to filter (optional)
    resources:
      type: string
      description: Space-separated list of resources to filter (optional)
scrub-resources:
  description: Remove extra resources no longer in manifests
  params:
    manifests:
      type: string
    resources:
      type: string
apply-missing-resources:
  description: Apply resources that are missing from the cluster
  params:
    manifests:
      type: string
    resources:
      type: string
```

## Important Implementation Details

### Caching and Performance

The library uses caching for efficiency:
- `@cached_property`: Results are cached for the lifetime of the object
  - `client`, `manifest_path`, `releases`, `default_release`, `latest_release`
- `@lru_cache()`: Results are cached with LRU eviction
  - `_safe_load()`: YAML file parsing is cached

**Implication**: Changes to config during a charm's lifecycle may not be reflected if cached properties are accessed before the change.

### Handling List Resources

Kubernetes manifests sometimes contain "List" resources (kind ends with "List"):
```yaml
apiVersion: v1
kind: List
items:
  - apiVersion: v1
    kind: Pod
    ...
  - apiVersion: v1
    kind: Service
    ...
```

The library automatically flattens these into individual resources.

### Generic Resources and CRDs

The library supports Custom Resource Definitions (CRDs):
1. On client creation, loads all in-cluster CRDs using `load_in_cluster_generic_resources()`
2. When parsing manifests with kind="CustomResourceDefinition", registers them using `create_resources_from_crd()`
3. Can then work with custom resources like built-in resources

### Lightkube Integration

The library uses lightkube for Kubernetes API interactions:
- `Client`: Kubernetes API client with field management
- `codecs`: Convert between dicts and lightkube objects
- `ApiError`, `HTTPError`: Exception types from API operations
- Resource types: GenericGlobalResource, GenericNamespacedResource, model resources

### Version Sorting

Versions are sorted using natural ordering:
- "v1.2.10" > "v1.2.9" (numeric comparison of parts)
- "v1.2.0-alpha" < "v1.2.0" (string comparison of non-numeric parts)
- Implemented by `_by_version()` function

## Common Tasks for Agents

### Adding a New Manipulation Type

1. Create a new class in `ops/manifests/manipulations.py` inheriting from Patch, Addition, or Subtraction
2. Implement the `__call__` method
3. Add the class to exports in `ops/manifests/__init__.py`
4. Add to `__all__` list
5. Write tests in `tests/unit/test_manipulations.py`

### Adding a New Method to Manifests

1. Add the method to the `Manifests` class in `ops/manifests/manifest.py`
2. Document the method with a docstring
3. Consider if it should be cached (use `@cached_property` or `@lru_cache`)
4. Write tests in `tests/unit/test_manifests.py`

### Adding a New Feature to Collector

1. Add the method to the `Collector` class in `ops/manifests/collector.py`
2. If it's an action handler, accept `event` as first parameter
3. Consider aggregation patterns across multiple manifests
4. Write tests in `tests/unit/test_collector.py`

### Fixing a Bug

1. Write a test that reproduces the bug
2. Run the test to confirm it fails
3. Fix the bug with minimal changes
4. Run the test to confirm it passes
5. Run full test suite: `tox -e unit`
6. Run linting: `tox -e lint`
7. Format code: `tox -e format`

### Updating Dependencies

1. Edit `pyproject.toml` under `[project] dependencies`
2. Test locally with the new version
3. Run full test suite
4. Update CHANGELOG.md with the change

## Troubleshooting Common Issues

### Import Errors

**Problem**: Cannot import from ops.manifests  
**Solution**: The package structure uses namespace packages. Ensure:
- `ops/manifests/__init__.py` has correct exports
- Installation includes the ops.manifests package

### Type Checking Failures

**Problem**: mypy reports type errors  
**Solution**: 
- Add type hints to function signatures
- Use `# type: ignore` for unavoidable issues (with comment explaining why)
- Check that `types-PyYAML` and `types-backports` are installed

### Test Failures

**Problem**: Tests fail after making changes  
**Solution**:
- Check if test data needs updating (tests/data/mock_manifests)
- Verify mocks are set up correctly
- Run single test with `-vvs` for detailed output
- Check if cached properties need invalidation in tests

### Lightkube Issues

**Problem**: Resources not applying correctly  
**Solution**:
- Verify the resource kind and apiVersion are correct
- Check if CRDs are loaded properly
- Ensure field_manager is set correctly on the client
- Look for ApiError exceptions in logs

## Related Documentation

- **README.md**: User-facing documentation with examples
- **CHANGELOG.md**: Version history and breaking changes
- **Juju Operator Framework docs**: https://juju.is/docs/sdk/ops
- **Lightkube docs**: https://lightkube.readthedocs.io/
- **Kubernetes API reference**: https://kubernetes.io/docs/reference/kubernetes-api/

## Best Practices for Code Changes

1. **Make minimal changes**: Only modify what's necessary for the task
2. **Follow existing patterns**: Look at similar code in the codebase
3. **Write tests**: Add tests for new functionality or bug fixes
4. **Type hints**: Add type hints to new functions/methods
5. **Documentation**: Update docstrings if changing behavior
6. **Backwards compatibility**: Don't break existing APIs without major version bump
7. **Performance**: Consider caching for expensive operations
8. **Error handling**: Use appropriate exceptions and log warnings/errors
9. **Security**: Never log secrets or sensitive data
10. **Testing**: Run `tox` before committing changes

## Security Considerations

- **Never log secrets**: The codebase was audited for LP#2025283 to ensure secrets aren't leaked to logs
- **Validate input**: When processing user-provided config, validate and sanitize
- **Field manager**: The lightkube client uses field management to avoid conflicts
- **Resource deletion**: Be careful with delete operations to avoid removing unrelated resources
- **Labels**: The library uses labels to track ownership and prevent accidental deletion of other resources

## Version History Highlights

- **1.6.0** (current): Latest release
- **1.3.0**: Addition objects can create iterables of resources
- **1.2.0**: Introduced `all_conditions` and improved readiness checking
- **1.1.4**: Only deletes resources created by the charm application
- **1.1.3**: Fixed List resource handling
- **1.1.2**: Fixed status issues with client usage

See CHANGELOG.md for complete history.

---

**Note to AI Agents**: This document is intended to help you understand the codebase quickly. When making changes:
- Read the relevant source files to understand current implementation
- Check tests to understand expected behavior
- Run tests locally to verify changes
- Follow the existing code style and patterns
- Update this document if adding major new features
