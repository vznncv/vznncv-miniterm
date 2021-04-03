import os
import os.path
import stat
import subprocess
import sys

import pytest


@pytest.fixture
def artifact_path():
    dist_dir = os.path.join(os.path.dirname(__file__), 'dist')
    if not os.path.isdir(dist_dir):
        raise ValueError(f"dist directory \"{dist_dir}\" does not exist")
    dist_files = [dir_entry.path for dir_entry in os.scandir(dist_dir) if dir_entry.is_file()]
    if len(dist_files) > 1:
        raise ValueError(f"Find multiple artifacts in the \"{dist_dir}\" directory")
    elif len(dist_files) == 0:
        raise ValueError(f"No artifacts are found in the \"{dist_dir}\" directory")
    artifact_path = dist_files[0]
    artifact_mode = os.stat(artifact_path).st_mode
    if not artifact_mode & stat.S_IEXEC:
        os.chmod(artifact_path, artifact_mode | stat.S_IEXEC)
    return artifact_path


def test_help(artifact_path):
    result = subprocess.run([artifact_path, '--help'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    result.check_returncode()


def test_list_ports(artifact_path):
    result = subprocess.run([artifact_path, '--list'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    result.check_returncode()


if __name__ == '__main__':
    # check minimal python version
    assert sys.version_info >= (3, 6)
    sys.exit(pytest.main(args=[__file__]))
