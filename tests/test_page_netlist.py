import services.page_netlist as pn
from services.page_netlist import build_sheet_context


class _VS:
    """Minimal variant state — no DNP components."""
    def is_dnp(self, refdes: str) -> bool:
        return False


class _VS_DNP:
    """Variant state with all components DNP."""
    def __init__(self, dnp: set[str]):
        self._dnp = dnp

    def is_dnp(self, refdes: str) -> bool:
        return refdes in self._dnp


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


def _refdes_list(output: str) -> list[str]:
    return [
        line.split("|")[0]
        for line in output.split("\n")
        if "|" in line and not line.startswith(" ")
    ]


def _component_block(output: str, refdes: str) -> str:
    lines = output.split("\n")
    block, in_comp = [], False
    for line in lines:
        if line.startswith(f"{refdes}|"):
            in_comp = True
        elif in_comp and line and not line.startswith(" "):
            break
        if in_comp:
            block.append(line)
    return "\n".join(block)


def test_filters_components_by_sheet():
    result = build_sheet_context(_NETLIST, "MCU", _VS())
    assert _refdes_list(result) == ["U1"]
    assert "sheet:MCU" in result
    assert "total:1" in result


def test_cross_sheet_neighbor():
    result = build_sheet_context(_NETLIST, "MCU", _VS())
    u1 = _component_block(result, "U1")
    assert "R1" in u1


def test_cross_sheet_neighbor_has_at_sheet_suffix():
    result = build_sheet_context(_NETLIST, "MCU", _VS())
    u1 = _component_block(result, "U1")
    assert "@Comms" in u1


def test_dnp_annotation():
    result = build_sheet_context(_NETLIST, "MCU", _VS_DNP({"U1"}))
    assert "[DNP]" in _component_block(result, "U1")


def test_all_dnp_warning():
    result = build_sheet_context(_NETLIST, "MCU", _VS_DNP({"U1"}))
    assert "warning:all" in result
    assert "DNP" in result


def test_net_in_output():
    result = build_sheet_context(_NETLIST, "MCU", _VS())
    assert "MCU_TX" in result


_NETLIST_MULTI = {
    "components": {
        "C1": {
            "mpn": "GRM188R71C104KA01",
            "description": "CAP 100nF",
            "value": "100nF",
            "sheet": "MCU",
            "pins": {
                "1": {"name": "~", "net": "3V3"},
                "2": {"name": "~", "net": "GND"},
            },
        },
        "U1": {
            "mpn": "STM32G474",
            "description": "MCU",
            "value": None,
            "sheet": "MCU",
            "pins": {
                "PA9": {"name": "PA9", "net": "MCU_TX"},
            },
        },
    }
}


def test_has_more_false_when_fits():
    result = build_sheet_context(_NETLIST, "MCU", _VS())
    assert "has_more:false" in result


def test_single_component_no_pagination():
    result = build_sheet_context(_NETLIST, "Comms", _VS())
    assert "total:1" in result
    assert "has_more:false" in result


def test_pagination_has_more_true(monkeypatch):
    monkeypatch.setattr(pn, "_PAGE_CHAR_BUDGET", 10)
    result = build_sheet_context(_NETLIST_MULTI, "MCU", _VS())
    assert "has_more:true" in result
    assert "next:" in result


def test_pagination_offset_advances(monkeypatch):
    monkeypatch.setattr(pn, "_PAGE_CHAR_BUDGET", 10)
    result_p0 = build_sheet_context(_NETLIST_MULTI, "MCU", _VS())
    assert "C1" in result_p0
    assert "has_more:true" in result_p0
    result_p1 = build_sheet_context(_NETLIST_MULTI, "MCU", _VS(), offset=1)
    assert "U1" in result_p1
    assert "has_more:false" in result_p1


def test_high_fanout_rendering(monkeypatch):
    monkeypatch.setattr(pn, "_HIGH_FANOUT_THRESHOLD", 2)
    # GND net has U1.GND + R1.2 = 2 connections; threshold=2 triggers high-fanout path
    result = build_sheet_context(_NETLIST, "MCU", _VS())
    u1 = _component_block(result, "U1")
    assert "[HF:" in u1


def test_negative_offset_clamped_to_zero():
    result_neg = build_sheet_context(_NETLIST, "MCU", _VS(), offset=-5)
    result_zero = build_sheet_context(_NETLIST, "MCU", _VS(), offset=0)
    assert result_neg == result_zero


def test_all_dnp_warning_suppressed_on_later_pages(monkeypatch):
    monkeypatch.setattr(pn, "_PAGE_CHAR_BUDGET", 10)
    result_p1 = build_sheet_context(_NETLIST_MULTI, "MCU", _VS_DNP({"C1", "U1"}), offset=1)
    assert "warning:all" not in result_p1


def test_next_hint_contains_correct_offset_and_remaining(monkeypatch):
    monkeypatch.setattr(pn, "_PAGE_CHAR_BUDGET", 10)
    result = build_sheet_context(_NETLIST_MULTI, "MCU", _VS())
    assert "offset=1" in result
    assert "[1 remaining]" in result


def test_offset_beyond_total_returns_empty_page():
    result = build_sheet_context(_NETLIST, "MCU", _VS(), offset=999)
    assert "has_more:false" in result
    assert "total:1" in result
    # No component blocks should appear
    assert _refdes_list(result) == []


def test_oversized_component_warning(monkeypatch):
    monkeypatch.setattr(pn, "_PAGE_CHAR_BUDGET", 1)
    result = build_sheet_context(_NETLIST, "MCU", _VS())
    assert "warning:" in result
    assert "exceeds page budget" in result
