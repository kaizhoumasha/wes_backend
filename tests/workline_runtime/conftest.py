from __future__ import annotations

import pytest

from tests.workline_runtime.support.runtime_builders import (
    make_mock_db,
    make_mock_device,
    make_mock_devices_by_role,
    make_mock_session,
    make_mock_workline,
)


@pytest.fixture
def workline_runtime_mock_db():
    return make_mock_db()


@pytest.fixture
def workline_runtime_session():
    return make_mock_session()


@pytest.fixture
def workline_runtime_workline():
    return make_mock_workline()


@pytest.fixture
def workline_runtime_device():
    return make_mock_device()


@pytest.fixture
def workline_runtime_devices_by_role():
    return make_mock_devices_by_role()
