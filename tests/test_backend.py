# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
webdav tests
"""

from opsiconfd import set_contextvars_from_contex
from opsiconfd.backend import (
	get_session, get_user_store, get_option_store,
	get_client_backend, get_backend_interface, get_server_role
)
from .utils import (  # pylint: disable=unused-import
	clean_redis, config, get_config
)


def test_get_session(test_client):
	test_client.get("/")
	set_contextvars_from_contex(None)
	set_contextvars_from_contex(test_client.context)
	assert get_session()
	assert get_user_store()
	get_option_store()


def test_get_client_backend(test_client):
	test_client.get("/")
	set_contextvars_from_contex(test_client.context)
	backend = get_client_backend()
	assert backend
	idents = backend.host_getIdents()  # pylint: disable=no-member
	assert len(idents) > 0


def test_get_backend_interface():
	assert len(get_backend_interface()) > 50


def test_get_server_role(tmp_path):
	dispatch_config_file = tmp_path / "dispatch_mysql.conf"
	dispatch_config_file.write_text(".*         : mysql\n")
	with get_config({"dispatch_config_file": str(dispatch_config_file)}):
		assert get_server_role() == "config"

	dispatch_config_file = tmp_path / "dispatch_jsonrpc.conf"
	dispatch_config_file.write_text(".*         : jsonrpc\n")
	with get_config({"dispatch_config_file": str(dispatch_config_file)}):
		assert get_server_role() == "depot"