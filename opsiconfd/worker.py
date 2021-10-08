# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
worker
"""

import os
import gc
import ctypes
import signal
import threading
import asyncio
from concurrent.futures import ThreadPoolExecutor

from .logging import logger, init_logging
from .config import config
from .utils import aredis_client, get_manager_pid
from . import ssl
from .addon import AddonManager

_metrics_collector = None # pylint: disable=invalid-name
_worker_num = 1 # pylint: disable=invalid-name

def set_worker_num(num):
	global _worker_num # pylint: disable=global-statement,invalid-name
	_worker_num = num

def get_worker_num():
	return _worker_num

def init_pool_executor(loop):
	# https://bugs.python.org/issue41699
	pool_executor = ThreadPoolExecutor(  # pylint: disable=consider-using-with
		max_workers=config.executor_workers,
		thread_name_prefix="worker-ThreadPoolExecutor"
	)
	loop.set_default_executor(pool_executor)

def get_metrics_collector():
	return _metrics_collector

def handle_asyncio_exception(loop, context):
	# context["message"] will always be there but context["exception"] may not
	#msg = context.get("exception", context["message"])
	logger.error("Unhandled exception in asyncio loop '%s': %s", loop, context)

def memory_cleanup():
	gc.collect()
	ctypes.CDLL("libc.so.6").malloc_trim(0)

def signal_handler(signum, frame): # pylint: disable=unused-argument
	logger.info("Worker process %s received signal %d", os.getpid(), signum)
	if signum == signal.SIGHUP:
		logger.notice("Worker process %s reloading", os.getpid())
		config.reload()
		init_logging(log_mode=config.log_mode, is_worker=True)
		memory_cleanup()
		AddonManager().reload_addons()

async def main_loop():
	while True:
		await asyncio.sleep(120)
		memory_cleanup()

def exit_worker():
	for thread in threading.enumerate():
		if hasattr(thread, "stop"):
			thread.stop()
			thread.join()

def init_worker():
	global _metrics_collector # pylint: disable=global-statement, invalid-name
	from .backend import get_backend, get_client_backend # pylint: disable=import-outside-toplevel
	from .statistics import WorkerMetricsCollector # pylint: disable=import-outside-toplevel
	is_manager = get_manager_pid() == os.getpid()

	if not is_manager:
		try:
			set_worker_num(int(os.getenv("OPSICONFD_WORKER_WORKER_NUM")))
		except Exception as err: # pylint: disable=broad-except
			logger.error("Failed to get worker number from env: %s", err)
		# Only if this process is a worker only process (multiprocessing)
		signal.signal(signal.SIGHUP, signal_handler)
		init_logging(log_mode=config.log_mode, is_worker=True)
		opsi_ca_key = os.getenv("OPSICONFD_WORKER_OPSI_SSL_CA_KEY", None)
		if opsi_ca_key:
			ssl.KEY_CACHE[config.ssl_ca_key] = opsi_ca_key
			del os.environ["OPSICONFD_WORKER_OPSI_SSL_CA_KEY"]

	logger.notice("Init worker %d (pid %s)", get_worker_num(), os.getpid())
	loop = asyncio.get_event_loop()
	loop.set_debug(config.debug)
	init_pool_executor(loop)
	loop.set_exception_handler(handle_asyncio_exception)
	# create redis pool
	loop.create_task(aredis_client())
	loop.create_task(main_loop())
	# create and start MetricsCollector
	_metrics_collector = WorkerMetricsCollector()
	loop.create_task(_metrics_collector.main_loop())
	# create BackendManager instances
	get_backend()
	get_client_backend()
