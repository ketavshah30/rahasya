import pytest

try:
    from rahasya.core.modules import BaseModule, ModuleRegistry
    from rahasya.core.models import EntityType
    HAS_MODULES = True
except ImportError:
    HAS_MODULES = False

pytestmark = pytest.mark.skipif(not HAS_MODULES, reason="Modules module not found")

class DummyModule(BaseModule):
    name = "dummy"
    description = "Dummy module"
    supported_entity_types = [EntityType.PERSON]

    async def execute(self, entity):
        return []

def test_base_module_abstract():
    if not HAS_MODULES: return
    with pytest.raises(TypeError):
        BaseModule()

def test_is_available_default():
    if not HAS_MODULES: return
    mod = DummyModule()
    assert mod.is_available() is True

def test_is_available_with_key():
    if not HAS_MODULES: return
    class KeyModule(DummyModule):
        requires_api_key = True
        api_key_name = "test_key"
    
    mod = KeyModule()
    # Assuming the module checks some config/state for API key
    assert mod.is_available() is False

@pytest.mark.asyncio
async def test_safe_execute_error_handling():
    if not HAS_MODULES: return
    class ErrorModule(DummyModule):
        async def execute(self, entity):
            raise ValueError("Test error")
    
    mod = ErrorModule()
    res = await mod.safe_execute(None)
    assert res == []

def test_module_registry():
    if not HAS_MODULES: return
    registry = ModuleRegistry()
    registry.register(DummyModule)
    mods = registry.get_modules_for_entity_type(EntityType.PERSON)
    assert len(mods) > 0
    assert any(isinstance(m, DummyModule) for m in mods)
