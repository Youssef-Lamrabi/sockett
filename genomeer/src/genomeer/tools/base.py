from abc import ABC, abstractmethod
from typing import Dict, Any

class Tool(ABC):
    name: str
    description: str

    @abstractmethod
    def schema(self) -> Dict[str, Any]:
        ...

    @abstractmethod
    def run(self, **kwargs) -> Dict[str, Any]:
        ...
