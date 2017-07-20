#!/usr/bin/env bash

# VCFlow install script for Ubuntu (and Debian Wheezy+)
# Pedro Diniz (phds@cbpf.br)

# Fail on error
set -e

# Fail on unset var usage
set -o nounset

# Get directory containing VCFlow folder
VCFLOW_DIR="$( cd -P "$( dirname "${BASH_SOURCE[0]}" )/../.." && pwd -P )"

# Set up build directory, which by default is the working directory
#  unless the working directory is a subdirectory of vcflow,
#  in which case we use the directory containing vcflow
BUILD_DIR="$(pwd -P)"
case $BUILD_DIR in
  $VCFLOW_DIR/*) BUILD_DIR=$VCFLOW_DIR;; # currect directory is a subdirectory
  *) BUILD_DIR=$BUILD_DIR;;
esac

# Attempt to identify Linux release

DIST=Unknown
RELEASE=Unknown
CODENAME=Unknown
ARCH=`uname -m`
if [ "$ARCH" = "x86_64" ]; then ARCH="amd64"; fi
if [ "$ARCH" = "i686" ]; then ARCH="i386"; fi

test -e /etc/debian_version && DIST="Debian"
grep Ubuntu /etc/lsb-release &> /dev/null && DIST="Ubuntu"
if [ "$DIST" = "Ubuntu" ] || [ "$DIST" = "Debian" ]; then
    # Truly non-interactive apt-get installation
    install='sudo DEBIAN_FRONTEND=noninteractive apt-get -y -q install'
    remove='sudo DEBIAN_FRONTEND=noninteractive apt-get -y -q remove'
    pkginst='sudo dpkg -i'
    update='sudo apt-get'
    # Prereqs for this script
    if ! which lsb_release &> /dev/null; then
        $install lsb-release
    fi
fi
test -e /etc/fedora-release && DIST="Fedora"
test -e /etc/redhat-release && DIST="RedHatEnterpriseServer"
if [ "$DIST" = "Fedora" -o "$DIST" = "RedHatEnterpriseServer" ]; then
    install='sudo yum -y install'
    remove='sudo yum -y erase'
    pkginst='sudo rpm -ivh'
    update='sudo yum'
    # Prereqs for this script
    if ! which lsb_release &> /dev/null; then
        $install redhat-lsb-core
    fi
fi
test -e /etc/SuSE-release && DIST="SUSE Linux"
if [ "$DIST" = "SUSE Linux" ]; then
    install='sudo zypper --non-interactive install '
    remove='sudo zypper --non-interactive  remove '
    pkginst='sudo rpm -ivh'
    # Prereqs for this script
    if ! which lsb_release &> /dev/null; then
		$install openSUSE-release
    fi
fi
if which lsb_release &> /dev/null; then
    DIST=`lsb_release -is`
    RELEASE=`lsb_release -rs`
    CODENAME=`lsb_release -cs`
fi
echo "Detected Linux distribution: $DIST $RELEASE $CODENAME $ARCH"

# Kernel params

KERNEL_NAME=`uname -r`
KERNEL_HEADERS=kernel-headers-${KERNEL_NAME}

# Treat Raspbian as Debian
[ "$DIST" = 'Raspbian' ] && DIST='Debian'

DISTS='Ubuntu|Debian|Fedora|RedHatEnterpriseServer|SUSE LINUX'
if ! echo $DIST | egrep "$DISTS" >/dev/null; then
    echo "Install.sh currently only supports $DISTS."
    exit 1
fi

# More distribution info
DIST_LC=`echo $DIST | tr [A-Z] [a-z]` # as lower case


# Determine whether version $1 >= version $2
# usage: if version_ge 1.20 1.2.3; then echo "true!"; fi
function version_ge {
    # sort -V sorts by *version number*
    latest=`printf "$1\n$2" | sort -V | tail -1`
    # If $1 is latest version, then $1 >= $2
    [ "$1" == "$latest" ]
}


# Install VCFlow deps
function vcflow_deps {
    echo "Installing VCFlow dependencies"
    if [ "$DIST" = "Fedora" -o "$DIST" = "RedHatEnterpriseServer" ]; then
        $install python-pip
	elif [ "$DIST" = "SUSE LINUX"  ]; then
		$install python-pip
    else
        $install python-pip
    fi
    
    python_pip=`which pip`
    $python_pip install -r $VCFLOW_DIR/requirements.txt
}

# The following will cause a full OF install, covering:
# -user switch
# The instructions below are an abbreviated version from
# http://www.openflowswitch.org/wk/index.php/Debian_Install
function of {
    echo "Installing OpenFlow reference implementation..."
    cd $BUILD_DIR
    $install autoconf automake libtool make gcc
    if [ "$DIST" = "Fedora" -o "$DIST" = "RedHatEnterpriseServer" ]; then
        $install git pkgconfig glibc-devel
	elif [ "$DIST" = "SUSE LINUX"  ]; then
       $install git pkgconfig glibc-devel
    else
        $install git-core autotools-dev pkg-config libc6-dev
    fi
    # was: git clone git://openflowswitch.org/openflow.git
    # Use our own fork on github for now:
    git clone git://github.com/mininet/openflow
    cd $BUILD_DIR/openflow

    # Patch controller to handle more than 16 switches
    patch -p1 < $MININET_DIR/mininet/util/openflow-patches/controller.patch

    # Resume the install:
    ./boot.sh
    ./configure
    make
    sudo make install
    cd $BUILD_DIR
}

function of13 {
    echo "Installing OpenFlow 1.3 soft switch implementation..."
    cd $BUILD_DIR/
    $install  git-core autoconf automake autotools-dev pkg-config \
        make gcc g++ libtool libc6-dev cmake libpcap-dev libxerces-c2-dev  \
        unzip libpcre3-dev flex bison libboost-dev

    if [ ! -d "ofsoftswitch13" ]; then
        git clone https://github.com/CPqD/ofsoftswitch13.git
        if [[ -n "$OF13_SWITCH_REV" ]]; then
            cd ofsoftswitch13
            git checkout ${OF13_SWITCH_REV}
            cd ..
        fi
    fi

    # Resume the install:
    cd $BUILD_DIR/ofsoftswitch13
    ./boot.sh
    ./configure
    make
    sudo make install
    cd $BUILD_DIR
}


# Install RYU
function ryu {
    echo "Installing RYU..."

    # install Ryu dependencies"
    $install autoconf automake g++ libtool python make
    if [ "$DIST" = "Ubuntu" -o "$DIST" = "Debian" ]; then
        $install gcc python-pip python-dev libffi-dev libssl-dev \
            libxml2-dev libxslt1-dev zlib1g-dev
    fi

    # fetch RYU
    cd $BUILD_DIR/
    git clone git://github.com/osrg/ryu.git ryu
    cd ryu

    # install ryu
    sudo pip install -r tools/pip-requires -r tools/optional-requires \
        -r tools/test-requires
    sudo python setup.py install

    # Add symbolic link to /usr/bin
    sudo ln -s ./bin/ryu-manager /usr/local/bin/ryu-manager
}

# Copy Configuration File
function cp_config_file {
    echo "Installing configuration files..."
    
    mkdir /etc/vcflow

    cp -vp $BUILD_DIR/vcflow/config/vcflow_configuration_database.conf /etc/vcflow
    cp -vp $BUILD_DIR/vcflow/config/vcflow.conf /etc/vcflow
}

function all {
    if [ "$DIST" = "Fedora" ]; then
        printf "\nFedora 18+ support (still work in progress):\n"
        printf " * Fedora 18+ has kernel 3.10 RPMS in the updates repositories\n"
        printf " * Fedora 18+ has openvswitch 1.10 RPMS in the updates repositories\n"
        printf " * the install.sh script options [-bfnpvw] should work.\n"
        printf " * for a basic setup just try:\n"
        printf "       install.sh -fnpv\n\n"
        exit 3
    fi
    echo "Installing all packages..."
    ryu
    vcflow_deps
    cp_config_file
    echo "Enjoy VCFlow!"
}

# Restore disk space and remove sensitive files before shipping a VM.
function vm_clean {
    echo "Cleaning VM..."
    sudo apt-get clean
    sudo apt-get autoremove
    sudo rm -rf /tmp/*

    # Clear optional dev script for SSH keychain load on boot
    rm -f ~/.bash_profile

    # Clear git changes
    git config --global user.name "None"
    git config --global user.email "None"

}

function usage {
    printf '\nUsage: %s [-acdfhy03]\n\n' $(basename $0) >&2

    printf 'This install script attempts to install useful packages\n' >&2
    printf 'for VCFlow. It should (hopefully) work on Ubuntu 11.10+\n' >&2
    printf 'If you run into trouble, try\n' >&2
    printf 'installing one thing at a time, and looking at the \n' >&2
    printf 'specific installation function in this script.\n\n' >&2

    printf 'options:\n' >&2
    printf -- ' -a: (default) install (A)ll packages - good luck!\n' >&2
    printf -- ' -c: (C)opy configuration files\n' >&2
    printf -- ' -d: (D)elete some sensitive files from a VM image\n' >&2
    printf -- ' -f: install Open(F)low\n' >&2
    printf -- ' -h: print this (H)elp message\n' >&2
    printf -- ' -y: install R(y)u Controller\n' >&2
    printf -- ' -0: (default) -0[fx] installs OpenFlow 1.0 versions\n' >&2
    printf -- ' -3: -3[fx] installs OpenFlow 1.3 versions\n' >&2
    exit 2
}

OF_VERSION=1.0

if [ $# -eq 0 ]
then
    all
else
    while getopts 'acdfhy03' OPTION
    do
      case $OPTION in
      a)    all;;
      c)    cp_config_file;;
      d)    vm_clean;;
      f)    case $OF_VERSION in
            1.0) of;;
            1.3) of13;;
            *)  echo "Invalid OpenFlow version $OF_VERSION";;
            esac;;
      h)    usage;;
      y)    ryu;;
      0)    OF_VERSION=1.0;;
      3)    OF_VERSION=1.3;;
      ?)    usage;;
      esac
    done
    shift $(($OPTIND - 1))
fi
