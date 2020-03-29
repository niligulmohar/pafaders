#!/usr/bin/env python3

import logging
import time

from pafaders.midi import MidiListener
from pafaders.pulseaudio import Applications


LOG = logging.getLogger(__name__)


class Controller:
    def __init__(self):
        self.apps = None

    def set_applications(self, apps):
        self.apps = apps

    def set_volume(self, *, app, volume):
        if self.apps is not None:
            self.apps.set_volume(app=app, volume=volume)


def main():
    logging.basicConfig(level=logging.INFO)

    controller = Controller()

    with Applications(controller=controller):
        with MidiListener(controller=controller) as listener:
            try:
                while True:
                    # Periodically check for new MIDI ports
                    listener.check_ports()
                    time.sleep(1)
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
