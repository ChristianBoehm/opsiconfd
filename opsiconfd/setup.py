# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
opsiconfd - setup
"""

import getpass
import grp
import os
import pwd
import re
import resource
import subprocess
import sys
import time
from pathlib import Path

import psutil
from OPSI.System.Posix import (  # type: ignore[import]
	getNetworkConfiguration,
	locateDHCPDConfig,
)
from opsicommon.objects import (  # type: ignore[import]
	BoolConfig,
	ConfigState,
	OpsiConfigserver,
	UnicodeConfig,
)
from opsicommon.server.rights import (  # type: ignore[import]
	DirPermission,
	FilePermission,
	PermissionRegistry,
	set_rights,
)
from opsicommon.server.setup import (  # type: ignore[import]
	add_user_to_group,
	create_group,
	create_user,
	modify_user,
	set_primary_group,
)
from opsicommon.server.setup import (
	setup_users_and_groups as po_setup_users_and_groups,  # type: ignore[import]
)
from rich import print as rich_print
from rich.prompt import Confirm, Prompt

from opsiconfd.backend.mysql import MySQLConnection
from opsiconfd.backend.mysql.cleanup import cleanup_database
from opsiconfd.backend.mysql.schema import create_database, update_database
from opsiconfd.config import (
	DEPOT_DIR,
	FILE_TRANSFER_STORAGE_DIR,
	FQDN,
	LOG_DIR,
	NTFS_IMAGES_DIR,
	OPSI_LICENSE_DIR,
	OPSICONFD_HOME,
	PUBLIC_DIR,
	REPOSITORY_DIR,
	TMP_DIR,
	VAR_ADDON_DIR,
	WORKBENCH_DIR,
	config,
	get_configserver_id,
	get_depotserver_id,
	opsi_config,
)
from opsiconfd.grafana import setup_grafana
from opsiconfd.logging import logger
from opsiconfd.metrics.statistics import setup_metric_downsampling
from opsiconfd.redis import delete_recursively
from opsiconfd.ssl import setup_ssl, setup_ssl_file_permissions
from opsiconfd.utils import get_random_string


def setup_limits() -> None:
	logger.info("Setup system limits")
	# The hard limit is the maximum value that is allowed for the soft limit. Any changes to the hard limit require root access.
	# The soft limit is the value that Linux uses to limit the system resources for running processes.
	# The soft limit cannot be greater than the hard limit.
	(soft_limit, hard_limit) = resource.getrlimit(resource.RLIMIT_NOFILE)
	if 0 < soft_limit < 10000:
		try:
			# ulimit -n 10000
			soft_limit = 10000
			resource.setrlimit(resource.RLIMIT_NOFILE, (soft_limit, max(hard_limit, soft_limit)))
			(soft_limit, hard_limit) = resource.getrlimit(resource.RLIMIT_NOFILE)
		except Exception as err:  # pylint: disable=broad-except
			logger.warning("Failed to set RLIMIT_NOFILE: %s", err)
	logger.info("Maximum number of open file descriptors: %s", soft_limit)


def setup_users_and_groups() -> None:
	logger.info("Setup users and groups")

	if config.run_as_user == "root":
		return

	user = None
	try:
		user = pwd.getpwnam(config.run_as_user)
	except KeyError:
		# User not found
		create_user(
			username=config.run_as_user,
			primary_groupname=opsi_config.get("groups", "fileadmingroup"),
			home=OPSICONFD_HOME,
			shell="/bin/bash",
			system=True,
		)
		user = pwd.getpwnam(config.run_as_user)

	if user and user.pw_dir != OPSICONFD_HOME:
		try:
			modify_user(username=config.run_as_user, home=OPSICONFD_HOME)
		except Exception as err:  # pylint: disable=broad-except
			logger.warning(
				"Failed to change home directory of user %r (%s). Should be %r but is %r, please change manually.",
				config.run_as_user,
				err,
				OPSICONFD_HOME,
				user.pw_dir,
			)

	try:
		grp.getgrnam("shadow")
	except KeyError:
		create_group(groupname="shadow", system=True)

	gids = os.getgrouplist(user.pw_name, user.pw_gid)
	for groupname in ("shadow", opsi_config.get("groups", "admingroup"), opsi_config.get("groups", "fileadmingroup")):
		logger.debug("Processing group %s", groupname)
		try:  # pylint: disable=loop-try-except-usage
			group = grp.getgrnam(groupname)  # pylint: disable=dotted-import-in-loop
			if group.gr_gid not in gids:
				add_user_to_group(config.run_as_user, groupname)
			if groupname == opsi_config.get("groups", "fileadmingroup") and user.pw_gid != group.gr_gid:
				try:  # pylint: disable=loop-try-except-usage
					set_primary_group(user.pw_name, opsi_config.get("groups", "fileadmingroup"))
				except Exception as err:  # pylint: disable=broad-except
					# Could be a user in active directory / ldap
					logger.debug(
						"Failed to set primary group of %s to %s: %s", user.pw_name, opsi_config.get("groups", "fileadmingroup"), err
					)
		except KeyError:
			logger.debug("Group not found: %s", groupname)


def _get_default_dirs() -> list[str]:
	return [
		f"/{LOG_DIR}/bootimage",
		f"/{LOG_DIR}/clientconnect",
		f"/{LOG_DIR}/instlog",
		f"/{LOG_DIR}/userlogin",
		os.path.dirname(config.log_file),
		TMP_DIR,
		DEPOT_DIR,
		NTFS_IMAGES_DIR,
		REPOSITORY_DIR,
		PUBLIC_DIR,
		WORKBENCH_DIR,
		VAR_ADDON_DIR,
		OPSI_LICENSE_DIR,
		OPSICONFD_HOME,
		FILE_TRANSFER_STORAGE_DIR,
	]


def setup_files() -> None:
	for _dir in _get_default_dirs():
		if not os.path.isdir(_dir) and not os.path.islink(_dir):  # pylint: disable=dotted-import-in-loop
			os.makedirs(_dir)  # pylint: disable=dotted-import-in-loop
			set_rights(_dir)


def setup_file_permissions() -> None:
	logger.info("Setup file permissions")

	dhcpd_config_file = locateDHCPDConfig("/etc/dhcp3/dhcpd.conf")
	permissions = (
		FilePermission("/etc/shadow", None, "shadow", 0o640),
		FilePermission(
			f"{os.path.dirname(config.log_file)}/opsiconfd.log", config.run_as_user, opsi_config.get("groups", "admingroup"), 0o660
		),
		# On many systems dhcpd is running as unprivileged user (i.e. dhcpd)
		# This user needs read permission
		FilePermission(dhcpd_config_file, config.run_as_user, opsi_config.get("groups", "admingroup"), 0o664),
		DirPermission(OPSICONFD_HOME, config.run_as_user, opsi_config.get("groups", "admingroup"), 0o660, 0o770),
		DirPermission(VAR_ADDON_DIR, config.run_as_user, opsi_config.get("groups", "fileadmingroup"), 0o660, 0o770),
	)
	PermissionRegistry().register_permission(*permissions)
	for permission in permissions:
		set_rights(permission.path)

	set_rights("/etc/opsi")
	setup_ssl_file_permissions()

	for path_str in _get_default_dirs():
		try:  # pylint: disable=loop-try-except-usage
			path = Path(path_str)
			if path.is_dir() and path.owner() != config.run_as_user:
				set_rights(str(path))
		except KeyError as err:
			logger.warning("Failed to set permissions on '%s': %s", str(path), err)


def setup_systemd() -> None:
	systemd_running = False
	for proc in psutil.process_iter():  # pylint: disable=dotted-import-in-loop
		if proc.name() == "systemd":
			systemd_running = True
			break
	if not systemd_running:
		logger.debug("Systemd not running")
		return

	logger.info("Setup systemd")
	subprocess.check_output(["systemctl", "daemon-reload"])
	subprocess.check_output(["systemctl", "enable", "opsiconfd.service"])


def setup_mysql_user(root_mysql: MySQLConnection) -> None:
	mysql = MySQLConnection()
	mysql.address = root_mysql.address
	mysql.database = root_mysql.database
	mysql.password = get_random_string(16)

	logger.info("Creating user %r and granting all rights on %r", mysql.username, mysql.database)
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
		logger.notice("User %r created and privileges set", mysql.username)

	mysql.update_config_file()


def setup_mysql_connection(interactive: bool = False) -> None:
	if opsi_config.get("host", "server-role") != "configserver":
		return

	error: Exception | None = None
	mysql = MySQLConnection()
	try:
		with mysql.connection():
			# OK
			return
	except Exception as err:  # pylint: disable=broad-except
		logger.error("Failed to connect to database: %s", err)
		error = err

	mysql_root = MySQLConnection()
	auto_try = False
	if mysql_root.address in ("localhost", "127.0.0.1", "::1"):
		# Try unix socket connection as user root
		auto_try = True
		mysql_root.database = "localhost"
		mysql_root.username = "root"
		mysql_root.password = ""
		logger.info("Trying to connect to local database as %s", mysql_root.username)

	while True:
		if not auto_try:
			if not interactive:
				raise error  # type: ignore[misc]
			error_str = str(error).split("\n", 1)[0]
			match = re.search(r"(\(\d+,\s.*)", error_str)  # pylint: disable=dotted-import-in-loop
			if match:
				error_str = match.group(1).strip("()")
			rich_print(f"[b][red]Failed to connect to database[/red]: {error_str}[/b]")
			if not Confirm.ask("Do you want to configure the database connection?"):
				raise error  # type: ignore[misc]
			mysql_root.address = Prompt.ask("Enter address", default=mysql_root.address, show_default=True)
			mysql_root.database = Prompt.ask("Enter database", default=mysql_root.database, show_default=True)
			mysql_root.username = Prompt.ask("Enter admin username", default="root", show_default=True)
			mysql_root.password = Prompt.ask("Enter admin password", password=True)
			mysql_root.password = ""
		try:  # pylint: disable=loop-try-except-usage
			with mysql_root.connection():
				if not auto_try:
					rich_print("[b][green]MySQL admin connection established[/green][/b]")
					rich_print("[b]Setting up MySQL user[/b]")
				setup_mysql_user(mysql_root)
				if not auto_try:
					rich_print("[b][green]MySQL user setup successful[/green][/b]")
				break
		except Exception as err:  # pylint: disable=broad-except,loop-invariant-statement
			if not auto_try:
				error = err

		auto_try = False
		mysql_root = MySQLConnection()


def setup_backend(full: bool) -> None:
	if opsi_config.get("host", "server-role") != "configserver":
		return

	mysql = MySQLConnection()
	with mysql.connection():
		create_database(mysql)
		update_database(mysql, force=full)
		cleanup_database(mysql)

		if not mysql.get_idents(table="HOST", object_type=OpsiConfigserver, ace=[], filter={"type": "OpsiConfigserver"}):
			config_server_id = get_configserver_id()
			logger.notice("Creating config:server %r", config_server_id)
			network_config = getNetworkConfiguration()
			config_server = OpsiConfigserver(
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
				hardwareAddress=network_config["hardwareAddress"],
				ipAddress=network_config["ipAddress"],
				inventoryNumber=None,
				networkAddress=f"{network_config['subnet']}/{network_config['netmask']}",
				maxBandwidth=0,
				isMasterDepot=True,
				masterDepotId=None,
			)
			mysql.insert_object(table="HOST", obj=config_server, ace=[], create=True, set_null=True)

		conf_servers = list(mysql.get_objects(table="HOST", object_type=OpsiConfigserver, ace=[], filter={"type": "OpsiConfigserver"}))
		if conf_servers and isinstance(conf_servers[0], OpsiConfigserver) and conf_servers[0].opsiHostKey:
			opsi_config.set("host", "key", conf_servers[0].opsiHostKey, persistent=True)


def cleanup_log_files() -> None:
	logger.info("Cleanup log files")
	now = time.time()
	min_mtime = now - 3600 * 24 * 30  # 30 days
	log_dir = os.path.dirname(config.log_file)
	if not os.path.isdir(log_dir):
		return
	links = []
	for filename in os.listdir(log_dir):  # pylint: disable=dotted-import-in-loop
		try:  # pylint: disable=loop-try-except-usage
			file = os.path.join(log_dir, filename)  # pylint: disable=dotted-import-in-loop
			if os.path.islink(file):  # pylint: disable=dotted-import-in-loop
				links.append(file)
			elif os.path.isfile(file) and os.path.getmtime(file) < min_mtime:  # pylint: disable=dotted-import-in-loop
				logger.info("Deleting old log file: %s", file)
				os.remove(file)  # pylint: disable=dotted-import-in-loop
		except Exception as err:  # pylint: disable=broad-except
			logger.warning(err)

	for link in links:
		try:  # pylint: disable=loop-try-except-usage
			dst = os.path.realpath(link)  # pylint: disable=dotted-import-in-loop
			if not os.path.exists(dst):  # pylint: disable=dotted-import-in-loop
				os.unlink(link)  # pylint: disable=dotted-import-in-loop
		except Exception as err:  # pylint: disable=broad-except
			logger.warning(err)


def _get_windows_domain() -> str | None:
	try:
		out = subprocess.run(["net", "getdomainsid"], capture_output=True, check=True).stdout.decode()
		match = re.search(r"domain\s(\S+)\s", out)
		if not match:
			match = re.search(r"machine\s(\S+)\s", out)
		if match:
			return match.group(1)
	except Exception as err:  # pylint: disable=broad-except
		logger.info("Could not get domain: %s", err)
	return None


def setup_configs() -> None:  # pylint: disable=too-many-statements,too-many-branches
	if opsi_config.get("host", "server-role") != "configserver":
		return

	from .backend import get_unprotected_backend  # pylint: disable=import-outside-toplevel

	backend = get_unprotected_backend()

	config_ids = set(backend.config_getIdents(returnType="str"))

	add_configs: list[BoolConfig | UnicodeConfig] = []
	add_config_states: list[ConfigState] = []

	if "clientconfig.depot.user" not in config_ids:
		logger.info("Creating config: clientconfig.depot.user")

		depot_user = "pcpatch"
		domain = _get_windows_domain()
		if domain:
			depot_user = f"{domain}\\{depot_user}"
		logger.info("Using '%s' as clientconfig.depot.user", depot_user)
		add_configs.append(
			UnicodeConfig(
				id="clientconfig.depot.user",
				description="User for depot share",
				possibleValues=[],
				defaultValues=[depot_user],
				editable=True,
				multiValue=False,
			)
		)

	if "clientconfig.configserver.url" not in config_ids:
		logger.info("Creating config: clientconfig.configserver.url")
		add_configs.append(
			UnicodeConfig(
				id="clientconfig.configserver.url",
				description="URL(s) of opsi config service(s) to use",
				possibleValues=[config.external_url],
				defaultValues=[config.external_url],
				editable=True,
				multiValue=True,
			)
		)

	if "clientconfig.depot.id" not in config_ids:
		logger.info("Creating config: clientconfig.depot.id")
		add_configs.append(
			UnicodeConfig(
				id="clientconfig.depot.id",
				description="ID of the opsi depot to use",
				possibleValues=[get_depotserver_id()],
				defaultValues=[get_depotserver_id()],
				editable=True,
				multiValue=False,
			)
		)

	if "clientconfig.depot.dynamic" not in config_ids:
		logger.info("Creating config: clientconfig.depot.dynamic")
		add_configs.append(BoolConfig(id="clientconfig.depot.dynamic", description="Use dynamic depot selection", defaultValues=[False]))

	if "clientconfig.depot.selection_mode" not in config_ids:
		logger.info("Creating config: clientconfig.depot.selection_mode")
		add_configs.append(
			UnicodeConfig(
				id="clientconfig.depot.selection_mode",
				description="Depot selection mode.",
				possibleValues=["master_and_latency", "latency", "network_address", "network_address_best_match", "random"],
				defaultValues=["network_address"],
				editable=False,
				multiValue=False,
			)
		)

	if "clientconfig.depot.drive" not in config_ids:
		logger.info("Creating config: clientconfig.depot.drive")
		add_configs.append(
			UnicodeConfig(
				id="clientconfig.depot.drive",
				description="Drive letter for depot share",
				possibleValues=[
					"a:",
					"b:",
					"c:",
					"d:",
					"e:",
					"f:",
					"g:",
					"h:",
					"i:",
					"j:",
					"k:",
					"l:",
					"m:",
					"n:",
					"o:",
					"p:",
					"q:",
					"r:",
					"s:",
					"t:",
					"u:",
					"v:",
					"w:",
					"x:",
					"y:",
					"z:",
					"dynamic",
				],
				defaultValues=["p:"],
				editable=False,
				multiValue=False,
			)
		)

	if "clientconfig.depot.protocol" not in config_ids:
		logger.info("Creating config: clientconfig.depot.protocol")
		add_configs.append(
			UnicodeConfig(
				id="clientconfig.depot.protocol",
				description="Protocol to use when mounting an depot share on the client",
				possibleValues=["cifs", "webdav"],
				defaultValues=["cifs"],
				editable=False,
				multiValue=False,
			)
		)

	if "clientconfig.depot.protocol.netboot" not in config_ids:
		logger.info("Creating config: clientconfig.depot.protocol.netboot")
		add_configs.append(
			UnicodeConfig(
				id="clientconfig.depot.protocol.netboot",
				description="Protocol to use when mounting an depot share in netboot environment",
				possibleValues=["cifs", "webdav"],
				defaultValues=["cifs"],
				editable=False,
				multiValue=False,
			)
		)

	if "clientconfig.windows.domain" not in config_ids:
		logger.info("Creating config: clientconfig.windows.domain")
		domain = _get_windows_domain()
		add_configs.append(
			UnicodeConfig(
				id="clientconfig.windows.domain",
				description="Windows domain",
				possibleValues=[],
				defaultValues=[domain] if domain else [],
				editable=True,
				multiValue=False,
			)
		)

	if "opsiclientd.global.verify_server_cert" not in config_ids:
		logger.info("Creating config: opsiclientd.global.verify_server_cert")
		add_configs.append(
			BoolConfig(id="opsiclientd.global.verify_server_cert", description="Verify opsi server TLS certificates", defaultValues=[True])
		)

	if "opsiclientd.global.install_opsi_ca_into_os_store" not in config_ids:
		logger.info("Creating config: opsiclientd.global.install_opsi_ca_into_os_store")
		add_configs.append(
			BoolConfig(
				id="opsiclientd.global.install_opsi_ca_into_os_store",
				description="Automatically install opsi CA into operating systems certificate store",
				defaultValues=[False],
			)
		)

	if "opsi-linux-bootimage.append" not in config_ids:
		logger.info("Creating config: opsi-linux-bootimage.append")
		add_configs.append(
			UnicodeConfig(
				id="opsi-linux-bootimage.append",
				description="Extra options to append to kernel command line",
				possibleValues=[
					"acpi=off",
					"irqpoll",
					"noapic",
					"pci=nomsi",
					"vga=normal",
					"reboot=b",
					"mem=2G",
					"nomodeset",
					"ramdisk_size=2097152",
					"dhclienttimeout=N",
				],
				defaultValues=[""],
				editable=True,
				multiValue=True,
			)
		)

	if "license-management.use" not in config_ids:
		logger.info("Creating config: license-management.use")
		add_configs.append(BoolConfig(id="license-management.use", description="Activate license management", defaultValues=[False]))

	if "software-on-demand.active" not in config_ids:
		logger.info("Creating config: software-on-demand.active")
		add_configs.append(BoolConfig(id="software-on-demand.active", description="Activate software-on-demand", defaultValues=[False]))

	if "software-on-demand.product-group-ids" not in config_ids:
		logger.info("Creating config: software-on-demand.product-group-ids")
		add_configs.append(
			UnicodeConfig(
				id="software-on-demand.product-group-ids",
				description=("Product group ids containing products which are " "allowed to be installed on demand"),
				possibleValues=["software-on-demand"],
				defaultValues=["software-on-demand"],
				editable=True,
				multiValue=True,
			)
		)

	if "clientconfig.dhcpd.filename" not in config_ids:
		logger.info("Creating config: clientconfig.dhcpd.filename")
		add_configs.append(
			UnicodeConfig(
				id="clientconfig.dhcpd.filename",
				description=(
					"The name of the file that will be presented to the "
					"client on an TFTP request. For an client that should "
					"boot via UEFI this must include the term 'elilo'."
				),
				possibleValues=["elilo"],
				defaultValues=[""],
				editable=True,
				multiValue=False,
			)
		)

	if add_configs:
		backend.config_createObjects(add_configs)
	if add_config_states:
		backend.configState_createObjects(add_config_states)

	remove_configs = []
	for config_id in config_ids:
		if config_id.endswith(".product.cache.outdated") or config_id == "product_sort_algorithm":
			logger.info("Removing config %r", config_id)
			remove_configs.append({"id": config_id})
	if remove_configs:
		backend.config_deleteObjects(remove_configs)


def setup_redis() -> None:
	# Delete obsolete keys
	for delete_key in ("status",):
		delete_recursively(delete_key)


def setup(full: bool = True) -> None:  # pylint: disable=too-many-branches,too-many-statements
	logger.notice("Running opsiconfd setup")

	if config.skip_setup:
		logger.notice("Skipping setup tasks: %s", ", ".join(config.skip_setup))

	if "all" in config.skip_setup:
		return

	if not config.run_as_user:
		config.run_as_user = getpass.getuser()

	if "limits" not in config.skip_setup:
		setup_limits()

	backend_available = True
	if opsi_config.get("host", "server-role") != "configserver":
		try:
			setup_mysql_connection(interactive=sys.stdout.isatty() and full)
		except Exception as err:  # pylint: disable=broad-except
			# This can happen during package installation
			# where backend config files are missing
			logger.debug("Failed to setup MySQL: %s", err, exc_info=True)
			backend_available = False
			if not full:
				raise

	if "backend" not in config.skip_setup and backend_available:
		try:
			setup_backend(full)
		except Exception as err:  # pylint: disable=broad-except
			# This can happen during package installation
			# where backend config files are missing
			logger.warning("Failed to setup backend: %s", err, exc_info=True)
			backend_available = False

	if full:
		if "users" not in config.skip_setup and "groups" not in config.skip_setup:
			po_setup_users_and_groups(ignore_errors=True)
			setup_users_and_groups()

		# po_setup_file_permissions() # takes very long with many files in /var/lib/opsi
		if "systemd" not in config.skip_setup:
			setup_systemd()
	else:
		if "users" not in config.skip_setup and "groups" not in config.skip_setup:
			setup_users_and_groups()

	if "files" not in config.skip_setup:
		setup_files()

	if "file_permissions" not in config.skip_setup:
		# Always correct file permissions (run_as_user could be changed)
		setup_file_permissions()

	if "log_files" not in config.skip_setup:
		cleanup_log_files()

	if backend_available:
		setup_configs()

	if "grafana" not in config.skip_setup:
		try:
			setup_grafana()
		except Exception as err:  # pylint: disable=broad-except
			logger.warning("Failed to setup grafana: %s", err, exc_info=True)

	setup_redis()

	if "metric_downsampling" not in config.skip_setup:
		try:
			setup_metric_downsampling()
		except Exception as err:  # pylint: disable=broad-except
			logger.warning("Failed to setup redis downsampling: %s", err, exc_info=True)

	try:
		setup_ssl()
	except Exception as err:  # pylint: disable=broad-except
		# This can fail if fqdn is not valid
		logger.error("Failed to setup ssl: %s", err, exc_info=True)
		logger.error("Failed to setup ssl: %s", err, exc_info=True)
		logger.error("Failed to setup ssl: %s", err, exc_info=True)
		logger.error("Failed to setup ssl: %s", err, exc_info=True)
