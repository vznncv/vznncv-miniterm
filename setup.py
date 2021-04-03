#!/usr/bin/env python
# -*- coding: utf-8 -*-
import re

from setuptools import setup, find_namespace_packages

project_name = 'vznncv-miniterm'

with open('README.md') as readme_file:
    readme = readme_file.read()
readme = re.sub(r'!\[[^\[\]]*\]\S*', '', readme)

_locals = {}
with open('src/' + project_name.replace('-', '/') + '/_version.py') as fp:
    exec(fp.read(), None, _locals)
__version__ = _locals['__version__']

with open('requirements_dev.txt') as fp:
    test_requirements = fp.read()

setup(
    author="Konstantin Kochin",
    classifiers=[
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Topic :: Terminals :: Serial',
    ],
    description="Line buffered version of pyserial miniterm tool",
    long_description=readme,
    long_description_content_type="text/markdown",
    license='MIT',
    include_package_data=True,
    name=project_name,
    packages=find_namespace_packages(where='src'),
    package_dir={'': 'src'},
    entry_points={
        'console_scripts': [
            'vznncv-miniterm = vznncv.miniterm._cli:main',
        ]
    },
    install_requires=[
        'pyserial>=3.4,<4',
        'pyserial-asyncio>=0.4,<1',
        'prompt_toolkit>=3,<4',
    ],
    tests_require=test_requirements,
    version=__version__,
    python_requires='~=3.6',
)
