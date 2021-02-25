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
:author: Jan Schneider <j.schneider@uib.de>
:license: GNU Affero General Public License version 3
"""

import os
import re
import sys
import pwd
import asyncio
import threading
import signal
import pprint
import subprocess
import getpass
from concurrent.futures import ThreadPoolExecutor
import uvloop
import psutil

from OPSI import __version__ as python_opsi_version

from opsicommon.logging import (
	OPSI_LEVEL_TO_LEVEL, set_filter_from_string, print_logger_info, context_filter
)

from . import __version__
from .logging import logger, init_logging, AsyncRedisLogAdapter
from .config import config
from .setup import setup
from .patch import apply_patches
from .arbiter import main as arbiter_main


def run_with_jemlalloc():
	try:
		if sys.argv[0].endswith(".py"):
			return

		if "libjemalloc" in os.getenv("LD_PRELOAD", ""):
			return

		out = subprocess.check_output(["ldconfig", "-p"]).decode()
		match = re.search(r".*=>\s*(.*libjemalloc.*)\s*", out)
		if not match:
			raise RuntimeError("libjemalloc not found")

		new_env = os.environ.copy()
		new_env["LD_PRELOAD"] = match.group(1)
		#print(f"Restarting with LD_PRELOAD={new_env['LD_PRELOAD']}")

		os.execve(sys.argv[0], sys.argv, new_env)
	except Exception as err:  # pylint: disable=broad-except
		print(err, file=sys.stderr)

def main():  # pylint: disable=too-many-statements, too-many-branches too-many-locals
	if config.version:
		print(f"{__version__} [python-opsi={python_opsi_version}]")
		return

	if config.action == "setup":
		init_logging(log_mode="local")
		setup(full=True)
		return

	if config.action == "log-viewer":
		async def log_viewer():
			AsyncRedisLogAdapter(
				log_format_stderr=config.log_format_stderr,
				log_level_stderr=OPSI_LEVEL_TO_LEVEL[config.log_level_stderr],
				log_level_file=0
			)
		try:
			set_filter_from_string(config.log_filter)
			AsyncRedisLogAdapter(
				log_format_stderr=config.log_format_stderr,
				log_level_stderr=OPSI_LEVEL_TO_LEVEL[config.log_level_stderr],
				log_level_file=0
			)
			loop = asyncio.get_event_loop()
			#loop.create_task(log_viewer())
			loop.run_forever()
		except KeyboardInterrupt:
			pass
		return

	if config.action in ("reload", "stop"):
		send_signal = signal.SIGINT if config.action == "stop" else signal.SIGHUP
		our_pid = os.getpid()
		our_proc = psutil.Process(our_pid)
		ignore_pids = [our_pid]
		ignore_pids += [p.pid for p in our_proc.children(recursive=True)]
		ignore_pids += [p.pid for p in our_proc.parents()]
		pids = []
		for proc in psutil.process_iter():
			if proc.pid in ignore_pids:
				continue
			if proc.name() == "opsiconfd":
				pids.append(proc.pid)
				pids.extend([p.pid for p in proc.children(recursive=True)])
			elif proc.name() in ("python", "python3"):
				for arg in proc.cmdline():
					if arg.find("opsiconfd.__main__") != -1:
						pids.append(proc.pid)
						pids.extend([p.pid for p in proc.children(recursive=True)])
						break
		for pid in sorted(set(pids), reverse=True):
			os.kill(pid, send_signal)
		return

	if config.use_jemalloc:
		run_with_jemlalloc()

	apply_patches()

	try: # pylint: disable=too-many-nested-blocks
		asyncio.get_event_loop().set_default_executor(
			ThreadPoolExecutor(
				max_workers=5,
				thread_name_prefix="main-ThreadPoolExecutor"
			)
		)

		init_logging(log_mode=config.log_mode)

		if "libjemalloc" in os.getenv("LD_PRELOAD", ""):
			logger.notice("Running with %s", os.getenv("LD_PRELOAD"))

		setup(full=bool(config.setup))

		if config.run_as_user and getpass.getuser() != config.run_as_user:
			logger.essential("Switching to user %s", config.run_as_user)
			try:
				user = pwd.getpwnam(config.run_as_user)
				gids = os.getgrouplist(user.pw_name, user.pw_gid)
				logger.debug("Set uid=%s, gid=%s, groups=%s", user.pw_uid, user.pw_gid, gids)
				os.setgid(user.pw_gid)
				os.setgroups(gids)
				os.setuid(user.pw_uid)
				os.environ["HOME"] = user.pw_dir
			except Exception as err:
				raise Exception(f"Failed to run as user '{config.run_as_user}': {err}") from err

		# Do not use uvloop in redis logger thread because aiologger is currently incompatible with uvloop!
		# https://github.com/b2wdigital/aiologger/issues/38
		uvloop.install()

		logger.essential("opsiconfd is starting")
		logger.info("opsiconfd config:\n%s", pprint.pformat(config.items(), width=100, indent=4))

		arbiter_main()

	finally:
		for t in threading.enumerate(): # pylint: disable=invalid-name
			if hasattr(t, "stop"):
				t.stop()
				t.join()
