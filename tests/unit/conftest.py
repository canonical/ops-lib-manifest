# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import unittest.mock as mock

import pytest
from lightkube import ApiError

from ops.manifests import Manifests


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
def manifest():
    class TestManifests(Manifests):
        def __init__(self):
            self.data = {}
            super().__init__(
                "test-manifest",
                "unit-testing",
                "tests/data/mock_manifests",
            )

        @property
        def config(self):
            return self.data

    yield TestManifests()
