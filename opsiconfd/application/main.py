# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
application main
"""

import os
import asyncio
import urllib
from urllib.parse import urlparse
import datetime
from ctypes import c_long

from starlette import status
from starlette.endpoints import WebSocketEndpoint
from starlette.websockets import WebSocket
from starlette.types import ASGIApp, Message, Scope, Send, Receive
from starlette.datastructures import MutableHeaders
from starlette.concurrency import run_in_threadpool
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.requests import Request
from fastapi.responses import Response, FileResponse, RedirectResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute, Mount
from websockets.exceptions import ConnectionClosedOK

from OPSI import __version__ as python_opsi_version
from .. import __version__

from .. import contextvar_request_id, contextvar_client_address, contextvar_client_session
from ..logging import logger
from ..config import config
from ..worker import init_worker
from ..session import SessionMiddleware
from ..statistics import StatisticsMiddleware
from ..utils import normalize_ip_address, async_redis_client
from ..ssl import get_ca_cert_as_pem
from ..addon import AddonManager
from ..rest import OpsiApiException, RestApiValidationError, rest_api
from .metrics import metrics_setup
from .jsonrpc import jsonrpc_setup
from .webdav import webdav_setup
from .admininterface import admin_interface_setup
from .redisinterface import redis_interface_setup
from .monitoring.monitoring import monitoring_setup
from .status import status_setup
from .messagebroker import messagebroker_setup


app = FastAPI(
	title="opsiconfd",
	description="",
	version=f"{__version__} [python-opsi={python_opsi_version}]",
	responses={
		422: {'model': RestApiValidationError, 'description': 'Validation Error'}
	}
)


@app.websocket_route("/ws/log_viewer")
class LoggerWebsocket(WebSocketEndpoint):
	encoding = 'bytes'

	async def _reader(self, start_id='$', client=None):
		stream_name = f"opsiconfd:log:{config.node_name}"
		logger.info("Websocket client is starting to read log stream: stream_name=%s, start_id=%s, client=%s", stream_name, start_id, client)
		last_id = start_id

		def read_data(data):
			buf = bytearray()
			for stream in data:
				for dat in stream[1]:
					last_id = dat[0]
					if client and client != dat[1].get("client", b'').decode("utf-8"):
						continue
					buf += dat[1][b"record"]
			return (last_id, buf)

		while True:
			try:
				redis = await async_redis_client()
				# It is also possible to specify multiple streams
				data = await redis.xread(streams={stream_name: last_id}, block=1000)
				if not data:
					continue
				last_id, buf = await run_in_threadpool(read_data, data)
				await self._websocket.send_text(buf)
			except Exception as err:  # pylint: disable=broad-except
				if not app.is_shutting_down and not isinstance(err, ConnectionClosedOK):
					logger.error(err, exc_info=True)
				break

	async def on_connect(self, websocket: WebSocket):
		session = contextvar_client_session.get()
		if not session.user_store.isAdmin:
			logger.warning("Access to %s denied for user '%s'", self, session.user_store.username)
			await websocket.close(code=4403)
			return

		self._websocket = websocket  # pylint: disable=attribute-defined-outside-init
		params = urllib.parse.parse_qs(websocket.get('query_string', b'').decode('utf-8'))
		client = params.get("client", [None])[0]
		start_id = int(params.get("start_time", [0])[0]) * 1000  # Seconds to millis
		if start_id <= 0:
			start_id = "$"
		await self._websocket.accept()
		await asyncio.get_event_loop().create_task(self._reader(str(start_id), client))

	async def on_disconnect(self, websocket: WebSocket, close_code: int) -> None:
		pass


@app.websocket_route("/test/ws")
class TestWebsocket(WebSocketEndpoint):
	encoding = 'bytes'

	async def on_connect(self, websocket: WebSocket):
		session = contextvar_client_session.get()
		if not session.user_store.isAdmin:
			logger.warning("Access to %s denied for user '%s'", self, session.user_store.username)
			await websocket.close(code=4403)
			return
		# params = urllib.parse.parse_qs(websocket.get('query_string', b'').decode('utf-8'))
		# client = params.get("client", [None])[0]
		await websocket.accept()
		while True:
			current_time = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
			text = f"current utc time: {current_time}"
			logger.info("Sending '%s'", text)
			await websocket.send_text(text)
			await asyncio.sleep(10)

	async def on_disconnect(self, websocket: WebSocket, close_code: int) -> None:
		pass


@app.get("/test/random-data")
async def get_test_random(request: Request):  # pylint: disable=unused-argument
	if not contextvar_client_session.get().user_store.isAdmin:
		return Response(status_code=status.HTTP_403_FORBIDDEN)
	with open("/dev/urandom", mode="rb") as random:
		return StreamingResponse(random, media_type="application/binary")


@app.get("/")
async def index(request: Request, response: Response):  # pylint: disable=unused-argument
	return RedirectResponse("/admin", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@app.get("/favicon.ico")
async def favicon(request: Request, response: Response):  # pylint: disable=unused-argument
	return RedirectResponse("/static/favicon.ico", status_code=status.HTTP_301_MOVED_PERMANENTLY)


@app.on_event("startup")
async def startup_event():
	app.is_shutting_down = False
	try:
		init_worker()
		application_setup()
	except Exception as error:
		logger.critical("Error during worker startup: %s", error, exc_info=True)
		# Wait a second before raising error (which will terminate the worker process)
		# to give the logger time to send log messages to redis
		await asyncio.sleep(1)
		raise error


@app.get("/ssl/opsi-ca-cert.pem")
def get_ssl_ca_cert(request: Request):  # pylint: disable=unused-argument
	return Response(
		content=get_ca_cert_as_pem(),
		headers={
			"Content-Type": "application/x-pem-file",
			"Content-Disposition": 'attachment; filename="opsi-ca-cert.pem"'
		}
	)


class BaseMiddleware:  # pylint: disable=too-few-public-methods
	def __init__(self, app: ASGIApp) -> None:  # pylint: disable=redefined-outer-name
		self.app = app

	@staticmethod
	def get_client_address(scope: Scope):
		""" Get sanitized client address"""
		host, port = scope.get("client", (None, 0))
		if host:
			host = normalize_ip_address(host)
		return host, port

	@staticmethod
	def before_send(scope: Scope, receive: Receive, send: Send):
		pass

	async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
		if not scope["type"] in ("http", "websocket"):
			return await self.app(scope, receive, send)

		# Generate request id and store in contextvar
		request_id = id(scope)
		# Longs on Windows are only 32 bits, but memory adresses on 64 bit python are 64 bits
		# Ensure it fits inside a long, truncating if necessary
		request_id = abs(c_long(request_id).value)
		scope["request_id"] = request_id
		contextvar_request_id.set(request_id)

		client_host, client_port = self.get_client_address(scope)

		if client_host in config.trusted_proxies:
			proxy_host = client_host
			# from uvicorn/middleware/proxy_headers.py
			headers = dict(scope["headers"])
			# if b"x-forwarded-proto" in headers:
			# # Determine if the incoming request was http or https based on
			# # the X-Forwarded-Proto header.
			# x_forwarded_proto = headers[b"x-forwarded-proto"].decode("ascii")
			# scope["scheme"] = x_forwarded_proto.strip()

			if b"x-forwarded-for" in headers:
				# Determine the client address from the last trusted IP in the
				# X-Forwarded-For header. We've lost the connecting client's port
				# information by now, so only include the host.
				x_forwarded_for = headers[b"x-forwarded-for"].decode("ascii")
				client_host = x_forwarded_for.split(",")[-1].strip()
				client_port = 0
				logger.debug(
					"Accepting x-forwarded-for header (host=%s) from trusted proxy %s",
					client_host, proxy_host
				)

		scope["client"] = (client_host, client_port)
		contextvar_client_address.set(client_host)

		async def send_wrapper(message: Message) -> None:
			if message["type"] == "http.response.start":
				headers = dict(scope["headers"])
				host = headers.get(b"host", b"localhost:4447").decode().split(":")[0]
				origin_scheme = "https"
				origin_port = 4447
				try:
					origin = urlparse(headers[b"origin"].decode())
					origin_scheme = origin.scheme
					origin_port = int(origin.port)
				except Exception:  # pylint: disable=broad-except
					pass
				headers = MutableHeaders(scope=message)
				headers.append("Access-Control-Allow-Origin", f"{origin_scheme}://{host}:{origin_port}")
				headers.append("Access-Control-Allow-Methods", "*")
				headers.append(
					"Access-Control-Allow-Headers",
					"Accept,Accept-Encoding,Authorization,Connection,Content-Type,Encoding,Host,Origin,X-opsi-session-lifetime"
				)
				headers.append("Access-Control-Allow-Credentials", "true")

			self.before_send(scope, receive, send)
			await send(message)

		return await self.app(scope, receive, send_wrapper)


@app.exception_handler(RequestValidationError)
@rest_api
def validation_exception_handler(request, exc):
	raise OpsiApiException(
		message=f"Validation error: {exc}",
		http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
		error=exc
	)


def application_setup():
	FileResponse.chunk_size = 32 * 1024  # Speeds up transfer of big files massively, original value is 4*1024

	# Every Starlette application automatically includes two pieces of middleware by default:
	#    ServerErrorMiddleware: Ensures that application exceptions may return a custom 500 page,
	#                           or display an application traceback in DEBUG mode.
	#                           This is always the outermost middleware layer.
	#      ExceptionMiddleware: Adds exception handlers, so that particular types of expected
	#                           exception cases can be associated with handler functions.
	#                           For example raising HTTPException(status_code=404) within an endpoint
	#                           will end up rendering a custom 404 page.
	#
	# Last added middleware will be executed first
	# middleware stack:
	#    ServerErrorMiddleware
	#    user added middlewares
	#    ExceptionMiddleware
	#
	# Exceptions raised from user middleware will not be catched by ExceptionMiddleware
	app.add_middleware(SessionMiddleware, public_path=[
		"/metrics/grafana", "/ws/test", "/ssl/opsi-ca-cert.pem", "/status", "/public"
	])
	# app.add_middleware(GZipMiddleware, minimum_size=1000)
	app.add_middleware(StatisticsMiddleware, profiler_enabled=config.profiler, log_func_stats=config.profiler)
	app.add_middleware(BaseMiddleware)
	if os.path.isdir(config.static_dir):
		app.mount("/static", StaticFiles(directory=config.static_dir), name="static")
	else:
		logger.warning("Static dir '%s' not found", config.static_dir)

	jsonrpc_setup(app)
	admin_interface_setup(app)
	redis_interface_setup(app)
	monitoring_setup(app)
	webdav_setup(app)
	metrics_setup(app)
	status_setup(app)
	messagebroker_setup(app)

	AddonManager().load_addons()

	logger.debug("Routing:")
	routes = {}
	for route in app.routes:
		if isinstance(route, Mount):
			routes[route.path] = str(route.app.__module__)
		elif isinstance(route, APIRoute):
			module = route.endpoint.__module__
			if module.startswith("opsiconfd.addon_"):
				module = f"opsiconfd.addon.{module.split('/')[-1]}"
			routes[route.path] = f"{module}.{route.endpoint.__qualname__}"
		else:
			routes[route.path] = route.__class__.__name__
	for path in sorted(routes):
		logger.debug("%s: %s", path, routes[path])
