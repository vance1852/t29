from setuptools import setup, find_packages

setup(
    name="night-audit",
    version="1.0.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "night-audit=night_audit.cli:main",
        ],
    },
    python_requires=">=3.8",
)
