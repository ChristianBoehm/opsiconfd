# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
opsiconfd.backend.mysql.schema
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import TYPE_CHECKING, Callable, List, Literal

from opsiconfd.logging import logger

from .cleanup import remove_orphans_config_value, remove_orphans_product_property_value

if TYPE_CHECKING:
	from . import MySQLConnection, Session


CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS `CONFIG` (
	`configId` varchar(200) NOT NULL,
	`type` varchar(30) NOT NULL,
	`description` varchar(256) DEFAULT NULL,
	`multiValue` tinyint(1) NOT NULL,
	`editable` tinyint(1) NOT NULL,
	PRIMARY KEY (`configId`),
	KEY `index_config_type` (`type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `CONFIG_STATE` (
	`configId` varchar(200) NOT NULL,
	`objectId` varchar(255) NOT NULL,
	`values` text,
	PRIMARY KEY (`configId`,`objectId`),
	KEY `index_config_state_configId` (`configId`),
	KEY `index_config_state_objectId` (`objectId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `CONFIG_VALUE` (
	`config_value_id` int(11) NOT NULL AUTO_INCREMENT,
	`configId` varchar(200) NOT NULL,
	`value` text,
	`isDefault` tinyint(1) DEFAULT NULL,
	PRIMARY KEY (`config_value_id`),
	KEY `configId` (`configId`),
	FOREIGN KEY (`configId`) REFERENCES `CONFIG` (`configId`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=526 DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `GROUP` (
	`type` varchar(30) NOT NULL,
	`groupId` varchar(255) NOT NULL,
	`parentGroupId` varchar(255) DEFAULT NULL,
	`description` varchar(100) DEFAULT NULL,
	`notes` varchar(500) DEFAULT NULL,
	PRIMARY KEY (`type`,`groupId`),
	KEY `index_group_parentGroupId` (`parentGroupId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `HOST` (
	`hostId` varchar(255) NOT NULL,
	`type` varchar(30) DEFAULT NULL,
	`description` varchar(100) DEFAULT NULL,
	`notes` varchar(500) DEFAULT NULL,
	`hardwareAddress` varchar(17) DEFAULT NULL,
	`ipAddress` varchar(255) DEFAULT NULL,
	`inventoryNumber` varchar(64) DEFAULT NULL,
	`created` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
	`lastSeen` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
	`opsiHostKey` varchar(32) DEFAULT NULL,
	`oneTimePassword` varchar(32) DEFAULT NULL,
	`maxBandwidth` int(11) DEFAULT NULL,
	`depotLocalUrl` varchar(128) DEFAULT NULL,
	`depotRemoteUrl` varchar(255) DEFAULT NULL,
	`depotWebdavUrl` varchar(255) DEFAULT NULL,
	`repositoryLocalUrl` varchar(128) DEFAULT NULL,
	`repositoryRemoteUrl` varchar(255) DEFAULT NULL,
	`networkAddress` varchar(31) DEFAULT NULL,
	`isMasterDepot` tinyint(1) DEFAULT NULL,
	`masterDepotId` varchar(255) DEFAULT NULL,
	`workbenchLocalUrl` varchar(128) DEFAULT NULL,
	`workbenchRemoteUrl` varchar(255) DEFAULT NULL,
	PRIMARY KEY (`hostId`),
	KEY `index_host_type` (`type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `LICENSE_CONTRACT` (
	`licenseContractId` varchar(100) NOT NULL,
	`type` varchar(30) NOT NULL,
	`description` varchar(100) DEFAULT NULL,
	`notes` varchar(1000) DEFAULT NULL,
	`partner` varchar(100) DEFAULT NULL,
	`conclusionDate` timestamp NULL DEFAULT NULL,
	`notificationDate` timestamp NULL DEFAULT NULL,
	`expirationDate` timestamp NULL DEFAULT NULL,
	PRIMARY KEY (`licenseContractId`),
	KEY `index_license_contract_type` (`type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `LICENSE_POOL` (
	`licensePoolId` varchar(100) NOT NULL,
	`type` varchar(30) NOT NULL,
	`description` varchar(200) DEFAULT NULL,
	PRIMARY KEY (`licensePoolId`),
	KEY `index_license_pool_type` (`type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `OBJECT_TO_GROUP` (
	`groupType` varchar(30) NOT NULL,
	`groupId` varchar(255) NOT NULL,
	`objectId` varchar(255) NOT NULL,
	PRIMARY KEY (`groupType`,`groupId`,`objectId`),
	KEY `groupType` (`groupType`,`groupId`),
	KEY `index_object_to_group_objectId` (`objectId`),
	FOREIGN KEY (`groupType`, `groupId`) REFERENCES `GROUP` (`type`, `groupId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `OPSI_SCHEMA` (
	`version` int(11) NOT NULL,
	`updateStarted` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
	`updateEnded` timestamp NULL DEFAULT NULL,
	PRIMARY KEY (`version`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `PRODUCT` (
	`productId` varchar(255) NOT NULL,
	`productVersion` varchar(32) NOT NULL,
	`packageVersion` varchar(16) NOT NULL,
	`type` varchar(32) NOT NULL,
	`name` varchar(128) NOT NULL,
	`licenseRequired` varchar(50) DEFAULT NULL,
	`setupScript` varchar(50) DEFAULT NULL,
	`uninstallScript` varchar(50) DEFAULT NULL,
	`updateScript` varchar(50) DEFAULT NULL,
	`alwaysScript` varchar(50) DEFAULT NULL,
	`onceScript` varchar(50) DEFAULT NULL,
	`customScript` varchar(50) DEFAULT NULL,
	`userLoginScript` varchar(50) DEFAULT NULL,
	`priority` int(11) DEFAULT NULL,
	`description` text,
	`advice` text,
	`pxeConfigTemplate` varchar(50) DEFAULT NULL,
	`changelog` text,
	PRIMARY KEY (`productId`,`productVersion`,`packageVersion`),
	KEY `index_product_type` (`type`),
	KEY `index_productId` (`productId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `PRODUCT_DEPENDENCY` (
	`productId` varchar(255) NOT NULL,
	`productVersion` varchar(32) NOT NULL,
	`packageVersion` varchar(16) NOT NULL,
	`productAction` varchar(16) NOT NULL,
	`requiredProductId` varchar(255) NOT NULL,
	`requiredProductVersion` varchar(32) DEFAULT NULL,
	`requiredPackageVersion` varchar(16) DEFAULT NULL,
	`requiredAction` varchar(16) DEFAULT NULL,
	`requiredInstallationStatus` varchar(16) DEFAULT NULL,
	`requirementType` varchar(16) DEFAULT NULL,
	PRIMARY KEY (`productId`,`productVersion`,`packageVersion`,`productAction`,`requiredProductId`),
	FOREIGN KEY (`productId`, `productVersion`, `packageVersion`) REFERENCES `PRODUCT` (`productId`, `productVersion`, `packageVersion`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `PRODUCT_ID_TO_LICENSE_POOL` (
	`licensePoolId` varchar(100) NOT NULL,
	`productId` varchar(255) NOT NULL,
	PRIMARY KEY (`licensePoolId`,`productId`),
	FOREIGN KEY (`licensePoolId`) REFERENCES `LICENSE_POOL` (`licensePoolId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `PRODUCT_ON_CLIENT` (
	`productId` varchar(255) NOT NULL,
	`clientId` varchar(255) NOT NULL,
	`productType` varchar(16) NOT NULL,
	`targetConfiguration` varchar(16) DEFAULT NULL,
	`installationStatus` varchar(16) DEFAULT NULL,
	`actionRequest` varchar(16) DEFAULT NULL,
	`actionProgress` varchar(255) DEFAULT NULL,
	`actionResult` varchar(16) DEFAULT NULL,
	`lastAction` varchar(16) DEFAULT NULL,
	`productVersion` varchar(32) DEFAULT NULL,
	`packageVersion` varchar(16) DEFAULT NULL,
	`modificationTime` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
	PRIMARY KEY (`productId`,`clientId`),
	KEY `FK_PRODUCT_ON_CLIENT_HOST` (`clientId`),
	FOREIGN KEY (`clientId`) REFERENCES `HOST` (`hostId`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `PRODUCT_ON_DEPOT` (
	`productId` varchar(255) NOT NULL,
	`productVersion` varchar(32) NOT NULL,
	`packageVersion` varchar(16) NOT NULL,
	`depotId` varchar(255) NOT NULL,
	`productType` varchar(16) NOT NULL,
	`locked` tinyint(1) DEFAULT NULL,
	PRIMARY KEY (`productId`,`depotId`),
	KEY `productId` (`productId`,`productVersion`,`packageVersion`),
	KEY `depotId` (`depotId`),
	KEY `index_product_on_depot_productType` (`productType`),
	FOREIGN KEY (`depotId`) REFERENCES `HOST` (`hostId`) ON DELETE CASCADE ON UPDATE CASCADE,
	FOREIGN KEY (`productId`, `productVersion`, `packageVersion`) REFERENCES `PRODUCT` (`productId`, `productVersion`, `packageVersion`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `PRODUCT_PROPERTY` (
	`productId` varchar(255) NOT NULL,
	`productVersion` varchar(32) NOT NULL,
	`packageVersion` varchar(16) NOT NULL,
	`propertyId` varchar(200) NOT NULL,
	`type` varchar(30) NOT NULL,
	`description` text,
	`multiValue` tinyint(1) NOT NULL,
	`editable` tinyint(1) NOT NULL,
	PRIMARY KEY (`productId`,`productVersion`,`packageVersion`,`propertyId`),
	KEY `index_product_property_type` (`type`),
	FOREIGN KEY (`productId`, `productVersion`, `packageVersion`) REFERENCES `PRODUCT` (`productId`, `productVersion`, `packageVersion`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `PRODUCT_PROPERTY_STATE` (
	`productId` varchar(255) NOT NULL,
	`propertyId` varchar(200) NOT NULL,
	`objectId` varchar(255) NOT NULL,
	`values` text,
	PRIMARY KEY (`productId`,`propertyId`,`objectId`),
	KEY `index_product_property_state_objectId` (`objectId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `PRODUCT_PROPERTY_VALUE` (
	`product_property_id` int(11) NOT NULL AUTO_INCREMENT,
	`productId` varchar(255) NOT NULL,
	`productVersion` varchar(32) NOT NULL,
	`packageVersion` varchar(16) NOT NULL,
	`propertyId` varchar(200) NOT NULL,
	`value` text,
	`isDefault` tinyint(1) DEFAULT NULL,
	PRIMARY KEY (`product_property_id`),
	KEY `productId` (`productId`,`productVersion`,`packageVersion`,`propertyId`),
	KEY `index_product_property_value` (`productId`,`propertyId`,`productVersion`,`packageVersion`),
	FOREIGN KEY (`productId`, `productVersion`, `packageVersion`, `propertyId`) REFERENCES `PRODUCT_PROPERTY` (`productId`, `productVersion`, `packageVersion`, `propertyId`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=11237 DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `SOFTWARE` (
	`name` varchar(100) NOT NULL,
	`version` varchar(100) NOT NULL,
	`subVersion` varchar(100) NOT NULL,
	`language` varchar(10) NOT NULL,
	`architecture` varchar(3) NOT NULL,
	`windowsSoftwareId` varchar(100) DEFAULT NULL,
	`windowsDisplayName` varchar(100) DEFAULT NULL,
	`windowsDisplayVersion` varchar(100) DEFAULT NULL,
	`type` varchar(30) NOT NULL,
	`installSize` bigint(20) DEFAULT NULL,
	PRIMARY KEY (`name`,`version`,`subVersion`,`language`,`architecture`),
	KEY `index_software_windowsSoftwareId` (`windowsSoftwareId`),
	KEY `index_software_type` (`type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `SOFTWARE_CONFIG` (
	`clientId` varchar(255) NOT NULL,
	`name` varchar(100) NOT NULL,
	`version` varchar(100) NOT NULL,
	`subVersion` varchar(100) NOT NULL,
	`language` varchar(10) NOT NULL,
	`architecture` varchar(3) NOT NULL,
	`uninstallString` varchar(200) DEFAULT NULL,
	`binaryName` varchar(100) DEFAULT NULL,
	`firstseen` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
	`lastseen` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
	`state` tinyint(4) NOT NULL,
	`usageFrequency` int(11) NOT NULL DEFAULT '-1',
	`lastUsed` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
	`licenseKey` varchar(1024) DEFAULT NULL,
	PRIMARY KEY (`clientId`,`name`,`version`,`subVersion`,`language`,`architecture`),
	KEY `index_software_config_clientId` (`clientId`),
	KEY `index_software_config_nvsla` (`name`,`version`,`subVersion`,`language`,`architecture`),
	FOREIGN KEY (`clientId`) REFERENCES `HOST` (`hostId`) ON DELETE CASCADE ON UPDATE CASCADE,
	FOREIGN KEY (`name`, `version`, `subVersion`, `language`, `architecture`) REFERENCES `SOFTWARE` (`name`, `version`, `subVersion`, `language`, `architecture`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `SOFTWARE_LICENSE` (
	`softwareLicenseId` varchar(100) NOT NULL,
	`licenseContractId` varchar(100) NOT NULL,
	`type` varchar(30) NOT NULL,
	`boundToHost` varchar(255) DEFAULT NULL,
	`maxInstallations` int(11) DEFAULT NULL,
	`expirationDate` timestamp NULL DEFAULT NULL,
	PRIMARY KEY (`softwareLicenseId`),
	KEY `licenseContractId` (`licenseContractId`),
	KEY `index_software_license_type` (`type`),
	KEY `index_software_license_boundToHost` (`boundToHost`),
	FOREIGN KEY (`licenseContractId`) REFERENCES `LICENSE_CONTRACT` (`licenseContractId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `SOFTWARE_LICENSE_TO_LICENSE_POOL` (
	`softwareLicenseId` varchar(100) NOT NULL,
	`licensePoolId` varchar(100) NOT NULL,
	`licenseKey` varchar(1024) DEFAULT NULL,
	PRIMARY KEY (`softwareLicenseId`,`licensePoolId`),
	KEY `licensePoolId` (`licensePoolId`),
	FOREIGN KEY (`softwareLicenseId`) REFERENCES `SOFTWARE_LICENSE` (`softwareLicenseId`),
	FOREIGN KEY (`licensePoolId`) REFERENCES `LICENSE_POOL` (`licensePoolId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `WINDOWS_SOFTWARE_ID_TO_PRODUCT` (
	`windowsSoftwareId` varchar(100) NOT NULL,
	`productId` varchar(255) NOT NULL,
	PRIMARY KEY (`windowsSoftwareId`,`productId`),
	KEY `index_windows_software_id_to_product_productId` (`productId`),
	KEY `index_productId` (`productId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `LICENSE_ON_CLIENT` (
	`softwareLicenseId` varchar(100) NOT NULL,
	`licensePoolId` varchar(100) NOT NULL,
	`clientId` varchar(255) NOT NULL,
	`licenseKey` varchar(1024) DEFAULT NULL,
	`notes` varchar(1024) DEFAULT NULL,
	PRIMARY KEY (`softwareLicenseId`,`licensePoolId`,`clientId`),
	KEY `softwareLicenseId` (`softwareLicenseId`,`licensePoolId`),
	KEY `index_license_on_client_clientId` (`clientId`),
	FOREIGN KEY (`softwareLicenseId`, `licensePoolId`) REFERENCES `SOFTWARE_LICENSE_TO_LICENSE_POOL` (`softwareLicenseId`, `licensePoolId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

CREATE TABLE IF NOT EXISTS `AUDIT_SOFTWARE_TO_LICENSE_POOL` (
	`licensePoolId` varchar(100) NOT NULL,
	`name` varchar(100) NOT NULL,
	`version` varchar(100) NOT NULL,
	`subVersion` varchar(100) NOT NULL,
	`language` varchar(10) NOT NULL,
	`architecture` varchar(3) NOT NULL,
	PRIMARY KEY (`licensePoolId`,`name`,`version`,`subVersion`,`language`,`architecture`),
	KEY `licensePoolId` (`licensePoolId`),
	FOREIGN KEY (`licensePoolId`) REFERENCES `LICENSE_POOL` (`licensePoolId`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
"""


def create_audit_hardware_tables(  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
	session: Session, tables: dict[str, dict[str, dict[str, str | bool | None]]]
) -> None:
	from opsiconfd.backend.rpc.obj_audit_hardware import (  # pylint: disable=import-outside-toplevel
		get_audit_hardware_database_config,
	)

	existing_tables = set(tables.keys())

	for (hw_class, values) in get_audit_hardware_database_config().items():  # pylint: disable=too-many-nested-blocks
		logger.debug("Processing hardware class '%s'", hw_class)
		hardware_device_table_name = f"HARDWARE_DEVICE_{hw_class}"
		hardware_config_table_name = f"HARDWARE_CONFIG_{hw_class}"

		hardware_device_table_exists = hardware_device_table_name in existing_tables
		hardware_config_table_exists = hardware_config_table_name in existing_tables

		if hardware_device_table_exists:
			hardware_device_table = f"ALTER TABLE `{hardware_device_table_name}`\n"
		else:
			hardware_device_table = f"CREATE TABLE `{hardware_device_table_name}` (\n" f"`hardware_id` INTEGER NOT NULL AUTO_INCREMENT,\n"

		if hardware_config_table_exists:
			hardware_config_table = f"ALTER TABLE `{hardware_config_table_name}`\n"
		else:
			hardware_config_table = (
				f"CREATE TABLE `{hardware_config_table_name}` (\n"
				f"`config_id` INTEGER NOT NULL AUTO_INCREMENT,\n"
				"`hostId` varchar(255) NOT NULL,\n"
				"`hardware_id` INTEGER NOT NULL,\n"
				"`firstseen` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n"
				"`lastseen` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n"
				"`state` TINYINT NOT NULL,\n"
			)

		hardware_device_values_processed = 0
		hardware_config_values_processed = 0
		for (value, value_info) in values.items():
			logger.debug("  Processing value '%s'", value)
			if value_info["Scope"] == "g":
				if hardware_device_table_exists:
					if value in tables[hardware_device_table_name]:  # pylint: disable=loop-invariant-statement
						# Column exists => change
						hardware_device_table += f"CHANGE `{value}` `{value}` {value_info['Type']} NULL,\n"
					else:
						# Column does not exist => add
						hardware_device_table += f'ADD `{value}` {value_info["Type"]} NULL,\n'
				else:
					hardware_device_table += f'`{value}` {value_info["Type"]} NULL,\n'
				hardware_device_values_processed += 1
			elif value_info["Scope"] == "i":
				if hardware_config_table_exists:
					if value in tables[hardware_config_table_name]:  # pylint: disable=loop-invariant-statement
						# Column exists => change
						hardware_config_table += f'CHANGE `{value}` `{value}` {value_info["Type"]} NULL,\n'
					else:
						# Column does not exist => add
						hardware_config_table += f'ADD `{value}` {value_info["Type"]} NULL,\n'
				else:
					hardware_config_table += f'`{value}` {value_info["Type"]} NULL,\n'
				hardware_config_values_processed += 1

		if not hardware_device_table_exists:
			hardware_device_table += "PRIMARY KEY (`hardware_id`)\n"
		if not hardware_config_table_exists:
			hardware_config_table += "PRIMARY KEY (`config_id`)\n"

		# Remove leading and trailing whitespace
		hardware_device_table = hardware_device_table.strip()
		hardware_config_table = hardware_config_table.strip()

		# Remove trailing comma
		if hardware_device_table.endswith(","):
			hardware_device_table = hardware_device_table[:-1]
		if hardware_config_table.endswith(","):
			hardware_config_table = hardware_config_table[:-1]

		# Finish sql query
		if hardware_device_table_exists:
			hardware_device_table += " ;\n"
		else:
			hardware_device_table += "\n) ENGINE=InnoDB DEFAULT CHARSET=utf8;\n"

		if hardware_config_table_exists:
			hardware_config_table += " ;\n"
		else:
			hardware_config_table += "\n) ENGINE=InnoDB DEFAULT CHARSET=utf8;\n"

		# Execute sql query
		if hardware_device_values_processed or not hardware_device_table_exists:
			logger.debug(hardware_device_table)
			session.execute(hardware_device_table)
		if hardware_config_values_processed or not hardware_config_table_exists:
			logger.debug(hardware_config_table)
			session.execute(hardware_config_table)


def read_schema_version(session: Session) -> int | None:
	"""
	Read the version of the schema from the database.
	"""
	try:
		# Remove migration markers for failed migrations
		session.execute("DELETE FROM `OPSI_SCHEMA` WHERE `updateEnded` IS NULL OR `updateEnded` = '0000-00-00 00:00:00'")
		row = session.execute("SELECT MAX(`version`) FROM `OPSI_SCHEMA`").fetchone()
		if row and row[0] is not None:
			return int(row[0])
	except Exception as err:  # pylint: disable=broad-except
		logger.warning("Reading database schema version failed: %s", err)
	return None


def get_index_columns(session: Session, database: str, table: str, index: str) -> list[str]:
	res = session.execute(
		"SELECT GROUP_CONCAT(`COLUMN_NAME` ORDER BY `SEQ_IN_INDEX` ASC) FROM `INFORMATION_SCHEMA`.`STATISTICS`"
		" WHERE `TABLE_SCHEMA` = :database AND `TABLE_NAME` = :table AND `INDEX_NAME` = :index",
		params={"database": database, "table": table, "index": index},
	).fetchone()
	if not res or not res[0]:
		return []
	return res[0].split(",")


def create_index(session: Session, database: str, table: str, index: str, columns: list[str]) -> None:
	index_columns = get_index_columns(session=session, database=database, table=table, index=index)
	if index_columns != columns:
		key = ",".join([f"`{c}`" for c in columns])
		if index == "PRIMARY":
			logger.notice("Setting new PRIMARY KEY on table %r", table)
			if index_columns:
				session.execute(f"ALTER TABLE `{table}` DROP PRIMARY KEY")
			session.execute(f"ALTER TABLE `{table}` ADD PRIMARY KEY ({key})")
		else:
			logger.notice("Setting new index %r on table %r", index, table)
			if index_columns:
				session.execute(f"ALTER TABLE `{table}` DROP INDEX `{index}`")
			session.execute(f"CREATE INDEX `{index}` on `{table}` ({key})")


def remove_index(session: Session, database: str, table: str, index: str) -> None:
	index_columns = get_index_columns(session=session, database=database, table=table, index=index)
	if index_columns:
		logger.notice("Removing index %r on table %r", index, table)
		session.execute(f"ALTER TABLE `{table}` DROP INDEX `{index}`")


class UpdateRules(StrEnum):
	RESTRICT = "RESTRICT"
	CASCADE = "CASCADE"
	NO_ACTION = "NO ACTION"
	SET_NULL = "SET NULL"

	@classmethod
	def has_value(cls, value: str) -> bool:
		return value in cls._value2member_map_


@dataclass
class OpsiForeignKey:
	table: str
	ref_table: str
	f_keys: list[str] = field(default_factory=list)
	ref_keys: list[str] = field(default_factory=list)
	update_rule: Literal["RESTRICT", "CASCADE", "NO ACTION", "SET NULL"] = "CASCADE"
	delete_rule: Literal["RESTRICT", "CASCADE", "NO ACTION", "SET NULL"] | None = None

	def __post_init__(self) -> None:
		if not UpdateRules.has_value(self.update_rule):
			raise ValueError("update_rule is not a valid update rule.")

		if not self.delete_rule:
			self.delete_rule = self.update_rule
		elif UpdateRules.has_value(self.delete_rule):
			raise ValueError("update_rule is not a valid delete rule.")


def create_foreign_key(session: Session, database: str, foreign_key: OpsiForeignKey, cleanup_function: Callable = None) -> None:
	keys = ",".join([f"`{k}`" for k in foreign_key.f_keys])
	if foreign_key.ref_keys:
		refs = ",".join([f"`{k}`" for k in foreign_key.ref_keys])
	else:
		refs = keys
	res = session.execute(
		"""
		SELECT DISTINCT `t1`.`CONSTRAINT_NAME`, t2.UPDATE_RULE, t2.DELETE_RULE FROM `INFORMATION_SCHEMA`.`KEY_COLUMN_USAGE` AS `t1`
		INNER JOIN `INFORMATION_SCHEMA`.`REFERENTIAL_CONSTRAINTS` AS `t2`
		ON `t1`.`CONSTRAINT_SCHEMA` = `t2`.`CONSTRAINT_SCHEMA` AND `t1`.`CONSTRAINT_NAME` = `t2`.`CONSTRAINT_NAME`
		WHERE `t1`.`TABLE_SCHEMA` = :database AND `t1`.`TABLE_NAME` = :table
		AND `t1`.`REFERENCED_TABLE_NAME` = :ref_table
		""",
		params={"database": database, "table": foreign_key.table, "ref_table": foreign_key.ref_table},
	).fetchone()
	if not res or res[1] != foreign_key.update_rule or res[2] != foreign_key.delete_rule:
		if res:
			logger.notice(f"Removing foreign key to {foreign_key.ref_table} on table {foreign_key.table}")
			session.execute(f"ALTER TABLE `{foreign_key.table}` DROP FOREIGN KEY {res[0]}")
		if cleanup_function:
			cleanup_function(session=session, dry_run=False)
		logger.notice(
			(
				f"Creating foreign key to {foreign_key.ref_table} on table {foreign_key.table} "
				f"with ON UPDATE {foreign_key.update_rule} and ON DELETE {foreign_key.delete_rule}"
			)
		)
		session.execute(
			f"""
			ALTER TABLE `{foreign_key.table}` ADD
			FOREIGN KEY ({keys})
			REFERENCES `{foreign_key.ref_table}` ({refs})
			ON UPDATE {foreign_key.update_rule} ON DELETE {foreign_key.update_rule}
			"""
		)


def update_database(mysql: MySQLConnection) -> None:  # pylint: disable=too-many-branches,too-many-statements
	with mysql.session() as session:

		session.execute(CREATE_TABLES_SQL)
		create_audit_hardware_tables(session, mysql.tables)

		mysql.read_tables()

		schema_version = read_schema_version(session)
		logger.info("Current database schema version is %r", schema_version)

		if not schema_version or schema_version < mysql.schema_version:
			logger.notice("Starting update to schema version %r", mysql.schema_version)
			session.execute("INSERT INTO `OPSI_SCHEMA` (`version`) VALUES (:version)", params={"version": mysql.schema_version})

		logger.info("Running opsi 4.1 updates")

		if "BOOT_CONFIGURATION" in mysql.tables:
			logger.notice("Dropping table BOOT_CONFIGURATION")
			session.execute("DROP TABLE IF EXISTS `BOOT_CONFIGURATION`")

		if "workbenchLocalUrl" not in mysql.tables["HOST"]:
			logger.notice("Adding column 'workbenchLocalUrl' on table HOST.")
			session.execute("ALTER TABLE `HOST` add `workbenchLocalUrl` varchar(128)")

		if "workbenchRemoteUrl" not in mysql.tables["HOST"]:
			logger.notice("Adding column 'workbenchRemoteUrl' on table HOST.")
			session.execute("ALTER TABLE `HOST` add `workbenchRemoteUrl` varchar(255)")

		if mysql.tables["OBJECT_TO_GROUP"]["groupId"]["type"] != "varchar(255)":
			logger.notice("Changing size of column 'groupId' on table OBJECT_TO_GROUP")
			session.execute("ALTER TABLE `OBJECT_TO_GROUP` MODIFY COLUMN `groupId` varchar(255) NOT NULL")

		if mysql.tables["HOST"]["inventoryNumber"]["type"] != "varchar(64)":
			logger.notice("Changing size of column 'inventoryNumber' on table HOST")
			session.execute('ALTER TABLE `HOST` MODIFY COLUMN `inventoryNumber` varchar(64) NOT NULL DEFAULT ""')

		create_index(
			session=session,
			database=mysql.database,
			table="WINDOWS_SOFTWARE_ID_TO_PRODUCT",
			index="index_productId",
			columns=["productId"],
		)

		create_index(
			session=session,
			database=mysql.database,
			table="PRODUCT",
			index="index_productId",
			columns=["productId"],
		)

		logger.info("Running opsi 4.2 updates")

		if mysql.tables["HOST"]["ipAddress"]["type"] != "varchar(255)":
			logger.notice("Changing size of column 'ipAddress' on table HOST")
			session.execute("ALTER TABLE `HOST` MODIFY COLUMN `ipAddress` varchar(255)")

		logger.info("Running opsi 4.3 updates")

		for row in session.execute(
			"SELECT `TABLE_NAME`, `ENGINE`, `TABLE_COLLATION` FROM	`INFORMATION_SCHEMA`.`TABLES` WHERE `TABLE_SCHEMA` = :database",
			params={"database": mysql.database},
		).fetchall():
			row_dict = dict(row)
			if row_dict["ENGINE"] != "InnoDB":
				logger.notice("Changing table %s to InnoDB engine", row_dict["TABLE_NAME"])
				session.execute(f"ALTER TABLE `{row_dict['TABLE_NAME']}` ENGINE = InnoDB")
			if row_dict["TABLE_COLLATION"] != "utf8_general_ci":
				logger.notice("Changing table %s to utf8_general_ci collation", row_dict["TABLE_NAME"])
				session.execute(f"ALTER TABLE `{row_dict['TABLE_NAME']}` DEFAULT COLLATE utf8_general_ci")

		create_foreign_key(
			session=session,
			database=mysql.database,
			foreign_key=OpsiForeignKey(
				table="PRODUCT_ON_CLIENT", ref_table="HOST", f_keys=["clientId"], ref_keys=["hostId"], update_rule="CASCADE"
			),
		)

		create_foreign_key(
			session=session,
			database=mysql.database,
			foreign_key=OpsiForeignKey(table="PRODUCT_ON_DEPOT", ref_table="HOST", f_keys=["depotId"], ref_keys=["hostId"]),
		)

		create_index(
			session=session,
			database=mysql.database,
			table="PRODUCT_PROPERTY_VALUE",
			index="index_product_property_value",
			columns=["productId", "propertyId", "productVersion", "packageVersion"],
		)

		create_foreign_key(
			session=session,
			database=mysql.database,
			foreign_key=OpsiForeignKey(
				table="PRODUCT_PROPERTY_VALUE",
				ref_table="PRODUCT_PROPERTY",
				f_keys=["productId", "productVersion", "packageVersion", "propertyId"],
			),
			cleanup_function=remove_orphans_product_property_value,
		)

		create_foreign_key(
			session=session,
			database=mysql.database,
			foreign_key=OpsiForeignKey(
				table="CONFIG_VALUE",
				ref_table="CONFIG",
				f_keys=["configId"],
			),
			cleanup_function=remove_orphans_config_value,
		)

		create_index(
			session=session,
			database=mysql.database,
			table="AUDIT_SOFTWARE_TO_LICENSE_POOL",
			index="PRIMARY",
			columns=["licensePoolId", "name", "version", "subVersion", "language", "architecture"],
		)

		if "config_state_id" in mysql.tables["CONFIG_STATE"]:
			logger.notice("Removing duplicates from table CONFIG_STATE")
			duplicates = []
			for row in session.execute(
				"""
				SELECT GROUP_CONCAT(`config_state_id`) AS ids, COUNT(*) AS num
				FROM `CONFIG_STATE` GROUP BY `configId`, `objectId` HAVING num > 1
				"""
			).fetchall():
				ids = dict(row)["ids"].split(",")
				duplicates.extend(ids[1:])
			if duplicates:
				logger.notice("Deleting duplicate config_state_ids: %s", duplicates)
				session.execute("DELETE FROM `CONFIG_STATE` WHERE `config_state_id` IN :ids", params={"ids": duplicates})

			logger.notice("Dropping column 'config_state_id' from table CONFIG_STATE")
			session.execute("ALTER TABLE `CONFIG_STATE` DROP COLUMN `config_state_id`")

		create_index(
			session=session,
			database=mysql.database,
			table="CONFIG_STATE",
			index="PRIMARY",
			columns=["configId", "objectId"],
		)

		if "license_on_client_id" in mysql.tables["LICENSE_ON_CLIENT"]:
			session.execute("DELETE FROM `LICENSE_ON_CLIENT` WHERE `clientId` IS NULL")
			session.execute("ALTER TABLE `LICENSE_ON_CLIENT` MODIFY COLUMN `clientId` varchar(255) NOT NULL")

			logger.notice("Removing duplicates from table LICENSE_ON_CLIENT")
			duplicates = []
			for row in session.execute(
				"""
				SELECT GROUP_CONCAT(`license_on_client_id`) AS ids, COUNT(*) AS num
				FROM `LICENSE_ON_CLIENT` GROUP BY `softwareLicenseId`, `licensePoolId`, `clientId` HAVING num > 1
				"""
			).fetchall():
				ids = dict(row)["ids"].split(",")
				duplicates.extend(ids[1:])
			if duplicates:
				logger.notice("Deleting duplicate license_on_client_ids: %s", duplicates)
				session.execute(
					"DELETE FROM `LICENSE_ON_CLIENT` WHERE `license_on_client_id` IN :ids",
					params={"ids": duplicates},
				)

			logger.notice("Dropping column 'license_on_client_id' from table LICENSE_ON_CLIENT")
			session.execute("ALTER TABLE `LICENSE_ON_CLIENT` DROP COLUMN `license_on_client_id`")

		create_index(
			session=session,
			database=mysql.database,
			table="LICENSE_ON_CLIENT",
			index="PRIMARY",
			columns=["softwareLicenseId", "licensePoolId", "clientId"],
		)

		if "object_to_group_id" in mysql.tables["OBJECT_TO_GROUP"]:
			logger.notice("Removing duplicates from table OBJECT_TO_GROUP")
			duplicates = []
			for row in session.execute(
				"""
				SELECT GROUP_CONCAT(`object_to_group_id`) AS ids, COUNT(*) AS num
				FROM `OBJECT_TO_GROUP` GROUP BY `groupType`, `groupId`, `objectId` HAVING num > 1
				"""
			).fetchall():
				ids = dict(row)["ids"].split(",")
				duplicates.extend(ids[1:])
			if duplicates:
				logger.notice("Deleting duplicate object_to_group_ids: %s", duplicates)
				session.execute(
					"DELETE FROM `OBJECT_TO_GROUP` WHERE `object_to_group_id` IN :ids",
					params={"ids": duplicates},
				)

			logger.notice("Dropping column 'object_to_group_id' from table OBJECT_TO_GROUP")
			session.execute("ALTER TABLE `OBJECT_TO_GROUP` DROP COLUMN `object_to_group_id`")

		create_index(
			session=session,
			database=mysql.database,
			table="OBJECT_TO_GROUP",
			index="PRIMARY",
			columns=["groupType", "groupId", "objectId"],
		)

		if "product_property_state_id" in mysql.tables["PRODUCT_PROPERTY_STATE"]:
			session.execute("DELETE FROM `PRODUCT_PROPERTY_STATE` WHERE `productId` IS NULL")
			session.execute("ALTER TABLE `PRODUCT_PROPERTY_STATE` MODIFY COLUMN `productId` varchar(255) NOT NULL")

			logger.notice("Removing duplicates from table PRODUCT_PROPERTY_STATE")
			duplicates = []
			for row in session.execute(
				"""
				SELECT GROUP_CONCAT(`product_property_state_id`) AS ids, COUNT(*) AS num
				FROM `PRODUCT_PROPERTY_STATE` GROUP BY `productId`, `propertyId`, `objectId` HAVING num > 1
				"""
			).fetchall():
				ids = dict(row)["ids"].split(",")
				duplicates.extend(ids[1:])
			if duplicates:
				logger.notice("Deleting duplicate product_property_state_ids: %s", duplicates)
				session.execute(
					"DELETE FROM `PRODUCT_PROPERTY_STATE` WHERE `product_property_state_id` IN :ids",
					params={"ids": duplicates},
				)

			logger.notice("Dropping column 'product_property_state_id' from table PRODUCT_PROPERTY_STATE")
			session.execute("ALTER TABLE `PRODUCT_PROPERTY_STATE` DROP COLUMN `product_property_state_id`")

		create_index(
			session=session,
			database=mysql.database,
			table="PRODUCT_PROPERTY_STATE",
			index="PRIMARY",
			columns=["productId", "propertyId", "objectId"],
		)

		if "config_id" in mysql.tables["SOFTWARE_CONFIG"]:
			logger.notice("Removing duplicates from table SOFTWARE_CONFIG")
			duplicates = []
			for row in session.execute(
				"""
				SELECT GROUP_CONCAT(`config_id`) AS ids, COUNT(*) AS num
				FROM `SOFTWARE_CONFIG` GROUP BY `clientId`, `name`, `version`, `subVersion`, `language`, `architecture` HAVING num > 1
				"""
			).fetchall():
				ids = dict(row)["ids"].split(",")
				duplicates.extend(ids[1:])
			if duplicates:
				logger.notice("Deleting duplicate config_ids: %s", duplicates)
				session.execute(
					"DELETE FROM `SOFTWARE_CONFIG` WHERE `config_id` IN :ids",
					params={"ids": duplicates},
				)

			logger.notice("Dropping column 'config_id' from table SOFTWARE_CONFIG")
			session.execute("ALTER TABLE `SOFTWARE_CONFIG` DROP COLUMN `config_id`")

		create_index(
			session=session,
			database=mysql.database,
			table="SOFTWARE_CONFIG",
			index="PRIMARY",
			columns=["clientId", "name", "version", "subVersion", "language", "architecture"],
		)

		res = session.execute(
			"""
			SELECT DISTINCT `CONSTRAINT_NAME` FROM `INFORMATION_SCHEMA`.`KEY_COLUMN_USAGE`
			WHERE `TABLE_SCHEMA` = :database AND `TABLE_NAME` = 'SOFTWARE_CONFIG'
			""",
			params={"database": mysql.database},
		).fetchall()
		fk_names = []  # pylint: disable=use-tuple-over-list
		if res:
			fk_names = [r[0] for r in res]

		if "FK_HOST" not in fk_names or "FK_SOFTWARE" not in fk_names:
			res = session.execute(
				"""
				SELECT c.name, c.version, c.subVersion, c.`language`, c.architecture
				FROM SOFTWARE_CONFIG AS c
				LEFT JOIN SOFTWARE AS s ON
					s.name = c.name AND s.version = c.version AND s.subVersion = c.subVersion AND
					s.`language` = c.`language` AND	s.architecture = c.architecture
				LEFT JOIN HOST AS h ON h.hostId = c.clientId
				WHERE s.name IS NULL OR h.hostId IS NULL
				"""
			).fetchall()
			if res:
				logger.notice("Removing orphan entries from SOFTWARE_CONFIG")
				for row in res:
					session.execute(
						"""
						DELETE FROM SOFTWARE_CONFIG
						WHERE name = :name AND version = :version AND subVersion = :subVersion
							AND `language` = :language AND architecture = :architecture
						""",
						params=dict(row),
					)

			if "FK_SOFTWARE" not in fk_names:
				session.execute(
					"""
					ALTER TABLE `SOFTWARE_CONFIG` ADD
					FOREIGN KEY (`name`, `version`, `subVersion`, `language`, `architecture`)
					REFERENCES `SOFTWARE` (`name`, `version`, `subVersion`, `language`, `architecture`)
					ON UPDATE CASCADE ON DELETE CASCADE
					"""
				)
			if "FK_HOST" not in fk_names:
				session.execute(
					"""
					ALTER TABLE `SOFTWARE_CONFIG` ADD
					FOREIGN KEY (`clientId`)
					REFERENCES `HOST` (`hostId`)
					ON UPDATE CASCADE ON DELETE CASCADE
					"""
				)

		create_foreign_key(
			session=session,
			database=mysql.database,
			foreign_key=OpsiForeignKey(
				table="PRODUCT_ON_DEPOT", ref_table="PRODUCT", f_keys=["productId", "productVersion", "packageVersion"]
			),
		)

		create_foreign_key(
			session=session,
			database=mysql.database,
			foreign_key=OpsiForeignKey(
				table="PRODUCT_PROPERTY", ref_table="PRODUCT", f_keys=["productId", "productVersion", "packageVersion"]
			),
		)

		create_foreign_key(
			session=session,
			database=mysql.database,
			foreign_key=OpsiForeignKey(
				table="PRODUCT_DEPENDENCY", ref_table="PRODUCT", f_keys=["productId", "productVersion", "packageVersion"]
			),
		)

		create_foreign_key(
			session=session,
			database=mysql.database,
			foreign_key=OpsiForeignKey(
				table="OBJECT_TO_GROUP", ref_table="GROUP", f_keys=["groupType", "groupId"], ref_keys=["type", "groupId"]
			),
		)

		create_foreign_key(
			session=session,
			database=mysql.database,
			foreign_key=OpsiForeignKey(table="AUDIT_SOFTWARE_TO_LICENSE_POOL", ref_table="LICENSE_POOL", f_keys=["licensePoolId"]),
		)

		create_foreign_key(
			session=session,
			database=mysql.database,
			foreign_key=OpsiForeignKey(table="LICENSE_ON_CLIENT", ref_table="HOST", f_keys=["clientId"], ref_keys=["hostId"]),
		)

		create_foreign_key(
			session=session,
			database=mysql.database,
			foreign_key=OpsiForeignKey(table="SOFTWARE_LICENSE", ref_table="HOST", f_keys=["boundToHost"], ref_keys=["hostId"]),
		)

		if "LOG_CONFIG_VALUE" in mysql.tables:
			logger.notice("Dropping table LOG_CONFIG_VALUE")
			session.execute("DROP TABLE IF EXISTS `LOG_CONFIG_VALUE`")

		if "LOG_CONFIG" in mysql.tables:
			logger.notice("Dropping table LOG_CONFIG")
			session.execute("DROP TABLE IF EXISTS `LOG_CONFIG`")

		if "CONFIG_STATE_LOG" in mysql.tables:
			logger.notice("Dropping table CONFIG_STATE_LOG")
			session.execute("DROP TABLE IF EXISTS `CONFIG_STATE_LOG`")

		logger.info("All updates completed")

		if not schema_version or schema_version < mysql.schema_version:
			logger.notice("Setting updateEnded for schema version %r", mysql.schema_version)
			session.execute(
				"UPDATE `OPSI_SCHEMA` SET `updateEnded` = CURRENT_TIMESTAMP WHERE version = :version",
				params={"version": mysql.schema_version},
			)
