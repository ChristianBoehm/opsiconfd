# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
session handling
"""

import asyncio
import base64
import time
import uuid
from collections import namedtuple
from time import sleep as time_sleep
from typing import Any, Dict, List, Optional

import aioredis
from fastapi import HTTPException, status
from fastapi.exceptions import ValidationError
from fastapi.requests import HTTPConnection
from fastapi.responses import (
	JSONResponse,
	PlainTextResponse,
	RedirectResponse,
	Response,
)
from msgpack import dumps as msgpack_dumps  # type: ignore[import]
from msgpack import loads as msgpack_loads  # type: ignore[import]
from OPSI.Backend.Manager.AccessControl import UserStore  # type: ignore[import]
from OPSI.Config import FILE_ADMIN_GROUP, OPSI_ADMIN_GROUP  # type: ignore[import]
from OPSI.Exceptions import (  # type: ignore[import]
	BackendAuthenticationError,
	BackendPermissionDeniedError,
)
from OPSI.Util import (  # type: ignore[import]
	deserialize,
	ipAddressInNetwork,
	serialize,
	timestamp,
)
from opsicommon.logging import secret_filter, set_context  # type: ignore[import]
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import contextvar_client_session, contextvar_server_timing
from .addon import AddonManager
from .backend import get_client_backend
from .config import FQDN, config
from .logging import logger
from .utils import (
	async_redis_client,
	ip_address_to_redis_key,
	redis_client,
	utc_time_timestamp,
)

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


BasicAuth = namedtuple("BasicAuth", ["username", "password"])


def get_basic_auth(headers: Headers):
	auth_header = headers.get("authorization")
	if not auth_header:
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Authorization header missing",
			headers={"WWW-Authenticate": 'Basic realm="opsi", charset="UTF-8"'},
		)

	if not auth_header.startswith("Basic "):
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Authorization method unsupported",
			headers={"WWW-Authenticate": 'Basic realm="opsi", charset="UTF-8"'},
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


def get_session_from_context():
	try:
		return contextvar_client_session.get()
	except LookupError as exc:
		logger.debug("Failed to get session from context: %s", exc)
	return None


class SessionMiddleware:
	def __init__(self, app: ASGIApp, public_path: List[str] = None) -> None:
		self.app = app
		self.session_cookie_name = "opsiconfd-session"
		self.session_cookie_attributes = ("SameSite=Strict", "Secure")
		self._public_path = public_path or []
		# Zsync2 will send "curl/<curl-version>" as User-Agent.
		# RedHat / Alma / Rocky package manager will send "libdnf (<os-version>)".
		# Do not keep sessions because they will never send a cookie (session id).
		# If we keep the session, we may reach the maximum number of sessions per ip.
		self._session_unaware_user_agents = ("libdnf", "curl")
		# Store ip addresses of depots with last access time
		self._depot_addresses: Dict[str, float] = {}

	def get_session_id_from_headers(self, headers: Headers) -> Optional[str]:
		# connection.cookies.get(self.session_cookie_name, None)
		# Not working for opsi-script, which sometimes sends:
		# 'NULL; opsiconfd-session=7b9efe97a143438684267dfb71cbace2'
		# Workaround:
		cookies = headers.get("cookie")
		if cookies:
			for cookie in cookies.split(";"):
				cookie = cookie.strip().split("=", 1)
				if len(cookie) == 2:
					if cookie[0].strip().lower() == self.session_cookie_name:
						return cookie[1].strip().lower()
		return None

	async def handle_request(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
		self, connection: HTTPConnection, receive: Receive, send: Send
	) -> None:
		start = time.perf_counter()
		scope = connection.scope
		scope["session"] = None
		logger.trace("SessionMiddleware %s", scope)

		await check_network(connection)

		if scope["type"] not in ("http", "websocket"):
			await self.app(scope, receive, send)
			return

		# Process redirects
		if scope["path"].startswith("/admin/grafana") and connection.base_url.hostname not in ("127.0.0.1", "::1", "0.0.0.0", "localhost"):
			if connection.base_url.hostname != FQDN:
				url = f'https://{FQDN}:{connection.base_url.port}{scope["path"]}'
				logger.info("Redirecting %s to %s (%s)", connection.base_url.hostname, FQDN, url)
				response = RedirectResponse(url, status_code=308)
				await response(scope, receive, send)
				return

		# Set default access role
		required_access_role = ACCESS_ROLE_ADMIN
		access_role_public = ACCESS_ROLE_PUBLIC
		for pub_path in self._public_path:
			if scope["path"].startswith(pub_path):
				required_access_role = access_role_public
				break
		scope["required_access_role"] = required_access_role

		if scope["path"].startswith(("/rpc", "/monitoring")) or (
			scope["path"].startswith("/depot") and scope.get("method") in ("GET", "HEAD", "OPTIONS", "PROPFIND")
		):
			scope["required_access_role"] = ACCESS_ROLE_AUTHENTICATED

		# Get session
		started_authenticated = False
		session_id = self.get_session_id_from_headers(connection.headers)
		if scope["required_access_role"] != ACCESS_ROLE_PUBLIC or session_id:
			max_session_per_ip = config.max_session_per_ip
			if config.max_sessions_excludes and connection.client.host in config.max_sessions_excludes:
				logger.debug("Disable max_session_per_ip for address: %s", connection.client.host)
				max_session_per_ip = 0
			elif connection.client.host in self._depot_addresses:
				# Connection from a known depot server address
				if time.time() - self._depot_addresses[connection.client.host] <= config.session_lifetime:
					logger.debug("Disable max_session_per_ip for depot server: %s", connection.client.host)
					max_session_per_ip = 0
				else:
					# Address information is outdated
					del self._depot_addresses[connection.client.host]

			scope["session"] = OPSISession(self, session_id, connection, max_session_per_ip=max_session_per_ip)
			await scope["session"].init()

		if scope["session"]:
			contextvar_client_session.set(scope["session"])
			started_authenticated = scope["session"].user_store.authenticated

			if connection.headers.get("user-agent", "").startswith(self._session_unaware_user_agents):
				scope["session"].persistent = False
				logger.debug("Not keeping session for client %s (%s)", connection.client.host, connection.headers.get("user-agent"))

		# Addon request processing
		if scope["path"].startswith("/addons"):
			addon = AddonManager().get_addon_by_path(scope["path"])
			if addon:
				logger.debug("Calling %s.handle_request for path '%s'", addon, scope["path"])
				if await addon.handle_request(connection, receive, send):
					return

		await check_access(connection, receive)

		if scope["session"] and scope["session"].user_store.host:
			if scope["session"].user_store.host.getType() == "OpsiClient":
				logger.info("OpsiClient authenticated, updating host object")
				await run_in_threadpool(update_host_object, connection, scope["session"])
			elif scope["session"].user_store.host.getType() in ("OpsiConfigserver", "OpsiDepotserver"):
				logger.debug("Storing depot server address: %s", connection.client.host)
				self._depot_addresses[connection.client.host] = time.time()

		# Session handling time
		session_handling_millis = int((time.perf_counter() - start) * 1000)
		if started_authenticated and session_handling_millis > 1000:
			logger.warning("Session handling took %0.2fms", session_handling_millis)

		server_timing = contextvar_server_timing.get()
		if server_timing:
			server_timing["session_handling"] = session_handling_millis
		contextvar_server_timing.set(server_timing)

		async def send_wrapper(message: Message) -> None:
			if message["type"] == "http.response.start":
				if scope["session"] and not scope["session"].deleted and scope["session"].persistent:
					await scope["session"].store()
					headers = MutableHeaders(scope=message)
					scope["session"].add_cookie_to_headers(headers)
			await send(message)

		await self.app(scope, receive, send_wrapper)

	async def handle_request_exception(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements,no-self-use
		self, err: Exception, connection: HTTPConnection, receive: Receive, send: Send
	) -> None:
		logger.debug("Handle request exception %s: %s", err.__class__.__name__, err, exc_info=True)
		scope = connection.scope
		if scope["path"].startswith("/addons"):
			addon = AddonManager().get_addon_by_path(scope["path"])
			if addon:
				logger.debug("Calling %s.handle_request_exception for path '%s'", addon, scope["path"])
				if await addon.handle_request_exception(err, connection, receive, send):
					return

		status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
		headers = None
		error = None

		if isinstance(err, (BackendAuthenticationError, BackendPermissionDeniedError)):
			log = logger.warning
			if scope.get("method") == "MKCOL" and scope["path"] and scope["path"].lower().endswith("/system volume information"):
				# Windows WebDAV client is trying to create "System Volume Information"
				log = logger.debug
			log(err)

			status_code = status.HTTP_401_UNAUTHORIZED
			headers = {"WWW-Authenticate": 'Basic realm="opsi", charset="UTF-8"'}

			error = "Authentication error"
			if isinstance(err, BackendPermissionDeniedError):
				error = "Permission denied"

			if isinstance(err, BackendAuthenticationError) or not scope["session"] or not scope["session"].user_store.authenticated:
				cmd = (
					f"ts.add opsiconfd:stats:client:failed_auth:{ip_address_to_redis_key(connection.client.host)} "
					f"* 1 RETENTION 86400000 LABELS client_addr {connection.client.host}"
				)
				logger.debug(cmd)
				redis = await async_redis_client()
				await redis.execute_command(cmd)
				await asyncio.sleep(0.2)

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

		if scope["type"] == "websocket":
			websocket_close_code = status.WS_1008_POLICY_VIOLATION
			if status_code == status.HTTP_500_INTERNAL_SERVER_ERROR:
				websocket_close_code = status.WS_1011_INTERNAL_ERROR
			return await send({"type": "websocket.close", "code": websocket_close_code})

		headers = headers or {}
		if scope.get("session"):
			scope["session"].add_cookie_to_headers(headers)

		response: Optional[Response] = None
		if scope["path"].startswith("/rpc"):
			logger.debug("Returning jsonrpc response because path startswith /rpc")
			content = {"id": None, "result": None, "error": error}
			if scope.get("jsonrpc20"):
				content["jsonrpc"] = "2.0"
				del content["result"]
			response = JSONResponse(status_code=status_code, content=content, headers=headers)
		if not response:
			if connection.headers.get("accept") and "application/json" in connection.headers.get("accept"):
				logger.debug("Returning json response because of accept header")
				response = JSONResponse(status_code=status_code, content={"error": error}, headers=headers)
		if not response:
			logger.debug("Returning plaintext response")
			response = PlainTextResponse(status_code=status_code, content=error, headers=headers)
		await response(scope, receive, send)

	async def __call__(
		self, scope: Scope, receive: Receive, send: Send
	) -> None:  # pylint: disable=too-many-locals, too-many-branches, too-many-statements
		try:
			connection = HTTPConnection(scope)
			set_context({"client_address": connection.client.host})
			await self.handle_request(connection, receive, send)
		except Exception as err:  # pylint: disable=broad-except
			await self.handle_request_exception(err, connection, receive, send)


class OPSISession:  # pylint: disable=too-many-instance-attributes
	redis_key_prefix = "opsiconfd:sessions"

	def __init__(
		self, session_middelware: SessionMiddleware, session_id: Optional[str], connection: HTTPConnection, max_session_per_ip: int = None
	) -> None:
		self._session_middelware = session_middelware
		self._connection = connection
		self._max_session_per_ip = config.max_session_per_ip if max_session_per_ip is None else max_session_per_ip
		self.session_id = session_id or None
		self.client_addr = self._connection.client.host
		self.user_agent = self._connection.headers.get("user-agent")
		self.max_age = config.session_lifetime
		self.created = 0
		self.deleted = False
		self.persistent = True
		self.last_used = 0
		self.last_stored = 0
		self.user_store = UserStore()
		self.option_store: Dict[str, Any] = {}
		self._data: Dict[str, Any] = {}
		self._redis_expiration_seconds = 3600
		self._store_interval = 30

	def __repr__(self):
		return f"<{self.__class__.__name__} at {hex(id(self))} created={self.created} last_used={self.last_used}>"

	@property
	def session_cookie_name(self):
		return self._session_middelware.session_cookie_name

	@property
	def session_cookie_attributes(self):
		return self._session_middelware.session_cookie_attributes

	@property
	def redis_key(self) -> str:
		assert self.session_id
		return f"{self.redis_key_prefix}:{ip_address_to_redis_key(self.client_addr)}:{self.session_id}"

	@property
	def expired(self) -> bool:
		return self.validity <= 0

	@property
	def validity(self) -> int:
		return int(self.max_age - (utc_time_timestamp() - self.last_used))

	def get_cookie(self) -> Optional[str]:
		if not self.session_id or self.deleted or not self.persistent:
			return None
		attrs = "; ".join(self.session_cookie_attributes)
		if attrs:
			attrs += "; "
		return f"{self.session_cookie_name}={self.session_id}; {attrs}path=/; Max-Age={self.max_age}"

	def add_cookie_to_headers(self, headers: dict):
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

		await self.update_last_used(False)

	def _init_new_session(self) -> None:
		"""Generate a new session id if number of client sessions is less than max client sessions."""
		session_count = 0
		try:
			with redis_client() as redis:
				now = utc_time_timestamp()
				session_key = f"{self.redis_key_prefix}:{ip_address_to_redis_key(self.client_addr)}:*"
				for redis_key in redis.scan_iter(session_key):
					validity = 0
					data = redis.get(redis_key)
					if data:
						sess = msgpack_loads(data)
						validity = sess["max_age"] - (now - sess["last_used"])
					if validity > 0:
						session_count += 1
					else:
						redis.delete(redis_key)

			if self._max_session_per_ip > 0 and session_count + 1 > self._max_session_per_ip:
				error = f"Too many sessions from {self.client_addr} / {self.user_agent}, configured maximum is: {self._max_session_per_ip}"
				logger.warning(error)
				raise ConnectionRefusedError(error)
		except ConnectionRefusedError as err:
			raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(err)) from err

		self.session_id = str(uuid.uuid4()).replace("-", "")
		self.created = utc_time_timestamp()
		logger.confidential("Generated a new session id %s for %s / %s", self.session_id, self.client_addr, self.user_agent)

	async def init_new_session(self) -> None:
		await run_in_threadpool(self._init_new_session)

	def _load(self) -> bool:
		self._data = {}
		with redis_client() as redis:
			data = redis.get(self.redis_key)
		if not data:
			return False
		data = msgpack_loads(data)
		self.created = data.get("created", self.created)
		self.last_used = data.get("last_used", self.last_used)
		self.max_age = data.get("max_age", self.max_age)
		for key, val in data.get("user_store", {}).items():
			setattr(self.user_store, key, deserialize(val))
		self.option_store = data.get("option_store", self.option_store)
		self.last_stored = data.get("last_stored", utc_time_timestamp())
		self._data = data.get("data", self._data)
		return True

	async def load(self) -> bool:
		# aioredis is sometimes slow ~300ms load, using redis for now
		return await run_in_threadpool(self._load)

	def _store(self) -> None:
		if self.deleted or not self.persistent:
			return
		self.last_stored = utc_time_timestamp()
		self._update_max_age()
		# Remember that the session data in redis may have been
		# changed by another worker process since the last load.
		# Read session from redis if available and update session data.
		with redis_client() as redis:
			session_data = {}
			data = redis.get(self.redis_key)
			if data:
				session_data = msgpack_loads(data)
			session_data.update(
				{
					"created": session_data.get("created", self.created),
					"last_used": self.last_used,
					"last_stored": self.last_stored,
					"max_age": self.max_age,
					"user_agent": self.user_agent,
					"user_store": serialize(self.user_store.__dict__),
					"option_store": self.option_store,
					"data": session_data.get("data", {}),
				}
			)
			session_data["data"].update(self._data)
			# Set is not serializable
			if "userGroups" in session_data["user_store"]:
				session_data["user_store"]["userGroups"] = list(session_data["user_store"]["userGroups"])
			# Do not store password
			if "password" in session_data["user_store"]:
				del session_data["user_store"]["password"]

			redis.set(self.redis_key, msgpack_dumps(session_data), ex=self._redis_expiration_seconds)

	async def store(self, wait: Optional[bool] = True) -> None:
		# aioredis is sometimes slow ~300ms load, using redis for now
		task = run_in_threadpool(self._store)
		if wait:
			await task
		else:
			asyncio.get_running_loop().create_task(task)

	def sync_delete(self) -> None:
		with redis_client() as redis:
			for _ in range(10):
				redis.delete(self.redis_key)
				time_sleep(0.01)
				# Be sure to delete key
				if not redis.exists(self.redis_key):
					break
		self.session_id = None
		self.deleted = True

	async def delete(self) -> None:
		return await run_in_threadpool(self.sync_delete)

	async def update_last_used(self, store: Optional[bool] = None) -> None:
		now = utc_time_timestamp()
		if store or (store is None and now - self.last_stored > self._store_interval):
			await self.store()
		self.last_used = now

	def _update_max_age(self) -> None:
		if not self.user_store or not self.user_store.authenticated:
			return
		client_max_age = self._connection.headers.get("x-opsi-session-lifetime")
		if not client_max_age:
			return
		try:
			client_max_age = int(client_max_age)
			if 0 < client_max_age <= 3600 * 24:
				if client_max_age != self.max_age:
					logger.info("Accepting session lifetime %d from client", client_max_age)
					self.max_age = client_max_age
			else:
				logger.warning("Not accepting session lifetime %d from client", client_max_age)
		except ValueError:
			logger.warning("Invalid x-opsi-session-lifetime header with value '%s' from client", client_max_age)

	def get(self, name: str, default: Any = None) -> Any:
		return self._data.get(name, default)

	def set(self, key: str, value: Any) -> None:
		self._data[key] = value


def update_host_object(connection: HTTPConnection, session: OPSISession) -> None:
	hosts = get_client_backend().host_getObjects(["ipAddress", "lastSeen"], id=session.user_store.host.id)  # pylint: disable=no-member
	if not hosts:
		logger.error("Host %s not found in backend while trying to update ip address and lastseen", session.user_store.host.id)
		return
	host = hosts[0]
	if host.getType() != "OpsiClient":
		return
	host.setLastSeen(timestamp())
	if config.update_ip and connection.client.host not in (None, "127.0.0.1", "::1", host.ipAddress):
		host.setIpAddress(connection.client.host)
	else:
		# Value None on update means no change!
		host.ipAddress = None
	get_client_backend().host_updateObjects(host)  # pylint: disable=no-member


async def authenticate(connection: HTTPConnection, receive: Receive) -> None:  # pylint: disable=unused-argument
	logger.info("Start authentication of client %s", connection.client.host)
	session = connection.scope["session"]
	username = None
	password = None

	auth = get_basic_auth(connection.headers)
	username = auth.username
	password = auth.password

	auth_type = None
	if username == config.monitoring_user:
		auth_type = "opsi-passwd"

	def sync_auth(username, password, auth_type):
		get_client_backend().backendAccessControl.authenticate(username, password, auth_type=auth_type)

	await run_in_threadpool(sync_auth, username, password, auth_type)
	logger.debug("Client %s authenticated, username: %s", connection.client.host, session.user_store.username)

	if username == config.monitoring_user:
		session.user_store.isAdmin = False
		session.user_store.isReadOnly = True


async def check_blocked(connection: HTTPConnection) -> None:
	logger.info("Checking if client '%s' is blocked", connection.client.host)
	redis = await async_redis_client()
	is_blocked = bool(await redis.get(f"opsiconfd:stats:client:blocked:{ip_address_to_redis_key(connection.client.host)}"))
	if is_blocked:
		logger.info("Client '%s' is blocked", connection.client.host)
		raise ConnectionRefusedError(f"Client '{connection.client.host}' is blocked")

	now = round(time.time()) * 1000
	cmd = (
		f"ts.range opsiconfd:stats:client:failed_auth:{ip_address_to_redis_key(connection.client.host)} "
		f"{(now-(config.auth_failures_interval*1000))} {now} aggregation count {(config.auth_failures_interval*1000)}"
	)
	logger.debug(cmd)
	try:
		num_failed_auth = await redis.execute_command(cmd)
		num_failed_auth = int(num_failed_auth[-1][1])
		logger.debug("num_failed_auth: %s", num_failed_auth)
	except aioredis.ResponseError as err:
		num_failed_auth = 0
		if "key does not exist" not in str(err):
			raise
	except IndexError as err:
		logger.debug(err)
		num_failed_auth = 0
	if num_failed_auth >= config.max_auth_failures:
		is_blocked = True
		logger.warning("Blocking client '%s' for %0.2f minutes", connection.client.host, (config.client_block_time / 60))
		await redis.setex(f"opsiconfd:stats:client:blocked:{ip_address_to_redis_key(connection.client.host)}", config.client_block_time, 1)


async def check_network(connection: HTTPConnection) -> None:
	if not config.networks:
		return
	for network in config.networks:
		if ipAddressInNetwork(connection.client.host, network):
			return
	raise ConnectionRefusedError(f"Host '{connection.client.host}' is not allowed to connect")


async def check_access(connection: HTTPConnection, receive: Receive) -> None:
	scope = connection.scope
	if scope["required_access_role"] == ACCESS_ROLE_PUBLIC:
		return

	session = connection.scope["session"]

	if not session.user_store.username or not session.user_store.authenticated:
		# Check if host address is blocked
		await check_blocked(connection)
		# Authenticate
		await authenticate(connection, receive)

		if not session.user_store.host and scope["path"].startswith("/depot") and FILE_ADMIN_GROUP not in session.user_store.userGroups:
			raise BackendPermissionDeniedError(f"Not a file admin user '{session.user_store.username}'")

		if session.user_store.isAdmin and config.admin_networks:
			is_admin_network = False
			for network in config.admin_networks:
				if ipAddressInNetwork(connection.client.host, network):
					is_admin_network = True
					break

			if not is_admin_network:
				logger.warning(
					"User '%s' from '%s' not in admin network '%s'",
					session.user_store.username,
					connection.client.host,
					config.admin_networks,
				)
				session.user_store.isAdmin = False
				if OPSI_ADMIN_GROUP in session.user_store.userGroups:
					# Remove admin group from groups because acl.conf currently does not support isAdmin
					session.user_store.userGroups.remove(OPSI_ADMIN_GROUP)
				await session.store()

	if scope["required_access_role"] == ACCESS_ROLE_ADMIN and not session.user_store.isAdmin:
		raise BackendPermissionDeniedError(f"Not an admin user '{session.user_store.username}' {scope.get('method')} {scope.get('path')}")
