"""
:copyright: uib GmbH <info@uib.de>
This file is part of opsi - https://www.opsi.org

:license: GNU Affero General Public License version 3
See LICENSES/README.md for more Information
"""


import os
import datetime
from operator import itemgetter

from fastapi import APIRouter, Request, Response, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..session import OPSISession
from ..logging import logger
from ..config import config
from ..backend import get_client_backend, get_backend_interface
from ..worker import get_redis_client

admin_interface_router = APIRouter()
templates = Jinja2Templates(directory=os.path.join(config.static_dir, "templates"))

def admin_interface_setup(app):
	app.include_router(admin_interface_router, prefix="/admin")

@admin_interface_router.get("/?")
async def admin_interface_index(request: Request):

	now = datetime.time()
	time = datetime.datetime.now() - datetime.timedelta(days=2)
	date_first_rpc = time.strftime("%m/%d/%Y, %H:%M:%S")

	blocked_clients = await get_blocked_clients()
	rpc_list = await get_rpc_list()
	rpc_count = await get_rpc_count()
	
	context = {
		"request": request,
		"interface": get_backend_interface(),
		"rpc_count": rpc_count,
		"date_first_rpc": date_first_rpc,
		"rpc_list": rpc_list,
		"blocked_clients": blocked_clients
	}

	return templates.TemplateResponse("admininterface.html", context)

@admin_interface_router.post("/unblock-all")
async def unblock_all_clients(request: Request, response: Response):
	logger.notice("unblock_all_clients")
	redis_client = await get_redis_client()
	
	try:
		clients = []
		deleted_keys = []
		keys = redis_client.scan_iter("opsiconfd:stats:client:failed_auth:*")
		async for key in keys:
			deleted_keys.append(key.decode("utf8"))
			if key.decode("utf8").split(":")[-1] not in clients:
				clients.append(key.decode("utf8").split(":")[-1])
			logger.debug("redis key to delete: %s", key)
			await redis_client.delete(key)

		keys = redis_client.scan_iter("opsiconfd:stats:client:blocked:*")		
		async for key in keys:
			logger.debug("redis key to delete: %s", key)
			deleted_keys.append(key.decode("utf8"))
			if key.decode("utf8").split(":")[-1] not in clients:
				clients.append(key.decode("utf8").split(":")[-1])
			await redis_client.delete(key)

		response = JSONResponse({"status": 200, "error": None, "data": {"clients": clients, "redis-keys": deleted_keys}})
	except Exception as e:
		logger.error("Error while removing redis client keys: %s", e)
		response = JSONResponse({"status": 500, "error": { "message": "Error while removing redis client keys", "detail": str(e)}})

	return response


@admin_interface_router.post("/unblock-client")
async def unblock_client(request: Request):
	try:
		request_body = await request.json()
		client_addr = request_body.get("client_addr")
		
		logger.debug("unblock client addr: %s ", client_addr)
		redis_client = await get_redis_client()
		deleted_keys = []
		redis_code = await redis_client.delete(f"opsiconfd:stats:client:failed_auth:{client_addr}")
		if redis_code == 1:
			deleted_keys.append(f"opsiconfd:stats:client:failed_auth:{client_addr}")
		redis_code = await redis_client.delete(f"opsiconfd:stats:client:blocked:{client_addr}")
		if redis_code == 1:
			deleted_keys.append(f"opsiconfd:stats:client:blocked:{client_addr}")


		response = JSONResponse({"status": 200, "error": None, "data": {"client": client_addr, "redis-keys": deleted_keys}})
	except Exception as e:
		logger.error("Error while removing redis client keys: %s", e)
		response = JSONResponse({"status": 500, "error": { "message": "Error while removing redis client keys", "detail": str(e)}})

	return response


@admin_interface_router.post("/delete-client-sessions")
async def delete_client_sessions(request: Request):
	try:
		request_body = await request.json()
		client_addr = request_body.get("client_addr")
		redis_client = await get_redis_client()
		keys = redis_client.scan_iter(f"{OPSISession.redis_key_prefix}:{client_addr}:*")
		sessions = []
		deleted_keys = []
		async for key in keys:
			logger.warning(key)
			logger.notice(key.decode("utf8").split(":")[-1])
			logger.warning(sessions)
			sessions.append(key.decode("utf8").split(":")[-1])
			deleted_keys.append(key.decode("utf8"))
			await redis_client.delete(key)
			
		logger.notice(sessions)
		logger.notice(deleted_keys)
		response = JSONResponse({"status": 200, "error": None, "data": {"client": client_addr, "sessions": sessions, "redis-keys": deleted_keys}})
	except Exception as e:
		logger.error("Error while removing redis session keys: %s", e)
		response = JSONResponse({"status": 500, "error": { "message": "Error while removing redis client keys", "detail": str(e)}})
	return response

@admin_interface_router.get("/rpc-list")
async def get_rpc_list() -> list:

	redis_client = await get_redis_client()
	redis_keys = redis_client.scan_iter(f"opsiconfd:stats:rpc:*")

	rpc_list = []
	async for key in redis_keys:
		num_params = await redis_client.hget(key, "num_params")
		error = await redis_client.hget(key, "error")
		num_results = await redis_client.hget(key, "num_results")
		duration = await redis_client.hget(key, "duration")
		duration = "{:.3f}".format(float(duration.decode("utf8")))
		method_name = key.decode("utf8").split(":")[-1]	
		if error.decode("utf8") == "True":
			error = True
		else:
			error = False
		rpc = {"rpc_num": int(key.decode("utf8").split(":")[-2]), "method": method_name, "params": num_params.decode("utf8"), "results": num_results.decode("utf8"), "error": error, "duration": duration}
		rpc_list.append(rpc)

	rpc_list = sorted(rpc_list, key=itemgetter('rpc_num')) 

	return rpc_list

async def get_rpc_count() -> int: 
	redis_client = await get_redis_client()

	count = await redis_client.get("opsiconfd:stats:num_rpcs")
	if count:
		count = count.decode("utf8")
	logger.notice(count)

	return count

@admin_interface_router.get("/blocked-clients")
async def get_blocked_clients() -> list:
	redis_client = await get_redis_client()
	redis_keys = redis_client.scan_iter("opsiconfd:stats:client:blocked:*")

	block_clients = []
	async for key in redis_keys:
		logger.debug("redis key to delete: %s", key)
		block_clients.append(key.decode("utf8").split(":")[-1])
	
	return block_clients

