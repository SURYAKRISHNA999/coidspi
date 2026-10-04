"""
Sandboxed tracer. Reads Python source from stdin, runs it with sys.settrace,
prints ONE JSON object to stdout:
  {"steps": [...], "error": null | {...}, "truncated": bool, "output": str}

Run by main.py as a subprocess with a timeout. Not a hard security boundary
(see README) - for public hosting, run inside Docker/gVisor or use Pyodide.
"""
import builtins
import io
import json
import sys
import types

MAX_STEPS = 500
MAX_OUT = 4000
FILE = "<user>"
ALLOWED_IMPORTS = {
    "abc", "aifc", "argparse", "array", "ast", "asyncio", "atexit",
    "audioop", "base64", "bdb", "binascii", "bisect", "builtins",
    "bz2", "calendar", "cgi", "cgitb", "chunk", "cmath", "cmd",
    "code", "codecs", "codeop", "collections", "colorsys", "compileall",
    "concurrent", "configparser", "contextlib", "contextvars", "copy",
    "copyreg", "cProfile", "crypt", "csv", "ctypes", "curses",
    "dataclasses", "datetime", "dbm", "decimal", "difflib", "dis",
    "doctest", "email", "encodings", "enum", "errno", "faulthandler",
    "fcntl", "filecmp", "fileinput", "fnmatch", "fractions", "ftplib",
    "functools", "gc", "getopt", "getpass", "gettext", "glob", "graphlib",
    "grp", "gzip", "hashlib", "heapq", "hmac", "html", "http", "imaplib",
    "imghdr", "imp", "importlib", "inspect", "io", "ipaddress", "itertools",
    "json", "keyword", "lib2to3", "linecache", "locale", "logging",
    "lzma", "mailbox", "mailcap", "marshal", "math", "mimetypes",
    "mmap", "modulefinder", "multiprocessing", "netrc", "nis", "nntplib",
    "numbers", "operator", "optparse", "os", "ossaudiodev", "pathlib",
    "pdb", "pickle", "pickletools", "pipes", "pkgutil", "platform",
    "plistlib", "poplib", "posix", "posixpath", "pprint", "profile",
    "pstats", "pty", "pwd", "py_compile", "pyclbr", "pydoc", "queue",
    "quopri", "random", "re", "readline", "reprlib", "resource",
    "rlcompleter", "runpy", "sched", "secrets", "select", "selectors",
    "shelve", "shlex", "shutil", "signal", "site", "smtplib", "sndhdr",
    "socket", "socketserver", "sqlite3", "ssl", "stat", "statistics",
    "string", "stringprep", "struct", "subprocess", "sunau", "symtable",
    "sys", "sysconfig", "syslog", "tabnanny", "tarfile", "telnetlib",
    "tempfile", "termios", "test", "textwrap", "threading", "time",
    "timeit", "tkinter", "token", "tokenize", "tomllib", "trace",
    "traceback", "tracemalloc", "tty", "turtle", "turtledemo", "types",
    "typing", "unicodedata", "unittest", "urllib", "uuid", "venv",
    "warnings", "wave", "weakref", "webbrowser", "winreg", "winsound",
    "wsgiref", "xdrlib", "xml", "xmlrpc", "zipapp", "zipfile", "zipimport",
    "zlib", "zoneinfo",

    "numpy", "pandas", "scipy", "sympy", "sklearn",
    "matplotlib", "seaborn", "plotly",
    "torch", "torchvision", "torchaudio", "tensorflow", "keras",
    "requests", "httpx", "aiohttp", "flask", "fastapi", "django",
    "pydantic", "sqlalchemy", "bs4", "lxml", "PIL", "cv2",
    "openpyxl", "xlsxwriter", "yaml", "dotenv", "tqdm", "rich",
    "networkx", "joblib", "numba", "psutil", "pytest",
    "transformers", "datasets", "tokenizers", "accelerate",
    "huggingface_hub", "openai", "anthropic", "ollama",
    "google", "google.generativeai",
}


class StepLimit(BaseException):  # BaseException so user `except Exception` can't swallow it
    pass


real_stdout = sys.stdout
real_import = builtins.__import__
buf = io.StringIO()
steps = []


def short(v, n=60):
    try:
        s = repr(v)
    except Exception:
        s = "<unrepr>"
    return s if len(s) <= n else s[: n - 1] + "…"


def snap_locals(frame):
    out = {}
    for k, v in list(frame.f_locals.items()):
        if k.startswith("__"):
            continue
        if isinstance(v, (types.FunctionType, types.ModuleType, type, types.BuiltinFunctionType)):
            continue
        out[k] = {"v": short(v), "t": type(v).__name__}
        if len(out) >= 20:
            break
    return out


def fname(frame):
    n = frame.f_code.co_name
    return "main" if n == "<module>" else n


def stack_of(frame):
    st, f = [], frame
    while f is not None:
        if f.f_code.co_filename == FILE:
            st.append({"name": fname(f), "line": f.f_lineno})
        f = f.f_back
    return st[::-1]


def tracer(frame, event, arg):
    code = frame.f_code
    if code.co_filename != FILE:
        return None
    if code.co_name.startswith("<") and code.co_name != "<module>":
        return None  # skip comprehensions / lambdas / genexprs
    if event == "call" and code.co_name == "<module>":
        return tracer
    if event in ("call", "line", "return", "exception"):
        if len(steps) >= MAX_STEPS:
            raise StepLimit()
        rec = {
            "line": frame.f_lineno,
            "event": event,
            "func": fname(frame),
            "locals": snap_locals(frame),
            "stack": stack_of(frame),
            "out": buf.getvalue()[-MAX_OUT:],
        }
        if event == "return":
            rec["ret"] = short(arg)
        if event == "exception":
            rec["exc"] = f"{arg[0].__name__}: {arg[1]}"
        steps.append(rec)
    return tracer


def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".")[0] not in ALLOWED_IMPORTS:
        raise ImportError(
            f"'{name}' is not available in the sandbox. Allowed modules: "
            + ", ".join(sorted(ALLOWED_IMPORTS))
        )
    return real_import(name, globals, locals, fromlist, level)

def safe_builtins():
    allowed = {
        "abs", "all", "any", "ascii",
        "bin", "bool", "bytearray", "bytes",
        "callable", "chr", "classmethod", "complex",
        "delattr", "dict", "dir", "divmod",
        "enumerate", "filter", "float", "format", "frozenset",
        "getattr", "hasattr", "hash", "hex", "id",
        "int", "isinstance", "issubclass",
        "iter", "len", "list", "map", "max", "memoryview",
        "min", "next", "object", "oct", "open", "ord",
        "pow", "print", "property", "range", "repr",
        "reversed", "round", "set", "setattr", "slice",
        "sorted", "staticmethod", "str", "sum", "super",
        "tuple", "type", "vars", "zip",
        "__build_class__"
    }

    b = {k: v for k, v in vars(builtins).items() if k in allowed}
    b["__import__"] = safe_import
    return b

def emit(result):
    real_stdout.write(json.dumps(result))
    real_stdout.flush()


def main():
    code = sys.stdin.read()
    result = {"steps": steps, "error": None, "truncated": False, "output": ""}
    try:
        compiled = compile(code, FILE, "exec")
    except SyntaxError as e:
        result["error"] = {"type": "SyntaxError", "msg": e.msg, "line": e.lineno}
        emit(result)
        return

    g = {"__name__": "__main__", "__builtins__": safe_builtins()}
    sys.stdout = buf
    sys.settrace(tracer)
    try:
        exec(compiled, g)
    except StepLimit:
        result["truncated"] = True
    except BaseException as e:  # noqa: BLE001
        line, tb = None, e.__traceback__
        while tb:
            if tb.tb_frame.f_code.co_filename == FILE:
                line = tb.tb_lineno
            tb = tb.tb_next
        result["error"] = {"type": type(e).__name__, "msg": str(e), "line": line}
    finally:
        sys.settrace(None)
        sys.stdout = real_stdout

    result["output"] = buf.getvalue()[-MAX_OUT:]
    if steps:
        steps.append({
            "line": None,
            "event": "end",
            "func": "main",
            "locals": steps[-1]["locals"],
            "stack": [],
            "out": result["output"],
        })
    emit(result)


main()
