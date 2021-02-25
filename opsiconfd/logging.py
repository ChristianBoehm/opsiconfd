# -*- coding: utf-8 -*-

# This file is part of opsi.
# Copyright (C) 2020 uib GmbH <info@uib.de>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
:copyright: uib GmbH <info@uib.de>
:license: GNU Affero General Public License version 3
"""

import shutil
import time
import sys
import os
import socket
import threading
import asyncio
from queue import Queue, Empty
from concurrent.futures import ThreadPoolExecutor
import logging as pylogging
from logging import LogRecord, Formatter, StreamHandler

import msgpack
import colorlog
from gunicorn import glogging

from aiologger.handlers.streams import AsyncStreamHandler
from aiologger.handlers.files import AsyncFileHandler

from OPSI.Config import OPSI_ADMIN_GROUP

from opsicommon.logging import ( # pylint: disable=unused-import
	logger, secret_filter, handle_log_exception, set_format,
	set_filter_from_string, ContextSecretFormatter,
	SECRET_REPLACEMENT_STRING, LOG_COLORS, DATETIME_FORMAT,
	DEFAULT_COLORED_FORMAT, OPSI_LEVEL_TO_LEVEL
)
from opsicommon.logging.logging import add_context_filter_to_loggers

from .utils import retry_redis_call, get_aredis_connection, get_redis_connection
from .config import config

# Set default log level to ERROR early
logger.setLevel(pylogging.ERROR)

class AsyncRotatingFileHandler(AsyncFileHandler):
	rollover_check_interval = 10

	def __init__( # pylint: disable=too-many-arguments
		self, filename: str,
		formatter: Formatter,
		active_lifetime: int = 0,
		mode: str = "a",
		encoding: str = 'utf-8',
		max_bytes: int = 0,
		keep_rotated: int = 0
	) -> None:
		super().__init__(filename, mode, encoding)
		self.active_lifetime = active_lifetime
		self._max_bytes = max_bytes
		self._keep_rotated = keep_rotated
		self.formatter = formatter
		self._rollover_lock = asyncio.Lock(loop=self.loop)
		self.last_used = time.time()
		self.loop.create_task(self._periodically_test_rollover())

	async def _periodically_test_rollover(self):
		while True:
			try:
				if await self.loop.run_in_executor(None, self.should_rollover):
					async with self._rollover_lock:
						await self.do_rollover()
			except Exception as exc: # pylint: disable=broad-except
				handle_log_exception(exc)
			for i in range(self.rollover_check_interval): # pylint: disable=invalid-name, unused-variable
				await asyncio.sleep(1)

	def should_rollover(self, record: LogRecord = None) -> bool: # pylint: disable=unused-argument
		if not os.path.exists(self.absolute_file_path):
			# This will recreate a deleted log file
			return True
		return os.path.getsize(self.absolute_file_path) >= self._max_bytes

	async def do_rollover(self):
		if self.stream:
			await self.stream.close()
		if self._keep_rotated > 0:
			for n in range(self._keep_rotated, 0, -1): # pylint: disable=invalid-name
				src_file_path = self.absolute_file_path
				if n > 1:
					src_file_path = f"{self.absolute_file_path}.{n-1}"
				dst_file_path = f"{self.absolute_file_path}.{n}"
				if await self.loop.run_in_executor(None, lambda: os.path.exists(src_file_path)): # pylint: disable=cell-var-from-loop
					await self.loop.run_in_executor(None, lambda: os.rename(src_file_path, dst_file_path)) # pylint: disable=cell-var-from-loop
					shutil.chown(path=dst_file_path, user=config.run_as_user, group=OPSI_ADMIN_GROUP)
					os.chmod(path=dst_file_path, mode=0o644)
		self.stream = None
		await self._init_writer()
		shutil.chown(path=self.absolute_file_path, user=config.run_as_user, group=OPSI_ADMIN_GROUP)
		os.chmod(path=self.absolute_file_path, mode=0o644)

	async def emit(self, record: LogRecord):
		async with self._rollover_lock:
			self.last_used = time.time()
			return await super().emit(record)

	async def handle_error(self, record, exception):
		if not isinstance(exception, RuntimeError):
			handle_log_exception(exception, record)

class AsyncRedisLogAdapter: # pylint: disable=too-many-instance-attributes
	def __init__(self, running_event=None, log_file_template=None, # pylint: disable=too-many-arguments
				max_log_file_size=0, keep_rotated_log_files=0, symlink_client_log_files=False,
				log_format_stderr=DEFAULT_COLORED_FORMAT, log_format_file=DEFAULT_COLORED_FORMAT,
				log_level_stderr=pylogging.NOTSET, log_level_file=pylogging.NOTSET):
		self._running_event = running_event
		self._log_file_template = log_file_template
		self._max_log_file_size = max_log_file_size
		self._keep_rotated_log_files = keep_rotated_log_files
		self._symlink_client_log_files = symlink_client_log_files
		self._log_level_stderr = log_level_stderr
		self._log_level_file = log_level_file
		self._log_format_stderr = log_format_stderr
		self._log_format_file = log_format_file
		self._loop = asyncio.get_event_loop()
		self._redis = None
		self._file_logs = {}
		self._file_log_active_lifetime = 30
		self._file_log_lock = threading.Lock()
		self._stderr_handler = None
		if self._log_level_stderr != pylogging.NONE: # pylint: disable=no-member
			if sys.stderr.isatty():
				# colorize
				console_formatter = colorlog.ColoredFormatter(self._log_format_stderr, log_colors=LOG_COLORS, datefmt=DATETIME_FORMAT)
			else:
				console_formatter = Formatter(self._log_format_no_color(self._log_format_stderr), datefmt=DATETIME_FORMAT)
			self._stderr_handler = AsyncStreamHandler(stream=sys.stderr, formatter=ContextSecretFormatter(console_formatter))

		if self._log_level_file != pylogging.NONE: # pylint: disable=no-member
			if self._log_file_template:
				self.get_file_handler()

		self._loop.create_task(self._start())

	async def stop(self):
		self._loop.stop()

	def _log_format_no_color(self, log_format): # pylint: disable=no-self-use
		return log_format.replace('%(log_color)s', '').replace('%(reset)s', '')

	async def _create_client_log_file_symlink(self, ip_address):
		try:
			fqdn = await self._loop.run_in_executor(None, lambda: socket.getfqdn(ip_address))
			if fqdn != ip_address:
				src = self._log_file_template.replace('%m', ip_address)
				src = os.path.basename(src)
				dst = self._log_file_template.replace('%m', fqdn)
				if not os.path.exists(dst):
					await self._loop.run_in_executor(None, lambda: os.symlink(src, dst))
		except Exception as exc: # pylint: disable=broad-except
			handle_log_exception(exc)

	def get_file_handler(self, client=None):
		filename = None
		if not self._log_file_template:
			return None
		try:
			name = client or 'opsiconfd'
			filename = self._log_file_template.replace('%m', name)
			with self._file_log_lock:
				if not filename in self._file_logs:
					logger.info("Creating new file log '%s'", filename)
					log_dir = os.path.dirname(filename)
					if not os.path.isdir(log_dir):
						logger.info("Creating log dir '%s'", log_dir)
						os.makedirs(log_dir)
					# Do not close main opsiconfd log file
					active_lifetime = 0 if name == 'opsiconfd' else self._file_log_active_lifetime
					self._file_logs[filename] = AsyncRotatingFileHandler(
						filename=filename,
						formatter=ContextSecretFormatter(Formatter(self._log_format_no_color(self._log_format_file), datefmt=DATETIME_FORMAT)),
						active_lifetime=active_lifetime,
						mode='a',
						encoding='utf-8',
						max_bytes=self._max_log_file_size,
						keep_rotated=self._keep_rotated_log_files
					)
					if client and self._symlink_client_log_files:
						self._loop.create_task(self._create_client_log_file_symlink(client))
				return self._file_logs[filename]
		except Exception as exc: # pylint: disable=broad-except
			self._file_logs[filename] = None
			handle_log_exception(exc)

	async def _watch_log_files(self):
		if not self._log_file_template:
			return
		while True:
			try:
				for filename in list(self._file_logs):
					if not self._file_logs[filename] or self._file_logs[filename].active_lifetime == 0:
						continue
					dt = time.time() - self._file_logs[filename].last_used # pylint: disable=invalid-name
					if dt > self._file_logs[filename].active_lifetime:
						with self._file_log_lock:
							logger.info("Closing inactive file log '%s', file logs remaining active: %d", filename, len(self._file_logs) - 1)
							await self._file_logs[filename].close()
							del self._file_logs[filename]
			except Exception as err: # pylint: disable=broad-except
				logger.error(err, exc_info=True)
			for _i in range(60):
				await asyncio.sleep(1)

	async def _start(self):
		try:
			self._redis = await get_aredis_connection(config.redis_internal_url)
			stream_name = "opsiconfd:log"
			await self._redis.xtrim(name=stream_name, max_len=10000, approximate=True)
			await asyncio.gather(self._reader(stream_name=stream_name), self._watch_log_files())
		except Exception as err: # pylint: disable=broad-except
			handle_log_exception(err)

	@retry_redis_call
	async def _reader(self, stream_name):
		if self._running_event:
			self._running_event.set()

		b_stream_name = stream_name.encode("utf-8")
		last_id = '$'
		while True:
			try:
				# It is also possible to specify multiple streams
				data = await self._redis.xread(block=1000, **{stream_name: last_id})
				if not data:
					continue
				for entry in data[b_stream_name]:
					last_id = entry[0]
					client = entry[1].get(b"client_address", b"").decode("utf-8")
					record_dict = msgpack.unpackb(entry[1][b"record"])
					record_dict.update({
						"scope": None,
						"exc_info": None,
						"args": None
					})
					record = pylogging.makeLogRecord(record_dict)
					# workaround for problem in aiologger.formatters.base.Formatter.format
					record.get_message = record.getMessage
					if self._stderr_handler and record.levelno >= self._log_level_stderr:
						await self._stderr_handler.emit(record)

					if record.levelno >= self._log_level_file:
						file_handler = self.get_file_handler(client)
						if file_handler:
							await file_handler.emit(record)

					del record
					del record_dict
				del data

			except (KeyboardInterrupt, SystemExit): # pylint: disable=try-except-raise
				raise
			except EOFError:
				break
			except Exception as exc: # pylint: disable=broad-except
				handle_log_exception(exc, log=False)

class RedisLogHandler(threading.Thread, pylogging.Handler):
	"""
	Will collect log messages in pipeline and send collected
	log messages at once to redis in regular intervals.
	"""
	def __init__(self, max_msg_len: int = 0, max_delay: float = 0.1):
		pylogging.Handler.__init__(self)
		threading.Thread.__init__(self)
		self.name = "RedisLogHandlerThread"
		self._max_msg_len = max_msg_len
		self._max_delay = max_delay
		self._redis = get_redis_connection(config.redis_internal_url)
		self._queue = Queue()
		self._should_stop = False
		self.start()

	def run(self):
		while not self._should_stop:
			time.sleep(self._max_delay)
			if self._queue.qsize() > 0:
				pipeline = self._redis.pipeline()
				while True:
					try:
						pipeline.xadd("opsiconfd:log", self._queue.get_nowait())
					except Empty:
						break
				pipeline.execute()

	def stop(self):
		self._should_stop = True

	def log_record_to_dict(self, record):
		msg = record.getMessage()
		for secret in secret_filter.secrets:
			msg = msg.replace(secret, SECRET_REPLACEMENT_STRING)
		if self._max_msg_len and len(msg) > self._max_msg_len:
			msg = msg[:self._max_msg_len - 1] + '…'

		if hasattr(record, 'exc_info') and record.exc_info:
			# by calling format the formatted exception information is cached in attribute exc_text
			self.format(record)
			record.exc_info = None

		d = record.__dict__.copy() # pylint: disable=invalid-name
		d["msg"] = msg
		for attr in ('scope', 'exc_info', 'args', 'contextstring'):
			if attr in d:
				del d[attr]
		return d

	def emit(self, record):
		try:
			str_record = msgpack.packb(self.log_record_to_dict(record))
			entry = record.context or {}
			entry["record"] = str_record
			self._queue.put(entry)
		except (KeyboardInterrupt, SystemExit): # pylint: disable=try-except-raise
			raise
		except Exception as exc: # pylint: disable=broad-except
			handle_log_exception(exc, record, log=False)


class GunicornLoggerSetup(glogging.Logger):
	def setup(self, cfg):
		self.error_log.handlers = logger.handlers
		self.access_log.handlers = []
		self.access_log.setLevel(0)

def enable_slow_callback_logging(slow_callback_duration = None):
	_run_orig = asyncio.events.Handle._run # pylint: disable=protected-access
	if slow_callback_duration is None:
		slow_callback_duration = asyncio.get_event_loop().slow_callback_duration

	def _run(self):
		start = time.perf_counter()
		retval = _run_orig(self)
		dt = time.perf_counter() - start # pylint: disable=invalid-name
		if dt >= slow_callback_duration:
			logger.warning("Slow asyncio callback: %s took %.3f seconds", asyncio.base_events._format_handle(self), dt) # pylint: disable=protected-access
		return retval

	asyncio.events.Handle._run = _run  # pylint: disable=protected-access


redis_log_handler = None  # pylint: disable=invalid-name

def init_logging(log_mode: str = "redis", is_worker: bool = False): # pylint: disable=too-many-branches
	redis_error = None
	try:
		if log_mode not in ("redis", "local"):
			raise ValueError(f"Invalid log mode '{log_mode}'")

		log_level = max(config.log_level, config.log_level_stderr, config.log_level_file)
		if log_mode == "local":
			log_level = config.log_level_stderr
		log_level = OPSI_LEVEL_TO_LEVEL[log_level]
		log_handler = None

		if log_mode == "redis":
			try:
				global redis_log_handler  # pylint: disable=global-statement,invalid-name
				if not redis_log_handler:
					redis_log_handler = RedisLogHandler(max_msg_len=int(config.log_max_msg_len))
				log_handler = redis_log_handler
			except Exception as err: # pylint: disable=broad-except
				redis_error = err
				log_mode = "local"

		if log_mode == "local":
			log_handler = StreamHandler(stream=sys.stderr)

		log_handler.setLevel(log_level)
		logger.handlers = [log_handler]
		logger.setLevel(log_level)
		set_format(stderr_format=config.log_format_stderr, file_format=config.log_format_file)

		if config.log_filter:
			set_filter_from_string(config.log_filter)

		for ln in ("asyncio", "uvicorn.error", "uvicorn.access"): # pylint: disable=invalid-name
			al = pylogging.getLogger(ln) # pylint: disable=invalid-name
			al.setLevel(log_level)
			al.handlers = [log_handler]
			al.propagate = False

		add_context_filter_to_loggers()

		if config.log_slow_async_callbacks > 0:
			enable_slow_callback_logging(config.log_slow_async_callbacks)

		if not is_worker:
			if log_mode == "redis" and (config.log_level_stderr != pylogging.NONE or config.log_level_file != pylogging.NONE): # pylint: disable=no-member
				start_redis_log_adapter_thread()
			else:
				stop_redis_log_adapter_thread()

		if redis_error:
			logger.critical("Failed to initalize redis logging: %s", redis_error, exc_info=True)

	except Exception as exc: # pylint: disable=broad-except
		handle_log_exception(exc)


class RedisLogAdapterThread(threading.Thread):
	def __init__(self, running_event=None):
		threading.Thread.__init__(self)
		self.name = "RedisLogAdapterThread"
		self._running_event = running_event
		self._redis_log_adapter = None

	def stop(self):
		if self._redis_log_adapter:
			self._loop.create_task(self._redis_log_adapter.stop())

	def run(self):
		try:
			self._loop = asyncio.new_event_loop() # pylint: disable=attribute-defined-outside-init
			self._loop.set_default_executor(
				ThreadPoolExecutor(
					max_workers=5,
					thread_name_prefix="RedisLogAdapterThread-ThreadPoolExecutor"
				)
			)
			self._loop.set_debug(config.debug)
			asyncio.set_event_loop(self._loop)
			def handle_asyncio_exception(loop, context):
				if loop.is_running():
					msg = context.get("exception", context["message"])
					print("Unhandled exception in RedisLogAdapterThread asyncio loop: %s" % msg, file=sys.stderr)
			self._loop.set_exception_handler(handle_asyncio_exception)
			self._redis_log_adapter = AsyncRedisLogAdapter(
				running_event=self._running_event,
				log_file_template=config.log_file,
				log_format_stderr=config.log_format_stderr,
				log_format_file=config.log_format_file,
				max_log_file_size=round(config.max_log_size * 1000 * 1000),
				keep_rotated_log_files=config.keep_rotated_logs,
				symlink_client_log_files=config.symlink_logs,
				log_level_stderr=pylogging.opsi_level_to_level[config.log_level_stderr], # pylint: disable=protected-access, no-member
				log_level_file=pylogging.opsi_level_to_level[config.log_level_file] # pylint: disable=protected-access, no-member
			)
			self._loop.run_forever()
		except Exception as exc: # pylint: disable=broad-except
			logger.error(exc, exc_info=True)

redis_log_adapter_thread = None # pylint: disable=invalid-name
def start_redis_log_adapter_thread():
	global redis_log_adapter_thread # pylint: disable=global-statement, invalid-name
	if redis_log_adapter_thread:
		return
	running_event = threading.Event()
	redis_log_adapter_thread = RedisLogAdapterThread(running_event)
	redis_log_adapter_thread.daemon = True
	redis_log_adapter_thread.start()
	running_event.wait()

def stop_redis_log_adapter_thread():
	global redis_log_adapter_thread # pylint: disable=global-statement, invalid-name
	if not redis_log_adapter_thread:
		return
	redis_log_adapter_thread.stop()
