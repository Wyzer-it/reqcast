import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import pytest

from reqcast.extract import Config


@pytest.fixture
def base_cfg() -> Config:
    return Config(
        id_pattern=r"^(?P<id>REQ-\d{3})(?:\s+(?P<title>\S.*))?$",
        body_label="Description",
    )
