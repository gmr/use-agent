import importlib.metadata

try:
    __version__ = importlib.metadata.version('use-agent')
except importlib.metadata.PackageNotFoundError:
    __version__ = '0.0.0+unknown'

__all__ = ['__version__']
