.. _`changelog`:

=========
Changelog
=========

Versions follow `Semantic Versioning <https://semver.org/>`_ (``<major>.<minor>.<patch>``).

Backward incompatible (breaking) changes will only be introduced in major versions

ops-lib-manifest 1.2.0 (2024-02-14)
=========================
* [#31](https://github.com/canonical/ops-lib-manifest/issues/31)
  - The `Collector.conditions` property returns a mapping that ends up
    hiding information relating to all the conditions of a kubernetes 
    resource.  Only ONE condition is present in the mapping for each
    resource. 
  - Introduce `Collector.all_conditions` property returning a list of
    conditions and their associated manifests and object
  - Check unready using the `all_conditions` property
* Allows a manifest to filter the ready check of each `condition` of an 
  object that it has installed by overriding the `is_ready(..)` method



ops-lib-manifest 1.1.4 (2024-01-10)
=========================
* only deletes resources created by this charm application
* LP#2025283 - Audit library to ensure that secrets aren't leaked to logs
* maintains python 3.7 compatability


ops-lib-manifest 1.1.3 (2023-06-28)
=========================

Issues Resolved
* [LP#2025087](https://launchpad.net/bugs/2025087)
   - resolves issue where every item from a List 
     type resource object is read from the list

ops-lib-manifest 1.1.2 (2023-04-17)
=========================

Issues Resolved
* [LP#2006619](https://launchpad.net/bugs/2006619)
    - resolves status issues when trying to use a client
      which cannot reach the API endpoint

ops-lib-manifest 1.1.1 (2022-04-06)
=========================

Issues Resolved
* [LP#1999427](https://launchpad.net/bugs/1999427)
    - resolve issues when loading CRDs from an
      unreachable API endpoint

ops-lib-manifest 1.1.0 (2022-02-17)
=========================

Feature
* Supports image manipulation of `Job`, `CronJob`,
  `ReplicationController` and `ReplicaSet` objects


ops-lib-manifest 1.0.0 (2022-12-14)
=========================

Issues Resolved
* [LP#1999427](https://launchpad.net/bugs/1999427)
    - handles non-api errors from the client which are represented
      as an http error response without json content.

Breaking Changes
----------------

* no longer are `lightkube.core.exceptions.ApiError`s raised on the following methods:
   * Manifest.status
   * Manifest.installed_resources
   * Manifest.apply_manifest
   * Manifest.delete_manifest
   * Manifest.apply_resources
   * Manifest.delete_resources

    instead a more generic exception `ManifestClientError` is raised.
