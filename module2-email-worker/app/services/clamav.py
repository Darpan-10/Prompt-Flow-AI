"""
ClamAV Malware Scanner — ZINSTSTREAM protocol.

Policy:
  IF infected OR scan fails:
    → Upload to s3://promptflow-quarantine/
    → Emit CloudWatch metric: MalwareDetected
    → DO NOT publish to Kafka
"""

import socket
import struct
import logging
from enum import Enum
from dataclasses import dataclass
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

CHUNK_SIZE = 4096


class ScanResult(str, Enum):
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    ERROR = "ERROR"


@dataclass
class ClamAVResult:
    result: ScanResult
    virus_name: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def is_safe(self) -> bool:
        return self.result == ScanResult.CLEAN

    @property
    def requires_quarantine(self) -> bool:
        return self.result in (ScanResult.INFECTED, ScanResult.ERROR)


def scan_bytes(file_bytes: bytes, filename: str = "attachment") -> ClamAVResult:
    """
    Scan raw bytes using ClamAV ZINSTSTREAM protocol.

    ZINSTSTREAM format:
      1. Send b"zINSTREAM\\0"
      2. Send chunks: 4-byte big-endian length + data
      3. Send 4-byte zero to terminate
      4. Read response
    """
    try:
        with socket.create_connection(
            (settings.clamav_host, settings.clamav_port),
            timeout=settings.clamav_timeout_seconds,
        ) as sock:
            # Initiate ZINSTSTREAM
            sock.sendall(b"zINSTREAM\0")

            # Stream file in chunks
            offset = 0
            while offset < len(file_bytes):
                chunk = file_bytes[offset : offset + CHUNK_SIZE]
                # 4-byte big-endian chunk length
                sock.sendall(struct.pack("!I", len(chunk)))
                sock.sendall(chunk)
                offset += len(chunk)

            # Terminate stream
            sock.sendall(struct.pack("!I", 0))

            # Read response
            response = b""
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                response += data
                if b"\0" in data or b"\n" in data:
                    break

        response_str = response.strip(b"\0\n").decode("utf-8", errors="replace")
        logger.debug("ClamAV response for '%s': %s", filename, response_str)

        if "OK" in response_str and "FOUND" not in response_str:
            return ClamAVResult(result=ScanResult.CLEAN)

        if "FOUND" in response_str:
            # Format: "stream: Eicar-Test-Signature FOUND"
            parts = response_str.split(":")
            virus_name = parts[-1].replace("FOUND", "").strip() if len(parts) > 1 else "Unknown"
            logger.warning(
                "MALWARE DETECTED in '%s': %s", filename, virus_name
            )
            return ClamAVResult(result=ScanResult.INFECTED, virus_name=virus_name)

        logger.error("Unexpected ClamAV response: %s", response_str)
        return ClamAVResult(
            result=ScanResult.ERROR,
            error_message=f"Unexpected response: {response_str}",
        )

    except socket.timeout:
        logger.error("ClamAV timeout scanning '%s'", filename)
        return ClamAVResult(
            result=ScanResult.ERROR,
            error_message="ClamAV connection timeout",
        )
    except ConnectionRefusedError:
        logger.error("ClamAV daemon not reachable at %s:%d", settings.clamav_host, settings.clamav_port)
        return ClamAVResult(
            result=ScanResult.ERROR,
            error_message="ClamAV daemon not reachable",
        )
    except Exception as e:
        logger.error("ClamAV scan failed for '%s': %s", filename, str(e))
        return ClamAVResult(
            result=ScanResult.ERROR,
            error_message=str(e),
        )
