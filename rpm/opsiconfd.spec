#
# spec file for package opsiconfd
#
# Copyright (c) 2008-2015 uib GmbH.
# This file and all modifications and additions to the pristine
# package are under the same license as the package itself.
#

Name:           opsiconfd
BuildRequires:  python-devel python-setuptools openssl dbus-1-python procps
Requires:       python-opsi >= 4.0.6.50
Requires:       openssl
Requires:       python-twisted
Requires:       dbus-1-python
Requires:       psmisc
Requires:       procps
Url:            http://www.opsi.org
License:        AGPL-3.0+
Group:          Productivity/Networking/Opsi
AutoReqProv:    on
Version:        4.0.7.5.3
Release:        2
Summary:        This is the opsi configuration service
%define tarname opsiconfd
Source:         opsiconfd_4.0.7.5.3-2.tar.gz
BuildRoot:      %{_tmppath}/%{name}-%{version}-build
%if 0%{?suse_version} == 1110 || 0%{?suse_version} == 1315
# SLES
Requires:       pkg-config
BuildRequires:  python-opsi >= 4.0.6.50 zypper logrotate
BuildRequires:  pkg-config
PreReq:         %insserv_prereq
Suggests:       python-rrdtool
%{py_requires}
%else
%if 0%{?suse_version}
Suggests: logrotate
Requires:       python-avahi
Requires:       pkg-config
BuildRequires:  pkg-config
BuildRequires:  python-rrdtool zypper logrotate
PreReq:         %insserv_prereq
%{py_requires}
%else
Requires:       pkgconfig
%endif
%endif

%if 0%{?suse_version} != 1110
BuildArch:      noarch
%endif
%define fileadmingroup %(grep "fileadmingroup" /etc/opsi/opsi.conf | cut -d "=" -f 2 | sed 's/\s*//g')

%define toplevel_dir %{name}-%{version}

# ===[ description ]================================
%description
This package contains the opsi configuration service.

# ===[ prep ]=======================================
%prep

# ===[ setup ]======================================
%setup -n %{tarname}-%{version}

# ===[ build ]======================================
%build
%if 0%{?rhel_version} >= 700 || 0%{?centos_version} >= 700
# Fix for https://bugzilla.redhat.com/show_bug.cgi?id=1117878
export PATH="/usr/bin:$PATH"
%endif
export CFLAGS="$RPM_OPT_FLAGS"
python setup.py build


# ===[ install ]====================================
%install
mkdir -p $RPM_BUILD_ROOT/etc/opsi/systemdTemplates

%if 0%{?suse_version}
python setup.py install --prefix=%{_prefix} --root=$RPM_BUILD_ROOT --record-rpm=INSTALLED_FILES
%else
python setup.py install --prefix=%{_prefix} --root=$RPM_BUILD_ROOT --record=INSTALLED_FILES
%endif

mkdir -p $RPM_BUILD_ROOT/var/log/opsi/opsiconfd

mkdir -p $RPM_BUILD_ROOT/usr/sbin
ln -sf /etc/init.d/opsiconfd $RPM_BUILD_ROOT/usr/sbin/rcopsiconfd

sed -i 's#/etc/init.d$##;s#/etc/logrotate.d$##' INSTALLED_FILES

%if 0%{?suse_version} < 1110
echo "Detected openSuse / SLES"
LOGROTATE_VERSION="$(zypper info logrotate | grep -i "version" | awk '{print $2}' | cut -d '-' -f 1)"
if [ "$(zypper --terse versioncmp $LOGROTATE_VERSION 3.8)" == "-1" ]; then
	echo "Fixing logrotate configuration for logrotate version older than 3.8"
	LOGROTATE_TEMP=$RPM_BUILD_ROOT/opsi-logrotate_config.temp
	LOGROTATE_CONFIG=$RPM_BUILD_ROOT/etc/logrotate.d/opsiconfd
	grep -v "su opsiconfd opsiadmin" $LOGROTATE_CONFIG > $LOGROTATE_TEMP
	mv $LOGROTATE_TEMP $LOGROTATE_CONFIG
else
	echo "Logrotate version $LOGROTATE_VERSION is 3.8 or newer. Nothing to do."
fi
%else
	%if 0%{?rhel_version} || 0%{?centos_version} || 0%{?fedora_version}
		echo "Detected RHEL / CentOS / Fedora"
		%if 0%{?rhel_version} == 600 || 0%{?centos_version} == 600
			echo "Fixing logrotate configuration"
			LOGROTATE_TEMP=$RPM_BUILD_ROOT/opsi-logrotate_config.temp
			LOGROTATE_CONFIG=$RPM_BUILD_ROOT/etc/logrotate.d/opsiconfd
			grep -v "su opsiconfd opsiadmin" $LOGROTATE_CONFIG > $LOGROTATE_TEMP
			mv $LOGROTATE_TEMP $LOGROTATE_CONFIG
		%endif
	%endif
%endif


# ===[ clean ]======================================
%clean
rm -rf $RPM_BUILD_ROOT

# ===[ post ]=======================================
%post
arg0=$1

#fix for runlevel 4 (not used on rpm-based machines)
if  [ -e  "/etc/init.d/opsiconfd" ]; then
	sed -i "s/2 3 4 5/2 3 5/g; s/2345/235/g" /etc/init.d/opsiconfd
fi

fileadmingroup=$(grep "fileadmingroup" /etc/opsi/opsi.conf | cut -d "=" -f 2 | sed 's/\s*//g')
if [ -z "$fileadmingroup" ]; then
	fileadmingroup=pcpatch
fi

if [ $arg0 -eq 1 ]; then
	# Install
	%if 0%{?centos_version} || 0%{?rhel_version} || 0%{?fedora_version}
		chkconfig --add opsiconfd
	%else
		insserv opsiconfd || true
	%endif

	if [ $fileadmingroup != pcpatch -a -z "$(getent group $fileadmingroup)" ]; then
		groupmod -n $fileadmingroup pcpatch
	else
		if [ -z "$(getent group $fileadmingroup)" ]; then
			groupadd $fileadmingroup
		fi
	fi

	if [ -z "`getent passwd opsiconfd`" ]; then
		useradd --system -g $fileadmingroup -d /var/lib/opsi -s /bin/bash opsiconfd
	fi

	if [ -z "`getent group opsiadmin`" ]; then
		groupadd opsiadmin
	fi

	%if 0%{?rhel_version} || 0%{?centos_version} || 0%{?fedora_version} || 0%{?suse_version} >= 1230
		getent group shadow > /dev/null || groupadd -r shadow
		chgrp shadow /etc/shadow
		chmod g+r /etc/shadow
		usermod -a -G shadow opsiconfd 1>/dev/null 2>/dev/null || true
		usermod -a -G opsiadmin opsiconfd 1>/dev/null 2>/dev/null || true
	%else
		groupmod -A opsiconfd shadow 1>/dev/null 2>/dev/null || true
		groupmod -A opsiconfd uucp 1>/dev/null 2>/dev/null || true
		groupmod -A opsiconfd opsiadmin 1>/dev/null 2>/dev/null || true
	%endif
fi

if [ ! -e "/etc/opsi/opsiconfd.pem" ]; then
	umask 077

	cert_country="DE"
	cert_state="RP"
	cert_locality="Mainz"
	cert_organization="uib GmbH"
	cert_commonname=`hostname -f`
	cert_email="root@$cert_commonname"

	echo "RANDFILE = /tmp/opsiconfd.rand" 	>  /tmp/opsiconfd.cnf
	echo "" 				>> /tmp/opsiconfd.cnf
	echo "[ req ]" 				>> /tmp/opsiconfd.cnf
	echo "default_bits = 1024" 		>> /tmp/opsiconfd.cnf
	echo "encrypt_key = yes" 		>> /tmp/opsiconfd.cnf
	echo "distinguished_name = req_dn" 	>> /tmp/opsiconfd.cnf
	echo "x509_extensions = cert_type" 	>> /tmp/opsiconfd.cnf
	echo "prompt = no" 			>> /tmp/opsiconfd.cnf
	echo "" 				>> /tmp/opsiconfd.cnf
	echo "[ req_dn ]" 			>> /tmp/opsiconfd.cnf
	echo "C=$cert_country"			>> /tmp/opsiconfd.cnf
	echo "ST=$cert_state" 			>> /tmp/opsiconfd.cnf
	echo "L=$cert_locality" 		>> /tmp/opsiconfd.cnf
	echo "O=$cert_organization" 		>> /tmp/opsiconfd.cnf
	#echo "OU=$cert_unit" 			>> /tmp/opsiconfd.cnf
	echo "CN=$cert_commonname" 		>> /tmp/opsiconfd.cnf
	echo "emailAddress=$cert_email"		>> /tmp/opsiconfd.cnf
	echo "" 				>> /tmp/opsiconfd.cnf
	echo "[ cert_type ]" 			>> /tmp/opsiconfd.cnf
	echo "nsCertType = server" 		>> /tmp/opsiconfd.cnf

	dd if=/dev/urandom of=/tmp/opsiconfd.rand count=1 2>/dev/null
	openssl req -new -x509 -days 1000 -nodes \
		-config /tmp/opsiconfd.cnf -out /etc/opsi/opsiconfd.pem -keyout /etc/opsi/opsiconfd.pem
	openssl gendh -rand /tmp/opsiconfd.rand 512 >>/etc/opsi/opsiconfd.pem
	openssl x509 -subject -dates -fingerprint -noout -in /etc/opsi/opsiconfd.pem
	rm -f /tmp/opsiconfd.rand /tmp/opsiconfd.cnf
fi

chmod 600 /etc/opsi/opsiconfd.pem
chown opsiconfd:opsiadmin /etc/opsi/opsiconfd.pem || true
chmod 750 /var/log/opsi/opsiconfd
chown -R opsiconfd:$fileadmingroup /var/log/opsi/opsiconfd

set +e
SYSTEMDUNITDIR=$(pkg-config systemd --variable=systemdsystemunitdir)
set -e
if [ ! -z "$SYSTEMDUNITDIR" -a -d "$SYSTEMDUNITDIR" -a -d "/etc/opsi/systemdTemplates/" ]; then
	echo "Copying opsiconfd.service to $SYSTEMDUNITDIR"
	cp "/etc/opsi/systemdTemplates/opsiconfd.service" "$SYSTEMDUNITDIR" || echo "Copying opsiconfd.service failed"

	%if 0%{?suse_version} >= 1315 || 0%{?centos_version} >= 700 || 0%{?rhel_version} >= 700
		# Adjusting to the correct service names
		sed --in-place "s/=smbd.service/=smb.service/" "$SYSTEMDUNITDIR/opsiconfd.service" || True
		sed --in-place "s/=isc-dhcp-server.service/=dhcpd.service/" "$SYSTEMDUNITDIR/opsiconfd.service" || True
	%endif

	%if 0%{?suse_version} || 0%{?centos_version} || 0%{?rhel_version}
		MKDIR_PATH=$(which mkdir)
		CHOWN_PATH=$(which chown)
		sed --in-place "s!=-/bin/mkdir!=-$MKDIR_PATH!" "$SYSTEMDUNITDIR/opsiconfd.service" || True
		sed --in-place "s!=-/bin/chown!=-$CHOWN_PATH!" "$SYSTEMDUNITDIR/opsiconfd.service" || True
	%endif

	systemctl=`which systemctl 2>/dev/null` || true
	if [ ! -z "$systemctl" -a -x "$systemctl" ]; then
		echo "Reloading unit-files"
		$systemctl daemon-reload || echo "Reloading unit-files failed!"
		$systemctl enable opsiconfd.service && echo "Enabled opsiconfd.service" || echo "Enabling opsiconfd.service failed!"
	fi
fi

if [ $arg0 -eq 1 ]; then
	# Install
	/sbin/service opsiconfd start || true
else
	# Upgrade
	if [ -e /var/run/opsiconfd.pid -o -e /var/run/opsiconfd/opsiconfd.pid ]; then
		rm /var/run/opsiconfd.pid 2>/dev/null || true
		/sbin/service opsiconfd restart || true
	fi
fi

# ===[ preun ]======================================
%preun
%if 0%{?suse_version}
	%stop_on_removal opsiconfd
%else
	if [ $1 = 0 ] ; then
		/sbin/service opsiconfd stop >/dev/null 2>&1 || true
	fi
%endif

# ===[ postun ]=====================================
%postun
%restart_on_update opsiconfd
if [ $1 -eq 0 ]; then
	%if 0%{?centos_version} || 0%{?rhel_version} || 0%{?fedora_version}
		chkconfig --del opsiconfd
	%else
		%insserv_cleanup
	%endif
	%if 0%{?suse_version}
		groupmod -R opsiconfd shadow 1>/dev/null 2>/dev/null || true
		groupmod -R opsiconfd uucp 1>/dev/null 2>/dev/null || true
	%endif
	[ -z "`getent passwd opsiconfd`" ] || userdel opsiconfd
	rm -f /etc/opsi/opsiconfd.pem  1>/dev/null 2>/dev/null || true

	SYSTEMDUNITDIR=$(pkg-config systemd --variable=systemdsystemunitdir)
	if [ ! -z "$SYSTEMDUNITDIR" -a -d "$SYSTEMDUNITDIR" -a -e "$SYSTEMDUNITDIR/opsiconfd.service" ]; then
		systemctl=`which systemctl 2>/dev/null` || true
		if [ ! -z "$systemctl" -a -x "$systemctl" ]; then
			$systemctl disable opsiconfd.service && echo "Disabled opsiconfd.service" || echo "Disabling opsiconfd.service failed!"
			$systemctl daemon-reload || echo "Reloading unit-files failed!"
		fi

		rm "$SYSTEMDUNITDIR/opsiconfd.service" || echo "Removing opsiconfd.service failed"
	fi
fi

# ===[ files ]======================================
%files -f INSTALLED_FILES
# default attributes
%defattr(-,root,root)

# configfiles
%config(noreplace) /etc/opsi/opsiconfd.conf
%attr(0755,root,root) %config /etc/init.d/opsiconfd
%config /etc/logrotate.d/opsiconfd

## other files
%attr(0755,root,root) /usr/sbin/rcopsiconfd
## directories
%dir /var/log/opsi

%attr(0750,opsiconfd,root) %dir /var/log/opsi/opsiconfd

%if 0%{?rhel_version} || 0%{?centos_version} || 0%{?fedora_version}
%define python_sitearch %(%{__python} -c 'from distutils import sysconfig; print sysconfig.get_python_lib()')
%{python_sitearch}/opsiconfd/*
%endif

# ===[ changelog ]==================================
%changelog
