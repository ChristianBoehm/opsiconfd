# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
opsiconfd.auth
"""

from __future__ import annotations

from typing import Set

from opsicommon.exceptions import BackendAuthenticationError  # type: ignore[import]

from ..config import opsi_config


class AuthenticationModule:
	def get_instance(self) -> AuthenticationModule:
		return self.__class__()

	def authenticate(self, username: str, password: str) -> None:
		raise BackendAuthenticationError("Not implemented")

	def get_groupnames(self, username: str) -> Set[str]:  # pylint: disable=unused-argument
		return set()

	def get_admin_groupname(self) -> str:
		return opsi_config.get("groups", "admingroup")

	def get_read_only_groupnames(self) -> Set[str]:
		return set(opsi_config.get("groups", "readonly") or [])

	def user_is_admin(self, username: str) -> bool:
		return self.get_admin_groupname() in self.get_groupnames(username)

	def user_is_read_only(self, username: str, forced_user_groupnames: Set[str] = None) -> bool:
		user_groupnames = set()
		if forced_user_groupnames is None:
			user_groupnames = self.get_groupnames(username)
		else:
			user_groupnames = forced_user_groupnames

		read_only_groupnames = self.get_read_only_groupnames()
		for group_name in user_groupnames:
			if group_name in read_only_groupnames:
				return True
		return False
