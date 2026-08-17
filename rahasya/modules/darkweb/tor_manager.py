import asyncio
import httpx
from httpx_socks import AsyncProxyTransport
import logging
import time

from rahasya.storage.network_audit import record_audit_event

class TorManager:
    """Manages Tor connections and circuit renewal for dark web modules."""
    
    def __init__(
        self,
        socks_host: str = "127.0.0.1",
        socks_port: int = 9050,
        control_port: int = 9051,
        password: str = "",
    ):
        self.socks_host = socks_host
        self.socks_port = socks_port
        self.control_port = control_port
        self.password = password
        self.logger = logging.getLogger("rahasya.tor_manager")
        self.proxy_url = f"socks5h://{self.socks_host}:{self.socks_port}"

    def _proxy_transport(self) -> AsyncProxyTransport:
        # python-socks resolves destination hostnames through SOCKS5 but its
        # URL parser accepts only socks5://, not the socks5h:// alias.
        transport_url = self.proxy_url.replace("socks5h://", "socks5://", 1)
        return AsyncProxyTransport.from_url(transport_url)
        
    async def check_tor_running(self) -> bool:
        url = "https://check.torproject.org/api/ip"
        started = time.monotonic()
        try:
            transport = self._proxy_transport()
            async with httpx.AsyncClient(transport=transport, timeout=10.0) as client:
                resp = await client.get(url)
                is_tor = resp.status_code == 200 and bool(resp.json().get("IsTor", False))
                record_audit_event(
                    "network_request",
                    outcome="success" if is_tor else "failed",
                    url=url,
                    method="GET",
                    status_code=resp.status_code,
                    duration_ms=round((time.monotonic() - started) * 1000, 2),
                    via_proxy=True,
                    purpose="tor_exit_verification",
                    message=None if is_tor else "Endpoint did not confirm a Tor exit connection",
                )
                if resp.status_code == 200:
                    return is_tor
        except Exception as exc:
            record_audit_event(
                "network_request",
                outcome="failed",
                url=url,
                method="GET",
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                via_proxy=True,
                purpose="tor_exit_verification",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return False
        return False
        
    def get_async_client(self) -> httpx.AsyncClient:
        transport = self._proxy_transport()
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
        url = "https://duckduckgogg42xjoc72x3sjianso2pfpt5obsmzjhoqcwxvtzgw.onion/"
        started = time.monotonic()
        client = None
        try:
            client = self.get_async_client()
            resp = await client.get(url)
            record_audit_event(
                "network_request",
                outcome="success" if resp.status_code == 200 else "http_error",
                url=url,
                method="GET",
                status_code=resp.status_code,
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                via_proxy=True,
                purpose="tor_onion_health_check",
            )
            return resp.status_code == 200
        except Exception as exc:
            record_audit_event(
                "network_request",
                outcome="failed",
                url=url,
                method="GET",
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                via_proxy=True,
                purpose="tor_onion_health_check",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return False
        finally:
            if client is not None:
                await client.aclose()
