# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
webgui product methods
"""

from typing import List, Optional
from functools import lru_cache
from sqlalchemy import select, text, and_, alias, column
from sqlalchemy.dialects import mysql
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.sql.expression import table

from pydantic import BaseModel # pylint: disable=no-name-in-module
from fastapi import Body, APIRouter, Depends
from fastapi.responses import JSONResponse

from opsiconfd.logging import logger

from .utils import (
	get_mysql,
	get_configserver_id,
	order_by,
	pagination,
	get_depot_of_client,
	common_query_parameters,
	parse_depot_list,
	parse_client_list
)

mysql = get_mysql()
product_router = APIRouter()


def depot_get_product_version(depot, product):
	version = None
	params = {}
	with mysql.session() as session:

		params["depot"] = depot
		params["product"] = product
		where = text("pod.depotId = :depot AND pod.productId = :product")

		query = select(text("CONCAT(pod.productVersion,'-',pod.packageVersion) AS version"))\
			.select_from(text("PRODUCT_ON_DEPOT AS pod"))\
			.where(where)

		result = session.execute(query, params)
		result = result.fetchone()

		if result:
			version = dict(result).get("version")

		return version

@lru_cache(maxsize=1000)
def get_product_type(product_id, product_version, package_version):
	with mysql.session() as session:
		query = select(text("type"))\
			.select_from(text("PRODUCT"))\
			.where(text("productId = :product_id AND productVersion = :product_version AND packageVersion = :package_version"))

		result = session.execute(query, {
			"product_id": product_id,
			"product_version": product_version,
			"package_version": package_version
		})
		res = result.fetchone()
		if not res:
			return None
		return res[0]

def get_product_actions(product, version, package_version):

	params = {}
	params["product"] = product
	params["version"] = version
	params["package_version"] = package_version
	where = text("productId = :product AND productVersion = :version AND packageVersion = :package_version")

	with mysql.session() as session:
		actions = []
		query = select(text("""
			CONCAT_WS(',',
				IF(setupScript <> '','setup', NULL),
				IF(uninstallScript <> '','uninstall',NULL),
				IF(updateScript <> '','update',NULL),
				IF(alwaysScript <> '','always',NULL),
				IF(customScript <> '','custom',NULL),
				IF(onceScript <> '','once',NULL)
			) as actions
		"""))\
			.select_from(text("PRODUCT"))\
			.where(where)

		result = session.execute(query, params)
		result = result.fetchone()

		if result:
			actions = dict(result).get("actions").split(",")
		return actions

def is_product_on_depot(product, version, package_version, depot):

	params = {}
	params["product"] = product
	params["version"] = version
	params["package_version"] = package_version
	params["depot"] = depot

	with mysql.session() as session:
		query = select(text("productId"))\
			.select_from(text("PRODUCT_ON_DEPOT"))\
			.where(text("""
				productId = :product AND
				productVersion = :version AND
				packageVersion = :package_version AND
				depotId = :depot
			"""))

	result = session.execute(query, params)
	result = result.fetchone()

	if result:
		return True
	return False


class ProductsResponse(BaseModel): # pylint: disable=too-few-public-methods
	class Result(BaseModel): # pylint: disable=too-few-public-methods
		class Product(BaseModel): # pylint: disable=too-few-public-methods
			productId: str
			name: str
			description: str
			selectedDepots: List[str]
			depotVersions: List[str]
			depot_version_diff: bool
			selctedClients: List[str]
			clientVersions: List[str]
			client_version_outdated: bool
			actions: List[str]
			productType: str
			installationStatus: str
			actionRequest: str
			actionProgress: str
			actionResult: str

		products: List[Product]
		total: int
	result: Result
	configserver: str


@product_router.get("/api/opsidata/products", response_model=ProductsResponse)
def products(
	commons: dict = Depends(common_query_parameters),
	type: str = "LocalbootProduct",
	selectedClients: List[str] = Depends(parse_client_list),
	selectedDepots: List[str] = Depends(parse_depot_list),
): # pylint: disable=too-many-locals, too-many-branches, too-many-statements, redefined-builtin, invalid-name
	"""
	Get products from selected depots and clients.
	"""

	params = {}
	params["product_type"] = type
	if selectedClients == [] or selectedClients is None:
		params["clients"] = [""]
	else:
		params["clients"] = selectedClients
	if selectedDepots == [] or selectedDepots is None:
		params["depots"] = [get_configserver_id()]
	else:
		params["depots"] = selectedDepots



	with mysql.session() as session:
		where = text("pod.depotId IN :depots AND pod.producttype = :product_type")
		if commons.get("filterQuery"):
			where = and_(
				where,
				text("(pod.productId LIKE :search)")
			)
			params["search"] = f"%{commons['filterQuery']}%"

		query = select(text("""
					pod.productId AS productId,
					p.name AS name,
					p.description AS description,
					GROUP_CONCAT(pod.depotId SEPARATOR ',') AS selectedDepots,
					(
						SELECT GROUP_CONCAT(poc.clientId SEPARATOR ',')
						FROM PRODUCT_ON_CLIENT AS poc WHERE poc.clientId IN :clients AND poc.productId=pod.productId
						ORDER BY poc.clientId
					) AS selectedClients,
					(
						SELECT GROUP_CONCAT(poc.installationStatus SEPARATOR ',')
						FROM PRODUCT_ON_CLIENT AS poc WHERE poc.productId=pod.productId AND poc.clientId IN :clients
					) AS installationStatusDetails,
					(
						SELECT GROUP_CONCAT(poc.actionRequest SEPARATOR ',')
						FROM PRODUCT_ON_CLIENT AS poc WHERE poc.productId=pod.productId AND poc.clientId IN :clients
					) AS actionRequestDetails,
					(
						SELECT GROUP_CONCAT(poc.actionProgress SEPARATOR ',')
						FROM PRODUCT_ON_CLIENT AS poc WHERE poc.productId=pod.productId AND poc.clientId IN :clients
					) AS actionProgressDetails,
					(
						SELECT GROUP_CONCAT(poc.actionResult SEPARATOR ',')
						FROM PRODUCT_ON_CLIENT AS poc WHERE poc.productId=pod.productId AND poc.clientId IN :clients
					) AS actionResultDetails,
					(
						SELECT GROUP_CONCAT(CONCAT(poc.productVersion,'-',poc.packageVersion) SEPARATOR ',')
						FROM PRODUCT_ON_CLIENT AS poc WHERE poc.productId=pod.productId AND poc.clientId IN :clients
						ORDER BY poc.clientId
					) AS clientVersions,
					(
						SELECT CONCAT_WS(',',
							IF(setupScript <> '','setup', NULL),
							IF(uninstallScript <> '','uninstall',NULL),
							IF(updateScript <> '','update',NULL),
							IF(alwaysScript <> '','always',NULL),
							IF(customScript <> '','custom',NULL),
							IF(onceScript <> '','once',NULL)
						)
						FROM PRODUCT AS p
						WHERE p.productId=pod.productId AND
							p.productVersion=pod.productVERSION AND p.packageVersion=pod.packageVersion
					) AS actions,
					GROUP_CONCAT(CONCAT(pod.productVersion,'-',pod.packageVersion) SEPARATOR ',') AS depotVersions,
					pod.productType AS productType
				"""
				))\
				.select_from(text("PRODUCT_ON_DEPOT AS pod")).where(where).group_by(text("pod.productId"))\
				.join(text("PRODUCT AS p"),
					text("""
						p.productId=pod.productId
						 AND p.productVersion=pod.productVersion
						 AND p.packageVersion=pod.packageVersion
					"""
					)
				)

		query = order_by(query, commons)
		query = pagination(query, commons)

		result = session.execute(query, params)
		result = result.fetchall()

		products = [] # pylint: disable=redefined-outer-name
		for row in result:
			if row is not None:
				product = dict(row)
				if product.get("selectedDepots"):
					product["selectedDepots"] = product.get("selectedDepots").split(",")
				if product.get("actions"):
					product["actions"] = product.get("actions").split(",")
				if product.get("depotVersions"):
					product["depotVersions"] = product.get("depotVersions").split(",")
					if any(version != product.get("depotVersions")[0] for version in product.get("depotVersions")):
						product["depot_version_diff"] = True
					else:
						product["depot_version_diff"] = False
				if product.get("selectedClients"):
					product["selectedClients"] = product.get("selectedClients").split(",")
				if product.get("installationStatusDetails"):
					product["installationStatusDetails"] = product.get("installationStatusDetails").split(",")
					if all(value == product.get("installationStatusDetails")[0]  for value in product.get("installationStatusDetails")):
						product["installationStatus"] = product.get("installationStatusDetails")[0]
					else:
						product["installationStatus"] = "mixed"
				else:
					product["installationStatus"] = "not_installed"
					del product["installationStatusDetails"]
				if product.get("actionRequestDetails"):
					product["actionRequestDetails"] = product.get("actionRequestDetails").split(",")
					if all(value == product.get("actionRequestDetails")[0]  for value in product.get("actionRequestDetails")):
						product["actionRequest"] = product.get("actionRequestDetails")[0]
					else:
						product["actionRequest"] = "mixed"
				else:
					product["actionRequest"] = None
					del product["actionRequestDetails"]
				if product.get("actionProgressDetails"):
					product["actionProgressDetails"] = product.get("actionProgressDetails").split(",")
					if all(value == product.get("actionProgressDetails")[0]  for value in product.get("actionProgressDetails")):
						product["actionProgress"] = product.get("actionProgressDetails")[0]
					else:
						product["actionProgress"] = "mixed"
				else:
					product["actionProgress"] = None
					del product["actionProgressDetails"]
				if product.get("actionResultDetails"):
					product["actionResultDetails"] = product.get("actionResultDetails").split(",")
					if all(value == product.get("actionResultDetails")[0]  for value in product.get("actionResultDetails")):
						product["actionResult"] = product.get("actionResultDetails")[0]
					else:
						product["actionResult"] = "mixed"
				else:
					product["actionResult"] = None
					del product["actionResultDetails"]
				if product.get("clientVersions"):
					product["clientVersions"] = product.get("clientVersions").split(",")

					product["client_version_outdated"] = False
					for idx, client in enumerate(product.get("selectedClients")):
						depot = get_depot_of_client(client)
						client_version = product.get("clientVersions")[idx]
						product.get("depotVersions")
						if depot not in product.get("selectedDepots"):
							depot_version = depot_get_product_version(depot, product.get("productId"))
						else:
							depot_version = product.get("depotVersions")[product.get("selectedDepots").index(depot)]
						if client_version != depot_version:
							product["client_version_outdated"] = True
							break

			products.append(product)

		products_on_depots = alias(select(text("*"))\
			.select_from(text("PRODUCT_ON_DEPOT AS pod"))\
			.where(where)\
			.group_by(text("pod.productId"))
		)
		total = session.execute(
			select(text("COUNT(*)")).select_from(products_on_depots),
			params
		).fetchone()[0]

		response_data = {
			"result": {
				"products": products,
				"total": total
			},
			"configserver": get_configserver_id()
		}
		return JSONResponse(response_data)


class PocItem(BaseModel): # pylint: disable=too-few-public-methods
	clientIds: List[str]
	productIds: List[str]
	actionRequest: Optional[str] = None
	actionProgress: Optional[str] = None
	actionResult: Optional[str] = None
	installationStatus: Optional[str] = None

@product_router.patch("/api/opsidata/clients/products")
def save_poduct_on_client(data: PocItem = Body(..., embed=True)): # pylint: disable=too-many-locals, too-many-statements, too-many-branches
	"""
	Save a Product On Client object.
	"""
	status = 200
	error = {}
	result_data = {}
	depot_product_version = {}
	product_actions = {}

	get_product_type.cache_clear()

	for client_id in data.clientIds:
		if not client_id in result_data:
			result_data[client_id] = {}

		depot_id = get_depot_of_client(client_id)

		for product_id in data.productIds:
			if not depot_id in depot_product_version:
				depot_product_version[depot_id] = {}
			if not product_id in depot_product_version[depot_id]:
				depot_product_version[depot_id][product_id] = depot_get_product_version(depot_id, product_id)
			if not depot_product_version[depot_id][product_id]:
				status = 400
				result_data[client_id][product_id] = f"Product '{product_id}' not available on depot '{depot_id}'."
				error[client_id] = result_data[client_id][product_id] + "\n "
				continue

			version = depot_product_version[depot_id][product_id]
			product_version, package_version = version.split("-", 1)

			if not product_id in product_actions:
				product_actions[product_id] = {}
			if not product_version in product_actions[product_id]:
				product_actions[product_id][product_version] = {}
			if not package_version in product_actions[product_id][product_version]:
				product_actions[product_id][product_version][package_version] = get_product_actions(
					product_id, product_version, package_version
				)
			actions = product_actions[product_id][product_version][package_version]

			if data.actionRequest not in actions:
				status = 400
				result_data[client_id][product_id] = (
					f"Action request '{data.actionRequest}' not supported by product '{product_id}' version '{version}'."
				)
				error[client_id] = result_data[client_id][product_id] + "\n "
				continue

			values = {
				"clientId": client_id,
				"productId": product_id,
				"productType": get_product_type(product_id, product_version, package_version),
				"productVersion": product_version,
				"packageVersion": package_version
			}
			for attr in ("actionRequest", "actionProgress", "actionResult", "installationStatus"):
				if getattr(data, attr) is not None:
					values[attr] = getattr(data, attr)

			try:
				with mysql.session() as session:
					stmt = insert(
						table("PRODUCT_ON_CLIENT", *[column(name) for name in values.keys()]) # pylint: disable=consider-iterating-dictionary
					).values(**values).on_duplicate_key_update(**values)
					session.execute(stmt)

				result_data[client_id][product_id] = values
			except Exception as err: # pylint: disable=broad-except
				logger.error("Could not create ProductOnClient: %s", err)
				#error["Error"] = str(err)
				#status = max(status, 500)
				status = 400
				result_data[client_id][product_id] = "Failed to create ProductOnClient."
				error[client_id] = result_data[client_id][product_id] + "\n "
				continue

	return JSONResponse({"status": status, "error": error, "data": result_data})


@product_router.get("/api/opsidata/products/groups")
def get_product_groups(): # pylint: disable=too-many-locals
	"""
	Get all product groups as a tree of groups.
	"""

	params = {}
	where = text("g.`type` = 'ProductGroup'")

	with mysql.session() as session:

		query = select(text("""
			g.parentGroupId AS parent_id,
			g.groupId AS group_id,
			og.objectId AS object_id
		"""))\
		.select_from(text("`GROUP` AS g"))\
		.join(text("OBJECT_TO_GROUP AS og"), text("og.groupType = g.`type` AND og.groupId = g.groupId"), isouter=True)\
		.where(where)

		result = session.execute(query, params)
		result = result.fetchall()
		root_group = {
					"id": "root",
					"type": "ProductGroup",
					"text": "root",
					"parent": None
				}
		all_groups = {}
		for row in result:
			if not row["group_id"] in all_groups:
				all_groups[row["group_id"]] = {
					"id": row["group_id"],
					"type": "ProductGroup",
					"text": row["group_id"],
					"parent": row["parent_id"] or root_group["id"]
				}
			if row["object_id"]:
				if not "children" in all_groups[row["group_id"]]:
					all_groups[row["group_id"]]["children"] = {}
				if row.group_id == row.parent_id:
					if not row["object_id"] in all_groups:
						all_groups[row["object_id"]] = {
							"id": row["object_id"],
							"type": "ProductGroup",
							"text": row["object_id"],
							"parent": row["parent_id"] or root_group["id"]
						}
				else:
					all_groups[row["group_id"]]["children"][row["object_id"]] = {
						"id": row["object_id"],
						"type": "ObjectToGroup",
						"text": row["object_id"],
						"parent": row["group_id"],
					}

		response_data = {
			"result": {
				"groups": all_groups,
			}
		}
		return JSONResponse(response_data)


@product_router.get("/api/opsidata/producticons")
async def product_icons():
	return JSONResponse({
		"result": {"opsi-client-agent": "assets/images/product_icons/opsi-logo.png"}
	})


class Property(BaseModel): # pylint: disable=too-few-public-methods
	productId: str
	propertyId: str
	type: Optional[str] = "UnicodeProductProperty"
	version: Optional[str]
	possibleValues: Optional[List[str]]
	editable: Optional[bool]
	multiValue: Optional[bool]
	description: Optional[str]

class ProductProperiesResponse(BaseModel):
	status: int
	error: dict
	data: List[Property]

@product_router.get("/api/opsidata/products/{productId}/properties", response_model=ProductProperiesResponse)
def product_properties(
	productId: str,
	selectedClients: List[str] = Depends(parse_client_list),
): # pylint: disable=too-many-locals, too-many-branches, too-many-statements, redefined-builtin, invalid-name
	"""
	Get products propertiers.
	"""

	status_code = 200
	error = None
	data = {}
	params = {}
	data["properties"] = []
	params["productId"] = productId
	where = text("pp.productId = :productId")
	depots = set()
	for client in selectedClients:
		depots.add(get_depot_of_client(client))
	if depots:
		params["depots"] = list(depots)
		where = and_(
			where,
			text("(pod.depotId IN :depots)")
		)

	with mysql.session() as session:

		try:
			query = select(text("""
				pp.productId,
				pp.propertyId,
				CONCAT(pp.productVersion,'-',pp.packageVersion) AS version,
				pp.type,
				pp.description,
				pp.multiValue,
				pp.editable
			"""))\
			.select_from(text("PRODUCT_PROPERTY AS pp"))\
			.join(text("PRODUCT_ON_DEPOT AS pod"), text("""
				pod.productId = pp.productId AND
				pod.productVersion = pp.productVersion AND
				pod.packageVersion = pp.packageVersion
			"""))\
			.where(where) # pylint: disable=redefined-outer-name

			result = session.execute(query, params)
			result = result.fetchall()

			for row in result:
				if row is not None:
					property = dict(row)
					data["properties"].append(property)

		except Exception as err: # pylint: disable=broad-except
			logger.error("Could not get properties.")
			logger.error(err)
			error = {"message": str(err), "class": err.__class__.__name__}
			status_code = max(status_code, 500)



	return JSONResponse({"status": status_code, "error": error, "data": data})


class Dependency(BaseModel): # pylint: disable=too-few-public-methods
	productId: str
	productAction: str
	version: str
	requiredProductId: str
	requiredVersion: str
	requiredAction: str
	requiredInstallationStatus: str
	requirementType: str


class ProductDependenciesResponse(BaseModel):
	status: int
	error: dict
	data: List[Dependency]

@product_router.get("/api/opsidata/products/{productId}/dependencies", response_model=ProductDependenciesResponse)
def product_dependencies(
	productId: str,
	selectedClients: List[str] = Depends(parse_client_list),
): # pylint: disable=too-many-locals, too-many-branches, too-many-statements, redefined-builtin, invalid-name
	"""
	Get products dependencies.
	"""

	status_code = 200
	error = None
	data = {}
	params = {}
	data["dependencies"] = []
	params["productId"] = productId
	where = text("pd.productId = :productId")
	depots = set()
	for client in selectedClients:
		depots.add(get_depot_of_client(client))
	if depots:
		params["depots"] = list(depots)
		where = and_(
			where,
			text("(pod.depotId IN :depots)")
		)

	with mysql.session() as session:

		try:
			query = select(text("""
				pd.productId,
				pd.productAction,
				CONCAT(pd.productVersion,'-',pd.packageVersion) AS version,
				pd.requiredProductId,
				CONCAT(pd.requiredProductVersion,'-',pd.requiredPackageVersion) AS requiredVersion,
				pd.requiredAction,
				pd.requiredInstallationStatus,
				pd.requirementType
			"""))\
			.select_from(text("PRODUCT_DEPENDENCY AS pd"))\
			.join(text("PRODUCT_ON_DEPOT AS pod"), text("""
				pod.productId = pd.productId AND
				pod.productVersion = pd.productVersion AND
				pod.packageVersion = pd.packageVersion
			"""))\
			.where(where) # pylint: disable=redefined-outer-name

			result = session.execute(query, params)
			result = result.fetchall()

			for row in result:
				if row is not None:
					dependency = dict(row)
					data["dependencies"].append(dependency)

		except Exception as err: # pylint: disable=broad-except
			logger.error("Could not get dependencies.")
			logger.error(err)
			error = {"message": str(err), "class": err.__class__.__name__}
			status_code = max(status_code, 500)

	return JSONResponse({"status": status_code, "error": error, "data": data})
