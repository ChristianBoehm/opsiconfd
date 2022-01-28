# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
schemathesis tests
"""

import schemathesis

import pytest
import pytest_asyncio.plugin

from .utils import (  # pylint: disable=unused-import
	config, sync_clean_redis, disable_request_warning,
	ADMIN_USER, ADMIN_PASS
)


# Workaround for error:
# AttributeError: 'function' object has no attribute 'hypothesis'
def _hypothesis_test_wraps_coroutine(function) -> bool:  # pylint: disable=unused-argument
	return False


pytest_asyncio.plugin._hypothesis_test_wraps_coroutine = _hypothesis_test_wraps_coroutine  # pylint: disable=protected-access


@pytest.fixture
def get_schemathesis(config):  # pylint: disable=redefined-outer-name
	return schemathesis.from_uri(
		f"{config.external_url}/openapi.json",
		auth=(ADMIN_USER, ADMIN_PASS),
		verify=False
	)


schema = schemathesis.from_pytest_fixture("get_schemathesis")


@schema.parametrize(endpoint="^/rpc$")
def test_rpc(case):  # pylint: disable=redefined-outer-name
	sync_clean_redis()
	# case.call_and_validate(auth=(ADMIN_USER, ADMIN_PASS), verify=False)
	case.call(auth=(ADMIN_USER, ADMIN_PASS), verify=False)


@schema.parametrize(endpoint="^/admin/(?!memory)")
def test_admin(case):  # pylint: disable=redefined-outer-name
	sync_clean_redis()
	# case.call_and_validate(auth=(ADMIN_USER, ADMIN_PASS), verify=False)
	case.call(auth=(ADMIN_USER, ADMIN_PASS), verify=False)


@schema.parametrize(endpoint="^/ssl")
def test_ssl(case):  # pylint: disable=redefined-outer-name
	sync_clean_redis()
	case.call(auth=(ADMIN_USER, ADMIN_PASS), verify=False)
