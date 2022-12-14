.. _`changelog`:

=========
Changelog
=========

Versions follow `Semantic Versioning <https://semver.org/>`_ (``<major>.<minor>.<patch>``).

Backward incompatible (breaking) changes will only be introduced in major versions


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
