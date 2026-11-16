"""F-04: strict configuration validation (CLI and JSON share one validator)."""

import json
import math

import pytest

from backend.config import Config, ConfigError, load


@pytest.mark.parametrize(
    "over, needle",
    [
        ({"lan": "false"}, "lan must be true or false"),
        ({"stale_after_ms": math.nan}, "stale_after_ms must be finite"),
        ({"stale_after_ms": math.inf}, "stale_after_ms must be finite"),
        ({"udp_port": True}, "udp_port must be a whole number"),
        ({"web_port": 0}, "web_port must be within 1..65535"),
        ({"udp_port": 5250}, "used by the game"),
        ({"ui_rate_hz": "30"}, "ui_rate_hz must be a number"),
        ({"ui_rate_hz": 120}, "ui_rate_hz must be within"),
        ({"extra_allowed_hosts": "pc.local"}, "extra_allowed_hosts must be a list"),
        ({"extra_allowed_hosts": ["ok", 3]}, "extra_allowed_hosts must be a list"),
        ({"udp_host": "my pc"}, "udp_host must be an IP address"),
        ({"log_level": "LOUD"}, "log_level must be one of"),
        ({"equal_timestamp_policy": "accept"}, "equal_timestamp_policy"),
        ({"data_dir": 5}, "data_dir must be a folder path"),
    ],
)
def test_invalid_values_are_reported(over, needle):
    cfg = Config(**over)
    with pytest.raises(ConfigError) as e:
        cfg.validate()
    assert needle in str(e.value)


def test_defaults_are_valid_and_lan_string_does_not_widen_bind():
    Config().validate()
    cfg = Config(lan="false")
    assert cfg.problems()  # rejected before effective_web_host could be used


def test_all_problems_reported_together():
    with pytest.raises(ConfigError) as e:
        Config(lan="yes", udp_port=True, raw_rate_hz=-1).validate()
    assert len(e.value.problems) == 3


def write(tmp_path, text):
    p = tmp_path / "c.json"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_json_file_goes_through_same_validator(tmp_path):
    with pytest.raises(ConfigError, match="lan must be true or false"):
        load(["--config", write(tmp_path, json.dumps({"lan": "false"}))])


def test_json_nan_literal_rejected(tmp_path):
    with pytest.raises(ConfigError, match="NaN is not allowed"):
        load(["--config", write(tmp_path, '{"stale_after_ms": NaN}')])


def test_broken_json_reports_line(tmp_path):
    with pytest.raises(ConfigError, match="line 2"):
        load(["--config", write(tmp_path, '{\n "lan": tru }')])


def test_unknown_key_and_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="unknown keys"):
        load(["--config", write(tmp_path, '{"lan_mode": true}')])
    with pytest.raises(ConfigError, match="file not found"):
        load(["--config", str(tmp_path / "nope.json")])
    with pytest.raises(ConfigError, match="one JSON object"):
        load(["--config", write(tmp_path, "[1, 2]")])


def test_cli_values_validated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no pitwall.config.json here
    with pytest.raises(ConfigError, match="web_port must be within"):
        load(["--web-port", "0"])
    cfg = load(["--udp-port", "15607", "--log-level", "debug"])
    assert cfg.udp_port == 15607 and cfg.log_level == "DEBUG"
