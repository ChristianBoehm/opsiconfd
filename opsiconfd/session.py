# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
session handling
"""

import time
import typing
import asyncio
from typing import List
from collections import namedtuple
import uuid
import base64
import datetime
import orjson
import msgpack
from aredis.exceptions import ResponseError

from fastapi import HTTPException, status
from fastapi.requests import HTTPConnection, Request
from fastapi.responses import PlainTextResponse, JSONResponse, RedirectResponse
from starlette.datastructures import MutableHeaders, Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.concurrency import run_in_threadpool

from OPSI.Backend.Manager.AccessControl import UserStore
from OPSI.Util import serialize, deserialize, ipAddressInNetwork, timestamp
from OPSI.Exceptions import BackendAuthenticationError, BackendPermissionDeniedError
from OPSI.Config import OPSI_ADMIN_GROUP, FILE_ADMIN_GROUP

from opsicommon.logging import logger, secret_filter, set_context

from . import contextvar_client_session, contextvar_server_timing
from .backend import get_client_backend
from .config import config, FQDN
from .utils import redis_client, aredis_client, ip_address_to_redis_key, utc_time_timestamp
from .addon import AddonManager

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
			headers={"WWW-Authenticate": 'Basic realm="opsi", charset="UTF-8"'}
		)

	if not auth_header.startswith("Basic "):
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Authorization method unsupported",
			headers={"WWW-Authenticate": 'Basic realm="opsi", charset="UTF-8"'}
		)

	encoded_auth = auth_header[6:] # Stripping "Basic "
	secret_filter.add_secrets(encoded_auth)
	auth = base64.decodebytes(encoded_auth.encode("ascii")).decode("utf-8")

	(username, password) = auth.split(':', 1)
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
		self.session_cookie_name = 'opsiconfd-session'
		#self.security_flags = "httponly; samesite=lax; secure"
		self.security_flags = ""
		self._public_path = public_path or []

	def get_session_id_from_headers(self, headers: Headers) -> str:
		#connection.cookies.get(self.session_cookie_name, None)
		# Not working for opsi-script, which sometimes sends:
		# 'NULL; opsiconfd-session=7b9efe97a143438684267dfb71cbace2'
		# Workaround:
		cookies = headers.get("cookie")
		if cookies:
			for cookie in cookies.split(';'):
				cookie = cookie.strip().split('=', 1)
				if len(cookie) == 2:
					if cookie[0].strip().lower() == self.session_cookie_name:
						return cookie[1].strip().lower()
		return None

	async def handle_request(self, connection: HTTPConnection, receive: Receive, send: Send) -> None:  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
		start = time.perf_counter()
		scope = connection.scope
		scope["session"] = None
		logger.trace("SessionMiddleware %s", scope)

		if scope.get("http_version") and scope["http_version"] != "1.1":
			logger.warning(
				"Client %s (%s) is using http version %s",
				connection.client.host, connection.headers.get("user-agent"), scope.get("http_version")
			)

		await check_network(connection)

		if scope["type"] not in ("http", "websocket"):
			await self.app(scope, receive, send)
			return

		# Process redirects
		if (
			scope["path"].startswith("/admin/grafana") and
			connection.base_url.hostname not in ("127.0.0.1", "::1", "0.0.0.0", "localhost")
		):
			if connection.base_url.hostname != FQDN:
				url = f'https://{FQDN}:{connection.base_url.port}{scope["path"]}'
				logger.info("Redirecting %s to %s (%s)", connection.base_url.hostname, FQDN, url)
				response = RedirectResponse(url, status_code=308)
				await response(scope, receive, send)
				return

		# Set default access role
		scope["required_access_role"] = ACCESS_ROLE_ADMIN
		for pub_path in self._public_path:
			if scope["path"].startswith(pub_path):
				scope["required_access_role"] = ACCESS_ROLE_PUBLIC
				break

		if (
			scope["path"].startswith(("/rpc", "/monitoring")) or
			(scope["path"].startswith("/depot") and scope["method"] in ("GET", "HEAD", "OPTIONS", "PROPFIND"))
		):
			scope["required_access_role"] = ACCESS_ROLE_AUTHENTICATED

		# Get session
		session_id = self.get_session_id_from_headers(connection.headers)
		if scope["required_access_role"] != ACCESS_ROLE_PUBLIC or session_id:
			session = OPSISession(self, session_id, connection)
			await session.init()
		if session:
			contextvar_client_session.set(session)
			scope["session"] = session
			started_authenticated = session.user_store.authenticated

		if connection.headers.get("user-agent", "").startswith("curl/"):
			# Zsync2 will send "curl/<curl-version>" as User-Agent.
			# Do not keep zsync2 sessions because zsync2 will never send a session id.
			# If we keep the session, we may reach the maximum number of sessions per ip.
			session.persistent = False
			logger.debug(
				"Not keeping session for client %s (%s)",
				connection.client.host, connection.headers.get("user-agent")
			)


		# Addon request processing
		if scope["path"].startswith("/addons"):
			addon = AddonManager().get_addon_by_path(scope["path"])
			if addon:
				logger.debug("Calling %s.handle_request for path '%s'", addon, scope["path"])
				if await addon.handle_request(connection, receive, send):
					return

		if (
			scope["path"].startswith("/webgui/api/opsidata") and
			connection.base_url.hostname in  ("127.0.0.1", "::1", "0.0.0.0", "localhost")
		):
			if scope.get("method") == "OPTIONS":
				scope["required_access_role"] = ACCESS_ROLE_PUBLIC
		if scope["path"] == "/webgui/api/auth/login":
			if scope.get("method") == "OPTIONS":
				scope["required_access_role"] = ACCESS_ROLE_PUBLIC
			else:
				# Authenticate
				await authenticate(connection, receive)

		await check_access(connection, receive)

		# Session handling time
		session_handling_millis = (time.perf_counter() - start) * 1000
		if started_authenticated and session_handling_millis > 1000:
			logger.warning("Session handling took %0.2fms", session_handling_millis)

		server_timing = contextvar_server_timing.get()
		if server_timing:
			server_timing["session_handling"] = session_handling_millis
		contextvar_server_timing.set(server_timing)

		async def send_wrapper(message: Message) -> None:
			if message["type"] == "http.response.start":
				if session and not session.deleted and session.persistent:
					asyncio.get_event_loop().create_task(session.store())
					headers = MutableHeaders(scope=message)
					for key, val in session.get_headers().items():
						headers.append(key, val)
			await send(message)

		await self.app(scope, receive, send_wrapper)


	async def handle_request_exception(self, err: Exception, connection: HTTPConnection, receive: Receive, send: Send) -> None:  # pylint: disable=too-many-locals,too-many-branches,too-many-statements,no-self-use
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
			logger.debug(err, exc_info=True)
			logger.warning(err)

			status_code = status.HTTP_401_UNAUTHORIZED
			headers = {"WWW-Authenticate": 'Basic realm="opsi", charset="UTF-8"'}
			if scope["path"].startswith("/webgui/"):
				# Do not send WWW-Authenticate to webgui / axios
				headers = {}
			error = "Authentication error"
			if isinstance(err, BackendPermissionDeniedError):
				error = "Permission denied"
			cmd = (
				f"ts.add opsiconfd:stats:client:failed_auth:{ip_address_to_redis_key(connection.client.host)} "
				f"* 1 RETENTION 86400000 LABELS client_addr {connection.client.host}"
			)
			logger.debug(cmd)
			redis = await aredis_client()
			asyncio.get_event_loop().create_task(redis.execute_command(cmd))
			await asyncio.sleep(0.2)

		elif isinstance(err, ConnectionRefusedError):
			status_code = status.HTTP_403_FORBIDDEN
			error = str(err)

		elif isinstance(err, HTTPException):
			status_code = err.status_code # pylint: disable=no-member
			headers = err.headers # pylint: disable=no-member
			error = err.detail

		else:
			logger.error(err, exc_info=True)
			error = str(err)

		if scope["type"] == "websocket":
			return await send({"type": "websocket.close", "code": status_code})

		response = None
		headers = headers or {}
		if scope.get("session"):
			headers.update(scope["session"].get_headers())

		if scope["path"].startswith("/rpc"):
			logger.debug("Returning jsonrpc response because path startswith /rpc")
			content = {"id": None, "result": None, "error": error}
			if scope.get("jsonrpc20"):
				content["jsonrpc"] = "2.0"
				del content["result"]
			response = JSONResponse(
				status_code=status_code,
				content=content,
				headers=headers
			)
		if not response:
			if connection.headers.get("accept") and "application/json" in connection.headers.get("accept"):
				logger.debug("Returning json response because of accept header")
				response = JSONResponse(
					status_code=status_code,
					content={"error": error},
					headers=headers
				)
		if not response:
			logger.debug("Returning plaintext response")
			response = PlainTextResponse(
				status_code=status_code,
				content=error,
				headers=headers
			)
		await response(scope, receive, send)

	async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None: # pylint: disable=too-many-locals, too-many-branches, too-many-statements
		try:
			connection = HTTPConnection(scope)
			set_context({"client_address": connection.client.host})
			await self.handle_request(connection, receive, send)
		except Exception as err: # pylint: disable=broad-except
			await self.handle_request_exception(err, connection, receive, send)


class OPSISession(): # pylint: disable=too-many-instance-attributes
	redis_key_prefix = "opsiconfd:sessions"

	def __init__(self, session_middelware: SessionMiddleware, session_id: str, connection: HTTPConnection) -> None:
		self._session_middelware = session_middelware
		self.session_id = session_id
		self.client_addr = connection.client.host
		self.user_agent = connection.headers.get("user-agent")
		self.max_age = config.session_lifetime
		client_max_age = connection.headers.get("x-opsi-session-lifetime")
		if client_max_age:
			try:
				client_max_age = int(client_max_age)
				if 0 < client_max_age <= 3600 * 24:
					logger.info("Accepting session lifetime %d from client", client_max_age)
					self.max_age = client_max_age
				else:
					logger.warning("Not accepting session lifetime %d from client", client_max_age)
			except ValueError:
				logger.warning("Invalid x-opsi-session-lifetime header with value '%s' from client", client_max_age)

		self.created = 0
		self.deleted = False
		self.persistent = True
		self.last_used = 0
		self.user_store = UserStore()
		self.option_store = {}
		self._data: typing.Dict[str, typing.Any] = {}
		self.is_new_session = True

	def __repr__(self):
		return f"<{self.__class__.__name__} at {hex(id(self))} created={self.created} last_used={self.last_used}>"

	@property
	def session_cookie_name(self):
		return self._session_middelware.session_cookie_name

	@property
	def redis_key(self) -> str:
		assert self.session_id
		return f"{self.redis_key_prefix}:{ip_address_to_redis_key(self.client_addr)}:{self.session_id}"

	@property
	def expired(self) -> bool:
		return utc_time_timestamp() - self.last_used > self.max_age

	def get_headers(self):
		if not self.session_id or self.deleted or not self.persistent:
			return {}
		return {
			"Set-Cookie": f"{self.session_cookie_name}={self.session_id}; path=/; Max-Age={self.max_age}"
		}

	async def init(self) -> None:
		wait_for_store = True
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
					wait_for_store = False
			else:
				logger.debug("Session not found: %s (%s / %s)", self, self.client_addr, self.user_agent)
				await self.init_new_session()

		self._update_last_used()
		if wait_for_store:
			# Session not yet stored in redis.
			# Wait for store to complete to ensure that the
			# session can be loaded at the next request.
			await self.store()
		else:
			asyncio.get_event_loop().create_task(self.store())

	def _init_new_session(self) -> None:
		"""Generate a new session id if number of client sessions is less than max client sessions."""
		redis_session_keys = []
		try:
			with redis_client() as redis:
				for key in redis.scan_iter(f"{self.redis_key_prefix}:{ip_address_to_redis_key(self.client_addr)}:*"):
					redis_session_keys.append(key.decode("utf8"))
			#redis = await aredis_client()
			#async for key in redis.scan_iter(f"{self.redis_key_prefix}:{ip_address_to_redis_key(self.client_addr)}:*"):
			#	redis_session_keys.append(key.decode("utf8"))
			if config.max_session_per_ip > 0 and len(redis_session_keys) + 1 > config.max_session_per_ip:
				error = f"Too many sessions from {self.client_addr} / {self.user_agent}, configured maximum is: {config.max_session_per_ip}"
				logger.warning(error)
				raise ConnectionRefusedError(error)
		except ConnectionRefusedError as err:
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail=str(err)
			) from err

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
		try:
			data = msgpack.loads(data)
		except msgpack.exceptions.ExtraData:
			# Was json encoded before, can be removed in the future
			data = orjson.loads(data)  # pylint: disable=no-member
		self.created = data.get("created", self.created)
		self.last_used = data.get("last_used", self.last_used)
		self.max_age = data.get("max_age", self.max_age)
		for key, val in data.get("user_store", {}).items():
			setattr(self.user_store, key, deserialize(val))
		self.option_store = data.get("option_store", self.option_store)
		self._data = data.get("data", self._data)
		self.is_new_session = False
		return True

	async def load(self) -> bool:
		# aredis is sometimes slow ~300ms load, using redis for now
		return await run_in_threadpool(self._load)

	def _store(self) -> None:
		if self.deleted or not self.persistent:
			return

		data = {
			"created": self.created,
			"last_used": self.last_used,
			"max_age": self.max_age,
			"user_agent": self.user_agent,
			"user_store": serialize(self.user_store.__dict__),
			"option_store": self.option_store,
			"data": self._data
		}
		# Set is not serializable
		if "userGroups" in data["user_store"]:
			data["user_store"]["userGroups"] = list(data["user_store"]["userGroups"])
		# Do not store password
		if "password" in data["user_store"]:
			del data["user_store"]["password"]
		with redis_client() as redis:
			#start = time.perf_counter()
			redis.set(self.redis_key, msgpack.dumps(data), ex=self.max_age)
			#ms = (time.perf_counter() - start) * 1000
			#if ms > 100:
			#	logger.warning("Session storing to redis took %0.2fms", ms)
		#redis = await aredis_client()
		#await redis.set(self.redis_key, msgpack.dumps(data), ex=self.max_age)

	async def store(self) -> None:
		# aredis is sometimes slow ~300ms load, using redis for now
		await run_in_threadpool(self._store)

	def sync_delete(self) -> None:
		with redis_client() as redis:
			for _ in range(10):
				redis.delete(self.redis_key)
				time.sleep(0.01)
				# Be sure to delete key
				if not redis.exists(self.redis_key):
					break
		self.session_id = None
		self.deleted = True

	async def delete(self) -> None:
		return await run_in_threadpool(self.sync_delete)

	def _update_last_used(self):
		self.last_used = utc_time_timestamp()

	def get(self, name: str, default: typing.Any = None) -> typing.Any:
		return self._data.get(name, default)

	def set(self, key: str, value: typing.Any) -> None:
		self._data[key] = value


def update_host_object(connection: HTTPConnection, session: OPSISession) -> None:
	hosts = get_client_backend().host_getObjects(['ipAddress', 'lastSeen'], id=session.user_store.host.id) # pylint: disable=no-member
	if not hosts:
		logger.error("Host %s not found in backend while trying to update ip address and lastseen", session.user_store.host.id)
		return
	host = hosts[0]
	if host.getType() != 'OpsiClient':
		return
	host.setLastSeen(timestamp())
	if config.update_ip and connection.client.host not in (None, "127.0.0.1", "::1", host.ipAddress):
		host.setIpAddress(connection.client.host)
	else:
		# Value None on update means no change!
		host.ipAddress = None
	get_client_backend().host_updateObjects(host) # pylint: disable=no-member


async def authenticate(connection: HTTPConnection, receive: Receive) -> None:
	logger.info("Start authentication of client %s", connection.client.host)
	session = connection.scope["session"]
	username = None
	password = None
	if connection.scope["path"] == "/webgui/api/auth/login":
		req = Request(connection.scope, receive)
		form = await req.form()
		username = form.get("username")
		password = form.get("password")
	else:
		auth = get_basic_auth(connection.headers)
		username = auth.username
		password = auth.password

	auth_type = None
	if username == config.monitoring_user:
		auth_type = "opsi-passwd"

	def sync_auth(username, password, auth_type):
		get_client_backend().backendAccessControl.authenticate(username, password, auth_type=auth_type)

	await run_in_threadpool(sync_auth, username, password, auth_type)

	if username == config.monitoring_user:
		session.user_store.isAdmin = False
		session.user_store.isReadOnly = True


async def check_blocked(connection: HTTPConnection) -> None:
	logger.info("Checking if client %s is blocked", connection.client.host)
	redis = await aredis_client()
	is_blocked = bool(await redis.get(f"opsiconfd:stats:client:blocked:{ip_address_to_redis_key(connection.client.host)}"))
	if is_blocked:
		raise ConnectionRefusedError(f"Client '{connection.client.host}' is blocked")

	now = round(time.time())*1000
	cmd = (
		f"ts.range opsiconfd:stats:client:failed_auth:{ip_address_to_redis_key(connection.client.host)} "
		f"{(now-(config.auth_failures_interval*1000))} {now} aggregation count {(config.auth_failures_interval*1000)}"
	)
	logger.debug(cmd)
	try:
		num_failed_auth = await redis.execute_command(cmd)
		num_failed_auth =  int(num_failed_auth[-1][1])
		logger.debug("num_failed_auth: %s", num_failed_auth)
	except ResponseError as err:
		num_failed_auth = 0
		if "key does not exist" not in str(err):
			raise
	if num_failed_auth >= config.max_auth_failures:
		is_blocked = True
		logger.warning("Blocking client '%s' for %0.2f minutes", connection.client.host, (config.client_block_time/60))
		await redis.setex(
			f"opsiconfd:stats:client:blocked:{ip_address_to_redis_key(connection.client.host)}",
			config.client_block_time,
			True
		)


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

		if session.user_store.host:
			logger.info("Host authenticated, updating host object")
			await run_in_threadpool(update_host_object, connection, session)

		if (
			not session.user_store.host and
			scope["path"].startswith("/depot") and
			FILE_ADMIN_GROUP not in session.user_store.userGroups
		):
			raise BackendPermissionDeniedError(f"Not a file admin user '{session.user_store.username}'")

		if session.user_store.isAdmin and config.admin_networks:
			is_admin_network = False
			for network in config.admin_networks:
				if ipAddressInNetwork(connection.client.host, network):
					is_admin_network = True
					break

			if not is_admin_network:
				logger.warning("User '%s' from '%s' not in admin network '%s'",
					session.user_store.username,
					connection.client.host,
					config.admin_networks
				)
				session.user_store.isAdmin = False
				if OPSI_ADMIN_GROUP in session.user_store.userGroups:
					# Remove admin group from groups because acl.conf currently does not support isAdmin
					session.user_store.userGroups.remove(OPSI_ADMIN_GROUP)
				asyncio.get_event_loop().create_task(session.store())

	if scope["required_access_role"] == ACCESS_ROLE_ADMIN and not session.user_store.isAdmin:
		raise BackendPermissionDeniedError(f"Not an admin user '{session.user_store.username}' {scope['method']} {scope['path']}")
