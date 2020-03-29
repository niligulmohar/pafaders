"""MIDI input/feedback implementation."""

import logging
import time
from datetime import datetime

import rtmidi
from rtmidi.midiconstants import CONTROL_CHANGE, SYSTEM_EXCLUSIVE, END_OF_EXCLUSIVE


CHAN_16_CC = CONTROL_CHANGE | 0xF

LISTEN_TO_ALL_PORTS = False
DUMP_SYSEX_MESSAGES_TO_FILE = False

LOG = logging.getLogger(__name__)


class MidiPortListener:
    """Generic logging MIDI listener for a single port."""

    def __init__(self, *, port, port_name, controller):
        self.port = port
        self.port_name = port_name
        self.controller = controller

        self.port.set_callback(self.callback)

    @classmethod
    def get(cls, *, port_name):
        for subclass in cls.__subclasses__():
            if subclass.handles(port_name=port_name):
                return subclass
        if LISTEN_TO_ALL_PORTS:
            return cls
        return None

    def callback(self, event, data):
        octets, dt = event
        if DUMP_SYSEX_MESSAGES_TO_FILE and octets[0] == SYSTEM_EXCLUSIVE:
            LOG.debug
            now = datetime.now().strftime("%H:%M:%S.%f")
            filename = f"sysex-{now}"
            with open(filename, "wb") as bin_file:
                LOG.info(
                    "Port %r, Writing SysEx message to file %s",
                    self.port_name,
                    filename,
                )
                bin_file.write(bytes(octets[1:-1]))
        else:
            LOG.debug("Port %r, Event %r, data %r", self.port_name, octets, data)

    def set_volume(self, *, app, volume):
        self.controller.set_volume(app=app, volume=volume)


class RemoteZeroSLListener(MidiPortListener):
    """Novation ReMOTE ZeRO SL listener implementation.

    Display updating info from
    https://cycling74.com/forums/novation-automap-external .

    The post indicates that this information is available in the SDK
    from Novation, but it does not seem to be available anymore.

    """

    MANUFACTURER_ID = [0x00, 0x20, 0x29]

    # This is supposed to be the "host id". Is it constant?
    PID = 0x02

    SYSEX_PREFIX = [SYSTEM_EXCLUSIVE] + MANUFACTURER_ID + [0x03, 0x03]
    TEXT_SYSEX_PREFIX = SYSEX_PREFIX + [0x11, 0x04, PID]
    AUTOMAP_ENGAGE_SYSEX = SYSEX_PREFIX + [
        0x10,
        0x05,
        PID,
        0x00,
        0x01,
        0x01,
        END_OF_EXCLUSIVE,
    ]

    # Template 38 (Automap) is set up to send CC:s for every control
    # to this port.
    PORT_NAME = "ReMOTE ZeRO SL MIDI 3"

    def __init__(self, *, port, port_name, controller):
        super().__init__(port=port, port_name=port_name, controller=controller)
        midi_out = rtmidi.MidiOut()
        ports = midi_out.get_ports()
        for index, name in enumerate(ports):
            if name == self.port_name:
                self.out_port = midi_out.open_port(index)
                LOG.debug("Open output port %r for %r", name, self.__class__.__name__)
                break
        else:
            raise SystemError("No matching output port found")
        self.out_port.send_message(self.AUTOMAP_ENGAGE_SYSEX)
        self.update_displays()

    @classmethod
    def handles(cls, *, port_name):
        return cls.PORT_NAME in port_name

    def callback(self, event, data):
        super().callback(event, data)
        octets, dt = event
        if octets[0] == CHAN_16_CC:
            control, value = octets[1:]
            # CC 16..23 correspond to the faders
            if 16 <= control < 24:
                app = control - 16
                volume = value / 127.0
                self.set_volume(app=app, volume=volume)
        elif octets == self.AUTOMAP_ENGAGE_SYSEX:
            # We need to wait for the transient template change
            # message to disappear from the display.
            time.sleep(0.8)
            self.update_displays()

    def clear_displays(self):
        for display in (0x04, 0x05):
            msg = (
                [SYSTEM_EXCLUSIVE]
                + self.MANUFACTURER_ID
                + self.TEXT_SYSEX_PREFIX
                + [0x00, 0x02, 0x02, display, END_OF_EXCLUSIVE]
            )
            self.out_port.send_message(msg)

    def show_text(self, *, display, line, column, text):
        line_id = line * 2 + display + 1
        msg = (
            [SYSTEM_EXCLUSIVE]
            + self.MANUFACTURER_ID
            + self.TEXT_SYSEX_PREFIX
            + [0x00, 0x02, 0x01, column, line_id, 0x04]
            + list(text.encode("ascii"))
            + [END_OF_EXCLUSIVE]
        )
        self.out_port.send_message(msg)

    def update_displays(self):
        self.clear_displays()
        self.show_text(display=0, line=0, column=0, text="pafaders")
        text = " ".join(f"    {n}   " for n in range(8))
        self.show_text(display=1, line=1, column=0, text=text)


class MidiListener:
    """Overarching MIDI listener object."""

    def __init__(self, *, controller):
        self.controller = controller
        self.port_listeners = {}
        self.midi_in = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def check_ports(self):
        while True:
            midi_in = self.midi_in or rtmidi.MidiIn()
            midi_in.ignore_types(False, False, False)
            ports = midi_in.get_ports()
            for index, name in enumerate(ports):
                if name not in self.port_listeners:
                    try:
                        listener_class = MidiPortListener.get(port_name=name)
                        if listener_class is not None:
                            LOG.debug(
                                "Open port %r %r with %r",
                                index,
                                name,
                                listener_class.__name__,
                            )
                            port = midi_in.open_port(index)
                            listener = listener_class(
                                port=port, port_name=name, controller=self.controller
                            )
                            self.port_listeners[name] = listener
                            self.midi_in = None
                            break
                    except (rtmidi.InvalidUseError, rtmidi.SystemError):
                        LOG.exception("open_port")
            else:
                # Re-use MidiIn object if we don't use it to open a
                # port. Repeatedly creating new ones leads to some
                # kind of resource leakage and this exception:
                #
                # rtmidi._rtmidi.SystemError: MidiInAlsa::initialize:
                #     error creating ALSA sequencer client object.
                self.midi_in = midi_in
                break