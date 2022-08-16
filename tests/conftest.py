# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
conftest
"""

import asyncio
import os
import pprint
import shutil
import sys
import warnings
from tempfile import mkdtemp
from unittest.mock import patch

import urllib3
from _pytest.logging import LogCaptureHandler
from pytest import fixture, hookimpl, skip

from opsiconfd.application.main import application_setup
from opsiconfd.backend import BackendManager
from opsiconfd.config import config as _config
from opsiconfd.grafana import GRAFANA_DB, grafana_is_local
from opsiconfd.manager import Manager
from opsiconfd.setup import setup_ssl

GRAFANA_AVAILABLE = False
MYSQL_BACKEND_AVAILABLE = False


def signal_handler(self, signum, frame):  # pylint: disable=unused-argument
	sys.exit(1)


Manager.orig_signal_handler = Manager.signal_handler  # type: ignore[attr-defined]
Manager.signal_handler = signal_handler  # type: ignore[assignment]


def emit(*args, **kwargs) -> None:  # pylint: disable=unused-argument
	pass


LogCaptureHandler.emit = emit  # type: ignore[assignment]


@hookimpl()
def pytest_sessionstart(session):  # pylint: disable=unused-argument
	global GRAFANA_AVAILABLE  # pylint: disable=global-statement
	global MYSQL_BACKEND_AVAILABLE  # pylint: disable=global-statement

	_config.set_config_file("tests/data/default-opsiconfd.conf")
	_config.reload()

	ssl_dir = mkdtemp()
	_config.ssl_ca_key = os.path.join(ssl_dir, "opsi-ca-key.pem")
	_config.ssl_ca_cert = os.path.join(ssl_dir, "opsi-ca-cert.pem")
	_config.ssl_server_key = os.path.join(ssl_dir, "opsiconfd-key.pem")
	_config.ssl_server_cert = os.path.join(ssl_dir, "opsiconfd-cert.pem")

	print("Config:")
	pprint.pprint(_config.items(), width=200)

	BackendManager.default_config = {
		"backendConfigDir": _config.backend_config_dir,
		"dispatchConfigFile": _config.dispatch_config_file,
		"extensionConfigDir": _config.extension_config_dir,
		"extend": True,
	}

	if grafana_is_local() and os.access(GRAFANA_DB, os.W_OK):
		GRAFANA_AVAILABLE = True

	MYSQL_BACKEND_AVAILABLE = (
		"mysql_backend" in BackendManager().backend_getLicensingInfo()["available_modules"]  # pylint: disable=no-member
	)
	with (patch("opsiconfd.ssl.setup_ssl_file_permissions", lambda: None), patch("opsiconfd.ssl.install_ca", lambda x: None)):
		setup_ssl()
	application_setup()


@hookimpl()
def pytest_sessionfinish(session, exitstatus):  # pylint: disable=unused-argument
	ssl_dir = os.path.dirname(_config.ssl_ca_key)
	if os.path.exists(ssl_dir):
		shutil.rmtree(ssl_dir)


@hookimpl()
def pytest_configure(config):
	# https://pypi.org/project/pytest-asyncio
	# When the mode is auto, all discovered async tests are considered
	# asyncio-driven even if they have no @pytest.mark.asyncio marker.
	config.option.asyncio_mode = "auto"
	config.addinivalue_line("markers", "grafana_available: mark test to run only if a local grafana instance is available")
	config.addinivalue_line("markers", "mysql_backend_available: mark test to run only if the mysql backend is available")


@hookimpl()
def pytest_runtest_setup(item):
	grafana_available = GRAFANA_AVAILABLE
	mysql_backend_available = MYSQL_BACKEND_AVAILABLE
	for marker in item.iter_markers():
		if marker.name == "grafana_available" and not grafana_available:
			skip("Grafana not available")
		if marker.name == "mysql_backend_available" and not mysql_backend_available:
			skip("MySQL backend not available")


@fixture(scope="session")
def event_loop():
	"""Create an instance of the default event loop for each test case."""
	loop = asyncio.get_event_loop_policy().new_event_loop()
	yield loop
	loop.close()


@fixture(autouse=True)
def disable_insecure_request_warning():
	warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)


@fixture(autouse=True)
def disable_aioredis_deprecation_warning():
	# aioredis/connection.py:668: DeprecationWarning: There is no current event loop
	warnings.simplefilter("ignore", DeprecationWarning, 668)
