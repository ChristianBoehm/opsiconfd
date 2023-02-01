# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
messagebus.websocket
"""

import traceback
from asyncio import Task, create_task, sleep
from time import time
from typing import TYPE_CHECKING, Literal, Union

import msgspec
from fastapi import APIRouter, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from opsicommon.messagebus import (  # type: ignore[import]
	ChannelSubscriptionEventMessage,
	ChannelSubscriptionOperation,
	ChannelSubscriptionRequestMessage,
	Error,
	EventMessage,
	GeneralErrorMessage,
	Message,
	TraceRequestMessage,
	TraceResponseMessage,
	timestamp,
)
from starlette.concurrency import run_in_threadpool
from starlette.endpoints import WebSocketEndpoint
from starlette.status import (
	HTTP_401_UNAUTHORIZED,
	WS_1000_NORMAL_CLOSURE,
	WS_1011_INTERNAL_ERROR,
)
from starlette.types import Receive, Scope, Send
from starlette.websockets import WebSocket, WebSocketState

from opsiconfd.logging import get_logger
from opsiconfd.utils import compress_data, decompress_data
from opsiconfd.worker import Worker

from . import get_user_id_for_host, get_user_id_for_service_worker, get_user_id_for_user
from .redis import (
	ConsumerGroupMessageReader,
	MessageReader,
	create_messagebus_session_channel,
	delete_channel,
	get_websocket_connected_users,
	send_message,
	update_websocket_count,
)

if TYPE_CHECKING:
	from opsiconfd.session import OPSISession

messagebus_router = APIRouter()
logger = get_logger("opsiconfd.messagebus")


def messagebus_setup(_app: FastAPI) -> None:
	_app.include_router(messagebus_router, prefix="/messagebus")


@messagebus_router.get("/")
async def messagebroker_index() -> HTMLResponse:
	return HTMLResponse("<h1>messagebus</h1>")


@messagebus_router.websocket_route("/v1")
class MessagebusWebsocket(WebSocketEndpoint):  # pylint: disable=too-many-instance-attributes
	encoding = "bytes"

	def __init__(self, scope: Scope, receive: Receive, send: Send) -> None:
		super().__init__(scope, receive, send)
		self._worker = Worker.get_instance()
		self._messagebus_worker_id = get_user_id_for_service_worker(self._worker.id)
		self._messagebus_user_id = ""
		self._session_channel = ""
		self._compression: Union[str, None] = None
		self._messagebus_reader: list[MessageReader] = []
		self._manager_task = Union[Task, None]
		self._message_decoder = msgspec.msgpack.Decoder()

	@property
	def _user_channel(self) -> str:
		return self._messagebus_user_id

	async def _check_authorization(self) -> None:
		if not self.scope.get("session"):
			raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Access to messagebus denied, no valid session found")
		if not self.scope["session"].authenticated:
			raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Access to messagebus denied, not authenticated")

	async def _send_message_to_websocket(self, websocket: WebSocket, message: Message) -> None:
		if isinstance(message, (TraceRequestMessage, TraceResponseMessage)):
			message.trace = message.trace or {}
			message.trace["broker_ws_send"] = timestamp()

		data = message.to_msgpack()
		if self._compression:
			data = await run_in_threadpool(compress_data, data, self._compression)

		if websocket.client_state != WebSocketState.CONNECTED:
			logger.warning("Websocket client not connected")
			return

		logger.debug("Message to websocket: %r", message)
		await websocket.send_bytes(data)

	async def manager_task(self, websocket: WebSocket) -> None:
		update_session_interval = 5.0
		update_session_time = time()
		while websocket.client_state == WebSocketState.CONNECTED:
			await sleep(1.0)
			now = time()
			if update_session_time + update_session_interval <= now:
				update_session_time = now
				await self.scope["session"].update_last_used()  # pylint: disable=loop-invariant-statement

	async def message_reader_task(self, websocket: WebSocket, reader: MessageReader) -> None:
		ack_all_messages = isinstance(reader, ConsumerGroupMessageReader)
		try:
			async for redis_id, message, _context in reader.get_messages():
				await self._send_message_to_websocket(websocket, message)
				if ack_all_messages or message.channel == self._user_channel:
					# ACK message (set last-delivered-id)
					# create_task(reader.ack_message(redis_id))
					await reader.ack_message(message.channel, redis_id)
		except StopAsyncIteration:
			pass
		except Exception as err:  # pylint: disable=broad-except
			logger.error(err, exc_info=True)

	def _check_channel_access(
		self, channel: str, operation: Literal["read", "write"]
	) -> bool:  # pylint: disable=too-many-return-statements
		if operation not in ("read", "write"):
			raise ValueError(f"Invalid channel operation {operation!r}")

		if channel.startswith("session:"):
			return True
		if channel == self._user_channel:
			return True
		if channel.startswith("service:"):
			if channel in ("service:messagebus", "service:config:jsonrpc", "service:config:terminal"):
				if operation == "write":
					return True
			elif channel.startswith("service:depot:"):
				parts = channel.split(":")
				if len(parts) != 4 or parts[-1] not in ("jsonrpc", "terminal"):
					raise ValueError(f"Invalid channel {channel!r}")
			else:
				raise ValueError(f"Invalid channel {channel!r}")

		if self.scope["session"].is_admin:
			return True

		logger.warning("Access to channel %s denied for %s", channel, self.scope["session"].username, exc_info=True)
		return False

	async def _get_subscribed_channels(self) -> dict[str, MessageReader]:
		channels = {}
		for reader in self._messagebus_reader:
			for channel in await reader.get_channel_names():
				channels[channel] = reader
		return channels

	async def _process_channel_subscription(  # pylint: disable=too-many-locals, too-many-branches, too-many-statements
		self, websocket: WebSocket, channels: list[str], message: ChannelSubscriptionRequestMessage | None = None
	) -> None:
		subsciption_event = ChannelSubscriptionEventMessage(
			sender=self._messagebus_worker_id,
			channel=(message.back_channel if message else None) or self._session_channel,
			subscribed_channels=[],
			error=None,
		)
		operation = message.operation if message else ChannelSubscriptionOperation.ADD
		if operation not in (ChannelSubscriptionOperation.ADD, ChannelSubscriptionOperation.SET, ChannelSubscriptionOperation.REMOVE):
			err = f"Invalid operation {operation!r}"
			if not message:
				raise ValueError(err)
			subsciption_event.error = Error(code=0, message=err, details=None)
			await self._send_message_to_websocket(websocket, subsciption_event)

		subscribed_channels: dict[str, MessageReader] = await self._get_subscribed_channels()

		for idx, channel in enumerate(channels):
			channel = channel.strip()
			if channel == "@":
				channel = self._user_channel
			elif channel == "$":
				channel = self._session_channel
			channels[idx] = channel

		remove_channels = []
		if operation == ChannelSubscriptionOperation.REMOVE:
			for channel in channels:
				if channel in subscribed_channels:
					remove_channels.append(channel)
		elif operation == ChannelSubscriptionOperation.SET:
			for channel in subscribed_channels:
				if channel not in channels:
					remove_channels.append(channel)

		remove_by_reader: dict[MessageReader, list[str]] = {}
		for channel in remove_channels:
			reader = subscribed_channels.get(channel)
			if reader:
				if not reader in remove_by_reader:
					remove_by_reader[reader] = []
				remove_by_reader[reader].append(channel)

		for reader, chans in remove_by_reader.items():
			if sorted(chans) == sorted(await reader.get_channel_names()):
				await reader.stop(wait=False)
				self._messagebus_reader.remove(reader)
			else:
				await reader.remove_channels(chans)

		if operation in (ChannelSubscriptionOperation.SET, ChannelSubscriptionOperation.ADD):
			message_reader_channels: dict[str, str] = {}
			for channel in channels:
				if not self._check_channel_access(channel, "read"):
					subsciption_event.error = Error(  # pylint: disable=loop-invariant-statement
						code=0,
						message=f"Write access to channel {channel!r} denied",
						details=None,
					)
					await self._send_message_to_websocket(websocket, subsciption_event)
					return

				if channel.startswith("service:"):
					consumer_name = f"{self._messagebus_user_id}:{self._session_channel.split(':', 1)[1]}"
					reader = ConsumerGroupMessageReader(consumer_group=channel, consumer_name=consumer_name, channels={channel: "0"})
					self._messagebus_reader.append(reader)
					create_task(self.message_reader_task(websocket, reader))
				else:
					# ID ">" means that we want to receive all undelivered messages.
					# ID "$" means that we only want new messages (added after reader was started).
					message_reader_channels[channel] = "$" if channel.startswith("event:") else ">"
					if channel.startswith("session:") and channel != self._session_channel:
						await create_messagebus_session_channel(
							owner_id=self._messagebus_user_id, session_id=channel.split(":", 2)[1], exists_ok=True
						)

			if message_reader_channels:
				msr = [
					# Check for exact class (ConsumerGroupMessageReader is subclass of MessageReader)
					r
					for r in self._messagebus_reader
					if type(r) == MessageReader  # pylint: disable=unidiomatic-typecheck
				]
				if msr:
					await msr[0].add_channels(message_reader_channels)  # type: ignore[arg-type]
				else:
					reader = MessageReader(message_reader_channels)  # type: ignore[arg-type]
					self._messagebus_reader.append(reader)
					create_task(self.message_reader_task(websocket, reader))

		subsciption_event.subscribed_channels = list(await self._get_subscribed_channels())
		await self._send_message_to_websocket(websocket, subsciption_event)

	async def _process_channel_subscription_message(self, websocket: WebSocket, message: ChannelSubscriptionRequestMessage) -> None:
		await self._process_channel_subscription(websocket=websocket, channels=message.channels, message=message)

	async def dispatch(self) -> None:
		websocket = WebSocket(self.scope, receive=self.receive, send=self.send)
		await self._check_authorization()

		compression = websocket.query_params.get("compression")
		if compression:
			if compression not in ("lz4", "gzip"):
				msg = f"Invalid compression {compression!r}, valid compressions are lz4 and gzip"
				logger.error(msg)
				raise HTTPException(
					status_code=status.HTTP_400_BAD_REQUEST,
					detail=msg,
				)
			self._compression = compression

		await websocket.accept()

		self._manager_task = create_task(self.manager_task(websocket))

		await self.on_connect(websocket)

		close_code = WS_1000_NORMAL_CLOSURE
		try:
			while True:
				message = await websocket.receive()
				if message["type"] == "websocket.receive":
					data = await self.decode(websocket, message)
					await self.on_receive(websocket, data)
				elif message["type"] == "websocket.disconnect":
					close_code = int(message.get("code", WS_1000_NORMAL_CLOSURE))
					break
		except Exception as exc:
			close_code = WS_1011_INTERNAL_ERROR
			raise exc
		finally:
			await self.on_disconnect(websocket, close_code)

	async def on_receive(self, websocket: WebSocket, data: bytes) -> None:
		message_id = None
		try:
			receive_timestamp = timestamp()
			if self._compression:
				data = await run_in_threadpool(decompress_data, data, self._compression)
			msg_dict = self._message_decoder.decode(data)
			if not isinstance(msg_dict, dict):
				raise ValueError("Invalid message received")

			message_id = msg_dict["id"]
			msg_dict["sender"] = self._messagebus_user_id

			message = Message.from_dict(msg_dict)
			if not message.back_channel or message.back_channel == "$":
				message.back_channel = self._session_channel
			elif message.back_channel == "@":
				message.back_channel = self._user_channel

			if message.channel == "$":
				message.channel = self._session_channel
			elif message.channel == "@":
				message.channel = self._user_channel

			if not self._check_channel_access(message.channel, "write") or not self._check_channel_access(message.back_channel, "write"):
				raise RuntimeError(f"Read access to channel {message.channel!r} denied")
			logger.debug("Message from websocket: %r", message)

			if isinstance(message, ChannelSubscriptionRequestMessage):
				await self._process_channel_subscription_message(websocket, message)
			else:
				if isinstance(message, (TraceRequestMessage, TraceResponseMessage)):
					message.trace = message.trace or {}
					message.trace["broker_ws_receive"] = receive_timestamp

				await send_message(message, self.scope["session"].serialize())

		except Exception as err:  # pylint: disable=broad-except
			logger.warning(err, exc_info=True)
			await self._send_message_to_websocket(
				websocket,
				GeneralErrorMessage(
					sender=self._messagebus_worker_id,
					channel=self._session_channel,
					ref_id=message_id,
					error=Error(
						code=0,
						message=str(err),
						details=str(traceback.format_exc()) if self.scope["session"].is_admin else None,
					),
				),
			)

	async def on_connect(self, websocket: WebSocket) -> None:  # pylint: disable=arguments-differ
		logger.info("Websocket client connected to messagebus")
		session: OPSISession = self.scope["session"]

		event = EventMessage(
			sender=self._messagebus_worker_id,
			channel="",
			event="",
			data={
				"client_address": session.client_addr,
				"client_port": session.client_port,
				"worker": self._worker.id,
			},
		)

		if session.host:
			self._messagebus_user_id = get_user_id_for_host(session.host.id)

			user_type = "client" if session.host.getType() == "OpsiClient" else "depot"
			connected = bool([u async for u in get_websocket_connected_users(user_ids=[session.host.id], user_type=user_type)])
			if not connected:
				event.event = "host_connected"
				event.channel = "event:host_connected"
				event.data["host"] = {
					"type": session.host.getType(),
					"id": session.host.id,
				}
		elif session.username and session.is_admin:
			self._messagebus_user_id = get_user_id_for_user(session.username)

			connected = bool([u async for u in get_websocket_connected_users(user_ids=[session.username], user_type="user")])
			if not connected:
				event.event = "user_connected"
				event.channel = "event:user_connected"
				event.data["user"] = {"username": session.username}
		else:
			raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid session")

		await update_websocket_count(session, 1)

		self._session_channel = await create_messagebus_session_channel(owner_id=self._messagebus_user_id, exists_ok=True)
		await self._process_channel_subscription(websocket=websocket, channels=[self._user_channel, self._session_channel])

		await send_message(event)

	async def on_disconnect(self, websocket: WebSocket, close_code: int) -> None:  # pylint: disable=unused-argument
		logger.info("Websocket client disconnected from messagebus")
		for reader in self._messagebus_reader:
			try:
				await reader.stop(wait=False)
			except Exception as err:  # pylint: disable=broad-except
				logger.error(err, exc_info=True)

		session: OPSISession = self.scope["session"]

		await update_websocket_count(session, -1)
		await delete_channel(self._session_channel)

		event = EventMessage(
			sender=self._messagebus_worker_id,
			channel="",
			event="",
			data={
				"client_address": session.client_addr,
				"client_port": session.client_port,
				"worker": self._worker.id,
			},
		)

		if session.host:
			user_type = "client" if session.host.getType() == "OpsiClient" else "depot"
			connected = bool([u async for u in get_websocket_connected_users(user_ids=[session.host.id], user_type=user_type)])
			if not connected:
				event.event = "host_disconnected"
				event.channel = "event:host_disconnected"
				event.data["host"] = {
					"type": session.host.getType(),
					"id": session.host.id,
				}
				await send_message(event)
		elif session.username:
			connected = bool([u async for u in get_websocket_connected_users(user_ids=[session.username], user_type="user")])
			if not connected:
				event.event = "user_disconnected"
				event.channel = "event:user_disconnected"
				event.data["user"] = {"username": session.username}
				await send_message(event)
