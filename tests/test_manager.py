# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0

"""
Test opsiconfd.manager
"""

import asyncio
import signal
import time
from unittest.mock import patch

import pytest

from opsiconfd.manager import Manager

from .utils import get_config, reset_singleton


@pytest.fixture()
def manager():  # pylint: disable=redefined-outer-name
	with (
		patch("opsiconfd.server.Server.run", lambda *args, **kwargs: None),
		patch("opsiconfd.manager.init_logging", lambda *args, **kwargs: None),
		patch("opsiconfd.manager.register_opsi_services", lambda *args, **kwargs: asyncio.sleep(0.1)),
		patch("opsiconfd.manager.unregister_opsi_services", lambda *args, **kwargs: asyncio.sleep(0.1)),
	):
		reset_singleton(Manager)
		man = Manager()
		man.run()
		try:
			yield man
		finally:
			man.stop()
			while man.running:
				time.sleep(1)


def test_manager_signals(manager):  # pylint: disable=redefined-outer-name
	# signal_handler is replaced in conftest
	manager._last_reload = 0  # pylint: disable=protected-access
	manager.orig_signal_handler(signal.SIGHUP, None)
	assert manager._last_reload != 0  # pylint: disable=protected-access

	def stop(force=False):
		manager.test_stop = "force" if force else "normal"

	manager._server.stop = stop  # pylint: disable=protected-access
	manager.orig_signal_handler(signal.SIGKILL, None)
	assert manager._should_stop is True  # pylint: disable=protected-access
	assert manager.test_stop == "normal"
	time.sleep(0.1)
	manager.orig_signal_handler(signal.SIGKILL, None)
	assert manager._should_stop is True  # pylint: disable=protected-access
	assert manager.test_stop == "force"


@pytest.mark.parametrize("cert_changed", (False, True))
def test_check_server_cert(manager, cert_changed):  # pylint: disable=redefined-outer-name
	def restart_workers(self):
		self.test_restarted = True

	with (
		patch("opsiconfd.server.Server.restart_workers", restart_workers),
		patch("opsiconfd.manager.setup_server_cert", lambda: cert_changed),
	):
		with get_config({"ssl_server_cert_check_interval": 0.00001}):
			time.sleep(2)
			assert getattr(manager._server, "test_restarted", False) == cert_changed  # pylint: disable=protected-access
