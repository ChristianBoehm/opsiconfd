#! /usr/bin/env python
# -*- coding: utf-8 -*-

# This file is part of python-opsi.
# Copyright (C) 2010-2016 uib GmbH <info@uib.de>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
opsi configuration daemon (opsiconfd)


:copyright:uib GmbH <info@uib.de>
:author: Jan Schneider <j.schneider@uib.de>
:author: Erol Ueluekmen <e.ueluekmen@uib.de>
:author: Niko Wenselowski <n.wenselowski@uib.de>
:license: GNU Affero General Public License version 3
"""

from __future__ import print_function

import getopt
import os
import sys
import threading
import time
import socket
try:
	import dbus
except ImportError:
	dbus = None

try:
	import avahi
except ImportError:
	avahi = None

from contextlib import contextmanager
from datetime import datetime
from signal import signal, SIGHUP, SIGINT, SIGTERM
from ctypes import CDLL

from twisted.internet import epollreactor
epollreactor.install()
from twisted.internet import reactor

from OPSI.Application import Application
from OPSI.Logger import Logger, LOG_NONE, LOG_WARNING, LOG_NOTICE
from OPSI.web2 import server
from OPSI.Util import getfqdn, removeUnit
from OPSI.Util.File import IniFile
from OPSI.Util.AMP import OpsiProcessProtocolFactory
from OPSI.Util.MessageBus import MessageBusServer
from OPSI.Types import (forceBool, forceFilename, forceHostId, forceInt,
						forceNetworkAddress, forceUnicode)
from OPSI.System import which, execute
from OPSI.System.Posix import daemonize
from OPSI.Backend.BackendManager import BackendManager
from OPSI.Service import SSLContext, OpsiService

from resources import ResourceRoot, ResourceOpsiconfdJsonRpc, ResourceOpsiconfdJsonInterface, ResourceOpsiconfdDAV, ResourceOpsiconfdConfigedJNLP
from info import ResourceOpsiconfdInfo
from statistics import Statistics
from monitoring import ResourceOpsiconfdMonitoring
from session import OpsiconfdSessionHandler
from omb import MessageBusService, OpsiconfdHTTPFactory, OpsiconfdHTTPChannel

__version__ = "4.0.7.6"

logger = Logger()


class ZeroconfService(object):

	def __init__(self, name, port, serviceType="_opsiconfd._tcp", domain="", host="", text=""):
		self._name = name
		self._port = port
		self._serviceType = serviceType
		self._domain = domain
		self._host = host
		self._text = text
		self._group = None

	def publish(self):
		if not dbus or not avahi:
			logger.warning(u"Failed to publish ZeroconfService: avahi/dbus module missing")
			return

		bus = dbus.SystemBus()
		srv = dbus.Interface(
			bus.get_object(
				avahi.DBUS_NAME,
				avahi.DBUS_PATH_SERVER
			),
			avahi.DBUS_INTERFACE_SERVER
		)

		g = dbus.Interface(
			bus.get_object(
				avahi.DBUS_NAME,
				srv.EntryGroupNew()
			),
			avahi.DBUS_INTERFACE_ENTRY_GROUP
		)

		g.AddService(avahi.IF_UNSPEC, avahi.PROTO_UNSPEC, dbus.UInt32(0),
				self._name, self._serviceType, self._domain, self._host,
				dbus.UInt16(self._port), self._text)

		g.Commit()
		self._group = g

	def unpublish(self):
		if self._group:
			self._group.Reset()


class Opsiconfd(OpsiService):
	def __init__(self, config):
		self.config = {}
		self._running = False
		self._backend = None
		self._root = None
		self._site = None
		self._httpPort = None
		self._httpsPort = None
		self._sessionHandler = None
		self._statistics = None
		self._zeroconfService = None
		self._messageBusServer = None
		self._messageBusService = None
		self._socket = None
		self._debugShell = None

		self.authFailureCount = {}

		self.setConfig(config)
		self._setOpsiLogging()
		self._setTwistedLogging()

		logger.comment("""
==================================================================
=             opsi configuration service starting                =
==================================================================""")

	def getInterface(self):
		return self.getBackend().backend_getInterface()

	def setConfig(self, config):
		logger.notice(u"Got new config")
		oldStarttime = self.config.get('startTime')
		self.config = config

		if 'startTime' not in self.config:
			self.config['startTime'] = oldStarttime or datetime.now()

	def isRunning(self):
		return self._running

	def getBackend(self):
		return self._backend

	def statistics(self):
		return self._statistics

	def _getSessionHandler(self):
		return self._sessionHandler

	def stop(self):
		logger.notice(u"Stopping opsiconfd main thread")
		try:
			if self._zeroconfService:
				self._zeroconfService.unpublish()
			if self._messageBusService:
				self._messageBusService.stop()
			if self._messageBusServer:
				self._messageBusServer.stop(stopReactor=False)
				self._messageBusServer.join(5)
			if self._httpPort:
				self._httpPort.stopListening()
			if self._httpsPort:
				self._httpsPort.stopListening()
			if self._sessionHandler:
				self._sessionHandler.cleanup()
			if self._debugShell:
				self._debugShell.close()
			if self._backend:
				try:
					self._backend.backend_exit()
				except Exception:
					pass
			if self._socket:
				self._socket.stopListening()

			self._running = False
		except Exception as e:
			logger.error(u"Failed to stop opsiconfd cleanly.")
			logger.logException(e)

	def reload(self):
		logger.notice(u"Reloading opsiconfd")
		self.authFailureCount = {}

		self._setOpsiLogging()
		self._createBackendInstance()
		if self._sessionHandler:
			self._sessionHandler.cleanup()
		self._createSessionHandler()
		self._logStatistics()
		self._createStatistics()
		self._createSite()

		if self._httpPort:
			deferred = self._httpPort.stopListening()
			if deferred:
				deferred.addCallback(self._startListeningHTTP)
		else:
			self._startListeningHTTP()

		if self._httpsPort:
			deferred = self._httpsPort.stopListening()
			if deferred:
				deferred.addCallback(self._startListeningHTTPS)
		else:
			self._startListeningHTTPS()

	def _createStatistics(self):
		self._statistics = Statistics(self)

	def _createSessionHandler(self):
		self._sessionHandler = OpsiconfdSessionHandler(self)

	def _setOpsiLogging(self):
		# Set logging options
		logger.logWarnings()
		self.config['machineLogs'] = False
		if self.config['logFile']:
			if '%m' in self.config['logFile']:
				self.config['machineLogs'] = True
			logger.setLogFile(self.config['logFile'].replace('%m', 'opsiconfd'))

		if self.config['logFormat']:
			logger.setLogFormat(self.config['logFormat'])
		logger.setFileLevel(self.config['logLevel'])


	def _setTwistedLogging(self):
		logger.startTwistedLogging()

	def _createBackendInstance(self):
		logger.info(u"Creating backend instance")
		self._backend = BackendManager(
			dispatchConfigFile=self.config['dispatchConfigFile'],
			backendConfigDir=self.config['backendConfigDir'],
			extensionConfigDir=self.config['extensionConfigDir'],
			hostControlBackend=True,
			hostControlSafeBackend=True,
			depotBackend=bool(self.config['depotId']),
			messageBusNotifier=bool(self.config['messageBus']),
			startReactor=False,
			maxLogSize=self.config['maxlogsize'],
		)
		OpsiconfdHTTPChannel.backend = self._backend

	def _createSite(self):
		logger.info(u"Creating site")
		del self._site
		del self._root

		if self.config['staticDirectories'].get('/'):
			if not os.path.isdir(self.config['staticDirectories']['/']['path']):
				logger.error(u"Cannot add static content '/': directory '%s' does not exist." \
					% self.config['staticDirectories']['/']['path'])
			else:
				self._root = ResourceOpsiconfdDAV(self,
					path=self.config['staticDirectories']['/']['path'],
					readOnly=True,
					authRequired='noauth' not in self.config['staticDirectories']['/']['options']
				)
				logger.notice(u"Added static content '/' which points to directory '%s'" \
					% self.config['staticDirectories']['/']['path'])

		if not hasattr(self, '_root'):
			self._root = ResourceRoot()

		self._root.putChild('rrd', ResourceOpsiconfdDAV(self, path=self.config['rrdDir'], readOnly=True))
		self._root.putChild('rpc', ResourceOpsiconfdJsonRpc(self))
		self._root.putChild('interface', ResourceOpsiconfdJsonInterface(self))
		self._root.putChild('info', ResourceOpsiconfdInfo(self))
		self._root.putChild('monitoring', ResourceOpsiconfdMonitoring(self))
		# self._root.putChild('doc',             ResourceOpsiDocumentation())
		self._root.putChild('configed.jnlp', ResourceOpsiconfdConfigedJNLP())

		hosts = self._backend.host_getObjects(type='OpsiDepotserver', id=self.config['fqdn'])
		if hosts:
			depot = hosts[0]
			self.config['depotId'] = depot.getId()
			logger.notice(u"Running on depot server '%s', exporting repository directory" % self.config['depotId'])
			if not depot.getRepositoryLocalUrl():
				raise Exception(u"Repository local url for depot '%s' not found" % self.config['depotId'])
			if not depot.getRepositoryLocalUrl().startswith('file:///'):
				raise Exception(u"Repository local url '%s' not allowed" % depot.getRepositoryLocalUrl())
			path = depot.getRepositoryLocalUrl()[7:]
			if not os.path.isdir(path):
				raise Exception(u"Cannot add webdav content 'repository': directory '%s' does not exist." % path)
			if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
				raise Exception(u"Cannot add webdav content 'repository': permissions on directory '%s' not sufficient." % path)

			self.config['staticDirectories']['repository'] = {"path": path, "options": []}

			logger.notice(u"Running on depot server '%s', exporting depot directory" % self.config['depotId'])
			if not depot.getDepotLocalUrl():
				raise Exception(u"Repository local url for depot '%s' not found" % self.config['depotId'])
			if not depot.getDepotLocalUrl().startswith('file:///'):
				raise Exception(u"Repository local url '%s' not allowed" % depot.getDepotLocalUrl())
			path = depot.getDepotLocalUrl()[7:]
			if not os.path.isdir(path):
				raise Exception(u"Cannot add webdav content 'depot': directory '%s' does not exist." % path)
			if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
				raise Exception(u"Cannot add webdav content 'depot': permissions on directory '%s' not sufficient." % path)

			self.config['staticDirectories']['depot'] = {"path": path, "options": []}

		for (name, pathAndOptions) in self.config['staticDirectories'].items():
			path = pathAndOptions['path']
			options = pathAndOptions['options']
			if name == '/':
				continue

			if name in ('rpc', 'interface', 'info'):
				logger.error(u"Cannot add static content '%s', already in use!" % name)
				continue

			if not os.path.isdir(path):
				logger.error(u"Cannot add static content '%s': directory '%s' does not exist." % (name, path))
				continue

			readOnly = True
			if name in ('repository', 'depot'):
				readOnly = False

			authRequired = 'noauth' not in options
			self._root.putChild(name, ResourceOpsiconfdDAV(self, path, readOnly=readOnly, authRequired=authRequired))
			logger.notice(u"Added webdav content '%s' which points to directory '%s' %s" % (name, path, tuple(options)))

		self._site = server.Site(self._root)

	def _startListening(self):
		logger.info(u"Creating ports")
		self._startListeningHTTP()
		self._startListeningHTTPS()

	def _startListeningHTTP(self, dontcare=None):
		if self.config['httpPort'] <= 0:
			self._httpPort = None
			return

		if self.config['interface'] == '0.0.0.0':
			self._httpPort = reactor.listenTCP(
				self.config['httpPort'],
				OpsiconfdHTTPFactory(self._site)
			)
		else:
			self._httpPort = reactor.listenTCP(
				self.config['httpPort'],
				OpsiconfdHTTPFactory(self._site),
				interface=self.config['interface']
			)

		logger.notice(u"Accepting HTTP requests on %s:%s" % (self.config['interface'], self.config['httpPort']))

	def _startListeningHTTPS(self, dontcare=None):
		if self.config['httpsPort'] <= 0:
			self._httpsPort = None
			return

		sslContext = SSLContext(
			self.config['sslServerKeyFile'],
			self.config['sslServerCertFile'],
			acceptedCiphers=self.config['acceptedCiphers']
		)

		if self.config['interface'] == '0.0.0.0':
			self._httpsPort = reactor.listenSSL(
				self.config['httpsPort'],
				OpsiconfdHTTPFactory(self._site),
				sslContext
			)
		else:
			self._httpsPort = reactor.listenSSL(
				self.config['httpsPort'],
				OpsiconfdHTTPFactory(self._site),
				sslContext,
				interface=self.config['interface']
			)

		logger.notice(u"Accepting HTTPS requests on %s:%s" % (self.config['interface'], self.config['httpsPort']))

	def _publish(self):
		port = 0
		name = "opsi configuration daemon"

		if self._httpsPort is not None:
			port = self.config['httpsPort']
		elif self._httpPort is not None:
			port = self.config['httpPort']
		else:
			logger.notice(u"No open port found, there is nothing to publish")
			return

		logger.notice(u"Publishing opsiconfd over zeroconf as '%s' on '%s'" % (name, port))
		try:
			self._zeroconfService = ZeroconfService(name=name, port=port)
			self._zeroconfService.publish()
		except Exception as e:
			logger.error(u"Failed to publish opsiconfd over zeroconf: %s" % e)

	def _startListeningSocket(self):
		socket = self.config["socket"]

		if not os.path.exists(os.path.dirname(socket)):
			os.makedirs(os.path.dirname(socket))
		elif os.path.exists(socket):
			# If the daemon dies without closing the socket properly
			# this is necessary to clean up the remains.
			os.unlink(socket)

		logger.notice("Opening socket %s for interprocess communication." % socket)
		self._socket = reactor.listenUNIX(socket, OpsiProcessProtocolFactory(self))

	def _startListeningShell(self):
		from OPSI.Util.Debug import DebugShell

		ns = globals()
		ns.update({"opsiconfd": self})

		self._debugShell = DebugShell(self, self._backend, namespace=ns)

		logger.notice(u"Opening debug shell.")
		self._debugShell.open()

	def _startMessageBusServer(self):
		self._messageBusServer = MessageBusServer()
		self._messageBusServer.start(startReactor=False)
		self._messageBusService = MessageBusService()
		OpsiconfdHTTPChannel.messageBusService = self._messageBusService
		self._messageBusService.start()

	def run(self):
		@contextmanager
		def collectStatistics():
			self._createStatistics()
			try:
				yield
			finally:
				self._logStatistics()

		self._running = True
		logger.notice(u"Starting opsiconfd main thread")
		try:
			reactor.addSystemEventTrigger("before", "shutdown", self.stop)
			if self.config['messageBus']:
				self._startMessageBusServer()
			self._startListeningSocket()
			self._createBackendInstance()
			self._createSessionHandler()
			with collectStatistics():
				self._createSite()
				self._startListening()
				if self.config['loadbalancing']:
					logger.debug(u"Loadbalancing is activated, zeroconf-publishing is deactivated")
				else:
					self._publish()

				if self.config["debug"]:
					self._startListeningShell()

				if not reactor.running:
					reactor.run(installSignalHandlers=1)
		except Exception as e:
			logger.logException(e)
			self.stop()

		logger.notice(u"Opsiconfd main thread exiting...")
		self._running = False

	def _logStatistics(self):
		stats = self._statistics
		logger.debug("Current system status: {0}".format(stats.getStatistics()))
		logger.notice("Uptime: {0}".format(str(datetime.now() - self.config['startTime'])))

		logger.notice("Statistics: ")
		logger.notice("Methodname\tCallcount\tAverage processing duration")
		callStatistics = stats.getRPCCallCounts()
		callAverages = stats.getRPCAverageDurations()
		for key in sorted(callStatistics):
			logger.notice("{name}\t{count}\t{average}".format(name=key, count=callStatistics[key], average='{0:0.3f}s'.format(callAverages[key])))


class OpsiconfdInit(Application):
	def __init__(self):
		logger.debug(u"OpsiconfdInit")
		# Set umask
		os.umask(0077)
		self._pid = 0
		self.config = {}

		try:
			(self.opts, self.args) = getopt.getopt(sys.argv[1:], "vc:f:l:p:P:i:D", ["profile=", "profiler=", "debug"])
		except getopt.GetoptError:
			self.usage()
			sys.exit(1)

		self.setDefaultConfig()
		# Process command line arguments
		for (opt, arg) in self.opts:
			if opt == "-c":
				self.config['configFile'] = forceFilename(arg)
			elif opt == "-v":
				print(u"opsiconfd version {0}".format(__version__))
				sys.exit(0)
		self.readConfigFile()
		self.setCommandlineConfig()

		if self.config['daemon']:
			logger.setConsoleLevel(LOG_NONE)
		else:
			logger.setConsoleLevel(self.config['logLevel'])
			logger.setConsoleColor(True)

		Application.__init__(self, self.config)

		self.run()

	def _getApplication(self):
		self._opsiconfd = Opsiconfd(self.config)
		return self._opsiconfd

	def setup(self):
		# Call signalHandler on signal SIGHUP, SIGTERM, SIGINT
		signal(SIGHUP, self.signalHandler)

		if self.config['daemon']:
			daemonize()
			time.sleep(2)

		self.createPidFile()
		libc = CDLL("libc.so.6")
		libc.prctl(15, 'opsiconfd', 0, 0, 0)

	def shutdown(self):
		self.removePidFile()

	def setDefaultConfig(self):
		self.config = {
			'pidFile': u'/var/run/opsiconfd/opsiconfd.pid',
			'configFile': u'/etc/opsi/opsiconfd.conf',
			'daemon': False,
			'logLevel': LOG_NOTICE,
			'logFile': u'/var/log/opsi/opsiconfd/opsiconfd.log',
			'logFormat': u'[%l] [%D] %M (%F|%N)',
			'symlinkLogs': False,
			'maxlogsize': 5000000,
			'httpPort': 0,
			'httpsPort': 4447,
			'interface': u'0.0.0.0',
			'adminNetworks': [u'0.0.0.0/0'],
			'maxExecutionStatisticValues': 250,
			'sslServerCertFile': u'/etc/opsi/opsiconfd.pem',
			'sslServerKeyFile': u'/etc/opsi/opsiconfd.pem',
			'acceptedCiphers': '',
			'sessionName': u'OPSISID',
			'maxSessionsPerIp': 25,
			'maxAuthenticationFailures': 5,
			'resolveVerifyIp': False,
			'sessionMaxInactiveInterval': 120,
			'updateIpAddress': False,
			'staticDirectories': {},
			'depotId': None,
			'fqdn': forceHostId(getfqdn(conf='/etc/opsi/global.conf')),
			'ipAddress': socket.gethostbyname(socket.gethostname()),
			'rrdDir': u'/var/lib/opsiconfd/rrd',
			'backendConfigDir': u'/etc/opsi/backends',
			'dispatchConfigFile': u'/etc/opsi/backendManager/dispatch.conf',
			'extensionConfigDir': u'/etc/opsi/backendManager/extend.d',
			'aclFile': u'/etc/opsi/backendManager/acl.conf',
			'socket': u'/var/run/opsiconfd/opsiconfd.socket',
			'multiprocessing': False,
			'loadbalancing': False,
			'profile': False,
			'profiler': u'profiler',
			'debug': False,
			'messageBus': False,
			'monitoringUser': u"monitoring",
			'monitoringDebug': False,
		}

	def setCommandlineConfig(self):
		for (opt, arg) in self.opts:
			if opt == "-D":
				self.config['daemon'] = True
			elif opt == "-l":
				self.config['logLevel'] = forceInt(arg)
			elif opt == "-f":
				self.config['logFile'] = forceFilename(arg)
			elif opt == "-p":
				self.config['httpPort'] = forceInt(arg)
			elif opt == "-P":
				self.config['httpsPort'] = forceInt(arg)
			elif opt == "-i":
				self.config['interface'] = forceUnicode(arg)
			elif opt == "--profile":
				self.config["profile"] = forceFilename(arg)
			elif opt == "--profiler":
				self.config["profiler"] = forceUnicode(arg)
			elif opt == "--debug":
				self.config["debug"] = True

	def createPidFile(self):
		if not os.path.exists(os.path.dirname(self.config['pidFile'])):
			logger.notice(u"Missing directory {0!r} - creating.".format(self.config['pidFile']))
			os.makedirs(os.path.dirname(self.config['pidFile']))
		elif os.path.exists(self.config['pidFile']) and os.access(self.config['pidFile'], os.R_OK | os.W_OK):
			logger.notice(u"Found old pid file {0!r}".format(self.config['pidFile']))
			with open(self.config['pidFile'], 'r') as pf:
				pidFromFile = pf.readline().strip()

			if pidFromFile:
				running = False
				try:
					for pid in execute("%s -x opsiconfd" % which("pidof"))[0].strip().split():
						if pid == pidFromFile:
							running = True
							break
				except Exception as e:
					logger.error(e)

				if running:
					raise Exception(u"Another opsiconfd process is running (pid: %s), stop process first or change pidfile." % pidFromFile)

		pid = os.getpid()

		logger.notice(u"Creating pid file {0!r}".format(self.config['pidFile']))
		with open(self.config['pidFile'], "w") as pf:
			pf.write(str(pid))

	def removePidFile(self):
		try:
			if os.path.exists(self.config['pidFile']):
				with open(self.config['pidFile'], "r") as pf:
					pid = pf.read().strip()

				if int(pid) == int(os.getpid()):
					logger.info(u"Removing pid file '%s'" % self.config['pidFile'])
					os.unlink(self.config['pidFile'])
		except Exception as e:
			logger.error(u"Failed to remove pid file '%s': %s" % (self.config['pidFile'], e))

	def stop(self):
		if reactor and reactor.running and self._opsiconfd:
			reactor.callFromThread(self._opsiconfd.stop)

	def signalHandler(self, signo, stackFrame):
		for thread in threading.enumerate():
			logger.debug(u"Running thread before signal: %s" % thread)

		if signo == SIGHUP:
			if reactor and reactor.running and self._opsiconfd:
				self.setDefaultConfig()
				self.readConfigFile()
				self.setCommandlineConfig()
				self._opsiconfd.setConfig(self.config)
				reactor.callFromThread(self._opsiconfd.reload)

		if signo in (SIGTERM, SIGINT):
			self.stop()

		for thread in threading.enumerate():
			logger.debug(u"Running thread after signal: %s" % thread)

	def readConfigFile(self):
		''' Get settings from config file '''
		logger.notice(u"Trying to read config from file: '%s'" % self.config['configFile'])

		try:
			iniFile = IniFile(filename=self.config['configFile'], raw=True)
			config = iniFile.parse()

			for section in config.sections():
				logger.debug(u"Processing section '%s' in config file: '%s'" % (section, self.config['configFile']))
				if section.lower() == 'global':
					# Global settings
					for (option, value) in config.items(section):
						if option == 'pid file':
							self.config['pidFile'] = forceFilename(value)
						elif option == 'log level':
							self.config['logLevel'] = forceInt(value)
						elif option == 'log file':
							self.config['logFile'] = forceFilename(value)
						elif option == 'log format':
							self.config['logFormat'] = forceUnicode(value)
						elif option == 'max log size':
							self.config['maxlogsize'] = removeUnit(value)
						elif option == 'symlink logs':
							self.config['symlinkLogs'] = forceBool(value)
						elif option == 'backend config dir':
							self.config['backendConfigDir'] = forceFilename(value)
						elif option == 'dispatch config file':
							self.config['dispatchConfigFile'] = forceFilename(value)
						elif option == 'socket':
							self.config['socket'] = forceFilename(value)
						elif option == 'extension config dir':
							self.config['extensionConfigDir'] = forceFilename(value)
						elif option == 'acl file':
							self.config['aclFile'] = forceFilename(value)
						elif option == 'max execution statistics':
							self.config['maxExecutionStatisticValues'] = forceInt(value)
						elif option == 'multiprocessing':
							self.config['multiprocessing'] = forceBool(value)
						elif option == 'loadbalancing':
							self.config['loadbalancing'] = forceBool(value)
						elif option == 'admin networks':
							self.config['adminNetworks'] = []
							for net in value.split(','):
								self.config['adminNetworks'].append(forceNetworkAddress(net.strip()))
						elif option == 'message bus':
							self.config['messageBus'] = forceBool(value)
						elif option == 'monitoring user':
							self.config['monitoringUser'] = forceUnicode(value)
						elif option == 'monitoring debug':
							self.config['monitoringDebug'] = forceBool(value)
						else:
							logger.warning(u"Ignoring unknown option '%s' in config file: '%s'" % (option, self.config['configFile']))

				elif section.lower() == 'service':
					# Service settings
					for (option, value) in config.items(section):
						if option == 'http port':
							self.config['httpPort'] = forceInt(value)
						elif option == 'https port':
							self.config['httpsPort'] = forceInt(value)
						elif option == 'interface':
							self.config['interface'] = forceUnicode(value)
						elif option == 'ssl server cert':
							self.config['sslServerCertFile'] = forceFilename(value)
						elif option == 'ssl server key':
							self.config['sslServerKeyFile'] = forceFilename(value)
						elif option == 'accepted ciphers':
							self.config['acceptedCiphers'] = forceUnicode(value)
						else:
							logger.warning(u"Ignoring unknown option '%s' in config file: '%s'" % (option, self.config['configFile']))

				elif section.lower() == 'session':
					# Session settings
					for (option, value) in config.items(section):
						if option == 'session name':
							self.config['sessionName'] = forceUnicode(value)
						elif option == 'verify ip':
							self.config['resolveVerifyIp'] = forceBool(value)
						elif option == 'update ip':
							self.config['updateIpAddress'] = forceBool(value)
						elif option == 'max inactive interval':
							self.config['sessionMaxInactiveInterval'] = forceInt(value)
						elif option == 'max sessions per ip':
							self.config['maxSessionsPerIp'] = forceInt(value)
						elif option == 'max authentication failures':
							self.config['maxAuthenticationFailures'] = forceInt(value)
						else:
							logger.warning(u"Ignoring unknown option '%s' in config file: '%s'" % (option, self.config['configFile']))

				elif section.lower() == 'directories':
					# Static directories
					self.config['staticDirectories'] = {}
					for (directory, path) in config.items(section):
						opt = []
						if '(' in path:
							(path, opt) = path.split('(', 1)
							path = path.strip()
							opt = opt.lower().replace(')', '').strip().split(',')
							for i in range(len(opt)):
								opt[i] = opt[i].strip()
						self.config['staticDirectories'][directory] = {"path": forceFilename(path), "options": opt}
				else:
					logger.warning(u"Ignoring unknown section '%s' in config file: '%s'" % (section, self.config['configFile']))

		except Exception as error:
			# An error occured while trying to read the config file
			logger.error(u"Failed to read config file '%s': %s" % (self.config['configFile'], error))
			logger.logException(error)
			raise

		logger.notice(u"Config read")

	def usage(self):
		print(u"\nUsage: %s [-D] [-c <filename>] [-f <filename>] [-l <log level>] [-i <ipaddress>] [-p <http port>] [-P <https port>]" % os.path.basename(sys.argv[0]))
		print(u"Options:")
		print(u"  -v    Show version information and exit")
		print(u"  -D    Causes the server to operate as a daemon")
		print(u"  -p    HTTP Port to listen on (0 to disable)")
		print(u"  -P    HTTPS Port to listen on (0 to disable)")
		print(u"  -i    IP address of interface to listen on (default: 0.0.0.0)")
		print(u"  -f    Log to given file instead of syslog")
		print(u"  -c    Location of config file")
		print(u"  -s    Location of socket (default: /var/run/opsiconfd/opsiconfd.socket")
		print(u"  -l    Set log level (default: 4)")
		print(u"        0=nothing, 1=essential, 2=critical, 3=error, 4=warning")
		print(u"        5=notice, 6=info, 7=debug, 8=debug2, 9=confidential")
		print(u"")


def main():
	logger.setConsoleLevel(LOG_WARNING)

	try:
		OpsiconfdInit()
	except SystemExit:
		pass
	except Exception as exception:
		logger.logException(exception)
		print(u"ERROR: {0}".format(unicode(exception)), file=sys.stderr)
		return 1

	return 0

if __name__ == "__main__":
	sys.exit(main())
