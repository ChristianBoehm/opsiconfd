# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
opsiconfd.backend.rpc.audit_hardware
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from copy import deepcopy
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Protocol

from OPSI.Util.File import ConfigFile  # type: ignore[import]
from opsicommon.objects import (  # type: ignore[import]
	AuditHardware,
	AuditHardwareOnHost,
)
from opsicommon.types import forceLanguageCode, forceList  # type: ignore[import]

from opsiconfd.logging import logger

from ..auth import RPCACE
from . import rpc_method

if TYPE_CHECKING:
	from .protocol import BackendProtocol, IdentType

OPSI_HARDWARE_CLASSES: List[Dict[str, Any]] = []


def inherit_from_super_classes(classes: List[Dict[str, Any]], _class: Dict[str, Any], scname: str = None) -> None:  # pylint: disable=unused-private-member
	if not scname:  # pylint: disable=too-many-nested-blocks
		for _scname in _class["Class"].get("Super", []):
			inherit_from_super_classes(classes, _class, _scname)
	else:
		if not classes:
			logger.error("Super class '%s' of class '%s' not found", scname, _class["Class"].get("Opsi"))
		for cls in classes:
			if cls["Class"].get("Opsi") == scname:
				clcopy = deepcopy(cls)
				inherit_from_super_classes(classes, clcopy)
				new_values = []
				for new_value in clcopy["Values"]:
					found_at = -1
					for idx, current_value in enumerate(_class["Values"]):
						if current_value["Opsi"] == new_value["Opsi"]:
							if not current_value.get("UI"):
								_class["Values"][idx]["UI"] = new_value.get("UI", "")  # pylint: disable=loop-invariant-statement
							found_at = idx
							break
					if found_at > -1:
						new_value = _class["Values"][found_at]  # pylint: disable=loop-invariant-statement
						del _class["Values"][found_at]  # pylint: disable=loop-invariant-statement
					new_values.append(new_value)
				new_values.extend(_class["Values"])  # pylint: disable=loop-invariant-statement
				_class["Values"] = new_values  # pylint: disable=loop-invariant-statement
				break


class RPCAuditHardwareMixin(Protocol):
	_audit_hardware_config: Dict[str, Dict[str, Dict[str, str]]] = {}
	_audit_hardware_config_file: str = "/etc/opsi/hwaudit/opsihwaudit.conf"
	_audit_hardware_config_locales_dir: str = "/etc/opsi/hwaudit/locales"

	def __init__(self) -> None:
		super().__init__()
		self._set_audit_hardware_config(self.auditHardware_getConfig())

	def _set_audit_hardware_config(self, config: List[Dict[str, Dict[str, str] | List[Dict[str, str]]]]) -> None:
		self._audit_hardware_config = {}
		for conf in config:
			hw_class = conf["Class"]["Opsi"]  # type: ignore
			self._audit_hardware_config[hw_class] = {}
			for value in conf["Values"]:
				self._audit_hardware_config[hw_class][value["Opsi"]] = {"Type": value["Type"], "Scope": value["Scope"]}  # type: ignore  # pylint: disable=loop-invariant-statement
		AuditHardware.setHardwareConfig(config)
		AuditHardwareOnHost.setHardwareConfig(config)

	def _audit_hardware_by_hardware_class(
		self: BackendProtocol,
		audit_hardwares: List[dict] | List[AuditHardware] | dict | AuditHardware
	) -> Dict[str, List[AuditHardware]]:
		by_hardware_class = defaultdict(list)
		for ahoh in forceList(audit_hardwares):  # pylint: disable=use-list-copy
			if not isinstance(ahoh, AuditHardware):
				ahoh = AuditHardware.fromHash(ahoh)
			by_hardware_class[ahoh.hardwareClass].append(ahoh)
		return by_hardware_class

	def auditHardware_deleteAll(self: BackendProtocol) -> None:  # pylint: disable=invalid-name
		with self._mysql.session() as session:
			for hardware_class in self._audit_hardware_config:
				session.execute(f"TRUNCATE TABLE `HARDWARE_CONFIG_{hardware_class}`")
				session.execute(f"TRUNCATE TABLE `HARDWARE_DEVICE_{hardware_class}`")

	@rpc_method
	def auditHardware_getConfig(self: BackendProtocol, language: str = None) -> List[Dict[str, Dict[str, str] | List[Dict[str, str]]]]:  # pylint: disable=invalid-name,too-many-locals,too-many-branches,too-many-statements
		self._get_ace("auditHardware_getConfig")

		if not language:
			language = "en_US"
		language = forceLanguageCode(language).replace("-", "_")

		locale_file = os.path.join(self._audit_hardware_config_locales_dir, language or "en_US")
		if not os.path.exists(locale_file):
			logger.error("No translation file found for language %s, falling back to en_US", language)
			language = "en_US"
			locale_file = os.path.join(self._audit_hardware_config_locales_dir, language)

		locale = {}
		try:
			for line in ConfigFile(locale_file).parse():
				try:  # pylint: disable=loop-try-except-usage
					identifier, translation = line.split("=", 1)
					locale[identifier.strip()] = translation.strip()
				except ValueError as verr:
					logger.trace("Failed to read translation: %s", verr)
		except Exception as err:  # pylint: disable=broad-except
			logger.error("Failed to read translation file for language %s: %s", language, err)

		classes: List[Dict[str, Any]] = []
		try:  # pylint: disable=too-many-nested-blocks
			with open(self._audit_hardware_config_file, encoding="utf-8") as hwc_file:
				exec(hwc_file.read())  # pylint: disable=exec-used

			for cls_idx, current_class_config in enumerate(OPSI_HARDWARE_CLASSES):  # pylint: disable=loop-global-usage
				opsi_class = current_class_config["Class"]["Opsi"]
				if current_class_config["Class"]["Type"] == "STRUCTURAL":
					if locale.get(opsi_class):
						OPSI_HARDWARE_CLASSES[cls_idx]["Class"]["UI"] = locale[opsi_class]  # pylint: disable=loop-global-usage
					else:
						logger.error("No translation for class '%s' found", opsi_class)
						OPSI_HARDWARE_CLASSES[cls_idx]["Class"]["UI"] = opsi_class  # pylint: disable=loop-global-usage

				for val_idx, current_value in enumerate(current_class_config["Values"]):
					opsi_property = current_value["Opsi"]
					try:  # pylint: disable=loop-try-except-usage
						OPSI_HARDWARE_CLASSES[cls_idx]["Values"][val_idx]["UI"] = locale[f"{opsi_class}.{opsi_property}"]  # pylint: disable=loop-global-usage,loop-invariant-statement
					except KeyError:
						pass

			for owc in OPSI_HARDWARE_CLASSES:  # pylint: disable=loop-global-usage
				try:  # pylint: disable=loop-try-except-usage
					if owc["Class"].get("Type") == "STRUCTURAL":
						logger.debug("Found STRUCTURAL hardware class '%s'", owc["Class"].get("Opsi"))
						ccopy = deepcopy(owc)
						if "Super" in ccopy["Class"]:
							inherit_from_super_classes(OPSI_HARDWARE_CLASSES, ccopy)  # pylint: disable=loop-global-usage
							del ccopy["Class"]["Super"]
						del ccopy["Class"]["Type"]

						# Fill up empty display names
						for val_idx, current_value in enumerate(ccopy.get("Values", [])):
							if not current_value.get("UI"):
								logger.warning(
									"No translation found for hardware audit configuration property '%s.%s' in %s",
									ccopy["Class"]["Opsi"],
									current_value["Opsi"],
									locale_file,
								)
								ccopy["Values"][val_idx]["UI"] = current_value["Opsi"]

						classes.append(ccopy)
				except Exception as err:  # pylint: disable=broad-except
					logger.error("Error in config file '%s': %s", self._audit_hardware_config_file, err)
		except Exception as err:  # pylint: disable=broad-except
			logger.warning("Failed to read audit hardware configuration from file '%s': %s", self._audit_hardware_config_file, err)

		return classes

	@rpc_method
	def auditHardware_insertObject(self: BackendProtocol, auditHardware: dict | AuditHardware) -> None:  # pylint: disable=invalid-name
		ace = self._get_ace("auditHardware_insertObject")
		for hardware_class, auh in self._audit_hardware_by_hardware_class(auditHardware).items():
			for obj in auh:
				self._mysql.insert_object(table=f"HARDWARE_DEVICE_{hardware_class}", obj=obj, ace=ace, create=True, set_null=True)

	@rpc_method
	def auditHardware_updateObject(self: BackendProtocol, auditHardware: dict | AuditHardware) -> None:  # pylint: disable=invalid-name
		ace = self._get_ace("auditHardware_updateObject")
		for hardware_class, auh in self._audit_hardware_by_hardware_class(auditHardware).items():
			for obj in auh:
				self._mysql.insert_object(table=f"HARDWARE_DEVICE_{hardware_class}", obj=obj, ace=ace, create=False, set_null=False)

	@rpc_method
	def auditHardware_createObjects(  # pylint: disable=invalid-name
		self: BackendProtocol, auditHardwares: List[dict] | List[AuditHardware] | dict | AuditHardware
	) -> None:
		ace = self._get_ace("auditHardware_createObjects")
		for hardware_class, auh in self._audit_hardware_by_hardware_class(auditHardwares).items():
			for obj in auh:
				self._mysql.insert_object(table=f"HARDWARE_DEVICE_{hardware_class}", obj=obj, ace=ace, create=True, set_null=True)

	@rpc_method
	def auditHardware_updateObjects(  # pylint: disable=invalid-name
		self: BackendProtocol, auditHardwares: List[dict] | List[AuditHardware] | dict | AuditHardware
	) -> None:
		ace = self._get_ace("auditHardware_updateObjects")
		for hardware_class, auh in self._audit_hardware_by_hardware_class(auditHardwares).items():
			for obj in auh:
				self._mysql.insert_object(table=f"HARDWARE_DEVICE_{hardware_class}", obj=obj, ace=ace, create=True, set_null=False)

	def _audit_hardware_get(  # pylint: disable=redefined-builtin,too-many-branches,too-many-locals,too-many-statements,too-many-arguments
		self: BackendProtocol,
		ace: List[RPCACE],
		return_hardware_ids: bool = False,
		return_type: Literal["object", "dict", "ident"] = "object",
		ident_type: IdentType = "str",
		attributes: List[str] = None, filter: Dict[str, Any] = None
	) -> List[Dict[str, Any]]:
		attributes = attributes or []
		filter = filter or {}
		hardware_classes = set()
		hardware_class = filter.get("hardwareClass")
		if hardware_class not in ([], None):
			for hwc in forceList(hardware_class):
				regex = re.compile(f"^{hwc.replace('*', '.*')}$")  # pylint: disable=dotted-import-in-loop
				for key in self._audit_hardware_config:
					if regex.search(key):
						hardware_classes.add(key)

			if not hardware_classes:
				return []

		if not hardware_classes:
			hardware_classes = set(self._audit_hardware_config)

		for unwanted_key in ("hardwareClass", "type"):
			try:  # pylint: disable=loop-try-except-usage
				del filter[unwanted_key]
			except KeyError:
				pass  # not there - everything okay.

		if return_hardware_ids and attributes and "hardware_id" not in attributes:
			attributes.append("hardware_id")

		results = []
		with self._mysql.session() as session:
			for hardware_class in hardware_classes:  # pylint: disable=too-many-nested-blocks
				class_filter = {}
				ident_attributes = []
				for attr, info in self._audit_hardware_config[hardware_class].items():  # pylint: disable=use-list-comprehension
					if info.get("Scope") == "g":
						ident_attributes.append(attr)
						if attr in filter:
							class_filter[attr] = filter[attr]
						if attributes and return_type != "dict" and attr not in attributes:  # pylint: disable=loop-invariant-statement
							attributes.append(attr)

				if attributes and return_hardware_ids and "hardware_id" not in attributes:
					attributes.append("hardware_id")

				if return_type == "ident":  # pylint: disable=loop-invariant-statement
					attributes = ident_attributes

				if not class_filter and filter:
					continue

				table = f"HARDWARE_DEVICE_{hardware_class}"
				columns = self._mysql.get_columns(tables=[table], ace=ace, attributes=attributes)
				if not return_hardware_ids and "hardware_id" in columns:
					del columns["hardware_id"]
				where, params = self._mysql.get_where(columns=columns, ace=ace, filter=class_filter)
				query = f"""SELECT {', '.join([f"{c['select']} AS `{a}`" for a, c in columns.items() if c['select']])} FROM `{table}` {where}"""  # pylint: disable=loop-invariant-statement
				for row in session.execute(query, params=params).fetchall():
					data = dict(row)
					if return_type == "object":  # pylint: disable=loop-invariant-statement
						results.append(AuditHardware(hardwareClass=hardware_class, **data))
					elif return_type == "ident":  # pylint: disable=loop-invariant-statement
						results.append(self._mysql.get_ident(data=data, ident_attributes=ident_attributes, ident_type=ident_type))
					else:
						results.append(data)
		return results

	@rpc_method
	def auditHardware_getObjects(self: BackendProtocol, attributes: List[str] = None, **filter: Any) -> List[AuditHardware]:  # pylint: disable=redefined-builtin,invalid-name
		ace = self._get_ace("auditHardware_getObjects")
		return self._audit_hardware_get(ace=ace, return_hardware_ids=False, return_type="object", attributes=attributes, filter=filter)

	@rpc_method
	def auditHardware_getHashes(self: BackendProtocol, attributes: List[str] = None, **filter: Any) -> List[dict]:  # pylint: disable=redefined-builtin,invalid-name
		ace = self._get_ace("auditHardware_getObjects")
		return self._audit_hardware_get(ace=ace, return_hardware_ids=False, return_type="dict", attributes=attributes, filter=filter)

	@rpc_method
	def auditHardware_getIdents(  # pylint: disable=invalid-name
		self: BackendProtocol, returnType: IdentType = "str", **filter: Any  # pylint: disable=redefined-builtin
	) -> List[str] | List[dict] | List[list] | List[tuple]:
		ace = self._get_ace("auditHardware_getObjects")
		return self._audit_hardware_get(ace=ace, return_hardware_ids=False, return_type="ident", ident_type=returnType, filter=filter)

	@rpc_method
	def auditHardware_deleteObjects(self: BackendProtocol, auditHardwares: List[dict] | List[AuditHardware] | dict | AuditHardware) -> None:  # pylint: disable=invalid-name
		ace = self._get_ace("auditHardware_deleteObjects")
		self._mysql.delete_objects(table="AUDIT_HARDWARE", object_type=AuditHardware, obj=auditHardwares, ace=ace)

	@rpc_method
	def auditHardware_delete(self: BackendProtocol, id: str) -> None:  # pylint: disable=redefined-builtin,invalid-name
		self.auditHardware_deleteObjects([{"id": id}])