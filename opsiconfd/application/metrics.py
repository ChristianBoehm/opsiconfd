# -*- coding: utf-8 -*-

# opsiconfd is part of the desktop management solution opsi http://www.opsi.org
# Copyright (c) 2020-2021 uib GmbH <info@uib.de>
# All rights reserved.
# License: AGPL-3.0
"""
metrics
"""

import copy
from datetime import datetime
from operator import itemgetter
from time import time
from typing import Any, Dict, List, Set

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel  # pylint: disable=no-name-in-module
from redis import ResponseError as RedisResponseError

from ..config import config
from ..grafana import (
	GRAFANA_DASHBOARD_TEMPLATE,
	GRAFANA_DATASOURCE_TEMPLATE,
	async_grafana_admin_session,
)
from ..logging import logger
from ..metrics.registry import MetricsRegistry
from ..metrics.statistics import get_time_bucket_duration
from ..utils import async_redis_client, ip_address_from_redis_key

# / should return 200 ok. Used for "Test connection" on the datasource config page.
# /search used by the find metric options on the query tab in panels.
# /query should return metrics based on input.
# /annotations should return annotations.


grafana_metrics_router = APIRouter()


def metrics_setup(app: FastAPI) -> None:
	app.include_router(grafana_metrics_router, prefix="/metrics/grafana")


async def get_workers() -> List[Dict[str, str | int]]:
	redis = await async_redis_client()
	workers = []
	async for redis_key in redis.scan_iter("opsiconfd:worker_registry:*"):
		redis_key = redis_key.decode("utf-8")
		workers.append({"node_name": redis_key.split(":")[-2], "worker_num": int(redis_key.split(":")[-1])})
	workers.sort(key=itemgetter("node_name", "worker_num"))
	return workers


async def get_nodes() -> Set[str]:
	return {str(worker["node_name"]) for worker in await get_workers()}


async def get_clients(metric_id: str) -> List[Dict[str, str]]:
	redis = await async_redis_client()
	clients = []
	async for redis_key in redis.scan_iter(f"opsiconfd:stats:{metric_id}:*"):
		redis_key = redis_key.decode("utf-8")
		clients.append({"client_addr": ip_address_from_redis_key(redis_key.split(":")[-1])})
	clients.sort(key=itemgetter("client_addr"))
	return clients


@grafana_metrics_router.get("/")
async def grafana_index() -> None:
	# should return 200 ok. Used for "Test connection" on the datasource config page.
	return None


async def grafana_dashboard_config() -> Dict[str, Any]:  # pylint: disable=too-many-locals
	workers = await get_workers()
	nodes = await get_nodes()
	clients = await get_clients("client:sum_http_request_number")

	dashboard = copy.deepcopy(GRAFANA_DASHBOARD_TEMPLATE)
	panels = []
	pos_x = 0
	pos_y = 0
	for panel_id, metric in enumerate(MetricsRegistry().get_metrics()):
		if not metric.grafana_config:
			continue
		panel_id += 1
		panel = metric.grafana_config.get_panel(panel_id=panel_id, pos_x=pos_x, pos_y=pos_y)
		if metric.subject == "worker":
			for i, worker in enumerate(workers):  # pylint: disable=use-list-copy
				panel["targets"].append(
					{
						"refId": chr(65 + i),
						"target": metric.get_name(node_name=worker["node_name"], worker_num=worker["worker_num"]),
						"type": "timeserie",
					}
				)
		elif metric.subject == "node":
			for i, node_name in enumerate(nodes):  # pylint: disable=use-list-copy
				panel["targets"].append({"refId": chr(65 + i), "target": metric.get_name(node_name=node_name), "type": "timeserie"})
		elif metric.subject == "client":
			for i, client in enumerate(clients):  # pylint: disable=use-list-copy
				panel["targets"].append(
					{"refId": chr(65 + i), "target": metric.get_name(client_addr=client["client_addr"]), "type": "timeserie"}
				)
		panels.append(panel)
		pos_x += panel["gridPos"]["w"]
		if pos_x >= 24:
			pos_x = 0
			pos_y += panel["gridPos"]["h"]

	dashboard["panels"] = panels
	return dashboard


async def create_grafana_datasource() -> None:
	json = GRAFANA_DATASOURCE_TEMPLATE
	json["url"] = f"{config.grafana_data_source_url}/metrics/grafana/"
	async with async_grafana_admin_session() as (base_url, session):
		resp = await session.get(f"{base_url}/api/datasources/name/{json['name']}")
		if resp.status == 200:
			_id = (await resp.json())["id"]
			resp = await session.put(f"{base_url}/api/datasources/{_id}", json=json)
		else:
			resp = await session.post(f"{base_url}/api/datasources", json=json)

		if resp.status == 200:
			json = {"folderId": 0, "overwrite": True, "dashboard": await grafana_dashboard_config()}
			resp = await session.post(f"{base_url}/api/dashboards/db", json=json)
		else:
			logger.error("Failed to create grafana datasource: %s - %s", resp.status, await resp.text())


@grafana_metrics_router.get("/search")
@grafana_metrics_router.post("/search")
async def grafana_search() -> List[str]:
	workers = await get_workers()
	nodes = await get_nodes()
	clients = await get_clients("client:sum_http_request_number")

	names = []
	for metric in MetricsRegistry().get_metrics():
		if metric.subject == "worker":
			names += [metric.get_name(**worker) for worker in workers]  # pylint: disable=loop-invariant-statement
		elif metric.subject == "node":
			names += [metric.get_name(node_name=node_name) for node_name in nodes]  # pylint: disable=loop-invariant-statement
		elif metric.subject == "client":
			names += [metric.get_name(**client) for client in clients]  # pylint: disable=loop-invariant-statement
		else:
			names.append(metric.get_name())
	return sorted(names)


class GrafanaQueryTargetRange(BaseModel):  # pylint: disable=too-few-public-methods
	from_: str
	to: str
	raw: dict

	class Config:  # pylint: disable=too-few-public-methods
		fields = {"from_": "from"}


class GrafanaQueryTarget(BaseModel):  # pylint: disable=too-few-public-methods
	type: str
	target: str
	refId: str


class GrafanaQuery(BaseModel):  # pylint: disable=too-few-public-methods
	app: str
	range: GrafanaQueryTargetRange
	intervalMs: int
	timezone: str
	targets: List[GrafanaQueryTarget]


def align_timestamp(timestamp: int | float) -> int:
	"""Align timestamp to 5 second intervals, needed for stacking in grafana"""
	return 5000 * round(int(timestamp) / 5000)


@grafana_metrics_router.get("/query")
@grafana_metrics_router.post("/query")
async def grafana_query(query: GrafanaQuery) -> List[Dict[str, Any]]:  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
	logger.trace("Grafana query: %s", query)
	results = []
	redis = await async_redis_client()

	# Unix timestamp (UTC) in milliseconds
	from_ms = int(datetime.fromisoformat(query.range.from_.replace("Z", "+00:00")).timestamp()) * 1000
	to_ms = int(datetime.fromisoformat(query.range.to.replace("Z", "+00:00")).timestamp()) * 1000
	time_range_ms = to_ms - from_ms
	query_bucket_duration_ms = round(query.intervalMs)
	sorted_downsampling = {}

	timestamp_now = round(time() * 1000)
	for target in query.targets:
		if target.type != "timeserie":
			logger.warning("Unhandled target type: %s", target.type)
			continue

		bucket_duration_ms = query_bucket_duration_ms

		try:  # pylint: disable=loop-try-except-usage
			metric = MetricsRegistry().get_metric_by_name(target.target)
			metric_vars = metric.get_vars_by_name(target.target)
		except ValueError:
			try:  # pylint: disable=loop-try-except-usage
				metric = MetricsRegistry().get_metric_by_redis_key(target.target)
				metric_vars = metric.get_vars_by_redis_key(target.target)
			except ValueError as err:
				logger.debug(err)
				continue

		redis_key = metric.get_redis_key(**metric_vars)
		redis_key_extension = None
		ts_max_interval_ms = metric.retention

		if time_range_ms > ts_max_interval_ms and metric.downsampling:
			# Requested time range is bigger than the metric retention time
			# Get the best matching downsampling rule
			# downsampling: [<ts_key_extension>, <retention_time_in_ms>, <aggregation>]
			# e.g. ["minute", 24 * 3600 * 1000, "avg"]
			if metric.id not in sorted_downsampling:
				sorted_downsampling[metric.id] = sorted(metric.downsampling, key=lambda dsr: dsr[1])
			for ds_rule in sorted_downsampling[metric.id]:
				if time_range_ms <= ds_rule[1]:
					redis_key_extension = ds_rule[0]
					ts_max_interval_ms = ds_rule[1]
					break

		# Get timestamp and subtract the retention time of the metric
		oldest_possible_timestamp = timestamp_now - ts_max_interval_ms
		# If there are no timestamps in the interval and the metric has downsampling
		# we need to use the next "higher" time bucket: minute -> hour -> day
		if from_ms - oldest_possible_timestamp + 5000 < 0 and metric.downsampling:
			if metric.id not in sorted_downsampling:
				sorted_downsampling[metric.id] = sorted(metric.downsampling, key=lambda dsr: dsr[1])
			for ds_rule in sorted_downsampling[metric.id]:
				oldest_possible_timestamp = timestamp_now - ts_max_interval_ms
				if (from_ms - oldest_possible_timestamp + 5000) >= 0:
					break
				redis_key_extension = ds_rule[0]
				ts_max_interval_ms = ds_rule[1]  # ts_max_interval_ms: retention time of downsampling rule

		if redis_key_extension:
			bucket_duration_ms = get_time_bucket_duration(redis_key_extension)
			redis_key = f"{redis_key}:{redis_key_extension}"
		# https://redis.io/commands/ts.range/
		# Aggregate results into time buckets, duration of each bucket in milliseconds is bucket_duration_ms
		cmd = ("TS.RANGE", redis_key, from_ms, to_ms, "AGGREGATION", "avg", bucket_duration_ms)
		try:  # pylint: disable=loop-try-except-usage
			rows = await redis.execute_command(*cmd)  # type: ignore[no-untyped-call]
		except RedisResponseError as err:  # pylint: disable=dotted-import-in-loop
			logger.warning("%s %s", cmd, err)
			rows = []  # pylint: disable=use-tuple-over-list

		res = {"target": target.target, "datapoints": []}
		if metric.time_related and metric.aggregation == "sum":
			# Time series data is stored aggregated in 5 second intervals
			res["datapoints"] = [[float(r[1]) / 5.0, align_timestamp(r[0])] for r in rows]  # type: ignore[misc] # pylint: disable=loop-invariant-statement
		else:
			res["datapoints"] = [[float(r[1]), align_timestamp(r[0])] for r in rows]  # type: ignore[misc] # pylint: disable=loop-invariant-statement
		logger.trace("Grafana query result: %s", res)
		results.append(res)
	return results
