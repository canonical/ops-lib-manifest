# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

from collections import OrderedDict
from dataclasses import dataclass
from typing import Iterable, List, Mapping, MutableMapping, Optional, Tuple

from .manifest import Manifests
from .manipulations import AnyCondition, HashableResource


@dataclass
class _ResourceAnalysis:
    correct: Iterable[HashableResource] = frozenset()
    extra: Iterable[HashableResource] = frozenset()
    missing: Iterable[HashableResource] = frozenset()


class Collector:
    def __init__(self, *manifests: Manifests) -> None:
        """Collect manifests for an ops charm.

        Usage:
        from ops.manifests import Collector

        class ExampleCharm(CharmBase):
            def __init__(self, *args):
                super().__init__(*args)

                # collection of ManifestImpls
                self.manifests = [ ... ]

                # create an Collector handler object
                self.collector = Collector(*self.manifests)

                # Register collector callbacks
                self.framework.observe(
                    self.on.list_versions_action,
                    self.collector.list_versions
                )
        """
        d = {manifest.name: manifest for manifest in manifests}
        self.manifests = OrderedDict(sorted(d.items(), key=lambda x: x[0]))

    def list_versions(self, event) -> None:
        """Respond to list_versions action."""
        result = {
            f"{name}-versions": "\n".join(str(_) for _ in manifest.releases)
            for name, manifest in self.manifests.items()
        }
        event.set_results(result)

    def list_resources(self, event, manifests: Optional[str], resources: Optional[str]):
        """List available, extra, and missing resources for each manifest."""
        self._list_resources(event, manifests, resources)

    def scrub_resources(
        self, event, manifests: Optional[str], resources: Optional[str]
    ):
        """Remove extra resources installed by each manifest.

        Uses the list_resource analysis to determine the extra resource
        then delete those resources.
        """
        results = self._list_resources(event, manifests, resources)
        for name, analysis in results.items():
            if analysis.extra:
                event.log(f"Removing {','.join(str(_) for _ in analysis.extra)}")
                self.manifests[name].delete_resources(*analysis.extra)
        self._list_resources(event, manifests, resources)

    def apply_missing_resources(
        self, event, manifests: Optional[str], resources: Optional[str]
    ):
        """Applies manifest resources that are missing from the cluster

        Uses the list_resource analysis to determine the missing resources
        then applies those resources.
        """
        results = self._list_resources(event, manifests, resources)
        for name, analysis in results.items():
            if analysis.missing:
                event.log(f"Applying {','.join(str(_) for _ in analysis.missing)}")
                self.manifests[name].apply_resources(*analysis.missing)
        self._list_resources(event, manifests, resources)

    @property
    def unready(self) -> List[str]:
        """List of statuses of resources with non-ready conditions."""
        return sorted(
            f"{name}: {obj} is not {cond.type}"
            for (name, obj), cond in self.conditions.items()
            if cond.status != "True"
        )

    @property
    def conditions(self) -> Mapping[Tuple[str, HashableResource], AnyCondition]:
        """
        Condition of all resources with `$obj.status.conditions[]`.

        The key of the mapping is the pair of
          * the name of the manifest in the collection
          * the installed resource in the cluster

        Each value represents a Condition which conforms to proposed spec
        https://github.com/kubernetes/enhancements/blob/dfb5b64322fe861fcc173ca8246a2ec5a511e46e/keps/sig-api-machinery/1623-standardize-conditions/README.md#proposal
        """
        return {
            (name, obj): cond
            for name, manifest in self.manifests.items()
            for obj in manifest.status()
            for cond in obj.status_conditions
        }

    @property
    def short_version(self) -> str:
        """Short status of collective manifests."""
        return ",".join(c.current_release for c in self.manifests.values())

    @property
    def long_version(self) -> str:
        """Long status of collective manifests."""
        return "Versions: " + ", ".join(
            f"{app}={c.current_release}" for app, c in self.manifests.items()
        )

    def _list_resources(
        self, event, manifests: Optional[str], resources: Optional[str]
    ):
        filter_manifests = manifests.split() if manifests else []
        filter_resources = resources.split() if resources else []

        man_filter = set(_.lower() for _ in filter_manifests)
        if man_filter:
            event.log(f"Filter manifest listings with {man_filter}")
        man_filter = man_filter or set(self.manifests.keys())

        res_filter = set(_.lower() for _ in filter_resources)
        if res_filter:
            event.log(f"Filter resource listing with {res_filter}")

        def kind_filter(rsc: HashableResource) -> bool:
            return not res_filter or rsc.kind.lower() in res_filter

        results: MutableMapping[str, _ResourceAnalysis] = {}
        event_result: MutableMapping[str, str] = {}
        for name, manifest in self.manifests.items():
            if name not in man_filter:
                results[name] = _ResourceAnalysis()
                continue

            labelled = manifest.labelled_resources()
            expected = manifest.resources
            installed = manifest.installed_resources()

            analyses = [expected & installed, labelled - expected, expected - installed]
            analyses = [frozenset(filter(kind_filter, cws)) for cws in analyses]
            correct, extra, missing = analyses

            results[name] = _ResourceAnalysis(correct, extra, missing)
            event_result.update(
                {
                    f"{name}-correct": "\n".join(sorted(str(_) for _ in correct)),
                    f"{name}-extra": "\n".join(sorted(str(_) for _ in extra)),
                    f"{name}-missing": "\n".join(sorted(str(_) for _ in missing)),
                }
            )

        event_result = {k: v for k, v in event_result.items() if v}
        event.set_results(event_result)
        return results
