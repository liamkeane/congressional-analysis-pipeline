# Define the __all__ variable
__all__ = ["client", "endpoints", "CongressClient", "CongressAPIError"]

# Import submodules
from . import client
from . import endpoints
from .client import CongressClient, CongressAPIError
