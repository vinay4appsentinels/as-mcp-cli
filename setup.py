from setuptools import setup, find_packages

setup(
    name="as-mcp-cli",
    version="0.1.0",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=["requests>=2.25.0"],
    entry_points={
        "console_scripts": [
            "as-mcp-cli=as_mcp_cli.cli:main",
        ],
    },
    python_requires=">=3.8",
)
