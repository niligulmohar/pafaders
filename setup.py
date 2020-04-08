from setuptools import find_packages, setup

setup(
    name="pafaders",
    use_scm_version=True,
    setup_requires=["setuptools_scm"],
    packages=find_packages("src"),
    package_dir={"": "src"},
    requirements=["pulsectl", "rtmidi", "mpris2"],
    entry_points={"console_scripts": ["pafaders = pafaders:main"]},
)
