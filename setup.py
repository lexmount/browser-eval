"""Bundle root-level files named in pyproject.toml as harness package data.

Their repository paths stay where profiles and frozen bindings name them, and
setuptools package data can only reach files inside a package directory.
"""
import tomllib
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithBundledData(build_py):
    def run(self):
        super().run()
        if self.editable_mode:
            return
        project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
        for relative in project["tool"]["browser-eval"]["bundled-data"]:
            target = Path(self.build_lib, "harness", "distribution_data", relative)
            self.mkpath(str(target.parent))
            self.copy_file(relative, str(target))


setup(cmdclass={"build_py": BuildWithBundledData})
