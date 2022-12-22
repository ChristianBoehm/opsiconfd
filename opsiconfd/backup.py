# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
backup
"""

from contextlib import contextmanager, nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any, Generator, Literal

from msgspec import json, msgpack
from opsicommon.types import forceHostId  # type: ignore[import]
from rich.progress import Progress

from opsiconfd import __version__
from opsiconfd.application import MaintenanceState, app
from opsiconfd.backend import get_unprotected_backend
from opsiconfd.backend.mysql import MySQLConnection
from opsiconfd.backend.mysql.schema import (
	create_database,
	drop_database,
	update_database,
)
from opsiconfd.config import (
	FQDN,
	OPSI_LICENSE_DIR,
	OPSI_MODULES_FILE,
	OPSI_PASSWD_FILE,
	SSH_COMMANDS_CUSTOM_FILE,
	SSH_COMMANDS_DEFAULT_FILE,
	config,
	opsi_config,
)
from opsiconfd.logging import logger, secret_filter
from opsiconfd.redis import redis_lock
from opsiconfd.utils import compress_data, decompress_data

OBJECT_CLASSES = (
	"Host",
	"Config",
	"ConfigState",
	"Product",
	"ProductProperty",
	"ProductDependency",
	"ProductOnDepot",
	"ProductOnClient",
	"ProductPropertyState",
	"Group",
	"ObjectToGroup",
	"AuditSoftware",
	"AuditSoftwareOnClient",
	"AuditHardware",
	"AuditHardwareOnHost",
	"LicenseContract",
	"SoftwareLicense",
	"LicensePool",
	"AuditSoftwareToLicensePool",
	"SoftwareLicenseToLicensePool",
	"LicenseOnClient",
)


@contextmanager
def maintenance_mode(
	message: str, wait_accomplished: float, address_exceptions: list[str] | None = None, progress: Progress | None = None
) -> Generator[None, None, None]:
	logger.notice("Entering maintenance mode")
	if progress:
		maint_task = progress.add_task("Entering maintenance mode", total=None)
	orig_state = app.app_state
	if not isinstance(orig_state, MaintenanceState):
		# Not already in maintenance state
		app.set_app_state(
			MaintenanceState(retry_after=300, message=message, address_exceptions=address_exceptions or []),
			wait_accomplished=wait_accomplished,
		)
		if progress:
			progress.update(maint_task, total=1, completed=True)
	try:
		yield
	finally:
		if not isinstance(orig_state, MaintenanceState):
			logger.notice("Reentering %s mode", orig_state.type)
			if progress:
				progress.console.print(f"Reentering {orig_state.type} mode")
			app.set_app_state(orig_state, wait_accomplished=0)


def get_config_files() -> dict[str, Path]:
	backend_config_dir = Path(config.backend_config_dir)
	config_files = {
		"opsiconfd_conf": Path(config.config_file),
		"opsi_conf": Path(opsi_config.config_file),
		"opsi_passwd": Path(OPSI_PASSWD_FILE),
		"ssl_ca_key": Path(config.ssl_ca_key),
		"ssl_ca_cert": Path(config.ssl_ca_cert),
		"ssl_server_key": Path(config.ssl_server_key),
		"ssl_server_cert": Path(config.ssl_server_cert),
		"ssh_commands_custom": Path(SSH_COMMANDS_CUSTOM_FILE),
		"ssh_commands_default": Path(SSH_COMMANDS_DEFAULT_FILE),
		"dhcpd_conf": backend_config_dir / "dhcpd.conf",
		"hostcontrol_conf": backend_config_dir / "hostcontrol.conf",
		"jsonrpc_conf": backend_config_dir / "jsonrpc.conf",
		"mysql_conf": backend_config_dir / "mysql.conf",
		"opsipxeconfd_conf": backend_config_dir / "opsipxeconfd.conf",
		"acl_conf": Path(config.acl_file),
	}
	extension_config_dir = Path(config.extension_config_dir)
	for extension_config_file in extension_config_dir.glob("*.conf"):  # pylint: disable=use-dict-comprehension
		config_files[f"extension_conf_{extension_config_file.with_suffix('').name}"] = extension_config_file

	modules_file = Path(OPSI_MODULES_FILE)
	if modules_file.exists():
		config_files["modules"] = modules_file

	license_dir = Path(OPSI_LICENSE_DIR)
	for license_file in license_dir.glob("*.opsilic"):  # pylint: disable=use-dict-comprehension
		config_files[f"opsilic_{license_file.with_suffix('').name}"] = license_file

	return config_files


def create_backup(  # pylint: disable=too-many-arguments,too-many-locals,too-many-branches,too-many-statements
	config_files: bool = True,
	backup_file: Path | None = None,
	file_encoding: Literal["msgpack", "json"] = "msgpack",
	file_compression: Literal["lz4", "gz"] = "lz4",
	maintenance: bool = True,
	maintenance_address_exceptions: list[str] | None = None,
	progress: Progress | None = None,
) -> dict[str, dict[str, Any]]:
	with redis_lock("backup-restore", acquire_timeout=2.0, lock_timeout=12 * 3600):
		if opsi_config.get("host", "server-role") != "configserver":
			raise RuntimeError("Not a config server")

		backend = get_unprotected_backend()
		now = datetime.utcnow()
		server_ids = backend.host_getIdents(returnType="str", type="OpsiConfigserver")
		if not server_ids:
			raise ValueError("No configserver in database")
		data: dict[str, dict[str, Any]] = {
			"meta": {
				"type": "opsiconfd_backup",
				"version": "1",
				"opsiconfd_version": __version__,
				"timestamp": now.timestamp(),
				"datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
				"node_name": config.node_name,
				"fqdn": FQDN,
				"host_id": str(opsi_config.get("host", "id")),
				"server_id": server_ids[0],
			},
			"objects": {},
			"config_files": {},
		}

		ctm = (
			maintenance_mode(
				message="Maintenance mode, backup in progress, please try again later",
				wait_accomplished=30,
				address_exceptions=maintenance_address_exceptions or [],
				progress=progress,
			)
			if maintenance
			else nullcontext()
		)
		with ctm:
			logger.notice("Backing up objects")
			if progress:
				progress.console.print("Backing up database objects")
				backup_task = progress.add_task("Backing up database objects", total=len(OBJECT_CLASSES))
			for obj_class in OBJECT_CLASSES:  # pylint: disable=loop-global-usage
				logger.notice("Fetching objects of type %s", obj_class)
				if progress:
					progress.console.print(f"Backing up objects of type [bold]{obj_class}[/bold]")
				method = getattr(backend, f"{obj_class[0].lower()}{obj_class[1:]}_getObjects")
				data["objects"][obj_class] = [o.to_hash() for o in method()]  # pylint: disable=loop-invariant-statement
				logger.info(
					"Read %d objects of type %s", len(data["objects"][obj_class]), obj_class  # pylint: disable=loop-invariant-statement
				)
				if progress:
					progress.advance(backup_task)

			if config_files:
				logger.notice("Backing up config files")
				conf_files = get_config_files()
				num_files = len(conf_files)
				if progress:
					progress.console.print(f"Backing up {num_files} config files")
					file_task = progress.add_task("Backing up config files", total=num_files)

				for name, file in conf_files.items():
					content = None
					if file.exists():
						content = file.read_text(encoding="utf-8")
					else:
						logger.warning("Config file '%s' not found, skipping in backup", file)
					data["config_files"][name] = {  # pylint: disable=loop-invariant-statement
						"path": str(file.absolute()),
						"content": content,
					}
					if progress:
						progress.advance(file_task)

		if not backup_file:
			return data

		if not isinstance(backup_file, Path):
			backup_file = Path(backup_file)

		if progress:
			file_task = progress.add_task("Creating backup file", total=None)

		logger.notice("Encoding data to %s", file_encoding)
		if progress:
			progress.console.print(f"Encoding data to {file_encoding}")
		encode = json.encode if file_encoding == "json" else msgpack.encode
		bdata = encode(data)

		if file_compression:
			logger.notice("Compressing data with %s", file_compression)
			if progress:
				progress.console.print(f"Compressing data with {file_compression}")
			bdata = compress_data(bdata, compression=file_compression)

		logger.notice("Writing data to file %s", backup_file)
		if progress:
			progress.console.print("Writing data to file")
		backup_file.write_bytes(bdata)

		if progress:
			progress.update(file_task, total=1, completed=True)

		return data


def restore_backup(  # pylint: disable=too-many-arguments,too-many-locals,too-many-branches,too-many-statements
	data_or_file: dict[str, dict[str, Any]] | Path,
	config_files: bool = True,
	server_id: str = "backup",
	batch: bool = True,
	maintenance_address_exceptions: list[str] | None = None,
	progress: Progress | None = None,
) -> None:
	with redis_lock("backup-restore", acquire_timeout=2.0, lock_timeout=12 * 3600):
		data = {}
		if isinstance(data_or_file, Path):
			backup_file = data_or_file
			logger.notice("Reading data from file %s", backup_file)
			if progress:
				progress.console.print("Reading data from file")
				file_task = progress.add_task("Processing backup file", total=None)

			bdata = backup_file.read_bytes()

			head = bdata[0:4].hex()
			compression = None
			if head == "04224d18":
				compression = "lz4"
			elif head.startswith("1f8b"):
				compression = "gz"
			if compression:
				logger.notice("Decomressing %s data", compression)
				if progress:
					progress.console.print(f"Decomressing {compression} data")
				bdata = decompress_data(bdata, compression=compression)

			encoding = "json" if bdata.startswith(b"{") else "msgpack"
			logger.notice("Decoding %s data", encoding)
			if progress:
				progress.console.print(f"Decoding {encoding} data")
			decode = json.decode if encoding == "json" else msgpack.decode
			data = decode(bdata)  # type: ignore[operator]
			if progress:
				progress.update(file_task, total=1, completed=True)
		else:
			data = data_or_file

		if data.get("meta", {}).get("type") != "opsiconfd_backup":
			raise ValueError("Invalid backup")
		version = data["meta"].get("version")
		if version != "1":
			raise ValueError(f"Invalid backup version: {version!r}")

		backup_server_id = data["meta"].get("server_id")
		if not backup_server_id:
			raise ValueError("Server id missing in backup meta data")

		if server_id == "backup":
			server_id = backup_server_id
		elif server_id == "local":
			server_id = str(opsi_config.get("host", "id"))
		else:
			server_id = forceHostId(server_id)

		with maintenance_mode(
			message="Maintenance mode, restore in progress, please try again later",
			wait_accomplished=30,
			address_exceptions=maintenance_address_exceptions or [],
			progress=progress,
		):
			logger.notice("Preparing database")
			if progress:
				db_task = progress.add_task("Preparing database", total=3)

			mysql = MySQLConnection()
			mysql.connect()

			if progress:
				progress.console.print("Dropping database")
			logger.notice("Dropping database")
			drop_database(mysql)
			if progress:
				progress.advance(db_task)
				progress.console.print("Creating database")
			logger.notice("Creating database")
			create_database(mysql)
			if progress:
				progress.advance(db_task)
				progress.console.print("Updating database")
			logger.notice("Reconnecting database")
			mysql.disconnect()
			mysql.connect()
			logger.notice("Updating database")
			update_database(mysql, force=True)
			if progress:
				progress.advance(db_task)

			backend = get_unprotected_backend()
			total_objects = sum(len(objs) for objs in data["objects"].values())

			logger.notice("Restoring %d database objects", total_objects)
			if progress:
				restore_task = progress.add_task("Restoring database objects", total=total_objects, refresh_per_second=2)

			for obj_class in OBJECT_CLASSES:  # pylint: disable=loop-global-usage
				objects = data["objects"].get(obj_class)
				if not objects:
					continue

				num_objects = len(objects)
				logger.notice("Restoring %d objects of type %s", num_objects, obj_class)
				if progress:
					progress.console.print(f"Restoring {num_objects} objects of type [bold]{obj_class}[/bold]")
				host_attr = None
				check_config = False
				check_config_state = False
				if server_id != backup_server_id:  # pylint: disable=loop-invariant-statement
					if obj_class == "Host":
						host_attr = "id"
					if obj_class == "ProductOnDepot":
						host_attr = "depotId"
					elif obj_class == "AuditHardwareOnHost":
						host_attr = "hostId"
					elif obj_class == ("ObjectToGroup", "ConfigState", "ProductPropertyState"):
						host_attr = "objectId"

					check_config = obj_class == "Config"
					check_config_state = obj_class == "ConfigState"

				method = getattr(backend, f"{obj_class[0].lower()}{obj_class[1:]}_insertObject")
				if batch:
					method = getattr(backend, f"{obj_class[0].lower()}{obj_class[1:]}_createObjects")

				for obj in objects:
					if host_attr:
						if obj[host_attr] == backup_server_id:
							obj[host_attr] = server_id
					if check_config and obj["id"] == "clientconfig.depot.id":
						obj["possibleValues"] = [
							server_id if v == backup_server_id else v  # pylint: disable=loop-invariant-statement
							for v in obj["possibleValues"]  # pylint: disable=loop-invariant-statement
						]
						obj["defaultValues"] = [
							server_id if v == backup_server_id else v  # pylint: disable=loop-invariant-statement
							for v in obj["defaultValues"]
						]
					if check_config_state and obj["configId"] == "clientconfig.depot.id":
						obj["values"] = [
							server_id if v == backup_server_id else v for v in obj["values"]  # pylint: disable=loop-invariant-statement
						]

					logger.trace("Insert %s object: %s", obj_class, obj)
					if not batch:
						method(obj)
						if progress:
							progress.advance(restore_task)

				if batch:
					logger.info("Batch inserting %d objects", len(objects))
					method(objects)
					if progress:
						progress.advance(restore_task, advance=num_objects)

			if config_files and data.get("config_files"):
				logger.notice("Restoring config files")
				num_files = len([cf for cf in data["config_files"].values() if cf["content"] is not None])
				if progress:
					progress.console.print(f"Restoring {num_files} config files")
					file_task = progress.add_task("Restoring config files", total=num_files)
				for name, file in get_config_files().items():
					config_file = data["config_files"].get(name)
					if config_file and config_file["content"] is not None:
						logger.info("Restoring config file %r (%s)", name, file)
						file.write_text(config_file["content"], encoding="utf-8")
						if progress:
							progress.advance(file_task)
					else:
						logger.info("Skipping config file %r (%s)", name, file)

			server_key = backend.host_getObjects(returnType="opsiHostKey", type="OpsiConfigserver")[0].opsiHostKey
			secret_filter.add_secrets(server_key)

			if opsi_config.get("host", "id") != server_id:
				logger.notice("Setting host.id to %r in %r", server_id, opsi_config.config_file)
				opsi_config.set("host", "id", server_id, persistent=True)
			if opsi_config.get("host", "key") != server_key:
				logger.notice("Updating host.key in %r", opsi_config.config_file)
				opsi_config.set("host", "key", server_key, persistent=True)
