import os
import threading

import uvicorn

from config_store import ConfigStore
from hmi import HMIHandler
from measurement import MeasurementEngine
from state import LogBuffer, StateStore
from webapp import AppContext, create_app


def _read_env_override(config_store: ConfigStore) -> None:
    config = config_store.snapshot()
    al_ip = os.environ.get("AL1322_IP")
    if al_ip:
        config.al1322_ip = al_ip
    poll = os.environ.get("POLL_INTERVAL_S")
    if poll:
        config.poll_interval_s = float(poll)
    hmi_port = os.environ.get("HMI_SERIAL_PORT")
    if hmi_port:
        config.hmi.serial_port = hmi_port
    hmi_baud = os.environ.get("HMI_BAUD")
    if hmi_baud:
        config.hmi.baud = int(hmi_baud)
    config_store.replace_config(config)


def main() -> None:
    config_path = os.environ.get("CONFIG_PATH", "config/config.json")
    config_store = ConfigStore(config_path)
    _read_env_override(config_store)

    log_buffer = LogBuffer(capacity=config_store.snapshot().log_capacity)
    state_store = StateStore()

    mock_mode = os.environ.get("MOCK_AL1322", "0") == "1"

    hmi_handler = HMIHandler(config_store, log_buffer)
    hmi_handler.start()

    measurement_engine = MeasurementEngine(
        config_store=config_store,
        state_store=state_store,
        log_buffer=log_buffer,
        hmi_handler=hmi_handler,
        mock_mode=mock_mode,
    )
    measurement_engine.start()

    context = AppContext(config_store, state_store, log_buffer)
    app = create_app(context)

    def _run_server() -> None:
        uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")

    server_thread = threading.Thread(target=_run_server, name="web-server", daemon=True)
    server_thread.start()

    try:
        while True:
            threading.Event().wait(1.0)
    except KeyboardInterrupt:
        measurement_engine.stop()
        hmi_handler.stop()


if __name__ == "__main__":
    main()
