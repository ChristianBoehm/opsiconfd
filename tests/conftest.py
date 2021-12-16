# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0

import asyncio
import urllib3
import pytest
from _pytest.logging import LogCaptureHandler

from opsiconfd.backend import BackendManager

urllib3.disable_warnings()

def emit(*args, **kwargs) -> None:  # pylint: disable=unused-argument
	pass
LogCaptureHandler.emit = emit


BackendManager.default_config = {
	"backendConfigDir": "tests/opsi-config/backends",
	"dispatchConfigFile": "tests/opsi-config/backendManager/dispatch.conf"
}

@pytest.fixture(scope='session')
def event_loop():
	"""Create an instance of the default event loop for each test case."""
	loop = asyncio.get_event_loop_policy().new_event_loop()
	yield loop
	loop.close()
