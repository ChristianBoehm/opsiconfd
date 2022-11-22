# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
opsiconfd.backend.rpc.software_license
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Protocol

from opsicommon.objects import (  # type: ignore[import]
	ConcurrentSoftwareLicense,
	OEMSoftwareLicense,
	RetailSoftwareLicense,
	SoftwareLicense,
	VolumeSoftwareLicense,
)
from opsicommon.types import forceList  # type: ignore[import]

from . import rpc_method

if TYPE_CHECKING:
	from .protocol import BackendProtocol, IdentType


class RPCSoftwareLicenseMixin(Protocol):
	@rpc_method
	def softwareLicense_insertObject(self: BackendProtocol, softwareLicense: dict | SoftwareLicense) -> None:  # pylint: disable=invalid-name
		self._check_module("license_management")
		ace = self._get_ace("softwareLicense_insertObject")
		self._mysql.insert_object(table="SOFTWARE_LICENSE", obj=softwareLicense, ace=ace, create=True, set_null=True)

	@rpc_method
	def softwareLicense_updateObject(self: BackendProtocol, softwareLicense: dict | SoftwareLicense) -> None:  # pylint: disable=invalid-name
		ace = self._get_ace("softwareLicense_updateObject")
		self._mysql.insert_object(table="SOFTWARE_LICENSE", obj=softwareLicense, ace=ace, create=False, set_null=False)

	@rpc_method
	def softwareLicense_createObjects(  # pylint: disable=invalid-name
		self: BackendProtocol, softwareLicenses: List[dict] | List[SoftwareLicense] | dict | SoftwareLicense
	) -> None:
		self._check_module("license_management")
		ace = self._get_ace("softwareLicense_createObjects")
		for softwareLicense in forceList(softwareLicenses):
			self._mysql.insert_object(table="SOFTWARE_LICENSE", obj=softwareLicense, ace=ace, create=True, set_null=True)

	@rpc_method
	def softwareLicense_updateObjects(  # pylint: disable=invalid-name
		self: BackendProtocol, softwareLicenses: List[dict] | List[SoftwareLicense] | dict | SoftwareLicense
	) -> None:
		ace = self._get_ace("softwareLicense_updateObjects")
		for softwareLicense in forceList(softwareLicenses):
			self._mysql.insert_object(table="SOFTWARE_LICENSE", obj=softwareLicense, ace=ace, create=True, set_null=False)

	@rpc_method
	def softwareLicense_getObjects(self: BackendProtocol, attributes: List[str] = None, **filter: Any) -> List[SoftwareLicense]:  # pylint: disable=redefined-builtin,invalid-name
		ace = self._get_ace("softwareLicense_getObjects")
		return self._mysql.get_objects(
			table="SOFTWARE_LICENSE", ace=ace, object_type=SoftwareLicense, attributes=attributes, filter=filter
		)

	@rpc_method
	def softwareLicense_getHashes(self: BackendProtocol, attributes: List[str] = None, **filter: Any) -> List[dict]:  # pylint: disable=redefined-builtin,invalid-name
		ace = self._get_ace("softwareLicense_getObjects")
		return self._mysql.get_objects(
			table="SOFTWARE_LICENSE", object_type=SoftwareLicense, ace=ace, return_type="dict", attributes=attributes, filter=filter
		)

	@rpc_method
	def softwareLicense_getIdents(  # pylint: disable=invalid-name
		self: BackendProtocol, returnType: IdentType = "str", **filter: Any  # pylint: disable=redefined-builtin
	) -> List[str] | List[dict] | List[list] | List[tuple]:
		ace = self._get_ace("softwareLicense_getObjects")
		return self._mysql.get_idents(table="SOFTWARE_LICENSE", object_type=SoftwareLicense, ace=ace, ident_type=returnType, filter=filter)

	@rpc_method
	def softwareLicense_deleteObjects(  # pylint: disable=invalid-name
		self: BackendProtocol, softwareLicenses: List[dict] | List[SoftwareLicense] | dict | SoftwareLicense
	) -> None:
		ace = self._get_ace("softwareLicense_deleteObjects")
		self._mysql.delete_objects(table="SOFTWARE_LICENSE", object_type=SoftwareLicense, obj=softwareLicenses, ace=ace)

	@rpc_method
	def softwareLicense_delete(self: BackendProtocol, id: str) -> None:  # pylint: disable=redefined-builtin,invalid-name
		self.softwareLicense_deleteObjects([{"id": id}])

	@rpc_method
	def softwareLicense_createRetail(  # pylint: disable=too-many-arguments,invalid-name
		self: BackendProtocol,
		id: str,  # pylint: disable=redefined-builtin,unused-argument
		licenseContractId: str,  # pylint: disable=unused-argument
		maxInstallations: int = None,  # pylint: disable=unused-argument
		boundToHost: str = None,  # pylint: disable=unused-argument
		expirationDate: str = None,  # pylint: disable=unused-argument
	) -> None:
		_hash = locals()
		del _hash["self"]
		self.softwareLicense_createObjects(RetailSoftwareLicense.fromHash(_hash))

	@rpc_method
	def softwareLicense_createOEM(  # pylint: disable=too-many-arguments,invalid-name
		self: BackendProtocol,
		id: str,  # pylint: disable=redefined-builtin,unused-argument
		licenseContractId: str,  # pylint: disable=unused-argument
		maxInstallations: int = None,  # pylint: disable=unused-argument
		boundToHost: str = None,  # pylint: disable=unused-argument
		expirationDate: str = None,  # pylint: disable=unused-argument
	) -> None:
		_hash = locals()
		del _hash["self"]
		self.softwareLicense_createObjects(OEMSoftwareLicense.fromHash(_hash))

	@rpc_method
	def softwareLicense_createVolume(  # pylint: disable=too-many-arguments,invalid-name
		self: BackendProtocol,
		id: str,  # pylint: disable=redefined-builtin,unused-argument
		licenseContractId: str,  # pylint: disable=unused-argument
		maxInstallations: int = None,  # pylint: disable=unused-argument
		boundToHost: str = None,  # pylint: disable=unused-argument
		expirationDate: str = None,  # pylint: disable=unused-argument
	) -> None:
		_hash = locals()
		del _hash["self"]
		self.softwareLicense_createObjects(VolumeSoftwareLicense.fromHash(_hash))

	@rpc_method
	def softwareLicense_createConcurrent(  # pylint: disable=too-many-arguments,invalid-name
		self: BackendProtocol,
		id: str,  # pylint: disable=redefined-builtin,unused-argument
		licenseContractId: str,  # pylint: disable=unused-argument
		maxInstallations: int = None,  # pylint: disable=unused-argument
		boundToHost: str = None,  # pylint: disable=unused-argument
		expirationDate: str = None,  # pylint: disable=unused-argument
	) -> None:
		_hash = locals()
		del _hash["self"]
		self.softwareLicense_createObjects(ConcurrentSoftwareLicense.fromHash(_hash))
