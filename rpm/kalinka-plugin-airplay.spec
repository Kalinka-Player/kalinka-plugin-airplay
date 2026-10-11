# Built from files staged by scripts/build_rpm.sh, not from a tarball:
#   rpmbuild -bb --define "plugin_version X.Y.Z" --define "stage DIR" \
#     --define "wheel NAME.whl" rpm/kalinka-plugin-airplay.spec
#
# Mirrors the deb. The wheel joins the server's shipped wheels, which the
# server's bootstrap.sh installs into /opt/kalinka/venv whenever it starts.

# The binaries are built and stripped before packaging.
%global debug_package %{nil}

Name:           kalinka-plugin-airplay
Version:        %{plugin_version}
Release:        1%{?dist}
Summary:        AirPlay receiver input for Kalinka Music Player
License:        GPL-3.0-or-later AND MIT AND BSD-2-Clause AND GPL-2.0-only
URL:            https://github.com/Kalinka-Player/kalinka-plugin-airplay
# kalinka-server bundles the plugin SDK. The server skips the plugin when that
# SDK does not satisfy its REQUIRES_SDK (>=3.11,<4).
Requires:       kalinka-server
Requires:       python3 >= 3.11
Requires:       flac
Requires:       avahi
# rpmbuild adds the bundled binaries' shared-library requirements.

%description
Server-side AirPlay plugin: an iPhone, iPad or Mac plays through Kalinka's
selected renderer, which pulls the audio from the server as a live FLAC
stream. Bundles shairport-sync 5.5.2 with AirPlay 2 and nqptp 1.2.8, with
their source.

%install
install -D -m 644 %{stage}/%{wheel} %{buildroot}/opt/kalinka/wheels/%{wheel}
install -D -m 755 %{stage}/shairport-sync \
    %{buildroot}%{_libexecdir}/kalinka-plugin-airplay/shairport-sync
install -D -m 755 %{stage}/nqptp %{buildroot}%{_libexecdir}/kalinka-plugin-airplay/nqptp
install -d -m 755 %{buildroot}%{_datadir}/kalinka-plugin-airplay/source
install -m 644 %{stage}/*.tar.gz %{buildroot}%{_datadir}/kalinka-plugin-airplay/source/
install -D -m 644 %{stage}/airplay.conf \
    %{buildroot}%{_unitdir}/kalinka.service.d/airplay.conf
install -D -m 644 %{stage}/LICENSE %{buildroot}%{_licensedir}/%{name}/LICENSE
install -D -m 644 %{stage}/THIRD_PARTY.md %{buildroot}%{_licensedir}/%{name}/THIRD_PARTY.md
install -D -m 644 %{stage}/README.md %{buildroot}%{_docdir}/%{name}/README.md

%preun
# Removal only: take the plugin out of the server's venv.
if [ "$1" -eq 0 ] && [ -x /opt/kalinka/venv/bin/pip ]; then
    /opt/kalinka/venv/bin/pip uninstall -y kalinka-plugin-airplay >/dev/null 2>&1 || :
fi

%postun
if [ "$1" -eq 0 ]; then
    systemctl daemon-reload >/dev/null 2>&1 || :
    systemctl try-restart kalinka.service >/dev/null 2>&1 || :
fi

%posttrans
# The capability drop-in must be loaded before the server restarts with the new wheel.
systemctl daemon-reload >/dev/null 2>&1 || :
systemctl try-restart kalinka.service >/dev/null 2>&1 || :

%files
/opt/kalinka/wheels/%{wheel}
%dir %{_libexecdir}/kalinka-plugin-airplay
%{_libexecdir}/kalinka-plugin-airplay/shairport-sync
%{_libexecdir}/kalinka-plugin-airplay/nqptp
%dir %{_datadir}/kalinka-plugin-airplay
%dir %{_datadir}/kalinka-plugin-airplay/source
%{_datadir}/kalinka-plugin-airplay/source/*.tar.gz
%dir %{_unitdir}/kalinka.service.d
%{_unitdir}/kalinka.service.d/airplay.conf
%dir %{_licensedir}/%{name}
%license %{_licensedir}/%{name}/LICENSE
%license %{_licensedir}/%{name}/THIRD_PARTY.md
%dir %{_docdir}/%{name}
%doc %{_docdir}/%{name}/README.md

%changelog
* Sat Oct 10 2026 Dmitry Savin <envelsavinds@gmail.com> - 0.1.0-1
- Initial RPM package
