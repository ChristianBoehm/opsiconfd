# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
redisinterface
"""

from fastapi import APIRouter, FastAPI, Request, status
from starlette.concurrency import run_in_threadpool

from ..backend.rpc.cache import rpc_cache_clear, rpc_cache_info
from ..logging import logger
from ..rest import RESTErrorResponse, RESTResponse, rest_api
from ..utils import async_get_redis_info, async_redis_client, decode_redis_result

redis_interface_router = APIRouter()


def redis_interface_setup(app: FastAPI) -> None:
	app.include_router(redis_interface_router, prefix="/redis-interface")


@redis_interface_router.post("")
@redis_interface_router.post("/")
@rest_api(default_error_status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)
async def redis_command(request: Request) -> RESTResponse:
	redis = await async_redis_client()
	request_body = await request.json()
	redis_cmd = request_body.get("cmd")
	redis_result = await redis.execute_command(redis_cmd)  # type: ignore[no-untyped-call]
	return RESTResponse({"result": decode_redis_result(redis_result)})


@redis_interface_router.get("/redis-stats")
@rest_api
async def get_redis_stats() -> RESTResponse:  # pylint: disable=too-many-locals
	redis = await async_redis_client()
	try:
		redis_info = await async_get_redis_info(redis)
		return RESTResponse(redis_info)
	except Exception as err:  # pylint: disable=broad-except
		logger.error("Error while reading redis data: %s", err)
		return RESTErrorResponse(details=err, message="Error while reading redis data")


@redis_interface_router.get("/load-rpc-cache-info")
@rest_api
def load_rpc_cache_info() -> RESTResponse:
	return RESTResponse({"result": rpc_cache_info()})


@redis_interface_router.post("/clear-rpc-cache")
@rest_api
async def clear_rpc_cache(request: Request) -> RESTResponse:
	params = await request.json()
	cache_name = (params.get("cache_name") if params else None) or None
	await run_in_threadpool(rpc_cache_clear, cache_name)
	return RESTResponse({"result": "OK"})
