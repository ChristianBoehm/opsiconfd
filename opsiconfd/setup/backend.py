# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
opsiconfd.setup.backend
"""

import re
from pathlib import Path

import OPSI.Backend.File  # type: ignore[import]
from OPSI.Backend.Replicator import BackendReplicator  # type: ignore[import]
from opsicommon.objects import OpsiConfigserver  # type: ignore[import]
from rich import print as rich_print
from rich.prompt import Confirm, Prompt

from opsiconfd import __version__
from opsiconfd.backend.mysql import MySQLConnection
from opsiconfd.backend.mysql.cleanup import cleanup_database
from opsiconfd.backend.mysql.schema import (
	create_database,
	drop_database,
	update_database,
)
from opsiconfd.config import (
	DEPOT_DIR,
	FQDN,
	REPOSITORY_DIR,
	WORKBENCH_DIR,
	config,
	get_configserver_id,
	opsi_config,
)
from opsiconfd.logging import logger, secret_filter
from opsiconfd.utils import get_ip_addresses, get_random_string


def setup_mysql_user(root_mysql: MySQLConnection, mysql: MySQLConnection) -> None:
	mysql.address = root_mysql.address
	mysql.database = root_mysql.database
	mysql.password = get_random_string(16)
	secret_filter.add_secrets(mysql.password)

	logger.info("Creating MySQL user %r and granting all rights on %r", mysql.username, mysql.database)
	with root_mysql.session() as session:
		session.execute(f"CREATE USER IF NOT EXISTS '{mysql.username}'@'{mysql.address}'")
		try:
			session.execute(f"ALTER USER '{mysql.username}'@'{mysql.address}' IDENTIFIED WITH mysql_native_password BY '{mysql.password}'")
		except Exception as err:  # pylint: disable=broad-except
			logger.debug(err)
			try:
				session.execute(f"ALTER USER '{mysql.username}'@'{mysql.address}' IDENTIFIED BY '{mysql.password}'")
			except Exception as err2:  # pylint: disable=broad-except
				logger.debug(err2)
				session.execute(f"SET PASSWORD FOR '{mysql.username}'@'{mysql.address}' = PASSWORD('{mysql.password}')")
		session.execute(f"GRANT ALL ON {mysql.database}.* TO '{mysql.username}'@'{mysql.address}'")
		session.execute("FLUSH PRIVILEGES")
		logger.notice("MySQL user %r created and privileges set", mysql.username)

	mysql.update_config_file()


def setup_mysql_connection(interactive: bool = False, force: bool = False) -> None:  # pylint: disable=too-many-branches
	error: Exception | None = None

	mysql = MySQLConnection()
	if not force:
		try:
			with mysql.connection():
				# OK
				return
		except Exception as err:  # pylint: disable=broad-except
			logger.info("Failed to connect to MySQL database: %s", err)
			error = err

	mysql_root = MySQLConnection()
	auto_try = False
	if not force and mysql_root.address in ("localhost", "127.0.0.1", "::1"):
		# Try unix socket connection as user root
		auto_try = True
		mysql_root.address = "localhost"
		mysql_root.database = "opsi"
		mysql_root.username = "root"
		mysql_root.password = ""
		logger.info("Trying to connect to local MySQL database as %s", mysql_root.username)

	while True:
		if not auto_try:
			if not interactive:
				raise error  # type: ignore[misc]
			if error:
				error_str = str(error).split("\n", 1)[0]
				match = re.search(r"(\(\d+,\s.*)", error_str)
				if match:
					error_str = match.group(1).strip("()")
				rich_print(f"[b][red]Failed to connect to MySQL database[/red]: {error_str}[/b]")
			if not Confirm.ask("Do you want to configure the MySQL database connection?"):
				raise error  # type: ignore[misc]
			mysql_root.address = Prompt.ask("Enter MySQL server address", default=mysql_root.address, show_default=True)
			mysql_root.database = Prompt.ask("Enter MySQL database", default=mysql_root.database, show_default=True)
			mysql_root.username = Prompt.ask("Enter MySQL admin username", default="root", show_default=True)
			mysql_root.password = Prompt.ask("Enter MySQL admin password", password=True)
			secret_filter.add_secrets(mysql_root.password)
			if force:
				mysql.username = Prompt.ask("Enter MySQL username for opsiconfd", default=mysql.username, show_default=True)
		try:
			with mysql_root.connection():
				if not auto_try:
					rich_print("[b][green]MySQL admin connection established[/green][/b]")
					rich_print("[b]Setting up MySQL user[/b]")
				setup_mysql_user(mysql_root, mysql)
				if not auto_try:
					rich_print("[b][green]MySQL user setup successful[/green][/b]")
				break
		except Exception as err:  # pylint: disable=broad-except
			if not auto_try:
				error = err

		auto_try = False
		mysql_root = MySQLConnection()


def setup_mysql(interactive: bool = False, full: bool = False, force: bool = False) -> None:  # pylint: disable=too-many-branches
	setup_mysql_connection(interactive=interactive, force=force)

	mysql = MySQLConnection()
	if interactive and force:
		rich_print(f"[b]Creating MySQL database {mysql.database!r} on {mysql.address!r}[/b]")
	try:
		mysql.connect()
		create_database(mysql)
	except Exception as err:
		if interactive and force:
			rich_print(f"[b][red]Failed to create MySQL database: {err}[/red][/b]")
		raise
	if interactive and force:
		rich_print("[b][green]MySQL database created successfully[/green][/b]")

	if interactive and force:
		rich_print("[b]Updating MySQL database[/b]")
	try:
		update_database(mysql, force=full)
	except Exception as err:
		if interactive and force:
			rich_print(f"[b][red]Failed to update MySQL database: {err}[/red][/b]")
		raise
	if interactive and force:
		rich_print("[b][green]MySQL database updated successfully[/green][/b]")

	if interactive and force:
		rich_print("[b]Cleaning up MySQL database[/b]")
	try:
		cleanup_database(mysql)
	except Exception as err:
		if interactive and force:
			rich_print(f"[b][red]Failed to cleanup MySQL database: {err}[/red][/b]")
		raise
	if interactive and force:
		rich_print("[b][green]MySQL database cleaned up successfully[/green][/b]")


def file_mysql_migration() -> None:
	dipatch_conf = Path(config.dispatch_config_file)
	if not dipatch_conf.exists():
		return

	file_backend_used = False
	for line in dipatch_conf.read_text(encoding="utf-8").split("\n"):
		line = line.strip()
		if not line or line.startswith("#") or ":" not in line:
			continue
		if "file" in line.split(":", 1)[1]:
			file_backend_used = True
			break
	if not file_backend_used:
		return

	logger.notice("Converting File to MySQL backend, please wait...")
	config_server_id = opsi_config.get("host", "id")
	OPSI.Backend.File.getfqdn = lambda: config_server_id

	file_backend = OPSI.Backend.File.FileBackend()
	config_servers = file_backend.host_getObjects(type="OpsiConfigserver")

	if not config_servers:
		depot_servers = file_backend.host_getObjects(type="OpsiDepotserver")
		if len(depot_servers) > 1:
			error = (
				"Cannot convert File to MySQL backend:\n"
				f"Configserver {file_backend.__serverId!r} not found in File backend.\n"  # pylint: disable=protected-access
				f"Depot servers in File backend are: {', '.join(d.id for d in depot_servers)}.\n"
				f"Set host.id in {opsi_config.config_file!r} to one of these IDs and retry."
			)
			logger.error(error)
			raise ValueError(error)

		config_server_id = depot_servers[0].id
		config_servers = file_backend.host_getObjects(type="OpsiConfigserver")
		opsi_config.set("host", "id", config_server_id, persistent=True)

	# pylint: disable=import-outside-toplevel
	from opsiconfd.backend import get_unprotected_backend

	backend = get_unprotected_backend()
	backend.events_enabled = False

	mysql = MySQLConnection()
	mysql.connect()
	drop_database(mysql)
	create_database(mysql)
	mysql.disconnect()
	mysql.connect()
	update_database(mysql, force=True)

	backend_replicator = BackendReplicator(readBackend=file_backend, writeBackend=backend, cleanupFirst=False)
	backend_replicator.replicate(audit=False)

	dipatch_conf.rename(dipatch_conf.with_suffix(".conf.old"))


def setup_backend(force_server_id: str | None = None) -> None:
	if opsi_config.get("host", "server-role") != "configserver":
		return

	file_mysql_migration()

	# pylint: disable=import-outside-toplevel
	from opsiconfd.backend import get_unprotected_backend

	config_server_id = force_server_id or get_configserver_id()

	backend = get_unprotected_backend()
	backend.events_enabled = False
	conf_servers = backend.host_getObjects(type="OpsiConfigserver")
	if not conf_servers:
		logger.notice("Creating config server %r", config_server_id)

		ip_address = None
		network_address = None
		for addr in get_ip_addresses():
			if addr["interface"] == "lo":
				continue
			if not ip_address or addr["family"] == "ipv4":
				# Prefer IPv4
				ip_address = addr["address"]
				network_address = addr["network"]

		conf_servers = [
			OpsiConfigserver(
				id=config_server_id,
				opsiHostKey=None,
				depotLocalUrl=f"file://{DEPOT_DIR}",
				depotRemoteUrl=f"smb://{FQDN}/opsi_depot",
				depotWebdavUrl=f"webdavs://{FQDN}:4447/depot",
				repositoryLocalUrl=f"file://{REPOSITORY_DIR}",
				repositoryRemoteUrl=f"webdavs://{FQDN}:4447/repository",
				workbenchLocalUrl=f"file://{WORKBENCH_DIR}",
				workbenchRemoteUrl=f"smb://{FQDN}/opsi_workbench",
				description=None,
				notes=None,
				hardwareAddress=None,
				ipAddress=ip_address,
				inventoryNumber=None,
				networkAddress=network_address,
				maxBandwidth=0,
				isMasterDepot=True,
				masterDepotId=None,
			)
		]
		backend.host_createObjects(conf_servers)
	elif conf_servers[0].id != config_server_id:
		if force_server_id:
			logger.notice("Renaming configserver from %r to %r, do not abort", conf_servers[0].id, config_server_id)
			backend.host_renameOpsiDepotserver(conf_servers[0].id, config_server_id)
			opsi_config.set("host", "id", config_server_id, persistent=True)
		else:
			raise ValueError(
				f"Config server ID {conf_servers[0].id!r} in database differs from host.id {config_server_id!r} in /etc/opsi/opsi.conf. "
				f"Please change host.id in /etc/opsi/opsi.conf to {conf_servers[0].id!r} "
				"or use `opsiconfd setup --rename-server` to fix this issue."
			)
	backend.exit()

	opsi_config.set("host", "key", conf_servers[0].opsiHostKey, persistent=True)