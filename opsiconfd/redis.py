# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
opsiconfd redis utils
"""


from __future__ import annotations

import asyncio
import functools
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncGenerator, Callable, Generator
from uuid import uuid4

import redis
from redis import BusyLoadingError
from redis import ConnectionError as RedisConnectionError
from redis import ResponseError
from redis import asyncio as async_redis

from opsiconfd.config import config
from opsiconfd.utils import normalize_ip_address

redis_pool_lock = threading.Lock()
async_redis_pool_lock = asyncio.Lock()
redis_connection_pool: dict[str, redis.ConnectionPool] = {}
async_redis_connection_pool: dict[str, async_redis.ConnectionPool] = {}


def decode_redis_result(_obj: Any) -> Any:
	if isinstance(_obj, bytes):
		_obj = _obj.decode("utf8")
	elif isinstance(_obj, list):
		for idx in range(len(_obj)):  # pylint: disable=consider-using-enumerate
			_obj[idx] = decode_redis_result(_obj[idx])
	elif isinstance(_obj, dict):
		for (key, val) in _obj.items():
			_obj[decode_redis_result(key)] = decode_redis_result(val)
	elif isinstance(_obj, set):
		for val in _obj:
			_obj.remove(val)
			_obj.add(decode_redis_result(val))
	return _obj


def ip_address_to_redis_key(address: str) -> str:
	if ":" in address:
		# ipv6
		return normalize_ip_address(address, exploded=True).replace(":", ".")
	return address


def ip_address_from_redis_key(key: str) -> str:
	if key.count(".") > 3:
		# ipv6
		return key.replace(".", ":")
	return key


def retry_redis_call(func: Callable) -> Callable:
	@functools.wraps(func)
	def wrapper_retry(*args: Any, **kwargs: Any) -> Callable:  # pylint: disable=inconsistent-return-statements
		while True:
			try:  # pylint: disable=loop-try-except-usage
				return func(*args, **kwargs)  # pylint: disable=loop-invariant-statement
			except (  # pylint: disable=loop-invariant-statement
				BusyLoadingError,
				RedisConnectionError,
			):
				time.sleep(2)  # pylint: disable=dotted-import-in-loop

	return wrapper_retry


def get_redis_connection(url: str, db: int = 0, timeout: int = 0, test_connection: bool = False) -> redis.StrictRedis:  # pylint: disable=invalid-name
	start = time.time()
	con_id = f"{url}/{db}"
	while True:
		try:  # pylint: disable=loop-try-except-usage
			new_pool = False
			with redis_pool_lock:  # pylint: disable=loop-global-usage
				if con_id not in redis_connection_pool:  # pylint: disable=loop-global-usage,loop-invariant-statement
					new_pool = True
					redis_connection_pool[con_id] = redis.ConnectionPool.from_url(url, db=db)  # pylint: disable=dotted-import-in-loop,loop-global-usage,loop-invariant-statement
			client = redis.StrictRedis(connection_pool=redis_connection_pool[con_id])  # pylint: disable=dotted-import-in-loop,loop-invariant-statement,loop-global-usage
			if new_pool or test_connection:
				client.ping()
			return client
		except (RedisConnectionError, BusyLoadingError):  # pylint: disable=loop-invariant-statement
			if timeout and time.time() - start >= timeout:  # pylint: disable=dotted-import-in-loop
				raise
			time.sleep(2)  # pylint: disable=dotted-import-in-loop


@contextmanager
def redis_client(timeout: int = 0, test_connection: bool = False) -> Generator[redis.StrictRedis, None, None]:
	con = get_redis_connection(url=config.redis_internal_url, timeout=timeout, test_connection=test_connection)
	try:
		yield con
	finally:
		con.close()


async def get_async_redis_connection(url: str, db: int = 0, timeout: int = 0, test_connection: bool = False) -> async_redis.StrictRedis:  # pylint: disable=invalid-name
	start = time.time()
	while True:
		try:  # pylint: disable=loop-try-except-usage
			con_id = f"{id(asyncio.get_running_loop())}/{url}/{db}"  # pylint: disable=dotted-import-in-loop
			new_pool = False
			async with async_redis_pool_lock:  # pylint: disable=loop-global-usage
				if con_id not in async_redis_connection_pool:  # pylint: disable=loop-global-usage
					new_pool = True
					async_redis_connection_pool[con_id] = async_redis.ConnectionPool.from_url(url, db=db)  # pylint: disable=dotted-import-in-loop,loop-global-usage
			# This will return a client (no Exception) even if connection is currently lost
			client: async_redis.StrictRedis = async_redis.StrictRedis(connection_pool=async_redis_connection_pool[con_id])  # pylint: disable=dotted-import-in-loop,loop-global-usage
			if new_pool or test_connection:
				await client.ping()
			return client
		except (RedisConnectionError, BusyLoadingError):  # pylint: disable=loop-invariant-statement
			if timeout and time.time() - start >= timeout:  # pylint: disable=dotted-import-in-loop
				raise
			await asyncio.sleep(2)  # pylint: disable=dotted-import-in-loop


async def async_redis_client(timeout: int = 0, test_connection: bool = False) -> async_redis.StrictRedis:
	return await get_async_redis_connection(url=config.redis_internal_url, timeout=timeout, test_connection=test_connection)


def delete_recursively(redis_key: str, piped: bool = True) -> None:
	with redis_client() as client:
		delete_keys = []
		for key in client.scan_iter(f"{redis_key}:*"):  # pylint: disable=loop-invariant-statement
			if piped:
				delete_keys.append(key)
			else:
				client.unlink(key)

		if piped:
			with client.pipeline() as pipe:
				for key in delete_keys:
					pipe.unlink(key)
				pipe.unlink(redis_key)
				pipe.execute()
		else:
			client.unlink(redis_key)


async def async_delete_recursively(redis_key: str, piped: bool = True) -> None:
	client = await async_redis_client()
	delete_keys = []
	async for key in client.scan_iter(f"{redis_key}:*"):
		if piped:
			delete_keys.append(key)
		else:
			await client.unlink(key)

	if piped:
		async with client.pipeline() as pipe:
			for key in delete_keys:
				pipe.unlink(key)
			pipe.unlink(redis_key)
			await pipe.execute()
	else:
		await client.unlink(redis_key)


@contextmanager
def redis_lock(lock_name: str, acquire_timeout: float = 10.0, lock_timeout: float | None = None) -> Generator[str, None, None]:
	conf = config
	identifier = str(uuid4())
	indentifier_b = identifier.encode("utf-8")
	redis_key = f"{conf.redis_key('locks')}:{lock_name}"
	end = time.time() + acquire_timeout
	with redis_client() as client:
		while True:  # pylint: disable=dotted-import-in-loop
			if client.setnx(redis_key, identifier):
				if lock_timeout:
					client.pexpire(redis_key, round(lock_timeout * 1000))  # milliseconds  # pylint: disable=loop-invariant-statement
				break
			if time.time() >= end:  # pylint: disable=dotted-import-in-loop
				raise TimeoutError(f"Failed to acquire {lock_name} lock in {acquire_timeout:0.2f} seconds")  # pylint: disable=loop-invariant-statement
			time.sleep(0.5)  # pylint: disable=dotted-import-in-loop
		try:
			yield identifier
		finally:
			with client.pipeline(transaction=True) as pipe:
				while True:
					try:  # pylint: disable=loop-try-except-usage
						# Redis will only perform the transaction if the watched keys were not modified.
						pipe.watch(redis_key)
						if pipe.get(redis_key) == indentifier_b:
							# Release lock
							pipe.multi()
							pipe.delete(redis_key)
							pipe.execute()
						else:
							# Different identifier, not our lock
							pipe.unwatch()
						break
					except redis.exceptions.WatchError:  # pylint: disable=dotted-import-in-loop,loop-invariant-statement
						pass


@asynccontextmanager
async def async_redis_lock(lock_name: str, acquire_timeout: float = 10.0, lock_timeout: float | None = None) -> AsyncGenerator[str, None]:
	conf = config
	identifier = str(uuid4())
	identifier_b = identifier.encode("utf-8")
	redis_key = f"{conf.redis_key('locks')}:{lock_name}"
	end = time.time() + acquire_timeout
	client = await async_redis_client()

	while True:  # pylint: disable=dotted-import-in-loop
		if await client.setnx(redis_key, identifier):
			if lock_timeout:
				await client.pexpire(redis_key, round(lock_timeout * 1000))  # milliseconds  # pylint: disable=loop-invariant-statement
			break
		if time.time() >= end:  # pylint: disable=dotted-import-in-loop,
			raise TimeoutError(f"Failed to acquire {lock_name} lock in {acquire_timeout:0.2f} seconds")  # pylint: disable=loop-invariant-statement
		await asyncio.sleep(0.5)  # pylint: disable=dotted-import-in-loop

	try:
		yield identifier
	finally:
		async with client.pipeline(transaction=True) as pipe:
			while True:
				try:  # pylint: disable=loop-try-except-usage
					# Redis will only perform the transaction if the watched keys were not modified.
					await pipe.watch(redis_key)
					if await pipe.get(redis_key) == identifier_b:
						# Release lock
						pipe.multi()
						pipe.delete(redis_key)
						await pipe.execute()
					else:
						# Different identifier, not our lock
						await pipe.unwatch()
					break
				except redis.exceptions.WatchError:  # pylint: disable=dotted-import-in-loop,loop-invariant-statement
					pass


async def async_get_redis_info(client: async_redis.StrictRedis) -> dict[str, Any]:  # pylint: disable=too-many-locals
	conf = config

	key_info: dict[str, dict[str, list | int]] = {
		"rpc": {"keys": [], "memory": 0, "entries": 0, "prefixes": [f"{conf.redis_key('stats')}:rpc", f"{conf.redis_key('stats')}:num_rpc"]},
		"stats": {"keys": [], "memory": 0, "entries": 0, "prefixes": [conf.redis_key('stats')]},
		"session": {"keys": [], "memory": 0, "entries": 0, "prefixes": [conf.redis_key('session')]},
		"log": {"keys": [], "memory": 0, "entries": 0, "prefixes": [conf.redis_key('log')]},
		"state": {"keys": [], "memory": 0, "entries": 0, "prefixes": [conf.redis_key('state')]},
		"messagebus": {"keys": [], "memory": 0, "entries": 0, "prefixes": [conf.redis_key('messagebus')]},
		"misc": {"keys": [], "memory": 0, "entries": 0, "prefixes": []},
	}

	async for key in client.scan_iter(f"{conf.redis_key()}:*"):
		key = key.decode("utf8")
		matched_key_type = ""
		for key_type, info in key_info.items():
			for prefix in info["prefixes"]:  # type: ignore[union-attr]
				if key.startswith(prefix):
					matched_key_type = key_type
					break
		matched_key_type = matched_key_type or "misc"
		key_info[matched_key_type]["keys"].append(key)  # type: ignore[union-attr]
		try:
			command = f"MEMORY USAGE {key}"
			key_info[matched_key_type]["memory"] += (  # type: ignore[union-attr,operator]
				await client.execute_command(command)  # type: ignore[no-untyped-call]
			) or 0
		except ResponseError as err:
			from opsiconfd.logging import logger  # pylint: disable=import-outside-toplevel
			logger.error("Redis command %r failed: %s", command, err, exc_info=True)
		try:
			key_info[matched_key_type]["entries"] += (  # type: ignore[union-attr,operator]
				await client.execute_command(f"XLEN {key}")  # type: ignore[no-untyped-call]
			) or 0
		except ResponseError:
			# Wrong key type
			pass

	redis_info = decode_redis_result(await client.execute_command("INFO"))  # type: ignore[no-untyped-call]
	redis_info["key_info"] = {
		key_type: {
			"keys": len(info["keys"]),  # type: ignore[arg-type]
			"memory": info["memory"],
			"entries": info["entries"]
		}
		for key_type, info in key_info.items()
	}
	return redis_info
