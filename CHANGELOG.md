.. _`changelog`:

=========
Changelog
=========

Versions follow `Semantic Versioning <https://semver.org/>`_ (``<major>.<minor>.<patch>``).

Backward incompatible (breaking) changes will only be introduced in major versions

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
