from setuptools import setup, find_packages

setup(
    name="termstory",
    version="0.5.0",
    author="TermStory Contributors",
    description="Local shell history parsing, session grouping, and visual daily chronicle terminal dashboard",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/bitflicker64/Termstory",
    packages=find_packages(exclude=["tests", "tests.*"]),
    include_package_data=True,
    install_requires=[
        "typer>=0.9.0",
        "python-dateutil>=2.8.2",
        "rich>=13.0.0",
        "textual>=0.50.0",
        "pillow>=10.0.0",
     ],
    extras_require={
        "test": [
            "pytest>=7.0.0",
            "pytest-asyncio>=0.21.0",
        ],
        "rag": [
            "sentence-transformers>=2.2.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "termstory=termstory.cli:cli",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.9",
)
