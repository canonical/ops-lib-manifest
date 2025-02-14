# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import unittest.mock as mock
from collections import namedtuple

import lightkube.codecs as codecs
from lightkube.resources.storage_v1 import StorageClass

import ops
from ops.manifests.collector import Collector
from ops.manifests.manifest import HashableResource


def test_collector_list_versions(manifest):
    event = mock.MagicMock()
    collector = Collector(manifest)
    collector.list_versions(event)
    event.set_results.assert_called_once_with({"test-manifest-versions": "v0.3.1\nv0.2"})


def test_collector_short_version(manifest):
    collector = Collector(manifest)
    assert collector.short_version == "v0.2"


def test_collector_long_version(manifest):
    collector = Collector(manifest)
    assert collector.long_version == "Versions: test-manifest=v0.2"


def responder(resp_by_name):
    def _respond(klass, name, namespace=None):
        response = resp_by_name.get(name, lambda r: r)
        obj = mock.MagicMock(spec=klass)
        obj.kind = klass.__name__
        obj.metadata.name = name
        obj.metadata.namespace = namespace
        return response(obj)

    return _respond


def test_collector_analyze_resources_all(manifest, lk_client, api_error_klass):
    def label_correct(r):
        r.metadata.labels = {
            "juju.io/manifest": manifest.name,
            "juju.io/application": manifest.model.app.name,
        }
        return r

    def label_conflict(r):
        r.metadata.labels = {
            "juju.io/manifest": manifest.name,
            "juju.io/application": manifest.model.app.name + "-conflict",
        }
        return r

    def raise_not_found(r):
        not_found = api_error_klass()
        not_found.status.code = 404
        not_found.status.message = f"{r.kind} Not Found"
        raise not_found

    resp_by_name = {
        "test-manifest-crd": label_correct,
        "test-manifest-deployment": label_correct,
        "test-manifest-secret": raise_not_found,
        "test-manifest-manager": label_conflict,
        "test-storage-class": label_correct,
    }
    extra_resource = responder(resp_by_name)(StorageClass, "test-storage-class")
    lk_client.list.return_value = [extra_resource]
    lk_client.get.side_effect = responder(resp_by_name)
    event = mock.MagicMock(spec=ops.ActionEvent)
    collector = Collector(manifest)
    (analysis,) = collector.analyze_resources(event, None, None)
    event.set_results.assert_called_once_with(
        {
            "test-manifest-correct": "\n".join(
                [
                    "CustomResourceDefinition/test-manifest-crd",
                    "Deployment/kube-system/test-manifest-deployment",
                ]
            ),
            "test-manifest-extra": "StorageClass/test-storage-class",
            "test-manifest-missing": "\n".join(
                [
                    "Secret/kube-system/test-manifest-secret",
                    "ServiceAccount/kube-system/test-manifest-manager",
                ]
            ),
            "test-manifest-conflicting": "ServiceAccount/kube-system/test-manifest-manager",
        }
    )

    """
    Note: 

    There is a missing ServiceAccount and there is also a conflicting ServiceAccount.
    The conflicting ServiceAccount is not labeled correctly, so it is not considered part of the manifest.

    This is a different situation from the missing Secret, which is not found at all.
    """
    assert analysis.manifest == manifest.name
    assert len(analysis.conflicting) == 1
    assert len(analysis.correct) == 2
    assert len(analysis.missing) == 2
    assert len(analysis.extra) == 1


def test_collector_analyze_kind_filter(manifest, lk_client, api_error_klass):
    def raise_not_found(r):
        not_found = api_error_klass()
        not_found.status.code = 404
        not_found.status.message = f"{r.kind} Not Found"
        raise not_found

    resp_by_name = {
        "test-manifest-deployment": raise_not_found,
    }
    lk_client.get.side_effect = responder(resp_by_name)
    event = mock.MagicMock(spec=ops.ActionEvent)
    collector = Collector(manifest)
    collector.analyze_resources(event, None, "deployment")
    event.set_results.assert_called_once_with(
        {"test-manifest-missing": "Deployment/kube-system/test-manifest-deployment"}
    )


def test_collector_list_manifest_filter(manifest):
    event = mock.MagicMock(spec=ops.ActionEvent)
    collector = Collector(manifest)
    collector.list_resources(event, "garbage", None)
    event.set_results.assert_called_once_with({})


@mock.patch("ops.manifests.collector.Collector.analyze_resources")
def test_collector_scrub_resources(mock_analyze_resources, manifest, lk_client):
    resource = HashableResource(
        codecs.from_dict(
            dict(
                apiVersion="v1",
                kind="Namespace",
                metadata=dict(name="delete-me"),
            )
        )
    )
    analysis = ops.manifests.collector.ResourceAnalysis("test-manifest")
    analysis.extra = {resource}
    mock_analyze_resources.return_value = [analysis]

    event = mock.MagicMock(spec=ops.ActionEvent)
    collector = Collector(manifest)
    with mock.patch.object(manifest, "_delete") as mock_delete:
        collector.scrub_resources(event, None, None)

    assert mock_analyze_resources.call_count == 2
    mock_analyze_resources.assert_called_with(event, None, None)
    event.log.assert_called_once_with("Removing Namespace/delete-me")
    mock_delete.assert_called_once_with(resource, None, False)


@mock.patch("ops.manifests.collector.Collector.analyze_resources")
def test_collector_install_missing_resources(mock_analyze_resources, manifest, lk_client, caplog):
    resource = codecs.from_dict(
        dict(
            apiVersion="v1",
            kind="Namespace",
            metadata=dict(name="install-me-im-missing"),
        )
    )
    analysis = ops.manifests.collector.ResourceAnalysis("test-manifest")
    analysis.missing = {HashableResource(resource)}
    mock_analyze_resources.return_value = [analysis]

    event = mock.MagicMock(spec=ops.ActionEvent)
    collector = Collector(manifest)
    collector.apply_missing_resources(event, None, None)

    assert mock_analyze_resources.call_count == 2
    mock_analyze_resources.assert_called_with(event, None, None)
    event.log.assert_called_once_with("Applying Namespace/install-me-im-missing")
    assert lk_client.apply.call_count == 1
    assert caplog.messages == [
        "Applying Namespace/install-me-im-missing",
        "Applied 1 Resources",
    ]


def test_collector_unready(manifest, lk_client):
    Condition = namedtuple("Condition", "status,type")
    conditions = [
        [Condition("False", "Here")],
        [dict(status="False", type="Ready"), dict(status="False", type="Ignored")],
    ]

    def mock_status_responder(klass, name, namespace=None):
        response = mock.MagicMock(spec=klass)
        response.kind = klass.__name__
        response.metadata.name = name
        response.metadata.namespace = namespace
        if response.kind == "Deployment":
            response.status.conditions = conditions[0]
        elif response.kind == "CustomResourceDefinition":
            response.status = {"conditions": conditions[1]}
        return response

    collector = Collector(manifest)
    template = "test-manifest: {} is not {}"
    with mock.patch.object(lk_client, "get") as mock_get:
        mock_get.side_effect = mock_status_responder

        assert len(collector.conditions) == 2
        assert len(collector.all_conditions) == 3
        assert collector.unready == [
            template.format("CustomResourceDefinition/test-manifest-crd", "Ready"),
            template.format("Deployment/kube-system/test-manifest-deployment", "Here"),
        ]
