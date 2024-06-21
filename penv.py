# Copyright (c) 2014-present PlatformIO <contact@platformio.org>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
import os
import platform
import subprocess
import time

import click

from pioinstaller import __version__, core, exception, python, util

log = logging.getLogger(__name__)


VIRTUALENV_URL = "https://bootstrap.pypa.io/virtualenv/virtualenv.pyz"
PIP_URL = "https://bootstrap.pypa.io/get-pip.py"


def get_penv_dir(path=None):
    if os.getenv("PLATFORMIO_PENV_DIR"):
        return os.getenv("PLATFORMIO_PENV_DIR")

    core_dir = path or core.get_core_dir()
    return os.path.join(core_dir, "penv")


def get_penv_bin_dir(path=None):
    penv_dir = path or get_penv_dir()
    return os.path.join(penv_dir, "Scripts" if util.IS_WINDOWS else "bin")


def create_core_penv(penv_dir=None, ignore_pythons=None):
    penv_dir = penv_dir or get_penv_dir()

    click.echo("Creating a virtual environment at %s" % penv_dir)

    result_dir = None
    for python_exe in python.find_compatible_pythons(ignore_pythons):
        result_dir = create_virtualenv(python_exe, penv_dir)
        if result_dir:
            break

    if not result_dir and not python.is_portable():
        python_exe = python.fetch_portable_python(os.path.dirname(penv_dir))
        if python_exe:
            result_dir = create_virtualenv(python_exe, penv_dir)

    if not result_dir:
        raise exception.PIOInstallerException(
            "Could not create PIO Core Virtual Environment. Please report to "
            "https://github.com/platformio/platformio-core-installer/issues"
        )

    python_exe = os.path.join(
        get_penv_bin_dir(penv_dir), "python.exe" if util.IS_WINDOWS else "python"
    )
    init_state(python_exe, penv_dir)
    update_pip(python_exe, penv_dir)
    click.echo("Virtual environment has been successfully created!")
    return result_dir


def create_virtualenv(python_exe, penv_dir):
    log.debug("Using %s Python for virtual environment.", python_exe)
    try:
        return create_with_local_venv(python_exe, penv_dir)
    except Exception as e:  # pylint:disable=broad-except
        log.debug(
            "Could not create virtualenv with local packages"
            " Trying download virtualenv script and using it. Error: %s",
            str(e),
        )
        try:
            return create_with_remote_venv(python_exe, penv_dir)
        except Exception as exc:  # pylint:disable=broad-except
            log.debug(
                "Could not create virtualenv with downloaded script. Error: %s",
                str(exc),
            )
    return None


def create_with_local_venv(python_exe, penv_dir):
    venv_cmd_options = [
        [python_exe, "-m", "venv", penv_dir],
        [python_exe, "-m", "virtualenv", "-p", python_exe, penv_dir],
        ["virtualenv", "-p", python_exe, penv_dir],
        [python_exe, "-m", "virtualenv", penv_dir],
        ["virtualenv", penv_dir],
    ]
    last_error = None
    for command in venv_cmd_options:
        util.safe_remove_dir(penv_dir)
        log.debug("Creating virtual environment: %s", " ".join(command))
        try:
            subprocess.run(command, check=True)
            return penv_dir
        except Exception as e:  # pylint:disable=broad-except
            last_error = e
    raise last_error  # pylint:disable=raising-bad-type


def create_with_remote_venv(python_exe, penv_dir):
    util.safe_remove_dir(penv_dir)

    log.debug("Downloading virtualenv package archive")
    venv_script_path = util.download_file(
        VIRTUALENV_URL,
        os.path.join(
            os.path.dirname(penv_dir), ".cache", "tmp", os.path.basename(VIRTUALENV_URL)
        ),
    )
    if not venv_script_path:
        raise exception.PIOInstallerException("Could not find virtualenv script")
    command = [python_exe, venv_script_path, penv_dir]
    log.debug("Creating virtual environment: %s", " ".join(command))
    subprocess.run(command, check=True)
    return penv_dir


def init_state(python_exe, penv_dir):
    version_code = (
        "import sys; version=sys.version_info; "
        "print('%d.%d.%d'%(version[0],version[1],version[2]))"
    )
    python_version = (
        subprocess.check_output(
            [python_exe, "-c", version_code], stderr=subprocess.PIPE
        )
        .decode()
        .strip()
    )
    state = {
        "created_on": int(round(time.time())),
        "python": {
            "path": python_exe,
            "version": python_version,
        },
        "installer_version": __version__,
        "platform": {
            "platform": platform.platform(),
            "release": platform.release(),
        },
    }
    return save_state(state, penv_dir)


def load_state(penv_dir=None):
    penv_dir = penv_dir or get_penv_dir()
    state_path = os.path.join(penv_dir, "state.json")
    if not os.path.isfile(state_path):
        raise exception.PIOInstallerException(
            "Could not found state.json file in `%s`" % state_path
        )
    with open(state_path) as fp:
        return json.load(fp)


def save_state(state, penv_dir=None):
    penv_dir = penv_dir or get_penv_dir()
    state_path = os.path.join(penv_dir, "state.json")
    with open(state_path, "w") as fp:
        json.dump(state, fp)
    return state_path


def update_pip(python_exe, penv_dir):
    click.echo("Updating Python package manager (PIP) in the virtual environment")
    try:
        log.debug("Creating pip.conf file in %s", penv_dir)
        with open(os.path.join(penv_dir, "pip.conf"), "w") as fp:
            fp.write("\n".join(["[global]", "user=no"]))

        try:
            log.debug("Updating PIP ...")
            subprocess.run(
                [python_exe, "-m", "pip", "install", "-U", "pip"], check=True
            )
        except subprocess.CalledProcessError as e:
            log.debug(
                "Could not update PIP. Error: %s",
                str(e),
            )
            log.debug("Downloading 'get-pip.py' installer...")
            get_pip_path = os.path.join(
                os.path.dirname(penv_dir), ".cache", "tmp", os.path.basename(PIP_URL)
            )
            util.download_file(PIP_URL, get_pip_path)
            log.debug("Installing PIP ...")
            subprocess.run([python_exe, get_pip_path], check=True)

        click.echo("PIP has been successfully updated!")
        return True
    except Exception as e:  # pylint:disable=broad-except
        log.debug(
            "Could not install PIP. Error: %s",
            str(e),
        )
        return False
