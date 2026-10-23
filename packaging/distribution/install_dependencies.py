"""Install the same local SDK/modules for source, Docker and native builds."""
import json
import os
import sysconfig
from pathlib import Path
import subprocess
import sys
import tomllib

def normalize_runtime_editable_path(pth: Path) -> None:
    """Keep Python 3.11 site.py readable under legacy Windows code pages."""
    if not pth.is_file():
        return
    lines = []
    for line in pth.read_text(encoding="utf-8-sig").splitlines():
        if line.isascii():
            lines.append(line)
        elif line.startswith(("import ", "import\t", "#")):
            lines.append(line.encode("ascii", "backslashreplace").decode("ascii"))
        else:
            lines.append(f"import sys; sys.path.append({ascii(line)})")
    pth.write_text("\n".join(lines) + "\n", encoding="ascii")


def install(root):
    root = Path(root).resolve()
    manifest = root / 'distribution/manifest.json'
    if not manifest.is_file():
        return
    sdk = root / 'harness-engine/python/sdk'
    if (sdk / 'pyproject.toml').is_file():
        project = tomllib.loads((sdk / 'pyproject.toml').read_text())['project']
        dependencies = project.get('dependencies', [])
        runtime_requirements = [value for value in dependencies if value.startswith('deepseek-harness-runtime-bin')]
        if runtime_requirements:
            runtime = root / 'harness-engine/python/sdk-runtime'
            if not (runtime / 'pyproject.toml').is_file():
                raise ValueError('Local Harness SDK runtime source is required')
            # The bridge launches the exported Node engine explicitly. Editable
            # runtime installs keep its resolver available without fetching an
            # unrelated platform wheel (or publishing any private source).
            subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-deps', '-e', str(runtime)], check=True)
            if os.name == "nt":
                normalize_runtime_editable_path(
                    Path(sysconfig.get_paths()["purelib"])
                    / "_editable_impl_deepseek_harness_runtime_bin.pth"
                )
        dependencies = [value for value in dependencies if value not in runtime_requirements]
        if dependencies:
            subprocess.run([sys.executable, '-m', 'pip', 'install', *dependencies], check=True)
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-deps', str(sdk)], check=True)
    for relative in json.loads(manifest.read_text()).get('module_packages', []):
        directory = (root / relative).resolve()
        if not directory.is_relative_to((root / 'extensions').resolve()):
            raise ValueError('Invalid distribution module path')
        subprocess.run([sys.executable, '-m', 'pip', 'install', str(directory)], check=True)


if __name__ == '__main__':
    install(Path(__file__).resolve().parents[2])
