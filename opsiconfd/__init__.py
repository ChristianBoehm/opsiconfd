# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
The opsi configuration service.
"""

__version__ = "4.2.0.236"

from typing import Optional, Dict, TYPE_CHECKING

from contextvars import Context, ContextVar

if TYPE_CHECKING:
	# Prevent circular import error
	from .session import OPSISession

contextvar_request_id: ContextVar[Optional[int]] = ContextVar("request_id", default=None)
contextvar_client_session: ContextVar[Optional["OPSISession"]] = ContextVar("client_session", default=None)
contextvar_client_address: ContextVar[Optional[str]] = ContextVar("client_address", default=None)
contextvar_server_timing: ContextVar[Dict[str, int]] = ContextVar("server_timing", default={})


def set_contextvars_from_contex(context: Context) -> None:
	if not context:
		return
	for var, val in context.items():
		if var.name in ("request_id", "client_session", "client_address", "server_timing"):
			globals()[f"contextvar_{var.name}"].set(val)
