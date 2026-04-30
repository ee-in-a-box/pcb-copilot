import json
from services.page_netlist import build_sheet_context


class _VS:
    """Minimal variant state — no DNP components."""
    def is_dnp(self, refdes: str) -> bool:
        return False


_NETLIST = {
    "components": {
        "U1": {
            "mpn": "STM32G474",
            "description": "MCU",
            "value": None,
            "sheet": "MCU",
            "pins": {
                "PA9": {"name": "PA9", "net": "MCU_TX"},
                "GND": {"name": "GND", "net": "GND"},
            },
        },
        "R1": {
            "mpn": "RC0402",
            "description": "RES 10K",
            "value": "10K",
            "sheet": "Comms",
            "pins": {
                "1": {"name": "~", "net": "MCU_TX"},
                "2": {"name": "~", "net": "GND"},
            },
        },
    },
}


def test_build_sheet_context_returns_mcu_components():
    result = json.loads(build_sheet_context(_NETLIST, "MCU", _VS()))
    assert result["sheet"] == "MCU"
    refdes_list = [c["refdes"] for c in result["components"]]
    assert refdes_list == ["U1"]


def test_build_sheet_context_cross_sheet_neighbor():
    result = json.loads(build_sheet_context(_NETLIST, "MCU", _VS()))
    u1 = next(c for c in result["components"] if c["refdes"] == "U1")
    pa9_neighbors = u1["pins"]["PA9"]["connected_to"]
    assert any("R1" in n for n in pa9_neighbors)
