# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import unittest.mock as mock

import pytest
from lightkube import ApiError
from lightkube.codecs import from_dict
from ops.charm import CharmBase
from ops.testing import Harness

from ops.manifests import Manifests
from ops.manifests.manipulations import ManifestLabel, SubtractEq


@pytest.fixture(autouse=True)
def lk_client():
    with mock.patch("ops.manifests.manifest.Client", autospec=True) as mock_lightkube:
        yield mock_lightkube.return_value


@pytest.fixture()
def api_error_klass():
    class TestApiError(ApiError):
        status = mock.MagicMock()

        def __init__(self):
            pass

    yield TestApiError


@pytest.fixture
def harness():
    class UnitTestCharm(CharmBase):
        pass

    yield Harness(UnitTestCharm)


@pytest.fixture
def manifest(harness):
    remove_me = from_dict(
        dict(
            apiVersion="v1",
            kind="ConfigMap",
            metadata=dict(name="test-manifest-config-map", namespace="kube-system"),
        )
    )

    class TestManifests(Manifests):
        def __init__(self):
            self.data = {}
            super().__init__(
                "test-manifest",
                harness.model,
                "tests/data/mock_manifests",
                [ManifestLabel(self), SubtractEq(self, remove_me)],
            )

        @property
        def config(self):
            return self.data

    yield TestManifests()
