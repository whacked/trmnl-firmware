import os

import registry
from registry import resolve


class Dev:
    def __init__(self, mac="44:1B:F6:81:A2:80", model="og", tags=None):
        self.mac = mac
        self.model = model
        self.tags = tags or []


def test_clients_dir_loads_default_and_example():
    registry.reset()
    clients_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "clients")
    registry.load_clients(clients_dir)
    # default must be set (catch-all) and example_group renderer registered
    assert resolve(Dev(tags=[])).fn is not None
    assert resolve(Dev(tags=["example"])).priority >= 0
