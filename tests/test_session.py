# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
session tests
"""

import time
import uuid
from asyncio import sleep
from unittest.mock import patch

import pytest
from starlette.datastructures import Headers

from opsiconfd.session import FastAPI, OPSISession, SessionManager, SessionMiddleware
from opsiconfd.utils import asyncio_create_task, utc_time_timestamp

from .utils import (  # pylint: disable=unused-import
	ADMIN_PASS,
	ADMIN_USER,
	OpsiconfdTestClient,
	async_redis_client,
	clean_redis,
	get_config,
	test_client,
)


def test_session_serialize() -> None:
	client_addr = "172.10.11.12"
	session = OPSISession(client_addr=client_addr)
	data = session.serialize()
	session2 = OPSISession.from_serialized(data)
	assert session.serialize() == session2.serialize()


@pytest.mark.asyncio
async def test_session_store_and_load() -> None:
	async with async_redis_client() as redis_client:
		client_addr = "172.10.11.12"
		sess1 = OPSISession(client_addr=client_addr)
		sess1.is_read_only = False
		sess1.is_admin = True
		sess1.username = "test"
		sess1.user_groups = {"group1", "group2", "group3"}
		sess1.max_age = 123

		await sess1.init()

		assert not sess1.expired
		assert not sess1.deleted
		assert sess1.persistent

		await sess1.store()

		assert not sess1.modifications

		sess2 = OPSISession(client_addr=client_addr, session_id=sess1.session_id)
		await sess2.load()
		assert not sess2.modifications
		assert sess2.is_read_only == sess1.is_read_only
		assert sess2.is_admin == sess1.is_admin
		assert sess2.username == sess1.username
		assert sess2.user_groups == sess1.user_groups
		assert sess2.max_age == sess1.max_age
		assert sess2.last_used == sess1.last_used
		assert sess2.messagebus_last_used == sess1.messagebus_last_used

		await redis_client.delete(sess2.redis_key)
		await sleep(1)
		await sess2.update_last_used()
		assert list(sess2.modifications) == ["last_used"]
		await sess2.store(wait=True, modifications_only=True)

		sess3 = OPSISession(client_addr=client_addr, session_id=sess1.session_id)
		await sess3.load()
		assert not sess3.modifications
		assert sess3.is_read_only == sess2.is_read_only
		assert sess3.is_admin == sess2.is_admin
		assert sess3.username == sess2.username
		assert sess3.user_groups == sess2.user_groups
		assert sess3.max_age == sess2.max_age
		assert sess3.last_used == sess2.last_used
		assert sess3.messagebus_last_used == sess2.messagebus_last_used


@pytest.mark.asyncio
async def test_session_manager_max_age() -> None:
	with get_config({"session_lifetime": 10}):
		manager = SessionManager()
		asyncio_create_task(manager.manager_task())

		headers = Headers()
		sess = await manager.get_session("172.10.11.12", headers=headers)
		assert sess.max_age == 10

		await sess.store()
		await sess.load()
		assert sess.max_age == 10

		headers = Headers({"x-opsi-session-lifetime": "5"})
		sess = await manager.get_session("172.10.11.12", headers=headers)
		assert sess.max_age == 5

		await sess.store()
		await sess.load()
		assert sess.max_age == 5

		await sess.update_messagebus_last_used()
		assert sess.max_age == 5
		cookie = sess.get_cookie()
		assert cookie
		# Session cookie
		assert "Max-Age" not in cookie

		await sess.store()
		await sess.load()
		assert sess.max_age == 5
		cookie = sess.get_cookie()
		assert cookie
		# Session cookie
		assert "Max-Age" not in cookie

		sess._messagebus_last_used = int(utc_time_timestamp()) - 60  # pylint: disable=protected-access
		assert sess.max_age == 5
		cookie = sess.get_cookie()
		assert cookie
		assert cookie.endswith("Max-Age=5")

		await manager.stop(wait=True)


@pytest.mark.asyncio
async def test_session_refresh() -> None:
	async with async_redis_client() as redis_client:
		manager = SessionManager()
		asyncio_create_task(manager.manager_task())

		sess = await manager.get_session("172.10.11.12")
		sess.username = "testuser"
		await sess.store()
		assert sess.version
		res = await redis_client.hgetall(sess.redis_key)
		assert res[b"version"] == sess.version.encode("utf-8")

		# Change an attribute to see if session is loaded
		await redis_client.hset(sess.redis_key, "username", "changed-in-redis")

		# Should return True, but session should not be loaded from redis (username unchanged)
		assert await sess.refresh()
		assert sess.username == "testuser"

		await redis_client.hset(sess.redis_key, "version", str(uuid.uuid4()))
		# Should return True, and session should not be loaded from redis
		assert await sess.refresh()
		assert sess.username == "changed-in-redis"

		# Change an attribute to see if session is loaded
		await redis_client.hset(sess.redis_key, "username", "changed-in-redis-again")
		# Should return True, but session should not be loaded from redis (username unchanged)
		assert await sess.refresh()
		assert sess.username == "changed-in-redis"

		# Now delete session in redis
		await redis_client.delete(sess.redis_key)
		# Should return False, session not loaded
		assert not await sess.refresh()

		await manager.stop(wait=True)


@pytest.mark.asyncio
async def test_session_manager_store_session() -> None:
	async with async_redis_client() as redis_client:
		manager = SessionManager()
		asyncio_create_task(manager.manager_task())

		manager._session_store_interval = 60  # pylint: disable=protected-access
		sess1 = await manager.get_session("172.10.11.11")
		await sleep(2)
		res = await redis_client.hgetall(sess1.redis_key)
		sess1.authenticated = True
		await sleep(2)
		res = await redis_client.hgetall(sess1.redis_key)
		assert res

		manager._session_store_interval = 1  # pylint: disable=protected-access
		sess2 = await manager.get_session("172.10.11.12")
		res = await redis_client.hgetall(sess2.redis_key)
		assert not res
		await sleep(2)
		res = await redis_client.hgetall(sess2.redis_key)
		assert res

		await manager.stop(wait=True)


@pytest.mark.asyncio
async def test_session_manager_remove_expired_session() -> None:
	async with async_redis_client() as redis_client:
		manager = SessionManager()
		asyncio_create_task(manager.manager_task())

		headers = Headers({"x-opsi-session-lifetime": "5"})
		sess = await manager.get_session("172.10.11.12", headers=headers)
		sess.authenticated = True
		await sleep(3)
		assert sess.session_id in manager.sessions
		res = await redis_client.hgetall(sess.redis_key)
		assert res
		await sleep(8)
		assert sess.session_id not in manager.sessions
		res = await redis_client.hgetall(sess.redis_key)
		assert not res
		await manager.stop(wait=True)


@pytest.mark.asyncio
async def test_session_manager_changed_client_addr() -> None:
	manager = SessionManager()
	asyncio_create_task(manager.manager_task())

	sess1 = await manager.get_session("172.10.11.12")
	assert sess1
	sess2 = await manager.get_session("172.10.11.13", session_id=sess1.session_id)
	assert sess2
	assert sess1.session_id != sess2.session_id
	await manager.stop(wait=True)


@pytest.mark.asyncio
async def test_session_manager_concurrent() -> None:
	async with async_redis_client() as redis_client:
		manager1 = SessionManager()
		manager2 = SessionManager()
		asyncio_create_task(manager1.manager_task())
		asyncio_create_task(manager2.manager_task())

		headers = Headers({"x-opsi-session-lifetime": "5"})
		sess1 = await manager1.get_session("172.10.11.12", headers=headers)
		await sess1.store()

		res = await redis_client.hgetall(sess1.redis_key)
		assert res

		headers = Headers({"x-opsi-session-lifetime": "10"})
		sess2 = await manager2.get_session("172.10.11.12", headers=headers, session_id=sess1.session_id)
		assert sess2.session_id == sess1.session_id
		assert sess2.created == sess1.created
		assert sess2.max_age == 10

		sess2.max_age = 1
		await sleep(2)

		assert sess2.deleted

		res = await redis_client.hgetall(sess1.redis_key)
		assert not res

		sess1 = await manager1.get_session("172.10.11.12", session_id=sess1.session_id)
		assert sess1.session_id != sess2.session_id

		await manager1.stop(wait=True)
		await manager2.stop(wait=True)


def test_server_overload(
	test_client: OpsiconfdTestClient,  # pylint: disable=redefined-outer-name
) -> None:
	test_client.auth = (ADMIN_USER, ADMIN_PASS)
	session_middleware = None

	class MockSessionMiddleware(SessionMiddleware):
		def __init__(self, app: FastAPI, public_path: list[str] | None = None) -> None:
			SessionMiddleware.__init__(self, app, public_path)
			nonlocal session_middleware
			session_middleware = self

	with patch("opsiconfd.application.main.SessionMiddleware", MockSessionMiddleware):
		with test_client as client:
			for _ in range(4):
				if isinstance(session_middleware, MockSessionMiddleware):
					break
				time.sleep(1)
			assert isinstance(session_middleware, MockSessionMiddleware)

			response = client.get("/session/authenticated")
			assert response.status_code == 200

			# Set overload state for 5 seconds
			session_middleware.set_overload(5)

			# Localhost is not affected by overload
			response = client.get("/session/authenticated")
			assert response.status_code == 200

			# Set client address to be affected by overload
			test_client.set_client_address("4.3.2.1", 12345)
			response = client.get("/session/authenticated")
			assert response.status_code == 503
			assert response.text == "Server overload"
			assert int(response.headers["Retry-After"]) > 10

			# Wait for overload sate to end
			time.sleep(5)
			response = client.get("/session/authenticated")
			assert response.status_code == 200
