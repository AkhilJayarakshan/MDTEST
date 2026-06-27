import asyncio
import threading
import queue

from protocol import PacketProtocol

try:
    from bleak import BleakScanner, BleakClient
    BLE_AVAILABLE = True
except ImportError:
    BLE_AVAILABLE = False
    print("[ERROR] Bleak library is not installed.")


class BLEManager:
    def __init__(self, event_queue: queue.Queue):
        self.q = event_queue
        self.client: "BleakClient | None" = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._connected: bool = False
        self._start_loop()

    def _start_loop(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._thread.start()

    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def scan(self):
        self._run(self._scan())

    async def _scan(self):
        self.q.put(("ble_scan_start", None))
        try:
            if BLE_AVAILABLE:
                devices = await BleakScanner.discover(timeout=5.0)
                result = [{"name": d.name or "Unknown", "address": d.address} for d in devices]
            else:
                await asyncio.sleep(2)
                self.q.put(("ble_error", "Bleak library is not installed."))
                return
            self.q.put(("ble_scan_done", result))
        except Exception as e:
            self.q.put(("ble_error", str(e)))

    def connect(self, address: str):
        self._run(self._connect(address))

    async def _connect(self, address: str):
        try:
            if not BLE_AVAILABLE:
                self.q.put(("ble_error", "Bleak library is not installed."))
                return

            # Create client
            self.client = BleakClient(address)

            # Connect to the device
            await self.client.connect()

            # (Optional) Start notifications
            await self.client.start_notify(
                PacketProtocol.READ_UUID,
                self._on_notify
            )

            self._connected = True
            self.q.put(("ble_connected", address))

        except Exception as e:
            self.q.put(("ble_error", str(e)))

    def disconnect(self):
        self._run(self._disconnect())

    async def _disconnect(self):
        try:
            if BLE_AVAILABLE and self.client and self.client.is_connected:
                await self.client.disconnect()
            self.client = None
            self._connected = False
            self.q.put(("ble_disconnected", None))
        except Exception as e:
            self.q.put(("ble_error", str(e)))

    def write(self, data: bytes, expect_response_key: str = ""):
        self._run(self._write(data, expect_response_key))

    async def _write(self, data: bytes, expect_key: str):
        try:
            if BLE_AVAILABLE and self.client:
                await self.client.write_gatt_char(PacketProtocol.WRITE_UUID, data, response=True)
            else:
                self.q.put(("ble_error", "Bleak library is not installed."))
                return
        except Exception as e:
            self.q.put(("ble_error", str(e)))

    def _on_notify(self, sender, data: bytearray):
        from protocol import PacketProtocol

        parsed = PacketProtocol.parse_response(bytes(data))
        # # If connect() is awaiting initiation response, satisfy that future with the first notification
        # fut = getattr(self, "_init_fut", None)
        # if fut is not None and not fut.done():
        #     try:
        #         fut.set_result(parsed)
        #     except Exception:
        #         pass

        self.q.put(("ble_notify", parsed))

    @property
    def connected(self) -> bool:
        return self._connected
