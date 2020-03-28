#!/usr/bin/env python3

import logging
import time

import pulsectl
import rtmidi


# This port sends CC:s for every control in template 38 (Automap).
PORT_NAME = "ReMOTE ZeRO SL MIDI 3"

CHAN_16_CC = 0xBF

LOG = logging.getLogger(__name__)


def main():
    logging.basicConfig(level=logging.INFO)

    midiin = rtmidi.MidiIn()
    ports = midiin.get_ports()
    port_index = 0
    for index, name in enumerate(ports):
        if PORT_NAME in name:
            port_index = index
            break

    LOG.info("Opening port %d %r", port_index, ports[port_index])
    with midiin.open_port(port_index) as port:
        with pulsectl.Pulse("pafaders") as pulse:

            def set_volume(app_index, volume):
                try:
                    app = pulse.sink_input_list()[app_index]
                except IndexError:
                    return

                pulse.volume_set_all_chans(app, volume)

            def callback(event, data=None):
                octets, dt = event
                if octets[0] == CHAN_16_CC:
                    control, value = octets[1:]
                    if 16 <= control < 24:
                        app = control - 16
                        volume = value / 127.0
                        LOG.debug("App %d, volume %f", app, volume)
                        set_volume(app, volume)
                else:
                    LOG.debug("Event %r, data %r", event, data)

            port.set_callback(callback)

            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()