# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
session handling
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
import uuid
from collections import namedtuple
from time import sleep as time_sleep
from typing import Any, Optional

import msgspec
import pyotp
from fastapi import FastAPI, HTTPException, status
from fastapi.exceptions import ValidationError
from fastapi.requests import HTTPConnection
from fastapi.responses import (
	JSONResponse,
	PlainTextResponse,
	RedirectResponse,
	Response,
)
from opsicommon.exceptions import (  # type: ignore[import]
	BackendAuthenticationError,
	BackendPermissionDeniedError,
)
from opsicommon.logging import secret_filter, set_context  # type: ignore[import]
from opsicommon.objects import Host, OpsiClient, User  # type: ignore[import]
from opsicommon.utils import ip_address_in_network, timestamp
from redis import ResponseError as RedisResponseError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import Message, Receive, Scope, Send

from opsiconfd import contextvar_client_session, server_timing
from opsiconfd.addon import AddonManager
from opsiconfd.application import MaintenanceState
from opsiconfd.application import app as opsiconfd_app
from opsiconfd.auth import AuthenticationModule
from opsiconfd.auth.ldap import LDAPAuthentication
from opsiconfd.auth.pam import PAMAuthentication
from opsiconfd.backend import (
	get_unprotected_backend,  # pylint: disable=import-outside-toplevel
)
from opsiconfd.config import config, opsi_config
from opsiconfd.logging import logger
from opsiconfd.redis import async_redis_client, ip_address_to_redis_key, redis_client
from opsiconfd.utils import asyncio_create_task, utc_time_timestamp

# https://github.com/tiangolo/fastapi/blob/master/docs/tutorial/middleware.md
#
# You can add middleware to FastAPI applications.
#
# A "middleware" is a function that works with every request before it is processed by any specific path operation.
# And also with every response before returning it.
#
# 	It takes each request that comes to your application.
# 	It can then do something to that request or run any needed code.
# 	Then it passes the request to be processed by the rest of the application (by some path operation).
# 	It then takes the response generated by the application (by some path operation).
# 	It can do something to that response or run any needed code.
# 	Then it returns the response.

ACCESS_ROLE_PUBLIC = "public"
ACCESS_ROLE_AUTHENTICATED = "authenticated"
ACCESS_ROLE_ADMIN = "admin"
SESSION_COOKIE_NAME = "opsiconfd-session"
SESSION_COOKIE_ATTRIBUTES = ("SameSite=Strict", "Secure")
# Zsync2 will send "curl/<curl-version>" as User-Agent.
# RedHat / Alma / Rocky package manager will send "libdnf (<os-version>)".
# Do not keep sessions because they will never send a cookie (session id).
# If we keep the session, we may reach the maximum number of sessions per ip.
SESSION_UNAWARE_USER_AGENTS = ("libdnf", "curl")
# Store ip addresses of depots with last access time
depot_addresses: dict[str, float] = {}

session_data_msgpack_encoder = msgspec.msgpack.Encoder()
session_data_msgpack_decoder = msgspec.msgpack.Decoder()

BasicAuth = namedtuple("BasicAuth", ["username", "password"])


def get_basic_auth(headers: Headers) -> BasicAuth:
	auth_header = headers.get("authorization")

	headers_401 = {}
	if headers.get("X-Requested-With", "").lower() != "xmlhttprequest":
		headers_401 = {"WWW-Authenticate": 'Basic realm="opsi", charset="UTF-8"'}

	if not auth_header:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Authorization header missing",
			headers=headers_401,
		)

	if not auth_header.startswith("Basic "):
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Authorization method unsupported",
			headers=headers_401,
		)

	encoded_auth = auth_header[6:]  # Stripping "Basic "
	secret_filter.add_secrets(encoded_auth)
	auth = base64.decodebytes(encoded_auth.encode("ascii")).decode("utf-8")

	if auth.count(":") == 6:
		# Seems to be a mac address as username
		username, password = auth.rsplit(":", 1)
	else:
		username, password = auth.split(":", 1)
	secret_filter.add_secrets(password)

	return BasicAuth(username, password)


async def get_session(client_addr: str, client_port: int, headers: Headers, session_id: Optional[str] = None) -> "OPSISession":
	session = OPSISession(client_addr=client_addr, client_port=client_port, headers=headers, session_id=session_id)
	await session.init()
	assert session.client_addr == client_addr

	contextvar_client_session.set(session)

	if session.user_agent and session.user_agent.startswith(SESSION_UNAWARE_USER_AGENTS):
		session.persistent = False
		logger.debug("Not keeping session for client %s (%s)", client_addr, session.user_agent)

	return session


class SessionMiddleware:
	def __init__(self, app: FastAPI, public_path: list[str] | None = None) -> None:
		self.app = app
		self._public_path = public_path or []

	@staticmethod
	def get_session_id_from_headers(headers: Headers) -> Optional[str]:
		# connection.cookies.get(SESSION_COOKIE_NAME, None)
		# Not working for opsi-script, which sometimes sends:
		# 'NULL; opsiconfd-session=7b9efe97a143438684267dfb71cbace2'
		# Workaround:
		session_cookie_name = SESSION_COOKIE_NAME
		cookies = headers.get("cookie")
		if cookies:
			for cookie in cookies.split(";"):
				cookie_l = cookie.strip().split("=", 1)
				if len(cookie_l) == 2:
					if cookie_l[0].strip().lower() == session_cookie_name:
						return cookie_l[1].strip().lower()
		return None

	async def handle_request(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
		self, connection: HTTPConnection, receive: Receive, send: Send
	) -> None:
		if isinstance(opsiconfd_app.app_state, MaintenanceState):
			client_in_exceptions = False
			for network in opsiconfd_app.app_state.address_exceptions or []:
				if ip_address_in_network(connection.scope["client"][0], network):
					client_in_exceptions = True
					break
			if not client_in_exceptions:
				raise HTTPException(
					status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
					detail=opsiconfd_app.app_state.message,
					headers={"Retry-After": str(opsiconfd_app.app_state.retry_after)},
				)

		with server_timing("session_handling") as timing:
			scope = connection.scope
			scope["session"] = None
			logger.trace("SessionMiddleware %s", scope)

			await check_network(scope["client"][0])

			if scope["type"] not in ("http", "websocket"):
				await self.app(scope, receive, send)
				return

			# Set default access role
			required_access_role = ACCESS_ROLE_ADMIN
			access_role_public = ACCESS_ROLE_PUBLIC
			if scope["full_path"]:
				if scope["full_path"] == "/":
					required_access_role = access_role_public
				for pub_path in self._public_path:
					if scope["full_path"].startswith(pub_path):
						required_access_role = access_role_public
						break
			scope["required_access_role"] = required_access_role

			if scope["full_path"].startswith(("/rpc", "/monitoring", "/messagebus")) or (
				scope["full_path"].startswith(("/depot", "/boot")) and scope.get("method") in ("GET", "HEAD", "OPTIONS", "PROPFIND")
			):
				scope["required_access_role"] = ACCESS_ROLE_AUTHENTICATED

			# Get session
			session_id = self.get_session_id_from_headers(connection.headers)
			if scope["required_access_role"] != ACCESS_ROLE_PUBLIC or session_id:
				addr = scope["client"]
				scope["session"] = await get_session(
					client_addr=addr[0], client_port=addr[1], headers=connection.headers, session_id=session_id
				)

			started_authenticated = scope["session"] and scope["session"].authenticated

			# Addon request processing
			if scope["full_path"].startswith("/addons"):
				addon = AddonManager().get_addon_by_path("/".join(scope["full_path"].split("/", 3)[:3]))
				if addon:
					logger.debug("Calling %s.handle_request for path '%s'", addon, scope["full_path"])
					if await addon.handle_request(connection, receive, send):
						return

			await check_access(connection)
			if (
				scope["session"]
				and required_access_role == ACCESS_ROLE_ADMIN
				and not scope["session"].host
				and scope["full_path"].startswith("/depot")
				and opsi_config.get("groups", "fileadmingroup") not in scope["session"].user_groups
			):
				raise BackendPermissionDeniedError(f"Not a file admin user '{scope['session'].username}'")

		if started_authenticated and timing["session_handling"] > 1000:
			logger.warning("Session handling took %0.2fms", timing["session_handling"])

		async def send_wrapper(message: Message) -> None:
			if message["type"] == "http.response.start":
				headers = MutableHeaders(scope=message)
				if scope["session"]:
					await scope["session"].store()
					scope["session"].add_cookie_to_headers(headers)
				if scope.get("response-headers"):
					for key, value in scope["response-headers"].items():
						headers.append(key, value)
			await send(message)

		await self.app(scope, receive, send_wrapper)

	async def handle_request_exception(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
		self, err: Exception, connection: HTTPConnection, receive: Receive, send: Send
	) -> None:
		logger.debug("Handle request exception %s: %s", err.__class__.__name__, err, exc_info=True)
		scope = connection.scope
		if scope["full_path"].startswith("/addons"):
			addon = AddonManager().get_addon_by_path(scope["full_path"])
			if addon:
				logger.debug("Calling %s.handle_request_exception for path '%s'", addon, scope["full_path"])
				if await addon.handle_request_exception(err, connection, receive, send):
					return

		status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
		headers = None
		error = None

		if isinstance(err, (BackendAuthenticationError, BackendPermissionDeniedError)):
			log = logger.warning

			if scope["path"]:
				if scope.get("method") == "MKCOL" and scope["path"].lower().endswith("/system volume information"):
					# Windows WebDAV client is trying to create "System Volume Information"
					log = logger.debug
				elif scope.get("method") == "PROPFIND" and scope["path"] == "/":
					# Windows WebDAV client PROPFIND /
					log = logger.debug
			log(err)

			if isinstance(err, BackendAuthenticationError) or not scope["session"] or not scope["session"].authenticated:
				cmd = (
					f"ts.add {config.redis_key('stats')}:client:failed_auth:{ip_address_to_redis_key(scope['client'][0])} "
					f"* 1 RETENTION 86400000 LABELS client_addr {scope['client'][0]}"
				)
				logger.debug(cmd)
				redis = await async_redis_client()
				await redis.execute_command(cmd)  # type: ignore[no-untyped-call]
				await asyncio.sleep(0.2)

			status_code = status.HTTP_401_UNAUTHORIZED
			if connection.headers.get("X-Requested-With", "").lower() != "xmlhttprequest":
				headers = {"WWW-Authenticate": 'Basic realm="opsi", charset="UTF-8"'}
			error = "Authentication error"
			if isinstance(err, BackendPermissionDeniedError):
				error = "Permission denied"

		elif isinstance(err, ConnectionRefusedError):
			status_code = status.HTTP_403_FORBIDDEN
			error = str(err)

		elif isinstance(err, ValidationError):
			status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
			error = str(err)

		elif isinstance(err, HTTPException):
			status_code = err.status_code  # pylint: disable=no-member
			headers = err.headers  # pylint: disable=no-member
			error = err.detail

		else:
			logger.error(err, exc_info=True)
			error = str(err)

		headers = headers or {}

		if scope["type"] == "websocket":
			# Uvicorn (0.20.0) always closes websockets with code 403
			# There is currently no way to send a custom status code or headers
			websocket_close_code = status.WS_1008_POLICY_VIOLATION
			reason = error
			if status_code == status.HTTP_500_INTERNAL_SERVER_ERROR:
				websocket_close_code = status.WS_1011_INTERNAL_ERROR
			elif status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
				websocket_close_code = status.WS_1013_TRY_AGAIN_LATER
				reason = f"{reason[:100]}\nRetry-After: {headers.get('Retry-After')}"
			# reason max length 123 bytes
			logger.debug("Closing websocket with code=%r and reason=%r", websocket_close_code, reason)
			try:
				# Need to open (accept) websocket before close
				await send({"type": "websocket.accept"})
			except RuntimeError:
				# Alread accepted
				pass
			try:
				return await send({"type": "websocket.close", "code": websocket_close_code, "reason": reason})
			except RuntimeError:
				# Alread closed (can happen on shutdown)
				pass

		if scope.get("session"):
			scope["session"].add_cookie_to_headers(headers)

		response: Optional[Response] = None
		if scope["full_path"].startswith("/rpc"):
			logger.debug("Returning jsonrpc response because path startswith /rpc")
			content = {"id": None, "result": None, "error": error}
			if scope.get("jsonrpc20"):
				content["jsonrpc"] = "2.0"
				del content["result"]
			response = JSONResponse(status_code=status_code, content=content, headers=headers)
		if not response:
			if connection.headers.get("accept") and "application/json" in connection.headers.get("accept", ""):
				logger.debug("Returning json response because of accept header")
				response = JSONResponse(status_code=status_code, content={"error": error}, headers=headers)
		if (
			not response
			and status_code == status.HTTP_401_UNAUTHORIZED
			and scope["full_path"]
			and scope["full_path"].lower().split("#", 1)[0].rstrip("/") in ("/admin", "/admin/grafana")
		):
			response = RedirectResponse(f"/login?redirect={scope['full_path']}", headers=headers)
		if not response:
			logger.debug("Returning plaintext response")
			response = PlainTextResponse(status_code=status_code, content=error, headers=headers)
		await response(scope, receive, send)

	async def __call__(
		self, scope: Scope, receive: Receive, send: Send
	) -> None:  # pylint: disable=too-many-locals, too-many-branches, too-many-statements
		if scope["type"] == "lifespan":
			return await self.app(scope, receive, send)

		try:
			connection = HTTPConnection(scope)
			set_context({"client_address": scope["client"][0]})
			await self.handle_request(connection, receive, send)
		except Exception as err:  # pylint: disable=broad-except
			await self.handle_request_exception(err, connection, receive, send)


class OPSISession:  # pylint: disable=too-many-instance-attributes
	_store_interval = 30

	def __init__(  # pylint: disable=too-many-arguments
		self, client_addr: str, client_port: int, headers: Optional[Headers] = None, session_id: Optional[str] = None
	) -> None:
		self.client_addr = client_addr
		self.client_port = client_port
		self._headers = headers or Headers()
		self.session_id: str | None = session_id or None
		self.user_agent = self._headers.get("user-agent") or ""
		self.max_age = config.session_lifetime
		self.client_max_age = 0
		self.created = 0
		self.deleted = False
		self.persistent = True
		self.last_used = 0
		self.last_stored = 0
		self.username: str | None = None
		self.password: str | None = None
		self.user_groups: set[str] = set()
		self.host: Host | None = None
		self.authenticated = False
		self.is_admin = False
		self.is_read_only = False
		self._redis_expiration_seconds = 3600

	def __repr__(self) -> str:
		return f"<{self.__class__.__name__} at {hex(id(self))} created={self.created} last_used={self.last_used}>"

	@property
	def redis_key(self) -> str:
		assert self.session_id
		return f"{config.redis_key('session')}:{ip_address_to_redis_key(self.client_addr)}:{self.session_id}"

	@property
	def expired(self) -> bool:
		return self.validity <= 0

	@property
	def validity(self) -> int:
		return int(self.max_age - (utc_time_timestamp() - self.last_used))

	def serialize(self) -> dict[str, Any]:
		ser = {k: v for k, v in self.__dict__.items() if k[0] != "_" and k != "password"}
		ser["host"] = ser["host"].to_hash() if ser["host"] else None
		ser["user_groups"] = list(ser["user_groups"])
		return ser

	@classmethod
	def deserialize(cls, data: dict[str, Any]) -> dict[str, Any]:
		des = {}
		for attr, val in data.items():
			if attr == "host" and val:
				val = Host.fromHash(val)
			if attr == "user_groups":
				val = set(val)
			des[attr] = val
		return des

	@classmethod
	def from_serialized(cls, data: dict[str, Any]) -> OPSISession:
		data = cls.deserialize(data)
		obj = cls(data["client_addr"], data["client_port"])
		for attr, val in data.items():
			setattr(obj, attr, val)
		return obj

	def get_cookie(self) -> Optional[str]:
		if not self.session_id or not self.persistent:
			return None
		attrs = "; ".join(SESSION_COOKIE_ATTRIBUTES)
		if attrs:
			attrs += "; "

		# A zero or negative number will expire the cookie immediately
		max_age = 0 if self.deleted else self.max_age
		return f"{SESSION_COOKIE_NAME}={self.session_id}; {attrs}path=/; Max-Age={max_age}"

	def add_cookie_to_headers(self, headers: dict[str, str]) -> None:
		cookie = self.get_cookie()
		# Keep current set-cookie header if already set
		if cookie and "set-cookie" not in headers:
			headers["set-cookie"] = cookie

	async def init(self) -> None:
		if self.session_id is None:
			logger.debug("Session id missing (%s / %s)", self.client_addr, self.user_agent)
			await self.init_new_session()
		else:
			if await self.load():
				if self.expired:
					logger.debug("Session expired: %s (%s / %s)", self, self.client_addr, self.user_agent)
					await self.init_new_session()
				else:
					logger.debug("Reusing session: %s (%s / %s)", self, self.client_addr, self.user_agent)
			else:
				logger.debug("Session not found: %s (%s / %s)", self, self.client_addr, self.user_agent)
				await self.init_new_session()
		self._update_max_age()
		await self.update_last_used(False)

	def _reset_auth_data(self) -> None:
		self.username = None
		self.password = None
		self.user_groups = set()
		self.host = None
		self.authenticated = False
		self.is_admin = False
		self.is_read_only = False

	def _init_new_session(self) -> None:
		"""Generate a new session id if number of client sessions is less than max client sessions."""
		self._reset_auth_data()

		max_session_per_ip = config.max_session_per_ip
		if config.max_sessions_excludes and self.client_addr in config.max_sessions_excludes:
			logger.debug("Disable max_session_per_ip for address: %s", self.client_addr)
			max_session_per_ip = 0
		elif self.client_addr in depot_addresses:
			# Connection from a known depot server address
			if time.time() - depot_addresses[self.client_addr] <= config.session_lifetime:
				logger.debug("Disable max_session_per_ip for depot server: %s", self.client_addr)
				max_session_per_ip = 0
			else:
				# Address information is outdated
				del depot_addresses[self.client_addr]

		session_count = 0
		try:
			with redis_client() as redis:
				now = utc_time_timestamp()
				session_key = f"{config.redis_key('session')}:{ip_address_to_redis_key(self.client_addr)}:*"
				for redis_key in redis.scan_iter(session_key):
					validity = 0
					try:
						data = redis.get(redis_key)
					except RedisResponseError:
						data = None
					if data:
						sess = session_data_msgpack_decoder.decode(data)
						try:
							validity = sess["max_age"] - (now - sess["last_used"])
						except Exception as err:  # pylint: disable=broad-except
							logger.debug(err)
					if validity > 0:
						session_count += 1
					else:
						redis.delete(redis_key)

			if max_session_per_ip > 0 and session_count + 1 > max_session_per_ip:  # pylint: disable=chained-comparison
				error = f"Too many sessions from {self.client_addr} / {self.user_agent}, maximum is: {max_session_per_ip}"
				logger.warning(error)
				raise ConnectionRefusedError(error)
		except ConnectionRefusedError as err:
			raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(err)) from err

		self.session_id = str(uuid.uuid4()).replace("-", "")
		self.created = int(utc_time_timestamp())
		logger.confidential("Generated a new session id %s for %s / %s", self.session_id, self.client_addr, self.user_agent)

	async def init_new_session(self) -> None:
		await run_in_threadpool(self._init_new_session)

	def _load(self) -> bool:
		with redis_client() as redis:
			msgpack_data = redis.get(self.redis_key)
		if not msgpack_data:
			return False

		data = self.deserialize(session_data_msgpack_decoder.decode(msgpack_data))
		for attr, val in data.items():
			try:
				setattr(self, attr, val)
			except AttributeError:
				pass

		if not self.last_stored:
			self.last_stored = int(utc_time_timestamp())

		return True

	async def load(self) -> bool:
		# aioredis is sometimes slow ~300ms load, using redis for now
		return await run_in_threadpool(self._load)

	def _store(self) -> None:
		if self.deleted or not self.persistent:
			return
		self.last_stored = int(utc_time_timestamp())
		# Remember that the session data in redis may have been
		# changed by another worker process since the last load.
		# Read session from redis if available and update session data.
		with redis_client() as redis:
			session_data = {}
			data = redis.get(self.redis_key)
			if data:
				session_data = session_data_msgpack_decoder.decode(data)
			new_data = self.serialize()
			new_data["created"] = session_data.get("created", new_data["created"])
			redis.set(self.redis_key, session_data_msgpack_encoder.encode(new_data), ex=self._redis_expiration_seconds)

	async def store(self, wait: Optional[bool] = True) -> None:
		# aioredis is sometimes slow ~300ms load, using redis for now
		task = run_in_threadpool(self._store)
		if wait:
			await task
		else:
			asyncio_create_task(task)

	def sync_delete(self) -> None:
		with redis_client() as redis:
			for _ in range(10):
				redis.delete(self.redis_key)
				time_sleep(0.01)
				# Be sure to delete key
				if not redis.exists(self.redis_key):
					break
		self.deleted = True

	async def delete(self) -> None:
		return await run_in_threadpool(self.sync_delete)

	async def update_last_used(self, store: Optional[bool] = None) -> None:
		self.last_used = int(utc_time_timestamp())
		if store or (store is None and self.last_used - self.last_stored > self._store_interval):
			await self.store()

	def _update_max_age(self) -> None:
		x_opsi_session_lifetime = self._headers.get("x-opsi-session-lifetime")
		if not x_opsi_session_lifetime:
			return

		try:
			session_lifetime = int(x_opsi_session_lifetime)
			if 0 < session_lifetime <= 3600 * 24:
				logger.info("Accepting session lifetime %d from client", session_lifetime)
				self.max_age = session_lifetime
			else:
				logger.warning("Not accepting session lifetime %d from client", session_lifetime)
		except ValueError:
			logger.warning("Invalid x-opsi-session-lifetime header with value '%s' from client", x_opsi_session_lifetime)


auth_module: AuthenticationModule | None = None  # pylint: disable=invalid-name


def get_auth_module() -> AuthenticationModule:
	global auth_module  # pylint: disable=invalid-name,global-statement

	if not auth_module:
		try:
			ldap_conf = opsi_config.get("ldap_auth")
			if ldap_conf["ldap_url"]:
				logger.debug("Using LDAP auth with config: %s", ldap_conf)
				if "directory-connector" in get_unprotected_backend().available_modules:
					auth_module = LDAPAuthentication(**ldap_conf)
				else:
					logger.error("Disabling LDAP authentication: directory-connector module not available")
		except Exception as err:  # pylint: disable=broad-except
			logger.debug(err)

		if not auth_module:
			auth_module = PAMAuthentication()

	return auth_module.get_instance()


async def authenticate_host(scope: Scope) -> None:  # pylint: disable=too-many-branches,too-many-statements
	session = scope["session"]
	backend = get_unprotected_backend()

	hosts = []
	host_filter = {}
	if config.allow_host_key_only_auth:
		session.username = "<host-key-only-auth>"
		logger.debug("Trying to authenticate host by opsi host key only")
		host_filter["opsiHostKey"] = session.password
	elif re.search(r"^[a-fA-F0-9]{2}(:[a-fA-F0-9]{2}){5}$", session.username):
		logger.debug("Trying to authenticate host by mac address and opsi host key")
		host_filter["hardwareAddress"] = session.username
	else:
		logger.debug("Trying to authenticate host by host id and opsi host key")
		session.username = session.username.rstrip(".")
		host_filter["id"] = session.username

	hosts = await backend.async_call("host_getObjects", **host_filter)
	if not hosts:
		raise BackendAuthenticationError(f"Host not found '{session.username}'")
	if len(hosts) > 1:
		raise BackendAuthenticationError(f"More than one matching host object found '{session.username}'")
	host = hosts[0]
	if not host.opsiHostKey:
		raise BackendAuthenticationError(f"OpsiHostKey missing for host '{host.id}'")

	logger.confidential(
		"Host '%s' authentication: password sent '%s', host key '%s', onetime password '%s'",
		host.id,
		session.password,
		host.opsiHostKey,
		host.oneTimePassword if host.getType() == "OpsiClient" else "n.a.",
	)

	if host.opsiHostKey and session.password == host.opsiHostKey:
		logger.info("Host '%s' authenticated by host key", host.id)
	elif isinstance(host, OpsiClient) and host.oneTimePassword and session.password == host.oneTimePassword:
		logger.info("Host '%s' authenticated by onetime password", host.id)
		host.oneTimePassword = ""
		await backend.async_call("host_updateObject", host=host)
	else:
		raise BackendAuthenticationError(f"Authentication of host '{host.id}' failed")

	session.host = host
	session.authenticated = True
	session.is_read_only = False
	session.is_admin = host.getType() in ("OpsiConfigserver", "OpsiDepotserver")
	if session.username != host.id:
		session.username = host.id
		if not scope.get("response-headers"):
			scope["response-headers"] = {}
		scope["response-headers"]["x-opsi-new-host-id"] = session.username

	if host.getType() == "OpsiClient":
		logger.info("OpsiClient authenticated, updating host object")
		host.setLastSeen(timestamp())
		if config.update_ip and host.ipAddress not in (None, "127.0.0.1", "::1", host.ipAddress):
			host.setIpAddress(host.ipAddress)
		else:
			# Value None on update means no change!
			host.ipAddress = None
		await backend.async_call("host_updateObject", host=host)

	elif host.getType() in ("OpsiConfigserver", "OpsiDepotserver"):
		logger.debug("Storing depot server address: %s", session.client_addr)
		depot_addresses[session.client_addr] = time.time()


async def authenticate_user_passwd(scope: Scope) -> None:
	session = scope["session"]
	backend = get_unprotected_backend()
	credentials = await backend.async_call("user_getCredentials", username=session.username)
	if credentials and session.password == credentials.get("password"):
		session.authenticated = True
		session.is_read_only = False
		session.is_admin = False
	else:
		raise BackendAuthenticationError(f"Authentication failed for user {session.username}")


async def authenticate_user_auth_module(scope: Scope) -> None:
	session = scope["session"]
	authm = get_auth_module()

	if not authm:
		raise BackendAuthenticationError("Authentication module unavailable")

	logger.debug("Trying to authenticate by user authentication module %s", authm)

	try:
		await run_in_threadpool(authm.authenticate, session.username, session.password)
	except Exception as err:
		raise BackendAuthenticationError(f"Authentication failed for user '{session.username}': {err}") from err

	# Authentication did not throw exception => authentication successful
	session.authenticated = True
	session.user_groups = authm.get_groupnames(session.username)
	session.is_admin = authm.user_is_admin(session.username)
	session.is_read_only = authm.user_is_read_only(session.username)

	logger.info(
		"Authentication successful for user '%s', groups '%s', admin group is '%s', admin: %s, readonly groups %s, readonly: %s",
		session.username,
		",".join(session.user_groups),
		authm.get_admin_groupname(),
		session.is_admin,
		authm.get_read_only_groupnames(),
		session.is_read_only,
	)


async def authenticate(  # pylint: disable=unused-argument,too-many-branches
	scope: Scope, username: str, password: str, mfa_otp: str | None = None
) -> None:
	headers = Headers(scope=scope)
	if not scope["session"]:
		addr = scope["client"]
		scope["session"] = await get_session(client_addr=addr[0], client_port=addr[1], headers=headers)
	session = scope["session"]
	session.authenticated = False

	# Check if client address is blocked
	await check_blocked(session.client_addr)

	username = session.username = (username or "").lower()
	password = session.password = password or ""

	logger.info("Start authentication of client %s", session.client_addr)

	if not session.password:
		raise BackendAuthenticationError("No password specified")

	if session.username == config.monitoring_user:
		await authenticate_user_passwd(scope=scope)
	elif (
		re.search(r"^[^.]+\.[^.]+\.\S+$", session.username)
		or re.search(r"^[a-fA-F0-9]{2}(:[a-fA-F0-9]{2}){5}$", session.username)
		or not session.username
	):
		await authenticate_host(scope=scope)
	else:
		if config.multi_factor_auth in ("totp_optional", "totp_mandatory"):
			if not mfa_otp:
				mfa_otp = headers.get("x-opsi-mfa-otp")
			if not mfa_otp:
				logger.info("Assuming that TOTP is attached to password")
				match = re.search(r"^(.+)(\d{6})$", session.password)
				if match:
					session.password = match.group(1)
					secret_filter.add_secrets(session.password)
					mfa_otp = match.group(2)

		await authenticate_user_auth_module(scope=scope)

		backend = get_unprotected_backend()
		now = timestamp()
		users = await backend.async_call("user_getObjects", id=session.username)
		if users:
			user = users[0]
		else:
			user = User(id=username, created=now)
			await backend.async_call("user_insertObject", user=user)

		if config.multi_factor_auth in ("totp_optional", "totp_mandatory"):
			if config.multi_factor_auth == "totp_mandatory" or user.mfaState == "totp_active":
				if not user.otpSecret:
					raise BackendAuthenticationError("MFA OTP configuration error")
				if not mfa_otp:
					raise BackendAuthenticationError("MFA one-time password missing")
				totp = pyotp.TOTP(user.otpSecret)
				if not totp.verify(mfa_otp):
					raise BackendAuthenticationError("Incorrect one-time password")
				logger.info("OTP MFA successful")

		user.lastLogin = now
		await backend.async_call("user_updateObject", user=user)

	if not session.username or not session.authenticated:
		raise BackendPermissionDeniedError("Not authenticated")

	logger.debug("Client %s authenticated, username: %s", session.client_addr, session.username)

	await check_admin_networks(session)


async def check_admin_networks(session: OPSISession) -> None:
	if not session.is_admin or not config.admin_networks:
		return

	is_admin_network = False
	for network in config.admin_networks:
		if ip_address_in_network(session.client_addr, network):
			is_admin_network = True
			break

	if not is_admin_network:
		logger.warning(
			"User '%s' from '%s' not in admin network '%s'",
			session.username,
			session.client_addr,
			config.admin_networks,
		)
		session.is_admin = False
		admin_group = opsi_config.get("groups", "admingroup")
		if admin_group in session.user_groups:
			# Remove admin group from groups because acl.conf currently does not support is_admin
			session.user_groups.remove(admin_group)


async def check_blocked(ip_address: str) -> None:
	logger.info("Checking if client '%s' is blocked", ip_address)
	redis = await async_redis_client()
	is_blocked = bool(await redis.get(f"{config.redis_key('stats')}:client:blocked:{ip_address_to_redis_key(ip_address)}"))
	if is_blocked:
		logger.info("Client '%s' is blocked", ip_address)
		raise ConnectionRefusedError(f"Client '{ip_address}' is blocked")

	now = round(time.time()) * 1000
	cmd = (
		f"ts.range {config.redis_key('stats')}:client:failed_auth:{ip_address_to_redis_key(ip_address)} "
		f"{(now-(config.auth_failures_interval*1000))} {now} aggregation count {(config.auth_failures_interval*1000)}"
	)
	logger.debug(cmd)
	try:
		data = await redis.execute_command(cmd)  # type: ignore[no-untyped-call]
		num_failed_auth = int(data[-1][1])
		logger.debug("num_failed_auth: %s", num_failed_auth)
	except RedisResponseError as err:
		num_failed_auth = 0
		if "key does not exist" not in str(err):
			raise
	except IndexError as err:
		logger.debug(err)
		num_failed_auth = 0
	if num_failed_auth >= config.max_auth_failures:
		is_blocked = True
		logger.warning("Blocking client '%s' for %0.2f minutes", ip_address, (config.client_block_time / 60))
		await redis.setex(f"{config.redis_key('stats')}:client:blocked:{ip_address_to_redis_key(ip_address)}", config.client_block_time, 1)


async def check_network(client_addr: str) -> None:
	if not config.networks:
		return
	for network in config.networks:
		if ip_address_in_network(client_addr, network):
			return
	raise ConnectionRefusedError(f"Host '{client_addr}' is not allowed to connect")


async def check_access(connection: HTTPConnection) -> None:
	scope = connection.scope
	if scope["required_access_role"] == ACCESS_ROLE_PUBLIC:
		return

	session = connection.scope["session"]

	if not session.username or not session.authenticated:
		auth = get_basic_auth(connection.headers)
		await authenticate(connection.scope, auth.username, auth.password)

	if scope["required_access_role"] == ACCESS_ROLE_ADMIN and not session.is_admin:
		raise BackendPermissionDeniedError(f"Not an admin user '{session.username}' {scope.get('method')} {scope.get('path')}")
