from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("netaudio")
except PackageNotFoundError:
    __version__ = "0.1.4+local"
