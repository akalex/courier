from courier.adapters.matrix import MatrixDestination
from courier.adapters.waha import WAHATransport
from courier.config import load_config
from courier.main import _build_route


def test_route_builds_waha_source_and_matrix_destination(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
source_transport: waha
destination_transport: matrix
source_chat_id: source@g.us
target_chat_id: "!room:example.org"
matrix:
  homeserver_url: https://matrix.example.org
  access_token: token
""".strip(),
        encoding="utf-8",
    )

    source, destination = _build_route(load_config(path))

    assert isinstance(source, WAHATransport)
    assert isinstance(destination, MatrixDestination)
