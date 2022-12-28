# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
opsiconfd.backend.rpc.object_to_group
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from opsicommon.objects import ObjectToGroup  # type: ignore[import]
from opsicommon.types import forceList  # type: ignore[import]

from . import rpc_method

if TYPE_CHECKING:
	from .protocol import BackendProtocol, IdentType


class RPCObjectToGroupMixin(Protocol):
	@rpc_method(check_acl=False)
	def objectToGroup_insertObject(self: BackendProtocol, objectToGroup: dict | ObjectToGroup) -> None:  # pylint: disable=invalid-name
		ace = self._get_ace("objectToGroup_insertObject")
		self._mysql.insert_object(table="OBJECT_TO_GROUP", obj=objectToGroup, ace=ace, create=True, set_null=True)

	@rpc_method(check_acl=False)
	def objectToGroup_updateObject(self: BackendProtocol, objectToGroup: dict | ObjectToGroup) -> None:  # pylint: disable=invalid-name
		ace = self._get_ace("objectToGroup_updateObject")
		self._mysql.insert_object(table="OBJECT_TO_GROUP", obj=objectToGroup, ace=ace, create=False, set_null=False)

	@rpc_method(check_acl=False)
	def objectToGroup_createObjects(  # pylint: disable=invalid-name
		self: BackendProtocol, objectToGroups: list[dict] | list[ObjectToGroup] | dict | ObjectToGroup
	) -> None:
		ace = self._get_ace("objectToGroup_createObjects")
		with self._mysql.session() as session:
			for objectToGroup in forceList(objectToGroups):
				self._mysql.insert_object(table="OBJECT_TO_GROUP", obj=objectToGroup, ace=ace, create=True, set_null=True, session=session)

	@rpc_method(check_acl=False)
	def objectToGroup_updateObjects(  # pylint: disable=invalid-name
		self: BackendProtocol, objectToGroups: list[dict] | list[ObjectToGroup] | dict | ObjectToGroup
	) -> None:
		ace = self._get_ace("objectToGroup_updateObjects")
		with self._mysql.session() as session:
			for objectToGroup in forceList(objectToGroups):
				self._mysql.insert_object(table="OBJECT_TO_GROUP", obj=objectToGroup, ace=ace, create=True, set_null=False, session=session)

	@rpc_method(check_acl=False)
	def objectToGroup_getObjects(self: BackendProtocol, attributes: list[str] | None = None, **filter: Any) -> list[ObjectToGroup]:  # pylint: disable=redefined-builtin,invalid-name
		ace = self._get_ace("objectToGroup_getObjects")
		return self._mysql.get_objects(
			table="OBJECT_TO_GROUP", ace=ace, object_type=ObjectToGroup, attributes=attributes, filter=filter
		)

	@rpc_method(check_acl=False)
	def objectToGroup_getHashes(self: BackendProtocol, attributes: list[str] | None = None, **filter: Any) -> list[dict]:  # pylint: disable=redefined-builtin,invalid-name
		ace = self._get_ace("objectToGroup_getObjects")
		return self._mysql.get_objects(
			table="OBJECT_TO_GROUP", object_type=ObjectToGroup, ace=ace, return_type="dict", attributes=attributes, filter=filter
		)

	@rpc_method(check_acl=False)
	def objectToGroup_getIdents(  # pylint: disable=invalid-name
		self: BackendProtocol, returnType: IdentType = "str", **filter: Any  # pylint: disable=redefined-builtin
	) -> list[str] | list[dict] | list[list] | list[tuple]:
		ace = self._get_ace("objectToGroup_getObjects")
		return self._mysql.get_idents(table="OBJECT_TO_GROUP", object_type=ObjectToGroup, ace=ace, ident_type=returnType, filter=filter)

	@rpc_method(check_acl=False)
	def objectToGroup_deleteObjects(self: BackendProtocol, objectToGroups: list[dict] | list[ObjectToGroup] | dict | ObjectToGroup) -> None:  # pylint: disable=invalid-name
		ace = self._get_ace("objectToGroup_deleteObjects")
		self._mysql.delete_objects(table="OBJECT_TO_GROUP", object_type=ObjectToGroup, obj=objectToGroups, ace=ace)

	@rpc_method(check_acl=False)
	def objectToGroup_create(self: BackendProtocol, groupType: str, groupId: str, objectId: str) -> None:  # pylint: disable=invalid-name
		_hash = locals()
		del _hash["self"]
		self.objectToGroup_createObjects(ObjectToGroup.fromHash(_hash))

	@rpc_method(check_acl=False)
	def objectToGroup_delete(self: BackendProtocol, id: str) -> None:  # pylint: disable=redefined-builtin,invalid-name
		self.objectToGroup_deleteObjects([{"id": id}])
