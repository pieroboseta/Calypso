from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
ASSETS = ROOT / "assets"
DATA = ROOT / "data"
LIBRARY = ROOT / "library"

MAIN = SRC / "main.py"
ICON = ASSETS / "icon.ico"
LOGO = ASSETS / "logo.png"
THEME = ASSETS / "calypso_theme.json"


def validate_project() -> None:
    required_files = [
        MAIN,
        SRC / "gui.py",
        SRC / "core" / "ai.py",
        SRC / "ingest.py",
        SRC / "database.py",
        ICON,
        LOGO,
        THEME,
    ]

    missing = [path for path in required_files if not path.exists()]
    if missing:
        formatted = "\n".join(f"- {path}" for path in missing)
        raise FileNotFoundError(
            "The following required Calypso files are missing:\n"
            f"{formatted}"
        )

    with Image.open(ICON) as image:
        if image.format != "ICO":
            raise ValueError(f"Not a valid ICO file: {ICON}")

    DATA.mkdir(parents=True, exist_ok=True)
    LIBRARY.mkdir(parents=True, exist_ok=True)


def stop_running_calypso() -> None:
    if sys.platform != "win32":
        return

    subprocess.run(
        ["taskkill", "/F", "/IM", "Calypso.exe"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def clean_previous_build() -> None:
    for folder in (ROOT / "build", ROOT / "dist"):
        if folder.exists():
            shutil.rmtree(folder)

    spec_file = ROOT / "Calypso.spec"
    if spec_file.exists():
        spec_file.unlink()


def build() -> None:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--windowed",
        "--onedir",
        "--contents-directory",
        ".",
        "--name",
        "Calypso",
        "--icon",
        str(ICON),
        "--paths",
        str(SRC),
        "--collect-all",
        "customtkinter",
        "--collect-all",
        "sentence_transformers",
        "--collect-data",
        "transformers",
        "--collect-data",
        "huggingface_hub",
        "--collect-data",
        "langchain_community",
        "--collect-data",
        "langchain_text_splitters",
        "--collect-submodules",
        "faiss",
        "--hidden-import",
        "docx",
        "--hidden-import",
        "pypdf",
        "--hidden-import",
        "windnd",
        "--hidden-import",
        "tqdm",
        "--add-data",
        f"{ASSETS};assets",
        "--add-data",
        f"{DATA};data",
        "--add-data",
        f"{LIBRARY};library",
        str(MAIN),
    ]

    subprocess.run(
        command,
        cwd=ROOT,
        check=True,
    )


def main() -> None:
    validate_project()
    stop_running_calypso()
    clean_previous_build()
    build()

    executable = ROOT / "dist" / "Calypso" / "Calypso.exe"
    if not executable.exists():
        raise FileNotFoundError(
            f"Build completed but the executable was not found: {executable}"
        )

    print()
    print("=" * 58)
    print("Calypso build completed successfully.")
    print(f"Executable: {executable}")
    print("=" * 58)


if __name__ == "__main__":
    main()