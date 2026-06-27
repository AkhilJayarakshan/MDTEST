import asyncio
from bleak import BleakClient, BleakScanner
from datetime import datetime
import struct
import time

# UUID for communication (adjust based on your device)
WRITE_UUID = "00000001-8e22-4541-9d4c-21edae82ed19"
READ_UUID = "00000002-8e22-4541-9d4c-21edae82ed19"

class BLEDevice:
    def __init__(self):
        self.device = None
        self.client = None
        self.notification_queue = None
        self.screening_active = False
    
    async def scan_devices(self):
        """Scan for all nearby BLE devices and return them"""
        print("Scanning for BLE devices...")
        devices = await BleakScanner.discover()
        
        if not devices:
            print("No devices found!")
            return []
        
        print("\n=== Available Devices ===")
        for i, device in enumerate(devices):
            print(f"{i}: {device.name} - {device.address} (RSSI: {device.rssi})")
        
        return devices
    
    def select_device(self, devices):
        """Allow user to select a device by name"""
        while True:
            device_name = input("\nEnter device name to connect: ").strip()
            
            for device in devices:
                if device.name and device_name.lower() in device.name.lower():
                    self.device = device
                    print(f"Selected device: {device.name} ({device.address})")
                    return device
            
            print("Device not found. Please try again.")

    async def discover_characteristics(self):
        """Connect to the selected device and list all services and characteristics

        Returns a list of tuples (service_uuid, char_uuid, properties)
        """
        if not self.device:
            print("No device selected for discovery!")
            return []

        print(f"\nConnecting to {self.device.name} to discover services...")
        chars = []
        try:
            async with BleakClient(self.device.address) as client:
                services = await client.get_services()
                print("\n=== Services & Characteristics ===")
                for service in services:
                    print(f"Service: {service.uuid} - {service.description}")
                    for char in service.characteristics:
                        props = ",".join(char.properties)
                        print(f"  Char: {char.uuid} (props: {props})")
                        chars.append((service.uuid, char.uuid, char.properties))
        except Exception as e:
            print(f"Error discovering characteristics: {e}")

        return chars
    
    def build_command_packet(self):
        """Build command packet according to specification
        
        Byte (0): Req_Type = '0'
        Byte (1): Packet Length = 16
        Byte (2): Initiate = 'I'
        Bytes (3-14): Date & Time (IST) in DDMMYYHHMSS format
        Byte (15): Checksum = CRC-8 over bytes 0-14
        """
        packet = bytearray(16)
        
        # Byte 0: Request Type '0'
        packet[0] = 48
        
        # Byte 1: Packet Length
        packet[1] = 16
        
        # Byte 2: Initiate command 'I'
        packet[2] = ord('I')
        
        # Bytes 3-14: Date & Time (IST) in DDMMYYHHMSS format
        now = datetime.now()
        datetime_str = now.strftime("%d%m%y%H%M%S")  # DDMMYYHHMSS
        datetime_bytes = datetime_str.encode('ascii')
        
        for i, byte in enumerate(datetime_bytes):
            if i < 12:  # Ensure we don't exceed the allocated space
                packet[3 + i] = byte
        
        # Byte 15: Checksum using CRC-8 with polynomial 0xD8
        packet[15] = self.calculate_crc(packet[0:15])
        
        return bytes(packet)

    def calculate_crc(self, data: bytes) -> int:
        """Calculate CRC-8 using polynomial 0xD8, same as the provided C implementation."""
        crc = 0
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ 0xD8) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
        return crc

    def write_fixed_field(self, packet, offset, length, value):
        """Write an ASCII field into packet at fixed width with null padding."""
        value_bytes = value.encode('ascii', errors='ignore')
        for i in range(length):
            packet[offset + i] = value_bytes[i] if i < len(value_bytes) else 0

    def build_set_config_packet(self):
        """Build the default Set Config Details command packet."""
        packet = bytearray(122)

        # Byte 0: Req_Type = '1'
        packet[0] = 49

        # Byte 1: Packet Length = 122
        packet[1] = 122

        # Default subject info values
        self.write_fixed_field(packet, 2, 50, "KK Kumari")
        self.write_fixed_field(packet, 52, 3, "35")
        self.write_fixed_field(packet, 55, 16, "UHI_HYD_RSH_0001")
        self.write_fixed_field(packet, 71, 10, "MD12345678")
        self.write_fixed_field(packet, 81, 4, "XXL")
        self.write_fixed_field(packet, 85, 10, "WD87654321")
        self.write_fixed_field(packet, 95, 7, "26.5")
        self.write_fixed_field(packet, 102, 7, "36.5")

        now = datetime.now()
        datetime_str = now.strftime("%d%m%y%H%M%S")
        self.write_fixed_field(packet, 109, 12, datetime_str)

        packet[121] = self.calculate_crc(packet[0:121])

        return bytes(packet)

    def build_download_basic_info_packet(self, subject_id: str = "UHI_HYD_RSH_0001"):
        """Build the Download Screening Data command packet."""
        packet = bytearray(21)

        # Byte 0: Req_Type = '4'
        packet[0] = ord('4')

        # Byte 1: Packet Length = 21
        packet[1] = 21

        # Byte 2: Command = 'R' for download screening data
        packet[2] = ord('R')

        # Bytes 3-18: Subject ID (UHI ID)
        self.write_fixed_field(packet, 3, 16, subject_id)

        # Byte 19: Packet Type = 'B'
        packet[19] = ord('B')

        # Byte 20: Checksum for first 20 bytes
        packet[20] = self.calculate_crc(packet[0:20])

        return bytes(packet)

    def build_download_temperature_data_packet(self, subject_id: str = "UHI_HYD_RSH_0001"):
        """Build the Download Temperature Data command packet."""
        packet = bytearray(21)

        # Byte 0: Req_Type = '5'
        packet[0] = ord('5')

        # Byte 1: Packet Length = 21
        packet[1] = 21

        # Byte 2: Command = 'R' for download data
        packet[2] = ord('R')

        # Bytes 3-18: Subject ID (UHI ID)
        self.write_fixed_field(packet, 3, 16, subject_id)

        # Byte 19: Packet Type = 'T' for temperature
        packet[19] = ord('T')

        # Byte 20: Checksum for first 20 bytes
        packet[20] = self.calculate_crc(packet[0:20])

        return bytes(packet)

    def build_buzzer_command_packet(self):
        """Build the Buzzer command packet."""
        packet = bytearray(16)

        # Byte 0: Req_Type = '6'
        packet[0] = ord('6')

        # Byte 1: Packet Length = 16
        packet[1] = 16

        # Byte 2: Alarm command = 'A'
        packet[2] = ord('A')

        # Bytes 3-14: Date & Time (IST) in DDMMYYHHMMSSS format (12 bytes)
        now = datetime.now()
        datetime_str = now.strftime("%d%m%y%H%M%S")  # DDMMYYHHMMSSS (12 bytes)
        self.write_fixed_field(packet, 3, 12, datetime_str)

        # Byte 15: Checksum
        packet[15] = self.calculate_crc(packet[0:15])

        return bytes(packet)

    def build_start_screening_packet(self):
        """Build the Start Screening command packet."""
        packet = bytearray(16)

        # Byte 0: Req_Type = '2'
        packet[0] = ord('2')

        # Byte 1: Packet Length = 16
        packet[1] = 16

        # Byte 2: Start Screening command = 'S'
        packet[2] = ord('S')

        # Bytes 3-14: Date & Time (IST) in DDMMYYHHMMSSS format (12 bytes)
        now = datetime.now()
        datetime_str = now.strftime("%d%m%y%H%M%S")
        self.write_fixed_field(packet, 3, 12, datetime_str)

        # Byte 15: Checksum
        packet[15] = self.calculate_crc(packet[0:15])

        return bytes(packet)

    def build_stop_screening_packet(self):
        """Build the Stop Screening command packet."""
        packet = bytearray(16)

        # Byte 0: Req_Type = '7'
        packet[0] = ord('7')

        # Byte 1: Packet Length = 16
        packet[1] = 16

        # Byte 2: Stop Screening command = 'T'
        packet[2] = ord('T')

        # Bytes 3-14: Date & Time (IST) in DDMMYYHHMMSSS format (12 bytes)
        now = datetime.now()
        datetime_str = now.strftime("%d%m%y%H%M%S")
        self.write_fixed_field(packet, 3, 12, datetime_str)

        # Byte 15: Checksum
        packet[15] = self.calculate_crc(packet[0:15])

        return bytes(packet)
    
    def parse_response_packet(self, data):
        """Parse either BI response packet or the previous full response packet."""
        if len(data) < 2:
            print("Response packet too short!")
            return None

        packet_length = data[1]
        if packet_length > 0 and packet_length <= len(data):
            data = bytes(data[:packet_length])

        if len(data) == 7:
            response = {
                'req_type': data[0],
                'packet_length': data[1],
                'error_code': (data[2] << 8) | data[3],
                'status': chr(data[4]) if 32 <= data[4] <= 126 else str(data[4]),
                'battery_percentage': data[5],
                'checksum': data[6]
            }
            return response

        if packet_length == 16 and len(data) >= 16:
            # Buzzer response: 16 bytes
            response = {
                'req_type': data[0],
                'packet_length': packet_length,
                'error_code': (data[2] << 8) | data[3],
                'device_id': bytes(data[4:14]).decode('ascii', errors='ignore').rstrip('\x00'),
                'battery_percentage': data[14],
                'checksum': data[15]
            }
            return response
        
        if packet_length == 20 and len(data) >= 20:
            # Buzzer response: 20 bytes (alternative format)
            response = {
                'req_type': data[0],
                'packet_length': packet_length,
                'error_code': (data[2] << 8) | data[3],
                'device_id': bytes(data[4:14]).decode('ascii', errors='ignore').rstrip('\x00'),
                'battery_percentage': data[14],
                'checksum': data[19]
            }
            return response

        if len(data) < 30:
            print("Response packet too short!")
            return None

        packet_length = data[1]
        if packet_length == 126 and len(data) >= 126:
            response = {
                'req_type': data[0],
                'packet_length': packet_length,
                'error_code': (data[2] << 8) | data[3],
                'status': chr(data[4]) if 32 <= data[4] <= 126 else str(data[4]),
                'name': bytes(data[5:55]).decode('ascii', errors='ignore').rstrip('\x00'),
                'subject_age': bytes(data[55:58]).decode('ascii', errors='ignore').rstrip('\x00'),
                'uhi_id': bytes(data[58:74]).decode('ascii', errors='ignore').rstrip('\x00'),
                'device_id': bytes(data[74:84]).decode('ascii', errors='ignore').rstrip('\x00'),
                'wd_size': bytes(data[84:88]).decode('ascii', errors='ignore').rstrip('\x00'),
                'wd_id': bytes(data[88:98]).decode('ascii', errors='ignore').rstrip('\x00'),
                'room_temperature': bytes(data[98:105]).decode('ascii', errors='ignore').rstrip('\x00'),
                'body_temperature': bytes(data[105:112]).decode('ascii', errors='ignore').rstrip('\x00'),
                'date_time': bytes(data[112:124]).decode('ascii', errors='ignore').rstrip('\x00'),
                'battery_percentage': data[124],
                'checksum': data[125]
            }
            return response

        if packet_length == 213 and len(data) >= 213:
            # Temperature data response: 48 channels of 4-byte floats (bytes 18-210)
            temperature_channels = []
            for i in range(48):
                offset = 18 + (i * 4)
                if offset + 4 <= 210:
                    temp_float = struct.unpack('>f', data[offset:offset+4])[0]
                    temperature_channels.append(temp_float)
            
            response = {
                'req_type': data[0],
                'packet_length': packet_length,
                'error_code': (data[2] << 8) | data[3],
                'status': chr(data[4]) if 32 <= data[4] <= 126 else str(data[4]),
                'data_set': data[5],
                'date_time': bytes(data[6:18]).decode('ascii', errors='ignore').rstrip('\x00'),
                'temperature_data': temperature_channels,
                'validation_status': data[210],
                'battery_percentage': data[211],
                'checksum': data[212]
            }
            return response
        
        response = {
            'req_type': data[0],
            'packet_length': data[1],
            'error_code': (data[2] << 8) | data[3],
            'device_id': bytes(data[4:14]).decode('ascii', errors='ignore'),
            'wd_size': struct.unpack('>f', data[14:18])[0],  # 4 bytes float
            'wd_id': bytes(data[18:28]).decode('ascii', errors='ignore'),
            'battery_percentage': data[28],
            'checksum': data[29]
        }
        
        return response
    
    def is_non_zero_response(self, data: bytes) -> bool:
        return any(byte != 0 for byte in data)

    async def write_packet(self, client, packet: bytes) -> bool:
        """Write a packet to the configured BLE write characteristic UUID."""
        try:
            await client.write_gatt_char(WRITE_UUID, packet)
            return True
        except Exception as e:
            print(f"Could not write packet to {WRITE_UUID}: {e}")
            return False

    async def send_and_wait(self, client, packet: bytes, label: str, timeout: int = 3) -> bool:
        """Write a packet and wait for a notification response using the
        shared `notification_queue` (notifications are started once on connect).
        """
        if self.notification_queue is None:
            print("Notification queue not initialized. Ensure start_notify was called on connect.")
            return False

        try:
            # Send the packet
            print(f"Sending {label} packet to {WRITE_UUID}: {packet.hex()}")
            if not await self.write_packet(client, packet):
                return False

            print(f"{label} packet sent! Waiting for notification on {READ_UUID}...")

            try:
                data = await asyncio.wait_for(self.notification_queue.get(), timeout=timeout)
                if data:
                    print(f"Received {label} response via notification: {data.hex()}")
                    response = self.parse_response_packet(data)
                    if response:
                        self.print_response(response)
                    return True
                else:
                    return False
            except asyncio.TimeoutError:
                print(f"No {label} notification received within {timeout} seconds")
                return False

        except Exception as e:
            print(f"Error during {label} notification: {e}")
            return False

    async def connect_and_send(self):
        """Connect to device and send packets in the required sequence."""
        if not self.device:
            print("No device selected!")
            return False

        try:
            async with BleakClient(self.device.address) as client:
                print(f"\nConnected to {self.device.name}")

                # Initialize a shared notification queue and start notifications once
                self.notification_queue = asyncio.Queue()

                def notification_handler(sender, data):
                    if data and any(byte != 0 for byte in data):
                        raw = bytes(data)
                        # always print raw notification bytes for debugging
                        print(f"[notify] Raw bytes: {raw.hex()}")
                        if self.screening_active:
                            print(f"\n[screening debug] Raw non-zero bytes: {bytes(data).hex()}")
                        loop = None
                        try:
                            loop = asyncio.get_running_loop()
                        except RuntimeError:
                            loop = asyncio.get_event_loop()
                        loop.call_soon_threadsafe(self.notification_queue.put_nowait, bytes(data))

                try:
                    await client.start_notify(READ_UUID, notification_handler)

                    # # Step 1: send initiation packet and wait for its response
                    # init_packet = self.build_command_packet()
                    # if not await self.send_and_wait(client, init_packet, "initiation"):
                    #     await client.stop_notify(READ_UUID)
                    #     self.notification_queue = None
                    #     return False
                    
                    # # # time.sleep(0.5)   # 500 milliseconds

                    # # Step 2: send set-config details packet and wait for its response
                    # config_packet = self.build_set_config_packet()
                    # if not await self.send_and_wait(client, config_packet, "set-config"):
                    #     await client.stop_notify(READ_UUID)
                    #     self.notification_queue = None
                    #     return False

                    # # Step 3: send download-basic-info packet and wait for its response
                    # download_packet = self.build_download_basic_info_packet()
                    # if not await self.send_and_wait(client, download_packet, "download-basic-info"):
                    #     await client.stop_notify(READ_UUID)
                    #     self.notification_queue = None
                    #     return False

                    # Step 4: send download-temperature-data packet and collect all response packets
                    temp_packet = self.build_download_temperature_data_packet()
                    # send without relying on single-response helper
                    if not await self.write_packet(client, temp_packet):
                        await client.stop_notify(READ_UUID)
                        self.notification_queue = None
                        return False

                    # Collect multiple incoming packets until an idle timeout elapses
                    collected = []
                    print("Waiting for temperature data packets (idle timeout 2s)...")
                    while True:
                        try:
                            data = await asyncio.wait_for(self.notification_queue.get(), timeout=2.0)
                            if data:
                                print(f"Received temperature packet: {data.hex()}")
                                resp = self.parse_response_packet(data)
                                if resp:
                                    self.print_response(resp)
                                    collected.append(resp)
                                # continue waiting for more packets
                                continue
                        except asyncio.TimeoutError:
                            # no packets for timeout => assume transmission finished
                            break

                    if not collected:
                        print("No temperature packets received after download request.")
                        await client.stop_notify(READ_UUID)
                        self.notification_queue = None
                        return False

                    # stop_screening_packet = self.build_stop_screening_packet()
                    # print("\nSending stop-screening command after 10 seconds")
                    # if not await self.send_and_wait(client, stop_screening_packet, "stop-screening", timeout=30):
                    #     print("Failed to receive stop-screening response.")
                    #     await client.stop_notify(READ_UUID)
                    #     self.notification_queue = None
                    #     return False
                
                    # # Step 5: send buzzer command and wait for its response
                    # buzzer_packet = self.build_buzzer_command_packet()
                    # if not await self.send_and_wait(client, buzzer_packet, "buzzer"):
                    #     await client.stop_notify(READ_UUID)
                    #     self.notification_queue = None
                    #     return False

                    # # Step 6: send start-screening command and wait for its response
                    # start_screening_packet = self.build_start_screening_packet()
                    # if not await self.send_and_wait(client, start_screening_packet, "start-screening"):
                    #     await client.stop_notify(READ_UUID)
                    #     self.notification_queue = None
                    #     return False

                    # self.screening_active = True
                    # print("\nStart screening active. Will stop after receiving 10 packets...")

                    # # Counter and event to signal when we've received 10 packets
                    # packet_counter = {"count": 0}
                    # stop_event = asyncio.Event()

                    # async def consume_streaming_data():
                    #     """Consume streaming packets during screening and count them."""
                    #     try:
                    #         while self.screening_active:
                    #             try:
                    #                 data = await asyncio.wait_for(
                    #                     self.notification_queue.get(),
                    #                     timeout=5  # check periodically
                    #                 )
                    #                 if data:
                    #                     print(f"Received streaming data: {data.hex()}")
                    #                     response = self.parse_response_packet(data)
                    #                     if response:
                    #                         self.print_response(response)
                    #                     # count every non-zero packet processed
                    #                     packet_counter["count"] += 1
                    #                     print(f"Streaming packets received: {packet_counter['count']}/10")
                    #                     if packet_counter["count"] >= 10:
                    #                         # signal main task to stop streaming
                    #                         self.screening_active = False
                    #                         stop_event.set()
                    #                         break
                    #             except asyncio.TimeoutError:
                    #                 continue
                    #     except asyncio.CancelledError:
                    #         pass

                    # # Start consuming data concurrently
                    # consumer_task = asyncio.create_task(consume_streaming_data())

                    # try:
                    #     # Wait until 10 packets have been received
                    #     await stop_event.wait()
                    # except asyncio.CancelledError:
                    #     self.screening_active = False
                    #     consumer_task.cancel()
                    #     try:
                    #         await consumer_task
                    #     except asyncio.CancelledError:
                    #         pass
                    #     print("\nScreening task cancelled. Stopping notification stream...")
                    #     await client.stop_notify(READ_UUID)
                    #     self.notification_queue = None
                    #     return False
                    # finally:
                    #     self.screening_active = False
                    #     consumer_task.cancel()
                    #     try:
                    #         await consumer_task
                    #     except asyncio.CancelledError:
                    #         pass

                    # Stop notifications once finished (do not send a stop-screening command)
                    await client.stop_notify(READ_UUID)
                    self.notification_queue = None
                    return True

                except Exception as e:
                    print(f"Notification setup/error: {e}")
                    try:
                        await client.stop_notify(READ_UUID)
                    except Exception:
                        pass
                    self.notification_queue = None
                    return False

        except Exception as e:
            print(f"Connection error: {e}")
            return False
    
    def print_response(self, response):
        """Pretty print the response data"""
        print("\n=== Response Packet ===")
        print(f"Request Type: {response['req_type']}")
        print(f"Packet Length: {response['packet_length']}")
        print(f"Error Code: {response['error_code']}")
        if 'status' in response:
            print(f"Status: {response['status']}")
        if 'name' in response:
            print(f"Name: {response['name']}")
        if 'subject_age' in response:
            print(f"Subject Age: {response['subject_age']}")
        if 'uhi_id' in response:
            print(f"UHI ID: {response['uhi_id']}")
        if 'device_id' in response:
            print(f"Device ID: {response['device_id']}")
        if 'wd_size' in response:
            print(f"WD Size: {response['wd_size']}")
        if 'wd_id' in response:
            print(f"WD ID: {response['wd_id']}")
        if 'room_temperature' in response:
            print(f"Room Temperature: {response['room_temperature']}")
        if 'body_temperature' in response:
            print(f"Body Temperature: {response['body_temperature']}")
        if 'date_time' in response:
            print(f"Date & Time: {response['date_time']}")
        if 'data_set' in response:
            print(f"Data Set: {response['data_set']}")
        if 'temperature_data' in response:
            print(f"Temperature Data (48 channels):")
            for idx, temp in enumerate(response['temperature_data']):
                print(f"  Channel {idx}: {temp:.4f}°C")
        if 'validation_status' in response:
            print(f"Validation Status: {response['validation_status']}")
        print(f"Battery Percentage: {response['battery_percentage']}%")
        print(f"Checksum: {response['checksum']}")
    
    async def run(self):
        """Main execution flow"""
        # Step 1: Scan for devices
        devices = await self.scan_devices()
        if not devices:
            return
        
        # Step 2: User selects device
        self.select_device(devices)
        
        # Optional: Discover and print all characteristics for the selected device
        try:
            choice = input("List characteristics for the selected device? (y/n): ").strip().lower()
        except Exception:
            choice = 'n'

        if choice == 'y':
            await self.discover_characteristics()
        
        # Step 3: Connect and send command
        success = await self.connect_and_send()
        if success:
            print("\nCommand completed successfully!")
        else:
            print("\nCommand failed!")


async def main():
    ble_device = BLEDevice()
    await ble_device.run()


if __name__ == "__main__":
    asyncio.run(main())
