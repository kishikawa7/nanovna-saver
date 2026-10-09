import time

import serial


class PicoTempClient:
    def __init__(
        self, port: str, baudrate: int = 115200, timeout: float = 2.0
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser = None

    def open(self) -> None:
        self.ser = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
        time.sleep(2.0)  # Pico/MicroPython startup wait
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

        # Skip startup messages such as TEMP_SERVER_READY
        deadline = time.time() + 1.0
        while time.time() < deadline:
            line = self._readline_nonfatal()
            if not line:
                break

        try:
            if self.ping() != "PONG":
                raise RuntimeError("Pico did not respond to PING")
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self.ser is not None:
            try:
                self.ser.close()
            finally:
                self.ser = None

    def is_open(self) -> bool:
        return self.ser is not None

    def _ensure_open(self) -> None:
        if self.ser is None:
            raise RuntimeError("Serial port is not open")

    def _readline_nonfatal(self) -> str:
        self._ensure_open()
        return self.ser.readline().decode("utf-8", errors="replace").strip()

    def command(self, cmd: str) -> str:
        self._ensure_open()
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\n").encode("utf-8"))
        self.ser.flush()

        line = self.ser.readline().decode("utf-8", errors="replace").strip()
        if not line:
            raise TimeoutError(f"No response for command: {cmd}")
        return line

    def ping(self) -> str:
        return self.command("PING")

    def status(self) -> str:
        return self.command("STATUS")

    def read_temperature_c(self):
        line = self.command("READ")
        # Expected:
        #   T,24.8123
        #   ERR,FAULT,0x80
        #   ERR,EXC,...

        if line.startswith("ERR,FAULT,"):
            return None

        if line.startswith("ERR,"):
            return None

        parts = line.split(",")
        if len(parts) != 2 or parts[0] != "T":
            return None

        try:
            return float(parts[1])
        except ValueError:
            return None

    def read_raw(self) -> dict:
        line = self.command("READRAW")
        # RAW,<raw>,<res_ohm>,<temp_c>,<fault_hex>

        if line.startswith("ERR,"):
            raise RuntimeError(line)

        parts = line.split(",")
        if len(parts) != 5 or parts[0] != "RAW":
            raise RuntimeError(f"Unexpected response: {line}")

        return {
            "raw": int(parts[1]),
            "resistance_ohm": float(parts[2]),
            "temp_c": None if parts[3] == "NaN" else float(parts[3]),
            "fault": parts[4],
        }
