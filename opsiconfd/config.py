# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
global config
"""
# pylint: disable=too-many-lines

import getpass
import ipaddress
import os
import re
import socket
import sys
import warnings
from argparse import (
	OPTIONAL,
	SUPPRESS,
	ZERO_OR_MORE,
	Action,
	ArgumentParser,
	ArgumentTypeError,
	HelpFormatter,
	_ArgumentGroup,
)
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import certifi
import configargparse  # type: ignore[import]
import psutil
from dns import resolver, reversename
from dns.exception import DNSException
from fastapi.templating import Jinja2Templates
from opsicommon.config import OpsiConfig  # type: ignore[import]
from opsicommon.logging import secret_filter  # type: ignore[import]

from .utils import Singleton, is_manager, is_opsiconfd, running_in_docker

DEFAULT_CONFIG_FILE = "/etc/opsi/opsiconfd.conf"
CONFIG_FILE_HEADER = """
# This file was automatically migrated from an older opsiconfd version
# For available options see: opsiconfd --help
# config examples:
# log-level-file = 5
# networks = [192.168.0.0/16, 10.0.0.0/8, ::/0]
# update-ip = true
"""
DEPRECATED = ("monitoring-debug", "verify-ip", "dispatch-config-file", "jsonrpc-time-to-cache")

CA_KEY_DEFAULT_PASSPHRASE = "Toohoerohpiep8yo"
SERVER_KEY_DEFAULT_PASSPHRASE = "ye3heiwaiLu9pama"

FQDN = socket.getfqdn().lower()
DEFAULT_NODE_NAME = socket.gethostname()
VAR_ADDON_DIR = "/var/lib/opsiconfd/addons"
RPC_DEBUG_DIR = "/tmp/opsiconfd-rpc-debug"
GC_THRESHOLDS = (150_000, 50, 100)
OPSI_PASSWD_FILE = "/etc/opsi/passwd"
LOG_DIR = "/var/log/opsi"
LOG_SIZE_HARD_LIMIT = 10000000
OPSI_LICENSE_PATH = "/etc/opsi/licenses"
OPSI_MODULES_PATH = "/etc/opsi/modules"
SSH_COMMANDS_DEFAULT_FILE = "/etc/opsi/server_commands_default.conf"
SSH_COMMANDS_CUSTOM_FILE = "/var/lib/opsi/server_commands_custom.conf"

opsi_config = OpsiConfig()


def configure_warnings() -> None:
	warnings.filterwarnings(
		"ignore", category=DeprecationWarning, module="redis.asyncio.connection", message="There is no current event loop"
	)


if running_in_docker():
	try:
		ip = socket.gethostbyname(socket.getfqdn())  # pylint: disable=invalid-name
		rev = reversename.from_address(ip)
		DEFAULT_NODE_NAME = str(resolver.resolve(str(rev), "PTR")[0]).split(".", 1)[0].replace("docker_", "")
	except DNSException:
		pass


def network_address(value: str) -> str:
	try:
		return ipaddress.ip_network(value).compressed
	except ValueError as err:
		raise ArgumentTypeError(f"Invalid network address '{value}: {err}") from err


def ip_address(value: str) -> str:
	try:
		return ipaddress.ip_address(value).compressed
	except ValueError as err:
		raise ArgumentTypeError(f"Invalid ip address: {value}: {err}") from err


def str2bool(value: str) -> bool:
	if isinstance(value, bool):
		return value
	return str(value).lower() in ("yes", "true", "y", "1")


configargparse.ArgumentParser.format_help = ArgumentParser.format_help


class OpsiconfdHelpFormatter(HelpFormatter):
	CN = ""
	CB = ""
	CC = ""
	CW = ""
	if sys.stdout.isatty():
		CN = "\033[0;0;0m"
		CB = "\033[1;34;49m"
		CC = "\033[1;36;49m"
		CW = "\033[1;39;49m"
		CY = "\033[0;33;49m"

	def __init__(self, sub_command: str | None = None) -> None:
		super().__init__("opsiconfd", max_help_position=10, width=100)
		self._sub_command = sub_command

	def _split_lines(self, text: str, width: int) -> list[str]:
		# The textwrap module is used only for formatting help.
		# Delay its import for speeding up the common usage of argparse.
		text = text.replace("[env var: ", "\n[env var: ")
		text = text.replace("(default: ", "\n(default: ")
		lines = []  # pylint: disable=use-tuple-over-list
		from textwrap import wrap  # pylint: disable=import-outside-toplevel

		for line in text.split("\n"):
			lines += wrap(line, width)
		return lines

	def format_help(self) -> str:
		text = HelpFormatter.format_help(self)
		if not self._sub_command:
			text += (
				"\n"
				"Arguments can also be set in the configuration file.\n"
				"Config file syntax allows: option=value, flag=true, list-option=[a,b,c].\n"
				"For details, see syntax at https://goo.gl/R74nmi.\n"
				"\n"
			)
		return text

	def _format_usage(self, usage: str | None, actions: Iterable[Action], groups: Iterable[_ArgumentGroup], prefix: str | None) -> str:
		text = super()._format_usage(usage, actions, groups, prefix)
		sub = f" {self._sub_command}" if self._sub_command else ""
		text = re.sub(r"usage:\s+(\S+)\s+", rf"Usage: {self.CW}\g<1>{sub}{self.CN} ", text)
		return text

	def _format_actions_usage(self, actions: Iterable[Action], groups: Iterable) -> str:
		text = HelpFormatter._format_actions_usage(self, actions, groups)
		text = re.sub(r"(--?\S+)", rf"{self.CW}\g<1>{self.CN}", text)
		text = re.sub(r"([A-Z_]{2,})", rf"{self.CC}\g<1>{self.CN}", text)
		return text

	def _format_action_invocation(self, action: Action) -> str:
		text = HelpFormatter._format_action_invocation(self, action)
		text = re.sub(r"(--?\S+)", rf"{self.CW}\g<1>{self.CN}", text)
		text = re.sub(r"([A-Z_]{2,})", rf"{self.CC}\g<1>{self.CN}", text)
		return text

	def _format_args(self, action: Action, default_metavar: str) -> str:
		text = HelpFormatter._format_args(self, action, default_metavar)
		return f"{self.CC}{text}{self.CN}"

	def _get_help_string(self, action: Action) -> str:
		text = action.help or ""
		if "passphrase" not in action.dest and "%(default)" not in (action.help or ""):
			if action.default is not SUPPRESS:
				defaulting_nargs = (OPTIONAL, ZERO_OR_MORE)
				if action.dest == "config_file":
					text += f" (default: {DEFAULT_CONFIG_FILE})"
				elif action.option_strings or action.nargs in defaulting_nargs:
					text += " (default: %(default)s)"
		return text


class Config(metaclass=Singleton):  # pylint: disable=too-many-instance-attributes
	_initialized = False

	def __init__(self) -> None:
		if self._initialized:
			return
		self._initialized = True
		self._pytest = sys.argv[0].endswith("/pytest") or "pytest" in sys.argv
		self._args: list[str] = []
		self._ex_help = False
		self._parser: configargparse.ArgParser | None = None
		self._sub_command = None
		self._config = configargparse.Namespace()
		self._config.config_file = DEFAULT_CONFIG_FILE
		self.jinja_templates = Jinja2Templates(directory="")

		self._set_args()

	def __getattr__(self, name: str) -> Any:
		if not name.startswith("_") and self._config:
			return getattr(self._config, name)
		raise AttributeError()

	def __setattr__(self, name: str, value: Any) -> None:
		if not name.startswith("_") and hasattr(self._config, name):
			return setattr(self._config, name, value)
		return super().__setattr__(name, value)

	def _set_args(self, args: list[str] | None = None) -> None:
		self._args = sys.argv[1:] if args is None else args

		try:
			# Pre-parse command line / env to get sub_command and ex-help (may fail)
			self._init_parser()
			conf, _unknown = self._parser.parse_known_args(self._args, ignore_help_args=True, config_file_contents="")  # type: ignore[union-attr]
			self._config.config_file = conf.config_file
			self._ex_help = conf.ex_help
			if self._ex_help and "--help" not in self._args:
				self._args.append("--help")
			self._sub_command = conf.action if conf.action in ("health-check", "log-viewer", "setup", "backup", "restore") else None
			if self._sub_command:
				self._args.remove(self._sub_command)
		except BaseException:  # pylint: disable=broad-except
			pass

		self._init_parser()

		if is_manager(psutil.Process(os.getpid())):
			self._upgrade_config_file()
			self._update_config_file()

		self._parse_args()

	def _help(self, help_type: str | tuple[str, ...], help_text: str) -> str:
		help_type = help_type if isinstance(help_type, tuple) else (help_type,)
		if "expert" in help_type:
			return help_text if self._ex_help and self._sub_command is None else SUPPRESS

		if "all" in help_type or not self._sub_command:
			return help_text

		return help_text if self._sub_command in help_type else SUPPRESS

	def _parse_args(self) -> None:
		if not self._parser:
			raise RuntimeError("Parser not initialized")
		if is_opsiconfd(psutil.Process(os.getpid())):
			self._parser.exit_on_error = True
			self._config = self._parser.parse_args(self._args, config_file_contents=self._config_file_contents())
		else:
			self._parser.exit_on_error = False
			self._config, _unknown = self._parser.parse_known_args(self._args, config_file_contents=self._config_file_contents())
		self._update_config()

	def _update_config(self) -> None:  # pylint: disable=too-many-branches
		if self._sub_command:
			self._config.action = self._sub_command
		self.jinja_templates = Jinja2Templates(directory=os.path.join(self.static_dir, "templates"))

		if not self._config.ssl_ca_key_passphrase:
			# Use None if empty string
			self._config.ssl_ca_key_passphrase = None
		if not self._config.ssl_server_key_passphrase:
			# Use None if empty string
			self._config.ssl_server_key_passphrase = None

		secret_filter.add_secrets(self._config.ssl_ca_key_passphrase, self._config.ssl_server_key_passphrase)

		scheme = "http"
		if self._config.ssl_server_key and self._config.ssl_server_cert:
			scheme = "https"

		os.putenv("SSL_CERT_FILE", self._config.ssl_trusted_certs)

		if not self._config.internal_url:
			self._config.internal_url = f"{scheme}://{FQDN}:{self._config.port}"
		if not self._config.external_url:
			self._config.external_url = f"{scheme}://{FQDN}:{self._config.port}"
		if not self._config.grafana_data_source_url:
			self._config.grafana_data_source_url = f"{scheme}://{FQDN}:{self._config.port}"
		if self._config.grafana_internal_url:
			url = urlparse(self._config.grafana_internal_url)
			if url.password:
				secret_filter.add_secrets(url.password)
		if not self._config.skip_setup:
			self._config.skip_setup = []
		if self._parser and "all" in self._config.skip_setup:
			for action in self._parser._actions:  # pylint: disable=protected-access
				if action.dest == "skip_setup":
					self._config.skip_setup = action.choices
					break
		elif "ssl" in self._config.skip_setup:
			if "opsi_ca" not in self._config.skip_setup:
				self._config.skip_setup.append("opsi_ca")
			if "server_cert" not in self._config.skip_setup:
				self._config.skip_setup.append("server_cert")
		if not self._config.admin_interface_disabled_features:
			self._config.admin_interface_disabled_features = []

	def redis_key(self, prefix_type: str | None = None) -> str:
		if not prefix_type:
			return self._config.redis_prefix
		return f"{self._config.redis_prefix}:{prefix_type}"

	def reload(self) -> None:
		self._parse_args()

	def items(self) -> dict[str, Any]:
		return self._config.__dict__

	def set_config_file(self, config_file: str) -> None:
		self._config.config_file = config_file
		for idx, arg in enumerate(self._args):
			if arg in ("-c", "--config-file"):
				if len(self._args) > idx + 1:
					self._args[idx + 1] = self._config.config_file
					return
			elif arg.startswith("--config-file="):
				self._args[idx] = f"--config-file={self._config.config_file}"  # pylint: disable=loop-invariant-statement
				return
		self._args = ["--config-file", self._config.config_file] + self._args

	def _parse_config_file(self) -> dict[str, Any]:
		conf: dict[str, Any] = {}
		path = Path(self._config.config_file)
		if not path.exists():
			return conf
		data = path.read_text(encoding="utf-8")
		re_opt = re.compile(r"^\s*([^#;\s][^=]+)\s*=\s*(\S.*)\s*$")
		for line in data.split("\n"):
			match = re_opt.match(line)
			if match:
				conf[match.group(1).strip().lower()] = match.group(2).strip()
		return conf

	def _generate_config_file(self, conf: dict[str, Any]) -> None:
		conf = conf.copy()
		path = Path(self._config.config_file)
		data = path.read_text(encoding="utf-8")
		re_opt = re.compile(r"^\s*([^#;\s][^=]+)\s*=\s*(\S.*)\s*$")
		new_lines = []
		for line in data.split("\n"):
			match = re_opt.match(line)
			if match:
				arg = match.group(1).strip().lower()
				if arg in conf:
					# Update argumnet value in file
					line = f"{arg} = {conf.pop(arg)}"
				else:
					# Remove argument from file
					continue
			new_lines.append(line)

		if conf:
			# Add new arguments
			new_lines[-1:-1] = [f"{arg} = {val}" for arg, val in conf.items()]

		path.write_text("\n".join(new_lines), encoding="utf-8")

	def _config_file_contents(self) -> str:
		conf = self._parse_config_file()
		masked_config_file_arguments: tuple[str, ...] = tuple()
		if self._sub_command:
			masked_config_file_arguments = ("log-level-stderr", "log-level-file", "log-level")
		return "\n".join([f"{arg} = {val}" for arg, val in conf.items() if arg not in masked_config_file_arguments])

	def set_config_in_config_file(self, arg: str, value: Any) -> None:
		conf = self._parse_config_file()
		conf[arg] = value
		self._generate_config_file(conf)

	def _upgrade_config_file(self) -> None:
		if not self._parser:
			raise RuntimeError("Parser not initialized")
		defaults = {action.dest: action.default for action in self._parser._actions}  # pylint: disable=protected-access
		# Do not migrate ssl key/cert
		mapping = {
			"backend config dir": "backend-config-dir",
			"dispatch config file": "dispatch-config-file",
			"extension config dir": "extension-config-dir",
			"acl file": "acl-file",
			"admin networks": "admin-networks",
			"log file": "log-file",
			"symlink logs": "symlink-logs",
			"log level": "log-level",
			"monitoring user": "monitoring-user",
			"interface": "interface",
			"https port": "port",
			"update ip": "update-ip",
			"max inactive interval": "session-lifetime",
			"max authentication failures": "max-auth-failures",
			"max sessions per ip": "max-session-per-ip",
		}

		path = Path(self._config.config_file)
		data = path.read_text(encoding="utf-8")
		if "[global]" not in data:
			# Config file not in opsi 4.1 format
			return

		re_opt = re.compile(r"^\s*([^#;\s][^=]+)\s*=\s*(\S.*)\s*$")

		with open(str(path), "w", encoding="utf-8") as file:
			file.write(CONFIG_FILE_HEADER.lstrip())  # pylint: disable=loop-global-usage
			for line in data.split("\n"):
				match = re_opt.match(line)
				if match:
					opt = match.group(1).strip().lower()
					val = match.group(2).strip()
					if opt not in mapping:
						continue
					if val.lower() in ("yes", "no", "true", "false"):
						val = val.lower() in ("yes", "true")
					default = defaults.get(mapping[opt].replace("-", "_"))
					if str(default) == str(val):
						continue
					if isinstance(val, bool):
						val = str(val).lower()
					if "," in val:
						val = f"[{val}]"
					file.write(f"{mapping[opt]} = {val}\n")
			file.write("\n")

	def _update_config_file(self) -> None:
		conf = self._parse_config_file()
		for deprecated in DEPRECATED:  # pylint: disable=loop-global-usage
			conf.pop(deprecated, None)
		self._generate_config_file(conf)

	def _init_parser(self) -> None:  # pylint: disable=too-many-statements
		self._parser = configargparse.ArgParser(formatter_class=lambda prog: OpsiconfdHelpFormatter(self._sub_command))

		self._parser.add(
			"-c",
			"--config-file",
			env_var="OPSICONFD_CONFIG_FILE",
			required=False,
			is_config_file=True,
			default=DEFAULT_CONFIG_FILE,
			help=self._help("opsiconfd", "Path to config file."),
		)
		self._parser.add("--version", action="store_true", help=self._help("opsiconfd", "Show version info and exit."))
		self._parser.add("--setup", action="store_true", help=self._help("opsiconfd", "Run full setup tasks on start."))
		self._parser.add(
			"--run-as-user",
			env_var="OPSICONFD_RUN_AS_USER",
			default=getpass.getuser(),
			metavar="USER",
			help=self._help("opsiconfd", "Run service as USER."),
		)
		self._parser.add(
			"--workers",
			env_var="OPSICONFD_WORKERS",
			type=int,
			default=1,
			help=self._help("opsiconfd", "Number of workers to fork."),
		)
		self._parser.add(
			"--worker-stop-timeout",
			env_var="OPSICONFD_WORKER_STOP_TIMEOUT",
			type=int,
			default=120,
			help=self._help(
				"opsiconfd",
				"A worker terminates only when all open client connections have been closed."
				"How log, in seconds, to wait for a worker to stop."
				"After the timeout expires the worker will be forced to stop.",
			),
		)
		self._parser.add(
			"--backend-config-dir",
			env_var="OPSICONFD_BACKEND_CONFIG_DIR",
			default="/etc/opsi/backends",
			help=self._help("opsiconfd", "Location of the backend config dir."),
		)
		self._parser.add(
			"--dispatch-config-file",
			env_var="OPSICONFD_DISPATCH_CONFIG_FILE",
			default="/etc/opsi/backendManager/dispatch.conf",
			help=self._help("opsiconfd", "Location of the backend dispatcher config file."),
		)
		self._parser.add(
			"--extension-config-dir",
			env_var="OPSICONFD_EXTENSION_CONFIG_DIR",
			default="/etc/opsi/backendManager/extend.d",
			help=self._help("opsiconfd", "Location of the backend extension config dir."),
		)
		self._parser.add(
			"--acl-file",
			env_var="OPSICONFD_ACL_FILE",
			default="/etc/opsi/backendManager/acl.conf",
			help=self._help("opsiconfd", "Location of the acl file."),
		)
		self._parser.add(
			"--static-dir",
			env_var="OPSICONFD_STATIC_DIR",
			default="/usr/share/opsiconfd/static",
			help=self._help("opsiconfd", "Location of the static files."),
		)
		self._parser.add(
			"--networks",
			nargs="+",
			env_var="OPSICONFD_NETWORKS",
			default=["0.0.0.0/0", "::/0"],
			type=network_address,
			help=self._help("opsiconfd", "A list of network addresses from which connections are allowed."),
		)
		self._parser.add(
			"--admin-networks",
			nargs="+",
			env_var="OPSICONFD_ADMIN_NETWORKS",
			default=["0.0.0.0/0", "::/0"],
			type=network_address,
			help=self._help("opsiconfd", "A list of network addresses from which administrative connections are allowed."),
		)
		self._parser.add(
			"--trusted-proxies",
			nargs="+",
			env_var="OPSICONFD_TRUSTED_PROXIES",
			default=["127.0.0.1", "::1"],
			type=ip_address,
			help=self._help("opsiconfd", "A list of trusted reverse proxy addresses."),
		)
		self._parser.add(
			"--log-mode",
			env_var="OPSICONFD_LOG_MODE",
			default="redis",
			choices=("redis", "local"),
			help=self._help("opsiconfd", "Set the logging mode. 'redis': use centralized redis logging, 'local': local logging."),
		)
		self._parser.add(
			"--log-level",
			env_var="OPSICONFD_LOG_LEVEL",
			type=int,
			default=0 if self._sub_command else 5,
			choices=range(0, 10),
			help=self._help(
				"opsiconfd",
				"Set the general log level. "
				"0: nothing, 1: essential, 2: critical, 3: errors, 4: warnings, 5: notices, "
				"6: infos, 7: debug messages, 8: trace messages, 9: secrets",
			),
		)
		self._parser.add(
			"--log-levels",
			env_var="OPSICONFD_LOG_LEVELS",
			type=str,
			default="",
			help=self._help(
				"expert",
				"Set the log levels of individual loggers. "
				"<logger-regex>:<level>[,<logger-regex-2>:<level-2>]"
				r'Example: --log-levels=".*:4,opsiconfd\.headers:8"',
			),
		)
		self._parser.add(
			"--log-file",
			env_var="OPSICONFD_LOG_FILE",
			default="/var/log/opsi/opsiconfd/%m.log",
			help=self._help(
				"opsiconfd",
				"The macro %%m can be used to create use a separate log file for each client. %%m will be replaced by <client-ip>",
			),
		)
		self._parser.add(
			"--symlink-logs",
			env_var="OPSICONFD_SYMLINK_LOGS",
			type=str2bool,
			nargs="?",
			const=True,
			default=True,
			help=self._help(
				"opsiconfd",
				"If separate log files are used and this option is enabled "
				"opsiconfd will create a symlink in the log dir which points "
				"to the clients log file. The name of the symlink will be the same "
				"as the log files but %%m will be replaced by <client-fqdn>.",
			),
		)
		self._parser.add(
			"--max-log-size",
			env_var="OPSICONFD_MAX_LOG_SIZE",
			type=float,
			default=5.0,
			help=self._help(
				"opsiconfd",
				"Limit the size of logfiles to SIZE megabytes. "
				"Setting this to 0 will disable any limiting. "
				"If you set this to 0 we recommend using a proper logrotate configuration "
				"so that your disk does not get filled by the logs.",
			),
		)
		self._parser.add(
			"--keep-rotated-logs",
			env_var="OPSICONFD_KEEP_ROTATED_LOGS",
			type=int,
			default=1,
			help=self._help("opsiconfd", "Number of rotated log files to keep."),
		)
		self._parser.add(
			"--log-level-file",
			env_var="OPSICONFD_LOG_LEVEL_FILE",
			type=int,
			default=0 if self._sub_command else 4,
			choices=range(0, 10),
			help=self._help(
				"opsiconfd",
				"Set the log level for logfiles. "
				"0: nothing, 1: essential, 2: critical, 3: errors, 4: warnings, 5: notices, "
				"6: infos, 7: debug messages, 8: trace messages, 9: secrets",
			),
		)
		self._parser.add(
			"--log-format-file",
			env_var="OPSICONFD_LOG_FORMAT_FILE",
			default="[%(opsilevel)d] [%(asctime)s.%(msecs)03d] [%(contextstring)-15s] %(message)s   (%(filename)s:%(lineno)d)",
			help=self._help("opsiconfd", "Set the log format for logfiles."),
		)
		self._parser.add(
			"-l",
			"--log-level-stderr",
			env_var="OPSICONFD_LOG_LEVEL_STDERR",
			type=int,
			default=0 if self._sub_command else 4,
			choices=range(0, 10),
			help=self._help(
				"all",
				"Set the log level for stderr. "
				"0: nothing, 1: essential, 2: critical, 3: errors, 4: warnings, 5: notices "
				"6: infos, 7: debug messages, 8: trace messages, 9: secrets",
			),
		)
		self._parser.add(
			"--log-format-stderr",
			env_var="OPSICONFD_LOG_FORMAT_STDERR",
			default="%(log_color)s[%(opsilevel)d] [%(asctime)s.%(msecs)03d]%(reset)s [%(contextstring)-15s] %(message)s   (%(filename)s:%(lineno)d)",
			help=self._help("opsiconfd", "Set the log format for stder."),
		)
		self._parser.add(
			"--log-max-msg-len",
			env_var="OPSICONFD_LOG_MAX_MSG_LEN",
			type=int,
			default=5000,
			help=self._help("expert", "Set maximum log message length."),
		)
		self._parser.add(
			"--log-filter",
			env_var="OPSICONFD_LOG_FILTER",
			help=self._help(
				"opsiconfd",
				"Filter log records contexts (<ctx-name-1>=<val1>[,val2][;ctx-name-2=val3]).\n"
				'Example: --log-filter="client_address=192.168.20.101"',
			),
		)
		self._parser.add(
			"--monitoring-user",
			env_var="OPSICONFD_MONITORING_USER",
			default="monitoring",
			help=self._help("opsiconfd", "The User for opsi-Nagios-Connetor."),
		)
		self._parser.add("--internal-url", env_var="OPSICONFD_INTERNAL_URL", help=self._help("opsiconfd", "The internal base url."))
		self._parser.add("--external-url", env_var="OPSICONFD_EXTERNAL_URL", help=self._help("opsiconfd", "The external base url."))
		self._parser.add(
			"--interface",
			type=ip_address,
			env_var="OPSICONFD_INTERFACE",
			default="0.0.0.0",
			help=self._help(
				"opsiconfd",
				"The network interface to bind to (ip address of an network interface). "
				"Use 0.0.0.0 to listen on all ipv4 interfaces. "
				"Use :: to listen on all ipv6 (and ipv4) interfaces.",
			),
		)
		self._parser.add(
			"--port",
			env_var="OPSICONFD_PORT",
			type=int,
			default=4447,
			help=self._help("opsiconfd", "The port where opsiconfd will listen for https requests."),
		)
		self._parser.add(
			"--ssl-trusted-certs",
			env_var="OPSICONFD_SSL_TRUSTED_CERTS",
			default=certifi.where(),
			help=self._help("opsiconfd", "Path to the database of trusted certificates"),
		)
		# Cipher Strings from https://www.openssl.org/docs/man1.1.1/man1/ciphers.html
		# iPXE 1.20.1 supports these TLS v1.2 cipher suites:
		# AES128-SHA256 (TLS_RSA_WITH_AES_128_CBC_SHA256, 0x003c)
		# AES256-SHA256 (TLS_RSA_WITH_AES_256_CBC_SHA256, 0x003d)
		self._parser.add(
			"--ssl-ciphers",
			env_var="OPSICONFD_SSL_CIPHERS",
			default="",
			help=self._help(
				"opsiconfd",
				"TLS cipher suites to enable (OpenSSL cipher list format https://www.openssl.org/docs/man1.1.1/man1/ciphers.html).",
			),
		)
		self._parser.add(
			"--ssl-ca-subject-cn",
			env_var="OPSICONFD_SSL_CA_SUBJECT_CN",
			default="opsi CA",
			help=self._help("opsiconfd", "The common name to use in the opsi CA subject."),
		)
		self._parser.add(
			"--ssl-ca-key",
			env_var="OPSICONFD_SSL_CA_KEY",
			default="/etc/opsi/ssl/opsi-ca-key.pem",
			help=self._help("opsiconfd", "The location of the opsi ssl ca key."),
		)
		self._parser.add(
			"--ssl-ca-key-passphrase",
			env_var="OPSICONFD_SSL_CA_KEY_PASSPHRASE",
			default=CA_KEY_DEFAULT_PASSPHRASE,
			help=self._help("opsiconfd", "Passphrase to use to encrypt CA key."),
		)
		self._parser.add(
			"--ssl-ca-cert",
			env_var="OPSICONFD_SSL_CA_CERT",
			default="/etc/opsi/ssl/opsi-ca-cert.pem",
			help=self._help("opsiconfd", "The location of the opsi ssl ca certificate."),
		)
		self._parser.add(
			"--ssl-ca-cert-valid-days",
			env_var="OPSICONFD_SSL_CA_CERT_VALID_DAYS",
			type=int,
			default=360,
			help=self._help("expert", "The period of validity of the opsi ssl ca certificate in days."),
		)
		self._parser.add(
			"--ssl-ca-cert-renew-days",
			env_var="OPSICONFD_SSL_CA_CERT_RENEW_DAYS",
			type=int,
			default=300,
			help=self._help("expert", "The CA will be renewed if the validity falls below the specified number of days."),
		)
		self._parser.add(
			"--ssl-server-key",
			env_var="OPSICONFD_SSL_SERVER_KEY",
			default="/etc/opsi/ssl/opsiconfd-key.pem",
			help=self._help("opsiconfd", "The location of the ssl server key."),
		)
		self._parser.add(
			"--ssl-server-key-passphrase",
			env_var="OPSICONFD_SSL_SERVER_KEY_PASSPHRASE",
			default=SERVER_KEY_DEFAULT_PASSPHRASE,
			help=self._help("opsiconfd", "Passphrase to use to encrypt server key."),
		)
		self._parser.add(
			"--ssl-server-cert",
			env_var="OPSICONFD_SSL_SERVER_CERT",
			default="/etc/opsi/ssl/opsiconfd-cert.pem",
			help=self._help("opsiconfd", "The location of the ssl server certificate."),
		)
		self._parser.add(
			"--ssl-server-cert-valid-days",
			env_var="OPSICONFD_SSL_SERVER_CERT_VALID_DAYS",
			type=int,
			default=90,
			help=self._help("expert", "The period of validity of the server certificate in days."),
		)
		self._parser.add(
			"--ssl-server-cert-renew-days",
			env_var="OPSICONFD_SSL_SERVER_CERT_RENEW_DAYS",
			type=int,
			default=30,
			help=self._help(
				"expert",
				"The server certificate will be renewed if the validity falls below the specified number of days.",
			),
		)
		self._parser.add(
			"--ssl-client-cert-valid-days",
			env_var="OPSICONFD_SSL_CLIENT_CERT_VALID_DAYS",
			type=int,
			default=360,
			help=self._help("expert", "The period of validity of a client certificate in days."),
		)
		self._parser.add(
			"--ssl-server-cert-check-interval",
			env_var="OPSICONFD_SSL_SERVER_CERT_CHECK_INTERVAL",
			type=int,
			default=86400,
			help=self._help(
				"expert",
				"The interval in seconds at which the server certificate is checked for validity.",
			),
		)
		self._parser.add(
			"--update-ip",
			env_var="OPSICONFD_UPDATE_IP",
			type=str2bool,
			nargs="?",
			const=True,
			default=True,
			help=self._help(
				"opsiconfd",
				"If enabled, a client's ip address will be updated in the opsi database, "
				"when the client connects to the service and authentication is successful.",
			),
		)
		self._parser.add(
			"--session-lifetime",
			env_var="OPSICONFD_SESSION_LIFETIME",
			type=int,
			default=60,
			help=self._help("opsiconfd", "The interval in seconds after an inactive session expires."),
		)
		self._parser.add(
			"--max-auth-failures",
			env_var="OPSICONFD_MAX_AUTH_FAILURES",
			type=int,
			default=10,
			help=self._help("opsiconfd", "The maximum number of authentication failures before a client ip is blocked."),
		)
		self._parser.add(
			"--auth-failures-interval",
			env_var="OPSICONFD_AUTH_FAILURES_INTERVAL",
			type=int,
			default=120,
			help=self._help("opsiconfd", "The time window in seconds in which max auth failures are counted."),
		)
		self._parser.add(
			"--client-block-time",
			env_var="OPSICONFD_CLIENT_BLOCK_TIME",
			type=int,
			default=120,
			help=self._help("opsiconfd", "Time in seconds for which the client is blocked after max auth failures."),
		)
		self._parser.add(
			"--max-session-per-ip",
			env_var="OPSICONFD_MAX_SESSIONS_PER_IP",
			type=int,
			default=30,
			help=self._help("opsiconfd", "The maximum number of sessions that can be opened through one ip address."),
		)
		self._parser.add(
			"--max-sessions-excludes",
			nargs="+",
			env_var="OPSICONFD_MAX_SESSIONS_EXCLUDES",
			default=["127.0.0.1", "::1"],
			help=self._help("expert", "Allow unlimited sessions for these addresses."),
		)
		self._parser.add(
			"--skip-setup",
			nargs="+",
			env_var="OPSICONFD_SKIP_SETUP",
			default=None,
			help=self._help(
				("opsiconfd", "setup"),
				"A list of setup tasks to skip "
				"(tasks: all, limits, users, groups, grafana, backend, ssl, server_cert, opsi_ca, "
				"systemd, files, file_permissions, log_files, metric_downsampling).",
			),
			choices=[
				"all",
				"limits",
				"users",
				"groups",
				"grafana",
				"backend",
				"ssl",
				"server_cert",
				"opsi_ca",
				"systemd",
				"files",
				"file_permissions",
				"log_files",
				"metric_downsampling",
			],
		)
		self._parser.add(
			"--redis-internal-url",
			env_var="OPSICONFD_REDIS_INTERNAL_URL",
			default="redis://localhost",
			help=self._help(
				"opsiconfd",
				"Redis connection url. Examples:\n"
				"rediss://<username>:<password>@redis-server:6379/0\n"
				"unix:///var/run/redis/redis-server.sock",
			),
		)
		self._parser.add(
			"--redis-prefix",
			env_var="OPSICONFD_REDIS_PREFIX",
			default="opsiconfd",
			help=self._help("expert", "Prefix for redis keys"),
		)
		self._parser.add(
			"--grafana-internal-url",
			env_var="OPSICONFD_GRAFANA_INTERNAL_URL",
			default="http://localhost:3000",
			help=self._help("opsiconfd", "Grafana base url for internal use."),
		)
		self._parser.add(
			"--grafana-external-url",
			env_var="OPSICONFD_GRAFANA_EXTERNAL_URL",
			default="/grafana",
			help=self._help("opsiconfd", "External grafana base url."),
		)
		self._parser.add(
			"--grafana-verify-cert",
			env_var="OPSICONFD_GRAFANA_VERIFY_CERT",
			type=str2bool,
			nargs="?",
			const=True,
			default=True,
			help=self._help("opsiconfd", "If enabled, opsiconfd will check the tls certificate when connecting to grafana."),
		)
		self._parser.add(
			"--grafana-data-source-url",
			env_var="OPSICONFD_GRAFANA_DATA_SOURCE_URL",
			help=self._help("opsiconfd", "Grafana data source base url."),
		)
		self._parser.add(
			"--restart-worker-mem",
			env_var="OPSICONFD_RESTART_WORKER_MEM",
			type=int,
			default=0,
			help=self._help("opsiconfd", "Restart worker if allocated process memory (rss) exceeds this value (in MB)."),
		)
		self._parser.add(
			"--welcome-page",
			env_var="OPSICONFD_WELCOME_PAGE",
			type=str2bool,
			default=True,
			help=self._help("opsiconfd", "Show welcome page on index."),
		)
		self._parser.add(
			"--zeroconf",
			env_var="OPSICONFD_ZEROCONF",
			type=str2bool,
			default=True,
			help=self._help("opsiconfd", "Publish opsiconfd service via zeroconf."),
		)
		self._parser.add("--ex-help", action="store_true", help=self._help("expert", "Show expert help message and exit."))
		self._parser.add(
			"--debug",
			env_var="OPSICONFD_DEBUG",
			type=str2bool,
			nargs="?",
			const=True,
			default=False,
			help=self._help("expert", "Turn debug mode on, never use in production."),
		)
		self._parser.add(
			"--debug-options",
			nargs="+",
			env_var="OPSICONFD_DEBUG_OPTIONS",
			default=None,
			help=self._help("expert", "A list of debug options (possible options are: rpc-error-log)"),
		)
		self._parser.add(
			"--profiler",
			env_var="OPSICONFD_PROFILER",
			type=str2bool,
			nargs="?",
			const=True,
			default=False,
			help=self._help("expert", "Turn profiler on. This will slow down requests, never use in production."),
		)
		self._parser.add(
			"--node-name",
			env_var="OPSICONFD_NODE_NAME",
			help=self._help("expert", "Node name to use."),
			default=DEFAULT_NODE_NAME,
		)
		self._parser.add(
			"--executor-workers",
			env_var="OPSICONFD_EXECUTOR_WORKERS",
			type=int,
			default=10,
			help=self._help("expert", "Number of thread pool workers for asyncio."),
		)
		self._parser.add(
			"--log-slow-async-callbacks",
			env_var="OPSICONFD_LOG_SLOW_ASYNC_CALLBACKS",
			type=float,
			default=0.0,
			metavar="THRESHOLD",
			help=self._help("expert", "Log asyncio callbacks which takes THRESHOLD seconds or more."),
		)
		self._parser.add(
			"--addon-dirs",
			nargs="+",
			env_var="OPSI_ADDON_DIRS",
			default=["/usr/lib/opsiconfd/addons", VAR_ADDON_DIR],
			help=self._help("expert", "A list of addon directories"),
		)
		self._parser.add(
			"--jsonrpc-time-to-cache",
			env_var="OPSICONFD_JSONRPC_TIME_TO_CACHE",
			default=0.5,
			type=float,
			help=self._help("expert", "Minimum time in seconds that a jsonrpc must take before the data is cached."),
		)
		self._parser.add(
			"--admin-interface-disabled-features",
			nargs="+",
			env_var="OPSICONFD_ADMIN_INTERFACE_DISABLED_FEATURES",
			default=None,
			help=self._help(
				"opsiconfd",
				"A list of admin interface features to disable (features: terminal, rpc-interface).",
			),
			choices=["terminal", "rpc-interface"],
		)
		self._parser.add(
			"--admin-interface-terminal-shell",
			env_var="OPSICONFD_ADMIN_INTERFACE_TERMINAL_SHELL",
			default="/bin/bash",
			help=self._help("opsiconfd", "Shell command for admin interface terminal"),
		)
		self._parser.add(
			"--allow-host-key-only-auth",
			env_var="OPSICONFD_ALLOW_HOST_KEY_ONLY_AUTH",
			type=str2bool,
			nargs="?",
			const=True,
			default=False,
			help=self._help("expert", "Clients are allowed to login with the host key only."),
		)
		self._parser.add(
			"--maintenance",
			nargs="*",
			env_var="OPSICONFD_MAINTENANCE",
			default=False,
			help=self._help("opsicconfd", "Start opsiconfd in maintenance mode, except for these addresses."),
		)

		if self._pytest:
			self._parser.add("args", nargs="*")
			return

		if not self._sub_command:
			self._parser.add(
				"action",
				nargs=None if self._sub_command else "?",
				choices=(
					"start",
					"stop",
					"force-stop",
					"status",
					"restart",
					"reload",
					"setup",
					"log-viewer",
					"health-check",
					"backup",
					"restore",
				),
				default="start",
				metavar="ACTION",
				help=self._help(
					"opsiconfd",
					"The ACTION to perform:\n"
					"start:         Start opsiconfd.\n"
					"stop:          Stop opsiconfd, wait for connections to complete.\n"
					"force-stop:    Force stop opsiconfd, close all connections.\n"
					"status:        Get opsiconfd running status.\n"
					"restart:       Restart opsiconfd.\n"
					"reload:        Reload config from file.\n"
					"setup:         Run setup tasks.\n"
					"log-viewer:    Show log stream on console.\n"
					"health-check:  Run a health-check.\n"
					"backup:        Run backup.\n"
					"restore:       Restore backup.\n",
				),
			)
			return

		if self._sub_command == "health-check":
			self._parser.add("--detailed", action="store_true", help=self._help("health-check", "Print details of each check."))

		if self._sub_command in ("backup", "restore"):
			self._parser.add(
				"--quiet",
				action="store_true",
				help=self._help(("backup", "restore"), "Do not show output or progess except errors."),
			)

		if self._sub_command == "backup":
			now = datetime.now().strftime("%Y%m%d-%H%M%S")
			self._parser.add(
				"--no-config-files",
				action="store_true",
				help=self._help("backup", "Do not add config files to backup."),
			)
			self._parser.add(
				"--overwrite",
				action="store_true",
				help=self._help("backup", "Overwrite existing backup file."),
			)
			self._parser.add(
				"backup_file",
				nargs="?",
				default=f"opsiconfd-backup-{now}.msgpack.lz4",
				metavar="BACKUP_FILE",
				help=self._help("backup", "The BACKUP_FILE to write to."),
			)

		if self._sub_command == "restore":
			self._parser.add(
				"--config-files",
				action="store_true",
				help=self._help("restore", "Restore config files from backup."),
			)
			self._parser.add(
				"--server-id",
				env_var="OPSICONFD_SERVER_ID",
				default="local",
				help=self._help(
					"restore",
					(
						"The server ID to set. The following special values can be used: \n"
						"local: Use the locally configured server ID from opsi.conf.\n"
						"backup: Use the ID of the server from which the backup was created."
					),
				),
			)
			self._parser.add(
				"backup_file",
				metavar="BACKUP_FILE",
				help=self._help("backup", "The BACKUP_FILE to restore from."),
			)


config = Config()
