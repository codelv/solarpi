import re
from setuptools import setup, find_packages


def find_version():
    with open("solarpi/__init__.py") as f:
        for line in f:
            m = re.search(r'version = [\'"](.+)["\']', line)
            if m:
                return m.group(1)
    raise Exception("Could not find version in solarpi/__init__.py")


setup(
    name="solar-pi",
    version=find_version(),
    description="Solar montior",
    long_description=open("readme.md").read(),
    long_description_content_type="text/markdown",
    author="CodeLV",
    author_email="frmdstryr@gmail.com",
    url="https://github.com/codelv/codelv",
    packages=find_packages(),
    include_package_data=True,
    install_requires=["aiosqlite", "aiohttp", "bleak", "jinja2"],
)
