# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2023 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
health check
"""

from __future__ import annotations

import re
from datetime import date
from subprocess import run

import psutil
from opsicommon.system.info import (
	linux_distro_id,
	linux_distro_id_like_contains,
	linux_distro_version_id,
)
from opsicommon.utils import compare_versions
from requests import get
from requests.exceptions import ConnectionError as RequestConnectionError
from requests.exceptions import ConnectTimeout

from opsiconfd import __version__
from opsiconfd.check.common import CheckResult, CheckStatus, PartialCheckResult, exc_to_result
from opsiconfd.logging import logger

REPO_URL = "https://download.opensuse.org/repositories/home:/uibmz:/opsi:/4.3:/stable/Debian_11/"
CHECK_SYSTEM_PACKAGES = ("opsiconfd", "opsi-utils", "opsipxeconfd")
LINUX_DISTRO_EOL = {
	"ubuntu": {
		"18.04": date(2023, 4, 1),
		"20.04": date(2025, 4, 1),
		"22.04": date(2027, 4, 1),
	},
	"debian": {
		"9": date(2021, 5, 1),
		"10": date(2022, 8, 1),
		"11": date(2024, 7, 1),
		"12": date(2026, 6, 10),
	},
	"rhel": {
		"7": date(2029, 5, 1),
		"8": date(2030, 6, 1),
	},
	"opensuse-leap": {
		"15.3": date(2022, 12, 31),
		"15.4": date(2023, 11, 1),
	},
	"almalinux": {
		"8": date(2024, 5, 1),
		"9": date(2027, 5, 31),
	},
	"centos": {
		"7": date(2024, 5, 1),
		"8": date(2021, 12, 31),
	},
}


def check_distro_eol() -> CheckResult:
	"""
	## Operating System End Of Life

	Checks whether the server system still receives updates.
	The check issues a warning 90 days before the end of life of a distribution.
	After the end-of-life date, it issues an error.
	"""
	result = CheckResult(
		check_id="linux_distro_eol",
		check_name="Operating System End Of Life",
		check_description="""
			Check Operating System end-of-life date.
			'End-of-life' or EOL is a term used by software vendors indicating that it is ending or
			limiting it's support on the product and/or version to shift focus on their newer products and/or version.
		""",
	)
	with exc_to_result(result):
		distro = linux_distro_id()
		version = linux_distro_version_id()
		if version_info := LINUX_DISTRO_EOL.get(distro):
			if eol := version_info.get(version):
				today = date.today()
				diff = (today - eol).days
				if diff < -90:
					result.check_status = CheckStatus.OK
					result.message = f"Version {version} of distribution {distro} is supported until {eol}."
				elif 0 >= diff >= -90:
					result.check_status = CheckStatus.WARNING
					result.message = f"Version {version} of distribution {distro} is supported until {eol}."
				else:
					result.check_status = CheckStatus.ERROR
					result.message = f"Support of version {version} of distribution {distro} ended on {eol}"
					result.upgrade_issue = __version__
			else:
				result.check_status = CheckStatus.ERROR
				result.message = f"Version {version} of distribution {distro} is not supported."
				result.upgrade_issue = __version__

		else:
			result.check_status = CheckStatus.ERROR
			result.message = f"Linux distribution {distro} is not supported."
			result.upgrade_issue = __version__

	return result


def get_repo_versions() -> dict[str, str | None]:
	url = REPO_URL
	packages = CHECK_SYSTEM_PACKAGES
	repo_data = None

	repo_versions: dict[str, str | None] = {}

	try:
		repo_data = get(url, timeout=10)
	except (RequestConnectionError, ConnectTimeout) as err:
		logger.error("Could not get package versions from repository")
		logger.error(str(err))
		return {}
	if repo_data.status_code >= 400:
		logger.error("Could not get package versions from repository: %d - %s", repo_data.status_code, repo_data.text)
		return {}
	for package in packages:
		repo_versions[package] = None
		match = re.search(f"{package}_(.+?).tar.gz", repo_data.text)
		if match:
			version = match.group(1)
			repo_versions[package] = version
			logger.debug("Available version for %s: %s", package, version)
	return repo_versions


def get_installed_packages(packages: dict | None = None) -> dict:  # pylint: disable=too-many-branches
	installed_versions: dict[str, str] = {}
	if linux_distro_id_like_contains(("sles", "rhel")):
		cmd = ["yum", "list", "installed"]
		regex = re.compile(r"^(\S+)\s+(\S+)\s+(\S+).*$")
		res = run(cmd, shell=False, check=True, capture_output=True, text=True, encoding="utf-8", timeout=10).stdout
		for line in res.split("\n"):
			match = regex.search(line)
			if not match:
				continue
			p_name = match.group(1).split(".")[0]
			if not packages:
				if p_name.startswith("opsi"):
					logger.info("Package '%s' found: version '%s'", p_name, match.group(2))
					installed_versions[p_name] = match.group(2)
			else:
				if p_name in packages:
					logger.info("Package '%s' found: version '%s'", p_name, match.group(2))
					installed_versions[p_name] = match.group(2)
	elif linux_distro_id_like_contains("opensuse"):
		cmd = ["zypper", "search", "-is", "opsi*"]
		regex = re.compile(r"^[^S]\s+\|\s+(\S+)\s+\|\s+(\S+)\s+\|\s+(\S+)\s+\|\s+(\S+)\s+\|\s+(\S+).*$")
		res = run(cmd, shell=False, check=True, capture_output=True, text=True, encoding="utf-8", timeout=10).stdout
		for line in res.split("\n"):
			match = regex.search(line)
			if not match:
				continue
			p_name = match.group(1)
			if not packages:
				if p_name.startswith("opsi"):
					logger.info("Package '%s' found: version '%s'", p_name, match.group(3))
					installed_versions[p_name] = match.group(3)
			else:
				if p_name in packages:
					logger.info("Package '%s' found: version '%s'", p_name, match.group(3))
					installed_versions[p_name] = match.group(3)
	else:
		cmd = ["dpkg", "-l"]
		regex = re.compile(r"^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+.*$")
		res = run(cmd, shell=False, check=True, capture_output=True, text=True, encoding="utf-8", timeout=10).stdout
		for line in res.split("\n"):
			match = regex.search(line)
			if not match or match.group(1) != "ii":
				continue
			p_name = match.group(2)
			if not packages:
				if p_name.startswith("opsi"):
					logger.info("Package '%s' found: version '%s'", p_name, match.group(3))
					installed_versions[p_name] = match.group(3)
			else:
				if p_name in packages:
					logger.info("Package '%s' found: version '%s'", p_name, match.group(3))
					installed_versions[p_name] = match.group(3)
	return installed_versions


def check_system_packages() -> CheckResult:  # pylint: disable=too-many-branches, too-many-statements, too-many-locals
	"""
	## System packages
	Currently the following system packages are checked for actuality:

	* opsiconfd
	* opsi-utils
	* opsipxeconfd

	The check is carried out against the stable repository of uib
	(https://download.opensuse.org/repositories/home:/uibmz:/opsi:/4.3:/stable/Debian_11/).
	Older versions are considered a warning and if one of the packages is not installed, an error is issued.
	"""
	result = CheckResult(
		check_id="system_packages",
		check_name="System packages",
		check_description="Check system package versions",
		message="All packages are up to date.",
	)
	with exc_to_result(result):
		repo_versions = get_repo_versions()
		installed_versions: dict[str, str] = {}
		try:
			installed_versions = get_installed_packages(repo_versions)
		except RuntimeError as err:
			error = f"Could not get package versions from system: {err}"
			logger.error(error)
			result.check_status = CheckStatus.ERROR
			result.message = error
			return result

		logger.info("Installed packages: %s", installed_versions)

		not_installed = 0
		outdated = 0
		for package, available_version in repo_versions.items():
			details = {
				"package": package,
				"available_version": available_version,
				"version": installed_versions.get(package),
				"outdated": False,
			}
			partial_result = PartialCheckResult(
				check_id=f"system_packages:{package}", check_name=f"System package {package!r}", details=details
			)
			if not details["version"]:
				partial_result.check_status = CheckStatus.ERROR
				partial_result.message = f"Package {package!r} is not installed."
				partial_result.upgrade_issue = __version__
				not_installed = not_installed + 1
			elif compare_versions(available_version or "0", ">", details["version"]):  # type: ignore[arg-type]
				outdated = outdated + 1
				partial_result.check_status = CheckStatus.WARNING
				partial_result.message = (
					f"Package {package!r} is out of date. "
					f"Installed version {details['version']!r} < available version {available_version!r}"
				)
				details["outdated"] = True
			else:
				partial_result.check_status = CheckStatus.OK
				partial_result.message = f"Package {package!r} is up to date. Installed version: {details['version']!r}"
			result.add_partial_result(partial_result)

		result.details = {"packages": len(repo_versions.keys()), "not_installed": not_installed, "outdated": outdated}
		if not_installed > 0 or outdated > 0:
			result.message = (
				f"Out of {len(repo_versions.keys())} packages checked, {not_installed} are not installed and {outdated} are out of date."
			)
	return result


def get_disk_mountpoints() -> set:
	partitions = psutil.disk_partitions()
	check_mountpoints = set()
	var_added = False
	for mountpoint in sorted([p.mountpoint for p in partitions if p.fstype], reverse=True):
		if mountpoint in ("/", "/tmp") or mountpoint.startswith("/var/lib/opsi/"):
			check_mountpoints.add(mountpoint)
		elif mountpoint in ("/var", "/var/lib", "/var/lib/opsi") and not var_added:
			check_mountpoints.add(mountpoint)
			var_added = True
	return check_mountpoints


def check_disk_usage() -> CheckResult:
	"""
	## Disk usage
	Checks the free space for the following mount points:

	* /
	* /temp
	* /var, /var/lib or var/lib/opsi

	If there is less than 15 GiB free, a warning is given.
	If there are less than 7.5 GiB, it is considered an error.
	"""
	result = CheckResult(
		check_id="disk_usage",
		check_name="Disk usage",
		check_description="Check disk usage",
		message="Sufficient free space on all file systems.",
	)
	with exc_to_result(result):
		check_mountpoints = get_disk_mountpoints()

		count = 0
		for mountpoint in check_mountpoints:
			usage = psutil.disk_usage(mountpoint)
			percent_free = usage.free * 100 / usage.total
			free_gb = usage.free / 1_000_000_000
			check_status = CheckStatus.OK
			if free_gb < 7.5:
				count += 1
				check_status = CheckStatus.ERROR
			elif free_gb < 15:
				count += 1
				check_status = CheckStatus.WARNING
			partial_result = PartialCheckResult(
				check_id=f"disk_usage:{mountpoint}",
				check_name=f"Disk usage on filesystem {mountpoint!r}",
				check_status=check_status,
				message=(
					f"{'Sufficient' if check_status == CheckStatus.OK else 'Insufficient'}"
					f" free space of {free_gb:0.2f} GB ({percent_free:0.2f} %) on {mountpoint!r}"
				),
				details={"mountpoint": mountpoint, "total": usage.total, "used": usage.used, "free": usage.free},
			)
			result.add_partial_result(partial_result)

		if result.check_status != CheckStatus.OK:
			result.message = f"Insufficient free space on {count} file system{'s' if count > 1 else ''}."
	return result
