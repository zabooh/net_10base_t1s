"""
analyze_dependencies.py
-----------------------
Scans all Python files (.py) in the repository recursively, detects every
import statement, filters out Python standard-library modules, and writes
all found third-party module names to requirements.txt.

Usage:
    python analyze_dependencies.py [repo_root]

If repo_root is omitted the directory that contains this script is used.
"""

import ast
import importlib.util
import os
import pathlib
import sys

# ---------------------------------------------------------------------------
# Known standard-library top-level package names
# (covers Python 3.x; extend if needed)
# ---------------------------------------------------------------------------
_STDLIB_MODULES: set[str] = {
    "__future__", "_thread", "abc", "aifc", "argparse", "array", "ast",
    "asynchat", "asyncio", "asyncore", "atexit", "audioop", "base64",
    "bdb", "binascii", "binhex", "bisect", "builtins", "bz2", "calendar",
    "cgi", "cgitb", "chunk", "cmath", "cmd", "code", "codecs", "codeop",
    "collections", "colorsys", "compileall", "concurrent", "configparser",
    "contextlib", "contextvars", "copy", "copyreg", "cProfile", "csv",
    "ctypes", "curses", "dataclasses", "datetime", "dbm", "decimal",
    "difflib", "dis", "distutils", "doctest", "email", "encodings",
    "enum", "errno", "faulthandler", "fcntl", "filecmp", "fileinput",
    "fnmatch", "fractions", "ftplib", "functools", "gc", "getopt",
    "getpass", "gettext", "glob", "grp", "gzip", "hashlib", "heapq",
    "hmac", "html", "http", "idlelib", "imaplib", "imghdr", "imp",
    "importlib", "inspect", "io", "ipaddress", "itertools", "json",
    "keyword", "lib2to3", "linecache", "locale", "logging", "lzma",
    "mailbox", "mailcap", "marshal", "math", "mimetypes", "mmap",
    "modulefinder", "multiprocessing", "netrc", "nis", "nntplib",
    "numbers", "operator", "optparse", "os", "ossaudiodev", "pathlib",
    "pdb", "pickle", "pickletools", "pipes", "pkgutil", "platform",
    "plistlib", "poplib", "posix", "posixpath", "pprint", "profile",
    "pstats", "pty", "pwd", "py_compile", "pyclbr", "pydoc", "queue",
    "quopri", "random", "re", "readline", "reprlib", "resource",
    "rlcompleter", "runpy", "sched", "secrets", "select", "selectors",
    "shelve", "shlex", "shutil", "signal", "site", "smtpd", "smtplib",
    "sndhdr", "socket", "socketserver", "spwd", "sqlite3", "sre_compile",
    "sre_constants", "sre_parse", "ssl", "stat", "statistics", "string",
    "stringprep", "struct", "subprocess", "sunau", "symtable", "sys",
    "sysconfig", "syslog", "tabnanny", "tarfile", "telnetlib", "tempfile",
    "termios", "test", "textwrap", "threading", "time", "timeit",
    "tkinter", "token", "tokenize", "tomllib", "trace", "traceback",
    "tracemalloc", "tty", "turtle", "turtledemo", "types", "typing",
    "unicodedata", "unittest", "urllib", "uu", "uuid", "venv",
    "warnings", "wave", "weakref", "webbrowser", "winreg", "winsound",
    "wsgiref", "xdrlib", "xml", "xmlrpc", "zipapp", "zipfile",
    "zipimport", "zlib", "zoneinfo",
}

# Map import names that differ from the pip package name
_IMPORT_TO_PACKAGE: dict[str, str] = {
    "serial": "pyserial",
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "bs4": "beautifulsoup4",
    "yaml": "PyYAML",
    "wx": "wxPython",
    "gi": "PyGObject",
    "usb": "pyusb",
    "Crypto": "pycryptodome",
    "attr": "attrs",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "jwt": "PyJWT",
    "magic": "python-magic",
    "psutil": "psutil",
    "pkg_resources": "setuptools",
}


def _is_stdlib(name: str) -> bool:
    """Return True if *name* is a standard-library top-level module."""
    # Explicit third-party mapping always wins
    if name in _IMPORT_TO_PACKAGE:
        return False
    if name in _STDLIB_MODULES:
        return True
    # Fall back to importlib for any module that might be missing from our list
    try:
        spec = importlib.util.find_spec(name)
        if spec is None:
            return False
        origin = spec.origin or ""
        # stdlib modules live inside the Python installation prefix, not in
        # site-packages
        site_pkg_marker = os.sep + "site-packages" + os.sep
        return site_pkg_marker not in origin and bool(origin)
    except (ModuleNotFoundError, ValueError):
        return False


def _collect_top_level_imports(path: pathlib.Path) -> set[str]:
    """Parse *path* with the AST and return all top-level import names."""
    names: set[str] = set()
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        print(f"  [WARN] Could not parse {path} (SyntaxError) — skipping")
        return names

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # Only the top-level component matters for pip install
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module.split(".")[0])

    return names


def analyze(repo_root: pathlib.Path) -> list[str]:
    """
    Walk *repo_root* recursively, collect all third-party import names,
    and return a sorted list of pip package names.
    """
    all_imports: set[str] = set()
    py_files = sorted(repo_root.rglob("*.py"))

    # Build set of local module names (files that live inside the repo)
    local_modules: set[str] = {f.stem for f in py_files}

    print(f"\nScanning {len(py_files)} Python file(s) under '{repo_root}' ...\n")
    for py_file in py_files:
        imports = _collect_top_level_imports(py_file)
        rel = py_file.relative_to(repo_root)
        if imports:
            print(f"  {rel}: {sorted(imports)}")
        all_imports.update(imports)

    print()

    # Filter stdlib, local, and relative imports
    third_party: set[str] = set()
    for name in all_imports:
        if not name:
            continue
        if name in local_modules:
            print(f"  [local]       {name}")
            continue
        if name in _STDLIB_MODULES:
            continue
        if _is_stdlib(name):
            print(f"  [stdlib]      {name}")
            continue
        third_party.add(name)

    # Translate import names to pip package names
    packages: list[str] = []
    for name in sorted(third_party):
        pkg = _IMPORT_TO_PACKAGE.get(name, name)
        packages.append(pkg)
        print(f"  [third-party] {name!r}  ->  pip install {pkg!r}")

    return sorted(set(packages))


def write_requirements(packages: list[str], output_path: pathlib.Path) -> None:
    """Write *packages* to *output_path* (requirements.txt format)."""
    with output_path.open("w", encoding="utf-8") as fh:
        fh.write("# Auto-generated by analyze_dependencies.py\n")
        fh.write("# Run this script again whenever Python files change.\n\n")
        for pkg in packages:
            fh.write(pkg + "\n")
    print(f"\nrequirements.txt written to: {output_path}")


def main() -> None:
    repo_root = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).parent
    repo_root = repo_root.resolve()

    if not repo_root.is_dir():
        print(f"[ERROR] Path does not exist or is not a directory: {repo_root}")
        sys.exit(1)

    packages = analyze(repo_root)

    if not packages:
        print("\nNo third-party dependencies found.")
    else:
        print(f"\nFound {len(packages)} third-party package(s): {packages}")

    output_path = repo_root / "requirements.txt"
    write_requirements(packages, output_path)
    print("\nDone. Next step: run install_dependencies.bat to install all packages.\n")


if __name__ == "__main__":
    main()
