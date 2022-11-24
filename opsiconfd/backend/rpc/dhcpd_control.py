# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
opsiconfd.backend.rpc.extender
"""

from __future__ import annotations

import os
import socket
import sys
import threading
from contextlib import contextmanager
from fcntl import LOCK_EX, LOCK_NB, LOCK_UN, flock
from pathlib import Path
from time import sleep, time
from typing import TYPE_CHECKING, Any, Dict, Generator, List, Protocol

from OPSI.Exceptions import (  # type: ignore[import]
	BackendIOError,
	BackendUnableToConnectError,
)
from OPSI.Object import ConfigState, Host, OpsiClient  # type: ignore[import]
from OPSI.System import execute  # type: ignore[import]
from OPSI.System.Posix import (  # type: ignore[import]
	getDHCPDRestartCommand,
	locateDHCPDConfig,
)
from OPSI.Types import (  # type: ignore[import]
	forceBool,
	forceDict,
	forceHostId,
	forceList,
	forceObjectClass,
	forceObjectClassList,
)
from OPSI.Util.File import DHCPDConfFile  # type: ignore[import]
from opsicommon.client.jsonrpc import JSONRPCClient  # type: ignore[import]

from opsiconfd.config import FQDN, config
from opsiconfd.logging import logger

from . import rpc_method

if TYPE_CHECKING:
	from .protocol import BackendProtocol

WAIT_AFTER_RELOAD = 4.0


@contextmanager
def dhcpd_lock(lock_type: str = "") -> Generator[None, None, None]:
	lock_file = "/var/lock/opsi-dhcpd-lock"
	with open(lock_file, "a+", encoding="utf8") as lock_fh:
		try:
			os.chmod(lock_file, 0o666)
		except PermissionError:
			pass
		attempt = 0
		while True:
			attempt += 1
			try:  # pylint: disable=loop-try-except-usage
				flock(lock_fh, LOCK_EX | LOCK_NB)  # pylint: disable=loop-invariant-statement
				break
			except IOError:
				if attempt > 200:
					raise
				sleep(0.1)
		lock_fh.seek(0)
		lines = lock_fh.readlines()
		if len(lines) >= 100:
			lines = lines[-100:]
		lines.append(f"{time()};{os.path.basename(sys.argv[0])};{os.getpid()};{lock_type}\n")
		lock_fh.seek(0)
		lock_fh.truncate()
		lock_fh.writelines(lines)
		lock_fh.flush()
		yield None
		if lock_type == "config_reload":
			sleep(WAIT_AFTER_RELOAD)
		flock(lock_fh, LOCK_UN)
	# os.remove(lock_file)


class ReloadThread(threading.Thread):
	"""This class implements a thread regularly reloading the dhcpd.conf file."""

	def __init__(self, reload_config_command: str) -> None:
		threading.Thread.__init__(self)
		self._reload_config_command = reload_config_command
		self._reload_event = threading.Event()
		self._is_reloading = False
		self._wait_after_reload = WAIT_AFTER_RELOAD

	@property
	def is_busy(self) -> bool:
		return self._is_reloading or self._reload_event.is_set()

	def trigger_reload(self) -> None:
		"""Explicitely call a config file reload."""
		logger.debug("Reload triggered")
		if not self._reload_event.is_set():
			self._reload_event.set()

	def run(self) -> None:
		while True:
			if self._reload_event.wait(self._wait_after_reload):
				with dhcpd_lock("config_reload"):
					self._is_reloading = True  # pylint: disable=loop-invariant-statement
					self._reload_event.clear()
					try:  # pylint: disable=loop-try-except-usage
						logger.notice("Reloading dhcpd config using command: '%s'", self._reload_config_command)
						result = execute(self._reload_config_command)
						for line in result:
							if "error" in line:
								raise RuntimeError("\n".join(result))  # pylint: disable=loop-invariant-statement
					except Exception as err:  # pylint: disable=broad-except
						logger.critical("Failed to reload dhcpd config: %s", err)
					self._is_reloading = False  # pylint: disable=loop-invariant-statement


class RPCDHCPDControlMixin(Protocol):  # pylint: disable=too-many-instance-attributes
	_dhcpd_control_enabled: bool = False
	_dhcpd_control_dhcpd_config_file: str = "/etc/dhcp/dhcpd.conf"
	_dhcpd_control_reload_config_command: str | None = None
	_dhcpd_control_fixed_address_format: str = "IP"
	_dhcpd_control_default_client_parameters: dict[str, str] = {"next-server": FQDN, "filename": "linux/pxelinux.0"}
	_dhcpd_control_dhcpd_on_depot: bool = False
	_dhcpd_control_dhcpd_conf_file: DHCPDConfFile
	_dhcpd_control_reload_thread: ReloadThread | None
	_dhcpd_control_depot_connections: dict[str, JSONRPCClient]

	def __init__(self) -> None:
		# TODO:
		self._dhcpd_control_enabled = True
		self._dhcpd_control_default_client_parameters = {"next-server": FQDN, "filename": "linux/pxelinux.0"}
		self._dhcpd_control_dhcpd_config_file = locateDHCPDConfig(self._dhcpd_control_dhcpd_config_file)
		self._dhcpd_control_reload_config_command = f"/usr/bin/sudo {getDHCPDRestartCommand(default='/etc/init.d/dhcp3-server restart')}"
		self._read_dhcpd_control_config_file()

	def _read_dhcpd_control_config_file(self) -> None:
		mysql_conf = Path(config.backend_config_dir) / "dhcpd.conf"
		loc: Dict[str, Any] = {}
		exec(compile(mysql_conf.read_bytes(), "<string>", "exec"), None, loc)  # pylint: disable=exec-used

		for key, val in loc["config"].items():
			attr = "_dhcpd_control_" + "".join([f"_{c.lower()}" if c.isupper() else c for c in key])
			if attr == "_dhcpd_control_fixed_address_format" and val not in ("IP", "FQDN"):
				logger.error("Bad value %r for fixedAddressFormat, possible values are IP and FQDN", val)
				continue
			if attr == "_dhcpd_control_dhcpd_on_depot":
				val = forceBool(val)
			if hasattr(self, attr):
				setattr(self, attr, val)

		if os.path.exists(self._dhcpd_control_dhcpd_config_file):
			self._dhcpd_control_dhcpd_conf_file = DHCPDConfFile(self._dhcpd_control_dhcpd_config_file)
		else:
			logger.error("DHCPD config file %r not found, DHCPD control disabled", self._dhcpd_control_dhcpd_config_file)
			self._dhcpd_control_enabled = False

		self._dhcpd_control_reload_thread: ReloadThread | None = None
		self._dhcpd_control_depot_connections: dict[str, JSONRPCClient] = {}

	def _dhcpd_control_start_reload_thread(self) -> None:
		if not self._dhcpd_control_reload_config_command:
			return
		self._dhcpd_control_reload_thread = ReloadThread(self._dhcpd_control_reload_config_command)
		self._dhcpd_control_reload_thread.daemon = True
		self._dhcpd_control_reload_thread.start()

	def _dhcpd_control_trigger_reload(self) -> None:
		if not self._dhcpd_control_reload_thread:
			self._dhcpd_control_start_reload_thread()
		if self._dhcpd_control_reload_thread:
			self._dhcpd_control_reload_thread.trigger_reload()

	def _get_depot_jsonrpc_connection(self: BackendProtocol, depot_id: str) -> JSONRPCClient:
		depot_id = forceHostId(depot_id)
		if depot_id == self._depot_id:
			raise ValueError("Is local depot")

		if depot_id not in self._dhcpd_control_depot_connections:
			try:
				self._dhcpd_control_depot_connections[depot_id] = JSONRPCClient(
					address=f"https://{depot_id}:4447/rpc/backend/dhcpd", username=self._depot_id, password=self._opsi_host_key
				)
			except Exception as err:
				raise BackendUnableToConnectError(f"Failed to connect to depot '{depot_id}': {err}") from err
		return self._dhcpd_control_depot_connections[depot_id]

	def _get_responsible_depot_id(self: BackendProtocol, client_id: str) -> str | None:
		"""This method returns the depot a client is assigned to."""
		try:
			return self.configState_getClientToDepotserver(clientIds=[client_id])[0]["depotId"]
		except (IndexError, KeyError):
			return None

	def backend_exit(self) -> None:
		# TODO
		if self._dhcpd_control_reload_thread:
			logger.info("Waiting for reload thread")
			for _ in range(10):
				if self._dhcpd_control_reload_thread.is_busy:
					sleep(1)

	def dhcpd_control_hosts_updated(self: BackendProtocol, hosts: List[dict] | List[Host] | dict | Host) -> None:
		if not self._dhcpd_control_enabled:
			return
		hosts = forceObjectClassList(hosts, Host)
		delete_hosts = []
		for host in hosts:
			if not isinstance(host, OpsiClient):
				continue

			if not host.hardwareAddress:
				delete_hosts.append(host)
				continue

			if self._dhcpd_control_dhcpd_on_depot:
				depot_id = self._get_responsible_depot_id(host.id)
				if depot_id and depot_id != self._depot_id:
					logger.info("Not responsible for client '%s', forwarding request to depot '%s'", host.id, depot_id)
					self._get_depot_jsonrpc_connection(depot_id).execute_rpc(method="dhcpd_updateHost", params=[host])
					continue

			self.dhcpd_updateHost(host)

		if delete_hosts:
			self.dhcpd_control_hosts_deleted(delete_hosts)

	def dhcpd_control_hosts_deleted(self: BackendProtocol, hosts: List[dict] | List[Host] | dict | Host) -> None:
		if not self._dhcpd_control_enabled:
			return
		hosts = forceObjectClassList(hosts, Host)
		for host in hosts:
			if not isinstance(host, OpsiClient):
				continue

			if self._dhcpd_control_dhcpd_on_depot:
				# Call dhcpd_deleteHost on all non local depots
				for depot in self.host_getObjects(id=self._depot_id):
					if depot.id != self._depot_id:
						self._get_depot_jsonrpc_connection(depot.id).execute_rpc(method="dhcpd_deleteHost", params=[host])
			self.dhcpd_deleteHost(host)

	def dhcpd_control_config_states_updated(
		self: BackendProtocol, config_states: List[dict] | List[ConfigState] | dict | ConfigState
	) -> None:
		if not self._dhcpd_control_enabled:
			return
		object_ids = set()
		for config_state in forceList(config_states):
			if isinstance(config_state, ConfigState):
				if config_state.configId != "clientconfig.depot.id":
					continue
				if config_state.objectId:
					object_ids.add(config_state.objectId)
			else:
				if config_state.get("configId") != "clientconfig.depot.id":
					continue
				object_id = config_state.get("objectId")
				if object_id:
					object_ids.add(object_id)
		if not object_ids:
			return

		hosts = self.host_getObjects(id=list(object_ids))
		if hosts:
			self.dhcpd_control_hosts_updated(hosts)

	@rpc_method
	def dhcpd_updateHost(self: BackendProtocol, host: Host) -> None:  # pylint: disable=invalid-name,too-many-branches
		host = forceObjectClass(host, Host)

		if not host.hardwareAddress:
			logger.warning("Cannot update dhcpd configuration for client %s: hardware address unknown", host)
			return

		hostname = host.id.split(".", 1)[0]
		ip_address = host.ipAddress
		if not ip_address:
			try:
				logger.info("IP addess of client %s unknown, trying to get host by name", host)
				ip_address = socket.gethostbyname(host.id)
				logger.info("Client fqdn resolved to %s", ip_address)
			except Exception as err:  # pylint: disable=broad-except
				logger.debug("Failed to get IP by hostname: %s", err)
				with dhcpd_lock("config_read"):
					self._dhcpd_control_dhcpd_conf_file.parse()
					current_host_params = self._dhcpd_control_dhcpd_conf_file.getHost(hostname)

				if current_host_params:
					logger.debug("Trying to use address for %s from existing DHCP configuration.", hostname)

					if current_host_params.get("fixed-address"):
						ip_address = current_host_params["fixed-address"]
					else:
						raise BackendIOError(
							f"Cannot update dhcpd configuration for client {host.id}: "
							"ip address unknown and failed to get ip address from DHCP configuration file."
						) from err
				else:
					raise BackendIOError(
						f"Cannot update dhcpd configuration for client {host.id}: " "ip address unknown and failed to get host by name"
					) from err

		fixed_address = ip_address
		if self._dhcpd_control_fixed_address_format == "FQDN":
			fixed_address = host.id

		parameters = forceDict(self._dhcpd_control_default_client_parameters)
		if not self._dhcpd_control_dhcpd_on_depot:
			try:
				depot_id = self._get_responsible_depot_id(host.id)
				if depot_id:
					depot = self.host_getObjects(id=depot_id)[0]
					if depot.ipAddress:
						parameters["next-server"] = depot.ipAddress
			except Exception as err:  # pylint: disable=broad-except
				logger.error("Failed to get depot info: %s", err, exc_info=True)

		with dhcpd_lock("config_update"):
			try:
				self._dhcpd_control_dhcpd_conf_file.parse()
				current_host_params = self._dhcpd_control_dhcpd_conf_file.getHost(hostname)
				if (
					current_host_params
					and (current_host_params.get("hardware", " ").split(" ")[1] == host.hardwareAddress)
					and (current_host_params.get("fixed-address") == fixed_address)
					and (current_host_params.get("next-server") == parameters.get("next-server"))
				):

					logger.debug("DHCPD config of host '%s' unchanged, no need to update config file", host)
					return

				self._dhcpd_control_dhcpd_conf_file.addHost(
					hostname=hostname,
					hardwareAddress=host.hardwareAddress,
					ipAddress=ip_address,
					fixedAddress=fixed_address,
					parameters=parameters,
				)
				self._dhcpd_control_dhcpd_conf_file.generate()
			except Exception as err:  # pylint: disable=broad-except
				logger.error(err, exc_info=True)

		self._dhcpd_control_trigger_reload()

	@rpc_method
	def dhcpd_deleteHost(self: BackendProtocol, host: Host) -> None:  # pylint: disable=invalid-name
		host = forceObjectClass(host, Host)

		with dhcpd_lock("config_update"):
			try:
				self._dhcpd_control_dhcpd_conf_file.parse()
				hostname = host.id.split(".", 1)[0]
				if not self._dhcpd_control_dhcpd_conf_file.getHost(hostname):
					return
				self._dhcpd_control_dhcpd_conf_file.deleteHost(hostname)
				self._dhcpd_control_dhcpd_conf_file.generate()
			except Exception as err:  # pylint: disable=broad-except
				logger.error(err, exc_info=True)

		self._dhcpd_control_trigger_reload()