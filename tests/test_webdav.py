# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
webdav tests
"""

import os
import random
import shutil
from unittest.mock import patch

import pytest

from opsiconfd.config import FQDN
from opsiconfd.application.main import app
from opsiconfd.application.webdav import IgnoreCaseFilesystemProvider, webdav_setup

from .utils import app, backend, config, clean_redis, test_client, ADMIN_USER, ADMIN_PASS  # pylint: disable=unused-import


def test_webdav_setup():
	webdav_setup(app)


def test_webdav_upload_download_delete_with_special_chars(test_client):  # pylint: disable=redefined-outer-name
	test_client.auth = (ADMIN_USER, ADMIN_PASS)
	size = 1 * 1024 * 1024
	rand_bytes = bytearray(random.getrandbits(8) for _ in range(size))
	headers = {"Content-Type": "binary/octet-stream", "Content-Length": str(size)}
	filename = "陰陽_üß.bin"

	url = f"/repository/{filename}"
	res = test_client.put(url=url, headers=headers, data=rand_bytes)
	res.raise_for_status()

	assert os.path.exists(os.path.join("/var/lib/opsi/repository", filename))

	res = test_client.get(url=url)
	res.raise_for_status()
	assert rand_bytes == res.content

	res = test_client.delete(url=url)
	res.raise_for_status()


def test_webdav_auth(test_client):  # pylint: disable=redefined-outer-name
	url = "/repository/test_file.bin"
	res = test_client.get(url=url)
	assert res.status_code == 401


def test_client_permission(test_client):  # pylint: disable=redefined-outer-name
	client_id = "webdavtest.uib.local"
	client_key = "af521906af3c4666bed30a1774639ff8"
	rpc = {"id": 1, "method": "host_createOpsiClient", "params": [client_id, client_key]}
	res = test_client.post("/rpc", json=rpc, auth=(ADMIN_USER, ADMIN_PASS))
	assert res.status_code == 200
	res = res.json()
	assert res.get("error") is None
	test_client.reset_cookies()

	size = 1024
	data = bytearray(random.getrandbits(8) for _ in range(size))
	headers = {"Content-Type": "binary/octet-stream", "Content-Length": str(size)}
	for path in ("workbench", "repository", "depot"):
		url = f"/{path}/test_file_client.bin"

		res = test_client.put(url=url, data=data, headers=headers, auth=(ADMIN_USER, ADMIN_PASS))
		assert res.status_code in (201, 204)
		test_client.reset_cookies()

		res = test_client.put(url=url, auth=(client_id, client_key))
		assert res.status_code == 401

		res = test_client.get(url=url, auth=(client_id, client_key))
		assert res.status_code == 200 if path == "depot" else 401

		res = test_client.delete(url=url, auth=(client_id, client_key))
		assert res.status_code == 401
		test_client.reset_cookies()

		res = test_client.delete(url=url, auth=(ADMIN_USER, ADMIN_PASS))
		assert res.status_code == 204

		test_client.post(url="/admin/unblock-all")
		test_client.reset_cookies()

	rpc = {"id": 1, "method": "host_delete", "params": [client_id]}
	res = test_client.post("/rpc", json=rpc, auth=(ADMIN_USER, ADMIN_PASS))
	assert res.status_code == 200


@pytest.mark.parametrize(
	"filename, path, exception",
	(
		("/filename.txt", "/filename.TXT", None),
		("/outside.root", "../outside.root", RuntimeError),
		("/tEsT/TesT2/fileNaME1.TXt", "/test/test2/filename1.txt", None),
		("/Test/test/filename1.bin", "/test/test/filename1.bin", None),
		("/tEßT/TäsT2/陰陽_Üß.TXt", "/tEßT/täsT2/陰陽_üß.txt", None),
	),
)
def test_webdav_ignore_case_download(test_client, filename, path, exception):  # pylint: disable=redefined-outer-name
	test_client.auth = (ADMIN_USER, ADMIN_PASS)
	base_dir = "/var/lib/opsi/depot"
	directory, filename = filename.rsplit("/", 1)
	directory = directory.strip("/")
	abs_dir = os.path.join(base_dir, directory)
	abs_filename = os.path.join(abs_dir, filename)

	prov = IgnoreCaseFilesystemProvider(base_dir)

	if directory:
		os.makedirs(abs_dir)
	try:
		with open(abs_filename, "w", encoding="utf-8") as file:
			file.write(filename)

		if exception:
			with pytest.raises(exception):
				prov._loc_to_file_path(path)  # pylint: disable=protected-access
		else:
			file_path = prov._loc_to_file_path(path)  # pylint: disable=protected-access
			assert file_path == f"{base_dir}/{directory + '/' if directory else ''}{filename}"

		url = f"/depot/{path}"
		res = test_client.get(url=url, stream=True)
		if exception:
			assert res.status_code == 404
		else:
			res.raise_for_status()
			assert res.raw.read().decode("utf-8") == filename
	finally:
		if directory:
			shutil.rmtree(os.path.join(base_dir, directory.split("/")[0]))
		else:
			os.unlink(abs_filename)


def test_webdav_virtual_folder(test_client):  # pylint: disable=redefined-outer-name
	test_client.auth = (ADMIN_USER, ADMIN_PASS)
	res = test_client.get(url="/webdav")
	assert res.status_code == 200

	assert "/webdav/boot" in res.text
	assert "/webdav/depot" in res.text
	assert "/webdav/public" in res.text
	assert "/webdav/repository" in res.text
	assert "/webdav/workbench" in res.text


def test_webdav_setup_exception(backend):  # pylint: disable=redefined-outer-name
	host = backend.host_getObjects(type="OpsiDepotserver", id=FQDN)[0]  # pylint: disable=no-member
	repo_url = host.getRepositoryLocalUrl()
	depot_url = host.getDepotLocalUrl()
	workbench_url = host.getWorkbenchLocalUrl()
	with patch("opsiconfd.application.webdav.PUBLIC_FOLDER", "/file/not/found"):
		try:
			host.setRepositoryLocalUrl("file:///not/found")
			host.setDepotLocalUrl("file:///not/found")
			host.setWorkbenchLocalUrl("file:///not/found")
			webdav_setup(app)
		finally:
			host.setRepositoryLocalUrl(repo_url)
			host.setDepotLocalUrl(depot_url)
			host.setWorkbenchLocalUrl(workbench_url)
