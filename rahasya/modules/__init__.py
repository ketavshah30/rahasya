import pkgutil
import importlib
import inspect
from typing import Dict, List, Optional, Type

from rahasya.config import Settings, settings
from rahasya.modules.base import BaseModule
from rahasya.core.models import EntityType
from rahasya.utils.logging import get_logger

logger = get_logger("module_registry")

class ModuleRegistry:
    _module_classes: Dict[str, Type[BaseModule]] = {}

    def __init__(self, config: Optional[Settings] = None, auto_discover: bool = True):
        self.config = config or settings
        if auto_discover and not self.__class__._module_classes:
            self.__class__.discover_modules()
        self._instances: Dict[str, BaseModule] = {}

    @classmethod
    def discover_modules(cls, package_name: str = "rahasya.modules"):
        package = importlib.import_module(package_name)
        for _, name, is_pkg in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
            if is_pkg:
                continue
            try:
                module = importlib.import_module(name)
                for item_name, item in inspect.getmembers(module):
                    if inspect.isclass(item) and issubclass(item, BaseModule) and item is not BaseModule:
                        cls.register_module(item)
            except Exception as e:
                logger.error(f"Failed to load module {name}: {e}")

    @classmethod
    def register_module(cls, module_class: Type[BaseModule]):
        cls._module_classes[module_class.__name__] = module_class
        logger.debug(f"Registered module: {module_class.__name__}")

    register = register_module

    @classmethod
    def get_module(cls, name: str) -> Type[BaseModule]:
        return cls._module_classes.get(name)

    @classmethod
    def get_all_modules(cls) -> Dict[str, Type[BaseModule]]:
        return cls._module_classes

    def _get_instance(self, module_class: Type[BaseModule]) -> BaseModule:
        key = module_class.__name__
        if key not in self._instances:
            self._instances[key] = module_class(self.config)
        return self._instances[key]

    def get_modules_for(self, entity_type: EntityType) -> List[BaseModule]:
        """Return available module instances that accept the given entity type."""
        modules: List[BaseModule] = []
        for module_class in self.__class__._module_classes.values():
            accepts = getattr(
                module_class,
                "accepts",
                getattr(module_class, "supported_entity_types", []),
            )
            if entity_type in accepts:
                module = self._get_instance(module_class)
                if module.is_available():
                    modules.append(module)
        return modules

    def get_modules_for_entity_type(self, entity_type: EntityType) -> List[BaseModule]:
        return self.get_modules_for(entity_type)
