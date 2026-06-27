from datetime import datetime
import struct


class PacketProtocol:
    """
    Build and parse data packets exchanged with MDAQ.
    """

    WRITE_UUID = "00000001-8e22-4541-9d4c-21edae82ed19"
    READ_UUID = "00000002-8e22-4541-9d4c-21edae82ed19"

    @staticmethod
    def _write_fixed_field(packet: bytearray, offset: int, length: int, value: str):
        value_bytes = value.encode("ascii", errors="ignore")
        for i in range(length):
            packet[offset + i] = value_bytes[i] if i < len(value_bytes) else 0

    @staticmethod
    def calculate_crc(data: bytes) -> int:
        crc = 0
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ 0xD8) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
        return crc

    @staticmethod
    def build_register_bi(info: dict) -> bytes:
        packet = bytearray(122)
        packet[0] = ord("1")
        packet[1] = 122
        PacketProtocol._write_fixed_field(packet, 2, 50, info.get("Name", ""))
        PacketProtocol._write_fixed_field(packet, 52, 3, info.get("Age", ""))
        PacketProtocol._write_fixed_field(packet, 55, 16, info.get("UHI ID", ""))
        PacketProtocol._write_fixed_field(packet, 71, 10, info.get("MDAQ ID", ""))
        PacketProtocol._write_fixed_field(packet, 81, 4, info.get("WD Size", ""))
        PacketProtocol._write_fixed_field(packet, 85, 10, info.get("WD ID", ""))
        PacketProtocol._write_fixed_field(packet, 95, 7, info.get("Room Temperature", ""))
        PacketProtocol._write_fixed_field(packet, 102, 7, info.get("Body Temperature", ""))

        now = datetime.now()
        datetime_str = now.strftime("%d%m%y%H%M%S")
        PacketProtocol._write_fixed_field(packet, 109, 12, datetime_str)
        packet[121] = PacketProtocol.calculate_crc(packet[0:121])
        return bytes(packet)

    @staticmethod
    def build_start_screening() -> bytes:
        packet = bytearray(16)
        packet[0] = ord("2")
        packet[1] = 16
        packet[2] = ord("S")
        now = datetime.now()
        datetime_str = now.strftime("%d%m%y%H%M%S")
        PacketProtocol._write_fixed_field(packet, 3, 12, datetime_str)
        packet[15] = PacketProtocol.calculate_crc(packet[0:15])
        return bytes(packet)

    @staticmethod
    def build_stop_screening() -> bytes:
        packet = bytearray(16)
        packet[0] = ord("7")
        packet[1] = 16
        packet[2] = ord("T")
        now = datetime.now()
        datetime_str = now.strftime("%d%m%y%H%M%S")
        PacketProtocol._write_fixed_field(packet, 3, 12, datetime_str)
        packet[15] = PacketProtocol.calculate_crc(packet[0:15])
        return bytes(packet)

    @staticmethod
    def build_retrieve_data(uhid: str) -> bytes:
        packet = bytearray(21)
        packet[0] = ord("5")
        packet[1] = 21
        packet[2] = ord("R")
        PacketProtocol._write_fixed_field(packet, 3, 16, uhid)
        packet[19] = ord("T")
        packet[20] = PacketProtocol.calculate_crc(packet[0:20])
        return bytes(packet)

    @staticmethod
    def build_get_device_info(subject_id: str = "") -> bytes:
        packet = bytearray(21)
        packet[0] = ord("4")
        packet[1] = 21
        packet[2] = ord("R")
        PacketProtocol._write_fixed_field(packet, 3, 16, subject_id)
        packet[19] = ord("B")
        packet[20] = PacketProtocol.calculate_crc(packet[0:20])
        return bytes(packet)

    @staticmethod
    def build_find_device() -> bytes:
        packet = bytearray(16)
        packet[0] = ord("6")
        packet[1] = 16
        packet[2] = ord("A")
        now = datetime.now()
        datetime_str = now.strftime("%d%m%y%H%M%S")
        PacketProtocol._write_fixed_field(packet, 3, 12, datetime_str)
        packet[15] = PacketProtocol.calculate_crc(packet[0:15])
        return bytes(packet)

    @staticmethod
    def build_initiation() -> bytes:
        """Build the initiation packet (sets device date/time)."""
        packet = bytearray(16)
        packet[0] = ord("0")
        packet[1] = 16
        packet[2] = ord("I")
        now = datetime.now()
        datetime_str = now.strftime("%d%m%y%H%M%S")
        PacketProtocol._write_fixed_field(packet, 3, 12, datetime_str)
        packet[15] = PacketProtocol.calculate_crc(packet[0:15])
        return bytes(packet)

    @staticmethod
    def parse_response(data: bytes) -> dict:
        if not data or len(data) < 2:
            return {"raw": data.hex() if data else ""}

        packet_length = data[1]
        if packet_length > 0 and packet_length <= len(data):
            data = bytes(data[:packet_length])

        req_type = data[0]
        result = {
            "raw": data.hex(),
            "req_type": req_type,
            "packet_length": packet_length,
            "type": "unknown",
        }

        def ack_type(char_code: int) -> str:
            return {
                ord("1"): "bi_ack",
                ord("2"): "start_screening_ack",
                ord("4"): "download_basic_info_ack",
                ord("5"): "retrieve_data_ack",
                ord("6"): "buzzer_ack",
                ord("7"): "stop_screening_ack",
            }.get(char_code, "ack")

        if len(data) == 7:
            result["type"] = ack_type(req_type)
            result["error_code"] = (data[2] << 8) | data[3]
            result["status"] = chr(data[4]) if 32 <= data[4] <= 126 else str(data[4])
            result["battery_percentage"] = data[5]
            result["checksum"] = data[6]
            return result

        if packet_length == 16 and len(data) >= 16:
            result.update({
                "type": "device_summary",
                "error_code": (data[2] << 8) | data[3],
                "device_id": bytes(data[4:14]).decode("ascii", errors="ignore").rstrip("\x00"),
                "battery_percentage": data[14],
                "checksum": data[15],
            })
            return result

        if packet_length == 20 and len(data) >= 20:
            result.update({
                "type": "device_summary",
                "error_code": (data[2] << 8) | data[3],
                "device_id": bytes(data[4:14]).decode("ascii", errors="ignore").rstrip("\x00"),
                "battery_percentage": data[14],
                "checksum": data[19],
            })
            return result

        if packet_length == 30 and len(data) >= 30:
            result.update({
                "type": "retrieve_device_info",
                "error_code": (data[2] << 8) | data[3],
                "device_id": bytes(data[4:14]).decode("ascii", errors="ignore").rstrip("\x00"),
                "wd_size": bytes(data[14:18]).decode("ascii", errors="ignore").rstrip("\x00"),
                "wd_id": bytes(data[18:28]).decode("ascii", errors="ignore").rstrip("\x00"),
                "battery_percentage": data[28],
                "checksum": data[29],
            })
            return result

        if packet_length == 126 and len(data) >= 126:
            result.update({
                "type": "device_info",
                "error_code": (data[2] << 8) | data[3],
                "status": chr(data[4]) if 32 <= data[4] <= 126 else str(data[4]),
                "name": bytes(data[5:55]).decode("ascii", errors="ignore").rstrip("\x00"),
                "subject_age": bytes(data[55:58]).decode("ascii", errors="ignore").rstrip("\x00"),
                "uhi_id": bytes(data[58:74]).decode("ascii", errors="ignore").rstrip("\x00"),
                "device_id": bytes(data[74:84]).decode("ascii", errors="ignore").rstrip("\x00"),
                "wd_size": bytes(data[84:88]).decode("ascii", errors="ignore").rstrip("\x00"),
                "wd_id": bytes(data[88:98]).decode("ascii", errors="ignore").rstrip("\x00"),
                "room_temperature": bytes(data[98:105]).decode("ascii", errors="ignore").rstrip("\x00"),
                "body_temperature": bytes(data[105:112]).decode("ascii", errors="ignore").rstrip("\x00"),
                "date_time": bytes(data[112:124]).decode("ascii", errors="ignore").rstrip("\x00"),
                "battery_percentage": data[124],
                "checksum": data[125],
            })
            return result

        if packet_length >= 213 and len(data) >= 213:
            def parse_temps(endian: str):
                values = []
                for i in range(48):
                    offset = 18 + (i * 4)
                    if offset + 4 <= 210:
                        values.append(struct.unpack(f"{endian}f", data[offset:offset+4])[0])
                    else:
                        values.append(0.0)
                return values

            temperature_channels = parse_temps(">")
            if all(v == 0.0 for v in temperature_channels) and any(b != 0 for b in data[18:210]):
                little = parse_temps("<")
                if any(v != 0.0 for v in little):
                    temperature_channels = little

            result.update({
                "type": "retrieve_data",
                "error_code": (data[2] << 8) | data[3],
                "status": chr(data[4]) if 32 <= data[4] <= 126 else str(data[4]),
                "data_set": data[5],
                "date_time": bytes(data[6:18]).decode("ascii", errors="ignore").rstrip("\x00"),
                "temperature_data": temperature_channels,
                "validation_status": data[210],
                "battery_percentage": data[211],
                "checksum": data[212],
            })
            return result

        if len(data) >= 30:
            result.update({
                "type": "unknown",
                "error_code": (data[2] << 8) | data[3],
                "raw_data": data.hex(),
            })
            return result

        return result

    @staticmethod
    def decode_temperature(data: bytes) -> str:
        if len(data) % 4 != 0:
            return f"TEMP: {data.hex()}"
        values = [struct.unpack(">f", data[i:i+4])[0] for i in range(0, len(data), 4)]
        return " | ".join(f"{v:.2f}°C" for v in values)
