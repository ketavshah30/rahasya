import pkgutil
import importlib
import inspect
from typing import Dict, Type
from rahasya.modules.base import BaseModule
from rahasya.utils.logging import get_logger

logger = get_logger("module_registry")

class ModuleRegistry:
    _modules: Dict[str, Type[BaseModule]] = {}

    @classmethod
    def discover_modules(cls, package_name: str = "rahasya.modules"):
        package = importlib.import_module(package_name)
        for _, name, is_pkg in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
            try:
                module = importlib.import_module(name)
                for item_name, item in inspect.getmembers(module):
                    if inspect.isclass(item) and issubclass(item, BaseModule) and item is not BaseModule:
                        cls.register_module(item)
            except Exception as e:
                logger.error(f"Failed to load module {name}: {e}")

    @classmethod
    def register_module(cls, module_class: Type[BaseModule]):
        cls._modules[module_class.__name__] = module_class
        logger.debug(f"Registered module: {module_class.__name__}")

    @classmethod
    def get_module(cls, name: str) -> Type[BaseModule]:
        return cls._modules.get(name)

    @classmethod
    def get_all_modules(cls) -> Dict[str, Type[BaseModule]]:
        return cls._modules
