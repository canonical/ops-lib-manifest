# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import FrozenSet, List, Mapping, MutableMapping, Optional, Tuple

import ops

from .manifest import Manifests
from .manipulations import AnyCondition, HashableResource

logger = logging.getLogger(__name__)


@dataclass
class ResourceAnalysis:
    """Analysis of resources installed in the cluster."""

    manifest: str
    conflicting: FrozenSet[HashableResource] = frozenset()
    correct: FrozenSet[HashableResource] = frozenset()
    extra: FrozenSet[HashableResource] = frozenset()
    missing: FrozenSet[HashableResource] = frozenset()


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
        """List available, extra, conflicting, and missing resources for each manifest."""
        self.analyze_resources(event, manifests, resources)

    def scrub_resources(self, event, manifests: Optional[str], resources: Optional[str]):
        """Remove extra resources installed by each manifest.

        Uses the list_resource analysis to determine the extra resource
        then delete those resources.
        """
        for analysis in self.analyze_resources(event, manifests, resources):
            if analysis.extra:
                event.log(f"Removing {','.join(str(_) for _ in analysis.extra)}")
                self.manifests[analysis.manifest].delete_resources(*analysis.extra)
        self.list_resources(event, manifests, resources)

    def apply_missing_resources(self, event, manifests: Optional[str], resources: Optional[str]):
        """Applies manifest resources that are missing from the cluster

        Uses the list_resource analysis to determine the missing resources
        then applies those resources.
        """
        for analysis in self.analyze_resources(event, manifests, resources):
            if analysis.missing:
                event.log(f"Applying {','.join(str(_) for _ in analysis.missing)}")
                self.manifests[analysis.manifest].apply_resources(*analysis.missing)
        self.list_resources(event, manifests, resources)

    @property
    def unready(self) -> List[str]:
        """List of statuses of resources with non-ready conditions."""
        return sorted(
            f"{name}: {obj} is not {cond.type}"
            for name, obj, cond in self.all_conditions
            if self.manifests[name].is_ready(obj, cond) is False
        )

    @property
    def conditions(self) -> Mapping[Tuple[str, HashableResource], AnyCondition]:
        """
        Condition of all resources with `$obj.status.conditions[]`.

        The key of the mapping is the pair of
          * the name of the manifest in the collection
          * the installed resource in the cluster
        """
        return {(name, obj): cond for name, obj, cond in self.all_conditions}

    @property
    def all_conditions(self) -> List[Tuple[str, HashableResource, AnyCondition]]:
        """
        Condition of all resources with `$obj.status.conditions[]`.

        The list contains tuples of
          * the name of the manifest in the collection
          * the installed resource in the cluster
          * the condition

        Each value represents a Condition which conforms to proposed spec
        https://github.com/kubernetes/enhancements/blob/dfb5b64322fe861fcc173ca8246a2ec5a511e46e/keps/sig-api-machinery/1623-standardize-conditions/README.md#proposal
        """
        return [
            (name, obj, cond)
            for name, manifest in self.manifests.items()
            for obj in manifest.status()
            for cond in obj.status_conditions
        ]

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

    def analyze_resources(
        self, event: ops.EventBase, manifests: Optional[str], resources: Optional[str]
    ) -> List[ResourceAnalysis]:
        """Analyze resources installed in the cluster.

        Args:
            event: The event object that triggered the action.
            manifests: A space-separated list of manifests to filter.
            resources: A space-separated list of resources to filter.

        Returns:
            A list of ResourceAnalysis objects.
        """

        filter_manifests = manifests.split() if manifests else []
        filter_resources = resources.split() if resources else []
        log = event.log if isinstance(event, ops.ActionEvent) else logger.info

        man_filter = set(_.lower() for _ in filter_manifests)
        if man_filter:
            log(f"Filter manifest listings with {man_filter}")
        man_filter = man_filter or set(self.manifests.keys())

        res_filter = set(_.lower() for _ in filter_resources)
        if res_filter:
            log(f"Filter resource listing with {res_filter}")

        def kind_filter(rsc: HashableResource) -> bool:
            return not res_filter or rsc.kind.lower() in res_filter

        results: List[ResourceAnalysis] = []
        event_result: MutableMapping[str, str] = {}
        for name, manifest in self.manifests.items():
            if name not in man_filter:
                results.append(ResourceAnalysis(name))
                continue

            labelled = manifest.labelled_resources()
            expected = manifest.resources
            installed = manifest.installed_resources()
            conflicting = manifest.conflicting_resources(installed)

            analyses = [
                # kubernetes resources which are installed by another manifest
                conflicting,
                # expected kubernetes resources which are both installed and not conflicting
                expected & (installed - conflicting),
                # kubernetes resources labelled by this manifest but are not expected
                labelled - expected,
                # kubernetes resources expected by this manifest which are not installed
                expected - (installed - conflicting),
            ]
            analyses = [frozenset(filter(kind_filter, cws)) for cws in analyses]
            conflicting, correct, extra, missing = analyses

            results.append(ResourceAnalysis(name, conflicting, correct, extra, missing))
            event_result.update(
                {
                    f"{name}-correct": "\n".join(sorted(str(_) for _ in correct)),
                    f"{name}-extra": "\n".join(sorted(str(_) for _ in extra)),
                    f"{name}-missing": "\n".join(sorted(str(_) for _ in missing)),
                    f"{name}-conflicting": "\n".join(sorted(str(_) for _ in conflicting)),
                }
            )

        event_result = {k: v for k, v in event_result.items() if v}
        if isinstance(event, ops.ActionEvent):
            event.set_results(event_result)
        return results
