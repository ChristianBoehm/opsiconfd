# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
opsiconfd.backend.rpc.extender
"""

from __future__ import annotations

import base64
import glob
import os
import pwd
import re
import shutil
import socket
import time
from datetime import datetime
from functools import lru_cache
from hashlib import md5
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generator, Protocol
from uuid import UUID

from Crypto.Hash import MD5
from Crypto.Signature import pkcs1_15
from OPSI import __version__ as PYTHON_OPSI_VERSION  # type: ignore[import]
from OPSI.Util import (  # type: ignore[import]
	blowfishDecrypt,
	blowfishEncrypt,
	getPublicKey,
)
from OPSI.Util.File import ConfigFile  # type: ignore[import]
from OPSI.Util.Log import truncateLogData  # type: ignore[import]
from opsicommon.exceptions import (  # type: ignore[import]
	BackendAuthenticationError,
	BackendBadValueError,
	BackendMissingDataError,
	BackendPermissionDeniedError,
)
from opsicommon.license import (  # type: ignore[import]
	OPSI_CLIENT_INACTIVE_AFTER,
	OPSI_MODULE_IDS,
	OPSI_OBSOLETE_MODULE_IDS,
	get_default_opsi_license_pool,
)
from opsicommon.logging import secret_filter  # type: ignore[import]
from opsicommon.types import (  # type: ignore[import]
	forceBool,
	forceHostId,
	forceObjectId,
)

from opsiconfd import contextvar_client_address, contextvar_client_session
from opsiconfd.application import AppState
from opsiconfd.application.filetransfer import delete_file, prepare_file
from opsiconfd.backup import create_backup, restore_backup
from opsiconfd.check import health_check
from opsiconfd.config import (
	FILE_TRANSFER_STORAGE_DIR,
	FQDN,
	LOG_DIR,
	LOG_SIZE_HARD_LIMIT,
	OPSI_LICENSE_DIR,
	OPSI_MODULES_FILE,
	OPSI_PASSWD_FILE,
	config,
	opsi_config,
)
from opsiconfd.logging import logger
from opsiconfd.ssl import get_ca_cert_as_pem

from . import rpc_method

if TYPE_CHECKING:
	from .protocol import BackendProtocol

LOG_TYPES = {  # key = logtype, value = requires objectId for read
	"bootimage": True,
	"clientconnect": True,
	"instlog": True,
	"opsiconfd": False,
	"userlogin": True,
	"winpe": True,
}
PASSWD_LINE_REGEX = re.compile(r"^\s*([^:]+)\s*:\s*(\S+)\s*$")


class RPCGeneralMixin(Protocol):  # pylint: disable=too-many-public-methods
	opsi_modules_file: str = OPSI_MODULES_FILE
	opsi_license_path: str = OPSI_LICENSE_DIR

	@rpc_method
	def backend_createBase(self) -> None:  # pylint: disable=invalid-name
		return None

	@rpc_method
	def backend_deleteBase(self) -> None:  # pylint: disable=invalid-name
		return None

	@rpc_method
	def backend_getInterface(self: BackendProtocol) -> list[dict[str, Any]]:  # pylint: disable=invalid-name
		return self.get_interface()

	@rpc_method
	def backend_exit(self: BackendProtocol) -> None:
		session = contextvar_client_session.get()
		if session:
			session.sync_delete()

	@rpc_method(deprecated=True)
	def backend_setOptions(self: BackendProtocol, options: dict) -> None:  # pylint: disable=invalid-name
		return None

	@rpc_method(deprecated=True)
	def backend_getOptions(self: BackendProtocol) -> dict:  # pylint: disable=invalid-name
		return {}

	@rpc_method(check_acl=False)
	def backend_getSystemConfiguration(self: BackendProtocol) -> dict:  # pylint: disable=invalid-name
		"""
		Returns current system configuration.

		This holds information about server-side settings that may be relevant for clients.

		Under the key `log` information about log settings will be returned in form of a dict.
		In it under `size_limit` you will find the amount of bytes currently allowed as maximum log size.
		Under `types` you will find a list with currently supported log types.

		:rtype: dict
		"""
		return {"log": {"size_limit": config.max_log_size, "keep_rotated": config.keep_rotated_logs, "types": list(LOG_TYPES)}}

	@rpc_method(check_acl=False)
	def accessControl_authenticated(self: BackendProtocol) -> bool:  # pylint: disable=invalid-name
		session = contextvar_client_session.get()
		if not session or not session.authenticated:
			raise BackendAuthenticationError("Not authenticated")
		return True

	@rpc_method(check_acl=False)
	def accessControl_userIsAdmin(self: BackendProtocol) -> bool:  # pylint: disable=invalid-name
		session = contextvar_client_session.get()
		if not session:
			raise BackendPermissionDeniedError("Access denied")
		return session.is_admin

	@rpc_method(check_acl=False)
	def accessControl_userIsReadOnlyUser(self: BackendProtocol) -> bool:  # pylint: disable=invalid-name
		session = contextvar_client_session.get()
		if not session:
			raise BackendPermissionDeniedError("Access denied")
		return session.is_read_only

	@rpc_method(check_acl=False)
	def accessControl_getUserGroups(self: BackendProtocol) -> list[str]:  # pylint: disable=invalid-name
		session = contextvar_client_session.get()
		if not session:
			raise BackendPermissionDeniedError("Access denied")
		return list(session.user_groups)

	@rpc_method
	def service_checkHealth(self: BackendProtocol) -> dict:  # pylint: disable=invalid-name
		self._check_role("admin")
		return health_check()

	@rpc_method
	def service_createBackup(  # pylint: disable=invalid-name
		self: BackendProtocol,
		config_files: bool = True,
		maintenance_mode: bool = True,
		password: str | None = None,
		return_type: str = "file_id",
	) -> dict[str, dict[str, Any]] | str:
		self._check_role("admin")
		session = contextvar_client_session.get()
		if not session:
			raise BackendPermissionDeniedError("Access denied")

		file_id = None
		file_encoding = "msgpack"
		file_compression = "lz4"
		backup_file = None
		if return_type == "file_id":
			now = datetime.now().strftime("%Y%m%d-%H%M%S")
			file_id, backup_file = prepare_file(
				filename=f"opsiconfd-backup-{now}.{file_encoding}.{file_compression}{'.aes' if password else ''}",
				content_type="binary/octet-stream",
			)

		data = create_backup(
			config_files=config_files,
			backup_file=backup_file,
			file_encoding=file_encoding,  # type: ignore[arg-type]
			file_compression=file_compression,  # type: ignore[arg-type]
			password=password,
			maintenance=maintenance_mode,
			maintenance_address_exceptions=["::1/128", "127.0.0.1/32", session.client_addr],
		)
		if file_id:
			return file_id
		return data

	@rpc_method
	def service_restoreBackup(  # pylint: disable=invalid-name,too-many-arguments
		self: BackendProtocol,
		data_or_file_id: dict[str, dict[str, Any]] | str,
		config_files: bool = False,
		server_id: str = "backup",
		password: str | None = None,
		batch: bool = True,
	) -> None:
		self._check_role("admin")
		session = contextvar_client_session.get()
		if not session:
			raise BackendPermissionDeniedError("Access denied")

		data_or_file: dict[str, dict[str, Any]] | Path = {}
		file_id = None
		if isinstance(data_or_file_id, str):
			file_id = UUID(data_or_file_id)
			data_or_file = Path(FILE_TRANSFER_STORAGE_DIR) / str(file_id)
			if not data_or_file.exists():
				raise ValueError("Invalid file ID")
		else:
			data_or_file = data_or_file_id

		restore_backup(
			data_or_file=data_or_file,
			config_files=config_files,
			server_id=server_id,
			password=password,
			batch=batch,
			maintenance_address_exceptions=["::1/128", "127.0.0.1/32", session.client_addr],
		)
		if file_id:
			delete_file(file_id)

	@rpc_method
	def service_setAppState(  # pylint: disable=invalid-name
		self: BackendProtocol, app_state: dict[str, Any], wait_accomplished: float = 30.0
	) -> dict[str, Any]:
		self._check_role("admin")
		self._app.set_app_state(AppState.from_dict(app_state), wait_accomplished=wait_accomplished)
		return self._app.app_state.to_dict()

	@rpc_method
	def getDomain(self: BackendProtocol) -> str:  # pylint: disable=invalid-name
		try:
			client_address = contextvar_client_address.get()
			if not client_address:
				raise ValueError("Failed to get client address")
			if client_address not in ("127.0.0.1", "::1"):
				names = socket.gethostbyaddr(client_address)
				if names[0] and names[0].count(".") >= 2:
					return ".".join(names[0].split(".")[1:])
		except Exception as err:  # pylint: disable=broad-except
			logger.debug("Failed to get domain by client address: %s", err)

		return ".".join(FQDN.split(".")[1:])

	@rpc_method
	def getOpsiCACert(self: BackendProtocol) -> str:  # pylint: disable=invalid-name
		return get_ca_cert_as_pem()

	# @rpc_method
	def getData(self: BackendProtocol, query: str) -> Generator[Any, None, None]:  # pylint: disable=invalid-name
		if not query.lower().strip().startswith(("select", "show", "pragma")):
			raise ValueError("Only queries to SELECT/SHOW/PRAGMA data are allowed.")

		with self._mysql.session() as session:
			for row in session.execute(query).fetchall():
				yield {k: v.strftime("%Y-%m-%d %H:%M:%S") if isinstance(v, datetime) else v for k, v in dict(row).items()}

	# @rpc_method
	def getRawData(self: BackendProtocol, query: str) -> Generator[Any, None, None]:  # pylint: disable=invalid-name
		if not query.lower().strip().startswith(("select", "show", "pragma")):
			raise ValueError("Only queries to SELECT/SHOW/PRAGMA data are allowed.")

		with self._mysql.session() as session:
			for row in session.execute(query).fetchall():
				yield {v.strftime("%Y-%m-%d %H:%M:%S") if isinstance(v, datetime) else v for v in list(row)}

	def _get_client_info(self: BackendProtocol) -> dict[str, int]:
		logger.info("%s fetching client info", self)
		now = datetime.now()
		client_ids = [
			host.id
			for host in self.host_getObjects(attributes=["id", "lastSeen"], type="OpsiClient")
			if (host.lastSeen and now - datetime.fromisoformat(host.lastSeen)).days < OPSI_CLIENT_INACTIVE_AFTER
		]
		macos = 0
		linux = 0
		if client_ids:
			macos = len(
				self.productOnClient_getObjects(
					attributes=["clientId"], installationStatus="installed", productId="opsi-mac-client-agent", clientId=client_ids
				)
			)
			linux = len(
				self.productOnClient_getObjects(
					attributes=["clientId"], installationStatus="installed", productId="opsi-linux-client-agent", clientId=client_ids
				)
			)
		return {"macos": macos, "linux": linux, "windows": len(client_ids) - macos - linux}

	@lru_cache(maxsize=10)
	def _get_licensing_info(
		self: BackendProtocol, licenses: bool = False, legacy_modules: bool = False, dates: bool = False, ttl_hash: int = 0
	) -> dict[str, Any]:
		"""
		Returns opsi licensing information.
		"""
		del ttl_hash  # ttl_hash is only used to invalidate the cache after a ttl
		pool = get_default_opsi_license_pool(
			license_file_path=self.opsi_license_path, modules_file_path=self.opsi_modules_file, client_info=self._get_client_info
		)

		for config_id in ("client_limit_warning_percent", "client_limit_warning_absolute"):
			try:  # pylint: disable=loop-try-except-usage
				setattr(pool, config_id, int(self.config_getObjects(id=f"licensing.{config_id}")[0].getDefaultValues()[0]))
			except Exception as err:  # pylint: disable=broad-except
				logger.debug(err)

		try:
			disable_warning_for_modules = [
				m for m in self.config_getObjects(id="licensing.disable_warning_for_modules")[0].getDefaultValues() if m in OPSI_MODULE_IDS
			]
		except Exception as err:  # pylint: disable=broad-except
			logger.debug(err)
			disable_warning_for_modules = []  # pylint: disable=use-tuple-over-list

		try:
			client_limit_warning_days = int(self.config_getObjects(id="licensing.client_limit_warning_days")[0].getDefaultValues()[0])
		except Exception as err:  # pylint: disable=broad-except
			logger.debug(err)
			client_limit_warning_days = 30

		modules = pool.get_modules()
		info: dict[str, Any] = {
			"client_numbers": pool.client_numbers,
			"known_modules": OPSI_MODULE_IDS,
			"obsolete_modules": OPSI_OBSOLETE_MODULE_IDS,
			"available_modules": [module_id for module_id, info in modules.items() if info["available"]],
			"modules": modules,
			"licenses_checksum": pool.get_licenses_checksum(),
			"config": {
				"client_limit_warning_percent": pool.client_limit_warning_percent,
				"client_limit_warning_absolute": pool.client_limit_warning_absolute,
				"client_limit_warning_days": client_limit_warning_days,
				"disable_warning_for_modules": disable_warning_for_modules,
			},
		}
		if licenses:
			info["licenses"] = [lic.to_dict(serializable=True, with_state=True) for lic in pool.get_licenses()]
		if legacy_modules:
			info["legacy_modules"] = pool.get_legacy_modules()
		if dates:
			info["dates"] = {}
			for at_date in pool.get_relevant_dates():
				info["dates"][str(at_date)] = {"modules": pool.get_modules(at_date=at_date)}  # pylint: disable=loop-invariant-statement
		return info

	def get_licensing_info(  # pylint: disable=invalid-name
		self: BackendProtocol, licenses: bool = False, legacy_modules: bool = False, dates: bool = False, allow_cache: bool = True
	) -> dict[str, Any]:
		pool = get_default_opsi_license_pool(
			license_file_path=self.opsi_license_path, modules_file_path=self.opsi_modules_file, client_info=self._get_client_info
		)
		if not allow_cache or pool.modified():
			self._get_licensing_info.cache_clear()
		if pool.modified():
			pool.load()

		def get_ttl_hash(seconds: int = 3600) -> int:
			"""Return the same value withing `seconds` time period"""
			return round(time.time() / seconds)

		return self._get_licensing_info(licenses=licenses, legacy_modules=legacy_modules, dates=dates, ttl_hash=get_ttl_hash())

	@rpc_method
	def backend_getLicensingInfo(  # pylint: disable=invalid-name
		self: BackendProtocol, licenses: bool = False, legacy_modules: bool = False, dates: bool = False, allow_cache: bool = True
	) -> dict[str, Any]:
		return self.get_licensing_info(licenses=licenses, legacy_modules=legacy_modules, dates=dates, allow_cache=allow_cache)

	@rpc_method
	def backend_info(self: BackendProtocol) -> dict[str, Any]:  # pylint: disable=too-many-branches,too-many-statements
		"""
		Get info about the used opsi version and the licensed modules.

		:rtype: dict
		"""
		modules: dict[str, str | bool] = {"valid": False}
		helpermodules = {}

		if os.path.exists(self.opsi_modules_file):
			try:
				with open(self.opsi_modules_file, encoding="utf-8") as modules_file:
					for line in modules_file:
						line = line.strip()
						if "=" not in line:
							logger.error("Found bad line '%s' in modules file '%s'", line, self.opsi_modules_file)
							continue
						(module, state) = line.split("=", 1)
						module = module.strip().lower()
						state = state.strip()
						if module in ("signature", "customer", "expires"):
							modules[module] = state
							continue
						state = state.lower()
						if state not in ("yes", "no"):
							try:  # pylint: disable=loop-try-except-usage
								helpermodules[module] = state
								state = int(state)  # type: ignore[assignment]
							except ValueError:
								logger.error("Found bad line '%s' in modules file '%s'", line, self.opsi_modules_file)
								continue
						if isinstance(state, int):
							modules[module] = state > 0
						else:
							modules[module] = state == "yes"

				if not modules.get("signature"):
					modules = {"valid": False}
					raise ValueError("Signature not found")
				if not modules.get("customer"):
					modules = {"valid": False}
					raise ValueError("Customer not found")
				if (
					modules.get("expires", "") != "never"
					and time.mktime(time.strptime(str(modules.get("expires", "2000-01-01")), "%Y-%m-%d")) - time.time() <= 0
				):
					modules = {"valid": False}
					raise ValueError("Signature expired")

				public_key = getPublicKey(
					data=base64.decodebytes(
						b"AAAAB3NzaC1yc2EAAAADAQABAAABAQCAD/I79Jd0eKwwfuVwh5B2z+S8aV0C5suItJa18RrYip+d4P0ogzqoCfOoVWtDo"
						b"jY96FDYv+2d73LsoOckHCnuh55GA0mtuVMWdXNZIE8Avt/RzbEoYGo/H0weuga7I8PuQNC/nyS8w3W8TH4pt+ZCjZZoX8"
						b"S+IizWCYwfqYoYTMLgB0i+6TCAfJj3mNgCrDZkQ24+rOFS4a8RrjamEz/b81noWl9IntllK1hySkR+LbulfTGALHgHkDU"
						b"lk0OSu+zBPw/hcDSOMiDQvvHfmR4quGyLPbQ2FOVm1TzE0bQPR+Bhx4V8Eo2kNYstG2eJELrz7J1TJI0rCjpB+FQjYPsP"
					)
				)
				data = ""
				mks = list(modules.keys())
				mks.sort()
				for module in mks:
					if module in ("valid", "signature"):
						continue
					if module in helpermodules:
						val = helpermodules[module]
					else:
						val = modules[module]  # type: ignore[assignment]
						if isinstance(val, bool):
							val = "yes" if val else "no"
					data += f"{module.lower().strip()} = {val}\r\n"

				modules["valid"] = False
				if modules["signature"].startswith("{"):  # type: ignore[union-attr]
					s_bytes = int(modules["signature"].split("}", 1)[-1]).to_bytes(256, "big")  # type: ignore[union-attr]
					try:
						pkcs1_15.new(public_key).verify(MD5.new(data.encode()), s_bytes)
						modules["valid"] = True
					except ValueError:
						# Invalid signature
						pass
				else:
					h_int = int.from_bytes(md5(data.encode()).digest(), "big")
					s_int = public_key._encrypt(int(modules["signature"]))  # pylint: disable=protected-access
					modules["valid"] = h_int == s_int

			except Exception as err:  # pylint: disable=broad-except
				logger.error("Failed to read opsi modules file '%s': %s", self.opsi_modules_file, err)
		else:
			logger.info("Opsi modules file '%s' not found", self.opsi_modules_file)

		return {"opsiVersion": PYTHON_OPSI_VERSION, "modules": modules, "realmodules": helpermodules}

	@rpc_method
	def log_write(  # pylint: disable=invalid-name,too-many-branches
		self: BackendProtocol, logType: str, data: str, objectId: str | None = None, append: bool = False
	) -> None:
		"""
		Write log data into the corresponding log file.

		:param logType: Type of log. Currently supported: *bootimage*, *clientconnect*, *instlog*, *opsiconfd* or *userlogin*.
		:param data: Log content
		:type data: Unicode
		:param objectId: Specialising of ``logType``
		:param append: Changes the behaviour to either append or overwrite the log.
		:type append: bool
		"""
		logType = str(logType)
		if logType not in LOG_TYPES:
			raise BackendBadValueError(f"Unknown log type '{logType}'")

		if not objectId:
			raise BackendBadValueError(f"Writing {logType} log requires an objectId")
		objectId = forceObjectId(objectId)

		append = forceBool(append)

		bdata = data.encode("utf-8", "replace")
		if len(bdata) > LOG_SIZE_HARD_LIMIT:
			bdata = bdata[-1 * LOG_SIZE_HARD_LIMIT :]
			idx = bdata.find(b"\n")
			if idx > 0:
				bdata = bdata[idx + 1 :]

		log_file = os.path.join(LOG_DIR, logType, f"{objectId}.log")

		if not os.path.exists(os.path.dirname(log_file)):
			os.mkdir(os.path.dirname(log_file), 0o2770)

		try:
			if not append or (append and os.path.exists(log_file) and os.path.getsize(log_file) + len(bdata) > config.max_log_size):
				logger.info("Rotating file '%s'", log_file)
				if config.keep_rotated_logs <= 0:
					os.remove(log_file)
				else:
					for num in range(config.keep_rotated_logs, 0, -1):
						src_file_path = log_file
						if num > 1:
							src_file_path = f"{log_file}.{num-1}"
						if not os.path.exists(src_file_path):  # pylint: disable=dotted-import-in-loop
							continue
						dst_file_path = f"{log_file}.{num}"
						os.rename(src_file_path, dst_file_path)  # pylint: disable=dotted-import-in-loop
						try:  # pylint: disable=loop-try-except-usage
							shutil.chown(  # pylint: disable=dotted-import-in-loop
								dst_file_path, -1, opsi_config.get("groups", "admingroup")
							)
							os.chmod(dst_file_path, 0o644)  # pylint: disable=dotted-import-in-loop
						except Exception as err:  # pylint: disable=broad-except
							logger.error("Failed to set file permissions on '%s': %s", dst_file_path, err)

			for filename in glob.glob(f"{log_file}.*"):  # pylint: disable=dotted-import-in-loop,loop-invariant-statement
				try:  # pylint: disable=loop-try-except-usage
					if int(filename.split(".")[-1]) > config.keep_rotated_logs:
						os.remove(filename)  # pylint: disable=dotted-import-in-loop
				except ValueError:
					os.remove(filename)  # pylint: disable=dotted-import-in-loop
		except Exception as err:  # pylint: disable=broad-except
			logger.error("Failed to rotate log files: %s", err)

		with open(log_file, mode="ab" if append else "wb") as file:
			file.write(bdata)

		try:
			shutil.chown(log_file, group=opsi_config.get("groups", "admingroup"))
			os.chmod(log_file, 0o640)
		except Exception as err:  # pylint: disable=broad-except
			logger.error("Failed to set file permissions on '%s': %s", log_file, err)

	@rpc_method
	def log_read(self: BackendProtocol, logType: str, objectId: str | None = None, maxSize: int = 0) -> str:  # pylint: disable=invalid-name
		"""
		Return the content of a log.

		:param logType: Type of log. Currently supported: *bootimage*, *clientconnect*, *instlog*, *opsiconfd* or *userlogin*.
		:type data: Unicode
		:param objectId: Specialising of ``logType``
		:param maxSize: Limit for the size of returned characters in bytes. Setting this to `0` disables limiting.
		"""
		log_type = str(logType)
		max_size = int(maxSize)

		if log_type not in LOG_TYPES:
			raise BackendBadValueError(f"Unknown log type '{log_type}'")

		if objectId:
			objectId = forceObjectId(objectId)
			log_file = os.path.join(LOG_DIR, log_type, f"{objectId}.log")
		else:
			if LOG_TYPES[log_type]:
				raise BackendBadValueError(f"Log type '{log_type}' requires objectId")
			log_file = os.path.join(LOG_DIR, log_type, "opsiconfd.log")

		if not os.path.exists(log_file):
			return ""
		with open(log_file, encoding="utf-8", errors="replace") as log:
			data = log.read()

		if len(data) > max_size > 0:
			return truncateLogData(data, max_size)

		return data

	@rpc_method
	def user_getCredentials(  # pylint: disable=invalid-name
		self: BackendProtocol, username: str = "pcpatch", hostId: str | None = None
	) -> dict[str, str]:
		"""
		Get the credentials of an opsi user.
		The information is stored in ``/etc/opsi/passwd``.

		:param hostId: Optional value that should be the calling host.
		:return: Dict with the keys *password* and *rsaPrivateKey*.
		If this is called with an valid hostId the data will be encrypted with the opsi host key.
		:rtype: dict
		"""
		username = str(username)
		if hostId:
			hostId = forceHostId(hostId)

		result = {"password": "", "rsaPrivateKey": ""}

		for line in ConfigFile(filename=OPSI_PASSWD_FILE).parse():  # pylint: disable=loop-global-usage
			match = PASSWD_LINE_REGEX.search(line)  # pylint: disable=loop-global-usage
			if match is None:
				continue

			if match.group(1) == username:
				result["password"] = match.group(2)  # pylint: disable=loop-invariant-statement
				break

		if not result["password"]:
			raise BackendMissingDataError(f"Username '{username}' not found in '{OPSI_PASSWD_FILE}'")

		depot = self.host_getObjects(id=self._depot_id)
		if not depot:
			raise BackendMissingDataError(f"Depot '{self._depot_id}'' not found in backend")
		depot = depot[0]
		if not depot.opsiHostKey:
			raise BackendMissingDataError(f"Host key for depot '{self._depot_id}' not found")

		result["password"] = blowfishDecrypt(depot.opsiHostKey, result["password"])

		if username == "pcpatch":
			try:
				id_rsa = os.path.join(pwd.getpwnam(username)[5], ".ssh", "id_rsa")
				with open(id_rsa, encoding="utf-8") as file:
					result["rsaPrivateKey"] = file.read()
			except Exception as err:  # pylint: disable=broad-except
				logger.debug(err)

		if hostId:
			host = self.host_getObjects(id=hostId)
			try:
				host = host[0]
			except IndexError as err:
				raise BackendMissingDataError(f"Host '{hostId}' not found in backend") from err

			result["password"] = blowfishEncrypt(host.opsiHostKey, result["password"])
			if result["rsaPrivateKey"]:
				result["rsaPrivateKey"] = blowfishEncrypt(host.opsiHostKey, result["rsaPrivateKey"])

		return result

	@rpc_method
	def user_setCredentials(self: BackendProtocol, username: str, password: str) -> None:  # pylint: disable=invalid-name
		"""
		Set the password of an opsi user.
		The information is stored in ``/etc/opsi/passwd``.
		The password will be encrypted with the opsi host key of the depot where the method is.
		"""
		username = str(username).lower()
		password = str(password)
		secret_filter.add_secrets(password)

		if '"' in password:
			raise ValueError("Character '\"' not allowed in password")

		try:
			depot = self.host_getObjects(id=self._depot_id)[0]
		except IndexError as err:
			raise BackendMissingDataError(f"Depot {self._depot_id} not found in backend") from err

		encoded_password = blowfishEncrypt(depot.opsiHostKey, password)

		conf_file = ConfigFile(filename=OPSI_PASSWD_FILE)
		lines = []
		try:
			for line in conf_file.readlines():
				match = PASSWD_LINE_REGEX.search(line)  # pylint: disable=loop-global-usage
				if not match or match.group(1) != username:
					lines.append(line.rstrip())
		except FileNotFoundError:
			pass

		lines.append(f"{username}:{encoded_password}")
		conf_file.open("w")
		conf_file.writelines(lines)
		conf_file.close()
