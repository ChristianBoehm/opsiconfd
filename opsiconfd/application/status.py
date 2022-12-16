# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
status - available without authentication
"""

import datetime

from fastapi import APIRouter, FastAPI
from fastapi.responses import PlainTextResponse
from opsicommon import __version__ as python_opsi_common_version  # type: ignore[import]

from .. import __version__
from ..config import FQDN, config
from ..ssl import get_ca_cert_info, get_server_cert_info
from ..utils import async_get_redis_info, async_redis_client

status_router = APIRouter()


def status_setup(app: FastAPI) -> None:
	app.include_router(status_router, prefix="/status")


@status_router.get("/")
async def status_overview() -> PlainTextResponse:
	status = "ok"
	redis_status = "ok"
	redis_error = ""
	redis_mem = -1
	redis_mem_total = -1
	try:
		redis = await async_redis_client(timeout=3)
		await redis.ping()
		redis_info = await async_get_redis_info(redis)
		redis_mem_total = redis_info["used_memory"]
		for key_type in redis_info["key_info"]:
			redis_mem += redis_info["key_info"][key_type]["memory"]  # pylint: disable=loop-invariant-statement
		redis_status = "ok"
	except Exception as err:  # pylint: disable=broad-except
		redis_status = "error"
		status = "error"
		redis_error = str(err) or "connection error"

	data = (
		f"status: {status}\n"
		f"version: {__version__} [python-opsi-common={python_opsi_common_version}]\n"
		f"date: {datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()}\n"
		f"node: {config.node_name}\n"
		f"fqdn: {FQDN}\n"
		f"redis-status: {redis_status}\n"
		f"redis-error: {redis_error}\n"
		f"redis-mem: {redis_mem}\n"
		f"redis-mem-total: {redis_mem_total}\n"
		f"ssl-ca-valid-days: {get_ca_cert_info()['expires_in_days']}\n"
		f"ssl-cert-valid-days: {get_server_cert_info()['expires_in_days']}\n"
	)
	return PlainTextResponse(data)
