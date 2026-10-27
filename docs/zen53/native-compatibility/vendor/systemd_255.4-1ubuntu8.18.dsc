-----BEGIN PGP SIGNED MESSAGE-----
Hash: SHA512

Format: 3.0 (quilt)
Source: systemd
Binary: systemd, systemd-sysv, systemd-container, systemd-journal-remote, systemd-coredump, systemd-timesyncd, systemd-tests, libpam-systemd, libnss-myhostname, libnss-mymachines, libnss-resolve, libnss-systemd, libsystemd0, libsystemd-dev, libsystemd-shared, udev, libudev1, libudev-dev, udev-udeb, libudev1-udeb, systemd-standalone-sysusers, systemd-standalone-tmpfiles, systemd-oomd, systemd-userdbd, systemd-homed, systemd-boot, systemd-boot-efi, systemd-ukify, systemd-resolved, systemd-dev
Architecture: linux-any all
Version: 255.4-1ubuntu8.18
Maintainer: Ubuntu Developers <ubuntu-devel-discuss@lists.ubuntu.com>
Uploaders: Michael Biebl <biebl@debian.org>, Marco d'Itri <md@linux.it>, Sjoerd Simons <sjoerd@debian.org>, Martin Pitt <mpitt@debian.org>, Felipe Sateler <fsateler@debian.org>, Luca Boccassi <bluca@debian.org>,
Homepage: https://www.freedesktop.org/wiki/Software/systemd
Standards-Version: 4.6.2
Vcs-Browser: https://git.launchpad.net/~ubuntu-core-dev/ubuntu/+source/systemd
Vcs-Git: https://git.launchpad.net/~ubuntu-core-dev/ubuntu/+source/systemd
Testsuite: autopkgtest
Testsuite-Triggers: acl, apparmor, attr, autopkgtest, bind9-dnsutils, bind9-host, bsdutils, btrfs-progs, build-essential, busybox-static, cron, cryptsetup-bin, cryptsetup-initramfs, curl, dbus-user-session, dhcpcd-base, dmeventd, dnsmasq-base, dosfstools, e2fsprogs, erofs-utils, evemu-tools, fdisk, gcc, gdm3, gnutls-bin, iproute2, iputils-ping, isc-dhcp-client, jq, kbd, knot, knot-dnssecutils, knot-dnsutils, kpartx, less, libc-dev, libc6-dev, libcap-dev, libcap2-bin, libdw-dev, libelf-dev, libfido2-dev, libgcc-s1, liblz4-tool, libp11-kit-dev, libqrencode-dev, libssl-dev, libtss2-dev, locales, locales-all, lsb-release, lvm2, make, mdadm, mtools, multipath-tools, net-tools, netcat-openbsd, netlabel-tools, network-manager, nftables, nvme-cli, opensc, openssh-client, openssh-server, openssl, perl, pkg-config, plymouth, polkitd, psmisc, python3, python3-colorama, python3-pefile, python3-pexpect, qemu-system-arm, qemu-system-ppc, qemu-system-s390x, qemu-system-x86, quota, rsyslog, screen, seabios, snapd, socat, softhsm2, squashfs-tools, strace, stress, swtpm, tpm2-tools, tree, tzdata, tzdata-legacy, vim-tiny, xkb-data, xserver-xorg, xserver-xorg-video-dummy, xz-utils, zstd
Build-Depends: debhelper-compat (= 13), debhelper (>= 13.11.6), dh-exec, dh-package-notes, dh-sequence-installnss, dh-sequence-installsysusers, pkgconf, xsltproc <!nodoc>, docbook-xsl <!nodoc>, docbook-xml <!nodoc>, meson (>= 0.60.0), gettext, gperf, libcap-dev, libcrypt-dev, libpam0g-dev, libapparmor-dev (>= 2.13) <!stage1>, libidn2-dev <!stage1>, libiptc-dev <!stage1>, libaudit-dev <!stage1>, libdbus-1-dev (>= 1.3.2) <!nocheck> <!noinsttest>, libcryptsetup-dev (>= 2:2.4) <!stage1>, libselinux1-dev (>= 2.1.9), libacl1-dev, liblzma-dev, liblz4-dev (>= 1.3.0), liblz4-tool <!nocheck>, libbz2-dev <!stage1>, zlib1g-dev <!stage1> | libz-dev <!stage1>, libcurl4-openssl-dev <!stage1>, libmicrohttpd-dev <!stage1>, libpcre2-dev <!stage1>, libgcrypt20-dev, libkmod-dev (>= 15), libblkid-dev (>= 2.24), libmount-dev (>= 2.30), libfdisk-dev (>= 2.33), libseccomp-dev (>= 2.3.1) [amd64 arm64 armel armhf i386 mips mipsel mips64 mips64el x32 powerpc ppc64 ppc64el riscv64 s390x], libdw-dev (>= 0.158) <!stage1>, libpolkit-gobject-1-dev <!stage1>, libzstd-dev (>= 1.4.0), libtss2-dev [!i386] <!stage1>, libfido2-dev <!stage1>, libp11-kit-dev <!stage1>, libssl-dev <!stage1>, libqrencode-dev [!i386] <!stage1>, libxen-dev [amd64 arm64 armhf] <!stage1>, linux-base <!nocheck>, acl <!nocheck>, python3:native, python3-jinja2:native, python3-lxml:native, python3-pefile:native <!stage1 !nocheck> <!stage1 !noinsttest>, python3-pyelftools [amd64 i386 arm64 armhf riscv64] <!stage1>, python3-pyparsing <!nocheck>, python3-evdev:native <!nocheck>, tzdata-legacy <!nocheck> | tzdata (<< 2024a-1) <!nocheck>, libcap2-bin <!nocheck>, iproute2 <!nocheck>, zstd <!nocheck>, gawk <!nocheck>, fdisk <!nocheck>
Package-List:
 libnss-myhostname deb admin optional arch=linux-any
 libnss-mymachines deb admin optional arch=linux-any
 libnss-resolve deb admin optional arch=linux-any
 libnss-systemd deb admin standard arch=linux-any
 libpam-systemd deb admin standard arch=linux-any
 libsystemd-dev deb libdevel optional arch=linux-any
 libsystemd-shared deb libs optional arch=linux-any
 libsystemd0 deb libs optional arch=linux-any
 libudev-dev deb libdevel optional arch=linux-any
 libudev1 deb libs optional arch=linux-any
 libudev1-udeb udeb debian-installer optional arch=linux-any profile=!noudeb
 systemd deb admin important arch=linux-any
 systemd-boot deb admin optional arch=amd64,i386,arm64,armhf,riscv64 profile=!stage1
 systemd-boot-efi deb admin optional arch=amd64,i386,arm64,armhf,riscv64 profile=!stage1
 systemd-container deb admin optional arch=linux-any profile=!stage1
 systemd-coredump deb admin optional arch=linux-any profile=!stage1
 systemd-dev deb devel optional arch=all
 systemd-homed deb admin optional arch=linux-any profile=!stage1
 systemd-journal-remote deb admin optional arch=linux-any profile=!stage1
 systemd-oomd deb admin optional arch=linux-any
 systemd-resolved deb admin important arch=linux-any
 systemd-standalone-sysusers deb admin optional arch=linux-any
 systemd-standalone-tmpfiles deb admin optional arch=linux-any
 systemd-sysv deb admin important arch=linux-any
 systemd-tests deb admin optional arch=linux-any profile=!noinsttest
 systemd-timesyncd deb admin standard arch=linux-any
 systemd-ukify deb admin optional arch=all profile=!stage1
 systemd-userdbd deb admin optional arch=linux-any profile=!stage1
 udev deb admin important arch=linux-any
 udev-udeb udeb debian-installer optional arch=linux-any profile=!noudeb
Checksums-Sha1:
 c8d0e2e79a137bcace2550bb792cebcc42c98352 14952427 systemd_255.4.orig.tar.gz
 575a9f72513cbe16fa6a40cca709945dd773c819 273272 systemd_255.4-1ubuntu8.18.debian.tar.xz
Checksums-Sha256:
 96e75bd08c57ad401677456fb88ef54a9f05bb1695693013bc6ecce839640fd5 14952427 systemd_255.4.orig.tar.gz
 ec3ce620c3e072d34bbeaf9a4155cd91b5bee94eb412071bf81182f230ae2657 273272 systemd_255.4-1ubuntu8.18.debian.tar.xz
Files:
 fda0291dffa4a3255b99be184d13e710 14952427 systemd_255.4.orig.tar.gz
 f34dcd1ff2b167be84311bdacf0e701c 273272 systemd_255.4-1ubuntu8.18.debian.tar.xz
Original-Maintainer: Debian systemd Maintainers <pkg-systemd-maintainers@lists.alioth.debian.org>

-----BEGIN PGP SIGNATURE-----

iQIzBAEBCgAdFiEEcfvxe+flLQwqLJFE8LYUYLBMS1YFAmrOIBsACgkQ8LYUYLBM
S1asqw//bwdVJzR0qdY8RAOUy3TlJzLg4lqJq4fsaWbfXcOq1/N2qduFYLDyAR03
KI6otebYSRuFBa3dJJW/ZX/TKyZjMGx13Aa+QOmV5vPo7PoDUpY8S7xNf6ofv/Z1
OWsvg7QM3Bd1dxin1l2RTazC67EdG6gqleUp5MYbVenjucB33tmnyOUxmtVXztke
1qvpMyROQ04qLmLFywUKzSNJV6oFhy0NVH/P+gkBqfbR28bej3eOlGrtRcWqtnIX
FACccSqRfL16ew9Bz+sGTUr8Py37mJf7O4LBJtsogZxL3Bo4xpOWypkGonCwBP6y
v1/xaHSsFAiMAoXybVwRhM+z20TUER2b2KBbY2DgLXWbZDv6SEdZJNPCnfj/AsIe
txiqcWbIPQLdOtV4W1YntA1Ebtgwz9WYuYmx8cAPNheRLVPTjpsOGmu9dOHEs27d
FpYwhcPsvh4/REWE1MOOS99vNH4mGGHEPXYEx//ToXl0No8ruNS5WLyi3z9NGp8t
hp9BrNpIDg7xBRQ6CgCuSa0zXCXUXKX3/MrmGrXtN1YmQ/V2MoUgQGZU/IQ+PClX
UuHqkK6KkF0DSH+A1hwAZGu+mFQsj9WkY6guqVQvXbvzVRGlotjqdmNN9TBDMJTZ
Dh09ZfmDaIEcVPU6SFNOYRpLfFbFQFesu4dJCImNxzT1SvHP16k=
=Z1CB
-----END PGP SIGNATURE-----
