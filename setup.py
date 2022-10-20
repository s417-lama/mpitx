from setuptools import setup, find_packages

setup(
    name="mpitx",
    version="0.0.1",
    author="Shumpei Shiina",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "mpitx = mpitx.mpitx:main",
        ],
    },
    install_requires=[
    ],
)
