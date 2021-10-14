# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0

"""
test opsiconfd webgui products
"""

import socket
import pytest
import requests

from .utils import ( # pylint: disable=unused-import
	config, clean_redis, database_connection, create_check_data, disable_request_warning,
	ADMIN_USER, ADMIN_PASS
)

FQDN = socket.getfqdn()

depots = sorted([
	FQDN,
	"pytest-test-depot.uib.gmbh",
	"pytest-test-depot2.uib.gmbh"
])
depot_versions = {
	FQDN: "1.0-1",
	"pytest-test-depot.uib.gmbh": "1.0-1",
	"pytest-test-depot2.uib.gmbh": "2.0-1"
}

test_data = [
	(
		{
			'selectedClients': ["pytest-client-1.uib.local", "pytest-client-4.uib.local"],
			'selectedDepots': [FQDN],
			'type': 'LocalbootProduct',
			'pageNumber': 1,
			'perPage': 90,
			'sortBy': 'productId',
			'sortDesc': False,
			'filterQuery': ''
		},
		{
			"result": {
				"products": [
					{
						"productId": "pytest-prod-0",
						"name": "Pytest dummy PRODUCT 0",
						"description": None,
						"selectedDepots": [
							FQDN
						],
						"selectedClients": None,
						"clientVersions": None,
						"client_version_outdated": False,
						"actions": [
							"setup",
							"uninstall",
							"none"
						],
						"depotVersions": [
							"1.0-1"
						],
						"productType": "LocalbootProduct",
						"depot_version_diff": False,
						"installationStatus": "not_installed",
						"actionRequest": None,
						"actionProgress": None,
						"actionResult": None,
						"selected": 0
					},
					{
						"productId": "pytest-prod-1",
						"name": "Pytest dummy PRODUCT 1",
						"description": None,
						"selectedDepots": [
							FQDN
						],
						"selectedClients": [
							"pytest-client-1.uib.local"
						],
						"clientVersions": [
							"1.0-1"
						],
						"actions": [
							"setup",
							"uninstall",
							"none"
						],
						"depotVersions": [
							"1.0-1"
						],
						"productType": "LocalbootProduct",
						"depot_version_diff": False,
						"installationStatus": "not_installed",
						"actionRequest": "setup",
						"actionProgress": None,
						"actionResult": "none",
						"client_version_outdated": False,
						"selected": 0
					},
					{
						"productId": "pytest-prod-2",
						"name": "Pytest dummy PRODUCT 2",
						"description": None,
						"selectedDepots": [
							FQDN
						],
						"selectedClients": None,
						"clientVersions": None,
						"client_version_outdated": False,
						"actions": [
							"setup",
							"uninstall",
							"none"
						],
						"depotVersions": [
							"1.0-1"
						],
						"productType": "LocalbootProduct",
						"depot_version_diff": False,
						"installationStatus": "not_installed",
						"actionRequest": None,
						"actionProgress": None,
						"actionResult": None,
						"selected": 0
					},
					{
						"productId": "pytest-prod-3",
						"name": "Pytest dummy PRODUCT 3",
						"description": None,
						"selectedDepots": [
							FQDN
						],
						"selectedClients": None,
						"clientVersions": None,
						"client_version_outdated": False,
						"actions": [
							"setup",
							"uninstall",
							"none"
						],
						"depotVersions": [
							"1.0-1"
						],
						"productType": "LocalbootProduct",
						"depot_version_diff": False,
						"installationStatus": "not_installed",
						"actionRequest": None,
						"actionProgress": None,
						"actionResult": None,
						"selected": 0
					},
					{
						"productId": "pytest-prod-4",
						"name": "Pytest dummy PRODUCT 4",
						"description": None,
						"selectedDepots": [
							FQDN
						],
						"selectedClients": [
							"pytest-client-1.uib.local",
							"pytest-client-4.uib.local"
						],
						"actionRequestDetails": [
							"none",
							"setup"
						],
						"clientVersions": [
							"1.0-1",
							"1.0-1"
						],
						"client_version_outdated": False,
						"actions": [
							"setup",
							"uninstall",
							"none"
						],
						"depotVersions": [
							"1.0-1"
						],
						"productType": "LocalbootProduct",
						"depot_version_diff": False,
						"installationStatus": "not_installed",
						"actionRequest": "mixed",
						"actionProgress": None,
						"actionResult": "none",
						"client_version_outdated": True,
						"selected": 0
					}
				],
				"total": 5
			},
			"configserver": FQDN
		}
	),
	(
		{
			"type": "LocalbootProduct",
			"pageNumber": 1,
			"perPage": 90,
			"sortBy": "productId",
			"sortDesc": False,
			"filterQuery":"",
		},
		{
			"result": {
				"products": [
					{
						"productId": "pytest-prod-0",
						'name': 'Pytest dummy PRODUCT 0', 'description': None,
						"selectedDepots": [
							FQDN
						],
						"selectedClients": None,
						"installationStatus": "not_installed",
						"actionRequest": None,
						"actionProgress": None,
						"actionResult": None,
						"clientVersions": None,
						"client_version_outdated": False,
						"actions": [
							"setup",
							"uninstall",
							"none"
						],
						"depotVersions": [
							"1.0-1"
						],
						"depot_version_diff": False,
						"productType": "LocalbootProduct",
						"selected": 0
					},
					{
						"productId": "pytest-prod-1",
						'name': 'Pytest dummy PRODUCT 1', 'description': None,
						"selectedDepots": [
							FQDN
						],
						"selectedClients": None,
						"installationStatus": "not_installed",
						"actionRequest": None,
						"actionProgress": None,
						"actionResult": None,
						"clientVersions": None,
						"client_version_outdated": False,
						"actions": [
							"setup",
							"uninstall",
							"none"
						],
						"depotVersions": [
							"1.0-1"
						],
						"depot_version_diff": False,
						"productType": "LocalbootProduct",
						"selected": 0
					},
					{
						"productId": "pytest-prod-2",
						'name': 'Pytest dummy PRODUCT 2', 'description': None,
						"selectedDepots": [
							FQDN
						],
						"selectedClients": None,
						"installationStatus": "not_installed",
						"actionRequest": None,
						"actionProgress": None,
						"actionResult": None,
						"clientVersions": None,
						"client_version_outdated": False,
						"actions": [
							"setup",
							"uninstall",
							"none"
						],
						"depotVersions": [
							"1.0-1"
						],
						"depot_version_diff": False,
						"productType": "LocalbootProduct",
						"selected": 0
					},
					{
						"productId": "pytest-prod-3",
						'name': 'Pytest dummy PRODUCT 3', 'description': None,
						"selectedDepots": [
							FQDN
						],
						"selectedClients": None,
						"installationStatus": "not_installed",
						"actionRequest": None,
						"actionProgress": None,
						"actionResult": None,
						"clientVersions": None,
						"client_version_outdated": False,
						"actions": [
							"setup",
							"uninstall",
							"none"
						],
						"depotVersions": [
							"1.0-1"
						],
						"depot_version_diff": False,
						"productType": "LocalbootProduct",
						"selected": 0
					},
					{
						"productId": "pytest-prod-4",
						'name': 'Pytest dummy PRODUCT 4', 'description': None,
						"selectedDepots": [
							FQDN
						],
						"selectedClients": None,
						"installationStatus": "not_installed",
						"actionRequest": None,
						"actionProgress": None,
						"actionResult": None,
						"clientVersions": None,
						"client_version_outdated": False,
						"actions": [
							"setup",
							"uninstall",
							"none"
						],
						"depotVersions": [
							"1.0-1"
						],
						"depot_version_diff": False,
						"productType": "LocalbootProduct",
						"selected": 0
					}
				],
				"total": 5
			},
			"configserver": FQDN
		}
	),
	(
		{
			"selectedClients": ["pytest-client-1.uib.local", "pytest-client-4.uib.local"],
    		"selectedDepots": sorted([FQDN, "test-depot.uib.gmbh"]),
			"type": "LocalbootProduct",
			"pageNumber": 1,
			"perPage": 2,
			"sortBy": "productId",
			"sortDesc": False,
			"filterQuery":"",
		},
		{
			"result": {
				"products": [
					{
						"productId": "pytest-prod-0",
						'name': 'Pytest dummy PRODUCT 0', 'description': None,
						"selectedDepots": [
							FQDN
						],
						"selectedClients": None,
						"installationStatus": "not_installed",
						"actionRequest": None,
						"actionProgress": None,
						"actionResult": None,
						"clientVersions": None,
						"client_version_outdated": False,
						"actions": [
							"setup",
							"uninstall",
							"none"
						],
						"depotVersions": [
							"1.0-1"
						],
						"depot_version_diff": False,
						"productType": "LocalbootProduct",
						"selected": 0
					},
					{
						"productId": "pytest-prod-1",
						'name': 'Pytest dummy PRODUCT 1', 'description': None,
						"selectedDepots": [
							FQDN
						],
						"selectedClients": [
							"pytest-client-1.uib.local"
						],
						"installationStatus": "not_installed",
						"actionRequest": "setup",
						"actionProgress": None,
						"actionResult": "none",
						"clientVersions": [
							"1.0-1"
						],
						"client_version_outdated": False,
						"actions": [
							"setup",
							"uninstall",
							"none"
						],
						"depotVersions": [
							"1.0-1"
						],
						"depot_version_diff": False,
						"productType": "LocalbootProduct",
						"selected": 0
					}
				],
				"total": 5
			},
			"configserver": FQDN
		}
	),
	(
		{
			"selectedClients": ["pytest-client-1.uib.local", "pytest-client-4.uib.local"],
    		"selectedDepots": sorted([FQDN, "test-depot.uib.gmbh"]),
			"type": "LocalbootProduct",
			"pageNumber": 2,
			"perPage": 2,
			"sortBy": "productId",
			"sortDesc": False,
			"filterQuery":"",
		},
		{
			"result": {
				"products": [
					{
						"productId": "pytest-prod-2",
						'name': 'Pytest dummy PRODUCT 2', 'description': None,
						"selectedDepots": [
							FQDN
						],
						"selectedClients": None,
						"installationStatus": "not_installed",
						"actionRequest": None,
						"actionProgress": None,
						"actionResult": None,
						"clientVersions": None,
						"client_version_outdated": False,
						"actions": [
							"setup",
							"uninstall",
							"none"
						],
						"depotVersions": [
							"1.0-1"
						],
						"depot_version_diff": False,
						"productType": "LocalbootProduct",
						"selected": 0
					},
					{
						"productId": "pytest-prod-3",
						'name': 'Pytest dummy PRODUCT 3', 'description': None,
						"selectedDepots": [
							FQDN
						],
						"selectedClients": None,
						"installationStatus": "not_installed",
						"actionRequest": None,
						"actionProgress": None,
						"actionResult": None,
						"clientVersions": None,
						"client_version_outdated": False,
						"actions": [
							"setup",
							"uninstall",
							"none"
						],
						"depotVersions": [
							"1.0-1"
						],
						"depot_version_diff": False,
						"productType": "LocalbootProduct",
						"selected": 0
					}
				],
				"total": 5
			},
			"configserver": FQDN
		}
	),
	(
		{
			"selectedClients": ["pytest-client-1.uib.local", "pytest-client-4.uib.local"],
			"selectedDepots": ["pytest-test-depot.uib.gmbh", "pytest-test-depot2.uib.gmbh"],
			"type": "LocalbootProduct",
			"pageNumber":1,
			"perPage":3,
			"sortBy":"productId",
			"sortDesc":False,
			"filterQuery":""
		},
		{
			"result": {
				"products": [
					{
						"productId": "pytest-prod-1",
						"name": "Pytest dummy PRODUCT 1",
						"description": None,
						"selectedDepots": [
							"pytest-test-depot.uib.gmbh",
							"pytest-test-depot2.uib.gmbh"
						],
						"selectedClients": [
							"pytest-client-1.uib.local"
						],
						"clientVersions": [
							"1.0-1"
						],
						"actions": [
							"setup",
							"uninstall",
							"none"
						],
						"depotVersions": [
							"1.0-1",
							"2.0-1"
						],
						"productType": "LocalbootProduct",
						"depot_version_diff": True,
						"installationStatus": "not_installed",
						"actionRequest": "setup",
						"actionProgress": None,
						"actionResult": "none",
						"client_version_outdated": False,
						"selected": 0
					},
					{
						"productId": "pytest-prod-2",
						"name": "Pytest dummy PRODUCT 2",
						"description": None,
						"selectedDepots": [
							"pytest-test-depot.uib.gmbh",
							"pytest-test-depot2.uib.gmbh"
						],
						"selectedClients": None,
						"clientVersions": None,
						"client_version_outdated": False,
						"actions": [
							"setup",
							"uninstall",
							"none"
						],
						"depotVersions": [
							"1.0-1",
							"1.0-1"
						],
						"productType": "LocalbootProduct",
						"depot_version_diff": False,
						"installationStatus": "not_installed",
						"actionRequest": None,
						"actionProgress": None,
						"actionResult": None,
						"selected": 0
					},
					{
						"productId": "pytest-prod-3",
						"name": "Pytest dummy PRODUCT 3",
						"description": None,
						"selectedDepots": [
							"pytest-test-depot.uib.gmbh",
							"pytest-test-depot2.uib.gmbh"
						],
						"selectedClients": None,
						"clientVersions": None,
						"client_version_outdated": False,
						"actions": [
							"setup",
							"uninstall",
							"none"
						],
						"depotVersions": [
							"1.0-1",
							"1.0-1"
						],
						"productType": "LocalbootProduct",
						"depot_version_diff": False,
						"installationStatus": "not_installed",
						"actionRequest": None,
						"actionProgress": None,
						"actionResult": None,
						"selected": 0
					}
				],
				"total": 4
			},
			"configserver": FQDN
		}
	),
	(
		{
			"selectedClients": ["pytest-client-1.uib.local", "pytest-client-4.uib.local"],
			"selectedDepots": [FQDN, "pytest-test-depot.uib.gmbh","pytest-test-depot2.uib.gmbh"],
			"type": "LocalbootProduct",
			"pageNumber":1,
			"perPage":3,
			"sortBy":"productId",
			"sortDesc":False,
			"filterQuery":"prod-1"
		},
		{
			"result": {
				"products": [
					{
						"productId": "pytest-prod-1",
						'name': 'Pytest dummy PRODUCT 1', 'description': None,
						"selectedDepots": depots,
						"selectedClients": [
							"pytest-client-1.uib.local"
						],
						"installationStatus": "not_installed",
						"actionRequest": "setup",
						"actionProgress": None,
						"actionResult": "none",
						"clientVersions": [
							"1.0-1"
						],
						"client_version_outdated": False,
						"actions": [
							"setup",
							"uninstall",
							"none"
						],
						"depotVersions": [
							depot_versions.get(depots[0]),
							depot_versions.get(depots[1]),
							depot_versions.get(depots[2])
						],
						"depot_version_diff": True,
						"productType": "LocalbootProduct",
						"selected": 0
					}
				],
				"total": 1
			},
			"configserver": FQDN
		}
	),
	(
		{
			"selectedClients": ["pytest-client-1.uib.local", "pytest-client-4.uib.local"],
			"selectedDepots": [FQDN, "pytest-test-depot.uib.gmbh","pytest-test-depot2.uib.gmbh"],
			"type": "LocalbootProduct",
			"pageNumber":1,
			"perPage":3,
			"sortBy":"productId",
			"sortDesc":False,
			"filterQuery":"ffff"
		},
		{
			"result": {
				"products": [],
				"total": 0
			},
			"configserver": FQDN
		}
	)
]

@pytest.mark.parametrize("input_data, expected_result", test_data)
@pytest.mark.asyncio
async def test_products(config, input_data, expected_result): # pylint: disable=too-many-arguments,redefined-outer-name
	res = requests.get(
		f"{config.external_url}/webgui/api/opsidata/products", auth=(ADMIN_USER, ADMIN_PASS), verify=False, params=input_data
	)
	assert res.status_code == 200
	assert res.json() == expected_result
