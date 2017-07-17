# -*- coding: utf-8 -*-

from setuptools import setup, find_packages


with open('README.md') as f:
    readme = f.read()

with open('LICENSE') as f:
    license = f.read()

setup(
    name='vcflow',
    version='0.1.1',
    description='SDN/OpenFlow dynamic circuit provisioning system',
    long_description=readme,
    author='Pedro Diniz',
    author_email='pedrodiniz983@gmail.com',
    url='https://github.com/phdsbr/VCFlow',
    license=license,
    packages=find_packages(exclude=('tests', 'docs'))
)
