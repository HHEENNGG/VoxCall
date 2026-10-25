import unittest
from unittest import mock

from app import devices

APIS = [
    {"name": "MME", "devices": [0, 2], "default_input_device": 0, "default_output_device": 2},
    {"name": "Windows DirectSound", "devices": [1, 3], "default_input_device": -1, "default_output_device": -1},
    {"name": "Windows WASAPI", "devices": [4, 5], "default_input_device": 4, "default_output_device": 5},
]

_DEV = [
    ("Microphone", 0, 1, 0),
    ("Microphone", 1, 1, 0),
    ("CABLE Input", 0, 0, 2),
    ("CABLE Input", 1, 0, 2),
    ("Microphone", 2, 1, 0),
    ("CABLE Input", 2, 0, 2),
]

DEVICES = [
    {
        "name": name,
        "hostapi": api,
        "max_input_channels": ins,
        "max_output_channels": outs,
        "default_samplerate": 48000.0,
    }
    for name, api, ins, outs in _DEV
]


def _patch():
    return mock.patch.multiple(
        devices,
        hostapis=mock.Mock(return_value=APIS),
        sd=mock.Mock(
            query_devices=mock.Mock(return_value=DEVICES),
            query_hostapis=mock.Mock(return_value=APIS),
            default=mock.Mock(device=[0, 2], hostapi=0),
            WasapiSettings=mock.Mock(return_value="wasapi-settings"),
        ),
    )


class DeviceFilterTests(unittest.TestCase):
    def test_auto_shows_only_wasapi(self):
        with _patch():
            inputs = devices.list_devices("input", "auto")
            outputs = devices.list_devices("output", "auto")
        self.assertEqual([d.hostapi_name for d in inputs], ["Windows WASAPI"])
        self.assertEqual(len(inputs), 1)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0].name, "CABLE Input")

    def test_all_shows_every_api(self):
        with _patch():
            inputs = devices.list_devices("input", "all")
            self.assertEqual(len(inputs), 3)

    def test_specific_api_filters(self):
        with _patch():
            inputs = devices.list_devices("input", "MME")
            self.assertEqual(len(inputs), 1)
            self.assertEqual(inputs[0].hostapi_name, "MME")
            wasapi = devices.list_devices("input", "Windows WASAPI")
            self.assertEqual(len(wasapi), 1)

    def test_unknown_api_falls_back_to_everything(self):
        with _patch():
            inputs = devices.list_devices("input", "Nonexistent API")
            self.assertEqual(len(inputs), 3)

    def test_resolve_respects_chosen_api(self):
        with _patch():
            dev = devices.resolve("CABLE Input", "output", "MME")
            self.assertEqual(dev.hostapi_name, "MME")
            dev2 = devices.resolve("CABLE Input", "output", "auto")
            self.assertEqual(dev2.hostapi_name, "Windows WASAPI")

    def test_default_output_prefers_cable_input(self):
        apis = [
            {"name": "Windows WASAPI", "devices": [0, 1], "default_input_device": 0, "default_output_device": -1}
        ]
        devs = [
            {"name": "Speakers", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2,
             "default_samplerate": 48000.0},
            {"name": "CABLE Input (VB-Audio Virtual Cable)", "hostapi": 0, "max_input_channels": 0,
             "max_output_channels": 2, "default_samplerate": 48000.0},
        ]
        with mock.patch.multiple(
            devices,
            hostapis=mock.Mock(return_value=apis),
            sd=mock.Mock(
                query_devices=mock.Mock(return_value=devs),
                query_hostapis=mock.Mock(return_value=apis),
                default=mock.Mock(device=[-1, -1], hostapi=0),
                WasapiSettings=mock.Mock(return_value=None),
            ),
        ):
            chosen = devices.default_device("output", "auto")
        self.assertIn("CABLE Input", chosen.name)

    def test_wasapi_settings_only_for_wasapi(self):
        with _patch():
            wasapi = devices.resolve("Microphone", "input", "auto")
            mme = devices.resolve("Microphone", "input", "MME")
            self.assertEqual(devices.wasapi_settings(wasapi), "wasapi-settings")
            self.assertIsNone(devices.wasapi_settings(mme))

    def test_hostapi_choices_include_auto_and_each_api(self):
        with _patch():
            values = [v for v, _ in devices.hostapi_choices()]
        self.assertIn("auto", values)
        self.assertIn("all", values)
        self.assertIn("Windows WASAPI", values)
        self.assertIn("MME", values)


if __name__ == "__main__":
    unittest.main()
