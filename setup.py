from setuptools import setup

setup(
    name="pafaders",
    version="0.0.1",
    packages=["pafaders"],
    requirements=["pulsectl", "rtmidi", "mpris2"],
    entry_points={"console_scripts": ["pafaders = pafaders:main"],},
)
