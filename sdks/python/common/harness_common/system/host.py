"""Host identity and access configuration; operations belong to toolbox."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Host:
    """An optional constituent of an Environment.

    For a host environment this machine hosts the target. For Kubernetes it
    provides cluster access, and kubeconfig paths refer to its filesystem.
    SSH addresses use the caller's SSH configuration; credentials are not stored.
    """

    name: str
    transport: str = "local"
    address: str = ""

    def validate(self) -> None:
        if not self.name:
            raise ValueError("host requires name")
        if self.transport in {"", "local"}:
            if self.address:
                raise ValueError("local host cannot specify SSH address")
        elif self.transport == "ssh":
            if not self.address or self.address.startswith("-"):
                raise ValueError("SSH host requires a non-option address")
        else:
            raise ValueError(f"unknown host transport {self.transport!r}")


def parse_host(data: dict) -> Host:
    """Parse the shared Host wire representation."""
    if not isinstance(data, dict):
        raise ValueError("host must be a mapping")
    unknown = set(data) - {"name", "transport", "address"}
    if unknown:
        raise ValueError(
            f"host contains unknown field(s): {', '.join(sorted(unknown))}"
        )
    if any(not isinstance(value, str) for value in data.values()):
        raise ValueError("host fields must be strings")
    host = Host(**data) if "name" in data else Host(name="", **data)
    host.validate()
    return host
