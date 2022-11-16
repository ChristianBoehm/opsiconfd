# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
rpc methods dynamic depot
"""
from typing import Protocol

from . import rpc_method

SHOW_DEPOT_INFO_FUNCTION = """
	def showDepotInfo():
		logger.info("Choosing depot from list of depots:")
		logger.info("   Master depot: %s", masterDepot)
		for alternativeDepot in alternativeDepots:
			logger.info("   Alternative depot: %s", alternativeDepot)
"""

GET_DEPOT_WITH_LOWEST_LATENCY_FUNCTION = '''
	def getDepotWithLowestLatency(latency):
		"""
		Given a dict with depot as key and latency as value it will \
return the depot with the lowest latency.

		Will return None if no depot can be determined.
		"""
		selectedDepot = None
		if latency:
			minValue = 1000
			for (depot, value) in latency.items():
				if value < minValue:
					minValue = value
					selectedDepot = depot
			logger.notice("Depot with lowest latency: %s (%0.3f ms)", selectedDepot, minValue*1000)

		return selectedDepot
'''

GET_LATENCY_INFORMATION_FUNCTION = '''
	def getLatencyInformation(depots):
		"""
		Pings the given depots and returns the latency information in \
a dict with depot as key and the latency as value.

		Depots that can't be reached in time will not be included.
		"""
		from OPSI.Util.Ping import ping
		from urllib.parse import urlparse

		latency = {}
		for depot in depots:
			if not depot.repositoryRemoteUrl:
				logger.info("Skipping {depot} because repositoryRemoteUrl is missing.", depot)
				continue

			try:
				host = urlparse(depot.repositoryRemoteUrl).hostname
				# To increase the timeout (in seconds) for the ping you
				# can implement it in the following way:
				#  depotLatency = ping(host, timeout=5)
				depotLatency = ping(host)

				if depotLatency is None:
					logger.info("Ping to depot %s timed out.", depot)
				else:
					logger.info("Latency of depot %s: %0.3f ms", depot, depotLatency * 1000)
					latency[depot] = depotLatency
			except Exception as e:
				logger.warning(e)

		return latency
'''

DEPOT_SELECTION_ALGORITHM_BY_MASTER_DEPOT_AND_LATENCY = f"""
def selectDepot(clientConfig, masterDepot, alternativeDepots=[]):
	{GET_LATENCY_INFORMATION_FUNCTION}
	{GET_DEPOT_WITH_LOWEST_LATENCY_FUNCTION}
	{SHOW_DEPOT_INFO_FUNCTION}

	showDepotInfo()

	if alternativeDepots:
		from collections import defaultdict

		# Mapping of depots to its master.
		# key: Master depot
		# value: All slave depots + master
		depotsByMaster = defaultdict(list)

		allDepots = [masterDepot] + alternativeDepots

		for depot in allDepots:
			if depot.masterDepotId:
				depotsByMaster[depot.masterDepotId].append(depot)
			else:
				depotsByMaster[depot.id].append(depot)

		depotsWithLatency = getLatencyInformation(depotsByMaster[masterDepot.id])
		depotWithLowestLatency = getDepotWithLowestLatency(depotsWithLatency)

		if not depotWithLowestLatency:
			logger.info('No depot with lowest latency. Falling back to master depot.')
			depotWithLowestLatency = masterDepot

		return depotWithLowestLatency

	return masterDepot
"""

DEPOT_SELECTION_ALGORITHM_BY_LATENCY = f"""
def selectDepot(clientConfig, masterDepot, alternativeDepots=[]):
	{GET_LATENCY_INFORMATION_FUNCTION}
	{GET_DEPOT_WITH_LOWEST_LATENCY_FUNCTION}
	{SHOW_DEPOT_INFO_FUNCTION}

	showDepotInfo()

	selectedDepot = masterDepot
	if alternativeDepots:
		depotsWithLatency = getLatencyInformation([masterDepot] + alternativeDepots)
		selectedDepot = getDepotWithLowestLatency(depotsWithLatency)

		if not selectedDepot:
			logger.info('No depot with lowest latency. Falling back to master depot.')
			selectedDepot = masterDepot

	return selectedDepot
"""

DEPOT_SELECTION_ALGORITHM_BY_RANDOM = f"""
def selectDepot(clientConfig, masterDepot, alternativeDepots=[]):
	{SHOW_DEPOT_INFO_FUNCTION}

	showDepotInfo()

	import random

	allDepots = [masterDepot]
	allDepots.extend(alternativeDepots)
	return random.choice(allDepots)
"""


DEPOT_SELECTION_ALGORITHM_BY_NETWORK_ADDRESS = f"""
def selectDepot(clientConfig, masterDepot, alternativeDepots=[]):
	{SHOW_DEPOT_INFO_FUNCTION}

	showDepotInfo()

	selectedDepot = masterDepot
	if alternativeDepots:
		from OPSI.Util import ipAddressInNetwork

		depots = [masterDepot]
		depots.extend(alternativeDepots)
		for depot in depots:
			if not depot.networkAddress:
				logger.warning("Network address of depot '%s' not known", depot)
				continue

			if ipAddressInNetwork(clientConfig['ipAddress'], depot.networkAddress):
				logger.notice("Choosing depot with networkAddress %s for ip %s", depot.networkAddress, clientConfig['ipAddress'])
				selectedDepot = depot
				break
			else:
				logger.info("IP %s does not match networkAddress %s of depot %s", clientConfig['ipAddress'], depot.networkAddress, depot)

	return selectedDepot
"""

DEPOT_SELECTION_ALGORITHM_BY_NETWORK_ADDRESS_BEST_MATCH = f"""
def selectDepot(clientConfig, masterDepot, alternativeDepots=[]):
	{SHOW_DEPOT_INFO_FUNCTION}

	showDepotInfo()
	logger.debug("Alternative Depots are: %s", alternativeDepots)
	selectedDepot = masterDepot
	if alternativeDepots:
		from OPSI.Util import ipAddressInNetwork
		import ipaddress

		depots = [masterDepot]
		depots.extend(alternativeDepots)
		logger.debug("All considered Depots are: %s",depots)
		sorted_depots = sorted(depots, key=lambda depot: ipaddress.ip_network(depot.networkAddress), reverse=True)
		logger.debug("Sorted depots: %s", sorted_depots)
		for depot in sorted_depots:
			logger.debug("Considering Depot %s with NetworkAddress %s", depot, depot.networkAddress)
			if not depot.networkAddress:
				logger.warning("Network address of depot '%s' not known", depot)
				continue

			if ipAddressInNetwork(clientConfig['ipAddress'], depot.networkAddress):
				logger.notice("Choosing depot with networkAddress %s for ip %s", depot.networkAddress, clientConfig['ipAddress'])
				selectedDepot = depot
				break
			else:
				logger.info("IP %s does not match networkAddress %s of depot %s", clientConfig['ipAddress'], depot.networkAddress, depot)

	return selectedDepot
"""


class RPCExtDynamicDepotMixin(Protocol):
	@rpc_method
	def getDepotSelectionAlgorithmByMasterDepotAndLatency(self) -> str:  # pylint: disable=invalid-name
		return DEPOT_SELECTION_ALGORITHM_BY_MASTER_DEPOT_AND_LATENCY

	@rpc_method
	def getDepotSelectionAlgorithmByLatency(self) -> str:  # pylint: disable=invalid-name
		return DEPOT_SELECTION_ALGORITHM_BY_LATENCY

	@rpc_method
	def getDepotSelectionAlgorithmByRandom(self) -> str:  # pylint: disable=invalid-name
		return DEPOT_SELECTION_ALGORITHM_BY_RANDOM

	@rpc_method
	def getDepotSelectionAlgorithmByNetworkAddress(self) -> str:  # pylint: disable=invalid-name
		return DEPOT_SELECTION_ALGORITHM_BY_NETWORK_ADDRESS

	@rpc_method
	def getDepotSelectionAlgorithmByNetworkAddressBestMatch(self) -> str:  # pylint: disable=invalid-name
		return DEPOT_SELECTION_ALGORITHM_BY_NETWORK_ADDRESS_BEST_MATCH

	@rpc_method
	def getDepotSelectionAlgorithm(self) -> str:  # pylint: disable=invalid-name
		"""Returns the selected depot selection algorythm."""
		# return self.getDepotSelectionAlgorithmByMasterDepotAndLatency()
		# return self.getDepotSelectionAlgorithmByLatency()
		return self.getDepotSelectionAlgorithmByNetworkAddress()
		# return self.getDepotSelectionAlgorithmByNetworkAddressBestMatch
		# return self.getDepotSelectionAlgorithmByRandom()