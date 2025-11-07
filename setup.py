from setuptools import setup
from pathlib import Path
import os

def save_repo_path():

    """Finds the repository path and writes it to a file in the package."""

    # Get the directory where this script is running (which should be the repo root directory)
    repo_root = Path(__file__).parent.resolve()

    output_file = repo_root / "src" / "cli" / "utils" / "_repository_info.py"
    content = f'REPO_PATH = "{str(repo_root.as_posix())}"\n'

    with open(output_file, "w") as f:
        f.write(content)

save_repo_path()
setup()