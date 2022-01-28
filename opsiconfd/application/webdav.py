# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
webdav
"""

import os
from typing import Dict, List, Optional

from wsgidav import util
import wsgidav.fs_dav_provider
from wsgidav.dav_error import HTTP_FORBIDDEN, DAVError
from wsgidav.dav_provider import _DAVResource, DAVCollection, DAVProvider
from wsgidav.fs_dav_provider import FilesystemProvider, FolderResource
from wsgidav.wsgidav_app import WsgiDAVApp


from .. import __version__
from ..logging import logger
from ..config import FQDN
from ..backend import get_backend
from ..wsgi import WSGIMiddleware


PUBLIC_FOLDER = "/var/lib/opsi/public"
BLOCK_SIZE = 64 * 1024
APP_CONFIG_TEMPLATE = {
	"simple_dc": {
		"user_mapping": {"*": True}  # anonymous access
	},
	"hotfixes": {
		"re_encode_path_info": False,  # Encoding is done in opsiconfd.wsgi
	},
	"http_authenticator": {
		# None: dc.simple_dc.SimpleDomainController(user_mapping)
		"domain_controller": None,
		"accept_basic": False,  # Allow basic authentication, True or False
		"accept_digest": False,  # Allow digest authentication, True or False
		"trusted_auth_header": None,
	},
	"verbose": 1,
	"logging": {
		"enable_loggers": []
	},
	"property_manager": True,  # True: use property_manager.PropertyManager
	"lock_storage": True,  # True: use lock_manager.LockManager
	"block_size": BLOCK_SIZE,  # default = 8192
	"ssl_certificate": True,  # Prevent warning in log
	"dir_browser": {
		"show_user": False,
		"icon": False,
		"response_trailer": f"opsiconfd {__version__} (uvicorn/WsgiDAV)"
	},
	"cors": {
		"allow_origin": "*"
	},
	"provider_mapping": {},
	"mount_path": None
}

# Set file buffer size for reading and writing.
# Sent message chunks will have the same body size.
wsgidav.fs_dav_provider.BUFFER_SIZE = BLOCK_SIZE


# Prevent warning in log
def is_share_anonymous(self, path_info):  # pylint: disable=unused-argument
	return False


wsgidav.dc.base_dc.BaseDomainController.is_share_anonymous = is_share_anonymous


class IgnoreCaseFilesystemProvider(FilesystemProvider):

	def _loc_to_file_path(self, path, environ=None):
		"""Convert resource path to a unicode absolute file path.
		Optional environ argument may be useful e.g. in relation to per-user
		sub-folder chrooting inside root_folder_path.
		"""
		root_path = self.root_folder_path
		assert root_path is not None
		assert util.is_str(root_path)
		assert util.is_str(path)

		path_parts = path.strip("/").split("/")
		file_path = os.path.abspath(os.path.join(root_path, *path_parts))
		if not os.path.exists(file_path):
			cur_path = root_path
			name_found = None
			for part in path_parts:
				cur_path = os.path.join(cur_path, part)
				if not os.path.exists(cur_path):
					part_lower = part.lower()
					name_found = None
					for name in os.listdir(os.path.dirname(cur_path)):
						if name.lower() == part_lower:
							name_found = name
							break
					if not name_found:
						# Give up
						break
					cur_path = os.path.join(os.path.dirname(cur_path), name_found)
			if name_found and cur_path.lower() == file_path.lower():
				file_path = cur_path

		is_shadow, file_path = self._resolve_shadow_path(path, environ, file_path)
		if not file_path.startswith(root_path) and not is_shadow:
			raise RuntimeError(f"Security exception: tried to access file outside root: {file_path}")

		# Convert to unicode
		file_path = util.to_unicode_safe(file_path)
		return file_path


class VirtualRootFilesystemCollection(DAVCollection):
	def __init__(self, environ: dict, provider):
		DAVCollection.__init__(self, "/", environ)
		self.provider = provider

	def get_member_names(self) -> List[str]:
		return [name.lstrip("/") for name in self.provider.provider_mapping if name != "/"]

	def get_member(self, name: str) -> Optional[FolderResource]:
		if not (provider := self.provider.provider_mapping.get(f"/{name}")):
			raise DAVError(HTTP_FORBIDDEN)
		resource = FolderResource(f"/{name}", self.environ, provider.root_folder_path)
		resource.name = name
		return resource


class VirtualRootFilesystemProvider(DAVProvider):
	def __init__(self, provider_mapping: Dict[str, FilesystemProvider]) -> None:
		super().__init__()
		self.provider_mapping = provider_mapping
		self.readonly = True

	def get_resource_inst(self, path: str, environ: dict) -> _DAVResource:
		root = VirtualRootFilesystemCollection(environ, self)
		return root.resolve("", path)


def webdav_setup(app):  # pylint: disable=too-many-statements, too-many-branches, too-many-locals
	hosts = get_backend().host_getObjects(type='OpsiDepotserver', id=FQDN)  # pylint: disable=no-member
	if not hosts:
		logger.warning("Running on host %s which is not a depot server, webdav disabled.", FQDN)
		return

	depot = hosts[0]
	depot_id = depot.getId()

	filesystems = {}
	try:
		logger.notice(f"Running on depot server '{depot_id}', exporting repository directory")
		if not depot.getRepositoryLocalUrl():
			raise Exception(f"Repository local url for depot '{depot_id}' not found")
		if not depot.getRepositoryLocalUrl().startswith('file:///'):
			raise Exception(f"Invalid repository local url '{depot.getRepositoryLocalUrl()}'")
		path = depot.getRepositoryLocalUrl()[7:]
		logger.debug("Repository local path is '%s'", path)
		if not os.path.isdir(path):
			raise Exception(f"Cannot add webdav content 'repository': directory '{path}' does not exist.")
		if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
			raise Exception(f"Cannot add webdav content 'repository': permissions on directory '{path}' not sufficient.")

		filesystems["repository"] = {"path": path, "ignore_case": False, "read_only": False}
	except Exception as exc:  # pylint: disable=broad-except
		logger.error(exc, exc_info=True)

	try:
		logger.notice(f"Running on depot server '{depot_id}', exporting depot directory")
		if not depot.getDepotLocalUrl():
			raise Exception(f"Repository local url for depot '{depot_id}' not found")
		if not depot.getDepotLocalUrl().startswith('file:///'):
			raise Exception(f"Invalid repository local url '{depot.getDepotLocalUrl()}' not allowed")
		path = depot.getDepotLocalUrl()[7:]
		logger.debug("Depot local path is '%s'", path)
		if not os.path.isdir(path):
			raise Exception(f"Cannot add webdav content 'depot': directory '{path}' does not exist.")
		if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
			raise Exception(f"Cannot add webdav content 'depot': permissions on directory '{path}' not sufficient.")

		filesystems["depot"] = {"path": path, "ignore_case": True, "read_only": False}
	except Exception as exc:  # pylint: disable=broad-except
		logger.error(exc, exc_info=True)

	try:
		logger.notice(f"Running on depot server '{depot_id}', exporting workbench directory")
		if not depot.getWorkbenchLocalUrl():
			raise Exception(f"Workbench local url for depot '{depot_id}' not found")
		if not depot.getWorkbenchLocalUrl().startswith('file:///'):
			raise Exception(f"Invalid workbench local url '{depot.getWorkbenchLocalUrl()}' not allowed")
		path = depot.getWorkbenchLocalUrl()[7:]
		logger.debug("Workbench local path is '%s'", path)
		if not os.path.isdir(path):
			raise Exception(f"Cannot add webdav content 'workbench': directory '{path}' does not exist.")
		if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
			raise Exception(f"Cannot add webdav content 'workbench': permissions on directory '{path}' not sufficient.")

		filesystems["workbench"] = {"path": path, "ignore_case": False, "read_only": False}
	except Exception as exc:  # pylint: disable=broad-except
		logger.error(exc, exc_info=True)

	try:
		logger.notice(f"Running on depot server '{depot_id}', exporting public directory")
		logger.debug("Public path is '%s'", PUBLIC_FOLDER)
		if not os.path.isdir(PUBLIC_FOLDER):
			raise Exception(f"Cannot add webdav content 'public': directory '{PUBLIC_FOLDER}' does not exist.")
		if not os.access(PUBLIC_FOLDER, os.R_OK | os.W_OK | os.X_OK):
			raise Exception(f"Cannot add webdav content 'public': permissions on directory '{PUBLIC_FOLDER}' not sufficient.")

		filesystems["public"] = {"path": PUBLIC_FOLDER, "ignore_case": False, "read_only": True}
	except Exception as exc:  # pylint: disable=broad-except
		logger.error(exc, exc_info=True)

	if os.path.isdir("/tftpboot"):
		try:
			path = "/tftpboot"
			logger.notice(f"Running on depot server '{depot_id}', exporting boot directory")
			if not os.access(path, os.R_OK | os.X_OK):
				raise Exception(f"Cannot add webdav content 'boot': permissions on directory '{path}' not sufficient.")

			filesystems["boot"] = {"path": path, "ignore_case": True, "read_only": True}
		except Exception as err:  # pylint: disable=broad-except
			logger.error(err, exc_info=True)

	for name, conf in filesystems.items():
		app_config = APP_CONFIG_TEMPLATE.copy()
		prov_class = IgnoreCaseFilesystemProvider if conf["ignore_case"] else FilesystemProvider
		app_config["provider_mapping"]["/"] = prov_class(conf["path"], readonly=conf["read_only"])
		app_config["mount_path"] = f"/{name}"
		app.mount(f"/{name}", WSGIMiddleware(WsgiDAVApp(app_config)))

	app_config = APP_CONFIG_TEMPLATE.copy()
	for name, conf in filesystems.items():
		prov_class = IgnoreCaseFilesystemProvider if conf["ignore_case"] else FilesystemProvider
		app_config["provider_mapping"][f"/{name}"] = prov_class(conf["path"], readonly=False)
	virt_root_provider = VirtualRootFilesystemProvider(app_config["provider_mapping"])
	app_config["provider_mapping"]["/"] = virt_root_provider
	app_config["mount_path"] = "/webdav"
	app.mount("/webdav", WSGIMiddleware(WsgiDAVApp(app_config)))
