import asyncio
import httpx
from httpx_socks import AsyncProxyTransport
import logging

class TorManager:
    """Manages Tor connections and circuit renewal for dark web modules."""
    
    def __init__(self, socks_port: int = 9050, control_port: int = 9051, password: str = ""):
        self.socks_port = socks_port
        self.control_port = control_port
        self.password = password
        self.logger = logging.getLogger("rahasya.tor_manager")
        self.proxy_url = f"socks5://127.0.0.1:{self.socks_port}"
        
    async def check_tor_running(self) -> bool:
        try:
            transport = AsyncProxyTransport.from_url(self.proxy_url)
            async with httpx.AsyncClient(transport=transport, timeout=10.0) as client:
                resp = await client.get("https://check.torproject.org/api/ip")
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("IsTor", False)
        except Exception:
            return False
        return False
        
    def get_async_client(self) -> httpx.AsyncClient:
        transport = AsyncProxyTransport.from_url(self.proxy_url)
        return httpx.AsyncClient(transport=transport, timeout=30.0)
        
    async def renew_circuit(self) -> bool:
        try:
            from stem import Signal
            from stem.control import Controller
            
            # Using synchronous stem here, wrapped if possible, but stem is sync by default
            def _renew():
                with Controller.from_port(port=self.control_port) as controller:
                    controller.authenticate(password=self.password)
                    controller.signal(Signal.NEWNYM)
            
            await asyncio.to_thread(_renew)
            await asyncio.sleep(3) # cooldown
            return True
        except ImportError:
            self.logger.error("stem library not installed.")
            return False
        except Exception as e:
            self.logger.error(f"Failed to renew Tor circuit: {e}")
            return False
            
    async def health_check(self) -> bool:
        """Test .onion connectivity."""
        try:
            # DuckDuckGo Onion
            url = "https://duckduckgogg42xjoc72x3sjianso2pfpt5obsmzjhoqcwxvtzgw.onion/"
            client = self.get_async_client()
            resp = await client.get(url)
            await client.aclose()
            return resp.status_code == 200
        except Exception:
            return False
