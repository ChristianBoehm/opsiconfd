# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0

import sys
import json
import os
from datetime import datetime, timedelta
import socket
import pytest
import urllib3
import requests

# from MySQLdb import _mysql
import MySQLdb

from .utils import clean_redis

TEST_USER = "adminuser"
TEST_PW = "adminuser"
HOSTNAME = socket.gethostname()
LOCAL_IP = socket.gethostbyname(HOSTNAME)
DAYS = 31

@pytest.fixture(name="config")
def fixture_config(monkeypatch):
	monkeypatch.setattr(sys, 'argv', ["opsiconfd"])
	from opsiconfd.config import config # pylint: disable=import-outside-toplevel, redefined-outer-name
	return config


@pytest.fixture(autouse=True)
def disable_request_warning():
	urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def create_depot(opsi_url, depot_name):
	params= [depot_name,None,"file:///var/lib/opsi/depot","smb://172.17.0.101/opsi_depot",None,"file:///var/lib/opsi/repository","webdavs://172.17.0.101:4447/repository"] # pylint: disable=line-too-long

	rpc_request_data = json.dumps({"id": 1, "method": "host_createOpsiDepotserver", "params": params})
	res = requests.post(f"{opsi_url}/rpc", auth=(TEST_USER, TEST_PW), data=rpc_request_data, verify=False)
	result_json = json.loads(res.text)
	print(result_json)

@pytest.fixture(autouse=True)
def create_data(config):

	mysql_host = os.environ.get("MYSQL_HOST")
	if not mysql_host:
		mysql_host = "127.0.0.1"

	db=MySQLdb.connect(host=mysql_host,user="opsi",passwd="opsi",db="opsi") # pylint: disable=invalid-name, c-extension-no-member
	now = datetime.now()

	db.autocommit(True)
	cursor = db.cursor()

	cursor.execute(
		('DELETE FROM PRODUCT_ON_DEPOT WHERE productId like "pytest%";'
		'DELETE FROM PRODUCT_ON_CLIENT WHERE productId like "pytest%";'
		'DELETE FROM PRODUCT WHERE productId like "pytest%";'
		'DELETE FROM HOST WHERE hostId like "pytest%";')
	)


	# Product
	for i in range(0,5):
		sql_string = (f'INSERT INTO HOST (hostId, type, created, lastSeen) VALUES ("pytest-client-{i}.uib.local", '
			f'"OpsiClient", "{now}", "{now}");')
		cursor.execute(sql_string)
		sql_string = ('INSERT INTO PRODUCT (productId, productVersion, packageVersion, type,  name, priority) '
			f'VALUES ("pytest-prod-{i}", "1.0", "1", "LocalbootProduct", "Pytest dummy PRODUCT {i}", 60+{i});')  # pylint: disable=line-too-long
		cursor.execute(sql_string)
		sql_string = f'INSERT INTO PRODUCT_ON_DEPOT (productId, productVersion, packageVersion, depotId, productType) VALUES ("pytest-prod-{i}", "1.0", "1", "{socket.getfqdn()}", "LocalbootProduct");' # pylint: disable=line-too-long
		cursor.execute(sql_string)

	cursor.execute(
		(
			'INSERT INTO PRODUCT (productId, productVersion, packageVersion, type,  name, priority) '
			f'VALUES ("pytest-prod-1", "2.0", "1", "LocalbootProduct", "Pytest dummy PRODUCT 1 version 2", 60);'
		)
	)


	# Host
	cursor.execute(
		(
			'INSERT INTO HOST (hostId, type, created, lastSeen)'
			f'VALUES ("pytest-lost-client.uib.local", "OpsiClient", "{now}", "{now-timedelta(days=DAYS)}");'
			'INSERT INTO HOST (hostId, type, created, lastSeen) '
			f'VALUES ("pytest-lost-client-fp.uib.local", "OpsiClient", "{now}", "{now-timedelta(days=DAYS)}");'
			'INSERT INTO HOST (hostId, type, created, lastSeen) '
			f'VALUES ("pytest-lost-client-fp2.uib.local", "OpsiClient", "{now}", "{now-timedelta(days=DAYS)}");'
		)
	)

	create_depot(config.internal_url, "pytest-test-depot.uib.gmbh")
	create_depot(config.internal_url, "pytest-test-depot2.uib.gmbh")

	# Product on client
	cursor.execute(
		(
			'INSERT INTO PRODUCT_ON_CLIENT '
			'(productId, clientId, productType, installationStatus, actionRequest, actionResult, productVersion, packageVersion, modificationTime) '
			f'VALUES ("pytest-prod-1", "pytest-client-1.uib.local", "LocalbootProduct", "not_installed", "setup", "none", "1.0", 1, "{now}");'
			'INSERT INTO PRODUCT_ON_CLIENT '
			'(productId, clientId, productType, installationStatus, actionRequest, actionResult, productVersion, packageVersion, modificationTime) '
	 		f'VALUES ("pytest-prod-2", "pytest-client-2.uib.local", "LocalbootProduct", "unknown", "none", "failed", "1.0", 1, "{now}");'
			'INSERT INTO PRODUCT_ON_CLIENT '
			'(productId, clientId, productType, installationStatus, actionRequest, actionResult, productVersion, packageVersion, modificationTime) '
	 		f'VALUES ("pytest-prod-3", "pytest-client-3.uib.local", "LocalbootProduct", "installed", "none", "none", "1.0", 1, "{now}");'
			'INSERT INTO PRODUCT_ON_CLIENT '
			'(productId, clientId, productType, installationStatus, actionRequest, actionResult, productVersion, packageVersion, modificationTime) '
			f'VALUES ("pytest-prod-2", "pytest-lost-client-fp.uib.local", "LocalbootProduct", "unknown", "none", "failed", "1.0", 1, "{now}");'
			'INSERT INTO PRODUCT_ON_CLIENT '
			'(productId, clientId, productType, installationStatus, actionRequest, actionResult, productVersion, packageVersion, modificationTime) '
			f'VALUES ("pytest-prod-2", "pytest-lost-client-fp2.uib.local", "LocalbootProduct", "unknown", "none", "failed", "1.0", 1, "{now}");'
			'INSERT INTO PRODUCT_ON_CLIENT '
			'(productId, clientId, productType, installationStatus, actionRequest, actionResult, productVersion, packageVersion, modificationTime) '
			f'VALUES ("pytest-prod-1", "pytest-lost-client-fp2.uib.local", "LocalbootProduct", "not_installed", "setup", "none", "1.0", 1, "{now}");'
		)
	)

	# Product on depot
	cursor.execute((
			'INSERT INTO PRODUCT_ON_DEPOT (productId, productVersion, packageVersion, depotId, productType) '
			f'VALUES ("pytest-prod-1", "1.0", "1", "pytest-test-depot.uib.gmbh", "LocalbootProduct");'
			'INSERT INTO PRODUCT_ON_DEPOT (productId, productVersion, packageVersion, depotId, productType) '
			f'VALUES ("pytest-prod-2", "1.0", "1", "pytest-test-depot.uib.gmbh", "LocalbootProduct");'
			'INSERT INTO PRODUCT_ON_DEPOT (productId, productVersion, packageVersion, depotId, productType) '
			'VALUES ("pytest-prod-1", "2.0", "1", "pytest-test-depot2.uib.gmbh", "LocalbootProduct"); '
			'INSERT INTO PRODUCT_ON_DEPOT (productId, productVersion, packageVersion, depotId, productType) '
			'VALUES ("pytest-prod-2", "1.0", "1", "pytest-test-depot2.uib.gmbh", "LocalbootProduct");'
		)
	)
	cursor.close()

	yield

	cursor = db.cursor()
	cursor.execute(
		(
			'DELETE FROM PRODUCT_ON_DEPOT WHERE productId like "pytest%";'
			'DELETE FROM PRODUCT_ON_CLIENT WHERE productId like "pytest%";'
			'DELETE FROM PRODUCT WHERE productId like "pytest%";'
			'DELETE FROM HOST WHERE hostId like "pytest%";'
		)
	)
	cursor.close()


def test_check_product_status_none(config):

	data = json.dumps({
		'task': 'checkProductStatus',
		'param': {
			'task': 'checkProductStatus',
			'http': False,
			'opsiHost': 'localhost',
			'user': TEST_USER,
			'productIds': ['firefox'],
			'password': TEST_PW,
			'port': 4447
		}
	})

	request = requests.post(f"{config.internal_url}/monitoring", auth=(TEST_USER, TEST_PW), data=data, verify=False) # pylint: disable=line-too-long
	assert request.status_code == 200
	assert request.json() == {'message': "OK: No Problem found for productIds: 'firefox'", 'state': 0}


test_data = [
	(
		["pytest-prod-1"],
		{
			'message': (f"WARNING: \nResult for Depot: '{socket.getfqdn()}':\n"
				"For product 'pytest-prod-1' action set on 2 clients!\n"),
			'state': 1
		}
	),
	(
		["pytest-prod-2"],
		{
			'message': (f"CRITICAL: \nResult for Depot: '{socket.getfqdn()}':\n"
				"For product 'pytest-prod-2' problems found on 3 clients!\n"),
			'state': 2
		}
	),
	(
		["pytest-prod-1","pytest-prod-2"],
		{
			'message': (f"CRITICAL: \nResult for Depot: '{socket.getfqdn()}':\n"
				"For product 'pytest-prod-1' action set on 2 clients!\n"
				"For product 'pytest-prod-2' problems found on 3 clients!\n"),
			'state': 2
		}
	),
	(
		["pytest-prod-3"],
		{
			'message': "OK: No Problem found for productIds: 'pytest-prod-3'",
			'state': 0
		}
	),
	(
		["pytest-prod-1","pytest-prod-2","pytest-prod-3"],
		{
			'message': (f"CRITICAL: \nResult for Depot: '{socket.getfqdn()}':\n"
				"For product 'pytest-prod-1' action set on 2 clients!\n"
				"For product 'pytest-prod-2' problems found on 3 clients!\n"),
			'state': 2
		}
	)
]


@pytest.mark.parametrize("products, expected_result", test_data)
def test_check_product_status_action(config, products, expected_result):

	data = json.dumps({
		'task': 'checkProductStatus',
		'param': {
			'task': 'checkProductStatus',
			'http': False,
			'opsiHost': 'localhost',
			'user': TEST_USER,
			'productIds': products,
			'password': TEST_PW,
			'port': 4447
		}
	})
	request = requests.post(f"{config.internal_url}/monitoring", auth=(TEST_USER, TEST_PW), data=data, verify=False) # pylint: disable=line-too-long
	assert request.status_code == 200
	assert request.json() == expected_result


test_data = [
	(
		"pytest-prod-1",
		{
			'message': ("WARNING: 2 ProductStates for product: 'pytest-prod-1' found; "
				"checking for Version: '1.0' and Package: '1'; ActionRequest set on 2 clients"),
			'state': 1
		}
	),
	(
		"pytest-prod-2",
		{
			'message': ("CRITICAL: 3 ProductStates for product: 'pytest-prod-2' found; "
				"checking for Version: '1.0' and Package: '1'; Problems found on 3 clients"),
			'state': 2
		}
	),
	(
		"pytest-prod-3",
		{
			'message':  ("OK: 1 ProductStates for product: 'pytest-prod-3' found; "
				"checking for Version: '1.0' and Package: '1'"),
			'state': 0
		}
	)
]


@pytest.mark.parametrize("product, expected_result", test_data)
def test_check_product_status_short(config, product, expected_result):

	data = json.dumps({'task': 'checkShortProductStatus', 'param': {'task': 'checkShortProductStatus', 'http': False, 'opsiHost': 'localhost', 'user': TEST_USER, 'productId': product, 'password': TEST_PW, 'port': 4447}}) # pylint: disable=line-too-long

	request = requests.post(f"{config.internal_url}/monitoring", auth=(TEST_USER, TEST_PW), data=data, verify=False) # pylint: disable=line-too-long
	assert request.status_code == 200
	assert request.json() == expected_result


test_data = [
	("pytest-lost-client.uib.local", {
		'message': (f"WARNING: opsi-client pytest-lost-client.uib.local has not been seen, since {DAYS} days. "
			"Please check opsi-client-agent installation on client or perhaps a client that can be deleted. "),
		'state': 1
	}),
	("pytest-lost-client-fp.uib.local", {
		'message': (f"CRITICAL: opsi-client pytest-lost-client-fp.uib.local has not been seen, since {DAYS} days. "
			"Please check opsi-client-agent installation on client or perhaps a client that can be deleted. "
			"Products: 'pytest-prod-2' are in failed state. "),
		'state': 2
	}),
	("pytest-lost-client-fp2.uib.local", {
		'message': (f"CRITICAL: opsi-client pytest-lost-client-fp2.uib.local has not been seen, since {DAYS} days. "
			"Please check opsi-client-agent installation on client or perhaps a client that can be deleted. "
			"Products: 'pytest-prod-2' are in failed state. "
			"Actions set for products: 'pytest-prod-1 (setup)'."),
		'state': 2
	}),
	("pytest-client-1.uib.local", {
		'message': ("WARNING: opsi-client pytest-client-1.uib.local has been seen today. "
			"Actions set for products: 'pytest-prod-1 (setup)'."),
		'state': 1
	}),
	("pytest-client-2.uib.local", {
		'message': ("CRITICAL: opsi-client pytest-client-2.uib.local has been seen today. "
			"Products: 'pytest-prod-2' are in failed state. "),
		'state': 2
	}),
	("pytest-client-3.uib.local", {
		'message': ("OK: opsi-client pytest-client-3.uib.local has been seen today. "
			"No failed products and no actions set for client"),
		'state': 0
	}),
	("this-is-not-a-client.uib.local", {
		'message': "UNKNOWN: opsi-client: 'this-is-not-a-client.uib.local' not found",
		'state': 3
	}),

]
@pytest.mark.parametrize("client, expected_result", test_data)
def test_check_client_status(config, client, expected_result):

	data = json.dumps({
		'task': 'checkClientStatus',
		'param': {
			'task': 'checkClientStatus',
			'http': False,
			'opsiHost': 'localhost',
			'user': TEST_USER,
			'clientId': client,
			'password': TEST_PW,
			'port': 4447
			}
	})

	request = requests.post(f"{config.internal_url}/monitoring", auth=(TEST_USER, TEST_PW), data=data, verify=False) # pylint: disable=line-too-long
	assert request.status_code == 200
	assert request.json() == expected_result


test_data = [
	(
		[socket.getfqdn(), "pytest-test-depot.uib.gmbh" ],
		["pytest-prod-1","pytest-prod-2"],
		[],
		False,
		False,
		{
			"message": f"OK: Syncstate ok for depots {socket.getfqdn()}, pytest-test-depot.uib.gmbh",
			"state": 0
		}
	),
	(
		[socket.getfqdn(), "pytest-test-depot.uib.gmbh" ],
		["pytest-prod-1","pytest-prod-2"],
		[],
		True,
		False,
		{
			"message": f"OK: Syncstate ok for depots {socket.getfqdn()}, pytest-test-depot.uib.gmbh",
			"state": 0
		}
	),
	(
		[socket.getfqdn(), "pytest-test-depot.uib.gmbh" ],
		["pytest-prod-1","pytest-prod-2"],
		[],
		False,
		True,
		{
			"message": f"OK: Syncstate ok for depots {socket.getfqdn()}, pytest-test-depot.uib.gmbh",
			"state": 0
		}
	),
	(
		[socket.getfqdn(), "pytest-test-depot.uib.gmbh" ],
		["pytest-prod-1","pytest-prod-2"],
		[],
		True,
		True,
		{
			"message": f"OK: Syncstate ok for depots {socket.getfqdn()}, pytest-test-depot.uib.gmbh",
			"state": 0
		}
	),
	(
		[socket.getfqdn(), "pytest-test-depot2.uib.gmbh" ],
		["pytest-prod-1","pytest-prod-2"],
		[],
		False,
		False,
		{
			'message': 'WARNING: Differences found for 1 products',
			'state': 1
		}
	),
	(
		[socket.getfqdn(), "pytest-test-depot2.uib.gmbh" ],
		["pytest-prod-1","pytest-prod-2"],
		[],
		False,
		True,
		{
			'message': ("WARNING: Differences found for 1 products:\n"
			f"product 'pytest-prod-1': {socket.getfqdn()} (1.0-1) \n"
			"pytest-test-depot2.uib.gmbh (2.0-1) \n"),
			'state': 1
		}
	),
	(
		[socket.getfqdn(), "pytest-test-depot2.uib.gmbh" ],
		["pytest-prod-1","pytest-prod-2","pytest-prod-3"],
		[],
		True,
		True,
		{
			'message': ("WARNING: Differences found for 2 products:\n"
			f"product 'pytest-prod-1': {socket.getfqdn()} (1.0-1) \n"
			"pytest-test-depot2.uib.gmbh (2.0-1) \n"
			f"product 'pytest-prod-3': {socket.getfqdn()} (1.0-1) \n"
			"pytest-test-depot2.uib.gmbh (not installed) \n"),
			'state': 1
		}
	),
	(
		["pytest-test-depot2.uib.gmbh", socket.getfqdn()],
		["pytest-prod-1","pytest-prod-2","pytest-prod-3"],
		[],
		True,
		True,
		{
			'message': ("WARNING: Differences found for 2 products:\n"
			"product 'pytest-prod-1': pytest-test-depot2.uib.gmbh (2.0-1) \n"
			f"{socket.getfqdn()} (1.0-1) \n"
			"product 'pytest-prod-3': pytest-test-depot2.uib.gmbh (not installed) \n"
			f"{socket.getfqdn()} (1.0-1) \n"),
			'state': 1
		}
	)
	,
	(
		["pytest-test-depot2.uib.gmbh", socket.getfqdn()],
		["pytest-prod-1","pytest-prod-2","pytest-prod-3"],
		["pytest-prod-3"],
		True,
		True,
		{
			'message': ("WARNING: Differences found for 1 products:\n"
			"product 'pytest-prod-1': pytest-test-depot2.uib.gmbh (2.0-1) \n"
			f"{socket.getfqdn()} (1.0-1) \n"),
			'state': 1
		}
	)
]
@pytest.mark.parametrize("depot_ids, product_ids, exclude, strict, verbose, expected_result", test_data)
def test_check_depot_sync_status(config, depot_ids, product_ids, exclude, strict, verbose, expected_result):

	data = json.dumps({
		'task': 'checkDepotSyncStatus',
		'param': {
			'task': 'checkDepotSyncStatus',
			'http': False,
			'opsiHost': 'localhost',
			'user': TEST_USER,
			'depotIds': depot_ids,
			'productIds': product_ids,
			'exclude': exclude,
			'strict': strict,
			'verbose': verbose,
			'password': TEST_PW,
			'port': 4447
			}
	})

	request = requests.post(f"{config.internal_url}/monitoring", auth=(TEST_USER, TEST_PW), data=data, verify=False) # pylint: disable=line-too-long
	assert request.status_code == 200
	assert request.json() == expected_result
